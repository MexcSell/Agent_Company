"use strict";
// Agent Company dashboard. Plain JS, no build step.

const $ = (sel, root = document) => root.querySelector(sel);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = new Intl.NumberFormat();
const money = (v) => "$" + Number(v || 0).toFixed(4);
const ago = (iso) => { if (!iso) return "never"; const s = (Date.now() - Date.parse(iso + (iso.endsWith("Z") || iso.includes("+") ? "" : "Z"))) / 1000; return s < 60 ? "just now" : s < 3600 ? `${Math.floor(s / 60)} min ago` : s < 86400 ? `${Math.floor(s / 3600)} h ago` : `${Math.floor(s / 86400)} d ago`; };
const dur = (sec) => { const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60); return h >= 24 ? `${Math.floor(h / 24)}d ${h % 24}h` : `${h}h ${m}m`; };

// --- icons (24px stroke icons) --------------------------------------------------
const P = {
  dashboard: "M3 3h8v8H3zM13 3h8v5h-8zM13 10h8v11h-8zM3 13h8v8H3z",
  goals: "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm0 4a6 6 0 1 0 0 12 6 6 0 0 0 0-12zm0 4a2 2 0 1 0 0 4 2 2 0 0 0 0-4z",
  agents: "M16 11a4 4 0 1 0-8 0M4 21v-1a6 6 0 0 1 6-6h4a6 6 0 0 1 6 6v1M12 3v4",
  models: "M12 2l9 5v10l-9 5-9-5V7zM12 22V12M21 7l-9 5-9-5",
  research: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM21 21l-5-5",
  users: "M9 7a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM2 21v-1a5 5 0 0 1 5-5h4a5 5 0 0 1 5 5v1M16 3a4 4 0 0 1 0 8M22 21v-1a5 5 0 0 0-3-4.6",
  sun: "M12 7a5 5 0 1 0 0 10 5 5 0 0 0 0-10zM12 1v2M12 21v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1 12h2M21 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4",
  moon: "M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z",
  general_manager: "M12 2l3 6 6 1-4.5 4 1 6-5.5-3-5.5 3 1-6L3 9l6-1z",
  architect: "M3 21h18M5 21V9l7-6 7 6v12M9 21v-6h6v6",
  developer: "M8 6l-6 6 6 6M16 6l6 6-6 6M14 4l-4 16",
  frontend: "M3 4h18v12H3zM8 20h8M12 16v4",
  qa: "M9 12l2 2 4-4M12 2l8 4v6c0 5-3.5 9-8 10-4.5-1-8-5-8-10V6z",
  research_role: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM21 21l-5-5",
  external_data: "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zM2 12h20M12 2c3 3 3 17 0 20M12 2c-3 3-3 17 0 20",
  analyst: "M4 20V10M10 20V4M16 20v-7M22 20H2",
  security: "M12 2l8 4v6c0 5-3.5 9-8 10-4.5-1-8-5-8-10V6z",
  devops: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM12 1v4M12 19v4M4.2 4.2l2.8 2.8M17 17l2.8 2.8M1 12h4M19 12h4",
  learning: "M2 7l10-4 10 4-10 4zM6 9v6c3 2 9 2 12 0V9",
  business: "M3 7h18v13H3zM8 7V4h8v3",
  admin: "M4 4h16v16H4zM8 9h8M8 13h8M8 17h5",
  specialist: "M12 2a5 5 0 1 0 0 10 5 5 0 0 0 0-10zM4 22a8 8 0 0 1 16 0",
};
const ROLE_ICON = { general_manager: "general_manager", architect: "architect", developer: "developer", frontend: "frontend", qa: "qa", research: "research_role", external_data: "external_data", analyst: "analyst", comprehensive_analyst: "analyst", security: "security", network_security: "security", security_guard: "security", devops: "devops", tech_scout: "learning", tech_introducer: "learning", instructor: "learning", student: "learning", product: "business", sales: "business", administrator: "admin", agent_manager: "admin" };
const icon = (name, size = 20) => `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${P[name] || P.specialist}"/></svg>`;
const roleIcon = (role) => icon(ROLE_ICON[role] || "specialist");
const pill = (s) => `<span class="pill ${esc(s)}">${esc(String(s).replace(/_/g, " "))}</span>`;

// --- storage-safe helpers -------------------------------------------------------
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
  del(k) { try { localStorage.removeItem(k); } catch { /* ignore */ } },
};

