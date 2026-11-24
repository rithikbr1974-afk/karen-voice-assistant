"""Free images for videos: AI generation (free APIs only) and licensed web search, cleaned for a cream background.

    python ig-reels/images.py gen "a stick figure studying at a desk" out.png [--style doodle] [--provider auto]
    python ig-reels/images.py search "Carol Dweck" out.png [--kind photo]
    python ig-reels/images.py batch prompts.json out_dir       # [{"id", "prompt"} or {"id", "search"}], in parallel
    python ig-reels/images.py extract https://page.example out_dir   # every image >= 300 px on one page
    python ig-reels/images.py clean drawing.jpg drawing.png      # white paper -> transparent, lines kept

Generation providers, tried in order (all free):
  cloudflare  Workers AI FLUX.1 schnell: ~170 images/day on the free plan; needs CF_ACCOUNT_ID + CF_API_TOKEN in .env
  horde       AI Horde (volunteer GPUs): free, no account needed (HORDE_API_KEY gives priority); slow, ~1-4 min
  pollinations no key, instant, but a weak model and a small watermark: last resort
Search sources (licence recorded): Openverse (CC / public domain), Wikimedia Commons. Every image gets a line in
<out_dir>/credits.json (provider, prompt or source page, licence, author).
"""
import argparse
import base64
import hashlib
import io
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

import httpx
from dotenv import load_dotenv
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
# Wikimedia refuses generic agents; this format (with a link) is accepted
_SUBMIT, _POLL = threading.Lock(), threading.Lock()
UA = {"User-Agent": "Mozilla/5.0 (compatible; KarenReels/1.0; +https://commons.wikimedia.org/wiki/Commons:Reusing_content_outside_Wikimedia)"}
CACHE = ROOT / "ig-reels" / "cache" / "images"

STYLES = {  # appended to every prompt; "negative" is used where the provider supports it
    "doodle": ("xkcd style stick figure comic doodle: stick figures with a plain circle head and single-line body, "
               "arms and legs, simple props, black marker line art on plain white paper, minimalist, lots of empty "
               "white space, centered, no text",
               "color, grey, shading, gradient, realistic, photo, anime, manga, detailed face, hair, 3d, text, "
               "letters, watermark, signature, frame, border, circle frame, round vignette, background scenery"),
    "sketch": ("black and white pencil sketch illustration, hand-drawn, plain white background, centered, no text",
               "color, photo, text, watermark, frame"),
    "flat": ("flat vector illustration, bold simple shapes, plain white background, centered, no text",
             "photo, realistic, text, watermark, frame"),
    "none": ("", ""),
}


def log(msg):
    print(msg, file=sys.stderr, flush=True)


# ------------------------------------------------------------ generation providers
def cloudflare(prompt, negative, w, h):
    acct, token = os.environ.get("CF_ACCOUNT_ID"), os.environ.get("CF_API_TOKEN")
    if not (acct and token):
        raise LookupError("no CF_ACCOUNT_ID / CF_API_TOKEN")
    r = httpx.post(f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/run/@cf/black-forest-labs/flux-1-schnell",
                   headers={"Authorization": f"Bearer {token}"}, json={"prompt": prompt, "steps": 4}, timeout=90)
    data = r.json()
    if not data.get("success"):
        raise RuntimeError(f"cloudflare: {data.get('errors')}")
    return base64.b64decode(data["result"]["image"]), "cloudflare flux-1-schnell"


