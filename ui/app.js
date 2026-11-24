/* Karen interface. Python pushes updates by calling window.karen.<method>(...);
   the page calls Python through window.pywebview.api.<method>(...).
   Opened in a normal browser (no pywebview) it runs a clearly labelled demo. */
"use strict";

const $ = (id) => document.getElementById(id);
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const lerp = (a, b, t) => a + (b - a) * t;

/* ------------------------------------------------------------------ state */
const S = {
  mode: "sleeping",    // sleeping | listening | thinking | speaking | confirm | error
  mic: 0, voice: 0,    // live levels 0..1
  intensity: 0,        // 0..1, how hard Karen is working (drives amber, ring speed)
  puff: 0,             // 1 when a command starts, decays
  muted: false,
  stats: { cpu: 0, ram: 0, bat: null, plugged: true, disk: 0 },
  alerts: [],          // [{level: "warn"|"error", text}]
  backend: "",
  cpuHist: [], llmHist: [], micHist: [],
};

const MODES = {
  sleeping:  { h: 270, s: 72, l: 44, glow: 0.5, rot: 0.5, period: 3.0, label: "STANDBY" },
  listening: { h: 276, s: 100, l: 64, glow: 1.0, rot: 1.2, period: 1.2, label: "LISTENING" },
  thinking:  { h: 290, s: 95, l: 62, glow: 1.25, rot: 3.2, period: 0.8, label: "THINKING" },
  speaking:  { h: 282, s: 95, l: 60, glow: 0.9, rot: 1.6, period: 1.5, label: "SPEAKING" },
  confirm:   { h: 40, s: 100, l: 58, glow: 1.1, rot: 0.8, period: 1.0, label: "AWAITING CONFIRMATION" },
  error:     { h: 355, s: 95, l: 58, glow: 1.1, rot: 0.6, period: 0.9, label: "ERROR" },
};
const cur = { h: 270, s: 72, l: 44, glow: 0.5, rot: 0.5, period: 3.0 };

/* ------------------------------------------------------------------ sound */
const Sound = {
  ctx: null, on: false, hum: null,  // off until Karen's config says so: the browser demo looped beeps unseen in a hidden tab
  init() {
    try {
      if (!this.ctx) this.ctx = new (window.AudioContext || window.webkitAudioContext)();
      if (this.ctx.state === "suspended") this.ctx.resume();
    } catch (e) { this.ctx = null; }
  },
  tone(freq, dur, { type = "sine", gain = 0.05, delay = 0, to = null, lowpass = null } = {}) {
    if (!this.on || !this.ctx) return;
    const c = this.ctx, t0 = c.currentTime + delay;
    const o = c.createOscillator(), g = c.createGain();
    o.type = type; o.frequency.setValueAtTime(freq, t0);
    if (to) o.frequency.exponentialRampToValueAtTime(to, t0 + dur * 0.7);
    g.gain.setValueAtTime(0, t0);
    g.gain.linearRampToValueAtTime(gain, t0 + 0.015);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    let node = o;
    if (lowpass) { const f = c.createBiquadFilter(); f.type = "lowpass"; f.frequency.value = lowpass; o.connect(f); node = f; }
    node.connect(g).connect(c.destination);
    o.start(t0); o.stop(t0 + dur + 0.05);
  },
  wake() { this.tone(620, 0.25, { to: 940, gain: 0.04 }); },
  chime() { this.tone(988, 0.9, { gain: 0.035 }); this.tone(1480, 1.1, { gain: 0.025, delay: 0.09 }); },
  buzz() { this.tone(82, 0.5, { type: "sawtooth", gain: 0.06, lowpass: 380 }); this.tone(87, 0.5, { type: "sawtooth", gain: 0.04, lowpass: 380 }); },
  humStart() {
    if (!this.on || !this.ctx || this.hum) return;
    const c = this.ctx, g = c.createGain(), lfo = c.createOscillator(), lfoGain = c.createGain();
    g.gain.value = 0.0; g.gain.linearRampToValueAtTime(0.012, c.currentTime + 0.6);
    lfo.frequency.value = 1 / 1.5; lfoGain.gain.value = 0.006; lfo.connect(lfoGain).connect(g.gain);
    const oscs = [110, 165, 220].map((f, i) => {
      const o = c.createOscillator(); o.frequency.value = f;
      const og = c.createGain(); og.gain.value = [1, 0.5, 0.25][i]; o.connect(og).connect(g); o.start(); return o;
    });
    g.connect(c.destination); lfo.start();
    this.hum = { g, oscs, lfo };
  },
  humStop() {
    if (!this.hum) return;
    const { g, oscs, lfo } = this.hum, c = this.ctx;
    g.gain.cancelScheduledValues(c.currentTime);
    g.gain.setValueAtTime(g.gain.value, c.currentTime);
    g.gain.linearRampToValueAtTime(0, c.currentTime + 0.5);
    setTimeout(() => { oscs.forEach((o) => o.stop()); lfo.stop(); }, 600);
    this.hum = null;
  },
};
Sound.init();
addEventListener("pointerdown", () => Sound.init());
addEventListener("keydown", () => Sound.init());

/* ------------------------------------------------------------------ orb + rings (canvas) */
const cv = $("core"), ctx = cv.getContext("2d");
let W = 0, H = 0, cx = 0, cy = 0, R = 100;
function resize() {
  const dpr = Math.min(devicePixelRatio || 1, 2);
  W = innerWidth; H = innerHeight;
  cv.width = W * dpr; cv.height = H * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  cx = W / 2; cy = H / 2 + 6;
  R = Math.max(60, Math.min((H - 190) / 3.5, W / 4.4));
}
addEventListener("resize", resize);
resize();

