"""Meeting notes, as an MCP server (stdio): "Karen, start taking meeting notes" ... "stop taking meeting notes".

Its own feature, not part of study mode (his choice, 2026-10-29: notes are for meetings). While it runs:
- picture: ffmpeg records the meeting window (Zoom, Teams, Meet, Webex; else the whole screen) at 5 fps and drops
  frames where nothing changed (mpdecimate) -> video.mp4. A frozen screen costs almost nothing.
- sound: soundcard records the meeting sound (speaker loopback) and his mic, mixed -> audio.pcm (16 kHz mono).
  Picture and sound are separate files, joined at the end by their real start times (piping the sound into ffmpeg
  drifted by its start-up time).
- every ~30 s of sound with speech goes to Groq Whisper (free, zero data retention) -> transcript.md, and every few
  minutes the fast free model turns the new part into bullets -> live.html (refreshes itself in the viewer).
On stop: stretches with no speech AND no picture change are cut out (lossless, no re-encode), picture and sound are
joined into meeting.mp4, slide changes are saved as pictures, and Claude (his subscription; the fast model if that
fails) writes the notes: summary, decisions, action items, open questions -> notes.md + notes.html, shown in the viewer.
Everything goes to Documents/Karen/Meetings/<date time title>/. If Karen closes mid-meeting, the next start finishes
that meeting from what was saved (without the cuts).
"""
import ctypes
import html
import io
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import wave
from ctypes import wintypes

import numpy as np
import yaml
from mcp.server import MCPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import claude_code  # noqa: E402  (servers/claude_code.py: his Claude subscription)
from openrouter import _groq_ok as groq_ok, chat  # noqa: E402  (servers/openrouter.py: Groq first; also loads .env)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STATE = os.path.join(ROOT, "data", "meeting.json")
mcp = MCPServer("meeting")
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # real pixel coordinates for the window (display at 125%)
except OSError:
    pass

RATE, BLOCK = 16000, 1600  # 0.1 s blocks
SPEECH = 0.006  # RMS above this (about -44 dBFS) counts as someone talking
CHUNK_MIN, CHUNK_MAX = 25, 40  # seconds of sound per Whisper call; cut at a pause after CHUNK_MIN
NOTE_EVERY = 240  # live bullets every 4 minutes of new talk
MAX_HOURS = 3
MIN_CUT = 8  # only quiet + frozen stretches at least this long are cut, keeping 1 s at each edge
PHANTOM = {"thank you", "thanks for watching", "thank you for watching", "you", "bye", "thank you very much"}
MEETING = re.compile(r"^Zoom Meeting|^Zoom Webinar|^Meet - |Google Meet|Webex|(Meeting|Call).*Microsoft Teams", re.I)
NO_WINDOW = 0x08000000
rec = None  # the running Recorder


def known_folder(guid):
    import uuid
    buf = ctypes.c_wchar_p()
    ok = ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(ctypes.c_buffer(uuid.UUID(guid).bytes_le, 16)), 0,
                                                    None, ctypes.byref(buf)) == 0
    path = buf.value if ok else os.path.join(os.path.expanduser("~"), "Documents")
    ctypes.windll.ole32.CoTaskMemFree(buf)
    return path


OUT = os.path.join(known_folder("FDD39AD0-238F-46AF-ADB4-6C85480369C7"), "Karen", "Meetings")


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(**kw):
    st = load_state()
    st.update(kw)
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(st, f)


def clock(s):
    s = int(max(0, s))
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"


def ffmpeg():
    return shutil.which("ffmpeg") or "ffmpeg"


def meeting_window():
    """(gdigrab region args, what is recorded): the meeting app's window if one is open, else the whole screen."""
    found = []
    user32 = ctypes.windll.user32

    def each(hwnd, _):
        if user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if MEETING.search(buf.value):
                r = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(r))
                found.append((buf.value, r.left, r.top, r.right, r.bottom))
        return True
    user32.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)(each), 0)
    vx, vy = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)  # the virtual screen (all monitors)
    vw, vh = user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)
    for title, left, top, right, bottom in sorted(found, key=lambda w: -(w[3] - w[1]) * (w[4] - w[2])):
        left, top = max(left, vx), max(top, vy)
        w, h = (min(right, vx + vw) - left) // 2 * 2, (min(bottom, vy + vh) - top) // 2 * 2
        if w >= 480 and h >= 320:
            name = title.split(" - ")[0] if title.startswith("Meet - ") else title.split("|")[0].strip()
            return ["-offset_x", str(left), "-offset_y", str(top), "-video_size", f"{w}x{h}"], f"the {name} window"
    return [], "the whole screen, since no meeting window is open"