// --- API ------------------------------------------------------------------------
async function api(path, opts = {}) {
  const res = await fetch(path, { credentials: "same-origin", headers: { "Content-Type": "application/json" }, ...opts, body: opts.body ? JSON.stringify(opts.body) : undefined });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) { const e = new Error(data.detail || data.code || res.statusText); e.status = res.status; e.code = data.code; throw e; }
  return data;
}
function toast(msg) { const t = document.createElement("div"); t.className = "toast"; t.textContent = msg; document.body.append(t); setTimeout(() => t.remove(), 4000); }

// --- theme ----------------------------------------------------------------------
function applyTheme(t) { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; const dark = t ? t === "dark" : matchMedia("(prefers-color-scheme: dark)").matches; $("#theme").innerHTML = icon(dark ? "sun" : "moon", 18); }
function toggleTheme() { const dark = document.documentElement.dataset.theme ? document.documentElement.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches; const t = dark ? "light" : "dark"; store.set("ac.theme", t); applyTheme(t); }

// --- device sign-in -------------------------------------------------------------
function deviceSecret(create) {
  let s = store.get("ac.device");
  if (!s && create) { const b = new Uint8Array(32); crypto.getRandomValues(b); s = btoa(String.fromCharCode(...b)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, ""); store.set("ac.device", s); }
  return s;
}
function deviceInfo() {
  const ua = navigator.userAgent; const browser = /Edg\//.test(ua) ? "Edge" : /Chrome\//.test(ua) ? "Chrome" : /Firefox\//.test(ua) ? "Firefox" : /Safari\//.test(ua) ? "Safari" : "Browser";
  const os = /Windows/.test(ua) ? "Windows" : /Mac OS/.test(ua) ? "macOS" : /Android/.test(ua) ? "Android" : /Linux/.test(ua) ? "Linux" : "Unknown OS";
  return { label: `${browser} on ${os}`, userAgent: ua.slice(0, 300), screen: `${screen.width}x${screen.height}`, language: navigator.language, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone };
}
let me = null, pollTimer = null;

function authScreen(state, message = "") {
  $("#app").hidden = true; const box = $("#auth"); box.hidden = false;
  if (state === "pending") {
    box.innerHTML = `<div class="card"><h1>Waiting for approval</h1><p class="muted">${esc(message || "An administrator has to approve this computer. You will be signed in automatically.")}</p><button class="btn" id="again">Check again</button></div>`;
    $("#again").onclick = tryDevice; clearInterval(pollTimer); pollTimer = setInterval(tryDevice, 10000); return;
  }
  clearInterval(pollTimer);
  box.innerHTML = `<div class="card"><h1>Agent Company</h1><p class="muted">${esc(message || "Enter your name and register this computer. No password is needed.")}</p>
    <form id="reg"><label for="name">Name</label><input id="name" autocomplete="username" required minlength="3" maxlength="32" pattern="[A-Za-z0-9][A-Za-z0-9._\\-]{2,31}">
    <div class="row" style="margin-top:12px"><button class="btn primary" type="submit">Register</button></div></form></div>`;
  $("#reg").onsubmit = async (e) => {
    e.preventDefault();
    try {
      const r = await api("/api/auth/register", { method: "POST", body: { name: $("#name").value.trim(), device_secret: deviceSecret(true), device_info: deviceInfo() } });
      if (r.signed_in) boot(); else authScreen("pending");
    } catch (err) { toast(err.message); }
  };
}
async function tryDevice() {
  const s = deviceSecret(false);
  if (!s) return authScreen("register");
  try { await api("/api/auth/device", { method: "POST", body: { device_secret: s } }); clearInterval(pollTimer); boot(); }
  catch (e) {
    if (e.code === "unknown_device") { store.del("ac.device"); authScreen("register"); }
    else if (e.code === "pending") authScreen("pending");
    else if (e.status === 403) { clearInterval(pollTimer); authScreen("blocked", e.message); $("#reg")?.remove(); }
    else authScreen("pending", "The server is not reachable; retrying.");
  }
}

// --- shell ----------------------------------------------------------------------
const VIEWS = [
  { id: "dashboard", title: "Dashboard", icon: "dashboard" },
  { id: "goals", title: "Goals", icon: "goals" },
  { id: "agents", title: "Agents", icon: "agents" },
  { id: "models", title: "Models", icon: "models" },
  { id: "research", title: "Research", icon: "research" },
  { id: "users", title: "Users", icon: "users", admin: true },
];
let current = store.get("ac.view") || "dashboard", refreshTimer = null, badges = {};

