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
  workspace: "M3 4h18v16H3zM8 4v16M11.5 10l2.5 2-2.5 2M15.5 14h2.5",
  goals: "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm0 4a6 6 0 1 0 0 12 6 6 0 0 0 0-12zm0 4a2 2 0 1 0 0 4 2 2 0 0 0 0-4z",
  agents: "M12 2v3M8 5h8a3 3 0 0 1 3 3v7a3 3 0 0 1-3 3H8a3 3 0 0 1-3-3V8a3 3 0 0 1 3-3zM9.5 10.5h.01M14.5 10.5h.01M9.5 14.5h5M2 10v4M22 10v4M9 18v3M15 18v3",
  models: "M12 2l9 5v10l-9 5-9-5V7zM12 22V12M21 7l-9 5-9-5",
  research: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM21 21l-5-5",
  users: "M9 7a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM2 21v-1a5 5 0 0 1 5-5h4a5 5 0 0 1 5 5v1M16 3a4 4 0 0 1 0 8M22 21v-1a5 5 0 0 0-3-4.6",
  projects: "M12 2l10 5-10 5L2 7zM2 12l10 5 10-5M2 17l10 5 10-5",
  folder: "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
  plus: "M12 5v14M5 12h14", chevron_left: "M15 6l-6 6 6 6", chevron_right: "M9 6l6 6-6 6", edit: "M4 20h4L19 9l-4-4L4 16zM14 6l4 4", trash: "M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13",
  check: "M5 12l5 5L20 7", x: "M6 6l12 12M18 6L6 18", copy: "M8 8h12v12H8zM4 16V4h12", play: "M7 4l13 8-13 8z", stop: "M6 6h12v12H6z",
  refresh: "M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7", save: "M5 3h11l3 3v15H5zM8 3v6h8M8 21v-7h8v7", search: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM21 21l-5-5",
  send: "M4 12l16-8-6 16-2-7z", attach: "M21 12.5l-8.5 8.5a6 6 0 0 1-8.5-8.5L13 3.5a4 4 0 0 1 5.7 5.7L9.6 18.3a2 2 0 0 1-2.8-2.8L15 7.3",
  upload: "M12 16V4M7 9l5-5 5 5M4 20h16", download: "M12 13v8M8 17l4 4 4-4M4.4 15.3A7 7 0 1 1 15.7 8h1.8a4.5 4.5 0 0 1 2.4 8.3", link: "M10 14a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1M14 10a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1",
  pause: "M7 5h3v14H7zM14 5h3v14h-3z", clone: "M8 8h12v12H8zM4 16V4h12", merge: "M6 3v6a6 6 0 0 0 6 6h6M15 12l3 3-3 3M6 21V9",
  git: "M6 3v12M18 9a3 3 0 1 0 0-.01M6 21a3 3 0 1 0 0-.01M18 12c0 4-6 3-12 6", key: "M15 7a4 4 0 1 1-3.5 6L4 20.5V17h3v-3h3l1.5-1.5A4 4 0 0 1 15 7z",
  user_add: "M9 7a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM2 21v-1a5 5 0 0 1 5-5h4M19 14v6M16 17h6", sitemap: "M9 3h6v5H9zM3 16h6v5H3zM15 16h6v5h-6zM12 8v4M6 16v-4h12v4",
  bolt: "M13 2L4 14h7l-1 8 9-12h-7z", plug: "M9 2v6M15 2v6M6 8h12v4a6 6 0 0 1-12 0zM12 18v4", book: "M4 4h7a3 3 0 0 1 3 3v13a2 2 0 0 0-2-2H4zM20 4h-6a3 3 0 0 0-3 3v13a2 2 0 0 1 2-2h7z",
  grip: "M9 6h.01M15 6h.01M9 12h.01M15 12h.01M9 18h.01M15 18h.01", shield: "M12 2l8 4v6c0 5-3.5 9-8 10-4.5-1-8-5-8-10V6z",
  min: "M7 5l5 5 5-5M4 15h16v4H4z", max: "M14 4h6v6M20 4l-7 7M10 20H4v-6M4 20l7-7", restore: "M4 14h6v6M3 21l7-7M20 10h-6V4M21 3l-7 7", fit: "M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5", eye: "M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zM12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z",
  emergency: "M12 3l10 18H2zM12 10v4M12 17h.01", cpu: "M7 7h10v10H7zM9 3v4M15 3v4M9 17v4M15 17v4M3 9h4M3 15h4M17 9h4M17 15h4",
  system: "M4 4h16v6H4zM4 14h16v6H4zM8 7h.01M8 17h.01",
  files: "M6 2h9l5 5v15H6zM14 2v6h6M9 13h6M9 17h6",
  performance: "M4 19V9M10 19V5M16 19v-7M22 19H2M3 5l5 3 5-4 6 3",
  evolution: "M3 17l6-6 4 4 8-8M14 7h7v7",
  bell: "M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10 21a2 2 0 0 0 4 0",
  settings: "M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z",
  audit: "M9 11l3 3 8-8M20 12v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h9",
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
  machines: "M3 4h18v12H3zM8 20h8M12 16v4M7 8l3 2-3 2M12 12h4",
  specialist: "M12 2a5 5 0 1 0 0 10 5 5 0 0 0 0-10zM4 22a8 8 0 0 1 16 0",
  network: "M12 3a2 2 0 1 0 0 .01M5 17a2 2 0 1 0 0 .01M19 17a2 2 0 1 0 0 .01M12 10a2 2 0 1 0 0 .01M12 5v3M10.3 11.1l-3.6 4.6M13.7 11.1l3.6 4.6M7 17h10",
};
const ROLE_ICON = { general_manager: "general_manager", architect: "architect", developer: "developer", frontend: "frontend", qa: "qa", research: "research_role", external_data: "external_data", analyst: "analyst", comprehensive_analyst: "analyst", security: "security", network_security: "security", security_guard: "security", devops: "devops", tech_scout: "learning", tech_introducer: "learning", instructor: "learning", student: "learning", product: "business", sales: "business", administrator: "admin", agent_manager: "admin" };
// An icon-only button: the label becomes the tooltip and the accessible name.
const ib = (name, label, attrs = "", cls = "") => `<button type="button" class="btn icon ${cls}" title="${esc(label)}" aria-label="${esc(label)}" ${attrs}>${icon(name, 16)}</button>`;
const icon = (name, size = 20) => `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${P[name] || P.specialist}"/></svg>`;
const roleIcon = (role) => icon(ROLE_ICON[role] || "specialist");
const STOP_SCOPE = { E0: "an executor", E1: "an agent", E2: "a goal", E3: "a project", E4: "a room", E5: "the whole company" };  // governance.LEVELS
const GOAL_ACTIVE = ["queued", "planning", "running", "recovering", "paused"];  // workflow.ACTIVE
const pill = (s) => `<span class="pill ${esc(s)}">${esc(String(s).replace(/_/g, " "))}</span>`;
// Usage limits as Claude Code's /usage shows them (Workspace Claude panel, Models page): per window a ring with the
// share used, its label, a severity badge and when it resets. limits: [{label, percent, severity, resets_at (ISO)}].
const LIMIT_SEV = { warning: "near the limit", critical: "at the limit", exceeded: "limit reached" };
const timeLeft = (s) => { const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60); return d ? `${d} d ${h} h` : h ? `${h} h ${m} min` : `${m} min`; };
const limitRing = (pct, sev) => {
  const v = Math.max(0, Math.min(100, pct)), r = 15, len = 2 * Math.PI * r;
  return `<svg class="wc-ring ${LIMIT_SEV[sev] ? esc(sev) : "normal"}" viewBox="0 0 36 36" aria-hidden="true"><circle class="trk" cx="18" cy="18" r="${r}"/>`
    + (v > 0 ? `<circle class="val" cx="18" cy="18" r="${r}" stroke-dasharray="${(len * v / 100).toFixed(2)} ${len.toFixed(2)}" transform="rotate(-90 18 18)"/>` : "")
    + `<text x="18" y="18">${Math.round(v)}%</text></svg>`;
};
function limitCards(limits) {
  const now = Date.now();
  const card = (l) => {
    const at = Date.parse(l.resets_at || ""), pct = Math.round(l.percent);
    const when = at ? new Date(at).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" }) : "";
    return `<div class="wc-limit" role="meter" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}" aria-label="${esc(l.label)}: ${pct}% used" title="${esc(l.label)}: ${pct}% of the limit used${at ? `\nResets ${esc(new Date(at).toLocaleString())}` : ""}">
      ${limitRing(l.percent, l.severity)}<div><b>${esc(l.label)}</b><div><b>${pct}%</b> <span class="muted">used</span>${LIMIT_SEV[l.severity] ? ` <span class="wc-badge ${esc(l.severity)}">${LIMIT_SEV[l.severity]}</span>` : ""}</div>
      ${at ? `<div class="muted">resets ${esc(when)} · in ${timeLeft(Math.max(0, (at - now) / 1000))}</div>` : ""}</div></div>`;
  };
  return `<div class="wc-limits">${limits.map(card).join("")}</div>`;
}

// --- storage-safe helpers -------------------------------------------------------
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
  del(k) { try { localStorage.removeItem(k); } catch { /* ignore */ } },
};

