"""Thalamus + prefrontal cortex: "was that said to me?" (docs/CORTEX.md, step 2).

Each utterance becomes up to 6 tokens, one per sense; a sense that's missing (no audio for a typed or old request) is
masked out, like a silent input to the thalamus:
  words     what was said (MiniLM, 384)              prev      what Karen said just before (384)
  voice     who said it (WeSpeaker 256 + his score)  sound     live voice vs speaker (9 acoustics)
  context   how it arrived (wake word + score, follow-up, button), seconds since Karen spoke, did she ask a question,
            time of day, length                      media     which kinds of app were playing sound
Thalamus: a CLS token attends over the senses through 2 pre-norm transformer layers (64 wide, 4 heads).
Prefrontal cortex: a closed-form continuous-time (CfC, "liquid") cell keeps the conversation's state between
utterances; how much of it survives depends on the real seconds in between.
Output: P(for Karen). Trained in torch by scripts/train_cortex.py; run here in numpy (~1 ms, no torch at runtime).
"""
import datetime
import math

import numpy as np

SENSES = ["words", "prev", "voice", "sound", "context", "media"]
SIZES = {"words": 384, "prev": 384, "voice": 257, "sound": 9, "context": 12, "media": 4}
HOW = ["wake", "followup", "button"]
BROWSERS, MUSIC, VIDEO = {"chrome", "msedge", "firefox", "brave", "opera"}, {"spotify", "amazon music", "music.ui"}, \
    {"vlc", "netflix", "primevideo", "potplayer", "mpc-hc", "wmplayer", "video.ui"}
# rough typical ranges, so each acoustic number is about -1..1
SOUND_SCALE = [(-30, 15), (8, 6), (40, 20), (0.3, 0.3), (0.5, 0.3), (0.2, 0.2), (0.1, 0.1), (1.5, 1.0), (4, 4)]


def context(how, wake_score, gap_karen, karen_asked, t, text):
    hour = datetime.datetime.fromtimestamp(t).hour + datetime.datetime.fromtimestamp(t).minute / 60
    gap = 1.0 if gap_karen is None else min(math.log1p(max(gap_karen, 0)) / 8, 1.5)
    return np.array([how == h for h in HOW] + [wake_score, gap, gap_karen is None, karen_asked,
                    math.sin(hour / 24 * 2 * math.pi), math.cos(hour / 24 * 2 * math.pi),
                    math.log1p(len(text.split())) / 3, text.rstrip().endswith("?"),
                    any(w in text.lower() for w in ("karen", "karin", "caron"))], np.float32)


def sound(acoustics):
    from core.cortex.senses import ACOUSTICS
    return np.array([(acoustics[k] - m) / s for k, (m, s) in zip(ACOUSTICS, SOUND_SCALE)], np.float32)


def media(apps):
    apps = {a.lower() for a in apps}
    return np.array([bool(apps), bool(apps & BROWSERS), bool(apps & MUSIC), bool(apps & VIDEO)], np.float32)


def gelu(x):
    return 0.5 * x * (1 + np.tanh(0.7978845608 * (x + 0.044715 * x ** 3)))


def norm(x, w, b, eps=1e-5):
    m = x.mean(-1, keepdims=True)
    v = ((x - m) ** 2).mean(-1, keepdims=True)
    return (x - m) / np.sqrt(v + eps) * w + b


def sigmoid(x):
    return 1 / (1 + np.exp(-x))


class Attention:
    """Loads models/cortex/attention.npz (weights saved by scripts/train_cortex.py)."""

    def __init__(self, path="models/cortex/attention.npz"):
        z = np.load(path, allow_pickle=False)
        self.w = {k: z[k] for k in z.files if not k.startswith("_")}
        self.trained = [s for s in SENSES if s in str(z["_trained"])]  # senses it learned; the rest stay masked
        self.threshold = float(z["_threshold"])
        self.heads = int(z["_heads"])
        self.h = np.zeros(self.w["cfc.k.weight"].shape[0], np.float32)  # prefrontal state between utterances
        self.last_t = None

    def lin(self, name, x):
        return x @ self.w[name + ".weight"].T + self.w[name + ".bias"]

    def thalamus(self, inputs):
        """inputs: {sense: vector or None}. Returns the CLS vector and the CLS -> sense attention of the last layer."""
        toks, names = [self.w["cls"][0]], ["cls"]
        for s in SENSES:
            if s in self.trained and inputs.get(s) is not None:
                toks.append(self.lin(f"embed.{s}", np.asarray(inputs[s], np.float32)) + self.w[f"kind.{s}"][0])
                names.append(s)
        x = np.stack(toks)
        att = None
        for i in range(len([k for k in self.w if k.endswith(".qkv.weight")])):
            p = f"layers.{i}."
            h = norm(x, self.w[p + "n1.weight"], self.w[p + "n1.bias"])
            q, k, v = np.split(self.lin(p + "qkv", h), 3, axis=-1)
            n, d = x.shape[0], q.shape[-1] // self.heads
            q, k, v = (a.reshape(n, self.heads, d).transpose(1, 0, 2) for a in (q, k, v))
            s = q @ k.transpose(0, 2, 1) / math.sqrt(d)
            a = np.exp(s - s.max(-1, keepdims=True))
            a /= a.sum(-1, keepdims=True)
            x = x + self.lin(p + "out", (a @ v).transpose(1, 0, 2).reshape(n, -1))
            h = norm(x, self.w[p + "n2.weight"], self.w[p + "n2.bias"])
            x = x + self.lin(p + "ff2", gelu(self.lin(p + "ff1", h)))
            att = a[:, 0].mean(0)  # how much the CLS token listened to each sense
        x = norm(x, self.w["norm.weight"], self.w["norm.bias"])
        return x[0], dict(zip(names, att.round(3).tolist()))

    def cfc(self, u, h, gap):
        z = np.tanh(self.lin("cfc.backbone", np.concatenate([u, h])))
        f, g, k = self.lin("cfc.f", z), np.tanh(self.lin("cfc.g", z)), np.tanh(self.lin("cfc.k", z))
        gate = sigmoid(-f * gap)
        return gate * g + (1 - gate) * k

    def __call__(self, inputs, t, update=True):
        """P(for Karen) for one utterance at time t. update=False leaves the prefrontal state as it was."""
        u, heard = self.thalamus(inputs)
        gap = math.log1p(t - self.last_t) if self.last_t else math.log1p(3600)
        h = self.cfc(u, self.h, gap)
        p = float(sigmoid(self.lin("head2", gelu(self.lin("head1", np.concatenate([u, h]))))[0]))
        if update:
            self.h, self.last_t = h, t
        return p, heard
