# Karen: your voice assistant for Windows

Say **"Karen"**, then ask. She answers out loud in a natural voice, and she can act on what you ask. She can run your
PC, read and reply to Gmail, manage your Calendar, see through your webcam, research topics, remind you, and control
your smart home. She also chats with you on Telegram from your phone. The wake word, the voice, face unlock and
hand tracking all run **on your own PC**, and the AI uses free API tiers.

![Karen's window](docs/images/karen-window.png)

## What you need
- Windows 10 or 11, **64-bit**. An ordinary laptop CPU is enough; no graphics card is needed.
- 8 GB RAM (16 GB is better) and about **4 GB free disk** for Python, packages and models.
- A microphone and speakers. A webcam is optional (for face unlock, "what am I holding?" and hand gestures).
- A free **Gemini** and/or **Groq** API key. The setup shows you where to get them, and it takes 2 minutes.

## Install (one time, 10-20 minutes)
1. Get the code into a folder with space, such as `D:\Karen` or `Documents\Karen`:
   `git clone https://github.com/rithikbr1974-afk/karen-voice-assistant.git Karen`, or **Code > Download ZIP**
   on this page and unzip it (don't run it from inside the zip).
2. Double-click **`SETUP.bat`**. It installs its own Python inside the folder, then Karen's packages and models
   (~2.5 GB download). After that it asks for your name, city and keys.
3. Double-click **`START_KAREN.bat`**. Say *"Karen, what's the weather?"*, or press **Space** to talk.

Windows SmartScreen may warn about a downloaded `.bat` file. Click *More info → Run anyway*. You can read every
file first: they are plain text.

## The buttons
| File | What it does |
|---|---|
| `SETUP.bat` | Installs everything and runs the first-time questions. Safe to run again. |
| `START_KAREN.bat` | Opens Karen's window, then listens for "Karen". |
| `CHANGE_SETTINGS.bat` | Changes your name, city and keys (Enter keeps a value). |
| `TRAIN_VOICE.bat` | Teaches Karen **your** voice saying "Karen" (a personal wake word). |
| `TRAIN_FACE.bat` | Teaches Karen your face for face unlock. |
| `TRAIN_HANDS.bat` | Hand-gesture trainer for the 3D viewer and phone mirror. |
| `PHONE_CHAT.bat` | Karen on Telegram: chat, send photos, PDFs and voice notes. |

## Try saying
- "Karen, what's the weather tomorrow?" / "Good morning" (briefing)
- "Karen, remind me to call Mom at 6 pm" / "set a timer for 10 minutes"
- "Karen, open Spotify and play some music" / "turn the volume down" / "take a screenshot"
- "Karen, any new emails?" / "what's on my calendar today?" (after Google sign-in)
- "Karen, what am I holding?" (webcam) / "research the best budget phones"
- "Karen, translate good morning into Spanish" / "start a pomodoro"
- "repeat that" / "say it slower" / "work mode" / "love mode"

## Guides
- [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md): keys, Google, Telegram, phone, Alexa, start with Windows,
  and fixes.
- [docs/TRAINING.md](docs/TRAINING.md): voice, face and hand training.
- [docs/FEATURES.md](docs/FEATURES.md): everything Karen can do.
- [docs/AVATARS.md](docs/AVATARS.md): the anime avatar, your own characters, and motion capture.

## Privacy, in short
- Your keys live only in `.env` on your PC.
- Your memories, reminders, notes and face profile live in `data/`. Your voice model is in `models/`.
- The wake word, text-to-speech, face unlock and hand tracking never leave the PC.
- What you *ask* goes to the AI provider you configured, as with any AI assistant.
  - Requests that touch your email, files or camera go only to the providers marked private in `config.yaml`
    (OpenRouter with zero data retention, and Groq once you turn its Zero Data Retention on).
- **Asks first:** before she sends, deletes, shuts down or switches a high-power appliance, Karen asks "yes or no?"
  out loud.

See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for the open-source parts Karen is built on.
