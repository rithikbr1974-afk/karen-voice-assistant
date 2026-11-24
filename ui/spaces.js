/* Spaces drawer: pages your Dots write (data/spaces) and the Dots themselves (specialist helpers).
   Uses app.js's $, api() and toast(). Python pushes Dot progress with window.karen.dots(status).
   Page text comes from the web through an AI, so the Markdown renderer escapes everything first and only
   makes http(s) links (opened in their browser) and page links (opened here). */
"use strict";

/* ------------------------------------------------------------------ safe Markdown */
const escHtml = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function mdInline(s) {
  const codes = [];
  s = s.replace(/`([^`]+)`/g, (_, c) => { codes.push(c); return `\u0000${codes.length - 1}\u0000`; });
  s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_, text, href) => {  // page names may contain spaces
    const h = href.trim().replace(/&amp;/g, "&");
    if (/^https?:\/\//i.test(h)) return `<a data-url="${escHtml(h)}" title="${escHtml(h)}">${text}</a>`;
    if (!/^[a-z]+:/i.test(h)) return `<a data-page="${escHtml(decodeURIComponentSafe(h))}">${text}</a>`;
    return text;  // javascript:, file: and other schemes stay plain text
  });
  s = s.replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, (_, pre, url) => `${pre}<a data-url="${url}" title="${url}">${url.replace(/^https?:\/\/(www\.)?/, "").slice(0, 48)}</a>`);
  s = s.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>").replace(/(^|\W)\*([^*\s][^*]*)\*/g, "$1<i>$2</i>").replace(/(^|\W)_([^_\s][^_]*)_(?=\W|$)/g, "$1<i>$2</i>");
  s = s.replace(/~~([^~]+)~~/g, "<s>$1</s>");
  return s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[i]}</code>`);
}
function decodeURIComponentSafe(s) { try { return decodeURIComponent(s); } catch (e) { return s; } }

function renderMd(src) {
  const lines = escHtml(src || "").replace(/\r/g, "").split("\n");
  const out = [];
  let i = 0;
  const isTableSep = (l) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(l);
  const cells = (l) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => mdInline(c.trim()));
  while (i < lines.length) {
    const l = lines[i];
    if (/^```/.test(l)) {
      const code = []; i++;
      while (i < lines.length && !/^```/.test(lines[i])) code.push(lines[i++]);
      i++; out.push(`<pre><code>${code.join("\n")}</code></pre>`); continue;
    }
    let m;
    if ((m = l.match(/^(#{1,6})\s+(.*)/))) { const n = Math.min(m[1].length + 1, 6); out.push(`<h${n}>${mdInline(m[2])}</h${n}>`); i++; continue; }
    if (/^\s*([-*_])\s*\1\s*\1[\s\1]*$/.test(l)) { out.push("<hr>"); i++; continue; }
    if (l.includes("|") && i + 1 < lines.length && isTableSep(lines[i + 1])) {
      const head = cells(l); i += 2; const rows = [];
      while (i < lines.length && lines[i].includes("|") && lines[i].trim()) rows.push(cells(lines[i++]));
      out.push(`<table><thead><tr>${head.map((c) => `<th>${c}</th>`).join("")}</tr></thead><tbody>` +
        rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("") + "</tbody></table>");
      continue;
    }
    if (/^&gt;\s?/.test(l)) {
      const q = [];
      while (i < lines.length && /^&gt;\s?/.test(lines[i])) q.push(lines[i++].replace(/^&gt;\s?/, ""));
      out.push(`<blockquote>${renderMdEscaped(q)}</blockquote>`); continue;
    }
    if (/^\s*([-*+]|\d+[.)])\s+/.test(l)) {
      const ordered = /^\s*\d/.test(l), items = [];
      while (i < lines.length && /^\s*([-*+]|\d+[.)])\s+/.test(lines[i])) {
        let item = lines[i++].replace(/^\s*([-*+]|\d+[.)])\s+/, "");
        item = item.replace(/^\[( |x)\]\s*/i, (_, x) => (x.trim() ? "☑ " : "☐ "));
        items.push(`<li>${mdInline(item)}</li>`);
      }
      out.push(ordered ? `<ol>${items.join("")}</ol>` : `<ul>${items.join("")}</ul>`); continue;
    }
    if (!l.trim()) { i++; continue; }
    const para = [];
    while (i < lines.length && lines[i].trim() && !/^(#{1,6}\s|```|&gt;|\s*([-*+]|\d+[.)])\s+)/.test(lines[i])) para.push(lines[i++]);
    if (!para.length) para.push(lines[i++]);
    out.push(`<p>${mdInline(para.join(" "))}</p>`);
  }
  return out.join("\n");
}
function renderMdEscaped(lines) {  // quoted lines are already escaped: unescape once, render again (escapes again)
  const raw = lines.join("\n").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, "&");
  return renderMd(raw);
}

