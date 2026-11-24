"""Karen's eyes and research desk, as an MCP server (stdio): the webcam, and research mode.

look: takes a fresh webcam picture and asks a vision model (OpenRouter, zero data retention) about it. The camera stays
on for explore.camera_on_s after a look so follow-ups are instant, then turns off (its light goes out).
research_mode: opens a topic in several browser windows side by side: overview, the top web results (free search via
ddgs, no key) and specialist sites for the kind of topic. research_off closes only those windows.
research_report: a PDF report (report.py) about a topic, or the product shown to the camera (photo from the web).
work_mode: coding/study/chill setups from config.yaml (VS Code project, apps, websites, music), tiled; off closes them.
A line "[[show: <path>]]" in a result makes Karen open it in her viewer.
"""
import base64
import ctypes
import datetime
import glob
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote_plus

import yaml
from mcp.server import MCPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # report.py
from openrouter import chat, friendly  # noqa: E402  (servers/openrouter.py: vision with a free fallback)

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CFG = (yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8")) or {}).get("explore", {})
SHOTS = os.path.join(ROOT, "data", "camera")
mcp = MCPServer("explore")
user32 = ctypes.windll.user32
ctypes.windll.shcore.SetProcessDpiAwareness(2)  # real pixels, so the windows tile the screen exactly at 125%


def log(what, detail):
    with open(os.path.join(ROOT, "logs", "pc_actions.log"), "a", encoding="utf-8") as f:
        f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {what:<12} {detail}\n")


# ---------------------------------------------------------------- camera
class Camera:
    """The webcam, read continuously by a thread while on, so a look always gets the newest frame."""

    def __init__(self):
        self.cap, self.frame, self.frames, self.last_use = None, None, 0, 0.0
        self.lock = threading.Lock()

    def on(self):
        import cv2
        with self.lock:
            self.last_use = time.time()
            if self.cap is not None:
                return True
            cap = cv2.VideoCapture(int(CFG.get("camera", 0)), cv2.CAP_DSHOW)
            if not cap.isOpened():
                return False
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            self.cap, self.frame, self.frames = cap, None, 0
        threading.Thread(target=self._read, args=(cap,), daemon=True).start()
        for _ in range(50):  # the first frames are black while auto exposure settles
            time.sleep(0.1)
            if self.frames >= 15 and self.frame is not None and self.frame.mean() > 20:
                break
        return self.frame is not None

    def _read(self, cap):
        while self.cap is cap:
            ok, frame = cap.read()
            if ok:
                self.frame, self.frames = frame, self.frames + 1
            if time.time() - self.last_use > CFG.get("camera_on_s", 60):
                self.off()
        cap.release()  # only this thread touches the device, so it's never released mid-read
        log("camera", "off")

    def off(self):
        with self.lock:
            was_on, self.cap, self.frame = self.cap is not None, None, None
        return was_on


CAMERA = Camera()


def _vision(jpeg, question):
    return chat(CFG.get("vision_model"), max_tokens=350, messages=[{"role": "user", "content": [
            {"type": "text", "text": "This is a live webcam picture from the user's laptop. Answer for speaking aloud, in a "
                                     "few short sentences. If it's a product, name the brand and model if you can read "
                                     "or recognise them. Read any text that matters.\nQuestion: "
                                     + (question or "What do you see?")},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}}]}])


@mcp.tool()
@friendly
def look(question: str = "") -> str:
    """See through the webcam right now and answer about it: what the user is holding, a product, a label, a room.
    question: what he asked (empty = describe what you see). Every call takes a fresh picture."""
    t = time.time()
    shot = _capture()
    if not shot:
        return NO_CAMERA
    jpeg, path = shot
    answer = _vision(jpeg, question)
    CAMERA.last_use = time.time()
    log("look", f"{question[:60]} ({time.time() - t:.1f}s)")
    return f"{answer}\n(The camera stays on for follow-up questions for a minute.)\n[[show: {path}]]"


NO_CAMERA = ("I couldn't open the camera. Another app may be using it, or camera access is off in Settings > "
             "Privacy > Camera ('Let desktop apps access your camera').")


