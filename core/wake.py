"""Wake word detectors. Both have wait(mic) -> utterance containing the wake word, or None.

OwwWake: custom openWakeWord "Karen" model (streaming, low CPU), optional per-voice verifier.
WhisperWake: fallback - transcribe each utterance with a small Whisper model and look for "Karen".
PersonalWake: classifier trained on the user's own "Karen", plus VoiceGate (only his voice wakes her).
"""
import os
import re
import time
from difflib import SequenceMatcher

import numpy as np


class OwwWake:
    FRAME = 2560  # 160 ms; openWakeWord works in multiples of 1280 samples

    def __init__(self, model, threshold=0.5, verifier=None, verifier_threshold=0.3, feature_dir="models/oww"):
        from openwakeword.model import Model
        self.name = model.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
        verifiers = {self.name: verifier} if verifier else {}
        self.model = Model(wakeword_models=[model], inference_framework="onnx",
                           melspec_model_path=f"{feature_dir}/melspectrogram.onnx",
                           embedding_model_path=f"{feature_dir}/embedding_model.onnx",
                           custom_verifier_models=verifiers, custom_verifier_threshold=verifier_threshold)
        self.threshold = threshold

    def wait(self, mic, log=None):
        """Block until the wake word is heard. Returns None (the request comes after it)."""
        self.model.reset()
        buf = []
        while True:
            buf.append(mic.read())
            if sum(len(c) for c in buf) < self.FRAME:
                continue
            frame = (np.concatenate(buf) * 32767).astype(np.int16)
            buf = []
            score = self.model.predict(frame)[self.name]
            if score >= self.threshold:
                if log:
                    log("wake", f"score {score:.2f}")
                self.last_score = float(score)
                return None


