"""Karen's PC control, as an MCP server (stdio). Windows only.

Safety: the tools here run no shell or arbitrary commands. Apps are opened only from the Start Menu list (plus
config.yaml -> pc_control.apps) and closed only if they match that list, by asking their windows to close (unsaved
work gets the app's normal "save?" prompt). Files are opened, moved and deleted (to the Recycle Bin) only inside
user folders, never programs or scripts. Keys that open Run/Search/Start are blocked and nothing is typed, pressed
or clicked into terminals. Every action is logged to logs/pc_actions.log.

The one exception is power_mode (optional): Open Interpreter writes and runs code. Karen asks before the
task starts, power_mode.py asks again (MCP elicitation) before any step that deletes, kills, installs, downloads,
starts programs or changes system settings, and every code block is logged to logs/power_mode.log.
"""
import base64
import ctypes
import datetime
import difflib
import json
import os
import re
import subprocess
import sys
import time
import webbrowser
from ctypes import wintypes
from urllib.parse import quote_plus

import psutil
import yaml
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from pydantic import BaseModel, Field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from openrouter import chat, friendly  # noqa: E402  (servers/openrouter.py: vision with a free fallback)

ctypes.windll.shcore.SetProcessDpiAwareness(2)  # real pixels, so UI Automation, mouse and screenshots agree at 125% scaling
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CFG = (yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8")) or {}).get("pc_control", {})
LOG = os.path.join(ROOT, "logs", "pc_actions.log")
HOME = os.path.expanduser("~")
KNOWN_FOLDERS = {"Desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641", "Documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
                 "Downloads": "374DE290-123F-4565-9164-39C4925E467B", "Pictures": "33E28130-4E1E-4676-835A-98395C3BC3BB",
                 "Music": "4BD8D571-6D19-48D3-BE97-422220080E43", "Videos": "18989B1D-99B5-455B-841C-AB7C74E4DDFC"}


def known_folder(name):
    """Where Windows really keeps a user folder (OneDrive or a moved Desktop can live on another drive)."""
    import uuid
    guid = uuid.UUID(KNOWN_FOLDERS[name])
    buf = ctypes.c_wchar_p()
    ok = ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(ctypes.c_buffer(guid.bytes_le, 16)), 0, None,
                                                    ctypes.byref(buf)) == 0
    path = buf.value if ok else os.path.join(HOME, name)
    ctypes.windll.ole32.CoTaskMemFree(buf)
    return path


USER_DIRS = [p for p in dict.fromkeys(known_folder(n) for n in KNOWN_FOLDERS) if os.path.isdir(p)]
NEVER_OPEN = {".exe", ".bat", ".cmd", ".ps1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".msi", ".msp", ".lnk",
              ".scr", ".com", ".jar", ".py", ".pyw", ".reg", ".hta", ".cpl", ".url", ".appref-ms"}
PROTECTED = {"explorer.exe", "python.exe", "pythonw.exe", "msedgewebview2.exe", "svchost.exe", "csrss.exe",
             "winlogon.exe", "dwm.exe", "system", "lsass.exe", "services.exe", "uvx.exe", "uv.exe"}

# Start Menu apps Karen must never open: with type_text they would allow arbitrary commands.
BLOCKED_APPS = ("powershell", "command prompt", "terminal", "cmd", "wsl", "bash", "ubuntu", "native tools",
                "registry", "regedit", "uninstall", "group policy", "task scheduler", "services", "windows tools",
                "computer management", "run")
ALIASES = {"vs code": "visual studio code", "vscode": "visual studio code", "code": "visual studio code",
           "edge": "microsoft edge", "files": "file explorer", "explorer": "file explorer",
           "chrome": "google chrome", "teams": "microsoft teams", "store": "microsoft store"}

mcp = MCPServer("pc")


def _clip(text=None, tries=10):
    """Read (no argument) or set the clipboard, retrying: other apps (e.g. Typeless) briefly lock it."""
    import pyperclip
    for i in range(tries):
        try:
            if text is None:
                return pyperclip.paste()
            pyperclip.copy(text)
            return True
        except pyperclip.PyperclipException:
            time.sleep(0.1 * (i + 1))
    return "" if text is None else False


def _type(text):
    """Type text into the focused window: paste via the clipboard (any language), or press the keys one by one
    when another app has the clipboard stuck."""
    import pyautogui
    old = _clip()
    if _clip(text):
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.3)
        _clip(old)
        return "pasted"
    if text.isascii():
        pyautogui.write(text, interval=0.01)
        return "typed key by key (clipboard busy)"
    return "failed: the clipboard is busy and the text isn't plain English"


def blocked(name):
    """Short entries ("cmd", "run") match whole words; longer ones match anywhere in the name."""
    words = set(name.split())
    return any(b in name if len(b) > 3 else b in words for b in BLOCKED_APPS)


def log(action, detail=""):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {action:<12} {detail}\n")


# ---------------------------------------------------------------- apps
_apps = None


def apps():
    """Installed apps from the Start Menu (name -> AppID), plus config.yaml pc_control.apps (name -> target)."""
    global _apps
    if _apps is None:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress"],
                             capture_output=True, text=True, timeout=30, creationflags=0x08000000).stdout
        found = json.loads(out or "[]")
        found = found if isinstance(found, list) else [found]
        _apps = {a["Name"].lower(): ("appid", a["AppID"]) for a in found if a.get("Name") and a.get("AppID")
                 and not blocked(a["Name"].lower())}
        for name, target in (CFG.get("apps") or {}).items():
            _apps[str(name).lower()] = ("target", str(target))
    return _apps


def match_app(name):
    name = name.lower().strip()
    name = ALIASES.get(name, name)
    if blocked(name):
        return None  # never drift from a blocked request to a "close" match
    names = list(apps())
    if name in names:
        return name
    contains = sorted((n for n in names if name in n), key=len)
    if contains:
        return contains[0]
    close = difflib.get_close_matches(name, names, n=1, cutoff=0.6)
    return close[0] if close else None


@mcp.tool()
def open_app(name: str) -> str:
    """Open an installed app by name, e.g. "chrome", "spotify", "calculator", "word"."""
    found = match_app(name)
    if not found:
        return f"No installed app matches '{name}'."
    kind, target = apps()[found]
    os.startfile(f"shell:AppsFolder\\{target}" if kind == "appid" else target)
    log("open_app", f"{name} -> {found}")
    return f"Opened {found}."


KNOWN_EXES = {"google chrome": "chrome.exe", "microsoft edge": "msedge.exe", "visual studio code": "code.exe",
              "firefox": "firefox.exe", "brave": "brave.exe", "spotify": "spotify.exe", "whatsapp": "whatsapp.exe",
              "word": "winword.exe", "excel": "excel.exe", "powerpoint": "powerpnt.exe", "notepad": "notepad.exe",
              "microsoft teams": "ms-teams.exe", "telegram": "telegram.exe", "discord": "discord.exe"}


def _exe_names(app):
    kind, target = apps()[app]
    exe = os.path.basename(target).lower()
    if exe.endswith(".exe"):
        return {exe}
    if app in KNOWN_EXES:
        return {KNOWN_EXES[app]}
    words = [w for w in app.split() if len(w) > 3 and w not in ("microsoft", "google", "windows")]
    return {p.info["name"].lower() for p in psutil.process_iter(["name"])
            if p.info["name"] and any(w in p.info["name"].lower() for w in words)}


