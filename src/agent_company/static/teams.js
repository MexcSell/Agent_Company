// Teams & Agents (AICO UI-TEAM-01, UI-AG-01): the Teams tab — permanent departments are rooms (organization.py),
// project teams are the live goals' teams (UI-D-07) — and the Agents tab, a filterable grid of agent cards. Both
// open the agent card (agentModal) on a click. Figures: /api/performance (per room and agent), /api/dashboard.
let agentFilter = { room: "", status: "", model: "", skill: "", q: "" };
const AG_STATE = (a) => a.lifecycle === "retired" ? "retired" : a.lifecycle === "paused" ? "paused" : !a.enabled ? "disabled"
  : a.health === "degraded" ? "failed" : a.status === "busy" ? "working" : a.status === "blocked" ? "blocked" : "idle";
const AG_TONE = { working: "info", idle: "ok", blocked: "warn", failed: "bad", paused: "warn", retired: "neutral", disabled: "neutral" };

async function viewTeams(box) {
  const [rooms, agents, perf, dash, models] = await Promise.all([api("/api/rooms"), api("/api/agents"), api("/api/performance").catch(() => ({ rooms: [], agents: [] })),
    api("/api/dashboard?period=7d").catch(() => null), api("/api/models").catch(() => ({ routes: {} }))]);
  $("#ag-tools").innerHTML = `<span class="muted">${rooms.length} departments · ${dash?.live_goals.length || 0} project team(s)</span>${me.is_admin ? ib("sitemap", "Rooms: hierarchy, leads, switches, position history and export", 'id="a-rooms"') : ""}`;
  $("#a-rooms") && ($("#a-rooms").onclick = () => roomsModal());
  const byId = new Map(agents.map((a) => [a.id, a])), rp = Object.fromEntries((perf.rooms || []).map((r) => [r.room, r]));
  const live = (dash?.live_goals || []).filter((g) => g.tasks.some((t) => t.agent_id));
  const roomCard = (r) => {
    const members = agents.filter((a) => a.room === r.key && a.lifecycle !== "retired"), lead = byId.get(r.lead_agent_id), p = rp[r.key];
    const working = members.filter((a) => a.status === "busy");
    return `<section class="card team-card${r.enabled ? "" : " off"}"><div class="row nowrap"><span class="kpi-icon" style="margin:0">${icon("sitemap", 16)}</span><strong class="grow ellipsis">${esc(r.name)}</strong><span class="badge ${r.enabled ? "ok" : "neutral"}">${r.enabled ? "enabled" : "disabled"}</span></div>
      <p class="muted pf-desc">${esc(r.description || r.division || "–")}</p>
      <div class="gm-kv"><span class="muted">Lead</span><span>${lead ? `<a href="#" data-aid="${lead.id}">${esc(lead.name)}</a>` : "–"}</span><span class="muted">Members</span><span>${members.length} · ${working.length} working</span>
      <span class="muted">Performance</span><span>${p ? `${p.tasks} task(s) · ${p.completion_rate == null ? "not measured" : Math.round(p.completion_rate * 100) + "% completed"} · ${money(p.cost)}` : "not measured"}</span>
      <span class="muted">Budget</span><span>${r.daily_budget_usd ? `${money(r.spent_today_usd || 0)} of ${money(r.daily_budget_usd)} today` : "no daily budget"}</span>${r.parent ? `<span class="muted">Part of</span><span>${esc(r.parent)}</span>` : ""}</div>
      <div class="gm-team">${members.slice(0, 12).map((a) => `<button class="badge ${AG_TONE[AG_STATE(a)]}" data-aid="${a.id}" title="${esc(a.role)}">${esc(a.name)}</button>`).join("")}${members.length > 12 ? `<span class="muted dash-meta">+${members.length - 12}</span>` : ""}</div></section>`;
  };
  const projectTeam = (g) => {
    const ids = [...new Set(g.tasks.map((t) => t.agent_id).filter(Boolean))];
    return `<section class="card team-card"><div class="row nowrap"><span class="kpi-icon" style="margin:0">${icon("goals", 16)}</span><a class="grow ellipsis" href="#/goals/${g.id}"><strong>#${g.id} ${esc(g.title)}</strong></a>${pill(g.status)}</div>
      <div class="muted dash-meta">project-specific · iteration ${g.iteration} · ${g.tasks.length} task(s)</div>
      <div class="gm-team">${ids.map((id) => { const a = byId.get(id); return a ? `<button class="badge ${AG_TONE[AG_STATE(a)]}" data-aid="${a.id}">${esc(a.name)} · ${esc(a.role.replace(/_/g, " "))}</button>` : ""; }).join("")}</div></section>`;
  };
  const html = `<div class="team-scroll"><h3 class="team-h">Departments <span class="muted">(rooms: permanent teams)</span></h3><div class="grid pf-cards">${rooms.map(roomCard).join("")}</div>
    <h3 class="team-h">Project teams <span class="muted">(the agents on each live goal)</span></h3>${live.length ? `<div class="grid pf-cards">${live.map(projectTeam).join("")}</div>` : `<div class="card empty"><p class="empty-title">No project team right now</p><p class="muted">A team forms when the General Manager staffs a goal.</p></div>`}</div>`;
  if (box.dataset.html !== html) { box.innerHTML = html; box.dataset.html = html; }
  box.querySelectorAll("[data-aid]").forEach((el) => (el.onclick = (e) => { e.preventDefault(); agentModal(Number(el.dataset.aid)); }));
}

