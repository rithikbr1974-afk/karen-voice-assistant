"""Research reports: read web pages, have the LLM write a structured report, render it to PDF with Edge (headless).

The product photo comes from an online image search (not the user's camera picture). All free: ddgs search, trafilatura
for page text, Edge's built-in print-to-PDF, and servers/openrouter.py (zero data retention) for the writing.
"""
import base64
import datetime
import html
import io
import json
import os
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor

import httpx

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0 Safari/537.36 Edg/131.0"}
SKIP_PAGES = ("youtube.com", "facebook.com", "instagram.com", "x.com/", "twitter.com", "tiktok.com", "pinterest.")
SKIP_IMAGES = ("olx.", "ebay.", "facebook.", "instagram.", "quikr.", "alibaba.", "aliexpress.", "tokopedia.")
REPORTS = os.path.join(os.environ.get("USERPROFILE", os.path.expanduser("~")), "Documents", "Karen", "Reports")


def search(query, n=8):
    from ddgs import DDGS
    try:
        hits = DDGS(timeout=10).text(query, region="in-en", max_results=n + 4, backend="duckduckgo, yahoo, mojeek")
    except Exception:
        return []
    return [h["href"] for h in hits if not any(s in h["href"] for s in SKIP_PAGES)][:n]


def _page(url):
    import trafilatura
    try:
        r = httpx.get(url, headers=UA, follow_redirects=True, timeout=12)
        text = trafilatura.extract(r.text, include_tables=True, include_comments=False) if r.status_code == 200 else ""
    except Exception:
        text = ""
    return url, re.sub(r"\n{3,}", "\n\n", text or "")


def fetch_pages(urls, keep=6, chars=3500):
    """[(url, text)] of the first `keep` pages that had readable text, fetched in parallel."""
    with ThreadPoolExecutor(8) as pool:
        pages = list(pool.map(_page, urls))
    return [(u, t[:chars]) for u, t in pages if len(t) > 400][:keep]


def product_image(topic):
    """A clean online photo of the topic as JPEG bytes (or None)."""
    from ddgs import DDGS
    from PIL import Image
    try:
        hits = DDGS(timeout=10).images(topic, region="in-en", max_results=15, backend="bing")
    except Exception:
        return None
    for h in hits:
        url = h.get("image", "")
        if not url or any(s in url or s in h.get("source", "") for s in SKIP_IMAGES):
            continue
        try:
            r = httpx.get(url, headers=UA, follow_redirects=True, timeout=8)
            im = Image.open(io.BytesIO(r.content)).convert("RGB")
        except Exception:
            continue
        if min(im.size) < 300 or not 0.5 <= im.width / im.height <= 2:
            continue
        im.thumbnail((900, 900))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=88)
        return buf.getvalue()
    return None


PROMPT = """Write a research report about "{topic}" for the user using ONLY the sources below.
Reply with ONE JSON object, nothing else:
{{"title": "<name of the product/topic>", "subtitle": "<one line: what it is>",
 "summary": "<4-6 sentences>", "facts_title": "<'Key specs' for a product, else 'Key facts'>",
 "facts": [["<label>", "<value>"], ... up to 14],
 "pros": ["..."], "cons": ["..."],
 "price": "<price range in India in rupees with where to buy, or '' if not a product>",
 "alternatives": [{{"name": "...", "why": "<short>"}}] (2-3, or [] if not a product),
 "verdict": "<2-3 sentences>",
 "spoken": "<one short sentence summing it up, to be said aloud>"}}
Use numbers from the sources; don't invent specs or prices. If sources disagree, say so.

SOURCES:
{sources}"""


def write(topic, pages, chat, model=None):
    sources = "\n\n".join(f"[{i}] {u}\n{t}" for i, (u, t) in enumerate(pages, 1))
    raw = chat(model, max_tokens=2500, timeout=120,
               messages=[{"role": "user", "content": PROMPT.format(topic=topic, sources=sources)}])
    try:
        return json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
    except ValueError:
        return {"title": topic, "summary": raw.strip()[:2000]}


