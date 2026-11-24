/* Avatar mode: a full-body anime character in place of the orb (Settings > Avatar picks which one).
   Off by default; the dock's avatar button toggles it. Nothing heavy loads until the first toggle.

   Characters come from avatar/characters.json (config.yaml avatar.characters, linked in by core/ui.link_avatar):
   - VRM (Karen, the girl): three-vrm; Mixamo motions are retargeted with pixiv's loader; full face: expressions,
     blinking, eyes following the mouse, lip-sync from her real voice level.
   - FBX rigged by Mixamo's auto-rigger (Rimuru): the same Mixamo motions play straight on its mixamorig skeleton;
     no face shapes, so speaking adds a gentle head bob instead of lip-sync.
   Movement is real motion capture (models/motions/*.fbx, listed in avatar/motions.json), cross-faded by Karen's
   state, with a small head tilt while listening/confirming. No extra AI calls.
   Look: soft key/fill/rim lights, a real contact shadow, a slight three-quarter camera. 30 fps cap; stops when off. */
const HEIGHT = 1.55;  // non-VRM characters are scaled to this height (metres), so the camera framing fits all
// Karen's states -> motions to use (files that exist; several = take turns)
const PLAN = {
  idle: ["idle", "idle2"], listening: ["listening", "idle"], thinking: ["thinking", "idle"],
  talking: ["talking", "talking2", "talking3"], wave: ["wave"], happy: ["happy", "happy2"], sad: ["sad"],
  surprised: ["surprised"], nod: ["nod"],
  // on request ("dance", "catwalk") and reactions after an answer
  dance: ["dance", "dance2", "dance3", "dance4", "dance5", "dance6", "dance7"], catwalk: ["catwalk", "catwalk2"],
  cheer: ["cheer", "cheer2", "excited", "happy2"], romance: ["blowkiss", "kiss", "bashful"], kiss: ["blowkiss", "kiss"],
};
const A = {
  on: false, ready: false, loading: null, THREE: null, renderer: null, scene: null, camera: null,
  characters: [], wanted: null, char: null, motionNames: [], clipCache: {},
  action: null, actionName: "", oneShotUntil: 0,
  mode: "sleeping", voice: 0, emotion: "neutral", emotionUntil: 0, surpriseUntil: 0, waveUntil: 0,
  blinkAt: 2, mouse: { x: 0, y: 0 }, last: 0, t: 0, raf: 0, lookTarget: null, talkPick: 0, tilt: 0,
  showName: "", showUntil: 0, showAsk: 0, romance: false,  // a requested dance/catwalk; love words in her answer
};

/* ------------------------------------------------------------------ toggle / switch */
async function toggle(force) {
  A.on = force ?? !A.on;
  document.body.classList.toggle("avatar-mode", A.on);
  document.getElementById("btn-avatar").classList.toggle("on", A.on);
  if (!A.on) { cancelAnimationFrame(A.raf); return; }
  try {
    await load();
  } catch (e) {
    console.error(e);
    status("Couldn't load the avatar: " + (e.message || e));
    return;
  }
  greet();
  A.last = performance.now();
  A.raf = requestAnimationFrame(frame);
}

function status(text) { document.getElementById("avatar-status").textContent = text; }

function greet() {
  A.emotion = "happy"; A.emotionUntil = A.t + 3;
  if (A.char && A.char.clips.wave) oneShot("wave");
  else A.waveUntil = A.t + 2.6;
}

/* Pick a character (from Settings or the saved setting). Loads it now if avatar mode is already showing. */
async function setCharacter(id) {
  A.wanted = id;
  const sel = document.getElementById("set-avatar");
  if (sel && id) sel.value = id;
  if (!A.ready || (A.char && A.char.id === id)) return;
  try {
    await useCharacter(id);
    greet();
  } catch (e) { console.error(e); status("Couldn't load that character: " + (e.message || e)); }
}