def _capture():
    """(jpeg bytes, saved path) of a fresh webcam picture, or None if the camera won't open."""
    import cv2
    if not CAMERA.on():
        return None
    frame = CAMERA.frame
    ok, jpeg = cv2.imencode(".jpg", cv2.resize(frame, (960, int(frame.shape[0] * 960 / frame.shape[1]))),
                            [cv2.IMWRITE_JPEG_QUALITY, 85])
    os.makedirs(SHOTS, exist_ok=True)
    for old in sorted(glob.glob(os.path.join(SHOTS, "look-*.jpg")))[:-4]:
        os.remove(old)  # keep only the last few, just for the viewer
    path = os.path.join(SHOTS, f"look-{datetime.datetime.now():%H%M%S}.jpg")
    with open(path, "wb") as f:
        f.write(jpeg.tobytes())
    return jpeg.tobytes(), path


@mcp.tool()
def camera_off() -> str:
    """Turn the webcam off now."""
    return "Camera off." if CAMERA.off() else "The camera was already off."


# ---------------------------------------------------------------- research mode
KINDS = {  # specialist sites per kind of topic; {q} is the topic, URL-encoded
    "product": ["https://www.amazon.in/s?k={q}", "https://www.flipkart.com/search?q={q}",
                "https://www.youtube.com/results?search_query={q}+review", "https://www.reddit.com/search/?q={q}+review"],
    "design": ["https://www.pinterest.com/search/pins/?q={q}", "https://dribbble.com/search/{q}",
               "https://www.behance.net/search/projects?search={q}", "https://www.google.com/search?tbm=isch&q={q}"],
    "concept": ["https://scholar.google.com/scholar?q={q}", "https://www.youtube.com/results?search_query={q}+explained",
                "https://www.khanacademy.org/search?page_search_query={q}", "https://arxiv.org/search/?searchtype=all&query={q}"],
    "tech": ["https://github.com/search?type=repositories&q={q}", "https://stackoverflow.com/search?q={q}",
             "https://www.google.com/search?q={q}+official+documentation", "https://www.youtube.com/results?search_query={q}+tutorial"],
    "news": ["https://news.google.com/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en", "https://www.youtube.com/results?search_query={q}+news",
             "https://www.reddit.com/search/?q={q}&sort=new"],
    "general": ["https://www.youtube.com/results?search_query={q}", "https://www.reddit.com/search/?q={q}",
                "https://www.google.com/search?tbm=isch&q={q}"],
}
OVERVIEW = ["https://www.google.com/search?q={q}", "https://en.wikipedia.org/w/index.php?search={q}",
            "https://www.perplexity.ai/search?q={q}"]
RESEARCH = []  # hwnds of the windows research mode opened, for research_off
LAST = {"topic": "", "pages": []}  # the latest research topic and its top articles, for research_summary/report


def _browser():
    import winreg
    exe = {"chrome": "chrome.exe", "edge": "msedge.exe", "brave": "brave.exe"}.get(CFG.get("browser", "edge"), "msedge.exe")
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            path = winreg.QueryValue(hive, rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{exe}")
            if os.path.exists(path):
                return path
        except OSError:
            pass
    return None


def _top_windows():
    found = []

    def each(hwnd, _):
        if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd):
            found.append(hwnd)
        return True
    user32.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(each), 0)
    return found


def _open_window(exe, urls):
    """One new browser window with these tabs; returns its hwnd (or None)."""
    before = set(_top_windows())
    subprocess.Popen([exe, "--new-window", *urls], creationflags=0x08000000)
    for _ in range(40):
        time.sleep(0.25)
        new = [h for h in _top_windows() if h not in before]
        if new:
            return new[0]
    return None


def _tile(hwnds, weights=None):
    """Side by side across the work area (screen minus taskbar); weights = relative widths."""
    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
    area = RECT()
    user32.SystemParametersInfoW(0x30, 0, ctypes.byref(area), 0)  # SPI_GETWORKAREA
    weights = weights or [1] * len(hwnds)
    x = area.left
    for h, w in zip(hwnds, weights):
        width = (area.right - area.left) * w // sum(weights)
        user32.ShowWindow(h, 9)  # SW_RESTORE, in case it opened maximized
        user32.MoveWindow(h, x, area.top, width, area.bottom - area.top, True)
        x += width


def _top_results(topic, n):
    try:
        from ddgs import DDGS
        hits = DDGS(timeout=8).text(topic, region="in-en", max_results=n + 3, backend="duckduckgo, yahoo, mojeek")
    except Exception:
        return []
    skip = ("wikipedia.org", "youtube.com", "amazon.", "flipkart.com", "reddit.com")  # already in other windows
    return [h["href"] for h in hits if not any(s in h["href"] for s in skip)][:n]


