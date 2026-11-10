// Swarm Control (G5-CONTROL-HUB.md UI-G5-SW-01…05): each live mission's swarm as a layered topology
// Mission → Team → Agent → Task from /api/swarm — runtime state only (tasks, runs with their tool calls and isolation
// level, live processes, the approval queue); nothing is simulated. A node opens its detail in the drawer with links to
// the pages that act on it (mission window, agent card, task retry on a paused mission).
let swRecent = store.get("ac.sw.recent") === "1", swData = null;
const SW_W = 210, SW_H = 72, SW_GX = 56, SW_GY = 10;
const SW_TASK_TONE = { done: "ok", failed: "bad", skipped: "neutral", running: "info", queued: "neutral" };
const SW_GOAL_TONE = { running: "info", recovering: "warn", planning: "info", queued: "neutral", paused: "warn", awaiting_approval: "warn", approved: "ok", failed: "bad", rejected: "bad", cancelled: "neutral" };

function swTaskTone(t) { return t.stage === "blocked" ? "warn" : SW_TASK_TONE[t.status] || "neutral"; }
function swAgentTone(a) { return a.health === "degraded" ? "warn" : a.last_error ? "bad" : a.running ? "info" : "ok"; }

// One mission's canvas: four columns; a task row each, an agent centred over its tasks, a team over its agents.
function swGraph(m) {
  if (!m.tasks.length) return `<p class="muted dash-meta">${["queued", "planning"].includes(m.status) ? "The General Manager is planning this mission; its swarm forms once the plan exists." : "No task in this iteration."}</p>`;
  const agentTasks = new Map(), byAgent = new Map(m.agents.map((a) => [a.id, a]));
  m.tasks.forEach((t) => { const k = t.agent_id ?? 0; (agentTasks.get(k) || agentTasks.set(k, []).get(k)).push(t); });
  const teams = [...m.teams];
  if (agentTasks.has(0)) teams.push({ key: "@unassigned", name: "Not staffed yet", agents: [0] });
  const pos = new Map(), col = (i) => i * (SW_W + SW_GX);
  let row = 0;
  teams.forEach((tm) => {
    const top = row;
    tm.agents.forEach((aid) => {
      const ts = agentTasks.get(aid) || [], first = row;
      ts.forEach((t) => pos.set(`t${t.id}`, { x: col(3), y: row++ * (SW_H + SW_GY) }));
      if (!ts.length) row++;
      pos.set(`a${aid}`, { x: col(2), y: ((first + Math.max(row - 1, first)) / 2) * (SW_H + SW_GY) });
    });
    pos.set(`m-team-${tm.key}`, { x: col(1), y: ((top + row - 1) / 2) * (SW_H + SW_GY) });
  });
  pos.set("mission", { x: col(0), y: ((row - 1) / 2) * (SW_H + SW_GY) });
  const width = col(3) + SW_W + 60, height = Math.max(1, row) * (SW_H + SW_GY) - SW_GY;
  const link = (a, b, cls = "") => { const p = pos.get(a), q = pos.get(b); if (!p || !q) return ""; const x1 = p.x + SW_W, y1 = p.y + SW_H / 2, x2 = q.x, y2 = q.y + SW_H / 2, mx = (x1 + x2) / 2;
    return `<path d="M${x1} ${y1} C${mx} ${y1} ${mx} ${y2} ${x2} ${y2}" class="wf-edge ${cls}"/>`; };
  const byKey = new Map(m.tasks.map((t) => [t.key, t]));
  const dep = ([from, to]) => { const a = byKey.get(from), b = byKey.get(to); if (!a || !b) return ""; const p = pos.get(`t${a.id}`), q = pos.get(`t${b.id}`);
    const x = p.x + SW_W, y1 = p.y + SW_H / 2, y2 = q.y + SW_H / 2, bulge = 24 + Math.min(36, Math.abs(y2 - y1) / 6);
    return `<path d="M${x} ${y1} C${x + bulge} ${y1} ${x + bulge} ${y2} ${x} ${y2}" class="wf-edge sw-dep${a.status === "done" ? " ok" : ""}" marker-end="url(#sw-arrow)"/>`; };
  const edges = teams.flatMap((tm) => [link("mission", `m-team-${tm.key}`), ...tm.agents.flatMap((aid) => [link(`m-team-${tm.key}`, `a${aid}`),
    ...(agentTasks.get(aid) || []).map((t) => link(`a${aid}`, `t${t.id}`, t.status === "done" ? "ok" : ""))])]).join("") + m.edges.map(dep).join("");
  const box = (key, tone, inner, attrs) => { const p = pos.get(key); return `<button class="wf-node sw-node ${tone}" style="left:${p.x}px;top:${p.y}px;width:${SW_W}px;height:${SW_H}px" ${attrs}>${inner}</button>`; };
  const nodes = [
    box("mission", SW_GOAL_TONE[m.status] || "neutral", `<span class="row nowrap"><span class="wf-dot"></span><strong class="ellipsis grow">#${m.id} ${esc(m.title)}</strong></span><span class="muted ellipsis">${esc(m.status.replace(/_/g, " "))} · ${esc(m.pattern.label)}</span><span class="muted ellipsis">${m.approvals.length ? `${m.approvals.length} approval(s) waiting · ` : ""}${money(m.spent_usd).replace(/(\.\d\d)\d+$/, "$1")}${m.budget_usd ? ` of ${money(m.budget_usd).replace(/(\.\d\d)\d+$/, "$1")}` : ""}</span>`, `data-sw="mission" data-id="${m.id}"`),
    ...teams.map((tm) => box(`m-team-${tm.key}`, "neutral", `<span class="row nowrap">${icon("agents", 14)}<strong class="ellipsis grow">${esc(tm.name)}</strong></span><span class="muted">${tm.agents.filter(Boolean).length} agent${tm.agents.filter(Boolean).length === 1 ? "" : "s"}</span>`, `data-sw="team" data-id="${esc(tm.key)}" data-goal="${m.id}"`)),
    ...teams.flatMap((tm) => tm.agents.map((aid) => { const a = byAgent.get(aid);
      if (!a) return box(`a${aid}`, "neutral", `<strong>Waiting for staffing</strong><span class="muted">the loop staffs these tasks</span>`, `data-sw="none"`);
      return box(`a${aid}`, swAgentTone(a), `<span class="row nowrap"><span class="wf-dot${a.running ? " sw-live" : ""}"></span><strong class="ellipsis grow">${esc(a.name)}</strong>${a.isolation ? `<span class="badge neutral sw-iso" title="isolation level of its latest run">${esc(a.isolation)}</span>` : ""}</span><span class="muted ellipsis">${esc(a.model || a.role)}</span><span class="muted ellipsis">${a.tool_calls} tool call${a.tool_calls === 1 ? "" : "s"}${a.tool_failures ? ` · <span class="sw-fail">${a.tool_failures} failed</span>` : ""}${a.running ? " · running now" : ""}</span>`, `data-sw="agent" data-id="${aid}" data-goal="${m.id}"`); })),
    ...m.tasks.map((t) => box(`t${t.id}`, swTaskTone(t), `<span class="row nowrap"><span class="wf-dot"></span><strong class="ellipsis grow">${esc(t.title)}</strong></span><span class="muted ellipsis">${esc(t.key)} · ${esc(t.stage || t.status)}</span><span class="muted ellipsis">${t.retries ? `retry ${t.retries} · ` : ""}${t.critic.length ? `critic ${esc(t.critic.at(-1))} · ` : ""}${t.writes ? "writes" : "reads"}</span>`, `data-sw="task" data-id="${t.id}" data-goal="${m.id}"`)),
  ].join("");
  return `<div class="wf-canvas"><div class="wf-inner" style="width:${width}px;height:${height}px"><svg class="wf-svg" width="${width}" height="${height}"><defs><marker id="sw-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0L8 4L0 8z" class="sw-arrow"/></marker></defs>${edges}</svg>${nodes}</div></div>`;
}

