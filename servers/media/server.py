"""Karen's music and maps, as an MCP server: play a song in his YouTube Music app, pause/skip whatever is playing,
and show a Google Map in Karen Island. Free, no keys.

Songs: YouTube's own results page (no key) gives the video id, and the YouTube Music app (an Edge web app) is
launched straight at music.youtube.com/watch?v=<id>, which starts playing and keeps going with related songs. The
old YouTube Music window is closed so only one plays. Without the app it opens in the default browser.
Pause / resume / next / previous go through Windows' media sessions (the same controls as the keyboard's media
keys, but aimed at the right app). Logged to logs/pc_actions.log.
"""
import ctypes
import ctypes.wintypes as wt
import datetime
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import webbrowser

import yaml
from mcp.server import MCPServer

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
from core import media  # noqa: E402
ALL = yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8")) or {}
CFG = ALL.get("media") or {}
LOG = os.path.join(ROOT, "logs", "pc_actions.log")
YTM_APP = CFG.get("ytmusic_app_id", "cinhimbnkkaeohfgghhklpknlkffjgod")  # Edge's id for the YouTube Music web app
user32 = ctypes.windll.user32
mcp = MCPServer("media")


def log(action, detail=""):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {action:<12} {detail}\n")


# ---------------------------------------------------------------- finding the song
def search_youtube(query):
    """YouTube's results page (no key): [{id, title, channel, duration}] in relevance order."""
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
                found.append({"id": v["videoId"],
                              "title": "".join(r.get("text", "") for r in v.get("title", {}).get("runs", [])),
                              "channel": (v.get("ownerText", {}).get("runs") or [{}])[0].get("text", ""),
                              "duration": v.get("lengthText", {}).get("simpleText", "")})
            for x in o.values():
                walk(x)
        elif isinstance(o, list):
            for x in o:
                walk(x)
    if m:
        walk(json.loads(m.group(1)))
    return found


def pick(hits):
    """The first real song: skip Shorts-length clips and hour-long compilations unless that's all there is."""
    def minutes(d):
        parts = [int(p) for p in d.split(":") if p.isdigit()] if d else []
        return sum(p * 60 ** i for i, p in enumerate(reversed(parts))) / 60 if parts else 0
    for h in hits:
        if 1 <= minutes(h["duration"]) <= 15:
            return h
    return hits[0] if hits else None


# ---------------------------------------------------------------- the YouTube Music window
def _edge():
    import winreg
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            path = winreg.QueryValue(hive, r"Software\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe")
            if os.path.exists(path):
                return path
        except OSError:
            pass
    return None


def ytm_windows():
    """Visible top-level windows of the YouTube Music app (its title ends with "YouTube Music")."""
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def each(h, _):
        if user32.IsWindowVisible(h):
            buf = ctypes.create_unicode_buffer(300)
            user32.GetWindowTextW(h, buf, 300)
            if buf.value.endswith("YouTube Music"):
                found.append(h)
        return True
    user32.EnumWindows(each, 0)
    return found


def open_in_app(url):
    """Launch the YouTube Music app at url and close its older windows. False if the app didn't open (not installed:
    it is a packaged Edge web app, so there is no shortcut file to check for)."""
    edge = _edge()
    if not edge:
        return False
    old = set(ytm_windows())
    subprocess.Popen([edge, f"--app-id={YTM_APP}", f"--app-launch-url-for-shortcuts-menu-item={url}"],
                     creationflags=0x08000000)
    for _ in range(40):  # the new window: up to 8 s
        time.sleep(0.2)
        new = set(ytm_windows()) - old
        if new:
            for h in old:
                user32.PostMessageW(h, 0x0010, 0, 0)  # WM_CLOSE: the old window stops playing
            user32.ShowWindow(next(iter(new)), 6)  # minimised: it plays without covering his work (the island shows it)
            return True
    return bool(old)  # no new window: the open one took the song, or the app isn't there