@mcp.tool()
def research_mode(topic: str, kind: str = "general") -> str:
    """Research mode / power research: open many pages about a topic in several browser windows side by side.
    Only when he asks for research mode or to open pages; for a report or PDF use research_report alone.
    topic: a short search phrase. kind: product, design, concept (study, science, history), tech (code, software),
    news, or general."""
    exe = _browser()
    if not exe:
        return "I couldn't find the browser for research mode (explore.browser in config.yaml)."
    kind = kind.lower().strip() if kind.lower().strip() in KINDS else "general"
    q = quote_plus(topic)
    windows = [[u.format(q=q) for u in OVERVIEW],
               _top_results(topic, CFG.get("top_results", 5)),
               [u.format(q=q) for u in KINDS[kind]]]
    LAST.update(topic=topic, pages=windows[1])
    windows = [w for w in windows if w]
    research_off()  # a new topic replaces the old research windows
    for urls in windows:
        hwnd = _open_window(exe, urls)
        if hwnd:
            RESEARCH.append(hwnd)
    if RESEARCH:
        time.sleep(1.0)  # let the windows finish appearing before moving them
        _tile(RESEARCH)
    tabs = sum(len(w) for w in windows)
    log("research", f"{topic} ({kind}): {tabs} tabs in {len(windows)} windows")
    return (f"Research mode on for '{topic}': {tabs} pages in {len(windows)} windows side by side "
            f"(overview, top articles, {kind} sites).")


@mcp.tool()
def research_off() -> str:
    """Research mode off: close the browser windows research mode opened (other windows are left alone)."""
    closed = 0
    for hwnd in RESEARCH:
        if user32.IsWindow(hwnd):
            user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
            closed += 1
    RESEARCH.clear()
    return f"Research mode off: closed {closed} windows." if closed else "No research windows were open."


# ---------------------------------------------------------------- research reports and summaries
PAGES = {}  # the pages fetched for the last report, reused for 10 minutes
IDENTIFY = ("Webcam picture. What product or object is the user showing? Ignore the person. Reply with ONE JSON object: "
            '{"name": "<brand and model, or what it is>", "confident": true/false}. confident only if you could read '
            "or clearly recognise the brand/model (or it's a clearly identifiable thing).")


@mcp.tool()
@friendly
def research_report(topic: str = "", from_camera: bool = False, from_research: bool = False) -> str:
    """Make a PDF research report (summary, specs/facts, pros/cons, price in India, alternatives, verdict, sources,
    an online photo) and save it to Documents/Karen/Reports. from_camera=true: identify the product the user is showing
    the webcam first. from_research=true: use the topic and pages of a research mode that is already on. Needs no
    research mode (it searches by itself). Takes about a minute."""
    import report
    t = time.time()
    if from_camera:
        shot = _capture()
        if not shot:
            return NO_CAMERA
        raw = chat(CFG.get("vision_model"), max_tokens=120, messages=[{"role": "user", "content": [
            {"type": "text", "text": IDENTIFY},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(shot[0]).decode()}}]}])
        try:
            seen = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
        except ValueError:
            seen = {}
        if not seen.get("confident") or not seen.get("name"):
            guess = f" It might be {seen['name']}." if seen.get("name") else ""
            return (f"I couldn't tell exactly which product that is.{guess} Ask the user for the brand and model, "
                    "then make the report with that as the topic.")
        topic = seen["name"]
    if from_research and not topic:
        topic = LAST["topic"]
    if not topic:
        return "Which product or topic should the report be about?"
    if PAGES.get("topic") == topic and time.time() - PAGES.get("at", 0) < 600:
        pages = PAGES["pages"]  # a retry (the AI was busy): don't fetch everything again
    else:
        urls = list(LAST["pages"]) if from_research and topic == LAST["topic"] else []
        query = f"{topic} review specifications price India" if from_camera else topic
        urls += [u for u in report.search(query, 8) if u not in urls]
        pages = report.fetch_pages(urls)
        PAGES.update(topic=topic, pages=pages, at=time.time())
    if not pages:
        return f"I couldn't read any web pages about {topic} right now (no internet, or the sites blocked me)."
    with ThreadPoolExecutor(1) as pool:  # the photo search runs while the LLM writes
        image = pool.submit(report.product_image, topic)
        data = report.write(topic, pages, chat, CFG.get("report_model"))
        photo = image.result()
    pdf = report.render(data, pages, photo, edge=_browser_path("msedge.exe"))
    log("report", f"{topic}: {len(pages)} sources, {time.time() - t:.0f}s -> {pdf}")
    return (f"Report on {data.get('title') or topic} saved as a PDF in Documents/Karen/Reports: {pdf}\n"
            f"{data.get('spoken', '')}\n"
            f"(If he asks, it can be emailed as an attachment with that path.)\n[[show: {pdf}]]")


