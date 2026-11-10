// G5 Control Hub overviews (G5-CONTROL-HUB.md): Evolution Lab (UI-G5-EV), Governance (UI-G5-GV) and Observability
// (UI-G5-OB), from /api/evolution-lab, /api/governance and /api/observability. Read-only: each item links to the page
// that acts on it (Agent/Organization Evolution, the approval's own page, Settings, Security, System).
const HUB_LEVEL = { automatic: ["Automatic", "ok", "L1"], policy: ["Policy-controlled", "warn", "L2"], human: ["Human approval", "bad", "L3"] };
const HUB_VERDICT = { better: ["Better", "ok"], worse: ["Worse", "bad"], same: ["No change", "neutral"], insufficient: ["Not enough data", "neutral"] };
const HUB_CAP = { available: ["Available", "ok"], weak: ["Weak", "warn"], missing: ["Missing", "bad"] };
const HUB_ACTION = { reuse: "Reuse", improve: "Improve", develop: "Develop", acquire: "Acquire" };
const HUB_METRIC = { mission_success_rate: "Mission success rate", task_completion_rate: "Task completion rate", cost_per_successful_mission: "Cost per successful mission" };
let labPeriod = store.get("ac.lab.period") || "30d", obsPeriod = store.get("ac.obs.period") || "24h";

const hubEmpty = (title, text) => `<div class="empty"><p class="empty-title">${esc(title)}</p><p class="muted">${esc(text)}</p></div>`;
const hubPct = (v) => (v == null ? "–" : `${Math.round(v * 100)}%`);
function hubList(items, row, empty) { return items.length ? `<ul class="timeline">${items.map(row).join("")}</ul>` : hubEmpty(...empty); }
function hubSelect(id, cur, opts) { return `<select id="${id}" aria-label="Period">${Object.entries(opts).map(([k, l]) => `<option value="${k}"${k === cur ? " selected" : ""}>${l}</option>`).join("")}</select>`; }

