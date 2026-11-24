"""How well does speech-to-text understand the user? Runs the session's sentences through the STT model.

  python scripts/eval_stt.py [model]     default: the model in config.yaml (small.en)
"""
import glob
import json
import os
import re
import sys
import time
import wave

import numpy as np
import yaml

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ".")
from core.stt import STT  # noqa: E402

NUMS = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
        "eight": "8", "nine": "9", "ten": "10", "thirty": "30", "twenty": "20", "forty": "40", "fifty": "50"}


def words(text):
    text = re.sub(r"[^a-z0-9' ]", " ", text.lower().replace("-", " "))
    w = [NUMS.get(x, x) for x in text.split()]
    out = []
    for x in w:  # "twenty four" -> "24", "two four six eight" -> "2468", "250" stays
        if out and x.isdigit() and out[-1].isdigit() and (len(out[-1]) == 2 and out[-1].endswith("0") and len(x) == 1):
            out[-1] = str(int(out[-1]) + int(x))
        elif out and x.isdigit() and out[-1].isdigit() and len(x) == 1 and len(out[-1]) < 4 and out[-1] != "0":
            out[-1] += x
        else:
            out.append(x)
    return out


def distance(a, b):
    d = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        prev, d[0] = d[0], i
        for j, y in enumerate(b, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (x != y))
    return d[-1]


SESSION = "voice_tests/session1"  # recorded by scripts/record_session.py


def load(path):
    with wave.open(path) as w:
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768


def main():
    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    model = sys.argv[1] if len(sys.argv) > 1 else cfg["stt"]["model"]
    if model == "groq":  # the cloud path (Groq Whisper), as Karen uses it when stt.cloud is set
        from dotenv import load_dotenv
        load_dotenv()
        stt = STT(cfg["stt"]["model"], cfg["stt"]["compute_type"], threads=cfg["stt"]["threads"],
                  cloud=cfg["stt"].get("cloud") or {"model": "whisper-large-v3-turbo"})
    else:
        stt = STT(model, cfg["stt"]["compute_type"], threads=cfg["stt"]["threads"])
    phrases = json.load(open(f"{SESSION}/phrases.json", encoding="utf-8"))
    total = {}
    bad = []
    for kind in ("commands", "other"):
        errs = n = 0
        t = time.time()
        files = sorted(glob.glob(f"{SESSION}/{kind}/*.wav"))
        for path, ref in zip(files, phrases[kind]):
            if model == "groq":
                time.sleep(3.2)  # Groq's free speech limit is 20 requests a minute
            heard = stt.transcribe(load(path))
            if model == "groq" and stt.used != "groq":
                print("  (fell back to local:", stt.used, ")")
            r, h = words(ref), words(heard)
            e = distance(r, h)
            errs, n = errs + e, n + len(r)
            if e:
                bad.append((e, kind, ref, heard))
        total[kind] = (errs, n, (time.time() - t) / max(1, len(files)))
        print(f"{kind:9} word errors {errs}/{n} = {errs / n:.0%}   ({total[kind][2]:.1f} s per sentence)")
    print(f"\n{model}: overall {sum(v[0] for v in total.values()) / sum(v[1] for v in total.values()):.0%} word errors")
    print("\nWorst misses (what you said -> what she heard):")
    for e, kind, ref, heard in sorted(bad, reverse=True)[:14]:
        print(f"  [{e}] {ref}\n       -> {heard}")


if __name__ == "__main__":
    main()
