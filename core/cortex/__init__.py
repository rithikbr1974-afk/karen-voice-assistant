"""Karen's Cortex: a local, brain-inspired network that runs on everything she hears (docs/CORTEX.md).

Senses and hippocampus: an ear listens on the same mic stream while Karen waits for her wake word, transcribes what
it hears locally (tiny.en; overheard speech never goes to Groq) and stores it as episodes, so "what did that video
just say?" works (servers/brain). Requests to Karen are stored too, with their context and the attention verdict:
they are what the attention region learns from.
Attention (thalamus + prefrontal cortex, attention.py): "was that said to me?" for every spoken request.
"""
import os
import queue
import threading
import time

import numpy as np

from core.audio import CHUNK, RATE, Vad
from core.cortex.memory import Memory
from core.cortex.senses import Voice, Words, acoustics, playing


class Ear(threading.Thread):
    """Cuts the tapped mic stream into utterances, like Mic.listen, and hands each one on. Anything that overlaps
    a time Karen was busy (listening to a request, thinking, talking, muted) is dropped."""

    def __init__(self, mic, on_utterance, idle, threshold=0.5, silence_ms=700, min_speech_ms=400, max_s=15):
        super().__init__(daemon=True)
        self.q, self.on_utterance, self.idle = mic.tap(), on_utterance, idle
        self.vad = Vad("models/silero_vad.onnx")
        self.threshold = threshold
        self.silence = silence_ms * RATE // 1000 // CHUNK
        self.min_chunks = min_speech_ms * RATE // 1000 // CHUNK
        self.max_chunks = max_s * RATE // CHUNK

    def run(self):
        speech, silent, voiced, clean = [], 0, 0, True
        while True:
            chunk = self.q.get()
            if not self.idle():
                speech, clean = [], False
                self.vad.reset()
                continue
            is_speech = self.vad(chunk) >= self.threshold
            if not speech:
                if is_speech:
                    speech, silent, voiced, clean = [chunk], 0, 1, True
                continue
            speech.append(chunk)
            voiced, silent = (voiced + 1, 0) if is_speech else (voiced, silent + 1)
            if silent >= self.silence or len(speech) >= self.max_chunks:
                if clean and voiced >= self.min_chunks:
                    self.on_utterance(np.concatenate(speech))
                speech = []


