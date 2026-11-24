"""Daily Instagram reel for Karen Remote: make it each morning, send it to Telegram, post it only after a ✅ tap.

Making (ig-reels/make.py) and posting run as subprocesses so a slow render (~3-5 min) never blocks Remote's chat.
Posting uses ig-reels/igcli.py (password login, best-frame cover, its own venv) unless `ig_reels.poster: api`
(ig-reels/publish.py, the official token API). State is kept in data/ig_reels.json:
{"last_made": "YYYY-MM-DD", "pending": {id: folder}}.
"""
import datetime
import json
import os
import subprocess
import sys
import threading

STATE = "data/ig_reels.json"
PY = os.path.join(".venv", "Scripts", "python.exe") if os.path.exists(".venv") else sys.executable
IG_PY = os.path.join("ig-reels", ".venv-ig", "Scripts", "python.exe")  # igcli's venv (instagrapi needs Pillow 13)


def _state():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except (OSError, ValueError):
        return {"pending": {}}


def _save(state):
    json.dump(state, open(STATE + ".tmp", "w", encoding="utf-8"), indent=1)
    os.replace(STATE + ".tmp", STATE)


def _run(args, timeout, py=PY):
    """Run an ig-reels script; returns (last JSON line or None, last stderr lines)."""
    r = subprocess.run([py, *args], stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", timeout=timeout,
                       creationflags=0x08000000, env={**os.environ, "PYTHONUTF8": "1"})
    out = next((json.loads(l) for l in reversed(r.stdout.splitlines()) if l.startswith("{")), None)
    return out, "\n".join(r.stderr.strip().splitlines()[-3:])


class Reels:
    def __init__(self, cfg, notify_file, send, log):
        self.cfg = cfg.get("ig_reels", {})
        self.notify_file, self.send, self.log = notify_file, send, log  # notify_file(path, caption, buttons)
        self.busy = threading.Lock()

    def tick(self):
        """From Remote's ticker: start today's reel once the configured time has passed."""
        if not self.cfg.get("enabled"):
            return
        now = datetime.datetime.now()
        hh, mm = map(int, str(self.cfg.get("time", "08:30")).split(":"))
        if now.time() >= datetime.time(hh, mm) and _state().get("last_made") != now.date().isoformat():
            self.start()

    def start(self, topic=None, chat=None):
        if self.busy.locked():
            if chat:
                self.send(chat, "🎬 I'm already making a reel; it'll be here in a few minutes.")
            return
        state = _state()
        if not topic:  # mark the day first, so a failure doesn't retry every 20 seconds
            state["last_made"] = datetime.date.today().isoformat()
            _save(state)
        threading.Thread(target=self._make, args=(topic, chat), daemon=True).start()

    def _make(self, topic, chat):
        with self.busy:
            if chat:
                self.send(chat, "🎬 Making a reel" + (f" about {topic}" if topic else " from today's trends")
                          + ". About 5 minutes.")
            self.log("reel", f"making ({topic or 'trending'})")
            try:
                out, err = _run(["ig-reels/make.py", *(["--topic", topic] if topic else [])], timeout=1200)
            except subprocess.TimeoutExpired:
                out, err = None, "timed out after 20 minutes"
            if not out:
                self.log("error", f"reel: {err}")
                self.send(chat, f"🎬 Today's reel failed: {err[-300:]}")
                return
            state = _state()
            rid = str(max(map(int, state.get("pending") or {"0": ""})) + 1)
            state.setdefault("pending", {})[rid] = out["folder"]
            _save(state)
            warn = ("\n⚠️ Check: " + "; ".join(out["warnings"])) if out.get("warnings") else ""
            self.log("reel", f"ready: {out['folder']} (hook {out.get('hook')})")
            self.notify_file(out["video"], f"🎬 Today's reel (hook {out.get('hook')}/100). Not posted yet.\n\n"
                                           f"{out['caption']}{warn}"[:1000],
                             [("✅ Post to Instagram", f"ig:{rid}:yes"), ("❌ Skip", f"ig:{rid}:no")])

    def tapped(self, chat, rid, answer):
        state = _state()
        folder = state.get("pending", {}).pop(rid, None)
        _save(state)
        if not folder:
            self.send(chat, "That reel has expired or was already handled.")
        elif answer != "yes":
            self.log("reel", f"skipped: {folder}")
            self.send(chat, "Skipped. Not posted.")
        else:
            threading.Thread(target=self._post, args=(chat, folder), daemon=True).start()

    def _post(self, chat, folder):
        self.send(chat, "📤 Posting to Instagram… (about a minute)")
        if self.cfg.get("poster", "cli") == "api":
            args, py = ["ig-reels/publish.py", folder], PY
        elif not os.path.exists(IG_PY):
            return self.send(chat, "❌ Not posted: the Instagram CLI isn't installed (ig-reels/.venv-ig is missing).")
        else:  # he already said yes with the ✅ tap, so --yes skips the CLI's own question
            args, py = ["ig-reels/igcli.py", "upload-reel", folder, "--yes"], IG_PY
        try:
            out, err = _run(args, timeout=900, py=py)
        except subprocess.TimeoutExpired:
            out, err = None, "timed out"
        self.log("reel", f"posted: {out}" if out else f"post failed: {err}")
        self.send(chat, f"✅ Posted on @{out['account']}: {out['permalink']}" if out else f"❌ Not posted: {err[-300:]}")
