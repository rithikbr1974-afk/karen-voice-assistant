"""Karen's "do it on my phone" tool: Google's ARTEMIS agent (github.com/google/artemis, Apache-2.0), as an MCP server.

ARTEMIS looks at the phone screen (accessibility tree + screenshots) and taps/types step by step through any app. It is
installed in its own folder (config.yaml -> artemis.dir, own .venv, own .env with a Gemini key) and run as a subprocess
(`artemis run "<task>" --profile flash`), so none of its packages touch Karen's environment.

Privacy: ARTEMIS sends phone screenshots to Gemini (free tier: Google may use them). So Karen asks yes/no before EVERY
task (config confirm: artemis__phone_do), and tasks about money, passwords, codes or banking are refused here.
Logs: logs/artemis_actions.log. Results come from the session trace (traces/<session>/stdout.log).
"""
import ast
import datetime
import glob
import os
import re
import shutil
import subprocess
import threading

import sys

import yaml
from mcp.server import MCPServer

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from core import adbguard  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CFG = (yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8")) or {}).get("artemis", {})
DIR = os.path.expandvars(CFG.get("dir", ""))
EXE = os.path.join(DIR, ".venv", "Scripts", "artemis.exe")
mcp = MCPServer("artemis")
busy = threading.Lock()
# money, secrets and codes never go to a cloud model through this tool
PRIVATE = re.compile(r"\b(pay|payment|bank|banking|upi|gpay|google pay|phonepe|paytm|transfer money|send money|card|"
                     r"credit|debit|cvv|pin|otp|password|passcode|verification code|login|log in|sign in|wallet|"
                     r"invest|stock|crypto|bitcoin|loan)\b", re.I)


def log(what, detail):
    os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
    with open(os.path.join(ROOT, "logs", "artemis_actions.log"), "a", encoding="utf-8") as f:
        f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {what:12} {detail}\n")


def _adb():
    return shutil.which("adb") or (glob.glob(os.path.expandvars(
        r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Google.PlatformTools*\platform-tools\adb.exe")) or [""])[0]


def _run(cmd, timeout=30, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace", **kw)


LAST = os.path.join(ROOT, "data", "artemis_phone.txt")  # the phone's last adb address, to reconnect when mdns is silent


def _connected():
    """Make sure the phone is on adb (wireless debugging drops now and then): True if a device is attached."""
    adb = _adb()
    if not adb:
        return False

    def state():
        rows = [ln.split() for ln in _run([adb, "devices"]).stdout.splitlines()[1:] if ln.strip()]
        return [(r[0], r[1]) for r in rows if len(r) > 1]

    def attached():
        ok = [serial for serial, st in state() if st == "device"]
        if ok and ":" in ok[0]:
            os.makedirs(os.path.dirname(LAST), exist_ok=True)
            open(LAST, "w", encoding="utf-8").write(ok[0])
        return bool(ok)

    for serial, st in state():
        if st != "device":  # a stale "offline"/"unauthorized" entry blocks a fresh connection
            _run([adb, "disconnect", serial], timeout=10)
    if attached():
        return True
    addresses = re.findall(r"_adb-tls-connect\._tcp\.?\s+([\d.]+:\d+)", _run([adb, "mdns", "services"]).stdout)
    try:
        addresses.append(open(LAST, encoding="utf-8").read().strip())
    except OSError:
        pass
    for address in dict.fromkeys(a for a in addresses if a):
        try:
            _run([adb, "connect", address], timeout=12)
        except subprocess.TimeoutExpired:
            continue
        if attached():
            return True
    return False


def _result(output):
    """(status, explanation) of the finished task: from the session trace, else from the console text."""
    ids = re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", output)
    logs = [os.path.join(DIR, "traces", ids[-1], "stdout.log")] if ids else []
    logs += sorted(glob.glob(os.path.join(DIR, "traces", "*", "stdout.log")), key=os.path.getmtime, reverse=True)[:1]
    for path in logs:
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        found = re.findall(r"report_task_status\((\{.*\})\)", text)
        if found:
            try:
                data = ast.literal_eval(found[-1])
                return str(data.get("status", "")), str(data.get("explanation", ""))
            except (ValueError, SyntaxError):
                pass
    return ("completed" if "Task completed successfully" in output else "failed"), ""


@mcp.tool()
def phone_do(task: str) -> str:
    """Do a task on the user's Android phone by looking at its screen and tapping/typing through any app, like a
    person: "open YouTube and play lo-fi music", "open Settings and tell me the battery level", "in Maps find a
    route to the airport", "open Camera and take a photo". Use this INSTEAD of phone_operate for anything that
    needs tapping around in apps. task: the whole goal in one clear sentence, in English. Takes 30 s to 3 minutes.
    Not for calls or SMS (phone_call, phone_send_sms) and never for money, passwords or codes."""
    task = task.strip()
    if not task:
        return "What should I do on the phone?"
    if PRIVATE.search(task):
        return ("I don't do tasks about money, passwords or codes through the phone agent, because its screenshots go "
                "to a cloud model. Tell the user to do that one on the phone themselves.")
    if not os.path.exists(EXE):
        return f"The phone agent isn't installed (expected {EXE})."
    if adbguard.tools_blocked():
        return adbguard.MESSAGE
    if not busy.acquire(blocking=False):
        return "The phone agent is already working on a task. Wait for it to finish."
    try:
        if not _connected():
            return ("I can't reach the phone. Tell the user to turn on Wireless debugging (Settings > Developer "
                    "options) on the phone, on the same Wi-Fi as the PC.")
        log("task", task[:200])
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", NO_COLOR="1")
        if _adb():
            env["ADBUTILS_ADB_PATH"] = _adb()  # one adb binary for everything: Artemis's bundled copy stays unused
        try:
            r = _run([EXE, "run", task, "--profile", CFG.get("profile", "flash")], timeout=CFG.get("timeout_s", 240),
                     cwd=DIR, env=env)
            output = r.stdout + r.stderr
        except subprocess.TimeoutExpired:
            log("timeout", task[:100])
            return "The phone task took too long, so I stopped it. It may be partly done; check the phone."
        finally:
            try:  # ARTEMIS leaves a small server on port 8000 after a task: stop it so nothing keeps running
                _run([EXE, "stop"], timeout=30, cwd=DIR)
            except Exception:
                pass
        status, explanation = _result(output)
        log("result", f"{status}: {explanation[:200]}")
        if status == "completed":
            return f"Done on the phone. {explanation}".strip()
        return f"The phone task didn't finish ({status or 'failed'}). {explanation}".strip()
    finally:
        busy.release()


if __name__ == "__main__":
    mcp.run()
