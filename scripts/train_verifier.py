"""Check the custom wake word model on the user's recordings and train their voice verifier.

Needs models/karen.onnx (from the Colab notebook) and recordings from: python scripts/record_samples.py wake
"""
import glob
import os
import sys

import yaml

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from openwakeword import train_custom_verifier  # noqa: E402
from openwakeword.model import Model  # noqa: E402

cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))["wake"]
features = dict(inference_framework="onnx", melspec_model_path="models/oww/melspectrogram.onnx",
                embedding_model_path="models/oww/embedding_model.onnx")
pos = sorted(glob.glob("voice_tests/karen/*.wav"))
neg = sorted(glob.glob("voice_tests/other/*.wav")) + sorted(glob.glob("voice_tests/[0-9]*.wav"))
if not os.path.exists(cfg["oww_model"]) or not pos:
    sys.exit("Need models/karen.onnx and voice_tests/karen/*.wav first.")

name = os.path.splitext(os.path.basename(cfg["oww_model"]))[0]
model = Model(wakeword_models=[cfg["oww_model"]], **features)


def peak(path):
    model.reset()
    return max(p[name] for p in model.predict_clip(path))


print("Base model peak score per clip (wake fires at >= %.2f):" % cfg["threshold"])
pos_scores = [peak(p) for p in pos]
print("  'Karen' clips: ", " ".join(f"{s:.2f}" for s in pos_scores))
print("  other speech:  ", " ".join(f"{peak(p):.2f}" for p in neg))
hits = sum(s >= cfg["threshold"] for s in pos_scores)
print(f"  caught {hits}/{len(pos)} 'Karen' clips at threshold {cfg['threshold']}")

print("\nTraining voice verifier...")
# normpath: openWakeWord derives the model name by splitting the path on os.sep
train_custom_verifier(positive_reference_clips=pos, negative_reference_clips=neg,
                      output_path=cfg["verifier"], model_name=os.path.normpath(cfg["oww_model"]), **features)
print(f"Saved {cfg['verifier']}")
