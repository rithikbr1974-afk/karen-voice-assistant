"""Karen Remote: chat with Karen from the phone (Telegram now; WhatsApp next). Text only: no microphone, no voice.

  pythonw remote.py                 run (Windows starts it at sign-in: scripts/autostart.py remote on)
  python remote.py --pair telegram  link the user's Telegram account (only paired accounts are answered)

Same brain, tool servers and confirm rules as voice Karen. Confirmations ("Send this? yes/no") are asked in the chat.
Files from tools ([[show: ...]]) are sent into the chat; files they send land in data/uploads for the file tools.
"""
import ctypes
import datetime
import json
import os
import queue
import re
import sys
import threading
import time

import yaml
from dotenv import load_dotenv

os.chdir(os.path.dirname(os.path.abspath(__file__)))
load_dotenv()
os.makedirs("logs", exist_ok=True)
LOG = open("logs/remote.log", "a", encoding="utf-8", buffering=1)
if sys.stdout is None:  # pythonw at sign-in: no console
    sys.stdout = sys.stderr = LOG
import main as karen_main  # noqa: E402
from main import REFERS_BACK, SAY, SHOW, SLOW_SAY, SLOW_TOOLS, YES, NeedsSignIn, describe  # noqa: E402

karen_main.VOICE = False  # confirm questions show real addresses, not "at ... dot ..."

OWNERS = "data/remote.json"  # {"telegram": [user ids], "whatsapp": ["phone numbers"]}: who Karen answers
TEXT_NOTE = (" You're chatting with the user by text on their phone ({channel}), not by voice: short plain messages, "
             "simple lists are fine, no markdown headings. Files you produce are sent to them in the chat.")


def log(tag, msg=""):
    line = f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {tag:<6} {msg}"
    print(line, file=LOG)


