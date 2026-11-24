/* Karen Hands: webcam hand tracking (MediaPipe HandLandmarker, both hands, in this page) driving
   1) a 3D viewer (GLB/GLTF/FBX/OBJ/STL/VRM) and 2) a mirrored phone (scrcpy window + the real mouse, via Python).
   Gestures come from simple finger rules, refined by the user's own training samples (k-nearest neighbours).
   Python side: hands.py (window.pywebview.api). Opened in a normal browser it runs without Python (no phone). */
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { FBXLoader } from "three/addons/loaders/FBXLoader.js";
import { OBJLoader } from "three/addons/loaders/OBJLoader.js";
import { MTLLoader } from "three/addons/loaders/MTLLoader.js";
import { STLLoader } from "three/addons/loaders/STLLoader.js";
import { VRMLoaderPlugin, VRMUtils } from "@pixiv/three-vrm";
import { FilesetResolver, HandLandmarker } from "./vendor/mediapipe/vision_bundle.mjs";

const $ = (id) => document.getElementById(id);
const S = {
  mode: "3d", control: false, profile: null, model: null, mixer: null, hands: [], lastSeen: 0,
  slots: {}, holdKind: "", holdStart: 0, holdLatch: "", two: null, prev: {}, toastTimer: 0,
  phone: { x: 0.5, y: 0.5, down: false, pinchAt: 0, scrollAcc: 0, backAt: 0, palmTrail: [] },
};
let api = null;

/* ------------------------------------------------------------------ 3D scene */
const canvas = $("stage");
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
renderer.setPixelRatio(1);  // not the 1.25 display scale: leaves the graphics chip to hand tracking (15 -> more fps)
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(40, 1, 0.01, 100);
camera.position.set(0, 0.35, 3.6);
const controls = new OrbitControls(camera, canvas);
controls.enableDamping = true;
controls.target.set(0, 0, 0);
controls.saveState();
scene.add(new THREE.HemisphereLight(0xe6dcff, 0x1a0a30, 0.9));
const key = new THREE.DirectionalLight(0xfff1e0, 1.7); key.position.set(2.5, 3, 2.5); scene.add(key);
const fill = new THREE.DirectionalLight(0xbfd4ff, 0.55); fill.position.set(-3, 1, 1.5); scene.add(fill);
const rim = new THREE.DirectionalLight(0x9d4eff, 1.4); rim.position.set(0, 2, -3); scene.add(rim);
const grid = new THREE.GridHelper(8, 32, 0x9d4eff, 0x2a1450);
grid.position.y = -0.95; grid.material.transparent = true; grid.material.opacity = 0.35; scene.add(grid);
const holder = new THREE.Group(); scene.add(holder);
const clock = new THREE.Clock();

function resize() {
  const w = window.innerWidth, h = window.innerHeight;
  renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix();
}
window.addEventListener("resize", resize); resize();
(function render() {
  requestAnimationFrame(render);
  const dt = clock.getDelta();
  if (S.mixer) S.mixer.update(dt);
  if (S.vrm) S.vrm.update(dt);
  controls.update();
  renderer.render(scene, camera);
})();

function extOf(name) { return (name.split("?")[0].split(".").pop() || "").toLowerCase(); }

