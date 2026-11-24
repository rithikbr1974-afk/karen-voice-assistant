"""Karen's setup wizard: walks you through everything, one step at a time. Safe to run again at any time.

    python setup.py            all steps (Enter keeps the current answer)
    python setup.py --menu     pick one step, e.g. to add Gmail later

setup.bat runs this after installing the packages and models. Your answers go to .env (secret keys) and
config.yaml (everything else). Nothing leaves your PC except the key checks with each AI provider.
"""
import os
import re
import shutil
import subprocess
import sys
import time
import webbrowser

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
PY = sys.executable
os.system("")  # turns on colours in the Windows console
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # boxes and ticks on any console code page

BOLD, DIM, GREEN, YELLOW, RED, PURPLE, END = ("\033[1m", "\033[2m", "\033[92m", "\033[93m", "\033[91m",
                                              "\033[95m", "\033[0m")


# ---------------------------------------------------------------- small helpers
def title(n, text):
    print(f"\n{PURPLE}{BOLD}{'─' * 64}\n  Step {n}: {text}\n{'─' * 64}{END}")


def ok(text):
    print(f"  {GREEN}✔ {text}{END}")


def warn(text):
    print(f"  {YELLOW}! {text}{END}")


def note(text):
    print(f"  {DIM}{text}{END}")


def ask(question, default=""):
    shown = f" [{default}]" if default else ""
    try:
        answer = input(f"  {BOLD}{question}{END}{shown}: ").strip()
    except EOFError:
        answer = ""
    return answer or default


def yes(question, default=True):
    answer = ask(question + (" (Y/n)" if default else " (y/N)")).lower()
    return default if not answer else answer.startswith("y")


def secret(question, current):
    hint = "Enter = keep the saved key" if current else "Enter = skip"
    answer = ask(f"{question} ({hint})")
    return answer or current


def open_page(url):
    if yes(f"Open {url} in your browser?"):
        webbrowser.open(url)


# ---------------------------------------------------------------- .env and config.yaml
def read_env():
    values = {}
    if os.path.exists(".env"):
        for line in open(".env", encoding="utf-8"):
            m = re.match(r"\s*([A-Z0-9_]+)\s*=\s*(.*)", line)
            if m:
                value = re.sub(r"(^|\s+)#.*$", "", m.group(2)).strip().strip('"')
                values[m.group(1)] = value
    return values