async function viewAgentGrid(box, quiet) {
  const [agents, perf, models] = await Promise.all([api("/api/agents"), api("/api/performance").catch(() => ({ agents: [] })), api("/api/models").catch(() => ({ routes: {}, default_model: "" }))]);
  const pm = Object.fromEntries((perf.agents || []).map((m) => [m.agent_id, m]));
  const modelOf = (a) => (a.model_pref?.startsWith("agent:") ? "via another agent" : a.model_pref) || models.routes?.[a.role] || models.default_model || "default";
  const live = agents.filter((a) => a.lifecycle !== "retired" || agentFilter.status === "retired");
  const rooms = [...new Set(agents.map((a) => a.room))].sort(), mods = [...new Set(agents.map(modelOf))].sort(), skills = [...new Set(agents.flatMap((a) => Object.keys(a.skills || {})))].sort();
  $("#ag-tools").innerHTML = `<span class="muted">${live.length} agents · ${agents.filter((a) => a.status === "busy").length} working</span>${me.is_admin ? ib("user_add", "Add an agent", 'id="a-new"', "primary") : ""}`;
  $("#a-new") && ($("#a-new").onclick = async () => newAgentForm(await api("/api/rooms"), agents));
  if (!quiet || !$("#agf")) {
    const sel = (k, label, values) => `<select data-f="${k}" aria-label="${label}"><option value="">${label}: all</option>${values.map((v) => `<option value="${esc(v)}"${agentFilter[k] === v ? " selected" : ""}>${esc(v)}</option>`).join("")}</select>`;
    box.innerHTML = `<div class="team-scroll"><div class="row pf-tools" id="agf"><input type="search" data-f="q" placeholder="Search name, role, title" aria-label="Search agents" value="${esc(agentFilter.q)}">${sel("room", "Room", rooms)}${sel("status", "Status", ["working", "idle", "blocked", "failed", "paused", "disabled", "retired"])}${sel("model", "Model", mods)}${sel("skill", "Skill", skills)}</div><div id="ag-grid"></div></div>`;
    box.querySelectorAll("[data-f]").forEach((el) => (el[el.tagName === "INPUT" ? "oninput" : "onchange"] = () => { agentFilter[el.dataset.f] = el.value.trim(); viewAgentGrid(box, true); }));
  }
  const f = agentFilter, q = f.q.toLowerCase();
  const shown = agents.filter((a) => (f.status === "retired" || a.lifecycle !== "retired") && (!f.room || a.room === f.room) && (!f.status || AG_STATE(a) === f.status)
    && (!f.model || modelOf(a) === f.model) && (!f.skill || (a.skills || {})[f.skill] != null) && (!q || [a.name, a.role, a.title].some((x) => String(x || "").toLowerCase().includes(q))));
  const html = shown.length ? `<div class="grid ag-cards">${shown.map((a) => { const st = AG_STATE(a), m = pm[a.id];
    return `<button class="card ag-card" data-aid="${a.id}"><div class="row nowrap"><span class="avatar">${roleIcon(a.role)}</span><div class="grow" style="min-width:0;text-align:left"><strong class="ellipsis" style="display:block">${esc(a.name)}</strong><span class="muted dash-meta ellipsis" style="display:block">${esc(a.title || a.role)}</span></div><span class="badge ${AG_TONE[st]}">${st}</span></div>
      <div class="gm-kv"><span class="muted">Room</span><span class="ellipsis">${esc(a.room)}</span><span class="muted">Model</span><span class="ellipsis">${esc(modelOf(a))}</span><span class="muted">Tasks</span><span>${m ? m.tasks : 0}</span>
      <span class="muted">Success</span><span>${m?.completion_rate == null ? "not measured" : Math.round(m.completion_rate * 100) + "%"}</span><span class="muted">Active</span><span>${ago(a.last_active_at)}</span></div>
      <div class="gm-team">${Object.entries(a.skills || {}).sort((x, y) => y[1] - x[1]).slice(0, 4).map(([k, v]) => `<span class="badge neutral">${esc(k)} ${v}</span>`).join("")}</div></button>`; }).join("")}</div>`
    : `<div class="card empty"><p class="empty-title">No agent matches</p><p class="muted">Change the filters.</p></div>`;
  const grid = $("#ag-grid");
  if (grid.dataset.html !== html) { grid.innerHTML = html; grid.dataset.html = html; }
  grid.querySelectorAll("[data-aid]").forEach((el) => (el.onclick = () => agentModal(Number(el.dataset.aid))));
}

