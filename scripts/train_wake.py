"""Train Karen's personal wake word detector on the user's own recordings.

Features: openWakeWord's speech-embedding models (models/oww), 16 frames = the last 1.28 s of audio.
Positives: his "Karen" clips (voice_tests/karen) + Kokoro voices saying "Karen", heavily augmented
(volume, room noise, echo, speed). Negatives: his other sentences (voice_tests/other), room sound, and Kokoro
voices saying everyday and sound-alike sentences ("get in", "Karan", "garden"...).
Output: models/karen_personal.pkl -> used by core/wake.py PersonalWake (wake.engine: personal).

    python scripts/train_wake.py
"""
import glob
import os
import pickle
import random
import sys
import time
import wave

import numpy as np

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ".")
RATE, WIN = 16000, 32000  # 2 s windows -> 16 embedding frames
DATA = sys.argv[1] if len(sys.argv) > 1 else "voice_tests"  # the user's recordings
OUT = "models/karen_personal.pkl"
CACHE = "models/wake_cache"
rng = np.random.default_rng(7)
random.seed(7)

NEG_TEXT = [
    "Get in the car.", "We got him.", "Kevin is here.", "Gavin called.", "My friend Karan is coming.",
    "I'm caring for my plants.", "Let's go to the garden.", "The current is strong.", "Carry this bag.",
    "Can I have some water?", "Kiran is my cousin.", "Corinne sent a message.", "Karma is real.",
    "Get him out of there.", "Car in the garage.", "Good morning, how are you?", "What time is the meeting?",
    "Turn on the lights.", "I'll be there in ten minutes.", "Did you finish the assignment?",
    "Open the window please.", "The weather is nice today.", "Play some music.", "Thank you so much.",
    "I don't know about that.", "Let's order some food.", "Where are my keys?", "Call me when you reach home.",
    "That was a great movie.", "Please send me the notes.", "Okay, sounds good.", "Hold on a second.",
    "The cricket match starts at seven.", "Can you hear me?", "I'm going to sleep now.", "Garage door is open.",
    "Caring is sharing.", "Get in line.", "Hey, can you help me?", "Hey there, what's up?",
]
MINE_TEXT = [
    "Can you check the car tyres?", "The garage opens at nine.", "Kerala is famous for its backwaters.",
    "I want to build a career in robotics.", "Turn left at the next corner.", "The colour of the wall is green.",
    "He has a black belt in karate.", "Carrots are good for your eyes.", "The cabinet is full of books.",
    "Get going, we're already late.", "Got it, thanks a lot.", "Get them some snacks.", "Can we talk later?",
    "Garbage collection is on Tuesday.", "The kitchen sink is leaking again.", "Cotton shirts are comfortable.",
    "Come here and see this.", "Karthik is my lab partner.", "Kareena is a famous actress.", "The carpet needs cleaning.",
    "I can't find my charger.", "Good evening everyone.", "Can I borrow your pen?", "Keep the change.",
    "The canteen food was cold today.", "My cousin lives in Kanpur.", "Careful, the floor is wet.",
    "The compiler threw an error.", "Gary sent the files.", "Carol is coming tomorrow.", "Aaron called you twice.",
    "The children are playing outside.", "We watched a movie last night.", "The bus is running late again.",
    "Please close the door behind you.", "I need to recharge my metro card.", "The exam hall is on the second floor.",
    "Let me know if you need anything.", "Breakfast is ready on the table.", "This laptop is getting really slow.",
    "Remind me to buy milk.", "He's a really good guitar player.", "The temple is very crowded today.",
    "My sister is studying medicine.", "The internet is down again.", "Call the plumber tomorrow morning.",
    "I'm reading a book about space.", "That song is stuck in my head.", "We should start the project early.",
    "The traffic on the highway is terrible.", "Can you share the presentation?", "Hey, nice to meet you.",
    "Hey, where are you going?", "Okay, let's get started.", "Yes, I'll be there.", "No, that's not right.",
    "Karan, pass the ball.", "Kiran, wait for me.", "Caring parents make a difference.", "The current bill is high.",
]
POS_TEXT = ["Karen.", "Hey Karen.", "Karen!", "Karen?"]
VOICES = ["af_heart", "af_bella", "af_nicole", "af_sarah", "af_sky", "am_adam", "am_michael", "am_eric",
          "am_liam", "bf_emma", "bf_isabella", "bm_george", "bm_lewis", "hf_alpha", "hf_beta", "hm_omega", "hm_psi"]


