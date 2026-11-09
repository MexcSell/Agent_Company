// Projects (AICO UI-PROJ-01/02): the portfolio at #/projects and the project workspace's tabs Overview,
// Requirements, Tasks, Evaluation and Reports (Orders & reports, Plan and the GM console stay in projects.js).
// Every figure comes from stored records: phase and progress from /api/projects-portfolio, task columns from the
// tasks' status and stage, evaluation from the gate's history, completion from mission reports (UI-D-08).
let portfolioQuery = "", portfolioFilter = store.get("ac.portfolio.filter") || "open", portfolioSort = store.get("ac.portfolio.sort") || "priority",
  portfolioCards = store.get("ac.portfolio.view") !== "table";
const PF_FILTERS = { open: "Open", all: "All", draft: "Draft", planning: "Planning", active: "Active", blocked: "Blocked", review: "QA / Review",
  release_ready: "Release ready", paused: "Paused", completed: "Completed", archived: "Archived", cancelled: "Cancelled" };
const PF_SORTS = { priority: "Priority", activity: "Last activity", deadline: "Deadline", created: "Created", progress: "Progress" };
const PF_PRIO = { critical: 0, high: 1, normal: 2, low: 3 };
const BOARD_COLS = [["queued", "Queued", "neutral"], ["ready", "Ready", "info"], ["running", "Running", "info"], ["waiting", "Waiting", "neutral"],
  ["blocked", "Blocked", "warn"], ["failed", "Failed", "bad"], ["succeeded", "Succeeded", "ok"], ["cancelled", "Cancelled", "neutral"]];
let boardView = store.get("ac.board.view") || "board";

function pfPhase(p) { const [l, t] = DASH_PHASE[p.phase] || [p.phase, "neutral"]; return `<span class="badge ${t}">${esc(l)}</span>`; }