const hsl = (h, s, l, a = 1) => `hsla(${h},${s}%,${l}%,${a})`;
const ripples = [], pulses = [];
const blobs = Array.from({ length: 5 }, (_, i) => ({ a: i * 1.3, r: 0.25 + i * 0.08, sp: 0.15 + i * 0.07, size: 0.55 - i * 0.05, dh: [0, 30, -25, 45, -40][i] }));
const rings = [
  { f: 1.24, speed: 0.06, angle: 0, dash: [], name: "inner" },
  { f: 1.46, speed: -0.17, angle: 1, dash: [10, 6], name: "middle" },
  { f: 1.70, speed: 0.42, angle: 2, dash: [2, 9], name: "outer" },
];
let last = performance.now(), T = 0;

function lerpHue(a, b, t) { let d = ((b - a + 540) % 360) - 180; return (a + d * t + 360) % 360; }

function step(now) {
  const dt = Math.min(0.05, (now - last) / 1000); last = now; T += dt;
  const m = MODES[S.mode] || MODES.sleeping;
  let th = m.h;
  if (S.mode === "thinking") th = 262 + 52 * (0.5 + 0.5 * Math.sin(T * 1.7));      // violet <-> magenta
  if (S.intensity > 0.6 && S.mode !== "error") th = lerpHue(th, 42, (S.intensity - 0.6) / 0.4); // heavy work -> amber
  const k = 1 - Math.pow(0.02, dt);
  cur.h = lerpHue(cur.h, th, k); cur.s = lerp(cur.s, m.s, k); cur.l = lerp(cur.l, m.l, k);
  cur.glow = lerp(cur.glow, m.glow + (S.mode === "speaking" ? S.voice * 0.6 : 0), k);
  cur.rot = lerp(cur.rot, m.rot * (1 + S.intensity * 1.5), k); cur.period = lerp(cur.period, m.period, k);
  S.puff = Math.max(0, S.puff - dt * 1.4);
  S.intensity = Math.max(0, S.intensity - dt * 0.05);
  rings.forEach((r) => (r.angle += r.speed * cur.rot * dt));

  // energy pulses travelling from the core to the edge
  const rate = { sleeping: 0.6, listening: 2 + S.mic * 30, thinking: 22, speaking: 4 + S.voice * 30, confirm: 3, error: 2 }[S.mode] || 1;
  if (Math.random() < rate * dt) pulses.push({ a: Math.random() * Math.PI * 2, r: R * 1.02, v: R * (0.5 + Math.random()), life: 1 });
  for (let i = pulses.length - 1; i >= 0; i--) {
    const p = pulses[i]; p.r += p.v * dt; p.life = 1 - (p.r - R) / (R * 0.8);
    if (p.life <= 0) pulses.splice(i, 1);
  }
  for (let i = ripples.length - 1; i >= 0; i--) {
    const rp = ripples[i]; rp.r += R * 1.3 * dt; rp.a -= dt * 0.7;
    if (rp.a <= 0) ripples.splice(i, 1);
  }
  if (!document.body.classList.contains("avatar-mode")) draw();  // the anime avatar replaces the orb
  requestAnimationFrame(step);
}

function draw() {
  ctx.clearRect(0, 0, W, H);
  const { h, s, l, glow } = cur;
  const breath = 0.5 + 0.5 * Math.sin((T / cur.period) * Math.PI * 2);
  const scale = 1 + 0.018 * breath + 0.09 * easeOut(S.puff) + (S.mode === "speaking" ? S.voice * 0.03 : 0);
  const r = R * scale;

  // ambient glow behind the orb
  let g = ctx.createRadialGradient(cx, cy, r * 0.4, cx, cy, r * 2.8);
  g.addColorStop(0, hsl(h, s, l, 0.16 * glow + 0.05 * breath));
  g.addColorStop(1, hsl(h, s, l, 0));
  ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);

  ripples.forEach((rp) => {
    ctx.beginPath(); ctx.arc(cx, cy, rp.r, 0, Math.PI * 2);
    ctx.strokeStyle = hsl(h, s, l + 15, rp.a); ctx.lineWidth = 2; ctx.stroke();
  });

  drawRings(h, s, l, glow);

  // energy pulses
  ctx.globalCompositeOperation = "lighter";
  pulses.forEach((p) => {
    const x = cx + Math.cos(p.a) * p.r, y = cy + Math.sin(p.a) * p.r;
    const x2 = cx + Math.cos(p.a) * (p.r - 14), y2 = cy + Math.sin(p.a) * (p.r - 14);
    ctx.strokeStyle = hsl(h, s, l + 20, 0.6 * p.life); ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.moveTo(x2, y2); ctx.lineTo(x, y); ctx.stroke();
    ctx.fillStyle = hsl(h, s, 80, 0.9 * p.life);
    ctx.beginPath(); ctx.arc(x, y, 1.8, 0, Math.PI * 2); ctx.fill();
  });
  ctx.globalCompositeOperation = "source-over";

  drawWaveRing(r, h, s, l);
  drawOrb(r, h, s, l, glow, breath);
}

function easeOut(x) { return 1 - Math.pow(1 - x, 3); }

