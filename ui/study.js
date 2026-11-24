/* The study window: a conversation with Mam. Everything said in study mode is mirrored here by Python
   (core/ui.py: window.study.message / state / done), and the study tools' results arrive inside her answer
   (window.study.visual / lesson, from main.study_show). On load the page asks for the conversation so far
   (api.study_state) and replays it.
   Visual specs (servers/study): {title, kind: mermaid|chart|math|html|image, mermaid, chart, math, html, image,
   points, made_by}. "html" visuals are written by an AI, so they run in a sandboxed frame with no network access. */
const $ = (id) => document.getElementById(id);
const api = () => (window.pywebview && window.pywebview.api) || null;
const S = { turn: null, n: 0, saveTimer: 0, replaying: false, mode: "idle" };

mermaid.initialize({
  startOnLoad: false, securityLevel: "strict", theme: "base", fontFamily: "Inter, Segoe UI, sans-serif",
  themeVariables: {
    background: "#000000", primaryColor: "#1a1606", primaryTextColor: "#fdf8e1", primaryBorderColor: "#facc15",
    lineColor: "#facc15", secondaryColor: "#2a1f05", tertiaryColor: "#111111", textColor: "#fdf8e1",
    mainBkg: "#1a1606", nodeBorder: "#facc15", clusterBkg: "#0d0b04", edgeLabelBackground: "#000000",
    cScale0: "#facc15", cScale1: "#f59e0b", cScale2: "#fde047", cScale3: "#fb923c", cScale4: "#eab308",
    cScale5: "#fbbf24", cScale6: "#d97706", cScale7: "#fef08a", cScale8: "#ca8a04", cScale9: "#fdba74",
    cScale10: "#facc15", cScale11: "#f59e0b",
  },
});
Chart.defaults.color = "rgba(253,248,225,.75)";
Chart.defaults.borderColor = "rgba(250,204,21,.15)";
Chart.defaults.font.family = "Inter, Segoe UI, sans-serif";
const GOLD = ["#facc15", "#f59e0b", "#fde047", "#fb923c", "#eab308", "#fef08a", "#d97706"];
const STARTERS = ["Teach me Newton's laws", "Show me how photosynthesis works", "Make a formula sheet for kinematics",
                  "Draw a timeline of the French Revolution"];
const DOING = { visualize: "Drawing it…", teach: "Finding videos and websites…", play_video: "Finding the video…" };

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}
function ask(text) { const a = api(); if (a) a.send_text(text); else demoAsk(text); }
function openLink(url) { const a = api(); if (a) a.open_link(url); else window.open(url, "_blank", "noopener"); }
function scrollDown(force) {
  const t = $("thread");
  if (force || t.scrollHeight - t.scrollTop - t.clientHeight < 260) t.scrollTop = t.scrollHeight;
}
function chips(box, list) {
  list.forEach((c) => { const b = el("button", "chip", c); b.onclick = () => ask(c); box.appendChild(b); });
}

