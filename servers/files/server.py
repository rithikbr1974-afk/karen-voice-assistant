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
AUDIO_EXT = {".mp3", ".m4a", ".ogg", ".oga", ".opus", ".wav", ".flac", ".aac", ".wma", ".amr"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".3gp", ".m4v", ".wmv"}
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


def _pdf_text(full, pages=""):
    from pypdf import PdfReader
    reader = PdfReader(full)
    idx = pages_list(pages, len(reader.pages))
    return "\n".join(f"--- page {i + 1} ---\n{reader.pages[i].extract_text() or ''}" for i in idx)


def _ffmpeg(*args, timeout=300):
    import shutil
    import subprocess
    exe = shutil.which("ffmpeg")
    if not exe:
        try:  # the always-on PC (karen-server kit) has no ffmpeg install: imageio-ffmpeg carries its own
            import imageio_ffmpeg
            exe = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            raise ValueError("ffmpeg isn't installed (winget install Gyan.FFmpeg).") from None
    return subprocess.run([exe, "-hide_banner", "-y", *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, creationflags=0x08000000)


def _duration(full):
    """Seconds, from ffmpeg's "Duration: 00:01:23.45" line (0 if unknown)."""
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", _ffmpeg("-i", full).stderr)
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else 0


def _clock(s):
    return f"{int(s // 60)} min {int(s % 60)} s" if s >= 60 else f"{int(s)} s"


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
    """Text of a PDF, Word, Excel or PowerPoint file (also old .doc/.xls/.ppt, .rtf, .odt) or a text/code/CSV file,
    to summarise, translate or answer questions about it. pages: for PDFs, e.g. "1-3" (empty = all)."""
    full = resolve(path)
    ext = os.path.splitext(full)[1].lower()
    if ext == ".pdf":
        text = _pdf_text(full, pages)
        if len(re.sub(r"\s|--- page \d+ ---", "", text)) < 20:
            return ("This PDF has no text layer (it's scanned images). Use pdf_to_images and describe_image instead, "
                    "or describe_image on its page images.")
    elif ext == ".docx":
        import docx
        d = docx.Document(full)
        text = "\n".join(p.text for p in d.paragraphs)
        for t in d.tables:  # tables aren't in .paragraphs
            text += "\n" + "\n".join(" | ".join(c.text.strip() for c in row.cells) for row in t.rows)
    elif ext in (".xlsx", ".xlsm"):
        import openpyxl
        book = openpyxl.load_workbook(full, read_only=True, data_only=True)  # data_only: values, not formulas
        parts = []
        for sheet in book.worksheets:
            rows = [" | ".join("" if v is None else str(v) for v in row) for row in sheet.iter_rows(values_only=True)]
            rows = [r for r in rows if r.strip(" |")]
            parts.append(f"--- sheet {sheet.title} ({len(rows)} rows) ---\n" + "\n".join(rows[:400]))
        book.close()
        text = "\n".join(parts)
    elif ext == ".pptx":
        from pptx import Presentation
        slides = []
        for i, slide in enumerate(Presentation(full).slides, 1):
            bits = [s.text_frame.text for s in slide.shapes if s.has_text_frame and s.text_frame.text.strip()]
            if slide.has_notes_slide and slide.notes_slide.notes_text_frame.text.strip():
                bits.append("(notes) " + slide.notes_slide.notes_text_frame.text)
            slides.append(f"--- slide {i} ---\n" + "\n".join(bits))
        text = "\n".join(slides)
    elif ext in OFFICE:  # .doc .rtf .odt .ppt .xls: Office turns it into a PDF in a temp folder, then read that
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            pdf = _office_to_pdf(full, OFFICE[ext][0], os.path.join(tmp, "read.pdf"))
            text = _pdf_text(pdf, pages)
    elif ext in TEXT_EXT or ext in (".htm", ".srt", ".vtt", ".tex", ".rst"):
        text = open(full, encoding="utf-8", errors="replace").read()
    elif ext in IMAGE_EXT:
        return "That's an image: use describe_image."
    elif ext in AUDIO_EXT or ext in VIDEO_EXT:
        return "That's audio or video: use transcribe (what's said) or describe_video (what's shown)."
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


@mcp.tool()
@guarded
def transcribe(path: str, language: str = "") -> str:
    """What is said in an audio file, voice note or video (any language): the words, so you can summarise, translate or
    answer questions about it. Up to 60 minutes. language: a 2-letter code like en/hi/ta if known, else empty."""
    full = resolve(path)
    import tempfile
    seconds = _duration(full)
    with tempfile.TemporaryDirectory() as tmp:
        mp3 = os.path.join(tmp, "speech.mp3")  # mono 16 kHz 32 kbit/s: an hour is ~14 MB, under Groq's 25 MB
        r = _ffmpeg("-i", full, "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k", "-t", "3600", mp3)
        if not os.path.exists(mp3) or os.path.getsize(mp3) < 1000:
            if "does not contain any stream" in r.stderr or "matches no streams" in r.stderr:
                return "That file has no sound track. For a video, use describe_video to see what's in it."
            return f"ffmpeg couldn't read the sound: {r.stderr.strip()[-200:]}"
        text, how = _speech_to_text(mp3, seconds, language.strip().lower()[:2])
    if not text.strip():
        return f"I couldn't hear any speech in it ({_clock(seconds)} long; maybe music or silence)."
    limit = CFG.get("read_chars", 12000)
    cut = "\n...(rest cut)" if len(text) > limit else ""
    longer = ", only the first 60 min" if seconds > 3600 else ""
    return f"Transcript ({_clock(seconds)}{longer}, {how}):\n{text[:limit]}{cut}"


def _speech_to_text(mp3, seconds, language=""):
    """Groq Whisper (free, zero data retention, nothing runs on this PC); the local small.en model only for short clips
    when Groq can't be reached, since a long file would keep the CPU busy for minutes."""
    model = ((yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8")) or {}).get("stt", {})
             .get("cloud", {}).get("model", "whisper-large-v3"))
    err = "no GROQ_API_KEY"
    if os.environ.get("GROQ_API_KEY"):
        from openai import OpenAI
        client = OpenAI(base_url="https://api.groq.com/openai/v1", api_key=os.environ["GROQ_API_KEY"],
                        timeout=120, max_retries=1)
        try:
            with open(mp3, "rb") as f:
                r = client.audio.transcriptions.create(model=model, file=("speech.mp3", f.read()),
                                                       response_format="verbose_json", temperature=0,
                                                       **({"language": language} if language else {}))
            lang = getattr(r, "language", "") or ""
            return (r.text or "").strip(), f"Groq Whisper{', ' + lang if lang else ''}"
        except Exception as e:
            err = type(e).__name__
    if seconds > 600:
        raise ValueError(f"Groq's speech service isn't answering ({err}), and a file over 10 minutes would keep "
                         "the PC busy too long locally. Try again in a few minutes.")
    try:
        from faster_whisper import WhisperModel
    except ImportError:  # the always-on PC has no local speech model (no 32-bit build)
        raise ValueError(f"Groq's speech service isn't answering ({err}). Try again in a few minutes.") from None
    local = WhisperModel("small.en", device="cpu", compute_type="int8", cpu_threads=4,
                         download_root=os.path.join(ROOT, "models"))
    segments, _ = local.transcribe(mp3, language="en", beam_size=1, condition_on_previous_text=False)
    return " ".join(s.text.strip() for s in segments).strip(), f"local model, English only, Groq {err}"


@mcp.tool()
@guarded
def describe_video(path: str, question: str = "") -> str:
    """Look at a video: 4 frames from start to end, described in order (what's shown, any text on screen).
    For what is said in it, use transcribe."""
    full = resolve(path)
    import tempfile
    from io import BytesIO
    from PIL import Image
    seconds = _duration(full)
    if not seconds:
        return "I couldn't read that video."
    frames = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, part in enumerate((0.1, 0.37, 0.63, 0.9)):
            out = os.path.join(tmp, f"{i}.jpg")
            _ffmpeg("-ss", f"{seconds * part:.2f}", "-i", full, "-frames:v", "1", "-vf", "scale=640:-2", out, timeout=60)
            if os.path.exists(out):
                with Image.open(out) as im:
                    frames.append(im.convert("RGB"))
    if not frames:
        return "I couldn't take any pictures from that video."
    w, h = frames[0].size
    sheet = Image.new("RGB", (w * 2, h * ((len(frames) + 1) // 2)), "black")  # 2x2 grid, read left-right, top-down
    for i, im in enumerate(frames):
        sheet.paste(im.resize((w, h)), ((i % 2) * w, (i // 2) * h))
    buf = BytesIO()
    sheet.save(buf, "JPEG", quality=85)
    ask = (f"These are {len(frames)} frames, in order (left to right, top to bottom), from a {_clock(seconds)} video. "
           + (question or "Describe what happens in the video in a few plain sentences, including any text on screen."))
    return chat(CFG.get("vision_model"), max_tokens=450, messages=[{"role": "user", "content": [
        {"type": "text", "text": ask},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()}}]}])


# ---------------------------------------------------------------- converting
def _office_to_pdf(full, app, out=None):
    import comtypes
    import comtypes.client
    comtypes.CoInitialize()
    out = out or out_path(full, ".pdf")
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