/* ------------------------------------------------------------------ the stage (once) */
async function load() {
  if (A.ready) return;
  if (A.loading) return A.loading;
  status("Loading…");
  A.loading = (async () => {
    const THREE = A.THREE = await import("three");
    const canvas = document.getElementById("avatar");
    const r = A.renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true, powerPreference: "low-power" });
    r.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    r.outputColorSpace = THREE.SRGBColorSpace;
    r.shadowMap.enabled = true;
    r.shadowMap.type = THREE.PCFSoftShadowMap;
    A.scene = new THREE.Scene();
    A.camera = new THREE.PerspectiveCamera(22, 1, 0.1, 30);
    A.camera.position.set(2.0, 1.08, 5.2);  // off to the side and a bit above: a three-quarter view, not a mugshot
    A.camera.lookAt(0, 0.82, 0);

    // soft studio light: warm key with a soft shadow, cool fill, the interface's purple as a rim from behind
    const key = new THREE.DirectionalLight(0xfff1e6, 1.7);
    key.position.set(1.6, 3.2, 2.4);
    key.castShadow = true;
    key.shadow.mapSize.set(1024, 1024);
    key.shadow.radius = 6;
    key.shadow.bias = -0.0005;
    Object.assign(key.shadow.camera, { left: -1.2, right: 1.2, top: 2.2, bottom: -0.3, near: 0.5, far: 8 });
    const fill = new THREE.DirectionalLight(0xdfe6ff, 0.55);
    fill.position.set(-2, 1.4, 2);
    const rim = new THREE.DirectionalLight(0xb27bff, 1.8);
    rim.position.set(-1.4, 1.8, -2);
    A.scene.add(key, key.target, fill, rim, new THREE.HemisphereLight(0xffffff, 0x3a2160, 0.45));
    const floor = new THREE.Mesh(new THREE.CircleGeometry(1.6, 48), new THREE.ShadowMaterial({ opacity: 0.5 }));
    floor.rotation.x = -Math.PI / 2;
    floor.receiveShadow = true;
    A.scene.add(floor);
    A.lookTarget = new THREE.Object3D();
    A.scene.add(A.lookTarget);

    A.characters = await fetchJSON("avatar/characters.json", []);
    A.motionNames = await fetchJSON("avatar/motions.json", []);
    fillPicker();
    const usable = A.characters.filter((c) => c.available);
    if (!usable.length) throw new Error("no character model found (config.yaml avatar.characters)");
    const id = usable.some((c) => c.id === A.wanted) ? A.wanted : usable[0].id;
    resize();
    await useCharacter(id);
    A.ready = true;
    status("");
  })();
  return A.loading;
}

async function fetchJSON(url, fallback) {
  try { return await (await fetch(url, { cache: "no-store" })).json(); } catch (e) { return fallback; }
}

function fillPicker() {
  const sel = document.getElementById("set-avatar");
  if (!sel || sel.options.length) return;
  for (const c of A.characters) {
    const o = document.createElement("option");
    o.value = c.id;
    const by = c.credit ? ` — by ${c.credit}` : "";  // some licences require crediting the model's author
    o.textContent = c.available ? c.name + by : `${c.name} (not set up yet)`;
    o.disabled = !c.available;
    sel.appendChild(o);
  }
  if (A.wanted) sel.value = A.wanted;
}

/* ------------------------------------------------------------------ characters */
async function useCharacter(id) {
  const info = A.characters.find((c) => c.id === id && c.available);
  if (!info) throw new Error(`character ${id} isn't available`);
  status(`Loading ${info.name}…`);
  const char = info.kind === "vrm" ? await loadVRM(info) : await loadFBX(info);
  if (A.char) { A.scene.remove(A.char.root); A.char.mixer?.stopAllAction(); }
  A.char = char;
  char.romance = info.romance !== false;  // config `romance: false` (Nezuko, a child character): never kiss/blush
  A.action = null; A.actionName = ""; A.oneShotUntil = 0;
  A.scene.add(char.root);
  status("Loading motions…");
  await loadMotions(char);
  status("");
}