/* conversation: one row per message; Mam's row holds her words, then any visuals or lessons, then follow-ups */
function you(text) {
  $("welcome").hidden = true;
  const row = el("div", "row you"); row.appendChild(el("div", "bubble", text));
  $("thread").appendChild(row);
  S.turn = null;
  mamTurn().classList.add("waiting");
  scrollDown(true);
}
function mamTurn() {
  if (S.turn) return S.turn;
  $("welcome").hidden = true;
  const row = el("div", "row mam"), face = el("div", "mam-face", "M"), body = el("div", "body");
  const say = el("div", "say"), dots = el("div", "typing"), doing = el("div", "doing"), blocks = el("div", "blocks");
  dots.innerHTML = "<i></i><i></i><i></i>";
  body.append(dots, doing, say, blocks);
  row.append(face, body);
  $("thread").appendChild(row);
  S.turn = row;
  return row;
}
function words(box, text) {  // her spoken answer: paragraphs, numbered or bulleted lines and **bold**, all as text nodes
  box.innerHTML = "";
  let list = null;
  text.split(/\n+/).forEach((line) => {
    const item = /^\s*(?:[-*•]|\d+[.)])\s+(.*)/.exec(line);
    if (item) {
      if (!list) { list = el(/^\s*\d/.test(line) ? "ol" : "ul"); box.appendChild(list); }
      list.appendChild(rich(el("li"), item[1]));
    } else if (line.trim()) {
      list = null;
      box.appendChild(rich(el("p"), line));
    }
  });
}
function rich(node, text) {
  text.split(/(\*\*[^*]+\*\*)/).forEach((part) => {
    node.appendChild(part.startsWith("**") && part.endsWith("**") ? el("b", "", part.slice(2, -2)) : document.createTextNode(part));
  });
  return node;
}
function mamSays(text, meta) {
  if (S.turn && S.turn.dataset.text) S.turn = null;  // a reminder or a second answer: a new row
  const row = mamTurn();
  row.classList.remove("waiting");
  row.querySelector(".doing").textContent = "";
  words(row.querySelector(".say"), text);
  row.dataset.text = text;
  if (meta && meta !== "mode switch") row.title = meta;
  finishTurn(row);
  scrollDown();
}
function finishTurn(row) {  // under a finished answer: save to notes, and follow-ups if she drew or taught something
  row.querySelectorAll(".after").forEach((x) => x.remove());
  const after = el("div", "after"), save = el("button", "link", "Save to notes");
  save.onclick = () => { saveToNotes(row); save.textContent = "Saved ✓"; };
  after.appendChild(save);
  const topic = row.dataset.topic;
  if (topic) {
    const box = el("div", "chips");
    chips(box, row.dataset.kind === "lesson"
      ? [`Quiz me on ${topic}`, "Explain the hardest step again", `Draw a mind map of ${topic}`]
      : [`Explain ${topic} step by step`, `Quiz me on ${topic}`, `Make a detailed explainer of ${topic}`]);
    after.appendChild(box);
  }
  row.querySelector(".body").appendChild(after);
}

/* visuals inside her answer */
async function draw(stage, v, big) {
  stage.innerHTML = "";
  try {
    if (v.kind === "mermaid") {
      const id = "m" + ++S.n;
      try {
        await mermaid.parse(v.mermaid);  // throws on bad code without leaving mermaid's error picture on the page
        stage.innerHTML = (await mermaid.render(id, v.mermaid)).svg;
      } finally {
        document.querySelectorAll(`#d${id}, body > #${id}`).forEach((x) => x.remove());
      }
    } else if (v.kind === "chart") {
      const canvas = el("canvas"); stage.appendChild(canvas);
      const cfg = JSON.parse(JSON.stringify(v.chart));  // Chart.js changes its config; keep the spec for "expand"
      (cfg.data.datasets || []).forEach((d, i) => {
        const many = cfg.type === "pie" || cfg.type === "doughnut" || cfg.type === "polarArea";
        d.backgroundColor = d.backgroundColor || (many ? GOLD : GOLD[i % GOLD.length] + "cc");
        d.borderColor = d.borderColor || (many ? "#000" : GOLD[i % GOLD.length]);
      });
      new Chart(canvas, cfg);
    } else if (v.kind === "math") {
      const box = el("div", "math"); stage.appendChild(box);
      (v.math || []).forEach((tex) => { const d = el("div"); box.appendChild(d);
        katex.render(tex, d, { displayMode: true, throwOnError: false }); });
    } else if (v.kind === "html") {
      const f = el("iframe");
      f.setAttribute("sandbox", "allow-scripts");  // own origin: no access to Karen, her files or this page
      const csp = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; font-src data:";
      f.srcdoc = `<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${csp}">` +
                 `<style>html,body{margin:0;background:#000;color:#fdf8e1;font-family:Segoe UI,sans-serif}</style>` + v.html;
      if (big) f.className = "big";
      stage.appendChild(f);
    } else if (v.kind === "image") {
      const img = el("img"); img.src = v.image; img.alt = v.title || ""; stage.appendChild(img);
    } else {
      stage.appendChild(el("p", "hint", "Unknown visual."));
    }
  } catch (e) {
    stage.appendChild(el("p", "hint", "This one didn't draw. Ask Mam to try again."));
    stage.appendChild(el("pre", "", String((e && e.message) || e).slice(0, 400)));
  }
}
function addVisual(v) {
  const row = mamTurn(), card = el("div", "card visual");
  const head = el("div", "card-head");
  head.append(el("b", "", v.title || "Visual"), el("small", "", v.made_by ? "drawn by " + v.made_by : ""));
  const grow = el("button", "icon", "⤢"); grow.title = "Bigger"; grow.onclick = () => full(v);
  head.appendChild(grow);
  const stage = el("div", "stage");
  card.append(head, stage);
  if ((v.points || []).length) {
    const ul = el("ul", "points"); v.points.forEach((p) => ul.appendChild(el("li", "", p))); card.appendChild(ul);
  }
  row.querySelector(".blocks").appendChild(card);
  row.dataset.topic = row.dataset.topic || v.title || "";
  row.dataset.kind = row.dataset.kind || "visual";
  draw(stage, v).then(() => scrollDown());
  if (row.dataset.text) finishTurn(row);
}
function full(v) {
  $("full").classList.add("open");
  draw($("full-stage"), v, true);
}
function closeFull() { $("full").classList.remove("open"); $("full-stage").innerHTML = ""; }
$("full-close").onclick = closeFull;
$("full").onclick = (e) => { if (e.target === $("full")) closeFull(); };

