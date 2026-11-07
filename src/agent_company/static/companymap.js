// Company map (COMPANY-DESIGN §4): one read-only picture of §1–§3. The room tree from /api/rooms as nested boxes, the
// engine line General Management → Organization Engine → Evolution Engine → Orchestrator → Bridge → Development Flow
// in the main column and the other rooms beside it; each room's enabled agents with the model they use and the route's
// fallbacks; the live goals with their lifecycle step. Built from existing endpoints only; nothing here writes.
const CM_ENGINE = ["organization_engine", "evolution_engine", "orchestrator", "bridge", "development"];
const CM_STEPS = ["discover", "plan", "execute", "critique", "validate", "learn"];
const CM_LIVE = ["queued", "planning", "running", "recovering", "paused", "awaiting_approval"];
const CM_CLOSED = ["approved", "completed", "failed", "skipped", "cancelled"];

// COMPANY-DESIGN §3: the step a goal is in, from its status and the current iteration's tasks (the caller filters them).
function cmLifecycleStep(goal, tasks) {
  const st = (goal && goal.status) || "";
  if (st === "queued") return "discover";
  if (st === "planning") return "plan";
  if (st === "awaiting_approval") return "learn";
  if (!["running", "recovering", "paused"].includes(st)) return "plan";
  const ts = tasks || [];
  if (!ts.length) return "plan";
  const reviewer = (t) => t.role === "qa" || String(t.role || "").includes("review");
  if (ts.some((t) => t.stage === "review" || (reviewer(t) && ["in_progress", "assigned"].includes(t.stage)))) return "critique";
  if (ts.some((t) => ["in_progress", "assigned", "planned", "reopened"].includes(t.stage) || ["running", "queued"].includes(t.status))) return "execute";
  if (ts.every((t) => CM_CLOSED.includes(t.stage))) return "validate";
  return "execute";
}

function cmBadge(text, title = "", small = false) {
  return `<span class="pill" title="${esc(title)}" style="${small ? "font-size:10px;opacity:.8" : ""}">${esc(text)}</span>`;
}

// The model an agent uses: its own preference, else its role's route, else the default; then the route's fallbacks.
function cmModels(a, m) {
  const pref = a.model_pref || "";
  let main;
  if (pref.startsWith("agent:")) main = cmBadge(`via agent #${pref.slice(6)}`, "model of another agent");
  else if (pref) main = cmBadge(pref, "the agent's own model");
  else if (m.routes && m.routes[a.role]) main = cmBadge(`${m.routes[a.role]} (route)`, `route ${a.role}`);
  else main = cmBadge(`${m.default_model || "?"} (default)`, "default model");
  const fb = ((m.fallbacks && m.fallbacks[a.role]) || []).map((n) => cmBadge(`→ ${n}`, "fallback at a usage limit", true));
  return main + fb.join("");
}

function cmAgents(list, m) {
  if (!list.length) return '<div class="muted" style="font-size:12px">no active agents</div>';
  return list.map((a) => `<div class="row" style="flex-wrap:wrap;gap:4px;margin:3px 0"><strong>${esc(a.name)}</strong><span class="muted">${esc(a.role)}</span>${cmModels(a, m)}</div>`).join("");
}

function cmRoom(r, kids, byRoom, m, small = false) {
  const children = kids[r.key] || [];
  const next = children.find((c) => CM_ENGINE.includes(c.key));
  const others = children.filter((c) => c !== next);
  const off = r.enabled === false;
  return `<div class="cm-room" data-room="${esc(r.key)}" style="border:1px solid var(--border);border-radius:8px;padding:8px;margin:6px 0;background:var(--panel-2);${off ? "opacity:.55;" : ""}${small ? "flex:1 1 220px;" : ""}">
    <div class="row" style="gap:6px"><strong>${esc(r.name || r.key)}</strong><span class="muted" style="font-size:11px">${esc(r.key)}</span>${off ? pill("disabled") : ""}</div>
    ${cmAgents(byRoom[r.key] || [], m)}
    ${others.length ? `<div class="row" style="flex-wrap:wrap;align-items:stretch;gap:6px">${others.map((c) => cmRoom(c, kids, byRoom, m, true)).join("")}</div>` : ""}
    ${next ? cmRoom(next, kids, byRoom, m) : ""}</div>`;
}

