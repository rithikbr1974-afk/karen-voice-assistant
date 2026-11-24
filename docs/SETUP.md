# Setting up Karen, step by step

This guide assumes you have never done this before. Each step says what to click and what you should see.
It takes about **20 minutes** for the basics, plus extra time for any optional parts you want.

> **Short version:** install Python 3.12, download Karen, double-click `setup.bat`, then answer the wizard's
> questions. The rest of this page explains each question, and how to fix things if something goes wrong.

---

## What you need

| | |
|---|---|
| **Computer** | Windows 10 or 11, 8 GB RAM or more (16 GB is comfortable), any modern CPU. A GPU isn't needed. |
| **Disk space** | About 3 GB free (packages plus voice and speech models) |
| **Microphone** | Any: laptop mic, headset or webcam mic |
| **Internet** | For the AI brain. The wake word and voice run on your PC. |
| **Accounts** | A Google account (for the free Gemini key). Everything else is optional. |

---

## Step 1: Install Python 3.12

1. Go to <https://www.python.org/downloads/> and download **Python 3.12** (newer versions may not work with every
   package yet).
2. Run the installer. **Tick "Add python.exe to PATH"** at the bottom of the first screen, then click *Install Now*.
3. Check it worked: press `Win + R`, type `cmd`, press Enter, and type:
   ```bash
   py -3.12 --version
   ```
   You should see `Python 3.12.x`.

*(Optional, faster installs: [uv](https://docs.astral.sh/uv/getting-started/installation/). If it's installed,
`setup.bat` uses it automatically.)*

## Step 2: Download Karen

**Option A, without Git:** on the GitHub page, click the green **Code** button, then **Download ZIP**. Unzip it
somewhere with space, for example `D:\karen-voice-assistant`.

**Option B, with Git:**
```bash
git clone https://github.com/rithikbr1974-afk/karen-voice-assistant.git
```

> Avoid putting Karen inside OneDrive. OneDrive tries to sync the 1.5 GB of models.

## Step 3: Run the setup

Double-click **`setup.bat`** in Karen's folder. It does three things:

1. **Installs the packages** into a private `.venv` folder. This takes a few minutes and doesn't change the rest
   of your PC.
2. **Downloads the models** (about 1.5 GB): the voice (Kokoro), speech recognition (Whisper), wake word features,
   face recognition and the avatar.
3. **Starts the setup wizard**, which asks you the questions below. Press Enter to accept the answer shown in
   `[brackets]`. Press Ctrl+C to stop at any time; your answers so far are saved.

You can always come back to one step later:
```bash
setup.bat --menu
```

### Wizard step 1: About you
Your first name (Karen calls you by it), your city (for the weather) and your country (for news and prices).
If you have a regional accent, say which one; it helps the AI guess words the speech recognition misheard.

### Wizard step 2: AI keys (Karen's brain)
Karen thinks with online AI models. All three below are **free and need no credit card**. You need at least
one; having all three means another one answers when one is busy.

| Provider | What it does | Get a key |
|---|---|---|
| **Gemini** (Google) | Main brain for chat | <https://aistudio.google.com/apikey>: sign in, click *Create API key*, copy it |
| **Groq** ⭐ recommended | Very fast. Handles tools and speech-to-text | <https://console.groq.com/keys>: sign up, *Create API Key*, copy it |
| **OpenRouter** | Backup, with free models | <https://openrouter.ai/keys>: sign up, *Create key*, copy it |

Paste each key when asked. The wizard checks that the key works. Keys are saved in a file called `.env`, which
is **never uploaded** to GitHub.

**Privacy tip (Groq):** turn on *Zero Data Retention* at console.groq.com > **Settings > Data Controls**. Then
Groq also handles your private requests (email, files, camera), which is faster. Answer "yes" to the wizard's
question once it's on.

> Free tiers have daily limits (Gemini: plenty for personal use; Groq: 1,000 requests a day; OpenRouter free
> models: about 50 a day). Karen switches to the next provider by herself when one runs out.

### Wizard step 3: Microphone
Pick your microphone from the list (Enter = the Windows default), then speak for 3 seconds to test it.
- **"Very quiet":** the mic is muted, or it's the wrong one. Check *Windows Settings > System > Sound > Input*.
- **"Too loud":** lower the input volume there.

### Wizard step 4: Voice
Listen to a few voices and pick one. You can change it later in the window's **Settings**.

### Wizard steps 5 to 9: Optional extras
Each has its own section below. Skip any you don't want; you can add them later with `setup.bat --menu`.

### Wizard step 10: Teach Karen your voice
See **[TRAINING.md](TRAINING.md)**. This step is optional but recommended. Karen works without it: say
"Karen, …", press **Space** to talk, or type.

## Step 4: Start Karen

Double-click **`run.bat`**. A purple window opens. When she says *"I'm ready"*, try:

- "Karen, what's the weather today?"
- "Karen, set a timer for five minutes."
- "Karen, open Notepad."

More things to say: **[FEATURES.md](FEATURES.md)**.

**Other ways to start:**

| Command | What it does |
|---|---|
| `run.bat --text` | Type instead of talking (good for testing) |
| `run.bat --no-ui` | Voice only, no window |
| `run.bat --mic` | Microphone level meter |
| `run.bat --devices` | List the audio devices |

To start Karen with Windows, hidden in the tray:
```bash
.venv\Scripts\python.exe scripts\autostart.py on
```

---

## Optional parts

### Gmail and Calendar
Karen uses Google's official sign-in. Google needs you to create a free "OAuth client" for that, once:

1. Go to <https://console.cloud.google.com/> and create a project (top bar > project picker > **New project**,
   any name, e.g. "Karen").
