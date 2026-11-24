"""Karen's everyday extras, as an MCP server: expenses, countdowns, exchange rates, YouTube summaries and
"what did Karen do today?". Free, no keys. Data in data/ (expenses.json, countdowns.json); logs are only read."""
import datetime
import json
import os
import re
import urllib.parse
import urllib.request

from mcp.server import MCPServer

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DATA = os.path.join(ROOT, "data")
mcp = MCPServer("extras")


def load(name, default):
    try:
        with open(os.path.join(DATA, name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save(name, value):
    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, name), "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=1)


def money(x):
    return f"₹{x:,.0f}" if float(x).is_integer() else f"₹{x:,.2f}"


# ---------------------------------------------------------------- expenses
@mcp.tool()
def add_expense(amount: float, what: str, category: str = "", date: str = "") -> str:
    """Log money the user spent, e.g. "I spent 200 on lunch". amount: in rupees. what: what it was for.
    category: food, travel, shopping, bills, fun, health, education or other (guess from 'what' if not said).
    date: YYYY-MM-DD, empty = today."""
    if amount <= 0:
        return "The amount must be more than zero."
    items = load("expenses.json", [])
    day = date or datetime.date.today().isoformat()
    items.append({"date": day, "amount": round(float(amount), 2), "what": what.strip(),
                  "category": (category or "other").strip().lower()})
    save("expenses.json", items)
    month = sum(i["amount"] for i in items if i["date"][:7] == day[:7])
    return f"Logged {money(amount)} for {what}. This month so far: {money(month)}."


@mcp.tool()
def expense_summary(period: str = "month") -> str:
    """How much the user spent: period = today, week, month (this month), last month, or year. Gives the total,
    the split by category and the biggest items."""
    items = load("expenses.json", [])
    today = datetime.date.today()
    first = today.replace(day=1)
    if period == "today":
        start, end = today, today
    elif period == "week":
        start, end = today - datetime.timedelta(days=today.weekday()), today
    elif period == "last month":
        end = first - datetime.timedelta(days=1)
        start = end.replace(day=1)
    elif period == "year":
        start, end = today.replace(month=1, day=1), today
    else:
        start, end = first, today
    picked = [i for i in items if start.isoformat() <= i["date"] <= end.isoformat()]
    if not picked:
        return f"No expenses logged for {period}."
    total = sum(i["amount"] for i in picked)
    cats = {}
    for i in picked:
        cats[i["category"]] = cats.get(i["category"], 0) + i["amount"]
    split = ", ".join(f"{c} {money(v)}" for c, v in sorted(cats.items(), key=lambda kv: -kv[1]))
    top = ", ".join(f"{i['what']} {money(i['amount'])}" for i in sorted(picked, key=lambda i: -i["amount"])[:3])
    return f"Spent {money(total)} ({period}, {len(picked)} items). By category: {split}. Biggest: {top}."


@mcp.tool()
def delete_last_expense() -> str:
    """Remove the expense logged last (a mistake)."""
    items = load("expenses.json", [])
    if not items:
        return "There are no expenses to remove."
    gone = items.pop()
    save("expenses.json", items)
    return f"Removed {money(gone['amount'])} for {gone['what']}."


# ---------------------------------------------------------------- countdowns
@mcp.tool()
def add_countdown(event: str, date: str) -> str:
    """Count down to an event, e.g. "days until my exam on 15 November". date: YYYY-MM-DD."""
    try:
        day = datetime.date.fromisoformat(date[:10])
    except ValueError:
        return "Give the date as YYYY-MM-DD."
    items = [c for c in load("countdowns.json", []) if c["event"].lower() != event.strip().lower()]
    items.append({"event": event.strip(), "date": day.isoformat()})
    save("countdowns.json", items)
    return f"{event}: {(day - datetime.date.today()).days} days to go ({day:%A %d %B})."


@mcp.tool()
def countdowns() -> str:
    """Days left until each saved event (past ones are dropped)."""
    today = datetime.date.today()
    items = [c for c in load("countdowns.json", []) if datetime.date.fromisoformat(c["date"]) >= today]
    save("countdowns.json", items)
    if not items:
        return "No countdowns saved."
    items.sort(key=lambda c: c["date"])
    return "; ".join(f"{c['event']}: {(datetime.date.fromisoformat(c['date']) - today).days} days "
                     f"({datetime.date.fromisoformat(c['date']):%d %b})" for c in items)


@mcp.tool()
def remove_countdown(event: str) -> str:
    """Delete a saved countdown by its name."""
    items = load("countdowns.json", [])
    keep = [c for c in items if event.lower() not in c["event"].lower()]
    save("countdowns.json", keep)
    return f"Removed {len(items) - len(keep)} countdown(s)." if len(keep) < len(items) else "No countdown by that name."


# ---------------------------------------------------------------- exchange rates
@mcp.tool()
def exchange_rate(amount: float = 1, base: str = "USD", to: str = "INR") -> str:
    """Convert money between currencies at today's rate (European Central Bank data via frankfurter.dev, free).
    base/to: 3-letter codes like USD, INR, EUR, GBP, AED, JPY."""
    base, to = base.upper().strip(), to.upper().strip()
    url = "https://api.frankfurter.dev/v1/latest?" + urllib.parse.urlencode({"base": base, "symbols": to})
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Karen"}), timeout=10) as r:
            data = json.load(r)
        rate = data["rates"][to]
    except Exception as e:
        return f"Couldn't get the {base} to {to} rate right now ({type(e).__name__})."
    return f"{amount:g} {base} = {amount * rate:,.2f} {to} (rate {rate:.4f}, {data.get('date', 'today')})."


# ---------------------------------------------------------------- YouTube
@mcp.tool()
def youtube_transcript(url: str) -> str:
    """The spoken words of a YouTube video, from its link, so you can summarise it or answer questions about it.
    Use for "summarise this YouTube video", "what does this video say". Then give a short summary in your own words."""
    m = re.search(r"(?:v=|youtu\.be/|shorts/|embed/|live/)([\w-]{11})", url) or re.fullmatch(r"([\w-]{11})", url.strip())
    if not m:
        return "That doesn't look like a YouTube link."
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        parts = YouTubeTranscriptApi().fetch(m.group(1), languages=["en", "en-IN", "hi", "ta"])
    except Exception as e:
        return f"This video has no transcript I can read ({type(e).__name__}), so I can't summarise it."
    text = " ".join(p.text for p in parts).replace("\n", " ")
    minutes = int((parts[-1].start + parts[-1].duration) // 60) if len(parts) else 0
    cut = 9000
    return f"Transcript ({minutes} min video){', first part only' if len(text) > cut else ''}:\n{text[:cut]}"


# ---------------------------------------------------------------- what Karen did
@mcp.tool()
def activity_report(day: str = "") -> str:
    """What Karen did on a day (default today): requests, tools used and how often, from her own logs (voice and
    Telegram). Use for "what did you do today?", "what did I ask you yesterday?". day: YYYY-MM-DD or 'yesterday'."""
    when = datetime.date.today()
    if day == "yesterday":
        when -= datetime.timedelta(days=1)
    elif day:
        try:
            when = datetime.date.fromisoformat(day[:10])
        except ValueError:
            pass
    stamp = f"[{when.isoformat()}"
    asks, tools = [], {}
    for name, where in (("karen.log", "voice"), ("remote.log", "telegram"), ("app.log", "app")):
        try:
            with open(os.path.join(ROOT, "logs", name), encoding="utf-8", errors="replace") as f:
                for line in f:
                    if not line.startswith(stamp):
                        continue
                    m = re.match(r"\[[\d-]+ ([\d:]+)\] (\w+)\s+(.*)", line.strip())
                    if not m:
                        continue
                    t, kind, rest = m.groups()
                    if kind == "you" and not rest.startswith("[app:"):  # the app's own lines are in app.log
                        asks.append(f"{t[:5]} ({where}) {re.sub(r'  \(speech-to-text.*', '', rest)[:90]}")
                    elif kind == "tool":
                        tool = rest.split()[0]
                        tools[tool] = tools.get(tool, 0) + 1
        except OSError:
            continue
    if not asks and not tools:
        return f"Nothing in my logs for {when:%A %d %B}."
    used = ", ".join(f"{t} x{n}" for t, n in sorted(tools.items(), key=lambda kv: -kv[1])[:12]) or "none"
    shown = asks[-25:]
    return (f"{when:%A %d %B}: {len(asks)} requests ({'last 25 shown' if len(asks) > 25 else 'all'}). "
            f"Tools used: {used}.\n" + "\n".join(shown))


if __name__ == "__main__":
    mcp.run()