function cmSteps(step) {
  return `<span class="row nowrap" style="gap:2px">${CM_STEPS.map((s) => `<span class="pill${s === step ? " running" : ""}" style="${s === step ? "font-weight:700" : "opacity:.45"}">${esc(s)}</span>`).join("")}</span>`;
}

const cmFail = (path, e) => `<div class="card" style="margin-top:12px"><span class="pill failed">error</span> ${esc(`Could not load ${path}: ${(e && e.message) || e}`)}</div>`;

async function viewCompanyMap() {
  const v = $("#view");
  const load = (p) => api(p).then((d) => ({ d }), (e) => ({ e }));
  const [rr, ar, mr, gr] = await Promise.all(["/api/rooms", "/api/agents", "/api/models", "/api/goals"].map(load));
  const rooms = Array.isArray(rr.d) ? rr.d : [], agents = (Array.isArray(ar.d) ? ar.d : []).filter((a) => a.enabled);
  const m = mr.d || {}, goals = Array.isArray(gr.d) ? gr.d : [];
  const keys = new Set(rooms.map((r) => r.key)), kids = {}, roots = [], byRoom = {}, unassigned = [];
  for (const r of rooms) {
    const p = r.parent ?? r.parent_key;
    if (p && keys.has(p) && p !== r.key) (kids[p] = kids[p] || []).push(r); else roots.push(r);
  }
  for (const a of agents) (keys.has(a.room) ? (byRoom[a.room] = byRoom[a.room] || []) : unassigned).push(a);
  // The engine line in the main column; every other root child (research, analysis, security, business …) beside it.
  const side = [], main = roots.map((r) => {
    const children = kids[r.key] || [];
    side.push(...children.filter((c) => !CM_ENGINE.includes(c.key)));
    kids[r.key] = children.filter((c) => CM_ENGINE.includes(c.key));
    return cmRoom(r, kids, byRoom, m);
  });
  const live = goals.filter((g) => CM_LIVE.includes(g.status)).slice(0, 20);
  const details = await Promise.all(live.map((g) => api(`/api/goals/${encodeURIComponent(g.id)}`).catch(() => null)));
  const goalRow = (g, i) => {
    const d = details[i], tasks = d && Array.isArray(d.tasks) ? d.tasks.filter((t) => t.iteration === g.iteration) : [];
    return `<div class="row" style="flex-wrap:wrap;gap:6px;margin:4px 0"><a href="#" data-goal="${esc(g.id)}">#${esc(g.id)} ${esc(g.title)}</a>${pill(g.status)}${g.status === "paused" ? pill("paused") : ""}${cmSteps(cmLifecycleStep(g, tasks))}${d ? "" : '<span class="muted">tasks unavailable</span>'}</div>`;
  };
  v.innerHTML = `${[["/api/rooms", rr], ["/api/agents", ar], ["/api/models", mr], ["/api/goals", gr]].filter(([, x]) => x.e).map(([p, x]) => cmFail(p, x.e)).join("")}
    <div class="card" style="margin-top:12px"><h2>Live goals</h2>${live.map(goalRow).join("") || '<span class="muted">No live goals.</span>'}</div>
    <div class="row" style="align-items:flex-start;gap:12px;margin-top:12px;flex-wrap:wrap">
      <div class="card" style="flex:2 1 480px"><h2>Engine line</h2>${main.join("") || '<span class="muted">No rooms.</span>'}</div>
      <div class="card" style="flex:1 1 280px"><h2>Beside the line</h2>${side.map((r) => cmRoom(r, kids, byRoom, m)).join("") || '<span class="muted">No other rooms.</span>'}
        ${unassigned.length ? `<div class="cm-room" style="border:1px dashed var(--border);border-radius:8px;padding:8px;margin:6px 0"><strong>Unassigned</strong>${cmAgents(unassigned, m)}</div>` : ""}</div></div>`;
  v.querySelectorAll("[data-goal]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); goalModal(Number(a.dataset.goal)); }));
}

registerView({ id: "companymap", title: "Company map", icon: "network" }, viewCompanyMap);