async function loadModel(url, name, mtl) {
  const ext = extOf(name || url);
  toast(`Loading ${name || "model"}…`, 60000);
  let obj = null, clips = [];
  // many FBX/OBJ files name their textures by the author's own path ("D:\work\tex\skin.png"): look next to the model
  const base = url.startsWith("blob:") ? "" : url.slice(0, url.lastIndexOf("/") + 1);
  const manager = new THREE.LoadingManager();
  manager.setURLModifier((u) => {
    if (!base || /^(blob:|data:)/.test(u)) return u;
    const d = decodeURIComponent(u);
    return /[A-Za-z]:[\\/]|\\/.test(d) ? base + d.split(/[\\/]/).pop() : u;
  });
  try {
    if (ext === "glb" || ext === "gltf" || ext === "vrm") {
      const loader = new GLTFLoader(manager);
      loader.register((p) => new VRMLoaderPlugin(p));
      const gltf = await loader.loadAsync(url);
      const vrm = gltf.userData.vrm;
      if (vrm) { VRMUtils.rotateVRM0(vrm); obj = vrm.scene; S.vrm = vrm; } else { obj = gltf.scene; S.vrm = null; }
      clips = gltf.animations || [];
    } else if (ext === "fbx") {
      obj = await new FBXLoader(manager).loadAsync(url); clips = obj.animations || []; S.vrm = null;
      obj.traverse((m) => { if (m.isMesh && m.geometry && !m.geometry.attributes.normal) m.geometry.computeVertexNormals(); });
    } else if (ext === "obj") {
      const loader = new OBJLoader(manager);
      if (mtl) { const mats = await new MTLLoader(manager).loadAsync(mtl); mats.preload(); loader.setMaterials(mats); }
      obj = await loader.loadAsync(url); S.vrm = null;
      if (!mtl) obj.traverse((m) => { if (m.isMesh) m.material = new THREE.MeshStandardMaterial({ color: 0xc9b8ff, roughness: 0.5, metalness: 0.15 }); });
    } else if (ext === "stl") {
      const geo = await new STLLoader().loadAsync(url); geo.computeVertexNormals();
      obj = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ color: 0xc9b8ff, roughness: 0.45, metalness: 0.2 })); S.vrm = null;
    } else {
      toast(`Can't open .${ext} files. Use GLB, GLTF, FBX, OBJ, STL or VRM.`); return;
    }
  } catch (e) {
    console.error(e); toast(`Couldn't open ${name || "that file"}: ${e.message || e}`); return;
  }
  if (ext === "fbx" || (ext === "obj" && mtl)) fixMaterials(obj);
  // centre it and scale it to about 1.8 units, inside a pivot so gestures turn it around its middle
  const box = new THREE.Box3().setFromObject(obj), size = box.getSize(new THREE.Vector3()), mid = box.getCenter(new THREE.Vector3());
  obj.position.sub(mid);
  const pivot = new THREE.Group(); pivot.add(obj);
  pivot.scale.setScalar(1.8 / (Math.max(size.x, size.y, size.z) || 1));
  holder.clear(); holder.add(pivot);
  S.mixer = null;
  if (clips.length) { S.mixer = new THREE.AnimationMixer(obj); S.mixer.clipAction(clips[0]).play(); }
  S.model = { url, name };
  resetView();
  $("empty").style.display = "none";
  toast(name || "Model loaded");
}

/* FBX/OBJ come with shiny Phong materials that wash out to white under these lights: use standard materials,
   keeping each one's texture (in sRGB), colour, normal map and transparency */
function fixMaterials(obj) {
  obj.traverse((o) => {
    if (!o.isMesh) return;
    const mats = (Array.isArray(o.material) ? o.material : [o.material]).map((m) => {
      if (!m || m.isMeshStandardMaterial) return m;
      if (m.map) m.map.colorSpace = THREE.SRGBColorSpace;
      return new THREE.MeshStandardMaterial({
        name: m.name, map: m.map || null, color: m.map ? 0xffffff : (m.color || new THREE.Color(0xc9b8ff)),
        normalMap: m.normalMap || null, transparent: !!m.transparent, opacity: m.opacity ?? 1, alphaTest: m.alphaTest || 0,
        roughness: 0.75, metalness: 0.05, side: THREE.DoubleSide,
      });
    });
    o.material = mats.length === 1 ? mats[0] : mats;
  });
}

function resetView() {
  holder.position.set(0, 0, 0); holder.quaternion.identity(); holder.scale.setScalar(1);
  controls.reset();
}

/* ------------------------------------------------------------------ files */
async function openFile() {
  if (api) { const f = await api.pick_file(); if (f && f.url) loadModel(f.url, f.name, f.mtl); return; }
  const input = Object.assign(document.createElement("input"), { type: "file", accept: ".glb,.gltf,.fbx,.obj,.stl,.vrm" });
  input.onchange = () => { const f = input.files[0]; if (f) loadModel(URL.createObjectURL(f), f.name); };
  input.click();
}
async function nextModel() {
  if (!api) return toast("NEXT needs Karen (it looks in the model's folder).");
  const f = await api.next_model();
  if (f && f.url) loadModel(f.url, f.name, f.mtl); else toast("No other 3D files in that folder.");
}
document.addEventListener("dragover", (e) => { e.preventDefault(); document.body.classList.add("drag"); });
document.addEventListener("dragleave", () => document.body.classList.remove("drag"));
document.addEventListener("drop", (e) => {
  e.preventDefault(); document.body.classList.remove("drag");
  const f = e.dataTransfer.files[0];
  if (f) loadModel(URL.createObjectURL(f), f.name);
});
$("btn-open").onclick = openFile;
$("btn-next").onclick = nextModel;
$("btn-reset").onclick = () => { resetView(); toast("View reset"); };
$("btn-control").onclick = () => setControl(!S.control);
$("btn-train").onclick = () => setMode(S.mode === "train" ? (S.back || "3d") : "train");