/* ------------------------------------------------------------------ talking to Python (or demo data) */
const SP = { pages: [], page: null, text: "", editing: false, isNew: false, dots: [], abilities: [], jobs: [],
             running: [], recent: [], dotEdit: null, trashArm: 0, delArm: 0 };

async function call(name, ...args) {
  const real = window.pywebview && window.pywebview.api && window.pywebview.api[name];
  const r = real ? await window.pywebview.api[name](...args) : DemoSpaces[name] && DemoSpaces[name](...args);
  if (r && r.error) { toast("warn", r.error); return null; }
  return r;
}

/* ------------------------------------------------------------------ drawer */
$("btn-spaces").onclick = () => { const open = $("spaces").classList.toggle("open"); if (open) refresh(); };
$("close-spaces").onclick = () => $("spaces").classList.remove("open");
document.querySelectorAll(".sp-tabs button").forEach((b) => {
  b.onclick = () => {
    document.querySelectorAll(".sp-tabs button").forEach((x) => x.classList.toggle("on", x === b));
    document.querySelectorAll(".sp-tab").forEach((t) => t.classList.toggle("on", t.id === "tab-" + b.dataset.tab));
  };
});

async function refresh() {
  SP.pages = (await call("spaces_list")) || [];
  drawTree();
  applyDots(await call("dots_state"));
}

/* ------------------------------------------------------------------ pages */
function drawTree() {
  const q = $("sp-search").value.trim().toLowerCase();
  const groups = {};
  SP.pages.filter((p) => !q || p.name.toLowerCase().includes(q)).forEach((p) => {
    const [space, ...rest] = p.name.split("/");
    (groups[space] = groups[space] || []).push({ ...p, short: rest.join(" / ") });
  });
  const tree = $("sp-tree");
  tree.innerHTML = Object.keys(groups).length ? "" : `<p class="hint">${q ? "No pages match." : "No pages yet."}</p>`;
  Object.keys(groups).sort().forEach((space) => {
    const h = document.createElement("div"); h.className = "sp-space"; h.textContent = space; tree.appendChild(h);
    groups[space].sort((a, b) => b.edited.localeCompare(a.edited)).forEach((p) => {
      const el = document.createElement("button");
      el.className = "sp-page" + (SP.page === p.name ? " on" : "");
      el.innerHTML = `<span>${escHtml(p.short)}</span><small>${p.words} words · ${escHtml(p.edited.slice(5, 16).replace("T", " "))}</small>`;
      el.onclick = () => openPage(p.name);
      tree.appendChild(el);
    });
  });
}
$("sp-search").oninput = drawTree;

async function openPage(name) {
  if (SP.editing && SP.text !== $("sp-editor").value && !window.confirm("Discard your unsaved changes?")) return;
  const r = await call("spaces_read", name);
  if (!r) return;
  Object.assign(SP, { page: r.name, text: r.text, editing: false, isNew: false, trashArm: 0 });
  showPage();
  drawTree();
}