class Recorder:
    def __init__(self, folder, title=""):
        self.folder, self.title = folder, title
        self.t0 = time.time()
        self.stopping = threading.Event()
        self.levels = []  # RMS per 0.1 s block: where people talked (for the cuts)
        self.dev_levels = {}  # "them" (meeting sound) / "you" (his mic): RMS per block each, for who said what
        self.offset, self.dead = 0.0, []  # sound vs picture start, and the cut stretches (video time) after finish
        self.delivered = []  # where the notes were sent
        self.own = ""  # what Karen said when it started (her voice is in the loopback too)
        self.transcript = []  # (seconds into the meeting, text)
        self.bullets = []  # (seconds, bullet) live notes
        self.failed = []  # (seconds, pcm) chunks Whisper couldn't do; tried again at the end
        self.chunks = queue.Queue()
        self.video = self.video_start = self.audio_start = None
        self.source, self.sound, self.capped = "", "", False
        self.err = []
        self.noted_upto = 0  # transcript lines already turned into bullets
        self.last_note = time.time()

    def path(self, name):
        return os.path.join(self.folder, name)

    # ------------------------------------------------ recording
    def start(self):
        os.makedirs(self.folder, exist_ok=True)
        region, self.source = meeting_window()
        args = [ffmpeg(), "-hide_banner", "-nostats", "-loglevel", "info", "-y", "-f", "gdigrab", "-framerate", "5",
                "-draw_mouse", "0", *region, "-i", "desktop",
                "-vf", "mpdecimate,scale='trunc(min(1280,iw)/2)*2':-2", "-fps_mode", "vfr",
                "-force_key_frames", "expr:gte(t,n_forced*2)",  # a keyframe every 2 s: lossless cuts land close
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-pix_fmt", "yuv420p",
                "-movflags", "+frag_keyframe+empty_moov", self.path("video.mp4")]  # fragmented: a crash keeps the file
        self.video = subprocess.Popen(args, stdin=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=NO_WINDOW)
        threading.Thread(target=self._video_log, daemon=True).start()
        threading.Thread(target=self._audio, daemon=True).start()
        threading.Thread(target=self._transcriber, daemon=True).start()
        self.write_live()

    def _video_log(self):
        for line in io.TextIOWrapper(self.video.stderr, errors="replace"):
            m = self.video_start is None and re.search(r"start: (\d{9,}\.\d+)", line)
            if m:  # gdigrab stamps frames with the wall clock: this is when the picture began
                self.video_start = float(m.group(1))
            self.err = (self.err + [line.strip()])[-15:]

    def _audio(self):
        import warnings
        import soundcard as sc
        warnings.filterwarnings("ignore", category=sc.SoundcardRuntimeWarning)  # "data discontinuity": harmless gaps
        devices = []
        try:
            devices.append(("meeting sound", sc.get_microphone(id=str(sc.default_speaker().name), include_loopback=True)))
        except Exception as e:
            self.err.append(f"loopback: {e!r}")
        try:
            devices.append(("your mic", sc.default_microphone()))
        except Exception as e:
            self.err.append(f"mic: {e!r}")
        self.sound = " and ".join(n for n, _ in devices) or "no sound"
        if not devices:
            return
        who = ["them" if n == "meeting sound" else "you" for n, _ in devices]
        self.dev_levels = {w: [] for w in who}
        recorders = [d.recorder(samplerate=RATE, channels=1, blocksize=BLOCK) for _, d in devices]
        chunk, chunk_at = [], 0.0
        with open(self.path("audio.pcm"), "ab") as out:
            for r in recorders:
                r.__enter__()
            try:
                while not self.stopping.is_set():
                    xs = [r.record(numframes=BLOCK)[:, 0] for r in recorders]
                    for w, part in zip(who, xs):
                        self.dev_levels[w].append(float(np.sqrt(np.mean(part * part))))
                    x = sum(xs)
                    if self.audio_start is None:
                        self.audio_start = time.time() - BLOCK / RATE
                    pcm = (np.clip(x, -1, 1) * 32767).astype(np.int16)
                    out.write(pcm.tobytes())
                    self.levels.append(float(np.sqrt(np.mean(x * x))))
                    chunk.append(pcm)
                    seconds = len(chunk) * BLOCK / RATE
                    pause = len(self.levels) >= 3 and max(self.levels[-3:]) < SPEECH
                    if seconds >= CHUNK_MAX or (seconds >= CHUNK_MIN and pause):
                        self._send(chunk_at, chunk)
                        chunk, chunk_at = [], len(self.levels) * BLOCK / RATE
                    if time.time() - self.t0 > MAX_HOURS * 3600:
                        self.capped = True
                        break
            finally:
                self._send(chunk_at, chunk)
                for r in recorders:
                    r.__exit__(None, None, None)
                self.chunks.put(None)  # the transcriber finishes what's queued, then stops

    def _send(self, at, chunk):
        n = len(chunk)
        if n and max(self.levels[-n:]) >= SPEECH:  # skip chunks where nobody spoke
            self.chunks.put((at, np.concatenate(chunk)))

    # ------------------------------------------------ transcript and live notes
    def _transcriber(self):
        while True:
            item = self.chunks.get()
            if item is None:
                return
            self.transcribe(*item)
            if time.time() - self.last_note > NOTE_EVERY:
                self.live_notes()

    def read_later(self, notes, media, slides, saved):
        """Reads the slides with the private vision model, then adds what they say to notes.html and notes.md."""
        try:
            read = read_slides(self, slides)
            if any(x["text"] or x.get("skip") for x in read):
                write_atomic(self.path("notes.html"), notes_html(self, notes, media, read, saved))
                write_atomic(self.path("notes.md"), notes_md(self, notes, read))
        except Exception as e:  # noqa: BLE001
            print("slides:", repr(e), file=sys.stderr)

    def speaker(self, a, b):
        """Who said the words between a and b seconds: "You" (his mic) or "Them" (the meeting's sound), from the two
        recordings' loudness. Their voices also reach his mic through the speakers, but far quieter than the loopback;
        "" when only one of the two was recorded."""
        if len(self.dev_levels) < 2:
            return ""
        i, j = int(a * 10), max(int(b * 10), int(a * 10) + 1)
        them, you = (float(np.mean(self.dev_levels[w][i:j] or [0])) for w in ("them", "you"))
        return "Them" if them >= max(SPEECH * 0.5, you * 0.5) else "You"

    def transcribe(self, at, pcm):
        try:
            context = " ".join(t for _, t in self.transcript[-3:])[-300:]  # Whisper keeps names and terms consistent
            for start, end, text in whisper(pcm, f"{self.title}. {context}".strip(" .")):
                if len(plain(text)) >= 8 and plain(text) in plain(self.own):
                    continue  # Karen's own "Taking meeting notes..." came back through the speakers' loopback
                who = self.speaker(at + start, at + end)
                self.transcript.append((at + start, f"{who}: {text}" if who else text))
            with open(self.path("transcript.md"), "w", encoding="utf-8") as f:
                f.write(transcript_md(self))
        except Exception as e:
            self.failed.append((at, pcm))
            self.err.append(f"whisper: {type(e).__name__}")
        self.write_live()

    def live_notes(self):
        new = self.transcript[self.noted_upto:]
        if sum(len(t) for _, t in new) < 400:
            return
        self.last_note = time.time()
        self.noted_upto = len(self.transcript)
        lines = "\n".join(f"[{clock(s)}] {t}" for s, t in new)[-9000:]
        try:
            out = chat(None, [{"role": "user", "content":
                "Live meeting notes. Below is the newest part of the transcript (speech recognition, may mishear "
                "words). Write 1 to 4 short bullet points of what matters: facts, decisions, tasks, deadlines, "
                "questions. Each line starts with '- '. Only the bullets. If nothing matters, reply '-'.\n\n" + lines}],
                max_tokens=1200, timeout=40)
        except Exception as e:
            self.err.append(f"live notes: {type(e).__name__}")
            return
        at = new[0][0]
        for line in out.splitlines():
            line = line.strip().lstrip("-•* ").strip()
            if len(line) > 3:
                self.bullets.append((at, line))
        self.write_live()

    def write_live(self):
        recent = self.transcript[-14:]
        body = (f"<h1>{esc(self.title or 'Meeting notes')} <span class=rec>● recording</span></h1>"
                f"<p class=meta>Since {time.strftime('%H:%M', time.localtime(self.t0))} · {esc(self.source)} · "
                f"{esc(self.sound)} · this page refreshes itself</p>"
                "<h2>Notes so far</h2>" +
                ("<ul>" + "".join(f"<li><span class=t>{clock(s)}</span> {esc(b)}</li>" for s, b in self.bullets) + "</ul>"
                 if self.bullets else "<p class=meta>The first notes come after a few minutes of talk.</p>") +
                "<h2>Just said</h2>" +
                ("".join(f"<p><span class=t>{clock(s)}</span> {esc(t)}</p>" for s, t in recent) or
                 "<p class=meta>Nothing yet.</p>"))
        write_atomic(self.path("live.html"), page("Live meeting notes", body, refresh=15))

    # ------------------------------------------------ stop and finish
    def stop(self):
        self.stopping.set()
        if self.video and self.video.poll() is None:
            try:
                self.video.stdin.write(b"q")
                self.video.stdin.flush()
                self.video.wait(timeout=20)
            except Exception:
                self.video.kill()
        deadline = time.time() + 90  # the last Whisper calls
        while not self.chunks.empty() and time.time() < deadline:
            time.sleep(0.5)
        time.sleep(1)
        for at, pcm in self.failed[:]:  # one more try for chunks Whisper missed
            self.failed.remove((at, pcm))
            self.transcribe(at, pcm)
        self.transcript.sort()

    def finish(self, cuts=True):
        """Join picture and sound (cutting dead stretches), save and read the slides, write the notes (page, Markdown, PDF)
        and send them to his Telegram and phone. Returns the notes."""
        has_video = os.path.exists(self.path("video.mp4")) and os.path.getsize(self.path("video.mp4")) > 2000
        has_audio = os.path.exists(self.path("audio.pcm")) and os.path.getsize(self.path("audio.pcm")) > 0
        offset = (self.audio_start - self.video_start) if (self.audio_start and self.video_start) else 0.0
        self.offset = offset if has_video else 0.0
        media, saved = join(self, has_video, has_audio, offset, cuts)
        slides = save_slides(self.path("slides"), media) if media and media.endswith(".mp4") and has_video else []
        shown = [{"file": f, "at": to_meeting(self, t), "text": ""} for f, t in distinct(self.path("slides"), slides)]
        notes = write_notes(self)
        with open(self.path("transcript.md"), "w", encoding="utf-8") as f:
            f.write(transcript_md(self))
        write_atomic(self.path("notes.html"), notes_html(self, notes, media, shown, saved))
        write_atomic(self.path("notes.md"), notes_md(self, notes, shown))
        self.delivered = deliver(self, notes)
        if slides:  # reading them takes minutes (Groq's per-minute limit), so it happens after he has the notes
            threading.Thread(target=self.read_later, args=(notes, media, shown, saved)).start()
        if media and os.path.exists(media) and os.path.getsize(media) > 0:
            for name in ("video.mp4", "audio.pcm", "full.mp4", "cuts.txt", "live.html"):
                try:
                    os.remove(self.path(name))
                except OSError:
                    pass
        return notes, media, saved


