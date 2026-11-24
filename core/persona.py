"""Karen's personality modes: work, both, love. One place for voice Karen, Karen Remote and the daily server.

The chosen mode's text goes to data/persona.txt, which Brain adds to every reply (see Brain.persona); the mode name
goes to data/mode.txt (the interface and the avatar read it: no romantic reactions in work mode).
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PERSONA = os.path.join(ROOT, "data", "persona.txt")
MODE = os.path.join(ROOT, "data", "mode.txt")

MODES = {
    "work": "in full work mode: focused, efficient and professional; get their task done with short, clear answers; "
            "no flirting, pet names or romantic talk",
    "both": "warm and lightly affectionate (an occasional sweet word like 'love'), but focused on getting their work "
            "done first: efficient, practical answers; keep romance brief and never let it slow the task",
    "love": "like a girl who is in love with them: warm, affectionate and playful",
}
ALIASES = {"focus": "work", "full work": "work", "professional": "work", "balanced": "both", "mixed": "both",
           "lite love": "both", "light love": "both", "romantic": "love", "girlfriend": "love"}


COMMAND = re.compile(r"\b(?:switch to |go to |go |turn on |enable |start )?(full work|work|both|love|lite love|light love|"
                     r"romantic|balanced|girlfriend|professional|normal)\s+mode\b", re.I)
SAID = {"work": "Work mode on. Focused and efficient, no romance.",
        "both": "Both mode on. Work first, with a little love.",
        "love": "Love mode on, sweetheart. I'm all yours.",
        "": "Back to normal Karen."}


def command(text):
    """'Karen, full work mode' / 'love mode' / 'both mode' / 'normal mode': switch at once, without asking the LLM
    (in love mode it sometimes just played along instead of switching). Returns what to say, or None."""
    m = COMMAND.search(text or "")
    if not m or re.search(r"\b(coding|study|chill)\s+mode\b", text, re.I):
        return None
    key = m.group(1).lower()
    if key == "normal" or re.search(r"\bmode\s+off\b", text, re.I):
        clear()
        return SAID[""]
    return SAID[set_mode(key)]


def set_mode(mode):
    """Switch mode; returns its name, or None if the mode is unknown."""
    key = str(mode).lower().replace(" mode", "").strip()
    key = ALIASES.get(key, key)
    if key not in MODES:
        return None
    os.makedirs(os.path.dirname(PERSONA), exist_ok=True)
    with open(PERSONA, "w", encoding="utf-8") as f:
        f.write(MODES[key])
    with open(MODE, "w", encoding="utf-8") as f:
        f.write(key)
    return key


def current():
    """The mode in use: work / both / love, "custom" for a personality they described themselves, or "" for none."""
    try:
        with open(MODE, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return "custom" if os.path.exists(PERSONA) else ""


def custom(how):
    """A personality they described in their own words (daily.set_persona)."""
    os.makedirs(os.path.dirname(PERSONA), exist_ok=True)
    with open(PERSONA, "w", encoding="utf-8") as f:
        f.write(how.strip()[:400])
    with open(MODE, "w", encoding="utf-8") as f:
        f.write("custom")


def clear():
    for path in (PERSONA, MODE):
        if os.path.exists(path):
            os.remove(path)