function swMission(m) {
  const tone = SW_GOAL_TONE[m.status] || "neutral";
  return `<section class="card sw-mission" data-mission="${m.id}"><div class="card-head"><h3 class="ellipsis"><a href="#/goals/${m.id}">#${m.id} ${esc(m.title)}</a></h3>
      <div class="row nowrap"><span class="badge ${tone}">${esc(m.status.replace(/_/g, " "))}${m.paused_for ? ` · ${esc(m.paused_for)}` : ""}</span><span class="badge info" title="${esc(m.pattern.why)}">${esc(m.pattern.label)}</span></div></div>
    <div class="muted dash-meta sw-meta">iteration ${m.iteration}/${m.max_iterations} · ${m.teams.length} team${m.teams.length === 1 ? "" : "s"} · ${m.agents.length} agent${m.agents.length === 1 ? "" : "s"} · ${m.tasks.length} task${m.tasks.length === 1 ? "" : "s"}${m.failed ? ` · <span class="sw-fail">${m.failed} failed</span>` : ""}${m.recovered ? ` · ${m.recovered} recovered` : ""} · ${fmt.format(m.tokens)} tokens · ${money(m.spent_usd).replace(/(\.\d\d)\d+$/, "$1")}${m.budget_usd ? ` of ${money(m.budget_usd).replace(/(\.\d\d)\d+$/, "$1")} budget` : ""}${m.max_active_agents ? ` · at most ${m.max_active_agents} at once` : ""}</div>
    ${m.approvals.length ? `<div class="sw-approvals">${m.approvals.map((x) => `<a class="badge warn" href="${esc(x.link)}">${icon("check", 12)} ${esc(x.title)}</a>`).join("")}</div>` : ""}
    ${swGraph(m)}</section>`;
}