def write_env(values):
    """Fill .env.example's lines with the values (comments kept); keys not in the template go at the end."""
    lines, used = [], set()
    for line in open(".env.example", encoding="utf-8").read().splitlines():
        m = re.match(r"([A-Z0-9_]+)=(\s*)(#.*)?$", line)
        if m:
            key = m.group(1)
            used.add(key)
            value = values.get(key, "")
            comment = m.group(3) or ""
            line = f"{key}={value}" + (f"   {comment}" if comment else "")
        lines.append(line)
    lines += [f"{k}={v}" for k, v in values.items() if k not in used and v]
    with open(".env", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def yaml_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    return '"' + str(value).replace("\\", "/").replace('"', '\\"') + '"'


def set_config(path, value):
    """Change one setting in config.yaml in place (comments and layout stay), e.g. ("daily", "city")."""
    lines = open("config.yaml", encoding="utf-8").read().split("\n")
    start, parent = 0, -1  # search from this line; indent of the section we're inside
    for depth, key in enumerate(path):
        level, found = None, None  # indent of this section's direct children
        for i in range(start, len(lines)):
            line = lines[i]
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            here = len(line) - len(line.lstrip())
            if here <= parent:  # left the section without finding the key
                break
            level = here if level is None else level
            if here == level and re.match(r"\s*" + re.escape(key) + r":(\s|$)", line):
                found = i
                break
        if found is None:
            warn(f"Couldn't find {'.'.join(path)} in config.yaml; set it by hand.")
            return False
        start, parent = found + 1, level
    m = re.match(r"(\s*)([^:]+):\s*(.*?)(\s+#.*)?$", lines[found])
    lines[found] = f"{m.group(1)}{m.group(2)}: {yaml_value(value)}" + (f"   {m.group(4).strip()}" if m.group(4) else "")
    with open("config.yaml", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return True


def get_config():
    import yaml
    return yaml.safe_load(open("config.yaml", encoding="utf-8"))


# ---------------------------------------------------------------- the steps
COUNTRIES = {  # country -> Google News edition
    "india": "hl=en-IN&gl=IN&ceid=IN:en", "united states": "hl=en-US&gl=US&ceid=US:en",
    "usa": "hl=en-US&gl=US&ceid=US:en", "us": "hl=en-US&gl=US&ceid=US:en",
    "united kingdom": "hl=en-GB&gl=GB&ceid=GB:en", "uk": "hl=en-GB&gl=GB&ceid=GB:en",
    "canada": "hl=en-CA&gl=CA&ceid=CA:en", "australia": "hl=en-AU&gl=AU&ceid=AU:en",
    "ireland": "hl=en-IE&gl=IE&ceid=IE:en", "new zealand": "hl=en-NZ&gl=NZ&ceid=NZ:en",
    "singapore": "hl=en-SG&gl=SG&ceid=SG:en", "south africa": "hl=en-ZA&gl=ZA&ceid=ZA:en",
    "pakistan": "hl=en-PK&gl=PK&ceid=PK:en", "philippines": "hl=en-PH&gl=PH&ceid=PH:en",
    "nigeria": "hl=en-NG&gl=NG&ceid=NG:en", "malaysia": "hl=en-MY&gl=MY&ceid=MY:en",
}


def step_about():
    title(1, "About you")
    note("Karen uses your name when she talks to you, and your city for the weather.")
    cfg = get_config()
    user, daily, explore = cfg.get("user") or {}, cfg.get("daily") or {}, cfg.get("explore") or {}
    name = ask("Your first name", user.get("name", ""))
    city = ask("Your city (for the weather)", daily.get("city", ""))
    country = ask("Your country (for news and prices)", explore.get("country", ""))
    accent = ""
    if yes("Do you speak English with a regional accent (e.g. Indian, Scottish)? It helps Karen guess misheard words",
           bool(user.get("accent"))):
        kind = ask("Which accent, in one or two words (e.g. Indian)", (user.get("accent") or "").replace(
            " of ", "").replace("-accented English", ""))
        accent = f" of {kind}-accented English" if kind else ""
    set_config(("user", "name"), name)
    set_config(("user", "accent"), accent)
    set_config(("daily", "city"), city)
    set_config(("explore", "country"), country)
    edition = COUNTRIES.get(country.lower().strip())
    if edition:
        set_config(("daily", "news_region"), edition)
    elif country:
        note("News stays on the US English edition. To change it, edit daily.news_region in config.yaml.")
    ok("Saved.")


def check_key(kind, key):
    """True if the provider accepts the key (one tiny request, nothing else is sent)."""
    try:
        if kind == "openrouter":
            import httpx
            r = httpx.get("https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {key}"}, timeout=15)
            return r.status_code == 200
        from openai import OpenAI
        url = {"gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
               "groq": "https://api.groq.com/openai/v1"}[kind]
        OpenAI(base_url=url, api_key=key, timeout=15, max_retries=0).models.list()
        return True
    except Exception as e:
        warn(f"The check failed: {type(e).__name__}. {str(e)[:120]}")
        return False


def step_brain():
    title(2, "Karen's brain (AI keys)")
    print("""  Karen thinks with free online AI models. You need at least ONE key; two or three is better, because
  when one is busy the next one answers. All three have a free option and need no credit card:

    1. Gemini (Google)  : the main brain for chat.        https://aistudio.google.com/apikey
    2. Groq             : very fast, handles tools and     https://console.groq.com/keys
                          speech-to-text. Recommended.
    3. OpenRouter       : backup, free models.             https://openrouter.ai/keys
""")
    env = read_env()
    for env_key, kind, url in (("GEMINI_API_KEY", "gemini", "https://aistudio.google.com/apikey"),
                               ("GROQ_API_KEY", "groq", "https://console.groq.com/keys"),
                               ("OPENROUTER_API_KEY", "openrouter", "https://openrouter.ai/keys")):
        print(f"\n  {BOLD}{kind.title()}{END}")
        if not env.get(env_key):
            open_page(url)
        key = secret(f"Paste your {kind.title()} key", env.get(env_key, ""))
        if key and key != env.get(env_key):
            print("  Checking the key...")
            if check_key(kind, key):
                ok(f"{kind.title()} key works.")
            elif not yes("Save it anyway?", False):
                key = env.get(env_key, "")
        env[env_key] = key
    write_env(env)
    if not any(env.get(k) for k in ("GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY")):
        warn("No AI key yet: Karen can't answer until you add one. Run this step again: python setup.py --menu")
    if env.get("GROQ_API_KEY"):
        print("""
  Privacy: requests that use your data (email, files, camera) only go to providers that keep nothing.
  Groq keeps requests for up to 30 days for abuse checks UNLESS you turn on Zero Data Retention:
  console.groq.com > Settings > Data Controls > Zero Data Retention.""")
        set_config(("llm", "groq_zdr"), yes("Have you turned on Zero Data Retention in Groq?", False))
    ok("Saved to .env (this file is never uploaded to GitHub).")


def step_audio():
    title(3, "Microphone and speakers")
    import numpy as np
    import sounddevice as sd
    devices = sd.query_devices()
    default_in = sd.default.device[0]
    print("  Microphones:")
    for i, d in enumerate(devices):
        if d["max_input_channels"] > 0 and d["hostapi"] == 0:
            print(f"    {i:>3}  {d['name']}{'   <- Windows default' if i == default_in else ''}")
    choice = ask("Number of the microphone to use (Enter = Windows default)", "")
    device = int(choice) if choice.isdigit() else None
    set_config(("audio", "input_device"), device)
    if yes("Test it now? (speak for 3 seconds)"):
        print("  Recording... say something!")
        audio = sd.rec(16000 * 3, samplerate=16000, channels=1, dtype="float32", device=device)
        sd.wait()
        level = float(np.abs(audio).max())
        if level < 0.02:
            warn(f"Very quiet (level {level:.2f}). Check the microphone isn't muted, or pick another one.")
        elif level > 0.98:
            warn(f"Too loud (level {level:.2f}, clipping). Lower the mic volume in Windows Sound settings.")
        else:
            ok(f"Heard you (level {level:.2f}).")
    ok("Saved.")


VOICES = [("af_heart", "American, warm (default)"), ("af_bella", "American, bright"),
          ("af_nicole", "American, soft"), ("bf_emma", "British"), ("bf_isabella", "British, lively"),
          ("am_adam", "American, male"), ("bm_george", "British, male")]


def step_voice():
    title(4, "Karen's voice")
    cfg = get_config()
    tts = cfg["tts"]
    if not os.path.exists(tts["model"]):
        warn("The voice model isn't downloaded yet. Run setup.bat first.")
        return
    from kokoro_onnx import Kokoro
    import sounddevice as sd
    print("  Loading the voice model...")
    kokoro = Kokoro(tts["model"], tts["voices"])
    name = (cfg.get("user") or {}).get("name") or "there"
    for i, (voice, desc) in enumerate(VOICES, 1):
        print(f"    {i}. {voice:12} {desc}")
    while True:
        choice = ask("Number to hear a voice, or Enter to keep the current one", "")
        if not choice:
            break
        if choice.isdigit() and 1 <= int(choice) <= len(VOICES):
            voice = VOICES[int(choice) - 1][0]
            samples, rate = kokoro.create(f"Hi {name}! I'm Karen. How does this voice sound?", voice=voice, speed=1.0)
            sd.play(samples, rate)
            sd.wait()
            if yes(f"Use {voice}?"):
                set_config(("tts", "voice"), voice)
                ok(f"Voice set to {voice}. (You can change it later in the window's Settings.)")
                break


def find_uvx():
    found = shutil.which("uvx")
    if found:
        return "uvx"
    local = os.path.join(ROOT, ".venv", "Scripts", "uvx.exe")
    if not os.path.exists(local):
        print("  Installing uv (runs the Google server)...")
        subprocess.run([PY, "-m", "pip", "install", "-q", "uv"], check=False)
    return ".venv/Scripts/uvx.exe" if os.path.exists(local) else ""


def step_google():
    title(5, "Gmail, Calendar and Google Drive (optional)")
    print("""  Lets Karen read and answer your email, check your calendar and find Drive files.
  It needs a free Google Cloud "OAuth client" (about 10 minutes, once). Full steps with pictures of each
  screen: docs/SETUP.md, section "Gmail and Calendar".""")
    if not yes("Set it up now?", False):
        return
    env = read_env()
    open_page("https://console.cloud.google.com/apis/credentials")
    env["GOOGLE_OAUTH_CLIENT_ID"] = secret("Client ID (ends in .apps.googleusercontent.com)",
                                           env.get("GOOGLE_OAUTH_CLIENT_ID", ""))
    env["GOOGLE_OAUTH_CLIENT_SECRET"] = secret("Client secret", env.get("GOOGLE_OAUTH_CLIENT_SECRET", ""))
    env["USER_GOOGLE_EMAIL"] = ask("Your Gmail address", env.get("USER_GOOGLE_EMAIL", ""))
    write_env(env)
    uvx = find_uvx()
    if not uvx:
        warn("Couldn't install uv. Install it from https://docs.astral.sh/uv/ and run this step again.")
        return
    set_config(("mcp_servers", "google", "command"), uvx)
    if all(env.get(k) for k in ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "USER_GOOGLE_EMAIL")):
        set_config(("mcp_servers", "google", "enabled"), True)
        ok("Google is on. The first time you ask about email, a browser window opens to sign in.")
    else:
        warn("Something is missing, so Google stays off. Run this step again when you have all three.")


