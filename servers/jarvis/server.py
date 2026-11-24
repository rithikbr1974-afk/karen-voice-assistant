"""JARVIS features rebuilt for Karen, as an MCP server (stdio).

Ideas taken from the MIT-licensed JARVIS AI Assistant (github.com/Avinashb722/jarvis-ai-assistant), rewritten small
and safe: Karen speaks, Karen's confirm rules apply, no shell commands, no running generated code.

Batch 1: health & wellness (repeating reminders with quiet hours, Pomodoro, HIIT, BMI, calories, mood log) and quick
productivity tools (password generator to the clipboard, text tools on the clipboard, URL shortener).
Reminders and timers go into data/reminders.json, which voice Karen and Karen Remote announce.
"""
import datetime
import json
import os
import secrets
import string
import time
import urllib.parse
import urllib.request

from mcp.server import MCPServer

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DATA = os.path.join(ROOT, "data")
REMINDERS = os.path.join(DATA, "reminders.json")
HEALTH = os.path.join(DATA, "health.json")
mcp = MCPServer("jarvis")


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    os.replace(path + ".tmp", path)


def add_reminders(entries):
    """entries: dicts with text, due (datetime), and optional repeat/window/timer/tag. Returns their ids."""
    items = load(REMINDERS, [])
    rid = max([r["id"] for r in items] or [0])
    ids = []
    for e in entries:
        rid += 1
        due = e.pop("due")
        items.append({"id": rid, "text": e.pop("text"), "due": due.isoformat(timespec="seconds"), "repeat": None,
                      "done": False, **e})
        ids.append(rid)
    save(REMINDERS, items)
    return ids


def remove_tagged(tag):
    items = load(REMINDERS, [])
    keep = [r for r in items if r.get("tag") != tag or r.get("done")]
    save(REMINDERS, keep)
    return len(items) - len(keep)


# ---------------------------------------------------------------- health & wellness
WELLNESS = {  # kind -> (default minutes, what Karen says)
    "water": (60, "Time to drink a glass of water."),
    "eyes": (20, "Eye break: look at something 20 feet away for 20 seconds."),
    "stretch": (60, "Stand up and stretch for a minute."),
    "posture": (45, "Posture check: sit up straight and relax your shoulders."),
    "break": (50, "Take a five minute break away from the screen."),
}


@mcp.tool()
def wellness_reminder(kind: str, every_minutes: int = 0, start: str = "09:00", end: str = "22:00") -> str:
    """Repeating health reminder: kind = water, eyes, stretch, posture or break. every_minutes: 0 = a sensible
    default. Only between start and end (24 h "HH:MM"), so not at night. Replaces an earlier one of the same kind."""
    kind = kind.lower().strip()
    if kind not in WELLNESS:
        return f"Kinds: {', '.join(WELLNESS)}. For medicine use medicine_reminder."
    minutes, text = WELLNESS[kind]
    minutes = every_minutes or minutes
    remove_tagged(f"wellness:{kind}")
    first = datetime.datetime.now() + datetime.timedelta(minutes=minutes)
    add_reminders([{"text": text, "due": first, "repeat": f"every:{minutes}", "window": f"{start}-{end}",
                    "tag": f"wellness:{kind}"}])
    return f"{kind.capitalize()} reminder every {minutes} minutes, between {start} and {end}."


@mcp.tool()
def medicine_reminder(name: str, times: str) -> str:
    """Daily medicine reminder. times: 24 h times separated by commas, e.g. "08:00, 20:30"."""
    now = datetime.datetime.now()
    entries = []
    for t in [x.strip() for x in times.split(",") if x.strip()]:
        try:
            hh, mm = (int(v) for v in t.split(":"))
        except ValueError:
            return f"I couldn't read the time '{t}'. Use 24 h times like 08:00, 20:30."
        due = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if due <= now:
            due += datetime.timedelta(days=1)
        entries.append({"text": f"Time to take your {name}.", "due": due, "repeat": "daily", "tag": f"medicine:{name.lower()}"})
    said = ", ".join(e["due"].strftime("%I:%M %p") for e in entries)
    add_reminders(entries)
    return f"I'll remind you to take {name} every day at {said}."