function toast(text, ms = 2600) {
  const t = $("toast"); t.textContent = text; t.classList.add("show");
  clearTimeout(S.toastTimer); S.toastTimer = setTimeout(() => t.classList.remove("show"), ms);
}

/* ------------------------------------------------------------------ modes and help */
const HELP = {
  "3d": [["✋ Palm, hold 1 s", "hand control on / off"], ["🤏 Pinch + move", "rotate"], ["🤏🤏 Both pinch, apart/together", "zoom"],
         ["🤏🤏 Both pinch, twist", "roll"], ["✊ Fist + move", "move it"], ["👍 Thumbs up", "next model"], ["✌️ Two fingers, hold 1 s", "reset"]],
  phone: [["✋ Palm, hold 1 s", "hand control on / off"], ["☝️ Point", "move the cursor"], ["🤏 Pinch, hold 0.3 s, release", "tap"],
          ["🤏 Pinch, hold + move", "swipe / drag"], ["✌️ Two fingers up/down", "scroll"], ["✋ Palm swipe left", "back"], ["✊ Fist, hold 1 s", "home"]],
  train: [["Follow the card", "show each gesture for 3 s"], ["3 lights", "normal, dim, bright behind you"]],
};
function setMode(mode) {
  if (mode === "train" && S.mode !== "train") S.back = S.mode;
  S.mode = mode;
  document.body.classList.toggle("phone", mode === "phone");
  document.body.classList.toggle("train", mode === "train");
  $("mode").textContent = { "3d": "HANDS · 3D", phone: "HANDS · PHONE", train: "HANDS · TRAINING" }[mode];
  $("help").innerHTML = `<h3>GESTURES</h3>` + HELP[mode].map(([g, a]) => `<div><b>${g}</b> — ${a}</div>`).join("");
  for (const id of ["btn-open", "btn-next", "btn-reset"]) $(id).style.display = mode === "3d" ? "" : "none";
  $("btn-train").classList.toggle("on", mode === "train");
  if (mode === "train") trainStart();
  setControl(false);
}
function setControl(on) {
  if (on && S.mode === "train") return;
  S.control = on;
  if (!on) phoneRelease();
  $("ctl").textContent = on ? "HAND CONTROL ON" : "HAND CONTROL OFF";
  $("ctl").className = "big " + (on ? "on" : "off");
  $("btn-control").textContent = on ? "HANDS ON" : "HANDS OFF";
  $("btn-control").classList.toggle("on", on);
}
window.addEventListener("keydown", (e) => { if (e.key === "Escape") setControl(false); });

/* ------------------------------------------------------------------ hand tracking */
const video = $("video"), overlay = $("overlay"), octx = overlay.getContext("2d");
let landmarker = null, lastVideoTime = -1;
const BONES = [[0, 1], [1, 2], [2, 3], [3, 4], [0, 5], [5, 6], [6, 7], [7, 8], [5, 9], [9, 10], [10, 11], [11, 12],
               [9, 13], [13, 14], [14, 15], [15, 16], [13, 17], [17, 18], [18, 19], [19, 20], [0, 17]];

async function startHands() {
  try {
    const vision = await FilesetResolver.forVisionTasks("hands_assets");
    const opts = (delegate) => ({
      baseOptions: { modelAssetPath: "hands_assets/hand_landmarker.task", delegate }, runningMode: "VIDEO", numHands: 2,
      // a little lower than the 0.5 default for finding hands: his evening light found no hand most of the time
      minHandDetectionConfidence: 0.45, minHandPresenceConfidence: 0.45, minTrackingConfidence: 0.45,
    });
    try { landmarker = await HandLandmarker.createFromOptions(vision, opts("GPU")); }
    catch { landmarker = await HandLandmarker.createFromOptions(vision, opts("CPU")); }
  } catch (e) {
    $("gesture").textContent = "Hand tracking files are missing (run scripts/download_models.py)."; console.error(e); return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: "user" }, audio: false });
    video.srcObject = stream; await video.play();
  } catch (e) {
    $("gesture").textContent = "No camera: " + (e.message || e.name || e); console.error(e);
    call("report", "no camera: " + (e.message || e.name || e)); return;
  }
  $("gesture").textContent = "Show your hands to the camera";
  call("report", `camera ${video.videoWidth}x${video.videoHeight}`);
  requestAnimationFrame(track);
  setInterval(() => {  // a line in logs/hands.log every 10 s: frames per second and hands seen
    call("report", `${S.fps} fps, ${S.hands.length} hands, ${S.hands.map((h) => h.label).join("+") || "-"}, control ${S.control ? "on" : "off"}`);
  }, 10000);
}

