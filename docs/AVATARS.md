# The avatar

Click the **avatar button** in the dock and an anime character replaces the orb. She does the following:
- breathes, blinks, and follows your mouse with her eyes
- tilts her head while listening and puts a hand to her chin while thinking
- lip-syncs to her real voice
- shows the feeling of her answer: happy, sad, surprised or relaxed
- waves hello

Say "dance" or "catwalk" and she performs, if motion files are installed (see below).

## Included character
**Karen (girl):** pixiv's VRoid sample model `VRM1_Constraint_Twist_Sample.vrm` (from github.com/pixiv/three-vrm),
saved as `models/karen.vrm`. Its licence allows anyone to use, modify and redistribute it, including commercially,
with no credit needed.

## Add your own characters
1. Get a **VRM** model, for example from https://hub.vroid.com (check each model's licence) or one you made in the
   free VRoid Studio. Put it in `models/avatars/`.
2. Add it to `config.yaml` under `avatar: characters:`:
   ```yaml
   - {id: mia, name: Mia, file: models/avatars/mia.vrm, credit: "author's name"}
   ```
   - `romance: false` turns off the kiss and blush reactions for that character.
   - A Mixamo-rigged **FBX** works too: `file: models/x/x.fbx, textures: models/x`.
3. Restart Karen and pick the character in **Settings → Avatar**.

## Motion capture (optional, free)
Karen moves on her own built-in poses. For real motion capture, download Mixamo animations (free Adobe account).
Their licence lets you use them, but not share the files, which is why they aren't in the zip.

1. Sign in at https://www.mixamo.com.
2. For each animation below, search for it and click **Download**:
   - Format **FBX Binary**
   - **Without Skin**
   - 30 fps
3. Save the files into `models/motions/` with these exact names:

| File | Mixamo animation | Used when |
|---|---|---|
| `idle.fbx`, `idle2.fbx` | Idle, Weight Shift | resting |
| `thinking.fbx` | Thinking | thinking |
| `talking.fbx`, `talking2.fbx`, `talking3.fbx` | Talking (three variants) | speaking |
| `wave.fbx` | Waving | hello |
| `happy.fbx`, `sad.fbx`, `nod.fbx` | Happy Idle, Sad Idle, Head Nod Yes | reactions |
| `listening.fbx`, `surprised.fbx` *(optional)* | any listening / surprised clip | |
| `dance.fbx` … `dance7.fbx` *(optional)* | Hip Hop Dancing, Samba Dancing, Flair… | "dance" |
| `catwalk.fbx`, `catwalk2.fbx` *(optional)* | Catwalk Walk | "catwalk" |
| `cheer.fbx`, `cheer2.fbx`, `excited.fbx`, `happy2.fbx` *(optional)* | Cheering, Excited | happy answers |
| `blowkiss.fbx`, `kiss.fbx`, `bashful.fbx` *(optional)* | Blow A Kiss, Kiss, Bashful | love mode reactions |

Restart Karen: the motions are retargeted to every character automatically.
