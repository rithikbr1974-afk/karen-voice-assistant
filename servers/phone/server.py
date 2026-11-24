"""Karen's Android phone control, as an MCP server (stdio), over adb wireless debugging (scripts/phone_pair.py once).

Reads the screen through Android's accessibility dump (uiautomator: element names and positions, no screenshots, so a
text-only LLM works). phone_operate does one action per step like the PC screen agent; taps on send/pay/delete/post/
buy/sign-out ask the user first (ctx.elicit) unless Karen already asked (phone_send_whatsapp).
Typing uses `adb input text`: plain ASCII only. Logs: logs/phone_actions.log.
"""
import datetime
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

import yaml
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from pydantic import BaseModel, Field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from openrouter import BUSY, NoVision, chat  # noqa: E402
from core import adbguard  # noqa: E402  (no new adb server while Android Studio / phone mode has adb stopped)

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CFG = (yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8")) or {}).get("phone", {})
mcp = MCPServer("phone")
APPS = {"whatsapp": "com.whatsapp", "whatsapp business": "com.whatsapp.w4b", "youtube": "com.google.android.youtube",
        "youtube music": "com.google.android.apps.youtube.music", "gmail": "com.google.android.gm",
        "chrome": "com.android.chrome", "maps": "com.google.android.apps.maps", "photos": "com.google.android.apps.photos",
        "camera": "android.media.action.STILL_IMAGE_CAMERA", "settings": "com.android.settings",
        "instagram": "com.instagram.android", "spotify": "com.spotify.music", "telegram": "org.telegram.messenger",
        "calendar": "com.google.android.calendar", "phone": "com.google.android.dialer", "files": "com.google.android.documentsui",
        "play store": "com.android.vending", "clock": "com.google.android.deskclock", "messages": "com.google.android.apps.messaging"}
RISKY = re.compile(r"\b(send|pay|payment|buy|order|place order|delete|remove|post|share|publish|sign out|log ?out|"
                   r"uninstall|transfer|confirm)\b", re.I)
UPLOAD_DIR = "/sdcard/Download/Karen"


class Approve(BaseModel):
    approve: bool = Field(description="True to go ahead")


def log(what, detail):
    with open(os.path.join(ROOT, "logs", "phone_actions.log"), "a", encoding="utf-8") as f:
        f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {what:<12} {detail}\n")


def _adb_exe():
    import glob
    return shutil.which("adb") or next(iter(glob.glob(os.path.expandvars(  # winget's folder, if PATH isn't updated yet
        r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Google.PlatformTools*\platform-tools\adb.exe"))), None)


def adb(*args, timeout=20, binary=False):
    exe = _adb_exe()
    if not exe:
        raise RuntimeError("adb isn't installed (winget install Google.PlatformTools).")
    if adbguard.tools_blocked():
        raise RuntimeError(adbguard.MESSAGE)
    device = _dev or _serial("main")
    cmd = [exe] + (["-s", device] if device else []) + list(args)
    r = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=0x08000000)
    return r.stdout if binary else (r.stdout + r.stderr).decode("utf-8", "replace").strip()


_dev = ""  # the phone the current tool call talks to ("" = the main phone)


def _attached():
    """Serials of the phones adb sees as ready."""
    exe = _adb_exe()
    if not exe or adbguard.tools_blocked():
        return []
    out = subprocess.run([exe, "devices"], capture_output=True, text=True, creationflags=0x08000000).stdout
    return re.findall(r"^(\S+)\tdevice$", out, re.M)


def _seconds():
    """phone.second: one adb serial or a list (its USB serial and its Wi-Fi ip:5555)."""
    s = CFG.get("second") or []
    return [str(x) for x in (s if isinstance(s, list) else [s])]


def _serial(which="main"):
    """adb serial of the second phone (the attached one of phone.second; reconnects ip:port over Wi-Fi), or the
    main one (phone.device, else whichever connected phone isn't the second)."""
    seconds = _seconds()
    if which == "second":
        ready = _attached()
        hit = next((s for s in seconds if s in ready), "")
        for s in [] if hit else [s for s in seconds if re.fullmatch(r"[\d.]+:\d+", s)]:
            adb_exe = _adb_exe()
            subprocess.run([adb_exe, "connect", s], capture_output=True, timeout=10, creationflags=0x08000000)
            if s in _attached():
                return s
        return hit or (seconds[0] if seconds else "")
    main = CFG.get("device") or os.environ.get("PHONE_ADB", "")
    if main:
        return main
    others = [d for d in _attached() if d not in seconds]
    return others[0] if others else ""


def use(which=""):
    """Point the following adb calls at the main or the second phone (the spare SIM). Returns an error or ""."""
    global _dev
    second = str(which).lower() in ("second", "2", "other", "old", "spare", "karen")
    _dev = _serial("second") if second else ""
    if second and not _dev:
        return "The second phone isn't set up (phone.second in config.yaml)."
    return ""


def _connected():
    """True if the chosen phone is connected; the main one also tries to reconnect over Wi-Fi (mDNS)."""
    if _dev:
        return _dev in _attached()
    if _serial("main") in _attached():
        return True
    services = adb("mdns", "services")
    for addr in re.findall(r"_adb-tls-connect\._tcp\.?\s+([\d.]+:\d+)", services):
        adb("connect", addr)
    return _serial("main") in _attached()


NOT_CONNECTED = ("Your phone isn't connected. On the phone: Settings > Developer options > Wireless debugging must be "
                 "on and the phone on the same Wi-Fi as the PC (pair once with scripts/phone_pair.py).")
SECOND_OFF = "The second phone isn't connected. Plug it into the PC by USB with USB debugging on."


# ---------------------------------------------------------------- screen reading
def _elements():
    """[(id, label, x, y, clickable)] of what is on the phone screen now, plus the foreground app's package."""
    xml = adb("exec-out", "uiautomator", "dump", "/dev/tty", timeout=25)
    xml = xml[xml.find("<?xml"):xml.rfind("</hierarchy>") + 12]
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return [], ""
    items, pkg = [], ""
    for node in root.iter("node"):
        pkg = pkg or node.get("package", "")
        label = (node.get("text") or node.get("content-desc") or "").strip()
        rid = (node.get("resource-id") or "").split("/")[-1]
        click = node.get("clickable") == "true" or node.get("long-clickable") == "true"
        editable = node.get("class", "").endswith("EditText")
        if not label and not (click and rid):
            continue
        m = re.findall(r"\d+", node.get("bounds", ""))
        if len(m) != 4:
            continue
        x1, y1, x2, y2 = map(int, m)
        if x2 <= x1 or y2 <= y1:
            continue
        kind = "field" if editable else "button" if click else "text"
        items.append((len(items), f"{kind}: {label or rid}"[:90], (x1 + x2) // 2, (y1 + y2) // 2, click or editable))
    return items, pkg


@mcp.tool()
def find_my_phone() -> str:
    """Make the phone ring loudly so the user can find it ("where is my phone?", "ring my phone"). It rings at
    alarm volume, even on silent, until it is stopped on the phone."""
    err = _pick("")
    if err:
        return err
    # a 1-second Clock timer: timers ring at alarm volume and ignore silent mode (standard Android intent)
    out = adb("shell", "am", "start", "-a", "android.intent.action.SET_TIMER",
              "--ei", "android.intent.extra.alarm.LENGTH", "1", "--ez", "android.intent.extra.alarm.SKIP_UI", "true",
              "--es", "android.intent.extra.alarm.MESSAGE", _q("Karen: here I am!"))
    if "Error" in out or "unable" in out.lower():  # no clock app that takes timers: wake the screen and play a ringtone
        adb("shell", "input", "keyevent", "224")
        tones = [t for t in adb("shell", "ls", "/system/media/audio/ringtones").split() if t.endswith((".ogg", ".mp3"))]
        if not tones:
            return "I couldn't make the phone ring (no clock timer or ringtone available)."
        adb("shell", "am", "start", "-a", "android.intent.action.VIEW", "-t", "audio/*",
            "-d", f"file:///system/media/audio/ringtones/{tones[0]}")
        return "Playing a ringtone on your phone. Follow the sound."
    return "Your phone will ring in a second, loud even on silent. Stop the timer on the phone when you find it."


@mcp.tool()
def phone_status() -> str:
    """Whether the phone is connected, its battery, and which app is open."""
    err = _pick("")
    if err:
        return err
    model = adb("shell", "getprop", "ro.product.model")
    battery = re.search(r"level: (\d+)", adb("shell", "dumpsys", "battery"))
    focus = re.search(r"mCurrentFocus=.*?\s(\S+)/", adb("shell", "dumpsys", "window"))
    return (f"Connected to {model}. Battery {battery[1] if battery else '?'}%. "
            f"Open app: {focus[1] if focus else 'home screen'}.")


@mcp.tool()
def phone_open_app(name: str) -> str:
    """Open an app on the user's phone (WhatsApp, YouTube, Gmail, Chrome, Maps, Camera, Settings...)."""
    err = _pick("")
    if err:
        return err
    key = name.lower().strip()
    pkg = APPS.get(key)
    if not pkg:
        packages = [p.replace("package:", "") for p in adb("shell", "pm", "list", "packages").split()]
        hit = [p for p in packages if key.replace(" ", "") in p.lower()] or \
            difflib.get_close_matches(key.replace(" ", ""), packages, n=1, cutoff=0.5)
        pkg = hit[0] if hit else None
    if not pkg:
        return f"I couldn't find an app called {name} on the phone."
    if pkg.startswith("android.media.action"):
        adb("shell", "am", "start", "-a", pkg)
    else:
        adb("shell", "monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1")
    log("open_app", pkg)
    return f"Opened {name} on your phone."


@mcp.tool()
def phone_read_screen() -> str:
    """The text on the phone screen now (e.g. an open WhatsApp chat or a notification shade)."""
    err = _pick("")
    if err:
        return err
    items, pkg = _elements()
    texts = [label.split(": ", 1)[1] for _, label, _, _, _ in items]
    return f"App: {pkg}\n" + "\n".join(dict.fromkeys(texts))[:4000] if texts else "The phone screen has no readable text."


@mcp.tool()
def phone_notifications() -> str:
    """Recent notifications on the phone (app, title, text): new WhatsApp messages, OTP-free summaries, alerts."""
    err = _pick("")
    if err:
        return err
    dump = adb("shell", "dumpsys", "notification", "--noredact", timeout=30)
    out = []
    for block in re.split(r"\n\s*NotificationRecord\(", dump)[1:]:
        pkg = re.search(r"pkg=(\S+)", block)
        title = re.search(r"android\.title=\S*\((.*?)\)\n", block) or re.search(r"android\.title=String \((.*?)\)", block)
        text = re.search(r"android\.text=\S*\((.*?)\)\n", block) or re.search(r"android\.text=String \((.*?)\)", block)
        if pkg and (title or text):
            line = f"{pkg[1].split('.')[-1]}: {title[1] if title else ''} - {text[1] if text else ''}"
            if re.search(r"\b(otp|one.time|verification code|password)\b", line, re.I):
                line = f"{pkg[1].split('.')[-1]}: (a security code, hidden)"
            out.append(line[:200])
    return "\n".join(dict.fromkeys(out))[:3500] or "No notifications."


@mcp.tool()
def phone_screenshot() -> str:
    """Take a screenshot of the phone and show/send it to the user."""
    err = _pick("")
    if err:
        return err
    png = adb("exec-out", "screencap", "-p", binary=True, timeout=30)
    folder = os.path.join(ROOT, "data", "phone")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"phone {datetime.datetime.now():%Y-%m-%d %H.%M.%S}.png")
    with open(path, "wb") as f:
        f.write(png)
    log("screenshot", path)
    return f"Phone screenshot saved.\n[[show: {path}]]"


# ---------------------------------------------------------------- the agent
RULES = """You operate the user's Android phone, ONE action per reply, to reach the goal.
You get the screen's elements, each with an [id]. Reply with ONE JSON object and nothing else:
{"thought": "<few words>", "action": "tap|type|key|scroll|open_app|wait|done|fail", ...}
- tap: {"id": N}   - type: {"id": N or null, "text": "..."} (types into that field; plain ASCII)
- key: {"key": "back|home|enter"}   - scroll: {"direction": "down|up"}   - open_app: {"name": "whatsapp"}
- done: {"summary": "<one short sentence of what you did>"}   - fail: {"reason": "<short>"}
Check the next screen to confirm an action worked. Never repeat an action listed in DONE SO FAR.
Never pay, buy, delete or sign out unless the goal says so."""


def _do(action, items):
    kind = action.get("action")
    target = next((i for i in items if i[0] == action.get("id")), None)
    if kind == "tap" and target:
        adb("shell", "input", "tap", str(target[2]), str(target[3]))
    elif kind == "type":
        if target:
            adb("shell", "input", "tap", str(target[2]), str(target[3]))
            time.sleep(0.4)
        text = re.sub(r"[^\x20-\x7e]", "", str(action.get("text", "")))
        adb("shell", "input", "text", text.replace(" ", "%s").replace("'", "\\'").replace('"', '\\"')
            .replace("&", "\\&").replace("(", "\\(").replace(")", "\\)").replace(";", "\\;"))
    elif kind == "key":
        adb("shell", "input", "keyevent", {"back": "4", "home": "3", "enter": "66"}.get(action.get("key"), "4"))
    elif kind == "scroll":
        up = action.get("direction") == "up"
        adb("shell", "input", "swipe", "540", "700" if up else "1600", "540", "1600" if up else "700", "300")
    elif kind == "open_app":
        phone_open_app(str(action.get("name", "")))
    elif kind == "wait":
        time.sleep(1.5)
    else:
        return "nothing happened (unknown action or id)"
    return "ok"


async def _agent(goal, ctx, preapproved=False):
    import asyncio
    history, result = [], ""
    with open(os.path.join(ROOT, "logs", "phone_actions.log"), "a", encoding="utf-8") as plog:
        plog.write(f"\n===== {datetime.datetime.now():%Y-%m-%d %H:%M:%S} GOAL: {goal}\n")
        for step in range(1, CFG.get("max_steps", 14) + 1):
            items, pkg = await asyncio.to_thread(_elements)
            listing = "\n".join(f"[{i}] {label}" for i, label, _, _, _ in items) or "(nothing readable)"
            raw = await asyncio.to_thread(chat, CFG.get("model"), max_tokens=250, messages=[
                {"role": "system", "content": RULES},
                {"role": "user", "content": f"GOAL: {goal}\nAPP: {pkg}\nSCREEN:\n{listing[:6000]}\n"
                                            f"DONE SO FAR: {'; '.join(history) or 'nothing yet'}\nNext action as JSON:"}])
            try:
                action = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
            except ValueError:
                history.append("(unreadable reply)")
                continue
            kind = action.get("action")
            plog.write(f"--- step {step} [{pkg}] {json.dumps(action, ensure_ascii=False)[:300]}\n")
            if kind in ("done", "fail"):
                result = action.get("summary") or action.get("reason") or ""
                break
            target = next((i for i in items if i[0] == action.get("id")), None)
            what = f"{kind} '{target[1][:40]}'" if target else f"{kind} {action.get('text') or action.get('key') or action.get('name') or ''}"
            if kind == "tap" and sum(h.startswith(what + " ->") for h in history) >= 2:
                history.append(f"(skipped: {what} was already done twice; the goal is probably reached, say done)")
                continue
            if kind == "tap" and target and RISKY.search(target[1]) and not preapproved:
                answer = await ctx.elicit(message=f"On your phone: tap '{target[1].split(': ', 1)[-1][:60]}'?", schema=Approve)
                if not (answer.action == "accept" and answer.data.approve):
                    result = f"Stopped before tapping '{target[1][:60]}', as you said no."
                    break
            outcome = await asyncio.to_thread(_do, action, items)
            history.append(f"{what} -> {outcome}")
            await asyncio.sleep(CFG.get("settle_s", 1.2))
        else:
            result = f"Stopped after {step} steps without finishing."
    log("operate", f"{goal[:80]} -> {len(history)} actions")
    return result or "Done."


@mcp.tool()
async def phone_operate(goal: str, ctx: Context) -> str:
    """Older text-only phone agent; prefer artemis__phone_do when it is offered. Do a task on the user's phone by
    tapping and typing like a person (any app). goal: what to achieve, in full.
    Taps on send/pay/delete/post ask him first."""
    err = _pick("")
    if err:
        return err
    return await _guard(_agent(goal, ctx))


async def _guard(job):
    """The free AI model may be busy (429): say so plainly instead of a bare tool error."""
    try:
        return await job
    except NoVision as e:
        return str(e)
    except Exception as e:
        if getattr(e, "status_code", None) == 429 or "RateLimit" in type(e).__name__:
            return BUSY
        raise


@mcp.tool()
async def phone_send_whatsapp(contact: str, ctx: Context, message: str = "", file: str = "") -> str:
    """Send a WhatsApp message and/or a file from the user's own phone to a contact. file: a path on the PC (e.g. a
    report in Documents/Karen); it is copied to the phone's Download/Karen first. Karen asks him before sending."""
    err = _pick("")
    if err:
        return err
    goal = f"In WhatsApp, open the chat with {contact}"
    if file:
        if not os.path.isfile(file):
            return f"I couldn't find the file {file} on the PC."
        name = os.path.basename(file)
        adb("shell", "mkdir", "-p", UPLOAD_DIR)
        adb("push", file, f"{UPLOAD_DIR}/{name}", timeout=120)
        adb("shell", "am", "broadcast", "-a", "android.intent.action.MEDIA_SCANNER_SCAN_FILE",
            "-d", f"file://{UPLOAD_DIR}/{name}")
        goal += f", attach the document '{name}' (tap the attach/paperclip icon, Document, then look in Download > Karen)"
    if message:
        goal += f", type this message: {message}"
    goal += ", then tap send. Done when it shows as sent."
    phone_open_app("whatsapp")
    time.sleep(2)
    result = await _guard(_agent(goal, ctx, preapproved=True))  # Karen already read it back and he said yes
    log("whatsapp", f"{contact}: {message[:60]} {os.path.basename(file) if file else ''} -> {result[:80]}")
    return result


COMPOSER = (0.915, 0.894)  # the photo/file preview's round send button, as a share of the screen (480x960 phone)


def _focus():
    m = re.search(r"mCurrentFocus=\S+ \S+ (\S+?)\}", adb("shell", "dumpsys", "window", "windows"))
    return m.group(1) if m else ""


def _tap_send(timeout=25):
    """Tap WhatsApp's send button once its screen is up. Found by reading the screen, and remembered per phone and
    screen. The photo preview's "Restyle with AI" animation can keep the screen reader from ever settling, so
    there a known spot is used."""
    spots = json.load(open(SPOT)) if os.path.exists(SPOT) else {}
    end = time.time() + timeout
    while time.time() < end:
        screen = _focus()
        if "com.whatsapp/" not in screen:
            time.sleep(0.7)
            continue
        key = f"wa_send:{_dev or 'main'}:{screen.rsplit('.', 1)[-1]}"
        items, _pkg = _elements()
        hit = next((i for i in items if i[4] and re.search(r":\s*send$", i[1], re.I)), None)  # "button: Send"
        if hit:
            spots[key] = [hit[2], hit[3]]
            json.dump(spots, open(SPOT, "w"))
            adb("shell", "input", "tap", str(hit[2]), str(hit[3]))
            return True
        if "MediaComposer" in screen and time.time() > end - timeout + 6:  # the reader can't settle on this screen
            if key not in spots:
                w, h = map(int, re.findall(r"(\d+)x(\d+)", adb("shell", "wm", "size"))[-1])
                spots[key] = [int(w * COMPOSER[0]), int(h * COMPOSER[1])]
            adb("shell", "input", "tap", *map(str, spots[key]))
            time.sleep(1.5)
            return "MediaComposer" not in _focus()  # the preview closes once it's sent
        time.sleep(1)
    return False


@mcp.tool()
def whatsapp_me(message: str = "", file: str = "") -> str:
    """Send the user himself a WhatsApp from Karen's own number (the second phone): a message and/or a file on the PC
    (a screenshot, photo, PDF, report...). For 'WhatsApp me', 'send me ... on WhatsApp', 'message me'."""
    owner = re.sub(r"\D", "", os.environ.get("OWNER_PHONE", ""))
    if not owner:
        return "OWNER_PHONE isn't set in .env."
    if not message and not file:
        return "Nothing to send."
    err = _pick("second")
    if err:
        return err
    adb("shell", "input", "keyevent", "224")  # wake the screen
    sent = []
    if file:
        if not os.path.isfile(file):
            return f"I couldn't find the file {file} on the PC."
        name = re.sub(r"[^\w.-]", "_", os.path.basename(file))
        adb("shell", "mkdir", "-p", UPLOAD_DIR)
        adb("push", file, f"{UPLOAD_DIR}/{name}", timeout=120)
        ext = name.rsplit(".", 1)[-1].lower()
        mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp",
                "mp4": "video/mp4", "pdf": "application/pdf"}.get(ext, "*/*")
        args = ["shell", "am", "start", "-a", "android.intent.action.SEND", "-t", mime, "-p", "com.whatsapp",
                "--eu", "android.intent.extra.STREAM", _q(f"file://{UPLOAD_DIR}/{name}"),
                "--es", "jid", _q(f"{owner}@s.whatsapp.net")]
        if message:
            args += ["--es", "android.intent.extra.TEXT", _q(message)]
        adb(*args)
        if not _tap_send():
            return "WhatsApp opened with the file, but I couldn't find its send button. Tap send on the second phone."
        sent.append(name)
        time.sleep(4)  # WhatsApp keeps its own copy once sent; don't leave screenshots lying on the phone
        adb("shell", "rm", "-f", _q(f"{UPLOAD_DIR}/{name}"))
        message = "" if mime.startswith(("image/", "video/")) else message  # the caption went with the picture
    if message:
        # a share straight into his chat (jid); the wa.me link got stuck on "Searching…" for his number
        adb("shell", "am", "start", "-a", "android.intent.action.SEND", "-t", "text/plain", "-p", "com.whatsapp",
            "--es", "android.intent.extra.TEXT", _q(message), "--es", "jid", _q(f"{owner}@s.whatsapp.net"))
        if not _tap_send():
            return "WhatsApp opened with the message typed, but I couldn't find its send button."
        sent.append("the message")
    time.sleep(1.5)
    adb("shell", "input", "keyevent", "3")  # home: don't leave WhatsApp open on the chat
    log("wa_me", f"{', '.join(sent)}: {message[:60]}")
    return f"Sent you {' and '.join(sent)} on WhatsApp from my number."


# ---------------------------------------------------------------- calls and SMS (JARVIS batch 4)
# adb's shell user can read contacts, SMS and the call log on his phone (Android 13), so no app or root is needed.
# Calls and SMS sends are confirmed by Karen first (config confirm: phone__phone_call, *send*).
OTP = re.compile(r"\b(otp|one.time|verification|code is|passcode|password|cvv|\d{4,8} is your)\b", re.I)


def _q(s):
    """Quote for the phone's shell: adb joins its arguments into one command line."""
    return "'" + str(s).replace("'", "'\\''") + "'"


def _rows(uri, projection, where="", sort=""):
    """content query -> [dict]. The last projection column may hold commas/newlines (an SMS body)."""
    args = ["shell", "content", "query", "--uri", uri, "--projection", ":".join(projection)]
    if where:
        args += ["--where", _q(where)]
    if sort:
        args += ["--sort", _q(sort)]
    out = adb(*args, timeout=30)
    if "Permission Denial" in out:  # Android 7 and older: adb's shell may not read the call log or SMS
        raise PermissionError(f"This phone's Android doesn't let Karen read {uri.split('/')[2].replace('_', ' ')} "
                              f"over adb.")
    rows = []
    for chunk in re.split(r"(?:^|\n)Row: \d+ ", out)[1:]:
        row, rest = {}, chunk
        for i, col in enumerate(projection):
            rest = rest[len(col) + 1:] if rest.startswith(col + "=") else rest
            if i + 1 < len(projection):
                cut = rest.find(f", {projection[i + 1]}=")
                row[col], rest = rest[:cut], rest[cut + 2:]
            else:
                row[col] = rest.strip()
        rows.append({k: ("" if v == "NULL" else v) for k, v in row.items()})
    return rows


def _digits(number):
    return re.sub(r"\D", "", number)[-10:]


_book = (0, {})


def _contacts():
    """{name: [numbers]} from the main phone's address book (the second phone's if the main one is away; cached
    5 minutes; reading it takes ~1.5 s)."""
    global _book, _dev
    if time.time() - _book[0] < 300:
        return _book[1]
    book, chosen = {}, _dev
    if _dev and _serial("main") in _attached():
        _dev = _serial("main")  # his contacts live on the main phone
    try:
        rows = _rows("content://com.android.contacts/data/phones", ["display_name", "data1"])
    finally:
        _dev = chosen
    for r in rows:
        if r["display_name"] and r["data1"]:
            nums = book.setdefault(r["display_name"], [])
            if _digits(r["data1"]) not in map(_digits, nums):
                nums.append(r["data1"].replace(" ", ""))
    _book = (time.time(), book)
    return book


def _pick(which):
    """Choose the phone for this tool call: "" if it's ready, else what to tell him."""
    err = use(which)
    if err:
        return err
    if not _connected():
        return SECOND_OFF if _dev else NOT_CONNECTED
    return ""


WHICH = "Which phone: leave empty for his main phone; 'second' for the spare-SIM phone (only when he says so)."


def _lookup(who):
    """(name, number) for a contact name or a number, or (None, message) when unclear."""
    if len(re.sub(r"\D", "", who)) >= 6 and not re.search(r"[a-z]{3}", who, re.I):
        return who, re.sub(r"[^\d+]", "", who)
    book = _contacts()
    key = who.lower().strip()
    hits = [n for n in book if n.lower() == key] or [n for n in book if n.lower().startswith(key)] or \
        [n for n in book if key in n.lower()] or difflib.get_close_matches(who, list(book), n=3, cutoff=0.6)
    if not hits:
        return None, f"I couldn't find {who} in your phone's contacts."
    if len(hits) > 1:
        return None, f"Which one: {', '.join(hits[:5])}?"
    name = hits[0]
    if len(book[name]) > 1:
        return None, f"{name} has {len(book[name])} numbers: {', '.join(book[name])}. Which one?"
    return name, book[name][0]


def _names():
    return {_digits(num): name for name, nums in _contacts().items() for num in nums}


def _when(ms):
    return datetime.datetime.fromtimestamp(int(ms) / 1000).strftime("%d %b %I:%M %p")


@mcp.tool()
def phone_find_contact(name: str) -> str:
    """Look up a contact's phone number(s) in the user's phone."""
    err = _pick("")
    if err:
        return err
    book = _contacts()
    key = name.lower().strip()
    hits = [n for n in book if key in n.lower()] or difflib.get_close_matches(name, list(book), n=5, cutoff=0.6)
    return "\n".join(f"{n}: {', '.join(book[n])}" for n in hits[:8]) or f"No contact like {name}."


@mcp.tool()
def phone_call(contact: str, sim: int = 0, which: str = "") -> str:
    """Call a contact (name) or a number from the user's phone. Karen asks him first. Don't ask which SIM: leave
    sim 0 (the phone's default) unless he names SIM 1 or 2. which="second" calls from the spare-SIM phone."""
    err = _pick(which)
    if err:
        return err
    name, number = _lookup(contact)
    if not name:
        return number
    adb("shell", "am", "start", "-a", "android.intent.action.CALL", "-d", _q(f"tel:{number}"))
    time.sleep(2.5)
    items, _ = _elements()  # dual SIM: the phone may ask which SIM to use
    sims = [i for i in items if i[4] and re.search(r"\bsim\s*[12]\b|airtel|jio|vi\b|bsnl|vodafone", i[1], re.I)]
    if len(sims) >= 2:
        pick = sims[min(max(sim, 1), len(sims)) - 1]
        adb("shell", "input", "tap", str(pick[2]), str(pick[3]))
    log("call", f"{name} {number}{' [second]' if _dev else ''}")
    return f"Calling {name}{'' if name == number else f' ({number})'} on your {'second ' if _dev else ''}phone. Say hang up to end it."


def call_state():
    """"dialing" | "active" | "ended" for the chosen phone's call (telecom's own call list)."""
    dump = adb("shell", "dumpsys", "telecom")
    states = re.findall(r"^\s*state:\s*(\w+)", dump, re.M)
    if states:  # newer Android: each live call lists its state
        live = [s for s in states if s not in ("DISCONNECTED", "ABORTED")]
        return "active" if "ACTIVE" in live else "dialing" if live else "ended"
    # Android 7: only an event log per call; the newest call's last state event tells
    last = dump[dump.rfind("Call TC@"):] if "Call TC@" in dump else ""
    events = re.findall(r"\b(SET_DIALING|SET_RINGING|SET_ACTIVE|SET_ON_HOLD|SET_DISCONNECTED)\b", last)
    final = events[-1] if events else "SET_DISCONNECTED"
    return {"SET_ACTIVE": "active", "SET_ON_HOLD": "active", "SET_DISCONNECTED": "ended"}.get(final, "dialing")


SPOT = os.path.join(ROOT, "data", "speaker_spot.json")  # where each phone's in-call Speaker button is


def speakerphone():
    """Tap the in-call screen's Speaker button (not on yet). True if it tapped one. Reading the screen takes ~5 s
    on the old phone, so the button's spot is saved and later calls tap it as soon as the call screen is up."""
    spots = json.load(open(SPOT)) if os.path.exists(SPOT) else {}
    key = _dev or "main"
    if key in spots:
        for _ in range(20):
            if re.search(r"mCurrentFocus=\S+ \S+ \S*InCall", adb("shell", "dumpsys", "window", "windows")):
                adb("shell", "input", "tap", *map(str, spots[key]))
                return True
            time.sleep(0.5)
    for _ in range(4):
        items, _pkg = _elements()
        hit = next((i for i in items if re.search(r"\bspeaker\b", i[1], re.I)), None)
        if hit:
            adb("shell", "input", "tap", str(hit[2]), str(hit[3]))
            spots[key] = [hit[2], hit[3]]
            json.dump(spots, open(SPOT, "w"))
            return True
        time.sleep(1)
    return False


def call_volume_max(presses=10):
    """Turn the in-call volume all the way up (volume keys act on the call while one is going)."""
    for _ in range(presses):
        adb("shell", "input", "keyevent", "24")


@mcp.tool()
def phone_end_call(which: str = "") -> str:
    """Hang up / end / reject the phone call."""
    err = _pick(which)
    if err:
        return err
    adb("shell", "input", "keyevent", "6")  # KEYCODE_ENDCALL
    log("end_call", "")
    return "Call ended."


@mcp.tool()
def phone_answer_call(which: str = "") -> str:
    """Answer (pick up) the phone call that is ringing now."""
    err = _pick(which)
    if err:
        return err
    adb("shell", "input", "keyevent", "5")  # KEYCODE_CALL
    log("answer_call", "")
    return "Answered. Talk on your phone."


@mcp.tool()
def phone_call_log(count: int = 8, missed_only: bool = False, which: str = "") -> str:
    """Recent calls on the phone: who, incoming/outgoing/missed, when, how long."""
    err = _pick(which)
    if err:
        return err
    kinds = {"1": "incoming", "2": "outgoing", "3": "missed", "5": "rejected", "6": "blocked"}
    rows = _rows("content://call_log/calls", ["number", "type", "date", "duration"],
                 where="type=3" if missed_only else "", sort=f"date DESC LIMIT {min(max(count, 1), 30)}")
    names = _names()
    out = [f"{names.get(_digits(r['number']), r['number'] or 'unknown')}: {kinds.get(r['type'], 'call')}, "
           f"{_when(r['date'])}" + (f", {int(r['duration']) // 60}m{int(r['duration']) % 60}s" if r["duration"] not in ("", "0") else "")
           for r in rows]
    return "\n".join(out) or "No calls."


@mcp.tool()
def phone_read_sms(contact: str = "", count: int = 5, unread_only: bool = False, which: str = "") -> str:
    """Read recent SMS text messages on the phone, optionally only from one contact or only unread.
    Security codes (OTPs) are hidden."""
    err = _pick(which)
    if err:
        return err
    where = ["read=0"] if unread_only else []
    if contact:
        name, number = _lookup(contact)
        if not name:
            return number
        where.append(f"address LIKE '%{_digits(number)}'")
    rows = _rows("content://sms/inbox", ["address", "date", "read", "body"], where=" AND ".join(where),
                 sort=f"date DESC LIMIT {min(max(count, 1), 20)}")
    names = _names()
    out = []
    for r in rows:
        sender = names.get(_digits(r["address"]), r["address"])
        body = "(a security code, hidden)" if OTP.search(r["body"]) else " ".join(r["body"].split())[:300]
        out.append(f"{sender}, {_when(r['date'])}{'' if r['read'] == '1' else ' (unread)'}: {body}")
    return "\n".join(out) or "No messages."


@mcp.tool()
def phone_send_sms(contact: str, message: str, which: str = "") -> str:
    """Send an SMS text message from the user's phone to a contact (name) or number. Karen reads it back first.
    which="second" sends from the spare-SIM phone (only when he asks for the other/second number)."""
    err = _pick(which)
    if err:
        return err
    name, number = _lookup(contact)
    if not name:
        return number
    started = int(time.time() * 1000)
    adb("shell", "am", "start", "-a", "android.intent.action.SENDTO", "-d", _q(f"smsto:{number}"),
        "--es", "sms_body", _q(message), "--ez", "exit_on_sent", "true")
    send = None
    for _ in range(6):  # the Messages app takes a moment to open
        time.sleep(1)
        items, _pkg = _elements()
        send = next((i for i in items if i[4] and re.search(r"\bsend\b", i[1], re.I) and "button" in i[1]), None)
        if send:
            break
    if not send:
        return "The Messages app opened with the text typed in, but I couldn't find its send button. Tap send on the phone."
    adb("shell", "input", "tap", str(send[2]), str(send[3]))
    time.sleep(3)
    try:
        sent = _rows("content://sms/sent", ["address", "date"], where=f"date>{started}", sort="date DESC LIMIT 3")
    except PermissionError:  # can't check on old Android: the tap went through, trust it
        log("sms", f"[second] {name} {number}: {message[:60]} -> tapped send (can't verify)")
        return f"I tapped send to {name}. This phone doesn't let me check it went, so glance at it if it matters."
    ok = any(_digits(r["address"]) == _digits(number) for r in sent)
    log("sms", f"{'[second] ' if _dev else ''}{name} {number}: {message[:60]} -> {'sent' if ok else 'not confirmed'}")
    return (f"Sent the SMS to {name}{' from the second phone' if _dev else ''}." if ok else
            "I tapped send, but the phone hasn't shown it as sent yet. Check the Messages app.")


if __name__ == "__main__":
    mcp.run()