def step_remote():
    title(6, "Chat with Karen from your phone (optional, Telegram)")
    print("""  Karen Remote answers your Telegram messages with the same brain and tools, even when you're out.
  1. In Telegram, open @BotFather, send /newbot and follow the questions.
  2. It gives you a token like 123456:ABC... Paste it below.""")
    if not yes("Set it up now?", False):
        return
    env = read_env()
    env["TELEGRAM_BOT_TOKEN"] = secret("Bot token", env.get("TELEGRAM_BOT_TOKEN", ""))
    write_env(env)
    if not env["TELEGRAM_BOT_TOKEN"]:
        return
    print("\n  Now link your Telegram account, so the bot only ever answers you.")
    if yes("Start pairing now? (send any message to your bot when asked)"):
        subprocess.run([PY, "remote.py", "--pair", "telegram"])
    if yes("Start Karen Remote automatically when you sign in to Windows?", False):
        subprocess.run([PY, "scripts/autostart.py", "remote", "on"])
    note("Start it by hand any time: .venv\\Scripts\\python.exe remote.py")


def step_phone():
    title(7, "Android phone control (optional)")
    print("""  Karen can read notifications, send SMS, make calls and announce incoming calls, over Wi-Fi.
  Needs: an Android phone with Developer options > Wireless debugging on, on the same Wi-Fi, and Google's
  free adb tool (winget install Google.PlatformTools).""")
    if not yes("Set it up now?", False):
        return
    import glob
    winget_adb = glob.glob(os.path.expandvars(
        r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Google.PlatformTools*\platform-tools\adb.exe"))
    if not shutil.which("adb") and not winget_adb:
        if yes("adb isn't installed. Install it now with winget?"):
            subprocess.run(["winget", "install", "-e", "--id", "Google.PlatformTools"])
            warn("Close this window and run setup again so Windows finds adb.")
            return
    subprocess.run([PY, "scripts/phone_pair.py"])
    if yes("Did the pairing work?"):
        set_config(("mcp_servers", "phone", "enabled"), True)
        set_config(("phone_watch", "enabled"), yes("Should Karen say incoming calls and messages aloud?"))
        ok("Phone control is on.")


def step_alexa():
    title(8, "Smart home through Amazon Alexa (optional)")
    print("""  Karen switches the lights, fans, plugs and AC that are already in your Alexa app.
  Uses AlexaPy (unofficial), which signs in through your browser once.""")
    if not yes("Set it up now?", False):
        return
    region = ask("Your Amazon site (amazon.com, amazon.in, amazon.co.uk, amazon.de ...)",
                 (get_config().get("alexa") or {}).get("region", "amazon.com"))
    set_config(("alexa", "region"), region)
    subprocess.run([PY, "scripts/alexa_login.py"])
    if yes("Did the sign-in work?"):
        set_config(("mcp_servers", "alexa", "enabled"), True)
        ok("Smart home is on. Try: \"Karen, turn off the bedroom light\".")


def step_extras():
    title(9, "Extras (optional)")
    set_config(("morning", "enabled"), yes("Morning briefing: the first time each morning, Karen tells you the "
                                           "weather, news and your day. Turn it on?", False))
    if yes("Face unlock: Karen only answers you (checks your face on the webcam). Enroll your face now?", False):
        subprocess.run([PY, "scripts/enroll_face.py"])
        note("Turn it on in the window: Settings > Face unlock.")
    print("""
  Power mode lets Karen write and run small programs for bigger PC tasks (Open Interpreter). It asks before
  starting and before any risky step. It needs a separate Python 3.12 with: pip install open-interpreter""")
    if yes("Set up power mode?", False):
        path = ask("Full path to that python.exe",
                   os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python312\python.exe"))
        if os.path.exists(path):
            set_config(("pc_control", "power_mode", "python"), path)
            ok("Power mode is on.")
        else:
            warn("That file doesn't exist; power mode stays off.")
    if yes("Start Karen automatically when you sign in to Windows (hidden in the tray)?", False):
        subprocess.run([PY, "scripts/autostart.py", "on"])


def step_wake():
    title(10, "Teach Karen your voice (wake word training)")
    print("""  Out of the box Karen hears "Karen" with general speech recognition, which works but can miss your
  accent. A short recording session (~20 minutes: say "Karen" 40 times and read 60 sentences) trains a
  wake word detector on YOUR voice. Guide: docs/TRAINING.md

  Until then you can always press Space (or the mic button) to talk, or type.""")
    if not yes("Record the session now?", False):
        return
    subprocess.run([PY, "scripts/record_session.py"])
    if yes("Train the wake word now? (takes 10 to 20 minutes)"):
        subprocess.run([PY, "scripts/train_wake.py"])


STEPS = [step_about, step_brain, step_audio, step_voice, step_google, step_remote, step_phone, step_alexa,
         step_extras, step_wake]


def finish():
    print(f"""
{PURPLE}{BOLD}{'═' * 64}
  All set!
{'═' * 64}{END}
  Start Karen:  double-click {BOLD}run.bat{END}
  Then say {BOLD}"Karen, what's the weather today?"{END}  (or press Space and talk, or type)

  Try:  "Karen, set a timer for five minutes"
        "Karen, open Notepad"
        "Karen, what's on my screen?"
        "Karen, remind me to drink water every hour"

  Change anything later:  {BOLD}setup.bat --menu{END}   ·   Help: docs/SETUP.md
""")


def main():
    if not os.path.exists("config.yaml"):
        sys.exit("Run this from Karen's folder.")
    print(f"""{PURPLE}{BOLD}
   ██╗  ██╗ █████╗ ██████╗ ███████╗███╗   ██╗
   ██║ ██╔╝██╔══██╗██╔══██╗██╔════╝████╗  ██║
   █████╔╝ ███████║██████╔╝█████╗  ██╔██╗ ██║
   ██╔═██╗ ██╔══██║██╔══██╗██╔══╝  ██║╚██╗██║
   ██║  ██╗██║  ██║██║  ██║███████╗██║ ╚████║
   ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝╚═╝  ╚═══╝{END}
   {BOLD}Setup wizard{END}  {DIM}(Enter keeps the answer in [brackets]; Ctrl+C stops, nothing is lost){END}""")
    try:
        if "--menu" in sys.argv:
            for i, step in enumerate(STEPS, 1):
                print(f"    {i:>2}. {step.__doc__ or step.__name__[5:].title()}")
            choice = ask("Which step")
            if choice.isdigit() and 1 <= int(choice) <= len(STEPS):
                STEPS[int(choice) - 1]()
            return
        for step in STEPS:
            step()
        finish()
    except KeyboardInterrupt:
        print("\n  Stopped. Run setup.bat again to continue; your answers so far are saved.")


for _step, _doc in zip(STEPS, ["About you", "AI keys", "Microphone", "Voice", "Gmail and Calendar",
                               "Telegram (phone chat)", "Android phone", "Alexa smart home", "Extras",
                               "Wake word training"]):
    _step.__doc__ = _doc

if __name__ == "__main__":
    main()
