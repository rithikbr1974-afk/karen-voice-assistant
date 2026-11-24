"""Karen: say "Karen", then a request.

  run.bat             interface window + voice
  run.bat --no-ui     voice only, in this console
  run.bat --text      type instead of talking (console)
  run.bat --mic       microphone level check
  run.bat --devices   list audio devices
"""
import ctypes
import datetime
import json
import os
import queue
import re
import sys
import threading
import time

import yaml
from dotenv import load_dotenv

os.chdir(os.path.dirname(os.path.abspath(__file__)))
# numpy's OpenBLAS reserves a buffer per CPU thread when it loads: 500 MB of memory in every Python process on this
# 16-thread i7, 18 MB with one thread (measured 2026-11-08). Karen's heavy maths runs in onnxruntime / CTranslate2
# with their own threads; numpy only does small jobs. With C: nearly full the page file couldn't grow, and the
# Cortex failed to start ("mkl_malloc: failed to allocate memory"). Remote imports this file, so it gets it too;
# core/tools.py passes it to the servers.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
# CTranslate2 (faster-whisper) pre-packs the model for Intel MKL: small.en then commits 2.3 GB instead of 0.4 GB
# (it touches ~0.7 GB). Same speed without it (2.29 s vs 2.37 s a request), same transcripts. With the packing, a
# low-memory start either failed ("mkl_malloc") or crashed the whole process with an access violation, no log line
# (2026-11-08: voice Karen had vanished overnight).
os.environ.setdefault("CT2_PACKED_GEMM", "0")
load_dotenv()
os.environ["PATH"] = os.path.abspath("tools") + os.pathsep + os.environ.get("PATH", "")  # bundled uv/uvx (Google tools)
os.environ.setdefault("UV_PYTHON_INSTALL_DIR", os.path.abspath(".python"))  # the Python SETUP.bat installed, so uvx starts offline
os.makedirs("logs", exist_ok=True)
LOG = open("logs/karen.log", "a", encoding="utf-8", buffering=1)
if sys.stdout is None:  # started with pythonw (Windows autostart): no console, so keep the output in a file
    sys.stdout = sys.stderr = open("logs/console.log", "a", encoding="utf-8", buffering=1)
SETTINGS = "settings.json"  # choices made in the interface; they override config.yaml
GREETING = re.compile(r"^(hi|hello|hey|dear|good (morning|afternoon|evening))\b.{0,40},?$", re.I)
REFERS_BACK = re.compile(r"\b(it|that|this|them|those|these|him|her|one|first|second|third|last|latest|reply|again)\b", re.I)
YES = re.compile(r"^\W*(yes|yeah|yep|yup|sure|ok|okay|do it|go ahead|send it|confirm|correct|absolutely|please do)\b", re.I)


SHOW = re.compile(r"\[\[show:\s*(.+?)\]\]")  # a tool asks Karen to open something in her viewer
STUDY = re.compile(r"\[\[study:\s*(.+?)\]\]")  # a study tool wrote data/study/<visual|lesson>.json
# "stop everything": the AI had only *said* "all tasks have been stopped" while meeting notes kept recording (2026-11-03)
STOP_ALL = re.compile(r"^\W*(karen\W+)?(please\W+)?(stop|end|cancel|turn off)\W+(every\s*thing|it all|all( of it| that)?)"
                      r"(\W+(please|now|karen))*\W*$", re.I)
LOCK = re.compile(r"^\W*(karen\W+)?(lock (yourself|karen|up)|lock\W*$|go to lock)", re.I)  # face unlock: lock now
# "repeat that" / "say it again" / "what did you say" / "pardon": her last answer again, no AI call
REPEAT = re.compile(r"^\W*(karen\W+)?(can you |could you |please )*(repeat( that| it| please| again| yourself)?( please)?"
                    r"|say (that|it) again( please)?|what did you (just )?say|come again|pardon( me)?|i didn'?t (get|catch) that"
                    r")\W*$", re.I)
# "say it slower" / "slower please" / "repeat that slowly": the same, at 80% speed
SLOWER = re.compile(r"^\W*(karen\W+)?(can you |could you |please )*((say|repeat) (that|it)( again)? (more )?slow(er|ly)"
                    r"|(more )?slow(er|ly)( please)?|say (that|it) (again )?(more )?slowly)\W*$", re.I)
# right after the wake word, Whisper's take on his bare "Karen" (logs 2026-10-27/28): a wake, not a request
BARE = re.compile(r"^\W*(karen|karan|got (him|it)|get (him|it|in)|gavin|garham)\W*$", re.I)
MAP = re.compile(r"\[\[map:\s*([^|\]]+)\|([^|\]]*)\|([^\]]*)\]\]")  # show_map: embed url|title|link
SAY = re.compile(r"\[\[say:\s*([^|\]]+)\|([^|\]]+)\|(.+?)\]\]", re.S)  # a translation to speak: lang|voice|text
# "that wasn't for you": the Cortex learns the last request wasn't meant for her (no AI call)
NOT_FOR_ME = re.compile(r"^\W*(karen\W+)?(no\W+)?(that|this|it) (was ?n'?t|was not|is ?n'?t|is not) (meant )?for you"
                        r"|^\W*(karen\W+)?i (was ?n'?t|was not|am not|wasn't) talking to you", re.I)
VIEWABLE = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".txt", ".md", ".csv", ".json", ".py",
            ".js", ".html", ".htm", ".xml", ".log", ".yaml", ".yml", ".mp4", ".mp3", ".m4a", ".wav", ".ino", ".c", ".cpp"}


class NeedsSignIn(Exception):
    """A Google tool opened the sign-in page in the browser."""


def log(tag, msg=""):
    line = f"{tag:<6} {msg}"
    print(f"[{time.strftime('%H:%M:%S')}] {line}", flush=True)
    LOG.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {line}\n")


def tail_trace(exc=None, lines=8):
    """The last lines of a traceback (the current exception's by default), indented for the log."""
    import traceback
    text = "".join(traceback.format_exception(*exc) if exc else traceback.format_exc())
    return "\n".join("       " + s for s in text.rstrip().splitlines()[-lines:])


def crash_logging():
    """Until 2026-11-08 the log never said when or why Karen stopped: a closed window, a Python error in a thread
    and a hard crash all looked the same. Now uncaught errors go to karen.log and hard crashes to logs/crash.log."""
    import faulthandler
    try:
        faulthandler.enable(open("logs/crash.log", "a", encoding="utf-8"))  # native crashes (onnx, MKL, COM)
    except OSError:
        pass
    sys.excepthook = lambda *exc: log("error", f"uncaught: {exc[1]!r}\n{tail_trace(exc)}")
    threading.excepthook = lambda a: log("error", f"thread {a.thread.name if a.thread else '?'}: {a.exc_value!r}\n"
                                                  f"{tail_trace((a.exc_type, a.exc_value, a.exc_traceback))}")


