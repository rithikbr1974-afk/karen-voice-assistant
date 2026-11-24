"""/reelturbo for Karen Remote: ig-reels/turbo.py makes a 9:16 reel and the result is sent to Telegram.

Gemini writes short lines from current articles, MoneyPrinterTurbo (vendor/MoneyPrinterTurbo) voices them with Edge TTS
(ava/brian/emma/ryan) and times every word, Pixabay supplies footage per line and Remotion renders the captions and
HUD. It runs as a subprocess so a slow render never blocks Remote's chat.
Nothing is posted anywhere: the video only comes back to his Telegram chat.
"""
import json
import os
import subprocess
import sys
import threading

DIR = os.path.join("vendor", "MoneyPrinterTurbo")
PY = os.path.join(DIR, ".venv", "Scripts", "python.exe")  # MoneyPrinterTurbo's own venv (used by ig-reels/turbo.py)
KPY = os.path.join(".venv", "Scripts", "python.exe")  # Karen's venv runs turbo.py


class ReelTurbo:
    def __init__(self, cfg, send, send_file, log):
        self.cfg = cfg.get("reelturbo", {})
        self.send, self.send_file, self.log = send, send_file, log  # send(chat, text); send_file(chat, path, caption)
        self.busy = threading.Lock()

    def start(self, topic, chat):
        if not topic:
            self.send(chat, "Give me a topic: /reelturbo why UPI changed India")
        elif not os.path.exists(PY):
            self.send(chat, "MoneyPrinterTurbo isn't installed (run `uv sync` in vendor/MoneyPrinterTurbo).")
        elif not self._has_footage_key():
            self.send(chat, "I need a free Pixabay API key first (pixabay.com/api/docs). Put it in "
                            "vendor/MoneyPrinterTurbo/config.toml as pixabay_api_keys = [\"...\"].")
        elif self.busy.locked():
            self.send(chat, "🎬 Already making one; it'll be here in a few minutes.")
        else:
            threading.Thread(target=self._make, args=(topic, chat), daemon=True).start()

    @staticmethod
    def _has_footage_key():
        try:
            text = open(os.path.join(DIR, "config.toml"), encoding="utf-8").read()
        except OSError:
            return False
        return any(l.startswith(("pexels_api_keys", "pixabay_api_keys")) and '"' in l for l in text.splitlines())

    def _make(self, topic, chat):
        with self.busy:
            self.send(chat, f"🎬 Making a video about “{topic}” with MoneyPrinterTurbo. About 5-8 minutes.")
            self.log("reelturbo", f"making: {topic}")
            args = [sys.executable if not os.path.exists(KPY) else KPY, os.path.join("ig-reels", self.cfg.get("style", "launch") + ".py"), topic,
                    ]
            if self.cfg.get("voice"):  # ava | brian | emma | ryan; empty = a random one of the four
                args += ["--voice", self.cfg["voice"]]
            try:
                r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                                   timeout=self.cfg.get("timeout_s", 1800), creationflags=0x08000000,
                                   env={**os.environ, "PYTHONUTF8": "1"})
                out = next((json.loads(l) for l in reversed(r.stdout.splitlines()) if l.startswith("{")), None)
                err = " | ".join(r.stderr.strip().splitlines()[-3:])
            except subprocess.TimeoutExpired:
                out, err = None, "timed out"
            videos = [out["video"]] if out else []
            if not videos:
                self.log("error", f"reelturbo: {err}")
                self.send(chat, f"🎬 The video failed: {err[-300:] or 'no output'}")
                return
            self.log("reelturbo", f"ready: {videos[0]}")
            self.send_file(chat, videos[0], "🎬 " + topic + " (not posted)\n\n" + out["caption"][:700])