class Cortex:
    def __init__(self, cfg, log):
        from core.stt import STT
        self.cfg, self.log = cfg or {}, log
        self.voice, self.words = Voice(), Words()
        self.memory = Memory(text_hours=self.cfg.get("text_hours", 24), days=self.cfg.get("days", 14),
                             audio_mb=self.cfg.get("audio_mb", 300))
        self.stt = STT(self.cfg.get("ear_model", "tiny.en"), "int8", threads=2)  # local only, never the cloud
        self.inbox = queue.Queue(maxsize=12)  # utterances waiting for the ear's worker; more are dropped
        self.idle = lambda: True
        self.heard_count, self.pruned = 0, 0.0
        self.mode = self.cfg.get("attention", "shadow")  # shadow = decide and show, but still answer | on | off
        self.attention = None
        if self.mode != "off" and os.path.exists("models/cortex/attention.npz"):
            from core.cortex.attention import Attention
            self.attention = Attention()
        if self.voice.me is None:
            threading.Thread(target=self._enroll, daemon=True).start()

    def _enroll(self):
        n = self.voice.enroll()
        self.log("cortex", f"voice profile from {n} of his recordings" if n else "no recordings in voice_tests/ "
                 "for a voice profile (scripts/record_samples.py)")

    def start_ear(self, mic, idle):
        self.idle = idle
        Ear(mic, self._queue, idle).start()
        threading.Thread(target=self._work, daemon=True).start()
        self.log("cortex", "ear on: hearing memory keeps overheard text "
                 f"{self.memory.text_hours} h, clips {self.memory.days} days (data/cortex)")

    def _queue(self, audio):
        try:
            self.inbox.put_nowait((time.time() - len(audio) / RATE, audio, playing()))
        except queue.Full:
            pass  # a long stretch of TV: skip some rather than fall behind

    def _work(self, pack_s=24, wait_s=15):
        """Whisper encodes a 30 s window whatever the length, so overheard utterances are packed into one window
        (pack_s of speech, or whatever waited wait_s, or at once when Karen is woken) and transcribed together."""
        pending = []
        while True:
            try:
                pending.append(self.inbox.get(timeout=0.5))
            except queue.Empty:
                pass
            full = sum(len(p[1]) for p in pending) >= pack_s * RATE
            if pending and (full or time.time() - pending[0][0] > wait_s or not self.idle()):
                try:
                    self.hear(pending)
                except Exception as e:
                    self.log("cortex", f"ear: {e!r}")
                pending = []
            if time.time() - self.pruned > 3600:
                self.pruned = time.time()
                self.memory.prune()

    def senses(self, audio, text, apps=None):
        voice = self.voice.embed(audio) if audio is not None else None
        return {"words": self.words.embed(text), "voice": voice, "me": self.voice.similarity(voice),
                "acoustics": acoustics(audio) if audio is not None else None,
                "playing": playing() if apps is None else apps}

    def transcribe(self, clips, gap_s=0.8):
        """One Whisper pass over several utterances joined by short silences; each segment goes back to the
        utterance its middle falls in. Returns one text per clip."""
        gap = np.zeros(int(gap_s * RATE), np.float32)
        spans, parts, at = [], [], 0.0
        for a in clips:
            spans.append((at, at + len(a) / RATE))
            parts += [a, gap]
            at += (len(a) + len(gap)) / RATE
        segments, _ = self.stt.model.transcribe(np.concatenate(parts), language="en", beam_size=1,
                                                condition_on_previous_text=False, vad_filter=False)
        texts = [[] for _ in clips]
        for s in segments:
            if self.stt.made_up(s):
                continue
            mid = (s.start + s.end) / 2
            i = min(range(len(spans)), key=lambda j: 0 if spans[j][0] <= mid <= spans[j][1]
                    else min(abs(mid - spans[j][0]), abs(mid - spans[j][1])))
            texts[i].append(s.text.strip())
        return [" ".join(t).strip() for t in texts]

    def hear(self, pending):
        """Things said while Karen wasn't being spoken to: [(when it started, audio, apps playing), ...]."""
        clips = [p[1] for p in pending]
        for (t, audio, apps), text in zip(pending, self.transcribe(clips)):
            if not any(ch.isalpha() for ch in text):
                continue
            self.memory.store("heard", text, audio=audio, t=round(t, 2), **self.senses(audio, text, apps))
            self.heard_count += 1
            if self.heard_count % 50 == 0:  # never the words themselves: karen.log is kept for good
                self.log("cortex", f"ear: {self.heard_count} things heard since start")

    def attend(self, text, how, wake_score, karen_last, karen_t, t=None):
        """Thalamus + prefrontal cortex on a spoken request. how: wake | followup | button. karen_last / karen_t:
        her last reply and when it ended. Returns {p, threshold, verdict, heard} or None without a trained model."""
        if not self.attention:
            return None
        from core.cortex.attention import context
        t = t or time.time()
        gap = t - karen_t if karen_t else None
        prev = karen_last if gap is not None and gap < 600 else ""
        asked = bool(karen_last and karen_last.rstrip().endswith("?"))
        inputs = {"words": self.words.embed(text), "prev": self.words.embed(prev) if prev else None,
                  "context": context(how, wake_score, gap, asked, t, text)}
        p, heard = self.attention(inputs, t)
        thr = self.attention.threshold
        return {"p": round(p, 3), "threshold": round(thr, 3), "verdict": "answer" if p >= thr else "ignore",
                "heard": heard, "how": how, "wake_score": round(wake_score, 2), "gap_karen": gap,
                "karen_prev": prev, "karen_asked": asked}

    def request(self, text, audio=None, **context):
        """A request that reached Karen (spoken after the wake word, or typed). Stored in the background so the
        answer never waits for the voice model. context: how it arrived and the attention verdict. Returns its
        episode id."""
        eid = self.memory.new_id()

        def store():
            try:
                self.memory.store("request" if audio is not None else "typed", text, audio=audio, eid=eid,
                                  **context, **self.senses(audio, text))
            except Exception as e:
                self.log("cortex", f"request not stored: {e!r}")
        threading.Thread(target=store, daemon=True).start()
        self.last_request = eid
        return eid

    def not_for_me(self):
        """He said "that wasn't for you": the last request becomes a labelled example for training."""
        eid = getattr(self, "last_request", None)
        if eid:
            time.sleep(0.5)  # its store() thread may still be writing
            self.memory.label(eid, addressed=0)
        return eid
