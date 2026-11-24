"""Microphone input cut into utterances by Silero VAD (ONNX, CPU)."""
import queue
import threading
import time

import numpy as np
import onnxruntime as ort
import sounddevice as sd

RATE = 16000
CHUNK = 512  # Silero needs exactly 512 samples at 16 kHz (32 ms)


class Interrupted(Exception):
    """Raised by Mic.read/listen when the UI needs the loop (push-to-talk, typed command, mute)."""


class Vad:
    def __init__(self, model_path):
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = opts.intra_op_num_threads = 1
        self.session = ort.InferenceSession(model_path, opts, providers=["CPUExecutionProvider"])
        self.sr = np.array(RATE, dtype=np.int64)
        self.reset()

    def reset(self):
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.context = np.zeros((1, 64), dtype=np.float32)

    def __call__(self, chunk):
        x = np.concatenate([self.context, chunk[None, :]], axis=1)
        out, self.state = self.session.run(None, {"input": x, "state": self.state, "sr": self.sr})
        self.context = x[:, -64:]
        return float(out[0][0])


class Ducker:
    """Turns other apps' sound down while Karen listens and talks (music drowned the user out), then back up."""

    STATE = "data/duck_state.json"  # {program name: its volume before ducking}: survives a kill, /stop or shutdown

    def __init__(self, level=0.15):
        self.level = level  # fraction of each app's volume to keep; 1 = off
        self.saved = {}

    @staticmethod
    def _sessions():
        import os
        import comtypes
        from pycaw.pycaw import AudioUtilities
        comtypes.CoInitialize()
        for s in AudioUtilities.GetAllSessions():
            if s.ProcessId and s.ProcessId != os.getpid() and s.SimpleAudioVolume:
                try:
                    yield s.Process.name().lower(), s.SimpleAudioVolume
                except Exception:
                    continue  # the program closed while we looked

    def duck(self):
        if self.saved or self.level >= 1:
            return
        try:
            self.recover()  # never duck something that an earlier, interrupted run left turned down
            for name, vol in self._sessions():
                before = vol.GetMasterVolume()
                self.saved[name] = (vol, before)
                vol.SetMasterVolume(before * self.level, None)
            self._write({n: b for n, (_, b) in self.saved.items()})
        except Exception:
            pass  # ducking is a nicety; never let it break listening

    def restore(self):
        for vol, before in self.saved.values():
            try:
                vol.SetMasterVolume(before, None)
            except Exception:
                pass  # that app closed meanwhile
        self.saved.clear()
        self._write({})

    @classmethod
    def recover(cls):
        """Put back the volumes of an interrupted run (killed, /stop, power loss): called at start-up, before every
        duck, and by remote.py's /stop. Returns how many programs were put back."""
        import json
        import os
        try:
            old = json.load(open(cls.STATE, encoding="utf-8"))
        except (OSError, ValueError):
            return 0
        n = 0
        try:
            for name, vol in cls._sessions():
                if name in old:
                    vol.SetMasterVolume(min(1.0, float(old[name])), None)
                    n += 1
        except Exception:
            pass
        try:
            os.remove(cls.STATE)
        except OSError:
            pass
        return n

    def _write(self, state):
        import json
        import os
        try:
            if state:
                os.makedirs(os.path.dirname(self.STATE), exist_ok=True)
                json.dump(state, open(self.STATE, "w", encoding="utf-8"))
            elif os.path.exists(self.STATE):
                os.remove(self.STATE)
        except OSError:
            pass


class Mic:
    def __init__(self, vad_model, device=None, threshold=0.5, silence_ms=700,
                 min_speech_ms=250, max_utterance_s=15, pre_roll_ms=300):
        self.vad = Vad(vad_model)
        self.threshold = threshold
        self.silence_chunks = silence_ms * RATE // 1000 // CHUNK
        self.min_chunks = min_speech_ms * RATE // 1000 // CHUNK
        self.max_chunks = int(max_utterance_s * RATE) // CHUNK
        self.pre_roll = pre_roll_ms * RATE // 1000 // CHUNK
        self.q = queue.Queue()
        self.taps = []  # extra listeners on the same stream (the Cortex's ear)
        self.level = 0.0  # peak of the latest chunk, for the UI
        self.interrupt = threading.Event()
        self.finish = threading.Event()  # the island's ✓: stop listening now and use what was said so far
        self.stream = sd.InputStream(samplerate=RATE, channels=1, dtype="float32",
                                     blocksize=CHUNK, device=device, callback=self._callback)
        self.stream.start()

    def _callback(self, indata, frames, time_info, status):
        chunk = indata[:, 0].copy()
        self.level = float(np.abs(chunk).max())
        self.q.put(chunk)
        for tap in self.taps:
            try:
                tap.put_nowait(chunk)
            except queue.Full:
                pass  # that listener fell behind; the wake word loop never waits for it

    def tap(self):
        """A second queue that gets every chunk too."""
        q = queue.Queue(maxsize=500)  # 16 s
        self.taps.append(q)
        return q

    def flush(self):
        """Drop audio captured while Karen was talking or thinking."""
        while not self.q.empty():
            self.q.get_nowait()
        self.vad.reset()

    def read(self):
        """Next raw 512-sample chunk (for streaming wake word detection)."""
        while True:
            if self.interrupt.is_set():
                raise Interrupted
            try:
                chunk = self.q.get(timeout=0.2)
            except queue.Empty:
                continue
            if len(chunk) == CHUNK:
                return chunk

    def unread(self, chunks):
        """Put chunks back at the front of the queue: the wake word's voice check reads ~0.6 s past the word, and
        that audio may be the start of his request."""
        with self.q.mutex:
            self.q.queue.extendleft(reversed(chunks))
            self.q.not_empty.notify()

    def listen(self, timeout=None):
        """Block until one utterance is spoken. Returns float32 audio, or None on timeout."""
        start = time.time()
        before, speech, silent, voiced = [], [], 0, 0
        self.finish.clear()
        while True:
            if self.interrupt.is_set():
                raise Interrupted
            if self.finish.is_set():
                self.finish.clear()
                return np.concatenate(speech) if speech and voiced >= self.min_chunks else None
            try:
                chunk = self.q.get(timeout=0.2)
            except queue.Empty:
                chunk = None
            if not speech and timeout and time.time() - start > timeout:
                return None
            if chunk is None or len(chunk) != CHUNK:
                continue
            is_speech = self.vad(chunk) >= self.threshold
            if not speech:
                before = (before + [chunk])[-self.pre_roll:]
                if is_speech:
                    speech = before
                    voiced, silent = 1, 0
                continue
            speech.append(chunk)
            if is_speech:
                voiced += 1
                silent = 0
            else:
                silent += 1
            if silent >= self.silence_chunks or len(speech) >= self.max_chunks:
                if voiced >= self.min_chunks:
                    return np.concatenate(speech)
                before, speech = [], []  # too short (click, cough) - keep waiting

    def close(self):
        self.stream.stop()
        self.stream.close()