def horde(prompt, negative, w, h, wait=600):
    head = {"apikey": os.environ.get("HORDE_API_KEY") or "0000000000", "Client-Agent": "KarenReels:1.0:personal"}
    body = {"prompt": f"{prompt} ### {negative}" if negative else prompt,
            "params": {"width": w // 64 * 64, "height": h // 64 * 64, "steps": 20, "cfg_scale": 6,
                       "sampler_name": "k_euler_a", "n": 1},
            "models": ["AlbedoBase XL 3.1", "AlbedoBase XL (SDXL)"],  # SDXL: clean line art (Deliberate drew anime)
            "nsfw": False, "censor_nsfw": True, "r2": True}
    for attempt in range(8):  # Horde accepts 2 submissions a second: space them out across threads
        with _SUBMIT:
            time.sleep(0.6)
            job = httpx.post("https://aihorde.net/api/v2/generate/async", json=body, headers=head, timeout=30).json()
        if "id" in job:
            break
        if "per 1 second" not in str(job.get("message", "")) and "rate" not in str(job.get("message", "")).lower():
            raise RuntimeError(f"horde: {job.get('message', job)}")
        time.sleep(2 + attempt)
    else:
        raise RuntimeError("horde: rate-limited")
    start = time.time()
    while time.time() - start < wait:
        time.sleep(8)
        if httpx.get(f"https://aihorde.net/api/v2/generate/check/{job['id']}", headers=head, timeout=30).json().get("done"):
            gen = httpx.get(f"https://aihorde.net/api/v2/generate/status/{job['id']}", headers=head,
                            timeout=30).json()["generations"][0]
            if gen.get("censored"):
                raise RuntimeError("horde: image was filtered")
            return httpx.get(gen["img"], timeout=60).content, f"aihorde {gen['model']}"
    httpx.delete(f"https://aihorde.net/api/v2/generate/status/{job['id']}", headers=head, timeout=30)
    raise TimeoutError("horde: queue too slow")


def pollinations(prompt, negative, w, h):
    with _POLL:  # anonymous use: one request at a time, or it answers 402
        r = httpx.get(f"https://image.pollinations.ai/prompt/{quote(prompt)}",
                      params={"width": w, "height": h, "nologo": "true", "seed": 7}, timeout=120)
    if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image"):
        raise RuntimeError(f"pollinations: {r.status_code}")
    return r.content, "pollinations sana"


def horde_retry(prompt, negative, w, h):
    """Horde's safety filter sometimes blocks harmless drawings; a new seed usually passes."""
    for attempt in range(3):
        try:
            return horde(prompt, negative, w, h)
        except RuntimeError as e:
            if "filtered" not in str(e) or attempt == 2:
                raise


PROVIDERS = {"cloudflare": cloudflare, "horde": horde_retry, "pollinations": pollinations}


def generate(prompt, out, style="doodle", provider="auto", w=1024, h=1024):
    """Generate one image; returns its credits entry. Cached by (prompt, style, size)."""
    add, negative = STYLES.get(style, STYLES["none"])
    full = f"{prompt}, {add}" if add else prompt
    key = hashlib.sha1(f"{full}|{w}x{h}".encode()).hexdigest()[:16]
    cached = CACHE / f"{key}.png"
    # auto: Pollinations only as the last resort for non-drawings (its anonymous model can't do clean line art and
    # leaves a watermark); a failed drawing becomes a handwritten line in the video instead
    order = ([p for p in PROVIDERS if p != "pollinations" or style not in ("doodle", "sketch")]
             if provider == "auto" else [provider])
    if cached.exists():
        meta = json.loads(cached.with_suffix(".json").read_text(encoding="utf-8"))
    else:
        errors = []
        for name in order:
            try:
                raw, used = PROVIDERS[name](full, negative, w, h)
                break
            except LookupError:
                continue  # provider not set up
            except Exception as e:
                errors.append(f"{e}")
        else:
            raise RuntimeError("no image provider worked: " + "; ".join(errors))
        CACHE.mkdir(parents=True, exist_ok=True)
        Image.open(io.BytesIO(raw)).convert("RGB").save(cached)
        meta = {"provider": used, "prompt": full, "licence": "AI-generated (no copyright claimed)"}
        cached.with_suffix(".json").write_text(json.dumps(meta), encoding="utf-8")
    clean(cached, out, line_art=style in ("doodle", "sketch"))
    return {"file": Path(out).name, **meta}


