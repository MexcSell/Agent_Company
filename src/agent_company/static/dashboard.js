// Command Center — the G5 Control Hub's landing page (G5-CONTROL-HUB.md UI-G5-CC-01…05) on the AICO Dashboard
// (UI-DASH-01…10, UI-APPR-01). Figures come from /api/dashboard and /api/command-center (counted per period, trends
// only against the same count of the period before), /api/overview, /api/events and /api/approvals.
// The page is built once; live redraws replace a card's body only when it changed, so the GM composer keeps its text.
const DASH_PERIODS = { "24h": "Last 24 hours", "7d": "Last 7 days", "30d": "Last 30 days" };
const DASH_PHASE = { draft: ["Draft", "neutral"], planning: ["Planning", "info"], active: ["Active", "ok"], blocked: ["Blocked", "bad"],
  review: ["QA / Review", "warn"], release_ready: ["Release ready", "ok"], paused: ["Paused", "warn"], completed: ["Completed", "ok"],
  archived: ["Archived", "neutral"], cancelled: ["Cancelled", "neutral"] };
const DASH_KIND = { goal: ["Goal", "goals"], plan: ["Plan approval", "goals"], questions: ["GM questions", "goals"], org: ["Organisation change", "network"], decision: ["Decision", "check"], proposal: ["Proposal", "bolt"], definition: ["Agent evolution", "evolution"],
  recruitment: ["Recruitment", "user_add"], experiment: ["Experiment", "performance"], requirements: ["Requirements", "files"], user: ["Account", "users"] };
const DASH_TONES = ["--tone-blue", "--tone-green", "--tone-orange", "--tone-purple", "--tone-teal", "--tone-pink", "--tone-grey"];
const DASH_QUIET = new Set(["retrieval"]);  // ranking runs: thousands, not activity a person follows
let dashPeriod = store.get("ac.dashPeriod") in DASH_PERIODS ? store.get("ac.dashPeriod") : "24h", dashGoal = null;

function dashFill(id, html) {  // replace a card's body only when it changed (quiet live redraws)
  const el = document.getElementById(id);
  if (el && el.dataset.html !== html) { el.innerHTML = html; el.dataset.html = html; iconize(el); return el; }
  return null;
}
function dashCompany(o) {
  if (o.stop_level) return ["Stopped", "bad", `Emergency stop ${o.stop_level}`];
  if ((o.goals?.paused || 0) && !(o.goals?.running || 0)) return ["Paused", "warn", `${o.goals.paused} goal(s) paused`];
  if (o.degraded_agents) return ["Degraded", "warn", `${o.degraded_agents} agent(s) degraded`];
  return ["Operating", "ok", "All core services responding"];
}
function dashTrend(cur, prev, money_ = false, lowerIsBetter = false) {
  if (!prev && !cur) return `<div class="kpi-trend flat">no change</div>`;
  if (!prev) return `<div class="kpi-trend flat">none in the period before</div>`;
  const pct = Math.round(((cur - prev) / prev) * 100), dir = pct > 0 ? "up" : pct < 0 ? "down" : "flat";
  const good = lowerIsBetter ? (pct < 0 ? "up" : pct > 0 ? "down" : "flat") : dir;
  return `<div class="kpi-trend ${good}" title="period before: ${money_ ? money(prev) : fmt.format(prev)}">${pct > 0 ? "↑" : pct < 0 ? "↓" : "→"} ${Math.abs(pct)}% vs period before</div>`;
}
function dashKpi(ic, value, label, href, extra = "") {
  return `<a class="card kpi" href="${href}"><span class="kpi-icon">${icon(ic, 18)}</span><div class="value">${value}</div><div class="label">${label}</div>${extra}</a>`;
}
function dashDonut(models) {
  const r = 52, c = 2 * Math.PI * r, top = models.slice(0, 5), rest = models.slice(5).reduce((a, m) => a + m.share, 0);
  const parts = [...top.map((m) => [m.name, m.share]), ...(rest > 0 ? [["Other", rest]] : [])];
  let off = 0;
  const arcs = parts.map(([, share], i) => { const len = share * c, a = `<circle r="${r}" cx="70" cy="70" fill="none" stroke="var(${DASH_TONES[i % DASH_TONES.length]})" stroke-width="16" stroke-dasharray="${len} ${c - len}" stroke-dashoffset="${-off}" transform="rotate(-90 70 70)"/>`; off += len; return a; }).join("");
  const lead = parts[0];
  return `<div class="dash-donut"><svg viewBox="0 0 140 140" width="150" height="150" role="img" aria-label="Token share per model"><circle r="${r}" cx="70" cy="70" fill="none" stroke="var(--color-elevated)" stroke-width="16"/>${arcs}<text x="70" y="68" text-anchor="middle" class="dash-donut-v">${lead ? Math.round(lead[1] * 100) + "%" : "–"}</text><text x="70" y="86" text-anchor="middle" class="dash-donut-l">${lead ? "top model" : "no runs"}</text></svg>
    <ul class="dash-legend">${parts.map(([name, share], i) => `<li><span class="dot" style="background:var(${DASH_TONES[i % DASH_TONES.length]})"></span><span class="grow ellipsis">${esc(name)}</span><strong>${Math.round(share * 100)}%</strong></li>`).join("")}</ul></div>`;
}
function dashEventTone(e) {
  const m = (e.message || "").toLowerCase();
  if (e.kind === "error" || /fail|refused|error|stuck/.test(m)) return "bad";
  if (["escalation", "risk", "security"].includes(e.kind) || /limit|budget|paused|blocked/.test(m)) return "warn";
  if (["task", "gate"].includes(e.kind) && /finished|passed|merged|done/.test(m)) return "ok";
  return "info";
}

