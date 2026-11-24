"""Study mode's teacher tools, as an MCP server (stdio). Results end with "[[study: visual]]" or "[[study: lesson]]";
Karen then pushes data/study/<visual|lesson>.json into the study window (ui/study.html), opening study mode if needed.

visualize: a mind map, flowchart, timeline, chart, formula sheet or interactive explainer. Quick visuals come from the
free fast model (Groq gpt-oss); complex ones (explainers, simulations, "detailed") from Claude through the user's own
Claude subscription (servers/claude_code.py), with the free model as the fallback.
teach: a lesson: the best YouTube videos (ddgs, free), good websites, the steps, a 3-question quiz and a mind map.
"""
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from mcp.server import MCPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import claude_code  # noqa: E402  (servers/claude_code.py: his Claude subscription)
from openrouter import chat  # noqa: E402  (servers/openrouter.py: Groq first)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DIR = os.path.join(ROOT, "data", "study")
mcp = MCPServer("study")

COMPLEX = {"explainer", "interactive", "simulation", "animation"}
STYLES = "mind_map, flowchart, timeline, sequence, chart, formula, explainer (interactive, animated)"

SPEC = """Reply with ONE JSON object only, no markdown fence:
{"title": short title, "kind": "mermaid" | "chart" | "math", "points": [3 to 5 one-line key points],
 plus exactly one of:
 "mermaid": Mermaid code (kind mermaid): mindmap, flowchart TD/LR, timeline, sequenceDiagram or stateDiagram-v2.
    A mindmap is an indented tree with no arrows, e.g. "mindmap
  root((Cell))
    Parts
      Nucleus
    Types".
    Rules: plain ASCII labels; wrap any label with brackets, parentheses, commas or colons in double quotes,
    e.g. A["Light reactions (thylakoid)"]; no HTML, no %%init, no styling lines; at most 18 nodes; use \\n in JSON.
 "chart": a Chart.js 4 config {"type": "bar"|"line"|"pie"|"doughnut"|"radar"|"scatter", "data": {...},
    "options": {"plugins": {"title": {"display": true, "text": ...}}}} with real, correct numbers only.
 "math": [LaTeX display formulas, each a string, with a one-line meaning in \\text{} where useful].}
Pick the kind that teaches the idea best: processes -> flowchart, history -> timeline, overviews -> mindmap,
numbers/comparisons -> chart, physics/maths -> math."""

HTML_SPEC = """Make ONE self-contained interactive explainer as an HTML fragment (no <html>/<head>), with inline <style>
and <script> only: no external files, fonts, images or network requests (they are blocked). Black background (#000),
yellow/amber accents (#facc15, #f59e0b), light text (#fdf8e1), Segoe UI. Fit a ~700x500 area and scale down nicely.
Use SVG or canvas, with labelled parts, a short animation or a slider/buttons he can play with, and 2-3 caption lines.
It must be scientifically correct. Then reply with ONE JSON object only, no markdown fence:
{"title": short title, "kind": "html", "html": the fragment as a JSON string, "points": [3 to 5 one-line key points]}"""


def _json(text):
    text = text.replace("�", "-")  # gpt-oss sometimes emits a broken dash
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0) if m else text)


def _fast(prompt, tokens=2500):
    out = chat(None, [{"role": "user", "content": prompt}], max_tokens=tokens, timeout=60)
    return out if isinstance(out, str) else str(out)


SPECIAL = r"()\[{:;,&<>#|"  # never the closing ] or }: the label ends there
LABELS = [re.compile(r'(\b[\w-]+)(\[)(?!")([^\]\n"]*[' + SPECIAL + r'][^\]\n"]*?)(\])(?=\s*(-|&|$|\n|:::))'),
          re.compile(r'(\b[\w-]+)(\{)(?!")([^}\n"]*[' + SPECIAL + r'][^}\n"]*?)(\})(?=\s*(-|&|$|\n|:::))')]


def fix_mermaid(code):
    """Models often write A[Light reactions (thylakoid)], which Mermaid can't parse: quote such flowchart labels."""
    code = code.replace("\\n", "\n").strip()
    if re.match(r"\s*mindmap\b", code) and "-->" in code:
        return _mindmap_tree(code)
    if not re.match(r"\s*(flowchart|graph)\b", code):
        return code
    for label in LABELS:
        code = label.sub(lambda m: f'{m.group(1)}{m.group(2)}"{m.group(3)}"{m.group(4)}', code)
    return code


