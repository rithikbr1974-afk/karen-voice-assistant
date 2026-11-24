"""A longer training session for Karen: 40 x "Karen", 60 sentences, and the room's sound (saved in
voice_tests/session2/, git-ignored). Resumable: clips already recorded are skipped, so Ctrl+C any time and run it again.

  python scripts/record_session.py            record (close Karen first: she would answer you)
  python scripts/record_session.py --list     show everything you will be asked to say
  python scripts/record_session.py --redo     start this session over

Then: python scripts/train_wake.py   (trains on voice_tests/ plus every voice_tests/session*/ folder)
"""
import ctypes
import json
import os
import shutil
import sys
import time
import wave

import numpy as np

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ".")
FOLDER = "voice_tests/session2"

# 40 x the wake word: how to say each one (the point is to hear every way he really says it)
WAKE_TIPS = ([("normal voice, like calling her", 8), ("a bit louder", 4), ("softer, like late at night", 4),
              ("faster", 4), ("slower, drawn out", 4), ("from further away (lean back or stand up)", 4),
              ("across the room, raised voice", 4), ("casual, half-mumbled", 4),
              ("with music or a video playing at your normal volume", 4)])
WAKE = []
for tip, n in WAKE_TIPS:
    WAKE += [tip] * n
WAKE = [("Hey Karen" if (i + 1) % 5 == 0 else "Karen", tip) for i, tip in enumerate(WAKE)]  # every 5th: "Hey Karen"

COMMANDS = [
    "Karen, what's the weather like today?", "Karen, set a timer for ten minutes.",
    "Karen, remind me to call my mother at six thirty in the evening.", "Karen, any new emails from my college?",
    "Karen, what's on my calendar tomorrow?", "Karen, reply to that saying I'll check and get back.",
    "Karen, turn off the hall light.", "Karen, set the AC to twenty four degrees.",
    "Karen, open Chrome and play some music.", "Karen, set the volume to thirty percent.",
    "Karen, take a screenshot and show it to me.", "Karen, send a WhatsApp message to Rahul saying I'm on my way.",
    "Karen, what is the capital of Australia?", "Karen, research mode on for noise cancelling earbuds.",
    "Karen, make a research report on the Pixel nine a.", "Karen, open the camera and tell me what you see.",
    "Karen, coding mode on.", "Karen, full work mode.", "Karen, love mode.", "Karen, dance for me.",
    "Karen, add buy milk and eggs to my to-do list.", "Karen, what's the news today?",
    "Karen, create a document called project notes.", "Karen, minimize all windows.",
    "Karen, how much battery is left?", "Karen, summarise this page.",
    "Karen, play my most recent song on YouTube Music.",
    "Karen, remember that my locker number is two four six eight.", "Karen, stop talking.",
    "Karen, what's two hundred and fifty divided by five?",
]
TALK = [  # no wake word; many sound a bit like "Karen" on purpose
    "Can you open the window, it's really hot in here.", "My friend Karan is coming over tonight.",
    "I'm caring for my grandmother this week.", "Let's go to the garden after lunch.",
    "Get in the car, we're already late.", "We finally got him to agree.", "Kevin and Gavin are in my class.",
    "The current in the river is too strong.", "Kiran called me this morning.",
    "Turn the car around, we missed the exit.", "I need to finish my project report today.",
    "What are you doing this weekend?", "Okay, I'll check and get back to you.",
    "Can I get a cup of coffee, please?", "Carry the bag to the room.", "That's correct, I agree with you.",
    "Hello, how are you doing today?", "I'm going to the market in the evening.",
    "Play some music on the speaker.", "Did you see the cricket match yesterday?",
    "My career goals are changing every year.", "The carrot halwa at the canteen was amazing.",
    "Corinne sent the notes to everyone.", "Hey, wait for me at the gate.",
    "Can you hear the car horn outside?", "The exam starts at ten, so we should leave early.",
    "She's looking after her little cousins today.", "I can't find my phone charger anywhere.",
    "Let's talk about it after dinner.", "Okay, thanks a lot, see you tomorrow.",
]


def save(path, audio):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())


def voice_karen_running():
    handle = ctypes.windll.kernel32.OpenMutexW(0x00100000, False, r"Local\KarenVoiceAssistant")
    if handle:
        ctypes.windll.kernel32.CloseHandle(handle)
    return bool(handle)


def beep(sd):
    t = np.arange(int(16000 * 0.07)) / 16000
    sd.play((0.15 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32), 16000, blocking=True)


