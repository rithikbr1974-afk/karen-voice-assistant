"""Karen's hand-gesture window, as an MCP server: opens hands.py (3D viewer, phone mirror, gesture training).

The window runs on its own (pythonw hands.py ...); these tools only start it or hand it a request, so they return at
once and work from voice, the typed box and Telegram (the window opens on this PC). Hand tracking stays on this PC.
"""
import ctypes
import os
import subprocess
import sys
import uuid

from mcp.server import MCPServer

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MODELS3D = (".glb", ".gltf", ".fbx", ".obj", ".stl", ".vrm")
KNOWN = {"Desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641", "Documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
         "Downloads": "374DE290-123F-4565-9164-39C4925E467B", "Pictures": "33E28130-4E1E-4676-835A-98395C3BC3BB",
         "3D Objects": "31C0DD25-9439-4F12-BF41-7FF4EDA38722", "Videos": "18989B1D-99B5-455B-841C-AB7C74E4DDFC"}
mcp = MCPServer("hands")


def known_folder(name):
    """Where Windows really keeps a user folder (his Desktop is on another drive, in OneDrive)."""
    buf = ctypes.c_wchar_p()
    ok = ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(ctypes.c_buffer(uuid.UUID(KNOWN[name]).bytes_le, 16)),
                                                    0, None, ctypes.byref(buf)) == 0
    path = buf.value if ok else os.path.join(os.path.expanduser("~"), name)
    ctypes.windll.ole32.CoTaskMemFree(buf)
    return path


def folders():
    places = [known_folder(n) for n in KNOWN] + [os.path.join(ROOT, "data", "uploads"), os.path.join(ROOT, "models")]
    return [p for p in dict.fromkeys(places) if os.path.isdir(p)]


def find_model(name):
    """A 3D file by path, or by words in its name (newest match) in his user folders, uploads and Karen's models."""
    path = os.path.expandvars(os.path.expanduser(name.strip().strip('"')))
    if os.path.isfile(path) and path.lower().endswith(MODELS3D):
        return path
    words = [w for w in name.lower().replace("_", " ").replace("-", " ").split()
             if w not in {"the", "my", "a", "3d", "model", "file", "object"} and not w.lstrip(".") in
             {e.lstrip(".") for e in MODELS3D}]
    hits, seen = [], 0
    for top in folders():
        for dirpath, dirs, files in os.walk(top):
            if dirpath.count(os.sep) - top.count(os.sep) >= 4:
                dirs[:] = []
            dirs[:] = [d for d in dirs if not d.startswith(".") and d.lower() not in {"node_modules", "appdata"}]
            for f in files:
                seen += 1
                if f.lower().endswith(MODELS3D) and all(w in f.lower().replace("_", " ").replace("-", " ") for w in words):
                    full = os.path.join(dirpath, f)
                    hits.append((os.path.getmtime(full), full))
            if seen > 60000:
                break
    return max(hits)[1] if hits else None


STATUS = os.path.join(ROOT, "data", "hands_status.json")


def launch(*args, wait=30):
    """Start hands.py (or hand the request to the open window) and return its real outcome: (ok, message).
    Karen used to say "mirroring your phone" while the window was showing "I can't reach your phone"."""
    import json
    import time
    rid = f"{time.time():.3f}"
    exe = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    subprocess.Popen([exe if os.path.exists(exe) else sys.executable, os.path.join(ROOT, "hands.py"), *args, "--id", rid],
                     cwd=ROOT, creationflags=0x00000008 | 0x00000200)  # detached: the window outlives this call
    end = time.time() + wait
    while time.time() < end:
        time.sleep(0.3)
        try:
            with open(STATUS, encoding="utf-8") as f:
                got = json.load(f)
        except (OSError, ValueError):
            continue
        if got.get("id") == rid:
            return bool(got.get("ok")), str(got.get("message", ""))
    return False, "The hands window didn't answer in time; it may still be opening."


HOW = " Hold an open palm up for 1 second to turn hand control on (and again to turn it off)."


@mcp.tool()
def open_3d_viewer(file: str = "") -> str:
    """Open Karen's 3D viewer with hand-gesture control (webcam hand tracking): rotate, zoom, move a 3D model with
    your hands. file: a path or words from the file's name (searched in Desktop, Downloads, Documents, Pictures,
    3D Objects, uploads and Karen's models folder; GLB, GLTF, FBX, OBJ, STL, VRM). Empty = open the viewer to drop/pick a file."""
    if file.strip():
        path = find_model(file)
        if not path:
            ok, msg = launch("--model", "")
            return (f"I couldn't find a 3D file matching '{file}' in your folders, so I opened the empty 3D viewer: "
                    "drop the file on it or click OPEN FILE." if ok else msg)
        ok, msg = launch("--model", path)
        return msg + (HOW if ok else "")
    ok, msg = launch("--model", "")
    return ("The 3D viewer is open. Drop a 3D file on it or click OPEN FILE." + HOW) if ok else msg


@mcp.tool()
def mirror_phone() -> str:
    """Show the phone's screen on the PC (screen mirroring, scrcpy) and let the user control it with hand gestures
    from the webcam: point to move, pinch-hold to tap, two fingers to scroll, palm swipe left for back, fist for home.
    Also for "mirror my phone", "cast my phone", "phone on my laptop". Say exactly what the result says: if the
    phone couldn't be reached, nothing is mirrored."""
    ok, msg = launch("--phone")
    return (msg + HOW) if ok else ("NOT mirrored. " + msg)


@mcp.tool()
def hand_training() -> str:
    """Start the hand-gesture training session (about 5 minutes: each gesture in normal, dim and bright light) so
    hand tracking recognises the user's own hands better."""
    ok, msg = launch("--train")
    return "Hand training is open. Follow the card: each gesture for 3 seconds, in three kinds of light." if ok else msg


@mcp.tool()
def close_hands() -> str:
    """Close the hand-gesture window and stop phone mirroring."""
    ok, msg = launch("--stop", wait=8)
    return msg if ok else "The hands window wasn't open."


if __name__ == "__main__":
    mcp.run()