async function loadVRM(info) {
  const THREE = A.THREE;
  const { GLTFLoader } = await import("three/addons/loaders/GLTFLoader.js");
  const { VRMLoaderPlugin, VRMUtils } = await import("@pixiv/three-vrm");
  const loader = new GLTFLoader();
  loader.register((parser) => new VRMLoaderPlugin(parser));
  const gltf = await loader.loadAsync(`avatar/${info.file}`, (p) => {
    if (p.total) status(`Loading ${info.name}… ${Math.round((p.loaded / p.total) * 100)}%`);
  });
  const vrm = gltf.userData.vrm;
  VRMUtils.removeUnnecessaryVertices(gltf.scene);
  VRMUtils.combineSkeletons(gltf.scene);
  VRMUtils.rotateVRM0(vrm);  // older VRM 0.x models face the other way
  vrm.scene.traverse((o) => { o.frustumCulled = false; if (o.isMesh) o.castShadow = true; });
  // body faces forward (not at the camera) so the three-quarter angle shows. "+=": rotateVRM0 has already turned
  // VRM 0.x models round by 180°, and replacing that left them facing away
  vrm.scene.rotation.y += -0.12;
  if (vrm.lookAt) vrm.lookAt.target = A.lookTarget;
  const h = vrm.humanoid;
  return {
    id: info.id, kind: "vrm", root: vrm.scene, vrm, clips: {}, mixer: new THREE.AnimationMixer(vrm.scene),
    hipsY: h.getNormalizedBoneNode("hips").position.y,
    bone: (name) => h.getNormalizedBoneNode(name),
    update: (dt) => vrm.update(dt),
    clip: async (name) => {
      const { loadMixamoAnimation } = await import("./vendor/mixamo/loadMixamoAnimation.js");
      return loadMixamoAnimation(`avatar/motions/${name}.fbx`, vrm);
    },
  };
}

const MIXAMO = { hips: "mixamorigHips", spine: "mixamorigSpine", chest: "mixamorigSpine1", neck: "mixamorigNeck",
                 head: "mixamorigHead" };

