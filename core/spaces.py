"""Spaces: Markdown pages in data/spaces/<Space>/<Page>.md (subpages are folders). Shared by the dots MCP server
(Dots read and write pages) and the window's Spaces drawer.

Every overwrite keeps the old version in data/spaces/.history; deleting moves the page to data/spaces/.trash.
"""
import datetime
import os
import re
import shutil

from core.dots import SPACES

HIDDEN = (".history", ".trash")


def clean(part):
    part = re.sub(r'[<>:"\\|?*\x00-\x1f]', "-", part).strip(" .")
    return part[:80]


def path(page, space=""):
    """'Space/Page' -> (name, file path). A bare page name goes into `space` (or Inbox)."""
    parts = [clean(p) for p in re.split(r"[/\\]", (page or "").strip()) if clean(p)]
    if parts and parts[-1].lower().endswith(".md"):
        parts[-1] = parts[-1][:-3]
    if not parts or any(p in ("..", ".") or p in HIDDEN for p in parts):
        raise ValueError(f"'{page}' is not a page name. Use 'Space/Page title'.")
    if len(parts) == 1:
        parts = [clean(space) or "Inbox"] + parts
    return "/".join(parts), os.path.join(SPACES, *parts) + ".md"


def pages():
    """[(name, file path)] of every page."""
    out = []
    for base, dirs, files in os.walk(SPACES):
        dirs[:] = sorted(d for d in dirs if d not in HIDDEN)
        for f in sorted(files):
            if f.endswith(".md"):
                full = os.path.join(base, f)
                out.append((os.path.relpath(full, SPACES)[:-3].replace(os.sep, "/"), full))
    return out


def find(page):
    """The page he meant: exact name, else the only page whose name contains it."""
    try:
        name, full = path(page)
        if os.path.exists(full):
            return name, full
    except ValueError:
        pass
    want = (page or "").lower().replace(".md", "").strip()
    everything = pages()
    hits = [p for p in everything if p[0].lower().endswith("/" + want)] or [p for p in everything if want in p[0].lower()]
    if len(hits) == 1:
        return hits[0]
    if hits:
        raise ValueError(f"Several pages match '{page}': " + ", ".join(h[0] for h in hits[:8]))
    raise ValueError(f"No page called '{page}'. Use list_pages to see them.")


def read(full):
    return open(full, encoding="utf-8").read()


def info(name, full):
    st = os.stat(full)
    return {"name": name, "words": len(read(full).split()),
            "edited": datetime.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="minutes")}


def _keep_old(name, full):
    if os.path.exists(full):
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = os.path.join(SPACES, ".history", *name.split("/")) + f".{stamp}.md"
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(full, dest)


def save(page, content, append=False):
    """Create or overwrite (or append to) a page; returns its name."""
    name, full = path(page)
    _keep_old(name, full)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    text = content.strip() + "\n"
    if append and os.path.exists(full):
        text = read(full).rstrip() + "\n\n" + text
    open(full + ".tmp", "w", encoding="utf-8").write(text)
    os.replace(full + ".tmp", full)
    return name


def edit(page, find_text, replace):
    name, full = find(page)
    text = read(full)
    if find_text not in text:
        raise ValueError(f"That text isn't in {name}. Open the page and copy the exact text.")
    _keep_old(name, full)
    open(full, "w", encoding="utf-8").write(text.replace(find_text, replace, 1))
    return name


def trash(page):
    name, full = find(page)
    dest = os.path.join(SPACES, ".trash", *name.split("/")) + datetime.datetime.now().strftime(".%Y%m%d-%H%M%S.md")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.move(full, dest)
    folder = os.path.dirname(full)  # drop Spaces folders left empty
    while folder != SPACES and os.path.isdir(folder) and not os.listdir(folder):
        os.rmdir(folder)
        folder = os.path.dirname(folder)
    return name


def search(query, limit=8):
    """[(name, snippet)] best matches first."""
    words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 2] or [query.lower()]
    found = []
    for name, full in pages():
        text = read(full)
        low = (name + " " + text).lower()
        score = sum(low.count(w) for w in words)
        if score:
            i = max(0, min((low.find(w) for w in words if w in low), default=0) - len(name) - 60)
            found.append((score, name, re.sub(r"\s+", " ", text[i:i + 160]).strip()))
    found.sort(reverse=True)
    return [(n, s) for _, n, s in found[:limit]]