function track() {
  requestAnimationFrame(track);
  if (!landmarker || video.readyState < 2 || video.currentTime === lastVideoTime) return;
  lastVideoTime = video.currentTime;
  const t = performance.now();
  S.frames = (S.frames || []).filter((f) => t - f < 1000); S.frames.push(t); S.fps = S.frames.length;
  const res = landmarker.detectForVideo(video, t);
  const w = video.videoWidth, h = video.videoHeight;
  if (overlay.width !== w) { overlay.width = w; overlay.height = h; }
  S.hands = (res.landmarks || []).map((lm, i) => describe(lm, ((res.handedness || [])[i] || [{}])[0].categoryName || "Right", w, h));
  if (S.hands.length) S.lastSeen = performance.now();
  draw(S.hands, w, h);
  if (S.mode === "train") trainFrame(); else act();
}

function draw(hands, w, h) {
  octx.clearRect(0, 0, w, h);
  for (const hand of hands) {
    const color = S.control ? "#4dffb0" : "#b57bff";
    octx.strokeStyle = color; octx.lineWidth = 3; octx.shadowColor = color; octx.shadowBlur = 10;
    for (const [a, b] of BONES) {
      octx.beginPath(); octx.moveTo(hand.lm[a].x * w, hand.lm[a].y * h); octx.lineTo(hand.lm[b].x * w, hand.lm[b].y * h); octx.stroke();
    }
    octx.fillStyle = "#f1e9ff";
    for (const p of hand.lm) { octx.beginPath(); octx.arc(p.x * w, p.y * h, 3.2, 0, 7); octx.fill(); }
  }
  octx.shadowBlur = 0;
}

/* ------------------------------------------------------------------ gesture recognition */
const TIP = [8, 12, 16, 20], PIP = [6, 10, 14, 18];
function describe(lm, side, w, h) {
  const P = (i) => ({ x: lm[i].x * w, y: lm[i].y * h });
  const d = (a, b) => Math.hypot(P(a).x - P(b).x, P(a).y - P(b).y);
  const palm = d(0, 9) || 1;
  const fingers = TIP.map((t, i) => d(0, t) > d(0, PIP[i]) * 1.15);
  const thumbOut = d(4, 5) / palm > 0.55 && d(4, 9) / palm > 0.75;
  // a pinch keeps the index fingertip well away from the wrist; in a fist the thumb also lies near the curled index
  const pinch = d(0, 8) / palm > 1.05 ? d(4, 8) / palm : 9;
  // screen coordinates: the camera picture is shown mirrored, so x is flipped (move right = right)
  const sx = (i) => 1 - lm[i].x, sy = (i) => lm[i].y;
  const hand = { lm, side, palm: palm / w, fingers, thumbOut, pinch, wrist: { x: sx(0), y: sy(0) },
                 tip: { x: sx(8), y: sy(8) }, pinchPt: { x: 1 - (lm[4].x + lm[8].x) / 2, y: (lm[4].y + lm[8].y) / 2 },
                 thumbUp: lm[4].y < lm[2].y - (palm / h) * 0.35 };
  hand.vec = vector(lm, side, w, h);
  hand.raw = rules(hand);
  hand.label = classify(hand);
  return hand;
}

function rules(h) {
  const n = h.fingers.filter(Boolean).length;
  const pinchT = (S.profile && S.profile.pinch) || 0.33;
  if (h.pinch < pinchT) return "pinch";
  if (n === 4 && h.thumbOut) return "palm";
  if (n === 0 && h.thumbOut && h.thumbUp) return "thumbs";
  if (n === 0) return "fist";
  if (h.fingers[0] && !h.fingers[1] && !h.fingers[2] && !h.fingers[3]) return "point";
  if (h.fingers[0] && h.fingers[1] && !h.fingers[2] && !h.fingers[3]) return "two";
  return "other";
}

function vector(lm, side, w, h) {
  // 21 points relative to the wrist, scaled by palm length; the left hand mirrored to look like a right hand
  const x0 = lm[0].x * w, y0 = lm[0].y * h, palm = Math.hypot(lm[9].x * w - x0, lm[9].y * h - y0) || 1;
  const flip = side === "Left" ? -1 : 1, v = [];
  for (const p of lm) v.push(flip * (p.x * w - x0) / palm, (p.y * h - y0) / palm, (p.z * w) / palm);
  return v;
}

