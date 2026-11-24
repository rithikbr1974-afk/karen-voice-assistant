"""Karen's Cortex, as an MCP server: her hearing memory (core/cortex/memory.py).

Karen's ear stores what is said around her while she waits for her wake word: the TV, videos, people in the room,
The user himself. Overheard text is kept 24 hours, on this PC only. These tools read it back.
"""
import datetime
import os
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

from mcp.server import MCPServer  # noqa: E402

from core.cortex.memory import Memory  # noqa: E402

mcp = MCPServer("brain")
memory = Memory()
words = None  # MiniLM, loaded on the first search (~0.5 s)


def who(ep):
    if ep["kind"] != "heard":
        return "you, to Karen"
    me, apps = ep.get("me"), [a for a in ep.get("playing") or [] if a not in ("python", "pythonw")]
    if me is not None and me >= 0.3:
        return "you (by your voice)"
    return f"from {', '.join(apps)}" if apps else "someone, or a speaker"


def line(ep):
    return f"[{datetime.datetime.fromtimestamp(ep['t']):%H:%M}] ({who(ep)}) {ep['text']}"


@mcp.tool()
def heard(question: str = "", minutes: int = 0) -> str:
    """What was said around Karen in the last 24 hours while she wasn't being spoken to: the TV, a video, people in
    the room, the user himself. Use for "what did that video just say", "what did he say about the price", "did
    you hear that", "what was I saying about my exam". minutes: > 0 returns everything heard in the last N minutes
    (for "just now" / "a minute ago"). question: what to look for (searches by meaning)."""
    if minutes > 0:
        eps = memory.recent(minutes)
        if not eps:
            return f"I didn't hear anything in the last {minutes} minutes."
        text = "\n".join(line(e) for e in eps)
        return text[-3000:]  # the newest part if it's long
    if not question.strip():
        return "Say what to look for, or how many minutes back."
    global words
    if words is None:
        from core.cortex.senses import Words
        words = Words()
    found = memory.recall(words.embed(question))
    if not found:
        return "Nothing I heard in the last 24 hours matches that."
    return "\n\n".join("\n".join(line(e) for e in group) for group in found)[:3000]


@mcp.tool()
def delete_heard() -> str:
    """Erase Karen's hearing memory: everything overheard, its audio clips and its numbers."""
    n = 0
    for ep in memory.episodes():
        memory._drop_clip(ep)
        n += 1
    if os.path.exists(memory.path):
        os.remove(memory.path)
    return f"Erased {n} things I'd heard."


@mcp.tool()
def hearing_status() -> str:
    """How much Karen's hearing memory holds right now."""
    eps = memory.episodes()
    day = [e for e in eps if e["kind"] == "heard" and e.get("text") and time.time() - e["t"] < 86400]
    clips = [e for e in eps if e.get("clip") and os.path.exists(e["clip"])]
    return (f"{len(day)} overheard lines from the last 24 hours, {sum(e['kind'] != 'heard' for e in eps)} requests, "
            f"{len(clips)} audio clips kept for learning (deleted after {memory.days} days).")


if __name__ == "__main__":
    mcp.run()