async function loadFBX(info) {
  const THREE = A.THREE;
  const { FBXLoader } = await import("three/addons/loaders/FBXLoader.js");
  const obj = await new FBXLoader().loadAsync(`avatar/${info.file}`);
  // textures: the model's own paths point at its creator's PC, so match skin/clothes/hair... files by material name
  const maps = {};
  for (const f of info.textures) {
    const t = new THREE.TextureLoader().load(`avatar/${info.id}/${f}`);
    t.colorSpace = THREE.SRGBColorSpace;
    maps[f.replace(/\.\w+$/, "").toLowerCase()] = t;
  }
  const keys = Object.keys(maps);
  obj.traverse((o) => {
    if (!o.isMesh) return;
    if (!o.geometry.attributes.normal) o.geometry.computeVertexNormals();  // Mixamo's rigged Rimuru had none: all black
    o.castShadow = true;
    o.frustumCulled = false;
    const mats = (Array.isArray(o.material) ? o.material : [o.material]).map((m) => {
      const k = keys.find((k) => (m.name || "").toLowerCase().includes(k)) || (m.map ? null : keys[0]);
      return new THREE.MeshToonMaterial({ name: m.name, map: k ? maps[k] : m.map, color: k || m.map ? 0xffffff : m.color,
                                          side: THREE.DoubleSide });
    });
    o.material = mats.length === 1 ? mats[0] : mats;
  });
  // stand on the floor at a common height
  const box = new THREE.Box3().setFromObject(obj);
  const scale = HEIGHT / (box.max.y - box.min.y);
  obj.scale.multiplyScalar(scale);
  const box2 = new THREE.Box3().setFromObject(obj);
  obj.position.y -= box2.min.y;
  // each bone's rest rotation in the model's own space, for retargeting motions recorded on another Mixamo rig
  obj.updateMatrixWorld(true);
  const rest = {};
  obj.traverse((o) => {
    if (!o.isBone && !/^mixamorig/.test(o.name)) return;
    rest[o.name] = { q: o.getWorldQuaternion(new THREE.Quaternion()),
                     p: o.parent ? o.parent.getWorldQuaternion(new THREE.Quaternion()) : new THREE.Quaternion() };
  });
  const root = new THREE.Group();
  root.add(obj);
  root.rotation.y = -0.12;
  const byName = (n) => obj.getObjectByName(n);
  const hips = byName(MIXAMO.hips);
  return {
    id: info.id, kind: "fbx", root, obj, clips: {}, mixer: new THREE.AnimationMixer(obj),
    hipsY: hips ? hips.position.y : 0,
    bone: (name) => byName(MIXAMO[name] || name),
    update: () => {},
    clip: async (name) => {
      const src = await new FBXLoader().loadAsync(`avatar/motions/${name}.fbx`);
      const clip = (src.animations.find((a) => a.name === "mixamo.com") || src.animations[0]).clone();
      // retarget: the motion's rotation relative to its own rest pose, re-expressed on this skeleton's rest pose
      src.updateMatrixWorld(true);
      const q = new THREE.Quaternion(), sRest = new THREE.Quaternion(), sPar = new THREE.Quaternion();
      for (const track of clip.tracks) {
        const [name, prop] = track.name.split(".");
        const sNode = src.getObjectByName(name), tgt = rest[name];
        if (prop !== "quaternion" || !sNode || !tgt) continue;
        sNode.getWorldQuaternion(sRest).invert();
        if (sNode.parent) sNode.parent.getWorldQuaternion(sPar); else sPar.identity();
        const tParInv = tgt.p.clone().invert();
        for (let i = 0; i < track.values.length; i += 4) {
          q.fromArray(track.values, i).premultiply(sPar).multiply(sRest);  // world-space change from rest
          q.premultiply(tParInv).multiply(tgt.q);                         // back into this bone's local space
          q.toArray(track.values, i);
        }
      }
      // hips: keep only the up-down part, scaled from the motion's skeleton to this character's
      const t = clip.tracks.find((k) => k.name === `${MIXAMO.hips}.position`);
      if (t && hips) {
        const y0 = t.values[1] || 1, ratio = hips.position.y / y0;
        for (let i = 0; i < t.values.length; i += 3) {
          t.values[i] = hips.position.x; t.values[i + 2] = hips.position.z;
          t.values[i + 1] = hips.position.y + (t.values[i + 1] - y0) * ratio;
        }
      }
      clip.tracks = clip.tracks.filter((k) => k.name.endsWith(".quaternion") || k === t);
      return clip;
    },
  };
}

const EXTRAS = new Set([...PLAN.dance, ...PLAN.catwalk, ...PLAN.cheer, ...PLAN.romance]);

async function loadMotion(char, n) {
  try {
    const clip = await char.clip(n);
    if (char.kind === "vrm") {  // keep her on her spot: drop sideways/forward travel of the hips
      const hips = clip.tracks.find((t) => t.name.endsWith(".position"));
      if (hips) for (let i = 0; i < hips.values.length; i += 3) { hips.values[i] = 0; hips.values[i + 2] = 0; }
    }
    char.clips[n] = clip;
  } catch (e) { console.warn("motion", n, e); }
  return char.clips[n];
}

async function loadMotions(char) {
  // only the everyday motions up front; dances and reactions load the first time they're wanted (ensure()),
  // because parsing all 25 FBX files froze her for seconds and doubled the character switch time
  char.extras = A.motionNames.filter((n) => EXTRAS.has(n));
  await Promise.all(A.motionNames.filter((n) => !EXTRAS.has(n)).map((n) => loadMotion(char, n)));
}