@mcp.tool()
def close_app(name: str) -> str:
    """Close an app by name (also web apps like YouTube Music). Its windows are asked to close, so it can prompt
    to save unsaved work."""
    found = match_app(name)
    exes = (_exe_names(found) if found else set()) - PROTECTED
    pids = {p.pid for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() in exes}
    title = (found or name).lower()  # installed web apps run inside the browser: match their window title too
    user32 = ctypes.windll.user32
    closed = []
    for w in _windows():
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(w._hWnd, ctypes.byref(pid))
        if pid.value in pids or (title in w.title.lower() and w.title.strip().upper() != "KAREN"):
            user32.PostMessageW(w._hWnd, 0x0010, 0, 0)  # WM_CLOSE
            closed.append(w.title)
    log("close_app", f"{name} -> {found} ({len(closed)} windows)")
    if closed:
        return f"Asked {found or name} to close."
    return f"No installed app matches '{name}'." if not found else f"{found} doesn't seem to be open."


# ---------------------------------------------------------------- sound and media
def _speakers():
    import comtypes
    from pycaw.pycaw import AudioUtilities
    comtypes.CoInitialize()
    return AudioUtilities.GetSpeakers().EndpointVolume


@mcp.tool()
def set_volume(level: int | None = None, change: int | None = None) -> str:
    """Set the speaker volume to level (0-100), or change it by a step like +10 or -10."""
    vol = _speakers()
    now = round(vol.GetMasterVolumeLevelScalar() * 100)
    new = max(0, min(100, level if level is not None else now + (change or 0)))
    vol.SetMasterVolumeLevelScalar(new / 100, None)
    vol.SetMute(0, None)
    log("set_volume", f"{now} -> {new}")
    return f"Volume is now {new}%."


@mcp.tool()
def mute(on: bool = True) -> str:
    """Mute (on=true) or unmute (on=false) the speakers."""
    _speakers().SetMute(1 if on else 0, None)
    log("mute", str(on))
    return "Muted." if on else "Unmuted."


@mcp.tool()
def media(action: str) -> str:
    """Control music/video playing anywhere: "play_pause", "next" or "previous"."""
    import pyautogui
    key = {"play": "playpause", "pause": "playpause", "play_pause": "playpause", "resume": "playpause",
           "next": "nexttrack", "previous": "prevtrack", "prev": "prevtrack"}.get(action.lower())
    if not key:
        return "Use play_pause, next or previous."
    pyautogui.press(key)
    log("media", action)
    return f"Pressed {action.replace('_', '/')}."


# ---------------------------------------------------------------- web
BROWSER_EXES ={"chrome": "chrome.exe", "edge": "msedge.exe", "firefox": "firefox.exe", "brave": "brave.exe"}


def _browse(url, browser=""):
    """Open url in a specific browser if asked (registry App Paths), else the default one."""
    exe = BROWSER_EXES.get(browser.lower().replace("google ", "").replace("microsoft ", "").strip())
    if exe:
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                path = winreg.QueryValue(hive, rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{exe}")
                if os.path.exists(path):
                    subprocess.Popen([path, url], creationflags=0x08000000)
                    return True
            except OSError:
                pass
    webbrowser.open(url)
    return not exe  # False: that browser isn't installed, so the default one was used


@mcp.tool()
def open_url(url: str, browser: str = "") -> str:
    """Open a website. browser: "chrome", "edge", "firefox" or "brave" (empty = the default browser, Edge)."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    ok = _browse(url, browser)
    log("open_url", f"{url} {browser}")
    return f"Opened {url}" + (f" in {browser}." if browser and ok else " (that browser isn't installed, so in the default one)." if browser else ".")


@mcp.tool()
def web_search(query: str, site: str = "google", browser: str = "") -> str:
    """Search the web in the browser. site: "google" or "youtube". browser: like open_url."""
    url = (f"https://www.youtube.com/results?search_query={quote_plus(query)}" if site.lower() == "youtube"
           else f"https://www.google.com/search?q={quote_plus(query)}")
    _browse(url, browser)
    log("web_search", f"{site}: {query} {browser}")
    return f"Searching {site} for {query}."


# ---------------------------------------------------------------- system
@mcp.tool()
def system_status() -> str:
    """Battery, CPU, memory, disk space and the apps using the most memory."""
    bat = psutil.sensors_battery()
    disk = psutil.disk_usage(os.environ.get("SystemDrive", "C:") + "\\")
    top = sorted(psutil.process_iter(["name", "memory_info"]), key=lambda p: p.info["memory_info"].rss
                 if p.info["memory_info"] else 0, reverse=True)[:3]
    up = datetime.timedelta(seconds=int(time.time() - psutil.boot_time()))
    parts = [f"CPU {psutil.cpu_percent(interval=0.5):.0f}%",
             f"memory {psutil.virtual_memory().percent:.0f}% used",
             f"disk {disk.free / 2**30:.0f} GB free of {disk.total / 2**30:.0f} GB",
             f"up {up.days}d {up.seconds // 3600}h"]
    if bat:
        parts.insert(0, f"battery {bat.percent:.0f}% ({'charging' if bat.power_plugged else 'on battery'})")
    parts.append("most memory: " + ", ".join(p.info["name"] for p in top))
    return "; ".join(parts)


@mcp.tool()
def find_file(name: str) -> str:
    """Find files by name in Desktop, Documents, Downloads, Pictures, Music and Videos (newest first)."""
    want = name.lower()
    hits = []
    for base in USER_DIRS:
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "__pycache__")]
            hits += [os.path.join(root, f) for f in files if want in f.lower()]
            if len(hits) > 200:
                break
    hits.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    log("find_file", f"{name}: {len(hits)} found")
    if not hits:
        return f"No files named like '{name}' in your user folders."
    return "\n".join(f"{p} (modified {datetime.datetime.fromtimestamp(os.path.getmtime(p)):%d %b %Y})"
                     for p in hits[:8])


@mcp.tool()
def open_file(path: str) -> str:
    """Open a document, picture, video etc. from the user's folders with its usual app (not programs)."""
    full = resolve(path)
    if not any(full.lower().startswith(d.lower() + os.sep) for d in USER_DIRS):
        return "I can only open files from your Desktop, Documents, Downloads, Pictures, Music or Videos."
    if os.path.splitext(full)[1].lower() in NEVER_OPEN:
        return "For safety I don't open programs, scripts or shortcuts."
    if not os.path.isfile(full):
        return "That file doesn't exist."
    os.startfile(full)
    log("open_file", full)
    return f"Opened {os.path.basename(full)}."


# ---------------------------------------------------------------- keyboard and clipboard
@mcp.tool()
def type_text(text: str) -> str:
    """Type text into whatever window is focused (pasted via the clipboard, so any language works)."""
    how = _type(text)
    log("type_text", f"{len(text)} chars, {how}")
    return "Typed it." if not how.startswith("failed") else "I couldn't type it: another app has the clipboard stuck."


@mcp.tool()
def clipboard(text: str | None = None) -> str:
    """Read the clipboard, or set it when text is given."""
    if text is not None:
        _clip(text)
        log("clipboard", f"set {len(text)} chars")
        return "Copied to the clipboard."
    content = _clip()
    log("clipboard", "read")
    return content[:2000] if content else "The clipboard is empty."


# ---------------------------------------------------------------- screen
@mcp.tool()
def screenshot(describe: bool = False) -> str:
    """Save a screenshot to Pictures/Karen. describe=true also says what is on screen (sent to a vision model)."""
    import mss
    import mss.tools
    folder = os.path.join(known_folder("Pictures"), "Karen")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"screenshot {datetime.datetime.now():%Y-%m-%d %H.%M.%S}.png")
    with (mss.MSS() if hasattr(mss, "MSS") else mss.mss()) as s:
        shot = s.grab(s.monitors[1])
        png = mss.tools.to_png(shot.rgb, shot.size, output=path) or open(path, "rb").read()
    log("screenshot", path + (" (described)" if describe else ""))
    if not describe:
        return f"Saved a screenshot to Pictures/Karen.\n[[show: {path}]]"
    answer = chat(CFG.get("vision", {}).get("model"), max_tokens=250, timeout=40, messages=[{"role": "user", "content": [
            {"type": "text", "text": "Describe what is on this computer screen in two or three plain sentences, "
                                     "mentioning the main app and any important text."},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}}]}])
    return f"Saved to Pictures/Karen. On screen: {answer}\n[[show: {path}]]"


