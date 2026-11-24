"""Record the user's voice for testing and tuning (saved under voice_tests/, git-ignored).

  python scripts/record_samples.py         test phrases -> voice_tests/NN.wav + phrases.txt (STT accuracy)
  python scripts/record_samples.py wake    40x "Karen" -> voice_tests/karen/, other speech -> voice_tests/other/,
                                           15 s of room sound -> voice_tests/room.wav
                                           (then: python scripts/train_wake.py)
"""
import os
import sys
import time
import wave

import numpy as np

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ".")
from core.audio import Mic  # noqa: E402

PHRASES = [
    "Karen",
    "Hey Karen",
    "Karen, what time is it?",
    "Karen, any new emails from my college?",
    "Karen, what's on my calendar tomorrow?",
    "Karen, reply to that saying I'll check and get back.",
    "Karen, create a document called rover notes.",
    "Karen, turn off the hall light.",
    "Karen, set the AC to twenty four degrees.",
    "Karen, open Chrome and play some music.",
    "I was just talking to my friend about the exam.",
    "Karen, what is the capital of Japan?",
]
TIPS = ["normal voice", "a bit louder", "softer", "faster", "slower", "from further away (lean back)",
        "like you're calling her from across the room", "casually, half-mumbled"]
WAKE = [("Karen", TIPS[i % len(TIPS)]) for i in range(30)] + [("Hey Karen", TIPS[i % len(TIPS)]) for i in range(10)]
OTHER = [
    "Can you open the window, it's hot in here.",
    "My friend Karan is coming over tonight.",
    "I'm caring for my grandmother this week.",
    "Let's go to the garden after lunch.",
    "Get in the car, we're late.",
    "We finally got him to agree.",
    "Kevin and Gavin are in my class.",
    "The current is too strong to swim.",
    "Kiran called me in the morning.",
    "Turn the car around, we missed the exit.",
    "I need to finish my project report today.",
    "What are you doing this weekend?",
    "Okay, I'll check and get back to you.",
    "Can I get a cup of coffee?",
    "Carry the bag to the room.",
    "That's correct, I agree with you.",
    "Hello, how are you doing today?",
    "I'm going to the market in the evening.",
    "Play some music on the speaker.",
    "Did you see the cricket match yesterday?",
]


def save(path, audio):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())


def record(mic, lines, folder):
    os.makedirs(folder, exist_ok=True)
    for i, line in enumerate(lines):
        text, tip = line if isinstance(line, tuple) else (line, "")
        while True:
            print(f"[{i + 1}/{len(lines)}] Say: {text}" + (f"   ({tip})" if tip else ""))
            mic.flush()
            audio = mic.listen(timeout=10)
            if audio is not None:
                break
            print("   didn't hear anything, again...")
        peak = float(np.abs(audio).max())
        print(f"   got {len(audio) / 16000:.1f}s, level {peak:.3f}{'  (quiet!)' if peak < 0.05 else ''}")
        save(f"{folder}/{i:02d}.wav", np.concatenate([np.zeros(8000, np.float32), audio, np.zeros(8000, np.float32)]))


mic = Mic("models/silero_vad.onnx", silence_ms=900)
print("Read each line out loud, then pause. Ctrl+C to stop.\n")
if sys.argv[1:] == ["wake"]:
    print("Part 1 of 3: the wake word. Follow the hint in brackets so Karen learns how you really say it.\n")
    record(mic, WAKE, "voice_tests/karen")
    print("\nPart 2 of 3: normal sentences WITHOUT the wake word.\n")
    record(mic, OTHER, "voice_tests/other")
    print("\nPart 3 of 3: stay quiet for 15 seconds so Karen learns your room's background sound...")
    mic.flush()
    end, chunks = time.time() + 15, []
    while time.time() < end:
        chunks.append(mic.read())
    save("voice_tests/room.wav", np.concatenate(chunks))
    print("\nDone. Tell Claude the wake recordings are ready.")
else:
    record(mic, PHRASES, "voice_tests")
    with open("voice_tests/phrases.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(PHRASES) + "\n")
    print("\nDone. Tell Claude the recordings are ready.")
