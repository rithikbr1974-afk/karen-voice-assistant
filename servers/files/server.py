"""Karen's file helpers, as an MCP server (stdio): read, describe, convert, merge/split, resize, show.

All local and free: pypdf / pypdfium2 (PDF), python-docx (Word text), Pillow (images), Microsoft Word / PowerPoint /
Excel through COM for Office -> PDF. Only describe_image uses the cloud (vision model, zero-data-retention).
Works on files added with the interface's + button (data/uploads) and files in the user folders.
Results go to Documents/Karen. A line "[[show: <path or url>]]" in a result makes Karen open it in her viewer.
"""
import base64
import ctypes
import datetime
import functools
import os
import re
import sys
import uuid

import yaml
from mcp.server import MCPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from openrouter import chat  # noqa: E402  (servers/openrouter.py: vision with a free fallback)

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CFG = (yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8")) or {}).get("files", {})
UPLOADS = os.path.join(ROOT, "data", "uploads")
TEXT_EXT = {".txt", ".md", ".csv", ".json", ".py", ".js", ".html", ".css", ".xml", ".yaml", ".yml", ".log", ".ini",
            ".c", ".cpp", ".h", ".java", ".ts", ".ino", ".sql", ".bat", ".ps1", ".tsv"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
OFFICE = {".doc": ("Word", 17), ".docx": ("Word", 17), ".rtf": ("Word", 17), ".odt": ("Word", 17),
          ".ppt": ("PowerPoint", 32), ".pptx": ("PowerPoint", 32), ".xls": ("Excel", 0), ".xlsx": ("Excel", 0)}
KNOWN = {"Desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641", "Documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
         "Downloads": "374DE290-123F-4565-9164-39C4925E467B", "Pictures": "33E28130-4E1E-4676-835A-98395C3BC3BB",
         "Music": "4BD8D571-6D19-48D3-BE97-422220080E43", "Videos": "18989B1D-99B5-455B-841C-AB7C74E4DDFC"}

mcp = MCPServer("files")


def known_folder(name):
    buf = ctypes.c_wchar_p()
    guid = uuid.UUID(KNOWN[name])
    ok = ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(ctypes.c_buffer(guid.bytes_le, 16)), 0, None,
                                                    ctypes.byref(buf)) == 0
    path = buf.value if ok else os.path.join(os.path.expanduser("~"), name)
    ctypes.windll.ole32.CoTaskMemFree(buf)
    return path


OUT = os.path.join(known_folder("Documents"), "Karen")
ALLOWED = [UPLOADS, OUT] + [known_folder(n) for n in KNOWN]


def resolve(path):
    """Accept a full path, "Desktop/x.pdf", or just the name of an uploaded file."""
    p = str(path).strip().strip('"')
    parts = [x for x in p.replace("\\", "/").split("/") if x and x != "~"]
    names = {k.lower(): k for k in KNOWN}
    if parts and parts[0].lower() in names:
        p = os.path.join(known_folder(names[parts[0].lower()]), *parts[1:])
    elif not os.path.isabs(p) and os.path.exists(os.path.join(UPLOADS, p)):
        p = os.path.join(UPLOADS, p)
    full = os.path.abspath(os.path.expanduser(p))
    if not any(full.lower().startswith(d.lower() + os.sep) or full.lower() == d.lower() for d in ALLOWED):
        raise ValueError("I can only use uploaded files and files in your user folders.")
    if not os.path.exists(full):
        raise ValueError(f"{path} doesn't exist.")
    return full


def out_path(name, ext):
    os.makedirs(OUT, exist_ok=True)
    base = re.sub(r'[<>:"/\\|?*]', "_", os.path.splitext(os.path.basename(name))[0])[:80]
    path = os.path.join(OUT, base + ext)
    n = 2
    while os.path.exists(path):
        path = os.path.join(OUT, f"{base} ({n}){ext}")
        n += 1
    return path


def size(n):
    return f"{n / 2**20:.1f} MB" if n > 2**20 else f"{n / 2**10:.0f} KB"


def pages_list(spec, total):
    """ "1-3,5" -> [0, 1, 2, 4] (zero-based); empty = all."""
    if not str(spec).strip():
        return list(range(total))
    out = []
    for part in str(spec).replace(" ", "").split(","):
        a, _, b = part.partition("-")
        lo, hi = int(a), int(b or a)
        out += [i - 1 for i in range(lo, hi + 1) if 1 <= i <= total]
    return out


def guarded(fn):
    """Return errors as text, so Karen can say what went wrong instead of failing."""
    @functools.wraps(fn)  # keeps the signature, which the MCP SDK turns into the tool schema
    def wrap(*a, **k):
        try:
            return fn(*a, **k)
        except ValueError as e:
            return str(e)
        except Exception as e:
            return f"That didn't work: {type(e).__name__}: {e}"[:300]
    return wrap


# ---------------------------------------------------------------- reading
@mcp.tool()
@guarded
def list_uploads() -> str:
    """Files the user added with the + button (newest first)."""
    if not os.path.isdir(UPLOADS) or not os.listdir(UPLOADS):
        return "No files have been added."
    files = sorted((os.path.join(UPLOADS, f) for f in os.listdir(UPLOADS)), key=os.path.getmtime, reverse=True)
    return "\n".join(f"{os.path.basename(f)} ({size(os.path.getsize(f))})" for f in files[:30])


@mcp.tool()
@guarded
def file_info(path: str) -> str:
    """What a file is: type, size, pages (PDF), dimensions (image)."""
    full = resolve(path)
    ext = os.path.splitext(full)[1].lower()
    info = [os.path.basename(full), size(os.path.getsize(full)),
            f"modified {datetime.datetime.fromtimestamp(os.path.getmtime(full)):%d %b %Y}"]
    if ext == ".pdf":
        from pypdf import PdfReader
        info.append(f"{len(PdfReader(full).pages)} pages")
    elif ext in IMAGE_EXT:
        from PIL import Image
        with Image.open(full) as im:
            info.append(f"{im.width}x{im.height} {im.format}")
    return ", ".join(info) + f". Path: {full}"


@mcp.tool()
@guarded
def read_file(path: str, pages: str = "") -> str:
    """Text of a PDF, Word document, or text/code/CSV file, to summarise, translate or answer questions about it.
    pages: for PDFs, e.g. "1-3" (empty = all)."""
    full = resolve(path)
    ext = os.path.splitext(full)[1].lower()
    if ext == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(full)
        idx = pages_list(pages, len(reader.pages))
        text = "\n".join(f"--- page {i + 1} ---\n{reader.pages[i].extract_text() or ''}" for i in idx)
        if len(re.sub(r"\s|--- page \d+ ---", "", text)) < 20:
            return "This PDF has no text layer (it's scanned images). Use pdf_to_images and describe_image instead."
    elif ext == ".docx":
        import docx
        text = "\n".join(p.text for p in docx.Document(full).paragraphs)
    elif ext in TEXT_EXT:
        text = open(full, encoding="utf-8", errors="replace").read()
    elif ext in IMAGE_EXT:
        return "That's an image: use describe_image."
    else:
        return f"I can't read {ext} files directly; try convert to PDF first."
    limit = CFG.get("read_chars", 12000)
    return text[:limit] + ("\n...(rest cut)" if len(text) > limit else "")


@mcp.tool()
@guarded
def describe_image(path: str, question: str = "") -> str:
    """Look at an image (photo, screenshot, scanned page) and describe it or answer a question about it."""
    full = resolve(path)
    from io import BytesIO
    from PIL import Image
    with Image.open(full) as im:
        im = im.convert("RGB")
        im.thumbnail((1280, 1280))
        buf = BytesIO()
        im.save(buf, "JPEG", quality=85)
    return chat(CFG.get("vision_model"), max_tokens=400, messages=[{"role": "user", "content": [
            {"type": "text", "text": question or "Describe this image in a few plain sentences, including any text in it."},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()}}]}])