# ---------------------------------------------------------------- Windows media sessions (core/media.py: time limits,
# and window title / media keys when Windows' media service stops answering)
def playing_now():
    try:
        return media.playing_list()
    except media.Unavailable:
        song = media.window_song()
        return [(song["title"], song["artist"])] if song and song["playing"] else []


# ---------------------------------------------------------------- tools
@mcp.tool()
def play_music(query: str) -> str:
    """ALWAYS use this to play a song, artist, album, playlist or kind of music ("play Kesariya", "play some lofi",
    "play Arijit Singh songs"). It plays in his YouTube Music app automatically; nothing else is needed.
    query: what to play, as he said it."""
    try:
        hits = search_youtube(query if re.search(r"song|music|lofi|mix|playlist|album", query, re.I) else f"{query} song")
    except Exception as e:  # noqa: BLE001
        return f"I couldn't search YouTube ({type(e).__name__}). Check the internet connection."
    hit = pick(hits)
    if not hit:
        return f"I couldn't find {query} on YouTube."
    url = f"https://music.youtube.com/watch?v={hit['id']}"
    where = "his YouTube Music app" if open_in_app(url) else "the browser"
    if where == "the browser":
        webbrowser.open(url)
    log("play_music", f"{query} -> {hit['title']} ({hit['id']}) in {where}")
    return f"Playing {hit['title']} by {hit['channel']} in {where}. Say just that, briefly."


@mcp.tool()
def music(action: str) -> str:
    """Pause, resume, skip or go back in whatever music or video is playing (YouTube Music, Spotify, a browser tab).
    action: pause (also stop), resume (also play / continue), next, or previous."""
    action = {"stop": "pause", "play": "resume", "continue": "resume", "skip": "next", "back": "previous",
              "prev": "previous"}.get(action.lower().strip(), action.lower().strip())
    if action not in ("pause", "resume", "next", "previous"):
        return "Use pause, resume, next or previous."
    try:
        done = media.control(action)
    except Exception as e:  # noqa: BLE001
        return f"I couldn't reach the media controls ({type(e).__name__})."
    log("music", f"{action}: {', '.join(done) or 'nothing'}")
    if not done:
        return "Nothing is playing." if action != "resume" else "There's nothing paused to resume."
    if action in ("next", "previous"):
        time.sleep(1.5)  # the app needs a moment to load the next track
        now = playing_now()
        if now and now[0][0]:
            return f"Now playing {now[0][0]}" + (f" by {now[0][1]}." if now[0][1] else ".")
    return {"pause": "Paused.", "resume": f"Playing {done[0]} again.", "next": "Skipped.",
            "previous": "Went back."}[action]


@mcp.tool()
def now_playing() -> str:
    """What song or video is playing right now."""
    try:
        now = playing_now()
    except Exception as e:  # noqa: BLE001
        return f"I couldn't read the media controls ({type(e).__name__})."
    if not now:
        return "Nothing is playing."
    return "; ".join(f"{t}" + (f" by {a}" if a else "") for t, a in now)


@mcp.tool()
def show_map(place: str, start: str = "") -> str:
    """ALWAYS use this to show a map, a place or directions ("open maps", "where is Marina Beach", "show me the way
    from VIT to Katpadi station"). A small Google Map opens at the top of his screen. place: the place or destination
    (empty = his home city). start: where directions start from (empty = just show the place)."""
    place = place.strip() or (ALL.get("daily") or {}).get("city") or "India"
    if start.strip():
        q = urllib.parse.urlencode({"saddr": start.strip(), "daddr": place, "output": "embed"})
        link = "https://www.google.com/maps/dir/?" + urllib.parse.urlencode({"api": 1, "origin": start.strip(),
                                                                           "destination": place})
        title = f"{start.strip()} → {place}"
    else:
        q = urllib.parse.urlencode({"q": place, "output": "embed"})
        link = "https://www.google.com/maps/search/?" + urllib.parse.urlencode({"api": 1, "query": place})
        title = place
    log("show_map", title)
    return f"[[map: https://maps.google.com/maps?{q}|{title}|{link}]] Shown the map of {title}. Say so briefly."


if __name__ == "__main__":
    mcp.run()