def plain(s):
    return re.sub(r"[^a-z0-9]+", "", str(s).lower())


def esc(s):
    return html.escape(str(s or ""))


def write_atomic(path, text):
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(path + ".tmp", path)


def transcript_md(r):
    return f"# Transcript: {r.title or 'meeting'}\n\n" + "\n".join(f"[{clock(s)}] {t}" for s, t in sorted(r.transcript))


def whisper(pcm, prompt=""):
    """[(start, end, text)] for one chunk, from Groq Whisper; drops the made-up lines Whisper writes over noise.
    prompt: the title and the last words said, so names and terms are spelled the same way throughout."""
    from openai import OpenAI
    model = ((yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8")) or {}).get("stt", {})
             .get("cloud", {}).get("model", "whisper-large-v3"))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    client = OpenAI(base_url="https://api.groq.com/openai/v1", api_key=os.environ["GROQ_API_KEY"], timeout=60)
    r = client.audio.transcriptions.create(model=model, file=("chunk.wav", buf.getvalue()),
                                           response_format="verbose_json", temperature=0,
                                           **({"prompt": prompt[-400:]} if prompt else {}))
    out = []
    for seg in getattr(r, "segments", None) or []:
        seg = seg if isinstance(seg, dict) else seg.model_dump()
        text = (seg.get("text") or "").strip()
        if (not re.search(r"\w", text) or seg.get("compression_ratio", 0) > 2.4 or seg.get("avg_logprob", 0) < -1.2
                or (seg.get("no_speech_prob", 0) > 0.6 and seg.get("avg_logprob", 0) < -0.8)):
            continue
        if prompt and len(text) > 20 and text.strip(" .") in prompt:
            continue  # Whisper sometimes repeats its prompt over silence
        out.append((float(seg.get("start", 0)), float(seg.get("end", seg.get("start", 0))), text))
    if not out and re.search(r"\w", r.text or "") and not (prompt and len(r.text) > 20 and r.text.strip(" .") in prompt):
        out.append((0.0, len(pcm) / RATE, r.text.strip()))
    if len(out) == 1 and out[0][2].lower().strip(" .!") in PHANTOM:
        return []  # Whisper writes "Thank you." over room noise
    return out


def run(args, timeout=600):
    return subprocess.run([ffmpeg(), "-hide_banner", "-y", *args], capture_output=True, text=True, errors="replace",
                          timeout=timeout, creationflags=NO_WINDOW)


def dead_stretches(r, offset, frames, total):
    """Where nobody talked AND the picture didn't change, on the video's timeline: [(start, end)] to cut."""
    talk = np.zeros(int(total * 10) + 1, bool)
    lv = np.array(r.levels)
    for i in np.nonzero(lv >= SPEECH)[0]:  # each talking block, widened by 1.5 s each side
        a = int((i / 10 + offset - 1.5) * 10)
        talk[max(a, 0):max(a + 31, 0)] = True
    still = np.zeros_like(talk)
    for a, b in zip(frames, frames[1:]):  # mpdecimate dropped every unchanged frame: a long gap = a frozen picture
        if b - a > 1:
            still[int((a + 0.3) * 10):int(b * 10)] = True
    dead, cuts, start = ~talk & still, [], None
    for i, d in enumerate(np.append(dead, False)):
        if d and start is None:
            start = i
        elif not d and start is not None:
            if (i - start) / 10 >= MIN_CUT:
                cuts.append((start / 10 + 1, i / 10 - 1))
            start = None
    return cuts


def join(r, has_video, has_audio, offset, cuts):
    """meeting.mp4 (picture + sound, dead stretches cut) or meeting.m4a; returns (path, seconds saved)."""
    pcm = ["-f", "s16le", "-ar", str(RATE), "-ac", "1"]
    if not has_video and not has_audio:
        return None, 0
    if not has_video:
        out = r.path("meeting.m4a")
        run([*pcm, "-i", r.path("audio.pcm"), "-c:a", "aac", "-b:a", "64k", out])
        return out, 0
    full = r.path("full.mp4")
    if has_audio:
        p = run(["-i", r.path("video.mp4"), "-itsoffset", f"{offset:.3f}", *pcm, "-i", r.path("audio.pcm"),
                 "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "64k", full])
    else:
        p = run(["-i", r.path("video.mp4"), "-c", "copy", full])
    if p.returncode or not os.path.exists(full):
        r.err.append("join: " + p.stderr[-300:])
        return r.path("video.mp4"), 0
    out = r.path("meeting.mp4")
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "packet=pts_time",
                            "-of", "csv=p=0", full], capture_output=True, text=True, creationflags=NO_WINDOW)
    frames = sorted(float(x) for x in probe.stdout.split() if re.match(r"^[\d.]+$", x))
    total = max(frames[-1] if frames else 0, len(r.levels) / 10 + offset)
    dead = dead_stretches(r, offset, frames, total) if cuts and r.levels and frames else []
    saved = sum(b - a for a, b in dead)
    if saved < max(20, 0.05 * total):  # not worth a cut
        os.replace(full, out)
        return out, 0
    keep, t = [], 0.0
    for a, b in dead:
        keep.append((t, a))
        t = b
    keep.append((t, total + 1))
    with open(r.path("cuts.txt"), "w", encoding="utf-8") as f:
        f.write("ffconcat version 1.0\n" + "".join(f"file full.mp4\ninpoint {a:.2f}\noutpoint {b:.2f}\n" for a, b in keep))
    p = run(["-f", "concat", "-safe", "0", "-i", r.path("cuts.txt"), "-c", "copy", out])
    if p.returncode:
        r.err.append("cut: " + p.stderr[-300:])
        os.replace(full, out)
        return out, 0
    r.dead = dead
    return out, saved


def save_slides(folder, media):
    """A picture each time the screen changes a lot (a new slide), at most 40: [(file name, seconds)]."""
    os.makedirs(folder, exist_ok=True)
    p = run(["-i", media, "-vf", "select='eq(n,0)+gt(scene,0.15)',scale='min(960,iw)':-2,showinfo", "-fps_mode", "vfr",
             "-frames:v", "40", "-q:v", "4", os.path.join(folder, "%02d.jpg")])
    times = [float(t) for t in re.findall(r"pts_time:([\d.]+)", p.stderr)]
    names = sorted(f for f in os.listdir(folder) if f.endswith(".jpg"))
    if not names:
        shutil.rmtree(folder, ignore_errors=True)
    return list(zip(names, times + [0.0] * len(names)))


# ---------------------------------------------------------------- the meeting's clock vs the trimmed video
def to_video(r, t):
    """Seconds into the meeting (the sound's clock) -> seconds into meeting.mp4 (the sound starts `offset` after the
    picture, and the dead stretches are cut out)."""
    v, gone = t + r.offset, 0.0
    for a, b in r.dead:
        if v >= b:
            gone += b - a
        elif v > a:
            return max(0.0, a - gone)
    return max(0.0, v - gone)


def to_meeting(r, c):
    """Seconds into meeting.mp4 -> seconds into the meeting."""
    v = c
    for a, b in r.dead:
        if v >= a:
            v += b - a
    return max(0.0, v - r.offset)


def seconds(mmss):
    """"12:34" or "1:02:03" -> seconds; None if it isn't a time."""
    m = re.match(r"^\s*(?:(\d+):)?(\d{1,2}):(\d{2})\s*$", str(mmss or ""))
    return int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) if m else None