// --- Evolution Lab --------------------------------------------------------------------------------------------------
async function viewEvolutionLab(quiet) {
  const d = await api(`/api/evolution-lab?period=${labPeriod}`);
  if (!quiet || !$("#lab")) {
    $("#view").innerHTML = `<div id="lab" class="dash"><div class="page-head"><div class="page-head-text"><h2 class="page-title">Evolution Lab</h2><div class="muted">What the company learned and improved, measured — and what waits for your decision.</div></div>
      <div class="row nowrap"><a class="btn keep-text" href="#/evolution">Agent Evolution</a><a class="btn keep-text" href="#/organization">Organization Evolution</a>${hubSelect("lab-period", labPeriod, { "7d": "Last 7 days", "30d": "Last 30 days", "90d": "Last 90 days" })}</div></div>
      <section class="card"><div class="card-head"><h3>Evolution lifecycle</h3><span class="muted dash-meta">items per step in the period</span></div><div id="lab-life"></div></section>
      <div class="grid dash-kpis" id="lab-kpis" style="margin-top:var(--space-3)"></div>
      <div class="grid dash-row two-wide"><section class="card"><div class="card-head"><h3>Is the ecosystem performing better?</h3><span id="lab-verdict"></span></div><div id="lab-better"></div></section>
        <section class="card"><div class="card-head"><h3>Awaiting your approval <span id="lab-wait-n"></span></h3><a class="view-all" href="#/governance">Governance →</a></div><div id="lab-wait"></div></section></div>
      <div class="grid dash-row two-wide"><section class="card"><div class="card-head"><h3>Agents improving</h3><a class="view-all" href="#/evolution">Agent Evolution →</a></div><div id="lab-agents"></div></section>
        <section class="card"><div class="card-head"><h3>Versions deployed</h3><a class="view-all" href="#/changes">Change History →</a></div><div id="lab-deployed"></div></section></div>
      <div class="grid dash-row two-wide"><section class="card"><div class="card-head"><h3>Experiments</h3></div><div id="lab-exps"></div></section>
        <section class="card"><div class="card-head"><h3>New skills and knowledge</h3><a class="view-all" href="#/knowledge">Knowledge →</a></div><div id="lab-learned"></div></section></div>
      <section class="card" style="margin-top:var(--space-3)"><div class="card-head"><h3>Capabilities the active projects need</h3><span class="muted dash-meta">verified proficiency → what to do</span></div><div id="lab-caps"></div></section></div>`;
    $("#lab-period").onchange = (e) => { labPeriod = e.target.value; store.set("ac.lab.period", labPeriod); viewEvolutionLab(true); };
  }
  const max = Math.max(1, ...d.lifecycle.map((x) => x.count));
  dashFill("lab-life", `<ol class="stepper lab-steps">${d.lifecycle.map((x) => `<li class="${x.count ? "done" : ""}"><strong class="lab-step-n">${x.count}</strong>${esc(x.step[0].toUpperCase() + x.step.slice(1))}<span class="lab-step-bar"><span style="width:${(x.count / max) * 100}%"></span></span></li>`).join("")}</ol>`);
  dashFill("lab-kpis", [
    dashKpi("evolution", d.improving.length, "Agents improving", "#/evolution"),
    dashKpi("learning", d.capabilities.filter((c) => c.status === "passed").length, "Capabilities certified", "#/evolution", `<div class="kpi-trend flat">${d.capabilities.filter((c) => c.status === "failed").length} plan(s) failed</div>`),
    dashKpi("bolt", d.skills.length, "New skills", "#/agents"),
    dashKpi("research", d.knowledge.approved, "Knowledge acquired", "#/knowledge", `<div class="kpi-trend flat">${d.knowledge.drafts_waiting} draft(s) to review</div>`),
    dashKpi("performance", `${d.experiments.passed}<span class="muted dash-of"> / ${d.experiments.passed + d.experiments.failed}</span>`, "Experiments passed", "#/evolution", `<div class="kpi-trend ${d.experiments.rolled_back ? "down" : "flat"}">${d.experiments.rolled_back} rolled back</div>`),
    dashKpi("check", d.awaiting.length, "Awaiting approval", "#/governance"),
  ].join(""));
  const [vl, vt] = HUB_VERDICT[d.better.overall];
  dashFill("lab-verdict", `<span class="badge ${vt}">${vl}</span>`);
  dashFill("lab-better", `<table><thead><tr><th>Metric</th><th class="num">Now</th><th class="num">Before</th><th class="num">n</th><th>Verdict</th></tr></thead><tbody>${d.better.metrics.map((m) => { const money_ = m.metric === "cost_per_successful_mission", f = (v) => (v == null ? "–" : money_ ? money(v) : hubPct(v)), [l, t] = HUB_VERDICT[m.verdict];
    return `<tr><td>${esc(HUB_METRIC[m.metric] || m.metric)}</td><td class="num">${f(m.now)}</td><td class="num">${f(m.before)}</td><td class="num">${m.n} / ${m.n_before}</td><td><span class="badge ${t}">${l}</span></td></tr>`; }).join("")}</tbody></table>
    <p class="muted dash-meta">This period against the one before, counted the same way; a metric needs ${3} finished samples on each side to be judged.</p>`);
  dashFill("lab-wait-n", d.awaiting.length ? `<span class="badge warn">${d.awaiting.length}</span>` : "");
  dashFill("lab-wait", hubList(d.awaiting, (x) => `<li class="warn"><a href="${esc(x.link)}">${esc(x.title)}</a><time>${esc(x.kind)} · ${ago(x.at)}</time></li>`, ["Nothing waits", "No candidate, experiment, contract, organisation change or recruit waits for a person."]));
  dashFill("lab-agents", hubList(d.improving, (a) => `<li class="ok"><a href="#/agents/${a.id}">${esc(a.name)}</a>${a.steps.map((s) => `<div class="muted dash-meta">${esc(s)}</div>`).join("")}${a.success_rate ? `<div class="muted dash-meta">task success ${hubPct(a.success_rate.before)} → <strong>${hubPct(a.success_rate.now)}</strong> (n=${a.success_rate.n_before} → ${a.success_rate.n})</div>` : ""}</li>`, ["No measured improvement", "Certifications, rolled-out playbooks, applied definitions and higher task success rates show here."]));
  dashFill("lab-deployed", hubList(d.deployed, (x) => `<li class="info">${esc(x.title)}<time>${esc(x.kind)}${x.at ? " · " + ago(x.at) : ""}</time></li>`, ["Nothing deployed", "in the period."]));
  dashFill("lab-exps", d.experiments.items.length ? `<table><thead><tr><th>#</th><th>Target</th><th>Status</th><th class="num">Change</th><th class="num">Regr.</th></tr></thead><tbody>${d.experiments.items.map((e) => `<tr><td>${e.id}</td><td>${esc(e.target)}${e.agent ? " · " + esc(e.agent) : e.role ? " · " + esc(e.role) : ""}</td><td><span class="badge ${e.better ? "ok" : ["rejected", "failed", "rolled_back"].includes(e.status) ? "bad" : "neutral"}">${esc(e.status.replace(/_/g, " "))}</span></td><td class="num">${e.change == null ? "–" : `${e.change > 0 ? "+" : ""}${Math.round(e.change * 100)} pts`}</td><td class="num">${e.regressions}</td></tr>`).join("")}</tbody></table>`
    : hubEmpty("No experiment finished", "in the period."));
  dashFill("lab-learned", `${d.skills.length ? `<h4>Skills added</h4><ul class="timeline">${d.skills.map((x) => `<li class="${x.enabled ? "ok" : "neutral"}">${esc(x.name)}<time>${esc(x.source || x.by || "")} · ${ago(x.at)}${x.enabled ? "" : " · disabled until chosen"}</time></li>`).join("")}</ul>` : `<p class="muted dash-meta">No skill added in the period.</p>`}
    <h4>Knowledge approved</h4><p class="dash-meta">${d.knowledge.approved ? Object.entries(d.knowledge.by_outcome).map(([k, n]) => `${n} ${esc(k)}`).join(" · ") : "none in the period"}${d.knowledge.drafts_waiting ? ` · <a href="#/knowledge">${d.knowledge.drafts_waiting} draft(s) wait for review</a>` : ""}</p>`);
  dashFill("lab-caps", d.capability_table.length ? `<table><thead><tr><th>Skill</th><th class="num">Available</th><th>Status</th><th>Action</th><th>Needed by</th><th>Agents</th></tr></thead><tbody>${d.capability_table.map((c) => { const [l, t] = HUB_CAP[c.status];
    return `<tr><td>${esc(c.skill)}</td><td class="num">${c.available.toFixed(2)}</td><td><span class="badge ${t}">${l}</span></td><td><strong>${HUB_ACTION[c.action]}</strong></td><td class="dash-meta">${esc(c.projects.join(", "))}</td><td class="dash-meta">${c.agents.map((a) => `<a href="#/agents/${a.id}">${esc(a.name)}</a>`).join(", ") || "–"}</td></tr>`; }).join("")}</tbody></table>
    <p class="muted dash-meta">Available ≥ 1.0 of verified proficiency: reuse; below: improve with a learning plan; none: develop (a learning plan is under way) or acquire (recruit or the marketplace).</p>`
    : hubEmpty("No required skills", "Set the skills a project needs on its page; they appear here with who covers them."));
}