class Karen:
    """Wires mic -> wake word -> speech-to-text -> brain -> voice, and keeps the interface informed."""

    def __init__(self, cfg, settings):
        from core.ui import NullUI
        self.cfg, self.settings = cfg, settings
        self.ui = NullUI()
        self.controls = None  # the page's API (set when there is a window); the study window shares it
        self.actions = queue.Queue()          # from the interface: ("talk",) ("text", str) ("mute", bool)
        self.confirm_answers = queue.Queue()  # YES/NO clicks, for the confirmation gate (Phase 2)
        self.muted = False
        self.mic = self.speaker = self.brain = self.tools = None
        self.sticky = set()  # tool groups used in the last answer, offered again for follow-ups
        self.used = set()  # tool groups used while answering (run_tool adds to it)
        self.asked = False   # her last answer was a question
        self.awaiting = False  # Karen's last answer was a question
        self.said_on_it = 0.0  # when "On it." was last said before a slow tool
        self.barged = False    # he said "Karen" while she was talking
        from core.audio import Ducker
        self.ducker = Ducker(1)  # replaced in load() once the mic exists
        self.started = time.time()
        self.face, self.locked, self.face_seen = None, False, 0.0  # face unlock (Settings), see face_gate()
        self.phonewatch = None  # announces phone calls and messages (voice mode only)
        self.attachments = []  # files added with the + button / drag and drop (paths in data/uploads)
        self.cortex = None          # the local brain-inspired network (core/cortex)
        self.idle = False           # waiting for the wake word: the only time the Cortex's ear listens
        self.request_audio = None   # the audio of the request being answered, for the Cortex
        self.request_how = ("wake", 0.0)  # how it arrived: wake (+ score) | followup | button
        self.last_reply_t = None    # when her last answer ended

    # ---------------------------------------------------------------- setup
    def load(self, listen=True):
        from core.brain import Brain, private_backends
        from core.tts import Speaker
        c, st = self.cfg, self.settings
        self.ui.call("state", "sleeping", "LOADING")
        log("load", "voice and brain...")
        self.speaker = Speaker(c["tts"]["model"], c["tts"]["voices"], st.get("voice", c["tts"]["voice"]),
                               st.get("speed", c["tts"]["speed"]), device=c["audio"]["output_device"])
        self.speaker.on_start = lambda: self.ui.call("state", "speaking")
        backends = sorted(c["llm"]["backends"], key=lambda b: b["name"] != st.get("brain"))  # chosen one first
        self.brain = Brain(backends, c["llm"]["system_prompt"], c["llm"]["max_tokens"], c["llm"]["history_turns"],
                           private_backends(c["llm"]), c["llm"].get("max_tool_rounds", 5))
        night = c.get("night", {})  # night mode: quieter voice and short answers inside night.window
        self.speaker.volume = lambda: night.get("volume", 0.5) if quiet_now(night.get("window", "")) else 1.0
        from core import study
        self.brain.extra = lambda: ((" It's late at night and he may be winding down: answer in one or two short "
                                     "sentences unless he asks for more." if quiet_now(night.get("window", "")) else "")
                                    + (study.PROMPT if study.is_on() else ""))
        self.brain.persona = lambda: "" if study.is_on() else Brain.persona()  # Mam is a teacher: mode paused
        self.last_reply = ""  # for "repeat that" / "say it slower"
        from core.tools import Tools
        self.tools = Tools(c.get("mcp_servers"), c.get("confirm"), log, ask=self.confirm)  # servers can ask mid-task
        self.tools.on_change = lambda: self.ui.call("abilities", self.abilities())
        from core.dots import Runner
        self.dots = Runner(c, self.tools, log, announce=self.dot_done, confirm=self.dot_confirm)
        if listen:
            from core.audio import Mic
            from core.stt import STT
            log("load", "speech models...")
            a = c["audio"]
            self.mic = Mic("models/silero_vad.onnx", a["input_device"], a["vad_threshold"],
                           a["silence_ms"], max_utterance_s=a["max_utterance_s"])
            from core.audio import Ducker
            self.ducker = Ducker(a.get("duck_level", 0.15))
            n = Ducker.recover()  # a previous run was killed while other apps were turned down: put them back
            if n:
                log("audio", f"restored the volume of {n} program(s) left turned down by an earlier run")
            self.stt = STT(c["stt"]["model"], c["stt"]["compute_type"], threads=c["stt"]["threads"],
                           cloud=c["stt"].get("cloud"))
            self.wake, self.whisper_wake = self.load_wake()
            pw = c.get("phone_watch") or {}
            if pw.get("enabled", True):  # incoming calls and WhatsApp/SMS, said aloud (core/phonewatch.py)
                from core.phonewatch import PhoneWatch
                self.phonewatch = PhoneWatch(pw, self.phone_event, log, (c.get("phone") or {}).get("device", ""),
                                             (c.get("phone") or {}).get("second", "")).start()
            if (c.get("cortex") or {}).get("enabled"):
                threading.Thread(target=self.load_cortex, daemon=True).start()  # ~2 s; she can answer meanwhile
        self.speaker.synth("Yes?")  # warm up and cache
        self.load_face(st.get("face_lock", False))
        if study.is_on():  # study mode survives a restart (its window waits for the page, so not on this thread)
            threading.Thread(target=self.set_study, args=(True, True), daemon=True).start()
        self.push_config()
        self.ui.call("abilities", self.abilities())
        self.ui.call("muted", False)
        self.ui.call("memory", 0)

    def load_wake(self):
        from core.stt import STT
        from core.wake import OwwWake, WhisperWake
        w = self.cfg["wake"]
        # only its split() (find "Karen" in a transcript) is used next to the personal / openWakeWord detectors, so
        # its Whisper model is loaded only when it IS the detector (it was loaded every start, never used)
        whisper_wake = WhisperWake(None, w["words"])
        threshold = self.settings.get("wake_threshold")
        if w["engine"] == "personal":
            if os.path.exists(w["personal_model"]):
                from core.wake import PersonalWake
                log("wake", f"personal wake word {w['personal_model']}")
                return self.gated(PersonalWake(w["personal_model"], threshold, patience=w.get("patience"))), whisper_wake
            log("wake", f"{w['personal_model']} not found (run scripts/train_wake.py), using Whisper wake word")
        if w["engine"] == "oww":
            if os.path.exists(w["oww_model"]):
                verifier = w["verifier"] if os.path.exists(w["verifier"] or "") else None
                log("wake", f"openWakeWord {w['oww_model']}" + (" + voice verifier" if verifier else ""))
                return OwwWake(w["oww_model"], threshold or w["threshold"], verifier, w["verifier_threshold"]), whisper_wake
            log("wake", f"{w['oww_model']} not found, using Whisper wake word")
        whisper_wake.stt = STT(w["model"], self.cfg["stt"]["compute_type"], threads=2)
        return whisper_wake, whisper_wake

    def gated(self, wake):
        """Only his voice wakes her (config wake.voice_gate, 0 = off): see core.wake.VoiceGate."""
        level = self.cfg["wake"].get("voice_gate", 0)
        if level:
            from core.wake import VoiceGate
            wake.gate = VoiceGate(level, shared=lambda: getattr(getattr(self, "cortex", None), "voice", None), log=log)
            if wake.gate.me is None:
                wake.gate = None
            else:
                log("wake", f"voice check on (only your voice, {level})")
        return wake

    def load_cortex(self):
        if not os.path.exists("models/cortex/voice.onnx"):
            log("cortex", "models/cortex missing (run scripts/download_models.py), Cortex off")
            return
        try:
            from core.cortex import Cortex
            cortex = Cortex(self.cfg["cortex"], log)
            cortex.start_ear(self.mic, lambda: self.idle and not self.muted)
            self.cortex = cortex
        except Exception as e:
            log("cortex", f"couldn't start: {e!r}")

    def attend(self, text, audio, spoken):
        """The Cortex's attention on a request. Returns False when Karen should stay quiet (attention "on" and not
        for her); in shadow mode it only shows and stores its verdict."""
        cx = self.cortex
        if NOT_FOR_ME.search(text):
            threading.Thread(target=cx.not_for_me, daemon=True).start()
            log("cortex", "labelled the last request: not for Karen")
            self.ui.call("message", "karen", "Sorry. I'll learn from that.", "cortex")
            self.speaker.say("Sorry. I'll learn from that.")
            self.ui.call("done")
            return False
        how, score = self.request_how
        self.request_how = ("wake", 0.0)
        verdict = None
        if spoken:
            try:
                verdict = cx.attend(text, how, score, self.last_reply, self.last_reply_t)
            except Exception as e:
                log("cortex", f"attention failed: {e!r}")
        cx.request(text, audio, **({"attention": verdict} if verdict else {"how": how if spoken else "typed"}))
        if not verdict:
            return True
        quiet = verdict["verdict"] == "ignore" and how != "button"  # the talk button is always for her
        log("cortex", f"{'would ignore' if quiet else 'for me'} p={verdict['p']:.2f} (threshold "
                      f"{verdict['threshold']:.2f}, {how}{'' if cx.mode == 'on' else ', shadow'})")
        self.ui.call("cortex", dict(verdict, mode=cx.mode, acted=quiet and cx.mode == "on", heard_total=cx.heard_count))
        if quiet and cx.mode == "on":
            self.ui.call("state", "sleeping", "NOT FOR ME")
            return False
        return True

    def load_face(self, on):
        """Face unlock on: she starts locked and answers once she has seen the user's face."""
        from core.face import FaceLock
        self.face = self.locked = None
        if on and FaceLock.ready():
            f = self.cfg.get("face", {})
            self.face = FaceLock((self.cfg.get("explore") or {}).get("camera", 0), f.get("threshold", 0.42))
            self.locked, self.face_seen = True, 0.0
            log("face", "face unlock on: locked until she sees you")
        return bool(self.face)

    def face_gate(self, text):
        """While locked (start-up, "lock yourself", or relock_min idle), check the webcam for his face first.
        True = handled here, don't answer."""
        if not self.face:
            return False
        if LOCK.search(text):
            self.locked = True
            self.ui.call("message", "karen", "Locked. I'll answer once I see your face.", "face unlock")
            self.speaker.say("Locked.")
            self.ui.call("done")
            return True
        f = self.cfg.get("face", {})
        if self.locked or time.time() - self.face_seen > f.get("relock_min", 30) * 60:
            self.ui.call("state", "thinking", "FACE CHECK")
            t = time.time()
            ok, score, why = self.face.check(f.get("check_s", 8))
            log("face", f"{why}, score {score:.2f}, {time.time() - t:.1f}s")
            if not ok:
                self.locked = True
                if why == "not him" and f.get("intruder_alert", True) and self.face.stranger is not None \
                        and time.time() - getattr(self, "last_intruder", 0) > 300:  # one alert per 5 min
                    self.last_intruder = time.time()
                    from core.face import intruder_alert
                    threading.Thread(target=intruder_alert, daemon=True,
                                     args=(self.face.stranger[0], self.face.stranger[1], text, log)).start()
                msg = {"camera": "I can't use the camera right now, so I can't unlock.",
                       "no face": "I'm locked. Look at the camera so I can see it's you.",
                       }.get(why, "Sorry, I'm locked. I only answer my owner.")
                self.ui.call("message", "karen", msg, "face unlock")
                self.speaker.say(msg)
                self.ui.call("done")
                return True
            self.locked = False
        self.face_seen = time.time()
        return False

    def push_config(self):
        from core.wake import OwwWake, PersonalWake
        wake = getattr(self, "wake", None)
        engine = "personal" if isinstance(wake, PersonalWake) else "openwakeword" if isinstance(wake, OwwWake) else "whisper"
        self.ui.call("config", {
            "voice": self.speaker.voice, "speed": self.speaker.speed,
            "backends": [{"name": b["name"], "model": b["model"]} for b in self.brain.backends],
            "wake_engine": engine,
            "wake_threshold": wake.threshold if engine != "whisper" else None,
            "sounds": self.settings.get("sounds", True),
            "avatar": self.settings.get("avatar"),
            "mode": persona_mode(),
            "theme": self.settings.get("theme", "aurora"),
            "face_lock": bool(self.face), "face_ready": face_ready(),
        })

    def abilities(self):
        notes = {"on": "connected", "starting": "starting...", "error": "failed, see log", "setup": "needs .env keys"}
        servers = [{"name": spec.get("label", n), "short": spec.get("short"),
                    "status": "on" if self.tools.status.get(n) == "on" else "",
                    "note": notes.get(self.tools.status.get(n), "")}
                   for n, spec in (self.cfg.get("mcp_servers") or {}).items()]
        planned = [dict(p, status="planned") for p in (self.cfg.get("ui") or {}).get("planned") or []]
        return [{"name": "Conversation", "short": "Chat", "status": "on", "note": self.brain.backends[0]["name"]}] \
            + servers + planned

    # ---------------------------------------------------------------- main loop
    def run(self):
        from core.audio import Interrupted
        try:
            self.load()
        except Exception as e:
            log("error", repr(e))
            self.ui.call("state", "error", "STARTUP FAILED")
            self.ui.call("message", "error", f"Karen couldn't start: {e}")
            return
        threading.Thread(target=self.ticker, daemon=True).start()
        self.speaker.say("I'm ready.")  # not her own name: the mic would hear it and wake her
        log("ready", 'say "Karen" (Ctrl+C quits)')
        failures = []
        while True:
            try:
                self.mic.interrupt.clear()
                time.sleep(0.3)  # let the speaker's echo of Karen's last words die out before listening again
                self.mic.flush()
                if self.morning_due():
                    self.ducker.duck()
                    try:
                        self.answer(self.cfg["morning"]["request"], meta="morning briefing")
                    finally:
                        self.ducker.restore()
                elif not self.actions.empty():
                    self.do(*self.actions.get_nowait())
                elif self.muted:
                    self.ui.call("state", "sleeping", "MIC MUTED")
                    self.do(*self.actions.get())
                else:
                    self.ui.call("state", "sleeping")
                    self.idle = True
                    try:
                        audio = self.wake.wait(self.mic, log)
                    finally:
                        self.idle = False
                    self.ducker.duck()  # turn music down while we talk
                    try:
                        self.on_wake(audio)
                        self.follow_up()
                    finally:
                        self.ducker.restore()
            except Interrupted:
                pass  # an interface action is waiting in self.actions
            except Exception as e:  # anything else used to end this thread: window open, Karen deaf, nothing logged
                failures = [t for t in failures if time.time() - t < 60] + [time.time()]
                log("error", f"voice loop: {e!r}\n{tail_trace()}")
                self.ui.call("toast", "error", f"Something went wrong ({type(e).__name__}); still listening")
                time.sleep(min(30, 2 ** len(failures)))  # back off if it keeps failing (no busy loop)

    def do(self, kind, value=None):
        if kind in ("talk", "text"):
            self.ducker.duck()
            try:
                self.listen_and_answer(say_yes=False, how=("button", 0.0)) if kind == "talk" else self.answer(value)
                self.follow_up()
            finally:
                self.ducker.restore()
        elif kind == "announce":
            log("remind", value)
            self.ui.call("toast", "warn", value)
            self.ui.call("message", "karen", value, "reminder")
            self.ducker.duck()
            try:
                self.speaker.say(value)
            finally:
                self.ducker.restore()
        elif kind == "ring":  # the phone rings: say who, then listen for "pick up" / "reject" without the wake word
            if not self.phonewatch.ringing:
                return  # already answered or stopped while Karen was busy
            text = "Your phone is ringing." if self.locked else value
            log("phone", text)
            self.ui.call("toast", "warn", text)
            self.ui.call("message", "karen", text + " Pick up or reject?", "phone")
            self.ducker.duck()
            try:
                self.speaker.say(text + " Pick up or reject?")
                self.awaiting = self.mic is not None
                self.follow_up()
            finally:
                self.ducker.restore()
        elif kind == "message":  # a new WhatsApp / SMS on the phone
            app, sender, body = value
            if self.locked:  # face unlock: don't read his messages to whoever is in the room
                text = f"New {app} on your phone."
            else:
                text = f"{app.capitalize() if app != 'WhatsApp' else app} from {sender}: {body}"
                self.brain.turns.append([{"role": "user", "content": "(my phone showed a new message)"},
                                         {"role": "assistant", "content": text}])  # so "reply to that" works
                self.sticky = {self.tools.group_of(t) for t in ("phone__phone_send_sms", "phone__phone_send_whatsapp")
                               if t in self.tools.tools}
            log("phone", text)
            self.ui.call("message", "karen", text, "phone")
            if not quiet_now(self.cfg.get("phone_watch", {}).get("quiet", "")):
                self.ducker.duck()
                try:
                    self.speaker.say(text)
                finally:
                    self.ducker.restore()
        elif kind == "approve":  # a Dot working in the background needs a yes/no before a risky tool
            dot, tool, args, reply = value
            reply.put(self.confirm(f"{dot} asks: {describe(tool, args)}"))
        elif kind == "study":  # the Study / Exit study buttons
            from core import study
            if study.is_on() != value:
                text = self.set_study(value)
                self.push_config()
                self.ui.call("message", "karen", text, "mode switch")
                self.speaker.say(text)
                self.ui.call("done")
        elif kind == "mute":
            self.muted = value
            self.ui.call("muted", value)
            log("mute", "microphone muted" if value else "microphone on")

    def phone_event(self, kind, value):
        """PhoneWatch: a call or message shows in the island at once (her spoken line waits its turn)."""
        island = getattr(self.ui, "island", None)
        if island:
            island.ringing = lambda: bool(self.phonewatch and self.phonewatch.ringing)
            if kind == "ring":
                island.phone_call("Your phone" if self.locked else value.rsplit(" is calling", 1)[0])
            elif kind == "message":
                app, sender, body = value
                if self.locked:  # face unlock: no names or text for whoever is in the room
                    island.phone_message(app, f"New {app}", "On your phone")
                else:
                    island.phone_message(app, sender, body)
        self.action(kind, value)

    def action(self, kind, value=None):
        """Called from the interface thread: queue it and break the loop out of listening."""
        if kind == "talk" and self.speaker:
            self.speaker.stop_event.set()  # talking over Karen cuts her off
        self.actions.put((kind, value))
        if self.mic:
            self.mic.interrupt.set()

    def on_wake(self, audio):
        w = self.cfg["wake"]
        self.request_how = ("wake", getattr(self.wake, "last_score", 1.0))
        if audio is not None:  # Whisper wake: the request may be in the same utterance
            self.ui.call("state", "thinking")
            t = time.time()
            request = self.whisper_wake.split(self.stt.transcribe(audio))[1]
        else:  # openWakeWord: give him a moment to keep talking ("Karen, open Chrome")
            self.ui.call("state", "listening")
            self.mic.vad.reset()
            audio = self.mic.listen(timeout=w["continue_s"])
            t = time.time()
            request = ""
            if audio is not None:
                self.ui.call("state", "thinking")
                request = self.stt.transcribe(audio)
                if BARE.match(request):  # he said "Karen" again: chime and listen instead of "could you clarify?"
                    log("wake", f"bare wake ({request.strip()})")
                    request = ""
        if request:
            self.request_audio = audio
            self.answer(request, time.time() - t)
        elif not self.listen_and_answer(say_yes=True):
            self.save_false_wake()

    def save_false_wake(self):
        """Keep the sound that woke her when nothing followed, so scripts/train_wake.py can learn to ignore it."""
        audio = getattr(self.wake, "last_audio", None)
        if audio is None:
            return
        import wave
        import numpy as np
        os.makedirs("voice_tests/false_wakes", exist_ok=True)
        with wave.open(f"voice_tests/false_wakes/{datetime.datetime.now():%Y%m%d-%H%M%S}.wav", "wb") as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(16000)
            f.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())

    def follow_up(self):
        """Karen asked a question: keep listening a few seconds so "yes" works without saying "Karen" again."""
        while self.awaiting and not self.muted:
            self.awaiting = False
            self.listen_and_answer(say_yes=False, timeout=self.cfg["wake"].get("followup_s", 6), how=("followup", 0.0))

    def listen_and_answer(self, say_yes, timeout=None, how=None):
        if how:
            self.request_how = how
        if say_yes:  # a soft chime, not a spoken "Yes?": false wakes then don't talk over his day
            if self.settings.get("sounds", True):
                self.speaker.chime()
        self.mic.flush()
        self.ui.call("state", "listening")
        audio = self.mic.listen(timeout=timeout or self.cfg["wake"]["command_timeout_s"])
        if audio is None:
            log("idle", "no request, back to sleep")
            return False
        self.ui.call("state", "thinking")
        t = time.time()
        request = self.stt.transcribe(audio)
        if request:
            self.request_audio = audio
            self.answer(request, time.time() - t)
        return bool(request)

    def morning_due(self, path="data/morning.json"):
        """Once a day, the first time she's with him in the morning window (start-up, or right after his first
        request), she gives the briefing unasked. Not while face-locked: it reads his mail and calendar."""
        m = self.cfg.get("morning") or {}
        now = datetime.datetime.now()
        if not m.get("enabled") or self.locked or not quiet_now(m.get("window", "05:00-11:30")):
            return False
        try:
            last = json.load(open(path, encoding="utf-8")).get("last")
        except (OSError, ValueError):
            last = None
        if last == now.strftime("%Y-%m-%d"):
            return False
        json.dump({"last": now.strftime("%Y-%m-%d")}, open(path, "w", encoding="utf-8"))
        log("morning", "giving the morning briefing")
        return True

    def answer(self, text, stt_s=None, meta=None):
        self.awaiting = False
        meta = meta or (f"speech-to-text {self.stt.used} {stt_s:.1f}s" if stt_s else "typed")
        log("you", f"{text}  ({meta})")
        self.ui.call("message", "you", text, meta)
        audio, self.request_audio = self.request_audio, None
        if self.cortex and meta != "morning briefing" and not self.attend(text, audio, stt_s is not None):
            return
        if self.face_gate(text):
            return
        again = REPEAT.match(text) or SLOWER.match(text)
        if again and self.last_reply:  # "repeat that" / "say it slower": her last answer again, no AI call
            speed = self.speaker.speed
            if SLOWER.match(text):
                self.speaker.speed = max(0.6, speed * 0.8)
            log("karen", f"(again{', slower' if SLOWER.match(text) else ''}) {self.last_reply[:80]}")
            self.ui.call("state", "thinking", "AGAIN")
            try:
                self.speaker.say(self.last_reply)
            finally:
                self.speaker.speed = speed
            self.ui.call("done")
            return
        from core import persona
        switched = self.phonewatch and self.phonewatch.command(text)  # "pick up" / "reject" while it rings
        if not switched:
            from core import study
            on = study.command(text)  # "study mode" / "study mode off": colours, wake word and study window, no LLM
            switched = self.set_study(on) if on is not None else None
        if not switched:
            switched = self.meeting_command(text)  # "start / stop taking meeting notes": at once, no LLM
        if not switched:
            switched = self.stop_all(text)  # "stop everything": meeting notes, recordings, music
        switched = switched or persona.command(text)  # "work mode" / "love mode" / "both mode": at once, no LLM
        if not switched:
            from core import dots
            asked = dots.command(text)  # "ask the researcher to ...": a Dot works on it in the background
            if asked:
                dot, task = asked
                self.dots.start(dot, task)
                switched = f"{dot['name']}'s on it. I'll tell you when it's done."
                self.ui.call("dots", self.dot_status())
        if switched:
            log("karen", f"{switched}  (mode switch)")
            self.push_config()
            self.ui.call("message", "karen", switched, "mode switch")
            self.speaker.say(switched)
            self.ui.call("done")
            return
        self.ui.call("state", "thinking", self.brain.backends[0]["name"].upper())
        sticky = set(self.sticky) if REFERS_BACK.search(text) or self.asked else set()  # follow-ups keep the tools  # "reply to that" keeps the last tools
        if self.attachments:  # files he added: tell the brain, and offer the file tools
            names = ", ".join(os.path.basename(a) for a in self.attachments)
            text = f"{text}\n[Attached files in data/uploads: {names}]"
            sticky |= {("files", g) for g in {t["group"] for t in self.tools.tools.values() if t["server"] == "files"}}
            if any(a.lower().endswith((".csv", ".tsv", ".xlsx", ".xlsm", ".json")) for a in self.attachments):
                sticky.add(("friday", "data_*|convert_data"))  # tables: Friday's data tools too
        from core import study
        if study.is_on() and self.tools:  # study mode: Mam can always draw, teach and play videos
            sticky |= {("study", t["group"]) for t in self.tools.tools.values() if t["server"] == "study"}
        tools = self.tools.select(text, sticky) if self.tools else []
        self.used, offered = set(), self.tools.groups_of(tools) if self.tools else set()
        t = time.time()
        try:
            if tools:
                self.ui.call("state", "thinking", "TOOLS")
            answer_text = self.brain.ask(text, tools, self.run_tool)  # tool calls (and their confirms) happen here
            stop_watch = self.watch_for_barge_in()
            try:
                reply = self.speaker.speak_stream(answer_text)
            finally:
                stop_watch.set()
        except NeedsSignIn:
            log("tools", "Google sign-in opened in the browser")
            self.ui.call("message", "system", "Google sign-in opened in your browser. Sign in, then ask again.")
            self.speaker.say("I've opened Google sign-in in your browser. Sign in, then ask me again.")
            return
        except Exception as e:
            log("error", repr(e))
            self.ui.call("state", "error")
            self.ui.call("message", "error", f"Couldn't reach the brain: {type(e).__name__}")
            self.speaker.say("Sorry, I couldn't reach my brain. Check the internet or the API keys.")
            return
        b = self.brain.last
        voice = self.speaker.first_audio - t if self.speaker.first_audio else None
        meta = f"{b['failed']}first word {b['backend']} {b['first']:.1f}s" + (f", voice starts {voice:.1f}s" if voice else "")
        log("karen", f"{reply.strip()}  ({meta})")
        self.last_reply, self.last_reply_t = reply.strip(), time.time()
        self.ui.call("message", "karen", reply.strip(), meta)
        self.ui.call("timing", {"stt": stt_s, "llm": b["first"], "voice": voice,
                                "backend": b["backend"], "fallback": bool(b["failed"])})
        self.ui.call("memory", len(self.brain.turns))
        self.awaiting = reply.strip().endswith("?") and self.mic is not None  # she asked something: listen for it
        self.asked = reply.strip().endswith("?")  # her question: his answer gets the same tools ("like 2 weeks")
        # ...and the tools her question itself is about ("Should I add a reminder to your calendar?" asked in plain
        # chat): his "yes" / "mm" then goes to the tool path instead of a chat answer that only claims it was done
        self.sticky = self.used | ((offered | self.tools.groups_for(reply)) if self.asked and self.tools else set())
        from core.brain import claims_action
        if not tools and not self.used and claims_action(reply) and not self.speaker.stop_event.is_set():
            fix = self.brain.correct_claim()  # no tool ran, so whatever she says she did didn't happen
            log("karen", f"{fix}  (she claimed an action with no tool)")
            self.ui.call("message", "karen", fix, "correction")
            self.speaker.say(fix)
        if not self.speaker.stop_event.is_set():
            self.ui.call("done")
        if self.barged:  # he said "Karen" while she was talking: she stopped, now listen to him
            self.barged = False
            self.awaiting = False
            self.listen_and_answer(say_yes=False, how=("wake", 1.0))

    def watch_for_barge_in(self):
        """While Karen speaks, listen for the wake word; if heard, cut her off. Returns the event that ends the
        watch. Only streaming wake engines (personal / openWakeWord) can do this."""
        stop = threading.Event()
        self.barged = False
        if not (self.mic and hasattr(self.wake, "heard_during")):
            return stop
        self.mic.flush()

        def watch():
            try:
                if self.wake.heard_during(self.mic, stop):
                    self.barged = True
                    self.speaker.stop_event.set()
                    log("barge", "stopped talking: wake word heard")
            except Exception:
                pass  # interrupted by an interface action, or the mic closed
        threading.Thread(target=watch, daemon=True).start()
        return stop

    # ---------------------------------------------------------------- viewer and attachments
    def show(self, target):
        """Open a URL or file in the viewer window (files a browser can't show open in their own app)."""
        if target.startswith(("http://", "https://")):
            url = target
        elif os.path.exists(target):
            if os.path.splitext(target)[1].lower() not in VIEWABLE:
                os.startfile(target)  # Word, Excel, PowerPoint...
                log("show", f"opened {target}")
                return
            import pathlib
            url = pathlib.Path(target).resolve().as_uri()
        else:
            return
        log("show", url)
        self.ui.show(url)

    # ---------------------------------------------------------------- meeting notes (servers/meeting)
    def meeting_command(self, text):
        """"Start / stop taking meeting notes" calls the meeting tool directly. Returns what she says, or None."""
        from core import meeting
        what = meeting.command(text)
        name = f"meeting__{what}_meeting_notes"
        if not what or not self.tools or name not in self.tools.tools:
            return None  # not a meeting phrase, or the server isn't up yet: the LLM path waits for it
        result = self.run_tool(name, {})
        return result.split("\n", 1)[0].strip()

    def stop_all(self, text):
        """"Stop everything": ends meeting notes and recordings and pauses the music, with the real tools. Returns
        what she says, or None when it isn't that phrase."""
        if not STOP_ALL.match(text) or not self.tools:
            return None
        stopped = []

        def running(path):
            try:
                with open(path, encoding="utf-8") as f:
                    st = json.load(f)
            except (OSError, ValueError):
                return False
            return bool(st.get("on")) if "on" in st else bool(st)

        if running("data/meeting.json") and "meeting__stop_meeting_notes" in self.tools.tools:
            self.run_tool("meeting__stop_meeting_notes", {})
            stopped.append("the meeting notes (they're open in the viewer)")
        if running("data/recording.json") and "jarvis__stop_recording" in self.tools.tools:
            if self.run_tool("jarvis__stop_recording", {"kind": "all"}).startswith("Saved"):
                stopped.append("the recording")
        if "media__music" in self.tools.tools and self.tools.call("media__music", {"action": "pause"}).startswith("Paused"):
            stopped.append("the music")
        if not stopped:
            return "Nothing was running: no meeting notes, recording or music."
        return "Stopped " + (", ".join(stopped[:-1]) + " and " + stopped[-1] if len(stopped) > 1 else stopped[0]) + "."

    def push_meeting(self):
        """The red "meeting notes" pill in the window's header (data/meeting.json, written by the server)."""
        try:
            with open("data/meeting.json", encoding="utf-8") as f:
                st = json.load(f)
        except (OSError, ValueError):
            st = {}
        self.ui.call("meeting", {"on": bool(st.get("on")), "since": st.get("since"), "source": st.get("source", "")})

    # ---------------------------------------------------------------- study mode (core/study.py)
    def set_study(self, on, quiet=False):
        """Study mode on/off: black and yellow, called "Mam" (her own wake model once trained), the study window.
        Returns what she says."""
        from core import study
        study.set_on(on)
        w = self.cfg["wake"]
        model = w.get("study_model", "")
        if on and self.mic and os.path.exists(model) and not getattr(self, "karen_wake", None):
            from core.wake import PersonalWake
            self.karen_wake, self.wake = self.wake, self.gated(PersonalWake(model, None, patience=w.get("patience")))
            log("wake", f"study mode: wake word {model}")
        elif not on and getattr(self, "karen_wake", None):
            self.wake, self.karen_wake = self.karen_wake, None
        self.ui.call("study", on)
        self.ui.study_window(on, self.controls)
        log("study", "on" if on else "off")
        if quiet:
            return None
        if not on:
            return "Study mode off. Karen's back."
        if getattr(self, "karen_wake", None):
            return "Study mode on. I'm Mam now. Say Hey Mam when you need me."
        return "Study mode on. I'm Mam now. Until you train Hey Mam, you can still call me Karen."

    def study_show(self, what):
        """Push data/study/<what>.json (visual or lesson) to the study window, opening study mode if needed."""
        from core import study
        try:
            with open(os.path.join(study.DIR, f"{what}.json"), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        if not study.is_on():
            self.set_study(True, quiet=True)
            time.sleep(1.5)  # let the window load before the first call
        self.ui.study_call(what, data)

    def add_attachments(self, paths):
        import shutil
        os.makedirs("data/uploads", exist_ok=True)
        for src in paths:
            dst = os.path.join("data", "uploads", os.path.basename(src))
            if os.path.abspath(src) != os.path.abspath(dst):
                shutil.copy2(src, dst)
            dst = os.path.abspath(dst)
            if dst not in self.attachments:
                self.attachments.append(dst)
        self.push_attachments()

    def push_attachments(self):
        self.ui.call("attachments", [{"name": os.path.basename(a), "size": os.path.getsize(a)}
                                     for a in self.attachments if os.path.exists(a)])

    # ---------------------------------------------------------------- tools
    def run_tool(self, name, args):
        """Called by the brain's tool loop. Asks first for anything that sends, deletes or changes events."""
        short = name.split("__", 1)[-1]
        self.ui.call("message", "system", short.replace("_", " "), json.dumps(args, ensure_ascii=False)[:140])
        self.ui.call("intensity", 0.85)
        if self.tools.needs_confirm(name) and not self.confirm(describe(short, args)):
            log("tool", f"{short} cancelled by the user")
            return "The user said no, so nothing was done. Don't retry; just tell him it was cancelled."
        t = time.time()
        if short == "visualize" and (args.get("style") in ("explainer", "interactive", "simulation", "animation")
                                     or args.get("detail") in ("detailed", "deep")) and self.mic:
            self.speaker.say("Drawing it. Detailed ones take about a minute.")  # Claude's explainers: ~1 minute
        elif short in SLOW_TOOLS and self.mic and t - self.said_on_it > 30:
            self.said_on_it = t
            self.speaker.say(SLOW_SAY.get(short, "On it."))  # these take a while; don't leave him in silence
        result = self.tools.call(name, args)
        log("tool", f"{short} {json.dumps(args, ensure_ascii=False)[:200]} -> {len(result)} chars, {time.time() - t:.1f}s")
        if short in ("set_mode", "set_persona", "clear_persona"):
            self.push_config()  # the Settings menu and the avatar follow a mode changed by voice
        if short in ("start_meeting_notes", "stop_meeting_notes"):
            self.push_meeting()
        if "ACTION REQUIRED" in result and "Authorization URL" in result:
            raise NeedsSignIn()
        for what in STUDY.findall(result):  # a study tool made a visual or a lesson: the study window shows it
            self.study_show(what.strip())
        result = STUDY.sub("(now shown in the study window)", result)
        for target in SHOW.findall(result):
            self.show(target.strip())
        result = SHOW.sub("(now shown in Karen's viewer)", result)
        for url, title, link in MAP.findall(result):
            if getattr(self.ui, "island", None):
                self.ui.island.show_map(url.strip(), title.strip(), link.strip())
            else:
                self.show(link.strip() or url.strip())
        result = MAP.sub("(now shown on screen)", result)
        for lang, voice, text in SAY.findall(result):
            self.ui.call("message", "karen", text, "translation")
            try:
                self.speaker.say_in(text, lang.strip(), voice.strip())
            except Exception as e:
                log("tool", f"say_in {lang} failed: {e}")
        result = SAY.sub("Spoken aloud and shown already. Don't write the translation again. Reply with a few words, "
                         "e.g. That's how you say it in Spanish.", result)
        self.used.add(self.tools.group_of(name))
        return result

    def dot_done(self, run):
        """A Dot finished (from its thread): Karen says so between conversations, like a reminder."""
        self.ui.call("dots", self.dot_status())
        if run["origin"] in ("voice", "window"):
            self.action("announce", f"{run['dot']} finished. {run['answer']}")

    def dot_confirm(self, dot, tool, args, origin):
        """A Dot's risky tool: queue the yes/no for Karen's main loop and wait for it (10 minutes, else no)."""
        reply = queue.Queue()
        self.action("approve", (dot, tool, args, reply))
        try:
            return reply.get(timeout=600)
        except queue.Empty:
            return False

    def dot_status(self):
        from core import dots
        return {"running": list(self.dots.running.values()), "recent": dots.runs()[-8:]}

    def confirm(self, question):
        """Spoken yes/no (or the YES/NO buttons). Anything but a clear yes cancels."""
        from core.audio import Interrupted
        log("confirm", question)
        while not self.confirm_answers.empty():
            self.confirm_answers.get_nowait()
        self.ui.call("state", "confirm")
        self.ui.call("confirm", question)
        answer = None
        if not self.mic:  # --text mode
            answer = input(f"{question} [yes/no] ").strip().lower().startswith("y")
        else:
            self.speaker.say(question)
            self.ui.call("state", "confirm")
            self.mic.flush()
            try:
                audio = self.mic.listen(timeout=8)
                if audio is not None:
                    heard = self.stt.transcribe(audio)
                    log("heard", heard)
                    answer = bool(YES.match(heard))
            except Interrupted:
                self.mic.interrupt.clear()
            if answer is None and not self.confirm_answers.empty():
                answer = self.confirm_answers.get_nowait()
        self.ui.call("confirmClose")
        self.ui.call("state", "thinking", "TOOLS")
        log("confirm", "yes" if answer else "no")
        return bool(answer)

    # ---------------------------------------------------------------- interface
    def check_reminders(self, path="data/reminders.json"):
        """Announce reminders and timers that are due (set by the daily server). Missed ones are announced late."""
        try:
            items = json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError):
            return
        now, changed = datetime.datetime.now(), False
        for r in items:
            if r.get("done"):
                continue
            due = datetime.datetime.fromisoformat(r["due"])
            if due > now:
                continue
            late = now - due > datetime.timedelta(minutes=2)
            island = getattr(self.ui, "island", None)
            if island and r.get("tag") != "hiit":  # the capsule shows it with Snooze / Stop, even while she's busy
                # (not each HIIT call-out: they come every 20-40 s and the countdown already shows the next one)
                island.ring("timer" if r.get("timer") else "reminder", r["text"], dict(r))
            self.action("announce", ("" if r.get("timer") else "Reminder: ") + r["text"]
                        + (f" (this was due at {due:%I:%M %p})" if late else "") + ".")
            from core.reminders import advance
            advance(r, now)  # daily / every N minutes / once
            changed = True
        if changed:
            json.dump(items, open(path + ".tmp", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
            os.replace(path + ".tmp", path)
        self.check_announcements()

    def check_announcements(self, path="data/announce.json"):
        """Updates Karen Remote wants said aloud: he's at the PC, so she speaks instead of ringing his phone
        (remote.py ring_pending). The file is taken by renaming it, so a line Remote adds meanwhile isn't lost."""
        if not os.path.exists(path):
            return
        taking = path + ".taking"
        try:
            os.replace(path, taking)
            with open(taking, encoding="utf-8") as f:
                items = json.load(f)
        except (OSError, ValueError):
            return
        finally:
            try:
                os.remove(taking)
            except OSError:
                pass
        now = datetime.datetime.now()
        for it in items if isinstance(items, list) else []:
            try:
                fresh = now - datetime.datetime.fromisoformat(it["at"]) < datetime.timedelta(minutes=10)
            except (KeyError, TypeError, ValueError):
                fresh = False
            if fresh and it.get("text"):
                self.action("announce", it["text"])

    def ticker(self):
        """Audio levels ~15x a second and system stats once a second, for the interface."""
        import psutil
        drive = os.environ.get("SystemDrive", "C:") + "\\"
        n, sent_zero = 0, False
        while True:
            mic = 0.0 if self.muted else min(1.0, self.mic.level * 3)
            voice = min(1.0, self.speaker.level * 5)
            if mic > 0.01 or voice > 0.01 or not sent_zero:
                self.ui.call("level", mic, voice)
                sent_zero = mic <= 0.01 and voice <= 0.01
            if n % 15 == 0:  # every ~1 s: HIIT/Pomodoro announcements need to be on time
                self.check_reminders()
            if n % 75 == 0:  # every ~5 s
                self.dots.tick()  # scheduled Dot tasks
            if n % 15 == 0:
                bat = psutil.sensors_battery()
                up = int(time.time() - self.started) // 60
                self.ui.call("stats", {"cpu": psutil.cpu_percent(), "ram": psutil.virtual_memory().percent,
                                       "bat": bat.percent if bat else None, "plugged": bat.power_plugged if bat else True,
                                       "disk": psutil.disk_usage(drive).percent, "uptime": f"{up // 60}h {up % 60}m"})
            n += 1
            time.sleep(0.066)

    def set_setting(self, key, value):
        from core.wake import OwwWake, PersonalWake
        if key == "mode":  # Settings > Mode (work / both / love): saved in data/, shared with Remote and the tools
            from core import persona
            persona.set_mode(value) if value else persona.clear()
            self.push_config()
            log("set", f"mode = {value or 'normal'}")
            return
        if key == "voice":
            self.speaker.voice = value
            self.speaker.cache.clear()
        elif key == "speed":
            self.speaker.speed = float(value)
            self.speaker.cache.clear()
        elif key == "brain":
            self.brain.backends.sort(key=lambda b: b["name"] != value)
            self.ui.call("abilities", self.abilities())
        elif key == "wake_threshold" and isinstance(self.wake, (OwwWake, PersonalWake)):
            value = self.wake.threshold = max(float(value), getattr(self.wake, "floor", 0.0))
        elif key == "face_lock":
            value = self.load_face(bool(value))
            self.locked = False  # he's at the window switching it on; it locks again on "lock yourself" or idle
            self.face_seen = time.time()
        elif key not in ("sounds", "avatar"):
            return
        self.settings[key] = value
        json.dump(self.settings, open(SETTINGS, "w"), indent=2)
        self.push_config()
        log("set", f"{key} = {value}")


class Controls:
    """Exposed to the page as window.pywebview.api. Runs on pywebview threads, so keep each call quick."""

    def __init__(self, karen):
        self._k = karen  # leading underscore: pywebview does not expose it to the page

    def ready(self):
        self._k.ui.page_ready()

    def talk(self):
        self._k.action("talk")

    def send_text(self, text):
        if text and text.strip():
            self._k.action("text", text.strip())

    def stop(self):
        if self._k.speaker:
            self._k.speaker.stop_event.set()

    def set_muted(self, muted):
        self._k.action("mute", bool(muted))

    def set_setting(self, key, value):
        self._k.set_setting(key, value)

    def clear_memory(self):
        if self._k.brain:
            self._k.brain.forget()
        self._k.sticky = set()
        self._k.ui.call("memory", 0)
        log("memory", "conversation forgotten")

    def open_log(self):
        os.startfile(os.path.abspath("logs/karen.log"))

    def add_files(self):
        """The + button: native file picker, then the files ride along with the next requests."""
        paths = self._k.ui.window.create_file_dialog(self._k.ui.webview.OPEN_DIALOG, allow_multiple=True)
        if paths:
            self._k.add_attachments(list(paths))
            log("files", f"added {len(paths)}: {', '.join(os.path.basename(p) for p in paths)}")

    def upload(self, name, data_b64):
        """Drag and drop onto the window: the page sends the file's bytes."""
        import base64
        os.makedirs("data/uploads", exist_ok=True)
        path = os.path.join("data", "uploads", os.path.basename(name) or "upload.bin")
        with open(path, "wb") as f:
            f.write(base64.b64decode(data_b64.split(",", 1)[-1]))
        self._k.add_attachments([path])
        log("files", f"dropped {name}")

    def remove_attachment(self, name):
        self._k.attachments = [a for a in self._k.attachments if os.path.basename(a) != name]
        self._k.push_attachments()

    # ------------------------------------------------ Karen Island (core/island.py)
    def island_ready(self):
        self._k.ui.island.page_ready()

    def island_resize(self, w, h):
        self._k.ui.island.resize(w, h)

    def island_close(self):
        self._k.ui.island.collapse()

    def island_blur(self):
        self._k.ui.island.blur()

    def island_music(self, action):
        from core.island import media_control
        if action in ("pause", "resume", "next", "previous"):
            threading.Thread(target=media_control, args=(action,), daemon=True).start()

    def island_seek(self, seconds):
        threading.Thread(target=self._k.ui.island.seek, args=(float(seconds),), daemon=True).start()

    def island_volume(self, level=None, muted=None):
        from core.island import set_pc_volume
        set_pc_volume(level, muted)

    def island_clip(self, action):
        """The panel's clipboard buttons (Summarise / Translate / Explain / dismiss)."""
        def run():
            request = self._k.ui.island.clip_action(action)
            if request:
                self._k.action("text", request)
        threading.Thread(target=run, daemon=True).start()

    def island_files(self, paths):
        """Files dropped on the capsule: they ride along with the next requests, like the + button."""
        self._k.add_attachments(paths)
        log("files", f"dropped on the island: {', '.join(os.path.basename(p) for p in paths)}")

    def island_alert(self, how, item):
        """The capsule's Stop / Snooze on a timer or reminder that went off."""
        if self._k.speaker:
            self._k.speaker.stop_event.set()
        if how == "snooze" and item:
            from core.reminders import snooze
            snooze(item, 5)
            log("remind", f"snoozed 5 min: {item.get('text', '')}")

    def island_phone(self, answer):
        """The capsule's answer / reject buttons while the phone rings."""
        pw = self._k.phonewatch
        if not pw:
            return
        said = pw.command("pick up" if answer else "reject") or ""
        if self._k.speaker:
            self._k.speaker.stop_event.set()
        if self._k.mic:
            self._k.mic.interrupt.set()  # she was listening for "pick up" / "reject"
        log("phone", f"island: {said}")
        if said:
            self._k.ui.island.note(said)

    def open_main(self):
        self._k.ui.window.show()
        self._k.ui.window.restore()

    def listen_cut(self, how):
        """The island's listening pill: ✓ = that's all, use what I said; ✕ = never mind."""
        mic = self._k.mic
        if not mic:
            return
        if how == "done":
            mic.finish.set()
        else:
            if self._k.speaker:
                self._k.speaker.stop_event.set()
            mic.interrupt.set()

    def confirm_answer(self, yes):
        self._k.confirm_answers.put(bool(yes))
        if self._k.mic:
            self._k.mic.interrupt.set()  # stop listening for a spoken yes/no

    # ------------------------------------------------ Spaces drawer (pages and Dots); errors come back as {"error"}
    @staticmethod
    def _safe(fn, *args):
        try:
            return fn(*args)
        except (ValueError, OSError, KeyError) as e:
            return {"error": str(e)}

    def spaces_list(self):
        from core import spaces
        return [spaces.info(*p) for p in spaces.pages()]

    def spaces_read(self, name):
        from core import spaces
        return self._safe(lambda: {"name": name, "text": spaces.read(spaces.path(name)[1])})

    def spaces_save(self, name, text):
        from core import spaces
        return self._safe(lambda: {"name": spaces.save(name, text)})

    def spaces_trash(self, name):  # the page asks "Move to trash?" first
        from core import spaces
        return self._safe(lambda: {"name": spaces.trash(name)})

    def dots_state(self):
        from core import dots
        return {"dots": dots.dots(), "jobs": dots.jobs(), "abilities": list(dots.ABILITIES), **self._k.dot_status()}

    def dot_save(self, dot, old_name=""):
        from core import dots, spaces
        name = spaces.clean(dot.get("name", "")).strip()
        if not name or dot.get("abilities") not in dots.ABILITIES:
            return {"error": "A Dot needs a name and abilities."}
        items = [d for d in dots.dots() if d["name"].lower() not in (name.lower(), (old_name or "").lower())]
        items.append({"name": name, "role": dot.get("role", "").strip() or "helps the user",
                      "instructions": dot.get("instructions", "").strip(), "abilities": dot["abilities"],
                      "space": spaces.clean(dot.get("space", "")) or name, "private": bool(dot.get("private", True))})
        dots.save_dots(items)
        return self.dots_state()

    def dot_delete(self, name):  # the page asks first
        from core import dots
        dots.save_dots([d for d in dots.dots() if d["name"] != name])
        return self.dots_state()

    def dot_ask(self, name, task, page=""):
        from core import dots
        dot = dots.find(name)
        if not dot or not task.strip():
            return {"error": "Pick a Dot and type a task."}
        if page:
            task = f"{task.strip()} (This is about the page '{page}': open it first.)"
        self._k.dots.start(dot, task.strip(), origin="window")
        log("dot", f"{dot['name']} asked from the window: {task.strip()}")
        return self.dots_state()

    def job_cancel(self, job_id):
        from core import dots
        dots.save_jobs([j for j in dots.jobs() if j["id"] != job_id])
        return self.dots_state()

    # ------------------------------------------------ study window (ui/study.html)
    def study_state(self):
        """What the study window shows when it (re)loads: the conversation so far and today's notes."""
        from core import study
        out = {"notes": "", "thread": getattr(self._k.ui, "study_thread", [])}
        try:
            with open(os.path.join(study.DIR, "notes", time.strftime("%Y-%m-%d") + ".md"), encoding="utf-8") as f:
                out["notes"] = f.read()
        except OSError:
            pass
        return out

    def study_mode(self, on):
        """The Study button in Karen's dock, and Exit study in the study window."""
        self._k.action("study", bool(on))

    def study_save_notes(self, text):
        from core import study
        folder = os.path.join(study.DIR, "notes")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, time.strftime("%Y-%m-%d") + ".md"), "w", encoding="utf-8") as f:
            f.write(text or "")
        return True

    def open_link(self, url):
        """Links inside pages open in his browser, never inside Karen's window."""
        import webbrowser
        if re.match(r"^https?://", url or ""):
            webbrowser.open(url)