def _mindmap_tree(code):
    """A "mindmap" written with flowchart arrows (root --> A, A --> B): rebuild it as the indented tree Mermaid wants."""
    names, kids, root = {}, {}, None
    for line in code.splitlines()[1:]:
        line = line.strip()
        m = re.match(r"([\w-]+)\s*(\(\(.*\)\)|\[.*\]|\(.*\))?\s*$", line)
        if m and "-->" not in line:
            root = root or m.group(1)
            if m.group(2):
                names[m.group(1)] = m.group(2).strip("()[]")
            continue
        m = re.match(r"([\w-]+)\s*(?:\S*?)-->\s*(?:\|[^|]*\|)?\s*([\w-]+)", line)
        if m:
            root = root or m.group(1)
            kids.setdefault(m.group(1), []).append(m.group(2))
    out, seen = ["mindmap"], set()

    def walk(node, depth):
        if node in seen or depth > 6:
            return
        seen.add(node)
        label = names.get(node, node.replace("_", " "))
        out.append("  " * depth + (f"root(({label}))" if depth == 1 else re.sub(r"[()\[\]{}]", "", label)))
        for k in kids.get(node, []):
            walk(k, depth + 1)
    if root:
        walk(root, 1)
    return "\n".join(out)


def _valid(v):
    if isinstance(v.get("mermaid"), str):
        v["mermaid"] = fix_mermaid(v["mermaid"])
    kind = v.get("kind")
    ok = (kind == "mermaid" and isinstance(v.get("mermaid"), str) and len(v["mermaid"]) > 10
          or kind == "chart" and isinstance(v.get("chart"), dict) and "data" in v["chart"]
          or kind == "math" and isinstance(v.get("math"), list) and v["math"]
          or kind == "html" and isinstance(v.get("html"), str) and len(v["html"]) > 100)
    if not ok:
        raise ValueError(f"the visual came back incomplete ({kind})")
    return v