// Agent card tabs Evaluation and Memory (UI-AG-02): measured figures only, the agent's own knowledge items.
async function agentEvaluationTab(box, a) {
  const perf = await api("/api/performance").catch(() => ({ agents: [] })), m = (perf.agents || []).find((x) => x.agent_id === a.id);
  const pct = (v) => (v == null ? "not measured" : Math.round(v * 100) + "%");
  box.innerHTML = m ? `<div class="grid dash-kpis" style="grid-template-columns:repeat(auto-fit,minmax(140px,1fr))">
      ${[["Tasks", m.tasks], ["Completed", pct(m.completion_rate)], ["Goal pass rate", pct(m.goal_pass_rate)], ["Rework", pct(m.rework_rate)], ["Retries", pct(m.retry_rate)], ["Review failures", m.review_failures], ["Reliability", pct(m.reliability)], ["Cost", money(m.cost)], ["Tokens", fmt.format(m.tokens || 0)], ["Avg task", m.avg_task_seconds == null ? "not measured" : dur(m.avg_task_seconds)], ["Agent score", m.score == null ? "not measured" : `${m.score} / 100`]]
        .map(([l, v]) => `<div class="card kpi"><div class="value" style="font-size:${String(v) === "not measured" ? "13px;color:var(--muted)" : "20px"}">${esc(String(v))}</div><div class="label">${l}</div></div>`).join("")}</div>
    <p class="muted dash-meta">From the agent's tasks and runs (performance.py); certification and trials are on the Evolution page.</p>`
    : `<div class="empty"><p class="empty-title">Not measured</p><p class="muted">The agent has no task or run yet.</p></div>`;
  const vm = await api(`/api/agents/${a.id}/version-metrics`).catch(() => null);
  if (vm && vm.versions.length) {
    box.insertAdjacentHTML("beforeend", '<div id="ag-vm"></div>');
    agentVersionTable($("#ag-vm"), vm, false);
  }
}
// UI-EVO-03: per definition version, what the agent did while it was current (time-attributed, sample sizes shown)
function agentVersionTable(el, vm, all) {
  const rate = (r) => (r.rate == null ? '<span class="muted">–</span>' : `${Math.round(r.rate * 100)}% <span class="muted">(${r.hits}/${r.n})</span>`);
  const bar = (r) => (r.rate == null ? "" : `<div class="progress" style="height:4px;margin-top:3px"><span style="width:${Math.round(r.rate * 100)}%"></span></div>`);
  const worked = vm.versions.filter((v) => v.tasks || v.runs), rows = (all ? vm.versions : worked).slice(0, all ? 500 : 30);
  el.innerHTML = `<div class="row nowrap" style="margin-top:16px"><h3 class="grow" style="margin:0">Per version</h3><label class="plug"><input type="checkbox" id="ag-vm-all" ${all ? "checked" : ""}><span>versions without work</span></label></div>
    <p class="muted dash-meta">${worked.length} of ${vm.versions.length} definition versions did work. Attributed by ${esc(vm.basis)}.${vm.unattributed.tasks || vm.unattributed.runs ? ` Before the first recorded version: ${vm.unattributed.tasks} task(s), ${vm.unattributed.runs} run(s).` : ""}</p>
    <div class="table-wrap"><table><tr><th>Version</th><th>From</th><th>Task success</th><th>Failure rate</th><th class="num">Runs</th><th class="num">Cost</th><th class="num">Mean run</th><th>Tool-call failures</th><th>Change</th></tr>
      ${rows.map((v) => `<tr><td class="num"><code title="${esc(v.commit)}">v${v.version ?? "?"}</code></td><td class="nowrap muted">${esc(new Date(v.at).toLocaleString(undefined, inTz({ dateStyle: "short", timeStyle: "short" })))}</td><td>${rate(v.task_success)}${bar(v.task_success)}</td><td>${rate(v.failure_rate)}</td><td class="num">${v.runs}</td><td class="num">${v.runs ? money(v.cost) : "–"}</td><td class="num">${v.mean_run_seconds == null ? "–" : dur(v.mean_run_seconds)}</td><td>${rate(v.tool_call_failure_rate)}</td><td class="muted ellipsis" style="max-width:240px" title="${esc(v.reason)}">${esc(v.reason)}</td></tr>`).join("") || '<tr><td colspan="9" class="muted">No version did work yet.</td></tr>'}</table></div>`;
  $("#ag-vm-all").onchange = (e) => agentVersionTable(el, vm, e.target.checked);
}
async function agentMemoryTab(box, a) {
  if (!me.is_admin) { box.innerHTML = '<p class="muted">Knowledge items are shown to administrators.</p>'; return; }
  const items = await api(`/api/knowledge?agent_id=${a.id}&limit=100`).catch(() => []);
  box.innerHTML = items.length ? `<p class="muted">What the agent learned (lessons, experience reports, research), with their review status. Approved items reach its task packages.</p><div class="dash-list">${items.map((i) => `<div class="gm-card"><div class="row nowrap">${pill(i.status)}<span class="badge neutral">${esc(i.kind || i.outcome)}</span><span class="grow"></span><span class="muted dash-meta">${ago(i.at)}</span></div><div style="margin-top:6px;white-space:pre-wrap">${esc(String(i.text).slice(0, 600))}</div>${i.goal_id ? `<a class="dash-meta" href="#/goals/${i.goal_id}">goal #${i.goal_id}</a>` : ""}</div>`).join("")}</div>`
    : `<div class="empty"><p class="empty-title">No memory yet</p><p class="muted">Lessons appear after the agent's tasks are judged.</p></div>`;
}
