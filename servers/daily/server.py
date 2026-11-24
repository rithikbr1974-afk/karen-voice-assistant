"""Karen's everyday helpers, as an MCP server (stdio): weather, news, reminders/timers, long-term memory, briefing.

Free services only, no keys: Open-Meteo (weather) and Google News RSS (headlines, personal use).
Reminders and memories are local JSON files in data/ (git-ignored). Karen's main loop announces due reminders.
"""
import datetime
import json
import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import yaml
from mcp.server import MCPServer

import sys  # noqa: E402
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from core import persona  # noqa: E402  (work / both / love modes, shared with Karen and Remote)

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CFG = (yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8")) or {}).get("daily", {})
DATA = os.path.join(ROOT, "data")
REMINDERS = os.path.join(DATA, "reminders.json")
MEMORY = os.path.join(DATA, "memory.json")
PERSONA = os.path.join(DATA, "persona.txt")  # how Karen talks; the brain adds it to every reply (voice and phone)
WMO = {0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "fog", 51: "light drizzle",
       53: "drizzle", 55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain",
       67: "freezing rain", 71: "light snow", 73: "snow", 75: "heavy snow", 80: "light showers", 81: "showers",
       82: "heavy showers", 95: "thunderstorms", 96: "thunderstorms with hail", 99: "thunderstorms with hail"}

mcp = MCPServer("daily")


def load(path, default):
    try:
        return json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return default


def save(path, data):
    os.makedirs(DATA, exist_ok=True)
    tmp = path + ".tmp"
    json.dump(data, open(tmp, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    os.replace(tmp, path)  # atomic, so Karen never reads half a file


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Karen-assistant/1.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read()


# ---------------------------------------------------------------- weather
@mcp.tool()
def weather(place: str = "", days: int = 1) -> str:
    """Current weather and the forecast (days 1-7) for a place; empty place = the user's home city."""
    place = place.strip() or CFG.get("city", "")
    if not place:
        return "I don't know your city yet. Ask the user which city, and use it as the place."
    geo = json.loads(fetch("https://geocoding-api.open-meteo.com/v1/search?count=1&name=" + urllib.parse.quote(place)))
    if not geo.get("results"):
        return f"I couldn't find a place called {place}."
    g = geo["results"][0]
    q = urllib.parse.urlencode({
        "latitude": g["latitude"], "longitude": g["longitude"], "timezone": "auto", "forecast_days": max(1, min(days, 7)),
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code"})
    w = json.loads(fetch("https://api.open-meteo.com/v1/forecast?" + q))
    c, d = w["current"], w["daily"]
    out = [f"{g['name']}, {g.get('admin1', g.get('country', ''))}: now {c['temperature_2m']:.0f}°C "
           f"(feels {c['apparent_temperature']:.0f}°C), {WMO.get(c['weather_code'], 'unknown')}, "
           f"humidity {c['relative_humidity_2m']}%, wind {c['wind_speed_10m']:.0f} km/h."]
    for i, day in enumerate(d["time"]):
        name = "Today" if i == 0 else "Tomorrow" if i == 1 else datetime.date.fromisoformat(day).strftime("%A")
        out.append(f"{name}: {d['temperature_2m_min'][i]:.0f}-{d['temperature_2m_max'][i]:.0f}°C, "
                   f"{WMO.get(d['weather_code'][i], 'unknown')}, rain chance {d['precipitation_probability_max'][i]}%.")
    return " ".join(out)


# ---------------------------------------------------------------- news
@mcp.tool()
def news(topic: str = "", count: int = 5) -> str:
    """Latest news headlines, top stories (empty topic) or about a topic, e.g. "cricket", "AI", "world", "Chennai"."""
    region = CFG.get("news_region", "hl=en-IN&gl=IN&ceid=IN:en")
    url = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(topic) + "&" + region) if topic.strip() \
        else "https://news.google.com/rss?" + region
    items = ET.fromstring(fetch(url)).findall("./channel/item")[:max(1, min(count, 10))]
    if not items:
        return f"No news found about {topic}."
    lines = []
    for it in items:
        title = it.findtext("title", "")
        source = it.findtext("source", "")
        when = it.findtext("pubDate", "")[5:16]
        lines.append(f"- {title.rsplit(' - ', 1)[0]} ({source}, {when})")
    return ("Top stories" if not topic else f"News about {topic}") + ":\n" + "\n".join(lines)


# ---------------------------------------------------------------- reminders and timers
def _due(when):
    when = when.strip().replace(" ", "T")
    dt = datetime.datetime.fromisoformat(when)
    return dt.replace(tzinfo=None)


@mcp.tool()
def set_reminder(text: str, when: str, repeat: str = "") -> str:
    """Remind the user of something at a time. when: local date and time as ISO, e.g. "2026-10-03T18:00" (work it out
    from the current time). repeat: "" or "daily". Karen says it out loud when it is due."""
    try:
        due = _due(when)
    except ValueError:
        return "The time must look like 2026-10-03T18:00."
    if due < datetime.datetime.now() - datetime.timedelta(minutes=1):
        return "That time has already passed."
    items = load(REMINDERS, [])
    rid = max([r["id"] for r in items] or [0]) + 1
    items.append({"id": rid, "text": text, "due": due.isoformat(timespec="minutes"), "repeat": repeat or None,
                  "done": False})
    save(REMINDERS, items)
    return f"Reminder {rid} set for {due:%A %d %B at %I:%M %p}" + (", every day" if repeat else "") + f": {text}."


@mcp.tool()
def set_timer(minutes: float, label: str = "timer") -> str:
    """Start a countdown timer, e.g. 10 minutes for "tea". Karen announces it when it ends."""
    due = datetime.datetime.now() + datetime.timedelta(minutes=minutes)
    items = load(REMINDERS, [])
    rid = max([r["id"] for r in items] or [0]) + 1
    items.append({"id": rid, "text": f"Your {label} is done", "due": due.isoformat(timespec="seconds"),
                  "repeat": None, "done": False, "timer": True})
    save(REMINDERS, items)
    return f"Timer set: {label}, {minutes:g} minutes, ends at {due:%I:%M:%S %p}."


@mcp.tool()
def list_reminders() -> str:
    """Upcoming reminders and timers."""
    items = sorted((r for r in load(REMINDERS, []) if not r["done"]), key=lambda r: r["due"])
    if not items:
        return "No reminders or timers set."
    return "\n".join(f"{r['id']}. {datetime.datetime.fromisoformat(r['due']):%a %d %b %I:%M %p}: {r['text']}"
                     + (" (daily)" if r.get("repeat") else "") for r in items)


@mcp.tool()
def cancel_reminder(which: str) -> str:
    """Cancel a reminder or timer by its number or by words from its text ("all" cancels everything)."""
    items = load(REMINDERS, [])
    open_items = [r for r in items if not r["done"]]
    hit = open_items if which.strip().lower() == "all" else \
        [r for r in open_items if str(r["id"]) == which.strip() or which.lower() in r["text"].lower()]
    for r in hit:
        r["done"] = True
    save(REMINDERS, items)
    return f"Cancelled {len(hit)}: " + "; ".join(r["text"] for r in hit) if hit else f"No reminder matches '{which}'."


# ---------------------------------------------------------------- long-term memory
@mcp.tool()
def remember(fact: str) -> str:
    """Store a fact about the user for later, e.g. "their locker code is 2468", "their sister's birthday is 3 March"."""
    facts = load(MEMORY, [])
    facts.append({"fact": fact.strip(), "saved": f"{datetime.date.today():%Y-%m-%d}"})
    save(MEMORY, facts)
    return f"Got it, I'll remember that {fact.strip()}."


@mcp.tool()
def set_mode(mode: str) -> str:
    """Karen's personality mode: "work" (full work mode: focused, no romance), "love" (affectionate girlfriend) or
    "both" (light love with work focus). Not the work_mode that opens apps and windows."""
    key = persona.set_mode(mode)
    if not key:
        return "The modes are work, both and love."
    return {"work": "Work mode on: focused and efficient, no romance.",
            "both": "Both mode on: work first, with a little love.",
            "love": "Love mode on."}[key] + " Say it in that new style from now on."


@mcp.tool()
def set_persona(how: str) -> str:
    """Change how Karen talks to the user from now on, everywhere and every time, in their own words (e.g. "like a
    strict coach"). For work / love / both use set_mode. Replaces the previous one."""
    persona.custom(how)
    return f"From now on I'll talk {how.strip()}. It applies to every reply until they change it or says to be normal."


@mcp.tool()
def clear_persona() -> str:
    """Go back to Karen's normal way of talking (remove the personality or mode they set)."""
    persona.clear()
    return "Back to normal Karen."


@mcp.tool()
def recall(about: str = "") -> str:
    """Look up what the user asked Karen to remember. about: a few words, or empty for everything."""
    facts = load(MEMORY, [])
    words = [w for w in about.lower().split() if len(w) > 2]
    hits = [f for f in facts if not words or any(w in f["fact"].lower() for w in words)] or (facts if not about else [])
    if not hits:
        return "Nothing remembered about that." + (f" ({len(facts)} other things saved.)" if facts else "")
    return "\n".join(f"- {f['fact']} (saved {f['saved']})" for f in hits[-30:])


@mcp.tool()
def forget(fact: str) -> str:
    """Delete remembered facts that contain these words."""
    facts = load(MEMORY, [])
    keep = [f for f in facts if fact.lower() not in f["fact"].lower()]
    save(MEMORY, keep)
    gone = len(facts) - len(keep)
    return f"Forgot {gone} thing{'s' if gone != 1 else ''}." if gone else f"Nothing remembered matches '{fact}'."


# ---------------------------------------------------------------- notes and to-do list
NOTES = os.path.join(DATA, "notes.json")


@mcp.tool()
def add_todo(item: str) -> str:
    """Add something to the user's to-do list."""
    data = load(NOTES, {"todos": [], "notes": []})
    data["todos"].append({"item": item.strip(), "done": False, "added": f"{datetime.date.today():%Y-%m-%d}"})
    save(NOTES, data)
    return f"Added to your to-do list: {item.strip()}."


@mcp.tool()
def todo_list(include_done: bool = False) -> str:
    """The user's to-do list."""
    todos = load(NOTES, {"todos": []})["todos"]
    shown = [t for t in todos if include_done or not t["done"]]
    if not shown:
        return "Your to-do list is empty."
    return "\n".join(f"{i}. {'[done] ' if t['done'] else ''}{t['item']}" for i, t in enumerate(todos, 1) if t in shown)


@mcp.tool()
def complete_todo(which: str) -> str:
    """Tick off a to-do item by its number or words from it ("all" ticks everything)."""
    data = load(NOTES, {"todos": [], "notes": []})
    hits = [t for i, t in enumerate(data["todos"], 1) if not t["done"] and
            (which.strip().lower() == "all" or which.strip() == str(i) or which.lower() in t["item"].lower())]
    for t in hits:
        t["done"] = True
    data["todos"] = [t for t in data["todos"] if not t["done"]] + [t for t in data["todos"] if t["done"]][-50:]
    save(NOTES, data)
    return "Done: " + "; ".join(t["item"] for t in hits) if hits else f"Nothing on the list matches '{which}'."


@mcp.tool()
def add_note(text: str) -> str:
    """Save a quick note ("note this down: ...")."""
    data = load(NOTES, {"todos": [], "notes": []})
    data["notes"].append({"text": text.strip(), "at": f"{datetime.datetime.now():%Y-%m-%d %H:%M}"})
    save(NOTES, data)
    return "Noted."


@mcp.tool()
def list_notes(about: str = "") -> str:
    """The user's saved notes, newest first, optionally only those containing some words."""
    notes = load(NOTES, {"notes": []})["notes"]
    hits = [n for n in notes if not about or about.lower() in n["text"].lower()]
    return "\n".join(f"- ({n['at']}) {n['text']}" for n in reversed(hits[-30:])) or "No notes saved."


@mcp.tool()
def show_lists() -> str:
    """Show the to-do list and notes as a page in Karen's viewer."""
    import html
    data = load(NOTES, {"todos": [], "notes": []})
    todo = "".join(f"<li class='{'done' if t['done'] else ''}'>{html.escape(t['item'])}</li>" for t in data["todos"])
    notes = "".join(f"<li><small>{n['at']}</small> {html.escape(n['text'])}</li>" for n in reversed(data["notes"]))
    page = os.path.join(DATA, "lists.html")
    save_text = (f"<!doctype html><meta charset='utf-8'><title>Karen - lists</title><style>body{{font-family:Segoe UI;"
                 f"background:#0b0416;color:#f1e9ff;padding:30px;max-width:760px;margin:auto}}h1{{color:#9d4eff}}"
                 f"li{{margin:8px 0}}.done{{text-decoration:line-through;opacity:.5}}small{{color:#a08cc8}}</style>"
                 f"<h1>To-do</h1><ul>{todo or '<li>Nothing to do.</li>'}</ul><h1>Notes</h1>"
                 f"<ul>{notes or '<li>No notes.</li>'}</ul>")
    os.makedirs(DATA, exist_ok=True)
    open(page, "w", encoding="utf-8").write(save_text)
    return f"Showing your lists.\n[[show: {page}]]"


# ---------------------------------------------------------------- briefing
@mcp.tool()
def briefing() -> str:
    """Morning briefing parts: home weather, today's reminders and top news. Also call the calendar tool for today's
    events and Gmail for important unread mail, then give ONE short spoken summary."""
    parts = []
    for name, part in (("Weather", lambda: weather()), ("News", lambda: news(count=4))):
        try:
            parts.append(f"{name}: {part()}")
        except Exception as e:
            parts.append(f"{name}: unavailable ({type(e).__name__})")
    today = datetime.date.today().isoformat()
    todays = [r for r in load(REMINDERS, []) if not r["done"] and r["due"].startswith(today)]
    parts.append("Reminders today: " + ("; ".join(f"{r['due'][11:16]} {r['text']}" for r in todays) or "none"))
    return "\n".join(parts)


if __name__ == "__main__":
    mcp.run()