def owners():
    try:
        return json.load(open(OWNERS, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


class Remote:
    def __init__(self, cfg):
        from core.brain import Brain, private_backends
        from core.tools import Tools
        self.cfg = cfg
        c = cfg["llm"]
        self.brain = Brain(c["backends"], c["system_prompt"] + TEXT_NOTE.format(channel="Telegram"), c["max_tokens"], c["history_turns"],
                           private_backends(c), c.get("max_tool_rounds", 5))
        self.tools = Tools(cfg.get("mcp_servers"), cfg.get("confirm"), log, ask=self.confirm)
        self.channels, self.inbox, self.answers = {}, queue.Queue(), None
        self.ch, self.chat, self.sticky, self.used, self.attachments = None, None, set(), set(), []
        self.asked = False  # her last reply was a question
        self.proposals = {}
        self.started = datetime.datetime.now()  # reminder ideas from Gmail waiting for their tap: id -> {title, when, remind_at, subject}
        from core.dots import Runner
        self.dots = Runner(cfg, self.tools, log, announce=self.dot_done, confirm=self.dot_confirm)
        self.dot_asks = {}  # Dot yes/no questions waiting for a button tap: id -> Queue

    # ------------------------------------------------------------ channels
    def add(self, channel):
        self.channels[channel.name] = channel
        threading.Thread(target=self._listen, args=(channel,), daemon=True).start()

    def _listen(self, channel):
        for m in channel.poll():
            if m["user"] not in owners().get(channel.name, []):
                log("block", f"{channel.name} message from {m['name']} ({m['user']}) ignored: not paired")
                continue
            command = m["text"].strip().lower().replace("_", " ")
            if command in STOP_WORDS:  # handled here, at once, even while Karen is busy with something else
                stop_everything(channel, m["chat"])
            elif command in ("/status", "status"):
                channel.send(m["chat"], status_text(self.started))
            elif command in ("/work", "/both", "/love", "/normal"):  # personality modes, switched instantly
                from core import persona
                key = persona.set_mode(command[1:]) if command != "/normal" else persona.clear()
                channel.send(m["chat"], {"work": "💼 Work mode: focused and efficient, no romance.",
                                         "both": "💼💕 Both mode: work first, with a little love.",
                                         "love": "💕 Love mode on."}.get(key, "Back to normal Karen."))
            elif command in ("/dots", "dots"):
                channel.send(m["chat"], dots_text(self.dots))
            elif command in ("/pages", "pages"):
                channel.send(m["chat"], pages_text())
            elif m.get("button"):
                self.tapped(channel, m)
            elif self.answers is not None and m["chat"] == self.chat:  # the reply to "…? yes/no"
                self.answers.put(m["text"])
            else:
                self.inbox.put((channel, m))

    def notify(self, text, buttons=None, channel="telegram"):
        """A message Karen starts herself (reminders, mail ideas): to their first paired account. Telegram, because
        WhatsApp charges for messages she starts outside the 24 h reply window."""
        ch, who = self.channels.get(channel), owners().get(channel, [])
        if not (ch and who):
            return False
        ch.send(who[0], text, buttons) if buttons else ch.send(who[0], text)
        return True

    def notify_file(self, path, caption="", buttons=None):
        ch, who = self.channels.get("telegram"), owners().get("telegram", [])
        if ch and who:
            ch.send_file(who[0], path, caption, buttons)

    # ------------------------------------------------------------ background: due reminders and Gmail ideas
    def ticker(self):
        r = self.cfg.get("remote", {})
        every = r.get("mail_check_min", 60) * 60
        next_mail = time.time() + 120  # give the tool servers time to start
        while True:
            try:
                self.due_reminders()
                if not voice_karen_running():  # voice Karen runs scheduled Dot tasks herself while she's open
                    self.dots.tick()
                if r.get("mail_reminders", True) and time.time() >= next_mail:
                    next_mail = time.time() + every
                    self.mail_ideas()
            except Exception as e:
                log("error", f"ticker: {e!r}")
            time.sleep(20)

    def due_reminders(self, path="data/reminders.json"):
        """Send due reminders to their phone. If voice Karen is running she speaks them and marks them done; if not,
        Remote marks them done (or moves daily ones to tomorrow) itself."""
        try:
            items = json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError):
            return
        voice_on = voice_karen_running()
        now, changed = datetime.datetime.now(), False
        for r in items:
            due = datetime.datetime.fromisoformat(r["due"])
            if r.get("done") or due > now or r.get("phoned") == r["due"]:
                continue
            late = now - due > datetime.timedelta(minutes=2)
            if not self.notify(("⏰ " if r.get("timer") else "⏰ Reminder: ") + r["text"]
                               + (f" (due {due:%I:%M %p})" if late else "")):
                return
            r["phoned"], changed = r["due"], True
            if not voice_on:
                from core.reminders import advance
                advance(r, now)  # daily / every N minutes / once
        if changed:
            json.dump(items, open(path + ".tmp", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
            os.replace(path + ".tmp", path)

    def mail_ideas(self):
        if "google__search_gmail_messages" not in self.tools.tools:
            return
        from core.mailwatch import MailWatch
        for idea in MailWatch(self.tools, self.cfg.get("remote", {}).get("model"), log).scan():
            pid = str(len(self.proposals) + 1)
            self.proposals[pid] = idea
            source = f"\n(from the email: {idea['subject']})" if idea["subject"] else ""
            self.notify(f"📅 {idea['title']} — {idea['when']:%a %d %b, %I:%M %p}.\n"
                        f"Remind you {idea['remind_at']:%a %d %b at %I:%M %p}?{source}",
                        buttons=[("✅ Remind me", f"rem:{pid}:yes"), ("❌ Skip", f"rem:{pid}:no")])

    def tapped(self, channel, m):
        kind, _, rest = m["button"].partition(":")
        pid, _, answer = rest.partition(":")
        if kind == "dot":  # the answer to a Dot's yes/no
            waiting = self.dot_asks.pop(pid, None)
            channel.send(m["chat"], ("👍 Okay." if answer == "yes" else "🚫 Cancelled.") if waiting else "That question has expired.")
            if waiting:
                waiting.put(answer == "yes")
            return
        idea = self.proposals.pop(pid, None) if kind == "rem" else None
        if not idea:
            channel.send(m["chat"], "That suggestion has expired.")
        elif answer == "yes":
            result = self.tools.call("daily__set_reminder", {"text": idea["title"],
                                                             "when": idea["remind_at"].isoformat(timespec="minutes")})
            log("mail", f"reminder added: {idea['title']}")
            channel.send(m["chat"], "✅ " + result)
        else:
            channel.send(m["chat"], "Skipped.")

    # ------------------------------------------------------------ one message at a time
    def run(self):
        while True:
            channel, m = self.inbox.get()
            try:
                self.handle(channel, m)
            except Exception as e:
                log("error", repr(e))
                channel.send(m["chat"], f"Sorry, something went wrong: {type(e).__name__}.")

    def handle(self, channel, m):
        self.ch, self.chat = channel, m["chat"]
        text = m["text"].strip()
        if text.lower() in ("/start", "/new", "/reset", "new chat"):
            self.brain.forget()
            self.attachments = []
            channel.send(self.chat, "Fresh start. What can I do?")
            return
        for file_id, name in m["files"]:
            self.attachments.append(channel.download(file_id, name))
        if not text:
            if m["files"]:
                channel.send(self.chat, "Got it. What should I do with it?")
            return
        log("you", f"[{channel.name}] {text}")
        from core import persona
        switched = persona.command(text)  # "work mode" / "love mode" / "both mode": at once, no LLM
        if switched:
            log("karen", f"{switched}  (mode switch)")
            channel.send(self.chat, switched)
            return
        from core import dots
        asked = dots.command(re.sub(r"^/ask\s+", "ask ", text, flags=re.I))  # "ask the writer to …" or /ask writer …
        if asked:
            dot, task = asked
            self.dots.start(dot, task, origin=f"{channel.name}:{self.chat}")
            log("karen", f"{dot['name']} started: {task}")
            channel.send(self.chat, f"🟣 {dot['name']} is on it. I'll message you when it's done.")
            return
        channel.typing(self.chat)
        sticky = set(self.sticky) if REFERS_BACK.search(text) or self.asked else set()  # follow-ups keep the tools
        if self.attachments:
            text += f"\n[Attached files in data/uploads: {', '.join(os.path.basename(a) for a in self.attachments)}]"
            sticky |= {("files", g) for g in {t["group"] for t in self.tools.tools.values() if t["server"] == "files"}}
        self.brain.system_prompt = self.cfg["llm"]["system_prompt"] + TEXT_NOTE.format(channel=channel.name.title())
        tools = self.tools.select(text, sticky)
        self.used, offered = set(), self.tools.groups_of(tools) if self.tools else set()
        t = time.time()
        try:
            reply = "".join(self.brain.ask(text, tools, self.run_tool)).strip()
        except NeedsSignIn:
            reply = "Google sign-in opened in the browser on your PC. Sign in there, then ask me again."
        b = self.brain.last
        log("karen", f"{reply}  ({b.get('failed', '')}{b.get('backend')} {time.time() - t:.1f}s)")
        channel.send(self.chat, reply or "Done.")
        self.asked = reply.endswith("?")  # her question: their answer gets the same tools ("like 2 weeks")
        self.sticky = self.used | (offered if self.asked else set())

    def run_tool(self, name, args):
        short = name.split("__", 1)[-1]
        if self.tools.needs_confirm(name) and not self.confirm(describe(short, args)):
            log("tool", f"{short} cancelled")
            return "The user said no, so nothing was done. Don't retry; just tell them it was cancelled."
        if short in SLOW_TOOLS:
            self.ch.send(self.chat, SLOW_SAY.get(short, "On it."))
        t = time.time()
        result = self.tools.call(name, args)
        log("tool", f"{short} {json.dumps(args, ensure_ascii=False)[:200]} -> {len(result)} chars, {time.time() - t:.1f}s")
        if "ACTION REQUIRED" in result and "Authorization URL" in result:
            raise NeedsSignIn()
        for target in (s.strip() for s in SHOW.findall(result)):
            if os.path.isfile(target):
                self.ch.send_file(self.chat, target)
            elif target.startswith("http"):
                self.ch.send(self.chat, target)
        result = SHOW.sub("(now sent to them in the chat)", result)
        result = SAY.sub(lambda m: f'"{m.group(3)}" (no voice in the chat: write this translation in the reply)', result)
        self.used.add(self.tools.group_of(name))
        self.ch.typing(self.chat)
        return result

    # ------------------------------------------------------------ Dots (background helpers)
    def _origin(self, origin):
        """'telegram:12345' -> (channel, chat); scheduled or voice work goes to their Telegram."""
        name, _, chat = (origin or "").partition(":")
        if name in self.channels and chat:
            return self.channels[name], chat
        who = owners().get("telegram", [])
        return (self.channels.get("telegram"), who[0]) if who and "telegram" in self.channels else (None, None)

    def dot_done(self, run):
        ch, chat = self._origin(run["origin"])
        if not ch:
            return
        mark = "✅" if run["status"] == "done" else "⚠️"
        ch.send(chat, f"{mark} {run['dot']}: {run['answer']}")
        from core import spaces
        for page in run.get("pages", [])[:2]:  # the pages it wrote, as files they can open on the phone
            try:
                ch.send_file(chat, spaces.path(page)[1], caption=page)
            except Exception as e:
                log("error", f"sending page {page}: {e!r}")

    def dot_confirm(self, dot, tool, args, origin):
        """A Dot's risky tool: ask with Yes/No buttons (Telegram) and wait up to 10 minutes; anything else is no."""
        ch, chat = self._origin(origin)
        if not ch or ch.name != "telegram":
            return False  # WhatsApp has no buttons here; a typed "yes" would race with the conversation
        qid = str(int(time.time() * 1000))
        self.dot_asks[qid] = queue.Queue()
        ch.send(chat, f"🟣 {dot} asks: {describe(tool, args)}",
                buttons=[("✅ Yes", f"dot:{qid}:yes"), ("❌ No", f"dot:{qid}:no")])
        try:
            return self.dot_asks[qid].get(timeout=600)
        except (queue.Empty, KeyError):
            self.dot_asks.pop(qid, None)
            return False

    def confirm(self, question):
        """Ask in the chat; anything but a clear yes within 3 minutes cancels."""
        log("confirm", question)
        self.answers = queue.Queue()
        self.ch.send(self.chat, f"{question} (yes/no)")
        try:
            answer = self.answers.get(timeout=180)
        except queue.Empty:
            self.ch.send(self.chat, "No answer, so I didn't do it.")
            answer = ""
        finally:
            self.answers = None
        ok = bool(YES.match(answer))
        log("confirm", "yes" if ok else "no")
        return ok


def make_channel(name):
    """The channel object for 'telegram' or 'whatsapp' from .env, or None if its keys aren't there."""
    e = os.environ
    if name == "telegram" and e.get("TELEGRAM_BOT_TOKEN"):
        from core.telegram import Telegram
        return Telegram(e["TELEGRAM_BOT_TOKEN"])
    if name == "whatsapp" and all(e.get(k) for k in ("WHATSAPP_TOKEN", "WHATSAPP_PHONE_ID", "WHATSAPP_APP_SECRET")):
        from core.whatsapp import WhatsApp
        return WhatsApp(e["WHATSAPP_TOKEN"], e["WHATSAPP_PHONE_ID"], e["WHATSAPP_APP_SECRET"],
                        e.get("WHATSAPP_VERIFY_TOKEN", "karen"), e.get("NGROK_DOMAIN", ""), log=log)
    return None


def pair(name):
    """Link their account: the next message to Karen is shown here, and saved as an owner once they confirm."""
    bot = make_channel(name)
    if not bot:
        sys.exit(f"{name} isn't set up in .env yet.")
    where = f"@{bot.me()['username']} in Telegram" if name == "telegram" else "Karen's WhatsApp number"
    print(f"Send any message to {where} now...")
    for m in bot.poll():
        if input(f"Message from {m['name']} (id {m['user']}): {m['text']!r}. Is this you? [y/n] ").lower().startswith("y"):
            data = owners()
            data.setdefault(name, [])
            if m["user"] not in data[name]:
                data[name].append(m["user"])
            os.makedirs("data", exist_ok=True)
            json.dump(data, open(OWNERS, "w", encoding="utf-8"), indent=2)
            bot.send(m["chat"], "Paired. I'm Karen; ask me anything here.")
            print("Paired.")
            return


STOP_WORDS = {"/stop", "/stop conversation", "stop conversation", "/stopconversation", "stop karen", "/stop karen"}


def stop_everything(channel, chat):
    """/stop: shut down all of Karen on this PC: voice Karen (if open), Karen Remote and their tool servers.
    She starts again at the next Windows sign-in (scripts/autostart.py remote off turns that off)."""
    import psutil
    log("stop", "/stop from the phone: shutting down Karen on this PC")
    try:
        channel.send(chat, "Stopping Karen on your PC now: voice Karen, this chat and all their helpers. "
                           "I'll start again by myself the next time Windows starts.")
    except Exception:
        pass
    me = psutil.Process()
    here = os.path.abspath(".").lower()
    targets = []
    for p in psutil.process_iter(["pid", "cmdline", "cwd"]):
        try:
            cmd = " ".join(p.info["cmdline"] or []).lower()
            if p.pid != me.pid and "main.py" in cmd and (p.info["cwd"] or "").lower() == here:
                targets.append(p)  # voice Karen
        except (psutil.Error, OSError):
            continue
    for p in targets + [me]:
        try:
            targets_children = p.children(recursive=True)
        except psutil.Error:
            targets_children = []
        for c in targets_children:
            try:
                c.kill()
            except psutil.Error:
                pass
    for p in targets:
        try:
            p.terminate()
        except psutil.Error:
            pass
    psutil.wait_procs(targets, timeout=5)
    try:  # voice Karen may have been mid-listen with other apps turned down: put their volume back
        from core.audio import Ducker
        Ducker.recover()
    except Exception:
        pass
    os._exit(0)


def dots_text(runner):
    from core import dots
    busy = {r["dot"]: r for r in runner.running.values()}
    lines = ["🟣 Your Dots:"] + [f"• {d['name']}: {d['role']}" + (" (working…)" if d["name"] in busy else "")
                                for d in dots.dots()]
    jobs = dots.jobs()
    if jobs:
        lines += ["", "⏰ Scheduled:"] + [f"#{j['id']} {j['dot']} {j['at']} ({','.join(j['days'])}): {j['task']}" for j in jobs]
    return "\n".join(lines + ["", "Ask one: /ask researcher compare Pixel 10 and iPhone 17"])


def pages_text():
    from core import spaces
    pages = sorted((spaces.info(*p) for p in spaces.pages()), key=lambda i: i["edited"], reverse=True)[:12]
    if not pages:
        return "No pages yet. Try: ask the researcher to …"
    return "📄 Latest pages:\n" + "\n".join(f"• {i['name']} ({i['words']} words)" for i in pages)


def status_text(started):
    import psutil
    up = datetime.datetime.now() - started
    battery = psutil.sensors_battery()
    plugged = battery is None or battery.power_plugged
    awake = "keeping the PC awake (plugged in)" if plugged and KEEP_AWAKE["on"] else \
        "not keeping it awake (on battery)" if not plugged else "sleep allowed"
    return (f"Karen Remote is running ({int(up.total_seconds() // 3600)} h {int(up.total_seconds() % 3600 // 60)} min). "
            f"I'm {awake}. Voice Karen is {'open' if voice_karen_running() else 'closed'}"
            + (f". Battery {battery.percent:.0f}%." if battery else ".") + " Send /stop to shut me down.")


KEEP_AWAKE = {"on": False}


def keep_awake(mode):
    """Stop Windows from going to sleep while Remote runs (so the phone can reach the PC): 'plugged_in' (only on
    mains power, never draining the battery), 'always', or 'off'. Closing the lid or the power button still sleeps."""
    import psutil
    ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
    while True:
        battery = psutil.sensors_battery()
        on = mode == "always" or (mode == "plugged_in" and (battery is None or battery.power_plugged))
        if on != KEEP_AWAKE["on"]:
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))
            KEEP_AWAKE["on"] = on
            log("awake", "keeping the PC awake" if on else "letting the PC sleep normally")
        time.sleep(30)


def voice_karen_running():
    handle = ctypes.windll.kernel32.OpenMutexW(0x00100000, False, r"Local\KarenVoiceAssistant")  # SYNCHRONIZE
    if handle:
        ctypes.windll.kernel32.CloseHandle(handle)
    return bool(handle)


def main():
    if "--pair" in sys.argv:
        return pair(sys.argv[sys.argv.index("--pair") + 1])
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, r"Local\KarenRemote")  # noqa: F841 (held while running)
    if ctypes.windll.kernel32.GetLastError() == 183:
        print("Karen Remote is already running.")
        return
    from core.brain import load_config
    cfg = load_config()
    remote = Remote(cfg)
    for name in ("telegram", "whatsapp"):
        channel = make_channel(name)
        if channel:
            remote.add(channel)
    if not remote.channels:
        log("error", "no channels: put TELEGRAM_BOT_TOKEN (and the WHATSAPP_* keys) in .env")
        return
    try:  # Remote never touches the volume itself, but it can clean up after a voice Karen that was killed
        from core.audio import Ducker
        if not voice_karen_running() and Ducker.recover():
            log("audio", "restored the volume of programs left turned down by a killed voice Karen")
    except Exception:
        pass
    log("ready", f"channels: {', '.join(remote.channels)}")
    threading.Thread(target=remote.ticker, daemon=True).start()
    threading.Thread(target=keep_awake, args=(cfg.get("remote", {}).get("keep_awake", "plugged_in"),),
                     daemon=True).start()
    if "telegram" in remote.channels:  # the "/" menu in Telegram
        remote.channels["telegram"].set_commands([("work", "Full work mode: focused, no romance"),
                                                  ("both", "Work first, with a little love"),
                                                  ("love", "Love mode"),
                                                  ("normal", "Normal Karen"),
                                                  ("stop", "Stop Karen on the PC completely"),
                                                  ("status", "Is Karen running? Battery, keep-awake"),
                                                  ("dots", "Your Dots (helpers) and scheduled tasks"),
                                                  ("ask", "Give a Dot a task: /ask writer draft a leave letter"),
                                                  ("pages", "Latest Spaces pages"),
                                                  ("new", "Start a fresh conversation")])
    remote.run()


if __name__ == "__main__":
    main()
