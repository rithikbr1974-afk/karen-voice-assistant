"""Meeting notes on/off phrases, caught before the LLM (servers/meeting does the work): "Karen, start taking meeting
notes" starts at once, "stop taking meeting notes" finishes. Questions about the meeting still go to the LLM."""
import re

_NAME = r"^(?:(?:hey|ok|okay)\s+)?(?:karen|mam|ma'?am|madam)?[\s,.!]*(?:please\s+|can you\s+|could you\s+)?"
START = re.compile(_NAME + r"(?:start|begin)\s+(?:taking\s+|making\s+|the\s+)?(?:meeting|lecture|class|call)\s+"
                   r"(?:notes|minutes)\b|" + _NAME + r"(?:start\s+)?(?:take|taking|make)\s+(?:meeting\s+)?(?:notes|minutes)"
                   r"\s+(?:of|for|in|during|on)\s+(?:this|the|my)\s+(?:meeting|lecture|class|call|zoom)\b|" + _NAME +
                   r"(?:take|taking)\s+meeting\s+(?:notes|minutes)\b|" + _NAME +
                   r"(?:start\s+)?record(?:ing)?\s+(?:this|the|my)\s+(?:meeting|zoom|call)\b(?!.*\b(?:screen|video)\b)",
                   re.I)
STOP = re.compile(_NAME + r"(?:stop|end|finish)\s+(?:taking\s+|making\s+|the\s+)?(?:meeting\s+)?(?:notes|minutes)\b|" +
                  _NAME + r"(?:stop|end|finish)\s+(?:recording\s+)?(?:the\s+|this\s+)?meeting(?:\s+notes)?\b|" + _NAME +
                  r"(?:the\s+)?meeting(?:'s|\s+is)\s+(?:over|done|finished)\b", re.I)


def command(text):
    """"start", "stop" or None."""
    text = text.strip()
    if STOP.search(text):
        return "stop"
    if START.search(text):
        return "start"
    return None
