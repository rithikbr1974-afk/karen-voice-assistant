"""Karen rings the user's Telegram when there's news (2026-11-12, his choice). CallMeBot (free, not Telegram's own API)
calls him; when he picks up, a robot voice says one line: "Karen here. You have 2 important updates. Check our chat."
Nothing else goes to CallMeBot: the details are in Karen's own Telegram chat (text + her voice note).

Set-up, once: he authorizes CallMeBot (it only calls people who did): the AUTH_LINK page, "Log in with Telegram". Sending
/start to @CallMeBot_txtbot is the other way CallMeBot's docs give, but for him it didn't register (2026-11-13: "Authorization
for user ... is not received" three times). Then /callme to Karen saves his Telegram @username in data/calls.json
(CALLMEBOT_USER in .env works too).

No call in quiet hours (calls.quiet; a reminder marked urgent still rings), sooner than calls.gap_min after the last
one (the news waits and is counted in the next call), or more than calls.max_per_day a day. /calls off pauses them.
"""
import ctypes
import datetime
import json
import os
import re
import time

import httpx

STATE = "data/calls.json"
API = "https://api.callmebot.com/start.php"
TEXT = "Karen here. You have {n} important update{s}. Check our chat."
TEST = "Karen here. This is a test call. Calls from Karen are working."
# CallMeBot answers HTTP 200 even when it fails, with the reason in the page ("Error: No User specified.")
FAILED = re.compile(r"(?im)^\s*(error|warning)\b|not authori[sz]ed|need to authori[sz]e|has not (?:started|authori)|"
                    r"blocked|disabled|invalid|limit (?:reached|exceeded)|too many|not received")
AUTH_LINK = "https://api2.callmebot.com/txt/login.php"  # CallMeBot's own sign-in page (from its Telegram call API docs)
SETUP = (f"Once: open {AUTH_LINK} and tap \"Log in with Telegram\" (it asks in Telegram: confirm). Or send /start to "
         "@CallMeBot_txtbot. Then send me /callme.")


def advice(said):
    """What to do about a failed ring, from CallMeBot's own words."""
    if re.search(r"not authori[sz]|authori[sz]ation .*not received|need to authori[sz]e|has not (?:started|authori)",
                 said, re.I):
        return "CallMeBot doesn't have your permission to call you yet. " + SETUP
    if re.search(r"privacy|security settings|calls? (?:are )?(?:disabled|blocked|not allowed)", said, re.I):
        return ("Your Telegram blocks calls from people you don't know: Telegram > Settings > Privacy and Security > "
                "Calls > Everybody (or add @CallMeBot_API under Always Allow). Then send me /callme.")
    if re.search(r"limit|too many|queue", said, re.I):
        return "CallMeBot is busy or at its limit: try /callme again in a few minutes."
    return SETUP


def in_span(span, now=None):
    """True inside a "23:00-07:00" style window (it may cross midnight); "" means never."""
    if not span or "-" not in span:
        return False
    a, b = (s.strip() for s in span.split("-", 1))
    t = (now or datetime.datetime.now()).strftime("%H:%M")
    return a <= t < b if a <= b else t >= a or t < b


def idle_s():
    """Seconds since the last keyboard or mouse input in this Windows session, or None if Windows won't say."""
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
    info = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return None
    tick = ctypes.windll.kernel32.GetTickCount
    tick.restype = ctypes.c_uint32  # (the default c_int goes negative after 24 days of uptime)
    return ((tick() - info.dwTime) & 0xFFFFFFFF) / 1000


def page_text(html):
    """CallMeBot's reply page as plain text (its scripts and tags dropped). Links keep their address: its "Click here
    to authorize CallMeBot" lost the one thing he needed (2026-11-13)."""
    html = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", html)
    html = re.sub(r"""(?is)<a\b[^>]*?href\s*=\s*["']?(https?://[^"'\s>]+)["']?[^>]*>(.*?)</a>""", r"\2 (\1)", html)
    text = re.sub(r"<[^>]+>", " ", html).replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"[ \t]+", " ", re.sub(r"\s*\n\s*", "\n", text)).strip()


