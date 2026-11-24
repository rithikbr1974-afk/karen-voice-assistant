"""Karen's developer helpers, as an MCP server (stdio): find projects, open them in VS Code, a few git commands.

Only fixed git commands run (status, log, diff summary, pull, commit, push), never arbitrary shell. Commit and push
need the user's spoken yes (config.yaml confirm). Projects: folders under dev.project_roots (git repos are marked) plus
dev.projects names.
"""
import difflib
import glob
import os
import shutil
import subprocess
import winreg

import yaml
from mcp.server import MCPServer

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CFG = (yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8")) or {}).get("dev", {})
mcp = MCPServer("dev")


def projects():
    """name -> folder: configured ones, then folders under the project roots (up to two levels deep)."""
    found = {str(k).lower(): os.path.expandvars(str(v)) for k, v in (CFG.get("projects") or {}).items()}
    for root in CFG.get("project_roots", []):
        root = os.path.expandvars(os.path.expanduser(root))
        for pattern in ("*/", "*/*/"):
            for path in glob.glob(os.path.join(root, pattern)):
                path = path.rstrip("\\/")
                name = os.path.basename(path).lower()
                if name.startswith((".", "$", "_")) or name in ("node_modules", "venv", ".venv", "system volume information"):
                    continue
                if pattern == "*/*/" and not os.path.isdir(os.path.join(path, ".git")):
                    continue  # deeper folders only count when they are git projects
                found.setdefault(name, path)
    return found


def find(name):
    projs = projects()
    key = name.lower().strip()
    hit = key if key in projs else next((n for n in sorted(projs, key=len) if key in n), None)
    hit = hit or next(iter(difflib.get_close_matches(key, list(projs), n=1, cutoff=0.6)), None)
    return (hit, projs[hit]) if hit else (None, None)


def git(path, *args, timeout=60):
    if not os.path.isdir(os.path.join(path, ".git")):
        return None, f"{os.path.basename(path)} isn't a git project."
    r = subprocess.run(["git", *args], cwd=path, capture_output=True, text=True, timeout=timeout,
                       creationflags=0x08000000, encoding="utf-8", errors="replace")
    out = (r.stdout + ("\n" + r.stderr if r.returncode else "")).strip()
    return r.returncode, out[:3000]


@mcp.tool()
def list_projects() -> str:
    """The user's projects (git ones are marked)."""
    projs = projects()
    return "\n".join(f"{n}{' (git)' if os.path.isdir(os.path.join(p, '.git')) else ''}: {p}" for n, p in sorted(projs.items())) \
        or "No projects found; add folders to dev.project_roots in config.yaml."


@mcp.tool()
def open_project(name: str) -> str:
    """Open a project folder in VS Code."""
    hit, path = find(name)
    if not hit:
        return f"No project called '{name}'. Use list_projects."
    code = shutil.which("code")
    if not code:
        try:
            code = winreg.QueryValue(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\App Paths\Code.exe")
        except OSError:
            return "VS Code isn't installed."
    subprocess.Popen([code, path], creationflags=0x08000000)
    return f"Opened {hit} in VS Code."


@mcp.tool()
def open_in_editor(file: str, line: int = 0, project: str = "") -> str:
    """Open a file in VS Code at a line (e.g. where an error points). file: a full path, or a file name/relative
    path that is looked up in their projects (project narrows it)."""
    path = os.path.expandvars(os.path.expanduser(file.strip().strip('"')))
    if not os.path.isfile(path):
        roots = [find(project)[1]] if project and find(project)[1] else list(projects().values())
        name = path.replace("\\", "/").lstrip("./")
        hits = []
        for root in roots:
            hits += [h for h in glob.glob(os.path.join(root, "**", os.path.basename(name)), recursive=True)
                     if h.replace("\\", "/").endswith(name) and "node_modules" not in h and ".venv" not in h]
            if hits:
                break
        if not hits:
            return f"I couldn't find {file} in your projects."
        path = hits[0]
    code = shutil.which("code")
    if not code:
        try:
            code = winreg.QueryValue(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\App Paths\Code.exe")
        except OSError:
            return "VS Code isn't installed."
    subprocess.Popen([code, "-g", f"{path}:{max(1, int(line or 1))}"], creationflags=0x08000000)
    return f"Opened {os.path.basename(path)}" + (f" at line {line}" if line else "") + " in VS Code."


@mcp.tool()
def git_status(project: str) -> str:
    """Branch, whether it's ahead/behind, and which files changed in a git project."""
    hit, path = find(project)
    if not hit:
        return f"No project called '{project}'."
    _, out = git(path, "status", "--short", "--branch")
    return f"{hit}: {out or 'clean'}"


@mcp.tool()
def git_log(project: str, count: int = 5) -> str:
    """The latest commits of a git project."""
    hit, path = find(project)
    if not hit:
        return f"No project called '{project}'."
    _, out = git(path, "log", f"-{max(1, min(count, 20))}", "--pretty=format:%h %ar: %s")
    return f"{hit}:\n{out}"


@mcp.tool()
def git_changes(project: str) -> str:
    """Summary of uncommitted changes (files and lines changed), to write a commit message or review work."""
    hit, path = find(project)
    if not hit:
        return f"No project called '{project}'."
    _, stat = git(path, "diff", "HEAD", "--stat")
    _, untracked = git(path, "ls-files", "--others", "--exclude-standard")
    return f"{hit}:\n{stat or 'no changes to tracked files'}" + (f"\nNew files:\n{untracked}" if untracked else "")


@mcp.tool()
def git_pull(project: str) -> str:
    """Get the latest changes from the remote (git pull)."""
    hit, path = find(project)
    if not hit:
        return f"No project called '{project}'."
    code, out = git(path, "pull", "--ff-only", timeout=120)
    return f"{hit}: {'pulled' if code == 0 else 'pull failed'}: {out}"


@mcp.tool()
def git_commit(project: str, message: str) -> str:
    """Commit all changes in a project with a message (git add -A, then commit). Karen asks the user first."""
    hit, path = find(project)
    if not hit:
        return f"No project called '{project}'."
    git(path, "add", "-A")
    code, out = git(path, "commit", "-m", message)
    return f"{hit}: {'committed' if code == 0 else 'nothing committed'}: {out.splitlines()[0] if out else ''}"


@mcp.tool()
def git_push(project: str) -> str:
    """Push a project's commits to its remote. Karen asks the user first."""
    hit, path = find(project)
    if not hit:
        return f"No project called '{project}'."
    code, out = git(path, "push", timeout=120)
    return f"{hit}: {'pushed' if code == 0 else 'push failed'}: {out[-500:]}"


if __name__ == "__main__":
    mcp.run()