// --- Governance -----------------------------------------------------------------------------------------------------
async function viewGovernance(quiet) {
  const d = await api("/api/governance");
  if (!quiet || !$("#gov")) {
    $("#view").innerHTML = `<div id="gov" class="dash"><div class="page-head"><div class="page-head-text"><h2 class="page-title">Governance</h2><div class="muted">Autonomy with control: who decides what, what waits for you, the budgets and the safety mechanisms in force.</div></div>
      <div class="row nowrap">${me.is_admin ? `<a class="btn keep-text" href="#/settings">Settings</a><a class="btn keep-text" href="#/security">Security</a>` : ""}</div></div>
      <div class="grid dash-kpis" id="gov-kpis"></div>
      <section class="card" style="margin-top:var(--space-3)"><div class="card-head"><h3>Autonomy matrix</h3><span class="muted dash-meta">from the live settings</span></div><div id="gov-matrix"></div></section>
      <div class="grid dash-row two-wide"><section class="card"><div class="card-head"><h3>Approval queue <span id="gov-q-n"></span></h3></div><div id="gov-queue"></div></section>
        <section class="card"><div class="card-head"><h3>Safety mechanisms</h3></div><div id="gov-safety"></div></section></div>
      <div class="grid dash-row two-wide"><section class="card"><div class="card-head"><h3>Agents by autonomy level</h3><a class="view-all" href="#/agents">Agents →</a></div><div id="gov-autonomy"></div></section>
        <section class="card"><div class="card-head"><h3>Budgets</h3></div><div id="gov-budgets"></div></section></div></div>`;
  }
  const lv = (k) => d.matrix.filter((m) => m.level === k).length, ok = d.safety.filter((x) => x.ok).length;
  dashFill("gov-kpis", [
    dashKpi("check", d.approvals.length, "Pending approvals", "#/governance"),
    dashKpi("bolt", lv("automatic"), "Automatic (L1)", "#/governance", `<div class="kpi-trend flat">activities</div>`),
    dashKpi("settings", lv("policy"), "Policy-controlled (L2)", "#/governance", `<div class="kpi-trend flat">activities</div>`),
    dashKpi("users", lv("human"), "Human approval (L3)", "#/governance", `<div class="kpi-trend flat">activities</div>`),
    dashKpi("shield", `${ok}<span class="muted dash-of"> / ${d.safety.length}</span>`, "Safety mechanisms on", "#/security"),
    dashKpi("emergency", d.stop_level || "none", "Emergency stop", "#/security", `<div class="kpi-trend ${d.stop_level ? "down" : "flat"}">${d.stop_level ? "autonomous work stopped" : "E0–E5 from every page"}</div>`),
  ].join(""));
  dashFill("gov-matrix", `<table><thead><tr><th>Activity</th><th>Autonomous?</th><th>Approval</th><th>Control</th></tr></thead><tbody>${d.matrix.map((m) => { const [l, t, code] = HUB_LEVEL[m.level];
    return `<tr><td>${esc(m.activity)}</td><td>${m.autonomous ? "Yes" : "No"}</td><td><span class="badge ${t}">${code} · ${l}</span></td><td class="dash-meta">${esc(m.control)}${m.setting && me.is_admin ? ` · <a href="#/settings" title="Settings: ${esc(m.setting)}"><span class="mono">${esc(m.setting)}</span></a>` : ""}</td></tr>`; }).join("")}</tbody></table>
    <p class="muted dash-meta">No agent can grant itself permissions, change these policies, approve its own release, leave its sandbox or remove audit records.</p>`);
  dashFill("gov-q-n", d.approvals.length ? `<span class="badge warn">${d.approvals.length}</span>` : "");
  dashFill("gov-queue", hubList(d.approvals, (x) => `<li class="warn"><a href="${esc(x.link || "#/governance")}">${esc(x.title)}</a><div class="muted dash-meta">${esc(x.kind)}${x.project ? " · " + esc(x.project) : ""}${x.origin ? " · " + esc(x.origin) : ""}${x.spent_usd != null ? ` · spent ${money(x.spent_usd)}${x.budget_usd ? ` of ${money(x.budget_usd)}` : ""}${x.estimated_usd ? ` · estimated ${money(x.estimated_usd)}` : ""}` : ""}</div><time>waiting ${ago(x.at).replace(/ ago$/, "")}</time></li>`, ["All clear", "Nothing waits for a person."]));
  dashFill("gov-safety", `<ul class="timeline">${d.safety.map((x) => `<li class="${x.ok ? "ok" : "warn"}"><strong>${esc(x.name)}</strong><div class="muted dash-meta">${esc(x.control)}</div><time>${esc(x.state)}</time></li>`).join("")}</ul>`);
  const maxA = Math.max(1, ...Object.values(d.autonomy));
  dashFill("gov-autonomy", `<div class="dash-bars">${Object.entries(d.autonomy).map(([k, n]) => `<div class="dash-bar"><span>${k}</span><div class="dash-bar-track"><span class="ok" style="width:${(n / maxA) * 100}%"></span></div><strong>${n}</strong></div>`).join("")}</div>
    <p class="muted dash-meta">${d.agents} enabled agents · permissions: ${Object.entries(d.permissions).map(([k, n]) => `${n} ${esc(k)}`).join(" · ") || "none"}. G0 reads only; G4 may also run shell commands and MCP tools in its worktree.</p>`);
  const b = d.budgets, bar = (used, cap) => { const pct = cap ? Math.min(100, Math.round((used / cap) * 100)) : 0; return `<div class="progress ${pct >= 100 ? "bad" : pct >= 80 ? "warn" : "ok"}"><span style="width:${pct}%"></span></div>`; };
  dashFill("gov-budgets", `<div class="gm-kv"><span class="muted">Mission default</span><span>${b.goal_budget_usd ? money(b.goal_budget_usd) : "no limit"}${b.goal_token_budget ? ` · ${fmt.format(b.goal_token_budget)} tokens` : ""}${b.goal_agent_limit ? ` · ${b.goal_agent_limit} agents at once` : ""}</span><span class="muted">When exhausted</span><span>${esc(b.on_exhaustion)}</span></div>
    ${b.rooms.length ? `<h4>Daily room budgets</h4><div class="dash-list">${b.rooms.map((r) => `<div><div class="row nowrap"><span class="grow">${esc(r.name)}</span><span class="dash-meta">${money(r.today_usd)} of ${money(r.daily_budget_usd)} today</span></div>${bar(r.today_usd, r.daily_budget_usd)}</div>`).join("")}</div>` : `<p class="muted dash-meta">No daily room budget is set.</p>`}
    ${b.near_limit.length ? `<h4>Missions near their budget</h4><div class="dash-list">${b.near_limit.map((g) => `<div><div class="row nowrap"><a class="grow ellipsis" href="#/goals/${g.id}">#${g.id} ${esc(g.title)}</a><span class="dash-meta">${money(g.spent_usd)} of ${money(g.budget_usd)}</span></div>${bar(g.spent_usd, g.budget_usd)}</div>`).join("")}</div>` : ""}
    ${b.agents.length ? `<h4>Agent quotas today</h4><div class="dash-list">${b.agents.map((a) => `<div><div class="row nowrap"><a class="grow" href="#/agents/${a.id}">${esc(a.name)}</a><span class="dash-meta">${money(a.today_usd)} of ${money(a.quota_usd)}</span></div>${bar(a.today_usd, a.quota_usd)}</div>`).join("")}</div>` : ""}`);
}

