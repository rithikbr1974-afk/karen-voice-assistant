"""Wake word detectors. Both have wait(mic) -> utterance containing the wake word, or None.

OwwWake: custom openWakeWord "Karen" model (streaming, low CPU), optional per-voice verifier.
WhisperWake: fallback - transcribe each utterance with a small Whisper model and look for "Karen".
"""
import re
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
                return None


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
        self.features = AudioFeatures(melspec_model_path=f"{feature_dir}/melspectrogram.onnx",
                                      embedding_model_path=f"{feature_dir}/embedding_model.onnx",
                                      inference_framework="onnx")

    def score(self):
        return float(self.model.predict_proba(self.features.get_features(16).reshape(1, -1))[0, 1])

    def heard_during(self, mic, stop, boost=0.15):
        """Barge-in: True if the user says the wake word before `stop` is set (while Karen is talking).
        The threshold is raised by `boost` because Karen's own voice comes back through the mic."""
        self.features.reset()
        buf, hits = [], 0
        while not stop.is_set():
            buf.append(mic.read())
            if sum(len(c) for c in buf) < self.FRAME:
                continue
            self.features((np.concatenate(buf) * 32767).astype(np.int16))
            buf = []
            hits = hits + 1 if self.score() >= min(0.97, self.threshold + boost) else 0
            if hits >= self.patience:
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
                if log:
                    log("wake", f"score {best:.2f}")
                self.last_audio = np.concatenate(recent)
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