/* A random motion of this kind, loading it first if needed (about half a second the first time). */
async function ensure(kind) {
  const c = A.char;
  if (!c) return null;
  const names = (PLAN[kind] || []).filter((n) => c.clips[n] || (c.extras || []).includes(n));
  if (!names.length) return null;
  const name = names[Math.floor(Math.random() * names.length)];
  return c.clips[name] || (await loadMotion(c, name)) ? name : null;
}

function resize() {
  if (!A.renderer) return;
  const c = document.getElementById("avatar");
  const w = c.clientWidth || window.innerWidth, h = c.clientHeight || window.innerHeight;
  A.renderer.setSize(w, h, false);
  A.camera.aspect = w / h;
  A.camera.updateProjectionMatrix();
}

/* ------------------------------------------------------------------ motion capture playback */
function pick(kind) {
  const have = (PLAN[kind] || []).filter((n) => A.char.clips[n]);
  if (!have.length) return null;
  if (kind === "talking") return have[A.talkPick % have.length];
  if (kind === "idle") return have[Math.floor(A.t / 15) % have.length];  // change idle every ~15 s
  return have[0];
}

/* A dance or catwalk he asked for: keeps playing (even while she talks, the mouth still moves) until it times
   out or he says stop. */
async function show(kind, seconds = 25) {
  const asked = (A.showAsk = (A.showAsk || 0) + 1);
  const name = await ensure(kind);
  if (!name || asked !== A.showAsk) return false;  // nothing to play, or he asked for something else meanwhile
  A.showName = name;
  A.showUntil = A.t + seconds;
  A.oneShotUntil = 0;
  return true;
}

async function react(kind) {  // a one-shot reaction (cheer, kiss...), loaded on first use
  const name = await ensure(kind);
  if (name && A.t >= A.showUntil) oneShot(name);
}

function play(name, { once = false, fade = 0.45, reps = 1 } = {}) {
  if (!name || (name === A.actionName && !once)) return;
  const next = A.char.mixer.clipAction(A.char.clips[name]);
  next.reset();
  if (once && reps > 1) next.setLoop(A.THREE.LoopRepeat, reps);
  else next.setLoop(once ? A.THREE.LoopOnce : A.THREE.LoopRepeat, Infinity);
  next.clampWhenFinished = once;
  next.enabled = true;
  next.setEffectiveWeight(1);
  if (A.action && A.action !== next) next.crossFadeFrom(A.action, fade, true);
  next.play();
  A.action = next;
  A.actionName = name;
}

function oneShot(name) {
  if (!A.char || !A.char.clips[name]) return;
  const d = A.char.clips[name].duration, reps = Math.max(1, Math.ceil(2.4 / d));  // short clips (a 0.5 s wave) repeat
  play(name, { once: true, fade: 0.35, reps });
  A.oneShotUntil = A.t + Math.max(0.5, d * reps - 0.3);
}

function wantedMotion() {
  if (A.t < A.showUntil && A.char.clips[A.showName]) return A.showName;  // a requested dance/catwalk
  if (A.t < A.oneShotUntil) return null;  // let a wave/nod finish
  const mood = A.t < A.emotionUntil ? A.emotion : "";
  switch (A.mode) {
    case "speaking": return mood === "sad" ? pick("sad") || pick("talking") : pick("talking");
    case "thinking": return pick("thinking");
    case "listening": return pick("listening");
    case "error": return pick("sad") || pick("idle");
    default: return (mood === "happy" && pick("happy")) || pick("idle");
  }
}

