"""Karen Hands: a separate window (ui/hands.html) that tracks both hands on the webcam and turns gestures into
3D model control or phone control. Hand tracking runs in the page (MediaPipe, on this PC; no frames leave it).

    python hands.py                 3D viewer (drop a file, or OPEN FILE)
    python hands.py --model PATH    open a 3D file (GLB, GLTF, FBX, OBJ, STL, VRM)
    python hands.py --phone         mirror the phone (scrcpy) and control it with hand gestures
    python hands.py --train         gesture training session (data/gestures.json)
    python hands.py --stop          close the window (and the phone mirror)

Only one window runs (mutex Local\\KarenHands); a second start hands its request over (data/hands_cmd.json).
Phone control moves the real mouse over the scrcpy window ("Karen Phone"); scrcpy turns clicks into taps.
Back/Home use adb key events. Karen's adb rules apply (core/adbguard.py).
"""
import ctypes
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from ctypes import wintypes

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
UI = os.path.join(ROOT, "ui")
ASSETS = os.path.join(UI, "hands_assets")  # links to models/mediapipe + staged 3D files (git-ignored)
FILES = os.path.join(ASSETS, "files")
CMD = os.path.join(ROOT, "data", "hands_cmd.json")
PROFILE = os.path.join(ROOT, "data", "gestures.json")
LOG = os.path.join(ROOT, "logs", "hands.log")
STATUS = os.path.join(ROOT, "data", "hands_status.json")  # the outcome of the last request, for Karen's answer
LAST_PHONE = os.path.join(ROOT, "data", "artemis_phone.txt")  # the phone's last Wi-Fi adb address (shared)
MODELS3D = (".glb", ".gltf", ".fbx", ".obj", ".stl", ".vrm")
SIDECARS = (".bin", ".png", ".jpg", ".jpeg", ".webp", ".ktx2", ".mtl", ".tga", ".bmp")
PHONE_TITLE = "Karen Phone"
user32 = ctypes.windll.user32
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # real pixels: the cursor lands where the phone picture is
except OSError:
    pass


def log(what, detail=""):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {what:10} {detail}\n")