async function boot() {
  try { me = await api("/api/auth/me"); } catch { return tryDevice(); }
  $("#auth").hidden = true; $("#app").hidden = false;
  $("#whoami").textContent = me.name + (me.is_admin ? " · admin" : "");
  renderRail(); show(VIEWS.some((v) => v.id === current && (!v.admin || me.is_admin)) ? current : "dashboard");
}
function renderRail() {
  $("#rail").innerHTML = VIEWS.filter((v) => !v.admin || me.is_admin).map((v) => `<button data-v="${v.id}" title="${v.title}" class="${v.id === current ? "active" : ""}">${icon(v.icon)}${badges[v.id] ? `<span class="badge">${badges[v.id]}</span>` : ""}</button>`).join("");
  $("#rail").querySelectorAll("button").forEach((b) => (b.onclick = () => show(b.dataset.v)));
}
async function updateBadges() {
  try {
    const o = await api("/api/overview");
    badges = { goals: (o.goals.running || 0) + (o.goals.awaiting_approval || 0), users: o.pending_users || 0 };
    renderRail();
  } catch { /* signed out */ }
}
function show(id) {
  current = id; store.set("ac.view", id);
  const v = VIEWS.find((x) => x.id === id); $("#view-title").textContent = v.title; renderRail();
  clearInterval(refreshTimer); render(); updateBadges();
  refreshTimer = setInterval(() => { if (!document.hidden && $("#modal").hidden && !document.activeElement?.closest("form")) { render(true); updateBadges(); } }, 5000);
}
async function render(quiet) {
  const fn = { dashboard: viewDashboard, goals: viewGoals, agents: viewAgents, models: viewModels, research: viewResearch, users: viewUsers }[current];
  try { await fn(quiet); } catch (e) { if (e.status === 401) return tryDevice(); if (!quiet) $("#view").innerHTML = `<div class="card">Could not load: ${esc(e.message)}</div>`; }
}
function modal(html) { $("#modal-body").innerHTML = html; $("#modal").hidden = false; }
$("#modal-close").onclick = () => ($("#modal").hidden = true);
$("#modal").onclick = (e) => { if (e.target.id === "modal") $("#modal").hidden = true; };
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#modal").hidden = true; });

// --- dashboard ------------------------------------------------------------------
async function viewDashboard() {
  const [o, u, ev] = await Promise.all([api("/api/overview"), api("/api/usage"), api("/api/events")]);
  const k = (label, value) => `<div class="card kpi"><div class="label">${label}</div><div class="value">${value}</div></div>`;
  $("#view").innerHTML = `<div class="grid kpis">
    ${k("Running goals", o.goals.running || 0)}${k("Awaiting approval", o.goals.awaiting_approval || 0)}
    ${k("Busy agents", o.agents.busy || 0)}${k("Idle agents", o.agents.idle || 0)}
    ${k("Knowledge pages", fmt.format(o.documents))}${k("Spend (all time)", money(o.usage.cost))}</div>
    <div class="grid two" style="margin-top:12px">
      <div class="card"><h2>Recent activity</h2><div class="events">${ev.slice(-25).reverse().map((e) => `<div><span class="k">${esc(e.kind)}</span>${esc(e.message)} <span class="muted">${ago(e.at)}</span></div>`).join("") || '<span class="muted">Nothing yet. Create a goal.</span>'}</div></div>
      <div class="card"><h2>Usage by model</h2><p class="muted">Daily reset in ${dur(u.resets.daily_in_seconds)} · weekly reset in ${dur(u.resets.weekly_in_seconds)} (UTC)</p>${usageTable(u)}</div>
    </div>`;
}
function usageTable(u) {
  const lim = (w) => (w.limit ? ` / ${fmt.format(w.limit)}` : "");
  return `<div class="table-wrap"><table><tr><th>Model</th><th class="num">Today tokens</th><th class="num">Today runs</th><th class="num">Week tokens</th><th class="num">Week runs</th><th class="num">Week cost</th></tr>
  ${u.models.map((m) => `<tr><td>${esc(m.name)}</td><td class="num">${fmt.format(m.daily.tokens)}${lim(m.daily)}</td><td class="num">${m.daily.runs}</td><td class="num">${fmt.format(m.weekly.tokens)}${lim(m.weekly)}</td><td class="num">${m.weekly.runs}</td><td class="num">${money(m.weekly.cost)}</td></tr>`).join("")}</table></div>`;
}