# ---------------------------------------------------------------- converting
def _office_to_pdf(full, app):
    import comtypes
    import comtypes.client
    comtypes.CoInitialize()
    out = out_path(full, ".pdf")
    office = comtypes.client.CreateObject(f"{app}.Application")
    try:
        if app == "Word":
            doc = office.Documents.Open(full, ReadOnly=True)
            doc.SaveAs(out, FileFormat=17)
            doc.Close(False)
        elif app == "PowerPoint":
            deck = office.Presentations.Open(full, ReadOnly=True, WithWindow=False)
            deck.SaveAs(out, 32)
            deck.Close()
        else:
            book = office.Workbooks.Open(full, ReadOnly=True)
            book.ExportAsFixedFormat(0, out)
            book.Close(False)
    finally:
        office.Quit()
    return out


@mcp.tool()
@guarded
def convert(path: str, to: str) -> str:
    """Convert a file. to: "pdf" (from Word/PowerPoint/Excel/images/text), "txt" (from PDF/Word),
    "docx" (from PDF text or a text file), "png"/"jpg"/"webp" (images; for PDFs use pdf_to_images)."""
    full = resolve(path)
    ext, to = os.path.splitext(full)[1].lower(), to.lower().strip(". ")
    if to == "pdf" and ext in OFFICE:
        out = _office_to_pdf(full, OFFICE[ext][0])
    elif to == "pdf" and ext in IMAGE_EXT:
        return images_to_pdf(full)
    elif to == "pdf" and ext in TEXT_EXT:
        out = out_path(full, ".pdf")
        _text_pdf(open(full, encoding="utf-8", errors="replace").read(), out)
    elif to == "txt" and ext in (".pdf", ".docx"):
        out = out_path(full, ".txt")
        open(out, "w", encoding="utf-8").write(read_file(full, ""))
    elif to == "docx" and ext in ({".pdf"} | TEXT_EXT):
        import docx
        text = read_file(full, "") if ext == ".pdf" else open(full, encoding="utf-8", errors="replace").read()
        d = docx.Document()
        for para in text.split("\n"):
            if para.strip() and not para.startswith("--- page"):
                d.add_paragraph(para)
        out = out_path(full, ".docx")
        d.save(out)
    elif to in ("png", "jpg", "jpeg", "webp") and ext in IMAGE_EXT:
        from PIL import Image
        out = out_path(full, "." + to.replace("jpeg", "jpg"))
        with Image.open(full) as im:
            (im.convert("RGB") if to != "png" else im).save(out)
    else:
        return f"I can't convert {ext} to {to}."
    return f"Saved {os.path.basename(out)} ({size(os.path.getsize(out))}) in Documents/Karen.\n[[show: {out}]]"