def load(path):
    with wave.open(path) as w:
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768


def synth(texts, voices, tag):
    """Kokoro speech at 16 kHz, cached in models/wake_cache (saved per sentence, so a cut-off run resumes)."""
    os.makedirs(CACHE, exist_ok=True)
    path = f"{CACHE}/{tag}.npy"
    if os.path.exists(path):
        return list(np.load(path, allow_pickle=True))
    os.makedirs(f"{CACHE}/{tag}", exist_ok=True)
    k = None
    out = []
    for i, text in enumerate(texts):
        part = f"{CACHE}/{tag}/{i:03d}.npy"
        if os.path.exists(part):
            out += list(np.load(part, allow_pickle=True))
            continue
        if k is None:
            from kokoro_onnx import Kokoro
            k = Kokoro("models/kokoro-v1.0.onnx", "models/voices-v1.0.bin")
        clips = []
        for v in voices:
            s, sr = k.create(text, voice=v, speed=random.uniform(0.85, 1.15), lang="en-gb" if v[0] == "b" else "en-us")
            clips.append(np.interp(np.arange(0, len(s), sr / RATE), np.arange(len(s)), s).astype(np.float32))
        np.save(part, np.array(clips, dtype=object), allow_pickle=True)
        out += clips
        print(f"\r  {tag}: {i + 1}/{len(texts)}", end="", flush=True)
    print()
    np.save(path, np.array(out, dtype=object), allow_pickle=True)
    return out


def trim(x, thresh=0.02):
    """Cut leading/trailing quiet so the word's end can be placed precisely."""
    loud = np.where(np.abs(x) > thresh * max(1e-3, np.abs(x).max()) * 3)[0]
    return x[max(0, loud[0] - 800):loud[-1] + 800] if len(loud) else x


def augment(x, room):
    x = x * rng.uniform(0.3, 1.6)
    if rng.random() < 0.5:  # speed / pitch
        f = rng.uniform(0.9, 1.1)
        x = np.interp(np.arange(0, len(x), f), np.arange(len(x)), x).astype(np.float32)
    if rng.random() < 0.4:  # echo from a small room
        n = int(rng.uniform(0.05, 0.3) * RATE)
        ir = rng.standard_normal(n) * np.exp(-np.arange(n) / (n / 5))
        ir[0] = 1
        x = np.convolve(x, ir / np.abs(ir).sum() * 2)[:len(x)].astype(np.float32)
    return x


def window(x, room, end_at=None):
    """Place x in a 2 s window of room noise with its end at sample end_at (may lie past the window's edge).
    Default: the word ends just before the window does, which is when the detector should fire."""
    start = rng.integers(0, len(room) - WIN)
    out = room[start:start + WIN] * rng.uniform(0.5, 2.0)
    end = end_at if end_at is not None else WIN - int(rng.uniform(0.0, 0.3) * RATE)
    first = end - len(x)
    a, b = max(0, first), min(WIN, end)
    if b > a:
        out[a:b] += x[a - first:b - first]
    return np.clip(out, -1, 1)


def embed(windows, features):
    x = (np.stack(windows) * 32767).astype(np.int16)
    e = np.concatenate([features.embed_clips(x[i:i + 64], batch_size=64) for i in range(0, len(x), 64)])
    return e[:, -16:, :].reshape(len(x), -1)


def stream_windows(clips, room, features, lead=0.0):
    """Clips joined by room sound into one stream -> one 16-frame window per 80 ms step, as heard live."""
    parts = [room[:int(lead * RATE)]] if lead else []
    for x in clips:
        gap = int(rng.uniform(0.3, 1.2) * RATE)
        start = rng.integers(0, len(room) - gap)
        parts += [augment(x, room), room[start:start + gap]]
    s = np.concatenate(parts)
    s = np.clip(s + np.resize(room, len(s)) * rng.uniform(0.3, 1.0), -1, 1)
    emb = features._get_embeddings((s * 32767).astype(np.int16))  # embed_clips breaks on long streams
    win = np.lib.stride_tricks.sliding_window_view(emb, 16, axis=0)  # (frames-15, 96, 16)
    return np.ascontiguousarray(win.transpose(0, 2, 1)).reshape(len(win), -1)


