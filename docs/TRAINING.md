# Teach Karen your voice

Out of the box, Karen notices her name with general speech recognition. That works for many voices, but a
**bare "Karen"** is short and easy to mishear (it often comes out as "Get in" or "Kevin"), especially with an
accent or background noise.

A **20-minute recording session** fixes that. You say "Karen" in different ways and read some sentences, and
Karen trains a small wake word detector on **your own voice**. Everything stays on your PC.

| | Before training | After training |
|---|---|---|
| Hears a bare "Karen" | Sometimes | Almost always (about 9 in 10 in tests) |
| CPU while waiting | Higher (speech recognition) | Tiny (about 4 ms every 160 ms) |
| False wakes | Words that sound similar | Rare. A false wake only plays a soft chime. |

You don't *need* this. Without it you can also say "Karen" as part of a sentence ("Karen, open Chrome"), press
**Space** to talk, or type.

---

## Before you start

- **Close Karen** (her window and tray icon). Otherwise she answers you during the session.
- Sit where you normally use her, with the **microphone you'll normally use**.
- Find a time when the room sounds as it usually does. Some background noise is fine, and even good.
- Have some **music or a video** ready to play for a few clips (Karen must still hear you over it).
- About **20 minutes**. You can stop at any time with Ctrl+C. Run the session again and it carries on where you
  left off.

## Step 1: Record

Run the recording from the wizard (`setup.bat --menu` > **Wake word training**) or directly:

```bash
.venv\Scripts\python.exe scripts\record_session.py
```

For each clip: **wait for the beep, then speak**. The script checks each take and asks you to redo it if it was
too quiet, too loud or cut off.

| Part | What you say | Clips |
|---|---|---|
| **A. The wake word** | "Karen" (and every 5th time "Hey Karen"), in 9 styles: normal, louder, softer, faster, slower, from further away, across the room, half-mumbled, and with music playing | 40 |
| **B. Commands** | "Karen, set a timer for ten minutes" and similar, the way you'd really ask | 30 |
| **C. Normal talk** | Everyday sentences without the wake word, including sound-alikes ("Kevin and Gavin…", "Get in the car…") | 30 |
| **D. Room sound** | 15 s of your quiet room, then 15 s with music playing (don't talk) | 2 |

**Tips for good data:**
- **Say it the way you really would.** Don't use a "recording voice". Tired, lazy or quick versions help most.
- In the "far away" clips, actually lean back or stand up.
- Part C matters as much as part A: it teaches Karen what *isn't* her name.

To see every phrase first: `scripts\record_session.py --list`. To start over: `--redo`.

## Step 2: Train

```bash
.venv\Scripts\python.exe scripts\train_wake.py
```

This takes **10 to 20 minutes** on a laptop CPU, and you can keep using the PC while it runs. It:

1. Mixes your clips with computer voices saying "Karen" and similar-sounding words.
2. Adds variety: volume changes, room noise, echo and speed changes.
3. Trains a small classifier and keeps a quarter of your clips aside to test it honestly.
4. Picks the threshold that catches the most of your "Karen"s with almost no false wakes.

At the end it prints a line like:

```
Held out: wakes on 92% of your 'Karen's, 0.0 false wakes per hour of other speech
Saved models/karen_personal.pkl.
```

Karen uses it automatically (`wake.engine: personal` in `config.yaml`). Restart her.

## Step 3: Use it, and make it better over time

- The **Wake sensitivity** number in **Settings** is how sure she must be before she wakes.
- **She misses you sometimes:** lower that number a little.
- **She wakes by herself:** raise the number a little. Every false wake is also saved to `voice_tests/false_wakes/`.
  After about 20 of them, run `train_wake.py` again and she learns to ignore those sounds.
- **New mic, new room, or still not great:** record a second round. Training then uses both rounds.
  ```bash
  .venv\Scripts\python.exe scripts\record_session.py --session 2
  .venv\Scripts\python.exe scripts\train_wake.py
  ```

### Measure it yourself (optional)

```bash
.venv\Scripts\python.exe scripts\eval_wake.py models\karen_personal.pkl voice_tests\session1
.venv\Scripts\python.exe scripts\eval_stt.py
.venv\Scripts\python.exe scripts\eval_stt.py groq
```

- `eval_wake.py` replays your recordings through the detector exactly as it runs live. Try `--patience=3` for
  fewer false wakes, or `--threshold=0.9`.
- `eval_stt.py` shows how many words speech-to-text gets wrong on your sentences: local Whisper, or Groq with
  `groq`.

## Privacy

Your recordings (`voice_tests/`) and your trained model (`models/karen_personal.pkl`) stay on your PC and are
ignored by Git. **Don't share them.** A voice model is personal data.
