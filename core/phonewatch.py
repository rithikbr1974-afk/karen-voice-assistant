"""Phone watcher for voice Karen: announces incoming calls ("Alex is calling") and new WhatsApp/SMS messages,
over the same adb wireless-debugging link as servers/phone. "pick up" / "reject" while it rings are handled here,
before the LLM. Polls: call state every 1.5 s, notifications every 8 s; 30 s apart while the phone is away."""
import glob
import os
import re
import shutil
import subprocess
import threading
import time

from core import adbguard

OTP = re.compile(r"\b(otp|one.time|verification|code is|passcode|password|cvv|\d{4,8} is your)\b", re.I)
PICK = re.compile(r"\b(pick (it )?up|answer( it| the call)?|accept|take (it|the call))\b", re.I)
REJECT = re.compile(r"\b(reject|decline|cut (it|the call)|hang up|end (it|the call))\b", re.I)
IGNORE = re.compile(r"^\W*(no|nope|ignore( it)?|leave it|later|not now)\W*$", re.I)


def _adb_exe():
    return shutil.which("adb") or next(iter(glob.glob(os.path.expandvars(
        r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Google.PlatformTools*\platform-tools\adb.exe"))), None)


class PhoneWatch:
    def __init__(self, cfg, announce, log, device="", second=""):
        """cfg: config.yaml phone_watch. announce(kind, text): kind "ring" or "message".
        second: the spare-SIM phone's serial (phone.second), never watched."""
        self.cfg, self.announce, self.log = cfg, announce, log
        self.exe, self.fixed, self.device = _adb_exe(), device, device
        self.second = [str(s) for s in (second if isinstance(second, list) else [second]) if s]
        self.apps = set(cfg.get("apps", ["com.whatsapp", "com.whatsapp.w4b", "com.google.android.apps.messaging"]))
        self.ringing_since = 0.0
        self.seen = None  # message notifications already known (None until the first look: no flood at start)

    def start(self):
        if self.exe:
            threading.Thread(target=self._loop, daemon=True).start()
        return self

    def adb(self, *args, timeout=15):
        cmd = [self.exe] + (["-s", self.device] if self.device else []) + list(args)
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=0x08000000)
            return (r.stdout + r.stderr).decode("utf-8", "replace")
        except (subprocess.TimeoutExpired, OSError):
            return ""

    @property
    def ringing(self):
        return time.time() - self.ringing_since < 60

    def command(self, text):
        """While the phone rings: "pick up" / "reject" / "ignore" act at once. Returns what to say, or None."""
        if not self.ringing:
            return None
        if PICK.search(text):
            self.adb("shell", "input", "keyevent", "5")
            self.ringing_since = 0
            return "Picked up. Talk on your phone."
        if REJECT.search(text):
            self.adb("shell", "input", "keyevent", "6")
            self.ringing_since = 0
            return "Call rejected."
        if IGNORE.search(text):
            self.ringing_since = 0
            return "Okay, I'll leave it."
        return None

    # ------------------------------------------------------------ polling
    def _loop(self):
        n, state = 0, "0"
        while True:
            try:
                if not adbguard.background_ok():  # Android Studio is open, or someone stopped adb: stay off it
                    self.seen = None
                    time.sleep(10)
                    continue
                ready = [d for d in re.findall(r"^(\S+)\tdevice$", self.adb("devices"), re.M) if d not in self.second]
                self.device = self.fixed or (ready[0] if ready else "")
                if not ready or (self.fixed and self.fixed not in ready):
                    self.seen = None
                    time.sleep(30)
                    continue
                if self.cfg.get("calls", True):
                    calls = re.findall(r"mCallState=(\d)", self.adb("shell", "dumpsys", "telephony.registry"))
                    now = "1" if "1" in calls else "2" if "2" in calls else "0"
                    if now == "1" and state != "1":
                        self.ringing_since = time.time()
                        self.announce("ring", f"{self._caller()} is calling.")
                    elif now != "1":
                        self.ringing_since = 0
                    state = now
                if self.cfg.get("messages", True) and n % 5 == 0:
                    self._messages()
                n += 1
            except Exception as e:  # never let the watcher die
                self.log("phone", f"watch error: {e!r}")
                time.sleep(10)
            time.sleep(1.5)

    def _records(self):
        """(pkg, title, text, category) for each notification on the phone."""
        dump = self.adb("shell", "dumpsys", "notification", "--noredact", timeout=25)
        out = []
        for block in re.split(r"\n\s*NotificationRecord\(", dump)[1:]:
            pkg = re.search(r"pkg=(\S+)", block)
            title = re.search(r"\n\s*android\.title=\w+ \((.*)\)\s*\n", block)
            text = re.search(r"\n\s*android\.text=\w+ \((.*?)\)\s*\n\s*(?:android\.|mCreation|\w+=)", block, re.S)
            cat = re.search(r"category=(\w+)", block[:800])
            if pkg:
                out.append((pkg[1], title[1].strip() if title else "", text[1].strip() if text else "",
                            cat[1] if cat else ""))
        return out

    def _caller(self):
        """The caller's name from the dialer's (or Truecaller's) call notification."""
        for _ in range(3):  # the notification appears a moment after the ring starts
            for pkg, title, text, cat in self._records():
                if cat == "call" and title and not re.search(r"incoming|ongoing|calling", title, re.I):
                    return title
            time.sleep(0.7)
        return "Someone"

    def _messages(self):
        now = {(p, t, x) for p, t, x, _ in self._records() if p in self.apps and t and x}
        if self.seen is not None:
            for pkg, title, text in sorted(now - self.seen):
                if re.fullmatch(r"WhatsApp( Business)?|Messages", title) or re.match(r"Checking for|Backup", text):
                    continue
                app = "WhatsApp" if "whatsapp" in pkg else "text message"
                body = "a security code" if OTP.search(text) else " ".join(text.split())[:200]
                self.announce("message", (app, title, body))
        self.seen = now