function drawOrb(r, h, s, l, glow, breath) {
  // outer halo
  ctx.save();
  ctx.shadowColor = hsl(h, s, l, 0.9); ctx.shadowBlur = 40 * glow;
  ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2);
  ctx.fillStyle = hsl(h, s, 8, 0.85); ctx.fill();
  ctx.restore();

  ctx.save();
  ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.clip();

  // glass body
  let g = ctx.createRadialGradient(cx - r * 0.3, cy - r * 0.35, r * 0.05, cx, cy, r);
  g.addColorStop(0, hsl(h, s, l + 22, 0.45));
  g.addColorStop(0.45, hsl(h, s, l - 10, 0.22));
  g.addColorStop(0.85, hsl(h, s, l - 28, 0.18));
  g.addColorStop(1, hsl(h, s, l + 10, 0.5));
  ctx.fillStyle = g; ctx.fillRect(cx - r, cy - r, r * 2, r * 2);

  // drifting nebula inside the crystal
  ctx.globalCompositeOperation = "lighter";
  blobs.forEach((b) => {
    const a = b.a + T * b.sp * (0.6 + cur.rot * 0.25);
    const bx = cx + Math.cos(a) * r * b.r, by = cy + Math.sin(a * 1.3) * r * b.r;
    const br = r * b.size * (0.85 + 0.15 * breath);
    const bg = ctx.createRadialGradient(bx, by, 0, bx, by, br);
    bg.addColorStop(0, hsl((h + b.dh + 360) % 360, s, l, 0.16 * glow));
    bg.addColorStop(1, hsl(h, s, l, 0));
    ctx.fillStyle = bg; ctx.fillRect(bx - br, by - br, br * 2, br * 2);
  });

  // neon filaments curling from the rim toward the core
  const n = 34;
  for (let i = 0; i < n; i++) {
    const a = (i / n) * Math.PI * 2 + T * 0.05;
    const wob = Math.sin(T * 1.3 + i * 1.7);
    const x0 = cx + Math.cos(a) * r, y0 = cy + Math.sin(a) * r;
    const x1 = cx + Math.cos(a + 0.5 * wob) * r * 0.22, y1 = cy + Math.sin(a + 0.5 * wob) * r * 0.22;
    const qx = cx + Math.cos(a + 0.35 + 0.25 * wob) * r * 0.7, qy = cy + Math.sin(a + 0.35 + 0.25 * wob) * r * 0.7;
    ctx.strokeStyle = hsl(h, s, l + 15, (0.06 + 0.08 * glow) * (0.6 + 0.4 * Math.sin(T * 2 + i)));
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x0, y0); ctx.quadraticCurveTo(qx, qy, x1, y1); ctx.stroke();
  }

  // energy core
  const coreR = r * (0.16 + 0.06 * breath + 0.12 * S.puff + (S.mode === "speaking" ? 0.1 * S.voice : 0));
  g = ctx.createRadialGradient(cx, cy, 0, cx, cy, coreR * 2.2);
  g.addColorStop(0, hsl(h, 60, 92, 0.85 * glow));
  g.addColorStop(0.35, hsl(h, s, l + 10, 0.35 * glow));
  g.addColorStop(1, hsl(h, s, l, 0));
  ctx.fillStyle = g; ctx.fillRect(cx - coreR * 2.2, cy - coreR * 2.2, coreR * 4.4, coreR * 4.4);
  ctx.globalCompositeOperation = "source-over";

  // specular highlights (glass)
  g = ctx.createRadialGradient(cx - r * 0.38, cy - r * 0.45, 0, cx - r * 0.38, cy - r * 0.45, r * 0.42);
  g.addColorStop(0, "rgba(255,255,255,0.32)"); g.addColorStop(1, "rgba(255,255,255,0)");
  ctx.fillStyle = g; ctx.beginPath(); ctx.ellipse(cx - r * 0.38, cy - r * 0.45, r * 0.42, r * 0.26, -0.6, 0, Math.PI * 2); ctx.fill();
  g = ctx.createRadialGradient(cx + r * 0.45, cy + r * 0.5, 0, cx + r * 0.45, cy + r * 0.5, r * 0.3);
  g.addColorStop(0, "rgba(255,255,255,0.08)"); g.addColorStop(1, "rgba(255,255,255,0)");
  ctx.fillStyle = g; ctx.fillRect(cx, cy, r, r);
  ctx.restore();

  // rim + travelling metallic gleam
  ctx.save();
  ctx.shadowColor = hsl(h, s, l, 1); ctx.shadowBlur = 18 * glow;
  ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2);
  ctx.strokeStyle = hsl(h, s, l + 18, 0.75); ctx.lineWidth = 1.6; ctx.stroke();
  ctx.restore();
  const ga = T * 0.35;
  ctx.beginPath(); ctx.arc(cx, cy, r - 1, ga, ga + 0.7);
  ctx.strokeStyle = "rgba(255,255,255,0.55)"; ctx.lineWidth = 2; ctx.stroke();
  ctx.beginPath(); ctx.arc(cx, cy, r - 1, ga + Math.PI, ga + Math.PI + 0.25);
  ctx.strokeStyle = "rgba(255,255,255,0.25)"; ctx.lineWidth = 1.5; ctx.stroke();
}

function sevColor(level, h, s, l, a) {
  if (level === "bad") return `rgba(255,77,94,${a})`;
  if (level === "warn") return `rgba(255,181,71,${a})`;
  return hsl(h, s, l + 10, a);
}

