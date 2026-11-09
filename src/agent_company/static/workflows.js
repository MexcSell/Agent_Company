// Workflows (AICO UI-WF-01…05): each goal's execution graph — its current iteration's tasks as nodes in layers by
// dependency (parallel branches side by side), then the quality gate and the approval — with a node drawer (inputs,
// output, acceptance checks, findings, stage log, handoffs), the goal's agent messages beside it and the workflow
// templates. Read-only: retrying a task stays the goal loop's (open: POST /api/tasks/{id}/retry).
let wfFilter = { project: "", status: "live", blocked: false }, wfOpen = new Set(JSON.parse(store.get("ac.wf.open") || "[]"));
const WF_STATUS = [["queued", "Queued", "neutral"], ["ready", "Ready", "info"], ["running", "Running", "info"], ["waiting", "Waiting", "neutral"],
  ["blocked", "Blocked", "warn"], ["failed", "Failed", "bad"], ["succeeded", "Succeeded", "ok"], ["cancelled", "Cancelled", "neutral"]];
const WF_TONE = Object.fromEntries(WF_STATUS.map(([k, , t]) => [k, t]));
const WF_LIVE = ["queued", "planning", "running", "recovering", "paused", "awaiting_approval"];

function wfColumn(t, done) {  // dashboard.board_column, in the browser
  if (t.stage === "blocked") return "blocked";
  if (t.status === "done") return "succeeded";
  if (t.status === "failed" || t.stage === "failed") return "failed";
  if (t.status === "skipped" || t.stage === "cancelled") return "cancelled";
  if (t.status === "running") return "running";
  if (t.stage === "created") return "queued";
  return (t.depends_on || []).every((k) => done.has(k)) ? "ready" : "waiting";
}
const wfTime = (iso) => Date.parse(iso + (/Z$|[+-]\d\d:?\d\d$/.test(iso) ? "" : "Z"));  // the server's times are UTC
function wfSecs(t) {
  if (!t.started_at) return null;
  const end = t.finished_at ? wfTime(t.finished_at) : Date.now();
  return Math.max(0, Math.round((end - wfTime(t.started_at)) / 1000));
}
function wfGraph(g, names) {
  const ts = g.tasks.filter((t) => t.iteration === g.iteration);
  if (!ts.length) return `<p class="muted dash-meta">${g.status === "planning" || g.status === "queued" ? "The General Manager is planning this goal…" : "No task in this iteration."}</p>`;
  const done = new Set(ts.filter((t) => ["done", "skipped"].includes(t.status)).map((t) => t.key)), byKey = new Map(ts.map((t) => [t.key, t]));
  const level = new Map(), lv = (k, seen = new Set()) => {
    if (level.has(k)) return level.get(k);
    if (seen.has(k)) return 0; seen.add(k);
    const deps = (byKey.get(k)?.depends_on || []).filter((d) => byKey.has(d));
    const v = deps.length ? 1 + Math.max(...deps.map((d) => lv(d, seen))) : 0; level.set(k, v); return v;
  };
  ts.forEach((t) => lv(t.key));
  const layers = []; ts.forEach((t) => (layers[level.get(t.key)] ||= []).push(t));
  const last = (g.history || []).find((h) => h.iteration === g.iteration);
  const gate = last ? (last.passed ? "succeeded" : "failed") : ts.every((t) => ["done", "skipped", "failed"].includes(t.status)) && g.status === "running" ? "running" : "queued";
  const appr = g.status === "approved" ? "succeeded" : g.status === "awaiting_approval" ? "waiting" : ["rejected", "failed", "cancelled"].includes(g.status) ? (g.status === "rejected" ? "failed" : "cancelled") : "queued";
  const W = 190, H = 74, GX = 60, GY = 14, cols = [...layers.map((l) => l.length), 1, 1], tall = Math.max(...cols);
  const pos = new Map();
  layers.forEach((l, i) => l.forEach((t, j) => pos.set(t.key, { x: i * (W + GX), y: (tall - l.length) * (H + GY) / 2 + j * (H + GY) })));
  const gx = layers.length * (W + GX), gy = (tall - 1) * (H + GY) / 2;
  pos.set("@gate", { x: gx, y: gy }); pos.set("@approval", { x: gx + W + GX, y: gy });
  const width = gx + 2 * W + GX, height = tall * (H + GY) - GY;
  const edge = (a, b, ok) => { const p = pos.get(a), q = pos.get(b), x1 = p.x + W, y1 = p.y + H / 2, x2 = q.x, y2 = q.y + H / 2, mx = (x1 + x2) / 2;
    return `<path d="M${x1} ${y1} C${mx} ${y1} ${mx} ${y2} ${x2} ${y2}" class="wf-edge${ok ? " ok" : ""}"/>`; };
  const ends = ts.filter((t) => !ts.some((u) => (u.depends_on || []).includes(t.key)));
  const edges = [...ts.flatMap((t) => (t.depends_on || []).filter((d) => byKey.has(d)).map((d) => edge(d, t.key, done.has(d)))),
    ...ends.map((t) => edge(t.key, "@gate", done.has(t.key))), edge("@gate", "@approval", gate === "succeeded")].join("");
  const node = (t) => { const c = wfColumn(t, done), p = pos.get(t.key), s = wfSecs(t), retries = (t.stage_log || []).filter((x) => x.to === "reopened").length;
    return `<button class="wf-node ${WF_TONE[c]}" style="left:${p.x}px;top:${p.y}px;width:${W}px;height:${H}px" data-task="${t.id}" title="${esc(t.title)}"><span class="row nowrap"><span class="wf-dot"></span><strong class="ellipsis grow">${esc(t.title)}</strong></span>
      <span class="muted ellipsis">${esc(t.role)}${t.agent_id && names[t.agent_id] ? " · " + esc(names[t.agent_id]) : ""}</span><span class="muted">${c}${s != null ? " · " + dur(s).replace(/^0h /, "") : ""}${retries ? ` · ${retries} retr${retries === 1 ? "y" : "ies"}` : ""}</span></button>`; };
  const fixed = (k, label, c, sub) => { const p = pos.get(k); return `<div class="wf-node fixed ${WF_TONE[c]}" style="left:${p.x}px;top:${p.y}px;width:${W}px;height:${H}px"><span class="row nowrap"><span class="wf-dot"></span><strong>${label}</strong></span><span class="muted">${sub}</span></div>`; };
  return `<div class="wf-canvas"><div class="wf-inner" style="width:${width}px;height:${height}px"><svg class="wf-svg" width="${width}" height="${height}">${edges}</svg>${ts.map(node).join("")}
    ${fixed("@gate", "Quality gate", gate, last ? (last.passed ? "passed" : `failed: ${esc((last.failed_checks || []).slice(0, 3).join(", "))}`) : "after the tasks")}${fixed("@approval", "Approval &amp; merge", appr, esc(g.status.replace(/_/g, " ")))}</div></div>`;
}

