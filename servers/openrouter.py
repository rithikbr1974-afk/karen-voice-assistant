"""One private chat call for the MCP servers (camera, images, screenshots, screen agent, reports, mail ideas).

Zero-data-retention only. Tried in order: the configured OpenRouter model; Groq (gpt-oss-20b for text, qwen3.8-27b
for images; only once llm.groq_zdr is true in config.yaml, i.e. Zero Data Retention is on in their Groq account); then
OpenRouter's free zero-retention model (text only). A provider that says "out of credits" (402) rests for an hour.
"""
import os
import time

import yaml
from dotenv import load_dotenv
from openai import OpenAI

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))  # the servers only get the keys listed for them; Groq's lives in .env
FREE = "inclusionai/ling-3.0-flash-sante:free"  # OpenRouter: free, zero data retention, text only; busy at times
GROQ_TEXT, GROQ_VISION = "openai/gpt-oss-20b", "qwen/qwen3.8-27b"  # Groq tests 2026-10-10: 6/6 tools; reads images
PROVIDERS = {"openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
             "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY")}
_rest = {}


class NoVision(RuntimeError):
    def __init__(self):
        super().__init__("Seeing images needs a private model that reads pictures: turn on Zero Data Retention in "
                         "Groq (then groq_zdr: true in config.yaml) or add OpenRouter credits. Tell the user that, "
                         "briefly.")


def _groq_ok():
    try:
        llm = yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8")).get("llm", {})
    except OSError:
        return False
    return bool(llm.get("groq_zdr")) and bool(os.environ.get("GROQ_API_KEY"))


def _chain(model, image):
    chain = [("openrouter", model)] if model else []
    if _groq_ok():
        chain.append(("groq", GROQ_VISION if image else GROQ_TEXT))
    if not image:
        chain.append(("openrouter", FREE))
    return [c for c in dict.fromkeys(chain) if os.environ.get(PROVIDERS[c[0]][1])]


def _extra(provider, m):
    if provider == "openrouter":
        return {"extra_body": {"provider": {"zdr": True}, "reasoning": {"enabled": False}}}
    return {"reasoning_effort": "low"} if m.startswith("openai/gpt-oss") else {}


def chat(model, messages, max_tokens=300, timeout=60):
    image = any(isinstance(m.get("content"), list) and any(p.get("type") == "image_url" for p in m["content"])
                for m in messages)
    chain = _chain(model, image)
    if image and (not chain or all(_rest.get(c, 0) > time.time() for c in chain)):
        raise NoVision()
    for i, (provider, m) in enumerate(chain):
        last = i == len(chain) - 1
        if _rest.get((provider, m), 0) > time.time() and not last:
            continue
        url, key = PROVIDERS[provider]
        client = OpenAI(base_url=url, api_key=os.environ[key], timeout=timeout)
        try:
            budget = max(max_tokens, 1000) if m.startswith("openai/gpt-oss") else max_tokens  # its thinking counts too
            r = client.with_options(max_retries=6 if m.endswith(":free") else 1).chat.completions.create(
                model=m, max_tokens=budget, messages=messages, **_extra(provider, m))
            return (r.choices[0].message.content or "").strip()
        except Exception as e:
            if getattr(e, "status_code", None) == 402:  # out of credits: rest it for an hour
                _rest[(provider, m)] = time.time() + 3600
                if image and last:
                    raise NoVision() from e
            if last:
                raise


BUSY = ("The free AI model is busy right now (OpenRouter's free tier). Tell the user to try again in a minute; adding "
        "OpenRouter credits makes this reliable.")


def friendly(fn):
    """For MCP tools: an OpenRouter failure comes back as a plain sentence for Karen instead of a bare tool error."""
    import functools

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            if isinstance(e, NoVision):
                return str(e)
            if getattr(e, "status_code", None) == 429 or "RateLimit" in type(e).__name__:
                return BUSY
            if type(e).__module__.startswith("openai"):
                return f"The AI model failed ({getattr(e, 'status_code', type(e).__name__)}). Try again in a minute."
            raise
    return wrapper
