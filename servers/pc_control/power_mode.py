"""Power mode: hand one PC task to Open Interpreter (it writes and runs code). The user's choice, 2026-10-02.

Runs under the Python that has open-interpreter installed (config.yaml -> pc_control.power_mode.python), started by
the pc server's power_mode tool. Protocol: JSON lines prefixed "@@KAREN " on stdout; when a step looks destructive
it sends {"event": "confirm", ...} and waits for "yes" / "no" on stdin. Every code block is reported for the log.
"""
import json
import os
import re
import sys

PREFIX = "@@KAREN "
RISKY = [
    (r"\b(rm|del|erase|rmdir|rd)\b\s|Remove-Item|Clear-Content|Clear-RecycleBin|shutil\.rmtree|os\.(remove|unlink|rmdir)"
     r"|\.unlink\(|send2trash|Format-Volume|\bformat\s+[a-z]:", "delete files"),
    (r"Stop-Process|taskkill|\bkill\b|os\.kill|psutil\.Process\([^)]*\)\.(kill|terminate)", "close or kill programs"),
    (r"shutdown|Restart-Computer|Stop-Computer|SetSuspendState|logoff", "shut down, restart or sleep the PC"),
    (r"\breg(\.exe)?\s+(add|delete|import)|winreg\.(SetValue|CreateKey|DeleteKey|DeleteValue)|Set-ItemProperty"
     r"|New-ItemProperty|Remove-ItemProperty|New-Item\s+-Path\s+['\"]?HK", "change the registry"),  # reading is fine
    (r"pip\s+install|npm\s+(i|install)|winget|choco\s|msiexec|Install-", "install software"),
    (r"Invoke-WebRequest|\biwr\b|\bcurl\b|\bwget\b|Invoke-Expression|\biex\b|urlretrieve|requests\.(get|post)",
     "download or run things from the internet"),
    (r"Start-Process|subprocess\.|os\.system|os\.startfile|\.exe\b", "start other programs"),
    (r"icacls|takeown|Set-Acl|setx|schtasks|Set-Service|\bsc\s+(stop|delete|config)|netsh|bcdedit|Set-ExecutionPolicy",
     "change system settings"),
    (r"Move-Item|\bmove\b\s|shutil\.move|os\.rename|Rename-Item|\.rename\(|\.replace\(\s*['\"]?[A-Za-z]:",
     "move or rename files"),
]
CUSTOM = """You are running on the user's Windows PC on behalf of his voice assistant Karen.
- Prefer Python. Keep each code block small and do one step at a time.
- Work inside his user folders (Desktop, Documents, Downloads, Pictures, Music, Videos) unless the task says otherwise.
- Never delete permanently: move things to the Recycle Bin instead (send2trash). Never touch system folders,
  the registry, drivers, antivirus or security settings, and never install software unless the task asks for it.
- When the task is done, reply with ONE short plain sentence saying what you did (it will be spoken aloud)."""


FREE_MODEL = "inclusionai/ling-3.0-flash-sante:free"


def send(**event):
    print(PREFIX + json.dumps(event), flush=True)


def risk(code):
    return [label for pattern, label in RISKY if re.search(pattern, code, re.I)]


def main():
    task = sys.stdin.readline().strip()
    max_runs = int(os.environ.get("POWER_MODE_MAX_RUNS", "8"))
    from interpreter import OpenInterpreter
    oi = OpenInterpreter(auto_run=False, disable_telemetry=True, conversation_history=False, max_output=1500,
                         custom_instructions=CUSTOM)
    oi.llm.model = "openrouter/" + os.environ.get("POWER_MODE_MODEL", "deepseek/deepseek-v4.1-flash")
    oi.llm.api_key = os.environ["OPENROUTER_API_KEY"]
    oi.llm.supports_functions = True
    oi.llm.supports_vision = False
    oi.llm.context_window = 64000
    oi.llm.max_tokens = 2000
    complete = oi.llm.completions
    extra = {"provider": {"zdr": True}, "reasoning": {"enabled": False}}

    def completions(**p):
        try:
            yield from complete(**p, extra_body=extra)
        except Exception as e:  # out of OpenRouter credits (402): the free zero-retention model takes over
            if "402" not in str(e) and "credits" not in str(e).lower():
                raise
            oi.llm.model = p["model"] = "openrouter/" + FREE_MODEL
            yield from complete(**p, extra_body=extra, num_retries=4)
    oi.llm.completions = completions

    folders = json.loads(os.environ.get("POWER_MODE_FOLDERS", "{}"))
    if folders:  # his Desktop and Pictures are on another drive (OneDrive); saves the model hunting for them
        oi.custom_instructions += "\n- His folders: " + "; ".join(f"{k} = {v}" for k, v in folders.items())

    said, runs = "", 0
    stream = oi.chat(task, display=False, stream=True)
    for chunk in stream:
        kind = chunk.get("type")
        if chunk.get("role") == "assistant" and kind == "message" and isinstance(chunk.get("content"), str):
            said += chunk["content"]
        elif kind == "confirmation":
            code = chunk["content"]["content"]
            lang = chunk["content"]["format"]
            runs += 1
            send(event="code", language=lang, code=code, step=runs)
            if runs > max_runs:
                send(event="stopped", reason=f"stopped after {max_runs} steps")
                stream.close()
                return
            labels = risk(code)
            if labels:
                plan = re.sub(r"\s+", " ", said).strip().split(". ")[-1][:160] if said else ""
                send(event="confirm", risks=labels, plan=plan, step=runs)
                answer = sys.stdin.readline().strip().lower()
                if answer != "yes":
                    send(event="stopped", reason=f"The user said no, so it didn't {' or '.join(labels)}")
                    stream.close()
                    return
            said = ""
        elif chunk.get("role") == "computer" and kind == "console" and chunk.get("format") == "output":
            out = chunk.get("content") or ""
            if out:
                send(event="output", text=out[-1500:], step=runs)
    final = next((m["content"] for m in reversed(oi.messages)
                  if m.get("role") == "assistant" and m.get("type") == "message" and m.get("content")), "")
    send(event="done", summary=re.sub(r"\s+", " ", final).strip()[:400], steps=runs)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # report instead of dying silently
        send(event="error", text=f"{type(e).__name__}: {e}"[:400])
