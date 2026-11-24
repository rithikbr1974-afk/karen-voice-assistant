// Karen Island's panel. Python -> page: window.island.<event>(...) (core/island.py Island.panel_call).
// Page -> Python: window.pywebview.api (main.Controls).
const $ = (id) => document.getElementById(id);
const api = () => (window.pywebview && window.pywebview.api) || null;
const svg = (d) => `<svg viewBox="0 0 24 24"><path d="${d}"/></svg>`;
const ICON = {  // 24x24 line icons, all in one neutral colour
  music: "M9 18V5l11-2v13M9 18a3 3 0 1 1-6 0 3 3 0 0 1 6 0Zm11-2a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z",
  map: "M12 21s-7-6.2-7-11.5a7 7 0 0 1 14 0C19 14.8 12 21 12 21Zm0-8.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z",
  sun: "M12 2.5v2m0 15v2M4.6 4.6 6 6m12 12 1.4 1.4M2.5 12h2m15 0h2M4.6 19.4 6 18M18 6l1.4-1.4M12 16.5a4.5 4.5 0 1 0 0-9 4.5 4.5 0 0 0 0 9Z",
  timer: "M10 2h4m-2 12 3-3m-3 11a8 8 0 1 0 0-16 8 8 0 0 0 0 16Z",
  bell: "M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9m4.3 13a1.94 1.94 0 0 0 3.4 0",
  mic: "M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3Zm7 9a7 7 0 0 1-14 0m7 7v3",
  micOff: "M3 3l18 18M9 9v3a3 3 0 0 0 5.1 2.1M15 9.3V6a3 3 0 0 0-5.7-1.3M19 12a7 7 0 0 1-1.1 3.8M5 12a7 7 0 0 0 11.2 5.6M12 19v3",
  screen: "M3 4h18v12H3zM8 20h8m-4-4v4",
  notes: "M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9l-6-6Zm0 0v6h6M8 13h8m-8 4h5",
  shot: "M3 7V5a2 2 0 0 1 2-2h2m10 0h2a2 2 0 0 1 2 2v2m0 10v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2m6-5a3 3 0 1 0 6 0 3 3 0 0 0-6 0",
  mail: "M4 5h16a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1Zm-1 2 9 6 9-6",
  apps: "M4 4h6v6H4zm10 0h6v6h-6zM4 14h6v6H4zm10 0h6v6h-6z",
  todo: "M10 6h10M10 12h10M10 18h10M4 6l1 1 2-2M4 12l1 1 2-2M4 18l1 1 2-2",
  gear: "M12 3v3m0 12v3M3 12h3m12 0h3M5.6 5.6l2.1 2.1m8.6 8.6 2.1 2.1M5.6 18.4l2.1-2.1m8.6-8.6 2.1-2.1",
  check: "M5 12.5l4.5 4.5L19 7.5",
  vol: "M4 9h4l5-4v14l-5-4H4V9Zm12.5-.5a5 5 0 0 1 0 7M19 6a8.5 8.5 0 0 1 0 12",
  volLow: "M4 9h4l5-4v14l-5-4H4V9Zm12.5-.5a5 5 0 0 1 0 7",
  volOff: "M4 9h4l5-4v14l-5-4H4V9Zm13 1 5 5m0-5-5 5",
};
const CHIPS = [  // [label, text, send now?, icon]  prefilled ones wait for the rest of the request
  ["Music", "Play ", false, "music"], ["Weather", "What's the weather?", true, "sun"],
  ["Timer", "Set a timer for ", false, "timer"], ["Remind me", "Remind me to ", false, "bell"],
  ["Map", "Show me the map of ", false, "map"], ["Emails", "Any important emails today?", true, "mail"],
  ["To-do", "What's on my to-do list?", true, "todo"], ["Open app", "Open ", false, "apps"],
  ["Screenshot", "Take a screenshot", true, "shot"], ["Voice memo", "Start a voice recording", true, "mic"],
  ["Record screen", "Start a screen recording", true, "screen"], ["Meeting", "Start taking meeting notes", true, "notes"],
];
const APPS = [[/music\.youtube|cinhimbnkkaeohfgghhklpknlkffjgod/i, "YouTube Music"], [/spotify/i, "Spotify"],
  [/chrome/i, "Chrome"], [/edge/i, "Edge"], [/firefox/i, "Firefox"], [/zunemusic|media\.player/i, "Media Player"], [/vlc/i, "VLC"]];