# ------------------------------------------------------------ licensed web search
def search(query, out, kind="photo"):
    """Find a freely licensed image (commercial use + modification allowed) and save it; returns its credits entry."""
    hits = []
    try:
        r = httpx.get("https://api.openverse.org/v1/images/", headers=UA, timeout=30,
                      params={"q": query, "page_size": 10, "license_type": "commercial,modification",
                              **({"category": "illustration,digitized_artwork"} if kind == "drawing" else {})})
        for x in r.json().get("results", []):
            if (x.get("width") or 0) >= 400:
                hits.append({"url": x["url"], "page": x.get("foreign_landing_url"), "licence": f"CC {x['license'].upper()}",
                             "author": x.get("creator"), "title": x.get("title"), "source": "openverse"})
    except (httpx.HTTPError, ValueError):
        pass
    if True:  # Commons too: better for people, places and buildings
        try:
            r = httpx.get("https://commons.wikimedia.org/w/api.php", headers=UA, timeout=30, params={
                "action": "query", "generator": "search", "gsrsearch": query, "gsrnamespace": 6,
                "gsrlimit": 8, "prop": "imageinfo", "iiprop": "url|extmetadata|size", "iiurlwidth": 1200, "format": "json"})
            for p in r.json().get("query", {}).get("pages", {}).values():
                ii = p["imageinfo"][0]
                md = ii.get("extmetadata", {})
                lic = md.get("LicenseShortName", {}).get("value", "")
                if ii.get("width", 0) >= 400 and any(s in lic for s in ("CC0", "CC BY", "Public domain", "PD")) \
                        and "NC" not in lic and "ND" not in lic:
                    hits.append({"url": ii.get("thumburl") or ii["url"], "page": ii.get("descriptionurl"),
                                 "licence": lic, "author": md.get("Artist", {}).get("value", ""), "title": p["title"],
                                 "source": "wikimedia"})
        except (httpx.HTTPError, ValueError):
            pass
    # rank by how much of the query the title names ("Carol Dweck" must not match a Kindle photo that mentions her)
    words = [w for w in query.lower().replace("'", "").split() if len(w) > 2]

    def score(h):
        title = (h.get("title") or "").lower().replace("'", "")
        return sum(w in title for w in words) / max(1, len(words)) + (0.05 if h["source"] == "wikimedia" else 0)

    hits = [h for h in sorted(hits, key=score, reverse=True) if score(h) >= 0.5]
    for hit in hits:
        try:
            raw = httpx.get(hit["url"], headers=UA, timeout=60, follow_redirects=True).content
            img = Image.open(io.BytesIO(raw))
            img.load()
        except Exception:
            continue
        img.convert("RGB").save(out)
        return {"file": Path(out).name, **hit}
    raise LookupError(f"no freely licensed image found for {query!r}")


