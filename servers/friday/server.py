"""Friday's abilities rebuilt for Karen (2026-11-24, his request: merge his Friday copy of FatihMakes'
"Mark XXXIX" into Karen, then delete it). Only what Karen didn't do already, in Karen's own style (no Gemini SDK, no generated code):

- find_flights: Google Flights rendered by headless Edge (a separate profile, not his), rows read from the page
  text. Friday drove a Playwright browser and asked Gemini to read the page; plain parsing is free and instant.
- set_wallpaper / current_wallpaper: from a file in his folders, a web address, or Karen's newest AI picture.
- data_summary / data_table / convert_data: CSV, Excel and JSON files (columns, totals, averages, filters, sorting,
  converting), with the csv module and openpyxl (no pandas).

Friday's long-term memory (its facts sent with every request) went into core/brain.py (`Brain.memory`).
Left out on purpose: its code writer / dev agent (writes and runs code: against Karen's rules), Steam/Epic game
updater (no Steam or Epic on this PC), Playwright browser and planner (Karen's screen agent and tool loop do this).
"""
import csv
import ctypes
import datetime
import html
import importlib.util
import json
import os
import re
import statistics
import subprocess
import time
import urllib.parse
import urllib.request

from mcp.server import MCPServer

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_spec = importlib.util.spec_from_file_location("karen_files", os.path.join(ROOT, "servers", "files", "server.py"))
files = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(files)
resolve, out_path, guarded, known_folder = files.resolve, files.out_path, files.guarded, files.known_folder

mcp = MCPServer("friday")
NO_WINDOW = 0x08000000


# ---------------------------------------------------------------- flights
def edge_path():
    for base in (os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                 os.environ.get("ProgramFiles", r"C:\Program Files")):
        p = os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe")
        if os.path.exists(p):
            return p
    raise ValueError("Microsoft Edge isn't installed, and I need it to read Google Flights.")


def page_lines(url, wait_ms=15000):
    """The visible text of a page after its scripts ran, one line per text node."""
    profile = os.path.join(os.environ.get("LOCALAPPDATA", ROOT), "Karen", "edge-flights")  # not his Edge profile
    r = subprocess.run([edge_path(), "--headless=new", "--disable-gpu", "--no-first-run", f"--user-data-dir={profile}",
                        f"--virtual-time-budget={wait_ms}", "--lang=en-IN", "--dump-dom", url],
                       capture_output=True, timeout=90, creationflags=NO_WINDOW)
    page = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", r.stdout.decode("utf-8", "ignore"))
    return [s.strip() for s in html.unescape(re.sub(r"<[^>]+>", "\n", page)).split("\n") if s.strip()]


DEPART = re.compile(r"^(\d{1,2}:\d\d\s?[AP]M) on (.+)$")
STOPS = re.compile(r"^(Nonstop|\d+ stops?)$")
DURATION = re.compile(r"^\d+ hr( \d+ min)?$|^\d+ min$")


def parse_flights(lines):
    """Google Flights rows: '9:00 AM on Sat, Dec 5' | '–' | '12:05 PM' | '12:05 PM on Sat, Dec 5' | airline |
    '3 hr 5 min' | from | ... | to | ... | 'Nonstop' | ... | '₹11,138'. Each row's full times appear once."""
    out, seen = [], set()
    lines = [re.sub(r"[\xa0  ]", " ", s) for s in lines]  # "9:00 AM\xa0on\xa0Sat, Dec 5"
    for i, s in enumerate(lines):
        m = DEPART.match(s)
        if not m or i + 4 >= len(lines):
            continue
        arr = next((DEPART.match(x) for x in lines[i + 1:i + 4] if DEPART.match(x)), None)
        if not arr:
            continue
        window = lines[i + 1:i + 30]
        k = next((j for j, x in enumerate(window) if DEPART.match(x)), 0)
        airline = window[k + 1] if k + 1 < len(window) else "?"
        duration = next((x for x in window if DURATION.match(x)), "")
        stops = next((x for x in window if STOPS.match(x)), "")
        price = next((x for x in window if re.match(r"^[₹$€£][\d,]+$", x)), "")
        if not price:
            continue
        row = (m.group(1), arr.group(1), airline, duration, stops, price)
        if row in seen:
            continue
        seen.add(row)
        nights = "" if arr.group(2) == m.group(2) else f" (arrives {arr.group(2)})"
        out.append({"depart": m.group(1), "arrive": arr.group(1) + nights, "airline": airline, "duration": duration,
                    "stops": stops, "price": price, "value": int(re.sub(r"\D", "", price) or 0),
                    "minutes": minutes(duration)})
    return out


