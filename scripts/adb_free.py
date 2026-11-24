"""Free the phone for Android Studio (or Flutter, scrcpy, a web ADB tool): Karen stops using adb and lets go of the phone.

    python scripts/adb_free.py          Karen's background adb use stops for 4 hours and the adb server is stopped,
                                        so the next tool you start gets a clean server and the phone's USB connection
    python scripts/adb_free.py 8        the same, for 8 hours
    python scripts/adb_free.py off      Karen may use adb again (the phone watcher comes back within ~10 s)
    python scripts/adb_free.py status

You don't need this when Android Studio is open: Karen notices studio64.exe and stays off adb by herself. It is for
other tools, or to be extra safe before a deploy. Karen never restarts an adb server that someone stopped on purpose
for the next 15 minutes either (core/adbguard.py).
"""
import glob
import os
import shutil
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
from core import adbguard  # noqa: E402


def adb(*args):
    exe = shutil.which("adb") or next(iter(glob.glob(os.path.expandvars(
        r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Google.PlatformTools*\platform-tools\adb.exe"))), None)
    if not exe:
        return "adb isn't installed"
    r = subprocess.run([exe, *args], capture_output=True, text=True, timeout=30, creationflags=0x08000000)
    return (r.stdout + r.stderr).strip()


def status():
    left = adbguard.pause_status()
    print("Karen's background adb use:", f"PAUSED, {left // 60} min left" if left else "on")
    print("Android Studio open:       ", "yes (Karen stays off adb)" if adbguard.dev_tool_open() else "no")
    print("adb server running:        ", "yes" if adbguard.server_up() else "no")


def main():
    arg = sys.argv[1].lower() if len(sys.argv) > 1 else ""
    if arg == "status":
        return status()
    if arg == "off":
        adbguard.resume()
        print("Karen may use adb again. Her phone watcher comes back within about 10 seconds.")
        return status()
    hours = float(arg) if arg.replace(".", "", 1).isdigit() else 4
    adbguard.pause(int(hours * 60))
    adb("kill-server")
    print(f"Done. Karen won't touch adb for {hours:g} hours, and the adb server is stopped.")
    print("Now open Android Studio (or your other tool) and deploy to the phone.")
    print("When you're finished:  python scripts\\adb_free.py off")
    status()


if __name__ == "__main__":
    main()
