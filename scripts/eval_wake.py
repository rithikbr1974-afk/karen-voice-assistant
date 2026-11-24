"""Score a wake-word model on recordings, the way the live detector hears them.

  python scripts/eval_wake.py models/karen_personal.pkl voice_tests/session1

Recall: how many of the "Karen" clips wake her. False wakes: how often normal speech does, per hour (the sentences,
and the request part of the "Karen, ..." commands, joined with room sound). Also tests over the music room sound.
Only meaningful on recordings the model was NOT trained on.
"""
import glob
import os
import pickle
import sys
import wave

import numpy as np

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ".")
from openwakeword.utils import AudioFeatures  # noqa: E402

RATE, FRAME = 16000, 2560


def load(path):
    with wave.open(path) as w:
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768


def fires(features, saved, thr, audio):
    """Number of wakes while `audio` streams past, as core/wake.py PersonalWake.wait() would count them."""
    features.reset()
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    hits = rest = n = 0
    for i in range(0, len(pcm) - FRAME + 1, FRAME):
        features(pcm[i:i + FRAME])
        if rest > 0:
            rest -= 1
            continue
        p = float(saved["model"].predict_proba(features.get_features(16).reshape(1, -1))[0, 1])
        hits = hits + 1 if p >= thr else 0
        if hits >= saved["patience"]:
            n, hits, rest = n + 1, 0, 8  # one wake, then a short rest (she is listening to the request)
    return n


def main(model_path, folder, only_threshold=None, patience=None):
    saved = pickle.load(open(model_path, "rb"))
    thr = only_threshold or saved["threshold"]
    saved["patience"] = patience or saved["patience"]  # config.yaml wake.patience overrides the model's own
    features = AudioFeatures(melspec_model_path="models/oww/melspectrogram.onnx",
                             embedding_model_path="models/oww/embedding_model.onnx", inference_framework="onnx")
    rng = np.random.default_rng(1)
    quiet = load(f"{folder}/room.wav")
    music = load(f"{folder}/room_music.wav") if os.path.exists(f"{folder}/room_music.wav") else quiet

    def background(n, bed):
        s = rng.integers(0, len(bed) - n) if len(bed) > n else 0
        return np.resize(bed[s:], n).astype(np.float32)

    karen = sorted(glob.glob(f"{folder}/karen/*.wav"))
    caught = missed = 0
    miss_names = []
    for i, p in enumerate(karen):
        x = load(p)
        bed = music if i >= 36 else quiet  # the last four were said over music
        stream = np.concatenate([background(RATE, bed), x, background(RATE, bed)])
        if fires(features, saved, thr, stream) >= 1:
            caught += 1
        else:
            missed += 1
            miss_names.append(os.path.basename(p))
    others = [load(p) for p in sorted(glob.glob(f"{folder}/other/*.wav"))]
    cmds = [load(p)[RATE * 3 // 2:] for p in sorted(glob.glob(f"{folder}/commands/*.wav")) if len(load(p)) > RATE * 2]
    out = {}
    for name, clips in (("sentences", others), ("requests after 'Karen,'", cmds)):
        for bed_name, bed in (("quiet room", quiet), ("with music", music)):
            stream = np.concatenate([np.concatenate([c, background(int(RATE * 0.8), bed)]) for c in clips])
            stream = np.clip(stream + background(len(stream), bed) * 0.5, -1, 1)
            n = fires(features, saved, thr, stream)
            out[(name, bed_name)] = (n, len(stream) / RATE / 60)
    print(f"model {model_path}  (threshold {thr:.2f}, {saved['patience']} hits in a row)")
    print(f"  'Karen' clips woken: {caught}/{len(karen)}" + (f"   missed: {', '.join(miss_names)}" if miss_names else ""))
    total_n = sum(v[0] for v in out.values())
    total_min = sum(v[1] for v in out.values())
    for (name, bed_name), (n, minutes) in out.items():
        print(f"  false wakes in {name} ({bed_name}): {n} in {minutes:.1f} min")
    print(f"  => {total_n} false wakes in {total_min:.1f} min of normal speech = {total_n / total_min * 60:.1f} per hour")
    return caught / max(1, len(karen)), total_n / max(0.1, total_min) * 60


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    pat = next((int(a.split("=")[1]) for a in sys.argv if a.startswith("--patience=")), None)
    thr = next((float(a.split("=")[1]) for a in sys.argv if a.startswith("--threshold=")), None)
    main(args[0], args[1] if len(args) > 1 else "voice_tests/session1", only_threshold=thr, patience=pat)
