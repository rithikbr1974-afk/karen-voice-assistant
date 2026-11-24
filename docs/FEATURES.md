# Everything Karen can do

About 200 abilities in 13 groups. Karen sends the AI only the tools a request needs, so answers stay fast and cheap.
Anything that sends, deletes, shuts down or switches a high-power appliance asks **"yes or no?"** first.

## Talking with Karen
- **Wake word:** "Karen", as a personal model trained on your voice. She also has push-to-talk (Space) and typing.
- **Natural voice:** Kokoro TTS on your PC, in 6 voices (American and British). She starts speaking about 1 second
  after the AI's first word.
- **Fast brain with fallback:** Gemini → Groq → OpenRouter. If one is busy, the next answers, and a slow provider is
  benched for 10 minutes.
- **Barge-in:** say "Karen" while she talks and she stops and listens.
- **Follow-ups:** if her answer is a question, she listens for your reply without the wake word.
- **"Repeat that" / "say it slower"** replay her last answer instantly, without asking the AI again.
- **Night mode:** from 23:00 to 07:00 she speaks at half volume and gives one- or two-sentence answers.
- **Other apps' sound dips** while she listens and talks (music won't drown you out), then comes back.
- **Personality modes:** Normal, Work (focused), Both (work with a little warmth), Love, Toxic (a playful jealous
  act with hard limits), or Custom ("talk to me like a pirate").
- **Morning briefing:** the first time each morning, she gives you the weather, news, reminders, calendar and
  important mail.

## Your PC
- **Apps:** open or close any Start-menu app ("open Spotify", "close Chrome").
- **Sound and media:** volume, mute, play/pause/next.
- **Web:** open a site in a chosen browser, or search the web.
- **Windows:** list, focus, minimise, maximise, snap left/right, show the desktop.
- **Keys and clicks:** shortcuts, "click Save", "read this window" (UI Automation, no screenshots).
- **Screen agent:** "play the first song on YouTube Music". She looks at the window, then clicks and types step by
  step. Risky clicks (buy, send, delete) ask first.
- **Explain this error:** she reads your screen, explains the error and opens the file at the right line in
  VS Code.
- **Read this page:** summarise or translate the article or document that's open.
- **Files:** find, open, create folders, move, rename, copy; delete goes to the Recycle Bin and asks first.
- **Settings:** brightness, dark mode, Wi-Fi, Bluetooth and display pages.
- **System:** battery, CPU, RAM and disk; lock, sleep, restart, shut down (asks first).
- **Clean up the C: drive:** scan for junk (caches, temp files, Recycle Bin), then clean it (asks first).
- **Clipboard history:** "paste the link I copied earlier". The last 30 items are kept in memory only, and
  secrets are skipped.
- **Screenshots**, with an optional description.
- **WhatsApp (desktop/Web):** read chats, send messages and files (asks first).
- **Power mode:** hand a bigger task to Open Interpreter, which writes and runs code. She asks before the task and
  again before every risky step, and every step is logged.

## Gmail, Calendar, Drive, Docs, Sheets
- "Any new emails?", "read the one from my college", "reply saying I'll be there". The reply is read back to you
  before it's sent.
- **Organise the inbox:** labels, filters, batch archive (asks first).
- Calendar: today's agenda, create, move or cancel events.
- Search Drive, and create or read Google Docs and Sheets.
- **Gmail → reminders:** on Telegram she suggests reminders from dates in new mail (deadlines, bills); tap ✅ or ❌.
- Attach Karen's PDF reports to emails.

## Everyday life
- **Weather** (Open-Meteo) and **news** (Google News), both free with no keys.
- **Reminders and timers:** one-off, daily, or every N minutes within a time window. Spoken, shown as a toast, and
  sent to your phone.
- **Memory:** "remember my locker code is 2468", then "what's my locker code?".
- **To-dos, notes, shopping lists:** shown as a page in Karen's viewer.
- **Expenses:** "I spent 250 on lunch", "how much did I spend this week?".
- **Countdowns:** "how many days until my exam?".
- **Currency:** "50 dollars in rupees" (ECB rates).
- **YouTube:** "summarise this video" (from its transcript).
- **Activity report:** "what did I ask you today?".

## Health and focus
- Water, eye, stretch and posture reminders; medicine reminders.
- Pomodoro focus sessions and HIIT workouts with spoken intervals.
- BMI (including South Asian cut-offs), daily calories, and a mood log with a summary.