async function viewSwarm(quiet) {
  const d = await api(`/api/swarm${swRecent ? "?recent=5" : ""}`);
  swData = d;
  const view = $("#view");
  if (!quiet || !$("#sw")) {
    view.innerHTML = `<div id="sw" class="dash"><div class="page-head"><div class="page-head-text"><h2 class="page-title">Swarm Control</h2><div class="muted">Mission → Team → Agent → Task, from the runtime records. Select a node for its state, history and controls.</div></div>
      <div class="row nowrap"><label class="row nowrap sw-toggle"><input type="checkbox" id="sw-recent"${swRecent ? " checked" : ""} style="width:auto"> recent finished missions</label></div></div>
      <div class="grid dash-kpis" id="sw-kpis"></div><div id="sw-legend" class="muted dash-meta sw-legend"></div><div id="sw-missions"></div></div>`;
    $("#sw-recent").onchange = (e) => { swRecent = e.target.checked; store.set("ac.sw.recent", swRecent ? "1" : "0"); viewSwarm(true); };
    $("#sw").addEventListener("click", (e) => { const b = e.target.closest("[data-sw]"); if (b && b.dataset.sw !== "none") swOpen(b.dataset.sw, b.dataset.id, Number(b.dataset.goal)); });
  }
  const s = d.summary;
  dashFill("sw-kpis", [
    dashKpi("goals", s.missions, "Live missions", "#/goals"),
    dashKpi("agents", s.agents, "Agents in swarms", "#/agents", `<div class="kpi-trend flat">${s.idle_agents} idle</div>`),
    dashKpi("bolt", s.running_now, "Running now", "#/observability", `<div class="kpi-trend flat">executor processes</div>`),
    dashKpi("network", s.tool_calls, "Tool calls", "#/observability", `<div class="kpi-trend ${s.tool_failures ? "down" : "flat"}">${s.tool_failures} failed</div>`),
    dashKpi("check", s.approvals, "Waiting for approval", "#/governance"),
    dashKpi("shield", s.failed_tasks, "Failed tasks", "#/workflows"),
  ].join(""));
  dashFill("sw-legend", `<span class="badge info">running</span> <span class="badge ok">done / healthy</span> <span class="badge warn">blocked / degraded / waiting</span> <span class="badge bad">failed</span> · solid lines: who works on what · arrows beside the tasks: dependencies · L0–L3: the agent's isolation level`);
  dashFill("sw-missions", d.missions.length ? d.missions.map(swMission).join("")
    : `<div class="card empty"><p class="empty-title">No mission is running</p><p class="muted">Start a mission, or ask the General Manager; its swarm appears here as soon as it is planned.</p><div class="row" style="justify-content:center"><a class="btn keep-text" href="#/goals">New mission</a></div></div>`);
}