class Caller:
    def __init__(self, cfg, log=print, api=API):
        self.cfg, self.log, self.api = cfg.get("calls") or {}, log, api

    # ------------------------------------------------------------ saved state
    def state(self):
        try:
            with open(STATE, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        today = datetime.date.today().isoformat()
        if data.get("day") != today:
            data.update(day=today, today=0)
        return data

    def save(self, data):
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        with open(STATE + ".tmp", "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, ensure_ascii=False)
        os.replace(STATE + ".tmp", STATE)

    def user(self):
        return (os.environ.get("CALLMEBOT_USER") or self.state().get("user") or "").strip()

    def set_user(self, user):
        data = self.state()
        data["user"] = user
        self.save(data)

    def pause(self, off=True):
        data = self.state()
        data["paused"] = off
        self.save(data)

    # ------------------------------------------------------------ rules
    def quiet(self, now=None):
        return in_span(self.cfg.get("quiet", "23:00-07:00"), now)

    def why_not(self, urgent=False):
        """Why a call can't go now ("" = it can): off, paused, no user, quiet, cap, or gap (try again later)."""
        data = self.state()
        if not self.cfg.get("enabled", True):
            return "off"
        if data.get("paused"):
            return "paused"
        if not self.user():
            return "no user"
        if self.quiet() and not urgent:
            return "quiet"
        if data.get("today", 0) >= self.cfg.get("max_per_day", 20):
            return "cap"
        if time.time() - data.get("last", 0) < self.cfg.get("gap_min", 3) * 60:
            return "gap"
        return ""

    # ------------------------------------------------------------ the call
    def ring(self, n=1, text=None):
        """Call him now (the rules are the caller's job: why_not). Returns (ok, what CallMeBot said)."""
        user = self.user()
        if not user:
            return False, "no Telegram username saved: send /callme"
        text = text or TEXT.format(n=n, s="" if n == 1 else "s")
        params = {"user": user, "text": text, "lang": self.cfg.get("voice", "en-IN-Standard-A"),
                  "rpt": self.cfg.get("repeat", 2), "cc": "no"}
        try:
            r = httpx.get(self.api, params=params, timeout=40)
            said = page_text(r.text)[:300]
            ok = r.status_code == 200 and not FAILED.search(said)
        except httpx.HTTPError as e:
            ok, said = False, f"couldn't reach CallMeBot ({type(e).__name__})"
        data = self.state()
        if ok:
            data["last"], data["today"] = time.time(), data.get("today", 0) + 1
        data["rings"] = (data.get("rings", []) + [{"at": datetime.datetime.now().isoformat(timespec="seconds"),
                                                   "n": n, "ok": ok, "said": said[:160]}])[-20:]
        self.save(data)
        self.log("call", f"{'rang' if ok else 'ring FAILED'} ({n} update{'' if n == 1 else 's'}): {said[:160]}")
        return ok, said

    def status(self):
        data, why = self.state(), self.why_not()
        user = self.user()
        on = "paused (/calls on to resume)" if data.get("paused") else "on" if self.cfg.get("enabled", True) else \
            "off in config.yaml"
        quiet = self.cfg.get("quiet", "23:00-07:00")
        lines = [f"📞 Calls: {on}",
                 f"Who: {user or 'not set up yet. ' + SETUP}",
                 f"Quiet hours: {quiet} (no calls; messages arrive silently)" if quiet else "Quiet hours: none",
                 f"Today: {data.get('today', 0)} of {self.cfg.get('max_per_day', 20)} calls"]
        last = (data.get("rings") or [None])[-1]
        if last:
            lines.append(f"Last: {last['at'][11:16]} {'ok' if last['ok'] else 'failed: ' + last['said'][:80]}")
        if why == "quiet":
            lines.append("It's quiet hours now.")
        return "\n".join(lines)