def _text_pdf(text, out):
    """Plain text -> simple A4 PDF via Pillow pages (no extra dependencies)."""
    from PIL import Image, ImageDraw, ImageFont
    try:
        font = ImageFont.truetype("consola.ttf", 22)
    except OSError:
        font = ImageFont.load_default()
    lines = []
    for raw in text.splitlines():
        while len(raw) > 95:
            lines.append(raw[:95])
            raw = raw[95:]
        lines.append(raw)
    pages = []
    for i in range(0, max(1, len(lines)), 60):
        page = Image.new("RGB", (1240, 1754), "white")
        draw = ImageDraw.Draw(page)
        for j, line in enumerate(lines[i:i + 60]):
            draw.text((70, 70 + j * 27), line, fill="black", font=font)
        pages.append(page)
    pages[0].save(out, save_all=True, append_images=pages[1:], resolution=150)


@mcp.tool()
@guarded
def images_to_pdf(paths: str, name: str = "") -> str:
    """Combine one or more images (comma-separated paths or uploaded names) into a single PDF, in that order."""
    from PIL import Image
    files = [resolve(p) for p in paths.split(",") if p.strip()]
    imgs = [Image.open(f).convert("RGB") for f in files]
    out = out_path(name or files[0], ".pdf")
    imgs[0].save(out, save_all=True, append_images=imgs[1:], resolution=150)
    return f"Made {os.path.basename(out)} from {len(imgs)} image(s).\n[[show: {out}]]"


