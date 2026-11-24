"""Study mode (his request, 2026-10-29): Karen becomes "Mam", a teacher in black and yellow, with a study window
beside Zoom (notes, visualizer, teaching). One place for the on/off phrases and the state, read by voice Karen,
the window and the study server.

State: data/study.json {"on": bool, "since": epoch}. The study server writes visuals and lessons to data/study/.
"""
import json
import os
import re
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "data", "study.json")
DIR = os.path.join(ROOT, "data", "study")

_NAME = r"^\W*((hey\s+)?(karen|mam|ma'?am|madam)\W+)?(please\s+)?"
ON = re.compile(_NAME + r"((start|enter|open|turn on|switch (on|to)|go (in)?to|let'?s (start|do|go to)|begin)\s+)?"
                r"(the\s+)?study\s+mode(\s+on)?\W*$|" + _NAME + r"(let'?s|time to|i want to|start) (study|studying)\W*$",
                re.I)
OFF = re.compile(_NAME + r"((stop|exit|end|close|leave|quit|turn off|switch off)\s+(the\s+)?study(\s+mode|ing)?"
                 r"|study\s+mode\s+off|(i'?m\s+)?done studying)\W*$", re.I)

PROMPT = (" Study mode: in this mode you are called Mam, his patient teacher. Explain step by step in simple words, "
          "check he understood, and use the study tools (visualize, teach) whenever a picture, video or website would "
          "help him learn.")


def is_on():
    try:
        with open(STATE, encoding="utf-8") as f:
            return bool(json.load(f).get("on"))
    except (OSError, ValueError):
        return False


def set_on(on):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump({"on": bool(on), "since": time.time()}, f)


def command(text):
    """"study mode" / "study mode off" (and similar): True (on), False (off) or None (not a switch)."""
    if OFF.search(text):
        return False
    if ON.search(text):
        return True
    return None