@mcp.tool()
def stop_wellness(kind: str) -> str:
    """Stop a repeating health reminder (water, eyes, stretch, posture, break) or a medicine (its name),
    or "pomodoro" / "hiit" to cancel a running session."""
    k = kind.lower().strip()
    n = sum(remove_tagged(t) for t in (f"wellness:{k}", f"medicine:{k}", k))
    return f"Stopped {n} {k} reminder{'s' if n != 1 else ''}." if n else f"No {k} reminders were set."


@mcp.tool()
def pomodoro(work_minutes: int = 25, break_minutes: int = 5, rounds: int = 4, long_break_minutes: int = 15) -> str:
    """Start a Pomodoro focus session: work / short break cycles, with a long break at the end. Karen announces
    each change. Cancel with stop_wellness("pomodoro")."""
    remove_tagged("pomodoro")
    t = datetime.datetime.now()
    entries = []
    for i in range(1, rounds + 1):
        t += datetime.timedelta(minutes=work_minutes)
        last = i == rounds
        rest = long_break_minutes if last else break_minutes
        entries.append({"text": f"Pomodoro {i} of {rounds} done. Take a {rest} minute {'long ' if last else ''}break.",
                        "due": t, "timer": True, "tag": "pomodoro"})
        t += datetime.timedelta(minutes=rest)
        entries.append({"text": "Break's over. Pomodoro session complete, great work!" if last else
                        f"Break's over. Focus round {i + 1} of {rounds}, {work_minutes} minutes.",
                        "due": t, "timer": True, "tag": "pomodoro"})
    add_reminders(entries)
    return (f"Pomodoro started: {rounds} rounds of {work_minutes} minutes with {break_minutes} minute breaks. "
            f"It ends at {t:%I:%M %p}. Focus now.")


@mcp.tool()
def hiit(rounds: int = 8, work_seconds: int = 40, rest_seconds: int = 20, exercise: str = "") -> str:
    """Start a HIIT workout timer: Karen calls out work and rest for each round. exercise: e.g. "squats" (optional).
    Cancel with stop_wellness("hiit")."""
    remove_tagged("hiit")
    what = exercise or "work"
    t = datetime.datetime.now() + datetime.timedelta(seconds=5)
    entries = [{"text": f"Round 1 of {rounds}: {what}! Go!", "due": t, "timer": True, "tag": "hiit"}]
    for i in range(1, rounds + 1):
        t += datetime.timedelta(seconds=work_seconds)
        if i == rounds:
            entries.append({"text": "Done! Workout complete. Great job, cool down and drink some water.",
                            "due": t, "timer": True, "tag": "hiit"})
            break
        entries.append({"text": f"Rest {rest_seconds} seconds.", "due": t, "timer": True, "tag": "hiit"})
        t += datetime.timedelta(seconds=rest_seconds)
        entries.append({"text": f"Round {i + 1} of {rounds}: {what}! Go!", "due": t, "timer": True, "tag": "hiit"})
    add_reminders(entries)
    total = rounds * work_seconds + (rounds - 1) * rest_seconds
    return f"HIIT starts in 5 seconds: {rounds} rounds of {work_seconds} s {what}, {rest_seconds} s rest, " \
           f"about {total // 60} min {total % 60} s."


@mcp.tool()
def bmi(weight_kg: float, height_cm: float) -> str:
    """Body mass index from weight (kg) and height (cm), with the WHO category (and the Asian cut-offs)."""
    v = weight_kg / (height_cm / 100) ** 2
    cat = "underweight" if v < 18.5 else "normal" if v < 25 else "overweight" if v < 30 else "obese"
    asian = "underweight" if v < 18.5 else "normal" if v < 23 else "overweight" if v < 27.5 else "obese"
    healthy = (18.5 * (height_cm / 100) ** 2, 24.9 * (height_cm / 100) ** 2)
    return (f"BMI {v:.1f}: {cat} (WHO); for South Asians the cut-offs are lower, which makes it {asian}. "
            f"A healthy weight for {height_cm:.0f} cm is about {healthy[0]:.0f} to {healthy[1]:.0f} kg.")


@mcp.tool()
def calories(weight_kg: float, height_cm: float, age: int, sex: str, activity: str = "light") -> str:
    """Daily calorie needs (Mifflin-St Jeor). sex: male/female. activity: sedentary, light, moderate, active,
    very active."""
    base = 10 * weight_kg + 6.25 * height_cm - 5 * age + (5 if sex.lower().startswith("m") else -161)
    factor = {"sedentary": 1.2, "light": 1.375, "moderate": 1.55, "active": 1.725, "very active": 1.9}.get(
        activity.lower(), 1.375)
    tdee = base * factor
    return (f"About {tdee:.0f} calories a day to keep your weight ({activity} activity). To lose about half a kilo "
            f"a week: {tdee - 500:.0f}; to gain: {tdee + 300:.0f}. Resting burn: {base:.0f}.")