const FILE_ACTS = {  // what to offer for dropped files, by kind
  doc: [["Summarise", "Summarise the attached file"], ["Key points", "List the key points of the attached file"],
    ["Make a PDF", "Convert the attached file to PDF"]],
  pdf: [["Summarise", "Summarise the attached PDF"], ["Key points", "List the key points of the attached PDF"],
    ["Ask about it", "About the attached PDF: "]],
  image: [["Describe", "Describe the attached picture"], ["Read the text", "Read the text in the attached picture"],
    ["Make a PDF", "Put the attached pictures into a PDF"]],
  media: [["Transcribe", "Transcribe the attached file"], ["Summarise", "Transcribe and summarise the attached file"]],
  other: [["What is it?", "What is the attached file?"]],
};
let size = [440, 0], playing = false, track = null, dragging = false, typingEl = null, toolEl = null;
let maxH = 600;  // the tallest card that fits on his screen (Python sends the real value: Island.max_height)

function fit() {  // ask Python for a card that fits the content (logical px; the card adds a 10 px margin)
  // measured at once, not in requestAnimationFrame: the panel is still hidden when it opens.
  // Everything must fit in maxH with the action tiles visible. In order: the conversation shrinks (it scrolls inside
  // itself, down to 96 px); then the tiles fold into one row of pills that scrolls sideways; then the volume row goes.
  const talk = $("talk"), body = document.body, budget = maxH - 22;
  const atEnd = talk.scrollHeight - talk.scrollTop - talk.clientHeight < 24;
  const others = () => $("app").scrollHeight - talk.offsetHeight;  // everything but the conversation
  const need = () => others() + (talk.childElementCount ? 96 : 0);
  body.classList.remove("compact", "tight");
  if (need() > budget) body.classList.add("compact");
  if (need() > budget) body.classList.add("tight");
  const room = Math.max(96, Math.min(300, budget - others()));
  if (talk.style.maxHeight !== `${room}px`) talk.style.maxHeight = `${room}px`;
  if (atEnd) talk.scrollTop = talk.scrollHeight;  // stay on the newest line
  edge();
  const w = body.classList.contains("mapmode") ? 600 : 440;
  const h = Math.min(maxH, $("app").scrollHeight + 22);
  if (api() && (w !== size[0] || Math.abs(h - size[1]) > 2)) {
    size = [w, h];
    api().island_resize(w, h);
  }
}

function send(text) {
  text = (text || "").trim();
  if (!text || !api()) return;
  api().send_text(text);
  $("ask").value = "";
}

function prefill(text) {  // a request that needs the rest typed: put it in the box, cursor at the end
  $("ask").value = text;
  $("ask").focus();
  $("ask").setSelectionRange(text.length, text.length);
}

$("chips").addEventListener("wheel", (e) => {  // the folded row: the mouse wheel scrolls it sideways
  if (!document.body.classList.contains("compact") || Math.abs(e.deltaX) > Math.abs(e.deltaY)) return;
  $("chips").scrollLeft += e.deltaY;
  e.preventDefault();
}, { passive: false });

function chips() {
  $("chips").innerHTML = "";
  CHIPS.forEach(([label, text, now, icon], i) => {
    const b = document.createElement("button");
    b.className = "tile";
    b.innerHTML = `${svg(ICON[icon])}<span></span>`;
    b.querySelector("span").textContent = label;
    b.title = now ? text : `${text}…`;
    b.style.animationDelay = `${30 + i * 15}ms`;
    b.onclick = () => (now ? send(text) : prefill(text));
    $("chips").appendChild(b);
  });
}

