"""Download the local models into models/ (skips files that already exist)."""
import os
import sys
import urllib.request

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.makedirs("models/oww", exist_ok=True)
os.makedirs("models/mediapipe", exist_ok=True)
os.makedirs("models/cortex", exist_ok=True)

KOKORO = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/"
OWW = "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/"
ZOO = "https://github.com/opencv/opencv_zoo/raw/main/models/"
MP = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@1.0.1/wasm/"
HF = "https://huggingface.co/"
FILES = {
    "models/kokoro-v1.0.onnx": KOKORO + "kokoro-v1.0.onnx",  # full precision: int8 is 9x slower on CPU
    "models/voices-v1.0.bin": KOKORO + "voices-v1.0.bin",
    "models/oww/melspectrogram.onnx": OWW + "melspectrogram.onnx",  # wake word features;
    "models/oww/embedding_model.onnx": OWW + "embedding_model.onnx",  # models/karen.onnx comes from Colab
    "models/silero_vad.onnx": "https://raw.githubusercontent.com/snakers4/silero-vad/master/src/silero_vad/data/silero_vad.onnx",
    # face unlock (core/face.py): OpenCV Zoo's YuNet detector and SFace recognizer, Apache-2.0
    "models/face_detection_yunet.onnx": ZOO + "face_detection_yunet/face_detection_yunet_2023mar.onnx",
    "models/face_recognition_sface.onnx": ZOO + "face_recognition_sface/face_recognition_sface_2021dec.onnx",
    # hand gestures (hands.py): MediaPipe hand tracking in the page, Apache-2.0 (tasks-vision 1.0.1, model float16 v1)
    "models/mediapipe/vision_wasm_internal.js": MP + "vision_wasm_internal.js",
    "models/mediapipe/vision_wasm_internal.wasm": MP + "vision_wasm_internal.wasm",
    "models/mediapipe/hand_landmarker.task":
        "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task",
    # Karen's Cortex (core/cortex): who is speaking = WeSpeaker ResNet34-LM (CC-BY-4.0, wenet-e2e/wespeaker);
    # what the words mean = all-MiniLM-L6-v2 int8 for AVX2 (Apache-2.0; this CPU has no AVX-512)
    "models/cortex/voice.onnx": HF + "Wespeaker/wespeaker-voxceleb-resnet34-LM/resolve/main/voxceleb_resnet34_LM.onnx",
    "models/cortex/words.onnx": HF + "sentence-transformers/all-MiniLM-L6-v2/resolve/main/onnx/model_quint8_avx2.onnx",
    "models/cortex/words_tokenizer.json": HF + "sentence-transformers/all-MiniLM-L6-v2/resolve/main/tokenizer.json",
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