def quiet_now(span):
    """True inside a "23:00-07:00" style window (may cross midnight)."""
    if not span:
        return False
    a, b = span.split("-")
    now = datetime.datetime.now().strftime("%H:%M")
    return a <= now < b if a <= b else now >= a or now < b


def face_ready():
    from core.face import FaceLock
    return FaceLock.ready()


def persona_mode():
    from core import persona
    return persona.current()


VOICE = True  # Karen Remote sets this False: confirm questions are read, not heard


def spoken(text):
    """Make addresses and ISO times sound natural: arun@gmail.com -> arun at gmail dot com."""
    if not VOICE:
        return text
    text = re.sub(r"([\w.+-]+)@([\w-]+)\.([\w.]+)",
                  lambda m: f"{m[1]} at {m[2]} dot {m[3].replace('.', ' dot ')}", text)

    def when(m):
        try:
            d = datetime.datetime.fromisoformat(m[0])
        except ValueError:
            return m[0]
        return f"{d:%A %d %B}" + ("" if len(m[0]) <= 10 else f" at {d:%I:%M %p}".replace(" 0", " "))
    return re.sub(r"\d{4}-\d{2}-\d{2}(T[\d:]+(\.\d+)?(Z|[+-]\d{2}:?\d{2})?)?", when, text)