EXPLAIN = ("This is the user's screen. Find the error, warning or problem on it (code, terminal, app or website) and "
           "explain it for speaking aloud: what went wrong and the most likely fix, in two or three short plain "
           "sentences. {question}\nReply with ONE JSON object: {{\"answer\": \"...\", \"file\": \"<file path or name "
           "the error points to, or empty>\", \"line\": <line number or 0>}}")


@mcp.tool()
@friendly
def explain_error(question: str = "") -> str:
    """Look at the screen and explain the error/problem shown and how to fix it (code, terminal, app, website).
    If it points to a file and line, Karen can open it in VS Code (dev open_in_editor)."""
    import io
    import mss
    from PIL import Image
    with (mss.MSS() if hasattr(mss, "MSS") else mss.mss()) as s:
        shot = s.grab(s.monitors[1])
    im = Image.frombytes("RGB", shot.size, shot.rgb)
    im.thumbnail((1600, 1600))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    raw = chat(CFG.get("vision", {}).get("model"), max_tokens=350, messages=[{"role": "user", "content": [
        {"type": "text", "text": EXPLAIN.format(question=f"They asked: {question}" if question else "")},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()}}]}])
    try:
        found = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
    except ValueError:
        found = {"answer": raw}
    log("explain", f"{found.get('file') or '-'}:{found.get('line') or ''}")
    answer = str(found.get("answer") or "I couldn't spot an error on the screen.")
    if found.get("file"):
        answer += (f"\nIt points to {found['file']}" + (f" line {found['line']}" if found.get("line") else "") +
                   ". Offer to open it in VS Code at that line.")
    return answer


# ---------------------------------------------------------------- power
@mcp.tool()
def lock() -> str:
    """Lock the PC (back to the sign-in screen)."""
    log("lock")
    ctypes.windll.user32.LockWorkStation()
    return "Locked."


@mcp.tool()
def sleep() -> str:
    """Put the PC to sleep."""
    log("sleep")
    ctypes.windll.powrprof.SetSuspendState(False, False, False)
    return "Going to sleep."


@mcp.tool()
def shutdown() -> str:
    """Shut down the PC in 10 seconds."""
    log("shutdown")
    subprocess.run(["shutdown", "/s", "/t", "10"], creationflags=0x08000000)
    return "Shutting down in 10 seconds."


@mcp.tool()
def restart() -> str:
    """Restart the PC in 10 seconds."""
    log("restart")
    subprocess.run(["shutdown", "/r", "/t", "10"], creationflags=0x08000000)
    return "Restarting in 10 seconds."


# ---------------------------------------------------------------- desktop: windows, keys, clicks
TERMINALS = {"cmd.exe", "powershell.exe", "pwsh.exe", "windowsterminal.exe", "wsl.exe", "bash.exe", "conhost.exe",
             "openconsole.exe", "mintty.exe"}
BLOCKED_KEYS = {"win", "win+r", "win+s", "win+x", "win+q", "ctrl+esc"}  # Run / Search / Start: typed text could run commands


def _foreground_exe():
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    pid = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        return psutil.Process(pid.value).name().lower()
    except psutil.Error:
        return ""


def _real_window(hwnd):
    """Skip tool windows and hidden ("cloaked") ones like Windows Input Experience."""
    # tool windows, click-through overlays (e.g. Typeless's "Status") and windows that never take focus
    if ctypes.windll.user32.GetWindowLongW(hwnd, -20) & (0x80 | 0x20 | 0x8000000):  # TOOLWINDOW|TRANSPARENT|NOACTIVATE
        return False
    cloaked = ctypes.c_int(0)
    ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))  # DWMWA_CLOAKED
    return not cloaked.value


def _focus(hwnd):
    """Bring a window to the front, even if minimized. Windows only lets a background program take the focus
    right after a key press, so tap Alt first (the usual trick). Returns True if it worked."""
    u = ctypes.windll.user32
    if u.IsIconic(hwnd):
        u.ShowWindow(hwnd, 9)  # SW_RESTORE
    u.keybd_event(0x12, 0, 0, 0)
    u.keybd_event(0x12, 0, 2, 0)
    u.SetForegroundWindow(hwnd)
    time.sleep(0.3)
    return u.GetForegroundWindow() == hwnd


def _windows():
    import pygetwindow as gw
    return [w for w in gw.getAllWindows() if w.title.strip() and w.visible and w.width > 50
            and w.title != "Program Manager" and _real_window(w._hWnd)]


def _find_window(title):
    """Window whose title contains the words asked for (all of them), else a close spelling match."""
    wins = sorted(_windows(), key=lambda w: bool(w.isMinimized))  # visible ones first
    title = title.lower().strip()
    words = title.split()
    hit = next((w for w in wins if title in w.title.lower()), None) or \
        next((w for w in wins if words and all(word in w.title.lower() for word in words)), None)
    if not hit:
        close = difflib.get_close_matches(title, [w.title.lower() for w in wins], n=1, cutoff=0.7)
        hit = next((w for w in wins if close and w.title.lower() == close[0]), None)
    return hit


@mcp.tool()
def list_windows() -> str:
    """List the open windows; the one in front is marked."""
    import pygetwindow as gw
    active = gw.getActiveWindow()
    return "\n".join(("* " if active and w._hWnd == active._hWnd else "  ") + w.title[:80] for w in _windows()) or "No windows open."


@mcp.tool()
def window(title: str, action: str = "focus") -> str:
    """Do something to an open window found by (part of) its title.
    action: focus, minimize, maximize, restore, close (asks to save), snap_left, snap_right."""
    import pyautogui
    w = _find_window(title)
    if not w:
        return f"No open window matches '{title}'."
    action = action.lower()
    if action in ("focus", "snap_left", "snap_right"):
        _focus(w._hWnd)
        if action != "focus":
            time.sleep(0.2)
            pyautogui.hotkey("win", "left" if action == "snap_left" else "right")
    elif action in ("minimize", "maximize", "restore", "close"):
        getattr(w, action)()
    else:
        return "Use focus, minimize, maximize, restore, close, snap_left or snap_right."
    log("window", f"{action} {w.title[:60]}")
    return f"{action.replace('_', ' ').capitalize()} {w.title[:60]}."