function arcLabel(text, angle, radius, color, size = 10) {
  ctx.save();
  ctx.translate(cx + Math.cos(angle) * radius, cy + Math.sin(angle) * radius);
  let rot = angle + Math.PI / 2;
  if (Math.sin(angle) > 0.2) rot += Math.PI; // keep text upright on the lower half
  ctx.rotate(rot);
  ctx.font = `600 ${size}px Orbitron, Segoe UI, sans-serif`;
  ctx.fillStyle = color; ctx.textAlign = "center"; ctx.textBaseline = "middle";
  ctx.fillText(text, 0, 0);
  ctx.restore();
}

function drawRings(h, s, l, glow) {
  const st = S.stats;
  const ringHue = S.intensity > 0.5 ? lerpHue(h, 35, (S.intensity - 0.5) * 2) : h;
  rings.forEach((ring, idx) => {
    const rr = R * ring.f, A = ring.angle;
    // track
    ctx.beginPath(); ctx.arc(cx, cy, rr, 0, Math.PI * 2);
    ctx.setLineDash(ring.dash); ctx.lineWidth = 1;
    ctx.strokeStyle = hsl(ringHue, s, l, 0.14 + 0.1 * glow); ctx.stroke(); ctx.setLineDash([]);
    // ticks
    const ticks = idx === 0 ? 120 : idx === 1 ? 72 : 48;
    for (let i = 0; i < ticks; i++) {
      const a = A + (i / ticks) * Math.PI * 2, major = i % (ticks / 12) === 0;
      const len = major ? 8 : 3, r0 = rr + (idx === 2 ? 3 : -3);
      ctx.beginPath();
      ctx.moveTo(cx + Math.cos(a) * r0, cy + Math.sin(a) * r0);
      ctx.lineTo(cx + Math.cos(a) * (r0 + (idx === 2 ? len : -len)), cy + Math.sin(a) * (r0 + (idx === 2 ? len : -len)));
      ctx.strokeStyle = hsl(ringHue, s, l + 10, major ? 0.45 : 0.18); ctx.lineWidth = 1; ctx.stroke();
    }
  });

  // inner ring: system vitals as arcs
  const inner = rings[0], ri = R * inner.f;
  const vitals = [
    ["CPU", st.cpu / 100, st.cpu > 90 ? "bad" : st.cpu > 75 ? "warn" : ""],
    ["MEM", st.ram / 100, st.ram > 92 ? "bad" : st.ram > 80 ? "warn" : ""],
    ["PWR", st.bat == null ? 1 : st.bat / 100, st.bat != null && !st.plugged && st.bat < 10 ? "bad" : st.bat != null && !st.plugged && st.bat < 20 ? "warn" : ""],
  ];
  vitals.forEach(([name, v, sev], i) => {
    const a0 = inner.angle + i * (Math.PI * 2 / 3) + 0.12, span = Math.PI * 2 / 3 - 0.5;
    ctx.lineCap = "round";
    ctx.beginPath(); ctx.arc(cx, cy, ri + 7, a0, a0 + span);
    ctx.strokeStyle = hsl(h, s, l, 0.08); ctx.lineWidth = 4; ctx.stroke();
    ctx.beginPath(); ctx.arc(cx, cy, ri + 7, a0, a0 + span * clamp(v, 0.01, 1));
    ctx.strokeStyle = sevColor(sev, h, s, l, 0.85); ctx.lineWidth = 4; ctx.stroke();
    ctx.lineCap = "butt";
    const val = name === "PWR" && st.bat == null ? "AC" : Math.round(v * 100) + "%";
    arcLabel(`${name} ${val}`, a0 + span + 0.2, ri + 7, sevColor(sev, h, s, l, 0.8), 9);
  });

  // middle ring: audio activity + scanning sweep while listening
  const mid = rings[1], rm = R * mid.f;
  const lvl = S.mode === "speaking" ? S.voice : S.mic;
  ctx.beginPath(); ctx.arc(cx, cy, rm, mid.angle, mid.angle + 0.2 + lvl * Math.PI * 1.2);
  ctx.strokeStyle = hsl(h, s, l + 15, 0.7); ctx.lineWidth = 3; ctx.stroke();
  arcLabel(S.muted ? "MIC MUTED" : S.mode === "speaking" ? "VOICE OUT" : "AUDIO IN", mid.angle - 0.18, rm, hsl(h, s, l + 15, 0.75), 9);
  if (S.mode === "listening") {
    const a = T * 3;
    const g = ctx.createConicGradient ? ctx.createConicGradient(a - 0.6, cx, cy) : null;
    if (g) {
      g.addColorStop(0, hsl(h, s, l, 0)); g.addColorStop(0.09, hsl(h, s, l + 10, 0.22)); g.addColorStop(0.1, hsl(h, s, l, 0));
      ctx.fillStyle = g; ctx.beginPath(); ctx.arc(cx, cy, rm, 0, Math.PI * 2); ctx.arc(cx, cy, R * 1.05, 0, Math.PI * 2, true); ctx.fill();
    }
  }

  // outer ring: link status and alerts
  const outer = rings[2], ro = R * outer.f;
  arcLabel(S.backend ? `LINK ${S.backend.toUpperCase()}` : "LINK --", outer.angle, ro + 12, hsl(h, s, l + 10, 0.6), 9);
  S.alerts.forEach((al, i) => {
    const a = outer.angle * -0.4 + i * 0.5 - Math.PI / 2;
    const col = al.level === "error" ? "255,77,94" : "255,181,71";
    const pr = 4 + 2 * Math.sin(T * 6);
    ctx.beginPath(); ctx.arc(cx + Math.cos(a) * ro, cy + Math.sin(a) * ro, pr, 0, Math.PI * 2);
    ctx.fillStyle = `rgba(${col},0.9)`; ctx.shadowColor = `rgba(${col},1)`; ctx.shadowBlur = 12; ctx.fill(); ctx.shadowBlur = 0;
    arcLabel(al.text.toUpperCase().slice(0, 28), a, ro + 18, `rgba(${col},0.95)`, 9);
  });
}