SLOW_TOOLS = {"operate", "phone_do", "power_mode", "send_whatsapp", "send_whatsapp_file", "whatsapp_read", "whatsapp_chats",
              "junk_scan", "clean_junk", "look", "research_mode", "research_report", "research_summary",
              "explain_error", "work_mode", "teach", "stop_meeting_notes"}


SLOW_SAY = {"phone_do": "On it. Watch your phone.", "look": "Let me see.", "research_report": "On it. The report takes about a minute.",
            "explain_error": "Let me look.", "teach": "Let me set up the lesson.",
            "stop_meeting_notes": "Wrapping up the meeting notes. This takes about a minute."}


def describe(tool, args):
    """One line Karen asks before a risky tool runs."""
    a = {k: v for k, v in args.items() if v not in (None, "", [])}
    if tool == "delete_page":
        return spoken(f"Move the page {a.get('page', '')} to the Spaces trash?")
    if tool == "delete_dot":
        return f"Remove the {a.get('name', '')} Dot?"
    if tool == "send_gmail_message":
        reply = a.get("thread_id") or a.get("in_reply_to")
        lines = [ln.strip() for ln in str(a.get("body", "")).strip().splitlines() if ln.strip()]
        if len(lines) > 1 and GREETING.match(lines[0]):
            lines = lines[1:]  # "Hi Arun,"
        for _ in range(2):
            if len(lines) > 1 and len(lines[-1].split()) <= 3:
                lines = lines[:-1]  # "Thanks," / "The user"
        body = " ".join(lines).rstrip(".!?")
        body = body if len(body) <= 160 else body[:160].rsplit(" ", 1)[0] + "..."
        who = f" to {a['to']}" if a.get("to") else ""
        return spoken(f"{'Reply' if reply else 'Send the email'}{who} saying: {body}?")
    if tool == "manage_event":
        action = str(a.get("action", "create")).lower()
        if action == "delete":
            return "Delete that calendar event?"
        what = f" {a['summary']}" if a.get("summary") else ""
        when = f" on {a['start_time']}" if a.get("start_time") else ""
        guests = f", inviting {', '.join(map(str, a['attendees']))}" if isinstance(a.get("attendees"), list) else ""
        return spoken(f"{action.capitalize()} the calendar event{what}{when}{guests}?")
    if tool == "batch_modify_gmail_message_labels":
        add, remove = a.get("add_label_ids") or [], a.get("remove_label_ids") or []
        change = ", ".join(filter(None, [f"add {', '.join(map(str, add))}" if add else "",
                                         f"remove {', '.join(map(str, remove))}" if remove else ""]))
        return f"Change labels on {len(a.get('message_ids') or [])} emails ({change})?"
    if tool == "manage_gmail_filter":
        return f"{str(a.get('action', 'create')).capitalize()} a Gmail filter: {json.dumps(a.get('criteria') or a, ensure_ascii=False)[:160]}?"
    if tool == "manage_gmail_label":
        return f"{str(a.get('action', 'create')).capitalize()} the Gmail label {a.get('name') or a.get('label_id', '')}?"
    if tool == "git_commit":
        return f"Commit all changes in {a.get('project', '')} with the message: {str(a.get('message', '')).rstrip('.?!')}?"
    if tool == "git_push":
        return f"Push {a.get('project', '')} to its remote?"
    if tool == "clean_junk":
        return f"Permanently delete these to free space: {a.get('categories', '')}? They can't be restored."
    if tool == "send_whatsapp_file":
        cap = f" with the caption {a['caption']}" if a.get("caption") else ""
        return spoken(f"Send the file {os.path.basename(str(a.get('path', '')))} to {a.get('contact', '')} on WhatsApp{cap}?")
    if tool == "phone_send_whatsapp":
        what = " and ".join(filter(None, [f"the file {os.path.basename(str(a['file']))}" if a.get("file") else "",
                                          f"the message: {str(a['message']).rstrip('.?!')}" if a.get("message") else ""]))
        return spoken(f"From your phone, send {a.get('contact', '')} {what or 'a message'} on WhatsApp?")
    if tool == "phone_send_sms":
        phone = "your second phone" if a.get("which") else "your phone"
        return spoken(f"Text {a.get('contact', '')} from {phone}: {str(a.get('message', '')).rstrip('.?!')}?")
    if tool == "phone_call":
        sim = f" on SIM {a['sim']}" if a.get("sim") else ""
        phone = "your second phone" if a.get("which") else "your phone"
        return spoken(f"Call {a.get('contact', '')} from {phone}{sim}?")
    if tool == "send_whatsapp":
        return spoken(f"Send WhatsApp to {a.get('contact', '')}: {str(a.get('message', '')).rstrip('.?!')}?")
    if tool == "phone_do":
        return (f"On your phone: {str(a.get('task', '')).rstrip('.?!')}. It taps through the apps by itself and "
                f"screenshots go to Google's Gemini. Go ahead?")
    if tool == "power_mode":
        return f"Power mode: {str(a.get('task', '')).rstrip('.?!')}. It will write and run code on your PC. Go ahead?"
    if tool == "tidy_folder_apply":
        return f"Move the loose files in {a.get('folder', 'that folder')} into type folders?"
    if tool == "batch_rename_apply":
        how = ", ".join(filter(None, [f"replace '{a['find']}' with '{a.get('replace', '')}'" if a.get("find") else "",
                                      f"add '{a['prefix']}' in front" if a.get("prefix") else "",
                                      "number them" if a.get("numbering") else ""]))
        return f"Rename the files in {a.get('folder', 'that folder')}: {how or 'as previewed'}?"
    if tool == "delete_file":
        return f"Move {os.path.basename(str(a.get('path', 'that file')))} to the Recycle Bin?"
    if tool == "power_appliance":
        return f"Turn {'on' if a.get('on') else 'off'} the {a.get('name', 'appliance')}?"
    if tool == "alexa_command":
        return f"Tell Alexa: {str(a.get('text', '')).rstrip('.?!')}?"
    if tool == "run_routine":
        return f"Run your Alexa routine {a.get('name', '')}?"
    return f"Go ahead and {tool.replace('_', ' ')}?"