def minutes(duration):
    h = re.search(r"(\d+) hr", duration)
    m = re.search(r"(\d+) min", duration)
    return (int(h.group(1)) * 60 if h else 0) + (int(m.group(1)) if m else 0) or 10 ** 6


def day_words(date):
    d = datetime.date.fromisoformat(date.strip())
    if d < datetime.date.today():
        raise ValueError(f"{date} is in the past.")
    return f"{d:%B} {d.day} {d.year}"


@mcp.tool()
@guarded
def find_flights(origin: str, destination: str, date: str, return_date: str = "", adults: int = 1) -> str:
    """Search Google Flights and read out the best, cheapest and fastest flights. origin/destination: city or airport
    names; date/return_date: YYYY-MM-DD (empty return_date = one way). Prices in rupees. ALWAYS call this for flight
    questions instead of guessing fares."""
    trip = (f"flights from {origin} to {destination} on {day_words(date)}"
            + (f" returning {day_words(return_date)}" if return_date.strip() else " one way")
            + (f" for {adults} adults" if adults > 1 else ""))
    url = "https://www.google.com/travel/flights?" + urllib.parse.urlencode(
        {"q": trip, "curr": "INR", "hl": "en", "gl": "in"})
    t = time.time()
    rows = parse_flights(page_lines(url))
    if not rows:
        return f"Google Flights showed no flights for {trip} (or the page didn't load). Link: {url}"
    best = rows[:3]
    cheap = min(rows, key=lambda r: r["value"])
    fast = min(rows, key=lambda r: r["minutes"])

    def line(r):
        return f"{r['airline']}, {r['depart']} → {r['arrive']}, {r['duration']}, {r['stops'] or '?'}, {r['price']}"
    text = [f"{len(rows)} flights for {trip} ({time.time() - t:.0f} s, Google Flights; price for "
            f"{'the round trip' if return_date.strip() else 'one way'}, {adults} adult{'s' if adults > 1 else ''}):",
            "Top picks:"] + [f"- {line(r)}" for r in best] + [
            f"Cheapest: {line(cheap)}", f"Fastest: {line(fast)}", f"Book or see all: {url}",
            "Say the airline, times and price in one or two sentences; don't read the link aloud."]
    return "\n".join(text)


# ---------------------------------------------------------------- wallpaper
SPI_SET, SPI_GET = 0x0014, 0x0073
PICS = (".jpg", ".jpeg", ".png", ".bmp", ".webp")


def wallpaper_dir():
    d = os.path.join(known_folder("Pictures"), "Karen", "Wallpapers")
    os.makedirs(d, exist_ok=True)
    return d


def newest_picture():
    folder = os.path.join(files.OUT, "Pictures")  # create_picture / find_photo save here
    found = [os.path.join(folder, f) for f in os.listdir(folder)] if os.path.isdir(folder) else []
    found = [f for f in found if f.lower().endswith(PICS)]
    if not found:
        raise ValueError("There's no picture of mine yet. Ask me to draw or find one first.")
    return max(found, key=os.path.getmtime)