/* ------------------------------------------------------------------ hand-made fallback poses (VRM without motions) */
const REST = {
  leftUpperArm: [0.1, 0.05, -1.32], rightUpperArm: [0.1, -0.05, 1.32],
  leftLowerArm: [0, -0.35, 0.15], rightLowerArm: [0, 0.35, -0.15], leftHand: [0, 0, 0.15], rightHand: [0, 0, -0.15],
};
const POSES = {
  sleeping: {}, speaking: { spine: [0.02, 0, 0] }, listening: { spine: [0.06, 0, 0] },
  thinking: { rightUpperArm: [-0.25, 0.35, 0.95], rightLowerArm: [0, 2.25, 0.15], rightHand: [0.2, 0, -0.3],
              head: [-0.16, 0.18, 0.08], neck: [-0.05, 0.05, 0] },
  confirm: {}, error: { head: [0.22, 0, 0], neck: [0.1, 0, 0], spine: [0.05, 0, 0] },
};
const WAVE = { rightUpperArm: [0, 0, -0.1], rightLowerArm: [0, 0, -1.6], rightHand: [0, 0, 0] };  // forearm up
const BONES = ["spine", "chest", "neck", "head", "leftUpperArm", "rightUpperArm", "leftLowerArm", "rightLowerArm",
               "leftHand", "rightHand"];

function handPose(dt) {
  const c = A.char, k = 1 - Math.exp(-dt * 6);
  const pose = { ...REST, ...(POSES[A.mode] || {}) };
  if (A.t < A.waveUntil) {
    Object.assign(pose, WAVE);
    pose.rightLowerArm = [0, 0, WAVE.rightLowerArm[2] + Math.sin(A.t * 9) * 0.35];
  }
  const breath = Math.sin(A.t * 1.6), sway = Math.sin(A.t * 0.55);
  for (const name of BONES) {
    const bone = c.bone(name);
    if (!bone) continue;
    let [x, y, z] = pose[name] || [0, 0, 0];
    if (name === "spine") { x += breath * 0.012; z += sway * 0.012; }
    if (name === "chest") x += breath * 0.015;
    if (name === "head" && A.mode === "speaking") x += Math.sin(A.t * 3.1) * 0.025;
    bone.rotation.x += (x - bone.rotation.x) * k;
    bone.rotation.y += (y - bone.rotation.y) * k;
    bone.rotation.z += (z - bone.rotation.z) * k;
  }
  c.bone("hips").position.y = c.hipsY + Math.sin(A.t * 1.6) * 0.003;  // keep the rest height
}

/* ------------------------------------------------------------------ layered on top */
const _q = { tilt: null, euler: null };

function headLayer(dt) {
  // a curious tilt while listening or asking for a yes/no; characters without a face nod a little while talking
  const want = A.mode === "confirm" ? 0.2 : A.mode === "listening" ? 0.12 : 0;
  A.tilt += (want - A.tilt) * (1 - Math.exp(-dt * 4));
  const bob = A.char.kind !== "vrm" && A.mode === "speaking" ? Math.sin(A.t * 7) * 0.04 * Math.min(1, A.voice * 3) : 0;
  if (Math.abs(A.tilt) < 0.002 && !bob) return;
  const head = A.char.bone("head");
  if (!head) return;
  _q.tilt = _q.tilt || new A.THREE.Quaternion();
  _q.euler = _q.euler || new A.THREE.Euler();
  _q.euler.set(0.04 * (A.tilt / 0.2) + bob, 0, A.tilt);
  head.quaternion.multiply(_q.tilt.setFromEuler(_q.euler));
}

const EMOTIONS = ["happy", "sad", "surprised", "relaxed", "angry"];
const face = { happy: 0, sad: 0, surprised: 0, relaxed: 0, angry: 0, aa: 0, oh: 0, ih: 0 };

