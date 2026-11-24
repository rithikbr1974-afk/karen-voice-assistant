# Karen interface: design spec

A single window: HTML/CSS/vanilla JS in `ui/`, shown by pywebview (Edge WebView2). Python pushes updates by
calling `window.karen.<method>()`, and the page calls Python through `window.pywebview.api.<method>()`.
Opened in a normal browser, the page runs a demo marked "DEMO · NOT CONNECTED".

Adapted from a J.A.R.V.I.S. UI brief. **Rule: show only real data.** Where Karen has no source yet (weather,
environment sensors, health), the slot shows real system data instead of fake numbers.

---

## 1. Central core (the orb)
| Aspect | Spec |
|---|---|
| Size | Radius `R = min((H-190)/3.5, W/4.4)`, so the orb plus rings fill the middle of the window |
| Material | Glass sphere: off-centre radial gradient body, brighter rim, top-left specular ellipse, faint lower-right reflection, and a white "metallic gleam" arc sliding round the rim |
| Inside | 5 drifting nebula blobs (additive blending), 34 neon filaments curling from the rim toward the core, and a bright energy core |
| Palette | Deep "dark shady" purple: near-black violet background (#05020a → #1a0a30), electric violet glow (#9d4eff) |
| Idle | Dim deep purple (hue 270). Breathes every 3 s when sleeping, every 1.5 s while speaking |
| Active | Brightens to violet. Thinking cycles violet↔magenta; heavy work (`intensity` > 0.6) blends toward amber/yellow |
| Command start ("puff") | Orb scales up 9% and eases back over ~0.7 s; core flares; two ripple rings expand outward |
| Colour changes | Hue, saturation, lightness and glow ease toward the new state's values (no jumps) |

## 2. Holographic data rings
| Ring | Radius | Speed | Shows |
|---|---|---|---|
| Inner | 1.24 R | slow | System vitals as arcs: CPU, memory, power. Purple when fine, amber/red past thresholds |
| Middle | 1.46 R | medium, reverse | Live audio: mic level while listening, voice level while speaking, a scanning sweep while listening, "MIC MUTED" label |
| Outer | 1.70 R | fast | Link (which LLM backend answered) and alerts as pulsing amber/red markers with labels |

- **Speed:** all rings rotate at `speed × state × (1 + 1.5 × intensity)`.
- **Colour:** rings shift toward orange during complex tasks.
- **Data flow:** energy pulses travel from the orb to the outer ring. The rate depends on state: idle 0.6/s,
  thinking 22/s, listening/speaking scale with audio level.
- **Waveform:** while listening or speaking, 120 radial bars around the rim follow the audio level.

## 3. Floating glass panels
| Panel | Contents |
|---|---|
| System | CPU / memory / battery / disk bars and a 60 s CPU sparkline |
| Brain | Model, backend, first-word time, memory turns, and bars for the last 20 answers (amber = fallback used) |
| Voice pipeline | Wake engine, speech-to-text time, time until voice starts, voice name, live mic waveform |
| Conversation | Transcript of you / Karen, with timings under each line |
| Abilities | Connected abilities (lit) and planned ones (dim, labelled by phase); lists MCP servers from Phase 2 on |

- **Look:** frosted glass (`backdrop-filter: blur`), thin violet edge, corner brackets, and a slight 3D tilt toward
  the orb.
- **Motion:** each panel bobs 6 px on a 7 s cycle, offset from the others.
- **Hover:** the panel brightens and extra detail text slides open.
- **Alerts:** amber pulsing border = warning (e.g. battery < 20%, CPU > 92%); red pulsing border = critical
  (battery < 10%).
- **Type:** Orbitron for labels and headings, Rajdhani for text. Falls back to Segoe UI offline.

## 4. Voice and sound
| Event | Feedback |
|---|---|
| Wake word heard | Soft rising blip (620→940 Hz) and a small puff |
| Speaking | Harmonic hum (110/165/220 Hz, very quiet) swelling with a 1.5 s pulse, in step with the orb's breathing |
| Answer finished | Chime (988 Hz + 1480 Hz) and a gentle ripple |
| Error | Low buzz (82/87 Hz sawtooth, low-pass filtered), red flash and a ripple |
| Confirm needed | Orb and panels turn amber; a dialog shows Karen's question with YES / NO (spoken yes/no also works) |

- **Generated, not recorded:** all sounds are made with WebAudio, so there are no audio files. They can be turned
  off in Settings.
- **The spoken voice is Kokoro.** The brief's "calm, British" voice is available as Emma/Isabella/Alice in
  Settings; the default stays `af_heart`.

## 5. Real-time data
- **Live charts:** CPU sparkline (1 Hz), LLM latency bars, mic waveform (~16 Hz).
- **Colour coding:** purple = healthy, amber = warning, red = critical, everywhere (ring arcs, bars, panel borders,
  HUD dots).
- **Not shown yet (no data source):**
  - Weather radar on the rings: a later weather MCP server.
  - Home environment and appliance status: Phase 4 SmartThings.
  - These plug into the outer ring and the Abilities panel when they exist.

## Controls
| Control | Action |
|---|---|
| Mic button / Space | Talk now, without saying "Karen" |
| Text box / `/` to focus, Enter to send | Typed command, same pipeline as voice |
| Stop / Esc | Cut Karen off mid-sentence (`Speaker.stop_event`) |
| Moon | Mute the microphone (Karen stops listening until unmuted) |
| Settings | Voice, speaking speed, primary brain, wake sensitivity (custom wake word only), interface sounds, forget conversation, open log |

## States
`sleeping` (standby) → `listening` → `thinking` → `speaking` → back to `sleeping`, plus `confirm` and `error`.
The `<body>` class carries the state for CSS accents. The canvas reads `S.mode` every frame.