function drawWaveRing(r, h, s, l) {
  // waveform bars around the rim: mic while listening, voice while speaking
  const lvl = S.mode === "listening" ? S.mic : S.mode === "speaking" ? S.voice : 0;
  if (lvl < 0.01) return;
  const n = 120;
  ctx.globalCompositeOperation = "lighter";
  for (let i = 0; i < n; i++) {
    const a = (i / n) * Math.PI * 2;
    const noise = 0.45 + 0.55 * Math.abs(Math.sin(i * 0.9 + T * 9) * Math.cos(i * 0.37 - T * 5));
    const len = r * 0.22 * lvl * noise;
    const r0 = r * 1.03;
    ctx.beginPath();
    ctx.moveTo(cx + Math.cos(a) * r0, cy + Math.sin(a) * r0);
    ctx.lineTo(cx + Math.cos(a) * (r0 + len), cy + Math.sin(a) * (r0 + len));
    ctx.strokeStyle = hsl(h, s, l + 15, 0.55); ctx.lineWidth = 2; ctx.stroke();
  }
  ctx.globalCompositeOperation = "source-over";
}

requestAnimationFrame(step);

/* ------------------------------------------------------------------ small charts */
function spark(canvas, data, { max = 100, bars = false, colors = null } = {}) {
  const dpr = Math.min(devicePixelRatio || 1, 2), w = canvas.clientWidth, h = canvas.clientHeight;
  if (canvas.width !== w * dpr) { canvas.width = w * dpr; canvas.height = h * dpr; }
  const c = canvas.getContext("2d"); c.setTransform(dpr, 0, 0, dpr, 0, 0); c.clearRect(0, 0, w, h);
  if (!data.length) return;
  const m = Math.max(max, ...data);
  if (bars) {
    const bw = w / 20;
    data.forEach((v, i) => {
      const bh = Math.max(2, (v / m) * (h - 4));
      c.fillStyle = colors && colors[i] ? colors[i] : "rgba(157,78,255,.7)";
      c.fillRect(i * bw + 1, h - bh, bw - 3, bh);
    });
    return;
  }
  const step = w / Math.max(1, data.length - 1);
  c.beginPath();
  data.forEach((v, i) => { const y = h - 2 - (v / m) * (h - 6); i ? c.lineTo(i * step, y) : c.moveTo(0, y); });
  c.strokeStyle = "rgba(157,78,255,.9)"; c.lineWidth = 1.5; c.shadowColor = "#9d4eff"; c.shadowBlur = 6; c.stroke(); c.shadowBlur = 0;
  c.lineTo(w, h); c.lineTo(0, h); c.closePath();
  const g = c.createLinearGradient(0, 0, 0, h); g.addColorStop(0, "rgba(157,78,255,.25)"); g.addColorStop(1, "rgba(157,78,255,0)");
  c.fillStyle = g; c.fill();
}

function drawWave() {
  const canvas = $("wave"), dpr = Math.min(devicePixelRatio || 1, 2), w = canvas.clientWidth, h = canvas.clientHeight;
  if (!w) return;
  if (canvas.width !== w * dpr) { canvas.width = w * dpr; canvas.height = h * dpr; }
  const c = canvas.getContext("2d"); c.setTransform(dpr, 0, 0, dpr, 0, 0); c.clearRect(0, 0, w, h);
  const d = S.micHist, n = d.length, bw = w / 64;
  for (let i = 0; i < n; i++) {
    const v = d[i], bh = Math.max(1, v * (h - 4));
    c.fillStyle = `rgba(157,78,255,${0.25 + v * 0.75})`;
    c.fillRect(w - (n - i) * bw, (h - bh) / 2, bw - 1, bh);
  }
}
setInterval(drawWave, 60);

/* ------------------------------------------------------------------ panels */
function setBar(id, valueId, v, text, sev) {
  const bar = $(id); bar.style.width = clamp(v, 0, 100) + "%"; bar.className = sev || "";
  $(valueId).textContent = text;
}

const PANEL_STATES = ["sleeping", "listening", "thinking", "speaking", "confirm", "error"];

function addMessage(role, text, meta) {
  const box = $("messages"), el = document.createElement("div");
  el.className = "msg " + role;
  const who = { you: "YOU", karen: "KAREN", error: "ERROR", system: "SYSTEM" }[role] || role.toUpperCase();
  el.innerHTML = `<span class="who"></span><span class="text"></span>${meta ? '<span class="meta"></span>' : ""}`;
  el.querySelector(".who").textContent = who;
  el.querySelector(".text").textContent = text;
  if (meta) el.querySelector(".meta").textContent = meta;
  box.appendChild(el);
  while (box.children.length > 60) box.firstChild.remove();
  box.scrollTop = box.scrollHeight;
  $("msg-count").textContent = box.querySelectorAll(".msg.you").length + " ASKED";
  return el;
}