// --- Observability --------------------------------------------------------------------------------------------------
async function viewObservability(quiet) {
  const d = await api(`/api/observability?period=${obsPeriod}`);
  if (!quiet || !$("#obs")) {
    $("#view").innerHTML = `<div id="obs" class="dash"><div class="page-head"><div class="page-head-text"><h2 class="page-title">Observability</h2><div class="muted">Sandboxes, tools, models, network and the host — what the swarm runs on, measured.</div></div>
      <div class="row nowrap"><a class="btn keep-text" href="#/system">System</a>${hubSelect("obs-period", obsPeriod, DASH_PERIODS)}</div></div>
      <div class="grid dash-kpis" id="obs-kpis"></div>
      <div class="grid dash-row two-wide"><section class="card"><div class="card-head"><h3>Sandboxes and processes</h3></div><div id="obs-sandbox"></div></section>
        <section class="card"><div class="card-head"><h3>Host resources</h3></div><div id="obs-host"></div></section></div>
      <div class="grid dash-row two-wide"><section class="card"><div class="card-head"><h3>Tool calls</h3></div><div id="obs-tools"></div></section>
        <section class="card"><div class="card-head"><h3>MCP servers</h3><a class="view-all" href="#/agents">Tools &amp; MCP →</a></div><div id="obs-mcp"></div></section></div>
      <div class="grid dash-row two-wide"><section class="card"><div class="card-head"><h3>Models and tokens</h3><a class="view-all" href="#/models">Models →</a></div><div id="obs-models"></div></section>
        <section class="card"><div class="card-head"><h3>Network and external sources</h3><a class="view-all" href="#/knowledge">Knowledge →</a></div><div id="obs-net"></div></section></div></div>`;
    $("#obs-period").onchange = (e) => { obsPeriod = e.target.value; store.set("ac.obs.period", obsPeriod); viewObservability(true); };
  }
  const sb = d.sandbox, t = d.tools, mcpOk = t.mcp_servers.filter((x) => x.enabled && x.status === "ok").length;
  dashFill("obs-kpis", [
    dashKpi("bolt", sb.processes.length, "Executor processes", "#/swarm", `<div class="kpi-trend flat">running now</div>`),
    dashKpi("folder", sb.worktrees.length, "Worktrees in use", "#/goals"),
    dashKpi("network", fmt.format(t.calls), "Tool calls", "#/swarm", `<div class="kpi-trend ${t.failed ? "down" : "flat"}">${t.failure_rate == null ? "none" : hubPct(t.failure_rate) + " failed"}</div>`),
    dashKpi("system", `${mcpOk}<span class="muted dash-of"> / ${t.mcp_servers.length}</span>`, "MCP servers healthy", "#/agents"),
    dashKpi("models", d.models.length, "Models used", "#/models", `<div class="kpi-trend flat">${fmt.format(d.models.reduce((a, m) => a + m.tokens, 0))} tokens</div>`),
    dashKpi("research", d.network.documents, "Documents fetched", "#/knowledge", `<div class="kpi-trend ${d.network.sync_failures ? "down" : "flat"}">${d.network.sync_failures} sync failure(s)</div>`),
  ].join(""));
  dashFill("obs-sandbox", `<div class="gm-kv"><span class="muted">L3 sandbox</span><span><span class="badge ${sb.available ? "ok" : sb.mode === "off" ? "neutral" : "bad"}">${sb.available ? "available" : "unavailable"}</span> mode ${esc(sb.mode)}${sb.reason ? ` · ${esc(sb.reason)}` : ""}</span><span class="muted">Limits</span><span>${sb.limits ? `${sb.memory_mb} MB · ${sb.cpus} CPUs · ${sb.tasks} tasks` : "systemd scope limits unavailable"}</span><span class="muted">Runs by isolation</span><span>${Object.entries(sb.isolation).map(([k, n]) => `${esc(k)}: ${n}`).join(" · ") || "no run in the period"}</span></div>
    ${sb.processes.length ? `<table><thead><tr><th>PID</th><th>Kind</th><th>Agent</th><th>Mission</th><th>Since</th></tr></thead><tbody>${sb.processes.map((p) => `<tr><td class="num">${p.pid}</td><td>${esc(p.kind)}</td><td>${p.agent_id ? `<a href="#/agents/${p.agent_id}">${esc(p.agent)}</a>` : "–"}</td><td>${p.goal_id ? `<a href="#/goals/${p.goal_id}">#${p.goal_id}</a>` : "–"}</td><td>${ago(p.started_at)}</td></tr>`).join("")}</tbody></table>` : `<p class="muted dash-meta">No executor process is running.</p>`}
    ${sb.worktrees.length ? `<h4>Worktrees of live missions</h4><ul class="timeline">${sb.worktrees.map((w) => `<li class="info"><a href="#/goals/${w.goal_id}">#${w.goal_id} ${esc(w.title)}</a><time class="mono">${esc(w.path)}</time></li>`).join("")}</ul>` : ""}`);
  const h = d.host;
  dashFill("obs-host", h ? `<div class="dash-bars cc-host">${ccMeter("CPU load", (h.load || [0])[0], h.cpus || 1, `load ${(h.load || []).join(" · ")} on ${h.cpus} CPUs`)}${ccMeter("Memory", (h.memory_total || 0) - (h.memory_available || 0), h.memory_total || 0, `${ccBytes(h.memory_available)} free of ${ccBytes(h.memory_total)}`)}${ccMeter("Disk", (h.disk_total || 0) - (h.disk_free || 0), h.disk_total || 0, `${ccBytes(h.disk_free)} free of ${ccBytes(h.disk_total)}`)}</div><p class="muted dash-meta">${esc(h.system || "")}</p>` : hubEmpty("No host figures", "Administrators see the host."));
  const maxT = Math.max(1, ...t.top.map((x) => x.calls));
  dashFill("obs-tools", t.top.length ? `<div class="dash-bars">${t.top.map((x) => `<div class="dash-bar"><span class="ellipsis mono">${esc(x.tool)}</span><div class="dash-bar-track" title="${x.calls} calls · ${x.failed} failed"><span class="ok" style="width:${((x.calls - x.failed) / maxT) * 100}%"></span><span class="bad" style="width:${(x.failed / maxT) * 100}%"></span></div><strong>${x.calls}</strong></div>`).join("")}</div><p class="muted dash-meta">${fmt.format(t.calls)} calls through the tool gateway · ${t.failed} failed · ${t.refused} security events in the period</p>` : hubEmpty("No tool call", "in the period."));
  dashFill("obs-mcp", t.mcp_servers.length ? `<table><thead><tr><th>Server</th><th>Status</th><th class="num">Tools</th><th>Tested</th></tr></thead><tbody>${t.mcp_servers.map((m) => `<tr><td>${esc(m.name)} <span class="muted dash-meta">${esc(m.transport)}</span></td><td><span class="badge ${!m.enabled ? "neutral" : m.status === "ok" ? "ok" : m.status === "error" ? "bad" : "neutral"}">${m.enabled ? esc(m.status) : "disabled"}</span>${m.last_error ? `<div class="muted dash-meta ellipsis-2">${esc(m.last_error)}</div>` : ""}</td><td class="num">${m.tools}</td><td>${m.tested_at ? ago(m.tested_at) : "never"}</td></tr>`).join("")}</tbody></table><p class="muted dash-meta">${t.mcp_calls} MCP calls in the period · ${t.mcp_failed} failed</p>` : hubEmpty("No MCP server", "Add one under Teams & Agents → Tools & MCP."));
  dashFill("obs-models", d.models.length ? `<table><thead><tr><th>Model</th><th class="num">Runs</th><th class="num">Failed</th><th class="num">Tokens</th><th class="num">Cost</th><th class="num">Run time</th></tr></thead><tbody>${d.models.map((m) => `<tr><td>${esc(m.name)}</td><td class="num">${m.runs}</td><td class="num">${m.failed}</td><td class="num">${fmt.format(m.tokens)}</td><td class="num">${money(m.cost)}</td><td class="num">${ccDur(m.seconds)}</td></tr>`).join("")}</tbody></table>` : hubEmpty("No model run", "in the period."));
  dashFill("obs-net", `<div class="gm-kv"><span class="muted">Documents fetched</span><span>${d.network.documents}</span><span class="muted">Source syncs</span><span>${d.network.syncs} · ${d.network.sync_failures} failed</span></div>${hubList(d.network.recent_syncs, (x) => `<li class="${x.ok === false ? "bad" : "ok"}">${esc(x.message)}<time>${ago(x.at)}</time></li>`, ["No sync", "External sources (Knowledge → External sources) report each sync here."])}`);
}

registerView({ id: "evolution-lab", title: "Evolution Lab", icon: "evolution" }, viewEvolutionLab);
registerView({ id: "governance", title: "Governance", icon: "shield" }, viewGovernance);
registerView({ id: "observability", title: "Observability", icon: "cpu", admin: true }, viewObservability);