def problem(audio, kind):
    """Why a take should be redone, or None."""
    secs, peak = len(audio) / 16000, float(np.abs(audio).max())
    if peak < 0.04:
        return "too quiet: speak up or move closer"
    if peak > 0.98:
        return "too loud (distorted): a little softer or further back"
    if kind == "karen" and secs > 4:
        return "too long: just the one word"
    if kind == "karen" and secs < 0.2:
        return "too short"
    if kind == "sentence" and secs < 0.8:
        return "too short: say the whole sentence"
    return None


def record(mic, sd, items, folder, kind, title, notes=None):
    print(f"\n=== {title} ===")
    for i, item in enumerate(items):
        text, tip = item if isinstance(item, tuple) else (item, "")
        path = f"{folder}/{i:02d}.wav"
        if os.path.exists(path):
            continue
        if notes and i in notes:
            input(f"\n>>> {notes[i]}\n    Press Enter when ready... ")
        while True:
            print(f"\n[{i + 1}/{len(items)}]  Say:  {text}" + (f"      ({tip})" if tip else ""))
            mic.flush()
            beep(sd)
            time.sleep(0.15)
            mic.flush()
            audio = mic.listen(timeout=12)
            if audio is None:
                print("   didn't hear anything, again...")
                continue
            why = problem(audio, kind)
            if why:
                print(f"   {why}. Again...")
                continue
            break
        save(path, np.concatenate([np.zeros(8000, np.float32), audio, np.zeros(8000, np.float32)]))
        print(f"   ok ({len(audio) / 16000:.1f}s)")


def room(mic, sd, path, seconds, ask):
    if os.path.exists(path):
        return
    input(f"\n>>> {ask}\n    Press Enter, then stay quiet (don't talk) for {seconds} seconds... ")
    beep(sd)
    mic.flush()
    end, chunks = time.time() + seconds, []
    while time.time() < end:
        chunks.append(mic.read())
    save(path, np.concatenate(chunks))
    print("   ok")


def main():
    if "--list" in sys.argv:
        for title, items in (("40 x Karen", WAKE), ("30 commands", COMMANDS), ("30 normal sentences", TALK)):
            print(f"\n{title}")
            for i, it in enumerate(items, 1):
                print(f"  {i:2}. " + (f"{it[0]}   ({it[1]})" if isinstance(it, tuple) else it))
        return
    if voice_karen_running():
        sys.exit("Karen is open and would answer you. Close her window first, then run this again.")
    if "--redo" in sys.argv and os.path.exists(FOLDER):
        shutil.rmtree(FOLDER)
    import sounddevice as sd
    from core.audio import Mic
    mic = Mic("models/silero_vad.onnx", silence_ms=900)
    done = sum(os.path.exists(f"{FOLDER}/{d}/{i:02d}.wav") for d, n in (("karen", 40), ("commands", 30), ("other", 30))
               for i in range(n))
    print("Karen training session: 40 x Karen, 30 commands, 30 normal sentences, then the room's sound.")
    print("Sit where you normally sit when you talk to her. When you hear a short beep, say the line,")
    print("then pause. About 15 minutes. Ctrl+C any time: run it again and it carries on." +
          (f"  ({done}/100 already done)" if done else ""))
    notes = {36: "Now start some music or a video at your normal volume (the next 4 are said over it)."}
    record(mic, sd, WAKE, f"{FOLDER}/karen", "karen", "Part A: the wake word (40)", notes)
    input("\n>>> Stop the music/video now. Press Enter for the sentences... ")
    record(mic, sd, COMMANDS, f"{FOLDER}/commands", "sentence", "Part B: commands for Karen (30)")
    record(mic, sd, TALK, f"{FOLDER}/other", "sentence", "Part C: normal talk, no wake word (30)")
    print("\n=== Part D: your room ===")
    room(mic, sd, f"{FOLDER}/room.wav", 15, "Quiet as it normally is (fan, AC, whatever is usually on).")
    room(mic, sd, f"{FOLDER}/room_music.wav", 15, "Now play music or a video at your normal volume.")
    with open(f"{FOLDER}/phrases.json", "w", encoding="utf-8") as f:
        json.dump({"commands": COMMANDS, "other": TALK}, f, indent=1)
    print("\nAll done. Tell Claude 'the session is recorded' and I'll retrain her and test the result.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped. Run the same command again to carry on where you left off.")