def start_tray(karen, visible=True):
    """Tray icon (show/hide, mute, quit). The window's X quits Karen fully, so she never listens unseen."""
    try:
        from core.tray import Tray
    except ImportError:
        return None

    def quit_app():
        log("quit", "from the tray")
        karen.ducker.restore()
        tray.stop()
        os._exit(0)

    tray = Tray(show=karen.ui.window.show, hide=karen.ui.window.hide, set_muted=lambda m: karen.action("mute", m),
                is_muted=lambda: karen.muted, quit_app=quit_app)
    tray.start(visible)
    return tray


def mic_meter(cfg, seconds=15):
    """Live mic level and speech probability, to check the mic before using Karen."""
    import numpy as np
    from core.audio import Mic
    mic = Mic("models/silero_vad.onnx", cfg["audio"]["input_device"])
    print(f"Talk normally for {seconds}s. 'speech' should jump above {cfg['audio']['vad_threshold']} when you talk.")
    end = time.time() + seconds
    while time.time() < end:
        chunks = [mic.read() for _ in range(8)]  # ~0.25 s
        peak = max(float(np.abs(c).max()) for c in chunks)
        prob = max(mic.vad(c) for c in chunks)
        print(f"\rlevel {'#' * int(min(peak, 1) * 50):<50} {peak:.3f}  speech {prob:.2f}", end="", flush=True)
    print()


