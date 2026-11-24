"""Start Karen (voice, in the tray) or Karen Remote (phone chat, no microphone) when the user logs in to Windows.

    python scripts/autostart.py remote on     Karen Remote at sign-in (text only: Telegram/WhatsApp)
    python scripts/autostart.py remote off
    python scripts/autostart.py on | off      voice Karen at sign-in (he prefers starting her himself)
"""
import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
STARTUP = os.path.join(os.environ["APPDATA"], r"Microsoft\Windows\Start Menu\Programs\Startup")
TARGETS = {"karen": ("Karen.lnk", "main.py --background", "Karen voice assistant"),
           "remote": ("Karen Remote.lnk", "remote.py", "Karen Remote (phone chat, no microphone)"),
           }
PYTHONW = {}


def ps_quote(s):
    return "'" + s.replace("'", "''") + "'"


def main():
    args = sys.argv[1:]
    which = args.pop(0) if args and args[0] in TARGETS else "karen"
    if args not in (["on"], ["off"]):
        sys.exit(__doc__)
    name, arguments, description = TARGETS[which]
    link = os.path.join(STARTUP, name)
    if args == ["off"]:
        if os.path.exists(link):
            os.remove(link)
        print(f"{description} will no longer start with Windows.")
        return
    pythonw = PYTHONW.get(which) or os.path.join(ROOT, ".venv", "Scripts", "pythonw.exe")  # no console window
    script = (f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut({ps_quote(link)}); "
              f"$s.TargetPath = {ps_quote(pythonw)}; $s.Arguments = {ps_quote(arguments)}; "
              f"$s.WorkingDirectory = {ps_quote(ROOT)}; $s.Description = {ps_quote(description)}; $s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True)
    print(f"{description} will start with Windows. Shortcut: {link}")


if __name__ == "__main__":
    main()