@mcp.tool()
@guarded
def set_wallpaper(image: str) -> str:
    """Set the desktop wallpaper. image: a file ("Pictures/beach.jpg", an uploaded file's name), a web address of a
    picture, or "last" for the picture Karen made or found most recently (draw/find one first with the picture tools)."""
    from PIL import Image
    src = image.strip()
    stamp = f"{datetime.datetime.now():%Y-%m-%d %H.%M.%S}"
    if re.match(r"https?://", src, re.I):
        tmp = os.path.join(wallpaper_dir(), f"download {stamp}")
        req = urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r, open(tmp, "wb") as f:
            f.write(r.read(30 * 2**20))
        path = tmp
    else:
        path = newest_picture() if src.lower() in ("last", "latest", "that", "it", "that picture", "") else resolve(src)
    try:
        with Image.open(path) as im:
            im = im.convert("RGB")
            if im.width < 400 or im.height < 300:
                raise ValueError("That picture is too small for a wallpaper.")
            dest = os.path.join(wallpaper_dir(), f"wallpaper {stamp}.jpg")  # Windows reads jpg; webp/png converted
            im.save(dest, quality=95)
    finally:
        if path.startswith(os.path.join(wallpaper_dir(), "download ")):
            os.remove(path)
    if not ctypes.windll.user32.SystemParametersInfoW(SPI_SET, 0, dest, 3):  # 3 = save + tell other apps
        raise RuntimeError("Windows refused the wallpaper")
    return f"Wallpaper set ({os.path.basename(path) if not src.startswith('http') else 'from the web'})."


@mcp.tool()
@guarded
def current_wallpaper() -> str:
    """Which picture is the desktop wallpaper now (and show it)."""
    buf = ctypes.create_unicode_buffer(520)
    ctypes.windll.user32.SystemParametersInfoW(SPI_GET, 520, buf, 0)
    if not buf.value or not os.path.exists(buf.value):
        return "No picture wallpaper (a plain colour or a slideshow)."
    return f"Wallpaper: {buf.value}\n[[show: {buf.value}]]"


# ---------------------------------------------------------------- CSV / Excel / JSON
MAX_ROWS = 50000


