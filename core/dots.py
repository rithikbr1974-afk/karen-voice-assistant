"""Dots: specialist helpers (Researcher, Writer, Coder, Organiser) that work in the background, each with its own
conversation, role and allowed tools, and keep their results as Markdown pages in Spaces (data/spaces).

The idea comes from CopilotKit's OpenDots, rebuilt small and local on Karen's own Brain and MCP tools.
Storage helpers (dots, scheduled tasks) are shared with servers/dots; Runner is used by voice Karen and Remote.
"""
import datetime
import json
import os
import re
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("KAREN_DATA") or os.path.join(ROOT, "data")  # tests point this elsewhere
DOTS = os.path.join(DATA, "dots.json")
JOBS = os.path.join(DATA, "dot_jobs.json")
RUNS = os.path.join(DATA, "dot_runs.json")
SPACES = os.path.join(DATA, "spaces")

# what each kind of Dot may use: "server" = all its tools, "server:glob" = matching tools
ABILITIES = {
    "research": ["dots:*page*", "dots:web_*", "dots:read_url"],
    "writing": ["dots:*page*"],
    "coding": ["dots:*page*", "dev", "files"],
    "organising": ["dots:*page*", "google:*gmail*", "google:*calendar*", "google:*event*", "daily:*todo*",
                   "daily:*reminder*", "daily:*note*"],
}
DEFAULTS = [
    {"name": "Researcher", "role": "researches topics on the web and writes sourced notes",
     "instructions": "Search, read the best sources, compare them, and cite links.", "abilities": "research",
     "space": "Research", "private": False},
    {"name": "Writer", "role": "writes and edits documents: drafts, summaries, essays, emails, plans",
     "instructions": "Clear, well-structured writing that matches what the user asked for.", "abilities": "writing",
     "space": "Drafts", "private": False},
    {"name": "Coder", "role": "helps with the user's code projects, git status and files",
     "instructions": "Look before you change anything; explain code simply.", "abilities": "coding",
     "space": "Code", "private": True},
    {"name": "Organiser", "role": "handles email, calendar, to-dos and reminders",
     "instructions": "Be precise with dates and people; summarise inboxes briefly.", "abilities": "organising",
     "space": "Plans", "private": True},
]
WEB_TOOLS = {"web_search_text", "read_url"}
WEB_BUDGET = 3   # web reads per task; Gemini Flash-Lite kept searching (11 times) and never wrote the page
SCHEDULE = re.compile(r"\b(every|each)\s+(day|morning|evening|night|week|weekday|weekend|month|hour|mon|tue|wed|thu|"
                      r"fri|sat|sun)|\b(daily|weekly) at\b|\bon (weekdays|weekends)\b", re.I)  # not "a daily plan"
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _load(path, default):
    try:
        return json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _save(path, data):
    os.makedirs(DATA, exist_ok=True)
    json.dump(data, open(path + ".tmp", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    os.replace(path + ".tmp", path)


# ---------------------------------------------------------------- dots
def dots():
    found = _load(DOTS, None)
    if found is None:
        _save(DOTS, DEFAULTS)
        return [dict(d) for d in DEFAULTS]
    return found


def save_dots(items):
    _save(DOTS, items)


def find(name):
    name = (name or "").strip().lower().removeprefix("the ").replace("z", "s")  # speech writes "organizer"
    for d in dots():
        if d["name"].lower().replace("z", "s") == name:
            return d
    return None


def allowed(dot):
    """Tool rules for a Dot: its abilities preset (or an explicit list)."""
    a = dot.get("abilities", "writing")
    return ABILITIES.get(a, ABILITIES["writing"]) if isinstance(a, str) else list(a)


def command(text):
    """'ask the researcher to …', 'tell writer to …', 'researcher, …' -> (dot, task), else None. No LLM needed."""
    names = "|".join(re.escape(n) for d in dots() for n in {d["name"], d["name"].replace("s", "z")})
    if not names:
        return None
    t = re.sub(r"^\s*(hey |ok )?karen[,.!]?\s*", "", text or "", flags=re.I).strip()
    m = (re.match(rf"(?:please |can you |could you )?(?:ask|tell|get|have|let) (?:the |my |our )?({names})\b(?: dot)?"
                  rf"[,:]?\s+(?:to\s+)?(.+)", t, re.I | re.S)
         or re.match(rf"(?:hey |ok )?({names})(?: dot)?[,:]\s*(.+)", t, re.I | re.S))
    if not m or len(m.group(2).strip()) < 3 or SCHEDULE.search(t):
        return None  # "... every weekday at 8": Karen's LLM schedules it with dots__schedule_dot_task
    return find(m.group(1)), m.group(2).strip().rstrip(".")


# ---------------------------------------------------------------- scheduled tasks
def jobs():
    return _load(JOBS, [])


def save_jobs(items):
    _save(JOBS, items)


def parse_days(days):
    """'daily' | 'weekdays' | 'weekends' | 'mon,wed,fri' -> list of day names."""
    d = (days or "daily").lower().replace(" ", "")
    if d in ("daily", "everyday", "every"):
        return list(DAYS)
    if d == "weekdays":
        return DAYS[:5]
    if d == "weekends":
        return DAYS[5:]
    picked = [x[:3] for x in re.split(r"[,/&]|and", d) if x[:3] in DAYS]
    return picked or list(DAYS)


def next_run(at, days, after=None):
    """The next datetime at 'HH:MM' on one of the days, strictly after `after`."""
    after = after or datetime.datetime.now()
    hh, mm = (int(x) for x in at.split(":"))
    for i in range(8):
        day = after.date() + datetime.timedelta(days=i)
        when = datetime.datetime.combine(day, datetime.time(hh, mm))
        if when > after and DAYS[day.weekday()] in days:
            return when
    return after + datetime.timedelta(days=1)


def runs():
    return _load(RUNS, [])


def _record(run):
    items = [r for r in runs() if r["id"] != run["id"]] + [run]
    _save(RUNS, items[-30:])


# ---------------------------------------------------------------- running
PROMPT = ("You are {name}, one of the user's specialist helpers inside their assistant Karen. You {role}. {instructions} "
          "Work on the task with your tools, without asking them questions: at most 3 web searches or page reads, then "
          "write. Keep anything worth keeping "
          "as a Markdown page with dots__save_page, in the '{space}' space unless they named another, with headings "
          "and source links. Your final message is read aloud to them: one or two plain sentences (no markdown), "
          "calling them 'you', saying what you did and the page name.")


class Runner:
    """Runs Dot tasks in background threads. announce(run) delivers the result (run: dot, task, answer, status,
    pages, origin); confirm(dot_name, tool, args, origin) -> bool asks the user where the task came from (scheduled
    work has nobody to ask: those tools are refused)."""

    def __init__(self, cfg, tools, log=print, announce=None, confirm=None):
        self.cfg, self.tools, self.log = cfg, tools, log
        self.announce = announce or (lambda run: log("dot", f"{run['dot']}: {run['answer']}"))
        self.confirm = confirm
        self.brains = {}    # dot name -> Brain (each Dot keeps its own short conversation)
        self.running = {}   # run id -> run record
        self.lock = threading.Lock()

    def brain(self, dot):
        from core.brain import Brain
        key = dot["name"]
        b = self.brains.get(key)
        sig = json.dumps(dot, sort_keys=True)
        if not b or b.sig != sig:  # created, or its settings changed
            llm = self.cfg["llm"]
            private = llm.get("private_backends", ()) if dot.get("private", True) else ()
            b = Brain(llm["backends"], PROMPT.format(**{"space": "Inbox", **dot}), max_tokens=2000, history_turns=4,
                      private=private, max_rounds=8)
            b.use_persona, b.sig = False, sig
            self.brains[key] = b
        return b

    def start(self, dot, task, origin="voice", scheduled=False):
        run = {"id": f"{int(time.time() * 1000)}", "dot": dot["name"], "task": task, "origin": origin,
               "status": "running", "started": datetime.datetime.now().isoformat(timespec="seconds"), "steps": [], "pages": []}
        with self.lock:
            self.running[run["id"]] = run
        _record(run)
        threading.Thread(target=self._run, args=(dot, task, run, scheduled), daemon=True).start()
        return run

    def _run(self, dot, task, run, scheduled):
        t = time.time()
        rules = allowed(dot)
        schemas = self.tools.schemas(rules)

        def run_tool(name, args):
            if not self.tools.allows(rules, name):
                return f"Error: {dot['name']} is not allowed to use {name}."
            short = name.split("__", 1)[-1]
            if short in WEB_TOOLS and sum(s in WEB_TOOLS for s in run["steps"]) >= WEB_BUDGET:
                return ("Research limit reached: don't search again. Write up what you found now with "
                        "dots__save_page, then give your final message.")
            run["steps"].append(short)
            _record(run)
            if self.tools.needs_confirm(name):
                ask = None if scheduled else self.confirm
                if not ask:
                    return "Not done: this needs the user's OK and they aren't here. Tell them to ask you directly."
                if not ask(dot["name"], short, args, run["origin"]):
                    return "The user said no, so nothing was done. Don't retry; mention it in your final message."
            result = self.tools.call(name, args)
            self.log("dot", f"{dot['name']} {short} -> {len(result)} chars")
            if short == "save_page" and result.startswith("Saved page "):
                page = result[len("Saved page "):].rsplit(" (", 1)[0]
                run["pages"] = [p for p in run["pages"] if p != page] + [page]
            if short in WEB_TOOLS and sum(s in WEB_TOOLS for s in run["steps"]) >= WEB_BUDGET:
                result += "\n\n(That was your last web read: now save the page and finish.)"
            return result

        try:
            brain = self.brain(dot)
            answer = "".join(brain.ask(task, schemas, run_tool))
            run["status"] = "failed" if answer.startswith("Sorry, that took too many steps") else "done"
        except Exception as e:
            answer = f"I couldn't finish that: {type(e).__name__}."
            run["status"] = "failed"
            self.log("error", f"dot {dot['name']}: {e!r}")
        run.update(answer=answer.strip(), seconds=round(time.time() - t, 1))
        with self.lock:
            self.running.pop(run["id"], None)
        _record(run)
        self.log("dot", f"{dot['name']} {run['status']} in {run['seconds']}s: {run['answer']}")
        try:
            self.announce(run)
        except Exception as e:
            self.log("error", f"dot announce: {e!r}")

    def tick(self, now=None):
        """Start scheduled tasks that are due (called every few seconds by Karen's ticker or by Remote)."""
        now = now or datetime.datetime.now()
        items, changed = jobs(), False
        for j in items:
            if j.get("paused") or datetime.datetime.fromisoformat(j["next"]) > now:
                continue
            dot = find(j["dot"])
            if dot:
                self.log("dot", f"scheduled: {dot['name']}: {j['task']}")
                self.start(dot, j["task"], origin=j.get("origin", "voice"), scheduled=True)
            j["last"] = now.isoformat(timespec="minutes")
            j["next"] = next_run(j["at"], j["days"], now).isoformat(timespec="minutes")
            changed = True
        if changed:
            save_jobs(items)
