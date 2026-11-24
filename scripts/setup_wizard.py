"""Karen's first-run setup: your name, city, free AI keys, optional extras, then training (voice, face).

Writes .env (your keys, never shared) and fills in config.yaml. Safe to run again: Enter keeps what's saved.

    python scripts/setup_wizard.py          the whole setup
    python scripts/setup_wizard.py keys     only the keys
    python scripts/setup_wizard.py train    only the training menu
"""
import os
import re
import subprocess
import sys
import webbrowser

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.stdout.reconfigure(errors="replace")  # "•" and "✓" on an old console code page print as "?" instead of crashing
PY = sys.executable
NEWS = {  # Google News editions for the news and the morning briefing
    "IN": "hl=en-IN&gl=IN&ceid=IN:en", "US": "hl=en-US&gl=US&ceid=US:en", "GB": "hl=en-GB&gl=GB&ceid=GB:en",
    "CA": "hl=en-CA&gl=CA&ceid=CA:en", "AU": "hl=en-AU&gl=AU&ceid=AU:en", "SG": "hl=en-SG&gl=SG&ceid=SG:en",
    "AE": "hl=en-AE&gl=AE&ceid=AE:en", "NZ": "hl=en-NZ&gl=NZ&ceid=NZ:en", "IE": "hl=en-IE&gl=IE&ceid=IE:en",
    "ZA": "hl=en-ZA&gl=ZA&ceid=ZA:en", "PH": "hl=en-PH&gl=PH&ceid=PH:en", "NG": "hl=en-NG&gl=NG&ceid=NG:en",
}
KEYS = [  # (name, what it is, where to get it, required)
    ("GEMINI_API_KEY", "Google Gemini (free): Karen's main brain for everyday chat",
     "https://aistudio.google.com/apikey", True),
    ("GROQ_API_KEY", "Groq (free, very fast): backup brain, tools, and cloud speech-to-text",
     "https://console.groq.com/keys", True),
    ("OPENROUTER_API_KEY", "OpenRouter (optional, pay-as-you-go pennies): best for images and the screen agent",
     "https://openrouter.ai/keys", False),
]
EXTRAS = [
    ("TELEGRAM_BOT_TOKEN", "Telegram bot token: chat with Karen from your phone (send her photos, PDFs, voice notes)",
     "https://t.me/BotFather  (send /newbot, copy the token)"),
    ("CF_ACCOUNT_ID", "Cloudflare account ID: free AI pictures (\"draw me a ...\")",
     "https://dash.cloudflare.com  (Workers AI; see docs/GETTING_STARTED.md)"),
    ("CF_API_TOKEN", "Cloudflare API token (Workers AI permission)", "https://dash.cloudflare.com/profile/api-tokens"),
    ("GOOGLE_OAUTH_CLIENT_ID", "Google OAuth client ID: Gmail, Calendar, Drive, Docs, Sheets",
     "see docs/GETTING_STARTED.md, section Google"),
    ("GOOGLE_OAUTH_CLIENT_SECRET", "Google OAuth client secret", "same page as the client ID"),
    ("USER_GOOGLE_EMAIL", "Your Gmail address (the account Karen reads)", ""),
]


def title(text):
    print("\n" + "=" * 64 + f"\n  {text}\n" + "=" * 64)


def ask(prompt, saved=""):
    shown = f" [{mask(saved)}]" if saved else ""
    answer = input(f"{prompt}{shown}: ").strip()
    return answer or saved


def mask(value):
    return value if len(value) < 12 else value[:4] + "…" + value[-3:]