// --- goals ----------------------------------------------------------------------
let selectedGoal = null;
async function viewGoals(quiet) {
  const goals = await api("/api/goals");
  if (selectedGoal === null && goals.length) selectedGoal = goals[0].id;
  const openIds = new Set([...document.querySelectorAll("#goal-detail details[open]")].map((d) => d.dataset.id));
  const list = goals.map((g) => `<div class="list-item ${g.id === selectedGoal ? "selected" : ""}" data-id="${g.id}"><div class="row"><strong class="grow">${esc(g.title)}</strong>${pill(g.status)}</div><div class="muted">#${g.id} · iteration ${g.iteration}/${g.max_iterations} · ${ago(g.updated_at)}</div></div>`).join("") || '<p class="muted">No goals yet.</p>';
  if (!quiet || !$("#goal-list")) {
    $("#view").innerHTML = `<div class="grid two"><div class="grid"><div class="card"><h2>New goal</h2><form id="goal-form">
      <label for="g-title">Goal</label><input id="g-title" required minlength="3" placeholder="Build a REST API for a todo list">
      <label for="g-desc">Details</label><textarea id="g-desc" placeholder="Constraints, acceptance criteria, tech choices"></textarea>
      <label for="g-urls">Research URLs (one per line; linked pages are collected too)</label><textarea id="g-urls" placeholder="https://docs.example.com/guide"></textarea>
      ${me.is_admin ? `<label for="g-repo">Existing git repository (optional; default: new workspace)</label><input id="g-repo" placeholder="/home/you/project">
      <label for="g-checks">Quality commands (one per line, run in the worktree)</label><textarea id="g-checks" placeholder="uv run pytest -q"></textarea>` : ""}
      <label for="g-iter">Max iterations</label><input id="g-iter" type="number" min="1" max="10" value="3">
      <div class="row" style="margin-top:12px"><button class="btn primary">Start</button></div></form></div>
      <div class="card"><h2>Goals</h2><div class="list" id="goal-list"></div></div></div>
      <div id="goal-detail" class="card"><p class="muted">Select a goal.</p></div></div>`;
    $("#goal-form").onsubmit = async (e) => {
      e.preventDefault();
      const lines = (id) => ($(id)?.value || "").split("\n").map((x) => x.trim()).filter(Boolean);
      try {
        const r = await api("/api/goals", { method: "POST", body: { title: $("#g-title").value, description: $("#g-desc").value, urls: lines("#g-urls"), repo_path: $("#g-repo")?.value.trim() || "", quality_commands: lines("#g-checks"), max_iterations: Number($("#g-iter").value) || null } });
        selectedGoal = r.id; e.target.reset(); toast(`Goal #${r.id} started`); viewGoals(true);
      } catch (err) { toast(err.message); }
    };
  }
  $("#goal-list").innerHTML = list;
  $("#goal-list").querySelectorAll(".list-item").forEach((el) => (el.onclick = () => { selectedGoal = Number(el.dataset.id); viewGoals(true); }));
  if (selectedGoal !== null) await goalDetail(selectedGoal, openIds);
}
async function goalDetail(id, openIds = new Set()) {
  const g = await api(`/api/goals/${id}`);
  const iters = [...new Set(g.tasks.map((t) => t.iteration))].sort((a, b) => b - a);
  const actions = [
    g.status === "awaiting_approval" && me.is_admin ? `<button class="btn primary" data-act="approve">Approve &amp; merge</button><button class="btn danger" data-act="reject">Reject</button>` : "",
    ["queued", "running"].includes(g.status) ? `<button class="btn danger" data-act="cancel">Cancel</button>` : "",
  ].join("");
  $("#goal-detail").innerHTML = `<div class="row"><h2 class="grow" style="margin:0">#${g.id} ${esc(g.title)}</h2>${pill(g.status)}${actions}</div>
    <p class="muted">Iteration ${g.iteration}/${g.max_iterations} · branch <code>${esc(g.branch || "-")}</code> · worktree <code>${esc(g.worktree || "-")}</code></p>
    ${g.result ? `<pre>${esc(g.result)}</pre>` : ""}
    ${iters.map((it) => `<h3>Iteration ${it}</h3>${g.tasks.filter((t) => t.iteration === it).map((t) => `<details data-id="t${t.id}" ${openIds.has("t" + t.id) ? "open" : ""}><summary><span class="row" style="display:inline-flex">${pill(t.status)} <strong>${esc(t.title)}</strong> <span class="muted">${esc(t.role)} · ${esc(t.model || "-")}</span></span></summary>
      ${(t.gate?.acceptance || []).map((c) => `<div class="muted">${c.passed ? "✓" : "✗"} ${esc(JSON.stringify(c.condition))} ${esc(c.detail || "")}</div>`).join("")}
      ${t.gate?.files?.length ? `<div class="muted">Files: ${esc(t.gate.files.join(", "))}</div>` : ""}<pre>${esc(t.output || "(no output yet)")}</pre></details>`).join("")}`).join("")}
    <h3>Activity</h3><div class="events">${g.events.map((e) => `<div><span class="k">${esc(e.kind)}</span>${esc(e.message)} <span class="muted">${ago(e.at)}</span></div>`).join("")}</div>`;
  $("#goal-detail").querySelectorAll("[data-act]").forEach((b) => (b.onclick = async () => {
    try { await api(`/api/goals/${id}/${b.dataset.act}`, { method: "POST" }); toast(`Goal ${b.dataset.act}d`); viewGoals(true); } catch (err) { toast(err.message); }
  }));
}