function applyFace(dt) {
  const em = A.char.vrm && A.char.vrm.expressionManager;
  if (!em) return;
  const want = { happy: 0, sad: 0, surprised: 0, relaxed: 0, angry: 0 };
  const emotion = A.t < A.emotionUntil ? A.emotion
    : { listening: "relaxed", confirm: "surprised", error: "sad", sleeping: "relaxed" }[A.mode] || "neutral";
  if (want[emotion] !== undefined) want[emotion] = emotion === "surprised" || emotion === "relaxed" ? 0.3 : 0.6;
  if (A.t < A.surpriseUntil) { want.surprised = 0.75; want.relaxed = 0; }
  const k = 1 - Math.exp(-dt * 5);
  // models name some expressions their own way ("Surprised"): match case-insensitively, once per character
  if (!A.char.exprNames) {
    A.char.exprNames = {};
    for (const x of em.expressions) A.char.exprNames[x.expressionName.toLowerCase()] = x.expressionName;
  }
  for (const e of EMOTIONS) {
    face[e] += (want[e] - face[e]) * k;
    if (A.char.exprNames[e]) em.setValue(A.char.exprNames[e], face[e]);
  }

  // lip-sync from her real voice level, shaped into a few mouth shapes
  const v = A.mode === "speaking" ? Math.min(1, A.voice * 2.2) : 0;
  const wob = 0.5 + 0.5 * Math.sin(A.t * 13);
  const lk = 1 - Math.exp(-dt * 18);
  face.aa += (v * (0.55 + 0.45 * wob) - face.aa) * lk;
  face.oh += (v * 0.35 * (1 - wob) - face.oh) * lk;
  face.ih += (v * 0.2 * wob - face.ih) * lk;
  em.setValue("aa", face.aa * 0.9); em.setValue("oh", face.oh); em.setValue("ih", face.ih);

  // during a kiss: puckered lips ("ou") and a soft smile, over whatever she is saying
  const kissing = (A.actionName === "kiss" || A.actionName === "blowkiss") && A.t < A.oneShotUntil;
  face.ou = (face.ou || 0) + ((kissing ? 0.7 : 0) - (face.ou || 0)) * (1 - Math.exp(-dt * 8));
  em.setValue("ou", face.ou);
  if (kissing && A.char.exprNames.relaxed) em.setValue(A.char.exprNames.relaxed, Math.max(face.relaxed, 0.5));

  // blink every 2-6 s (not during a broad smile: the happy face already closes the eyes)
  if (A.t > A.blinkAt) A.blinkAt = A.t + 2 + Math.random() * 4;
  const phase = A.blinkAt - A.t;
  em.setValue("blink", face.happy > 0.45 ? 0 : phase < 0.14 ? Math.sin((phase / 0.14) * Math.PI) : 0);
}

function applyLook() {
  // eyes follow the mouse; while thinking she looks up and away (VRM only: the others have painted eyes)
  if (A.mode === "thinking") A.lookTarget.position.set(0.9, 2.3, 2);
  else A.lookTarget.position.set(2.0 + A.mouse.x * 1.4, 1.3 + A.mouse.y * 0.7, 5.2);
}

/* ------------------------------------------------------------------ loop */
function frame(now) {
  if (!A.on) return;
  A.raf = requestAnimationFrame(frame);
  if (now - A.last < 1000 / 30) return;  // 30 fps is plenty
  const dt = Math.min(0.1, (now - A.last) / 1000);
  A.last = now;
  A.t += dt;
  const c = A.char;
  if (!c) return;
  if (Object.keys(c.clips).length) {
    play(wantedMotion());
    c.mixer.update(dt);
  } else if (c.kind === "vrm") {
    handPose(dt);
  }
  headLayer(dt);
  applyFace(dt);
  applyLook();
  c.update(dt);
  A.renderer.render(A.scene, A.camera);
}

/* ------------------------------------------------------------------ Karen's page events */
const MOOD = [
  [/\b(sorry|unfortunately|can't|cannot|couldn't|failed|unable|not able|no luck|error)\b/i, "sad"],
  [/\b(wow|whoa|really\?|no way|amazing)\b|!{2,}/i, "surprised"],
  [/\b(done|great|sure|perfect|awesome|happy|glad|good news|congrat|nice|sent|ready|here you go|hello|hi)\b|!/i, "happy"],
  [/\?\s*$/, "relaxed"],
];

