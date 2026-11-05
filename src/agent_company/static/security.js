"use strict";
// Security (SPEC-15 S15-R-03, SPEC-14): active stops, incidents (open first, worst severity first), refused
// commands per agent, security alerts. The emergency stop itself is in the top bar on every page.

async function viewSecurity() {
  const sec = await api("/api/security");
  const inc = sec.incidents, open = inc.filter((i) => i.status !== "closed");
  const next = (s) => sec.flow[sec.flow.indexOf(s) + 1];
  const refusals = sec.refused.reduce((n, r) => n + r.count, 0);
  const k = (label, value, bad) => `<div class="card kpi"><div class="label">${label}</div><div class="value"${bad ? ' style="color:var(--bad)"' : ""}>${value}</div></div>`;
  $("#view").innerHTML = `<div class="grid kpis">${k("Stop level", sec.level || "none", !!sec.level)}${k("Open incidents", open.length, open.some((i) => i.severity <= "SEV-2"))}${k("Refused commands", refusals)}${k("Recent alerts", sec.alerts.length)}</div>
    ${sec.stops.length ? `<div class="card" style="margin-top:12px"><div class="row"><h2 class="grow" style="margin:0">Active stops</h2><button class="btn keep-text" id="sec-system">Open System to lift</button></div><div class="events" style="margin-top:6px">${sec.stops.map((x) => `<div><span class="k">${esc(x.level)}</span>${esc(x.scope)} · ${esc(x.reason || "")} <span class="muted">${esc(x.actor || "")} · ${ago(x.at)}</span></div>`).join("")}</div></div>` : ""}
    <div class="card" style="margin-top:12px"><div class="row"><h2 class="grow" style="margin:0">Security incidents</h2><span class="muted">${open.length} open · ${inc.length} total</span>${ib("plus", "Open an incident", 'id="inc-new"')}</div>
    <p class="muted" style="margin:4px 0 8px">Opened by the behaviour rules (credentials in a change, writes outside a worktree, repeated refused commands) or by an administrator. An incident moves forward only (${sec.flow.join(" → ")}); closing needs a root cause and a remediation note, and becomes a lesson for the agent.</p>
    ${inc.length ? `<div class="table-wrap"><table><tr><th>#</th><th>When</th><th>Severity</th><th>Category</th><th>Title</th><th>Goal</th><th>Status</th><th></th></tr>
    ${inc.map((i) => `<tr><td class="num">${i.id}</td><td>${ago(i.at)}</td><td><span class="pill ${i.severity <= "SEV-2" ? "failed" : "pending"}">${esc(i.severity)}</span></td><td>${esc(i.category.replaceAll("_", " "))}</td><td>${esc(i.title)}${i.rule ? ` <span class="muted">(rule ${esc(i.rule)})</span>` : ""}</td><td>${i.goal_id ? `<a href="#" data-sgoal="${i.goal_id}">#${i.goal_id}</a>` : ""}</td><td>${pill(i.status)}</td>
      <td class="nowrap">${i.status !== "closed" ? `<button class="btn" data-inc-next="${i.id}" data-to="${next(i.status)}">${esc(next(i.status))}</button>${i.agent_id ? `<button class="btn" data-inc-contain="${i.id}" data-how="pause_agent">Pause agent</button>` : ""}${i.goal_id ? `<button class="btn" data-inc-contain="${i.id}" data-how="cancel_goal">Cancel goal</button>` : ""}` : `<span class="muted">${esc(i.root_cause.slice(0, 80))}</span>`}</td></tr>`).join("")}</table></div>` : '<span class="muted">No incidents.</span>'}</div>
    <div class="grid two" style="margin-top:12px">
      <div class="card"><h2>Refused commands by agent</h2><p class="muted">Commands the policy refused (privilege escalation, production access, destructive operations). Three in one goal pause the agent (E1).</p>
        <div class="table-wrap"><table><tr><th>Agent</th><th class="num">Refused</th><th class="num">Goals</th><th>Last</th></tr>${sec.refused.map((r) => `<tr><td>${r.agent_id ? `<a href="#" data-sagent="${r.agent_id}">${esc(r.agent)}</a>` : esc(r.agent)}</td><td class="num">${r.count}</td><td class="num">${r.goals}</td><td>${ago(r.last_at)}</td></tr>`).join("") || '<tr><td colspan="4" class="muted">No command was refused.</td></tr>'}</table></div>
        ${sec.recent_refusals.length ? `<h3>Latest</h3><div class="events">${sec.recent_refusals.map((e) => `<div><span class="k">${esc(e.agent || "?")}</span>${esc(e.message)} ${e.goal_id ? `<a href="#" data-sgoal="${e.goal_id}">#${e.goal_id}</a>` : ""} <span class="muted">${ago(e.at)}</span></div>`).join("")}</div>` : ""}</div>
      <div class="card"><h2>Security alerts</h2><p class="muted">Raised by the behaviour rules and incidents; also in Notifications.</p><div class="events">${sec.alerts.map((n) => `<div><span class="pill ${n.level === "error" ? "failed" : "pending"}">${esc(n.level)}</span> <strong>${esc(n.title)}</strong>${n.goal_id ? ` <a href="#" data-sgoal="${n.goal_id}">#${n.goal_id}</a>` : ""} <span class="muted">${ago(n.at)}</span>${n.body ? `<div class="muted">${esc(n.body)}</div>` : ""}</div>`).join("") || '<span class="muted">No alerts.</span>'}</div></div></div>`;
  const v = $("#view");
  const send = async (id, body) => { try { await api(`/api/incidents/${id}`, { method: "POST", body }); viewSecurity(); } catch (e) { toast(e.message); } };
  v.querySelectorAll("[data-sgoal]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); goalModal(Number(a.dataset.sgoal)); }));
  v.querySelectorAll("[data-sagent]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); agentModal(Number(a.dataset.sagent)); }));
  v.querySelectorAll("[data-inc-next]").forEach((b) => (b.onclick = () => {
    const to = b.dataset.to;
    if (to === "closed") {
      const root_cause = prompt("Root cause of the incident?"); if (!root_cause) return;
      const note = prompt("Remediation: what was done so it does not happen again?"); if (!note) return;
      return send(b.dataset.incNext, { status: to, root_cause, note });
    }
    send(b.dataset.incNext, { status: to, note: prompt(`Note for "${to}" (optional)`) || "" });
  }));
  v.querySelectorAll("[data-inc-contain]").forEach((b) => (b.onclick = () => { if (confirm(`${b.textContent} as containment?`)) send(b.dataset.incContain, { contain: b.dataset.how }); }));
  $("#sec-system") && ($("#sec-system").onclick = () => show("system"));
  $("#inc-new").onclick = async () => {
    const title = prompt("Incident title"); if (!title) return;
    const category = prompt(`Category (${sec.categories.join(", ")})`, "policy_violation"); if (!category) return;
    const severity = prompt(`Severity (${sec.severities.join(", ")}; SEV-1 is the worst)`, "SEV-3") || "SEV-3";
    const goal = prompt("Goal id it concerns (optional)") || "";
    try { await api("/api/incidents", { method: "POST", body: { title, category, severity, goal_id: goal ? Number(goal) : null } }); viewSecurity(); } catch (e) { toast(e.message); }
  };
}

registerView({ id: "security", title: "Security", icon: "shield", admin: true }, viewSecurity);