function classify(h) {
  // with his training, his own samples decide every gesture, pinch included: on his 841 samples k-NN was 99.3% right
  // and the finger rules 80% (open palm only 38%), so the rules are just the fallback when the neighbours disagree
  const prof = S.profile;
  if (!prof || !prof.samples || prof.samples.length < 30) return h.raw;
  // k-nearest neighbours over the user's own samples
  const best = [];
  for (const s of prof.samples) {
    let dist = 0;
    for (let i = 0; i < s.v.length; i++) { const t = s.v[i] - h.vec[i]; dist += t * t; }
    if (best.length < 5 || dist < best[best.length - 1][0]) {
      best.push([dist, s.l]); best.sort((a, b) => a[0] - b[0]); if (best.length > 5) best.pop();
    }
  }
  const votes = {};
  for (const [, l] of best) votes[l] = (votes[l] || 0) + 1;
  const [label, count] = Object.entries(votes).sort((a, b) => b[1] - a[1])[0];
  const near = Math.sqrt(best[0][0]);
  if (count >= 3 && near < (prof.maxDist || 99)) return label;
  return h.raw;
}

/* a gesture must be seen in 3 frames in a row before it counts; slots are per hand side */
function stable(hand) {
  const slot = S.slots[hand.side] || (S.slots[hand.side] = { label: "", count: 0, stable: "" });
  if (hand.label === slot.label) slot.count++; else { slot.label = hand.label; slot.count = 1; }
  if (slot.count >= 3) slot.stable = slot.label;
  return slot.stable;
}

/* hold a still gesture for ms (the progress bar fills); fires once until the gesture is released */
function held(kind, hand, ms) {
  const now = performance.now(), bar = $("hold").firstElementChild;
  if (S.holdKind !== kind) { S.holdKind = kind; S.holdStart = now; S.holdFrom = { ...hand.wrist }; }
  const moved = Math.hypot(hand.wrist.x - S.holdFrom.x, hand.wrist.y - S.holdFrom.y);
  if (moved > 0.06) { S.holdStart = now; S.holdFrom = { ...hand.wrist }; }
  const p = Math.min(1, (now - S.holdStart) / ms);
  bar.style.width = (p * 100) + "%";
  if (p >= 1 && S.holdLatch !== kind) { S.holdLatch = kind; return true; }
  return false;
}
function noHold() { S.holdKind = ""; $("hold").firstElementChild.style.width = "0%"; }

const NAMES = { palm: "✋ open palm", fist: "✊ fist", point: "☝️ point", two: "✌️ two fingers", thumbs: "👍 thumbs up",
                pinch: "🤏 pinch", other: "hand", "": "hand" };

function act() {
  const now = performance.now();
  const hands = S.hands.map((h) => ({ ...h, label: stable(h) }));
  for (const side of Object.keys(S.slots)) if (!hands.find((h) => h.side === side)) delete S.slots[side];
  $("gesture").textContent = hands.length ? hands.map((h) => NAMES[h.label] || h.label).join("  +  ") : "No hands in view";
  if (!hands.length) {
    noHold(); S.holdLatch = ""; S.prev = {}; S.two = null; phoneRelease();
    if (S.control && S.mode === "phone" && now - S.lastSeen > 4000) { setControl(false); toast("Hand control off (no hands for 4 s)"); }
    return;
  }
  const main = hands.slice().sort((a, b) => b.palm - a.palm)[0];  // the nearest hand leads
  // open palm held still for 1 s: control on/off (works in both modes, also while control is off)
  if (hands.length === 1 && main.label === "palm" && !palmSwiping(main)) {
    if (held("palm", main, 1000)) { setControl(!S.control); toast(S.control ? "Hand control ON" : "Hand control OFF"); }
  } else if (main.label === "two" && S.mode === "3d" && S.control) {
    if (held("two", main, 1000)) { resetView(); toast("View reset"); }
  } else if (main.label === "thumbs" && S.mode === "3d" && S.control) {
    // held, not instant: a stray thumbs-up flipped through 7 models in 20 s (2026-10-19 log)
    if (held("thumbs", main, 800)) nextModel();
  } else if (main.label === "fist" && S.mode === "phone" && S.control && hands.length === 1) {
    if (held("fist", main, 1000)) { phoneKey("home"); }
  } else {
    noHold();
  }
  if (!hands.some((h) => h.label === S.holdLatch)) S.holdLatch = "";
  if (!S.control) { S.prev = {}; S.two = null; phoneRelease(); return; }
  if (S.mode === "3d") act3d(hands, main); else if (S.mode === "phone") actPhone(main);
}