@mcp.tool()
def log_mood(mood: int, note: str = "") -> str:
    """Log how the user feels, mood 1 (awful) to 10 (great), with an optional note."""
    data = load(HEALTH, {})
    data.setdefault("mood", []).append({"at": f"{datetime.datetime.now():%Y-%m-%d %H:%M}",
                                        "mood": max(1, min(10, int(mood))), "note": note[:200]})
    save(HEALTH, data)
    return f"Logged mood {mood}/10" + (f": {note}" if note else "") + "."


@mcp.tool()
def mood_summary(days: int = 7) -> str:
    """The user's mood over the last days: average, best and worst moments."""
    since = datetime.datetime.now() - datetime.timedelta(days=days)
    rows = [m for m in load(HEALTH, {}).get("mood", []) if datetime.datetime.fromisoformat(m["at"]) >= since]
    if not rows:
        return f"No moods logged in the last {days} days."
    avg = sum(m["mood"] for m in rows) / len(rows)
    best, worst = max(rows, key=lambda m: m["mood"]), min(rows, key=lambda m: m["mood"])
    return (f"{len(rows)} entries in {days} days, average {avg:.1f}/10. Best {best['mood']} on {best['at'][:10]}"
            + (f" ({best['note']})" if best["note"] else "") + f"; lowest {worst['mood']} on {worst['at'][:10]}"
            + (f" ({worst['note']})" if worst["note"] else "") + ".")


# ---------------------------------------------------------------- quick productivity
def _clip_get():
    import pyperclip
    for _ in range(10):
        try:
            return pyperclip.paste()
        except Exception:
            time.sleep(0.2)
    return ""


def _clip_set(text):
    import pyperclip
    for _ in range(10):
        try:
            pyperclip.copy(text)
            return True
        except Exception:
            time.sleep(0.2)
    return False


@mcp.tool()
def password_generator(length: int = 16, symbols: bool = True) -> str:
    """Make a strong random password and put it on the clipboard. It is never shown or stored."""
    length = max(8, min(64, length))
    pool = string.ascii_letters + string.digits + ("!@#$%^&*-_=+?" if symbols else "")
    while True:
        pw = "".join(secrets.choice(pool) for _ in range(length))
        if (any(c.islower() for c in pw) and any(c.isupper() for c in pw) and any(c.isdigit() for c in pw)
                and (not symbols or any(not c.isalnum() for c in pw))):
            break
    ok = _clip_set(pw)
    return (f"A {length}-character password is on your clipboard (not shown or saved, for safety). Paste it with "
            f"Ctrl+V." if ok else "I couldn't reach the clipboard; try again.")