function toast(level, text) {
  const el = document.createElement("div");
  el.className = "toast " + (level || "");
  el.textContent = text;
  $("toasts").appendChild(el);
  setTimeout(() => el.remove(), 5200);
}

/* ------------------------------------------------------------------ API for Python */
window.karen = {
  state(mode, detail) {
    const prev = S.mode;
    if (!MODES[mode]) return;
    S.mode = mode;
    document.body.classList.remove(...PANEL_STATES);
    document.body.classList.add(mode);
    $("state-label").textContent = detail ? `${MODES[mode].label} · ${detail}` : MODES[mode].label;
    $("btn-talk").classList.toggle("live", mode === "listening");
    if (mode !== "confirm") window.karen.confirmClose();
    if (mode === "listening" && prev === "sleeping") { Sound.wake(); S.puff = 0.5; }
    if (mode === "thinking") { S.puff = 1; S.intensity = Math.max(S.intensity, 0.35); ripples.push({ r: R, a: 0.7 }, { r: R * 0.9, a: 0.5 }); }
    if (mode === "speaking") Sound.humStart(); else Sound.humStop();
    if (mode === "error") { Sound.buzz(); ripples.push({ r: R, a: 0.8 }); }
  },
  done() { Sound.chime(); ripples.push({ r: R, a: 0.4 }); },
  level(mic, voice) {
    S.mic = clamp(mic || 0, 0, 1); S.voice = clamp(voice || 0, 0, 1);
    S.micHist.push(S.mode === "speaking" ? S.voice : S.mic); if (S.micHist.length > 64) S.micHist.shift();
  },
  intensity(v) { S.intensity = clamp(v, 0, 1); },
  stats(st) {
    Object.assign(S.stats, st);
    setBar("bar-cpu", "v-cpu", st.cpu, Math.round(st.cpu) + "%", st.cpu > 90 ? "bad" : st.cpu > 75 ? "warn" : "");
    setBar("bar-ram", "v-ram", st.ram, Math.round(st.ram) + "%", st.ram > 92 ? "bad" : st.ram > 80 ? "warn" : "");
    if (st.bat == null) setBar("bar-bat", "v-bat", 100, "AC", "");
    else setBar("bar-bat", "v-bat", st.bat, Math.round(st.bat) + "%" + (st.plugged ? "+" : ""),
                !st.plugged && st.bat < 10 ? "bad" : !st.plugged && st.bat < 20 ? "warn" : "");
    setBar("bar-disk", "v-disk", st.disk, Math.round(st.disk) + "%", st.disk > 95 ? "bad" : st.disk > 90 ? "warn" : "");
    if (st.uptime) $("sys-uptime").textContent = "UP " + st.uptime;
    S.cpuHist.push(st.cpu); if (S.cpuHist.length > 60) S.cpuHist.shift();
    spark($("spark-cpu"), S.cpuHist);
    const low = st.bat != null && !st.plugged && st.bat < 20;
    $("p-system").className = "panel" + (low && st.bat < 10 ? " alert" : low || st.cpu > 92 ? " warn" : "");
    S.alerts = S.alerts.filter((a) => a.src !== "battery");
    if (low) S.alerts.push({ src: "battery", level: st.bat < 10 ? "error" : "warn", text: `Battery ${Math.round(st.bat)}%` });
  },
  timing(t) {
    if (t.stt != null) $("t-stt").textContent = t.stt.toFixed(1) + " s";
    if (t.voice != null) $("t-voice").textContent = t.voice.toFixed(1) + " s";
    if (t.llm != null) {
      $("brain-latency").textContent = t.llm.toFixed(1) + " s";
      S.llmHist.push({ v: t.llm, fb: !!t.fallback }); if (S.llmHist.length > 20) S.llmHist.shift();
      spark($("spark-llm"), S.llmHist.map((x) => x.v), { max: 3, bars: true, colors: S.llmHist.map((x) => (x.fb ? "rgba(255,181,71,.85)" : null)) });
    }
    if (t.backend) {
      S.backend = t.backend; $("brain-backend").textContent = t.backend + (t.fallback ? " (fallback)" : "");
      $("hud-brain").textContent = t.backend.toUpperCase();
      $("dot-brain").className = "dot" + (t.fallback ? " warn" : "");
    }
  },
  message(role, text, meta) { addMessage(role, text, meta); if (role === "error") $("dot-brain").className = "dot bad"; },
  memory(turns) { $("brain-memory").textContent = turns + (turns === 1 ? " turn" : " turns"); },
  config(c) {
    if (c.voice) { $("set-voice").value = c.voice; $("voice-name").textContent = $("set-voice").selectedOptions[0]?.text.split(" ")[0] || c.voice; }
    if (c.speed) { $("set-speed").value = c.speed; $("lbl-speed").textContent = Number(c.speed).toFixed(2); }
    if (c.backends) {
      $("set-brain").innerHTML = "";
      c.backends.forEach((b) => { const o = document.createElement("option"); o.value = b.name; o.textContent = `${b.name} · ${b.model}`; $("set-brain").appendChild(o); });
      $("set-brain").value = c.backends[0].name;
      $("brain-model").textContent = c.backends[0].model; S.backend = S.backend || c.backends[0].name;
    }
    if (c.wake_engine) $("wake-engine").textContent = c.wake_engine.toUpperCase();
    if (c.wake_threshold != null) { $("set-wake").value = c.wake_threshold; $("lbl-wake").textContent = Number(c.wake_threshold).toFixed(2); }
    $("set-wake").disabled = !c.wake_engine || c.wake_engine === "whisper";
    if (c.sounds != null) { Sound.on = !!c.sounds; $("set-sounds").checked = Sound.on; if (!Sound.on) Sound.humStop(); }
    if (c.mode !== undefined) showMode(c.mode || "");
    if (c.face_ready !== undefined) {
      $("set-face").checked = !!c.face_lock; $("set-face").disabled = !c.face_ready;
      $("face-hint").textContent = c.face_ready
        ? 'She starts locked and answers once the webcam sees your face. Say "Karen, lock yourself" to lock.'
        : "First enroll your face: run python scripts/enroll_face.py";
    }
  },
  abilities(list) {
    const box = $("abilities"); box.innerHTML = "";
    list.forEach((a) => {
      const el = document.createElement("div");
      el.className = "ability " + (a.status || "");
      el.innerHTML = '<i class="led"></i><span></span><small></small>';
      el.querySelector("span").textContent = a.name; el.querySelector("small").textContent = a.note || "";
      box.appendChild(el);
    });
  },
  muted(m) {
    S.muted = !!m; $("btn-sleep").classList.toggle("active", S.muted);
    $("dot-mic").className = "dot" + (S.muted ? " off" : "");
    $("btn-sleep").title = S.muted ? "Unmute the microphone" : "Mute the microphone";
  },
  confirm(text) { $("confirm-text").textContent = text; $("confirm").classList.add("show"); },
  confirmClose() { $("confirm").classList.remove("show"); },
  alert(level, text) { if (level) S.alerts.push({ src: "py", level, text }); else S.alerts = S.alerts.filter((a) => a.src !== "py"); },
  toast,
  attachments(list) {
    const box = $("attachments"); box.innerHTML = "";
    list.forEach((f) => {
      const el = document.createElement("div");
      el.className = "chip";
      el.innerHTML = '<span></span><small></small><button title="Remove">×</button>';
      el.querySelector("span").textContent = f.name;
      el.querySelector("small").textContent = f.size > 1048576 ? (f.size / 1048576).toFixed(1) + " MB" : Math.ceil(f.size / 1024) + " KB";
      el.querySelector("button").onclick = () => api("remove_attachment", f.name);
      box.appendChild(el);
    });
  },
};