CSS = """
@page { size: A4; margin: 14mm 14mm 16mm; }
body { font-family: 'Segoe UI', Arial, sans-serif; color: #1d1626; font-size: 10.5pt; line-height: 1.5; margin: 0; }
header { background: linear-gradient(135deg, #2a0f45, #5b2a86); color: #fff; border-radius: 10px; padding: 18px 22px;
         display: flex; gap: 20px; align-items: center; }
header .text { flex: 1; }
header h1 { margin: 0 0 4px; font-size: 22pt; line-height: 1.15; }
header .sub { opacity: .9; font-size: 11pt; }
header .meta { opacity: .7; font-size: 8.5pt; margin-top: 10px; }
header img { width: 190px; height: 190px; object-fit: contain; background: #fff; border-radius: 8px; padding: 6px; }
h2 { color: #5b2a86; font-size: 13pt; border-bottom: 2px solid #e6dcf0; padding-bottom: 3px; margin: 20px 0 8px; }
table { width: 100%; border-collapse: collapse; }
td { padding: 5px 8px; border-bottom: 1px solid #eee7f4; vertical-align: top; }
td:first-child { color: #5b2a86; font-weight: 600; width: 32%; }
.cols { display: flex; gap: 16px; }
.cols > div { flex: 1; background: #f7f3fb; border-radius: 8px; padding: 6px 14px; }
.cols h3 { margin: 6px 0; font-size: 11pt; }
ul { margin: 4px 0 8px; padding-left: 18px; }
.box { background: #f7f3fb; border-left: 4px solid #5b2a86; border-radius: 6px; padding: 10px 14px; }
.sources { font-size: 8.5pt; color: #555; word-break: break-all; }
section { break-inside: avoid; }
"""


def render(data, pages, image, out_dir=REPORTS, edge=None):
    """Writes the report as PDF; returns its path."""
    e = lambda s: html.escape(str(s or ""))  # noqa: E731
    img = f'<img src="data:image/jpeg;base64,{base64.b64encode(image).decode()}">' if image else ""
    parts = [f"""<header><div class="text"><h1>{e(data.get('title'))}</h1><div class="sub">{e(data.get('subtitle'))}</div>
<div class="meta">Research report by Karen · {datetime.datetime.now():%d %B %Y, %I:%M %p} · {len(pages)} sources</div></div>{img}</header>"""]
    if data.get("summary"):
        parts.append(f"<section><h2>Summary</h2><p>{e(data['summary'])}</p></section>")
    if data.get("facts"):
        rows = "".join(f"<tr><td>{e(r[0])}</td><td>{e(r[1])}</td></tr>" for r in data["facts"] if len(r) >= 2)
        parts.append(f"<section><h2>{e(data.get('facts_title') or 'Key facts')}</h2><table>{rows}</table></section>")
    if data.get("pros") or data.get("cons"):
        li = lambda xs: "".join(f"<li>{e(x)}</li>" for x in xs or [])  # noqa: E731
        parts.append(f"""<section><h2>Pros and cons</h2><div class="cols"><div><h3>👍 Pros</h3><ul>{li(data.get('pros'))}</ul></div>
<div><h3>👎 Cons</h3><ul>{li(data.get('cons'))}</ul></div></div></section>""")
    if data.get("price"):
        parts.append(f"<section><h2>Price in India</h2><p>{e(data['price'])}</p></section>")
    if data.get("alternatives"):
        alts = "".join(f"<li><b>{e(a.get('name'))}</b>: {e(a.get('why'))}</li>" for a in data["alternatives"]
                       if isinstance(a, dict))
        parts.append(f"<section><h2>Alternatives</h2><ul>{alts}</ul></section>")
    if data.get("verdict"):
        parts.append(f"<section><h2>Verdict</h2><div class='box'>{e(data['verdict'])}</div></section>")
    src = "".join(f"<li>[{i}] <a href='{e(u)}'>{e(u)}</a></li>" for i, (u, _) in enumerate(pages, 1))
    parts.append(f"<section><h2>Sources</h2><ul class='sources'>{src}</ul></section>")
    page = f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head><body>{''.join(parts)}</body></html>"

    os.makedirs(out_dir, exist_ok=True)
    name = re.sub(r'[<>:"/\\|?*]+', " ", str(data.get("title") or "Report")).strip()[:70]
    pdf = os.path.join(out_dir, f"{name} {datetime.datetime.now():%Y-%m-%d %H.%M}.pdf")
    with tempfile.TemporaryDirectory() as tmp:
        src_html = os.path.join(tmp, "report.html")
        with open(src_html, "w", encoding="utf-8") as f:
            f.write(page)
        profile = os.path.join(os.environ.get("LOCALAPPDATA", tmp), "Karen", "edge-print")  # not his Edge profile
        subprocess.run([edge, "--headless=new", "--disable-gpu", "--no-first-run", f"--user-data-dir={profile}",
                        "--no-pdf-header-footer", f"--print-to-pdf={pdf}", "file:///" + src_html.replace("\\", "/")],
                       capture_output=True, timeout=90, creationflags=0x08000000)
    if not os.path.exists(pdf):
        raise RuntimeError("Edge didn't produce the PDF")
    return pdf
