"""Calendar events out of workspace-mcp's get_events text (Karen Remote's heads-up before each event, briefings).

    - "Lab review" (Starts: 2026-11-13T10:30:00+05:30 [Asia/Kolkata; weekday: Friday; ISO weekday: 5], Ends: ...)
      Meeting: https://meet.google.com/abc ID: 5h3k... | Link: https://www.google.com/calendar/event?eid=...
"""
import datetime
import re

LINE = re.compile(r'^- "(?P<title>.*)" \(Starts: (?P<start>[^\s,\[]+)')


def day_range(day):
    """time_min / time_max for one local day, with this PC's UTC offset. A bare date ("2026-11-13") is read by the
    server as UTC midnight, which is 05:30 here: the wrap-up missed events before 05:30 and took in the next day's."""
    start = datetime.datetime.combine(day, datetime.time()).astimezone()
    return start.isoformat(timespec="seconds"), (start + datetime.timedelta(days=1)).isoformat(timespec="seconds")


def stamp(when):
    """A local time as the server wants it: RFC3339 with this PC's offset."""
    return when.astimezone().isoformat(timespec="seconds")


def events(text):
    """[{title, start (local, no zone), id, meet}] for events with a start time; all-day events are left out."""
    out = []
    for line in (text or "").splitlines():
        m = LINE.match(line.strip())
        if not m or "T" not in m["start"]:
            continue
        try:
            start = datetime.datetime.fromisoformat(m["start"].replace("Z", "+00:00"))
        except ValueError:
            continue
        if start.tzinfo:
            start = start.astimezone().replace(tzinfo=None)
        eid, meet = re.search(r"\bID: (\S+)", line), re.search(r"\bMeeting: (\S+)", line)
        out.append({"title": m["title"].strip() or "Untitled event", "start": start,
                    "id": eid[1] if eid else m["title"], "meet": meet[1] if meet else ""})
    return out