const CC_METRICS = [  // UI-G5-CC-02: the nine operational metrics, in the specification's order
  ["mission_success_rate", "Mission success rate", "Accepted ÷ finished missions"],
  ["task_completion_rate", "Task completion rate", "Done ÷ finished tasks"],
  ["validation_score", "Validation score", "Quality-gate pass rate"],
  ["cost_per_successful_mission", "Cost per successful mission", "Spend ÷ accepted missions"],
  ["agent_utilisation", "Agent utilisation", "Run time ÷ agent capacity"],
  ["rework_rate", "Rework rate", "Review rejections and retries"],
  ["recovery_rate", "Recovery rate", "Missions with a failed task that still passed"],
  ["improvement_gain", "Improvement gain", "Latest experiment, candidate vs baseline"],
  ["approval_wait", "Approval wait time", "Median time a person took to decide"]];
const CC_LOWER = new Set(["cost_per_successful_mission", "rework_rate", "approval_wait"]);
function ccValue(k, m) {
  if (!m) return [null, "–"];
  if (k === "cost_per_successful_mission") return [m.usd, m.usd == null ? "–" : money(m.usd)];
  if (k === "approval_wait") return [m.median_seconds, m.median_seconds == null ? "–" : ccDur(m.median_seconds)];
  if (k === "improvement_gain") return [m.pass_rate_change, m.pass_rate_change == null ? "–" : `${m.pass_rate_change > 0 ? "+" : ""}${Math.round(m.pass_rate_change * 100)} pts`];
  return [m.rate, m.rate == null ? "–" : `${Math.round(m.rate * 100)}%`];
}
function ccMetric(k, label, how, cur, prev) {
  const [v, shown] = ccValue(k, cur), [pv] = ccValue(k, prev), basis = cur?.basis || "period";
  let trend = `<span class="muted">${basis === "period" ? "this period" : basis === "all" ? "all recorded work" : esc(basis)}</span>`;
  if (basis === "period" && v != null && pv != null && pv !== v) {
    const up = v > pv, good = CC_LOWER.has(k) ? !up : up;
    trend = `<span class="kpi-trend ${good ? "up" : "down"}">${up ? "↑" : "↓"} vs period before</span>`;
  }
  const link = k === "improvement_gain" && cur?.experiment_id ? "#/evolution-lab" : k === "approval_wait" ? "#/governance" : k === "agent_utilisation" ? "#/swarm" : "#/goals";
  return `<a class="cc-metric" href="${link}" title="${esc(how)}"><span class="muted cc-metric-l">${esc(label)}</span><strong class="cc-metric-v">${shown}</strong><span class="cc-metric-n">${trend} · n=${Number(cur?.n || 0)}</span></a>`;
}
function ccDur(sec) {
  if (sec == null) return "–";
  if (sec < 90) return `${Math.round(sec)} s`;
  if (sec < 5400) return `${Math.round(sec / 60)} min`;
  if (sec < 172800) return `${(sec / 3600).toFixed(1)} h`;
  return `${(sec / 86400).toFixed(1)} d`;
}
function ccBytes(n) { return n == null ? "–" : n > 1e12 ? `${(n / 1e12).toFixed(1)} TB` : n > 1e9 ? `${(n / 1e9).toFixed(1)} GB` : `${Math.round(n / 1e6)} MB`; }
function ccMeter(label, used, total, text) {
  const pct = total ? Math.min(100, Math.round((used / total) * 100)) : 0;
  return `<div class="dash-bar"><span class="ellipsis">${esc(label)}</span><div class="progress ${pct >= 90 ? "bad" : pct >= 75 ? "warn" : "ok"}" title="${pct}%"><span style="width:${pct}%"></span></div><strong>${pct}%</strong></div><div class="muted dash-meta cc-meter-t">${text}</div>`;
}
function ccBudget(b) {
  const max = Math.max(b.daily_budget_usd || 0, ...b.days.map((d) => d.usd), 0.0001);
  const line = b.daily_budget_usd ? `<div class="cc-budget-line" style="bottom:${(b.daily_budget_usd / max) * 100}%" title="daily budget ${money(b.daily_budget_usd)}"></div>` : "";
  const bars = b.days.map((d) => `<div class="cc-day" title="${esc(d.day)}: ${money(d.usd)}"><span class="${b.daily_budget_usd && d.usd > b.daily_budget_usd ? "bad" : ""}" style="height:${Math.max(2, (d.usd / max) * 100)}%"></span><em>${esc(d.day.slice(5))}</em></div>`).join("");
  const host = b.host ? [ccMeter("CPU load", (b.host.load || [0])[0], b.host.cpus || 1, `load ${(b.host.load || []).join(" · ")} on ${b.host.cpus} CPUs`),
    ccMeter("Memory", (b.host.memory_total || 0) - (b.host.memory_available || 0), b.host.memory_total || 0, `${ccBytes(b.host.memory_available)} free of ${ccBytes(b.host.memory_total)}`),
    ccMeter("Disk", (b.host.disk_total || 0) - (b.host.disk_free || 0), b.host.disk_total || 0, `${ccBytes(b.host.disk_free)} free of ${ccBytes(b.host.disk_total)}`)].join("") : "";
  return `<div class="cc-days">${line}${bars}</div><p class="muted dash-meta">Spend per day, last 7 days${b.daily_budget_usd ? ` · daily budget ${money(b.daily_budget_usd)}` : " · no daily room budget set"} · today ${money(b.today_usd)} · ${fmt.format(b.tokens_today)} tokens today</p>${host ? `<div class="dash-bars cc-host">${host}</div>` : ""}`;
}
const CC_ALERT_ICON = { stop: "shield", incident: "shield", agent: "agents", model: "models", blocker: "bolt", mission: "goals", source: "book" };