// --- agents ---------------------------------------------------------------------
async function viewAgents() {
  const [agents, rooms] = await Promise.all([api("/api/agents"), api("/api/rooms")]);
  const divisions = [...new Set(rooms.map((r) => r.division))];
  $("#view").innerHTML = divisions.map((d) => `<h3>${esc(d)}</h3>${rooms.filter((r) => r.division === d).map((r) => {
    const members = agents.filter((a) => a.room === r.key);
    return `<div class="room" style="margin-bottom:14px"><h2>${esc(r.name)} ${r.rotational ? '<span class="pill">rotational</span>' : ""} <span class="muted" style="font-weight:400">${esc(r.description)}</span></h2>
      <div class="grid agents">${members.map((a) => `<div class="card agent ${a.enabled ? "" : "off"}" data-id="${a.id}"><div class="avatar">${roleIcon(a.role)}</div><div style="min-width:0"><strong>${esc(a.name)}</strong><div class="muted">${esc(a.role)} · L${a.level} · ${a.xp} XP</div><div class="row" style="margin-top:4px">${pill(a.enabled ? a.status : "disabled")}<span class="muted">${a.tasks_done} done · ${a.tasks_failed} failed</span></div></div></div>`).join("") || '<span class="muted">No agents.</span>'}</div></div>`;
  }).join("")}`).join("");
  document.querySelectorAll(".agent").forEach((el) => (el.onclick = () => agentModal(Number(el.dataset.id))));
}
async function agentModal(id) {
  const [a, m] = await Promise.all([api(`/api/agents/${id}`), api("/api/models")]);
  const skills = Object.entries(a.skills || {}).sort((x, y) => y[1] - x[1]).map(([k, v]) => `<span class="pill">${esc(k)} ${v}</span>`).join(" ");
  modal(`<div class="row"><div class="avatar">${roleIcon(a.role)}</div><div class="grow"><h2 style="margin:0">${esc(a.name)}</h2><div class="muted">${esc(a.title)} · ${esc(a.room)}</div></div>${pill(a.enabled ? a.status : "disabled")}</div>
    <div class="grid two" style="margin-top:12px"><div>
      <h3>Mission</h3><p>${esc(a.goal)}</p><p class="muted">${esc(a.backstory)}</p>
      <h3>Skills</h3><div class="row">${skills || '<span class="muted">none</span>'}</div>
      <h3>Permissions</h3><div class="row">${(a.permissions || []).map((p) => `<span class="pill">${esc(p)}</span>`).join(" ")}</div>
      <h3>Stats</h3><table><tr><td>Level / XP</td><td class="num">${a.level} / ${a.xp}</td></tr><tr><td>Tasks done / failed</td><td class="num">${a.tasks_done} / ${a.tasks_failed}</td></tr><tr><td>Runs</td><td class="num">${a.usage.runs}</td></tr><tr><td>Input / output tokens</td><td class="num">${fmt.format(a.usage.input_tokens)} / ${fmt.format(a.usage.output_tokens)}</td></tr><tr><td>Cost</td><td class="num">${money(a.usage.cost)}</td></tr><tr><td>Last active</td><td class="num">${ago(a.last_active_at)}</td></tr></table>
      ${me.is_admin ? `<h3>Settings</h3><label>Model preference (overrides the role route)</label><select id="a-model"><option value="">Role route</option>${m.models.map((x) => `<option ${x.name === a.model_pref ? "selected" : ""}>${esc(x.name)}</option>`).join("")}</select>
        <div class="row" style="margin-top:10px"><button class="btn" id="a-toggle">${a.enabled ? "Disable" : "Enable"}</button><button class="btn primary" id="a-save">Save</button></div>` : ""}
    </div><div>
      <h3>Playbook</h3><pre>${esc(a.playbook || "Empty: the agent learns from finished work.")}</pre>
      <h3>Recent lessons</h3><div class="events">${a.lessons.map((l) => `<div>${pill(l.outcome)} ${esc(l.text)}</div>`).join("") || '<span class="muted">None yet.</span>'}</div>
      <h3>Related agents (shared experience)</h3><div class="events">${a.related.map((r) => `<div>${esc(r.name)} <span class="muted">${esc(r.role)} · ${r.weight.toFixed(2)}</span></div>`).join("") || '<span class="muted">None.</span>'}</div>
      <h3>Reports to it</h3><div class="events">${a.subordinates.map((r) => `<div>${esc(r.name)} <span class="muted">${esc(r.role)}</span></div>`).join("") || '<span class="muted">Nobody.</span>'}</div>
    </div></div>`);
  if (me.is_admin) {
    const save = async (changes) => { try { await api(`/api/agents/${id}`, { method: "PATCH", body: changes }); toast("Saved"); agentModal(id); viewAgents(); } catch (e) { toast(e.message); } };
    $("#a-save").onclick = () => save({ model_pref: $("#a-model").value || null });
    $("#a-toggle").onclick = () => save({ enabled: !a.enabled });
  }
}