@mcp.tool()
def show_desktop() -> str:
    """Minimize everything and show the desktop (run again to bring the windows back)."""
    import pyautogui
    pyautogui.hotkey("win", "d")
    log("show_desktop")
    return "Showing the desktop."


@mcp.tool()
def press_keys(keys: str) -> str:
    """Press a key or shortcut in the window in front, e.g. "ctrl+c", "alt+tab", "ctrl+shift+t", "enter", "f5".
    Run, Search and Start shortcuts are blocked, and nothing is pressed into terminals."""
    import pyautogui
    combo = keys.lower().replace(" ", "").replace("windows", "win").replace("control", "ctrl")
    if combo in BLOCKED_KEYS:
        return "For safety I don't open Run, Search or Start with the keyboard. Use open_app instead."
    if _foreground_exe() in TERMINALS:
        return "A terminal is in front; for safety I don't press keys into terminals."
    parts = [{"win": "winleft", "esc": "escape", "del": "delete", "pgdn": "pagedown", "pgup": "pageup"}.get(k, k)
             for k in combo.split("+")]
    bad = [k for k in parts if k not in pyautogui.KEYBOARD_KEYS]
    if bad:
        return f"Unknown key: {', '.join(bad)}."
    pyautogui.hotkey(*parts)
    log("press_keys", combo)
    return f"Pressed {keys}."


@mcp.tool()
def scroll(direction: str = "down", amount: int = 5) -> str:
    """Scroll the window under the mouse up or down by a number of steps."""
    import pyautogui
    pyautogui.scroll((1 if direction.lower() == "up" else -1) * max(1, min(amount, 30)) * 120)
    return f"Scrolled {direction}."


def _controls(root, depth=0, limit=400):
    """Visible controls in a window, breadth-limited so huge pages stay fast."""
    out, stack = [], [(root, 0)]
    while stack and len(out) < limit:
        ctl, d = stack.pop(0)
        for child in ctl.GetChildren():
            if not child.IsOffscreen:
                out.append(child)
                if d < 12:
                    stack.append((child, d + 1))
    return out


@mcp.tool()
def click(name: str) -> str:
    """Click a button, menu, tab, link or list item by the text you see on it, in the window in front.
    Example: "Save", "File", "Downloads", "Sign in"."""
    import uiautomation as auto
    with auto.UIAutomationInitializerInThread():
        root = auto.GetForegroundControl().GetTopLevelControl()
        if _foreground_exe() in TERMINALS:
            return "A terminal is in front; for safety I don't click in terminals."
        want = name.lower().strip()
        found = [c for c in _controls(root) if c.Name and want in c.Name.lower()]
        if not found:
            return f"Nothing called '{name}' is visible in {root.Name[:60]}."
        best = min(found, key=lambda c: (c.Name.lower() != want, len(c.Name)))
        for getter, action in (("GetInvokePattern", "Invoke"), ("GetSelectionItemPattern", "Select"),
                               ("GetTogglePattern", "Toggle"), ("GetExpandCollapsePattern", "Expand")):
            try:
                pattern = getattr(best, getter)()
                if pattern:
                    getattr(pattern, action)()
                    break
            except Exception:
                continue
        else:
            best.Click(simulateMove=False)  # no accessibility action: click its centre
        log("click", f"{best.ControlTypeName} '{best.Name[:50]}' in {root.Name[:40]}")
        return f"Clicked {best.ControlTypeName.replace('Control', '').lower()} '{best.Name[:50]}'."


@mcp.tool()
def read_window() -> str:
    """Read the visible text of the window in front (titles, labels, buttons, text)."""
    import uiautomation as auto
    with auto.UIAutomationInitializerInThread():
        root = auto.GetForegroundControl().GetTopLevelControl()
        seen, lines = set(), [f"Window: {root.Name[:100]}"]
        for c in _controls(root, limit=600):
            text = (c.Name or "").strip()
            if text and text not in seen and len(text) > 1:
                seen.add(text)
                lines.append(text[:200])
        log("read_window", root.Name[:60])
        return "\n".join(lines)[:2500]


@mcp.tool()
def read_page(window: str = "") -> str:
    """Full text of the web page or document that is open (or in the named window), so it can be summarised,
    translated or questioned: "summarise this article", "what does this page say about fees", "translate this"."""
    w = _find_window(window) if window else None
    if window and not w:
        return f"No open window matches '{window}'."
    hwnd = w._hWnd if w else _target_window()
    if not hwnd:
        return "No window is open."
    title, text = _page_text(hwnd)
    limit = CFG.get("read_page_chars", 9000)
    log("read_page", f"{title[:60]} ({len(text)} chars)")
    return f"{title}\n\n{text[:limit]}" + ("\n...(rest of the page cut)" if len(text) > limit else "")


def _page_text(hwnd):
    """(window title, cleaned text) of the page or document in a window."""
    import uiautomation as auto
    with auto.UIAutomationInitializerInThread():
        root = auto.ControlFromHandle(hwnd)
        # browsers, Word and PDF readers expose the page as a Document; Edge has several (side panes, empty web
        # views), so read every document and keep the one with the most text
        text, stack = "", [(root, 0)]
        while stack:
            ctl, depth = stack.pop(0)
            try:
                kids = ctl.GetChildren()
            except Exception:
                continue
            for k in kids:
                if k.ControlTypeName == "DocumentControl":
                    try:
                        t = k.GetTextPattern().DocumentRange.GetText(-1)
                        text = t if len(t) > len(text) else text
                    except Exception:
                        pass
                if depth < 40:
                    stack.append((k, depth + 1))
        if len(text.strip()) < 200:  # no text pattern: gather the visible names instead
            text = "\n".join(c.Name for c in _controls(root, limit=1500) if c.Name and len(c.Name) > 2)
        title = root.Name
    text = text.replace("￼", "")  # placeholders where images and icons sit
    return title, re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()


# ---------------------------------------------------------------- clipboard history (memory only)
CLIP_HISTORY = []  # newest last; never written to disk, gone when Karen quits
SECRET_LIKE = re.compile(r"^(?=.*\d)(?=.*[A-Za-z])\S{8,}$|^(sk|ghp|gho|xox[bp]|AKIA|AIza|GOCSPX|eyJ)[-_A-Za-z0-9.]{10,}")


def _watch_clipboard():
    last = None
    while True:
        text = _clip(tries=1)
        secret = SECRET_LIKE.match(text.strip()) and not text.strip().startswith(("http://", "https://"))
        if text and text != last and not secret:  # skip passwords, keys and tokens (links are fine)
            if text in CLIP_HISTORY:
                CLIP_HISTORY.remove(text)
            CLIP_HISTORY.append(text[:5000])
            del CLIP_HISTORY[:-30]
        last = text or last
        time.sleep(1.5)