/* ------------------------------------------------------------------ controls -> Python */
let demo = null;
function api(name, ...args) {
  if (window.pywebview && window.pywebview.api && window.pywebview.api[name]) return window.pywebview.api[name](...args);
  if (demo) return demo.api(name, ...args);
}

$("btn-talk").onclick = () => api("talk");
$("btn-stop").onclick = () => api("stop");
$("btn-sleep").onclick = () => { window.karen.muted(!S.muted); api("set_muted", S.muted); };
$("btn-send").onclick = sendText;
$("cmd").addEventListener("keydown", (e) => { if (e.key === "Enter") sendText(); });
function sendText() {
  const v = $("cmd").value.trim(); if (!v) return;
  $("cmd").value = ""; api("send_text", v);
}
$("btn-settings").onclick = () => $("settings").classList.toggle("open");
$("btn-add").onclick = () => api("add_files");

/* drag and drop files onto the window: sent to Python as base64 */
let dragDepth = 0;
addEventListener("dragenter", (e) => { if (e.dataTransfer?.types?.includes("Files")) { dragDepth++; document.body.classList.add("dragging"); } });
addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; document.body.classList.remove("dragging"); } });
addEventListener("dragover", (e) => e.preventDefault());
addEventListener("drop", (e) => {
  e.preventDefault(); dragDepth = 0; document.body.classList.remove("dragging");
  [...(e.dataTransfer?.files || [])].forEach((file) => {
    if (file.size > 60 * 1048576) { toast("warn", `${file.name} is over 60 MB: use the + button instead`); return; }
    const reader = new FileReader();
    reader.onload = () => api("upload", file.name, reader.result);
    reader.readAsDataURL(file);
  });
});
$("close-settings").onclick = () => $("settings").classList.remove("open");
$("set-voice").onchange = (e) => { api("set_setting", "voice", e.target.value); window.karen.config({ voice: e.target.value }); };
$("set-speed").oninput = (e) => { $("lbl-speed").textContent = Number(e.target.value).toFixed(2); };
$("set-speed").onchange = (e) => api("set_setting", "speed", Number(e.target.value));
$("set-brain").onchange = (e) => api("set_setting", "brain", e.target.value);
$("set-wake").oninput = (e) => { $("lbl-wake").textContent = Number(e.target.value).toFixed(2); };
$("set-wake").onchange = (e) => api("set_setting", "wake_threshold", Number(e.target.value));
/* Settings > Mode buttons (work / both / love / normal); a mode changed by voice or Telegram arrives via config(). */
function showMode(mode) {
  window.karenMode = mode;
  document.querySelectorAll("#set-mode button").forEach((b) => b.classList.toggle("on", b.dataset.mode === mode));
  $("mode-hint").textContent = mode === "custom" ? "Custom personality (set by voice). Pick a mode to replace it."
    : 'Or say "Karen, work mode" / "both mode" / "love mode".';
}
document.querySelectorAll("#set-mode button").forEach((b) => {
  b.onclick = () => { showMode(b.dataset.mode); api("set_setting", "mode", b.dataset.mode); };
});
$("set-sounds").onchange = (e) => {
  Sound.on = e.target.checked; if (!Sound.on) Sound.humStop();
  api("set_setting", "sounds", Sound.on);
};
$("set-face").onchange = (e) => api("set_setting", "face_lock", e.target.checked);
$("btn-clear").onclick = () => api("clear_memory");
$("btn-log").onclick = () => api("open_log");
$("confirm-yes").onclick = () => { window.karen.confirmClose(); api("confirm_answer", true); };
$("confirm-no").onclick = () => { window.karen.confirmClose(); api("confirm_answer", false); };