# ---------------------------------------------------------------- the private AI for notes and slides
GROQ_NOTES, GROQ_SPARE, GROQ_EYES = "openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"


def ask_ai(messages, max_tokens, vision=False, deadline=240):
    """One private AI call (zero data retention): Groq first. Its free tier allows 8,000 tokens a minute per model, so
    the notes alternate between two models and a 429 waits for the reset. Else servers/openrouter.chat (DeepSeek, if
    OpenRouter has credits)."""
    from openai import OpenAI, RateLimitError
    if groq_ok():
        client = OpenAI(base_url="https://api.groq.com/openai/v1", api_key=os.environ["GROQ_API_KEY"], timeout=120,
                        max_retries=0)
        models = [GROQ_EYES] if vision else [GROQ_NOTES, GROQ_SPARE]
        end, i = time.time() + deadline, 0
        while time.time() < end:
            m = models[i % len(models)]
            try:
                extra = {"reasoning_effort": "low" if m.startswith("openai/gpt-oss") else "none"}
                out = client.chat.completions.create(model=m, messages=messages, max_tokens=max_tokens, **extra)
                return (out.choices[0].message.content or "").strip()
            except RateLimitError as e:
                i += 1
                if i % len(models) == 0:  # both models used up this minute: wait for the reset
                    wait = float(e.response.headers.get("retry-after") or 10)
                    time.sleep(min(max(wait, 2), 30, max(0.0, end - time.time())))
            except Exception:  # noqa: BLE001  (a bad answer or Groq down: the fallback below)
                break
    return chat(None, messages, max_tokens=max_tokens, timeout=120)


# ---------------------------------------------------------------- slides: what was on the screen
SLIDE_SPEC = ("This is a picture of a screen during a meeting or lecture. Write out everything useful on it: the heading, "
              "every line of text and bullet, numbers, tables (short rows), code, and formulas in LaTeX between $ signs; "
              "then one sentence about any diagram, chart or drawing. Plain text, no preamble. If it only shows people's "
              "faces, a video-call grid, a desktop or an empty page, reply NONE.")
MAX_SLIDES = 12  # each one is a vision call of ~2,100 tokens, and Groq allows 8,000 a minute: ~3 minutes for 12
SLIDES_TIME = 420  # reading stops after this many seconds (unread slides are still shown)


def distinct(folder, slides):
    """Drops near-repeats (the same slide with a moved cursor or a changed webcam corner), then keeps at most
    MAX_SLIDES spread over the meeting."""
    from PIL import Image
    keep, seen = [], []
    for f, t in slides:
        try:
            g = np.asarray(Image.open(os.path.join(folder, f)).convert("L").resize((24, 14)), float)
        except OSError:
            continue
        if all(np.abs(g - s).mean() > 6 for s in seen):
            keep.append((f, t))
            seen.append(g)
    if len(keep) > MAX_SLIDES:
        keep = [keep[round(i * (len(keep) - 1) / (MAX_SLIDES - 1))] for i in range(MAX_SLIDES)]
    return keep


def read_slides(r, slides):
    """The slides ({"file", "at", "text"}) with the text the private vision model reads on each; "skip": True when it
    only shows faces or a desktop. "text" stays "" when the model was busy."""
    import base64
    folder = r.path("slides")
    end = time.time() + SLIDES_TIME
    out = []
    for x in slides:
        item, f = dict(x), x["file"]
        if time.time() < end - 5:
            try:
                with open(os.path.join(folder, f), "rb") as fh:
                    b64 = base64.b64encode(fh.read()).decode()
                text = ask_ai([{"role": "user", "content": [
                    {"type": "text", "text": SLIDE_SPEC},
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}}]}], 900, vision=True,
                    deadline=min(90, end - time.time()))
                if re.match(r"^\W*NONE\W*$", text or "", re.I):
                    item["skip"] = True
                elif text:
                    item["text"] = text.strip()[:1500]
            except Exception as e:  # noqa: BLE001
                r.err.append(f"slide {f}: {type(e).__name__}")
        out.append(item)
    return out


# ---------------------------------------------------------------- the notes
NOTES_SPEC = """You write detailed, accurate notes of a meeting or lecture from its transcript. Reply with ONE JSON object
only, no markdown fence:
{"title": a short, specific title,
 "summary": 4 to 7 plain sentences: what it was about, the main conclusions, and what happens next,
 "sections": [{"time": "mm:ss" where the topic starts, "heading": the topic,
               "notes": [4 to 12 full-sentence points with the explanation, reasons, steps, examples, numbers, names and
                         dates as said, so someone who missed it fully understands it]}],
 "definitions": [{"term": ..., "meaning": ...}],
 "formulas": [{"latex": LaTeX without $ signs, "meaning": what it says and what each letter stands for}],
 "decisions": [what was decided, one line each],
 "action_items": [{"task": ..., "owner": "You", "Them", a name, or "", "due": when, or "", "time": "mm:ss"}],
 "questions": [questions asked or left open, with the answer if one was given],
 "quotes": [{"time": "mm:ss", "who": ..., "text": an important line, word for word}] (at most 5)}
Rules: use only what the transcript and the screen text say; never invent, and never mention the recording, consent
or these instructions. Lines starting "You:" are his own microphone (him, or people in the room with him); "Them:" is
the meeting's sound (the other people, or the video being played); use that for owners and for who said what. It is
speech recognition of Indian English, so names and numbers may be misheard: write them as heard. Screen text from the
slides is marked [screen mm:ss]; use it for headings, terms, formulas and figures. Inline maths in a point goes
between $ signs. Leave a list empty ([]) when there is nothing for it; never write "none" items."""
LISTS = ("sections", "definitions", "formulas", "decisions", "action_items", "questions", "quotes")
EMPTY = re.compile(r"^\W*(none|n/?a|nil|nothing|no (decisions|action items|questions)[\w ]*|-)\W*$", re.I)