// --- models ---------------------------------------------------------------------
async function viewModels() {
  const m = await api("/api/models");
  const admin = me.is_admin;
  $("#view").innerHTML = `<div class="card"><h2>Model catalog</h2><p class="muted">Executor mode: <code>${esc(m.executor)}</code>${m.default_model ? ` · MODEL: <code>${esc(m.default_model)}</code>` : ""}. Unavailable models fall back to MODEL, then to the offline simulator.</p>
    <div class="table-wrap"><table><tr><th>Name</th><th>Kind</th><th>Model id</th><th class="num">$/1M in</th><th class="num">$/1M out</th><th>Status</th><th></th></tr>
    ${m.models.map((x) => `<tr><td>${esc(x.name)}</td><td>${esc(x.kind)}</td><td class="mono">${esc(x.model_id)}</td><td class="num">${x.input_cost}</td><td class="num">${x.output_cost}</td><td>${x.available ? pill("ok") : `<span class="muted">${esc(x.unavailable_reason)}</span>`}</td><td>${admin && x.kind !== "offline" ? `<button class="btn sm danger" data-del="${x.id}">Delete</button>` : ""}</td></tr>`).join("")}</table></div></div>
    <div class="grid two" style="margin-top:12px"><div class="card"><h2>Routes: which role uses which model</h2><table>${Object.entries(m.routes).map(([role, name]) => `<tr><td>${esc(role)}</td><td>${admin ? `<select data-role="${esc(role)}">${m.models.map((x) => `<option ${x.name === name ? "selected" : ""}>${esc(x.name)}</option>`).join("")}</select>` : esc(name)}</td></tr>`).join("")}</table></div>
    <div class="grid">${admin ? `<div class="card"><h2>Add model</h2><form id="m-form"><label>Name</label><input id="m-name" required><label>Kind</label><select id="m-kind"><option value="llm">API model (CrewAI / LiteLLM id)</option><option value="claude_code">Claude Code CLI</option><option value="codex">Codex CLI</option></select>
      <label>Model id (e.g. openai/gpt-5, ollama/qwen3-coder, openrouter/deepseek/deepseek-chat)</label><input id="m-id">
      <div class="row"><div class="grow"><label>$/1M input</label><input id="m-in" type="number" step="0.01" value="0"></div><div class="grow"><label>$/1M output</label><input id="m-out" type="number" step="0.01" value="0"></div></div>
      <div class="row"><div class="grow"><label>Daily token limit (0 = none)</label><input id="m-dl" type="number" value="0"></div><div class="grow"><label>Weekly token limit</label><input id="m-wl" type="number" value="0"></div></div>
      <div class="row" style="margin-top:10px"><button class="btn primary">Add</button></div></form></div>
      <div class="card"><h2>OpenRouter models</h2><div class="row"><input id="or-q" class="grow" placeholder="Search (e.g. qwen, deepseek, llama)"><label class="row" style="margin:0"><input type="checkbox" id="or-free" style="width:auto"> free only</label><button class="btn" id="or-go">Search</button></div><div id="or-res" class="table-wrap" style="margin-top:8px"></div></div>` : ""}</div></div>`;
  document.querySelectorAll("[data-del]").forEach((b) => (b.onclick = async () => { if (!confirm("Delete this model? Routes using it move to another model.")) return; try { await api(`/api/models/${b.dataset.del}`, { method: "DELETE" }); viewModels(); } catch (e) { toast(e.message); } }));
  document.querySelectorAll("[data-role]").forEach((s) => (s.onchange = async () => { try { await api(`/api/routes/${s.dataset.role}`, { method: "PUT", body: { model_name: s.value } }); toast("Route saved"); } catch (e) { toast(e.message); } }));
  if (!admin) return;
  $("#m-form").onsubmit = async (e) => { e.preventDefault(); try { await api("/api/models", { method: "POST", body: { name: $("#m-name").value, kind: $("#m-kind").value, model_id: $("#m-id").value, input_cost: +$("#m-in").value, output_cost: +$("#m-out").value, daily_token_limit: +$("#m-dl").value, weekly_token_limit: +$("#m-wl").value } }); toast("Model added"); viewModels(); } catch (err) { toast(err.message); } };
  $("#or-go").onclick = async () => {
    $("#or-res").innerHTML = '<span class="muted">Loading…</span>';
    try {
      const rows = await api(`/api/openrouter/models?q=${encodeURIComponent($("#or-q").value)}&free=${$("#or-free").checked}`);
      $("#or-res").innerHTML = `<table><tr><th>Model</th><th class="num">$/1M in</th><th class="num">$/1M out</th><th class="num">Context</th><th></th></tr>${rows.slice(0, 60).map((r) => `<tr><td>${esc(r.name)}<div class="muted mono">${esc(r.id)}</div></td><td class="num">${r.input_cost}</td><td class="num">${r.output_cost}</td><td class="num">${r.context_length ? fmt.format(r.context_length) : "-"}</td><td><button class="btn sm" data-or="${esc(r.id)}">Add</button></td></tr>`).join("")}</table>`;
      document.querySelectorAll("[data-or]").forEach((b) => (b.onclick = async () => { try { await api("/api/openrouter/models", { method: "POST", body: { id: b.dataset.or } }); toast("Added"); viewModels(); } catch (e) { toast(e.message); } }));
    } catch (e) { $("#or-res").innerHTML = `<span class="muted">${esc(e.message)}</span>`; }
  };
}

