"""Keeps Karen's background adb use from fighting other tools (Android Studio, Flutter, scrcpy, web ADB tools).

Why: there can be only ONE adb server, and whichever starts first holds the phone's USB connection ("Device busy",
"connection lost" in the other tool). Karen's watchers (core/phonewatch.py, core/waphone.py) used to run adb every
1.5 s, so after `adb kill-server` (the usual fix) Karen started a new server within a second and grabbed the phone
again. Now background loops ask background_ok() first:
  - Android Studio (studio64.exe) is open, or `python scripts/adb_free.py` was run: Karen's background loops stay off
    adb completely (nothing polls, nothing restarts the server).
  - the server was running and someone stopped it (kill-server): Karen leaves adb alone for 15 minutes.
  - cold start (no server was ever seen by this process): Karen may start it, once.
Requests Karen is asked to do (battery, call, phone_do) still work whenever the server is up; while paused with the
server down they say so instead of starting it (tools_blocked()).
"""
import json
import os
import socket
import time

import psutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAUSE = os.path.join(ROOT, "data", "adb_pause.json")
COOLDOWN_S = 900  # after someone kills the adb server, Karen waits this long before starting it again
DEV_TOOLS = {"studio64.exe", "studio.exe"}  # Android Studio
_state = {"was_up": False, "down_at": 0.0, "tool_at": 0.0, "tool_open": False}


def server_up(port=5037):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def dev_tool_open():
    """Is Android Studio running? (checked at most every 5 s)"""
    now = time.time()
    if now - _state["tool_at"] > 5:
        _state["tool_at"] = now
        try:
            _state["tool_open"] = any((p.info["name"] or "").lower() in DEV_TOOLS
                                      for p in psutil.process_iter(["name"]))
        except Exception:
            _state["tool_open"] = False
    return _state["tool_open"]


def pause_status():
    """Seconds left of a manual pause (scripts/adb_free.py), 0 if none."""
    try:
        with open(PAUSE, encoding="utf-8") as f:
            return max(0, int(json.load(f).get("until", 0) - time.time()))
    except (OSError, ValueError):
        return 0


def pause(minutes=240):
    os.makedirs(os.path.dirname(PAUSE), exist_ok=True)
    with open(PAUSE, "w", encoding="utf-8") as f:
        json.dump({"until": time.time() + minutes * 60}, f)


def resume():
    try:
        os.remove(PAUSE)
    except OSError:
        pass


def paused():
    return pause_status() > 0 or dev_tool_open()


def background_ok():
    """May a background loop talk to adb right now?"""
    if paused():
        return False
    now = time.time()
    if server_up():
        _state["was_up"], _state["down_at"] = True, 0.0
        return True
    if _state["was_up"]:  # it was running and someone stopped it on purpose: don't resurrect it
        _state["down_at"] = _state["down_at"] or now
        if now - _state["down_at"] < COOLDOWN_S:
            return False
        _state["was_up"] = False
    return True


MESSAGE = ("I'm keeping my hands off the phone's USB debugging because Android Studio is open (or phone mode is off). "
           "Close Android Studio, or run scripts/adb_free.py off, and ask me again.")


def tools_blocked():
    """True when a user-requested phone action should not start a new adb server."""
    return paused() and not server_up()