@mcp.tool()
def clipboard_history(count: int = 10, search: str = "") -> str:
    """Text the user copied recently (newest first, numbered), optionally only items containing some words.
    Passwords and keys are never kept."""
    items = [(i, t) for i, t in enumerate(reversed(CLIP_HISTORY), 1) if not search or search.lower() in t.lower()]
    if not items:
        return "Nothing copied yet." if not CLIP_HISTORY else f"Nothing copied matches '{search}'."
    return "\n".join(f"{i}. {t[:200]}" + ("..." if len(t) > 200 else "") for i, t in items[:max(1, min(count, 30))])


@mcp.tool()
def copy_from_history(number: int) -> str:
    """Put an earlier copied item back on the clipboard (number from clipboard_history), ready to paste."""
    items = list(reversed(CLIP_HISTORY))
    if not 1 <= number <= len(items):
        return "There's no item with that number."
    return "Copied it again: press ctrl+v (or ask me to paste)." if _clip(items[number - 1]) else \
        "Another app has the clipboard stuck right now."


# ---------------------------------------------------------------- free up space
def _junk_places():
    la, home = os.environ["LOCALAPPDATA"], os.path.expanduser("~")
    return {"temp files": os.path.join(la, "Temp"), "crash dumps": os.path.join(la, "CrashDumps"),
            "uv cache": os.path.join(la, "uv", "cache"), "pip cache": os.path.join(la, "pip", "cache"),
            "npm cache": os.path.join(la, "npm-cache"),
            "chrome cache": os.path.join(la, r"Google\Chrome\User Data\Default\Cache"),
            "edge cache": os.path.join(la, r"Microsoft\Edge\User Data\Default\Cache"),
            "hugging face cache": os.path.join(home, ".cache", "huggingface")}


_junk_cache = {}


def _dir_size(path):
    total = 0
    for root, _, files in os.walk(path, onerror=lambda e: None):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def _recycle_bin():
    class Info(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_ulong), ("size", ctypes.c_longlong), ("items", ctypes.c_longlong)]
    info = Info()
    info.cbSize = ctypes.sizeof(Info)
    ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(info))
    return info.size, info.items


@mcp.tool()
def junk_scan() -> str:
    """How much space junk takes on C: (temp files, caches, crash dumps, Recycle Bin) and how much is free.
    Takes about a minute the first time."""
    import shutil
    if time.time() - _junk_cache.get("at", 0) > 600:
        _junk_cache.update({name: _dir_size(p) for name, p in _junk_places().items() if os.path.isdir(p)}, at=time.time())
    sizes = {k: v for k, v in _junk_cache.items() if k != "at" and v > 5 * 2**20}
    rb, items = _recycle_bin()
    free = shutil.disk_usage("C:\\").free
    lines = [f"{k}: {v / 2**30:.2f} GB" for k, v in sorted(sizes.items(), key=lambda kv: -kv[1])]
    lines.append(f"recycle bin: {rb / 2**30:.2f} GB ({items} items)")
    lines.append("Windows Update leftovers need admin: suggest open_settings('storage') then Temporary files.")
    log("junk_scan", f"{sum(sizes.values()) / 2**30:.1f} GB junk, C: free {free / 2**30:.1f} GB")
    return f"C: has {free / 2**30:.1f} GB free. Junk: " + "; ".join(lines)


@mcp.tool()
def clean_junk(categories: str) -> str:
    """Permanently delete junk to free space. categories: comma-separated names from junk_scan (e.g. "temp files,
    uv cache, recycle bin") or "all". Files that are in use are skipped. Karen asks the user first."""
    import shutil
    places = _junk_places()
    want = {c.strip().lower() for c in categories.split(",")}
    if "all" in want:
        want = set(places) | {"recycle bin"}
    before = shutil.disk_usage("C:\\").free
    done = []
    for name in want:
        if name == "recycle bin":
            ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x7)  # no confirm, progress or sound
            done.append(name)
        elif name in places and os.path.isdir(places[name]):
            for entry in os.scandir(places[name]):
                try:
                    shutil.rmtree(entry.path) if entry.is_dir(follow_symlinks=False) else os.remove(entry.path)
                except OSError:
                    pass  # in use: leave it
            done.append(name)
    _junk_cache.clear()
    freed = shutil.disk_usage("C:\\").free - before
    log("clean_junk", f"{', '.join(done)}: freed {freed / 2**30:.2f} GB")
    return f"Cleaned {', '.join(done) or 'nothing'}: freed {max(freed, 0) / 2**30:.2f} GB. C: now has " \
           f"{shutil.disk_usage('C:/').free / 2**30:.1f} GB free."


# ---------------------------------------------------------------- settings
SETTINGS_PAGES = {"wifi": "network-wifi", "wi-fi": "network-wifi", "network": "network-status", "bluetooth": "bluetooth",
                  "display": "display", "night light": "nightlight", "sound": "sound", "battery": "batterysaver",
                  "power": "powersleep", "notifications": "notifications", "focus": "focus", "apps": "appsfeatures",
                  "default apps": "defaultapps", "storage": "storagesense", "update": "windowsupdate",
                  "windows update": "windowsupdate", "privacy": "privacy", "mouse": "mousetouchpad",
                  "keyboard": "keyboard", "personalization": "personalization", "themes": "themes",
                  "background": "personalization-background", "date": "dateandtime", "time": "dateandtime",
                  "language": "regionlanguage", "accounts": "yourinfo", "about": "about", "printers": "printers",
                  "camera": "privacy-webcam", "microphone": "privacy-microphone", "vpn": "network-vpn",
                  "hotspot": "network-mobilehotspot", "airplane mode": "network-airplanemode"}


@mcp.tool()
def open_settings(page: str) -> str:
    """Open a Windows Settings page, e.g. wifi, bluetooth, display, night light, sound, battery, update, storage."""
    key = page.lower().strip()
    hit = SETTINGS_PAGES.get(key) or SETTINGS_PAGES.get(next(iter(difflib.get_close_matches(key, SETTINGS_PAGES, 1, 0.6)), ""))
    if not hit:
        return f"I know these settings pages: {', '.join(sorted(SETTINGS_PAGES))}."
    os.startfile(f"ms-settings:{hit}")
    log("open_settings", hit)
    return f"Opened {key} settings."


@mcp.tool()
def brightness(level: int | None = None, change: int | None = None) -> str:
    """Set the screen brightness to level (0-100) or change it by a step like +20 or -20. Laptop screens only."""
    def ps(script):
        return subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True,
                              timeout=15, creationflags=0x08000000).stdout.strip()
    now = ps("(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness).CurrentBrightness")
    if not now.isdigit():
        return "This screen's brightness can't be controlled from Windows."
    new = max(0, min(100, level if level is not None else int(now) + (change or 0)))
    ps(f"(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods)"
       f" | Invoke-CimMethod -MethodName WmiSetBrightness -Arguments @{{Timeout=1; Brightness={int(new)}}}")
    log("brightness", f"{now} -> {new}")
    return f"Brightness is now {new}%."


@mcp.tool()
def dark_mode(on: bool = True) -> str:
    """Switch Windows and apps to dark mode (on=true) or light mode (on=false)."""
    import winreg
    path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as k:
        for name in ("AppsUseLightTheme", "SystemUsesLightTheme"):
            winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, 0 if on else 1)
    log("dark_mode", str(on))
    return "Dark mode on." if on else "Light mode on."