// --- research -------------------------------------------------------------------
async function viewResearch(quiet) {
  if (quiet && $("#r-crawl")) return; // keep forms and results stable
  const [docs, src] = await Promise.all([api("/api/research/documents"), api("/api/research/sources")]);
  $("#view").innerHTML = `<div class="grid two"><div class="grid">
    <div class="card"><h2>Collect a website</h2><form id="r-crawl"><label>URL</label><input id="r-url" type="url" required placeholder="https://docs.crewai.com/">
      <div class="row"><div class="grow"><label>Link depth (0 = this page only)</label><input id="r-depth" type="number" min="0" max="3" value="1"></div><div class="grow"><label>Max pages</label><input id="r-max" type="number" min="1" max="200" value="20"></div></div>
      <div class="row" style="margin-top:10px"><button class="btn primary">Collect</button></div></form><div id="r-out"></div></div>
    <div class="card"><h2>Rotation sources</h2><p class="muted">The external-data room visits these in turn${src.interval ? ` every ${src.interval}s` : " (set ROTATION_INTERVAL to run automatically)"}. Roster: ${src.roster.length} agent(s).</p>
      ${src.sources.map((s) => `<div class="events"><div>${esc(s.url)} <span class="muted">depth ${s.depth} · ${ago(s.last_run_at)}</span></div></div>`).join("")}
      ${me.is_admin ? `<form id="r-src" class="row" style="margin-top:8px"><input id="r-src-url" class="grow" type="url" required placeholder="https://news.example.com/"><button class="btn">Add</button><button class="btn" type="button" id="r-rotate">Run next</button></form>` : ""}</div></div>
    <div class="grid"><div class="card"><h2>Search knowledge</h2><form id="r-search" class="row"><input id="r-q" class="grow" placeholder="What do we know about…"><button class="btn">Search</button></form><div id="r-hits"></div></div>
    <div class="card"><h2>Collected pages (${docs.length})</h2><div class="events">${docs.slice(0, 80).map((d) => `<div><a href="${esc(d.url)}" target="_blank" rel="noopener noreferrer">${esc(d.title || d.url)}</a> <span class="muted">${fmt.format(d.chars)} chars · ${ago(d.at)}</span></div>`).join("") || '<span class="muted">Nothing collected yet.</span>'}</div></div></div></div>`;
  $("#r-crawl").onsubmit = async (e) => {
    e.preventDefault(); $("#r-out").innerHTML = '<p class="muted">Collecting…</p>';
    try { const r = await api("/api/research/crawl", { method: "POST", body: { url: $("#r-url").value, depth: +$("#r-depth").value, max_pages: +$("#r-max").value } }); $("#r-out").innerHTML = `<p>Stored ${r.stored} of ${r.pages} page(s).</p>${r.errors.length ? `<pre>${esc(r.errors.map((x) => `${x.url}: ${x.error}`).join("\n"))}</pre>` : ""}`; setTimeout(() => viewResearch(), 1500); }
    catch (err) { $("#r-out").innerHTML = `<p class="muted">${esc(err.message)}</p>`; }
  };
  $("#r-search").onsubmit = async (e) => { e.preventDefault(); const hits = await api(`/api/research/search?q=${encodeURIComponent($("#r-q").value)}`); $("#r-hits").innerHTML = hits.map((h) => `<h3><a href="${esc(h.url)}" target="_blank" rel="noopener noreferrer">${esc(h.title || h.url)}</a></h3><pre>${esc(h.text)}</pre>`).join("") || '<p class="muted">No matches.</p>'; };
  if (me.is_admin) {
    $("#r-src").onsubmit = async (e) => { e.preventDefault(); try { await api("/api/research/sources", { method: "POST", body: { url: $("#r-src-url").value } }); viewResearch(); } catch (err) { toast(err.message); } };
    $("#r-rotate").onclick = async () => { try { const r = await api("/api/research/rotate", { method: "POST" }); toast(r.url ? `Agent ${r.agent_id} visited ${r.url}: ${r.stored} page(s)` : r.detail); viewResearch(); } catch (err) { toast(err.message); } };
  }
}