def read_table(path, sheet=""):
    """(header, rows) from .csv/.tsv/.xlsx/.xlsm/.json (a list of objects)."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".csv", ".tsv", ".txt"):
        with open(path, encoding="utf-8-sig", errors="replace", newline="") as f:
            sample = f.read(8192)
            f.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            except csv.Error:
                dialect = csv.excel_tab if ext == ".tsv" else csv.excel
            rows = [r for _, r in zip(range(MAX_ROWS + 1), csv.reader(f, dialect), strict=False)]
    elif ext in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook
        wb = load_workbook(path, read_only=True, data_only=True)
        ws = wb[sheet] if sheet and sheet in wb.sheetnames else wb.worksheets[0]
        rows = [["" if v is None else v for v in r] for _, r in zip(range(MAX_ROWS + 1), ws.iter_rows(values_only=True), strict=False)]
        wb.close()
    elif ext == ".json":
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data = next((v for v in data.values() if isinstance(v, list)), [data])
        if not data or not all(isinstance(x, dict) for x in data):
            raise ValueError("That JSON isn't a list of records, so it isn't a table (data_summary can still check it).")
        header = list(dict.fromkeys(k for x in data for k in x))
        rows = [header] + [[x.get(k, "") for k in header] for x in data[:MAX_ROWS]]
    else:
        raise ValueError("I can read tables from .csv, .tsv, .xlsx and .json files.")
    rows = [r for r in rows if any(str(v).strip() for v in r)]
    if not rows:
        raise ValueError("The file is empty.")
    width = max(len(r) for r in rows)
    header = [str(h).strip() or f"column {i + 1}" for i, h in enumerate(list(rows[0]) + [""] * (width - len(rows[0])))]
    return header, [list(r) + [""] * (width - len(r)) for r in rows[1:]]


def number(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    s = re.sub(r"[₹$€£,\s%]", "", str(v))
    try:
        return float(s) if s else None
    except ValueError:
        return None


def col_index(header, name):
    low = [h.lower() for h in header]
    n = name.strip().lower()
    if n in low:
        return low.index(n)
    hits = [i for i, h in enumerate(low) if n in h]
    if len(hits) == 1:
        return hits[0]
    raise ValueError(f"No single column called '{name}'. Columns: {', '.join(header)}")


def fmt(x):
    return f"{x:,.0f}" if float(x).is_integer() else f"{x:,.2f}"


@mcp.tool()
@guarded
def data_summary(file: str, sheet: str = "") -> str:
    """Overview of a CSV, Excel or JSON file: rows, columns, and for each column its type with total / average /
    min / max (numbers) or the most common values (text). Also checks a .json file is valid. Use before data_table."""
    path = resolve(file)
    if path.lower().endswith(".json"):
        try:
            with open(path, encoding="utf-8-sig") as f:
                data = json.load(f)
        except ValueError as e:
            return f"{os.path.basename(path)} is NOT valid JSON: {e}"
        if not (isinstance(data, list) and data and all(isinstance(x, dict) for x in data)):
            kind = type(data).__name__
            keys = ", ".join(list(data)[:20]) if isinstance(data, dict) else ""
            return f"{os.path.basename(path)} is valid JSON: a {kind}" + (f" with keys {keys}" if keys else "") + "."
    header, rows = read_table(path, sheet)
    out = [f"{os.path.basename(path)}: {len(rows):,} rows" + (" (only the first 50,000 read)" if len(rows) >= MAX_ROWS
                                                               else "") + f", {len(header)} columns."]
    for i, h in enumerate(header[:40]):
        vals = [r[i] for r in rows if str(r[i]).strip()]
        nums = [n for n in (number(v) for v in vals) if n is not None]
        if vals and len(nums) >= 0.8 * len(vals):
            out.append(f"- {h}: numbers, total {fmt(sum(nums))}, average {fmt(statistics.fmean(nums))}, "
                       f"min {fmt(min(nums))}, max {fmt(max(nums))}" + (f", {len(rows) - len(vals)} empty"
                                                                         if len(vals) < len(rows) else ""))
        else:
            common = {}
            for v in vals:
                common[str(v)] = common.get(str(v), 0) + 1
            top = sorted(common.items(), key=lambda kv: -kv[1])[:4]
            out.append(f"- {h}: text, {len(common)} different values" +
                       (f"; most common: {', '.join(f'{k[:30]} ({n})' for k, n in top)}" if top else "; empty"))
    if len(header) > 40:
        out.append(f"(+{len(header) - 40} more columns)")
    return "\n".join(out)


OPS = re.compile(r"^\s*(.+?)\s*(>=|<=|!=|=|>|<|contains)\s*(.+?)\s*$", re.I)


@mcp.tool()
@guarded
def data_table(file: str, where: str = "", sort_by: str = "", descending: bool = True, group_by: str = "",
               total_of: str = "", columns: str = "", limit: int = 15, sheet: str = "", save: bool = False) -> str:
    """Answer questions about a CSV/Excel/JSON table. where: filters joined by ';' like "city = Chennai; amount > 500;
    name contains raj". group_by + total_of: totals per group (e.g. group_by "category", total_of "amount").
    sort_by: a column (descending by default). columns: which columns to show, comma separated. save: also write the
    result as an Excel file in Documents/Karen and show it."""
    path = resolve(file)
    header, rows = read_table(path, sheet)
    for cond in [c for c in where.split(";") if c.strip()]:
        m = OPS.match(cond)
        if not m:
            raise ValueError(f"I didn't understand the filter '{cond}'. Use e.g. 'amount > 500' or 'city = Chennai'.")
        i, op, want = col_index(header, m.group(1)), m.group(2).lower(), m.group(3).strip().strip("'\"")
        wn = number(want)

        def keep(v, i=i, op=op, want=want, wn=wn):
            vn = number(v)
            if op == "contains":
                return want.lower() in str(v).lower()
            if wn is not None and vn is not None:
                return {"=": vn == wn, "!=": vn != wn, ">": vn > wn, "<": vn < wn, ">=": vn >= wn, "<=": vn <= wn}[op]
            s = str(v).strip().lower()
            return {"=": s == want.lower(), "!=": s != want.lower(), ">": s > want.lower(), "<": s < want.lower(),
                    ">=": s >= want.lower(), "<=": s <= want.lower()}[op]
        rows = [r for r in rows if keep(r[i])]
    matched = len(rows)
    if group_by:
        g = col_index(header, group_by)
        t = col_index(header, total_of) if total_of else None
        groups = {}
        for r in rows:
            key = str(r[g]).strip() or "(empty)"
            cnt, tot = groups.get(key, (0, 0.0))
            groups[key] = (cnt + 1, tot + (number(r[t]) or 0 if t is not None else 0))
        header = [header[g], "rows"] + ([f"total {header[t]}"] if t is not None else [])
        rows = [[k, c] + ([round(s, 2)] if t is not None else []) for k, (c, s) in groups.items()]
        rows.sort(key=lambda r: -(r[2] if t is not None else r[1]))
    if sort_by:
        i = col_index(header, sort_by)
        rows.sort(key=lambda r: (number(r[i]) is None, number(r[i]) if number(r[i]) is not None else str(r[i]).lower()),
                  reverse=False)
        if descending:
            nums = [r for r in rows if number(r[i]) is not None]
            rows = nums[::-1] + [r for r in rows if number(r[i]) is None]
    if columns.strip():
        idx = [col_index(header, c) for c in columns.split(",") if c.strip()]
        header, rows = [header[i] for i in idx], [[r[i] for i in idx] for r in rows]
    shown = rows[:max(1, min(limit, 100))]
    out = [f"{matched:,} matching rows" + (f", {len(rows)} groups" if group_by else "") +
           (f" (showing {len(shown)})" if len(shown) < len(rows) else "") + ":", " | ".join(header)]
    out += [" | ".join(fmt(v) if isinstance(v, float) else str(v)[:40] for v in r) for r in shown]
    if save and rows:
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.append(header)
        for r in rows:
            ws.append(r)
        dest = out_path(os.path.basename(path) + " result", ".xlsx")
        wb.save(dest)
        out.append(f"Saved: {dest}\n[[show: {dest}]]")
    return "\n".join(out)


@mcp.tool()
@guarded
def convert_data(file: str, to: str, sheet: str = "") -> str:
    """Convert a table between CSV, Excel (xlsx) and JSON. to: "csv", "xlsx" or "json". Saved in Documents/Karen."""
    path = resolve(file)
    to = to.lower().strip(". ")
    if to not in ("csv", "xlsx", "json"):
        raise ValueError("I can convert to csv, xlsx or json.")
    header, rows = read_table(path, sheet)
    dest = out_path(os.path.basename(path), "." + to)
    if to == "csv":
        with open(dest, "w", encoding="utf-8-sig", newline="") as f:
            csv.writer(f).writerows([header] + rows)
    elif to == "json":
        with open(dest, "w", encoding="utf-8") as f:
            json.dump([dict(zip(header, r, strict=False)) for r in rows], f, ensure_ascii=False, indent=1, default=str)
    else:
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        for r in [header] + rows:
            ws.append([number(v) if number(v) is not None and str(v).strip() and not str(v).startswith("0")
                       else v for v in r])
        wb.save(dest)
    return f"Converted {len(rows):,} rows to {os.path.basename(dest)} in Documents/Karen.\n[[show: {dest}]]"


if __name__ == "__main__":
    mcp.run()