function swOpen(kind, id, goalId) {
  const m = swData?.missions.find((x) => x.id === (kind === "mission" ? Number(id) : goalId));
  if (!m) return;
  const kv = (rows) => `<div class="gm-kv">${rows.filter(Boolean).map(([k, v]) => `<span class="muted">${esc(k)}</span><span>${v}</span>`).join("")}</div>`;
  if (kind === "mission") {
    openDrawer(`Mission #${m.id}`, `<p><strong>${esc(m.title)}</strong></p>${kv([["Status", esc(m.status.replace(/_/g, " ")) + (m.paused_for ? ` (${esc(m.paused_for)})` : "")], ["Pattern", `${esc(m.pattern.label)} — ${esc(m.pattern.why)}`], ["Execution", esc(m.execution)], ["Iteration", `${m.iteration} of ${m.max_iterations}`], ["Spent", `${money(m.spent_usd)}${m.budget_usd ? ` of ${money(m.budget_usd)}` : " (no budget set)"}`], ["Tokens", `${fmt.format(m.tokens)}${m.budget_tokens ? ` of ${fmt.format(m.budget_tokens)}` : ""}`], m.deadline && ["Deadline", esc(new Date(m.deadline).toLocaleString())]])}
      <h3>Waiting for a person</h3>${m.approvals.length ? `<ul class="timeline">${m.approvals.map((x) => `<li class="warn"><a href="${esc(x.link)}">${esc(x.title)}</a><time>${esc(x.kind)} · ${ago(x.at)}</time></li>`).join("")}</ul>` : '<p class="muted dash-meta">Nothing.</p>'}
      <h3>Policy and recovery events</h3>${m.events.length ? `<ul class="timeline">${m.events.map((e) => `<li class="${["error", "security"].includes(e.kind) ? "bad" : "warn"}">${esc(e.message)}<time>${esc(e.kind)} · ${ago(e.at)}</time></li>`).join("")}</ul>` : '<p class="muted dash-meta">None recorded.</p>'}
      <div class="row" style="margin-top:12px"><button class="btn keep-text primary" id="sw-open-goal">Open the mission</button><a class="btn keep-text" href="#/workflows">Workflow graph</a></div>`);
    $("#sw-open-goal").onclick = () => { closeDrawer(); goalModal(m.id); };
    return;
  }
  if (kind === "team") {
    const tm = m.teams.find((x) => x.key === id);
    return openDrawer(tm ? tm.name : "Team", `${kv([["Room", `<span class="mono">${esc(id)}</span>`], ["Mission", `#${m.id} ${esc(m.title)}`]])}<h3>Agents</h3><ul class="timeline">${(tm?.agents || []).map((aid) => m.agents.find((a) => a.id === aid)).filter(Boolean).map((a) => `<li class="${swAgentTone(a)}"><a href="#/agents/${a.id}">${esc(a.name)}</a> · ${esc(a.role)}<time>${a.tool_calls} tool calls · ${money(a.cost)}</time></li>`).join("")}</ul>`);
  }
  if (kind === "agent") {
    const a = m.agents.find((x) => x.id === Number(id));
    if (!a) return;
    return openDrawer(a.name, `${kv([["Role", esc(a.role)], ["Team", esc(a.team)], ["Model", esc(a.model || "–")], ["Isolation", esc(a.isolation || "–")], ["Autonomy", esc(a.autonomy || "–")], ["Health", esc(a.health || "–")], ["Now", a.running ? "running an executor process" : esc(a.status || "–")], ["In this mission", `${a.runs} run(s) · ${fmt.format(a.tokens)} tokens · ${money(a.cost)}`]])}
      ${a.last_error ? `<h3>Last error</h3><pre>${esc(a.last_error)}</pre>` : ""}
      <h3>Latest tool calls</h3>${a.recent_tools.length ? `<ul class="timeline">${a.recent_tools.slice().reverse().map((c) => `<li class="${c.ok ? "ok" : "bad"}"><span class="mono">${esc(c.tool)}</span> ${esc(c.input)}<time>${c.ok ? "ok" : "failed"}</time></li>`).join("")}</ul>` : '<p class="muted dash-meta">No tool call recorded.</p>'}
      ${a.denied_tools.length ? `<h3>Denied tools</h3><p class="mono dash-meta">${esc(a.denied_tools.join(", "))}</p>` : ""}
      <div class="row" style="margin-top:12px"><a class="btn keep-text primary" href="#/agents/${a.id}">Open the agent card</a></div>`);
  }
  if (kind === "task") {
    const t = m.tasks.find((x) => x.id === Number(id));
    if (!t) return;
    openDrawer(t.title, `${kv([["Task", `<span class="mono">${esc(t.key)}</span> · ${t.writes ? "writes" : "reads"}`], ["Role", esc(t.role)], ["Agent", t.agent_id ? esc(m.agents.find((a) => a.id === t.agent_id)?.name || `#${t.agent_id}`) : "not staffed yet"], ["Status", `${esc(t.status)} · ${esc(t.stage)}`], ["After", t.depends_on.length ? esc(t.depends_on.join(", ")) : "nothing"], ["Retries", String(t.retries)], ["Critic", t.critic.length ? esc(t.critic.join(", ")) : "–"], ["Model", esc(t.model || "–")], t.started_at && ["Started", ago(t.started_at)], t.finished_at && ["Finished", ago(t.finished_at)]])}
      <h3>Latest output</h3>${t.output ? `<pre>${esc(t.output)}</pre>` : '<p class="muted dash-meta">No output yet.</p>'}
      ${["failed", "skipped"].includes(t.status) && me.is_admin ? (m.status === "paused" ? `<div class="row" style="margin-top:12px"><button class="btn keep-text primary" id="sw-retry">Retry this task</button></div>` : '<p class="muted dash-meta">Pause the mission to retry this task.</p>') : ""}
      <div class="row" style="margin-top:12px"><a class="btn keep-text" href="#/workflows">Open in Workflows</a></div>`);
    $("#sw-retry") && ($("#sw-retry").onclick = async () => {
      try { const r = await api(`/api/tasks/${t.id}/retry`, { method: "POST", body: { agent_id: null } }); toast(r.resumed ? "Task queued again; the mission resumed." : `Task queued again; the mission stays paused: ${r.reason}`); closeDrawer(); viewSwarm(true); }
      catch (e) { toast(e.message); }
    });
  }
}
registerView({ id: "swarm", title: "Swarm Control", icon: "network" }, viewSwarm);