// ---------------------------------------------------------------- now playing
function clock(s) {
  s = Math.max(0, Math.floor(s));
  const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, ss = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

function setPlaying(on) {
  if (track && playing !== on) track = { ...track, pos: position(), at: Date.now() };  // freeze / restart the clock
  playing = on;
  $("ppIcon").setAttribute("d", on ? "M7 5h3.5v14H7V5Zm6.5 0H17v14h-3.5V5Z" : "M8 5v14l11-7L8 5Z");
  $("playPause").title = on ? "Pause" : "Play";
}

function position() {
  if (!track) return 0;
  return Math.min(track.dur, track.pos + (playing ? (Date.now() - track.at) / 1000 : 0));
}

function paint(p) {
  const pct = `${(100 * p / track.dur).toFixed(2)}%`;
  $("songPos").textContent = clock(p);
  $("songLeft").textContent = `-${clock(track.dur - p)}`;
  $("songBar").style.width = pct;
  $("songKnob").style.left = pct;
}

function tick() {  // the progress bar runs on its own between Python's updates
  $("songProg").hidden = !track;
  if (!track || $("song").hidden || dragging) return;
  paint(position());
}
setInterval(tick, 500);

function seekFrom(e) {
  const r = $("songTrack").getBoundingClientRect();
  return Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * track.dur;
}
$("songTrack").addEventListener("pointerdown", (e) => {
  if (!track) return;
  dragging = true;
  $("songTrack").classList.add("drag");
  $("songTrack").setPointerCapture(e.pointerId);
  paint(seekFrom(e));
});
$("songTrack").addEventListener("pointermove", (e) => { if (dragging) paint(seekFrom(e)); });
$("songTrack").addEventListener("pointerup", (e) => {
  if (!dragging) return;
  dragging = false;
  $("songTrack").classList.remove("drag");
  const to = seekFrom(e);
  track = { ...track, pos: to, at: Date.now() };
  paint(to);
  if (api()) api().island_seek(to);
});

function volume(v) {
  $("vol").hidden = !v;
  if (!v) return fit();
  const level = v.muted ? 0 : v.level;
  if (document.activeElement !== $("volRange")) $("volRange").value = level;
  $("volRange").style.setProperty("--v", `${level}%`);
  $("volVal").textContent = v.muted ? "Off" : level;
  $("volIcon").setAttribute("d", ICON[v.muted || !level ? "volOff" : level < 45 ? "volLow" : "vol"]);
  $("volMute").dataset.muted = v.muted ? "1" : "";
  fit();
}
$("volRange").addEventListener("input", () => {
  const level = +$("volRange").value;
  volume({ level, muted: false });
  if (api()) api().island_volume(level, null);
});
$("volMute").onclick = () => {
  const muted = !$("volMute").dataset.muted;
  volume({ level: +$("volRange").value || 30, muted });
  if (api()) api().island_volume(null, muted);
};

// ---------------------------------------------------------------- the conversation
function scrollTalk() { const t = $("talk"); t.scrollTop = t.scrollHeight; edge(); }
function edge() { const t = $("talk"); t.classList.toggle("more", t.scrollTop > 4); }  // fade only with lines above
$("talk").addEventListener("scroll", edge);

function typing(on) {
  if (on && !typingEl) {
    typingEl = document.createElement("div");
    typingEl.className = "typing";
    typingEl.innerHTML = "<i></i><i></i><i></i>";
    $("talk").appendChild(typingEl);
    scrollTalk();
    fit();
  } else if (!on && typingEl) {
    typingEl.remove();
    typingEl = null;
  }
}

function settleTool() {  // the tool she was using is finished: spinner -> tick
  if (!toolEl) return;
  toolEl.classList.remove("busy");
  toolEl.querySelector("svg").outerHTML = svg(ICON.check);
  toolEl = null;
}

function write(el, text) {  // her answer writes itself in, a few words at a time
  const words = text.split(/(\s+)/);
  let i = 0;
  const step = () => {
    i = Math.min(words.length, i + 3);
    el.textContent = words.slice(0, i).join("");
    scrollTalk();
    if (i < words.length) setTimeout(step, 45);
    else fit();
  };
  step();
}

function add(role, text) {
  if (!text) return;
  const talk = $("talk");
  if (role === "system") {  // "using weather": one quiet line with a spinner
    settleTool();
    toolEl = document.createElement("div");
    toolEl.className = "tool busy";
    toolEl.innerHTML = `${svg(ICON.gear)}<span></span>`;
    toolEl.querySelector("span").textContent = text.replace(/_/g, " ");
    talk.insertBefore(toolEl, typingEl);
  } else if (["you", "karen", "error"].includes(role)) {
    if (role !== "you") { typing(false); settleTool(); }
    const d = document.createElement("div");
    d.className = `msg ${role}`;
    talk.insertBefore(d, typingEl);
    if (role === "karen") write(d, text);
    else d.textContent = text.length > 240 ? `${text.slice(0, 240)}…` : text;  // a long paste stays one short line
  } else return;
  while (talk.children.length > 40) talk.firstChild.remove();  // it scrolls, so keep a good stretch of it
  scrollTalk();
  fit();
}

// ---------------------------------------------------------------- clipboard and dropped files
function kindOf(name) {
  const ext = (name.split(".").pop() || "").toLowerCase();
  if (ext === "pdf") return "pdf";
  if (/^(png|jpe?g|webp|gif|bmp|heic|tiff?)$/.test(ext)) return "image";
  if (/^(mp3|m4a|wav|ogg|opus|aac|flac|mp4|mov|mkv|webm|avi)$/.test(ext)) return "media";
  if (/^(docx?|txt|md|rtf|odt|pptx?|xlsx?|xlsm|csv)$/.test(ext)) return "doc";
  return "other";
}

function bytes(n) {
  return n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 1048576).toFixed(1)} MB`;
}

function files(list) {
  list = list || [];
  $("files").hidden = !list.length;
  document.body.classList.toggle("busy", !!list.length);
  $("fileList").innerHTML = "";
  $("fileActs").innerHTML = "";
  if (!list.length) return fit();
  $("filesLabel").textContent = list.length === 1 ? "Attached file" : `${list.length} attached files`;
  list.forEach((f) => {
    const row = document.createElement("div");
    row.className = "file";
    const ext = (f.name.split(".").pop() || "").slice(0, 4).toUpperCase();
    row.innerHTML = `<i class="ico"></i><div><b></b><span></span></div>
      <button class="x" title="Remove">${svg("M6 6l12 12M18 6 6 18")}</button>`;
    row.querySelector(".ico").textContent = ext;
    row.querySelector("b").textContent = f.name;
    row.querySelector("span").textContent = bytes(f.size || 0);
    row.querySelector("button").onclick = () => api() && api().remove_attachment(f.name);
    $("fileList").appendChild(row);
  });
  const kinds = [...new Set(list.map((f) => kindOf(f.name)))];
  (FILE_ACTS[kinds.length === 1 ? kinds[0] : "other"] || FILE_ACTS.other).forEach(([label, text]) => {
    const b = document.createElement("button");
    b.textContent = label;
    b.onclick = () => (text.endsWith(": ") ? prefill(text) : send(text));
    $("fileActs").appendChild(b);
  });
  fit();
}

function clip(info) {
  $("clip").hidden = !info;
  if (info) {
    $("clipLabel").textContent = info.link ? "Copied link" : `Copied text · ${info.chars.toLocaleString()} characters`;
    $("clipText").textContent = info.text;
    $("clip").querySelector("[data-clip=translate]").hidden = info.link;
  }
  fit();
}
document.querySelectorAll("[data-clip]").forEach((b) => {
  b.onclick = () => {
    if (api()) api().island_clip(b.dataset.clip);
    clip(null);
  };
});

// ---------------------------------------------------------------- Python -> page
window.island = {
  opened(why) {
    document.body.classList.add("open");
    if (why === "click") setTimeout(() => $("ask").focus(), 60);
    fit();
  },
  closed() {
    document.body.classList.remove("open", "mapmode");
    $("map").hidden = true;
    $("mapFrame").src = "about:blank";
  },
  state(s) {
    document.body.dataset.state = s || "sleeping";
    typing(s === "thinking");
    if (s !== "thinking") settleTool();
  },
  message(role, text) { add(role, text); },
  done() { typing(false); settleTool(); },
  confirm(q) { $("question").textContent = q; $("confirm").hidden = false; document.body.classList.add("asking"); fit(); },
  confirmClose() { $("confirm").hidden = true; document.body.classList.remove("asking"); fit(); },
  muted(on) {
    document.body.classList.toggle("muted", !!on);
    $("mute").title = on ? "Unmute Karen's microphone" : "Mute Karen's microphone";
  },
  meeting() {},
  attachments(list) { files(list); },
  clip(info) { clip(info); },
  volume(v) { volume(v); },
  limits(l) { if (l && l.maxh) { maxH = l.maxh; fit(); } },
  keys(k) {
    $("keyhint").hidden = !k.ask;
    $("keyhint").textContent = k.ask || "";
    if (k.talk) $("mic").title = `Talk (${k.talk})`;
  },
  music(song) {
    const has = !!(song && song.title);
    $("song").hidden = !has;
    if (has) {
      $("songTitle").textContent = song.title;
      $("songArtist").textContent = song.artist || "";
      const app = APPS.find(([re]) => re.test(song.app || ""));
      $("songApp").textContent = app ? app[1] : "Now playing";
      if ($("songImg").getAttribute("src") !== (song.art || "")) $("songImg").setAttribute("src", song.art || "");
      $("song").style.setProperty("--cover", song.art ? `url("${song.art}")` : "none");
      $("song").style.setProperty("--accent", (song.accent || [235, 235, 245]).join(","));
      if (!dragging) track = song.dur ? { pos: song.pos || 0, dur: song.dur, at: (song.at || Date.now() / 1000) * 1000 } : null;
    } else {
      track = null;
    }
    setPlaying(!!(song && song.playing));
    tick();
    fit();
  },
  map(url, title, link) {
    document.body.classList.add("mapmode");
    $("mapTitle").textContent = title || "Map";
    $("mapLink").dataset.url = link || url;
    $("mapFrame").src = url;
    $("map").hidden = false;
    fit();
  },
};

// ---------------------------------------------------------------- page -> Python
$("ask").addEventListener("keydown", (e) => {
  if (e.key === "Enter") send($("ask").value);
  if (e.key === "Escape") api() && api().island_close();
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") api() && api().island_close(); });
$("mic").onclick = () => { if (!api()) return; api().island_close(); api().talk(); };  // the pill shows ✕ waveform ✓
$("mute").onclick = () => api() && api().set_muted(!document.body.classList.contains("muted"));
$("big").onclick = () => api() && api().open_main();
$("yes").onclick = () => api() && api().confirm_answer(true);
$("no").onclick = () => api() && api().confirm_answer(false);
$("mapClose").onclick = () => api() && api().island_close();
$("mapLink").onclick = (e) => { e.preventDefault(); api() && api().open_link(e.currentTarget.dataset.url); };
document.querySelectorAll("[data-music]").forEach((b) => {
  b.onclick = () => {
    if (!api()) return;
    const action = b.dataset.music === "toggle" ? (playing ? "pause" : "resume") : b.dataset.music;
    api().island_music(action);
    if (b.dataset.music === "toggle") { setPlaying(!playing); tick(); }  // at once; Python confirms within 2 s
  };
});
window.addEventListener("blur", () => api() && api().island_blur());
window.addEventListener("pywebviewready", () => { chips(); api().island_ready(); });
setTimeout(() => {  // opened in a normal browser: a static preview (pywebview injects its bridge after load)
  if (window.pywebview) return;
  chips();
  document.body.classList.add("open");
  window.island.keys({ ask: "Ctrl Alt K", talk: "Ctrl Alt Space" });
  window.island.music({ title: "Aaruyire", artist: "A.R. Rahman, Chinmayi", app: "music.youtube.com", playing: true,
    pos: 62, dur: 309, at: Date.now() / 1000, accent: [251, 146, 60] });
  window.island.volume({ level: 62, muted: false });
  window.island.clip({ text: "Photosynthesis is the process by which green plants use sunlight, water and carbon dioxide to make glucose and oxygen…", link: false, chars: 1840 });
  add("you", "What's the weather?");
  add("system", "weather");
  add("karen", "It's 31 degrees and sunny in London, with a light breeze this evening.");
}, 1500);