// --- users ----------------------------------------------------------------------
async function viewUsers() {
  const users = await api("/api/users");
  const btn = (u, act, label, cls = "") => `<button class="btn sm ${cls}" data-u="${u.id}" data-act="${act}">${label}</button>`;
  $("#view").innerHTML = `<div class="card"><h2>Users</h2><p class="muted">New registrations wait here. Approved users are signed in automatically from the computer they registered on.</p><div class="table-wrap"><table>
    <tr><th>Name</th><th>Status</th><th>Computer</th><th>Registered</th><th></th></tr>
    ${users.map((u) => `<tr><td>${esc(u.name)}${u.is_admin ? ' <span class="pill">admin</span>' : ""}</td><td>${pill(u.status)}</td><td>${esc(u.device_info?.label || (u.device_bound ? "bound" : "none"))}<div class="muted">${esc([u.device_info?.screen, u.device_info?.timezone, u.device_info?.language].filter(Boolean).join(" · "))}</div></td><td>${ago(u.created_at)}</td>
      <td class="row">${u.id === me.id ? "" : [
        ["pending", "rejected"].includes(u.status) ? btn(u, "approve", "Approve", "primary") : "",
        u.status === "pending" && !u.is_admin ? btn(u, "reject", "Reject", "danger") : "",
        u.status === "active" && !u.is_admin ? btn(u, "disable", "Disable") : "",
        u.status === "disabled" ? btn(u, "enable", "Enable") : "",
        u.device_bound ? btn(u, "reset_device", "Reset computer") : "",
        u.status === "active" && !u.is_admin ? btn(u, "make_admin", "Make admin") : "",
        !u.is_admin ? btn(u, "delete", "Delete", "danger") : ""].join("")}</td></tr>`).join("")}</table></div></div>`;
  document.querySelectorAll("[data-u]").forEach((b) => (b.onclick = async () => {
    const { u, act } = b.dataset;
    if (["delete", "reset_device"].includes(act) && !confirm(`${act.replace("_", " ")} this user?`)) return;
    try { await api(act === "delete" ? `/api/users/${u}` : `/api/users/${u}/${act}`, { method: act === "delete" ? "DELETE" : "POST" }); viewUsers(); updateBadges(); } catch (e) { toast(e.message); }
  }));
}

// --- start ----------------------------------------------------------------------
$("#theme").onclick = toggleTheme;
$("#logout").onclick = async () => { await api("/api/auth/logout", { method: "POST" }).catch(() => {}); clearInterval(refreshTimer); me = null; authScreen("register", "Signed out. Register again or reload to sign in on this computer."); };
applyTheme(store.get("ac.theme"));
api("/api/auth/me").then(boot).catch(tryDevice);