/* ------------------------------------------------------------------ 3D gestures */
function act3d(hands, main) {
  const pinched = hands.filter((h) => h.label === "pinch");
  if (pinched.length === 2) {  // two hands: zoom (distance) and roll (angle)
    const [a, b] = pinched[0].pinchPt.x < pinched[1].pinchPt.x ? [pinched[0], pinched[1]] : [pinched[1], pinched[0]];
    const dist = Math.hypot(b.pinchPt.x - a.pinchPt.x, b.pinchPt.y - a.pinchPt.y);
    const ang = Math.atan2(b.pinchPt.y - a.pinchPt.y, b.pinchPt.x - a.pinchPt.x);
    if (!S.two) S.two = { dist, ang, scale: holder.scale.x, q: holder.quaternion.clone() };
    holder.scale.setScalar(THREE.MathUtils.clamp(S.two.scale * dist / S.two.dist, 0.1, 12));
    const axis = new THREE.Vector3().subVectors(camera.position, controls.target).normalize();
    const roll = new THREE.Quaternion().setFromAxisAngle(axis, -(ang - S.two.ang));
    holder.quaternion.copy(roll.multiply(S.two.q));
    S.prev = {};
    return;
  }
  S.two = null;
  const key = main.side + main.label, p = main.label === "pinch" ? main.pinchPt : main.wrist;
  const last = S.prev[key];
  S.prev = { [key]: { ...p } };
  if (!last) return;
  const dx = p.x - last.x, dy = p.y - last.y;
  if (Math.abs(dx) > 0.2 || Math.abs(dy) > 0.2) return;  // a jump (tracking glitch), skip
  if (main.label === "pinch") {  // rotate: left/right turns around the vertical axis, up/down tips it
    holder.rotateOnWorldAxis(new THREE.Vector3(0, 1, 0), dx * 5);
    const right = new THREE.Vector3().setFromMatrixColumn(camera.matrixWorld, 0);
    holder.rotateOnWorldAxis(right, dy * 5);
  } else if (main.label === "fist") {  // move it in the screen plane
    const right = new THREE.Vector3().setFromMatrixColumn(camera.matrixWorld, 0), up = new THREE.Vector3().setFromMatrixColumn(camera.matrixWorld, 1);
    holder.position.addScaledVector(right, dx * 3).addScaledVector(up, -dy * 3);
  }
}

/* ------------------------------------------------------------------ phone gestures */
function palmSwiping(hand) {
  const now = performance.now(), trail = S.phone.palmTrail;
  trail.push({ t: now, x: hand.wrist.x });
  while (trail.length && now - trail[0].t > 450) trail.shift();
  return trail.length > 2 && trail[0].x - hand.wrist.x > 0.12;
}

function actPhone(main) {
  const P = S.phone, now = performance.now();
  if (main.label === "palm") {  // fast swipe to the left: Back
    P.palmTrail.push({ t: now, x: main.wrist.x });
    while (P.palmTrail.length && now - P.palmTrail[0].t > 450) P.palmTrail.shift();
    if (P.palmTrail.length > 2 && P.palmTrail[0].x - main.wrist.x > 0.22 && now - P.backAt > 1200) {
      P.backAt = now; P.palmTrail = []; phoneKey("back");
    }
    phoneRelease();
    return;
  }
  P.palmTrail = [];
  if (!["point", "pinch", "two"].includes(main.label)) { phoneRelease(); return; }
  const raw = main.label === "pinch" ? main.pinchPt : main.tip;
  // the middle 60% of the camera picture covers the whole phone screen, so every corner is easy to reach
  const tx = THREE.MathUtils.clamp((raw.x - 0.2) / 0.6, 0, 1), ty = THREE.MathUtils.clamp((raw.y - 0.15) / 0.6, 0, 1);
  P.x += (tx - P.x) * 0.35; P.y += (ty - P.y) * 0.35;  // smoothing: steadier cursor
  if (main.label === "two") {  // scroll with two fingers moving up/down
    phoneRelease();
    if (P.scrollY !== undefined) {
      P.scrollAcc += raw.y - P.scrollY;
      while (Math.abs(P.scrollAcc) > 0.035) { const dir = P.scrollAcc > 0 ? 1 : -1; P.scrollAcc -= dir * 0.035; call("phone_scroll", P.x, P.y, dir); }
    }
    P.scrollY = raw.y;
    return;
  }
  P.scrollY = undefined; P.scrollAcc = 0;
  if (main.label === "pinch") {
    if (!P.pinchAt) P.pinchAt = now;
    if (!P.down && now - P.pinchAt >= 300) { P.down = true; call("phone_pointer", P.x, P.y, "down"); return; }  // 0.3 s hold = press
  } else {
    if (P.down) { P.down = false; call("phone_pointer", P.x, P.y, "up"); }  // release = tap (or end of a swipe)
    P.pinchAt = 0;
  }
  call("phone_pointer", P.x, P.y, "move");
}
function phoneRelease() {
  const P = S.phone;
  if (P.down) { P.down = false; call("phone_pointer", P.x, P.y, "up"); }
  P.pinchAt = 0; P.scrollY = undefined;
}
function phoneKey(name) { call("phone_key", name); toast(name === "home" ? "Home" : "Back"); }
function call(name, ...args) { if (api && api[name]) api[name](...args).catch(() => {}); }