/* Romantic reactions: never for a character with romance: false (Nezuko), never in work mode. */
function romanceOn() {
  return !!(A.char && A.char.romance && window.karenMode !== "work");
}

const ROMANCE = /\b(love you|my love|kiss(es|ed|ing)?|miss(ed)? you|sweetheart|darling|my sweet(est)?|honey|babe)\b/i;

function hook() {
  const k = window.karen;
  if (!k) return setTimeout(hook, 100);
  const wrap = (name, after) => {
    const orig = k[name];
    k[name] = function (...args) {
      const r = orig.apply(this, args);
      try { after(...args); } catch (e) { /* never break the page */ }
      return r;
    };
  };
  wrap("state", (mode) => {
    if (mode === "listening" && A.mode === "sleeping") A.surpriseUntil = A.t + 0.6;  // she heard "Karen"
    if (mode === "speaking" && A.mode !== "speaking") A.talkPick++;
    A.mode = mode;
  });
  wrap("level", (mic, voice) => { A.voice = voice || 0; });
  wrap("message", (role, text) => {
    text = text || "";
    if (role === "you") {  // his request, spoken or typed
      if (/\bstop (dancing|it|that|walking)\b|\benough dancing\b/i.test(text)) { A.showUntil = 0; A.showAsk++; }
      else if (/\b(dance|dancing|show me (your|some) moves)\b/i.test(text)) show("dance");
      else if (/\b(cat ?walk|model walk|walk like a model|ramp walk)\b/i.test(text)) show("catwalk", 15);
      else if (/\b(kiss(es|ing)?|mwah|muah)\b/i.test(text) && romanceOn()) {
        A.showUntil = 0;  // a kiss interrupts a dance
        A.kissedAt = A.t;
        react("kiss");  // right away, while she answers (not after)
      }
    } else if (role === "karen") {
      const hit = MOOD.find(([re]) => re.test(text));
      A.emotion = hit ? hit[1] : "neutral";
      A.emotionUntil = A.t + Math.min(12, 3 + text.length / 18);  // about as long as she's talking
      A.romance = ROMANCE.test(text);
    } else if (role === "error") { A.emotion = "sad"; A.emotionUntil = A.t + 4; }
  });
  wrap("done", () => {  // after she finishes an answer: a small reaction now and then
    if (!A.on || !A.char || A.t < A.showUntil) return;
    // not twice for one "kiss me"; in "both" mode only now and then
    const often = window.karenMode === "both" ? Math.random() < 0.3 : true;
    if (A.romance && romanceOn() && often && A.t - (A.kissedAt || -99) > 8) react("romance");
    else if (A.emotion === "happy") { if (Math.random() < 0.35) react("cheer"); else oneShot("nod"); }
  });
  wrap("config", (c) => { if (c && c.avatar) setCharacter(c.avatar); });
}

window.addEventListener("resize", resize);
window.addEventListener("mousemove", (e) => {
  A.mouse.x = (e.clientX / window.innerWidth) * 2 - 1;
  A.mouse.y = 1 - (e.clientY / window.innerHeight) * 2;
});
document.getElementById("btn-avatar").addEventListener("click", () => toggle());
const picker = document.getElementById("set-avatar");
if (picker) {
  fetchJSON("avatar/characters.json", []).then((list) => { A.characters = A.characters.length ? A.characters : list; fillPicker(); });
  picker.addEventListener("change", () => {
    setCharacter(picker.value);
    if (window.pywebview && window.pywebview.api) window.pywebview.api.set_setting("avatar", picker.value);
  });
}
hook();
window.KarenAvatar = { toggle, setCharacter, state: A, oneShot, REST, POSES };  // for tuning from the dev console