function showPage() {
  $("sp-title").textContent = SP.isNew ? "" : SP.page ? SP.page.split("/").slice(1).join(" / ") : "No page open";
  $("sp-title").title = SP.page || "";
  $("sp-title").contentEditable = SP.isNew ? "true" : "false";
  $("sp-view").innerHTML = SP.page ? renderMd(SP.text) : $("sp-view").innerHTML;
  $("spaces").classList.toggle("editing", SP.editing);
  $("sp-edit").textContent = SP.editing ? "PREVIEW" : "EDIT";
  $("sp-edit").disabled = !SP.page && !SP.isNew;
  $("sp-save").disabled = !SP.editing;
  $("sp-trash").disabled = !SP.page || SP.isNew;
  $("sp-trash").textContent = "TRASH";
  $("sp-status").textContent = "";
}

$("sp-edit").onclick = () => {
  if (SP.editing) { SP.text = $("sp-editor").value; SP.editing = false; }
  else { $("sp-editor").value = SP.text; SP.editing = true; setTimeout(() => $("sp-editor").focus(), 50); }
  showPage();
  if (SP.editing) $("sp-save").disabled = false;
};

async function savePage() {
  let name = SP.page;
  if (SP.isNew) {
    name = $("sp-title").textContent.trim();
    if (!name) { toast("warn", "Type the page name first, like Notes/Ideas"); $("sp-title").focus(); return; }
    if (!name.includes("/")) name = "Inbox/" + name;
  }
  const text = $("sp-editor").value;
  const r = await call("spaces_save", name, text);
  if (!r) return;
  Object.assign(SP, { page: r.name, text, editing: false, isNew: false });
  showPage();
  $("sp-status").textContent = "saved";
  SP.pages = (await call("spaces_list")) || SP.pages;
  drawTree();
}
$("sp-save").onclick = savePage;
$("sp-editor").addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); savePage(); }
  e.stopPropagation();  // Space / Escape here are typing, not Karen's shortcuts
});

$("sp-new").onclick = () => {
  Object.assign(SP, { page: null, text: "# New page\n\n", isNew: true, editing: true });
  $("sp-editor").value = SP.text;
  showPage();
  $("sp-save").disabled = false;
  $("sp-title").textContent = "Notes/";
  $("sp-title").focus();
  document.getSelection().selectAllChildren($("sp-title")); document.getSelection().collapseToEnd();
};
$("sp-title").addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Enter") { e.preventDefault(); $("sp-editor").focus(); } });

$("sp-trash").onclick = async () => {  // two clicks: the first one arms it
  if (Date.now() - SP.trashArm > 3000) { SP.trashArm = Date.now(); $("sp-trash").textContent = "SURE?"; return; }
  const r = await call("spaces_trash", SP.page);
  if (!r) return;
  toast("", `Moved ${r.name} to the Spaces trash`);
  Object.assign(SP, { page: null, text: "", editing: false });
  $("sp-view").innerHTML = '<p class="hint">Page moved to the trash (data/spaces/.trash).</p>';
  showPage();
  SP.pages = (await call("spaces_list")) || [];
  drawTree();
};

$("sp-view").addEventListener("click", (e) => {
  const a = e.target.closest("a");
  if (!a) return;
  e.preventDefault();
  if (a.dataset.url) call("open_link", a.dataset.url);
  else if (a.dataset.page) {
    const want = a.dataset.page.replace(/\.md$/i, "").toLowerCase();
    const hit = SP.pages.find((p) => p.name.toLowerCase() === want) || SP.pages.find((p) => p.name.toLowerCase().endsWith("/" + want));
    hit ? openPage(hit.name) : toast("warn", `No page called ${a.dataset.page}`);
  }
});

/* ask a Dot about the open page (or anything) */
async function askDot() {
  const dot = $("sp-ask-dot").value, task = $("sp-ask-text").value.trim();
  if (!dot || !task) return;
  const r = await call("dot_ask", dot, task, SP.page || "");
  if (!r) return;
  $("sp-ask-text").value = "";
  toast("", `${dot} is on it`);
  applyDots(r);
}
$("sp-ask-go").onclick = askDot;
$("sp-ask-text").addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Enter") askDot(); });