/* ------------------------------------------------------------------ training */
const T_GESTURES = [["palm", "✋ Open palm", "Hand up, fingers spread"], ["fist", "✊ Fist", "Close your hand"],
  ["point", "☝️ Point", "Only your index finger up"], ["two", "✌️ Two fingers", "Index and middle finger up, like a V"],
  ["thumbs", "👍 Thumbs up", "Only your thumb up"], ["pinch", "🤏 Pinch", "Thumb and index fingertips touching"],
  ["other", "🖐 Relaxed hand", "Your normal, relaxed hand: not a gesture"]];
const T_LIGHTS = [["normal", "Normal light"], ["dim", "Dim light: switch a light off or move away from the lamp"],
  ["bright", "Bright light behind you: sit with a window or lamp behind you"]];
const T = { steps: [], i: 0, phase: "idle", until: 0, samples: [], got: 0 };

function trainStart() {
  T.steps = [];
  for (const [lid, ltext] of T_LIGHTS) for (const [gid, gname, how] of T_GESTURES) T.steps.push({ gid, gname, how, lid, ltext });
  T.i = 0; T.samples = []; T.phase = "idle";
  showStep("Each gesture for 3 seconds, in 3 kinds of light (about 5 minutes). Use either hand; both is even better.");
}
function showStep(note) {
  const s = T.steps[T.i];
  if (!s) return;
  const lightChange = T.i % T_GESTURES.length === 0;
  $("t-step").textContent = `STEP ${T.i + 1} / ${T.steps.length} · ${s.ltext.split(":")[0].toUpperCase()}`;
  $("t-what").textContent = s.gname;
  $("t-how").textContent = note || (lightChange ? s.ltext + ". Then: " + s.how : s.how);
  $("t-bar").style.width = "0%";
  $("t-go").style.display = ""; $("t-go").textContent = T.i ? "NEXT" : "START";
}
$("t-go").onclick = () => {
  if (T.phase === "done") { setMode(S.back || "3d"); return; }
  T.phase = "count"; T.until = performance.now() + 1500; T.got = 0; $("t-go").style.display = "none";
};
function trainFrame() {
  const now = performance.now(), s = T.steps[T.i];
  if (T.phase === "count") {
    $("t-how").textContent = `Get ready… ${Math.ceil((T.until - now) / 500) / 2}s`;
    if (now >= T.until) { T.phase = "rec"; T.until = now + 3000; }
    return;
  }
  if (T.phase !== "rec") return;
  $("t-how").textContent = S.hands.length ? `Recording ${s.gname}… keep going, move it a little` : "No hand in view: show your hand to the camera";
  if (S.hands.length && T.got < 40) {
    for (const h of S.hands.slice(0, 2)) { T.samples.push({ l: s.gid, v: h.vec.map((x) => +x.toFixed(3)), p: +h.pinch.toFixed(3), c: s.lid }); T.got++; }
  }
  $("t-bar").style.width = Math.min(100, 100 - (T.until - now) / 30) + "%";
  if (S.hands.length) { if (now >= T.until) next(); } else T.until += 16;  // pause the clock while no hand is visible
  function next() {
    T.i++;
    if (T.i < T.steps.length) { T.phase = "idle"; showStep(); return; }
    finishTraining();
  }
}
function finishTraining() {
  T.phase = "done";
  const samples = T.samples;
  // pinch threshold: between the user's real pinches and everything else
  const pin = samples.filter((s) => s.l === "pinch").map((s) => s.p).sort((a, b) => a - b);
  const rest = samples.filter((s) => s.l !== "pinch").map((s) => s.p).sort((a, b) => a - b);
  const hi = pin[Math.floor(pin.length * 0.9)] || 0.3, lo = rest[Math.floor(rest.length * 0.05)] || 0.5;
  // just above your loosest real pinch (never past halfway to your other gestures): no stray pinches
  const pinch = THREE.MathUtils.clamp(Math.min(hi * 1.6, (hi + lo) / 2), 0.18, 0.42);
  // nearest-neighbour check, leaving each sample out once
  let right = 0; const nn = [];
  for (let i = 0; i < samples.length; i++) {
    let best = 1e9, label = "";
    for (let j = 0; j < samples.length; j++) {
      if (i === j) continue;
      let d = 0; for (let k = 0; k < samples[i].v.length; k++) { const t = samples[i].v[k] - samples[j].v[k]; d += t * t; }
      if (d < best) { best = d; label = samples[j].l; }
    }
    if (label === samples[i].l) right++;
    nn.push(Math.sqrt(best));
  }
  nn.sort((a, b) => a - b);
  const maxDist = (nn[Math.floor(nn.length * 0.95)] || 1) * 1.5;
  S.profile = { samples: samples.map(({ l, v, c }) => ({ l, v, c })), pinch: +pinch.toFixed(3), maxDist: +maxDist.toFixed(3),
                accuracy: samples.length ? +(right / samples.length).toFixed(3) : 0, trained: new Date().toISOString() };
  call("save_profile", JSON.stringify(S.profile));
  $("t-step").textContent = "TRAINING DONE";
  $("t-what").textContent = `${Math.round(S.profile.accuracy * 100)}% recognised`;
  $("t-how").textContent = `${samples.length} samples saved. Pinch threshold ${S.profile.pinch}. Train again any time (e.g. at night).`;
  $("t-bar").style.width = "100%"; $("t-go").style.display = ""; $("t-go").textContent = "DONE";
}

