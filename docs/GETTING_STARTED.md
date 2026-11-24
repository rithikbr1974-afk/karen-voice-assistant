# Getting started

## 1. Install
Clone or download Karen into a folder with ~4 GB free (for example `D:\Karen`), then double-click **SETUP.bat**.
The first run also downloads `uv` (Astral's Python installer) into `Karen\tools`.

1. **Python 3.12** is downloaded into `Karen\.python` and doesn't touch any Python you already have.
2. **Packages** (`requirements.txt`) go into `Karen\.venv`.
3. **Models** go into `Karen\models`:
   - Kokoro voice (330 MB)
   - Whisper speech-to-text (tiny.en and small.en)
   - Silero VAD
   - openWakeWord features
   - YuNet and SFace (face)
   - MediaPipe hands
4. **The setup wizard** asks for your name, city, news country, keys and optional extras. It writes `.env` and
   `config.yaml`.

SETUP.bat is safe to run again: finished steps are skipped. To change answers later, use **CHANGE_SETTINGS.bat**.

## 2. The AI keys (free)
| Key | Used for | Get it |
|---|---|---|
| `GEMINI_API_KEY` | everyday chat (fast, free tier) | https://aistudio.google.com/apikey |
| `GROQ_API_KEY` | backup brain, tool requests, cloud speech-to-text (~0.5 s) | https://console.groq.com/keys |
| `OPENROUTER_API_KEY` *(optional)* | images and screen understanding, the screen agent, power mode | https://openrouter.ai/keys |

- Karen tries the brains in order, Gemini → Groq → OpenRouter. If one is busy or out of quota, the next one answers.
- **Privacy switch:** Groq keeps requests up to 30 days for abuse checks unless you turn on **Zero Data Retention**
  (console.groq.com → Settings → Data Controls).
  - When it's on, answer *yes* in the wizard (`llm.groq_zdr: true`).
  - Requests that use your email, files or camera only go to these "private" backends.

## 3. Using Karen
- **Talk:** say "Karen", then your request, in one breath ("Karen, what time is it?"). Or say "Karen", wait for the
  soft chime, then speak.
- **Push-to-talk:** press **Space**, or click the mic button. **Type:** press `/`, or click the text box.
- **Stop her talking:** say "Karen" while she speaks, press **Esc**, or click stop.
- **Settings** (gear icon): voice, speed, which brain answers first, wake sensitivity, sounds, look (Aurora or
  Classic), avatar, personality mode and face unlock.
- **Files:** drag files onto the window, or use **+**. Then ask "summarise this", "convert it to PDF" or "what's in
  this picture?".
- **Asks first:** before sending, deleting, shutting down, or switching on a high-power appliance, Karen asks
  "…yes or no?". Answer by voice or with the buttons; 8 seconds of silence counts as *no*.

## 4. Google: Gmail, Calendar, Drive, Docs, Sheets (optional, ~10 min)
Karen uses the open-source `workspace-mcp` server. Google needs your own free "OAuth client":

1. Go to https://console.cloud.google.com and create a project (any name, e.g. "Karen").
2. Under **APIs & Services → Library**, enable Gmail API, Google Calendar API, Google Drive API, Google Docs API and
   Google Sheets API.
3. Under **APIs & Services → OAuth consent screen**, choose *External*. Fill in the app name and your email.
   Under **Audience → Test users**, add your own Gmail address.
4. Under **Clients → Create client**, pick type **Desktop app**. Copy the *Client ID* and *Client secret*.
5. Run CHANGE_SETTINGS.bat and paste them, plus your Gmail address.
6. Ask Karen "any new emails?". Your browser opens for Google sign-in the first time.

While the app stays in *Testing*, Google asks you to sign in again about every 7 days.

## 5. Karen on your phone: Telegram (optional, 3 min)
1. In Telegram, message **@BotFather**, send `/newbot`, pick a name, and copy the token.
2. Run CHANGE_SETTINGS.bat and paste it as `TELEGRAM_BOT_TOKEN`.
3. Run **PHONE_CHAT.bat** and send your bot any message. Confirm it's you, and the bot is linked to your account.
   Only linked accounts get answers.
4. Now chat with Karen from anywhere. You can send:
   - **photos:** she describes them
   - **PDFs, Word, Excel, PowerPoint:** she summarises them
   - **voice notes:** she transcribes them and answers
   - **videos:** she describes the frames and the sound
5. Commands: `/status`, `/wrap` (tomorrow's plan), `/new`, `/stop`, `/work` `/both` `/love` `/normal`.
6. To start Karen Remote with Windows: `.venv\Scripts\python.exe scripts\autostart.py remote on`.

Karen Remote also sends you due reminders, a 9 pm wrap-up (`evening:` in config.yaml), and alerts for low battery
or a full disk.

## 6. Your Android phone (optional)
1. Install Google's platform tools: `winget install Google.PlatformTools`. For the phone mirror, also
   `winget install Genymobile.scrcpy`.
2. On the phone, open **Settings → Developer options → Wireless debugging → Pair device with pairing code**.
3. Run `.venv\Scripts\python.exe scripts\phone_pair.py`.
4. Then try: "call Alex", "read my last SMS", "who called?", "find my phone", "open YouTube on my phone".
   - Calls and SMS always ask first.
   - Security codes (OTPs) are hidden.
5. Set `phone_watch: enabled: true` in config.yaml and Karen announces incoming calls ("Alex is calling. Pick up or
   reject?") and new messages.

## 7. Smart home through Alexa (optional)
1. Set `alexa: region:` in config.yaml to your Amazon site (amazon.com, amazon.in, amazon.co.uk…).
2. Run `.venv\Scripts\python.exe scripts\alexa_login.py` and sign in.
3. Try "turn off the bedroom light", "set the fan to 50%", or "run good night".
   - Air conditioners, heaters and other high-power devices ask first.

## 8. Extras
- **Free AI pictures** ("draw a cat astronaut"): add `CF_ACCOUNT_ID` and `CF_API_TOKEN` from a free Cloudflare
  account. The token needs the *Workers AI* permission. Without them, Karen uses the free AI Horde, which is slower.
- **Power mode** ("power mode: rename all photos in Downloads by date"):
  - Install Python 3.12 from python.org, then run `py -3.12 -m pip install open-interpreter==0.4.3`.
  - Check the `pc_control: power_mode: python:` path in config.yaml.
  - Power mode needs OpenRouter credits.
  - It asks before the task, and again before any risky step.
- **ARTEMIS phone agent:** install https://github.com/google/artemis, then add this to config.yaml under
  `mcp_servers:` and set `artemis: dir:` to its folder:
  ```yaml
  artemis:
    label: Phone agent (ARTEMIS)
    command: "{python}"
    args: [servers/artemis/server.py]
    timeout: 300
    triggers:
      "*": [on my phone, in my phone, from my phone, my phone, phone, mobile, android]
  ```
- **Start with Windows:** `.venv\Scripts\python.exe scripts\autostart.py on` starts voice Karen hidden in the tray.
  Use `off` to undo it.
- **Night mode:** from 23:00 to 07:00 she speaks at half volume and answers briefly (`night:` in config.yaml).

## 9. Fixes
| Problem | Fix |
|---|---|
| She doesn't hear me | `START_KAREN.bat --mic` shows the mic level. `--devices` lists microphones; set `audio: input_device:` in config.yaml. |
| She wakes by mistake / misses "Karen" | Run TRAIN_VOICE.bat for your personal wake word, then move Settings → Wake sensitivity a little. |
| "No LLM API key found" | Run CHANGE_SETTINGS.bat and add a Gemini or Groq key. |
| Slow first answer | The first start loads the models (~10 s). After that she answers in about 1-2 s. |
| A feature says "not signed in" | Check its section above: Google, Alexa or phone. |
| Something else | Open the log in Settings → *Open log*, or `logs\karen.log`. Each tool server logs to `logs\mcp_<name>.log`. |

Uninstall: delete the Karen folder. If you turned on autostart, run `scripts\autostart.py off` first.
