"""Desktop interface: a pywebview window showing ui/index.html.

Python -> page: ui.call("state", "listening") runs window.karen.state("listening") in the page.
Page -> Python: the page calls methods of the `controls` object passed to UI (exposed as window.pywebview.api).
"""
import json
import os
import threading

STICKY = {"state", "config", "abilities", "muted", "memory", "stats", "attachments"}  # replayed when the page (re)loads


class UI:
    def __init__(self, controls, title="KAREN", hidden=False, characters=None):
        import webview
        self.webview = webview
        self.ready = threading.Event()
        self.sticky = {}
        self.viewer = None  # the "Karen Viewer" window, created on first use
        page = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ui", "index.html")
        link_avatar(os.path.dirname(os.path.abspath(page)), characters)
        self.window = webview.create_window(title, os.path.abspath(page), js_api=controls, width=1280, height=800,
                                            min_size=(900, 600), background_color="#05020a", hidden=hidden)

    def call(self, method, *args):
        if method in STICKY:
            self.sticky[method] = args
        if self.ready.is_set():
            try:
                self.window.run_js(f"window.karen && window.karen.{method}(...{json.dumps(args)})")
            except Exception:
                pass  # window closing

    def show(self, url):
        """Open (or reuse) the "Karen Viewer" window on a URL or file:// address."""
        try:
            if self.viewer is not None:
                self.viewer.load_url(url)
                self.viewer.show()
                self.viewer.restore()
                return
        except Exception:
            self.viewer = None  # it was closed
        self.viewer = self.webview.create_window("Karen Viewer", url, width=1100, height=820, background_color="#ffffff")
        self.viewer.events.closed += lambda: setattr(self, "viewer", None)

    def page_ready(self):
        self.ready.set()
        for method, args in list(self.sticky.items()):
            self.call(method, *args)

    def run(self, func):
        """Show the window (blocks the main thread) and run func in a background thread."""
        storage = os.path.join(os.environ.get("LOCALAPPDATA", "."), "Karen", "webview")
        clear_web_cache(storage)
        # http_server: the page is served from 127.0.0.1 instead of file://, because Chromium blocks JS modules and
        # model loading (the avatar) from file:// pages. Fixed storage folder: no temp-dir cleanup errors.
        self.webview.start(func, private_mode=False, storage_path=storage, http_server=True)


def clear_web_cache(storage):
    """WebView2 keeps its own copy of the page and scripts, and served an OLD index.html after the interface was
    updated (new Settings buttons missing, 2026-10-08). The files come from this PC's own server, so the cache saves
    nothing: delete it at every start. Settings and cookies are in other folders and stay."""
    import shutil
    for sub in ("Cache", "Code Cache", "GPUCache", "Service Worker"):
        shutil.rmtree(os.path.join(storage, "EBWebView", "Default", sub), ignore_errors=True)


def _link(src, dst):
    if os.path.exists(dst) and os.path.getsize(dst) == os.path.getsize(src):
        return
    if os.path.exists(dst):
        os.remove(dst)
    try:
        os.link(src, dst)  # a hard link: no second copy on disk
    except OSError:
        import shutil
        shutil.copyfile(src, dst)


def link_avatar(ui_dir, characters=None, motions="models/motions"):
    """The page can only load files under ui/, so ui/avatar/ gets hard links to each character's model (and
    textures) and to the Mixamo motions (models/motions/*.fbx), plus characters.json and motions.json for the page."""
    root = os.path.dirname(ui_dir)
    out = os.path.join(ui_dir, "avatar")
    os.makedirs(os.path.join(out, "motions"), exist_ok=True)
    listed = []
    for c in characters or [{"id": "karen", "name": "Karen", "file": "models/karen.vrm"}]:
        src = os.path.join(root, c["file"])
        ext = os.path.splitext(src)[1].lower()
        entry = {"id": c["id"], "name": c["name"], "kind": "vrm" if ext == ".vrm" else "fbx",
                 "file": f"{c['id']}/model{ext}", "available": os.path.exists(src), "textures": [],
                 "credit": c.get("credit", ""), "romance": c.get("romance", True)}
        if entry["available"]:
            os.makedirs(os.path.join(out, c["id"]), exist_ok=True)
            _link(src, os.path.join(out, entry["file"]))
            tex = os.path.join(root, c.get("textures", ""))
            for f in sorted(os.listdir(tex)) if c.get("textures") and os.path.isdir(tex) else []:
                if f.lower().endswith((".png", ".jpg", ".jpeg")):
                    _link(os.path.join(tex, f), os.path.join(out, c["id"], f))
                    entry["textures"].append(f)
        listed.append(entry)
    with open(os.path.join(out, "characters.json"), "w", encoding="utf-8") as fh:
        json.dump(listed, fh)
    names = []
    folder = os.path.join(root, motions)
    for f in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        if f.lower().endswith(".fbx"):
            _link(os.path.join(folder, f), os.path.join(out, "motions", f.lower()))
            names.append(f.lower()[:-4])
    with open(os.path.join(out, "motions.json"), "w", encoding="utf-8") as fh:
        json.dump(names, fh)


class NullUI:
    """Console mode (--no-ui)."""
    def call(self, method, *args):
        pass

    def show(self, url):
        import webbrowser
        webbrowser.open(url)