# ------------------------------------------------------------ extract from a page
def extract(url, out_dir, min_side=300, limit=40):
    """Download the images on one web page (img src/srcset, og:image) that are at least min_side px. Their licence
    is unknown: the credits mark them so, and they are only for pages the user owns or that state a free licence."""
    import re as _re
    from urllib.parse import urljoin
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    page = httpx.get(url, headers=UA, timeout=30, follow_redirects=True).text
    found = _re.findall(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', page)
    for tag in _re.findall(r"<img\b[^>]*>", page, _re.I):
        srcset = _re.search(r'srcset=["\']([^"\']+)', tag)
        src = _re.search(r'(?:data-src|src)=["\']([^"\']+)', tag)
        if srcset:  # the largest candidate
            found.append(srcset.group(1).split(",")[-1].strip().split(" ")[0])
        elif src:
            found.append(src.group(1))
    credits, seen = [], set()
    for link in found:
        link = urljoin(url, link)
        if link in seen or link.startswith("data:") or link.endswith(".svg"):
            continue
        seen.add(link)
        try:
            img = Image.open(io.BytesIO(httpx.get(link, headers=UA, timeout=30, follow_redirects=True).content))
            img.load()
        except Exception:
            continue
        if min(img.size) < min_side:
            continue
        name = f"{len(credits):02}.png"
        img.convert("RGBA" if img.mode in ("RGBA", "LA", "P") else "RGB").save(out_dir / name)
        credits.append({"file": name, "url": link, "page": url, "size": img.size,
                        "licence": "unknown: check the page before using"})
        if len(credits) >= limit:
            break
    (out_dir / "credits.json").write_text(json.dumps(credits, indent=1), encoding="utf-8")
    log(f"{len(credits)} images from {url} -> {out_dir}")
    return credits


# ------------------------------------------------------------ cleaning
def clean(src, out, line_art=True, size=1024):
    """Line art: white -> transparent, lines kept dark (so it sits on any background). Photos: just resized."""
    img = Image.open(src).convert("RGB")
    img.thumbnail((size, size))
    if line_art:
        from PIL import ImageChops, ImageFilter
        grey = img.convert("L")
        # level the paper: estimate the background (lines removed by a max filter, then blurred) and divide it out,
        # so grey tints, vignettes and soft gradients from the models become white
        paper = grey.filter(ImageFilter.MaxFilter(15)).filter(ImageFilter.GaussianBlur(25))
        flat = Image.eval(ImageChops.subtract(paper, grey), lambda d: 255 - d)  # 255 = paper, darker = ink
        flat = ImageOps.autocontrast(flat, cutoff=0.5)
        alpha = flat.point(lambda v: 0 if v > 215 else min(255, int((215 - v) * 255 / 110)))  # paper -> clear
        ink = Image.new("RGBA", img.size, (24, 24, 24, 255))
        ink.putalpha(alpha)
        bbox = alpha.getbbox()  # trim empty margins so the drawing can be placed and scaled precisely
        ink = ink.crop(bbox) if bbox else ink
        ink.save(out)
    else:
        img.save(out)


# ------------------------------------------------------------ batch
def batch(spec, out_dir, style="doodle", provider="auto", workers=8):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    items = json.loads(Path(spec).read_text(encoding="utf-8")) if isinstance(spec, (str, Path)) else spec

    def one(item):
        out = out_dir / f"{item['id']}.png"
        try:
            if item.get("search"):
                return search(item["search"], out, item.get("kind", "photo"))
            return generate(item["prompt"], out, item.get("style", style), provider)
        except Exception as e:
            log(f"  {item['id']}: {e}")
            return {"file": out.name, "error": str(e)}

    t = time.time()
    with ThreadPoolExecutor(workers) as pool:
        credits = list(pool.map(one, items))
    (out_dir / "credits.json").write_text(json.dumps(credits, indent=1, ensure_ascii=False), encoding="utf-8")
    ok = sum("error" not in c for c in credits)
    log(f"{ok}/{len(items)} images in {time.time() - t:.0f}s -> {out_dir}")
    return credits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["gen", "search", "batch", "extract", "clean"])
    ap.add_argument("what")
    ap.add_argument("out")
    ap.add_argument("--style", default="doodle", choices=list(STYLES))
    ap.add_argument("--provider", default="auto", choices=["auto", *PROVIDERS])
    ap.add_argument("--kind", default="photo", choices=["photo", "drawing"])
    a = ap.parse_args()
    if a.mode == "gen":
        print(json.dumps(generate(a.what, a.out, a.style, a.provider)))
    elif a.mode == "search":
        print(json.dumps(search(a.what, a.out, a.kind), ensure_ascii=False))
    elif a.mode == "extract":
        extract(a.what, a.out)
    elif a.mode == "clean":  # make any white-background drawing transparent
        clean(a.what, a.out, line_art=True)
    else:
        batch(a.what, a.out, a.style, a.provider)


if __name__ == "__main__":
    main()
