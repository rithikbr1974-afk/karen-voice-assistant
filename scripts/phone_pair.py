"""Pair the user's Android phone with the PC once, for Karen's phone control (adb wireless debugging).

On the phone: Settings > About phone > tap "Build number" 7 times (Developer options), then
Settings > Developer options > Wireless debugging: ON > "Pair device with pairing code".
Keep that screen open, then run:  python scripts/phone_pair.py
After pairing, Karen reconnects by herself whenever Wireless debugging is on and the phone is on the same Wi-Fi.
"""
import os
import re
import shutil
import subprocess
import sys


def adb(*args):
    import glob
    exe = shutil.which("adb") or glob.glob(os.path.expandvars(
        r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Google.PlatformTools*\platform-tools\adb.exe"))[0]
    r = subprocess.run([exe, *args], capture_output=True, text=True, timeout=60)
    return (r.stdout + r.stderr).strip()


def main():
    print(__doc__)
    address = input("IP address & port shown under the pairing code (e.g. 192.168.1.5:37123): ").strip()
    code = input("Wi-Fi pairing code (6 digits): ").strip()
    out = adb("pair", address, code)
    print(out)
    if "Successfully paired" not in out:
        sys.exit("Pairing failed. Check the numbers (they change each time you open the pairing screen).")
    ip = address.split(":")[0]
    print("Now the main Wireless debugging screen shows 'IP address & Port' (a different port).")
    found = re.findall(r"_adb-tls-connect\._tcp\.?\s+([\d.]+:\d+)", adb("mdns", "services"))
    target = next((a for a in found if a.startswith(ip)), None) or \
        f"{ip}:" + input("Port shown under 'IP address & Port' on the Wireless debugging screen: ").strip()
    print(adb("connect", target))
    print(adb("devices"))
    print("Done. Ask Karen: 'what's my phone battery?'")


if __name__ == "__main__":
    main()
