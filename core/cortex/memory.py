"""Hippocampus: episodic memory of what Karen heard, and the replay store the other regions learn from.

One episode = one utterance: when, what was said (overheard text is kept `text_hours`, 24 h), its senses as
numbers (voice and word embeddings, acoustics, which apps were playing), whether it was a request to Karen, and the
label the brain learns from. Episodes and their audio clips are deleted after `days` (14), and the clips folder is
capped at `audio_mb`. Everything stays in data/cortex on this PC.

Recall works like the hippocampus:
  dentate gyrus  pattern separation: each memory gets a sparse code (2,048 cells, 3% active) so similar
                 sentences still get distinct codes
  CA3            pattern completion: a modern Hopfield network settles a vague cue onto the stored memories
  time cells     the moments just before and after the best match come with it, so a reply carries its context
"""
import base64
import datetime
import json
import os
import threading
import time
import wave

import numpy as np

DIR = "data/cortex"
CELLS, ACTIVE = 2048, 64


def read_wav(path):
    with wave.open(path, "rb") as f:
        return np.frombuffer(f.readframes(f.getnframes()), np.int16).astype(np.float32) / 32768


def write_wav(path, audio):
    with wave.open(path, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(16000)
        f.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())


def pack(v):
    return None if v is None else base64.b64encode(np.asarray(v, np.float16).tobytes()).decode()


def unpack(s):
    return None if not s else np.frombuffer(base64.b64decode(s), np.float16).astype(np.float32)


def sparse(x, proj):
    """Dentate gyrus: project up to 2,048 cells and keep only the 64 most active (k-winners-take-all)."""
    h = x @ proj
    code = np.zeros_like(h)
    top = np.argpartition(-h, ACTIVE, axis=-1)[..., :ACTIVE]
    np.put_along_axis(code, top, 1.0, axis=-1)
    return code


class Memory:
    def __init__(self, folder=DIR, text_hours=24, days=14, audio_mb=300):
        self.folder, self.text_hours, self.days, self.audio_mb = folder, text_hours, days, audio_mb
        self.path = os.path.join(folder, "episodes.jsonl")
        self.clips = os.path.join(folder, "clips")
        self.lock = threading.Lock()
        self.proj = np.random.default_rng(783).standard_normal((384, CELLS)).astype(np.float32)  # fixed wiring
        os.makedirs(self.clips, exist_ok=True)

    # ---------------------------------------------------------------- writing (Karen's process)
    @staticmethod
    def new_id():
        now = time.time()
        return datetime.datetime.fromtimestamp(now).strftime("%Y%m%d-%H%M%S-") + f"{int(now * 1000) % 1000:03d}"

    def store(self, kind, text, words=None, voice=None, me=None, acoustics=None, playing=(), audio=None, eid=None,
              **extra):
        """kind: heard (overheard), request (spoken to Karen), typed. Returns the episode id."""
        now = time.time()
        eid = eid or self.new_id()
        clip = None
        if audio is not None and self.audio_mb > 0:
            clip = os.path.join(self.clips, eid + ".wav")
            write_wav(clip, audio)
        ep = {"id": eid, "t": round(now, 2), "kind": kind, "text": text, "me": me, "playing": list(playing),
              "acoustics": acoustics, "words": pack(words), "voice": pack(voice), "clip": clip, **extra}
        with self.lock, open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(ep, ensure_ascii=False) + "\n")
        return eid

    def label(self, eid, **fields):
        """Add what was learned about an episode later (e.g. addressed=0 after "that wasn't for you")."""
        self._rewrite(lambda ep: dict(ep, **fields) if ep["id"] == eid else ep)

    def prune(self):
        """Forget on schedule: overheard text after text_hours, whole episodes and clips after days, oldest clips
        past the size cap."""
        now = time.time()

        def keep(ep):
            age = now - ep["t"]
            if age > self.days * 86400:
                self._drop_clip(ep)
                return None
            if ep["kind"] == "heard" and age > self.text_hours * 3600 and ep.get("text"):
                ep = dict(ep, text=None)
            return ep
        self._rewrite(keep)
        clips = sorted(os.scandir(self.clips), key=lambda e: e.name)
        size = sum(c.stat().st_size for c in clips)
        while clips and size > self.audio_mb * 1024 * 1024:
            c = clips.pop(0)
            size -= c.stat().st_size
            os.remove(c.path)

    def _drop_clip(self, ep):
        if ep.get("clip") and os.path.exists(ep["clip"]):
            os.remove(ep["clip"])

    def _rewrite(self, fn):
        with self.lock:
            eps = [e for e in (fn(ep) for ep in self.episodes()) if e]
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.writelines(json.dumps(e, ensure_ascii=False) + "\n" for e in eps)
            os.replace(tmp, self.path)

    # ---------------------------------------------------------------- reading (also the brain MCP server)
    def episodes(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                return [json.loads(line) for line in f if line.strip()]
        except OSError:
            return []

    def recent(self, minutes, kinds=("heard", "request", "typed")):
        since = time.time() - minutes * 60
        return [e for e in self.episodes() if e["t"] >= since and e.get("text") and e["kind"] in kinds]

    def recall(self, cue, k=3, around=2, beta=12.0, steps=3, min_sim=0.3):
        """Episodes that best match the cue vector (a Words embedding), each with its neighbours in time.
        Returns lists of episodes, best first. The settled state always lands on some memory, so whether a memory
        matches at all is judged on the cue itself (min_sim); the attractor only ranks and completes."""
        eps = [e for e in self.episodes() if e.get("text") and e.get("words")]
        if not eps:
            return []
        keys = np.stack([unpack(e["words"]) for e in eps])
        codes = sparse(keys, self.proj)
        xi = np.asarray(cue, np.float32)
        for _ in range(steps):  # CA3: xi <- K^T softmax(beta K xi), settles onto the stored memories
            a = np.exp(beta * (keys @ xi - (keys @ xi).max()))
            xi = keys.T @ (a / a.sum())
            xi /= np.linalg.norm(xi) + 1e-9
        overlap = codes @ sparse(xi, self.proj) / ACTIVE  # dentate gyrus codes: shared active cells
        direct = keys @ np.asarray(cue, np.float32)
        score = 0.5 * (keys @ xi) + 0.3 * direct + 0.2 * overlap
        picked, used = [], set()
        for i in np.argsort(-score):
            if len(picked) == k:
                break
            if i in used or direct[i] < min_sim:
                continue
            span = [j for j in range(i - around, i + around + 1)
                    if 0 <= j < len(eps) and abs(eps[j]["t"] - eps[i]["t"]) < 120 and j not in used]
            used.update(span)
            picked.append([eps[j] for j in span])
        return picked
