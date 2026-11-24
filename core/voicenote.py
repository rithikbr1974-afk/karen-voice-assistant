"""Karen's own voice as a Telegram voice note (2026-11-12): Kokoro reads the text and ffmpeg makes OGG/Opus, which
Telegram plays as a voice message. It runs in its own short-lived process at low priority, so Karen Remote never
holds the ~0.5 GB voice model and voice Karen's own speech isn't slowed down.

    python -m core.voicenote <text file> <out.ogg>     (what make() runs; prints the length in seconds)
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "voice_notes")
MAX_CHARS = 1500  # about a minute and a half of speech; longer text is cut at a sentence end
NO_WINDOW, LOW = 0x08000000, 0x00004000  # CREATE_NO_WINDOW, BELOW_NORMAL_PRIORITY_CLASS


def speakable(text):
    """What Kokoro should read: no emojis, markdown, links or codes; money and symbols as words."""
    text = re.sub(r"https?://\S+|\((?:link|join link)\)", "", text, flags=re.I)
    # gpt-oss writes non-breaking hyphens and narrow spaces ("sign‑in", "4 250"); the emoji filter below would drop them
    text = re.sub(r"(?<=\d)[  ](?=\d{3}(?!\d))", ",", text)  # "4 250" -> "4,250"
    text = re.sub(r"[‐-‒]", "-", re.sub(r"[  -  ]", " ", text))
    text = re.sub(r"(?m)^[ \t]*[-*][ \t]+", "", text)  # list bullets
    text = re.sub(r"(?:₹|Rs\.?|INR)\s?([\d,]+(?:\.\d+)?)", r"\1 rupees", text)
    text = text.replace("&", " and ").replace("%", " percent").replace("•", ",").replace("—", ", ").replace("–", ", ")
    text = re.sub(r"[*_#`>|~\[\]]", "", text)
    text = re.sub(r"[^\x00-ɏ‘-”… ]", "", text)  # emojis etc. (same range as core.tts.clean)
    text = re.sub(r"[ \t]+", " ", re.sub(r"\s*\n\s*", "\n", text)).strip()
    text = re.sub(r"(?<![.!?:;,])\n", ". ", text).replace("\n", " ")  # a line is a sentence: pause after it
    if len(text) > MAX_CHARS:
        cut = text[:MAX_CHARS]
        end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
        text = cut[:end + 1] if end > MAX_CHARS // 2 else cut
    return text.strip()


def make(text, timeout=180, log=print):
    """Text -> data/voice_notes/<time>.ogg; returns (path, seconds) or (None, 0) if it couldn't be made."""
    text = speakable(text)
    if not text:
        return None, 0
    os.makedirs(OUT, exist_ok=True)
    for old in sorted(glob.glob(os.path.join(OUT, "*.ogg")))[:-20]:  # keep the last 20
        try:
            os.remove(old)
        except OSError:
            pass
    stamp = time.strftime("%Y%m%d-%H%M%S")
    src, out = os.path.join(OUT, f"{stamp}.txt"), os.path.join(OUT, f"{stamp}.ogg")
    with open(src, "w", encoding="utf-8") as f:
        f.write(text)
    env = dict(os.environ, OPENBLAS_NUM_THREADS="1", PYTHONUTF8="1")
    t = time.time()
    try:
        r = subprocess.run([sys.executable, "-m", "core.voicenote", src, out], cwd=ROOT, env=env, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=timeout,
                           creationflags=NO_WINDOW | LOW)
    except subprocess.TimeoutExpired:
        log("voice", f"voice note took over {timeout} s; sent the text only")
        return None, 0
    finally:
        try:
            os.remove(src)
        except OSError:
            pass
    if r.returncode or not os.path.exists(out):
        log("voice", f"voice note failed: {(r.stderr or '').strip()[-300:]}")
        return None, 0
    try:
        seconds = float((r.stdout or "0").strip().splitlines()[-1])
    except (ValueError, IndexError):
        seconds = 0
    log("voice", f"voice note {seconds:.0f} s of speech, made in {time.time() - t:.1f} s")
    return out, seconds


def _ffmpeg():
    exe = shutil.which("ffmpeg")
    if not exe:
        import imageio_ffmpeg  # the always-on PC kit has no ffmpeg install
        exe = imageio_ffmpeg.get_ffmpeg_exe()
    return exe


def _main(src, out):
    import numpy as np
    import yaml
    from kokoro_onnx import Kokoro
    os.chdir(ROOT)
    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))["tts"]
    try:  # the voice and speed he picked in Settings
        with open("settings.json", encoding="utf-8") as f:
            settings = json.load(f)
    except (OSError, ValueError):
        settings = {}
    with open(src, encoding="utf-8") as f:
        text = f.read()
    kokoro = Kokoro(cfg["model"], cfg["voices"])
    samples, rate = kokoro.create(text, voice=settings.get("voice") or cfg.get("voice", "af_heart"),
                                  speed=float(settings.get("speed") or cfg.get("speed", 1.0)), lang="en-us")
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
    subprocess.run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-f", "s16le", "-ar", str(rate), "-ac", "1",
                    "-i", "pipe:0", "-c:a", "libopus", "-b:a", "32k", "-application", "voip", out],
                   input=pcm, check=True, creationflags=NO_WINDOW)
    print(f"{len(samples) / rate:.1f}")


if __name__ == "__main__":
    _main(sys.argv[1], sys.argv[2])