/* lessons inside her answer: a player, more videos, the steps, websites and a quick quiz */
function ytId(url) { const m = /(?:v=|youtu\.be\/|embed\/|shorts\/)([\w-]{11})/.exec(url || ""); return m ? m[1] : ""; }
function play(player, video, auto) {
  const id = ytId(video.url);
  if (!id) { openLink(video.url); return; }
  player.innerHTML = "";
  const f = el("iframe");
  f.src = `https://www.youtube-nocookie.com/embed/${id}?autoplay=${auto ? 1 : 0}&rel=0&start=${Math.max(0, video.start | 0)}`;
  f.allow = "autoplay; encrypted-media; picture-in-picture; fullscreen"; f.allowFullscreen = true;
  player.appendChild(f);
}
function tile(title, sub, img, onclick) {
  const c = el("button", "tile");
  if (img) { const i = el("img"); i.src = img; i.alt = ""; i.loading = "lazy"; c.appendChild(i); }
  c.appendChild(el("b", "", title));
  if (sub) c.appendChild(el("small", "", sub));
  c.onclick = onclick; return c;
}
function section(card, name) { card.appendChild(el("h4", "", name)); }
function addLesson(l) {
  const row = mamTurn(), card = el("div", "card lesson");
  const head = el("div", "card-head"); head.append(el("b", "", "Lesson: " + (l.topic || "")), el("small", "", l.level || ""));
  card.appendChild(head);
  if (l.intro) card.appendChild(el("p", "intro", l.intro));
  const videos = l.videos || [];
  if (videos.length) {
    const player = el("div", "player"); card.appendChild(player);
    play(player, videos[0], !S.replaying && l.autoplay !== false);
    if (videos.length > 1) {
      const strip = el("div", "strip");
      videos.forEach((v) => { const id = ytId(v.url);
        strip.appendChild(tile(v.title, [v.channel, v.duration].filter(Boolean).join(" · "),
          id ? `https://i.ytimg.com/vi/${id}/mqdefault.jpg` : "", () => play(player, v, true))); });
      card.appendChild(strip);
    }
  }
  if ((l.steps || []).length) {
    section(card, "Step by step");
    const ol = el("ol", "steps"); l.steps.forEach((s) => ol.appendChild(el("li", "", s))); card.appendChild(ol);
  }
  if ((l.sites || []).length) {
    section(card, "Read more");
    const sites = el("div", "sites");
    l.sites.forEach((s) => sites.appendChild(tile(s.title, s.snippet || s.url, "", () => openLink(s.url))));
    card.appendChild(sites);
  }
  if ((l.quiz || []).length) {
    section(card, "Quick quiz");
    l.quiz.forEach((q) => {
      const box = el("div", "q"); box.appendChild(el("p", "", q.q));
      (q.options || []).forEach((o, i) => {
        const b = el("button", "", o);
        b.onclick = () => { b.classList.add(i === q.answer ? "right" : "wrong");
          if (i !== q.answer) box.querySelectorAll("button")[q.answer]?.classList.add("right"); };
        box.appendChild(b);
      });
      card.appendChild(box);
    });
  }
  row.querySelector(".blocks").appendChild(card);
  row.dataset.topic = l.topic || row.dataset.topic || "";
  row.dataset.kind = "lesson";
  if (row.dataset.text) finishTurn(row);
  scrollDown();
}

/* what she is doing, in the header and in her waiting bubble */
const STATUS = { listening: "listening…", thinking: "thinking…", speaking: "speaking", sleeping: "your study teacher",
                 confirm: "waiting for your yes or no", error: "something went wrong" };