class VoiceGate:
    """Is the voice that just said the wake word the user's? (2026-11-07, his "it should only activate for my voice,
    not other sounds or media".) WeSpeaker embedding of the ~2 s that woke her (the Cortex's Voice model), mean cosine
    to his 3 closest recordings (`models/cortex/wake_voice.npy`, built from voice_tests by `build()`). Measured: his
    unseen session-2 "Karen"s streamed like live (over music, across the room...) at least 0.69; the 39 saved false
    wakes the detector still fires on (TV, videos, music, other people) at most 0.49. ~70 ms per check."""
    PROFILE = "models/cortex/wake_voice.npy"

    def __init__(self, threshold=0.58, shared=None, log=None):
        """shared: a callable that returns an already loaded Voice (the Cortex's) or None; else it loads its own."""
        self.threshold, self.shared, self._voice, self.log, self.last = threshold, shared, None, log, None
        self.me = np.load(self.PROFILE) if os.path.exists(self.PROFILE) else None
        if self.me is None and log:
            log("wake", f"voice check off: {self.PROFILE} missing (python scripts/build_wake_voice.py)")

    def voice(self):
        if self._voice is None and self.shared:
            self._voice = self.shared()
        if self._voice is None:
            from core.cortex.senses import Voice
            self._voice = Voice()
        return self._voice

    def score(self, audio):
        """Similarity to his voice, or None when it can't judge (no profile, too short)."""
        if self.me is None or audio is None:
            return None
        emb = self.voice().embed(audio)
        return None if emb is None else float(np.sort(self.me @ emb)[-3:].mean())

    AFTER = 19  # chunks (~0.6 s) read past the moment the detector fires: it fires before "Karen" has ended, and
    # the 2 s it holds then is mostly room sound. Measured on his session-2 clips streamed like live: the window alone
    # gave him 0.29-0.92 over music (lowest 0.42 in a quiet room); with 0.6 s more, lowest 0.69, over music 0.83-0.87;
    # false wakes stayed at most 0.49.

    # Loudest 0.1 s (rms) below this = nobody spoke near the mic. On 2026-11-09 the detector fired 18 times on faint room
    # sound (0.0004-0.007). His live calls were at least 0.042, his softest recorded "Karen" (across the room) 0.021.
    QUIET = 0.01
    AGAIN = (10, 0.25)  # a second call within 10 s that scores at least 0.25 goes through: he's calling again

    def his(self, recent, mic):
        """True unless it's clearly someone (or something) else (if it can't judge, the wake goes through).
        Reads ~0.6 s more from the mic and puts it back, so a request said straight after "Karen" isn't lost.
        Live, his calls score lower than his recordings (2026-11-09: 0.31-0.54 calling across the room, while the
        recordings gave 0.69+), and the TV 0.06-0.28. So the cut-off is ~0.3 (config wake.voice_gate), silence is
        refused on its loudness, and a repeated call gets through on a lower score."""
        more = []
        try:
            for _ in range(self.AFTER):
                more.append(mic.read())
        finally:
            if hasattr(mic, "unread"):
                mic.unread(more)
        self.clip = np.concatenate([recent] + more)
        return self.judge(self.clip)

    def judge(self, audio, now=None):
        now = time.time() if now is None else now
        self.why = ""
        frames = audio[:len(audio) // 1600 * 1600].reshape(-1, 1600)
        if not len(frames) or float(np.sqrt((frames ** 2).mean(1)).max()) < self.QUIET:
            self.last, self.why = None, "too quiet"
            return False
        s = self.last = self.score(audio)
        if s is None or s >= self.threshold:
            return True
        if s < self.AGAIN[1]:
            return False  # clearly someone else
        again = now - getattr(self, "missed", 0) <= self.AGAIN[0]
        self.missed = now
        if again:
            self.why = "called again"
        return again

    @staticmethod
    def build(folder="voice_tests"):
        """His recordings (both sessions: "Karen"s, commands, other sentences, test phrases), never the room/music
        clips or the false wakes."""
        import glob
        from core.cortex.memory import read_wav
        from core.cortex.senses import Voice
        voice = Voice()
        paths = [p for pat in ("*.wav", "karen/*.wav", "other/*.wav", "session*/karen/*.wav", "session*/commands/*.wav",
                               "session*/other/*.wav") for p in glob.glob(os.path.join(folder, pat))
                 if "room" not in os.path.basename(p).lower()]
        embs = [e for e in (voice.embed(read_wav(p)) for p in paths) if e is not None]
        np.save(VoiceGate.PROFILE, np.stack(embs))
        return len(embs)


def save_clip(audio, folder, keep=100):
    """A wake clip as 16 kHz WAV in folder (the newest `keep` are kept)."""
    import datetime
    import glob
    import wave
    os.makedirs(folder, exist_ok=True)
    with wave.open(os.path.join(folder, f"{datetime.datetime.now():%Y%m%d-%H%M%S}.wav"), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(16000)
        f.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
    for old in sorted(glob.glob(os.path.join(folder, "*.wav")))[:-keep]:
        os.remove(old)


class PersonalWake:
    """Detector trained on the user's own "Karen" (scripts/train_wake.py) over openWakeWord speech embeddings."""
    FRAME = 2560  # 160 ms

    def __init__(self, model_path, threshold=None, feature_dir="models/oww", patience=None):
        import pickle
        from openwakeword.utils import AudioFeatures
        with open(model_path, "rb") as f:
            saved = pickle.load(f)
        self.model = saved["model"]
        # the slider may raise it, but not drop it far below the trained value: at 0.4 music and talk woke her
        self.floor = round(saved["threshold"] - 0.1, 2)
        self.threshold = max(threshold if threshold is not None else saved["threshold"], self.floor)
        self.patience = patience or saved.get("patience", 2)  # hits in a row needed (config wake.patience)
        self.last_audio = None  # the ~2 s that woke her, saved by Karen when nothing followed (false wake)
        self.gate = None  # VoiceGate, set by Karen: only his voice wakes her
        self.features = AudioFeatures(melspec_model_path=f"{feature_dir}/melspectrogram.onnx",
                                      embedding_model_path=f"{feature_dir}/embedding_model.onnx",
                                      inference_framework="onnx")

    def score(self):
        return float(self.model.predict_proba(self.features.get_features(16).reshape(1, -1))[0, 1])

    def heard_during(self, mic, stop, boost=0.15):
        """Barge-in: True if the user says the wake word before `stop` is set (while Karen is talking).
        The threshold is raised by `boost` because Karen's own voice comes back through the mic."""
        self.features.reset()
        buf, hits, recent = [], 0, []
        while not stop.is_set():
            buf.append(mic.read())
            if sum(len(c) for c in buf) < self.FRAME:
                continue
            recent = (recent + buf)[-60:]
            self.features((np.concatenate(buf) * 32767).astype(np.int16))
            buf = []
            hits = hits + 1 if self.score() >= min(0.97, self.threshold + boost) else 0
            if hits >= self.patience:
                if self.gate and not self.gate.his(np.concatenate(recent), mic):
                    hits = 0  # her own voice or the TV, not him
                    continue
                return True
        return False

    def wait(self, mic, log=None):
        """Block until the wake word is heard. Returns None (the request comes after it)."""
        self.features.reset()
        buf, hits, best, recent = [], 0, 0.0, []
        while True:
            buf.append(mic.read())
            if sum(len(c) for c in buf) < self.FRAME:
                continue
            recent = (recent + buf)[-60:]  # about 2 s of audio
            self.features((np.concatenate(buf) * 32767).astype(np.int16))
            buf = []
            p = self.score()
            hits = hits + 1 if p >= self.threshold else 0
            best = max(best, p) if hits else 0.0
            if hits >= self.patience:
                audio = np.concatenate(recent)
                if self.gate and not self.gate.his(audio, mic):
                    if log:
                        log("wake", f"score {best:.2f}, " + (self.gate.why or f"not your voice ({self.gate.last:.2f})")
                            + ": ignored")
                    save_clip(self.gate.clip, "voice_tests/rejected_wakes")  # to look over, never trained on by itself
                    hits, best, recent = 0, 0.0, []
                    self.features.reset()
                    continue
                if log:
                    log("wake", f"score {best:.2f}" + (f", voice {self.gate.last:.2f}"
                                                        if self.gate and self.gate.last is not None else "")
                        + (f" ({self.gate.why})" if self.gate and self.gate.why else ""))
                self.last_audio = audio
                self.last_score = best
                return None


class WhisperWake:
    def __init__(self, stt, words, max_position=3, max_seconds=12):
        self.stt = stt
        self.words = [w.lower() for w in words]
        self.max_position = max_position  # wake word must be among the first N words
        self.max_samples = int(max_seconds * 16000)

    def is_wake(self, word):
        word = re.sub(r"[^a-z]", "", word.lower())
        word = "k" + word[1:] if word.startswith("c") else word  # Caron, Carin -> K...
        return word in self.words or (len(word) >= 4 and SequenceMatcher(None, word, self.words[0]).ratio() >= 0.8)

    def split(self, text):
        """Return (found, text after the wake word)."""
        words = text.split()
        for i, w in enumerate(words[:self.max_position]):
            if self.is_wake(w):
                return True, " ".join(words[i + 1:]).strip(" ,.!?")
        return False, ""

    def wait(self, mic, log=None):
        """Block until an utterance starts with the wake word. Returns that utterance's audio."""
        while True:
            audio = mic.listen()
            if len(audio) > self.max_samples:
                continue
            text = self.stt.transcribe(audio)
            if log:
                log("heard", text or "...")
            if self.split(text)[0]:
                return audio