@mcp.tool()
@friendly
def research_summary() -> str:
    """Summarise the current research-mode topic from its top articles: three key points, spoken."""
    import report
    if not LAST["topic"]:
        return "Research mode isn't on, so there's nothing to summarise. Say 'research mode on for <topic>' first."
    pages = report.fetch_pages(LAST["pages"] or report.search(LAST["topic"], 6), keep=5, chars=3000)
    if not pages:
        return "I couldn't read the research pages right now."
    sources = "\n\n".join(f"[{i}] {u}\n{txt}" for i, (u, txt) in enumerate(pages, 1))
    return chat(CFG.get("report_model"), max_tokens=300, timeout=90, messages=[{"role": "user", "content": (
        f"From these sources about '{LAST['topic']}', give the user the three most useful points, spoken aloud: plain "
        f"sentences, under 80 words, no lists or markdown.\n\n{sources}")}])


def _browser_path(exe):
    import winreg
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            path = winreg.QueryValue(hive, rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{exe}")
            if os.path.exists(path):
                return path
        except OSError:
            pass
    return None


# ---------------------------------------------------------------- work modes
MODE = {"name": "", "windows": []}


def _launch(cmd, wait_s=12):
    """Start something and return the new top-level window it opens (or None)."""
    before = set(_top_windows())
    if isinstance(cmd, list):
        subprocess.Popen(cmd, creationflags=0x08000000)
    else:
        os.startfile(cmd)
    end = time.time() + wait_s
    while time.time() < end:
        time.sleep(0.3)
        new = [h for h in _top_windows() if h not in before]
        if new:
            return new[0]
    return None


@mcp.tool()
def work_mode(name: str) -> str:
    """Start a work mode (e.g. coding, study, chill): opens its VS Code project, apps, websites and music, arranged
    side by side. Modes are set in config.yaml (explore.modes)."""
    modes = CFG.get("modes") or {}
    key = name.lower().replace(" mode", "").strip()
    mode = modes.get(key)
    if not mode:
        return f"There's no '{name}' mode. Modes: {', '.join(modes) or 'none yet'} (config.yaml explore.modes)."
    work_mode_off()
    edge, main = _browser(), []
    if mode.get("project"):
        import shutil
        code = shutil.which("code") or _browser_path("Code.exe")
        folder = os.path.expandvars(os.path.expanduser(mode["project"]))
        if code and os.path.isdir(folder):
            main.append(_launch([code, "-n", folder], wait_s=20))
    for app in mode.get("apps") or []:
        main.append(_launch(os.path.expandvars(app)))
    if mode.get("urls") and edge:
        main.append(_open_window(edge, mode["urls"]))
    main = [h for h in main if h]
    windows = list(main)
    if mode.get("music") and edge:  # its own window, minimized: it plays without taking space
        music = _open_window(edge, [mode["music"]])
        if music:
            time.sleep(2)
            user32.ShowWindow(music, 6)  # SW_MINIMIZE
            windows.append(music)
    if main:
        time.sleep(1.0)
        _tile(main, [3, 2] if len(main) == 2 else None)  # the first (VS Code / main app) gets more room
    MODE.update(name=key, windows=windows)
    log("work_mode", f"{key}: {len(windows)} windows")
    return f"{key.capitalize()} mode on: opened {len(windows)} windows" + \
        (", with music playing in a minimized window." if mode.get("music") else ".")


@mcp.tool()
def work_mode_off() -> str:
    """End the current work mode: close the windows it opened (VS Code may ask to save unsaved files)."""
    closed = 0
    for hwnd in MODE["windows"]:
        if user32.IsWindow(hwnd):
            user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
            closed += 1
    name, MODE["name"], MODE["windows"] = MODE["name"], "", []
    return f"{name.capitalize()} mode off: closed {closed} windows." if closed else "No work mode was on."


# ---------------------------------------------------------------- pictures (find online or create with AI)
BLOCKED = re.compile(r"\b(sex|sexy|sexual|nude|nudes|naked|porn|porno|nsfw|erotic|xxx|lust|topless|fetish|hentai|"
                     r"intercourse|orgasm|genitals?|boobs)\b", re.I)
PICTURES = os.path.join(os.environ.get("USERPROFILE", os.path.expanduser("~")), "Documents", "Karen", "Pictures")


def _images():
    """ig-reels/images.py: Cloudflare FLUX (CF_ACCOUNT_ID + CF_API_TOKEN in .env), AI Horde, Openverse/Wikimedia."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("ig_images", os.path.join(ROOT, "ig-reels", "images.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _web_photo(what, out):
    """The best real photo from a web image search (ddgs, free): biggest sensible picture that downloads."""
    import io
    import httpx
    import report
    from ddgs import DDGS
    from PIL import Image
    for hit in DDGS(timeout=10).images(what, max_results=20, backend="bing"):
        url = hit.get("image", "")
        if not url or any(s in url or s in hit.get("source", "") for s in report.SKIP_IMAGES):
            continue
        try:
            r = httpx.get(url, headers=report.UA, follow_redirects=True, timeout=8)
            img = Image.open(io.BytesIO(r.content)).convert("RGB")
        except Exception:
            continue
        if min(img.size) >= 400 and 0.4 <= img.width / img.height <= 2.5:
            img.thumbnail((1600, 1600))
            img.save(out, quality=92)
            return hit.get("url") or url
    return None


def _picture_prep(what):
    if BLOCKED.search(what):
        return None, "I don't get sexual or explicit pictures. Tell the user that plainly and offer something else."
    os.makedirs(PICTURES, exist_ok=True)
    name = "".join(c if c.isalnum() else "-" for c in what.lower())[:40].strip("-") or "picture"
    return os.path.join(PICTURES, f"{name}-{{kind}}{datetime.datetime.now():%H%M%S}"), None


@mcp.tool()
def find_photo(what: str) -> str:
    """Find a REAL photo online and send/show it (in Telegram it arrives in the chat). Use for "send me a photo / pic /
    picture / image of ...", "show me ...": anything that exists (people, places, buildings, products, animals, cars,
    food, flowers). what: the subject in English, specific (e.g. "Taj Mahal at sunset", "red Ferrari SF90")."""
    base, refused = _picture_prep(what)
    if refused:
        return refused
    try:
        out = base.format(kind="") + ".jpg"
        source = _web_photo(what, out)
        if not source:  # freely licensed search as a second try
            meta = _images().search(what, out.replace(".jpg", ".png"))
            out, source = out.replace(".jpg", ".png"), meta.get("page")
        log("find_photo", f"'{what}' from {source}")
        return f"Found a real photo of {what} (from {source}).\n[[show: {out}]]"
    except Exception as e:
        log("find_photo", f"'{what}' failed: {e!r}")
        return f"Couldn't find a photo of {what} right now ({type(e).__name__}). Try different words."


@mcp.tool()
def create_picture(what: str, style: str = "none") -> str:
    """Make a NEW picture with AI and send/show it. ONLY when the user says draw / generate / create / make / paint /
    imagine a picture or image, or the subject can't exist (a chocolate dragon). Not for "send me a photo of" (that is
    find_photo). what: describe the picture in English. style: none (photo-like), flat (vector illustration),
    sketch (pencil), doodle (stick figures)."""
    base, refused = _picture_prep(what)
    if refused:
        return refused
    try:
        out = base.format(kind="ai-") + ".png"
        meta = _images().generate(what, out, style if style in ("none", "flat", "sketch", "doodle") else "none")
        log("create_picture", f"'{what}' via {meta.get('provider')}")
        return f"Made a new picture of {what} with AI ({meta.get('provider')}).\n[[show: {out}]]"
    except Exception as e:
        log("create_picture", f"'{what}' failed: {e!r}")
        return f"Couldn't make that picture right now ({type(e).__name__}). Try again in a bit."


if __name__ == "__main__":
    mcp.run()
