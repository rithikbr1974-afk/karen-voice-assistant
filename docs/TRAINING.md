# Teaching Karen you

Karen works right after setup. Training makes her **yours**: she wakes to your voice, unlocks for your face, and
reads your hands. Everything runs on your PC, and the recordings and profiles stay in `voice_tests/`, `data/` and
`models/`.

## Voice: your personal wake word (TRAIN_VOICE.bat, ~25 min, most of it unattended)
Before training, Karen uses speech recognition to spot "Karen". That works, but accents vary: a short "Karen"
alone can come out as "Get him" or "Garden". A personal wake word fixes this.

1. **Close Karen** (she uses the mic) and sit where you usually talk to her.
2. Run **TRAIN_VOICE.bat**. It records:
   - **40 × "Karen" / "Hey Karen"**: the window tells you how to say each one (normal, louder, softer, faster,
     slower, further away, across the room, mumbled).
   - **20 everyday sentences**, including sound-alikes ("Karan", "garden", "get in"), so she learns what *isn't*
     her name.
   - **15 seconds of room sound**: just stay quiet.
3. Training starts by itself and takes 10-20 minutes.
   - It mixes your clips with synthetic voices, room noise, echo and speed changes.
   - It tests the model on clips it didn't train on, and picks a threshold with at most 0.5% false alarms.
4. Start Karen again. The log says `personal wake word models/karen_personal.pkl`.

**Better still:** `.venv\Scripts\python.exe scripts\record_session.py` records a longer session:
- 40 "Karen"s in 9 styles, including over music
- 30 commands
- 30 sentences
- quiet and music room sound

Then run `scripts\train_wake.py` again; it uses every session it finds.

**She learns from mistakes:** when she wakes and hears nothing, the ~2 s of sound is saved to
`voice_tests/false_wakes/`. After ~20 of those, run `scripts\train_wake.py` again and she'll ignore those sounds.

**Tuning:** Settings → Wake sensitivity. Higher = fewer false wakes, lower = catches more. Check a model with
`scripts\eval_wake.py models\karen_personal.pkl voice_tests`.

## Face unlock (TRAIN_FACE.bat, 2 min)
1. Run **TRAIN_FACE.bat**. A webcam window asks for 6 head poses × 5 samples: straight, a little left, a little
   right, chin up, chin down, then straight again.
2. It prints your self-match score. **TRAIN_FACE.bat --test** shows the live score for whoever is in front of the
   camera.
3. In Karen's window, turn on **Settings → Face unlock**.
   - She starts locked, and checks your face before answering, then again after 30 idle minutes.
   - "Karen, lock yourself" locks her.
4. **Intruder alert:** if a stranger tries to use her, she saves their photo to `data/intruders/`. If Telegram is
   set up, she sends it to you.

**Kept:** only numbers (face embeddings) in `data/face_profile.npy`; no photos of you.
**Limits:** this is a privacy lock, not bank-grade security. There's no liveness check, so a good photo might pass.

## Hand gestures (TRAIN_HANDS.bat, 5 min)
The hands window tracks both hands with MediaPipe on your PC. Training records each gesture for 3 seconds in 3
kinds of light: normal, dim, and bright behind you.

| Gesture | 3D viewer | Phone mirror |
|---|---|---|
| ✋ Palm, hold 1 s | hand control on/off | hand control on/off |
| 🤏 Pinch + move | rotate | swipe / drag (hold 0.3 s + release = tap) |
| 🤏🤏 Both hands pinch | zoom (apart/together), roll (twist) | |
| ✊ Fist | move the model | hold 1 s = Home |
| ☝️ Point | | move the cursor |
| ✌️ Two fingers | hold 1 s = reset | up/down = scroll |
| 👍 Thumbs up (0.8 s) | next model | |

Then say "Karen, open my 3D model in the viewer" or "mirror my phone".

## Speech-to-text check (optional)
`scripts\record_samples.py` records 12 test phrases, and `scripts\eval_stt.py` scores how well each speech model
understands you. By default, Groq's Whisper large-v3 handles speech in the cloud (~0.5 s), and the local small.en
model takes over if Groq is unavailable.