# ---------------------------------------------------------------- files (user folders only)
def resolve(path):
    """ "~/Desktop/x" or "Desktop/x" -> the real Desktop (which may be on another drive); other paths as given."""
    parts = [p for p in str(path).replace("\\", "/").split("/") if p]
    if parts and parts[0] == "~":
        parts = parts[1:]
    names = {k.lower(): k for k in KNOWN_FOLDERS}
    if parts and parts[0].lower() in names:
        return os.path.join(known_folder(names[parts[0].lower()]), *parts[1:])
    return os.path.abspath(os.path.expanduser(path))


def _user_path(path, must_exist=True):
    full = resolve(path)
    if not any(full.lower() == d.lower() or full.lower().startswith(d.lower() + os.sep) for d in USER_DIRS):
        return None, "I can only work with files in these folders: " + "; ".join(USER_DIRS)
    if must_exist and not os.path.exists(full):
        return None, f"{path} doesn't exist."
    return full, ""


@mcp.tool()
def create_folder(path: str) -> str:
    """Create a folder, e.g. "~/Desktop/Rover project"."""
    full, err = _user_path(path, must_exist=False)
    if err:
        return err
    os.makedirs(full, exist_ok=True)
    log("create_folder", full)
    return f"Created {full}."


@mcp.tool()
def move_file(source: str, destination: str, copy: bool = False) -> str:
    """Move, rename or copy (copy=true) a file or folder within the user folders."""
    import shutil
    src, err = _user_path(source)
    if err:
        return err
    dst, err = _user_path(destination, must_exist=False)
    if err:
        return err
    if os.path.isdir(dst):
        dst = os.path.join(dst, os.path.basename(src))
    if os.path.exists(dst):
        return f"{dst} already exists; I won't overwrite it."
    (shutil.copytree if copy and os.path.isdir(src) else shutil.copy2 if copy else shutil.move)(src, dst)
    log("copy_file" if copy else "move_file", f"{src} -> {dst}")
    return f"{'Copied' if copy else 'Moved'} to {dst}."


@mcp.tool()
def delete_file(path: str) -> str:
    """Move a file or folder from the user folders to the Recycle Bin (it can be restored from there)."""
    from send2trash import send2trash
    full, err = _user_path(path)
    if err:
        return err
    if full.lower() in (d.lower() for d in USER_DIRS):
        return "I won't delete a whole user folder."
    send2trash(full)
    log("delete_file", full)
    return f"Moved {os.path.basename(full)} to the Recycle Bin."


# ---------------------------------------------------------------- screen agent: operate apps and websites like a person
INTERACTIVE = {"ButtonControl", "HyperlinkControl", "ListItemControl", "MenuItemControl", "TabItemControl",
               "EditControl", "ComboBoxControl", "CheckBoxControl", "RadioButtonControl", "TreeItemControl",
               "DataItemControl", "SplitButtonControl"}
RISKY_CLICK = re.compile(r"\b(buy|purchase|order|pay|checkout|send|delete|remove|unsubscribe|subscribe|sign out|"
                         r"log ?out|uninstall|format|transfer|post|publish|share|reply all|empty|erase)\b", re.I)
AGENT_RULES = """You operate the user's Windows PC like a careful person, ONE action per reply, to reach the goal.
You get the current window's elements (each clickable one has an [id]) and a screenshot.
Reply with ONE JSON object and nothing else:
{"thought": "<few words>", "action": "click|type|key|scroll|open_url|wait|done|fail", ...}
- click: {"id": N}                      - type: {"id": N or null, "text": "..."} (types into that field)
- key: {"keys": "enter" or "ctrl+l"...}   - scroll: {"direction": "down|up"}
- open_url: {"url": "https://..."}        - wait: {} (page still loading)
- done: {"summary": "<one short spoken sentence of what you did>"}   - fail: {"reason": "<short, spoken>"}
Prefer clicking ids over keys. After an action, check the next screen to confirm it worked before saying done.
Never repeat an action listed in DONE SO FAR. As soon as the main result is reached (e.g. music is playing), reply done;
don't redo earlier steps to satisfy every word of the goal.
Never buy, pay, send, post, delete or sign out unless the goal says so. Stop with fail if you are stuck."""


def _work_windows():
    """Real windows Karen may work in: everything but her own window."""
    return [w for w in _windows() if w.title.strip().upper() != "KAREN"]


def _target_window():
    """hwnd of the window to start in: the top real, non-minimized one that isn't Karen's (else the latest minimized)."""
    wins = _work_windows()
    w = next((w for w in wins if not w.isMinimized), None) or (wins[0] if wins else None)
    return w._hWnd if w else None