/* ------------------------------------------------------------------ dots */
function applyDots(st) {
  if (!st) return;
  if (st.dots) { SP.dots = st.dots; SP.abilities = st.abilities || SP.abilities; SP.jobs = st.jobs || []; }
  SP.running = st.running || []; SP.recent = st.recent || [];
  $("dots-count").textContent = SP.running.length ? `· ${SP.running.length} working` : "";
  $("dots-busy").className = SP.running.length ? "on" : "";
  const sel = $("sp-ask-dot"), keep = sel.value;
  sel.innerHTML = SP.dots.map((d) => `<option>${escHtml(d.name)}</option>`).join("");
  if (keep) sel.value = keep;
  $("dot-form").abilities.innerHTML = SP.abilities.map((a) => `<option>${escHtml(a)}</option>`).join("");
  drawDots(); drawRuns(); drawJobs();
  if (!SP.dotEdit && SP.dots.length) editDot(SP.dots[0]);
}

function drawDots() {
  const busy = new Set(SP.running.map((r) => r.dot));
  $("dot-list").innerHTML = "";
  SP.dots.forEach((d) => {
    const el = document.createElement("button");
    el.className = "dot-card" + (SP.dotEdit === d.name ? " on" : "");
    el.innerHTML = `<i class="${busy.has(d.name) ? "busy" : ""}"></i><b>${escHtml(d.name)}</b><small>${escHtml(d.role)}</small>` +
      `<span class="tag">${escHtml(d.abilities)}${d.private ? " · private" : ""}</span>`;
    el.onclick = () => editDot(d);
    $("dot-list").appendChild(el);
  });
}

function editDot(d) {
  const f = $("dot-form");
  SP.dotEdit = d ? d.name : null;
  f.name.value = d ? d.name : ""; f.role.value = d ? d.role : ""; f.instructions.value = d ? d.instructions || "" : "";
  f.abilities.value = d ? d.abilities : "writing"; f.space.value = d ? d.space || "" : ""; f.private.checked = d ? !!d.private : true;
  $("dot-delete").style.visibility = d ? "visible" : "hidden";
  $("dot-delete").textContent = "REMOVE";
  drawDots();
}
$("dot-new").onclick = () => { editDot(null); $("dot-form").name.focus(); };
$("dot-form").addEventListener("keydown", (e) => e.stopPropagation());
$("dot-form").onsubmit = async (e) => {
  e.preventDefault();
  const f = e.target;
  const dot = { name: f.name.value.trim(), role: f.role.value, instructions: f.instructions.value,
                abilities: f.abilities.value, space: f.space.value, private: f.private.checked };
  const r = await call("dot_save", dot, SP.dotEdit || "");
  if (!r) return;
  SP.dotEdit = dot.name;
  applyDots(r);
  toast("", `${dot.name} saved`);
};
$("dot-delete").onclick = async () => {
  if (Date.now() - SP.delArm > 3000) { SP.delArm = Date.now(); $("dot-delete").textContent = "SURE?"; return; }
  const name = SP.dotEdit;
  const r = await call("dot_delete", name);
  if (!r) return;
  SP.dotEdit = null;
  applyDots(r);
  toast("", `${name} removed (its pages stay)`);
};