async function viewWorkflows(quiet) {
  const [goals, projs, agents] = await Promise.all([api("/api/goals"), api("/api/projects").catch(() => []), api("/api/agents").catch(() => [])]);
  const names = Object.fromEntries(agents.map((a) => [a.id, a.name])), pname = Object.fromEntries(projs.map((p) => [p.id, p.name]));
  if (!quiet || !$("#wf")) {
    $("#view").innerHTML = `<div id="wf"><div class="page-head"><div class="page-head-text"><h2 class="page-title">Workflows</h2><p class="muted page-sub">How each goal's work flows: tasks by dependency, the quality gate and the approval. Click a task for its inputs, output and checks.</p></div></div>
      <div class="ag-tabs" role="tablist"><button role="tab" data-wft="graphs" aria-selected="${(store.get("ac.wf.tab") || "graphs") === "graphs"}">${icon("network", 16)} Execution</button><button role="tab" data-wft="templates" aria-selected="${store.get("ac.wf.tab") === "templates"}">${icon("book", 16)} Templates</button></div>
      <div id="wf-body"></div></div>`;
    $("#view").querySelectorAll("[data-wft]").forEach((b) => (b.onclick = () => { store.set("ac.wf.tab", b.dataset.wft); viewWorkflows(); }));
  }
  if (store.get("ac.wf.tab") === "templates") return wfTemplates();
  const body = $("#wf-body");
  if (!$("#wf-tools")) {
    body.innerHTML = `<div class="row pf-tools" id="wf-tools"><select data-wf="project" aria-label="Project"><option value="">Project: all</option>${projs.map((p) => `<option value="${p.id}"${String(wfFilter.project) === String(p.id) ? " selected" : ""}>${esc(p.name)}</option>`).join("")}</select>
      <select data-wf="status" aria-label="Goals"><option value="live"${wfFilter.status === "live" ? " selected" : ""}>Live goals</option><option value="recent"${wfFilter.status === "recent" ? " selected" : ""}>Live and the last 10 finished</option></select>
      <label class="row nowrap" style="margin:0"><input type="checkbox" data-wf="blocked" style="width:auto"${wfFilter.blocked ? " checked" : ""}> blocked only</label>
      <span class="grow"></span><span class="wf-legend">${WF_STATUS.map(([, l, t]) => `<span class="badge ${t}">${l}</span>`).join("")}</span></div><div id="wf-list"></div>`;
    body.querySelectorAll("[data-wf]").forEach((el) => (el.onchange = () => { wfFilter[el.dataset.wf] = el.type === "checkbox" ? el.checked : el.value; $("#wf-list").dataset.html = ""; viewWorkflows(true); }));
  }
  let shown = goals.filter((g) => g.origin !== "eval" && (!wfFilter.project || String(g.project_id) === String(wfFilter.project)));
  const live = shown.filter((g) => WF_LIVE.includes(g.status));
  shown = wfFilter.status === "recent" ? [...live, ...shown.filter((g) => !WF_LIVE.includes(g.status)).slice(0, 10)] : live;
  const details = await Promise.all(shown.slice(0, 20).map((g) => api(`/api/goals/${g.id}`).catch(() => null)));
  let rows = details.filter(Boolean);
  if (wfFilter.blocked) rows = rows.filter((g) => g.status === "paused" || g.tasks.some((t) => t.iteration === g.iteration && t.stage === "blocked"));
  const html = rows.length ? rows.map((g) => { const open = !wfOpen.has(-g.id), blocked = g.tasks.filter((t) => t.iteration === g.iteration && t.stage === "blocked");
    const msgs = (g.blackboard || []).filter((e) => e.section === "messages");
    return `<section class="card wf-goal"><div class="row nowrap wf-goal-head" data-fold="${g.id}"><span class="wf-caret">${open ? "▾" : "▸"}</span><a href="#/goals/${g.id}" class="grow ellipsis"><strong>#${g.id} ${esc(g.title)}</strong></a>${g.project_id ? `<span class="muted dash-meta">${esc(pname[g.project_id] || "")}</span>` : ""}<span class="muted dash-meta">iteration ${g.iteration}</span>${pill(g.status)}</div>
      ${g.status === "paused" ? `<div class="wf-why"><span class="badge warn">paused</span> ${esc(g.paused_for || "")}</div>` : ""}${blocked.map((t) => `<div class="wf-why"><span class="badge warn">blocked</span> ${esc(t.title)} · ${esc(names[t.agent_id] || t.role)}: ${esc(((t.gate || {}).blockers || []).join("; ") || (t.output || "").split("\n").find((l) => l.startsWith("BLOCKER")) || "")}</div>`).join("")}
      ${open ? `<div class="wf-split">${wfGraph(g, names)}${msgs.length ? `<aside class="wf-msgs"><h3>Agent messages</h3>${msgs.slice(-12).map((m) => `<div class="gm-card"><div class="muted dash-meta">${esc(names[m.author_agent_id] || "agent")} · ${esc(m.key)}</div><div class="ellipsis-2">${esc(m.content)}</div></div>`).join("")}</aside>` : ""}</div>` : ""}</section>`; }).join("")
    : `<div class="card empty"><p class="empty-title">No workflow ${wfFilter.blocked ? "is blocked" : "to show"}</p><p class="muted">A goal's workflow appears once the General Manager plans it.</p></div>`;
  const list = $("#wf-list");
  if (list.dataset.html !== html) { const sx = [...list.querySelectorAll(".wf-canvas")].map((c) => c.scrollLeft); list.innerHTML = html; list.dataset.html = html; list.querySelectorAll(".wf-canvas").forEach((c, i) => (c.scrollLeft = sx[i] || 0)); }
  list.querySelectorAll("[data-fold]").forEach((h) => (h.onclick = (e) => { if (e.target.closest("a")) return; const id = -Number(h.dataset.fold); wfOpen.has(id) ? wfOpen.delete(id) : wfOpen.add(id); store.set("ac.wf.open", JSON.stringify([...wfOpen])); list.dataset.html = ""; viewWorkflows(true); }));
  list.querySelectorAll("[data-task]").forEach((b) => (b.onclick = () => { const id = Number(b.dataset.task), g = rows.find((x) => x.tasks.some((t) => t.id === id)); wfNode(g, g.tasks.find((t) => t.id === id), names); }));
}

