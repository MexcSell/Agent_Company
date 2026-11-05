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
  agents: "M16 11a4 4 0 1 0-8 0M4 21v-1a6 6 0 0 1 6-6h4a6 6 0 0 1 6 6v1M12 3v4",
  models: "M12 2l9 5v10l-9 5-9-5V7zM12 22V12M21 7l-9 5-9-5",
  research: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM21 21l-5-5",
  users: "M9 7a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM2 21v-1a5 5 0 0 1 5-5h4a5 5 0 0 1 5 5v1M16 3a4 4 0 0 1 0 8M22 21v-1a5 5 0 0 0-3-4.6",
  projects: "M12 2l10 5-10 5L2 7zM2 12l10 5 10-5M2 17l10 5 10-5",
  folder: "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
  plus: "M12 5v14M5 12h14", edit: "M4 20h4L19 9l-4-4L4 16zM14 6l4 4", trash: "M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13",
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
};
const ROLE_ICON = { general_manager: "general_manager", architect: "architect", developer: "developer", frontend: "frontend", qa: "qa", research: "research_role", external_data: "external_data", analyst: "analyst", comprehensive_analyst: "analyst", security: "security", network_security: "security", security_guard: "security", devops: "devops", tech_scout: "learning", tech_introducer: "learning", instructor: "learning", student: "learning", product: "business", sales: "business", administrator: "admin", agent_manager: "admin" };
// An icon-only button: the label becomes the tooltip and the accessible name.
const ib = (name, label, attrs = "", cls = "") => `<button type="button" class="btn icon ${cls}" title="${esc(label)}" aria-label="${esc(label)}" ${attrs}>${icon(name, 16)}</button>`;
const icon = (name, size = 20) => `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${P[name] || P.specialist}"/></svg>`;
const roleIcon = (role) => icon(ROLE_ICON[role] || "specialist");
const pill = (s) => `<span class="pill ${esc(s)}">${esc(String(s).replace(/_/g, " "))}</span>`;

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
  try { navLayout = (await api("/api/ui/nav_layout")).value || null; } catch { navLayout = null; }
  renderRail(); show(VIEWS.some((v) => v.id === current && (!v.admin || me.is_admin)) ? current : "dashboard");
  connectLive();
}
// --- rail: two groups (top, bottom) in the order an administrator set (Settings → Navigation) -----------
const NAV_DEFAULT = { top: ["dashboard", "projects", "agents", "models", "evolution", "research", "performance"],
  bottom: ["workspace", "files", "machines", "notifications", "audit", "system", "settings", "users"] };