def _link(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.exists(dst):
        os.remove(dst)
    try:
        os.link(src, dst)  # same drive: no copy
    except OSError:
        shutil.copyfile(src, dst)


def link_assets():
    """MediaPipe's wasm + hand model (models/mediapipe) into ui/hands_assets, and an empty staging folder."""
    for f in ("vision_wasm_internal.js", "vision_wasm_internal.wasm", "hand_landmarker.task"):
        src = os.path.join(ROOT, "models", "mediapipe", f)
        dst = os.path.join(ASSETS, f)
        if os.path.exists(src) and not (os.path.exists(dst) and os.path.getsize(dst) == os.path.getsize(src)):
            _link(src, dst)
    shutil.rmtree(FILES, ignore_errors=True)
    os.makedirs(FILES, exist_ok=True)


_staged = {"n": 0, "path": ""}


def stage(path):
    """Make a 3D file loadable by the page (only ui/ is served): link it, plus its textures/.bin/.mtl next to it."""
    path = os.path.abspath(os.path.expandvars(os.path.expanduser(path)))
    if not os.path.isfile(path) or not path.lower().endswith(MODELS3D):
        return None
    _staged["n"] += 1
    sub = f"m{_staged['n']}"
    folder = os.path.dirname(path)
    _link(path, os.path.join(FILES, sub, os.path.basename(path)))
    if path.lower().endswith((".gltf", ".obj", ".fbx")):  # these can point at files next to them
        total = 0
        for dirpath, _, names in os.walk(folder):
            if dirpath.count(os.sep) - folder.count(os.sep) > 2:
                continue
            for n in names:
                if n.lower().endswith(SIDECARS):
                    src = os.path.join(dirpath, n)
                    total += os.path.getsize(src)
                    if total > 300 * 1024 * 1024:
                        break
                    _link(src, os.path.join(FILES, sub, os.path.relpath(src, folder)))
    _staged["path"] = path
    name = os.path.basename(path)
    url = f"hands_assets/files/{sub}/{name}"
    mtl = os.path.splitext(name)[0] + ".mtl"
    log("model", path)
    return {"url": url, "name": name,
            "mtl": f"hands_assets/files/{sub}/{mtl}" if name.lower().endswith(".obj") and os.path.exists(os.path.join(folder, mtl)) else ""}


# ---------------------------------------------------------------- phone (scrcpy + mouse)
def adb_exe():
    return shutil.which("adb") or next(iter(glob.glob(os.path.expandvars(
        r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Google.PlatformTools*\platform-tools\adb.exe"))), None)


def scrcpy_exe():
    return shutil.which("scrcpy") or next(iter(glob.glob(os.path.expandvars(
        r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Genymobile.scrcpy*\scrcpy-win64-*\scrcpy.exe"))), None)


def run(cmd, timeout=15):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, creationflags=0x08000000,
                          encoding="utf-8", errors="replace")


def main_phone_serial():
    """The main phone's adb serial: not the spare-SIM phone (config phone.second); Wi-Fi before USB."""
    import yaml
    cfg = (yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8")) or {}).get("phone", {})
    second = cfg.get("second") or []
    second = [str(s) for s in (second if isinstance(second, list) else [second])]
    if cfg.get("device"):
        return cfg["device"]
    adb = adb_exe()

    def ready():
        found = [s for s in re.findall(r"^(\S+)\tdevice$", run([adb, "devices"]).stdout, re.M) if s not in second]
        return sorted(found, key=lambda s: ":" not in s)  # wireless first: leaves USB to Android Studio

    found = ready()
    if not found:  # wireless debugging drops now and then: the phone's mDNS name, then its last address
        addresses = re.findall(r"_adb-tls-connect\._tcp\.?\s+([\d.]+:\d+)", run([adb, "mdns", "services"]).stdout)
        try:
            addresses.append(open(LAST_PHONE, encoding="utf-8").read().strip())
        except OSError:
            pass
        for address in dict.fromkeys(a for a in addresses if a and a not in second):
            try:
                run([adb, "connect", address], timeout=10)
            except subprocess.TimeoutExpired:
                continue
            found = ready()
            if found:
                break
    if found and ":" in found[0]:
        with open(LAST_PHONE, "w", encoding="utf-8") as f:
            f.write(found[0])
    return found[0] if found else ""


def work_area():
    r = wintypes.RECT()
    user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0)  # SPI_GETWORKAREA
    return r.left, r.top, r.right, r.bottom


class Phone:
    def __init__(self):
        self.proc, self.serial, self.size, self.down = None, "", (1080, 2400), False

    def start(self):
        from core import adbguard
        if self.running():
            return {"ok": True, "message": "The phone mirror is already open."}
        exe, adb = scrcpy_exe(), adb_exe()
        if not exe:
            return {"ok": False, "message": "scrcpy isn't installed (winget install Genymobile.scrcpy)."}
        if not adb:
            return {"ok": False, "message": "adb isn't installed."}
        if adbguard.tools_blocked():
            return {"ok": False, "message": adbguard.MESSAGE}
        self.serial = main_phone_serial()
        if not self.serial:
            log("phone", "no phone on adb")
            return {"ok": False, "message": "I can't reach your phone. On the phone, turn on Settings > Developer options > "
                                            "Wireless debugging (same Wi-Fi as the PC), or plug in the USB cable, then ask again."}
        m = re.search(r"(\d+)x(\d+)", run([adb, "-s", self.serial, "shell", "wm", "size"]).stdout.split("Override")[-1])
        if m:
            self.size = (int(m.group(1)), int(m.group(2)))
        left, top, right, bottom = work_area()
        height = bottom - top - 60
        x = right - int(height * self.size[0] / self.size[1]) - 40
        self.proc = subprocess.Popen([exe, "-s", self.serial, "--window-title", PHONE_TITLE, "--no-audio", "--stay-awake",
                                      f"--window-x={x}", f"--window-y={top + 30}", f"--window-height={height}",
                                      "--max-size=1600"], creationflags=0x08000000,
                                     stdout=subprocess.DEVNULL, stderr=open(os.path.join(ROOT, "logs", "scrcpy.log"), "w"),
                                     env=dict(os.environ, ADB=adb))  # scrcpy ships its own adb: use Karen's (one server)
        log("phone", f"mirror {self.serial} {self.size}")
        for _ in range(60):  # wait for its window (up to ~12 s)
            if self.window():
                return {"ok": True, "message": "Your phone is mirrored on the right."}
            if self.proc.poll() is not None:  # scrcpy quit: say why
                why = open(os.path.join(ROOT, "logs", "scrcpy.log"), encoding="utf-8", errors="replace").read()
                log("phone", "scrcpy quit: " + why.strip()[-200:].replace("\n", " | "))
                hint = (" Unlock the phone and allow USB debugging if it asks." if "unauthorized" in why.lower() else "")
                return {"ok": False, "message": "The phone mirror couldn't start." + hint}
            time.sleep(0.2)
        return {"ok": False, "message": "The phone mirror is taking long to open; check that the phone is unlocked."}

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def window(self):
        return user32.FindWindowW(None, PHONE_TITLE)

    def stop(self):
        self.up()
        if self.running():
            self.proc.terminate()
        self.proc = None

    def _point(self, x, y):
        """Screen pixel for (x, y) in 0..1 of the phone picture (scrcpy letterboxes it in its window)."""
        hwnd = self.window()
        if not hwnd:
            return None
        rc = wintypes.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(rc))
        origin = wintypes.POINT(0, 0)
        user32.ClientToScreen(hwnd, ctypes.byref(origin))
        cw, ch = rc.right, rc.bottom
        scale = min(cw / self.size[0], ch / self.size[1])
        w, h = self.size[0] * scale, self.size[1] * scale
        ox, oy = origin.x + (cw - w) / 2, origin.y + (ch - h) / 2
        return int(ox + min(max(x, 0.0), 1.0) * (w - 1)), int(oy + min(max(y, 0.0), 1.0) * (h - 1))

    def pointer(self, x, y, action):
        p = self._point(x, y)
        if not p:
            return
        user32.SetCursorPos(*p)
        if action == "down" and not self.down:
            self.down = True
            user32.mouse_event(0x0002, 0, 0, 0, 0)  # left button down
        elif action == "up" and self.down:
            self.up()

    def up(self):
        if self.down:
            self.down = False
            user32.mouse_event(0x0004, 0, 0, 0, 0)  # left button up

    def scroll(self, x, y, direction):
        p = self._point(x, y)
        if p:
            user32.SetCursorPos(*p)
            user32.mouse_event(0x0800, 0, 0, 120 * int(direction), 0)  # wheel: hand down = content down

    def key(self, name):
        code = {"back": "4", "home": "3", "recents": "187"}.get(name)
        if code and self.serial:
            run([adb_exe(), "-s", self.serial, "shell", "input", "keyevent", code], timeout=8)
            log("phone", f"key {name}")


# ---------------------------------------------------------------- the page's API
class Api:
    def __init__(self, first):
        # underscore names: pywebview exposes public attributes to the page and walks them (touching WebView2 off-thread)
        self._first, self._window, self._phone = first, None, Phone()

    def ready(self):
        allow_camera(self._window)
        cmd, self._first = self._first, {}
        return dict(prepare(cmd, self), profile=load_profile())

    def pick_file(self):
        import webview
        start = _staged["path"] and os.path.dirname(_staged["path"]) or os.path.join(os.path.expanduser("~"), "Downloads")
        got = self._window.create_file_dialog(webview.FileDialog.OPEN, directory=start,
                                             file_types=("3D files (*.glb;*.gltf;*.fbx;*.obj;*.stl;*.vrm)", "All files (*.*)"))
        return stage(got[0]) if got else None

    def next_model(self):
        cur = _staged["path"]
        if not cur:
            return None
        folder = os.path.dirname(cur)
        files = sorted(f for f in os.listdir(folder) if f.lower().endswith(MODELS3D))
        if len(files) < 2:
            return None
        i = files.index(os.path.basename(cur)) if os.path.basename(cur) in files else -1
        return stage(os.path.join(folder, files[(i + 1) % len(files)]))

    def save_profile(self, text):
        os.makedirs(os.path.dirname(PROFILE), exist_ok=True)
        with open(PROFILE, "w", encoding="utf-8") as f:
            f.write(text)
        data = json.loads(text)
        log("training", f"{len(data.get('samples', []))} samples, accuracy {data.get('accuracy')}, pinch {data.get('pinch')}")
        return True

    def report(self, text):
        log("page", str(text)[:200])

    def phone_start(self):
        return self._phone.start()

    def phone_pointer(self, x, y, action):
        self._phone.pointer(float(x), float(y), action)

    def phone_scroll(self, x, y, direction):
        self._phone.scroll(float(x), float(y), direction)

    def phone_key(self, name):
        self._phone.key(name)


def load_profile():
    try:
        with open(PROFILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def status(cmd, ok, message):
    if cmd.get("id"):
        with open(STATUS, "w", encoding="utf-8") as f:
            json.dump({"id": cmd["id"], "ok": ok, "message": message}, f)


def prepare(cmd, api):
    """Turn a request ({mode, model path}) into what the page needs; starts the phone mirror for phone mode."""
    out = {"mode": cmd.get("mode") or ""}
    result = (True, {"3d": "The 3D viewer is open.", "train": "Hand training is open."}.get(out["mode"], "Done."))
    if cmd.get("model"):
        staged = stage(cmd["model"])
        out["model"] = staged
        if not staged:
            out["toast"] = f"Couldn't find the 3D file {cmd['model']}"
        result = (bool(staged), f"Opened {staged['name']} in the 3D viewer." if staged else out["toast"])
    if out["mode"] == "phone":
        res = api._phone.start()
        out["toast"] = res["message"] + (" Hold an open palm for 1 s to start hand control." if res["ok"] else "")
        if not res["ok"]:
            out["mode"] = "3d"
        result = (res["ok"], res["message"])
    status(cmd, *result)
    if not out["mode"]:
        out.pop("mode")
    return out


_keep = []


def allow_camera(window):
    """WebView2 asks before a page uses the camera; allow it for this page only (Karen's own local server)."""
    try:
        import clr  # noqa: F401  (pythonnet, used by pywebview)
        from Microsoft.Web.WebView2.Core import CoreWebView2PermissionKind, CoreWebView2PermissionState
        from System import Action

        def on_request(sender, args):
            if args.PermissionKind == CoreWebView2PermissionKind.Camera and str(args.Uri).startswith("http://127.0.0.1"):
                args.State = CoreWebView2PermissionState.Allow

        def register():
            core = window.native.browser.webview.CoreWebView2
            core.PermissionRequested += on_request

        _keep.append(on_request)  # keep the handler alive
        window.native.Invoke(Action(register))
    except Exception as e:  # WebView2 then shows its own Allow/Block prompt once
        log("camera", f"auto-allow not set: {e!r}")


def watch_commands(api):
    """Requests from a second `hands.py` start (voice, Telegram) arrive as data/hands_cmd.json."""
    while True:
        time.sleep(0.4)
        try:
            with open(CMD, encoding="utf-8") as f:
                cmd = json.load(f)
            os.remove(CMD)
        except (OSError, ValueError):
            continue
        log("request", json.dumps(cmd))
        if cmd.get("stop"):
            status(cmd, True, "Closed the hands window and the phone mirror.")
            api._phone.stop()
            api._window.destroy()
            return
        out = prepare(cmd, api)
        api._window.restore()
        api._window.show()
        api._window.evaluate_js(f"window.hands && window.hands.command({json.dumps(out)})")


def request(argv):
    cmd = {}
    if "--model" in argv:
        cmd["model"] = argv[argv.index("--model") + 1]
        cmd["mode"] = "3d"
    if "--phone" in argv:
        cmd["mode"] = "phone"
    if "--train" in argv:
        cmd["mode"] = "train"
    if "--stop" in argv:
        cmd["stop"] = True
    if "--id" in argv:
        cmd["id"] = argv[argv.index("--id") + 1]
    return cmd


def main():
    cmd = request(sys.argv[1:])
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\KarenHands")
    if ctypes.windll.kernel32.GetLastError() == 183:  # already open: pass the request on
        if cmd:
            os.makedirs(os.path.dirname(CMD), exist_ok=True)
            with open(CMD, "w", encoding="utf-8") as f:
                json.dump(cmd, f)
        return
    if cmd.get("stop"):
        return
    import webview
    link_assets()
    api = Api(cmd)
    left, top, right, bottom = work_area()
    scale = user32.GetDpiForSystem() / 96  # pywebview sizes windows in scaled units (the display is at 125%)
    phone = cmd.get("mode") == "phone"
    width = int((right - left) * (0.55 if phone else 0.8) / scale)
    api._window = webview.create_window("Karen Hands", os.path.join(UI, "hands.html"), js_api=api,
                                       width=width, height=int((bottom - top) * 0.9 / scale),
                                       x=int((left + 16) / scale), y=int((top + 16) / scale), background_color="#05020a")
    api._window.events.closed += lambda: api._phone.stop()
    threading.Thread(target=watch_commands, args=(api,), daemon=True).start()
    storage = os.path.join(os.environ.get("LOCALAPPDATA", "."), "Karen", "webview-hands")  # not Karen's: two
    from core.ui import clear_web_cache                                                     # windows, two profiles
    clear_web_cache(storage)
    log("open", json.dumps(cmd))
    webview.start(private_mode=False, storage_path=storage, http_server=True)
    del mutex


if __name__ == "__main__":
    main()
