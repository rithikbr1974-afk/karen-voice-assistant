"""What's playing, pause / next / seek: Windows' media sessions, done so they can never freeze Karen.

Windows' media-session service can stop answering. On 2026-11-05 `GlobalSystemMediaTransportControlsSessionManager
.request_async()` never returned, in every process, and one waiting call had frozen Karen Island's song display for
hours (the watch thread sat in `asyncio.run` forever). So:
- every call runs on one worker thread with its own event loop and has a time limit (`TIMEOUT`). A call that runs over
  retires that worker (if Windows holds it, only that thread waits) and the next call starts a fresh one;
- the session manager is asked for once per worker and reused (Karen used to ask for a new one every 1-2 s);
- after a failure the service is left alone for a while (5 s, doubling up to 5 min) and the fallbacks answer: the song
  from the YouTube Music / Spotify window title, "playing" from that app's audio meter, and media keys for the buttons.
"""
import asyncio
import concurrent.futures
import ctypes
import ctypes.wintypes as wt
import re
import threading
import time

TIMEOUT = 3.0
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


class Unavailable(Exception):
    """The media-session service didn't answer (or isn't there); use the fallbacks."""


class _Worker:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.mgr = None
        started = threading.Event()
        threading.Thread(target=self._run, args=(started,), daemon=True, name="media-sessions").start()
        started.wait(2)

    def _run(self, started):
        ctypes.windll.ole32.CoInitializeEx(None, 0)  # multithreaded apartment: WinRT's async calls need no message pump
        asyncio.set_event_loop(self.loop)
        started.set()
        self.loop.run_forever()

    async def manager(self):
        if self.mgr is None:
            from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager
            self.mgr = await Manager.request_async()
        return self.mgr


STATE = {"worker": None, "rest_until": 0.0, "fails": 0, "retired": 0, "last_error": "", "probing": False}
_lock = threading.Lock()


def call(fn, timeout=TIMEOUT):
    """`await fn(manager)` on the worker thread. Raises Unavailable at once while the service is resting or being
    re-checked after a failure (the check runs in the background, so the caller never waits on a broken service),
    and after `timeout` if it doesn't answer."""
    with _lock:
        if time.time() < STATE["rest_until"]:
            raise Unavailable(STATE["last_error"] or "resting")
        if STATE["fails"]:
            if not STATE["probing"]:
                STATE["probing"] = True
                threading.Thread(target=_probe, daemon=True, name="media-probe").start()
            raise Unavailable("checking")
    return _call(fn, timeout)


def _probe():
    async def ping(mgr):
        mgr.get_current_session()  # any answer from the service will do
        return True
    try:
        _call(ping, TIMEOUT)
    except Unavailable:
        pass
    finally:
        STATE["probing"] = False


def _call(fn, timeout):
    with _lock:
        if STATE["worker"] is None:
            STATE["worker"] = _Worker()
        w = STATE["worker"]

    async def job():
        return await fn(await w.manager())
    fut = asyncio.run_coroutine_threadsafe(job(), w.loop)
    try:
        out = fut.result(timeout)
    except concurrent.futures.TimeoutError:
        fut.cancel()
        _failed(w, "timed out")
        raise Unavailable("timed out") from None
    except ImportError:
        _failed(w, "winrt missing", rest=3600)
        raise Unavailable("winrt missing") from None
    except Exception as e:  # a session that vanished mid-read etc.: the service did answer, so it counts as up
        with _lock:
            STATE["fails"] = 0
        raise Unavailable(f"{type(e).__name__}: {e}") from None
    with _lock:
        STATE["fails"] = 0
    return out


def _failed(w, why, rest=None):
    with _lock:
        if STATE["worker"] is w:
            STATE["worker"] = None
            STATE["retired"] += 1
            w.loop.call_soon_threadsafe(w.loop.call_later, 1, w.loop.stop)  # after the cancel lands; ends the thread
        STATE["fails"] += 1
        STATE["last_error"] = why
        STATE["rest_until"] = time.time() + (rest or min(300, 5 * 2 ** min(STATE["fails"] - 1, 6)))


def available():
    return time.time() >= STATE["rest_until"]


# ---------------------------------------------------------------- reading
def _pick(mgr):
    """The playing session (a paused Chrome video mustn't win over his music), else Windows' current one."""
    sessions = list(mgr.get_sessions())
    playing = [s for s in sessions if s.get_playback_info().playback_status == 4]
    return playing[0] if playing else mgr.get_current_session()


async def _thumbnail_bytes(thumbnail):
    from winrt.windows.storage.streams import Buffer, InputStreamOptions
    try:
        stream = await thumbnail.open_read_async()
        if not stream.size:
            return b""
        buf = Buffer(stream.size)
        return bytes(await stream.read_async(buf, stream.size, InputStreamOptions.READ_AHEAD))
    except Exception:
        return b""


def now(thumbnail=True):
    """The song: {title, artist, album, app, playing, pos, dur, at, thumb (bytes)}, or None when nothing is playing.
    Raises Unavailable when Windows' media service doesn't answer."""
    async def read(mgr):
        s = _pick(mgr)
        if not s:
            return None
        p = await s.try_get_media_properties_async()
        song = {"title": p.title or "", "artist": p.artist or "", "album": p.album_title or "",
                "app": s.source_app_user_model_id or "", "playing": s.get_playback_info().playback_status == 4,
                "at": time.time(), "source": "sessions"}
        try:
            tl = s.get_timeline_properties()
            pos, dur = tl.position.total_seconds(), tl.end_time.total_seconds()
            if song["playing"]:  # the position is from the last time the app reported it
                pos += max(0.0, time.time() - tl.last_updated_time.timestamp())
            if 0 < dur < 24 * 3600:
                song.update(pos=min(pos, dur), dur=dur)
        except Exception:
            pass
        song["thumb"] = await _thumbnail_bytes(p.thumbnail) if thumbnail and p.thumbnail else b""
        return song
    return call(read)