function wfNode(g, t, names) {
  const gate = t.gate || {}, c = t.contract || {};
  const list = (xs) => (xs && xs.length ? `<ul class="gm-acc">${xs.map((x) => `<li>${esc(typeof x === "string" ? x : x.text || x.check || x.description || JSON.stringify(x))}</li>`).join("")}</ul>` : '<p class="muted dash-meta">none</p>');
  const handoffs = (g.blackboard || []).filter((e) => e.task_id === t.id);
  openDrawer(`${t.title}`, `<div class="row nowrap"><span class="badge ${WF_TONE[wfColumn(t, new Set(g.tasks.filter((x) => x.status === "done").map((x) => x.key)))]}">${esc(t.stage || t.status)}</span><span class="muted dash-meta grow">${esc(t.role)}${t.agent_id ? " · " + esc(names[t.agent_id] || "") : ""}${t.model ? " · " + esc(t.model) : ""}</span><a class="view-all" href="#/goals/${g.id}">goal #${g.id} →</a></div>
    <h3>Input</h3><p class="dash-meta" style="white-space:pre-wrap;margin:0">${esc(String(c.description || t.title).slice(0, 1500))}</p>${(c.allowed_paths || []).length ? `<p class="muted dash-meta">paths: <span class="mono">${esc(c.allowed_paths.join(", "))}</span></p>` : ""}${(t.depends_on || []).length ? `<p class="muted dash-meta">after ${esc(t.depends_on.join(", "))}</p>` : ""}
    <h3>Acceptance checks</h3>${(gate.acceptance || []).length ? `<ul class="gm-acc">${gate.acceptance.map((a) => `<li><span class="badge ${a.passed ? "ok" : "bad"}">${a.passed ? "passed" : "failed"}</span> ${esc(a.text || a.check || (a.condition && JSON.stringify(a.condition)) || "")}</li>`).join("")}</ul>` : list(c.acceptance)}
    <h3>Output</h3>${t.output ? `<pre>${esc(t.output.slice(-3000))}</pre>` : '<p class="muted dash-meta">No output yet.</p>'}
    ${(gate.files || []).length ? `<h3>Changed files</h3>${list(gate.files)}` : ""}${(gate.findings || []).length ? `<h3>Findings</h3>${list(gate.findings)}` : ""}${(gate.blockers || []).length ? `<h3>Blockers</h3>${list(gate.blockers)}` : ""}
    ${handoffs.length ? `<h3>Handoffs</h3>${handoffs.map((e) => `<div class="gm-card"><div class="muted dash-meta">${esc(e.section)} · ${esc(e.key)} v${e.version}</div><div class="ellipsis-2">${esc(e.content)}</div></div>`).join("")}` : ""}
    <h3>Stage log</h3>${(t.stage_log || []).length ? `<ul class="timeline">${t.stage_log.map((x) => `<li class="${x.to === "failed" ? "bad" : x.to === "blocked" ? "warn" : ["approved", "completed"].includes(x.to) ? "ok" : "info"}">${esc(x.from)} → <strong>${esc(x.to)}</strong> ${esc(x.reason || "")}<time>${x.at ? ago(x.at) : ""}</time></li>`).join("")}</ul>` : '<p class="muted dash-meta">No stage changes recorded.</p>'}
    <p class="muted dash-meta">Tool calls and logs: the goal window's trace and the agent card's Tasks &amp; logs.</p>`);
}

