"""Karen's senses: what the Cortex knows about one utterance before it thinks about it.

  Voice     who is speaking: WeSpeaker ResNet34 embedding (256), compared with the user's voice profile
  Words     what it means: all-MiniLM-L6-v2 sentence embedding (384)
  acoustics live voice in the room or sound from a speaker (TV, phone, YouTube): level, dynamics, spectrum
  playing   which apps are making sound right now (Windows audio session meters)
"""
import glob
import os

import numpy as np
import onnxruntime as ort

RATE = 16000


def _session(path, threads=2):
    opts = ort.SessionOptions()
    opts.inter_op_num_threads, opts.intra_op_num_threads = 1, threads
    return ort.InferenceSession(path, opts, providers=["CPUExecutionProvider"])


def _mel_banks(n_mels=80, n_fft=512, low=20.0, high=RATE / 2):
    """Kaldi's triangular filters, spaced on the 1127*ln(1+f/700) mel scale."""
    mel = lambda f: 1127.0 * np.log(1.0 + np.asarray(f) / 700.0)
    edges = np.linspace(mel(low), mel(high), n_mels + 2)
    bins = mel(np.arange(n_fft // 2) * RATE / n_fft)
    left, centre, right = edges[:-2, None], edges[1:-1, None], edges[2:, None]
    up, down = (bins - left) / (centre - left), (right - bins) / (right - centre)
    return np.maximum(0.0, np.minimum(up, down))


BANKS = _mel_banks()
WINDOW = np.hamming(400)


def fbank(audio):
    """80 log-mel features per 10 ms, the way torchaudio.compliance.kaldi.fbank makes them for WeSpeaker
    (25 ms Hamming frames, DC removed, pre-emphasis 0.97, no dither), then mean-normalised."""
    x = np.asarray(audio, dtype=np.float64) * 32768
    n = 1 + (len(x) - 400) // 160
    if n < 1:
        return np.zeros((0, 80), np.float32)
    frames = x[np.arange(400)[None, :] + 160 * np.arange(n)[:, None]]
    frames -= frames.mean(axis=1, keepdims=True)
    frames = np.concatenate([frames[:, :1] * 0.03, frames[:, 1:] - 0.97 * frames[:, :-1]], axis=1)
    power = np.abs(np.fft.rfft(frames * WINDOW, 512)) ** 2
    feats = np.log(np.maximum(power[:, :256] @ BANKS.T, np.finfo(float).eps))
    return (feats - feats.mean(axis=0)).astype(np.float32)


def unit(v):
    v = np.asarray(v, dtype=np.float32)
    return v / (np.linalg.norm(v, axis=-1, keepdims=True) + 1e-9)


class Voice:
    """Who is speaking. Measured on his clips (2026-10-28): k-nearest-neighbour similarity to his enrolled
    recordings gave him 0.32-0.83 and 8 Kokoro voices at most 0.20. A mean profile overlapped, and CAM++ was 2x
    faster (36 vs 79 ms per second of audio) but overlapped too."""

    def __init__(self, model="models/cortex/voice.onnx", profile="models/cortex/voice_profile.npy"):
        self.session = _session(model)
        self.profile_path = profile
        self.me = np.load(profile) if os.path.exists(profile) else None  # his enrolled clips, one row each

    def embed(self, audio):
        feats = fbank(audio[: RATE * 4])  # 4 s is plenty to know a voice, and keeps it ~0.3 s
        if len(feats) < 50:
            return None  # under half a second: no voice to judge
        return unit(self.session.run(None, {"feats": feats[None]})[0][0])

    def similarity(self, emb, k=3):
        """Mean cosine similarity to his k closest recordings."""
        if emb is None or self.me is None:
            return None
        return float(np.sort(self.me @ emb)[-k:].mean())

    def enroll(self, folder="voice_tests"):
        """His clips: the test phrases, his 40 "Karen"s, his other sentences and the second session."""
        from core.cortex.memory import read_wav
        paths = [p for pattern in ("*.wav", "karen/*.wav", "other/*.wav", "session2/*.wav")
                 for p in glob.glob(os.path.join(folder, pattern))
                 if "room" not in os.path.basename(p).lower()]  # room.wav and room_music.wav aren't his voice
        embs = [e for e in (self.embed(read_wav(p)) for p in paths) if e is not None]
        if not embs:
            return 0
        self.me = np.stack(embs)
        os.makedirs(os.path.dirname(self.profile_path), exist_ok=True)
        np.save(self.profile_path, self.me)
        return len(embs)


class Words:
    """What the words mean: mean-pooled MiniLM, unit length, so a dot product is the cosine similarity."""

    def __init__(self, model="models/cortex/words.onnx", tokenizer="models/cortex/words_tokenizer.json"):
        from tokenizers import Tokenizer
        self.session = _session(model)
        self.tok = Tokenizer.from_file(tokenizer)
        self.tok.enable_truncation(128)

    def embed(self, texts):
        one = isinstance(texts, str)
        texts = [texts] if one else list(texts)
        self.tok.enable_padding()
        enc = self.tok.encode_batch([t or "" for t in texts])
        ids = np.array([e.ids for e in enc], np.int64)
        mask = np.array([e.attention_mask for e in enc], np.int64)
        hidden = self.session.run(None, {"input_ids": ids, "attention_mask": mask,
                                         "token_type_ids": np.zeros_like(ids)})[0]
        pooled = (hidden * mask[..., None]).sum(1) / np.maximum(mask.sum(1, keepdims=True), 1)
        out = unit(pooled)
        return out[0] if one else out


ACOUSTICS = ["level_db", "crest", "dynamics_db", "low", "mid", "high", "flatness", "centroid", "seconds"]


def acoustics(audio):
    """Clues to live voice vs loudspeaker. Broadcast and YouTube audio is compressed (small dynamics, low crest);
    laptop and TV speakers lose the lows; a voice across the room is quieter and duller than one near the mic."""
    a = np.asarray(audio, dtype=np.float32)
    rms = float(np.sqrt(np.mean(a ** 2)) + 1e-9)
    frames = a[: len(a) // 400 * 400].reshape(-1, 400)
    if not len(frames):
        return dict.fromkeys(ACOUSTICS, 0.0)
    energy = 10 * np.log10(np.mean(frames ** 2, axis=1) + 1e-10)
    spec = np.abs(np.fft.rfft(frames * np.hanning(400), axis=1)) ** 2 + 1e-12
    freqs = np.fft.rfftfreq(400, 1 / RATE)
    loud = spec[energy > np.percentile(energy, 50)]  # the voiced half of the frames
    total = loud.sum(axis=1)
    band = lambda lo, hi: float(np.mean(loud[:, (freqs >= lo) & (freqs < hi)].sum(axis=1) / total))
    return {
        "level_db": 20 * np.log10(rms),
        "crest": float(np.abs(a).max() / rms),
        "dynamics_db": float(np.percentile(energy, 95) - np.percentile(energy, 10)),
        "low": band(0, 300),
        "mid": band(300, 3000),
        "high": band(3000, 8000),
        "flatness": float(np.mean(np.exp(np.mean(np.log(loud), axis=1)) / np.mean(loud, axis=1))),
        "centroid": float(np.mean((loud * freqs).sum(axis=1) / total) / 1000),
        "seconds": len(a) / RATE,
    }


def playing(threshold=0.01):
    """Programs making sound right now, from each Windows audio session's peak meter."""
    try:
        import comtypes
        from pycaw.pycaw import AudioUtilities, IAudioMeterInformation
        comtypes.CoInitialize()
        found = set()
        for s in AudioUtilities.GetAllSessions():
            if not (s.Process and s.ProcessId != os.getpid()):
                continue
            try:
                if s._ctl.QueryInterface(IAudioMeterInformation).GetPeakValue() > threshold:
                    found.add(s.Process.name().lower().removesuffix(".exe"))
            except Exception:
                continue  # the program closed while we looked
        return sorted(found)
    except Exception:
        return []