function setState(mode) {
  S.mode = mode;
  $("status").textContent = STATUS[mode] || "your study teacher";
  document.body.dataset.mode = mode;
  $("ask-mic").classList.toggle("live", mode === "listening");
}
function toolLine(name) {
  const row = mamTurn();
  row.querySelector(".doing").textContent = DOING[name] || name.replace(/_/g, " ") + "…";
}

/* notes drawer: saved a second after he stops typing */
$("n-date").textContent = new Date().toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
function saveNotesSoon() {
  clearTimeout(S.saveTimer); $("n-saved").textContent = "…";
  S.saveTimer = setTimeout(async () => {
    const a = api(); if (!a) { $("n-saved").textContent = "not saved (demo)"; return; }
    await a.study_save_notes($("n-text").value); $("n-saved").textContent = "saved";
  }, 900);
}
function saveToNotes(row) {
  const parts = [`## ${row.dataset.topic || new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`,
                 row.dataset.text || ""];
  row.querySelectorAll(".points li, .steps li").forEach((li) => parts.push("- " + li.textContent));
  const t = $("n-text");
  t.value = (t.value.trim() ? t.value.trimEnd() + "\n\n" : "") + parts.filter(Boolean).join("\n") + "\n";
  saveNotesSoon();
}
$("n-text").addEventListener("input", saveNotesSoon);
$("btn-notes").onclick = () => $("notes").classList.toggle("open");
$("n-close").onclick = () => $("notes").classList.remove("open");
$("btn-exit").onclick = () => { const a = api(); if (a) a.study_mode(false); };

/* composer */
$("ask").onsubmit = (e) => {
  e.preventDefault(); const text = $("ask-text").value.trim(); if (!text) return;
  ask(text); $("ask-text").value = "";
};
$("ask-mic").onclick = () => { const a = api(); if (a) a.talk(); };
document.addEventListener("keydown", (e) => { if (e.key === "Escape") { closeFull(); $("notes").classList.remove("open"); } });
chips($("starters"), STARTERS);

window.study = {
  message(role, text, meta) {
    if (role === "you") you(text);
    else if (role === "karen" && meta === "mode switch" && !S.turn) return;  // "Study mode on": the welcome says it
    else if (role === "karen") mamSays(text, meta);
    else if (role === "system") toolLine(text.replace(/ /g, "_"));
    else if (role === "error") { const row = mamTurn(); row.classList.remove("waiting"); row.querySelector(".say").appendChild(el("p", "err", text)); }
  },
  state(mode) { setState(mode); },
  done() { setState("sleeping"); if (S.turn) S.turn.classList.remove("waiting"); if (S.turn?.dataset.text) S.turn = null; },
  visual(v) { addVisual(v); },
  lesson(l) { addLesson(l); },
};

async function load() {
  const a = api(); if (!a) return demo();
  const st = await a.study_state();
  if (st.notes) $("n-text").value = st.notes;
  S.replaying = true;
  try {
    for (const [method, args] of st.thread || []) if (method !== "state") window.study[method]?.(...args);
  } finally {
    S.replaying = false;
  }
  if (S.turn) S.turn.classList.remove("waiting");
  scrollDown(true);
}
window.addEventListener("pywebviewready", load);
setTimeout(() => { if (!api()) demo(); }, 1200);

function demo() {  // in a normal browser: a sample conversation, so the layout can be checked
  if (S.demo) return; S.demo = true;
  $("status").textContent = "demo, not connected";
  demoAsk("Show me how photosynthesis works");
}
function demoAsk(text) {
  window.study.message("you", text, "typed");
  window.study.message("system", "visualize", "{}");
  setTimeout(() => {
    window.study.visual({ id: "d" + Date.now(), title: "Photosynthesis", kind: "mermaid", made_by: "demo",
      mermaid: "flowchart LR\n  S[Sunlight] --> C(Chloroplast)\n  W[Water] --> C\n  A[CO2] --> C\n  C --> G[Glucose]\n  C --> O[Oxygen]",
      points: ["Light energy is captured by chlorophyll.", "Water is split; oxygen is released.", "CO2 is fixed into glucose."] });
    window.study.message("karen", "Here's how it works. Plants take in **sunlight**, water and carbon dioxide, and the chloroplast turns them into glucose and oxygen.\n1. Light is captured.\n2. Water is split.\n3. Sugar is built.", "demo");
    window.study.done();
  }, 700);
}