@mcp.tool()
@guarded
def pdf_to_images(path: str, pages: str = "", dpi: int = 130) -> str:
    """Save PDF pages as PNG images (e.g. pages "1-2"). Good for sharing a page or reading scanned PDFs."""
    import pypdfium2 as pdfium
    full = resolve(path)
    pdf = pdfium.PdfDocument(full)
    idx = pages_list(pages, len(pdf))[:30]
    outs = []
    for i in idx:
        img = pdf[i].render(scale=dpi / 72).to_pil()
        out = out_path(f"{os.path.splitext(os.path.basename(full))[0]} page {i + 1}", ".png")
        img.save(out)
        outs.append(out)
    return f"Saved {len(outs)} page image(s): " + ", ".join(os.path.basename(o) for o in outs) + f"\n[[show: {outs[0]}]]"


@mcp.tool()
@guarded
def merge_pdfs(paths: str, name: str = "merged") -> str:
    """Join PDFs (comma-separated paths or uploaded names, in order) into one."""
    from pypdf import PdfWriter
    files = [resolve(p) for p in paths.split(",") if p.strip()]
    w = PdfWriter()
    for f in files:
        w.append(f)
    out = out_path(name, ".pdf")
    with open(out, "wb") as fh:
        w.write(fh)
    return f"Merged {len(files)} PDFs into {os.path.basename(out)}.\n[[show: {out}]]"


@mcp.tool()
@guarded
def extract_pages(path: str, pages: str, name: str = "") -> str:
    """Make a new PDF with only some pages, e.g. pages "1-3,7" (also how to split a PDF)."""
    from pypdf import PdfReader, PdfWriter
    full = resolve(path)
    reader = PdfReader(full)
    w = PdfWriter()
    idx = pages_list(pages, len(reader.pages))
    for i in idx:
        w.add_page(reader.pages[i])
    out = out_path(name or f"{os.path.splitext(os.path.basename(full))[0]} pages {pages}", ".pdf")
    with open(out, "wb") as fh:
        w.write(fh)
    return f"Saved {len(idx)} page(s) as {os.path.basename(out)}.\n[[show: {out}]]"


@mcp.tool()
@guarded
def resize_image(path: str, max_side: int = 1600, quality: int = 80) -> str:
    """Shrink an image (longest side in pixels) and compress it as JPEG, e.g. to send or upload it."""
    from PIL import Image
    full = resolve(path)
    out = out_path(os.path.basename(full) + " small", ".jpg")
    with Image.open(full) as im:
        im = im.convert("RGB")
        im.thumbnail((max_side, max_side))
        im.save(out, "JPEG", quality=max(30, min(quality, 95)), optimize=True)
    return (f"Saved {os.path.basename(out)}: {size(os.path.getsize(full))} -> {size(os.path.getsize(out))}."
            f"\n[[show: {out}]]")


# ---------------------------------------------------------------- showing
@mcp.tool()
@guarded
def show(target: str) -> str:
    """Show the user something in Karen's viewer window: an image, PDF, text file or a website URL."""
    if target.startswith(("http://", "https://", "www.")):
        url = target if target.startswith("http") else "https://" + target
        return f"Showing {url}.\n[[show: {url}]]"
    full = resolve(target)
    return f"Showing {os.path.basename(full)}.\n[[show: {full}]]"


if __name__ == "__main__":
    mcp.run()
