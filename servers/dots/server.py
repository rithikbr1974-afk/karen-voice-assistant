"""Spaces and Dots, as an MCP server (stdio): Markdown pages in data/spaces, web reading for the Researcher,
and managing Dots (specialist helpers) and their scheduled tasks. Local and free; web search uses ddgs.

Pages (core/spaces.py) are "Space/Page" or "Space/Parent/Child". Every overwrite keeps the old version; deleting
moves the page to the Spaces trash, and Karen asks first.
"""
import functools
import os
import re
import sys

from mcp.server import MCPServer

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "servers", "explore"))
from core import dots, spaces  # noqa: E402  (shared with Karen's window and Remote)

mcp = MCPServer("dots")


# ---------------------------------------------------------------- pages
def guarded(fn):
    """Return errors as text, so the Dot can read what went wrong instead of failing."""
    @functools.wraps(fn)  # keeps the signature, which the MCP SDK turns into the tool schema
    def wrap(*a, **k):
        try:
            return fn(*a, **k)
        except (ValueError, OSError) as e:
            return f"Error: {e}"
    return wrap


@mcp.tool()
@guarded
def list_pages(space: str = "") -> str:
    """List Spaces and their pages (all Spaces, or the pages of one Space)."""
    found = spaces.pages()
    if space:
        found = [p for p in found if p[0].lower().startswith(space.lower().strip("/") + "/")]
    if not found:
        return "No pages yet." if not space else f"No pages in '{space}'."
    return "\n".join(f"{i['name']}  ({i['words']} words, edited {i['edited'].replace('T', ' ')})"
                     for i in (spaces.info(*p) for p in found))


@mcp.tool()
@guarded
def open_page(page: str) -> str:
    """Read a page from Spaces ('Space/Page', or part of its name)."""
    name, full = spaces.find(page)
    return f"# Page: {name}\n\n{spaces.read(full)}"


@mcp.tool()
@guarded
def search_pages(query: str) -> str:
    """Find pages whose name or text contains the words."""
    return "\n".join(f"{n}: ...{snip}..." for n, snip in spaces.search(query)) or f"No pages mention '{query}'."


@mcp.tool()
@guarded
def save_page(page: str, content: str, append: bool = False) -> str:
    """Create or overwrite a Markdown page 'Space/Page title' (append=true adds to the end). Old versions are kept."""
    name = spaces.save(page, content, append)
    return f"Saved page {name} ({len(content.split())} words)."


@mcp.tool()
@guarded
def edit_page(page: str, find: str, replace: str) -> str:
    """Replace exact text in a page (for small edits without rewriting it). Old version is kept."""
    return f"Edited page {spaces.edit(page, find, replace)}."


@mcp.tool()
@guarded
def delete_page(page: str) -> str:
    """Move a page to the Spaces trash (it can be restored from data/spaces/.trash)."""
    return f"Moved page {spaces.trash(page)} to the trash."


# ---------------------------------------------------------------- web (Researcher)
@mcp.tool()
@guarded
def web_search_text(query: str, pages: int = 4) -> str:
    """Search the web and return the readable text of the top pages, with their links."""
    import report  # servers/explore/report.py: ddgs search + trafilatura text
    urls = report.search(query, n=pages + 3)
    if not urls:
        return "The web search returned nothing (or is busy). Try other words."
    got = report.fetch_pages(urls, keep=max(1, min(pages, 5)), chars=1600)
    if not got:
        return "Found results but couldn't read any of the pages:\n" + "\n".join(urls)
    return "\n\n".join(f"## Source: {u}\n{t}" for u, t in got)


@mcp.tool()
@guarded
def read_url(url: str) -> str:
    """Read the main text of one web page."""
    import report
    if not re.match(r"https?://", url):
        return "Error: give a full http(s) link."
    _, text = report._page(url)
    return f"## Source: {url}\n{text[:4000]}" if text else f"Couldn't read text from {url}."


# ---------------------------------------------------------------- dots and their schedules
@mcp.tool()
def list_dots() -> str:
    """List the user's Dots (specialist helpers) and what they do."""
    return "\n".join(f"{d['name']}: {d['role']} (abilities: {d.get('abilities')}, saves to {d.get('space', 'Inbox')})"
                     for d in dots.dots()) or "No Dots."


@mcp.tool()
def create_dot(name: str, role: str, abilities: str = "writing", instructions: str = "", space: str = "") -> str:
    """Create or update a Dot. abilities: research | writing | coding | organising."""
    name = spaces.clean(name).title()
    if not name or abilities not in dots.ABILITIES:
        return f"Error: give a name, and abilities as one of {', '.join(dots.ABILITIES)}."
    items = [d for d in dots.dots() if d["name"].lower() != name.lower()]
    items.append({"name": name, "role": role, "instructions": instructions, "abilities": abilities,
                  "space": spaces.clean(space) or name, "private": abilities in ("coding", "organising")})
    dots.save_dots(items)
    return f"{name} is ready. Say 'ask {name} to …'."


@mcp.tool()
def delete_dot(name: str) -> str:
    """Remove a Dot (its pages stay)."""
    items = dots.dots()
    keep = [d for d in items if d["name"].lower() != name.lower().removeprefix("the ")]
    if len(keep) == len(items):
        return f"There's no Dot called {name}."
    dots.save_dots(keep)
    return f"Removed {name}."


@mcp.tool()
def schedule_dot_task(dot: str, task: str, at: str, days: str = "daily") -> str:
    """Have a Dot do a task regularly. at = 24-hour 'HH:MM'; days = daily | weekdays | weekends | 'mon,wed,fri'."""
    d = dots.find(dot)
    if not d:
        return f"There's no Dot called {dot}. Dots: " + ", ".join(x["name"] for x in dots.dots())
    m = re.match(r"^(\d{1,2})[:.](\d{2})$", at.strip())
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return "Error: give the time as 24-hour HH:MM, e.g. 08:00."
    at = f"{int(m.group(1)):02d}:{m.group(2)}"
    picked = dots.parse_days(days)
    items = dots.jobs()
    job = {"id": max([j["id"] for j in items], default=0) + 1, "dot": d["name"], "task": task, "at": at,
           "days": picked, "next": dots.next_run(at, picked).isoformat(timespec="minutes")}
    dots.save_jobs(items + [job])
    when = "every day" if len(picked) == 7 else ", ".join(x.title() for x in picked)
    return f"Scheduled #{job['id']}: {d['name']} will '{task}' at {at}, {when}. First run {job['next'].replace('T', ' ')}."


@mcp.tool()
def list_dot_tasks() -> str:
    """List the Dots' scheduled tasks."""
    return "\n".join(f"#{j['id']} {j['dot']} at {j['at']} ({','.join(j['days'])}): {j['task']}"
                     + (" [paused]" if j.get("paused") else "") for j in dots.jobs()) or "No scheduled tasks."


@mcp.tool()
def cancel_dot_task(task_id: int) -> str:
    """Stop a scheduled Dot task by its number."""
    items = dots.jobs()
    keep = [j for j in items if j["id"] != task_id]
    if len(keep) == len(items):
        return f"No scheduled task #{task_id}."
    dots.save_jobs(keep)
    return f"Cancelled scheduled task #{task_id}."


if __name__ == "__main__":
    mcp.run()