def _save(name, data):
    os.makedirs(os.path.join(DIR, "visuals"), exist_ok=True)
    with open(os.path.join(DIR, f"{name}.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    if name == "visual":  # keep a dated copy: the window's history and his revision later
        with open(os.path.join(DIR, "visuals", f"{data['id']}.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)


def make_visual(topic, style="auto", detail="normal"):
    """Returns the saved visual spec. Claude for complex visuals, the free model otherwise; each falls back."""
    style, detail = (style or "auto").lower(), (detail or "normal").lower()
    use_claude = style in COMPLEX or detail in ("deep", "detailed", "complex")
    ask = f"Topic to visualise for a student: {topic}\nWanted style: {style}. Detail: {detail}."
    tries = ([("claude", HTML_SPEC if style in COMPLEX else SPEC)] if use_claude else []) + [("fast", SPEC), ("claude", SPEC)]
    errors = []
    for who, spec in tries:
        t = time.time()
        try:
            if who == "claude":
                text = claude_code.ask(f"{ask}\n\n{spec}", system="You make clear, correct study visuals. Output JSON only.")
            else:
                text = _fast(f"{ask}\n\n{spec}")
            v = _valid(_json(text))
        except Exception as e:  # noqa: BLE001  (bad JSON, a busy model, Claude not signed in: try the next)
            errors.append(f"{who}: {str(e)[:120]}")
            continue
        v.update(id=time.strftime("%Y%m%d-%H%M%S"), topic=topic, t=time.time(),
                 made_by=f"{'Claude' if who == 'claude' else 'quick AI'} in {time.time() - t:.0f} s")
        _save("visual", v)
        return v
    raise RuntimeError("Couldn't draw it: " + "; ".join(errors))


@mcp.tool()
def visualize(topic: str, style: str = "auto", detail: str = "normal") -> str:
    """Draw a study visual in the study window: mind map, flowchart, timeline, chart, formula sheet or an interactive
    animated explainer. ALWAYS call it when he asks to see, draw, show, map or visualise something, and whenever a
    picture would help him understand or revise; don't describe a diagram in words instead.

    Args:
        topic: what to show, specific, e.g. "how a bill becomes law in India" or "Ohm's law with an example".
        style: auto, mind_map, flowchart, timeline, sequence, chart, formula, or explainer (interactive/animated, slower).
        detail: normal, or detailed for a fuller visual (slower, drawn by Claude).
    """
    v = make_visual(topic, style, detail)
    return (f"Drew '{v['title']}' ({v['kind']}, {v['made_by']}). Key points: " + " | ".join(v.get("points", [])[:5])
            + "\nSay one or two sentences about it; don't read the points out.\n[[study: visual]]")


def _views(hit):
    m = re.search(r"viewCount'?:\s*(\d+)", str(hit.get("statistics", "")))
    return int(m.group(1)) if m else 0


def _minutes(d):
    parts = [int(x) for x in re.findall(r"\d+", d or "")][-3:]
    secs = 0
    for p in parts:
        secs = secs * 60 + p
    return secs / 60


def _youtube(query):
    """YouTube's own results page (no key): its ytInitialData lists videoRenderer entries in relevance order."""
    import urllib.parse
    import urllib.request
    url = "https://www.youtube.com/results?" + urllib.parse.urlencode({"search_query": query, "hl": "en", "gl": "IN"})
    req = urllib.request.Request(url, headers={"Accept-Language": "en", "User-Agent": "Mozilla/5.0 (Windows NT 10.0; "
                                               "Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"})
    page = urllib.request.urlopen(req, timeout=8).read().decode("utf-8", "replace")
    m = re.search(r"var ytInitialData = (\{.*?\});</script>", page, re.S)
    found = []

    def walk(o):
        if isinstance(o, dict):
            v = o.get("videoRenderer")
            if v and v.get("videoId"):
                found.append({"title": "".join(r.get("text", "") for r in v.get("title", {}).get("runs", [])),
                              "url": f"https://www.youtube.com/watch?v={v['videoId']}",
                              "channel": (v.get("ownerText", {}).get("runs") or [{}])[0].get("text", ""),
                              "duration": v.get("lengthText", {}).get("simpleText", ""),
                              "views": int(re.sub(r"\D", "", v.get("viewCountText", {}).get("simpleText", "")) or 0)})
            for x in o.values():
                walk(x)
        elif isinstance(o, list):
            for x in o:
                walk(x)
    if m:
        walk(json.loads(m.group(1)))
    return found


def _ddgs_videos(query):
    from ddgs import DDGS
    return [{"title": h["title"], "url": h["content"], "channel": h.get("uploader", ""), "duration": h.get("duration", ""),
             "views": _views(h)} for h in DDGS(timeout=8).videos(query, region="in-en", max_results=15)
            if "youtube.com/watch" in h.get("content", "")]


def find_videos(topic, n=3):
    """The best teaching videos: YouTube's relevance order, 2-40 minutes, 10k+ views; ddgs if YouTube fails."""
    for search in (_youtube, _ddgs_videos):
        try:
            hits = search(f"{topic} explained")
        except Exception:  # noqa: BLE001  (blocked, changed page, offline): try the next
            continue
        good = [h for h in hits if 2 <= _minutes(h["duration"]) <= 40 and h["views"] >= 10000]
        if good:
            return good[:n]
    return []


GOOD_SITES = ("khanacademy.org", "wikipedia.org", "britannica.com", "byjus.com", "geeksforgeeks.org", "ncert.nic.in",
              "libretexts.org", "physicsclassroom.com", "mathsisfun.com", "w3schools.com", "bbc.co.uk", "nasa.gov",
              "openstax.org", "toppr.com", "vedantu.com", "sciencedirect.com", "nature.com", "investopedia.com")


def find_sites(topic, n=4):
    from ddgs import DDGS
    hits = DDGS(timeout=8).text(topic, region="in-en", max_results=15, backend="duckduckgo, yahoo, mojeek")
    hits = [h for h in hits if h.get("href", "").startswith("http") and "youtube.com" not in h["href"]]
    hits.sort(key=lambda h: not any(s in h["href"] for s in GOOD_SITES))  # trusted teaching sites first
    return [{"title": h.get("title", h["href"]), "url": h["href"], "snippet": h.get("body", "")[:160]} for h in hits[:n]]


LESSON = """Make a short lesson plan for a student. Reply with ONE JSON object only, no markdown fence:
{"intro": one friendly sentence on what he'll learn, "steps": [4 to 6 short learning steps, simplest first],
 "quiz": [3 objects {"q": question, "options": [4 short options], "answer": index of the right option 0-3}],
 "mermaid": a Mermaid mindmap of the topic (plain ASCII labels, at most 14 nodes, use \\n in JSON)}"""


def _plan(topic, level):
    ask = f"Topic: {topic}\nLevel: {level}\n\n{LESSON}"
    try:
        return _json(_fast(ask, 2500))
    except Exception:  # noqa: BLE001  (Groq's 8K tokens/min limit, bad JSON): Claude's quick model instead
        return _json(claude_code.ask(ask, system="You plan short lessons. Output JSON only.", model="haiku"))


@mcp.tool()
def teach(topic: str, level: str = "school") -> str:
    """Teach him a topic visually in the study window: plays the best YouTube video, lists good websites, the steps,
    a quick quiz and a mind map. ALWAYS call this for "teach me ...", "help me learn ...", "explain ... with videos";
    never answer those in plain text, he wants the lesson on screen.

    Args:
        topic: the topic, e.g. "Newton's laws of motion".
        level: school, college or beginner.
    """
    with ThreadPoolExecutor(3) as pool:
        videos = pool.submit(find_videos, topic)
        sites = pool.submit(find_sites, f"{topic} {level} explained")
        plan = pool.submit(_plan, topic, level)
        try:
            videos = videos.result()
        except Exception:  # noqa: BLE001
            videos = []
        try:
            sites = sites.result()
        except Exception:  # noqa: BLE001
            sites = []
        try:
            p = plan.result()
        except Exception:  # noqa: BLE001
            p = {}
    lesson = {"topic": topic, "level": level, "intro": p.get("intro", ""), "steps": p.get("steps", []),
              "quiz": [q for q in p.get("quiz", []) if isinstance(q, dict) and len(q.get("options", [])) >= 2],
              "videos": videos, "sites": sites, "t": time.time()}
    _save("lesson", lesson)
    out = [f"Lesson on {topic} is in the study window."]
    if videos:
        out.append(f"Playing: {videos[0]['title']} ({videos[0]['channel']}, {videos[0]['duration']}).")
    else:
        out.append("No video found.")
    out.append(f"{len(sites)} websites, {len(lesson['steps'])} steps, {len(lesson['quiz'])} quiz questions.")
    markers = "[[study: lesson]]"
    if isinstance(p.get("mermaid"), str) and len(p["mermaid"]) > 10:
        _save("visual", {"id": time.strftime("%Y%m%d-%H%M%S"), "title": f"{topic}: mind map", "kind": "mermaid",
                         "mermaid": fix_mermaid(p["mermaid"]), "points": p.get("steps", [])[:4], "topic": topic, "t": time.time(),
                         "made_by": "quick AI"})
        markers = "[[study: visual]]\n[[study: lesson]]"  # the lesson tab ends up in front
    return " ".join(out) + "\nIntroduce the lesson in one or two sentences; the video is already playing.\n" + markers


@mcp.tool()
def play_video(topic_or_url: str, start_seconds: int = 0) -> str:
    """Play a YouTube video in the study window: a link, or the best video found for a topic.

    Args:
        topic_or_url: a YouTube link, or what the video should explain.
        start_seconds: where to start, in seconds.
    """
    if "youtu" in topic_or_url:
        videos = [{"title": "Video", "url": topic_or_url.strip(), "channel": "", "duration": "", "start": start_seconds}]
    else:
        videos = find_videos(topic_or_url, 3)
        if not videos:
            return f"No YouTube video found for {topic_or_url}."
        videos[0]["start"] = start_seconds
    _save("lesson", {"topic": topic_or_url if "youtu" not in topic_or_url else "Video", "videos": videos,
                     "sites": [], "steps": [], "quiz": [], "t": time.time()})
    return f"Playing {videos[0]['title']} in the study window.\n[[study: lesson]]"


if __name__ == "__main__":
    mcp.run()