@mcp.tool()
def text_tool(action: str) -> str:
    """Work on the text on the user's clipboard: count (words, characters, reading time), clean (tidy spaces and
    blank lines), upper, lower, title, sentence. The result goes back on the clipboard."""
    text = _clip_get()
    if not text.strip():
        return "The clipboard has no text. Copy some text first."
    a = action.lower().strip()
    if a == "count":
        words = len(text.split())
        return (f"{words} words, {len(text)} characters, {text.count(chr(10)) + 1} lines; about "
                f"{max(1, round(words / 200))} min to read.")
    import re
    out = {"clean": lambda s: "\n".join(re.sub(r"[ \t]+", " ", ln).strip() for ln in re.sub(r"\n{3,}", "\n\n", s).split("\n")),
           "upper": str.upper, "lower": str.lower, "title": str.title,
           "sentence": lambda s: re.sub(r"(^|[.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), s.lower())}.get(a)
    if not out:
        return "Actions: count, clean, upper, lower, title, sentence."
    _clip_set(out(text))
    return f"Done: the {a} version of your text is on the clipboard."


SHORTENERS = ["https://tinyurl.com/api-create.php?url=",  # free, no account; is.gd timed out on their network
              "https://is.gd/create.php?format=simple&url="]


@mcp.tool()
def shorten_url(url: str) -> str:
    """Make a short link (TinyURL, free, no account) and put it on the clipboard."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    for api in SHORTENERS:
        try:
            req = urllib.request.Request(api + urllib.parse.quote(url, safe=""), headers={"User-Agent": "Mozilla/5.0"})
            short = urllib.request.urlopen(req, timeout=12).read().decode().strip()
        except Exception:
            continue
        if short.startswith("http"):
            _clip_set(short)
            return f"Short link {short} is on your clipboard."
    return "The link shorteners didn't answer. Try again later."


# ---------------------------------------------------------------- batch 2: files & PDF power tools
# Same safety as Karen's files server: only their user folders and uploads (its resolve()); outputs to Documents/Karen.
import hashlib  # noqa: E402
import importlib.util  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import zipfile  # noqa: E402

_spec = importlib.util.spec_from_file_location("karen_files", os.path.join(ROOT, "servers", "files", "server.py"))
files = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(files)
resolve, out_path, size, pages_list, guarded = files.resolve, files.out_path, files.size, files.pages_list, files.guarded
OUT = files.OUT


def _pdf(path):
    from pypdf import PdfReader
    full = resolve(path)
    if not full.lower().endswith(".pdf"):
        raise ValueError("That isn't a PDF.")
    return full, PdfReader(full)


def _write(writer, full, suffix):
    out = out_path(os.path.basename(full)[:-4] + suffix, ".pdf")
    with open(out, "wb") as f:
        writer.write(f)
    return out


@mcp.tool()
@guarded
def pdf_lock(path: str, password: str) -> str:
    """Put a password on a PDF (AES-256). Saves a locked copy in Documents/Karen; the original is kept."""
    from pypdf import PdfWriter
    full, reader = _pdf(path)
    w = PdfWriter(clone_from=reader)
    w.encrypt(user_password=password, owner_password=password, algorithm="AES-256")
    out = _write(w, full, " (locked)")
    return f"Locked copy saved: {out}. It opens only with the password."


@mcp.tool()
@guarded
def pdf_unlock(path: str, password: str) -> str:
    """Remove the password from a PDF they own (needs the right password). Saves an unlocked copy."""
    from pypdf import PdfReader, PdfWriter
    full = resolve(path)
    reader = PdfReader(full)
    if reader.is_encrypted and not reader.decrypt(password):
        return "That password is wrong for this PDF."
    out = _write(PdfWriter(clone_from=reader), full, " (unlocked)")
    return f"Unlocked copy saved: {out}."


@mcp.tool()
@guarded
def pdf_compress(path: str, image_quality: int = 60) -> str:
    """Make a PDF smaller (recompresses images and page streams). Saves a copy and reports the sizes."""
    from pypdf import PdfWriter
    full, reader = _pdf(path)
    w = PdfWriter(clone_from=reader)
    for page in w.pages:
        for img in page.images:
            try:
                img.replace(img.image, quality=max(20, min(90, image_quality)))
            except Exception:
                pass  # some image types can't be re-encoded; keep them
        page.compress_content_streams()
    w.compress_identical_objects(remove_identicals=True, remove_orphans=True)
    out = _write(w, full, " (smaller)")
    a, b = os.path.getsize(full), os.path.getsize(out)
    return f"Saved {out}: {size(a)} -> {size(b)} ({100 - b * 100 // max(1, a)}% smaller)."


@mcp.tool()
@guarded
def pdf_rotate(path: str, degrees: int = 90, pages: str = "") -> str:
    """Rotate PDF pages clockwise by 90/180/270 degrees. pages: e.g. "1-3,5" (empty = all)."""
    from pypdf import PdfWriter
    full, reader = _pdf(path)
    w = PdfWriter(clone_from=reader)
    chosen = pages_list(pages, len(w.pages))
    for i in chosen:
        w.pages[i].rotate(int(degrees) % 360)
    out = _write(w, full, " (rotated)")
    return f"Rotated {len(chosen)} page(s) by {degrees}°: {out}."


def _watermark_pdf(text, width, height):
    """A one-page PDF with big faint diagonal text (hand-written PDF: no extra library needed)."""
    import io
    t = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")[:60]
    fs = max(24, min(90, int(min(width, height) / max(4, len(t)) * 1.7)))
    stream = (f"q /GS1 gs 0.5 0.5 0.5 rg BT /F1 {fs} Tf 0.707 0.707 -0.707 0.707 {width * 0.18:.0f} {height * 0.25:.0f} Tm "
              f"({t}) Tj ET Q").encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width:.0f} {height:.0f}] /Contents 4 0 R "
             f"/Resources << /Font << /F1 5 0 R >> /ExtGState << /GS1 6 0 R >> >> >>").encode(),
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>", b"<< /Type /ExtGState /ca 0.18 /CA 0.18 >>"]
    buf = io.BytesIO(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(buf.tell())
        buf.write(b"%d 0 obj\n" % i + o + b"\nendobj\n")
    xref = buf.tell()
    buf.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offsets))
    buf.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref))
    buf.seek(0)
    return buf


@mcp.tool()
@guarded
def pdf_watermark(path: str, text: str = "CONFIDENTIAL") -> str:
    """Stamp faint diagonal text (e.g. CONFIDENTIAL, DRAFT, their name) across every page of a PDF. Saves a copy."""
    from pypdf import PdfReader, PdfWriter
    full, reader = _pdf(path)
    w = PdfWriter(clone_from=reader)
    for page in w.pages:
        box = page.mediabox
        mark = PdfReader(_watermark_pdf(text, float(box.width), float(box.height))).pages[0]
        page.merge_page(mark, over=True)
    out = _write(w, full, " (watermarked)")
    return f"Watermarked every page with \"{text}\": {out}.\n[[show: {out}]]"


@mcp.tool()
@guarded
def zip_files(paths: str, name: str = "") -> str:
    """Pack files or folders into one .zip in Documents/Karen. paths: separated by ";" or "|"."""
    items = [resolve(p) for p in re.split(r"[;|]", paths) if p.strip()]
    if not items:
        return "Which files should go in the zip?"
    out = out_path(name or os.path.basename(items[0]), ".zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for it in items:
            if os.path.isdir(it):
                for root, _, fs in os.walk(it):
                    for f in fs:
                        p = os.path.join(root, f)
                        z.write(p, os.path.relpath(p, os.path.dirname(it)))
            else:
                z.write(it, os.path.basename(it))
    return f"Zipped {len(items)} item(s) into {out} ({size(os.path.getsize(out))})."


@mcp.tool()
@guarded
def unzip(path: str) -> str:
    """Extract a .zip into a new folder next to it (never overwrites, never writes outside that folder)."""
    full = resolve(path)
    if not zipfile.is_zipfile(full):
        return "That isn't a zip file."
    dest = os.path.splitext(full)[0]
    n = 2
    while os.path.exists(dest):
        dest = f"{os.path.splitext(full)[0]} ({n})"
        n += 1
    with zipfile.ZipFile(full) as z:
        for m in z.infolist():
            target = os.path.abspath(os.path.join(dest, m.filename))
            if not target.startswith(os.path.abspath(dest) + os.sep):  # "zip slip": a file trying to escape the folder
                return f"Stopped: {m.filename} tries to write outside the folder. The zip may be malicious."
        z.extractall(dest)
        count = len(z.infolist())
    return f"Extracted {count} item(s) into {dest}."


def _walk(folder, limit=20000):
    for root, dirs, fs in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith((".", "$")) and d not in ("node_modules", "__pycache__", ".git")]
        for f in fs:
            yield os.path.join(root, f)
            limit -= 1
            if limit <= 0:
                return


@mcp.tool()
@guarded
def find_duplicates(folder: str) -> str:
    """Find duplicate files (identical content) in a folder, e.g. Downloads. Reports them; deletes nothing."""
    root = resolve(folder)
    by_size = {}
    for p in _walk(root):
        try:
            s = os.path.getsize(p)
        except OSError:
            continue
        if s > 1024:
            by_size.setdefault(s, []).append(p)
    groups = []
    for s, ps in by_size.items():
        if len(ps) < 2:
            continue
        by_hash = {}
        for p in ps:
            h = hashlib.sha1()
            with open(p, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            by_hash.setdefault(h.hexdigest(), []).append(p)
        groups += [(s, g) for g in by_hash.values() if len(g) > 1]
    if not groups:
        return f"No duplicate files in {root}."
    groups.sort(key=lambda g: -g[0] * (len(g[1]) - 1))
    waste = sum(s * (len(g) - 1) for s, g in groups)
    lines = [f"{len(groups)} sets of duplicates, {size(waste)} wasted. Biggest:"]
    for s, g in groups[:8]:
        lines.append(f"- {size(s)} x{len(g)}: " + " | ".join(os.path.relpath(p, root) for p in g[:3]))
    return "\n".join(lines) + "\n(Karen's delete_file moves one to the Recycle Bin if they ask.)"


@mcp.tool()
@guarded
def folder_size(folder: str) -> str:
    """How big a folder is, and which subfolders and files take the most space."""
    root = resolve(folder)
    total, sub = 0, {}
    for p in _walk(root, limit=60000):
        try:
            s = os.path.getsize(p)
        except OSError:
            continue
        total += s
        rel = os.path.relpath(p, root).split(os.sep)
        key = rel[0] + (os.sep if len(rel) > 1 else "")
        sub[key] = sub.get(key, 0) + s
    top = sorted(sub.items(), key=lambda kv: -kv[1])[:8]
    return f"{root}: {size(total)}. Biggest: " + "; ".join(f"{k} {size(v)}" for k, v in top)


KINDS = {"Images": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic", ".svg"},
         "Documents": {".pdf", ".doc", ".docx", ".txt", ".rtf", ".odt", ".ppt", ".pptx", ".xls", ".xlsx", ".csv", ".md"},
         "Videos": {".mp4", ".mkv", ".mov", ".avi", ".webm"}, "Music": {".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg"},
         "Archives": {".zip", ".rar", ".7z", ".tar", ".gz"}, "Installers": {".exe", ".msi", ".apk"},
         "Code": {".py", ".js", ".html", ".css", ".java", ".c", ".cpp", ".ino", ".json", ".ipynb"}}


def _tidy_plan(root):
    plan = []
    for f in sorted(os.listdir(root)):
        p = os.path.join(root, f)
        if not os.path.isfile(p) or f.startswith((".", "~$")) or f.lower() == "desktop.ini":
            continue
        ext = os.path.splitext(f)[1].lower()
        kind = next((k for k, exts in KINDS.items() if ext in exts), "Others")
        plan.append((p, os.path.join(root, kind, f)))
    return plan


@mcp.tool()
@guarded
def tidy_folder(folder: str) -> str:
    """Preview tidying a messy folder (e.g. Downloads, Desktop): which files would move into Images, Documents,
    Videos, Music, Archives, Installers, Code, Others. Moves nothing; tidy_folder_apply does it after they agree."""
    root = resolve(folder)
    plan = _tidy_plan(root)
    if not plan:
        return f"{root} has no loose files to tidy."
    counts = {}
    for _, dst in plan:
        counts[os.path.basename(os.path.dirname(dst))] = counts.get(os.path.basename(os.path.dirname(dst)), 0) + 1
    return (f"Tidying {root} would move {len(plan)} files: " + ", ".join(f"{v} to {k}" for k, v in counts.items())
            + ". Say yes to do it (tidy_folder_apply).")


@mcp.tool()
@guarded
def tidy_folder_apply(folder: str) -> str:
    """Actually tidy the folder (after the preview). Moves files into type subfolders; never overwrites."""
    root = resolve(folder)
    moved = 0
    log = []
    for src, dst in _tidy_plan(root):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        base, ext = os.path.splitext(dst)
        n = 2
        while os.path.exists(dst):
            dst = f"{base} ({n}){ext}"
            n += 1
        shutil.move(src, dst)
        log.append({"from": src, "to": dst})
        moved += 1
    save(os.path.join(DATA, "last_tidy.json"), log)  # so it can be undone by hand if they want
    return f"Moved {moved} files into type folders in {root}. (A list of every move is in data/last_tidy.json.)"


def _rename_plan(root, find, replace, prefix, numbering):
    files_ = sorted(f for f in os.listdir(root) if os.path.isfile(os.path.join(root, f)) and not f.startswith("."))
    plan = []
    for i, f in enumerate(files_, 1):
        base, ext = os.path.splitext(f)
        new = base.replace(find, replace) if find else base
        if numbering:
            new = f"{new} {i:03d}"
        new = re.sub(r'[<>:"/\\|?*]', "_", (prefix + new).strip()) + ext
        if new != f:
            plan.append((f, new))
    return plan


@mcp.tool()
@guarded
def batch_rename(folder: str, find: str = "", replace: str = "", prefix: str = "", numbering: bool = False) -> str:
    """Preview renaming all files in a folder: replace text, add a prefix, and/or number them. Renames nothing;
    batch_rename_apply does it after they agree."""
    plan = _rename_plan(resolve(folder), find, replace, prefix, numbering)
    if not plan:
        return "Nothing would change."
    return (f"{len(plan)} files would be renamed, e.g. " + "; ".join(f"{a} -> {b}" for a, b in plan[:4])
            + ". Say yes to do it (batch_rename_apply).")


@mcp.tool()
@guarded
def batch_rename_apply(folder: str, find: str = "", replace: str = "", prefix: str = "", numbering: bool = False) -> str:
    """Actually rename the files (same options as the preview). Never overwrites an existing file."""
    root = resolve(folder)
    done, skipped = 0, 0
    for a, b in _rename_plan(root, find, replace, prefix, numbering):
        if os.path.exists(os.path.join(root, b)):
            skipped += 1
            continue
        os.rename(os.path.join(root, a), os.path.join(root, b))
        done += 1
    return f"Renamed {done} files" + (f"; skipped {skipped} whose new name already existed" if skipped else "") + "."


@mcp.tool()
@guarded
def qr_code(text: str) -> str:
    """Make a QR code (a link, Wi-Fi text, phone number...) as an image in Documents/Karen and show it."""
    import segno
    out = out_path("QR " + re.sub(r"\W+", " ", text)[:30], ".png")
    segno.make(text, error="m").save(out, scale=12, border=3)
    return f"QR code saved: {out}\n[[show: {out}]]"


@mcp.tool()
@guarded
def convert_image(path: str, to: str = "png") -> str:
    """Convert an image to png, jpg, webp, bmp, ico or pdf. Saves a copy in Documents/Karen."""
    from PIL import Image
    full = resolve(path)
    to = to.lower().lstrip(".").replace("jpeg", "jpg")
    if to not in ("png", "jpg", "webp", "bmp", "ico", "pdf"):
        return "Formats: png, jpg, webp, bmp, ico, pdf."
    out = out_path(os.path.basename(full), "." + to)
    with Image.open(full) as im:
        if to in ("jpg", "pdf", "bmp"):
            im = im.convert("RGB")
        im.save(out, **({"quality": 92} if to in ("jpg", "webp") else {}), **({"sizes": [(256, 256)]} if to == "ico" else {}))
    return f"Saved {out}.\n[[show: {out}]]"


@mcp.tool()
@guarded
def convert_audio(path: str, to: str = "mp3") -> str:
    """Convert audio or extract the sound from a video: mp3, wav, m4a, ogg or flac (uses ffmpeg). Saves a copy."""
    full = resolve(path)
    to = to.lower().lstrip(".")
    if to not in ("mp3", "wav", "m4a", "ogg", "flac"):
        return "Formats: mp3, wav, m4a, ogg, flac."
    exe = shutil.which("ffmpeg")
    if not exe:
        return "ffmpeg isn't installed (winget install Gyan.FFmpeg)."
    out = out_path(os.path.basename(full), "." + to)
    r = subprocess.run([exe, "-hide_banner", "-loglevel", "error", "-i", full, "-vn", out],  # fixed args, no shell
                       capture_output=True, text=True, timeout=600, creationflags=0x08000000)
    if r.returncode or not os.path.exists(out):
        return f"ffmpeg couldn't convert it: {r.stderr.strip()[:200]}"
    return f"Saved {out} ({size(os.path.getsize(out))})."


# ---------------------------------------------------------------- batch 3: translator voice, voice & screen recorder
# Kokoro voices per language. espeak can't read Japanese script (it says "Chinese letter"), so Japanese is left out.
SPOKEN = {"spanish": ("es", "ef_dora"), "french": ("fr-fr", "ff_siwis"), "hindi": ("hi", "hf_alpha"),
          "italian": ("it", "if_sara"), "portuguese": ("pt-br", "pf_dora"), "chinese": ("cmn", "zf_xiaobei"),
          "mandarin": ("cmn", "zf_xiaobei"), "brazilian": ("pt-br", "pf_dora")}


@mcp.tool()
def speak_translation(language: str, text: str) -> str:
    """ALWAYS call this for any translation or "how do you say X in <language>" request: Karen's normal voice can
    only speak English, so this is the only way they hear it pronounced. Translate it yourself first and pass the
    translated text in that language's own script (Hindi in Devanagari, Chinese in characters).
    Spoken: Spanish, French, Hindi, Italian, Portuguese, Chinese."""
    lang = SPOKEN.get(language.strip().lower().split()[0])
    if not lang:
        return (f"I can't pronounce {language} yet; just write the translation in the reply. "
                f"Spoken languages: Spanish, French, Hindi, Italian, Portuguese, Chinese.")
    text = " ".join(text.split())[:400]
    return f"[[say: {lang[0]}|{lang[1]}|{text}]]"


REC_DIR = os.path.join(OUT, "Recordings")
recordings = {}  # "voice" / "screen" -> (process, path, started)


def _ffmpeg():
    exe = shutil.which("ffmpeg")
    if not exe:
        raise ValueError("ffmpeg isn't installed (winget install Gyan.FFmpeg).")
    return exe


def _mic_name():
    """The first DirectShow microphone (ffmpeg needs it by name)."""
    r = subprocess.run([_ffmpeg(), "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=0x08000000)
    names = re.findall(r'"([^"]+)" \(audio\)', r.stderr)
    if not names:
        raise ValueError("No microphone found.")
    return names[0]


def _start(kind, name, args, ext):
    if kind in recordings and recordings[kind][0].poll() is None:
        return f"A {kind} recording is already running. Say stop recording first."
    os.makedirs(REC_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H-%M")
    base = re.sub(r'[<>:"/\\|?*]', "", name).strip() or f"{kind.title()} {stamp}"
    path = os.path.join(REC_DIR, base + ext)
    n = 2
    while os.path.exists(path):
        path, n = os.path.join(REC_DIR, f"{base} ({n}){ext}"), n + 1
    proc = subprocess.Popen([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", *args, path],  # fixed args, no shell
                            stdin=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=0x08000000)
    time.sleep(1.5)
    if proc.poll() is not None:
        return f"The recorder didn't start: {proc.stderr.read().decode(errors='replace').strip()[:200]}"
    recordings[kind] = (proc, path, time.time())
    return None


@mcp.tool()
@guarded
def start_voice_recording(name: str = "") -> str:
    """Record the microphone (a voice memo, a lecture, a meeting) until they say stop recording. Max 2 hours."""
    mic = _mic_name()
    err = _start("voice", name, ["-f", "dshow", "-i", f"audio={mic}", "-t", "7200", "-ac", "1",
                                 "-c:a", "aac", "-b:a", "96k"], ".m4a")
    return err or "Recording your voice now. Say stop recording when you're done."


@mcp.tool()
@guarded
def start_screen_recording(with_mic: bool = False, name: str = "") -> str:
    """Record the whole screen to an MP4 video (15 fps), optionally with the microphone, until they say stop
    recording. Max 1 hour."""
    audio = ["-f", "dshow", "-i", f"audio={_mic_name()}"] if with_mic else []
    err = _start("screen", name, ["-f", "gdigrab", "-framerate", "15", "-draw_mouse", "1", "-i", "desktop", *audio,
                                  "-t", "3600", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
                                  "-pix_fmt", "yuv420p", "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                                  *(["-c:a", "aac", "-b:a", "96k"] if with_mic else []),
                                  "-movflags", "+frag_keyframe+empty_moov"], ".mp4")  # playable even if cut off
    return err or "Recording the screen now. Say stop recording when you're done."


@mcp.tool()
@guarded
def stop_recording(kind: str = "all") -> str:
    """Stop the voice and/or screen recording (kind: voice, screen or all) and show the file."""
    done = []
    for k in ([kind] if kind in ("voice", "screen") else ["voice", "screen"]):
        if k not in recordings:
            continue
        proc, path, started = recordings.pop(k)
        if proc.poll() is None:
            try:
                proc.stdin.write(b"q")  # ffmpeg finishes the file properly
                proc.stdin.flush()
                proc.wait(15)
            except Exception:
                proc.kill()
        if os.path.exists(path):
            mins, secs = divmod(int(time.time() - started), 60)
            done.append(f"Saved the {k} recording ({mins} min {secs} s, {size(os.path.getsize(path))}) "
                        f"as {path}.\n[[show: {path}]]")
    return "\n".join(done) or "Nothing is being recorded."


import atexit  # noqa: E402

atexit.register(lambda: stop_recording("all"))  # Karen closing finishes the files instead of leaving ffmpeg running


@mcp.tool()
def recording_status() -> str:
    """Is anything being recorded right now, and for how long."""
    live = [f"{k} for {int(time.time() - s) // 60} min" for k, (p, _, s) in recordings.items() if p.poll() is None]
    return "Recording: " + ", ".join(live) if live else "Nothing is being recorded."


if __name__ == "__main__":
    mcp.run()