def single_instance():
    """Only one Karen at a time: a second copy would hear the same wake word and talk over the first."""
    global _MUTEX
    _MUTEX = ctypes.windll.kernel32.CreateMutexW(None, False, r"Local\KarenVoiceAssistant")
    return ctypes.windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def main():
    if sys.stdout:
        sys.stdout.reconfigure(errors="replace")  # a "₹" in a Dot's answer crashed print() on the cp1252 console
    if "--devices" in sys.argv:
        import sounddevice as sd
        print(sd.query_devices())
        return
    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    try:
        settings = json.load(open(SETTINGS, encoding="utf-8"))
    except (OSError, ValueError):
        settings = {}
    if "--mic" in sys.argv:
        mic_meter(cfg)
        return

    if not single_instance():
        msg = "Karen is already running."
        print(msg)
        if "--text" not in sys.argv and "--no-ui" not in sys.argv:
            ctypes.windll.user32.MessageBoxW(None, msg, "Karen", 0x40)
        return

    crash_logging()
    karen = Karen(cfg, settings)
    if "--text" in sys.argv:
        karen.load(listen=False)
        log("ready", "type a request (empty line quits)")
        while text := input("> ").strip():
            if text == "/wait":  # wait for background work (Dots) to announce or ask something
                try:
                    karen.do(*karen.actions.get(timeout=180))
                except queue.Empty:
                    pass
            else:
                karen.answer(text)
            while not karen.actions.empty():
                karen.do(*karen.actions.get_nowait())
    elif "--no-ui" in sys.argv:
        karen.run()
    else:
        from core.ui import UI
        background = "--background" in sys.argv  # autostart: only the tray icon until he opens the window
        karen.controls = Controls(karen)
        island = cfg.get("island") or {}
        hidden = background or (island.get("enabled") and not island.get("window_on_start"))  # the island is enough
        karen.ui = UI(karen.controls, hidden=hidden, characters=cfg.get("avatar", {}).get("characters"), island=island)
        tray = start_tray(karen, visible=not background)
        karen.ui.run(karen.run)
        log("quit", "window closed")
        if tray:
            tray.stop()
        karen.ducker.restore()  # never leave other apps turned down
        os._exit(0)  # window closed: stop the voice thread and audio streams too


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("quit", "Ctrl+C")
        print("Bye.")