def triggers(scores, thr, patience):
    """How many times the detector would wake: patience hits in a row, then ~1.3 s of rest."""
    n, hits = 0, 0
    for p in scores:
        hits = hits + 1 if p >= thr else min(hits, 0)
        if hits >= patience:
            n, hits = n + 1, -16
    return n


def main():
    from openwakeword.utils import AudioFeatures
    from sklearn.neural_network import MLPClassifier
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    sessions = [DATA] + sorted(glob.glob(f"{DATA}/session*"))  # every recording session adds to the first one
    his_pos = [trim(load(p)) for d in sessions for p in sorted(glob.glob(f"{d}/karen/*.wav"))]
    his_neg = [trim(load(p)) for d in sessions for p in sorted(glob.glob(f"{d}/other/*.wav"))]
    # "Karen, <request>" sentences: what comes after the wake word is ordinary speech (no "Karen" in it)
    his_neg += [trim(load(p)[RATE * 3 // 2:]) for d in sessions for p in sorted(glob.glob(f"{d}/commands/*.wav"))
                if len(load(p)) > RATE * 2]
    his_neg += [load(p) for p in sorted(glob.glob(f"{DATA}/false_wakes/*.wav"))]  # sounds that woke her by mistake
    if len(his_pos) < 15 or not os.path.exists(f"{DATA}/room.wav"):
        sys.exit("Record first: python scripts/record_samples.py wake")
    his_neg += [trim(load(f"{DATA}/10.wav"))] if os.path.exists(f"{DATA}/10.wav") else []
    # room sound: the quiet room, plus the room with music playing (the background she must still hear "Karen" over)
    room = np.concatenate([load(p) for p in [f"{DATA}/room.wav"] + sorted(glob.glob(f"{DATA}/session*/room*.wav"))
                           if os.path.exists(p)])
    room = np.concatenate([room, rng.standard_normal(RATE * 5).astype(np.float32) * 0.003])
    print(f"The user: {len(his_pos)} 'Karen' clips, {len(his_neg)} other sentences, {len(room) / RATE:.0f}s room sound")

    print("Making synthetic voices (cached after the first run)...")
    syn_pos = [trim(x) for x in synth(POS_TEXT, VOICES, "pos")]
    syn_neg = [trim(x) for x in synth(NEG_TEXT, VOICES[::2], "neg")]

    # hold out 25% of his own clips to measure honestly
    random.shuffle(his_pos)
    random.shuffle(his_neg)
    k = max(4, len(his_pos) // 4)
    val_pos, train_pos = his_pos[:k], his_pos[k:]
    kn = max(3, len(his_neg) // 4)
    val_neg, train_neg = his_neg[:kn], his_neg[kn:]

    features = AudioFeatures(melspec_model_path="models/oww/melspectrogram.onnx",
                             embedding_model_path="models/oww/embedding_model.onnx", inference_framework="onnx")

    def neg_windows(clips, per_clip):
        out = []
        for x in clips:
            for _ in range(per_clip):  # slide the sentence through the window at random positions
                xa = augment(x, room)
                out.append(window(xa, room, end_at=int(rng.integers(RATE // 2, WIN + len(xa) // 2))))
        return out

    def partial(x):
        """Only the first 40-70% of "Karen" is in the window: the detector should wait for the whole word."""
        xa = augment(x, room)
        return window(xa, room, end_at=WIN + int(len(xa) * rng.uniform(0.3, 0.6)))

    talk = train_neg + syn_neg

    def positive(x):
        """ "Karen" ending just before the window does; half the time a request follows ("Karen, open...")."""
        xa = augment(x, room)
        after = int(rng.uniform(0.0, 0.3) * RATE)
        if after and rng.random() < 0.5:
            follow = talk[rng.integers(len(talk))]
            follow = follow[:after] * rng.uniform(0.5, 1.2)
            gap = np.zeros(int(rng.uniform(0.0, 0.15) * RATE), np.float32)
            return window(np.concatenate([xa, gap, follow])[:len(xa) + after], room, end_at=WIN)
        return window(xa, room, end_at=WIN - after)

    # the slowest step, cached until his recordings (or this recipe: v2) change
    sig = "v2 " + str(sorted((os.path.relpath(p, DATA), os.path.getsize(p))
                             for p in glob.glob(f"{DATA}/**/*.wav", recursive=True)))
    emb_path = f"{CACHE}/base_embeddings.npz"
    if os.path.exists(emb_path) and str(np.load(emb_path)["sig"]) == sig:
        cached = np.load(emb_path)
        P, N = cached["P"], cached["N"]
        print(f"Using cached embeddings ({len(P)} positive, {len(N)} negative windows)")
    else:
        t = time.time()
        X_pos = [positive(x) for x in train_pos for _ in range(30)]
        X_pos += [positive(x) for x in syn_pos for _ in range(3)]
        X_neg = neg_windows(train_neg, 30) + neg_windows(syn_neg, 4)
        X_neg += [window(np.zeros(1, np.float32), room) for _ in range(400)]  # room sound only
        X_neg += [partial(x) for x in train_pos for _ in range(5)]
        print(f"Embedding {len(X_pos)} positive and {len(X_neg)} negative windows...")
        P, N = embed(X_pos, features), embed(X_neg, features)
        np.savez(emb_path, P=P, N=N, sig=sig)
        print(f"  done in {time.time() - t:.0f}s")

    # continuous speech, heard the way the live detector hears it: mining set and a held-out set
    mine = [trim(x) for x in synth(MINE_TEXT, VOICES[1::3], "mine")]
    random.shuffle(mine)
    cut = len(mine) // 5
    mine_train = stream_windows(mine[cut:], room, features)
    val_stream = stream_windows(mine[:cut] + val_neg * 2, room, features)
    val_minutes = len(val_stream) * 0.08 / 60
    val_pos_windows = [stream_windows([x], room, features, lead=2.5) for x in val_pos for _ in range(3)]

    model = make_pipeline(StandardScaler(), MLPClassifier((64,), alpha=1e-2, max_iter=400, early_stopping=True,
                                                          random_state=0))
    X, y = np.concatenate([P, N]), np.concatenate([np.ones(len(P)), np.zeros(len(N))])
    for rnd in range(3):
        model.fit(X, y)
        scores = model.predict_proba(mine_train)[:, 1]
        hard = mine_train[scores >= 0.2]
        print(f"  round {rnd + 1}: {len(hard)} of {len(mine_train)} moments of other speech look like 'Karen'")
        if rnd == 2 or not len(hard):
            break
        easy = mine_train[rng.choice(len(mine_train), min(1500, len(mine_train)), replace=False)]
        X = np.concatenate([X, hard, hard, easy])  # hard ones twice: they matter most
        y = np.concatenate([y, np.zeros(2 * len(hard) + len(easy))])

    # pick threshold and hit rule: most of his held-out "Karen"s at ~0 false wakes on held-out speech.
    # Among equally good thresholds take the lowest (his voice on another day scores lower than the
    # same-session clips used here), then add a small safety margin above the last false wake.
    vs = model.predict_proba(val_stream)[:, 1]
    vp = [model.predict_proba(w)[:, 1] for w in val_pos_windows]
    best = None
    for patience in (1, 2):
        for thr in np.arange(0.3, 0.99, 0.02):
            recall = np.mean([triggers(p, thr, patience) > 0 for p in vp])
            per_hour = triggers(vs, thr, patience) / val_minutes * 60
            key = (per_hour <= 1.0, recall if per_hour <= 1.0 else -per_hour, -thr)
            if best is None or key > best[0]:
                best = (key, thr, patience, recall, per_hour)
    _, thr, patience, recall, per_hour = best
    thr = min(0.95, thr + 0.05)
    recall = np.mean([triggers(p, thr, patience) > 0 for p in vp])
    per_hour = triggers(vs, thr, patience) / val_minutes * 60
    print(f"Held out: wakes on {recall:.0%} of your 'Karen's, {per_hour:.1f} false wakes per hour of other speech "
          f"({val_minutes:.0f} min tested; threshold {thr:.2f}, {patience} hit{'s' if patience > 1 else ''} in a row)")
    with open(OUT, "wb") as f:
        pickle.dump({"model": model, "threshold": float(thr), "patience": patience, "recall": float(recall),
                     "false_per_hour": float(per_hour)}, f)
    print(f"Saved {OUT}. Set wake.engine: personal in config.yaml.")

if __name__ == "__main__":
    main()