## Camera, research and pictures
- **"What am I holding?"**: a live webcam look. The camera stays on for 60 s for follow-ups.
- **Research mode:** opens 3 tiled browser windows with an overview, the top real results and specialist sites.
  "Research summary" gives you 3 spoken points.
- **Research reports:** a designed PDF report on any product or topic, built from 6 web sources with facts, pros
  and cons, prices, alternatives and a verdict. It's ready in about 30 s and can start from the camera ("scan this
  and make a report").
- **Photos:** "send me a photo of the Eiffel Tower" finds a real picture. "Draw a cat astronaut" makes one with
  free AI.
- **Work modes:** "coding mode" opens VS Code, docs and lo-fi music, tiled. There are also study and chill modes.

## Files and documents
- Read PDF, Word, Excel, PowerPoint, text and old Office formats; describe images and videos; transcribe audio
  (up to 60 min).
- Convert (Office → PDF, images → PDF, PDF → images), merge, split, extract pages, resize.
- PDF tools: lock and unlock with a password (AES-256), compress, rotate, add a watermark.
- Zip and unzip, find duplicates, folder sizes, tidy a folder by type, batch rename (previews first, asks to apply).
- QR codes, image and audio conversion, word counts and text clean-up on the clipboard, a strong password
  generator, URL shortener.
- Translate and **say it in that language's voice**: Spanish, French, Hindi, Italian, Portuguese, Chinese.
- Voice memos and screen recordings, with or without the mic.
- **Karen's viewer:** any image, PDF, page or report opens in its own window.

## Karen on your phone (Telegram)
- Chat with the same brain and tools from anywhere, with the same "yes or no?" rules.
- Send her **anything**:
  - photos (described)
  - documents (summarised)
  - **voice notes** (transcribed, then answered)
  - videos (frames and sound)
  - albums (answered once)
- Proactive messages: due reminders, a **9 pm wrap-up** (tomorrow's calendar, to-dos and important mail), and
  alerts for low PC battery, a full disk or low phone battery.
- `/stop` shuts Karen down on the PC remotely; `/status` shows uptime and battery.
- WhatsApp works too (Meta Cloud API), if you set it up.

## Your Android phone (adb)
- Calls (dial, answer, hang up, choose a SIM), call log, read and send SMS, contacts. Calls and SMS ask first, and
  security codes are hidden.
- Incoming calls are announced: "Alex is calling. Pick up or reject?" Answer by voice.
- New WhatsApp/SMS messages are read out (quiet at night).
- Open apps, read the screen, notifications, screenshots, **find my phone** (it rings loudly).
- The phone agent taps through any app for you (ARTEMIS, optional).
- **Phone mirror with hand gestures:** control the phone on your PC screen with your hands.

## Smart home (Alexa)
- Lights, fans, plugs and TVs on/off, brightness, status. Alexa routines ("good night").
- High-power devices (AC, heater, geyser, oven…) ask first.

## Developer tools
- List your projects and open them in VS Code; open a file at a line.
- git status, log, changes, pull; commit and push (ask first).

## Dots and Spaces
- **Dots:** specialist helpers (Researcher, Writer, Coder, Organiser, or your own) that work in the background and
  on schedules ("every morning at 8, summarise AI news").
- **Spaces:** Markdown pages Karen and the Dots write and you can read and edit.

## Security and privacy
- **Face unlock:** she answers only you, re-checks after 30 idle minutes, and "lock yourself" locks her.
- **Intruder alert:** a stranger's photo is saved and sent to your Telegram.
- **"Yes or no?"** before every send, delete, shutdown, purchase-like click or high-power appliance.
- Your data stays in local files. Requests that touch your email, files or camera go only to zero-data-retention
  AI providers.
- Never typed into terminals; no shells or registry editor; no arbitrary code except power mode, which asks at
  every risky step.

## The interface
- **Aurora look:** a living orb of colour light that changes with her state (violet resting, cyan listening,
  magenta thinking, pink speaking, amber confirm), glass cards and chat bubbles. There's also a **Classic** HUD
  look.
- **Avatar mode:** an anime character with lip-sync, expressions, motion capture, dances, and more characters
  you can add.
- Live stats (CPU, RAM, battery), status chips, the abilities panel, drag-and-drop files, a tray icon, start with
  Windows, and **hand-gesture 3D viewer** for GLB, GLTF, FBX, OBJ, STL and VRM files.