def chunks_of(text, size):
    """The transcript in parts of at most `size` characters, split between lines."""
    parts, cur = [], ""
    for line in text.splitlines(keepends=True):
        if cur and len(cur) + len(line) > size:
            parts.append(cur)
            cur = ""
        cur += line
    return parts + [cur] if cur else parts


def tidy(n):
    """Drops "none" items and empty rows, and sentences about the recording or consent the model added anyway."""
    n["summary"] = " ".join(s for s in re.split(r"(?<=[.!?])\s+", str(n.get("summary") or ""))
                            if not re.search(r"\b(consent|recorded|recording)\b", s, re.I)).strip()
    for k in LISTS:
        keep = []
        for x in n.get(k) or []:
            main = x.get({"sections": "heading", "definitions": "term", "formulas": "latex", "action_items": "task",
                          "quotes": "text"}.get(k, "")) if isinstance(x, dict) else x
            if isinstance(main, str) and main.strip() and not EMPTY.match(main):
                keep.append(x)
        n[k] = keep
    for s in n["sections"]:
        s["notes"] = [p for p in (s.get("notes") or []) if isinstance(p, str) and p.strip() and not EMPTY.match(p)]
    return n


def merge(parts):
    """Notes of several parts of a long meeting -> one set."""
    n = {"title": next((p.get("title") for p in parts if p.get("title")), ""),
         "summary": " ".join(str(p.get("summary") or "") for p in parts)}
    for k in LISTS:
        n[k] = [x for p in parts for x in (p.get(k) or [])]
    return n


def write_notes(r, slides=()):
    """The final notes: Claude through his subscription; else Groq's gpt-oss-120b, part by part (its free tier allows
    8,000 tokens a minute, so a long meeting is done in ~9,000-character parts) and a summary of the parts."""
    blank = {"title": r.title or "Meeting", "summary": "Nothing was said that I could transcribe.", "by": ""}
    if not r.transcript:
        return {**blank, **{k: [] for k in LISTS}}
    lines = [(s, f"[{clock(s)}] {t}") for s, t in r.transcript]
    lines += [(x["at"], f"[screen {clock(x['at'])}] {x['text']}") for x in slides if x["text"]]
    text = "\n".join(line for _, line in sorted(lines, key=lambda x: x[0]))
    head = f"Meeting title (if given): {r.title or '-'}\n\nTranscript and screen text:\n"

    def part_spec(i, total):
        return NOTES_SPEC + (f"\nThis is part {i + 1} of {total} of a longer meeting: cover only this part, and keep "
                             "its summary to 2 or 3 sentences." if total > 1 else "")

    notes = None
    try:
        parts = chunks_of(text, 120000)
        notes = merge([parse(claude_code.ask(head + p, system=part_spec(i, len(parts)), model="sonnet", timeout=300))
                       for i, p in enumerate(parts)])
        notes["by"] = "Claude"
    except Exception as e:  # noqa: BLE001
        r.err.append(f"claude notes: {e}"[:300])
        parts = chunks_of(text, 9000)
        got = []
        for i, p in enumerate(parts):
            try:
                got.append(parse(ask_ai([{"role": "system", "content": part_spec(i, len(parts))},
                                         {"role": "user", "content": head + p}], 4000)))
            except Exception as e2:  # noqa: BLE001
                r.err.append(f"notes part {i + 1}: {type(e2).__name__}")
        if not got:
            return {**blank, "summary": "I couldn't write the notes (the AI didn't answer). The transcript and "
                    "recording are in the folder.", **{k: [] for k in LISTS}}
        notes = merge(got)
        notes["by"] = "Groq gpt-oss-120b"
    if len(chunks_of(text, 120000 if notes["by"] == "Claude" else 9000)) > 1:  # one summary for the whole meeting
        try:
            notes["summary"] = ask_ai([{"role": "user", "content": (
                "These are the summaries of the parts of one meeting, in order, and its topics. Write one summary of "
                "the whole meeting in 4 to 7 plain sentences: what it was about, the main conclusions, what happens "
                "next. Only the summary.\n\n" + notes["summary"] + "\n\nTopics: " +
                "; ".join(str(s.get("heading")) for s in notes["sections"] if isinstance(s, dict)))}], 1500)
        except Exception:  # noqa: BLE001
            pass
    return tidy(notes)


def parse(text):
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip().replace("�", "-"))
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0) if m else text)


KATEX = os.path.join(ROOT, "ui", "vendor", "katex")