function ago(iso) {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  return s < 60 ? "just now" : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago` : iso.slice(0, 10);
}
function drawRuns() {
  const ids = new Set(SP.running.map((r) => r.id));
  const list = [...SP.running, ...SP.recent.filter((r) => !ids.has(r.id)).reverse()].slice(0, 8);
  $("dot-runs").innerHTML = list.length ? "" : '<p class="hint">Nothing yet.</p>';
  list.forEach((r) => {
    const el = document.createElement("div");
    el.className = "run " + r.status;
    const steps = (r.steps || []).map((s) => s.replace(/_/g, " ")).join(" → ");
    el.innerHTML = `<div><b>${escHtml(r.dot)}</b> <span class="st">${r.status === "running" ? "working…" : escHtml(r.status)}</span>` +
      `<small>${ago(r.started)}${r.seconds ? ` · ${r.seconds}s` : ""}</small></div>` +
      `<p>${escHtml(r.task)}</p>` + (steps ? `<small class="steps">${escHtml(steps)}</small>` : "") +
      (r.answer ? `<p class="ans">${escHtml(r.answer)}</p>` : "");
    $("dot-runs").appendChild(el);
  });
}
function drawJobs() {
  $("dot-jobs").innerHTML = SP.jobs.length ? "" : '<p class="hint">No scheduled tasks.</p>';
  SP.jobs.forEach((j) => {
    const el = document.createElement("div");
    el.className = "job";
    const days = j.days.length === 7 ? "every day" : j.days.join(", ");
    el.innerHTML = `<span><b>${escHtml(j.dot)}</b> ${escHtml(j.at)} · ${escHtml(days)}<br><small>${escHtml(j.task)}</small></span>`;
    const x = document.createElement("button");
    x.textContent = "✕"; x.title = "Cancel this scheduled task";
    x.onclick = async () => applyDots(await call("job_cancel", j.id));
    el.appendChild(x);
    $("dot-jobs").appendChild(el);
  });
}

/* Python -> page: a Dot started, moved on, or finished */
window.karen.dots = (st) => {
  applyDots(st);
  if ($("spaces").classList.contains("open")) call("spaces_list").then((p) => { if (p) { SP.pages = p; drawTree(); } });
};

/* ------------------------------------------------------------------ demo data (browser preview only) */
const DemoSpaces = (() => {
  const pages = {
    "Research/Best laptops under ₹60,000 for coding": "# Best laptops under ₹60,000 for coding\n\n**Checked:** October 2026 (demo page).\n\n## The three picks\n\n| Laptop | CPU | Price |\n|---|---|---|\n| Lenovo IdeaPad Slim 3 | i5-13420H | ~₹56k |\n| ASUS Vivobook 16 | i5-13420H | ~₹55k |\n| Acer Aspire Lite | Ryzen 7 7730U | ~₹50k |\n\n- 16 GB RAM is the sweet spot\n- [ ] check the screen in a shop\n\n> Prices move fast: verify before buying.\n\nSource: https://example.com/laptops · see also [the message](Drafts/WhatsApp message)",
    "Drafts/WhatsApp message": "# WhatsApp message\n\nHey! If I had to pick **one**: the *Lenovo IdeaPad Slim 3*. Details in [the research](Research/Best laptops under ₹60,000 for coding).",
  };
  let dots = [{ name: "Researcher", role: "researches topics on the web and writes sourced notes", abilities: "research", space: "Research", private: false, instructions: "" },
              { name: "Writer", role: "writes and edits documents", abilities: "writing", space: "Drafts", private: false, instructions: "" }];
  const jobs = [{ id: 1, dot: "Researcher", task: "AI news into the News page", at: "08:00", days: ["mon", "tue", "wed", "thu", "fri"] }];
  const recent = [{ id: "1", dot: "Writer", task: "turn the laptop page into a WhatsApp message", status: "done", started: new Date(Date.now() - 300000).toISOString(), seconds: 19.5, steps: ["list_pages", "open_page", "save_page"], answer: "Saved as Drafts/WhatsApp message." }];
  const state = () => ({ dots, jobs, recent, running: [], abilities: ["research", "writing", "coding", "organising"] });
  return {
    spaces_list: () => Object.keys(pages).map((name) => ({ name, words: pages[name].split(/\s+/).length, edited: "2026-10-09T21:47" })),
    spaces_read: (name) => (name in pages ? { name, text: pages[name] } : { error: "no such page" }),
    spaces_save: (name, text) => { pages[name] = text; return { name }; },
    spaces_trash: (name) => { delete pages[name]; return { name }; },
    dots_state: state,
    dot_save: (d, old) => { dots = dots.filter((x) => x.name !== d.name && x.name !== old).concat([d]); return state(); },
    dot_delete: (name) => { dots = dots.filter((x) => x.name !== name); return state(); },
    dot_ask: (dot, task) => { toast("", `Demo: ${dot} would start "${task}"`); return state(); },
    job_cancel: (id) => { jobs.splice(jobs.findIndex((j) => j.id === id), 1); return state(); },
    open_link: (url) => toast("", `Demo: would open ${url} in your browser`),
  };
})();
