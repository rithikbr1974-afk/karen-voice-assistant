"""Karen's watchdog (2026-11-12, his choice): a Windows scheduled task runs this every 5 minutes. If Karen Remote isn't
running (it crashed, or Windows ended it), it starts it again. It leaves her off after /stop in Telegram
(data/stopped.flag, which Remote clears when it starts again at the next sign-in).

    python scripts/watchdog.py            check once (what the task runs, with pythonw, no window)
    python scripts/watchdog.py on | off   create / remove the scheduled task "Karen Remote watchdog"
    python scripts/watchdog.py status     is the task there, when did it last run
"""
import ctypes
import datetime
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TASK = "Karen Remote watchdog"
PYTHONW = os.path.join(ROOT, ".venv", "Scripts", "pythonw.exe")
FLAG = os.path.join(ROOT, "data", "stopped.flag")


def log(msg):
    with open(os.path.join(ROOT, "logs", "remote.log"), "a", encoding="utf-8") as f:
        f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] watch  {msg}\n")


def remote_running():
    """Remote holds the mutex Local\\KarenRemote once it's up; while it's still starting (the first seconds after
    sign-in) only its process shows, so look for that too."""
    handle = ctypes.windll.kernel32.OpenMutexW(0x00100000, False, r"Local\KarenRemote")  # SYNCHRONIZE
    if handle:
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    import psutil
    for p in psutil.process_iter(["cmdline", "cwd"]):
        try:
            cmd = " ".join(p.info["cmdline"] or []).lower()
            if "remote.py" in cmd and (p.info["cwd"] or "").lower() == ROOT.lower():
                return True
        except (psutil.Error, OSError):
            continue
    return False


def check():
    if os.path.exists(FLAG) or remote_running():
        return
    import psutil
    if (datetime.datetime.now().timestamp() - psutil.boot_time()) < 180:
        return  # Windows just started: the Startup shortcut is about to start Remote itself
    log("Karen Remote wasn't running: starting it again")
    flags = 0x00000200 | 0x08000000  # CREATE_NEW_PROCESS_GROUP, CREATE_NO_WINDOW
    try:  # out of the task's job, so the task finishing can never take Remote with it
        subprocess.Popen([PYTHONW, "remote.py", "--watchdog"], cwd=ROOT, creationflags=flags | 0x01000000,
                         close_fds=True)
    except OSError:
        subprocess.Popen([PYTHONW, "remote.py", "--watchdog"], cwd=ROOT, creationflags=flags, close_fds=True)


def ps(script):
    return subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True)


def quote(s):
    return "'" + s.replace("'", "''") + "'"


def install():
    """Every 5 minutes, as him, only while he's signed in (no password stored), on battery too, at most 2 minutes."""
    script = (
        f"$a = New-ScheduledTaskAction -Execute {quote(PYTHONW)} -Argument {quote('scripts/watchdog.py')} "
        f"-WorkingDirectory {quote(ROOT)}; "
        "$t = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5); "
        "$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable "
        "-MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 2); "
        f"Register-ScheduledTask -TaskName {quote(TASK)} -Action $a -Trigger $t -Settings $s -Force "
        f"-Description {quote('Starts Karen Remote again if it stopped by itself (not after /stop). ' + ROOT)} "
        "| Out-Null; 'ok'")
    r = ps(script)
    print("Watchdog on: every 5 minutes Windows checks that Karen Remote is running." if "ok" in r.stdout
          else f"Couldn't create the task: {r.stderr.strip()[:400]}")


def uninstall():
    r = ps(f"Unregister-ScheduledTask -TaskName {quote(TASK)} -Confirm:$false; 'ok'")
    print("Watchdog off." if "ok" in r.stdout else f"No watchdog task to remove ({r.stderr.strip()[:200]})")


def status():
    r = ps(f"$t = Get-ScheduledTask -TaskName {quote(TASK)} -ErrorAction Stop; $i = $t | Get-ScheduledTaskInfo; "
           "\"$($t.State) | last run $($i.LastRunTime) (result $($i.LastTaskResult)) | next $($i.NextRunTime) | "
           "every $($t.Triggers[0].Repetition.Interval)\"")
    print(r.stdout.strip() or "The watchdog task isn't there (python scripts/watchdog.py on).")


if __name__ == "__main__":
    os.chdir(ROOT)
    arg = (sys.argv[1:] or ["check"])[0]
    {"on": install, "off": uninstall, "status": status}.get(arg, check)()
