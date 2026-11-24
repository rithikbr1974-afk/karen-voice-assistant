"""Claude through the user's own Claude subscription (his choice, 2026-10-29): the Claude Code CLI in print mode.

Used by the study server for complex visuals. His global ~/.claude/settings.json points the CLI at OpenRouter's free
model, so Karen runs it with only project settings (an empty folder has none), without the ANTHROPIC_* overrides, no
tools and no MCP servers: one plain answer from his logged-in account.
"""
import json
import os
import shutil
import subprocess

WORK = os.path.join(os.environ.get("LOCALAPPDATA", "."), "Karen", "claude-work")  # empty folder: no CLAUDE.md to load


def exe():
    return shutil.which("claude") or next((p for p in (os.path.expanduser(r"~\.local\bin\claude.exe"),)
                                           if os.path.exists(p)), None)


def ask(prompt, system="", model="sonnet", timeout=180):
    """Returns Claude's text answer; raises RuntimeError with a short reason if the CLI isn't there or fails."""
    path = exe()
    if not path:
        raise RuntimeError("Claude Code isn't installed")
    os.makedirs(WORK, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ANTHROPIC_", "CLAUDE_CODE_", "CLAUDECODE"))}
    cmd = [path, "-p", "--output-format", "json", "--model", model, "--setting-sources", "project",
           "--tools", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']
    if system:
        cmd += ["--system-prompt", system]
    p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=WORK, env=env, timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    out = p.stdout[p.stdout.find("{"):] if "{" in p.stdout else ""
    try:
        j = json.loads(out)
    except ValueError:
        raise RuntimeError(f"Claude Code gave no answer: {(p.stderr or p.stdout).strip()[:200]}")
    if j.get("is_error"):
        raise RuntimeError(f"Claude Code error: {str(j.get('result'))[:200]}")
    return j.get("result") or ""