addEventListener("keydown", (e) => {
  const a = document.activeElement;
  const typing = a && ((a.tagName === "INPUT" && a.type !== "range") || a.tagName === "TEXTAREA" || a.tagName === "SELECT" || a.isContentEditable);
  if (e.key === "Escape") { $("settings").classList.remove("open"); $("spaces").classList.remove("open"); api("stop"); }
  else if (e.code === "Space" && !typing) { e.preventDefault(); api("talk"); }
  else if (e.key === "/" && !typing) { e.preventDefault(); $("cmd").focus(); }
});

setInterval(() => {
  const d = new Date();
  $("clock").textContent = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}, 1000);

window.addEventListener("pywebviewready", () => {
  if (demo) { demo.stop(); demo = null; $("messages").innerHTML = ""; }
  api("ready");
});

/* ------------------------------------------------------------------ demo (browser preview only) */
setTimeout(() => {
  if (window.pywebview || /pywebview/i.test(navigator.userAgent)) return;
  document.body.classList.add("demo");
  demo = makeDemo();
}, 2000);

function makeDemo() {
  const k = window.karen, timers = [];
  const later = (ms, fn) => timers.push(setTimeout(fn, ms));
  let micTimer = null, statTimer = null, cycle = 0, busy = false;
  const st = { cpu: 18, ram: 54, bat: 76, plugged: false, disk: 61 };
  k.config({ voice: "af_heart", speed: 1, wake_engine: "whisper", wake_threshold: 0.5,
             backends: [{ name: "gemini", model: "gemini-3.5-flash-lite" }, { name: "openrouter", model: "deepseek-v4.1-flash" }] });
  k.abilities([
    { name: "Conversation", status: "on", note: "ready" },
    { name: "Gmail & Calendar", status: "planned", note: "phase 2" },
    { name: "PC control", status: "planned", note: "phase 3" },
    { name: "Smart home", status: "planned", note: "phase 4" },
  ]);
  k.muted(false); k.memory(0);
  k.message("system", "Demo preview: animations and layout only. Nothing here is real data.");
  statTimer = setInterval(() => {
    st.cpu = clamp(st.cpu + (Math.random() - 0.5) * 10 + (busy ? 3 : -2), 4, 65);
    st.ram = clamp(st.ram + (Math.random() - 0.5) * 2, 40, 80);
    k.stats({ ...st, uptime: "2h 14m" });
  }, 1000);
  const script = [
    ["What's the capital of Japan?", "Tokyo is the capital of Japan."],
    ["Any new emails from my college?", "Once Gmail is connected in phase two, I'll read those out for you."],
    ["Send the email to Arun saying I'll be late.", null],
    ["Tell me a short joke.", "Why don't skeletons fight each other? They don't have the guts."],
  ];
  function run(text) {
    if (busy) return; busy = true;
    const [q, a] = text ? [text, "This is the demo preview, so I'm not really connected yet."] : script[cycle++ % script.length];
    k.state("listening");
    micTimer = setInterval(() => k.level(Math.random() * 0.8 + 0.1, 0), 70);
    later(2200, () => {
      clearInterval(micTimer); k.level(0, 0); k.message("you", q, "speech-to-text 2.4s");
      k.state("thinking", "GEMINI");
      if (!a) {
        later(1200, () => {
          k.state("confirm"); k.confirm("Send the email to Arun saying you'll be late?");
          later(4500, () => { k.confirmClose(); k.message("karen", "Okay, I won't send it."); k.state("sleeping"); busy = false; });
        });
        return;
      }
      later(1100, () => {
        const llm = 0.6 + Math.random() * 0.5, fb = cycle % 4 === 0;
        k.timing({ stt: 2.4, llm, voice: 1.6 + Math.random(), backend: fb ? "openrouter" : "gemini", fallback: fb });
        k.state("speaking");
        micTimer = setInterval(() => k.level(0, Math.abs(Math.sin(Date.now() / 120)) * 0.7 + Math.random() * 0.2), 60);
        later(3800, () => {
          clearInterval(micTimer); k.level(0, 0); k.message("karen", a, `first word ${llm.toFixed(1)}s`);
          k.memory(Math.min(6, cycle)); k.state("sleeping"); k.done(); busy = false;
        });
      });
    });
  }
  later(1500, () => run());
  const loop = setInterval(() => run(), 14000);
  return {
    stop() { timers.forEach(clearTimeout); clearInterval(loop); clearInterval(statTimer); clearInterval(micTimer); document.body.classList.remove("demo"); },
    api(name, ...args) {
      if (name === "talk") run();
      else if (name === "send_text") run(args[0]);
      else if (name === "stop") { /* nothing to stop in demo */ }
      else if (name === "set_setting") toast("", `Demo: ${args[0]} → ${args[1]} (saved when connected)`);
      else if (name === "clear_memory") { k.memory(0); toast("", "Demo: conversation forgotten"); }
      else if (name === "open_log") toast("warn", "Demo: no log file in preview");
      else if (name === "confirm_answer") toast("", args[0] ? "Demo: confirmed" : "Demo: cancelled");
    },
  };
}