let navLayout = null, railEditing = false;
function navGroups() {
  const lay = navLayout && (navLayout.top || navLayout.bottom) ? navLayout : NAV_DEFAULT;
  const seen = new Set(), vis = VIEWS.filter((v) => !v.admin || me.is_admin), by = Object.fromEntries(vis.map((v) => [v.id, v]));
  const pick = (ids) => ids.filter((id) => by[id] && !seen.has(id) && seen.add(id)).map((id) => by[id]);
  // A view the saved order does not name takes its default place (before the next default neighbour it has).
  const saved = new Set([...(lay.top || []), ...(lay.bottom || []), ...(lay.hidden || [])]);
  const withDefaults = (g) => {
    const ids = [...(lay[g] || [])];
    NAV_DEFAULT[g].forEach((id, i) => {
      if (saved.has(id)) return;
      const next = NAV_DEFAULT[g].slice(i + 1).find((x) => ids.includes(x));
      ids.splice(next ? ids.indexOf(next) : ids.length, 0, id);
    });
    return ids;
  };
  const top = pick(withDefaults("top")), bottom = pick(withDefaults("bottom")), hidden = pick((lay.hidden || []).filter((id) => id !== "settings"));
  return { top, bottom: [...bottom, ...vis.filter((v) => !seen.has(v.id))], hidden };  // views with no default place go to the bottom
}
function railButton(v) {
  return `<button data-v="${v.id}" title="${v.title}" aria-label="${v.title}" class="${v.id === current ? "active" : ""}" draggable="${railEditing}">${icon(v.icon)}<span class="rail-label">${v.title}</span>${badges[v.id] ? `<span class="badge">${badges[v.id]}</span>` : ""}</button>`;
}
function renderRail() {
  const g = navGroups();
  $("#rail").innerHTML = `<a class="brand" href="#" data-home title="Agent Company" aria-label="Agent Company: dashboard">${BRAND}</a>
    <div class="rail-group" data-group="top">${g.top.map(railButton).join("")}</div><div class="rail-gap"></div>
    <div class="rail-group" data-group="bottom">${g.bottom.map(railButton).join("")}</div>
    ${me.is_admin ? `<button class="rail-edit${railEditing ? " on" : ""}" id="rail-edit" title="${railEditing ? "Done: save the order" : "Edit: drag icons to reorder (also in Settings → Navigation)"}" aria-label="Edit navigation">${icon(railEditing ? "check" : "grip", 18)}</button>` : ""}`;
  $("#rail").classList.toggle("editing", railEditing);
  $("#rail").querySelectorAll("button[data-v]").forEach((b) => {
    b.onclick = () => { if (!railEditing) show(b.dataset.v); };
    b.onpointerenter = b.onfocus = () => { if (!railEditing) prefetchView(b.dataset.v); };
    b.classList.toggle("pending", b.dataset.v === current && $("#view").classList.contains("loading"));
  });
  $("#rail [data-home]").onclick = (e) => { e.preventDefault(); show("dashboard"); };
  $("#rail-edit") && ($("#rail-edit").onclick = () => { railEditing = !railEditing; if (!railEditing) saveNav(readNav($("#rail"))); renderRail(); });
  if (railEditing) wireNavDrag($("#rail"), () => {});
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
const readNav = (root) => {
  const ids = (g) => [...root.querySelectorAll(`[data-group="${g}"] [data-v]`)].map((b) => b.dataset.v);
  const hiddenNow = root.querySelector('[data-group="hidden"]') ? ids("hidden") : (navLayout?.hidden || []);
  return { top: ids("top"), bottom: ids("bottom"), hidden: hiddenNow };
};
async function saveNav(lay) {
  try { navLayout = (await api("/api/ui/nav_layout", { method: "PUT", body: { value: lay } })).value; toast("Navigation saved for every user"); } catch (e) { toast(e.message); }
  renderRail();
}
// The home icon: a small enchanted emblem (gradient, a twinkling star and fairy dust).
const BRAND = `<svg viewBox="0 0 40 40" width="34" height="34" aria-hidden="true"><defs><linearGradient id="bg-g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#7c3aed"/><stop offset=".55" stop-color="#db2777"/><stop offset="1" stop-color="#f59e0b"/></linearGradient>
  <radialGradient id="bg-s" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="#fff"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient></defs>
  <rect x="2" y="2" width="36" height="36" rx="11" fill="url(#bg-g)"/><path class="brand-castle" d="M10 30V19l3-2v-3h2v2l2-1.5L19 16v-4l1-2 1 2v4l2-1.5 2 1.5v-2h2v3l3 2v11z" fill="#fff" opacity=".95"/>
  <path d="M18 30v-5a2 2 0 0 1 4 0v5z" fill="url(#bg-g)"/><path class="brand-star" d="M30 6l1.2 2.8L34 10l-2.8 1.2L30 14l-1.2-2.8L26 10l2.8-1.2z" fill="#fff"/>
  <circle class="brand-dust d1" cx="8" cy="9" r="1.2" fill="url(#bg-s)"/><circle class="brand-dust d2" cx="34" cy="20" r="1" fill="#fff"/><circle class="brand-dust d3" cx="7" cy="22" r=".9" fill="#fff"/></svg>`;
async function updateBadges() {
  try {
    const o = await api("/api/overview", { fresh: true });
    if (o.build && BUILD && o.build !== BUILD) newBuild = true;  // reloaded on the next page switch
    badges = { users: o.pending_users || 0, notifications: o.unread || 0, projects: (o.pending_proposals || 0) + (o.goals.awaiting_approval || 0) + (o.goals.running || 0) };
    renderRail(); setFavicon(o);
  } catch { /* signed out */ }
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
function show(id) {
  const view = $("#view");
  if (newBuild && id !== current && !window.WSP?.dirtyTabs?.()) { store.set("ac.view", id); location.reload(); return; }
  if (id !== current) {
    VIEWS.find((x) => x.id === current)?.leave?.();  // e.g. Machines closes its console
    if (!SELF_UPDATING.has(current) && !view.classList.contains("loading"))
      viewCache[current] = { html: view.innerHTML, cls: ["flush", "fit"].filter((c) => view.classList.contains(c)) };
  }
  current = id; store.set("ac.view", id);
  view.classList.remove("flush", "fit"); view.scrollTop = 0;  // a new page starts at its top
  const v = VIEWS.find((x) => x.id === id); $("#view-title").textContent = v.title; renderRail();
  const snap = !SELF_UPDATING.has(id) && viewCache[id];
  if (snap) { view.innerHTML = snap.html; view.classList.add(...snap.cls); enter(); }
  else if (!SELF_UPDATING.has(id)) view.innerHTML = `<div class="view-wait muted"><span class="spin"></span> Loading ${esc(v.title)}…</div>`;
  // The bar and an inert page until the view has its data; the last page is only a picture of it.
  const seq = ++showSeq;
  show.overwritten = false;
  view.classList.remove("stale"); view.classList.add("loading"); view.inert = !!snap; $("#loadbar").hidden = false;
  $("#rail").querySelector(`button[data-v="${id}"]`)?.classList.add("pending");
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
    $("#rail").querySelectorAll("button.pending").forEach((b) => b.classList.remove("pending"));
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
const SELF_UPDATING = new Set(["workspace", "files", "machines"]);
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

// --- goals: no page of their own any more (orders go through the General Manager); a goal opens in a modal
let selectedGoal = null;
function goalModal(id) {
  selectedGoal = id;
  modal(`<div id="goal-detail"><div class="cc-working"><span class="spin"></span> Loading goal #${Number(id)}…</div></div>`);
  goalDetail(id).catch((e) => ($("#goal-detail").innerHTML = `<p class="muted">${esc(e.message)}</p>`));
}
async function goalDetail(id, openIds = new Set()) {
  const g = await api(`/api/goals/${id}`);
  const iters = [...new Set(g.tasks.map((t) => t.iteration))].sort((a, b) => b - a);
  const actions = [
    g.status === "awaiting_approval" && me.is_admin ? ib("check", "Approve and merge", 'data-act="approve"', "primary") + ib("x", "Reject", 'data-act="reject"', "danger") : "",
    ["queued", "running"].includes(g.status) ? ib("stop", "Cancel the goal", 'data-act="cancel"', "danger") : "",
  ].join("");
  $("#goal-detail").innerHTML = `<div class="row"><h2 class="grow" style="margin:0">#${g.id} ${esc(g.title)}</h2>${pill(g.status)}${actions}</div>
    <p class="muted">Iteration ${g.iteration}/${g.max_iterations} · branch <code>${esc(g.branch || "-")}</code> · worktree <code>${esc(g.worktree || "-")}</code></p>
    ${g.result ? `<pre>${esc(g.result)}</pre>` : ""}
    ${Object.keys(g.baseline || {}).length ? `<h3>Before any change</h3>${Object.entries(g.baseline).map(([c, r]) => `<div class="muted">${r.passed ? "✓" : "✗"} <code>${esc(c)}</code> ${r.passed ? "passed" : "already failing"}</div>`).join("")}` : ""}
    ${(g.history || []).length ? `<h3>Iterations</h3><div class="row">${g.history.map((h) => `<span class="pill ${h.passed ? "done" : "failed"}" title="${esc(h.commit || "")}">#${h.iteration} ${Math.round(h.score * 100)}%</span>`).join("")}</div>` : ""}
    ${(g.allowed_paths || []).length || g.protect_tests ? `<p class="muted">Scope: ${esc((g.allowed_paths || []).join(", ") || "anywhere")}${g.protect_tests ? " · existing tests protected" : ""}</p>` : ""}
    ${iters.map((it) => `<h3>Iteration ${it}</h3>${g.tasks.filter((t) => t.iteration === it).map((t) => `<details data-id="t${t.id}" ${openIds.has("t" + t.id) ? "open" : ""}><summary><span class="row" style="display:inline-flex">${pill(t.status)} <strong>${esc(t.title)}</strong> <span class="muted">${esc(t.role)} · ${esc(t.model || "-")}</span></span></summary>
      ${(t.gate?.acceptance || []).map((c) => `<div class="muted">${c.passed ? "✓" : "✗"} ${esc(JSON.stringify(c.condition))} ${esc(c.detail || "")}</div>`).join("")}
      ${t.gate?.files?.length ? `<div class="muted">Files: ${esc(t.gate.files.join(", "))}</div>` : ""}<pre>${esc(t.output || "(no output yet)")}</pre></details>`).join("")}`).join("")}
    ${(g.defects || []).length ? `<h3>Defects</h3>${g.defects.map((d) => `<div class="row">${pill(d.status === "closed" ? "done" : d.status === "accepted_risk" || d.status === "deferred" ? "pending" : "failed").replace(/>[^<]+</, `>${esc(d.status.replace("_", " "))}<`)}<span class="pill">${esc(d.severity)}</span><span class="grow">${esc(d.check)}</span><span class="muted">iterations ${d.first_iteration}–${d.closed_iteration || d.last_iteration}</span></div>`).join("")}` : ""}
    <h3>Activity</h3><div class="events">${g.events.map((e) => `<div><span class="k">${esc(e.kind)}</span>${esc(e.message)} <span class="muted">${ago(e.at)}</span></div>`).join("")}</div>`;
  $("#goal-detail").querySelectorAll("[data-act]").forEach((b) => (b.onclick = async () => {
    try { await api(`/api/goals/${id}/${b.dataset.act}`, { method: "POST" }); toast(`Goal ${b.dataset.act}d`); goalDetail(id); } catch (err) { toast(err.message); }
  }));
}

// --- research -------------------------------------------------------------------
async function viewResearch(quiet) {
  if (quiet && $("#r-crawl")) return; // keep forms and results stable
  const [docs, src, reports] = await Promise.all([api("/api/research/documents"), api("/api/research/sources"), api("/api/research/requests")]);
  $("#view").innerHTML = `<div class="grid two"><div class="grid">
    <div class="card"><h2>Collect a website</h2><form id="r-crawl"><label>URL</label><input id="r-url" type="url" required placeholder="https://docs.crewai.com/">
      <div class="row"><div class="grow"><label>Link depth (0 = this page only)</label><input id="r-depth" type="number" min="0" max="3" value="1"></div><div class="grow"><label>Max pages</label><input id="r-max" type="number" min="1" max="200" value="20"></div></div>
      <div class="row" style="margin-top:10px"><button class="btn primary">Collect</button></div></form><div id="r-out"></div></div>
    <div class="card"><h2>Rotation sources</h2><p class="muted">The external-data room visits these in turn${src.interval ? ` every ${src.interval}s` : " (set ROTATION_INTERVAL to run automatically)"}. Roster: ${src.roster.length} agent(s).</p>
      ${src.sources.map((s) => `<div class="events"><div>${esc(s.url)} <span class="muted">depth ${s.depth} · ${ago(s.last_run_at)}</span></div></div>`).join("")}
      ${me.is_admin ? `<form id="r-src" class="row" style="margin-top:8px"><input id="r-src-url" class="grow" type="url" required placeholder="https://news.example.com/"><button class="btn">Add</button><button class="btn" type="button" id="r-rotate">Run next</button></form>` : ""}</div></div>
    <div class="grid"><div class="card"><h2>Ask the research room</h2><form id="r-ask"><label>Question</label><textarea id="r-question" rows="2" required minlength="5" placeholder="Which agent framework fits a hierarchical team, and why?"></textarea>
      <label>Sources to read first (optional, one URL per line)</label><textarea id="r-urls" rows="2" placeholder="https://docs.crewai.com/"></textarea><div class="row" style="margin-top:8px"><button class="btn primary">Ask</button></div></form>
      <div class="events" style="margin-top:8px">${reports.map((r) => `<div class="row"><a href="#" data-report="${r.id}" class="grow">${esc(r.question)}</a>${pill(r.status)}<span class="muted">${r.sources.length} source(s) · ${ago(r.at)}</span></div>`).join("") || '<span class="muted">No reports yet.</span>'}</div></div>
    <div class="card"><h2>Search knowledge</h2><form id="r-search" class="row"><input id="r-q" class="grow" placeholder="What do we know about…"><button class="btn">Search</button></form><div id="r-hits"></div></div>
    <div class="card"><h2>Collected pages (${docs.length})</h2><div class="events">${docs.slice(0, 80).map((d) => `<div><a href="${esc(d.url)}" target="_blank" rel="noopener noreferrer">${esc(d.title || d.url)}</a> <span class="pill">${esc(d.category)}</span>${(d.flags || []).length ? ` <span class="pill failed" title="kept out of agent prompts">${esc(d.flags.join(", "))}</span>` : ""} <span class="muted">${fmt.format(d.chars)} chars · ${ago(d.at)}</span></div>`).join("") || '<span class="muted">Nothing collected yet.</span>'}</div></div></div></div>`;
  $("#r-crawl").onsubmit = async (e) => {
    e.preventDefault(); $("#r-out").innerHTML = '<p class="muted">Collecting…</p>';
    try { const r = await api("/api/research/crawl", { method: "POST", body: { url: $("#r-url").value, depth: +$("#r-depth").value, max_pages: +$("#r-max").value } }); $("#r-out").innerHTML = `<p>Stored ${r.stored} of ${r.pages} page(s).</p>${r.errors.length ? `<pre>${esc(r.errors.map((x) => `${x.url}: ${x.error}`).join("\n"))}</pre>` : ""}`; setTimeout(() => viewResearch(), 1500); }
    catch (err) { $("#r-out").innerHTML = `<p class="muted">${esc(err.message)}</p>`; }
  };
  $("#r-search").onsubmit = async (e) => { e.preventDefault(); const hits = await api(`/api/research/search?q=${encodeURIComponent($("#r-q").value)}`); $("#r-hits").innerHTML = hits.map((h) => `<h3><a href="${esc(h.url)}" target="_blank" rel="noopener noreferrer">${esc(h.title || h.url)}</a> <span class="pill">${esc(h.category)}</span>${h.flags.length ? ` <span class="pill failed">${esc(h.flags.join(", "))}</span>` : ""}</h3><pre>${esc(h.text)}</pre>`).join("") || '<p class="muted">No matches.</p>'; };
  $("#r-ask").onsubmit = async (e) => { e.preventDefault(); try { await api("/api/research/requests", { method: "POST", body: { question: $("#r-question").value, urls: $("#r-urls").value.split("\n").map((x) => x.trim()).filter(Boolean) } }); toast("Asked; the report appears here when it is ready"); setTimeout(() => viewResearch(), 1500); } catch (err) { toast(err.message); } };
  document.querySelectorAll("[data-report]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); const r = reports.find((x) => x.id === Number(a.dataset.report)); modal(`<h2>${esc(r.question)}</h2>${pill(r.status)}<div class="md">${typeof renderMarkdown === "function" ? renderMarkdown(r.report || "(not ready yet)") : `<pre>${esc(r.report)}</pre>`}</div>`); }));
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
        !u.is_admin ? btn(u, "perms", "Permissions") : "",
        btn(u, "git", "Git"),
        !u.is_admin ? btn(u, "delete", "Delete", "danger") : ""].join("")}</td></tr>`).join("")}</table></div></div>`;
  document.querySelectorAll("[data-u]").forEach((b) => (b.onclick = async () => {
    const { u, act } = b.dataset;
    const user = users.find((x) => x.id === Number(u));
    if (act === "perms") return permissionsForm(user);
    if (act === "git") return gitForm(user);
    if (["delete", "reset_device"].includes(act) && !confirm(`${act.replace("_", " ")} this user?`)) return;
    try { await api(act === "delete" ? `/api/users/${u}` : `/api/users/${u}/${act}`, { method: act === "delete" ? "DELETE" : "POST" }); viewUsers(); updateBadges(); } catch (e) { toast(e.message); }
  }));
}

// --- dynamic favicon: running goals (blue) or waiting approvals (amber) -----------
function setFavicon(o) {
  const running = o.goals.running || 0, waiting = o.goals.awaiting_approval || 0;
  const n = running || waiting, dot = running ? "%232563eb" : "%23d97706";
  const base = "%3Cdefs%3E%3ClinearGradient id='g' x1='0' y1='0' x2='1' y2='1'%3E%3Cstop offset='0' stop-color='%237c3aed'/%3E%3Cstop offset='.55' stop-color='%23db2777'/%3E%3Cstop offset='1' stop-color='%23f59e0b'/%3E%3C/linearGradient%3E%3C/defs%3E%3Crect x='2' y='2' width='36' height='36' rx='11' fill='url(%23g)'/%3E%3Cpath d='M10 30V19l3-2v-3h2v2l2-1.5L19 16v-4l1-2 1 2v4l2-1.5 2 1.5v-2h2v3l3 2v11z' fill='white'/%3E";
  const badge = n ? `%3Ccircle cx='30' cy='10' r='9' fill='${dot}' stroke='white' stroke-width='2'/%3E%3Ctext x='30' y='14' font-size='11' font-family='sans-serif' font-weight='700' text-anchor='middle' fill='white'%3E${n > 9 ? "9+" : n}%3C/text%3E`
    : "%3Cpath d='M30 6l1.2 2.8L34 10l-2.8 1.2L30 14l-1.2-2.8L26 10l2.8-1.2z' fill='white'/%3E";
  document.querySelector("link[rel=icon]").href = `data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 40 40'%3E${base}${badge}%3C/svg%3E`;
  document.title = n ? `(${n}) Agent Company` : "Agent Company";
}

registerView({ id: "dashboard", title: "Dashboard", icon: "dashboard" }, viewDashboard);
registerView({ id: "research", title: "Research", icon: "research" }, viewResearch);
registerView({ id: "users", title: "Users", icon: "users", admin: true }, viewUsers);

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
window.addEventListener("resize", () => applyProjectLayout());
applyTheme(store.get("ac.theme"));
api("/api/auth/me").then(boot).catch(tryDevice);
}