/* The pinch threshold, worked out again from the saved samples on every start (the first training version
   saved 0.5, far too loose: open hands counted as pinches). Each sample's thumb-index gap is in its vector. */
function calibrate(prof) {
  const samples = (prof && prof.samples) || [];
  const gap = (v) => Math.hypot(v[12] - v[24], v[13] - v[25]);  // landmarks 4 and 8, already divided by palm length
  const pin = samples.filter((s) => s.l === "pinch").map((s) => gap(s.v)).sort((a, b) => a - b);
  const rest = samples.filter((s) => s.l !== "pinch").map((s) => gap(s.v)).sort((a, b) => a - b);
  if (pin.length > 10 && rest.length > 10) {
    const hi = pin[Math.floor(pin.length * 0.9)], lo = rest[Math.floor(rest.length * 0.05)];
    prof.pinch = +THREE.MathUtils.clamp(Math.min(hi * 1.6, (hi + lo) / 2), 0.18, 0.42).toFixed(3);
  } else {
    prof.pinch = Math.min(prof.pinch || 0.33, 0.42);
  }
  call("report", `profile: ${samples.length} samples, pinch threshold ${prof.pinch}`);
  return prof;
}

/* ------------------------------------------------------------------ Python bridge */
window.hands = {
  command(cmd) {  // from hands.py: another request while the window is open
    if (cmd.mode && cmd.mode !== S.mode) setMode(cmd.mode);
    if (cmd.model && cmd.model.url) { if (S.mode !== "3d") setMode("3d"); loadModel(cmd.model.url, cmd.model.name, cmd.model.mtl); }
    if (cmd.toast) toast(cmd.toast);
  },
  // tests: feed made-up hands ([[landmarks, "Left"|"Right"], ...]) through the same gesture code, and read the result
  feed(list, w = 640, h = 480) {
    S.hands = list.map(([lm, side]) => describe(lm, side, w, h));
    if (S.hands.length) S.lastSeen = performance.now();
    if (S.mode === "train") trainFrame(); else act();
  },
  state: () => ({ mode: S.mode, control: S.control, labels: S.hands.map((h) => h.label),
                  rot: [holder.rotation.x, holder.rotation.y, holder.rotation.z].map((v) => +v.toFixed(3)),
                  scale: +holder.scale.x.toFixed(3), pos: holder.position.toArray().map((v) => +v.toFixed(3)) }),
};

async function boot() {
  setMode((location.hash || "#3d").slice(1) || "3d");
  const bridge = () => window.pywebview && window.pywebview.api;
  if (!bridge()) await new Promise((r) => { window.addEventListener("pywebviewready", r, { once: true }); setTimeout(r, 1500); });
  api = bridge() || null;
  if (!api) { startHands(); toast("Preview without Karen: drop a 3D file, gestures work, phone control needs Karen."); return; }
  const init = await api.ready();  // also lets this page use the camera (hands.py answers WebView2's permission request)
  if (init.profile) S.profile = calibrate(init.profile);
  startHands();
  window.hands.command(init);
}
boot();