def _snapshot(hwnd):
    """(title, interactive elements [(id, kind, name, x, y)], texts, png) of window hwnd, brought to the front.
    None if the window is gone. Runs in a worker thread."""
    import uiautomation as auto
    import mss
    import mss.tools
    import numpy as np
    if not hwnd or not ctypes.windll.user32.IsWindow(hwnd):
        return None
    if ctypes.windll.user32.GetForegroundWindow() != hwnd:
        _focus(hwnd)
    with auto.UIAutomationInitializerInThread():
        root = auto.ControlFromHandle(hwnd)
        title = root.Name or ""
        wr = root.BoundingRectangle
        items, texts, seen, stack = [], [], set(), [(root, 0)]
        while stack and len(items) < 150:
            ctl, depth = stack.pop(0)
            try:
                children = ctl.GetChildren()
            except Exception:  # COMError: the element vanished while the page changed
                continue
            for ch in children:
                try:
                    if depth < 30:
                        stack.append((ch, depth + 1))
                    if ch.IsOffscreen or not ch.Name:
                        continue
                    r = ch.BoundingRectangle
                    if r.width() < 3 or r.height() < 3 or r.right < wr.left or r.left > wr.right \
                            or r.bottom < wr.top or r.top > wr.bottom:
                        continue
                    key = (ch.Name, r.left, r.top)
                    if key in seen:
                        continue
                    seen.add(key)
                    if ch.ControlTypeName in INTERACTIVE:
                        items.append((len(items), ch.ControlTypeName[:-7].lower(), ch.Name[:80],
                                      (r.left + r.right) // 2, (r.top + r.bottom) // 2))
                    elif ch.ControlTypeName == "TextControl" and len(texts) < 60:
                        texts.append(ch.Name[:100])
                except Exception:
                    continue  # same: skip elements that disappear mid-read
    with (mss.MSS() if hasattr(mss, "MSS") else mss.mss()) as s:
        box = {"left": max(0, wr.left), "top": max(0, wr.top), "width": max(1, wr.width()), "height": max(1, wr.height())}
        shot = s.grab(box)
    img = np.frombuffer(shot.rgb, np.uint8).reshape(shot.height, shot.width, 3)
    step = max(1, int(np.ceil(shot.width / 1024)))
    img = np.ascontiguousarray(img[::step, ::step])
    png = mss.tools.to_png(img.tobytes(), (img.shape[1], img.shape[0]))
    return title, items, texts, png


def _do(action, items, hwnd):
    """Carry out one agent action in window hwnd. Runs in a worker thread."""
    import pyautogui
    kind = action.get("action")
    if kind in ("click", "type") and action.get("id") is not None:
        hit = next((i for i in items if i[0] == action["id"]), None)
        if not hit:
            return f"no element [{action['id']}] on screen"
        pyautogui.moveTo(hit[3], hit[4], duration=0.25)  # visible cursor movement
        pyautogui.click()
        time.sleep(0.3)
    if kind == "type":
        if _foreground_exe() in TERMINALS:
            return "refused: a terminal is in front"
        how = _type(str(action.get("text", "")))
        if how.startswith("failed"):
            return how
    elif kind == "key":
        return press_keys(str(action.get("keys", "")))
    elif kind == "scroll":
        rect = wintypes.RECT()
        if ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            pyautogui.moveTo((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2, duration=0.15)
        pyautogui.scroll((1 if str(action.get("direction")).lower() == "up" else -1) * 600)
    elif kind == "open_url":
        url = str(action.get("url", ""))
        webbrowser.open(url if url.startswith(("http://", "https://")) else "https://" + url)
        time.sleep(2.5)
    elif kind == "wait":
        time.sleep(1.5)
    return "ok"


@mcp.tool()
async def operate(goal: str, ctx: Context, window: str = "") -> str:
    """Do something on screen like a person, step by step: look at the window, move the mouse, click buttons,
    links and list items, type into fields, press keys, scroll. Use it for things like "play the first song in my
    YouTube Music library", "click the second search result", "fill in this form", "open my latest email in the
    browser". Never tell the user to click something themselves; use this instead. Give the whole goal in one sentence.
    window: optional part of the title of the window to work in (otherwise the one in front, or open_url first)."""
    import asyncio
    if window:
        found = await asyncio.to_thread(_find_window, window)
        if not found:
            return f"No open window matches '{window}'."
        hwnd = found._hWnd
    else:
        hwnd = await asyncio.to_thread(_target_window)
    return await _agent(goal, ctx, hwnd)


async def _agent(goal, ctx, hwnd, preapproved=False):
    """The screen-agent loop. preapproved: Karen already asked the user (send_whatsapp), so risky clicks don't ask again."""
    import asyncio
    sa = CFG.get("screen_agent", {})
    history, slog = [], open(os.path.join(ROOT, "logs", "screen_actions.log"), "a", encoding="utf-8")
    slog.write(f"\n===== {datetime.datetime.now():%Y-%m-%d %H:%M:%S} GOAL: {goal}\n")
    result = ""
    try:
        for step in range(1, sa.get("max_steps", 12) + 1):
            snap = await asyncio.to_thread(_snapshot, hwnd)
            if snap is None:  # the window closed: carry on in whatever is in front now
                hwnd = await asyncio.to_thread(_target_window)
                snap = await asyncio.to_thread(_snapshot, hwnd)
            title, items, texts, png = snap or ("(no window open)", [], [], None)
            listing = "\n".join(f"[{i}] {k}: {n}" for i, k, n, _, _ in items) or "(no clickable elements found)"
            content = [{"type": "text", "text": (
                f"GOAL: {goal}\nWINDOW: {title}\nCLICKABLE ELEMENTS:\n{listing}\n"
                f"OTHER TEXT ON SCREEN: {' | '.join(texts)[:1500]}\n"
                f"DONE SO FAR: {'; '.join(history) or 'nothing yet'}\nYour next single action as JSON:")}]
            if sa.get("screenshots", True) and png:
                content.append({"type": "image_url",
                                "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}})
            raw = await asyncio.to_thread(chat, sa.get("model"), max_tokens=300, messages=[
                {"role": "system", "content": AGENT_RULES}, {"role": "user", "content": content}])
            try:
                action = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
            except ValueError:
                history.append("(unreadable reply, try again)")
                continue
            kind = action.get("action")
            slog.write(f"--- step {step} [{title[:60]}] {json.dumps(action, ensure_ascii=False)[:300]}\n")
            if kind in ("done", "fail"):
                result = action.get("summary") or action.get("reason") or ""
                break
            target = next((i for i in items if i[0] == action.get("id")), None)
            if kind == "click" and target and RISKY_CLICK.search(target[2]) and not preapproved:
                answer = await ctx.elicit(message=f"On screen: click '{target[2][:60]}'?", schema=Approve)
                if not (answer.action == "accept" and answer.data.approve):
                    result = f"Stopped before clicking '{target[2][:60]}', as you said no."
                    break
            what = f"{kind} '{target[2][:40]}'" if target else f"{kind} {action.get('url') or action.get('keys') or action.get('text') or ''}".strip()
            if kind == "click" and sum(h.startswith(what + " ->") for h in history) >= 2:
                history.append(f"(skipped: {what} was already done twice; the goal is probably reached, say done)")
                continue  # it was going round in circles (Home -> Library -> Home...)
            try:
                outcome = await asyncio.to_thread(_do, action, items, hwnd)
            except Exception as e:  # pyautogui's fail-safe: The user slammed the mouse into a screen corner
                result = "Stopped: you moved the mouse into a corner." if "FailSafe" in type(e).__name__ else \
                    f"Stopped: {type(e).__name__}."
                break
            history.append(f"{what} -> {outcome}")
            await asyncio.sleep(sa.get("settle_s", 1.2))  # let the page react before looking again
            # stay in this window unless our own action opened another one (a browser, a dialog)
            fg = ctypes.windll.user32.GetForegroundWindow()
            if fg != hwnd and kind in ("click", "key", "open_url", "type") and \
                    fg in {w._hWnd for w in await asyncio.to_thread(_work_windows)}:
                hwnd = fg
        else:
            result = f"I stopped after {sa.get('max_steps', 12)} steps without finishing."
    finally:
        slog.write(f"--- result: {result}\n")
        slog.close()
    log("operate", f"{goal[:80]} -> {len(history)} actions")
    return result or "Done."


def _clip_file(path):
    """Put a file on the clipboard (as if copied in Explorer), retrying while other apps hold the clipboard."""
    script = ("for ($i = 0; $i -lt 10; $i++) { try { Set-Clipboard -LiteralPath $env:KAREN_FILE -ErrorAction Stop; "
              "if ((Get-Clipboard -Format FileDropList).FullName -eq $env:KAREN_FILE) { exit 0 } } catch {}; "
              "Start-Sleep -Milliseconds 250 }; exit 1")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", script], env=dict(os.environ, KAREN_FILE=path),
                       capture_output=True, creationflags=0x08000000, timeout=30)
    return r.returncode == 0


async def _whatsapp_window():
    """The WhatsApp window (app or WhatsApp Web), opening WhatsApp Web in the configured browser if needed."""
    import asyncio
    found = await asyncio.to_thread(_find_window, "WhatsApp")
    if not found:
        await asyncio.to_thread(_browse, "https://web.whatsapp.com", CFG.get("whatsapp_browser", "chrome"))
        await asyncio.sleep(8)  # WhatsApp Web takes a while to load chats
        found = await asyncio.to_thread(_find_window, "WhatsApp")
    return found


@mcp.tool()
async def whatsapp_chats() -> str:
    """The WhatsApp chat list (names, last messages, unread counts), to summarise what's new on WhatsApp."""
    import asyncio
    found = await _whatsapp_window()
    if not found:
        return "WhatsApp isn't open. Is WhatsApp Web signed in in the browser?"
    await asyncio.to_thread(_focus, found._hWnd)
    title, text = await asyncio.to_thread(_page_text, found._hWnd)
    log("whatsapp_chats", f"{len(text)} chars")
    return f"{title}\n{text[:CFG.get('whatsapp_chars', 5000)]}"


@mcp.tool()
async def whatsapp_read(contact: str, ctx: Context) -> str:
    """Open the chat with a contact or group and read its recent messages."""
    import asyncio
    found = await _whatsapp_window()
    if not found:
        return "WhatsApp isn't open. Is WhatsApp Web signed in in the browser?"
    opened = await _agent(f"In WhatsApp, open the chat with '{contact}' (search for it if it isn't in the list) and "
                          f"then reply done as soon as that chat's messages are showing. Don't type any message. If no "
                          f"chat clearly matches '{contact}', fail and say which names you saw.", ctx, found._hWnd)
    title, text = await asyncio.to_thread(_page_text, found._hWnd)
    log("whatsapp_read", contact)
    limit = CFG.get("whatsapp_chars", 5000)
    return f"{opened}\nRecent messages (the open chat is at the end):\n{text[-limit:]}"


@mcp.tool()
async def send_whatsapp_file(contact: str, path: str, ctx: Context, caption: str = "") -> str:
    """Send a file or photo (from the user folders, Documents/Karen or files added with +) to a WhatsApp contact.
    Karen reads it back and only sends after their yes."""
    import asyncio
    uploads = os.path.join(ROOT, "data", "uploads")
    places = [resolve(path)] + ([] if os.path.isabs(path) else
                                [os.path.join(uploads, path), os.path.join(known_folder("Documents"), "Karen", path)])
    full = next((p for p in places if os.path.isfile(p)), None)
    if not full:
        return f"I can't find the file {path}."
    if not any(full.lower().startswith(d.lower() + os.sep) for d in USER_DIRS + [uploads]):
        return "I can only send files from your user folders or files you added."
    found = await _whatsapp_window()
    if not found:
        return "WhatsApp isn't open. Is WhatsApp Web signed in in the browser?"
    if await asyncio.to_thread(_clip_file, full):  # WhatsApp attaches pasted files
        attach = f"click the message box at the bottom, press ctrl+v to attach the file {os.path.basename(full)!r}"
    else:  # clipboard stuck: use the attach button and the file dialog instead
        attach = (f"click the Attach (paperclip or plus) button, choose Document, and in the file window type this "
                  f"full path into the file name box and press enter: {full}")
    goal = (f"In WhatsApp, open the chat with '{contact}', {attach}, wait for the preview, "
            + (f"type the caption {caption!r}, " if caption else "")
            + f"then press enter (or click Send) to send it and check it shows in the chat. If no chat clearly "
              f"matches '{contact}', fail and say which names you saw.")
    result = await _agent(goal, ctx, found._hWnd, preapproved=True)
    log("send_whatsapp_file", f"{contact}: {full} -> {result[:80]}")
    return result


@mcp.tool()
async def send_whatsapp(contact: str, message: str, ctx: Context) -> str:
    """Send a WhatsApp message to a contact or group through WhatsApp (app or WhatsApp Web), typing it for the user.
    Karen reads the message back and only sends after their yes."""
    found = await _whatsapp_window()
    if not found:
        return "WhatsApp Web didn't open. Is it signed in in the browser?"
    goal = (f"In WhatsApp, open the chat with '{contact}' (click the search box, type the name, click the matching "
            f"chat), click the message box at the bottom, type exactly: {message!r} and press enter to send it. "
            f"Then check it shows in the chat as sent. If no chat clearly matches '{contact}', fail and say which "
            f"names you saw instead of guessing.")
    result = await _agent(goal, ctx, found._hWnd, preapproved=True)
    log("send_whatsapp", f"{contact}: {len(message)} chars -> {result[:80]}")
    return result


# ---------------------------------------------------------------- power mode (Open Interpreter)
class Approve(BaseModel):
    approve: bool = Field(description="Allow this step?")


@mcp.tool()
async def power_mode(task: str, ctx: Context) -> str:
    """Last resort for PC tasks the other tools can't do (bulk renaming, sorting files, data or spreadsheet work,
    small automations): Open Interpreter writes and runs code. Not for clicking around apps or websites: use
    operate for that. Give the whole task in one clear sentence."""
    import asyncio
    pm = CFG.get("power_mode", {})
    python = os.path.expandvars(pm.get("python", ""))
    if not os.path.exists(python):
        return "Power mode isn't set up: config.yaml pc_control.power_mode.python must point to the Python with Open Interpreter."
    env = dict(os.environ, POWER_MODE_MAX_RUNS=str(pm.get("max_steps", 8)), POWER_MODE_MODEL=pm.get("model", ""),
               POWER_MODE_FOLDERS=json.dumps({n: known_folder(n) for n in KNOWN_FOLDERS}),
               PYTHONIOENCODING="utf-8")
    proc = await asyncio.create_subprocess_exec(python, os.path.join(os.path.dirname(__file__), "power_mode.py"),
                                                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.DEVNULL, env=env,
                                                creationflags=0x08000000)
    proc.stdin.write((task.replace("\n", " ") + "\n").encode())
    await proc.stdin.drain()
    result, steps = "", 0
    plog = open(os.path.join(ROOT, "logs", "power_mode.log"), "a", encoding="utf-8")
    plog.write(f"\n===== {datetime.datetime.now():%Y-%m-%d %H:%M:%S} TASK: {task}\n")

    async def run():
        nonlocal result, steps
        async for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").rstrip()
            if not line.startswith("@@KAREN "):
                continue
            ev = json.loads(line[8:])
            kind = ev.get("event")
            if kind == "code":
                steps = ev["step"]
                plog.write(f"--- step {steps} ({ev['language']}):\n{ev['code']}\n")
            elif kind == "output":
                plog.write(f"--- output:\n{ev['text']}\n")
            elif kind == "confirm":
                question = f"Power mode wants to {' and '.join(ev['risks'])}" + (f": {ev['plan']}" if ev.get("plan") else "") + ". Allow it?"
                answer = await ctx.elicit(message=question, schema=Approve)
                ok = answer.action == "accept" and answer.data.approve
                plog.write(f"--- asked: {question} -> {'yes' if ok else 'no'}\n")
                proc.stdin.write(b"yes\n" if ok else b"no\n")
                await proc.stdin.drain()
            elif kind in ("done", "stopped", "error"):
                result = ev.get("summary") or ev.get("reason") or ev.get("text") or ""
                result = {"done": "", "stopped": "Stopped: ", "error": "Power mode failed: "}[kind] + result
    try:
        await asyncio.wait_for(run(), timeout=pm.get("timeout_s", 240))
    except asyncio.TimeoutError:
        result = f"Power mode ran out of time after {steps} steps."
    finally:
        if proc.returncode is None:
            proc.kill()
        plog.write(f"--- result: {result}\n")
        plog.close()
    log("power_mode", f"{task[:80]} -> {steps} steps")
    return (result or "Power mode finished.") + f" ({steps} code steps; details in logs/power_mode.log)"


if __name__ == "__main__":
    import threading
    threading.Thread(target=_watch_clipboard, daemon=True).start()
    mcp.run()