def yes(prompt, default=True):
    answer = input(f"{prompt} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
    return default if not answer else answer.startswith("y")


def read_env():
    env = {}
    if os.path.exists(".env"):
        for line in open(".env", encoding="utf-8"):
            m = re.match(r"\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$", line)
            if m and not m.group(2).startswith("#"):
                env[m.group(1)] = re.sub(r"\s+#.*$", "", m.group(2))
    return env


def write_env(env):
    lines = ["# Karen's keys. Private: never share or upload this file.\n"]
    lines += [f"{k}={v}\n" for k, v in env.items() if v and v != "..."]
    with open(".env", "w", encoding="utf-8") as f:
        f.writelines(lines)


def check_key(name, value):
    """A one-word test request, so a mistyped key shows up now and not when Karen first speaks."""
    if name not in ("GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY") or not value:
        return
    import yaml
    from openai import OpenAI
    backend = next((b for b in yaml.safe_load(open("config.yaml", encoding="utf-8"))["llm"]["backends"]
                    if b["key_env"] == name), None)
    if not backend:
        return
    try:
        OpenAI(base_url=backend["base_url"], api_key=value, timeout=20, max_retries=0).chat.completions.create(
            model=backend["model"], messages=[{"role": "user", "content": "Say OK"}], max_tokens=200,
            **{k: v for k, v in backend.get("params", {}).items() if k != "extra_body"})
        print("   ✓ key works")
    except Exception as e:
        code = getattr(e, "status_code", "")
        print(f"   ! test failed ({code or type(e).__name__}). "
              + ("The key looks wrong." if code in (400, 401, 403) else "It may be a network hiccup; Karen will retry."))


def set_config(pattern, replacement):
    with open("config.yaml", encoding="utf-8") as f:
        text = f.read()
    new, n = re.subn(pattern, replacement, text, count=1, flags=re.M)
    if n:
        with open("config.yaml", "w", encoding="utf-8") as f:
            f.write(new)
    return bool(n)


def config_value(pattern):
    m = re.search(pattern, open("config.yaml", encoding="utf-8").read(), re.M)
    return m.group(1) if m else ""


def about_you():
    title("1/4  About you")
    name = ask("Your first name (Karen calls you this)", config_value(r"You are Karen, (.+?)'s personal voice assistant"))
    if name:
        set_config(r"You are Karen, (?:a |.+?'s )personal voice assistant\.",
                   f"You are Karen, {name.replace(chr(92), '')}'s personal voice assistant.")
    city = ask("Your city, for weather (e.g. Chennai, London, Austin)", config_value(r'^  city: "(.*)"'))
    set_config(r'^  city: ".*"', f'  city: "{city.replace(chr(34), "")}"')
    region = config_value(r'news_region: "(.*)"')
    now = next((c for c, v in NEWS.items() if v == region), "IN")
    code = ask(f"Country for news ({', '.join(NEWS)})", now).upper()
    set_config(r'news_region: ".*"', f'news_region: "{NEWS.get(code, NEWS["IN"])}"')


def keys(env):
    title("2/4  Free AI keys (Karen's brain)")
    print("Karen needs at least one. Both are free; with both, one covers for the other.\n"
          "Each link opens in your browser: sign in, create a key, copy it, paste it here (right-click pastes).\n")
    for name, what, url, _ in KEYS:
        print(f"• {what}\n  {url}")
        if not env.get(name) and yes("  Open the page now?", name != "OPENROUTER_API_KEY"):
            webbrowser.open(url)
        env[name] = ask(f"  {name}", env.get(name, ""))
        write_env(env)
        check_key(name, env[name])
        print()
    if not any(env.get(k) for k, *_ in KEYS):
        print("No AI key yet: Karen can't answer until you add one (run CHANGE_SETTINGS.bat).")
    if env.get("GROQ_API_KEY"):
        print("Privacy: Groq keeps requests up to 30 days for abuse checks unless you switch on Zero Data Retention\n"
              "(console.groq.com > Settings > Data Controls). With it on, Karen also lets Groq handle requests that\n"
              "touch your email, files and camera. Without it, those go only to OpenRouter (if you have a key).")
        zdr = yes("Have you turned on Zero Data Retention in Groq?", config_value(r"groq_zdr: (true)") == "true")
        set_config(r"groq_zdr: (true|false)", f"groq_zdr: {'true' if zdr else 'false'}")


def extras(env):
    title("3/4  Optional extras (Enter skips any of them)")
    for name, what, where in EXTRAS:
        print(f"• {what}" + (f"\n  {where}" if where else ""))
        env[name] = ask(f"  {name}", env.get(name, ""))
    write_env(env)
    if env.get("TELEGRAM_BOT_TOKEN"):
        print("\nTelegram: run PHONE_CHAT.bat next. It links your Telegram account (only you get answers).")


def train():
    title("4/4  Teach Karen your voice and face (optional, any time later too)")
    print("  1  Voice: record yourself saying \"Karen\" 40 times + 20 normal sentences (~10 min), then\n"
          "     train your personal wake word (~15 min, runs by itself). Without it Karen still wakes, using\n"
          "     speech recognition, but less reliably.\n"
          "  2  Face: ~30 webcam samples (2 min). Then Settings > Face unlock makes Karen answer only you.\n"
          "  3  Hand gestures: open the hand-tracking trainer (for the 3D viewer and phone mirror).\n"
          "  Enter = done")
    while True:
        pick = input("\nChoose 1, 2, 3 or Enter: ").strip()
        if pick == "1":
            subprocess.run([PY, "scripts/record_samples.py", "wake"])
            if yes("Train the wake word now (about 15 minutes)?"):
                subprocess.run([PY, "scripts/train_wake.py"])
        elif pick == "2":
            subprocess.run([PY, "scripts/enroll_face.py"])
        elif pick == "3":
            subprocess.Popen([PY, "hands.py", "--train"])
        else:
            break


def main():
    if not os.path.exists("config.yaml"):
        sys.exit("config.yaml is missing: run this from Karen's folder.")
    os.makedirs("data", exist_ok=True)
    env = read_env()
    part = sys.argv[1] if len(sys.argv) > 1 else "all"
    if part == "train":
        return train()
    if part == "keys":
        keys(env)
        return extras(env)
    print("\nHi! Let's set up Karen. This takes about 5 minutes (plus training, if you want it).")
    about_you()
    keys(env)
    extras(env)
    train()
    title("All set")
    print("Start Karen with START_KAREN.bat, then say \"Karen, what's the weather?\" or press Space to talk.\n"
          "Change any of this later with CHANGE_SETTINGS.bat. Guides: README.md and docs/.")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nStopped. Run it again any time; what you entered is kept.")