async function viewDashboard(quiet) {
  const [o, d, ev, q, c] = await Promise.all([api("/api/overview"), api(`/api/dashboard?period=${dashPeriod}`), api("/api/events"),
    api("/api/approvals").catch(() => ({ items: [], total: 0 })), api(`/api/command-center?period=${dashPeriod}`)]);
  const view = $("#view");
  if (!quiet || !$("#dash")) {
    view.innerHTML = `<div id="dash" class="dash">
      <div class="page-head"><div class="page-head-text"><h2 class="page-title">Command Center</h2><div class="muted" id="dash-sub"></div></div>
        <div class="row nowrap"><span id="dash-status"></span><select id="dash-period" aria-label="Period">${Object.entries(DASH_PERIODS).map(([k, l]) => `<option value="${k}"${k === dashPeriod ? " selected" : ""}>${l}</option>`).join("")}</select></div></div>
      <div class="grid dash-kpis" id="dash-kpis"></div>
      <div class="grid dash-row two-wide">
        <section class="card"><div class="card-head"><h3>Operational metrics</h3><a class="view-all" href="#/evaluation">Quality →</a></div><div id="cc-metrics" class="cc-metrics"></div></section>
        <section class="card"><div class="card-head"><h3>Alerts &amp; risks <span id="cc-alert-n"></span></h3><a class="view-all" href="#/security">Security →</a></div><div id="cc-alerts"></div></section>
      </div>
      <div class="grid dash-row">
        <section class="card dash-gm"><div class="card-head"><h3>General Manager</h3><span id="dash-gm-state"></span></div><div id="dash-gm"></div>
          <form id="dash-gm-form" class="dash-gm-form"><select id="dash-gm-project" aria-label="Project"></select><input id="dash-gm-text" maxlength="4000" placeholder="Tell the General Manager what you need…" aria-label="Message to the General Manager"><button class="btn primary" type="submit">${icon("send", 16)} Send</button></form>
          <div class="row dash-actions"><button class="btn sm" data-act="new">Create a project</button><button class="btn sm" data-act="status">Check project status</button><button class="btn sm" data-act="reports">View reports</button><button class="btn sm" data-act="teams">Manage teams</button></div></section>
        <section class="card"><div class="card-head"><h3>Active projects</h3><a class="view-all" href="#/projects">View all →</a></div><div id="dash-projects"></div></section>
        <section class="card"><div class="card-head"><h3>Pending approvals <span id="dash-appr-n"></span></h3><a class="view-all" href="#/notifications">View all →</a></div><div id="dash-approvals"></div></section>
      </div>
      <div class="grid dash-row">
        <section class="card"><div class="card-head"><h3>Company lifecycle</h3><select id="dash-goal" aria-label="Goal" class="sm"></select></div><div id="dash-life"></div></section>
        <section class="card"><div class="card-head"><h3>Recent activity <span class="badge ok">● Live</span></h3><a class="view-all" href="#/goals">View all →</a></div><div id="dash-activity"></div></section>
        <section class="card"><div class="card-head"><h3>Model usage</h3><a class="view-all" href="#/models">View details →</a></div><div id="dash-models"></div></section>
      </div>
      <div class="grid dash-row two-wide">
        <section class="card"><div class="card-head"><h3>Resource &amp; budget monitor</h3><a class="view-all" href="#/observability">Observability →</a></div><div id="cc-budget"></div></section>
        <section class="card"><div class="card-head"><h3>Quick actions</h3></div><div class="cc-actions">
          <button class="btn keep-text" data-act="mission">${icon("goals", 16)} New mission</button><button class="btn keep-text" data-act="new">${icon("projects", 16)} New project</button>
          <button class="btn keep-text" data-act="status">${icon("general_manager", 16)} Ask the General Manager</button><button class="btn keep-text" data-act="swarm">${icon("network", 16)} Swarm Control</button>
          <button class="btn keep-text" data-act="governance">${icon("shield", 16)} Approvals &amp; governance</button><button class="btn keep-text" data-act="lab">${icon("evolution", 16)} Evolution Lab</button></div></section>
      </div>
      <div class="grid dash-row two-wide">
        <section class="card"><div class="card-head"><h3>Team performance</h3><a class="view-all" href="#/evaluation">View details →</a></div><div id="dash-teams"></div></section>
        <section class="card"><div class="card-head"><h3>Recent completions</h3><a class="view-all" href="#/goals">View all →</a></div><div id="dash-done"></div></section>
      </div></div>`;
    $("#dash-period").onchange = (e) => { dashPeriod = e.target.value; store.set("ac.dashPeriod", dashPeriod); render(true); };
    $("#dash-goal").onchange = (e) => { dashGoal = Number(e.target.value) || null; render(true); };
    view.querySelectorAll("[data-act]").forEach((b) => (b.onclick = () => {
      const a = b.dataset.act;
      if (a === "new") projectForm();
      else if (a === "mission") { goalFocus = true; show("goals"); }
      else if (a === "status") show("general-manager");
      else if (a === "reports") { goalFilter = "approved"; show("goals"); }
      else if (["swarm", "governance"].includes(a)) show(a);
      else if (a === "lab") show("evolution-lab");
      else show("agents");
    }));
    $("#dash-gm-form").onsubmit = async (e) => {
      e.preventDefault();
      const pid = Number($("#dash-gm-project").value), text = $("#dash-gm-text").value.trim();
      if (!pid || !text) return toast(pid ? "Write a message first" : "Choose a project first");
      try { await api(`/api/projects/${pid}/command`, { method: "POST", body: { content: text } }); $("#dash-gm-text").value = ""; location.hash = `#/general-manager/${pid}`; }
      catch (err) { toast(err.message); }
    };
  }
  const [state, tone, why] = dashCompany(o);
  const today = new Date().toLocaleDateString(undefined, inTz({ weekday: "long", month: "long", day: "numeric" }));
  dashFill("dash-sub", `${esc(today)} · ${esc(why)}`);
  dashFill("dash-status", `<span class="badge ${tone}">● ${state}</span>`);
  applyStatus?.(o);
  const g = o.goals || {}, live = (g.running || 0) + (g.planning || 0) + (g.recovering || 0);
  // UI-G5-CC-01: the six Command Center KPIs
  const m = c.metrics, pm = c.previous, sr = m.mission_success_rate, util = m.agent_utilisation, bd = c.budget;
  const warns = c.alerts.length - c.critical;
  dashFill("dash-kpis", [
    dashKpi("goals", c.missions.active, "Active missions", "#/goals", `<div class="kpi-trend flat">${g.running || 0} running · ${g.queued || 0} queued · ${g.paused || 0} paused</div>`),
    dashKpi("check", q.total, "Pending approvals", "#/governance", `<div class="kpi-trend ${q.total ? "down" : "flat"}">${q.counts?.goal || 0} merge(s) · ${q.counts?.plan || 0} plan(s)</div>`),
    dashKpi("agents", `${c.agents.busy}<span class="muted dash-of"> / ${c.agents.enabled}</span>`, "Agent utilisation", "#/swarm", `<div class="kpi-trend flat">${util.rate == null ? "no run time" : Math.round(util.rate * 100) + "% of capacity in the period"}</div>`),
    dashKpi("performance", money(d.current.spend).replace(/(\.\d\d)\d+$/, "$1"), `Budget burn · ${DASH_PERIODS[d.period].toLowerCase()}`, "#/governance", bd.daily_budget_usd ? `<div class="kpi-trend ${bd.today_usd > bd.daily_budget_usd ? "down" : "flat"}">today ${Math.round((bd.today_usd / bd.daily_budget_usd) * 100)}% of ${money(bd.daily_budget_usd)}</div>` : dashTrend(d.current.spend, d.previous.spend, true, true)),
    dashKpi("bolt", sr.rate == null ? "–" : `${Math.round(sr.rate * 100)}%`, "Mission success rate", "#/goals", sr.n ? `<div class="kpi-trend ${pm.mission_success_rate.rate == null || pm.mission_success_rate.rate === sr.rate ? "flat" : sr.rate > pm.mission_success_rate.rate ? "up" : "down"}">${sr.hits} of ${sr.n} finished accepted</div>` : `<div class="kpi-trend flat">no mission finished</div>`),
    dashKpi("shield", c.critical, "Critical alerts", "#/security", `<div class="kpi-trend ${c.critical ? "down" : "flat"}">${warns} warning${warns === 1 ? "" : "s"}</div>`),
  ].join(""));
  dashFill("cc-metrics", CC_METRICS.map(([k, label, how]) => ccMetric(k, label, how, m[k], pm[k])).join(""));
  dashFill("cc-alert-n", c.alerts.length ? `<span class="badge ${c.critical ? "bad" : "warn"}">${c.alerts.length}</span>` : "");
  dashFill("cc-alerts", c.alerts.length ? `<div class="dash-list">${c.alerts.slice(0, 8).map((a) => `<a class="dash-appr cc-alert" href="${esc(a.link)}"><span class="kpi-icon cc-${a.level}">${icon(CC_ALERT_ICON[a.kind] || "bolt", 16)}</span><div class="grow" style="min-width:0"><div class="ellipsis-2">${esc(a.title)}</div><div class="muted dash-meta">${esc(a.level)} · ${esc(a.kind)}${a.at ? " · " + ago(a.at) : ""}</div></div></a>`).join("")}${c.alerts.length > 8 ? `<p class="muted dash-meta">and ${c.alerts.length - 8} more</p>` : ""}</div>`
    : `<div class="empty"><p class="empty-title">No alert</p><p class="muted">No stop, incident, degraded agent, limited model, blocker or failed mission.</p></div>`);
  dashFill("cc-budget", ccBudget(bd));
  // General Manager card (UI-DASH-03)
  const gm = d.general_manager;
  dashFill("dash-gm-state", gm ? `<span class="badge ${gm.health === "degraded" ? "warn" : gm.status === "busy" ? "info" : "ok"}">● ${gm.health === "degraded" ? "Degraded" : gm.status === "busy" ? "Working" : "Online"}</span>` : "");
  dashFill("dash-gm", gm ? `<div class="row nowrap dash-gm-who"><span class="avatar dash-gm-avatar">${icon("general_manager", 24)}</span><div class="grow" style="min-width:0"><strong class="ellipsis" style="display:block">${esc(gm.name)}</strong><div class="muted ellipsis">${esc(gm.model || "no model routed")} · your company's orchestrator and planner</div></div></div>
    <p class="dash-gm-say">${q.total ? `<strong>${q.total}</strong> decision${q.total === 1 ? "" : "s"} need${q.total === 1 ? "s" : ""} your attention.` : "Nothing waits for you right now."} ${live ? `${live} goal${live === 1 ? " is" : "s are"} in progress.` : ""}</p>`
    : `<div class="empty"><p class="empty-title">No General Manager</p><p class="muted">Enable the General Manager on the Agents page.</p></div>`);
  const sel = $("#dash-gm-project");
  const opts = d.projects.map((p) => `<option value="${p.id}">${esc(p.name)}</option>`).join("");
  if (sel && sel.dataset.html !== opts) { const v = sel.value; sel.innerHTML = opts || '<option value="">No project</option>'; sel.dataset.html = opts; if (v) sel.value = v; }
  // Active projects (UI-DASH-04)
  dashFill("dash-projects", d.projects.length ? `<div class="dash-list">${d.projects.slice(0, 6).map((p) => { const [label, t] = DASH_PHASE[p.phase] || [p.phase, "neutral"], pct = Math.round(p.progress * 100);
    return `<a class="dash-proj" href="#/projects/${p.id}"><div class="row nowrap"><strong class="grow ellipsis">${esc(p.name)}</strong><span class="badge ${t}">${label}</span><strong class="dash-pct">${pct}%</strong></div>
      <div class="progress ${t === "bad" ? "bad" : t === "warn" ? "warn" : ""}"><span style="width:${pct}%"></span></div>
      <div class="muted dash-meta">${p.running} goal${p.running === 1 ? "" : "s"} live · ${p.team} agent${p.team === 1 ? "" : "s"}${p.next_milestone ? ` · next: ${esc(p.next_milestone.name)}${p.next_milestone.overdue ? ' <span class="badge bad">overdue</span>' : ""}` : ""}</div></a>`; }).join("")}</div>`
    : `<div class="empty"><p class="empty-title">No active project</p><p class="muted">Create one, or ask the General Manager.</p></div>`);
  // Pending approvals (UI-DASH-06): Review opens the item; Approve/Reject only where the list may decide
  dashFill("dash-appr-n", q.total ? `<span class="badge bad">${q.total}</span>` : "");
  const box = dashFill("dash-approvals", q.items.length ? `<div class="dash-list">${q.items.slice(0, 6).map((x) => { const [k, ic] = DASH_KIND[x.kind] || [x.kind, "check"];
    return `<div class="dash-appr"><span class="kpi-icon">${icon(ic, 16)}</span><div class="grow" style="min-width:0"><div class="ellipsis"><strong>${esc(x.title)}</strong></div><div class="muted dash-meta ellipsis">${esc(k)}${x.project ? " · " + esc(x.project) : ""}${x.origin ? " · " + esc(x.origin) : ""} · ${ago(x.at)}</div></div>
      <div class="row nowrap">${x.link ? `<a class="btn sm" href="${esc(x.link)}">Review</a>` : ""}${x.actions.includes("approve") ? `<button class="btn sm primary" data-ok="${x.kind}:${x.id}">Approve</button>` : ""}${x.actions.includes("reject") ? `<button class="btn sm" data-no="${x.kind}:${x.id}">Reject</button>` : ""}</div></div>`; }).join("")}${q.items.length > 6 ? `<p class="muted dash-meta">and ${q.items.length - 6} more</p>` : ""}</div>`
    : `<div class="empty"><p class="empty-title">All clear</p><p class="muted">Nothing waits for your approval.</p></div>`);
  if (box) box.querySelectorAll("[data-ok],[data-no]").forEach((b) => (b.onclick = async () => {
    const ok = !!b.dataset.ok, [kind, id] = (b.dataset.ok || b.dataset.no).split(":");
    const note = ok ? "" : prompt("Why is it rejected? (recorded with the decision)");
    if (!ok && note === null) return;
    try {
      if (kind === "decision") await api(`/api/decisions/${id}/decide`, { method: "POST", body: { approve: ok, note: note || "" } });
      else if (kind === "proposal") await api(`/api/proposals/${id}/${ok ? "approve" : "reject"}`, { method: "POST" });
      else if (kind === "plan") await api(`/api/goals/${id}/plan/approve`, { method: "POST" });
      toast(ok ? "Approved" : "Rejected"); render(true); updateBadges();
    } catch (err) { toast(err.message); }
  }));
  // Company lifecycle (UI-DASH-05): the live goals' step, as on the Company map
  const goals = d.live_goals;
  const gsel = $("#dash-goal"), gopts = goals.map((x) => `<option value="${x.id}">#${x.id} ${esc(x.title.slice(0, 48))}</option>`).join("");
  if (gsel.dataset.html !== gopts) { gsel.innerHTML = gopts || '<option value="">No live goal</option>'; gsel.dataset.html = gopts; }
  if (!goals.some((x) => x.id === dashGoal)) dashGoal = goals[0]?.id || null;
  if (dashGoal) gsel.value = String(dashGoal);
  const cur = goals.find((x) => x.id === dashGoal);
  const step = cur ? cmLifecycleStep(cur, cur.tasks) : null, at = step ? CM_STEPS.indexOf(step) : -1;
  dashFill("dash-life", cur ? `<ol class="stepper">${CM_STEPS.map((s, i) => `<li class="${i < at ? "done" : i === at ? "current" : ""}">${s[0].toUpperCase() + s.slice(1)}</li>`).join("")}</ol>
    <p class="muted dash-meta" style="margin-top:12px">${at} of ${CM_STEPS.length} stages complete · <a href="#/goals/${cur.id}">goal #${cur.id}</a> ${pill(cur.status)}</p>
    <div class="muted dash-meta">${goals.length} live goal${goals.length === 1 ? "" : "s"}: ${CM_STEPS.map((s) => { const n = goals.filter((x) => cmLifecycleStep(x, x.tasks) === s).length; return n ? `${n} ${s}` : ""; }).filter(Boolean).join(" · ")}</div>`
    : `<ol class="stepper">${CM_STEPS.map((s) => `<li>${s[0].toUpperCase() + s.slice(1)}</li>`).join("")}</ol><p class="muted dash-meta" style="margin-top:12px">No goal is running. A goal moves Discover → Plan → Execute → Critique → Validate → Learn.</p>`);
  // Recent activity (UI-DASH-07)
  const evs = ev.filter((e) => !DASH_QUIET.has(e.kind)).slice(-8).reverse();  // the endpoint answers oldest first
  dashFill("dash-activity", evs.length ? `<ul class="timeline">${evs.map((e) => `<li class="${dashEventTone(e)}"><div class="ellipsis-2">${e.goal_id ? `<a href="#/goals/${e.goal_id}">${esc(e.message)}</a>` : esc(e.message)}</div><time>${esc(e.kind)} · ${ago(e.at)}</time></li>`).join("")}</ul>`
    : `<div class="empty"><p class="empty-title">Quiet</p><p class="muted">Nothing happened yet.</p></div>`);
  // Model usage (UI-DASH-08)
  dashFill("dash-models", d.models.length ? dashDonut(d.models) + `<p class="muted dash-meta">${fmt.format(d.models.reduce((a, m) => a + m.tokens, 0))} tokens · ${fmt.format(d.models.reduce((a, m) => a + m.runs, 0))} runs in the period</p>`
    : `<div class="empty"><p class="empty-title">No model runs</p><p class="muted">in ${DASH_PERIODS[d.period].toLowerCase()}.</p></div>`);
  // Team performance (UI-DASH-09): accepted / failed tasks per room in the period
  const max = Math.max(1, ...d.rooms.map((r) => r.done + r.failed));
  dashFill("dash-teams", d.rooms.length ? `<div class="dash-bars">${d.rooms.slice(0, 8).map((r) => { const n = r.done + r.failed, rate = n ? Math.round((r.done / n) * 100) : 0;
    return `<div class="dash-bar"><span class="ellipsis">${esc(r.room.replace(/_/g, " "))}</span><div class="dash-bar-track" title="${r.done} done · ${r.failed} failed"><span class="ok" style="width:${(r.done / max) * 100}%"></span><span class="bad" style="width:${(r.failed / max) * 100}%"></span></div><strong>${rate}%</strong></div>`; }).join("")}</div><p class="muted dash-meta">Share of finished tasks accepted, per room, ${DASH_PERIODS[d.period].toLowerCase()}.</p>`
    : `<div class="empty"><p class="empty-title">No finished tasks</p><p class="muted">in ${DASH_PERIODS[d.period].toLowerCase()}.</p></div>`);
  // Recent completions (UI-DASH-10): the outcome is the gate's (goal status), the report is the mission report
  dashFill("dash-done", d.completed.length ? `<div class="dash-list">${d.completed.map((x) => `<div class="row nowrap dash-done"><span class="badge ${x.status === "approved" ? "ok" : "bad"}">${x.status === "approved" ? "merged" : "failed"}</span><a class="grow ellipsis" href="#/goals/${x.id}">#${x.id} ${esc(x.title)}</a><a class="view-all" href="/api/goals/${x.id}/report?format=md" target="_blank" rel="noopener">report</a><span class="muted dash-meta">${ago(x.at)}</span></div>`).join("")}</div>`
    : `<div class="empty"><p class="empty-title">No goal finished</p><p class="muted">in ${DASH_PERIODS[d.period].toLowerCase()}.</p></div>`);
}
registerView({ id: "dashboard", title: "Command Center", icon: "dashboard" }, viewDashboard);