def page(title, body, refresh=0, maths=False):
    meta = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    tex = ""
    if maths and os.path.exists(os.path.join(KATEX, "katex.min.js")):  # formulas, when opened on this PC
        import pathlib
        base = pathlib.Path(KATEX).as_uri()
        tex = (f'<link rel="stylesheet" href="{base}/katex.min.css"><script defer src="{base}/katex.min.js" '
               'onload="texAll()"></script>')
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">{meta}<title>{esc(title)}</title>{tex}
<style>
body{{font:15px/1.65 "Segoe UI",Inter,sans-serif;color:#1f2430;background:#f6f7f9;margin:0}}
main{{max-width:900px;margin:0 auto;padding:32px 28px 60px;background:#fff;min-height:100vh;box-sizing:border-box}}
h1{{font-size:26px;margin:0 0 4px;color:#111827}} h2{{font-size:13px;letter-spacing:.08em;text-transform:uppercase;
color:#4f46e5;margin:30px 0 8px}} h3{{font-size:17px;margin:22px 0 6px;color:#111827}} .meta{{color:#6b7280;font-size:13px;
margin:0}} .t{{color:#4f46e5;font:12px Consolas,monospace;margin-right:6px;text-decoration:none;cursor:pointer}}
a.t:hover{{text-decoration:underline}} .rec{{font-size:13px;color:#dc2626;vertical-align:middle}} ul{{padding-left:20px}}
li{{margin:5px 0}} table{{border-collapse:collapse;width:100%}} td,th{{text-align:left;padding:7px 10px;
border-bottom:1px solid #e5e7eb;vertical-align:top}} th{{font-size:12px;color:#6b7280;font-weight:600}}
video,audio{{width:100%;border-radius:10px;background:#000}} .sticky{{position:sticky;top:0;z-index:2;background:#fff;
padding:8px 0}} .slides{{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:10px;margin:10px 0}}
.slides figure{{margin:0}} .slides img{{width:100%;border-radius:8px;border:1px solid #e5e7eb;cursor:zoom-in}}
.slides figcaption{{font-size:12px;color:#6b7280}} details summary{{cursor:pointer;color:#4f46e5}}
.screen{{white-space:pre-wrap;font-size:12px;color:#374151;background:#f9fafb;border-radius:6px;padding:6px 8px}}
.summary{{font-size:16px}} p{{margin:6px 0}} .toc a{{color:#1f2430}} dl dt{{font-weight:600}} dl dd{{margin:0 0 8px 0}}
.who{{font-weight:600;color:#0f766e}} .who.them{{color:#9333ea}} blockquote{{margin:8px 0;padding:6px 12px;
border-left:3px solid #c7d2fe;color:#374151}} .formula{{margin:8px 0}} .formula .tex{{font-size:17px}}
@media print{{.sticky{{position:static}} details{{display:none}} video,audio{{display:none}}}}
</style></head><body><main>{body}</main>
<script>
function texAll(){{if(!window.katex)return;document.querySelectorAll('.tex').forEach(function(el){{
  var parts=el.textContent.split(/\\$([^$]+)\\$/);if(parts.length<2)return;el.textContent='';
  parts.forEach(function(p,i){{var s=document.createElement('span');
    if(i%2){{try{{katex.render(p,s,{{throwOnError:false}})}}catch(e){{s.textContent='$'+p+'$'}}}}else s.textContent=p;
    el.appendChild(s)}})}})}}
document.addEventListener('click',function(e){{var a=e.target.closest('a.t[data-t]');if(!a)return;e.preventDefault();
  var v=document.querySelector('video,audio');if(!v)return;v.currentTime=parseFloat(a.dataset.t);v.play();
  v.scrollIntoView({{behavior:'smooth',block:'center'}})}});
</script></body></html>"""


def notes_html(r, n, media, slides, saved):
    def at(t):  # a time in the meeting, as a link that plays the recording from there
        if t is None:
            return ""
        if not media:
            return f"<span class=t>{clock(t)}</span>"
        return f'<a class=t href="#" data-t="{to_video(r, t):.1f}">{clock(t)}</a>'

    def items(xs):
        return "<ul>" + "".join(f"<li class=tex>{esc(x)}</li>" for x in xs) + "</ul>" if xs else "<p class=meta>None.</p>"

    def line(t, text):  # "You: ..." / "Them: ..." coloured
        m = re.match(r"^(You|Them): (.*)", text)
        who = f'<span class="who {m.group(1).lower()}">{m.group(1)}:</span> ' if m else ""
        return f"<p>{at(t)}{who}{esc(m.group(2) if m else text)}</p>"

    def slide(x):
        cap = ("" if media else f"{clock(x['at'])} · ") + f"slide {esc(x['file'][:-4])}"
        read = f"<details><summary>What it says</summary><div class=screen>{esc(x['text'])}</div></details>" if x["text"] else ""
        return (f'<figure><a href="slides/{esc(x["file"])}" target=_blank><img src="slides/{esc(x["file"])}" loading=lazy></a>'
                f"<figcaption>{at(x['at']) if media else ''}{cap}</figcaption>{read}</figure>")

    slides = [x for x in slides if not x.get("skip")]
    dur = len(r.levels) / 10 or max((s for s, _ in r.transcript), default=0)
    who = " · You = your mic, Them = the meeting's sound" if r.dev_levels and len(r.dev_levels) > 1 else ""
    body = (f"<h1>{esc(n.get('title') or r.title or 'Meeting')}</h1>"
            f"<p class=meta>{time.strftime('%A %d %B %Y, %H:%M', time.localtime(r.t0))} · {clock(dur)} long · "
            f"notes by {esc(n.get('by'))}{who}</p>"
            f"<h2>Summary</h2><p class='summary tex'>{esc(n.get('summary'))}</p>")
    if media:
        name = os.path.basename(media)
        tag = "video controls preload=metadata" if name.endswith(".mp4") else "audio controls"
        body += (f"<h2>Recording</h2><div class=sticky><{tag} src=\"{esc(name)}\"></{tag.split()[0]}></div>"
                 f"<p class=meta>{esc(name)} · click any time stamp to play from there"
                 f"{f' · {clock(saved)} of silence with a still screen cut out' if saved else ''}</p>")
    acts = [a for a in n["action_items"] if isinstance(a, dict)]
    body += "<h2>Action items</h2>" + ("<table><tr><th>Task</th><th>Who</th><th>When</th><th></th></tr>" + "".join(
        f"<tr><td class=tex>{esc(a.get('task'))}</td><td>{esc(a.get('owner'))}</td><td>{esc(a.get('due'))}</td>"
        f"<td>{at(seconds(a.get('time')))}</td></tr>" for a in acts) + "</table>" if acts else "<p class=meta>None.</p>")
    body += f"<h2>Decisions</h2>{items(n['decisions'])}"
    secs = sorted((s for s in n["sections"] if isinstance(s, dict)), key=lambda s: seconds(s.get("time")) or 0)
    placed = set()
    if secs:
        body += "<h2>Contents</h2><ol class=toc>" + "".join(
            f'<li><a href="#s{i}">{esc(s.get("heading"))}</a> {at(seconds(s.get("time")))}</li>'
            for i, s in enumerate(secs)) + "</ol><h2>Detailed notes</h2>"
        for i, s in enumerate(secs):
            start = seconds(s.get("time")) or 0
            end = seconds(secs[i + 1].get("time")) if i + 1 < len(secs) else None
            here = [x for x in slides if x["at"] >= start - 2 and (end is None or x["at"] < end - 2)
                    and x["file"] not in placed]
            placed |= {x["file"] for x in here}
            body += (f'<h3 id="s{i}">{esc(s.get("heading"))} {at(start)}</h3>' + items(s.get("notes") or []) +
                     (f"<div class=slides>{''.join(slide(x) for x in here)}</div>" if here else ""))
    if n["definitions"]:
        body += "<h2>Definitions</h2><dl>" + "".join(
            f"<dt class=tex>{esc(d.get('term'))}</dt><dd class=tex>{esc(d.get('meaning'))}</dd>"
            for d in n["definitions"] if isinstance(d, dict)) + "</dl>"
    if n["formulas"]:
        body += "<h2>Formulas</h2>" + "".join(
            f"<div class=formula><div class=tex>${esc(f.get('latex'))}$</div><div class='meta tex'>{esc(f.get('meaning'))}</div></div>"
            for f in n["formulas"] if isinstance(f, dict))
    body += f"<h2>Open questions</h2>{items(n['questions'])}"
    if n["quotes"]:
        body += "<h2>Worth quoting</h2>" + "".join(
            f"<blockquote>{at(seconds(q.get('time')))}<b>{esc(q.get('who'))}</b> “{esc(q.get('text'))}”</blockquote>"
            for q in n["quotes"] if isinstance(q, dict))
    rest = [x for x in slides if x["file"] not in placed]
    if rest:
        body += f"<h2>Slides and screen changes</h2><div class=slides>{''.join(slide(x) for x in rest)}</div>"
    body += ("<h2>Transcript</h2><details><summary>Show the full transcript</summary>" +
             "".join(line(s, t) for s, t in sorted(r.transcript)) + "</details>")
    return page(n.get("title") or "Meeting notes", body, maths=True)


def notes_md(r, n, slides=()):
    out = [f"# {n.get('title') or r.title or 'Meeting'}", time.strftime("%A %d %B %Y, %H:%M", time.localtime(r.t0)) +
           f" · notes by {n.get('by')}", "", "## Summary", n.get("summary", ""), "", "## Action items"]
    out += [f"- [ ] {a.get('task')}" + (f" ({a.get('owner')})" if a.get("owner") else "") +
            (f", {a.get('due')}" if a.get("due") else "") for a in n["action_items"] if isinstance(a, dict)] or ["- none"]
    out += ["", "## Decisions"] + ([f"- {x}" for x in n["decisions"]] or ["- none"])
    for s in sorted((s for s in n["sections"] if isinstance(s, dict)), key=lambda s: seconds(s.get("time")) or 0):
        out += ["", f"## {s.get('heading')} ({s.get('time')})"] + [f"- {p}" for p in s.get("notes") or []]
    if n["definitions"]:
        out += ["", "## Definitions"] + [f"- **{d.get('term')}**: {d.get('meaning')}" for d in n["definitions"]
                                         if isinstance(d, dict)]
    if n["formulas"]:
        out += ["", "## Formulas"] + [f"- $${f.get('latex')}$$ {f.get('meaning')}" for f in n["formulas"]
                                      if isinstance(f, dict)]
    out += ["", "## Open questions"] + ([f"- {x}" for x in n["questions"]] or ["- none"])
    if n["quotes"]:
        out += ["", "## Worth quoting"] + [f"> [{q.get('time')}] {q.get('who')}: \"{q.get('text')}\"" for q in n["quotes"]
                                           if isinstance(q, dict)]
    read = [x for x in slides if x["text"]]
    if read:
        out += ["", "## On screen"] + [f"### Slide {x['file'][:-4]} ({clock(x['at'])})\n{x['text']}" for x in read]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- after the meeting
def edge_pdf(src_html, pdf):
    """notes.html -> notes.pdf with headless Edge (its own profile, not his), for Telegram and the phone."""
    import winreg
    edge = None
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            edge = winreg.QueryValue(hive, r"Software\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe")
            break
        except OSError:
            pass
    if not edge or not os.path.exists(edge):
        return None
    profile = os.path.join(os.environ.get("LOCALAPPDATA", ROOT), "Karen", "edge-print")
    subprocess.run([edge, "--headless=new", "--disable-gpu", "--no-first-run", f"--user-data-dir={profile}",
                    "--no-pdf-header-footer", "--virtual-time-budget=4000", f"--print-to-pdf={pdf}",
                    "file:///" + src_html.replace("\\", "/")], capture_output=True, timeout=120, creationflags=NO_WINDOW)
    return pdf if os.path.exists(pdf) else None


def deliver(r, n):
    """config meeting.telegram: the PDF and the summary to his paired Telegram (Karen's bot). meeting.phone_note: a
    note with the summary and action items in data/notes.json, which syncs to the phone app. Returns what was done."""
    cfg = ((yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8")) or {}).get("meeting") or {})
    done = []
    acts = [a for a in n["action_items"] if isinstance(a, dict)]
    todo = "; ".join(f"{a.get('task')}" + (f" ({a.get('owner')})" if a.get("owner") else "") +
                     (f", {a.get('due')}" if a.get("due") else "") for a in acts)
    if cfg.get("phone_note", True):
        try:
            path = os.path.join(ROOT, "data", "notes.json")
            try:
                data = json.load(open(path, encoding="utf-8"))
            except (OSError, ValueError):
                data = {"todos": [], "notes": []}
            text = f"Meeting notes: {n.get('title')} ({time.strftime('%d %b %H:%M', time.localtime(r.t0))}). {n.get('summary')}"
            data.setdefault("notes", []).append({"text": text + (f" Action items: {todo}." if todo else ""),
                                                 "at": time.strftime("%Y-%m-%d %H:%M")})
            write_atomic(path, json.dumps(data, ensure_ascii=False, indent=1))
            done.append("a note for your phone")
        except Exception as e:  # noqa: BLE001
            r.err.append(f"note: {type(e).__name__}")
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    try:
        owners = json.load(open(os.path.join(ROOT, "data", "remote.json"), encoding="utf-8")).get("telegram", [])
    except (OSError, ValueError):
        owners = []
    if cfg.get("telegram", True) and token and owners:
        try:
            pdf = edge_pdf(r.path("notes.html"), r.path("notes.pdf")) or r.path("notes.md")
            sys.path.insert(0, ROOT)
            from core.telegram import Telegram
            bot = Telegram(token)
            caption = f"📝 {n.get('title')}\n\n{n.get('summary')}" + (f"\n\n✅ {todo}" if todo else "")
            for chat_id in owners:
                bot.send_file(chat_id, pdf, caption[:1000])
            done.append("the PDF to your Telegram")
        except Exception as e:  # noqa: BLE001  (not repr: it could carry the bot URL)
            r.err.append(f"telegram: {type(e).__name__}")
    return done


def folder_for(title):
    name = time.strftime("%Y-%m-%d %H%M") + (" " + re.sub(r'[<>:"/\\|?*]+', "", title).strip()[:50] if title else "")
    path, n = os.path.join(OUT, name.strip()), 2
    while os.path.exists(path):  # two meetings in the same minute
        path, n = os.path.join(OUT, f"{name.strip()} ({n})"), n + 1
    return path


def recover():
    """Karen closed while a meeting was being recorded: finish it from the files (no cuts: the levels were in memory)."""
    st = load_state()
    if not st.get("on"):
        return
    save_state(on=False)
    folder = st.get("folder", "")
    if not os.path.isdir(folder):
        return
    r = Recorder(folder, st.get("title", ""))
    r.t0 = st.get("since", r.t0)
    try:
        with open(r.path("transcript.md"), encoding="utf-8") as f:
            for line in f:
                m = re.match(r"\[(?:(\d+):)?(\d+):(\d+)\] (.*)", line)
                if m:
                    h, mm, ss, text = m.groups()
                    r.transcript.append((int(h or 0) * 3600 + int(mm) * 60 + int(ss), text))
    except OSError:
        pass
    try:
        r.finish(cuts=False)
    except Exception as e:
        print("recover failed:", repr(e), file=sys.stderr)


# ---------------------------------------------------------------- tools
@mcp.tool()
def start_meeting_notes(title: str = "") -> str:
    """Start taking meeting notes: records the meeting (the Zoom / Teams / Meet window, the meeting sound and his mic),
    transcribes it live and writes notes. ALWAYS call this when he says take meeting notes / record this meeting.
    title: optional, e.g. "physics lecture"."""
    global rec
    if rec and not rec.stopping.is_set():
        return f"Already taking meeting notes (for {clock(time.time() - rec.t0)}). Say stop taking meeting notes to finish."
    rec = Recorder(folder_for(title), title)
    rec.start()
    time.sleep(1.5)  # let the sound devices open, so the reply can say what is recorded
    first = not load_state().get("told")
    save_state(on=True, since=rec.t0, folder=rec.folder, title=title, source=rec.source, told=True)
    said = f"Taking meeting notes. I'm recording {rec.source}, with {rec.sound or 'the sound'}."
    if first:
        said += " Only record meetings where everyone's okay with it."
    rec.own = said
    return (said + "\nSay \"stop taking meeting notes\" when it ends: the final notes then take about a minute. "
            f"Live notes: show meeting notes. Folder: {rec.folder}")


@mcp.tool()
def stop_meeting_notes() -> str:
    """Stop taking meeting notes and make the final detailed notes (summary, topic-by-topic notes, decisions, action
    items, slides read), the trimmed recording and a PDF, send them to his Telegram and phone, then open them. Takes
    one to three minutes."""
    global rec
    if not rec or rec.stopping.is_set():
        return "Meeting notes aren't running. Say start taking meeting notes to begin one."
    r = rec
    r.stop()
    save_state(on=False)
    try:
        notes, media, saved = r.finish()
    finally:
        rec = None
    acts, decs = len(notes["action_items"]), len(notes["decisions"])
    said = (f"Meeting notes are ready: {acts} action item{'s' if acts != 1 else ''} and {decs} decision"
            f"{'s' if decs != 1 else ''}. They're open in the viewer"
            f"{', and the PDF is on your Telegram' if any('Telegram' in d for d in r.delivered) else ''}.")
    if not r.transcript:
        said = "I stopped the recording, but I couldn't hear anything to take notes from. It's saved in the folder."
    lines = [said, f"Title: {notes.get('title')}", f"Summary: {notes.get('summary')}"]
    lines += [f"Action: {a.get('task')} ({a.get('owner') or 'no owner'}{', ' + a['due'] if a.get('due') else ''})"
              for a in notes["action_items"] if isinstance(a, dict)]
    lines += [f"Decision: {d}" for d in notes["decisions"]]
    if saved:
        lines.append(f"Cut {clock(saved)} of silence with a still screen from the recording.")
    if r.capped:
        lines.append(f"The recording stopped by itself after {MAX_HOURS} hours.")
    if r.failed:
        lines.append(f"{len(r.failed)} parts couldn't be transcribed (Groq didn't answer); the recording has them.")
    if r.delivered:
        lines.append("Also sent: " + " and ".join(r.delivered) + ".")
    lines.append(f"Folder: {r.folder}")
    lines.append(f"[[show: {r.path('notes.html')}]]")
    return "\n".join(lines)


@mcp.tool()
def meeting_notes_so_far(question: str = "") -> str:
    """The live notes and the latest transcript of the meeting being recorded (or the last meeting's notes), to answer
    what was said, e.g. "what did she say about the deadline". question: what he asked."""
    if rec and not rec.stopping.is_set():
        notes = "\n".join(f"- [{clock(s)}] {b}" for s, b in rec.bullets) or "(no live notes yet)"
        said = "\n".join(f"[{clock(s)}] {t}" for s, t in rec.transcript)[-6000:] or "(nothing transcribed yet)"
        return (f"Recording for {clock(time.time() - rec.t0)} ({rec.source}). Live notes:\n{notes}\n\nLatest transcript "
                f"(speech recognition, may mishear):\n{said}\n\nAnswer his question briefly from this: {question}")
    last = last_meeting()
    if not last:
        return "No meeting notes yet."
    try:
        with open(os.path.join(last, "notes.md"), encoding="utf-8") as f:
            return f"Not recording now. The last meeting's notes ({os.path.basename(last)}):\n{f.read()[:6000]}"
    except OSError:
        return f"Not recording now. The last meeting is in {last}, without finished notes."


@mcp.tool()
def show_meeting_notes() -> str:
    """Open the meeting notes in the viewer: the live notes while recording, else the last meeting's notes."""
    if rec and not rec.stopping.is_set():
        rec.write_live()
        return f"Live notes ({clock(time.time() - rec.t0)} so far) are open.\n[[show: {rec.path('live.html')}]]"
    last = last_meeting()
    if last and os.path.exists(os.path.join(last, "notes.html")):
        return f"The last meeting's notes are open.\n[[show: {os.path.join(last, 'notes.html')}]]"
    return "No meeting notes yet. Say start taking meeting notes during a meeting."


STOP_WORDS = {"the", "a", "an", "meeting", "meetings", "notes", "of", "in", "on", "about", "what", "did", "we", "my",
              "last", "from", "and", "for", "with", "was", "is", "it", "that", "say", "said", "lecture", "class", "call"}


@mcp.tool()
def past_meetings(query: str = "") -> str:
    """Find a past meeting's notes, to answer questions like "what did we decide in Monday's meeting" or "the physics
    lecture last week". query: words about it (title, topic, day or date); empty = the latest few."""
    if not os.path.isdir(OUT):
        return "No meeting notes yet."
    words = [w for w in re.findall(r"[a-z0-9]+", query.lower()) if w not in STOP_WORDS]
    days = {"today": time.strftime("%Y%m%d"), "yesterday": time.strftime("%Y%m%d", time.localtime(time.time() - 86400))}
    found = []
    for name in os.listdir(OUT):
        f = os.path.join(OUT, name)
        try:
            with open(os.path.join(f, "notes.md"), encoding="utf-8") as fh:
                notes = fh.read()
        except OSError:
            continue
        t = os.path.getmtime(f)
        when = time.strftime("%A %d %B %Y %b %a", time.localtime(t)).lower()
        when += "".join(" " + d for d, ymd in days.items() if time.strftime("%Y%m%d", time.localtime(t)) == ymd)
        head, low = notes[:600].lower(), notes.lower()
        score = sum(3 * (w in name.lower() or w in when) + 2 * (w in head) + (w in low) for w in words)
        found.append((score, t, name, notes))
    if not found:
        return "No finished meeting notes yet."
    if not words:
        best = sorted(found, key=lambda x: -x[1])[:3]
    else:
        found.sort(key=lambda x: (x[0], x[1]), reverse=True)
        if found[0][0] == 0:
            return "No meeting matched that. The latest ones: " + "; ".join(
                n for _, _, n, _ in sorted(found, key=lambda x: -x[1])[:5])
        best = found[:1]
    size = 6000 if words else 2500
    return ("\n\n".join(f"Meeting folder: {n}\n{notes[:size]}" for _, _, n, notes in best) +
            "\n\nAnswer his question from these notes.")


@mcp.tool()
def meeting_followup_email(meeting: str = "") -> str:
    """The notes of a meeting, to write a follow-up email (thanks, summary, decisions, action items with owners and
    dates). meeting: which one (words, like past_meetings); empty = the last meeting. Then draft it with
    draft_gmail_message; send only if he asks to."""
    if meeting:
        notes = past_meetings(meeting)
        if notes.startswith("No "):
            return notes
    else:
        try:
            with open(os.path.join(last_meeting() or "", "notes.md"), encoding="utf-8") as f:
                notes = f.read()[:6000]
        except OSError:
            return "The last meeting has no finished notes yet."
    return (notes + "\n\nWrite a short follow-up email from these notes: a one-line thanks, a 2-3 sentence summary, the "
            "decisions, and the action items with who and when. Plain and friendly, nothing made up. If he didn't say "
            "who it goes to, ask. Save it as a draft (draft_gmail_message) and tell him; send only when he says send.")


def last_meeting():
    if not os.path.isdir(OUT):
        return None
    folders = sorted((os.path.join(OUT, f) for f in os.listdir(OUT)), key=os.path.getmtime, reverse=True)
    return next((f for f in folders if os.path.isdir(f)), None)


if __name__ == "__main__":
    threading.Thread(target=recover, daemon=True).start()
    mcp.run()