async function wfTemplates() {
  const list = await api("/api/strategies");
  const box = $("#wf-body");
  const html = `<p class="muted">Each goal kind runs under a workflow template: the roles it may staff, whether tasks write, its checks, iteration limit, execution mode and review policy. Changes are made on the Evolution page (versions, experiments, canary).</p>
    <div class="grid pt-grid">${list.map((s) => `<section class="card"><div class="card-head"><h3>${esc(s.kind)}</h3><span class="badge info">v${s.version}</span></div>
      <div class="gm-kv"><span class="muted">Roles</span><span>${esc((s.roles || []).join(", "))}</span><span class="muted">Writes</span><span>${s.writes ? "yes" : "no"}</span><span class="muted">Iterations</span><span>${esc(String(s.max_iterations ?? "–"))}</span><span class="muted">Mode</span><span>${esc(s.execution || s.mode || "–")}</span><span class="muted">Review</span><span>${esc(typeof s.review === "object" ? JSON.stringify(s.review) : String(s.review ?? "–"))}</span><span class="muted">Checks</span><span>${esc(typeof s.checks === "object" ? JSON.stringify(s.checks) : String(s.checks ?? "–"))}</span></div>
      <div class="muted dash-meta">${s.versions.length} version(s) · ${s.versions.reduce((n, v) => n + v.goals, 0)} goal(s) ran under it${s.note ? " · " + esc(s.note) : ""}</div></section>`).join("")}</div>`;
  if (box.dataset.html !== html) { box.innerHTML = html; box.dataset.html = html; }
}
registerView({ id: "workflows", title: "Workflows", icon: "network" }, viewWorkflows);