2. **Turn on the APIs.** Search the top bar for each of these and click **Enable**: *Gmail API*,
   *Google Calendar API*, *Google Drive API*, *Google Docs API*, *Google Sheets API*.
3. **Consent screen.** Open **Google Auth Platform** (search "OAuth consent"):
   - **Branding:** app name "Karen", your email as the support and contact email. Save.
   - **Audience:** user type **External**. Under **Test users**, add your own Gmail address.
4. **Client.** Under **Clients**, click **Create client**, set *Application type* to **Desktop app**, then
   **Create**. Copy the **Client ID** and **Client secret**.
5. Run `setup.bat --menu`, choose **Gmail and Calendar**, and paste them along with your Gmail address.
6. Start Karen and ask *"Karen, any new emails?"* A browser window opens. Sign in, then click *Continue* on the
   "Google hasn't verified this app" screen (it's your own app), and allow access.

> While the app is in *Testing* mode, Google asks you to sign in again every 7 days. To avoid that, click
> **Publish app** under Audience. Google doesn't need to review an app used only by you.

Karen **reads emails back and asks before sending** anything.

### Telegram: chat with Karen from your phone
1. In Telegram, open **@BotFather**, send `/newbot`, pick a name and a username ending in `bot`.
2. Copy the token it gives you (looks like `123456789:AA...`).
3. Run `setup.bat --menu`, choose **Telegram**, paste the token, and send any message to your new bot when the
   wizard asks. That links your account; the bot ignores everyone else.
4. Start Karen Remote: `.venv\Scripts\python.exe remote.py`, or let the wizard start it with Windows.

Useful commands in the chat: `/status`, `/stop`, `/new`, `/work`, `/love`, `/normal`.

*WhatsApp also works, through Meta's WhatsApp Cloud API and ngrok, but it needs a Meta developer account. See
the comments in `core/whatsapp.py` and `.env.example`.*

### Android phone (calls, SMS, notifications)
Karen talks to your phone over Wi-Fi with Google's `adb` tool. No app is needed on the phone.

1. Install adb: `winget install Google.PlatformTools`
2. On the phone: **Settings > About phone**, then tap **Build number** 7 times. Developer options are now on.
3. **Settings > Developer options > Wireless debugging**: turn it on, then tap **Pair device with pairing code**.
4. Run `setup.bat --menu`, choose **Android phone**, and type the IP address, port and code shown on the phone.

The phone and PC must be on the same Wi-Fi. Karen asks before calling or sending SMS, and hides one-time
codes (OTPs).

### Smart home (Alexa)
If your lights, plugs, fans or AC are in the Alexa app, Karen can switch them.

1. Run `setup.bat --menu`, choose **Alexa**, and type your Amazon site (`amazon.com`, `amazon.in`,
   `amazon.co.uk`…).
2. A browser page opens. Sign in to Amazon there (including any two-step code).

High-power devices (AC, heater, geyser…) need a spoken yes. Alexa support uses the unofficial AlexaPy library;
if Amazon changes its sign-in, re-run `.venv\Scripts\python.exe scripts\alexa_login.py`.

### Face unlock
Karen answers only you: she checks the webcam before the first request, and again after 30 idle minutes.
1. `.venv\Scripts\python.exe scripts\enroll_face.py`: look at the camera and turn your head slowly.
2. Turn it on in the window: **Settings > Face unlock**.

It's a privacy lock, not strong security: a good photo of you may pass. Only face numbers are saved
(`data/face_profile.npy`), never pictures.

### Power mode (optional, advanced)
For bigger PC jobs ("rename all these files by date"), Karen can hand the task to Open Interpreter, which writes
and runs small programs. **She asks before starting and before every risky step**, and every program is saved to
`logs/power_mode.log`.

1. Install a separate Python 3.12, then in a terminal: `py -3.12 -m pip install open-interpreter`
2. Run `setup.bat --menu`, choose **Extras**, and give the path to that `python.exe`.

### Anime avatar
Click the **person icon** in the window's bottom bar. The free sample character is downloaded during setup.
- **More characters:** download a VRM from [VRoid Hub](https://hub.vroid.com) (check its licence allows it),
  put it in `models/avatars/`, and add a line to `config.yaml → avatar → characters`.
- **Motions (optional, makes her much more lifelike):** sign in at <https://www.mixamo.com> (free), and download
  animations as **FBX, Without Skin**. Save them in `models/motions/` with these names:
  `idle`, `idle2`, `listening`, `thinking`, `talking`, `talking2`, `wave`, `happy`, `sad`, `surprised`, `nod`,
  plus extras: `dance` … `dance7`, `catwalk`, `cheer`, `excited`. Example: `models/motions/wave.fbx`.
  Without motions she uses simple built-in poses.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `setup.bat` says Python 3.12 wasn't found | Install it (step 1) and tick *Add to PATH*; then close and reopen the folder |
| A package fails to install | Run `setup.bat` again (downloads sometimes fail). Check there are 3 GB free. |
| "No LLM API key found" | Run `setup.bat --menu` > **AI keys** |
| She never hears "Karen" | Press **Space** to talk for now; check the mic (`run.bat --mic`); do the [voice training](TRAINING.md) |
| She wakes up by herself | Raise the *Wake sensitivity* number in **Settings** (higher = she must be surer), or record a second training session |
| She mishears words | Add a Groq key (much better speech recognition); speak a little slower; set your accent in step 1 |
| Gmail says "access blocked" | Add your Gmail as a **test user** (Gmail step 3) |
| Gmail asks to sign in every week | Publish the app (see the note in the Gmail section) |
| "The free AI model is busy" | Wait a minute, or add another provider's key |
| Feature shows "needs .env keys" | That feature's key is missing in `.env`; run its wizard step |
| Anything else | Look in `logs/karen.log` (and `logs/mcp_<name>.log` for a feature), then open a GitHub issue with the error |

## Where your data lives

Everything personal stays in Karen's folder and is ignored by Git, so it never goes to GitHub:

| Folder / file | What's in it |
|---|---|
| `.env` | Your keys |
| `settings.json` | Window settings |
| `data/` | Reminders, notes, memories, to-dos, face profile, uploads |
| `logs/` | What happened, for fixing problems |
| `voice_tests/` | Your training recordings |
| `models/` | Downloaded models and your trained wake word |

Deleting the folder removes Karen completely. Run `scripts\autostart.py off` first if you turned on autostart.
