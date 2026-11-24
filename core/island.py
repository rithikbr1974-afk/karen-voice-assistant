"""Karen Island: a small black capsule at the top centre of the screen that grows into a panel, like Dynamic Island.

Two windows, because WebView2 can't be see-through on Windows (tested 2026-11-03: a window region cuts the page and the
clicks, but the area outside still painted solid):
  - Capsule: a native layered window drawn here with PIL (per-pixel alpha, so the capsule has smooth edges and
    everything around it is see-through and click-through). It shows what Karen is doing: resting dot, listening
    (✕ waveform ✓, like a dictation bar), thinking, speaking (her reply as text), music, recording, and grows into
    the black card the panel sits on. Redraws only while something moves (~60 fps), idle otherwise.
  - Panel: a pywebview window (ui/island.html) shown inside the card, inset from its rounded corners so its own
    square corners are hidden: input box, mic, ability chips, the conversation, yes/no, the map.
"""
import ctypes
import ctypes.wintypes as wt
import json
import math
import os
import re
import threading
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFont

user32, gdi32, kernel32 = ctypes.windll.user32, ctypes.windll.gdi32, ctypes.windll.kernel32
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
gdi32.CreateDIBSection.restype = wt.HBITMAP
gdi32.CreateDIBSection.argtypes = [wt.HDC, ctypes.c_void_p, wt.UINT, ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD]
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.SelectObject.restype = wt.HGDIOBJ
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
user32.SetWindowLongW.argtypes = [wt.HWND, ctypes.c_int, wt.LONG]
user32.CreateWindowExW.restype = wt.HWND
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]

shell32 = ctypes.windll.shell32
shell32.DragQueryFileW.argtypes = [wt.HANDLE, wt.UINT, wt.LPWSTR, wt.UINT]
shell32.DragFinish.argtypes = [wt.HANDLE]
shell32.DragAcceptFiles.argtypes = [wt.HWND, wt.BOOL]
user32.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]
user32.GetCursorInfo.argtypes = [ctypes.c_void_p]

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
FILL = (10, 10, 12)  # the capsule and the panel page share this colour (#0a0a0c), so the panel blends into the card
SS = 3  # supersampling: shapes are drawn 3x and scaled down, for smooth edges
MAXW, MAXH = 1000, 1100  # physical pixels: the biggest card fits (the page is told the real limit: max_height)
# clean monochrome: white and greys; colour only for live things (the cover's accent, listening, timers, calls)
WHITE, GREY, DIM = (245, 245, 247), (152, 152, 160), (58, 58, 64)
ORANGE, RED, GREEN, CYAN = (255, 159, 10), (255, 69, 58), (48, 209, 88), (100, 210, 255)
GLYPH = {"cancel": "", "check": "", "phone": "", "hangup": "", "timer": "",
         "bell": "", "chat": "", "drop": ""}  # Segoe Fluent Icons (Windows 11)
VK = {"space": 0x20, "enter": 0x0D, **{chr(c): c for c in range(0x41, 0x5B)}, **{str(n): 0x30 + n for n in range(10)}}
MODS = {"alt": 0x1, "ctrl": 0x2, "shift": 0x4, "win": 0x8}


def hotkey(spec):
    """"ctrl+alt+k" -> (modifiers, virtual key) for RegisterHotKey, or None."""
    parts = [p.strip().lower() for p in (spec or "").split("+") if p.strip()]
    if not parts or parts[-1].upper() not in VK and parts[-1] not in VK:
        return None
    mods = sum(MODS.get(p, 0) for p in parts[:-1])
    return mods | 0x4000, VK.get(parts[-1], VK.get(parts[-1].upper()))  # 0x4000 = MOD_NOREPEAT


class CURSORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("flags", wt.DWORD), ("hCursor", wt.HANDLE), ("pt", wt.POINT)]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON), ("hCursor", wt.HANDLE),
                ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR),
                ("hIconSm", wt.HICON)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_byte), ("BlendFlags", ctypes.c_byte), ("SourceConstantAlpha", ctypes.c_ubyte),
                ("AlphaFormat", ctypes.c_byte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


def font(size, bold=False, name=None):
    for file in ((name,) if name else ("seguisb.ttf", "segoeuib.ttf") if bold else ("segoeui.ttf",)):
        try:
            return ImageFont.truetype(os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts", file), size)
        except OSError:
            pass
    return ImageFont.load_default()


class Spring:
    """A value that eases toward its target with a little overshoot (the island's bounce)."""

    def __init__(self, x):
        self.x = self.target = float(x)
        self.v = 0.0

    def step(self):
        self.v = self.v * 0.72 + (self.target - self.x) * 0.2
        self.x += self.v
        return abs(self.target - self.x) > 0.3 or abs(self.v) > 0.3

    def done(self, tol=1.5):
        return abs(self.target - self.x) < tol


class Capsule:
    """The native capsule window. Thread-safe: other threads call set(); its own thread draws."""

    def __init__(self, on_click, top=8, keys=None, on_drop=None):
        self.on_click = on_click  # on_click(part): "cancel" / "done" / "body" / "right" / a button / "ask" / "talk"
        self.on_drop = on_drop  # on_drop([paths]): files dropped on the capsule
        self.keys = keys or {}  # global shortcuts: {"ask": "ctrl+alt+k", "talk": "ctrl+alt+space"}
        self.top = top
        self.hits = []  # the buttons drawn this frame: (x0, x1, part) in logical px from the capsule's left edge
        self.dragging = False  # files are being dragged near the top centre: show the drop target
        self.drag_since = 0.0
        self.lock = threading.Lock()
        self.view = {"mode": "rest"}  # mode: rest | listen | think | speak | card
        self.extras = {}  # always-on indicators: music {title, artist, playing}, recording {since}, peek until
        self.levels = [0.0] * 24  # recent mic/voice levels for the waveform
        self.wake = threading.Event()
        self.w, self.h, self.r = Spring(96), Spring(26), Spring(13)
        self.fade = 1.0
        self.hover = False
        self.ready = threading.Event()
        self.hwnd = None
        self.box = (0, 0, 0, 0)  # physical rect of the capsule on screen
        threading.Thread(target=self._thread, daemon=True, name="island").start()
        self.ready.wait(5)

    # ------------------------------------------------------------ called from other threads
    def set(self, **view):
        with self.lock:
            self.view = view
        self.wake.set()

    def extra(self, key, value):
        with self.lock:
            if value is None:
                self.extras.pop(key, None)
            else:
                self.extras[key] = value
        self.wake.set()

    def level(self, value):
        self.levels = self.levels[1:] + [max(0.0, min(1.0, value))]

    def card_rect(self):
        """Physical screen rect of the card while it is fully open, or None."""
        with self.lock:
            view = dict(self.view)
        if view.get("mode") != "card" or (self.w.target, self.h.target) != (view.get("w", 440), view.get("h", 300)):
            return None  # not open, or the draw thread hasn't picked up the new size yet
        if not (self.w.done() and self.h.done()):
            return None
        return self.box

    # ------------------------------------------------------------ the window thread
    def _thread(self):
        try:
            user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))  # per-monitor v2: sharp at 125%
        except Exception:
            pass
        self.scale = user32.GetDpiForSystem() / 96
        self.proc = WNDPROC(self._wndproc)  # keep a reference: ctypes callbacks must not be collected
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSEXW(ctypes.sizeof(WNDCLASSEXW), 0, self.proc, 0, 0, hinst, None,
                         user32.LoadCursorW(None, 32649), None, None, "KarenIsland", None)  # hand cursor
        user32.RegisterClassExW(ctypes.byref(wc))
        # layered | topmost | tool window (no taskbar) | never takes focus | accepts dropped files
        ex = 0x80000 | 0x8 | 0x80 | 0x08000000 | 0x10
        self.hwnd = user32.CreateWindowExW(ex, "KarenIsland", "Karen Island", 0x80000000, 0, 0, 10, 10,
                                           None, None, hinst, None)
        shell32.DragAcceptFiles(self.hwnd, True)
        self.hotkeys = {}
        for i, (name, spec) in enumerate(self.keys.items(), start=1):
            key = hotkey(spec)
            if key and user32.RegisterHotKey(self.hwnd, i, *key):
                self.hotkeys[i] = name
        self.plain = {user32.LoadCursorW(None, c) for c in (32512, 32513, 32649, 32646, 32644, 32645, 32642, 32643)}
        self.screen = user32.GetSystemMetrics(0)
        self.dc = gdi32.CreateCompatibleDC(None)
        bmi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), MAXW, -MAXH, 1, 32, 0, 0, 0, 0, 0, 0)
        bits = ctypes.c_void_p()
        self.bitmap = gdi32.CreateDIBSection(self.dc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
        gdi32.SelectObject(self.dc, self.bitmap)
        self.pixels = np.ctypeslib.as_array(ctypes.cast(bits, ctypes.POINTER(ctypes.c_ubyte)), shape=(MAXH, MAXW, 4))
        self.fonts = {k: font(round(v * self.scale * SS), b) for k, (v, b) in
                      {"small": (12, False), "text": (13.5, False), "bold": (13.5, True), "time": (12.5, True)}.items()}
        self.fonts["note"] = font(round(15 * self.scale * SS), name="seguisym.ttf")
        self._icon_fonts()
        self._draw()
        user32.ShowWindow(self.hwnd, 4)  # SW_SHOWNOACTIVATE
        self.ready.set()
        msg = wt.MSG()
        last_hover = 0.0
        drawn = None
        while True:
            moving = self._animate()
            sig = self._signature()
            if moving or self._live() or sig != drawn:  # a still card isn't redrawn (the call card costs ~20 ms)
                self._draw()
                drawn = sig
            if time.time() - last_hover > 0.1:
                last_hover = time.time()
                self._check_hover()
            timeout = 16 if moving or self._smooth() else 50 if self._live() else 120
            user32.MsgWaitForMultipleObjects(0, None, False, timeout, 0x04FF)
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            if self.wake.is_set():
                self.wake.clear()

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == 0x0021:  # WM_MOUSEACTIVATE: clicking the capsule never takes focus from his work
            return 3
        if msg in (0x0202, 0x0205):  # left / right button up
            x = ctypes.c_short(lparam & 0xFFFF).value / self.scale
            part = "right" if msg == 0x0205 else "body"
            if part == "body":
                part = next((p for x0, x1, p in self.hits if x0 <= x <= x1), "body")  # a drawn button
            self._fire(self.on_click, part)
            return 0
        if msg == 0x0312:  # WM_HOTKEY: one of his global shortcuts
            name = self.hotkeys.get(int(wparam))
            if name:
                self._fire(self.on_click, name)
            return 0
        if msg == 0x0233:  # WM_DROPFILES: files dropped on the capsule
            drop = wt.HANDLE(wparam)
            buf = ctypes.create_unicode_buffer(1024)
            paths = []
            for i in range(shell32.DragQueryFileW(drop, 0xFFFFFFFF, None, 0)):
                shell32.DragQueryFileW(drop, i, buf, 1024)
                paths.append(buf.value)
            shell32.DragFinish(drop)
            self.dragging = False
            if paths and self.on_drop:
                threading.Thread(target=self._fire, args=(self.on_drop, paths), daemon=True).start()
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    @staticmethod
    def _fire(fn, arg):
        try:
            fn(arg)
        except Exception:
            pass

    def _check_hover(self):
        pt = wt.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        x0, y0, x1, y1 = self.box
        hover = x0 <= pt.x <= x1 and y0 <= pt.y <= y1 + 4
        if hover != self.hover:
            self.hover = hover
            self.wake.set()
        # a file being dragged towards the top centre: the left button is down and the cursor isn't a plain one
        # (arrow, text, hand, resize: a window being moved or text selected), held there for a moment
        info = CURSORINFO(ctypes.sizeof(CURSORINFO))
        user32.GetCursorInfo(ctypes.byref(info))
        near = abs(pt.x - self.screen // 2) < 260 * self.scale and pt.y < (self.top + 110) * self.scale
        held = user32.GetAsyncKeyState(0x01) & 0x8000 and info.hCursor not in self.plain
        card = self.view.get("mode") == "card"
        if held and near and not card:
            self.drag_since = self.drag_since or time.time()
        elif not held or card:
            self.drag_since = 0.0
        dragging = bool(self.drag_since and time.time() - self.drag_since > 0.2)
        if dragging != self.dragging:
            self.dragging = dragging
            self.wake.set()

    # ------------------------------------------------------------ what to show
    def _live(self):
        """Something keeps moving (waveform, equaliser, recording clock): keep drawing."""
        mode = self.view.get("mode")
        if mode in ("listen", "think", "speak"):
            return True
        if mode not in ("rest", None) or self.dragging:  # a card, a call, an alert: the music isn't on screen
            return False
        music = self.extras.get("music")
        return "recording" in self.extras or bool(music and music.get("playing"))

    def _smooth(self):
        """The waveform and her voice need full frame rate; the small equaliser and clocks look fine at 20 fps."""
        return self.view.get("mode") in ("listen", "think", "speak")

    def _signature(self):
        """What the capsule shows right now; while it stays the same (and nothing moves), there's nothing to draw.
        A timer changes it once a second."""
        with self.lock:
            return (repr(self.view), repr(self.extras), self.hover, self.dragging,
                    int(time.time()) if "timer" in self.extras else 0)

    def _target(self):
        """(width, height, radius) in logical pixels for the current view."""
        v, ex = self.view, self.extras
        mode = v.get("mode")
        if mode == "card":
            return v.get("w", 440), v.get("h", 300), 26
        if self.dragging:
            return 340, 64, 32
        if mode in ("call", "alert"):
            return 400, 64, 32
        if mode == "banner":
            return 420, 58, 29
        if mode == "listen":
            return 236, 44, 22
        if mode == "think":
            return 120, 32, 16
        if mode == "speak":
            lines = self._wrap(v.get("text", ""), 400)
            return (440, 26 + 19 * len(lines), 20) if lines else (150, 32, 16)
        if mode == "notice":
            lines = self._wrap(v.get("text", ""), 400)
            return 440, 26 + 19 * max(1, len(lines)), 20
        music, rec, timer = ex.get("music"), ex.get("recording"), ex.get("timer")
        if music and music.get("peek", 0) > time.time():
            return 330, 40, 20
        if self.hover:
            return 196 + (70 if rec else 0) + (72 if timer else 0), 32, 16
        width = 96 + (64 if rec else 0) + (24 if music and music.get("playing") else 0) + (66 if timer else 0)
        return width, 26, 13

    def _animate(self):
        with self.lock:
            w, h, r = self._target()
        self.w.target, self.h.target, self.r.target = w, h, r
        moving = any([self.w.step(), self.h.step(), self.r.step()])
        settled = 1 - min(1.0, abs(self.w.target - self.w.x) / 120 + abs(self.h.target - self.h.x) / 50)
        self.fade = max(0.0, settled) ** 2
        return moving

    def _wrap(self, text, width):
        """Her reply as at most 3 lines that fit width (logical px)."""
        if not text:
            return []
        f = self.fonts["text"] if hasattr(self, "fonts") else None
        if f is None:
            return [text[:60]]
        limit = width * self.scale * SS
        words, lines, line = text.split(), [], ""
        for word in words:
            test = (line + " " + word).strip()
            if f.getlength(test) <= limit:
                line = test
            else:
                lines.append(line)
                line = word
                if len(lines) == 3:
                    break
        if len(lines) < 3 and line:
            lines.append(line)
        if len(lines) == 3 and " ".join(lines) != " ".join(words):
            lines[2] = lines[2].rstrip(".,") + "…"
        return lines[:3]

    def _draw(self):
        s = self.scale
        w, h = max(20, self.w.x), max(8, self.h.x)
        pw, ph = int(round(w * s)), int(round(h * s))
        pw, ph = min(pw, MAXW - 2), min(ph, MAXH - 2)
        # the shape from its distance field (fast at any size, so the card can grow at full frame rate)
        rad = min(self.r.x * s, ph / 2, pw / 2)
        xs = np.arange(pw, dtype=np.float32)[None, :]
        ys = np.arange(ph, dtype=np.float32)[:, None]
        qx = np.maximum(np.abs(xs + 0.5 - pw / 2) - (pw / 2 - rad), 0)
        qy = np.maximum(np.abs(ys + 0.5 - ph / 2) - (ph / 2 - rad), 0)
        dist = np.sqrt(qx * qx + qy * qy) - rad  # < 0 inside
        alpha = np.clip(0.5 - dist, 0, 1)
        edge = np.clip(1 - np.abs(dist + 0.9 * s), 0, 1) * 0.09  # a faint light rim
        chans = [FILL[k] + (255 - FILL[k]) * edge for k in range(3)]  # R, G, B as 2D float32
        with self.lock:
            view, extras = dict(self.view), dict(self.extras)
        self.hits = []
        if self.fade > 0.05 and view.get("mode") != "card":
            layer = Image.new("RGBA", (pw * SS, ph * SS), (0, 0, 0, 0))
            self._content(layer, ImageDraw.Draw(layer), view, extras, pw * SS, ph * SS, s * SS, int(255 * self.fade))
            top = np.asarray(layer.resize((pw, ph), Image.LANCZOS)).astype(np.float32)
            ta = top[..., 3] / 255
            chans = [c * (1 - ta) + top[..., k] * ta for k, c in enumerate(chans)]
        bgra = np.empty((ph, pw, 4), dtype=np.uint8)
        for k in range(3):
            bgra[..., 2 - k] = chans[k] * alpha  # premultiplied, BGR order
        bgra[..., 3] = alpha * 255
        self.pixels[:ph, :pw] = bgra
        x = self.screen // 2 - pw // 2
        y = int(self.top * s)
        self.box = (x, y, x + pw, y + ph)
        blend = BLENDFUNCTION(0, 0, 255, 1)
        user32.UpdateLayeredWindow(self.hwnd, None, ctypes.byref(wt.POINT(x, y)), ctypes.byref(wt.SIZE(pw, ph)),
                                   self.dc, ctypes.byref(wt.POINT(0, 0)), 0, ctypes.byref(blend), 2)

    def _content(self, layer, d, view, extras, W, H, u, alpha):
        """Draw the mode's content into the supersampled image (W x H px, u = px per logical px)."""
        t = time.time()
        white = WHITE + (alpha,)
        grey = GREY + (alpha,)
        mode = view.get("mode")
        cy = H / 2
        hits = []

        def button(cx, r, fill, glyph, color, part, size="icon"):  # a round button with a Fluent icon
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=tuple(fill[:3]) + (alpha,))
            d.text((cx, cy), GLYPH[glyph], font=self.fonts[size], fill=tuple(color[:3]) + (alpha,), anchor="mm")
            hits.append(((cx - r - 3 * u) / u, (cx + r + 3 * u) / u, part))

        def pill(x1, w, h, fill, text, color, part):  # a text button ending at x1
            d.rounded_rectangle((x1 - w, cy - h / 2, x1, cy + h / 2), radius=h / 2, fill=tuple(fill[:3]) + (alpha,))
            d.text((x1 - w / 2, cy), text, font=self.fonts["bold"], fill=tuple(color[:3]) + (alpha,), anchor="mm")
            hits.append(((x1 - w - 3 * u) / u, (x1 + 3 * u) / u, part))
            return x1 - w

        def two_lines(x, label, text, width, label_color=grey):
            d.text((x, cy - 8.5 * u), self._fit(label, width, "small"), font=self.fonts["small"], fill=label_color,
                   anchor="lm")
            d.text((x, cy + 9 * u), self._fit(text, width, "bold"), font=self.fonts["bold"], fill=white, anchor="lm")

        if self.dragging:  # files dragged near the top: a target to drop them on
            d.rounded_rectangle((8 * u, 8 * u, W - 8 * u, H - 8 * u), radius=(H - 16 * u) / 2, outline=DIM + (alpha,),
                                width=int(1.5 * u))
            label = "Drop to give it to Karen"
            lw = self.fonts["bold"].getlength(label)
            x = W / 2 - (lw + 26 * u) / 2
            d.text((x + 8 * u, cy), GLYPH["drop"], font=self.fonts["icon"], fill=white, anchor="mm")
            d.text((x + 26 * u, cy), label, font=self.fonts["bold"], fill=white, anchor="lm")
        elif mode == "call":  # the phone rings: who, then reject (red) and answer (green)
            name = view.get("name") or "Someone"
            r = 20 * u
            d.ellipse((12 * u, cy - r, 12 * u + 2 * r, cy + r), fill=(72, 72, 78, alpha))
            d.text((12 * u + r, cy), name[:1].upper(), font=self.fonts["big"], fill=white, anchor="mm")
            button(W - 34 * u, 18 * u, GREEN, "phone", WHITE, "answer")
            button(W - 80 * u, 18 * u, RED, "hangup", WHITE, "reject")
            two_lines(62 * u, "Phone · incoming call", name, W - 168 * u)
        elif mode == "alert":  # a timer or reminder went off: Snooze / Stop
            timer = view.get("kind") == "timer"
            r = 20 * u
            d.ellipse((12 * u, cy - r, 12 * u + 2 * r, cy + r), fill=(64, 42, 8, alpha))
            d.text((12 * u + r, cy), GLYPH["timer" if timer else "bell"], font=self.fonts["icon_l"],
                   fill=ORANGE + (alpha,), anchor="mm")
            x = pill(W - 14 * u, 62 * u, 32 * u, ORANGE, "Stop", (20, 12, 0), "stop")
            x = pill(x - 8 * u, 78 * u, 32 * u, (52, 52, 58), "Snooze", WHITE, "snooze")
            two_lines(62 * u, "Timer done" if timer else "Reminder", view.get("text", ""), x - 72 * u,
                      ORANGE + (alpha,))
        elif mode == "banner":  # a WhatsApp / text message on his phone
            whatsapp = "whatsapp" in view.get("app", "").lower()
            r = 17 * u
            d.ellipse((13 * u, cy - r, 13 * u + 2 * r, cy + r),
                      fill=((37, 211, 102) if whatsapp else (10, 132, 255)) + (alpha,))
            d.text((13 * u + r, cy), GLYPH["chat"], font=self.fonts["icon"], fill=white, anchor="mm")
            d.text((W - 18 * u, cy - 8.5 * u), "now", font=self.fonts["small"], fill=grey, anchor="rm")
            title = view.get("title") or ("WhatsApp" if whatsapp else "Message")
            d.text((58 * u, cy - 8.5 * u), self._fit(title, W - 120 * u, "bold"), font=self.fonts["bold"], fill=white,
                   anchor="lm")
            d.text((58 * u, cy + 9 * u), self._fit(view.get("text", ""), W - 76 * u, "small"), font=self.fonts["small"],
                   fill=grey, anchor="lm")
        elif mode == "listen":  # like a dictation bar: ✕ | waveform | ✓
            rr = 15 * u
            lx, rx = 7 * u + rr, W - 7 * u - rr
            button(lx, rr, (48, 48, 54), "cancel", WHITE, "cancel", "icon_s")
            button(rx, rr, WHITE, "check", FILL, "done", "icon_s")
            self._bars(d, (lx + rr + 12 * u, rx - rr - 12 * u), cy, 14 * u, u, CYAN + (alpha,), self.levels[-18:])
        elif mode == "think":  # three soft white dots, rising in turn
            for i in range(3):
                phase = (math.sin(t * 6 - i * 0.9) + 1) / 2
                r = 3.2 * u
                x = W / 2 + (i - 1) * 13 * u
                y = cy - 2.5 * u * phase
                d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 255, 255, int(alpha * (0.35 + 0.65 * phase))))
        elif mode in ("speak", "notice"):
            lines = self._wrap(view.get("text", ""), 400)
            if not lines:
                self._bars(d, (W / 2 - 40 * u, W / 2 + 40 * u), cy, 9 * u, u, white, self.levels[-12:])
            for i, line in enumerate(lines):
                d.text((20 * u, 13 * u + i * 19 * u), line, font=self.fonts["text"], fill=white)
        elif mode != "card":
            self._compact(layer, d, extras, W, H, u, alpha, white, grey, t)
        self.hits = hits

    def _icon_fonts(self):
        for key, size in (("icon", 15), ("icon_s", 12), ("icon_l", 18)):
            self.fonts[key] = font(round(size * self.scale * SS), name="SegoeIcons.ttf")
        self.fonts["big"] = font(round(17 * self.scale * SS), True)

    def _compact(self, layer, d, ex, W, H, u, alpha, white, grey, t):
        cy = H / 2
        music, rec, timer = ex.get("music"), ex.get("recording"), ex.get("timer")
        tint = tuple((music or {}).get("accent") or (52, 211, 153))  # the equaliser takes the cover's colour
        if music and music.get("peek", 0) > t:  # a new song: cover, title and artist for a few seconds
            a = 28 * u
            self._cover(layer, d, music, 7 * u, cy - a / 2, a, 7 * u, alpha, white)
            title = self._fit(music.get("title", ""), 220 * u, "bold")
            d.text((44 * u, cy - 7.5 * u), title, font=self.fonts["bold"], fill=white, anchor="lm")
            d.text((44 * u, cy + 8 * u), self._fit(music.get("artist", ""), 220 * u, "small"), font=self.fonts["small"],
                   fill=grey, anchor="lm")
            self._eq(d, W - 26 * u, cy, u, alpha, t, tint)
            return
        playing = bool(music and music.get("playing"))
        if playing and not self.hover:  # like Dynamic Island: the cover on the left, the equaliser on the right
            a = 18 * u
            self._cover(layer, d, music, 5 * u, cy - a / 2, a, 5 * u, alpha, white)
        else:  # resting: a small soft white dot (breathes while something is live)
            glow = (math.sin(t * 2) + 1) / 2 if self._live() else 0.5
            x = 16 * u
            r = 3.5 * u
            d.ellipse((x - r, cy - r, x + r, cy + r), fill=(255, 255, 255, int(alpha * (0.35 + 0.4 * glow))))
            if self.hover:
                d.text((x + 12 * u, cy), "Ask Karen", font=self.fonts["text"], fill=white, anchor="lm")
                hint = (self.keys.get("ask") or "").replace("+", " ").title() if self.hotkeys else ""
                if hint:
                    x += 12 * u + self.fonts["text"].getlength("Ask Karen") + 8 * u
                    d.text((x, cy), hint, font=self.fonts["small"], fill=(110, 110, 118, alpha), anchor="lm")
        right = W - 14 * u
        if playing:
            self._eq(d, right - 6 * u, cy, u, alpha, t, tint)
            right -= 26 * u
        if timer:  # the next timer: a ring that empties and the time left, in orange
            left = max(0, timer["due"] - t)
            label = f"{int(left) // 3600}:{int(left) // 60 % 60:02d}:{int(left) % 60:02d}" if left >= 3600 else \
                f"{int(left) // 60}:{int(left) % 60:02d}"
            if timer.get("bell"):  # a reminder within the next minutes: a bell and "in 4 min"
                label = f"in {max(1, round(left / 60))} min"
            d.text((right, cy), label, font=self.fonts["time"], fill=ORANGE + (alpha,), anchor="rm")
            cx = right - self.fonts["time"].getlength(label) - 11 * u
            rr = 6 * u
            if timer.get("bell"):
                d.text((cx, cy), GLYPH["bell"], font=self.fonts["icon_s"], fill=ORANGE + (alpha,), anchor="mm")
            else:
                frac = left / max(1.0, timer.get("total", left))
                d.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), outline=(90, 58, 10, alpha), width=int(2 * u))
                if frac > 0.002:
                    d.arc((cx - rr, cy - rr, cx + rr, cy + rr), -90, -90 + 360 * frac, fill=ORANGE + (alpha,),
                          width=int(2 * u))
            right = cx - rr - 10 * u
        if rec:
            secs = int(t - rec.get("since", t))
            label = f"{secs // 60:02d}:{secs % 60:02d}"
            d.text((right, cy), label, font=self.fonts["time"], fill=(255, 90, 90, alpha), anchor="rm")
            tw = self.fonts["time"].getlength(label)
            on = int(t * 2) % 2 == 0
            rr = 3.5 * u
            dx = right - tw - 9 * u
            d.ellipse((dx - rr, cy - rr, dx + rr, cy + rr), fill=(255, 59, 48, alpha if on else alpha // 3))

    def _fit(self, text, width, font_key):
        f = self.fonts[font_key]
        if f.getlength(text) <= width:
            return text
        while text and f.getlength(text + "…") > width:
            text = text[:-1]
        return text.rstrip() + "…"

    @staticmethod
    def _bars(d, span, cy, height, u, color, levels):
        x0, x1 = span
        n = len(levels)
        step = (x1 - x0) / max(1, n)
        w = max(1.5 * u, step * 0.42)
        for i, lv in enumerate(levels):
            hh = max(1.6 * u, height * min(1.0, lv * 1.6))
            x = x0 + step * (i + 0.5)
            d.rounded_rectangle((x - w / 2, cy - hh, x + w / 2, cy + hh), radius=w / 2, fill=color)

    def _cover(self, layer, d, music, x, y, size, radius, alpha, white):
        """The song's cover with rounded corners at (x, y), size px square; a ♫ tile when the app gave none."""
        img = music.get("image")
        n, x, y = int(round(size)), int(round(x)), int(round(y))
        if img is None:
            d.rounded_rectangle((x, y, x + n, y + n), radius=radius, fill=(52, 52, 58, alpha))  # grey: monochrome
            d.text((x + n / 2, y + n / 2), "♫", font=self.fonts["note"], fill=white, anchor="mm")
            return
        key = (music.get("cover"), music.get("title"), n)  # the cover's version, never id(img): ids get reused
        if getattr(self, "_art", (None,))[0] != key:  # resized once per cover and size, not every frame
            mask = Image.new("L", (n, n), 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, n - 1, n - 1), radius=radius, fill=255)
            self._art = (key, img.resize((n, n), Image.LANCZOS).convert("RGBA"), mask)
        _, art, mask = self._art
        if alpha < 255:
            mask = mask.point(lambda v: v * alpha // 255)
        layer.paste(art, (x, y), mask)

    @staticmethod
    def _eq(d, x, cy, u, alpha, t, color=(52, 211, 153)):
        """A small equaliser in the cover's colour: music is playing."""
        for i in range(3):
            hh = (3 + 5 * abs(math.sin(t * (4 + i * 1.3) + i))) * u
            bx = x + (i - 1) * 5 * u
            d.rounded_rectangle((bx - 1.5 * u, cy + 6 * u - hh * 1.6, bx + 1.5 * u, cy + 6 * u), radius=1.5 * u,
                                fill=tuple(color[:3]) + (alpha,))


class Island:
    """Ties the capsule and the panel to Karen: what she is doing, now playing, recordings, the map."""

    def __init__(self, webview, controls, cfg=None):
        self.webview, self.controls, self.cfg = webview, controls, cfg or {}
        self.panel = None  # the pywebview panel window
        self.panel_hwnd = None
        self.panel_ready = threading.Event()
        self.open = False  # the card is open (clicked, a confirm, a map)
        self.opened_by = ""  # "click" | "confirm" | "map"
        self.card = (440, 300)
        self.state = "sleeping"
        self.reply_until = 0.0
        self.blurred = 0.0
        self.before = None  # the window he was using before the panel took focus
        self.alert = None  # a timer / reminder that went off: {kind, text, item, until}
        self.call = None  # the phone ringing: {name, until}
        self.banner = None  # a phone message: {app, title, text, until}
        self.ringing = lambda: True  # main.py points this at PhoneWatch.ringing
        self.attachments = []
        self.clip = {"seq": user32.GetClipboardSequenceNumber(), "since": 0.0, "dismissed": None, "text": ""}
        self.volume = None
        keys = self.cfg.get("hotkeys", {"ask": "ctrl+alt+k", "talk": "ctrl+alt+t"}) or {}
        self.capsule = Capsule(self.clicked, top=self.cfg.get("top", 8), keys=keys, on_drop=self.dropped)
        threading.Thread(target=self._watch, daemon=True, name="island-watch").start()

    # ------------------------------------------------------------ panel window
    def create_panel(self, base):
        """The panel page, created once the main page's local server is up (base = its http address).
        It is never hidden, only parked off screen: a WebView2 window hidden and then shown with plain Win32 calls
        stays blank (WinForms still thinks it's hidden), and a hidden page lays itself out at zero width."""
        self.before = user32.GetForegroundWindow()  # a new window takes focus: give it back once it's parked
        self.panel = self.webview.create_window("Karen Island Panel", f"{base}/island.html", js_api=self.controls,
                                                width=420, height=280, x=-10000, y=-10000, frameless=True,
                                                on_top=True, shadow=False, focus=True, background_color="#0a0a0c")

    def panel_call(self, method, *args):
        if self.panel and self.panel_ready.is_set():
            try:
                self.panel.run_js(f"window.island && window.island.{method}(...{json.dumps(args)})")
            except Exception:
                pass

    def page_ready(self):
        try:
            self.panel_hwnd = self.panel.native.Handle.ToInt32()
            ex = user32.GetWindowLongW(self.panel_hwnd, -20)
            user32.SetWindowLongW(self.panel_hwnd, -20, (ex | 0x80) & ~0x40000)  # tool window: no taskbar or Alt+Tab
            self._place_panel(False)
            self._give_focus_back()
        except Exception:
            self.panel_hwnd = None
        self.panel_ready.set()
        song = self.capsule.extras.get("music")
        if song:
            self.send_song(song)
        if self.attachments:
            self.panel_call("attachments", self.attachments)
        self.panel_call("limits", {"maxh": self.max_height()})
        self.panel_call("keys", {k: v.replace("+", " ").title() for k, v in self.capsule.keys.items()
                                 if k in self.capsule.hotkeys.values()})

    def _place_panel(self, show, activate=False):
        if not self.panel_hwnd:
            return
        s = self.capsule.scale
        if not show:  # parked off screen at the card's inner size, so the page lays out as it will look
            w, h = self.card
            user32.SetWindowPos(self.panel_hwnd, -1, -10000, -10000, int((w - 20) * s), int((h - 20) * s), 0x0010)
            return
        rect = self.capsule.card_rect()
        if not rect:
            return
        inset = int(10 * s)
        x0, y0, x1, y1 = rect
        user32.SetWindowPos(self.panel_hwnd, -1, x0 + inset, y0 + inset, x1 - x0 - 2 * inset, y1 - y0 - 2 * inset,
                            0x0040 | (0 if activate else 0x0010))  # SHOWWINDOW, NOACTIVATE unless he clicked
        if activate:
            user32.SetForegroundWindow(self.panel_hwnd)

    def _give_focus_back(self):
        """The panel is parked off screen: if it still has the keyboard, hand it back to his window."""
        if self.panel_hwnd and user32.GetForegroundWindow() == self.panel_hwnd and self.before:
            user32.SetForegroundWindow(self.before)

    def expand(self, why="click", size=None):
        if not self.open:
            fg = user32.GetForegroundWindow()
            if fg != self.panel_hwnd:
                self.before = fg
        self.open, self.opened_by = True, why
        if size:
            self.card = size
        self.capsule.set(mode="card", w=self.card[0], h=self.card[1])
        self.volume = None  # sent again within 2 s, for the slider
        if why == "click":
            threading.Thread(target=self.offer_clip, daemon=True).start()  # the clipboard can be slow to open
        self.panel_call("opened", why)
        threading.Thread(target=self._show_when_open, args=(why == "click",), daemon=True).start()

    def offer_clip(self):
        """Text or a link he copied in the last 10 minutes: the panel offers Summarise / Translate / Explain."""
        c = self.clip
        if not c["since"] or time.time() - c["since"] > 600 or c["dismissed"] == c["seq"]:
            self.panel_call("clip", None)
            return
        try:
            import pyperclip
            text = (pyperclip.paste() or "").strip()
        except Exception:
            text = ""
        link = bool(re.fullmatch(r"https?://\S+", text))
        if len(text) < (8 if link else 25) or (not link and SECRET_LIKE.match(text)):
            self.panel_call("clip", None)  # too short to be worth it, or looks like a password / key
            return
        c["text"] = text
        self.panel_call("clip", {"text": text[:280], "link": link, "chars": len(text)})

    def clip_action(self, action):
        """The panel's clipboard buttons: builds the request from the whole copied text (up to 6,000 characters)."""
        text, c = self.clip["text"], self.clip
        c["dismissed"] = c["seq"]
        if action == "dismiss" or not text:
            self.panel_call("clip", None)
            return None
        if re.fullmatch(r"https?://\S+", text):
            if re.search(r"youtu\.?be", text):
                return f"Summarise this YouTube video: {text}"
            page = read_page_text(text)
            if not page:
                return f"I couldn't read that page ({text}). Say so briefly."
            text = f"{text}\n\n{page}"
        what = {"summarise": "Summarise this in a few short points", "translate": "Translate this into English",
                "explain": "Explain this simply"}.get(action, "Summarise this")
        return f"{what}:\n\n{text[:6000]}"

    def _show_when_open(self, activate):
        for _ in range(80):  # the card grows first, then the panel appears on it
            if not self.open:
                return
            if self.capsule.card_rect():
                self._place_panel(True, activate)
                return
            time.sleep(0.02)

    def collapse(self):
        if not self.open:
            return
        self.open = False
        self._place_panel(False)
        self._give_focus_back()
        if self.opened_by == "map":
            self.card = (440, self.card[1])
        self.panel_call("closed")
        self._show_state()

    def blur(self):
        """The panel lost focus (he clicked somewhere else): fold back, unless a yes/no is waiting."""
        if self.open and self.opened_by != "confirm":
            self.blurred = time.time()
            self.collapse()

    def max_height(self):
        """The tallest card that fits (logical px): the drawing buffer, the screen's work area under the capsule, and
        640 at most. The page fits itself into this (its conversation scrolls), so its buttons are never cut off: a
        card taller than the buffer used to be drawn short and the panel's bottom (the action tiles) was lost."""
        s = self.capsule.scale
        work = wt.RECT()
        user32.SystemParametersInfoW(0x30, 0, ctypes.byref(work), 0)  # SPI_GETWORKAREA (physical px for this thread)
        screen_h = (work.bottom - work.top) or user32.GetSystemMetrics(1)
        return int(min(640, (MAXH - 4) / s, screen_h / s - self.capsule.top - 24))

    def resize(self, w, h):
        """The panel page asks for a card size that fits its content."""
        h = min(h, self.max_height())
        smaller = w < self.card[0] or h < self.card[1]
        self.card = (int(w), int(h))
        if self.open:
            if smaller:
                self._place_panel(False)  # hide it while the card shrinks, so it never sticks out
            self.capsule.set(mode="card", w=self.card[0], h=self.card[1])
            threading.Thread(target=self._show_when_open, args=(False,), daemon=True).start()

    def clicked(self, part):
        if part == "right":
            self.controls.open_main()
        elif part == "cancel":
            self.controls.listen_cut("cancel")
        elif part == "done":
            self.controls.listen_cut("done")
        elif part in ("stop", "snooze"):  # a timer / reminder that went off
            alert, self.alert = self.alert, None
            self.controls.island_alert(part, (alert or {}).get("item"))
            self._show_state()
        elif part in ("answer", "reject"):  # the phone ringing
            self.call = None
            self._show_state()
            threading.Thread(target=self.controls.island_phone, args=(part == "answer",), daemon=True).start()
        elif part == "ask":  # his shortcut (Ctrl+Alt+K): the ask box, from anywhere
            if self.open and self.opened_by == "click":
                self.collapse()
            else:
                self.expand("click")
        elif part == "talk":  # his push-to-talk shortcut (Ctrl+Alt+T; Ctrl+Alt+Space is taken on his PC)
            if self.open:
                self.collapse()
            self.controls.talk()
        elif self.banner and self.capsule.view.get("mode") == "banner":
            self.banner = None  # a click puts the message away
            self._show_state()
        elif time.time() - self.blurred < 0.4:
            return  # this click is what took focus from the panel, which already closed it
        elif self.state == "speaking":
            self.controls.stop()
        elif self.open:
            self.collapse()
        else:
            self.expand("click")

    # ------------------------------------------------------------ Karen's events (from UI.call)
    def event(self, method, *args):
        if method == "state":
            self.state = args[0] if args else "sleeping"
            if self.state == "confirm" and not self.open:
                self.expand("confirm")
            self._show_state()
        elif method == "level":
            mic, voice = (list(args) + [0, 0])[:2]
            self.capsule.level(voice if self.state == "speaking" else mic)
        elif method == "message":
            role, text = args[0], args[1] if len(args) > 1 else ""
            if role == "karen" and text:
                self.reply = text
                self.reply_until = time.time() + 4 + min(10, len(text) / 18)
                self._show_state()
        elif method == "done":
            self._show_state()
            if self.open and self.opened_by == "confirm":
                self.collapse()
        elif method == "confirmClose" and self.open and self.opened_by == "confirm":
            self.collapse()
        elif method == "attachments":
            self.attachments = args[0] if args else []
        if method in ("state", "message", "done", "confirm", "confirmClose", "muted", "meeting", "attachments"):
            self.panel_call(method, *args)

    # ------------------------------------------------------------ live activities (called by main.py)
    def ring(self, kind, text, item=None):
        """A timer or reminder went off: the capsule shows it with Snooze / Stop for a minute."""
        self.alert = {"kind": kind, "text": text, "item": item, "until": time.time() + 60}
        self._make_room()

    def phone_call(self, name):
        self.call = {"name": name, "until": time.time() + 60}
        self._make_room()

    def _make_room(self):
        """A call or an alarm is more urgent than the panel he opened: fold it so the buttons show."""
        if self.open and self.opened_by != "confirm":
            self.collapse()
        self._show_state()

    def phone_message(self, app, title, text):
        self.banner = {"app": app, "title": title, "text": text, "until": time.time() + 7}
        self._show_state()

    def note(self, text):
        """A short line in the capsule (e.g. "Call rejected.")."""
        self.reply = text
        self.reply_until = time.time() + 4
        self._show_state()

    def dropped(self, paths):
        """Files dropped on the capsule: attach them and open the panel, which suggests what to do with them."""
        files = [p for p in paths if os.path.isfile(p)]
        if not files:
            self.note("Drop files, not folders.")
            return
        self.controls.island_files(files)
        self.expand("click")

    def _show_state(self):
        if self.open:
            return
        st, now = self.state, time.time()
        if self.call and (self.call["until"] < now or not self.ringing()):
            self.call = None
        if self.alert and self.alert["until"] < now:
            self.alert = None
        if self.banner and self.banner["until"] < now:
            self.banner = None
        if self.call:
            self.capsule.set(mode="call", name=self.call["name"])
        elif st == "listening":
            self.capsule.set(mode="listen")
        elif self.alert:
            self.capsule.set(mode="alert", kind=self.alert["kind"], text=self.alert["text"])
        elif self.banner:
            self.capsule.set(mode="banner", **{k: v for k, v in self.banner.items() if k != "until"})
        elif st == "thinking":
            self.capsule.set(mode="think")
        elif st == "speaking":
            self.capsule.set(mode="speak", text=getattr(self, "reply", "") if self.reply_until > time.time() else "")
        elif self.reply_until > time.time() and getattr(self, "reply", ""):
            self.capsule.set(mode="notice", text=self.reply)
        else:
            self.capsule.set(mode="rest")

    def show_map(self, url, title, link=""):
        self.panel_call("map", url, title, link)
        self.expand("map", (600, 420))

    def close_map(self):
        if self.open and self.opened_by == "map":
            self.collapse()

    # ------------------------------------------------------------ now playing and recordings
    def send_song(self, song):
        """The panel's now-playing card (everything but the PIL cover)."""
        self.panel_call("music", {k: v for k, v in (song or {}).items() if k != "image"})

    def _watch(self):
        last_song, told, n = None, (None, 0.0), 0
        while True:
            try:
                if n % 2 == 0 or time.time() - COVER["since"] < 12:  # every second just after a song change
                    song = now_playing()
                    if song and song.get("playing"):
                        key = (song["title"], song["artist"])
                        if key != last_song:
                            song["peek"] = time.time() + 5  # a new song: show it, then shrink back
                            last_song = key
                        else:
                            song["peek"] = (self.capsule.extras.get("music") or {}).get("peek", 0)
                        self.capsule.extra("music", song)
                    else:
                        self.capsule.extra("music", None)
                        last_song = None if not song else last_song
                    # the panel: on a new song, play/pause, a cover that arrived late, and every 10 s (the progress
                    # bar runs on its own)
                    state = song and (song["title"], song["artist"], song["playing"], song.get("cover"))
                    if state != told[0] or (song and time.time() - told[1] > 10):
                        self.send_song(song if song and song["title"] else {})
                        told = (state, time.time())
                self.capsule.extra("recording", recording_since())
                self.capsule.extra("timer", next_timer())
                if self.reply_until and time.time() > self.reply_until:
                    self.reply_until = 0
                    self._show_state()
                now = time.time()
                if any(x and x["until"] < now for x in (self.alert, self.banner, self.call)) or \
                        (self.call and not self.ringing()):
                    self._show_state()  # an alert, message or call has run its time (or the phone stopped)
                seq = user32.GetClipboardSequenceNumber()
                if seq != self.clip["seq"]:  # he copied something: worth offering for the next few minutes
                    self.clip.update(seq=seq, since=now)
                if self.open and n % 2 == 1:  # the PC volume, for the panel's slider
                    vol = pc_volume()
                    if vol != self.volume:
                        self.volume = vol
                        self.panel_call("volume", vol)
            except Exception:
                pass
            n += 1
            time.sleep(1)

    def seek(self, seconds):
        media_seek(seconds)
        time.sleep(0.4)
        self.send_song(now_playing() or {})


COVER = {"key": None, "image": None, "url": "", "accent": None, "hash": None, "since": 0.0, "source": None, "tries": 0,
         "web_at": 0.0, "version": 0}
# the current song's cover. Apps change the title first and the picture later (YouTube Music can keep the last song's
# picture until a pause or seek), so it's read again every poll and only decoded when its bytes change; `source` is
# "app", "web" (YouTube thumbnail) or None (nothing yet). "version" goes up on
# every change: the capsule and the panel compare it, never id(image) (a freed picture's id is reused by the next one:
# 48 of 50 simulated song changes, so the capsule kept drawing the old cover).


def now_playing():
    """What Windows' media sessions say is playing (YouTube Music, Spotify, a browser tab...), or None. A session
    that is playing wins over the "current" one (a paused video in Chrome was shown instead of his music). Adds the
    position and length (seconds, at time `at`) and the cover: `image` (PIL, for the capsule), `art` (a data URL for
    the panel) and `accent` (r, g, b from the cover)."""
    from core import media
    try:
        song = media.now()
    except media.Unavailable:  # Windows' media service isn't answering: the app's window title says what's on
        song = media.window_song()
    except Exception:
        song = None
    if song is None:
        return None
    update_cover(song, song.pop("thumb", b""))
    song.update(image=COVER["image"], art=COVER["url"], accent=COVER["accent"], cover=COVER["version"])
    return song


COVER_LOCK = threading.Lock()  # the watch loop and a seek can read the song at the same moment


def update_cover(song, data):
    with COVER_LOCK:
        _update_cover(song, data)


OWNERS = {}  # picture md5 -> (song key, album) it was first taken for
WEB_CACHE = {}  # song key -> its web cover (a song that comes round again needs no new search)


def _update_cover(song, data):
    """Keeps COVER right for this song. YouTube Music changes the title at once but can keep the LAST song's picture
    until he pauses or seeks (seen 2026-11-04/05), so a picture that already belonged to another song is never taken
    (unless it's the same album); a new song starts blank, and 1 s without its own picture fetches its YouTube
    thumbnail. A real new picture from the app later replaces the web one."""
    import hashlib
    key = (song["title"], song["artist"], song["app"])
    album = song.get("album") or ""
    now = time.time()
    if key != COVER["key"]:
        COVER.update(key=key, image=None, url="", accent=None, hash=None, since=now, source=None, tries=0, web_at=0.0,
                     version=COVER["version"] + 1)
        if key in WEB_CACHE:
            _set_cover(WEB_CACHE[key], "web")
    digest = hashlib.md5(data).hexdigest() if data else None
    if digest and digest != COVER["hash"]:
        owner = OWNERS.get(digest)
        same_album = owner and album and owner[1] == album and owner[0][1] == key[1]
        if owner is None or owner[0] == key or same_album:
            image = square(data)
            if image is not None:
                _set_cover(image, "app", digest)
                OWNERS.setdefault(digest, (key, album))
                while len(OWNERS) > 200:
                    OWNERS.pop(next(iter(OWNERS)))
    if COVER["source"] is None and song["title"] and now - COVER["since"] >= 1 and COVER["tries"] < 3 \
            and now - COVER["web_at"] > 20:
        COVER.update(tries=COVER["tries"] + 1, web_at=now)  # in the background: a web request mustn't hold up the poll
        threading.Thread(target=web_cover, args=(key,), daemon=True).start()


def _set_cover(image, source, digest=None):
    COVER.update(image=image, url=data_url(image), accent=accent(image), source=source, hash=digest,
                 version=COVER["version"] + 1)


def square(data):
    """Picture bytes -> a square RGB image (a video's 16:9 thumbnail is cropped to its centre), or None."""
    import io
    try:
        im = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:
        return None
    side = min(im.size)
    x, y = (im.width - side) // 2, (im.height - side) // 2
    return im.crop((x, y, x + side, y + side))


def web_cover(key):
    """The app gave no picture: the song's YouTube video thumbnail (its results page, no key; i.ytimg.com's
    mqdefault is 16:9 without black bars, so its centre square is the picture)."""
    import urllib.parse
    import urllib.request
    title, artist, _ = key
    try:
        vid = None
        # YouTube can return nothing at all for a bare title ("Polladha Aasaigal": 0 results; "... song": plenty)
        for query in (f"{title} {artist}", f"{title} {artist} song"):
            url = "https://www.youtube.com/results?" + urllib.parse.urlencode({"search_query": " ".join(query.split())
                                                                                [:120]})
            req = urllib.request.Request(url, headers={"Accept-Language": "en", "User-Agent": "Mozilla/5.0 (Windows "
                                                       "NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0 Safari/537.36"})
            page = urllib.request.urlopen(req, timeout=8).read().decode("utf-8", "replace")
            vid = re.search(r'"videoRenderer":\{"videoId":"([\w-]{11})"', page)
            if vid:
                break
        if not vid:
            return
        data = urllib.request.urlopen(f"https://i.ytimg.com/vi/{vid.group(1)}/mqdefault.jpg", timeout=8).read()
    except Exception:
        return
    image = square(data)
    if image is None:
        return
    with COVER_LOCK:
        WEB_CACHE[key] = image
        while len(WEB_CACHE) > 40:
            WEB_CACHE.pop(next(iter(WEB_CACHE)))
        if COVER["key"] == key and COVER["source"] is None:  # still the same song, still without its own picture
            _set_cover(image, "web")


def data_url(image):
    if image is None:
        return ""
    import base64
    import io
    buf = io.BytesIO()
    image.save(buf, "JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def accent(image):
    """A bright colour from the cover for the progress bar, glow and equaliser (most colourful of its main colours,
    lifted so it reads on black); None for a grey cover."""
    if image is None:
        return None
    import colorsys
    small = image.resize((32, 32)).quantize(6)
    pal = small.getpalette()
    best, score = None, 0.0
    for count, i in small.getcolors():
        r, g, b = (c / 255 for c in pal[i * 3:i * 3 + 3])
        h, s, v = colorsys.rgb_to_hsv(r, g, b)
        sc = count * s * (0.3 + v)
        if s > 0.2 and v > 0.15 and sc > score:
            best, score = (h, s, v), sc
    if not best:
        return None
    h, s, v = best
    r, g, b = colorsys.hsv_to_rgb(h, min(max(s, 0.45), 0.8), max(v, 0.85))
    return [round(r * 255), round(g * 255), round(b * 255)]


def recording_since():
    """When the earliest running recording started: meeting notes (servers/meeting) or a voice/screen recording
    (servers/jarvis). None if nothing records."""
    starts = []
    for path in ("data/meeting.json", "data/recording.json"):
        try:
            with open(path, encoding="utf-8") as f:
                st = json.load(f)
        except (OSError, ValueError):
            continue
        if path.endswith("meeting.json"):
            if st.get("on") and st.get("since"):
                starts.append(float(st["since"]))
        else:
            starts += [float(v) for v in st.values() if isinstance(v, (int, float)) and time.time() - v < 7300]
    return {"since": min(starts)} if starts else None


TOTALS = {}  # (timer id, due) -> its length in seconds, for timers saved without their start ("set")
SECRET_LIKE = re.compile(r"^(?=.*\d)(?=.*[A-Za-z])\S{8,}$|^(sk|ghp|gho|xox[bp]|AKIA|AIza|GOCSPX|eyJ)[-_A-Za-z0-9.]{10,}")


def next_timer(path="data/reminders.json"):
    """The capsule's countdown: the next timer (any time in the next 24 h), else a reminder due within 10 minutes."""
    import datetime
    try:
        with open(path, encoding="utf-8") as f:
            items = json.load(f)
    except (OSError, ValueError):
        return None
    now = datetime.datetime.now()
    timers, soon = [], []
    for r in items:
        if r.get("done"):
            continue
        try:
            due = datetime.datetime.fromisoformat(r["due"])
        except (KeyError, ValueError):
            continue
        left = (due - now).total_seconds()
        if left <= 0:
            continue
        if r.get("timer") and left < 24 * 3600:
            total = TOTALS.setdefault((r.get("id"), r["due"]), left)  # with its due time: ids can be reused
            if r.get("set"):
                total = (due - datetime.datetime.fromisoformat(r["set"])).total_seconds()
            timers.append({"due": due.timestamp(), "total": max(total, left)})
        elif not r.get("timer") and left <= 600:
            soon.append({"due": due.timestamp(), "bell": True})
    pick = timers or soon
    return min(pick, key=lambda x: x["due"]) if pick else None


def read_page_text(url):
    """The main text of a web page (for "summarise the link I copied"), or ""."""
    try:
        import trafilatura
        html = trafilatura.fetch_url(url)
        return (trafilatura.extract(html) or "")[:6000] if html else ""
    except Exception:
        return ""


def _speakers():
    import comtypes
    comtypes.CoInitialize()
    from pycaw.pycaw import AudioUtilities
    return AudioUtilities.GetSpeakers().EndpointVolume


def pc_volume():
    """The PC's master volume: {"level": 0-100, "muted": bool}, or None."""
    try:
        ep = _speakers()
        return {"level": round(ep.GetMasterVolumeLevelScalar() * 100), "muted": bool(ep.GetMute())}
    except Exception:
        return None


def set_pc_volume(level=None, muted=None):
    try:
        ep = _speakers()
        if level is not None:
            ep.SetMasterVolumeLevelScalar(max(0.0, min(1.0, float(level) / 100)), None)
            if level > 0 and ep.GetMute():
                ep.SetMute(0, None)
        if muted is not None:
            ep.SetMute(1 if muted else 0, None)
    except Exception:
        pass


def media_seek(seconds):
    """Jump the playing song to a position (the panel's progress bar)."""
    from core import media
    return media.seek(seconds)


def media_control(action):
    """The panel's ⏮ ⏯ ⏭ buttons: pause / resume / next / previous (media keys if Windows' sessions don't answer)."""
    from core import media
    try:
        return media.control(action)
    except Exception:
        return []
