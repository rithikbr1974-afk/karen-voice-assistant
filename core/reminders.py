"""When a reminder is due next (shared by voice Karen's ticker and Karen Remote).

Reminders live in data/reminders.json (written by the daily and jarvis servers). "repeat" is None (once), "daily",
or "every:N" (every N minutes, e.g. drink water every 45 min), optionally limited by "window": "09:00-22:00"
(no water reminders at 3 am). Missed repeats are skipped, not announced one by one.
"""
import datetime


def _in_window(t, window):
    if not window:
        return True
    start, end = (datetime.time.fromisoformat(x.strip()) for x in window.split("-"))
    return start <= t.time() <= end if start <= end else (t.time() >= start or t.time() <= end)


def advance(r, now=None):
    """After a reminder went off: move it to its next time, or mark it done. Changes r in place."""
    now = now or datetime.datetime.now()
    due = datetime.datetime.fromisoformat(r["due"])
    repeat = r.get("repeat") or ""
    if repeat == "daily":
        step = datetime.timedelta(days=1)
    elif repeat.startswith("every:"):
        step = datetime.timedelta(minutes=max(1, int(repeat.split(":")[1])))
    else:
        r["done"] = True
        return r
    while due <= now:
        due += step
    for _ in range(2000):  # skip the hours outside his window (e.g. the night)
        if _in_window(due, r.get("window")):
            break
        due += step
    r["due"] = due.isoformat(timespec="minutes")
    return r


def snooze(item, minutes=5, path="data/reminders.json"):
    """The island's Snooze: the same reminder or timer again in a few minutes, as a new one-off item."""
    import json
    import os
    try:
        with open(path, encoding="utf-8") as f:
            items = json.load(f)
    except (OSError, ValueError):
        items = []
    now = datetime.datetime.now()
    items.append({"id": max([r.get("id", 0) for r in items] or [0]) + 1, "text": item.get("text", "Reminder"),
                  "due": (now + datetime.timedelta(minutes=minutes)).isoformat(timespec="seconds"), "repeat": None,
                  "done": False, "timer": bool(item.get("timer")), "set": now.isoformat(timespec="seconds")})
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(items, f, indent=1, ensure_ascii=False)
    os.replace(path + ".tmp", path)