// A scheme switch (HTTP <-> HTTPS, see transport.py) hands the device secret over in the
// URL fragment, which never leaves the browser. Keep it only if this origin has none yet.
(function takeHandoff() {
  const m = location.hash.match(/^#ac-device=([^&]+)/);
  if (!m) return;
  const s = decodeURIComponent(m[1]);
  if (/^[A-Za-z0-9_-]{32,128}$/.test(s) && !store.get("ac.device")) store.set("ac.device", s);
  history.replaceState(null, "", location.pathname + location.search);
})();

// --- API ------------------------------------------------------------------------
// Page data: every GET a page makes is kept (the latest answer per URL). While show() builds a page, a kept
// answer is used at once and the page is refreshed right after; pointing at a rail icon fetches that page's
// data ahead (a far-away browser waits a round trip per page otherwise). Any change (POST, PUT…) drops it all.
const apiCache = new Map(), apiInflight = new Map();
const API_FRESH = 4000, API_KEEP = 200;
const NO_CACHE = /^\/api\/(workspace|files|auth|stream|machines\/)/;  // live panels, consoles and sign-in: always asked
let viewFetch = null;  // { urls, stale } while show() builds a page
async function api(path, opts = {}) {
  const get = !opts.method || opts.method === "GET", cacheable = get && !NO_CACHE.test(path);
  if (!get) apiCache.clear();
  const vf = cacheable && !opts.fresh ? viewFetch : null;
  if (vf) {
    vf.urls.add(path);
    const hit = apiCache.get(path);
    if (hit) { if (Date.now() - hit.at > API_FRESH) vf.stale = true; return structuredClone(hit.data); }
  }
  if (cacheable && apiInflight.has(path)) return structuredClone(await apiInflight.get(path));
  const req = (async () => {
    const res = await fetch(path, { credentials: "same-origin", headers: { "Content-Type": "application/json" }, ...opts, body: opts.body ? JSON.stringify(opts.body) : undefined });
    const data = await res.json().catch(() => ({}));
    if (res.status === 421 && data.code === "wrong_scheme") { location.reload(); throw new Error(data.detail); }  // the hand-off page moves this tab
    if (!res.ok) { if (res.status === 401) apiCache.clear(); const e = new Error(data.detail || data.code || res.statusText); e.status = res.status; e.code = data.code; throw e; }
    if (cacheable) { apiCache.delete(path); apiCache.set(path, { data, at: Date.now() }); if (apiCache.size > API_KEEP) apiCache.delete(apiCache.keys().next().value); }
    return data;
  })();
  if (!cacheable) return req;
  apiInflight.set(path, req);
  try { return structuredClone(await req); } finally { apiInflight.delete(path); }
}
// The URLs each page asked for last time (kept per browser): fetched ahead when the pointer reaches its icon.
const viewUrls = (() => { try { return JSON.parse(store.get("ac.viewUrls") || "{}"); } catch { return {}; } })();
function prefetchView(id) {
  if (id === current || SELF_UPDATING.has(id)) return;
  for (const url of viewUrls[id] || []) {
    const hit = apiCache.get(url);
    if (!apiInflight.has(url) && (!hit || Date.now() - hit.at > API_FRESH)) api(url).catch(() => {});
  }
}
function toast(msg) { const t = document.createElement("div"); t.className = "toast"; t.textContent = msg; document.body.append(t); setTimeout(() => t.remove(), 4000); }

// --- theme ----------------------------------------------------------------------
// ac.theme is "dark" (the default), "light" or "system"; data-theme always holds the resolved "dark" or "light".
const THEMES = ["dark", "light", "system"];
const systemDark = matchMedia("(prefers-color-scheme: dark)");
function themePref() { const t = store.get("ac.theme"); return THEMES.includes(t) ? t : "dark"; }
function applyTheme(pref = themePref()) {
  const dark = pref === "system" ? systemDark.matches : pref !== "light";
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  const b = $("#theme");
  if (b) { b.innerHTML = icon(dark ? "sun" : "moon", 18); b.title = `Theme: ${pref}${pref === "system" ? ` (${dark ? "dark" : "light"} now)` : ""}`; }
}
function toggleTheme() { const t = THEMES[(THEMES.indexOf(themePref()) + 1) % THEMES.length]; store.set("ac.theme", t); applyTheme(t); }
systemDark.addEventListener("change", () => { if (themePref() === "system") applyTheme("system"); });
// Accent per browser (ac.accent); blue is the primary colour, so it sets no attribute.
const ACCENTS = ["indigo", "violet", "blue", "green", "amber", "rose", "grey"];
function accentPref() { const a = store.get("ac.accent"); return ACCENTS.includes(a) ? a : "blue"; }
function applyAccent(name = accentPref()) {
  if (ACCENTS.includes(name) && name !== "blue") document.documentElement.dataset.accent = name;
  else delete document.documentElement.dataset.accent;
}
function setAccent(name) { if (!ACCENTS.includes(name)) return; store.set("ac.accent", name); applyAccent(name); }

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
// Views register here; extra view files call registerView() before start().
const VIEWS = [];
const VIEW_FNS = {};
function registerView(view, fn, at = VIEWS.length) { VIEWS.splice(at, 0, view); VIEW_FNS[view.id] = fn; }
let current = store.get("ac.view") || "workspace", refreshTimer = null, badges = {};

async function boot() {
  try { me = await api("/api/auth/me"); } catch { return tryDevice(); }
  $("#auth").hidden = true; $("#app").hidden = false;
  $("#whoami").textContent = me.name + (me.is_admin ? " · admin" : "");
  $("#my-git").onclick = () => gitForm(me);
  if (me.is_admin) {  // S14-R-01: the stop is one click from every page; while a stop is on it leads to System
    $("#estop").hidden = false;
    $("#estop").onclick = async () => {
      if ($("#estop").classList.contains("on")) return show("system");
      const reason = prompt("Emergency stop (E5): why stop all autonomous work now?");
      if (!reason) return;
      try { const r = await api("/api/emergency", { method: "POST", body: { on: true, reason } }); toast(`Emergency stop: ${r.cancelled_goals.length} goal(s) cancelled, ${r.terminated.length} process(es) ended`); updateBadges(); } catch (e) { toast(e.message); }
    };
  }
  try { const n = await api("/api/ui/nav_layout"); navLayout = n.value || null; navDefault = n.default || NAV_DEFAULT; } catch { navLayout = null; }
  renderRail();
  const linked = parseHash(location.hash);  // a link (#/projects/1) wins, also in Simple mode; ac.view is the fallback
  if (linked) route(linked);
  else show(me.ui_mode === "simple" ? "simple" : VIEWS.some((v) => v.id === current && (!v.admin || me.is_admin)) ? current : "dashboard", { replace: true });
  connectLive();
}
// --- grouped sidebar in the order an administrator set (Settings → Navigation) -----------
// Notifications, the audit log, settings and users are buttons at the top right instead (TOPBAR), never on the rail.
// Keep the legacy top-order export for existing shell consumers. Groups own rendering and persistence.
// Offline mirror of prefs.DEFAULT_NAV_GROUPS (UI-D-02 order with pages of later phases; ids without a view are dropped),
// kept equal by tests/test_nav_layout.py.
const NAV_DEFAULT = { top: ["dashboard", "general-manager", "projects", "goals", "teams", "agents", "companymap", "workflows", "deliverables"], groups: [
  {key:"company", title:"Company", items:["dashboard","general-manager","projects","goals","teams","agents","companymap","workflows","deliverables"]},
  {key:"intelligence", title:"Intelligence", items:["research","knowledge","performance","evaluation","models"]},
  {key:"evolution", title:"Evolution", items:["evolution","organization","changes"]},
  {key:"system", title:"System", items:["workspace","machines","security","system"]}], hidden:[] };
const TOPBAR = ["notifications", "audit", "settings", "users"];
const VIEW_GROUP = Object.assign(Object.fromEntries(NAV_DEFAULT.groups.flatMap(g => g.items.map(id => [id,g.key]))), {simple:"company"});
let navLayout = null, navDefault = NAV_DEFAULT, railEditing = false, lastOverview = null;
function navGroups() {
  const lay = navLayout?.groups ? navLayout : navDefault;
  const vis = VIEWS.filter(v => (!v.admin || me.is_admin) && !TOPBAR.includes(v.id) && (v.id !== "simple" || me.ui_mode === "simple"));
  const by = Object.fromEntries(vis.map(v => [v.id,v]));
  // The editor's rule (placeNav): a default page the layout lacks goes after its predecessor; an unknown id goes last.
  const groups = placeNav(lay.groups, navDefault.groups, [], lay.hidden);
  const saved = new Set([...groups.flatMap(g => g.items), ...lay.hidden]);
  vis.forEach(v => {
    if (saved.has(v.id)) return;
    (groups.find(g => g.key === (v.group || VIEW_GROUP[v.id] || "system")) || groups.at(-1)).items.push(v.id);
  });
  return {groups:groups.map(g => ({...g,items:g.items.filter(id => by[id] && !lay.hidden.includes(id)).map(id => by[id])})), hidden:lay.hidden.filter(id => by[id]).map(id => by[id])};
}
function railButton(v) {
  return `<button data-v="${esc(v.id)}" title="${esc(v.title)}" aria-label="${esc(v.title)}" class="sb-item${v.id === current ? " active" : ""}" draggable="${railEditing}">${icon(v.icon)}<span class="rail-label">${esc(v.title)}</span></button>`;
}
function sidebarDetails() {
  return `<${me.is_admin ? 'a href="#/system"' : 'div'} class="sb-status" id="sb-status" data-state="operating"><span class="sb-dot"></span><span class="sb-status-text"><strong>Operating</strong><span class="muted sb-status-sub"></span></span></${me.is_admin ? 'a' : 'div'}><div class="sb-user" id="sb-user"><span class="avatar">${esc(me.name.slice(0,1))}</span><span class="sb-user-text"><strong>${esc(me.name)}</strong><span class="muted">${me.is_admin ? "Administrator" : "Member"}</span></span></div>`;
}
function groupedMarkup(g) {
  return g.groups.map(x => `<section class="sb-group">${railEditing ? `<input data-group-title maxlength="40" value="${esc(x.title)}"><button data-group-shift="-1" aria-label="Move group up">↑</button><button data-group-shift="1" aria-label="Move group down">↓</button>` : `<h3 data-group-title>${esc(x.title)}</h3>`}<div class="sb-items" data-group="${x.key}">${x.items.map(railButton).join("")}</div></section>`).join("");
}
function wireSidebar(root) {
  root.querySelectorAll("button[data-v]").forEach(b => {
    b.onclick = () => { if (!railEditing) { closeDrawer({ quiet: true }); show(b.dataset.v); } };
    b.onpointerenter = b.onfocus = () => { if (!railEditing) prefetchView(b.dataset.v); };
  });
}
function renderRail() {
  if (!me) return;
  const g = navGroups(), rail = $("#rail");
  rail.classList.add("sidebar");
  rail.innerHTML = `<div class="sb-brand"><span class="sb-logo">${icon("companymap")}</span><div class="sb-brand-text"><strong>Agent Company</strong><span>Autonomous Engineering Organization</span></div><button class="sb-collapse" id="sb-collapse" aria-label="Toggle sidebar">↔</button></div><div class="sb-groups">${groupedMarkup(g)}${railEditing ? `<section class="sb-group"><h3>Hidden</h3><div class="sb-items" data-group="hidden">${g.hidden.map(railButton).join("")}</div></section>` : ""}</div>
    ${me.is_admin ? `<button class="rail-edit" id="rail-edit" title="Edit navigation">${icon(railEditing ? "check" : "grip",18)}</button>` : ""}${sidebarDetails()}
    <div class="sb-bottom">${(g.groups[0]?.items || []).slice(0,4).map(railButton).join("")}<button id="sb-more">More</button></div>`;
  wireSidebar(rail);
  $("#sb-collapse").onclick = () => { store.set("ac.sidebar", $("#app").dataset.labels === "1" ? "collapsed" : "expanded"); applyLayout(); };
  $("#rail-edit") && ($("#rail-edit").onclick = async () => { const lay = railEditing ? readNav(rail) : null; railEditing = !railEditing; if (lay) await saveNav(lay); else renderRail(); applyLayout(); });
  if (railEditing) {
    wireNavDrag(rail, () => {});
    rail.querySelectorAll("[data-group-shift]").forEach(b => b.onclick = () => {
      const col = b.parentElement, next = Number(b.dataset.groupShift) < 0 ? col.previousElementSibling : col.nextElementSibling;
      if (!next || !next.querySelector("[data-group-title]")) return;
      col.parentElement.insertBefore(Number(b.dataset.groupShift) < 0 ? col : next, Number(b.dataset.groupShift) < 0 ? next : col);
    });
  }
  $("#sb-more").onclick = () => { openDrawer("Menu", `<div class="sidebar-menu">${groupedMarkup(g)}${sidebarDetails().replaceAll(' id="sb-status"', "").replaceAll(' id="sb-user"', "")}</div>`); wireSidebar($("#drawer-body")); applyBadges(); applyStatus(lastOverview); };
  renderTopViews(); applyBadges(); setActiveNav(current); applyStatus(lastOverview);
}
function setActiveNav(id) {
  document.querySelectorAll("#rail [data-v], #top-views [data-v]").forEach(b => { const on = b.dataset.v === id; b.classList.toggle("active",on); if (on) b.setAttribute("aria-current","page"); else b.removeAttribute("aria-current"); });
}
function applyBadges() {
  document.querySelectorAll("[data-v]").forEach(b => {
    const n = badges[b.dataset.v], old = b.querySelector(".nav-count");
    if (!n) { old?.remove(); return; }
    if (!old) b.insertAdjacentHTML("beforeend", `<span class="nav-count">${Number(n)}</span>`);
    else if (old.textContent !== String(n)) old.textContent = n;
  });
}
function applyStatus(o) {
  if (!o) return;
  lastOverview = o;
  const state = o.stop_level ? "stop" : (o.goals?.paused && !o.goals?.running) ? "paused" : o.degraded_agents ? "degraded" : "operating";
  const title = state === "stop" ? `Stopped (${o.stop_level})` : state === "degraded" ? `Degraded · ${o.degraded_agents} agent(s)` : state[0].toUpperCase()+state.slice(1), sub = `${o.goals?.running || 0} goals running`;
  document.querySelectorAll(".sb-status").forEach(el => {
    if (el.dataset.state !== state) el.dataset.state = state;
    if (el.title !== title) el.title = title;
    const strong = el.querySelector("strong"), small = el.querySelector(".sb-status-sub");
    if (strong.textContent !== title) strong.textContent = title;
    if (small.textContent !== sub) small.textContent = sub;
  });
}
// The top-bar buttons: the same views as rail icons (active state, badges, prefetch), only placed top right.
function renderTopViews() {
  const box = $("#top-views");
  if (!box || !me) return;
  box.innerHTML = TOPBAR.map((id) => VIEWS.find((v) => v.id === id)).filter((v) => v && (!v.admin || me.is_admin))
    .map((v) => `<button class="icon-btn top-view${v.id === current ? " active" : ""}${v.id === current && $("#view").classList.contains("loading") ? " pending" : ""}" data-v="${v.id}" title="${esc(v.title)}" aria-label="${esc(v.title)}">${icon(v.icon, 18)}${badges[v.id] ? `<span class="nav-count">${badges[v.id]}</span>` : ""}</button>`).join("");
  box.querySelectorAll("button[data-v]").forEach((b) => {
    b.onclick = () => show(b.dataset.v);
    b.onpointerenter = b.onfocus = () => prefetchView(b.dataset.v);
  });
  // REQ-003: the user's own Simple/Advanced switch (not a view, so not in TOPBAR)
  if (VIEW_FNS.simple) {
    const simple = me.ui_mode === "simple", label = simple ? "Switch to Advanced mode (the full dashboard)" : "Switch to Simple mode (mission form and report)";
    box.insertAdjacentHTML("afterbegin", `<button class="icon-btn" id="mode-toggle" title="${label}" aria-label="${label}">${icon(simple ? "dashboard" : "goals", 18)}</button>`);
    $("#mode-toggle").onclick = async () => {
      try {
        const r = await api("/api/auth/me/ui_mode", { method: "PUT", body: { mode: simple ? "advanced" : "simple" } });
        me.ui_mode = r.mode; renderRail(); show(r.mode === "simple" ? "simple" : "dashboard");
      } catch (e) { toast(e.message); }
    };
  }
}
// Drag and drop between the two groups of a navigation list (the rail or the Settings card).
function wireNavDrag(root, changed) {
  let dragging = null;
  root.querySelectorAll("[data-v]").forEach((b) => {
    b.draggable = true;
    b.ondragstart = (e) => { dragging = b; b.classList.add("dragging"); e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", b.dataset.v); };
    b.ondragend = () => { b.classList.remove("dragging"); dragging = null; changed(readNav(root)); };
  });
  root.querySelectorAll("[data-group]").forEach((grp) => (grp.ondragover = (e) => {
    if (!dragging) return;
    e.preventDefault();
    const after = [...grp.querySelectorAll("[data-v]:not(.dragging)")].find((x) => { const r = x.getBoundingClientRect(); return grp.dataset.dir === "row" ? e.clientX < r.left + r.width / 2 : e.clientY < r.top + r.height / 2; });
    grp.insertBefore(dragging, after || null);
  }));
}
// Restore unseen pages without consulting the DOM; inputs remain unchanged.
function placeNav(groups, defaults, registered, hidden = []) {
  const placed = groups.map(g => ({...g, items: [...g.items]}));
  const present = new Set([...placed.flatMap(g => g.items), ...hidden]);
  defaults.forEach(g => g.items.forEach((id, i) => {
    const to = placed.find(x => x.key === g.key);
    if (!to || present.has(id) || registered.includes(id)) return;
    const prev = g.items.slice(0, i).reverse().find(x => to.items.includes(x));
    to.items.splice(prev ? to.items.indexOf(prev) + 1 : 0, 0, id);
    present.add(id);
  }));
  return placed;
}
const readNav = (root) => {
  const ids = el => [...el.querySelectorAll("[data-v]")].map(b => b.dataset.v);
  const groups = [...root.querySelectorAll("[data-group]")].filter(el => el.dataset.group !== "hidden").map(el => {
    const title = el.parentElement.querySelector("[data-group-title]");
    return {key:el.dataset.group, title:title?.value ?? title?.textContent ?? el.dataset.group, items:ids(el)};
  });
  // Preserve unregistered and mode/permission-filtered views when editing the visible navigation.
  const present = new Set([...groups.flatMap(g => g.items), ...ids(root.querySelector('[data-group="hidden"]') || document.createElement('div'))]);
  const placed = placeNav(groups, (navLayout || navDefault).groups,
    VIEWS.filter(v => (!v.admin || me.is_admin) && (v.id !== "simple" || me.ui_mode === "simple")).map(v => v.id), [...present]);
  const hidden = root.querySelector('[data-group="hidden"]');
  return {groups:placed, hidden:hidden ? [...new Set([...ids(hidden), ...(navLayout?.hidden || []).filter(id => !VIEWS.some(v => v.id === id))])] : (navLayout?.hidden || [])};
};
async function saveNav(lay) {
  try { navLayout = (await api("/api/ui/nav_layout", { method: "PUT", body: { value: lay } })).value; toast("Navigation saved for every user"); } catch (e) { toast(e.message); }
  renderRail();
}
async function updateBadges() {
  try {
    const o = await api("/api/overview", { fresh: true });
    if (o.build && BUILD && o.build !== BUILD) newBuild = true;  // reloaded on the next page switch
    // Audit records after the newest one this browser showed; counting starts on the first visit.
    if (store.get("ac.auditSeen") === null || current === "audit") store.set("ac.auditSeen", String(o.audit_last || 0));
    const audit = Math.max(0, (o.audit_last || 0) - Number(store.get("ac.auditSeen") || 0));
    // Projects: what blocks or occupies work (merges waiting, goals running); pending proposals are listed on the page.
    badges = { users: o.pending_users || 0, notifications: o.unread || 0, audit, projects: (o.goals.awaiting_approval || 0) + (o.goals.running || 0) };
    const es = $("#estop");
    if (es) { es.classList.toggle("on", !!o.stopped); es.title = o.stopped ? `A stop is active (${o.stop_level}): open System to see or lift it` : "Emergency stop: stop all autonomous work now"; }
    const sl = $("#stop-level");  // S15-R-01: every user sees that work is stopped, and how widely
    if (sl) { sl.hidden = !o.stop_level; sl.textContent = o.stop_level ? `${o.stop_level} stop` : ""; sl.title = o.stop_level ? `${o.stop_level} stop active (${STOP_SCOPE[o.stop_level]}): autonomous work is ${o.stop_level === "E5" ? "stopped company-wide" : "partly stopped"}` : ""; }
    applyBadges(); applyStatus(o); setFavicon(o);
  } catch (e) { if (!e.status || e.status >= 502) setFavicon(null); /* else signed out */ }
}
// The page each view showed last: painted at once when the user comes back, while the fresh one loads
// (a far-away browser waits a round trip or two for it). Views that keep their own windows are not kept.
const viewCache = {};
let showSeq = 0;
// The build this page was loaded from; after a deploy the next page switch loads the new one (not over unsaved edits).
const BUILD = document.querySelector('meta[name="ac-build"]')?.content || "";
let newBuild = false;
function enter() {
  const view = $("#view");
  view.classList.remove("enter"); void view.offsetWidth; view.classList.add("enter");
  clearTimeout(enter.timer); enter.timer = setTimeout(() => view.classList.remove("enter"), 700);  // live re-renders don't animate
}
function show(id, opts = {}) {
  if (!VIEWS.some(v => v.id === id && (!v.admin || me?.is_admin))) id = "dashboard";
  const view = $("#view");
  if (newBuild && id !== current && !window.WSP?.dirtyTabs?.()) { store.set("ac.view", id); writeHash("#/" + id, true); location.reload(); return; }
  if (id !== current) {
    VIEWS.find((x) => x.id === current)?.leave?.();  // e.g. Machines closes its console
    if (!SELF_UPDATING.has(current) && !view.classList.contains("loading"))
      viewCache[current] = { html: view.innerHTML, cls: ["flush", "fit"].filter((c) => view.classList.contains(c)) };
  }
  current = id; store.set("ac.view", id);
  if (!opts.fromHash) { closeModal(false); closeDrawer({ quiet: true }); writeHash(opts.hash || "#/" + id, opts.replace); }
  setCrumbs([]);
  view.classList.remove("flush", "fit"); view.scrollTop = 0;  // a new page starts at its top
  const v = VIEWS.find((x) => x.id === id); $("#view-title").textContent = v.title; setActiveNav(id);
  const snap = !SELF_UPDATING.has(id) && viewCache[id];
  if (snap) { view.innerHTML = snap.html; view.classList.add(...snap.cls); enter(); }
  else if (!SELF_UPDATING.has(id)) view.innerHTML = `<div class="view-wait muted"><span class="spin"></span> Loading ${esc(v.title)}…</div>`;
  // The bar and an inert page until the view has its data; the last page is only a picture of it.
  const seq = ++showSeq;
  show.overwritten = false;
  view.classList.remove("stale"); view.classList.add("loading"); view.inert = !!snap; $("#loadbar").hidden = false;
  document.querySelector(`#rail button[data-v="${id}"], #top-views button[data-v="${id}"]`)?.classList.add("pending");
  const vf = SELF_UPDATING.has(id) ? null : { urls: new Set(), stale: false };
  const done = async () => {
    if (vf && viewFetch === vf) viewFetch = null;
    if (seq !== showSeq || !view.classList.contains("loading")) return;
    if (show.overwritten) {  // a page the user had left finished over this one: build this one again
      show.overwritten = false; view.classList.remove("flush", "fit");
      await render();
      if (seq !== showSeq) return;
    }
    view.classList.remove("loading", "stale"); view.inert = false; $("#loadbar").hidden = true;
    document.querySelectorAll("#rail button.pending, #top-views button.pending").forEach((b) => b.classList.remove("pending"));
    iconize(view);  // now, not 300 ms later: buttons don't change shape after the page appears
    if (!snap) enter();
    if (vf) {
      if (vf.urls.size) { viewUrls[id] = [...vf.urls].slice(0, 12); store.set("ac.viewUrls", JSON.stringify(viewUrls)); }
      // It was drawn from data kept from before: now with fresh data (not under a form being filled in).
      if (vf.stale && !document.activeElement?.closest("form:not(#composer)")) render(true).then(() => seq === showSeq && iconize(view));
    }
  };
  clearTimeout(show.guard); show.guard = setTimeout(done, 15000);  // a request that never answers must not lock the page
  clearInterval(refreshTimer);
  viewFetch = vf;
  render().finally(done);
  updateBadges();
  // Live events push changes; polling stays as a slower fallback when the stream is down.
  refreshTimer = setInterval(() => { if (!liveOk) refreshNow(); }, 5000);
}
// Views that manage their own updates (terminals, consoles, editors) are never re-rendered by live events.
const SELF_UPDATING = new Set(["workspace", "machines"]);
function refreshNow() {
  if (document.hidden || !$("#modal").hidden || document.activeElement?.closest("form:not(#composer)")) return;
  if ($("#view").classList.contains("loading")) return;  // the page is still being built
  if (!SELF_UPDATING.has(current)) render(true).then(() => iconize($("#view")));  // buttons keep their icons through a refresh
  updateBadges();
}
let liveOk = false, liveSource = null, liveTimer = null;
function connectLive() {
  if (liveSource || typeof EventSource === "undefined") return;
  liveSource = new EventSource("/api/stream");
  liveSource.onopen = () => { liveOk = true; };
  liveSource.onmessage = () => { clearTimeout(liveTimer); liveTimer = setTimeout(refreshNow, 700); };
  liveSource.onerror = () => {
    liveOk = false; liveSource.close(); liveSource = null;
    api("/api/auth/me").catch(() => {});  // a 421 here (scheme switched) moves the tab via the hand-off page
    setTimeout(() => me && connectLive(), 10000);
  };
}
async function render(quiet) {
  const id = current, fn = VIEW_FNS[id];
  try { await fn(quiet); } catch (e) { if (e.status === 401) return tryDevice(); if (!quiet && id === current) $("#view").innerHTML = `<div class="card">Could not load: ${esc(e.message)}</div>`; }
  // A slow page that finished after the user moved on wrote over the newer one: hide it, and draw the
  // current page again (at once, or when its own load ends: it may have been building into what was replaced).
  if (id !== current) {
    if (!$("#view").classList.contains("loading")) { $("#view").classList.remove("flush", "fit"); render(); }
    else { show.overwritten = true; $("#view").classList.add("stale"); }
  }
}
// Hash routes and breadcrumbs share the existing page/detail renderers.
let lastHash = location.hash, pageCrumbs = [];
function writeHash(hash, replace = false) {
  if (location.hash !== hash) history[replace ? "replaceState" : "pushState"](null, "", hash);
  lastHash = location.hash;
}
function parseHash(hash) {
  const m = /^#\/([a-z][a-z0-9-]*)(?:\/(\d+))?\/?$/.exec(hash);
  return m ? {view: m[1], id: ["projects", "goals", "agents"].includes(m[1]) && m[2] ? Number(m[2]) : null} : null;
}
function route(r) {
  if (!me) return;
  if (!r || !VIEWS.some(v => v.id === r.view && (!v.admin || me.is_admin))) {
    r = {view: "dashboard"}; writeHash("#/dashboard", true);
  }
  closeModal(false); closeDrawer({ quiet: true });
  if (r.view === "projects" && r.id) { selectedProject = r.id; store.set("ac.project", r.id); }
  show(r.view, {fromHash: true});
  if (r.view === "goals" && r.id) goalModal(r.id);
  if (r.view === "agents" && r.id) agentModal(r.id).catch(e => toast(e.message));
}
addEventListener("hashchange", () => {
  if (location.hash === lastHash) return;
  lastHash = location.hash;
  const r = parseHash(location.hash);
  r && route(r);
});
window.setCrumbs = function setCrumbs(items) {
  items = items.slice(0, 4).map(x => ({label: String(x.label ?? ""), href: x.href}));
  if ($("#modal").hidden) pageCrumbs = items;
  const key = JSON.stringify([current, items]), root = $("#crumbs");
  if (root.dataset.key === key) return;  // a live redraw sets the same trail: keep the links in place
  root.dataset.key = key;
  const title = document.createElement("h1");
  title.id = "view-title"; title.textContent = VIEWS.find(v => v.id === current)?.title || "Dashboard";
  const first = document.createElement("li");
  if (items.length) { const a = document.createElement("a"); a.href = "#/" + current; a.append(title); first.append(a); }
  else { first.append(title); first.setAttribute("aria-current", "page"); }
  root.replaceChildren(first);
  items.forEach((x, i) => {
    const li = document.createElement("li"), a = document.createElement("a");
    a.textContent = x.label;
    if (i < items.length - 1 && /^#\//.test(x.href || "")) a.href = x.href;
    if (i === items.length - 1) li.setAttribute("aria-current", "page");
    li.append(a); root.append(li);
  });
};
function closeModal(updateHash = true) {
  $("#modal").hidden = true;
  setCrumbs(pageCrumbs);
  if (updateHash && /^#\/(goals|agents)\/\d+/.test(location.hash)) writeHash("#/" + current, true);
}
function initTopSearch() {
  const input = $("#top-search"), box = $("#top-search-results");
  let projects = [], fetched = 0, pending = null, results = [], active = 0;
  const close = () => { box.hidden = true; input.setAttribute("aria-expanded", "false"); };
  const open = i => { const r = results[i]; if (!r) return; close(); input.blur(); if (r.project) location.hash = "#/projects/" + r.id; else show(r.id); };
  function draw() {
    const q = input.value.trim().toLowerCase();
    const pages = navGroups().groups.flatMap(g => g.items);
    results = [...pages.filter(v => (v.title + " " + v.id).toLowerCase().includes(q)).map(v => ({...v, label:v.title})),
      ...projects.filter(p => (p.name + " " + (p.github_url || "")).toLowerCase().includes(q)).map(p => ({...p, label:p.name, project:true}))].slice(0,8);
    active = Math.min(active, Math.max(0, results.length - 1)); box.replaceChildren();
    results.forEach((r,i) => {
      const b = document.createElement("button"); b.type = "button"; b.setAttribute("role", "option"); b.setAttribute("aria-selected", String(i === active));
      const glyph = document.createElement("span"); glyph.innerHTML = icon(r.project ? VIEWS.find(v => v.id === "projects")?.icon : r.icon, 16);
      const label = document.createElement("span"); label.textContent = r.label;
      const kind = document.createElement("small"); kind.textContent = r.project ? "Project" : "Page";
      b.append(glyph, label, kind); b.onmousedown = e => e.preventDefault(); b.onclick = () => open(i); box.append(b);
    });
    box.hidden = !results.length; input.setAttribute("aria-expanded", String(!box.hidden));
  }
  input.onfocus = async () => {
    if (!me) return;
    draw();
    if (Date.now() - fetched < 60000) return;
    try {
      pending ||= api("/api/projects");
      projects = (await pending).filter(p => !["archived", "cancelled"].includes(p.status)); fetched = Date.now();
      if (document.activeElement === input) draw();
    } catch { /* pages remain searchable offline */ } finally { pending = null; }
  };
  input.oninput = () => { active = 0; if (me) draw(); };
  input.onblur = close;
  input.onkeydown = e => {
    if (["ArrowDown", "ArrowUp"].includes(e.key)) { e.preventDefault(); active = (active + (e.key === "ArrowDown" ? 1 : -1) + results.length) % (results.length || 1); draw(); }
    if (e.key === "Enter") { e.preventDefault(); open(active); }
    if (e.key === "Escape") { e.stopPropagation(); close(); }
  };
  document.addEventListener("keydown", e => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k" && !e.target.closest?.(".xterm")) { e.preventDefault(); input.focus(); }
  });
}

function modal(html) { $("#modal-body").innerHTML = html; $("#modal").hidden = false; }
$("#modal-close").onclick = closeModal;
$("#modal").onclick = (e) => { if (e.target.id === "modal") closeModal(); };
// The drawer: one right-side panel under the modal. A quiet redraw of the page never touches it.
let drawerState = null;
function openDrawer(title, html, { onClose } = {}) {
  const d = $("#drawer");
  if (!d) return;
  if (!drawerState) drawerState = { back: document.activeElement };
  drawerState.onClose = onClose;
  $("#drawer-title").textContent = title;
  $("#drawer-body").innerHTML = html;
  d.hidden = false;
  iconize(d);
  $("#drawer-close").focus();
}
function closeDrawer(opts) {  // opts.quiet: leaving the page (show/route), so onClose must not reopen anything there
  const d = $("#drawer");
  if (!d || d.hidden) return;
  d.hidden = true;
  const st = drawerState; drawerState = null;
  if (st?.onClose && !opts?.quiet) try { st.onClose(); } catch { /* ignore */ }
  if (st?.back && st.back.isConnected && typeof st.back.focus === "function") st.back.focus();
}
$("#drawer-close") && ($("#drawer-close").onclick = closeDrawer);
// Escape closes the topmost layer: the modal if it is open, otherwise the drawer.
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (!$("#modal").hidden) closeModal();
  else if ($("#drawer") && !$("#drawer").hidden) closeDrawer();
});

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

// --- goals: the Goals page (a new goal, the list by status); a goal opens in a modal
let selectedGoal = null, goalFilter = "all";
function goalModal(id) {
  selectedGoal = id;
  writeHash("#/goals/" + id);
  modal(`<div id="goal-detail"><div class="cc-working"><span class="spin"></span> Loading goal #${Number(id)}…</div></div>`);
  setCrumbs([...pageCrumbs, {label: "Goal #" + id}]);
  goalDetail(id).catch((e) => {
    if (selectedGoal === id && $("#goal-detail")) $("#goal-detail").innerHTML = `<p class="muted">${esc(e.message)}</p>`;
  });
}
const GOAL_FILTERS = { all: "All", active: "Active", awaiting_approval: "Awaiting approval", approved: "Merged",
  failed: "Failed", cancelled: "Cancelled" };
async function viewGoals(quiet) {
  const goals = await api("/api/goals");
  if (!quiet || !$("#goal-list")) {  // a live refresh keeps a half-typed goal
    $("#view").innerHTML = `<div class="grid two"><div class="card"><h2>New goal</h2><form id="goal-form">
      <label for="g-title">Goal</label><input id="g-title" required minlength="3" maxlength="256" placeholder="Build a REST API for a todo list">
      <label for="g-desc">Details</label><textarea id="g-desc" placeholder="Constraints, acceptance criteria, tech choices"></textarea>
      <label for="g-kind">Kind</label><select id="g-kind"><option value="">the planner picks</option>${["bugfix", "feature", "refactor", "research", "docs"].map((k) => `<option>${k}</option>`).join("")}</select>
      <label for="g-urls">Research URLs (one per line)</label><textarea id="g-urls" rows="2" placeholder="https://docs.example.com/guide"></textarea>
      ${me.is_admin ? `<label for="g-repo">Existing git repository (optional; default: new workspace)</label><input id="g-repo" placeholder="/home/you/project">
      <label for="g-checks">Quality commands (one per line, run in the worktree)</label><textarea id="g-checks" rows="2" placeholder="uv run pytest -q"></textarea>` : ""}
      <label for="g-allow">Allowed paths (globs, one per line; empty = anywhere)</label><textarea id="g-allow" rows="2" placeholder="src/*"></textarea>
      <label class="row" style="margin-top:8px"><input type="checkbox" id="g-protect" style="width:auto"> Protect existing tests (agents may add tests, not change them)</label>
      <label for="g-iter">Max iterations</label><input id="g-iter" type="number" min="1" max="10" value="3">
      <div class="row" style="margin-top:12px"><button class="btn primary">Start</button></div></form></div>
      <div class="card"><div class="row"><h2 class="grow" style="margin:0">Goals</h2><select id="goal-filter" style="width:auto">${Object.entries(GOAL_FILTERS).map(([k, l]) => `<option value="${k}"${k === goalFilter ? " selected" : ""}>${l}</option>`).join("")}</select></div>
      <div class="list" id="goal-list" style="margin-top:8px"></div></div></div>`;
    $("#goal-form").onsubmit = async (e) => {
      e.preventDefault();
      const lines = (id) => ($(id)?.value || "").split("\n").map((x) => x.trim()).filter(Boolean);
      try {
        const r = await api("/api/goals", { method: "POST", body: { title: $("#g-title").value.trim(), description: $("#g-desc").value, kind: $("#g-kind").value, urls: lines("#g-urls"), repo_path: $("#g-repo")?.value.trim() || "", quality_commands: lines("#g-checks"), allowed_paths: lines("#g-allow"), protect_tests: $("#g-protect").checked, max_iterations: Number($("#g-iter").value) || null } });
        e.target.reset(); toast(`Goal #${r.id} started`); viewGoals(true); goalModal(r.id);
      } catch (err) { toast(err.message); }
    };
    $("#goal-filter").onchange = (e) => { goalFilter = e.target.value; viewGoals(true); };
  }
  const shown = goals.filter((g) => goalFilter === "all" || (goalFilter === "active" ? GOAL_ACTIVE.includes(g.status) : g.status === goalFilter));
  $("#goal-list").innerHTML = shown.map((g) => `<div class="list-item" data-id="${Number(g.id)}"><div class="row"><strong class="grow">${esc(g.title)}</strong>${pill(g.status)}</div><div class="muted">#${Number(g.id)}${g.kind ? ` · ${esc(g.kind)}` : ""} · ${esc(g.origin || "user")} · iteration ${Number(g.iteration)}/${Number(g.max_iterations)} · ${ago(g.updated_at)}</div></div>`).join("")
    || `<p class="muted">${goals.length ? "No goal with this status." : "No goals yet."}</p>`;
  $("#goal-list").querySelectorAll(".list-item").forEach((el) => (el.onclick = () => goalModal(Number(el.dataset.id))));
}
async function goalDetail(id, openIds = new Set()) {
  const detail = $("#goal-detail");
  const g = await api(`/api/goals/${id}`);
  if (!detail || detail !== $("#goal-detail") || $("#modal").hidden || selectedGoal !== id) return;
  const iters = [...new Set(g.tasks.map((t) => t.iteration))].sort((a, b) => b - a);
  const actions = [
    g.status === "awaiting_approval" && (me.is_admin || (me.delegations || []).some((d) => d.permission === "approve_goals" && d.project_id === g.project_id)) ? ib("check", "Approve and merge", 'data-act="approve"', "primary") + ib("x", "Reject", 'data-act="reject"', "danger") : "",
    GOAL_ACTIVE.includes(g.status) && g.status !== "paused" && me.is_admin ? ib("pause", "Pause the goal (tasks already started finish)", 'data-act="pause"') : "",
    g.status === "paused" && me.is_admin ? ib("play", "Resume the goal from its checkpoint", 'data-act="resume"', "primary") : "",
    GOAL_ACTIVE.includes(g.status) ? ib("stop", "Cancel the goal", 'data-act="cancel"', "danger") : "",
    me.is_admin && !["approved", "rejected", "cancelled"].includes(g.status) ? ib("settings", "Change the goal's budget (cost, tokens, agents at once)", 'data-budget="1"') : "",
    ib("clock", "Execution trace: every run, gate and operation in order", 'data-trace="1"'),
    g.status === "approved" && g.branch ? ib("download", "Software bill of materials of the merged commit (CycloneDX JSON)", 'data-sbom="1"') : "",
  ].join("");
  // S04-R-09: the goal's budget per dimension; a goal's budget also covers the child goals its proposals spawn
  const b = g.budget || { limits: {}, used: {}, remaining: {} }, lim = b.limits || {};
  const budgetLine = [
    lim.cost_usd ? `cost ${money(b.used.cost_usd)} of ${money(lim.cost_usd)}${b.remaining.cost_usd != null ? ` (${money(b.remaining.cost_usd)} left with child goals)` : ""}` : "",
    lim.tokens ? `tokens ${fmt.format(b.used.tokens)} of ${fmt.format(lim.tokens)}` : "",
    lim.agents ? `at most ${lim.agents} agent${lim.agents === 1 ? "" : "s"} at once` : "",
  ].filter(Boolean).join(" · ");
  $("#goal-detail").innerHTML = `<div class="row" style="padding-right:24px"><h2 class="grow" style="margin:0">#${g.id} ${esc(g.title)}</h2>${pill(g.status)}${actions}</div>
    <p class="muted">Iteration ${g.iteration}/${g.max_iterations} · branch <code>${esc(g.branch || "-")}</code> · worktree <code>${esc(g.worktree || "-")}</code>${g.estimated_cost_usd ? ` · estimate ${money(g.estimated_cost_usd)}` : ""}</p>
    ${g.time ? `<p class="muted" title="S05-R-05: estimate = the planner's estimated_minutes of this iteration's tasks; active = time in agent runs; waiting = queued or awaiting approval; blocked = paused">Time: ${g.time.estimated ? `estimated ${dur(g.time.estimated)} · ` : ""}active ${dur(g.time.active)} · waiting ${dur(g.time.waiting)} · blocked ${dur(g.time.blocked)}</p>` : ""}
    ${g.digest && !["queued", "planning", "running", "recovering"].includes(g.status) ? `<p class="muted" title="G-R-09 mission digest: the last iteration's tasks, time since the order, tokens and cost of every run, merge state">Digest: ${Number(g.digest.tasks_done)}/${Number(g.digest.tasks)} task(s) passed · ${Number(g.digest.tokens).toLocaleString()} tokens · $${Number(g.digest.cost).toFixed(4)} · ${esc(g.digest.merge)}</p>` : ""}
    ${budgetLine ? `<p class="muted">Budget: ${budgetLine}${g.parent_goal_id ? ` · shared from goal #${Number(g.parent_goal_id)}` : ""}</p>` : ""}
    ${g.status === "paused" && g.paused_for ? `<p class="muted">${g.paused_for === "cost_overrun" ? "Paused: it cost more than twice its estimate. Resuming accepts the cost so far as the new estimate." : "Paused: its budget is used up. Raise the goal's budget (the settings button above, or Settings for every goal) before resuming."}</p>` : ""}
    ${g.result ? `<pre>${esc(g.result)}</pre>` : ""}
    ${Object.keys(g.baseline || {}).length ? `<h3>Before any change</h3>${Object.entries(g.baseline).map(([c, r]) => `<div class="muted">${r.passed ? "✓" : "✗"} <code>${esc(c)}</code> ${r.passed ? "passed" : "already failing"}</div>`).join("")}` : ""}
    ${g.test_strategy?.risk ? (() => { const ts = g.test_strategy; return `<h3>Test strategy <span class="muted">(iteration ${Number(ts.iteration)})</span></h3><div class="row"><span class="pill ${ts.risk === "high" ? "failed" : ts.risk === "medium" ? "pending" : "done"}">risk ${esc(ts.risk)}</span><span class="grow muted">${esc(ts.reasons.join("; "))}</span></div>${ts.mandatory_stages.length ? `<div class="muted">Mandatory stages: ${esc(ts.mandatory_stages.join(", "))} · reviewers: ${esc(ts.reviewers.join(", "))}</div>` : ""}${Object.keys(ts.defect_history || {}).length ? `<div class="muted">Files with earlier failures: ${Object.entries(ts.defect_history).map(([f, gs]) => `${esc(f)} (${gs.map((x) => `#${Number(x)}`).join(", ")})`).join(", ")}</div>` : ""}`; })() : ""}
    ${Object.keys(g.history?.at(-1)?.stages || {}).length ? `<h3>Gate by stage <span class="muted">(iteration ${g.history.at(-1).iteration})</span></h3><div class="row">${Object.entries(g.history.at(-1).stages).map(([st, v]) => `<span class="pill ${v === "pass" ? "done" : v === "fail" ? "failed" : v === "waiting" ? "pending" : ""}" title="${esc(st)}: ${esc(v)}">${esc(st)} ${v === "pass" ? "✓" : v === "fail" ? "✗" : v === "waiting" ? "…" : "–"}</span>`).join("")}</div>` : ""}
    ${(g.history?.at(-1)?.verification || []).length ? `<h3>Verification <span class="muted" title="S09-R-04: the gate ran these commands itself; an agent's report never counts as evidence">(run by the gate${Object.entries(g.history.at(-1).environment || {}).length ? ` · ${esc(Object.entries(g.history.at(-1).environment).map(([k, v]) => `${k} ${v}`).join(" · "))}` : ""})</span></h3>${g.history.at(-1).verification.map((v) => `<details><summary class="muted">${v.passed ? "✓" : "✗"} <code>${esc(v.command)}</code> · ${v.timed_out ? "timed out" : v.exit_code == null ? "not run" : `exit ${Number(v.exit_code)}`} · ${Number(v.duration_s || 0).toFixed(1)} s · ${esc(v.isolation || "-")} · <code>${esc((v.commit || "").slice(0, 10))}</code></summary><pre>${esc(v.output_tail || "")}</pre></details>`).join("")}` : ""}
    ${(() => { const d = g.history?.at(-1)?.dependencies || {}; const ch = d.changes || []; const notes = d.notes || []; return ch.length || notes.length ? `<h3>Dependencies <span class="muted" title="S08-R-05: dependency changes against the base branch; audited with pip-audit / npm audit when installed">(${ch.length} change${ch.length === 1 ? "" : "s"})</span></h3>${ch.length ? `<div class="table-wrap"><table><tr><th>Manifest</th><th>Dependency</th><th>Change</th><th>Before</th><th>After</th></tr>${ch.slice(0, 50).map((c) => `<tr><td><code>${esc(c.manifest)}</code></td><td>${esc(c.name || "–")}</td><td>${esc(c.change)}</td><td><code>${esc(c.before || "")}</code></td><td><code>${esc(c.after || "")}</code></td></tr>`).join("")}</table></div>` : ""}${notes.map((n) => `<p class="muted">${esc(n)}</p>`).join("")}` : ""; })()}
    ${(g.history || []).length ? `<h3>Iterations</h3><div class="row">${g.history.map((h) => `<span class="pill ${h.passed ? "done" : "failed"}" title="${esc(h.commit || "")}">#${h.iteration} ${Math.round(h.score * 100)}%</span>`).join("")}</div>` : ""}
    ${g.kind ? `<p class="muted" title="S04-R-11: the goal kind's workflow template and the version it runs under">Workflow: ${esc(g.strategy?.name || g.kind)} v${Number(g.strategy?.version || 0)} · ${esc({ order: "named by the order", planner: "picked by the planner", inferred: "inferred from the plan (a label only)" }[g.strategy?.source] || g.strategy?.source || "")}${g.strategy?.source !== "inferred" ? ` · ${g.strategy?.writes ? "changes files" : "no file changes"} · ${esc(g.strategy?.execution || "")} · checks ${esc(g.strategy?.checks || "")}` : ""}</p>` : ""}
    ${(g.allowed_paths || []).length || g.protect_tests ? `<p class="muted">Scope: ${esc((g.allowed_paths || []).join(", ") || "anywhere")}${g.protect_tests ? " · existing tests protected" : ""}</p>` : ""}
    ${iters.map((it) => `<h3>Iteration ${it}</h3>${g.tasks.filter((t) => t.iteration === it).map((t) => `<details data-id="t${t.id}" ${openIds.has("t" + t.id) ? "open" : ""}><summary><span class="row" style="display:inline-flex"><span title="S05-R-08 stage (status ${esc(t.status)})${esc((t.stage_log || []).map((x) => `\n${x.at.slice(0, 19).replace("T", " ")} ${x.from} → ${x.to}${x.reason ? `: ${x.reason}` : ""}`).join(""))}">${pill(t.stage || t.status)}</span> <strong>${esc(t.title)}</strong> <span class="muted">${esc(t.role)} · ${esc(t.model || "-")}</span></span></summary>
      ${(t.gate?.acceptance || []).map((c) => `<div class="muted">${c.passed ? "✓" : "✗"} ${esc(JSON.stringify(c.condition))} ${esc(c.detail || "")}</div>`).join("")}
      ${t.gate?.files?.length ? `<div class="muted">Files: ${esc(t.gate.files.join(", "))}</div>` : ""}
      ${t.gate?.recommendation ? `<div class="muted">Recommends: ${esc(t.gate.recommendation)}</div>` : ""}
      ${(t.gate?.blockers || []).map((f) => `<div class="muted">Blocker: ${esc(f)}</div>`).join("")}${(t.gate?.findings || []).map((f) => `<div class="muted">Finding: ${esc(f)}</div>`).join("")}${(t.gate?.next_actions || []).map((n) => `<div class="muted">Next: ${esc(n)}</div>`).join("")}<pre>${esc(t.output || "(no output yet)")}</pre></details>`).join("")}`).join("")}
    ${(g.defects || []).length ? `<h3>Defects</h3>${g.defects.map((d) => { const v = d.verification || {}; return `<div class="row">${pill(d.status)}<span class="pill" title="priority (1 first)">P${Number(d.priority)}</span><span class="pill">${esc(d.severity)}</span><span class="grow" title="${esc((d.history || []).map((h) => `${h.at.slice(0, 19).replace("T", " ")} ${h.from || "–"} → ${h.to} by ${h.by}${h.note ? `: ${h.note}` : ""}`).join("\n"))}">#${d.id} ${esc(d.check)}${d.assignee ? ` <span class="muted">· ${esc(d.assignee)}</span>` : ""}${d.duplicate_of ? ` <span class="muted">· duplicate of #${Number(d.duplicate_of)}</span>` : ""}${d.resolution ? ` <span class="muted">· ${esc(d.resolution)}</span>` : ""}${v.how ? `<div class="muted">${v.how === "retest" ? "retest" : "gate"} ${v.passed ? "passed" : "failed"}${v.commit ? ` on ${esc(String(v.commit).slice(0, 10))}` : ""}${v.exit_code != null ? ` · exit ${Number(v.exit_code)}` : ""}${v.duration_s != null ? ` · ${Number(v.duration_s)}s` : ""}</div>` : ""}</span><span class="muted">iterations ${d.first_iteration}–${d.closed_iteration || d.last_iteration}</span>${me.is_admin && typeof defectActions === "function" ? defectActions(d) : ""}</div>`; }).join("")}` : ""}
    ${(g.rca || []).map((a) => `<h3>Root-cause analysis <span class="muted">K${Number(a.id)} · ${esc(a.trigger)}${a.goals.length > 1 ? ` · goals ${a.goals.map((x) => "#" + Number(x)).join(", ")}` : ""}${a.status !== "approved" ? ` · ${esc(a.status)} (screened, waits for review)` : ""}</span></h3>
      ${a.confirmed.map((c) => `<div><strong>Confirmed:</strong> ${c.layer ? `<span class="pill">${esc(c.layer)}</span> ` : ""}${esc(c.cause)} <span class="muted">${esc(c.evidence.join(", "))}</span></div>`).join("")}${a.contributing.map((c) => `<div class="muted">Contributing: ${esc(c.cause)}${c.evidence.length ? ` (${esc(c.evidence.join(", "))})` : ""}${c.demoted ? ` — stated as a cause, ${esc(c.demoted)}` : ""}</div>`).join("")}
      ${a.actions.map((x) => `<div class="muted">Action (proposal #${Number(x.proposal_id)}): ${esc(x.title)} · ${esc(x.result)}${x.goal_id ? ` in goal #${Number(x.goal_id)}` : ""}</div>`).join("")}${a.follow_up ? `<div class="muted">Follow-up: ${esc(a.follow_up)}</div>` : ""}`).join("")}
    ${(g.decisions || []).length ? `<h3>Decisions</h3>${g.decisions.map((d) => `<div class="card" style="margin:6px 0;padding:8px 10px"><div class="row">${pill(d.status)}<strong class="grow">#${d.id} ${esc(d.topic)}</strong><span class="pill">${esc(d.category)}</span><span class="muted" title="an estimate${d.confidence_note ? `: ${esc(d.confidence_note)}` : ""}">confidence ~${Math.round(d.confidence * 100)}%</span>
        ${me.is_admin && ["submitted", "under_review", "escalated", "draft"].includes(d.status) ? `<button class="btn primary" data-dec="${d.id}" data-ok="1">Approve</button><button class="btn danger" data-dec="${d.id}" data-ok="0">Reject</button>` : ""}</div>
        ${d.question && d.question !== d.topic ? `<div class="muted">Question: ${esc(d.question)}</div>` : ""}<div>${esc(d.recommendation)}${d.authorised ? "" : ' <span class="muted">(not yet authorised to implement)</span>'}</div>${d.alternatives.length ? `<div class="muted">Alternatives: ${esc(d.alternatives.map((a) => (typeof a === "string" ? a : JSON.stringify(a))).join(" · "))}</div>` : ""}${(d.criteria || []).length ? `<div class="muted">Criteria: ${esc(d.criteria.join(" · "))}</div>` : ""}${d.reasoning ? `<div class="muted">${esc(d.reasoning)}</div>` : ""}
        ${(d.evidence_labelled || []).length || (d.assumptions || []).length ? `<div class="muted">Evidence: ${[...(d.evidence_labelled || []).map((x) => `<span class="pill" title="SPEC-11 evidence class">${esc(x.class)}</span> ${esc(x.ref ?? Object.entries(x).filter(([k]) => k !== "class").map(([k, v]) => `${k} ${v}`).join(", "))}`), ...(d.assumptions || []).map((a) => `<span class="pill">ASSUMPTION</span> ${esc(typeof a === "string" ? a : JSON.stringify(a))}`)].join(" · ")}</div>` : ""}
        ${(d.limitations || []).length ? `<div class="muted">Limitations: ${esc(d.limitations.join(" · "))}</div>` : ""}${d.adr_path ? `<div class="muted" title="S08-R-02: Architecture Decision Record in the goal's branch">ADR: <code>${esc(d.adr_path)}</code></div>` : ""}${(d.reviews || []).map((r) => `<div class="muted">Review by ${esc(r.by)}: <strong>${esc(r.verdict)}</strong>${r.note ? ` — ${esc(r.note)}` : ""}</div>`).join("")}
        ${d.important && d.missing.length ? `<div class="muted" style="color:var(--warn,#b7791f)">Report incomplete: ${esc(d.missing.join(", "))}</div>` : ""}${d.approver ? `<div class="muted">${esc(d.status)} by ${esc(d.approver)}${d.note ? `: ${esc(d.note)}` : ""}</div>` : ""}
        ${["rejected", "expired", "cancelled"].includes(d.status) ? "" : `<div class="row" style="margin-top:4px"><span class="muted grow">Independent review:</span>${["agree", "concerns", "disagree"].map((v) => `<button class="btn" data-drev="${d.id}" data-v="${v}">${v}</button>`).join("")}</div>`}</div>`).join("")}` : ""}
    ${(g.blackboard || []).length ? `<details data-id="bb" ${openIds.has("bb") ? "open" : ""}><summary><strong>Blackboard</strong> <span class="muted">${g.blackboard.length} entr${g.blackboard.length === 1 ? "y" : "ies"}: the shared context agents read and write</span></summary>${g.blackboard.map((e) => `<div class="muted" style="margin-top:6px"><span class="pill">${esc(e.section)}</span> <code>${esc(e.key)}</code> v${e.version}${e.meta?.to ? ` · ${esc(e.meta.kind)} from ${esc(e.meta.from)} to ${esc(e.meta.to)}` : ""}</div><pre>${esc(e.content)}</pre>`).join("")}</details>` : ""}
    ${(g.team || []).length || (me.is_admin && GOAL_ACTIVE.includes(g.status)) ? `<h3>Team</h3>${(() => {
      const latest = new Map(); (g.team || []).forEach((m) => latest.set(m.agent_id || `role:${m.role}`, m));
      return [...latest.values()].map((m) => `<div class="row">${pill(m.action === "released" ? "failed" : m.action === "requested" ? "pending" : "done").replace(/>[^<]+</, `>${esc(m.action)}<`)}<span class="grow">${esc(m.agent || "(waiting for recruitment)")} <span class="muted">${esc(m.role)} · iteration ${Number(m.iteration)}${m.reason && m.action !== "joined" ? ` · ${esc(m.reason)}` : ""}</span></span>${me.is_admin && m.agent_id && GOAL_ACTIVE.includes(g.status) ? (m.action === "released" ? `<button class="btn" data-team="add" data-agent="${Number(m.agent_id)}">Take back</button>` : `<button class="btn danger" data-team="release" data-agent="${Number(m.agent_id)}">Release</button>`) : ""}</div>`).join("");
    })()}${me.is_admin && GOAL_ACTIVE.includes(g.status) ? `<div class="row" style="margin-top:4px"><button class="btn" data-team="add">Add a role</button></div>` : ""}${(g.team || []).length > 1 ? `<details><summary class="muted">History (${g.team.length} change${g.team.length === 1 ? "" : "s"})</summary>${g.team.map((m) => `<div class="muted">${esc(m.action)} ${esc(m.agent || m.role)} (${esc(m.role)}) by ${esc(m.actor)} · ${ago(m.at)}${m.reason ? ` · ${esc(m.reason)}` : ""}</div>`).join("")}</details>` : ""}` : ""}
    ${(g.transitions || []).length ? `<h3>Workflow</h3><div class="events">${g.transitions.map((x) => `<div><span class="k">${esc(x.to.replace(/_/g, " "))}</span>${esc(x.from.replace(/_/g, " "))} → ${esc(x.to.replace(/_/g, " "))}${x.reason ? ` · ${esc(x.reason)}` : ""} <span class="muted">${esc(x.actor)} · ${ago(x.at)}</span></div>`).join("")}</div>` : ""}
    ${!GOAL_ACTIVE.includes(g.status) && typeof missionReportHtml === "function" ? `<details data-id="report" id="goal-report" ${openIds.has("report") ? "open" : ""}><summary><strong>Mission report</strong> <span class="muted">each task verified, partial or failed from recorded checks and reviews</span></summary><div class="muted">Loading…</div></details>` : ""}
    <h3>Activity</h3><div class="events">${g.events.map((e) => `<div><span class="k">${esc(e.kind)}</span>${esc(e.message)} <span class="muted">${ago(e.at)}</span></div>`).join("")}</div>`;
  $("#goal-detail").querySelectorAll("[data-dec]").forEach((b) => (b.onclick = async () => {
    const approve = b.dataset.ok === "1", note = prompt(approve ? "Note (optional)" : "Why reject it?") ?? null;
    if (note === null) return;
    try { await api(`/api/decisions/${b.dataset.dec}/decide`, { method: "POST", body: { approve, note } }); goalDetail(id, openIds); } catch (err) { toast(err.message); }
  }));
  $("#goal-detail").querySelectorAll("[data-drev]").forEach((b) => (b.onclick = async () => {
    const verdict = b.dataset.v, note = prompt(verdict === "agree" ? "Note (optional)" : verdict === "disagree" ? "What is wrong with it? (a person will then decide)" : "Which concerns?") ?? null;
    if (note === null) return;
    try { await api(`/api/decisions/${b.dataset.drev}/review`, { method: "POST", body: { verdict, note } }); goalDetail(id, openIds); } catch (err) { toast(err.message); }
  }));
  const rep = $("#goal-report");  // REQ-004
  if (rep) api(`/api/goals/${id}/report`).then((d) => { rep.querySelector("div").outerHTML = `<div>${missionReportHtml(d)}</div>`; })
    .catch((e) => { rep.querySelector("div").textContent = e.message; });
  if (typeof bindDefectActions === "function") bindDefectActions($("#goal-detail"), () => goalDetail(id, openIds));  // S09-R-03
  $("#goal-detail").querySelectorAll("[data-team]").forEach((b) => (b.onclick = async () => {
    const body = { action: b.dataset.team };
    if (b.dataset.agent) body.agent_id = Number(b.dataset.agent);
    else { const role = prompt("Role to add to the team (e.g. security, qa, analyst)"); if (!role) return; body.role = role.trim(); }
    const reason = prompt(body.action === "release" ? "Why release this agent?" : "Why? (optional)") ?? null;
    if (reason === null) return;
    try { await api(`/api/goals/${id}/team`, { method: "POST", body: { ...body, reason } }); goalDetail(id, openIds); } catch (err) { toast(err.message); }
  }));
  $("#goal-detail").querySelector("[data-trace]").onclick = () => goalTrace(id).catch((err) => toast(err.message));
  const sb = $("#goal-detail").querySelector("[data-sbom]");
  sb && (sb.onclick = () => { const a = document.createElement("a"); a.href = `/api/goals/${id}/sbom`; a.download = ""; document.body.append(a); a.click(); a.remove(); });
  const bb = $("#goal-detail").querySelector("[data-budget]");
  if (bb) bb.onclick = async () => {
    const own = b.own || {}, ask = (label, v) => prompt(`${label} (0 = the setting for every goal)`, String(v ?? 0));
    const cost = ask("Cost budget in USD", own.budget_usd); if (cost === null) return;
    const tokens = ask("Token budget", own.budget_tokens); if (tokens === null) return;
    const agents = ask("Agents working at once", own.max_active_agents); if (agents === null) return;
    try {
      await api(`/api/goals/${id}/budget`, { method: "POST", body: { budget_usd: Number(cost) || 0, budget_tokens: parseInt(tokens, 10) || 0, max_active_agents: parseInt(agents, 10) || 0 } });
      toast("Budget changed"); goalDetail(id, openIds);
    } catch (err) { toast(err.message); }
  };
  $("#goal-detail").querySelectorAll("[data-act]").forEach((b) => (b.onclick = async () => {
    try { await api(`/api/goals/${id}/${b.dataset.act}`, { method: "POST" }); toast(`Goal ${b.dataset.act}d`); goalDetail(id); } catch (err) { toast(err.message); }
  }));
}

// The tool calls a CLI run made (S04-R-12, S14-R-08): tool, short input, failed ones marked.
const toolCalls = (calls) => `<details class="tool-calls"><summary class="muted">${calls.length} tool call(s)${calls.some((c) => !c.ok) ? ` · ${calls.filter((c) => !c.ok).length} failed` : ""}</summary><ol>${calls.map((c) => `<li class="${c.ok ? "" : "failed"}"><span class="mono">${esc(c.tool)}</span> ${esc(c.input)}${c.ok ? "" : ' <span class="pill failed">failed</span>'}</li>`).join("")}</ol></details>`;

// The execution trace of a goal (S15-R-04, S04-R-12): workflow, runs, gates, decisions and operations on one
// time line, with totals per agent and model.
async function goalTrace(id) {
  const t = await api(`/api/goals/${id}/trace`);
  const tasks = Object.fromEntries(t.tasks.map((x) => [x.id, x]));
  const rows = [
    ...t.transitions.map((x) => ({ at: x.at, k: "workflow", cls: "", text: `${x.from.replace(/_/g, " ")} → ${x.to.replace(/_/g, " ")}${x.reason ? ` · ${x.reason}` : ""}`, who: x.actor })),
    ...t.tasks.filter((x) => x.started_at).map((x) => ({ at: x.started_at, k: "task", cls: "", text: `#${x.id} ${x.title} (${x.role}, iteration ${x.iteration})`, who: x.agent || "" })),
    ...t.tasks.filter((x) => x.finished_at).map((x) => ({ at: x.finished_at, k: x.status, cls: x.status === "done" ? "done" : x.status === "failed" ? "failed" : "", text: `#${x.id} ${x.title}${x.files.length ? ` · ${x.files.length} file(s)` : ""}${x.commit ? ` · ${x.commit.slice(0, 10)}` : ""}`, who: x.agent || "" })),
    ...t.runs.map((r) => ({ at: r.at, k: "run", cls: r.ok ? "" : "failed", text: `${r.purpose} via ${r.executor} · ${r.model || "-"} · ${r.duration}s · ${fmt.format(r.tokens)} tokens · ${money(r.cost)}${r.isolation ? ` · ${r.isolation} (${(r.permissions || []).join(", ")})` : ""}${r.withheld?.length ? ` · withheld: ${r.withheld.join("; ")}` : ""}${r.denied_tools?.length ? ` · refused tool calls: ${r.denied_tools.join(", ")}` : ""}${r.task_id && tasks[r.task_id] ? ` · task #${r.task_id}` : ""}${r.ok ? "" : ` · ${r.error}`}`, who: r.agent || "", calls: r.tool_calls || [] })),
    ...t.gates.map((x) => ({ at: x.at, k: "gate", cls: x.passed ? "done" : "failed", text: `${x.message}${(x.checks || []).length ? ": " + x.checks.map((c) => `${c.passed ? "✓" : "✗"} ${c.check}${c.isolation ? ` (${c.isolation})` : ""}`).join(" · ") : ""}`, who: "" })),
    ...t.decisions.map((d) => ({ at: d.created_at || d.at, k: "decision", cls: "", text: `#${d.id} ${d.topic}: ${d.status}`, who: d.approver || "" })),
    ...t.operations.map((o) => ({ at: o.at, k: "operation", cls: o.status === "done" ? "done" : "", text: `${o.key.split(":").slice(2).join(":")} ${o.status}`, who: "" })),
  ].filter((r) => r.at).sort((a, b) => Date.parse(a.at) - Date.parse(b.at));
  const by = {};
  t.runs.forEach((r) => { const key = `${r.agent || "?"} · ${r.model || "-"}`; const x = (by[key] ||= { runs: 0, failed: 0, tokens: 0, cost: 0, sec: 0 }); x.runs++; x.failed += r.ok ? 0 : 1; x.tokens += r.tokens; x.cost += r.cost; x.sec += r.duration; });
  const time = (iso) => new Date(iso + (iso.endsWith("Z") || iso.includes("+") ? "" : "Z")).toLocaleTimeString();
  $("#goal-detail").innerHTML = `<div class="row" style="padding-right:24px"><button class="btn icon" id="trace-back" title="Back to the goal" aria-label="Back to the goal">${icon("up", 16)}</button><h2 class="grow" style="margin:0">Trace · #${t.goal.id} ${esc(t.goal.title)}</h2>${pill(t.goal.status)}</div>
    <p class="muted">${t.runs.length} run(s) · ${t.gates.length} gate result(s) · ${t.operations.length} operation(s) · ${money(t.runs.reduce((n, r) => n + r.cost, 0))}</p>
    ${Object.keys(by).length ? `<div class="table-wrap"><table><tr><th>Agent · model</th><th class="num">Runs</th><th class="num">Failed</th><th class="num">Tokens</th><th class="num">Seconds</th><th class="num">Cost</th></tr>${Object.entries(by).map(([key, x]) => `<tr><td>${esc(key)}</td><td class="num">${x.runs}</td><td class="num">${x.failed}</td><td class="num">${fmt.format(x.tokens)}</td><td class="num">${x.sec.toFixed(1)}</td><td class="num">${money(x.cost)}</td></tr>`).join("")}</table></div>` : ""}
    <h3>Time line</h3><div class="events">${rows.map((r) => `<div><span class="muted mono">${time(r.at)}</span> <span class="pill ${r.cls}">${esc(r.k)}</span> ${esc(r.text)}${r.who ? ` <span class="muted">${esc(r.who)}</span>` : ""}${r.calls?.length ? toolCalls(r.calls) : ""}</div>`).join("") || '<span class="muted">Nothing recorded yet.</span>'}</div>`;
  $("#trace-back").onclick = () => goalDetail(id);
}

// --- research -------------------------------------------------------------------
// Monitoring rules of a research source (ASEAO06 S06-R-02): how often, which words make a change matter, whose
// agents hear of it.
const SRC_INTERVALS = [["", "rotation interval"], ["hourly", "hourly"], ["daily", "daily"], ["weekly", "weekly"], ["monthly", "monthly"], ["manual", "manual only"]];
async function sourceForm(s) {
  const rooms = await api("/api/rooms");
  modal(`<h2>${icon("research", 18)} ${esc(s.url)}</h2><form id="sf">
    <label for="sf-int">Check</label><select id="sf-int">${SRC_INTERVALS.map(([v, l]) => `<option value="${v}" ${s.interval === v ? "selected" : ""}>${l}</option>`).join("")}</select>
    <label for="sf-subj">Subject: a change is an event when the page mentions one of these words (empty: every change)</label><input id="sf-subj" value="${esc(s.subject)}">
    <label for="sf-room">Tell the agents of this room about relevant changes</label><select id="sf-room"><option value="">nobody (notification only)</option>${rooms.map((r) => `<option value="${esc(r.key)}" ${s.notify_room === r.key ? "selected" : ""}>${esc(r.name)}</option>`).join("")}</select>
    <div class="row"><div class="grow"><label for="sf-depth">Link depth</label><input id="sf-depth" type="number" min="0" max="3" value="${s.depth}"></div><div class="grow"><label for="sf-max">Max pages</label><input id="sf-max" type="number" min="1" max="200" value="${s.max_pages}"></div></div>
    <label class="row" style="margin-top:8px"><input type="checkbox" id="sf-on" style="width:auto" ${s.enabled ? "checked" : ""}> Enabled</label>
    <div class="row" style="margin-top:12px"><button class="btn icon primary" title="Save" aria-label="Save">${icon("save", 16)}</button></div></form>`);
  $("#sf").onsubmit = async (e) => {
    e.preventDefault();
    try { await api(`/api/research/sources/${s.id}`, { method: "PATCH", body: { interval: $("#sf-int").value, subject: $("#sf-subj").value, notify_room: $("#sf-room").value, depth: +$("#sf-depth").value, max_pages: +$("#sf-max").value, enabled: $("#sf-on").checked } }); $("#modal").hidden = true; viewResearch(); } catch (err) { toast(err.message); }
  };
}

// Research shifts (SPEC-12 S12-R-05): the agent on duty, what its shift holds, the handovers between shifts.
function shiftCard(src) {
  const sh = src.shift;
  const len = src.shift_seconds ? `shifts of ${Math.round(src.shift_seconds / 360) / 10} h` : "a new shift every rotation turn";
  if (!sh) return `<div class="card"><h2>Shift</h2><p class="muted">No shift yet: the first rotation turn starts one (${len}).</p></div>`;
  const list = (items, f) => items.length ? `<ul>${items.slice(-8).map((x) => `<li>${f(x)}</li>`).join("")}</ul>` : '<span class="muted">none</span>';
  return `<div class="card"><h2>Shift: ${esc(sh.phase)}</h2><p class="muted">${esc(sh.agent || `agent ${sh.agent_id}`)} on duty since ${ago(sh.started_at)} · ${len} · confidence ${esc(sh.confidence)}. ${esc(sh.objective)}</p>
    <h3>Findings (${sh.findings.length})</h3>${list(sh.findings, (f) => esc(f.text))}
    <h3>Open questions (${sh.open_questions.length})</h3>${list(sh.open_questions, (q) => esc(q.text))}
    ${sh.known_problems.length ? `<h3>Known problems</h3>${list(sh.known_problems, (p) => `${esc(p.url)}: ${esc(p.error)}`)}` : ""}
    <div class="row" style="margin-top:8px"><a href="#" data-shifts class="grow">Shift history and handovers</a>${me.is_admin ? `<button class="btn sm" id="r-shift-replace" title="Hand the running shift to the next agent on the roster">Replace agent</button>` : ""}</div></div>`;
}

async function shiftHistory() {
  const rows = await api("/api/research/shifts?limit=30");
  const pkg = (h) => h.from_agent ? `<details><summary>Handover from ${esc(h.from_agent)} (${esc(h.reason)}) · confidence ${esc(h.confidence)}</summary><pre>${esc(JSON.stringify(h, null, 1))}</pre></details>` : "";
  modal(`<h2>Research shifts</h2><div class="events">${rows.map((x) => `<div><div class="row"><strong>#${x.id} ${esc(x.phase)}</strong><span class="grow">${esc(x.agent || `agent ${x.agent_id}`)} <span class="muted">· ${x.sources.length} visit(s) · ${x.findings.length} finding(s) · ${x.open_questions.length} open question(s) · ${ago(x.started_at)}</span></span>${pill(x.status)}</div>${pkg(x.handover)}</div>`).join("") || '<span class="muted">No shifts yet.</span>'}</div>`);
}

async function viewResearch(quiet) {
  if (quiet && $("#r-crawl")) return; // keep forms and results stable
  const [docs, src, reports] = await Promise.all([api("/api/research/documents"), api("/api/research/sources"), api("/api/research/requests")]);
  $("#view").innerHTML = `<div class="grid two"><div class="grid">
    <div class="card"><h2>Collect a website</h2><form id="r-crawl"><label>URL</label><input id="r-url" type="url" required placeholder="https://docs.crewai.com/">
      <div class="row"><div class="grow"><label>Link depth (0 = this page only)</label><input id="r-depth" type="number" min="0" max="3" value="1"></div><div class="grow"><label>Max pages</label><input id="r-max" type="number" min="1" max="200" value="20"></div></div>
      <div class="row" style="margin-top:10px"><button class="btn primary">Collect</button></div></form><div id="r-out"></div></div>
    <div class="card"><h2>Rotation sources</h2><p class="muted">The external-data room visits these in turn${src.interval ? ` every ${src.interval}s` : " (set ROTATION_INTERVAL to run automatically)"}. Roster: ${src.roster.length} agent(s).</p>
      <div class="events">${src.sources.map((s) => `<div class="row nowrap"><span class="grow ellipsis" title="${esc(s.url)}">${esc(s.url)} <span class="muted">${esc(s.interval || "rotation")}${s.subject ? ` · watching “${esc(s.subject)}”` : ""}${s.notify_room ? ` · tells ${esc(s.notify_room.replace(/_/g, " "))}` : ""} · depth ${s.depth} · ${ago(s.last_run_at)}${s.not_before && new Date(s.not_before) > new Date() ? ` · held until ${new Date(s.not_before).toLocaleString()}` : ""}${s.enabled ? "" : " · off"}</span></span>${me.is_admin ? ib("play", "Run this source now", `data-srun="${s.id}"`, "sm") + ib("edit", "Monitoring rules", `data-sedit="${s.id}"`, "sm") : ""}</div>`).join("")}</div>
      ${me.is_admin ? `<form id="r-src" class="row" style="margin-top:8px"><input id="r-src-url" class="grow" type="url" required placeholder="https://news.example.com/"><select id="r-src-int" style="width:auto" title="How often to check it">${SRC_INTERVALS.map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}</select><input id="r-src-subj" placeholder="subject words (optional)" style="max-width:180px"><button class="btn">Add</button><button class="btn" type="button" id="r-rotate">Run next</button></form>` : ""}</div>
    ${shiftCard(src)}</div>
    <div class="grid"><div class="card"><h2>Ask the research room</h2><form id="r-ask"><label>Question</label><textarea id="r-question" rows="2" required minlength="5" placeholder="Which agent framework fits a hierarchical team, and why?"></textarea>
      <label>Sources to read first (optional, one URL per line)</label><textarea id="r-urls" rows="2" placeholder="https://docs.crewai.com/"></textarea>
      <details style="margin-top:6px"><summary class="muted">More options: keywords, kinds of source, publication dates, languages, priority, repeat, deadline, budget</summary>
        <label>Keywords (comma separated)</label><input id="r-kw" placeholder="crewai, hierarchical process">
        <div class="row"><div class="grow"><label>Prefer sources</label><select id="r-pref">${[["any", "any kind"], ["official", "official docs and standards"], ["academic", "academic"], ["open_source", "open source repositories and packages"]].map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}</select></div>
          <div class="grow"><label>Priority</label><select id="r-prio">${["normal", "high", "critical", "low"].map((v) => `<option>${v}</option>`).join("")}</select></div>
          <div class="grow"><label>Repeat</label><select id="r-rec"><option value="">once</option>${["hourly", "daily", "weekly", "monthly"].map((v) => `<option>${v}</option>`).join("")}</select></div></div>
        <div class="row"><div class="grow"><label>Published from (optional)</label><input id="r-pa" type="date"></div><div class="grow"><label>Published to (optional)</label><input id="r-pb" type="date"></div>
          <div class="grow"><label>Languages (comma separated, e.g. en, de)</label><input id="r-lang" placeholder="any" title="The language a page or feed declares; pages that declare none are kept and marked"></div></div>
        <div class="row"><div class="grow"><label>Deadline (optional)</label><input id="r-dl" type="datetime-local"></div><div class="grow"><label>Budget in USD over all runs (0 = none)</label><input id="r-bud" type="number" min="0" step="0.01" value="0"></div></div>
      </details><div class="row" style="margin-top:8px"><button class="btn primary">Ask</button></div></form>
      <div class="events" style="margin-top:8px">${reports.map((r) => `<div class="row"><a href="#" data-report="${r.id}" class="grow">${esc(r.question)}</a>${r.priority !== "normal" ? `<span class="pill">${esc(r.priority)}</span>` : ""}${r.recurring ? `<span class="pill info" title="${r.next_run_at ? `next run ${esc(new Date(r.next_run_at).toLocaleString())}` : "not scheduled"}">${esc(r.recurring)}</span>` : ""}${pill(r.status)}${r.review ? `<span class="pill ${esc(r.review)}" title="review">${esc(r.review.replace("_", " "))}</span>` : ""}<span class="muted">${r.sources.length} source(s) · ${ago(r.at)}</span></div>`).join("") || '<span class="muted">No reports yet.</span>'}</div></div>
    <div class="card"><h2>Search knowledge</h2><form id="r-search" class="row"><input id="r-q" class="grow" placeholder="What do we know about…"><button class="btn">Search</button></form><div id="r-hits"></div></div>
    <div class="card"><h2>Collected pages (${docs.length})</h2><div class="events">${docs.slice(0, 80).map((d) => `<div><a href="${esc(d.url)}" target="_blank" rel="noopener noreferrer">${esc(d.title || d.url)}</a> <span class="pill">${esc(d.category)}</span>${(d.flags || []).length ? ` <span class="pill failed" title="kept out of agent prompts">${esc(d.flags.join(", "))}</span>` : ""} ${d.version > 1 ? ` <span class="pill info" title="changed since it was first collected">v${d.version}</span>` : ""} <span class="muted">${d.published_at ? `published ${esc(d.published_at.slice(0, 10))} · ` : ""}${d.language ? `<span title="declared language">${esc(d.language)}</span> · ` : ""}${d.license ? `<span title="licence">${esc(d.license.length > 40 ? d.license.slice(0, 40) + "…" : d.license)}</span> · ` : ""}${fmt.format(d.chars)} chars · ${ago(d.at)}</span></div>`).join("") || '<span class="muted">Nothing collected yet.</span>'}</div></div></div></div>`;
  $("#r-crawl").onsubmit = async (e) => {
    e.preventDefault(); $("#r-out").innerHTML = '<p class="muted">Collecting…</p>';
    try { const r = await api("/api/research/crawl", { method: "POST", body: { url: $("#r-url").value, depth: +$("#r-depth").value, max_pages: +$("#r-max").value } }); $("#r-out").innerHTML = `<p>Stored ${r.stored} of ${r.pages} page(s).</p>${r.errors.length ? `<pre>${esc(r.errors.map((x) => `${x.url}: ${x.error}`).join("\n"))}</pre>` : ""}`; setTimeout(() => viewResearch(), 1500); }
    catch (err) { $("#r-out").innerHTML = `<p class="muted">${esc(err.message)}</p>`; }
  };
  $("#r-search").onsubmit = async (e) => { e.preventDefault(); const hits = await api(`/api/research/search?q=${encodeURIComponent($("#r-q").value)}`); $("#r-hits").innerHTML = hits.map((h) => `<h3><a href="${esc(h.url)}" target="_blank" rel="noopener noreferrer">${esc(h.title || h.url)}</a> <span class="pill">${esc(h.category)}</span>${h.flags.length ? ` <span class="pill failed">${esc(h.flags.join(", "))}</span>` : ""}</h3><pre>${esc(h.text)}</pre>`).join("") || '<p class="muted">No matches.</p>'; };
  $("#r-ask").onsubmit = async (e) => {
    e.preventDefault();
    const body = { question: $("#r-question").value, urls: $("#r-urls").value.split("\n").map((x) => x.trim()).filter(Boolean),
      keywords: $("#r-kw").value.split(",").map((x) => x.trim()).filter(Boolean), source_preferences: $("#r-pref").value, priority: $("#r-prio").value,
      recurring: $("#r-rec").value, budget_usd: Number($("#r-bud").value) || 0, deadline: $("#r-dl").value ? new Date($("#r-dl").value).toISOString() : null,
      published_after: $("#r-pa").value || null, published_before: $("#r-pb").value || null, languages: $("#r-lang").value.split(",").map((x) => x.trim()).filter(Boolean) };
    try {
      const r = await api("/api/research/requests", { method: "POST", body });
      toast(r.duplicate ? `Already asked: request #${r.id} answers this question` : "Asked; the report appears here when it is ready"); setTimeout(() => viewResearch(), 1500);
    } catch (err) { toast(err.message); }
  };
  document.querySelectorAll("[data-shifts]").forEach((a) => (a.onclick = async (e) => { e.preventDefault(); shiftHistory(); }));
  document.querySelectorAll("[data-report]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); reportModal(reports.find((x) => x.id === Number(a.dataset.report))); }));
  if (me.is_admin) {
    $("#r-src").onsubmit = async (e) => { e.preventDefault(); try { await api("/api/research/sources", { method: "POST", body: { url: $("#r-src-url").value, interval: $("#r-src-int").value, subject: $("#r-src-subj").value } }); viewResearch(); } catch (err) { toast(err.message); } };
    document.querySelectorAll("[data-srun]").forEach((b) => (b.onclick = async () => { try { const r = await api(`/api/research/sources/${b.dataset.srun}/run`, { method: "POST" }); toast(r.url ? `${r.stored} page(s): ${r.new || 0} new, ${r.changed?.length || 0} changed, ${r.events || 0} event(s)` : r.detail); viewResearch(); } catch (err) { toast(err.message); } }));
    document.querySelectorAll("[data-sedit]").forEach((b) => (b.onclick = () => sourceForm(src.sources.find((s) => s.id === Number(b.dataset.sedit)))));
    if ($("#r-shift-replace")) $("#r-shift-replace").onclick = async () => {
      const reason = prompt("Why is the agent on duty being replaced? The next agent on the roster continues the shift with its findings and open questions.");
      if (!reason) return;
      try { const r = await api("/api/research/shifts/replace", { method: "POST", body: { reason } }); toast(`${r.agent || "Agent " + r.agent_id} continues the ${r.phase} shift`); viewResearch(); } catch (err) { toast(err.message); }
    };
    $("#r-rotate").onclick = async () => { try { const r = await api("/api/research/rotate", { method: "POST" }); toast(r.url ? `Agent ${r.agent_id} visited ${r.url}: ${r.stored} page(s)` : r.detail); viewResearch(); } catch (err) { toast(err.message); } };
  }
}

// A research report with its claims and review (ASEAO06 S06-R-03/R-04): the approved text once approved, else the draft.
// EL-R-02: the research lead's tool loop (research_agent.plan_and_collect): per round its model, each tool call
// (action, its argument, documents found, errors) or why the round stopped. Older reports have none.
function reportToolSteps(steps) {
  steps = Array.isArray(steps) ? steps : [];
  if (!steps.length) return "";
  const cut = (s, n = 200) => (s.length > n ? `${s.slice(0, n)}…` : s);
  const rows = steps.flatMap((s) => {
    const head = `round ${esc(s.round)}${s.model ? ` · <span class="muted">${esc(s.model)}</span>` : ""}`;
    const note = s.stopped ? `stopped: ${esc(s.stopped)}` : s.sufficient ? `material sufficient${s.reason ? `: ${esc(s.reason)}` : ""}` : s.reason ? esc(s.reason) : "";
    const calls = (s.results || []).map((x) => {
      const { action, documents, titles, errors, ...args } = x || {};
      const docs = (documents || []).length, errs = (errors || []).filter(Boolean);
      const outcome = `${docs} document(s)${(titles || []).length ? `: ${esc(cut(titles.join("; "), 160))}` : ""}${errs.length ? ` · <span class="pill failed">error</span> ${esc(cut(errs.join("; ")))}` : ""}`;
      return `<div class="row" style="flex-wrap:wrap;gap:6px">${head}<strong>${esc(action)}</strong><code>${esc(cut(JSON.stringify(args)))}</code><span class="muted">→ ${outcome}</span></div>`;
    });
    const refused = (Array.isArray(s.refused) ? s.refused : s.refused ? [s.refused] : []).map((x) => (typeof x === "string" ? x : JSON.stringify(x)));
    const extra = [note, refused.length ? `refused: ${esc(cut(refused.join("; ")))}` : ""].filter(Boolean).join(" · ");
    return [...calls, ...(extra || !calls.length ? [`<div class="row muted" style="flex-wrap:wrap;gap:6px">${head}${extra ? ` · ${extra}` : ""}</div>`] : [])];
  });
  return `<details${rows.length <= 6 ? " open" : ""}><summary>Tool steps (${rows.length})</summary><div class="events">${rows.join("")}</div></details>`;
}

function reportModal(r) {
  const langs = r.languages || [];
  const meta = [r.keywords.length ? `keywords: ${esc(r.keywords.join(", "))}` : "", r.source_preferences !== "any" ? `prefers ${esc(r.source_preferences.replace("_", " "))} sources` : "", r.published_after || r.published_before ? `published ${esc(r.published_after || "…")}..${esc(r.published_before || "…")}` : "", langs.length ? `languages: ${esc(langs.join(", "))}` : "", `priority ${esc(r.priority)}`, r.recurring ? `${esc(r.recurring)}, ${r.runs} run(s)${r.next_run_at ? `, next ${esc(new Date(r.next_run_at).toLocaleString())}` : ""}` : "", r.deadline ? `deadline ${esc(new Date(r.deadline).toLocaleString())}` : "", r.budget_usd ? `${money(r.cost_usd)} of ${money(r.budget_usd)}` : "", r.goal_id ? `for goal #${Number(r.goal_id)}` : ""].filter(Boolean).join(" · ");
  const review = r.review ? `<p><span class="pill ${esc(r.review)}">${esc(r.review.replace("_", " "))}</span> ${r.reviewed_by ? `by ${esc(r.reviewed_by)}${r.review_provider ? ` <span class="muted">(${esc(r.review_provider)})</span>` : ""}` : ""} ${r.review_note ? `<span class="muted">${esc(r.review_note)}</span>` : ""}</p>` : "";
  const claims = r.claims.length ? `<details${r.review === "in_review" ? " open" : ""}><summary>Claims (${r.claims.filter((c) => ["verified", "supported", "contested"].includes(c.status)).length} of ${r.claims.length} kept)</summary><div class="events">${r.claims.map((c) => `<div><span class="pill ${esc(c.status)}" title="excerpt check: ${esc(c.support)}">${esc(c.status)}</span> ${esc(c.claim)}${c.evidence.map((e) => `<div class="muted" style="margin-left:14px">[${Number(e.n)}] ${esc(e.relationship)} · ${e.found ? `${esc(e.match)}: “${esc(e.excerpt.slice(0, 240))}”` : `not backed (${esc(e.match)})`}${e.retrieved ? ` · retrieved ${esc(e.retrieved.slice(0, 10))}` : ""}</div>`).join("")}${c.review ? `<div class="muted" style="margin-left:14px">reviewer: ${esc(c.review)}</div>` : ""}</div>`).join("")}</div></details>` : "";
  const text = r.review === "approved" ? r.approved_report : r.report;
  const label = r.review && r.review !== "approved" ? `<p class="muted">Draft: not used by knowledge, search or goals${r.approved_report ? " (an earlier approved version is)" : ""}.</p>` : "";
  const act = me.is_admin && ["draft", "in_review"].includes(r.review) ? `<div class="row" style="margin:8px 0"><button class="btn primary" id="rr-ok">Approve</button><button class="btn danger" id="rr-no">Reject</button></div>` : "";
  modal(`<h2>${esc(r.question)}</h2>${pill(r.status)}<p class="muted">${meta}</p>${review}${act}${claims}${reportToolSteps(r.tool_steps)}${label}<div class="md">${typeof renderMarkdown === "function" ? renderMarkdown(text || "(not ready yet)") : `<pre>${esc(text)}</pre>`}</div>`);
  const send = async (approve) => {
    const note = approve ? "" : prompt("Why is this report rejected?");
    if (!approve && !note) return;
    try { const out = await api(`/api/research/requests/${r.id}/review`, { method: "POST", body: { approve, note: note || "" } }); toast(`Report ${out.review}`); $("#modal").hidden = true; viewResearch(); } catch (err) { toast(err.message); }
  };
  if ($("#rr-ok")) { $("#rr-ok").onclick = () => send(true); $("#rr-no").onclick = () => send(false); }
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
        !u.is_admin ? btn(u, "perms", "Permissions") : "",
        u.status === "active" && !u.is_admin ? btn(u, "delegate", "Delegate") : "",
        btn(u, "git", "Git"),
        !u.is_admin ? btn(u, "delete", "Delete", "danger") : ""].join("")}</td></tr>`).join("")}</table></div></div>`;
  document.querySelectorAll("[data-u]").forEach((b) => (b.onclick = async () => {
    const { u, act } = b.dataset;
    const user = users.find((x) => x.id === Number(u));
    if (act === "perms") return permissionsForm(user);
    if (act === "git") return gitForm(user);
    if (act === "delegate") return delegateForm(user);
    if (["delete", "reset_device"].includes(act) && !confirm(`${act.replace("_", " ")} this user?`)) return;
    try { await api(act === "delete" ? `/api/users/${u}` : `/api/users/${u}/${act}`, { method: act === "delete" ? "DELETE" : "POST" }); viewUsers(); updateBadges(); } catch (e) { toast(e.message); }
  }));
}

// --- the browser tab's icon: status, current tasks, notifications and the audit log at a glance ------------
// Tile: the brand colours, red while a stop is on, grey while the server is unreachable. Centre: the goals running
// now (else the emblem). Dots: unread notifications (top right), goals waiting for approval (bottom left), audit
// records not seen yet (bottom right). The tab's title spells the same out (it is the icon's tooltip).
function setFavicon(o) {
  const offline = !o, running = o?.goals.running || 0, waiting = o?.goals.awaiting_approval || 0;
  const unread = o?.unread || 0, audit = badges.audit || 0, stop = o?.stop_level || "";
  const n = (v) => (v > 9 ? "9+" : String(v));
  const dot = (cx, cy, r, fill, v) => `<circle cx="${cx}" cy="${cy}" r="${r}" fill="${fill}" stroke="#fff" stroke-width="2"/><text x="${cx}" y="${cy + r * 0.42}" font-size="${r * 1.25}" font-family="sans-serif" font-weight="700" text-anchor="middle" fill="#fff">${n(v)}</text>`;
  const tile = offline ? "#6b7280" : stop ? "#dc2626" : "url(#g)";
  const centre = offline ? '<path d="M20 10v13M20 29v1" stroke="#fff" stroke-width="5" stroke-linecap="round"/>'
    : running ? `<text x="20" y="${running > 9 ? 27 : 29}" font-size="${running > 9 ? 19 : 25}" font-family="sans-serif" font-weight="800" text-anchor="middle" fill="#fff">${n(running)}</text>`
    : '<path d="M10 30V19l3-2v-3h2v2l2-1.5L19 16v-4l1-2 1 2v4l2-1.5 2 1.5v-2h2v3l3 2v11z" fill="#fff"/>';
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#7c3aed"/><stop offset=".55" stop-color="#db2777"/><stop offset="1" stop-color="#f59e0b"/></linearGradient></defs>`
    + `<rect x="2" y="2" width="36" height="36" rx="11" fill="${tile}"/>${centre}`
    + (unread ? dot(31, 9, 8.5, "#ef4444", unread) : "") + (waiting ? dot(8, 32, 7.5, "#d97706", waiting) : "") + (audit ? dot(32, 32, 7.5, "#2563eb", audit) : "") + "</svg>";
  document.querySelector("link[rel=icon]").href = `data:image/svg+xml,${encodeURIComponent(svg)}`;
  const parts = offline ? ["offline"] : [stop && `${stop} stop`, running && `${running} running`, waiting && `${waiting} awaiting approval`,
    unread && `${unread} unread`, audit && `${audit} new in the audit log`].filter(Boolean);
  document.title = parts.length ? `(${parts.join(" · ")}) Agent Company` : "Agent Company";
}

registerView({ id: "dashboard", title: "Dashboard", icon: "dashboard" }, viewDashboard);
registerView({ id: "goals", title: "Goals", icon: "goals" }, viewGoals);
registerView({ id: "research", title: "Research", icon: "research" }, viewResearch);
registerView({ id: "users", title: "Users", icon: "users", admin: true }, viewUsers);

// Delegated approval rights (SPEC-01 S01-R-03): one project, until an expiry; revocable at once.
async function delegateForm(u) {
  const [list, d] = await Promise.all([api("/api/projects"), api("/api/delegations")]);
  const names = new Map(list.map((p) => [p.id, p.name]));
  const mine = d.items.filter((x) => x.user_id === u.id);
  const until = new Date(Date.now() + 7 * 864e5).toISOString().slice(0, 10);
  modal(`<h2>Delegations · ${esc(u.name)}</h2><p class="muted">A delegate may approve or reject the goals of one project that wait for a merge, until the expiry (at most ${d.max_days} days). The platform project stays with administrators; agents never receive approval rights.</p>
    <form id="dl" class="row"><select id="dl-p" class="grow">${list.filter((p) => !p.protected).map((p) => `<option value="${p.id}">${esc(p.name)}</option>`).join("")}</select><input id="dl-until" type="date" value="${until}" style="width:auto"><input id="dl-note" placeholder="Note" class="grow"><button class="btn primary">Delegate</button></form>
    ${mine.length ? `<table><tr><th>Project</th><th>Right</th><th>Until</th><th>State</th><th></th></tr>${mine.map((x) => `<tr><td>${esc(names.get(x.project_id) || `#${x.project_id}`)}</td><td>${esc(x.permission)}</td><td>${esc(x.expires_at.slice(0, 10))}</td><td>${pill(x.state)}</td><td>${x.state === "active" ? `<button class="btn sm danger" data-rev="${x.id}">Revoke</button>` : ""}</td></tr>`).join("")}</table>` : '<p class="muted">No delegations yet.</p>'}`);
  $("#dl").onsubmit = async (e) => {
    e.preventDefault();
    try { await api("/api/delegations", { method: "POST", body: { user_id: u.id, project_id: Number($("#dl-p").value), expires_at: `${$("#dl-until").value}T23:59:59Z`, note: $("#dl-note").value } }); delegateForm(u); } catch (err) { toast(err.message); }
  };
  document.querySelectorAll("[data-rev]").forEach((b) => (b.onclick = async () => { try { await api(`/api/delegations/${b.dataset.rev}/revoke`, { method: "POST" }); delegateForm(u); } catch (err) { toast(err.message); } }));
}

async function permissionsForm(u) {
  const list = await api("/api/projects");
  const p = u.permissions;
  const all = p.projects === "all";
  const box = (k, label) => `<label class="row"><input type="checkbox" data-perm="${k}" style="width:auto" ${p[k] ? "checked" : ""}> ${label}</label>`;
  modal(`<h2>Permissions · ${esc(u.name)}</h2><form id="pm">
    <h3>Projects</h3><p class="muted">A user always sees the projects they created. The protected platform project is always admin-only.</p><label class="row"><input type="checkbox" id="pm-all" style="width:auto" ${all ? "checked" : ""}> Every regular project</label>
    <div id="pm-list" style="margin-left:24px">${list.filter((x) => !x.protected).map((x) => `<label class="row"><input type="checkbox" data-pid="${x.id}" style="width:auto" ${all || (Array.isArray(p.projects) && p.projects.includes(x.id)) ? "checked" : ""} ${all ? "disabled" : ""}> ${esc(x.name)}</label>`).join("") || '<span class="muted">No projects.</span>'}</div>
    <h3>Capabilities</h3>${box("run", "Give orders, start goals, ask the research room (without it: read only)")}${box("create_projects", "Create projects (in a new workspace)")}${box("manage_agents", "Manage agents and evolution")}${box("manage_models", "Manage models and routes")}
    <div class="row" style="margin-top:12px"><button class="btn icon primary" title="Save" aria-label="Save">${icon("save", 16)}</button></div></form>`);
  $("#pm-all").onchange = (e) => document.querySelectorAll("[data-pid]").forEach((c) => (c.disabled = e.target.checked));
  $("#pm").onsubmit = async (e) => {
    e.preventDefault();
    const ticked = [...document.querySelectorAll("[data-pid]:checked")].map((c) => Number(c.dataset.pid));
    const body = { projects: $("#pm-all").checked ? "all" : ticked.length ? ticked : "own" };
    document.querySelectorAll("[data-perm]").forEach((c) => (body[c.dataset.perm] = c.checked));
    try { await api(`/api/users/${u.id}/permissions`, { method: "PUT", body }); $("#modal").hidden = true; toast("Saved"); viewUsers(); } catch (err) { toast(err.message); }
  };
}
async function gitForm(u) {
  let st;
  try { st = await api(`/api/users/${u.id}/git`); } catch (err) { return toast(err.message); }
  const id = st.identity || {};
  const plain = location.protocol === "http:" && !["localhost", "127.0.0.1"].includes(location.hostname);
  modal(`<h2>Git settings · ${esc(u.name)}</h2>
    <p class="muted">Work ${u.id === me.id ? "you order" : "this user orders"} is committed under this name and email, to this branch, and pushed to each project's GitHub URL with the GitHub access below. Empty fields use the repository's own settings.</p>
    <form id="gf"><label>Name</label><input id="gf-name" value="${esc(id.name || "")}"><label>Email</label><input id="gf-email" type="email" value="${esc(id.email || "")}"><label>Branch</label><input id="gf-branch" value="${esc(id.branch || "")}" placeholder="the project's current branch">
    <div class="row" style="margin-top:12px"><button class="btn primary">Save</button></div></form>
    <h3 style="margin-top:18px">GitHub access</h3>
    <p class="muted">Use a token <em>or</em> an SSH key; the token wins if both are set. Without either, pushes use the server's own GitHub login, which may not have access to ${u.id === me.id ? "your" : "this user's"} repositories.</p>
    ${plain ? '<p class="pill failed" style="display:inline-block">This page is on HTTP: a token typed here crosses the network unencrypted. Switch to HTTPS first.</p>' : ""}
    <div class="card" style="margin:8px 0"><strong>Token</strong> <span class="muted">${st.token ? `set (${esc(st.token_hint)})` : "not set"}</span>
      <p class="muted">GitHub → Settings → Developer settings → Fine-grained tokens: choose the repositories, give <em>Contents: Read and write</em>. The token is stored encrypted and never shown again.</p>
      <div class="row"><input id="gf-token" type="password" autocomplete="off" placeholder="github_pat_…" class="grow"><button class="btn" id="gf-token-save">Save token</button>${st.token ? '<button class="btn" id="gf-token-del">Remove</button>' : ""}</div></div>
    <div class="card" style="margin:8px 0"><strong>SSH key</strong> <span class="muted">${st.ssh_key ? "created" : "none"}</span>
      ${st.ssh_key ? `<p class="muted">Add this public key on GitHub → Settings → SSH and GPG keys → New SSH key (signed in as the account that owns the repositories).</p><textarea readonly rows="3" class="mono" style="width:100%">${esc(st.ssh_key)}</textarea><div class="row"><button class="btn" id="gf-key-copy">Copy</button><button class="btn" id="gf-key-del">Delete key</button></div>`
        : '<p class="muted">The server creates a key pair; only the public half leaves the server.</p><button class="btn" id="gf-key-new">Create SSH key</button>'}</div>
    <div class="row"><button class="btn" id="gf-test">Test GitHub access</button><span id="gf-test-out" class="muted grow"></span></div>`);
  const reopen = () => gitForm(u);
  const call = async (path, method, body) => { try { await api(`/api/users/${u.id}/git${path}`, { method, body }); reopen(); } catch (err) { toast(err.message); } };
  $("#gf").onsubmit = async (e) => { e.preventDefault(); try { await api(`/api/users/${u.id}/git`, { method: "PUT", body: { name: $("#gf-name").value, email: $("#gf-email").value, branch: $("#gf-branch").value } }); toast("Saved"); if (current === "users") viewUsers(); } catch (err) { toast(err.message); } };
  $("#gf-token-save").onclick = () => { const v = $("#gf-token").value.trim(); if (v) call("/token", "PUT", { token: v }); };
  $("#gf-token-del") && ($("#gf-token-del").onclick = () => confirm("Remove the GitHub token?") && call("/token", "DELETE"));
  $("#gf-key-new") && ($("#gf-key-new").onclick = () => call("/ssh-key", "POST"));
  $("#gf-key-del") && ($("#gf-key-del").onclick = () => confirm("Delete the SSH key? Also remove it from GitHub.") && call("/ssh-key", "DELETE"));
  $("#gf-key-copy") && ($("#gf-key-copy").onclick = async () => { try { await navigator.clipboard.writeText(st.ssh_key); toast("Copied"); } catch { toast("Select the text and copy it"); } });
  $("#gf-test").onclick = async () => { $("#gf-test-out").textContent = "Testing…"; try { const r = await api(`/api/users/${u.id}/git/test`, { method: "POST" }); $("#gf-test-out").textContent = r.message; } catch (err) { $("#gf-test-out").textContent = err.message; } };
}

// --- icon buttons everywhere: a text button with a known label shows an icon; the label becomes its tooltip and
// accessible name. Buttons that already carry an icon just drop the text. Long or unknown labels stay text.
P.cut = "M6 6a3 3 0 1 0 0 .01M6 18a3 3 0 1 0 0 .01M8.5 7.5L20 18M8.5 16.5L20 6";
P.paste = "M9 4h6v3H9zM7 5H5v16h14V5h-2";
P.up = "M12 19V5M5 12l7-7 7 7";
P.file_add = "M6 2h9l5 5v15H6zM14 2v6h6M12 11v6M9 14h6";
P.folder_add = "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM12 10v6M9 13h6";
P.columns = "M3 4h18v16H3zM12 4v16";
P.clock = "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 7v5l3 2";
P.rocket = "M5 15c-1 1-2 4-2 6 2 0 5-1 6-2M9 15l-3-3c1-4 5-9 12-9 0 7-5 11-9 12zM15 9h.01";
P.broom = "M19 3l-7 7M9 13l-6 8h9l1-6zM10 11l3 3";
const BTN_ICONS = { save: "save", delete: "trash", remove: "trash", cancel: "x", copy: "copy", cut: "cut", paste: "paste", rename: "edit", edit: "edit",
  up: "up", "new file": "file_add", "new folder": "folder_add", upload: "upload", download: "download", search: "search", add: "plus", ask: "send",
  collect: "download", run: "play", "run next": "play", compare: "columns", defer: "clock", "accept risk": "check", test: "bolt", switch: "refresh",
  "reset to .env": "refresh", reset: "refresh", start: "play", stop: "stop", restart: "refresh", launch: "rocket", "install docker": "download",
  "install lxd": "download", "initialise lxd": "play", "start docker": "play", "clean up": "broom", "check again": "refresh", approve: "check", reject: "x",
  disable: "pause", enable: "play", "make admin": "shield", permissions: "key", git: "git", open: "eye", continue: "play", "use this folder": "check",
  "diagnose all agents now": "bolt", "merge idle clones": "merge", create: "check", post: "send", "open folder": "folder", "reset computer": "refresh",
  "save token": "save", "create ssh key": "key", "delete key": "trash", "test github access": "bolt", "download certificate (ca)": "download",
  source: "files", preview: "eye", wrap: "columns", close: "x", "ctrl+alt+del": "cpu", "full screen": "max", fit: "fit", "100%": "max", refresh: "refresh",
  "make machine": "check", "delete": "trash" };
function iconize(root) {
  root.querySelectorAll("button.btn:not(.icon):not(.keep-text), a.btn:not(.icon):not(.keep-text)").forEach((b) => {
    const label = b.textContent.replace(/\s+/g, " ").trim().replace(/…$/, "");
    const name = BTN_ICONS[label.toLowerCase()];
    if (!label || (!name && !b.querySelector("svg"))) return;
    if (!b.title || b.dataset.autot) { b.title = label; b.dataset.autot = "1"; }  // a label that changes (Connect/Disconnect) follows
    if (!b.getAttribute("aria-label") || b.dataset.autot) b.setAttribute("aria-label", label);
    const svgEl = b.querySelector("svg");
    if (svgEl) { [...b.childNodes].forEach((n) => { if (n !== svgEl && !(n.nodeType === 1 && n.querySelector?.("svg"))) n.remove(); }); }
    else b.innerHTML = icon(name, 16);
    b.classList.add("icon");
  });
}
// At most every 300 ms, and only where buttons live (not terminals or the rail).
let iconizeQueued = false;
const iconizeObserver = new MutationObserver(() => {
  if (iconizeQueued) return;
  iconizeQueued = true;
  setTimeout(() => { iconizeQueued = false; ["#view", "#modal", "#auth"].forEach((sel) => { const el = $(sel); if (el) iconize(el); }); }, 300);
});
addEventListener("DOMContentLoaded", () => ["#view", "#modal", "#auth"].forEach((sel) => $(sel) && iconizeObserver.observe($(sel), { childList: true, subtree: true })));

// --- start (called after every view file has loaded) ----------------------------
function start() {
$("#theme").onclick = toggleTheme;
$("#layout").onclick = layoutPicker;
applyLayout();
initTopSearch();
window.addEventListener("resize", () => applyProjectLayout());
applyTheme();
applyAccent();
api("/api/auth/me").then(boot).catch(tryDevice);
}