def playing_list():
    """[(title, artist)] of every playing session. Raises Unavailable."""
    async def read(mgr):
        out = []
        for s in mgr.get_sessions():
            if s.get_playback_info().playback_status == 4:
                p = await s.try_get_media_properties_async()
                out.append((p.title or "", p.artist or ""))
        return out
    return call(read)


# ---------------------------------------------------------------- controls
def control(action):
    """pause / resume / next / previous. Returns the titles it acted on ([] = nothing to act on). Pause hits every
    playing session; resume the current one when nothing plays; next/previous the playing (else current) one.
    Falls back to the keyboard's media keys when the service doesn't answer."""
    async def run(mgr):
        sessions = list(mgr.get_sessions())
        playing = [s for s in sessions if s.get_playback_info().playback_status == 4]
        current = mgr.get_current_session()
        if action == "pause":
            targets = playing
        elif action == "resume":
            targets = [] if playing else [current or (sessions[0] if sessions else None)]
        else:
            targets = playing[:1] or [current]
        done = []
        for s in [t for t in targets if t]:
            op = {"pause": s.try_pause_async, "resume": s.try_play_async, "next": s.try_skip_next_async,
                  "previous": s.try_skip_previous_async}[action]
            if await op():
                try:
                    done.append((await s.try_get_media_properties_async()).title or "the media")
                except Exception:
                    done.append("the media")
        return done
    try:
        return call(run)
    except Unavailable:
        return media_key(action)


def seek(seconds):
    """Jumps the playing song to `seconds`. False when it couldn't (the service is down: there's no key for that)."""
    async def run(mgr):
        s = _pick(mgr)
        return bool(s and await s.try_change_playback_position_async(int(max(0.0, float(seconds)) * 10_000_000)))
    try:
        return call(run)
    except Unavailable:
        return False


KEYS = {"pause": 0xB3, "resume": 0xB3, "next": 0xB0, "previous": 0xB1}  # VK_MEDIA_PLAY_PAUSE / NEXT / PREV_TRACK


def media_key(action):
    """The keyboard's media key, for when the sessions don't answer. Play/pause is one toggle, so pause is only
    pressed when something is heard playing, and resume only when nothing is."""
    if action not in KEYS:
        return []
    song = window_song()
    heard = song["playing"] if song else audible({"msedge.exe", "chrome.exe", "firefox.exe", "spotify.exe"})
    if (action == "pause" and not heard) or (action == "resume" and heard):
        return []
    user32.keybd_event(KEYS[action], 0, 1, 0)  # KEYEVENTF_EXTENDEDKEY
    user32.keybd_event(KEYS[action], 0, 1 | 2, 0)  # + KEYEVENTF_KEYUP
    return [song["title"] if song else "the media"]


# ---------------------------------------------------------------- fallback: window titles + audio meter
def _windows():
    """(title, exe name) of visible top-level windows."""
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def each(h, _):
        if user32.IsWindowVisible(h):
            n = user32.GetWindowTextLengthW(h) + 1  # long video titles: a fixed 400 could cut off " | YouTube Music"
            buf = ctypes.create_unicode_buffer(n)
            user32.GetWindowTextW(h, buf, n)
            if buf.value:
                pid = wt.DWORD()
                user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
                found.append((buf.value, _exe(pid.value)))
        return True
    user32.EnumWindows(each, 0)
    return found


def _exe(pid):
    h = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf, size = ctypes.create_unicode_buffer(600), wt.DWORD(600)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value.rsplit("\\", 1)[-1].lower()
        return ""
    finally:
        kernel32.CloseHandle(h)


YTM_TITLE = re.compile(r"^(?:YouTube Music - )?(.+?)(?: [|-] YouTube Music)$")


def window_song():
    """The song from the YouTube Music app's or Spotify's window title, or None. "playing" is whether that app is
    making sound right now (its audio meter)."""
    for title, exe in _windows():
        if title.endswith("YouTube Music"):
            m = YTM_TITLE.match(title)
            name = (m.group(1) if m else "").strip()
            if not name or name == "YouTube Music":
                continue
            # a video's title: "Song - Video Song | Film | Actor | ...", sometimes "Song #tag BTS😍|remix #shorts"
            raw = name
            name = re.sub(r"(^|\s)#\w+", " ", re.split(r"\s*\|\s*", name)[0])
            name = re.sub(r"\s{2,}", " ", name).strip() or raw
            song, artist = (name.split(" • ", 1) + [""])[:2]
            return {"title": song.strip(), "artist": artist.strip(), "album": "", "app": "music.youtube.com (window)",
                    "playing": audible({"msedge.exe", "chrome.exe"}), "at": time.time(), "thumb": b"",
                    "source": "window"}
        if exe == "spotify.exe" and " - " in title:  # "Artist - Song" while playing; "Spotify Premium" when paused
            artist, song = title.split(" - ", 1)
            return {"title": song.strip(), "artist": artist.strip(), "album": "", "app": "Spotify.exe",
                    "playing": audible({"spotify.exe"}), "at": time.time(), "thumb": b"", "source": "window"}
    return None


def audible(exes):
    """True if any audio session of these programs is making sound now (peak meter)."""
    try:
        import comtypes
        comtypes.CoInitialize()
        from pycaw.pycaw import AudioUtilities, IAudioMeterInformation
        for s in AudioUtilities.GetAllSessions():
            if s.Process and s.Process.name().lower() in exes:
                meter = s._ctl.QueryInterface(IAudioMeterInformation)
                if meter.GetPeakValue() > 0.0005:
                    return True
    except Exception:
        pass
    return False