async function projectPortfolio(quiet) {
  const rows = await api("/api/projects-portfolio");
  const view = $("#view");
  if (!quiet || !$("#pf")) {
    view.innerHTML = `<div id="pf"><div class="page-head"><div class="page-head-text"><h2 class="page-title">Projects</h2><p class="muted page-sub">Every project, its phase, team and progress. Open one to work in it.</p></div>
      <div class="page-actions">${me.is_admin || me.permissions?.create_projects ? `<button class="btn primary" id="pf-new">${icon("plus", 16)} New project</button>` : ""}</div></div>
      <div class="row pf-tools"><input type="search" id="pf-q" placeholder="Search name, id, requirement, repository" aria-label="Search projects" value="${esc(portfolioQuery)}">
        <select id="pf-filter" aria-label="Filter">${Object.entries(PF_FILTERS).map(([k, l]) => `<option value="${k}"${k === portfolioFilter ? " selected" : ""}>${l}</option>`).join("")}</select>
        <select id="pf-sort" aria-label="Sort">${Object.entries(PF_SORTS).map(([k, l]) => `<option value="${k}"${k === portfolioSort ? " selected" : ""}>Sort: ${l}</option>`).join("")}</select>
        <div class="seg" role="group" aria-label="View"><button class="btn sm${portfolioCards ? " on" : ""}" data-pfv="cards">Cards</button><button class="btn sm${portfolioCards ? "" : " on"}" data-pfv="table">Table</button></div></div>
      <div id="pf-body"></div></div>`;
    $("#pf-new") && ($("#pf-new").onclick = () => projectForm());
    let t = 0;
    $("#pf-q").oninput = () => { clearTimeout(t); t = setTimeout(() => { portfolioQuery = $("#pf-q").value.trim().toLowerCase(); projectPortfolio(true); }, 200); };
    $("#pf-filter").onchange = (e) => { portfolioFilter = e.target.value; store.set("ac.portfolio.filter", portfolioFilter); projectPortfolio(true); };
    $("#pf-sort").onchange = (e) => { portfolioSort = e.target.value; store.set("ac.portfolio.sort", portfolioSort); projectPortfolio(true); };
    view.querySelectorAll("[data-pfv]").forEach((b) => (b.onclick = () => { portfolioCards = b.dataset.pfv === "cards"; store.set("ac.portfolio.view", b.dataset.pfv); projectPortfolio(); }));
  }
  const q = portfolioQuery;
  let list = rows.filter((p) => (portfolioFilter === "all" || (portfolioFilter === "open" ? !["completed", "archived", "cancelled"].includes(p.phase) : p.phase === portfolioFilter))
    && (!q || [p.name, String(p.id), "#" + p.id, p.description, p.github_url].some((x) => String(x || "").toLowerCase().includes(q))));
  const by = { priority: (a, b) => (PF_PRIO[a.priority] ?? 2) - (PF_PRIO[b.priority] ?? 2) || a.id - b.id,
    activity: (a, b) => String(b.last_activity || "").localeCompare(String(a.last_activity || "")),
    deadline: (a, b) => (a.deadline ? 0 : 1) - (b.deadline ? 0 : 1) || String(a.deadline || "").localeCompare(String(b.deadline || "")),
    created: (a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")), progress: (a, b) => b.progress - a.progress };
  list = list.sort(by[portfolioSort] || by.priority);
  const pct = (p) => Math.round(p.progress * 100);
  const html = !list.length ? `<div class="card empty"><p class="empty-title">${rows.length ? "No project matches" : "No project yet"}</p><p class="muted">${rows.length ? "Change the search or the filter." : "Create one, or ask the General Manager."}</p></div>`
    : portfolioCards ? `<div class="grid pf-cards">${list.map((p) => `<a class="card pf-card" href="#/projects/${p.id}"><div class="row nowrap"><span class="avatar proj-av" style="width:30px;height:30px">${icon(p.protected ? "settings" : "projects", 18)}</span><strong class="grow ellipsis">${esc(p.name)}</strong>${pfPhase(p)}</div>
        <p class="muted pf-desc">${esc(p.description || p.github_url || "No description.")}</p>
        <div class="row nowrap"><div class="progress grow"><span style="width:${pct(p)}%"></span></div><strong class="dash-pct">${pct(p)}%</strong></div>
        <div class="pf-facts"><span>${icon("general_manager", 14)} ${esc(p.manager || "–")}</span><span>${icon("agents", 14)} ${p.team} working</span><span>${icon("check", 14)} ${p.tasks_done}/${p.tasks_total} tasks</span>${p.priority !== "normal" ? `<span class="badge ${p.priority === "critical" || p.priority === "high" ? "warn" : "neutral"}">${esc(p.priority)}</span>` : ""}</div>
        <div class="muted dash-meta">${p.next_milestone ? `Next: ${esc(p.next_milestone.name)}${p.next_milestone.overdue ? " (overdue)" : ""} · ` : ""}${p.deadline ? `Due ${esc(p.deadline.slice(0, 10))} · ` : ""}active ${ago(p.last_activity)}</div></a>`).join("")}</div>`
    : `<div class="card"><div class="table-wrap"><table class="pf-table"><tr><th>Project</th><th>Phase</th><th>Priority</th><th class="num">Progress</th><th>Manager</th><th class="num">Working</th><th class="num">Tasks</th><th>Next milestone</th><th>Deadline</th><th>Last activity</th></tr>
        ${list.map((p) => `<tr data-href="#/projects/${p.id}"><td><a href="#/projects/${p.id}">${esc(p.name)}</a> <span class="muted">#${p.id}</span></td><td>${pfPhase(p)}</td><td>${esc(p.priority)}</td><td class="num">${pct(p)}%</td><td>${esc(p.manager || "–")}</td><td class="num">${p.team}</td><td class="num">${p.tasks_done}/${p.tasks_total}</td><td>${p.next_milestone ? esc(p.next_milestone.name) : "–"}</td><td>${p.deadline ? esc(p.deadline.slice(0, 10)) : "–"}</td><td>${ago(p.last_activity)}</td></tr>`).join("")}</table></div></div>`;
  const body = $("#pf-body");
  if (body.dataset.html !== html) { body.innerHTML = html; body.dataset.html = html; }
  setCrumbs([]);
}

// --- workspace tabs --------------------------------------------------------------------------------------------
async function ptOverview(id, d) {
  const [plan, port, tasks] = await Promise.all([api(`/api/projects/${id}/plan`).catch(() => null), api("/api/projects-portfolio").catch(() => []), api(`/api/projects/${id}/tasks`).catch(() => [])]);
  const p = port.find((x) => x.id === id), pct = Math.round((p?.progress ?? plan?.progress?.progress ?? 0) * 100);
  const blockers = (plan?.risks || []).filter((r) => r.kind === "blocker" && r.status === "open"), risks = (plan?.risks || []).filter((r) => r.kind !== "blocker");
  const now = tasks.filter((t) => ["running", "blocked", "ready", "waiting"].includes(t.column));
  const ms = plan?.milestones || [];
  return `<div class="grid pt-grid">
    <section class="card"><div class="card-head"><h3>Status</h3>${p ? pfPhase(p) : ""}</div><div class="row nowrap"><div class="progress grow"><span style="width:${pct}%"></span></div><strong class="dash-pct">${pct}%</strong></div>
      <div class="gm-kv" style="margin-top:8px"><span class="muted">Manager</span><span>${esc(p?.manager || "–")}</span><span class="muted">Outcome</span><span>${esc(d.target_outcome || "–")}</span><span class="muted">Team</span><span>${esc({ gm: "the GM recommends", reuse: "reuse existing teams", new: "form a new team" }[d.team_strategy] || d.team_strategy)}</span>
      <span class="muted">Budget</span><span>${d.budget_usd ? `${money(d.spent_usd)} of ${money(d.budget_usd)}` : `${money(d.spent_usd)} spent, no budget`}</span><span class="muted">Deadline</span><span>${d.deadline ? esc(d.deadline.slice(0, 10)) + (d.overdue ? ' <span class="badge bad">overdue</span>' : "") : "–"}</span></div></section>
    <section class="card"><div class="card-head"><h3>Milestones</h3><a class="view-all" href="#" data-ptab="plan">Plan →</a></div>${ms.length ? `<ul class="timeline">${ms.map((m) => `<li class="${m.status === "completed" ? "ok" : m.overdue ? "bad" : "info"}"><strong>${esc(m.name)}</strong> <span class="muted">${Math.round(m.progress * 100)}%</span><time>${m.target_date ? "due " + esc(m.target_date.slice(0, 10)) : "no date"} · ${esc(m.status)}</time></li>`).join("")}</ul>` : gmEmpty("No milestone yet.")}</section>
    <section class="card"><div class="card-head"><h3>Current tasks</h3><a class="view-all" href="#" data-ptab="tasks">Tasks →</a></div>${now.length ? `<div class="dash-list">${now.slice(0, 8).map((t) => `<div class="row nowrap"><span class="badge ${BOARD_COLS.find((c) => c[0] === t.column)[2]}">${t.column}</span><span class="grow ellipsis">${esc(t.title)}</span><span class="muted dash-meta">${esc(t.agent || t.role)}</span></div>`).join("")}</div>` : gmEmpty("Nothing running.")}</section>
    <section class="card"><div class="card-head"><h3>Blockers and risks</h3></div>${blockers.length + risks.length ? `<div class="dash-list">${[...blockers, ...risks.slice(0, 5)].map((r) => `<div><span class="badge ${r.kind === "blocker" ? "bad" : r.severity === "high" || r.severity === "critical" ? "warn" : "neutral"}">${esc(r.kind)} · ${esc(r.severity)}</span> ${esc(r.title)}</div>`).join("")}</div>` : gmEmpty("No open blocker or risk.")}</section>
    <section class="card"><div class="card-head"><h3>Team</h3></div>${d.members.length ? `<div class="gm-team">${d.members.map((a) => `<span class="badge ${a.current_task ? "info" : "neutral"}" title="${esc(a.current_task || a.role)}">${esc(a.name)}</span>`).join(" ")}</div>` : gmEmpty("Agents appear when they work on the project.")}</section>
    <section class="card"><div class="card-head"><h3>Goals</h3><a class="view-all" href="#/goals">All goals →</a></div>${d.goals.length ? `<div class="dash-list">${d.goals.slice(0, 8).map((g) => `<div class="row nowrap">${pill(g.status)}<a class="grow ellipsis" href="#/goals/${g.id}">#${g.id} ${esc(g.title)}</a><span class="muted dash-meta">${ago(g.updated_at)}</span></div>`).join("")}</div>` : gmEmpty("No goal yet: give the General Manager an order.")}</section></div>`;
}

async function ptRequirements(id) {
  const [tr, all] = await Promise.all([api(`/api/projects/${id}/traceability`), api(`/api/projects/${id}/requirements`)]);
  const full = Object.fromEntries(all.map((r) => [r.id, r])), c = tr.counts;
  const kinds = { functional: "Functional", non_functional: "Non-functional", story: "Stories", use_case: "Use cases" };
  return `<div class="row pt-counts"><span class="badge neutral">${c.total} requirements</span><span class="badge info">${c.implemented} implemented</span><span class="badge ok">${c.tested} tested</span><span class="badge ok">${c.merged} merged</span>${c.untested ? `<span class="badge warn">${c.untested} untested</span>` : ""}${c.conflicts ? `<span class="badge bad">${c.conflicts} conflicts</span>` : ""}</div>
    ${tr.requirements.length ? Object.entries(kinds).map(([k, label]) => { const rs = tr.requirements.filter((r) => r.kind === k); return rs.length ? `<section class="card"><div class="card-head"><h3>${label}</h3></div><div class="table-wrap"><table><tr><th>Key</th><th>Requirement</th><th>Status</th><th>v</th><th>Acceptance</th><th>Trace</th></tr>
      ${rs.map((r) => `<tr><td class="mono">${esc(r.key)}</td><td>${esc(r.text)}</td><td>${pill(r.status)}</td><td class="num">${r.version}</td><td class="dash-meta">${(full[r.id]?.acceptance || []).map((a) => esc(typeof a === "string" ? a : a.text || JSON.stringify(a))).join("<br>") || "–"}</td>
        <td class="dash-meta">${r.implemented ? '<span class="badge info">impl.</span> ' : ""}${r.tested ? '<span class="badge ok">tested</span> ' : ""}${r.merged.length ? '<span class="badge ok">merged</span> ' : ""}${r.failing.length ? `<span class="badge bad" title="${esc(r.failing.join(", "))}">failing</span> ` : ""}${r.goals.map((g) => `<a href="#/goals/${g}">#${g}</a>`).join(" ")}</td></tr>`).join("")}</table></div></section>` : ""; }).join("")
    : `<div class="card empty"><p class="empty-title">No requirements yet</p><p class="muted">The brief given at registration, imported documents (docs/requirements/) and the GM's analysis become requirements here.</p></div>`}`;
}

async function ptTasks(id) {
  const tasks = await api(`/api/projects/${id}/tasks`);
  const head = `<div class="row nowrap"><span class="muted grow">${tasks.length} task(s) in the current iteration of the project's goals</span><div class="seg" role="group"><button class="btn sm${boardView === "board" ? " on" : ""}" data-bv="board">Board</button><button class="btn sm${boardView === "table" ? " on" : ""}" data-bv="table">Table</button></div></div>`;
  if (!tasks.length) return head + `<div class="card empty"><p class="empty-title">No tasks</p><p class="muted">Tasks appear when the General Manager plans a goal.</p></div>`;
  if (boardView === "table") return head + `<div class="card"><div class="table-wrap"><table><tr><th>Task</th><th>Goal</th><th>Role</th><th>Agent</th><th>Status</th><th>Model</th><th>After</th><th>Started</th><th>Finished</th></tr>
    ${tasks.map((t) => `<tr><td>${esc(t.title)} <span class="muted mono">${esc(t.key)}</span></td><td><a href="#/goals/${t.goal_id}">#${t.goal_id}</a></td><td>${esc(t.role)}</td><td>${esc(t.agent || "–")}</td><td><span class="badge ${BOARD_COLS.find((c) => c[0] === t.column)[2]}">${t.column}</span></td><td>${esc(t.model || "–")}</td><td class="mono">${esc(t.depends_on.join(", ") || "–")}</td><td>${t.started_at ? ago(t.started_at) : "–"}</td><td>${t.finished_at ? ago(t.finished_at) : "–"}</td></tr>`).join("")}</table></div></div>`;
  const cols = BOARD_COLS.filter(([k]) => k === "running" || tasks.some((t) => t.column === k));  // empty columns stay out
  return head + `<div class="board" style="grid-template-columns:repeat(${cols.length}, minmax(180px, 1fr))">${cols.map(([k, label, tone]) => { const ts = tasks.filter((t) => t.column === k); return `<section class="board-col"><div class="row nowrap board-head"><span class="badge ${tone}">${label}</span><span class="muted">${ts.length}</span></div>
    ${ts.map((t) => `<a class="board-card" href="#/goals/${t.goal_id}"><strong class="ellipsis-2">${esc(t.title)}</strong><div class="muted dash-meta">${esc(t.role)}${t.agent ? " · " + esc(t.agent) : ""}</div><div class="muted dash-meta ellipsis">#${t.goal_id} ${esc(t.goal)}</div></a>`).join("")}</section>`; }).join("")}</div>`;
}

async function ptEvaluation(id, d) {
  const defects = (await api("/api/defects").catch(() => [])).filter((x) => d.goals.some((g) => g.id === x.goal_id));
  const judged = d.goals.filter((g) => (g.history || []).length);
  return `<div class="grid pt-grid"><section class="card" style="grid-column:1/-1"><div class="card-head"><h3>Latest evaluation per goal</h3></div>${judged.length ? `<div class="table-wrap"><table><tr><th>Goal</th><th>Iteration</th><th>Gate</th><th>Stages</th><th>Failed checks</th><th>Status</th></tr>
    ${judged.map((g) => { const h = g.history[g.history.length - 1]; return `<tr><td><a href="#/goals/${g.id}">#${g.id} ${esc(g.title)}</a></td><td class="num">${h.iteration}</td><td><span class="badge ${h.passed ? "ok" : "bad"}">${h.passed ? "passed" : "failed"}</span></td>
      <td>${Object.entries(h.stages || {}).map(([s, st]) => `<span class="badge ${st === "passed" ? "ok" : st === "failed" ? "bad" : "neutral"}" title="${esc(st)}">${esc(s)}</span>`).join(" ") || "–"}</td><td class="dash-meta">${esc((h.failed_checks || []).join(", ") || "–")}</td><td>${pill(g.status)}${g.status === "awaiting_approval" && me.is_admin ? ` <button class="btn sm" data-back="${g.id}" title="Send the work back with what has to change: the next iteration plans from it">Return to development</button> <button class="btn sm" data-return="${g.id}" title="Reject the work: the goal ends as rejected and nothing is merged">Reject</button>` : ""}</td></tr>`; }).join("")}</table></div>` : gmEmpty("No goal has been through the gate yet.")}</section>
    <section class="card" style="grid-column:1/-1"><div class="card-head"><h3>Defects</h3></div>${defects.length ? `<div class="table-wrap"><table><tr><th>Check</th><th>Goal</th><th>Priority</th><th>Severity</th><th>Status</th></tr>${defects.map((x) => `<tr><td>${esc(x.check)}</td><td><a href="#/goals/${x.goal_id}">#${x.goal_id}</a></td><td>${esc(x.priority || "–")}</td><td>${esc(x.severity || "–")}</td><td>${pill(x.status)}</td></tr>`).join("")}</table></div>` : gmEmpty("No defect recorded.")}</section></div>`;
}

async function ptReports(id, d) {
  const done = d.goals.filter((g) => ["approved", "failed", "rejected", "cancelled"].includes(g.status));
  const decs = await api(`/api/decisions?project_id=${id}`).catch(() => []);
  return `<div class="grid pt-grid"><section class="card"><div class="card-head"><h3>Mission reports</h3></div>${done.length ? `<div class="dash-list">${done.map((g) => `<div class="row nowrap">${pill(g.status)}<a class="grow ellipsis" href="#/goals/${g.id}">#${g.id} ${esc(g.title)}</a><a class="view-all" href="/api/goals/${g.id}/report?format=md" target="_blank" rel="noopener">report</a></div>`).join("")}</div>` : gmEmpty("No finished goal yet.")}</section>
    <section class="card"><div class="card-head"><h3>Decisions and ADRs</h3></div>${decs.length ? `<div class="dash-list">${decs.slice(0, 30).map((x) => `<div><div class="row nowrap">${pill(x.status)}<strong class="grow ellipsis">${esc(x.topic)}</strong>${x.goal_id ? `<a href="#/goals/${x.goal_id}" class="muted">#${x.goal_id}</a>` : ""}</div><div class="muted dash-meta ellipsis">${esc(x.recommendation || "")}${x.adr_path ? ` · <span class="mono">${esc(x.adr_path)}</span>` : ""}</div></div>`).join("")}</div>` : gmEmpty("No decision recorded.")}</section></div>`;
}

const PROJECT_TABS = { overview: ptOverview, requirements: ptRequirements, tasks: ptTasks, evaluation: ptEvaluation, reports: ptReports };
async function projectTab(mode, id, d) {
  const box = $("#ptab");
  if (!box) return;
  const html = await PROJECT_TABS[mode](id, d);
  if (box.dataset.html === html || !$("#ptab")) return;
  box.innerHTML = html; box.dataset.html = html; iconize(box);
  box.querySelectorAll("[data-ptab]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); projectMode = a.dataset.ptab; store.set("ac.project.view", projectMode); projectMain(id); }));
  box.querySelectorAll("[data-bv]").forEach((b) => (b.onclick = () => { boardView = b.dataset.bv; store.set("ac.board.view", boardView); box.dataset.html = ""; projectTab(mode, id, d); }));
  box.querySelectorAll("[data-back]").forEach((b) => (b.onclick = async () => {
    const reason = prompt(`Return goal #${b.dataset.back} to development. What has to change?`);
    if (!reason || !reason.trim()) return;
    try { await api(`/api/goals/${b.dataset.back}/return`, { method: "POST", body: { reason } }); toast("Returned to development"); box.dataset.html = ""; projectMain(id, true); } catch (e) { toast(e.message); }
  }));
  box.querySelectorAll("[data-return]").forEach((b) => (b.onclick = async () => {
    if (!confirm(`Reject goal #${b.dataset.return}? Nothing is merged; give the General Manager a new order to redo it.`)) return;
    try { await api(`/api/goals/${b.dataset.return}/reject`, { method: "POST" }); toast("Rejected"); box.dataset.html = ""; projectMain(id, true); } catch (e) { toast(e.message); }
  }));
}
