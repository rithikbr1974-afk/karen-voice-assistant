"""Download the local models into models/ (skips files that already exist)."""
import os
import sys
import urllib.request

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.makedirs("models/oww", exist_ok=True)

KOKORO = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/"
OWW = "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/"
ZOO = "https://github.com/opencv/opencv_zoo/raw/main/models/"
FILES = {
    "models/kokoro-v1.0.onnx": KOKORO + "kokoro-v1.0.onnx",  # full precision: int8 is 9x slower on CPU
    "models/voices-v1.0.bin": KOKORO + "voices-v1.0.bin",
    "models/oww/melspectrogram.onnx": OWW + "melspectrogram.onnx",  # wake word features;
    "models/oww/embedding_model.onnx": OWW + "embedding_model.onnx",  # your wake word: scripts/train_wake.py
    "models/silero_vad.onnx": "https://raw.githubusercontent.com/snakers4/silero-vad/master/src/silero_vad/data/silero_vad.onnx",
    # face unlock (core/face.py): OpenCV Zoo's YuNet detector and SFace recognizer, Apache-2.0
    "models/face_detection_yunet.onnx": ZOO + "face_detection_yunet/face_detection_yunet_2023mar.onnx",
    "models/face_recognition_sface.onnx": ZOO + "face_recognition_sface/face_recognition_sface_2021dec.onnx",
    # avatar mode: pixiv's free sample VRoid girl (its licence allows anyone to use, modify and redistribute it)
    "models/karen.vrm": "https://raw.githubusercontent.com/pixiv/three-vrm/dev/packages/three-vrm/examples/models/"
                        "VRM1_Constraint_Twist_Sample.vrm",
}


def progress(blocks, block_size, total):
    if total > 0:
        print(f"\r  {min(100, blocks * block_size * 100 // total)}%", end="", flush=True)


for path, url in FILES.items():
    if os.path.exists(path):
        print(f"ok   {path}")
        continue
    print(f"get  {path}")
    try:
        urllib.request.urlretrieve(url, path + ".part", progress)
    except Exception as e:
        sys.exit(f"\nDownload failed: {url}\n{e}")
    os.replace(path + ".part", path)
    print()

from faster_whisper import WhisperModel  # noqa: E402

for name in ("tiny.en", "small.en"):
    print(f"get  whisper {name}")
    WhisperModel(name, device="cpu", compute_type="int8", download_root="models")
print("Models ready.")
