"use strict";
// Performance: company/room/agent metrics, defects, certifications, eval harness.

const pct = (v) => (v === null || v === undefined ? "–" : `${Math.round(v * 100)}%`);

// S07-R-04: the planner's estimates are predictions; ratio = actual / estimate (above 1: it took longer)
function calibrationCard(cal) {
  if (!cal) return "";
  const row = (name, a, unit) => `<tr><td>${esc(name)}</td><td class="num">${a.samples}</td><td class="num">${a.estimated}${unit} → ${a.actual}${unit}</td><td class="num">×${a.median_ratio}</td><td class="num">${pct(a.within_50)}</td><td>${a.bias === "calibrated" ? pill("done").replace(/>[^<]+</, ">calibrated<") : `<span class="muted">${esc(a.bias)}</span>`}</td></tr>`;
  const rows = cal.time_by_role.map((x) => row(x.role, x, " min")).join("") + (cal.goal_cost ? row("goal cost (USD)", cal.goal_cost, "") : "");
  return `<div class="card"><h2>Estimate accuracy</h2><p class="muted">Planner estimates against what happened: task minutes per role, goal cost. Median ratio actual/estimate; within ±50%; labelled from ${Number(cal.min_samples)} samples.</p><div class="table-wrap"><table><tr><th>Role</th><th class="num">Samples</th><th class="num">Estimated → actual</th><th class="num">Ratio</th><th class="num">±50%</th><th></th></tr>${rows || '<tr><td colspan="6" class="muted">No finished task had an estimate yet.</td></tr>'}</table></div></div>`;
}

// S09-R-03: an administrator's defect decisions; a retest runs the command check again in a fresh process
function defectActions(d) {
  const b = (s, label) => `<button class="btn sm" data-dst="${d.id}" data-s="${s}">${label}</button>`;
  if (!d.open) return d.status === "detected" ? "" : b("detected", "Open again");
  return (d.retestable ? `<button class="btn sm" data-dretest="${d.id}">Retest</button>` : "") + b("accepted_risk", "Accept risk") + b("deferred", "Defer") + b("duplicate", "Duplicate") + (d.verification.how === "retest" && d.verification.passed ? b("not_reproducible", "Not reproducible") : "");
}

function bindDefectActions(root, refresh) {
  root.querySelectorAll("[data-dst]").forEach((b) => (b.onclick = async () => {
    const body = { status: b.dataset.s };
    if (body.status === "duplicate") { const id = prompt("Duplicate of defect # (same goal)"); if (!id) return; body.duplicate_of = Number(id.replace("#", "")); }
    if (body.status !== "detected") { const note = prompt("Why? (kept with the defect)"); if (note === null) return; body.note = note; }
    try { await api(`/api/defects/${b.dataset.dst}/status`, { method: "POST", body }); refresh(); } catch (err) { toast(err.message); }
  }));
  root.querySelectorAll("[data-dprio]").forEach((sel) => (sel.onchange = async () => { try { await api(`/api/defects/${sel.dataset.dprio}/priority`, { method: "POST", body: { priority: Number(sel.value) } }); refresh(); } catch (err) { toast(err.message); } }));
  root.querySelectorAll("[data-dretest]").forEach((b) => (b.onclick = async () => {
    b.disabled = true;
    try { const d = await api(`/api/defects/${b.dataset.dretest}/retest`, { method: "POST" }); toast(`Retest ${d.verification.passed ? "passed" : "failed"} (exit ${d.verification.exit_code ?? "–"}, ${d.verification.duration_s}s): ${d.status.replace(/_/g, " ")}`); refresh(); } catch (err) { toast(err.message); b.disabled = false; }
  }));
}

function operationsCard(o) {
  if (!o) return "";
  const n = (x) => `<span class="muted">n=${Number(x.n)}</span>`;
  const r = (label, x, title) => `<tr title="${esc(title)}"><td>${label}</td><td class="num">${pct(x.rate)}</td><td class="num">${n(x)}</td></tr>`;
  const t = (label, x, title) => `<tr title="${esc(title)}"><td>${label}</td><td class="num">${x.median_seconds == null ? "–" : dur(x.median_seconds)}</td><td class="num">${n(x)}</td></tr>`;
  const sec = o.security_findings;
  return `<div class="card"><h2>Operational metrics</h2><p class="muted">Verified outcomes of real work (benchmark goals left out), each with its sample size; nothing is combined into one score.</p><table><tr><th>Metric</th><th class="num">Value</th><th class="num">Samples</th></tr>
    ${r("Quality gate pass rate", o.gate_pass_rate, "Gate evaluations that passed, of all gate evaluations (one per iteration)")}
    ${r("Passed in iteration 1", o.first_iteration_pass_rate, "Goals whose first gate evaluation passed")}
    ${r("Review rejection rate", o.review_rejection_rate, "QA and security reviews that answered VERDICT: FAIL")}
    ${r("Escaped defects", o.escaped_defects, "Defects closed at a merge and reopened afterwards (a retest failed)")}
    ${t("Median cycle time", o.cycle_time, "From the order to the goal's end (merged, rejected, cancelled or failed)")}
    <tr title="The cycle time 95% of ended goals stayed within: the tail the median hides"><td>p95 cycle time</td><td class="num">${o.cycle_time.p95_seconds == null ? "–" : dur(o.cycle_time.p95_seconds)}</td><td class="num">${n(o.cycle_time)}</td></tr>
    <tr title="Time ended goals spent paused, and its share of their cycle time"><td>Blocked time</td><td class="num">${dur(o.blocked_time.seconds)}${o.blocked_time.share != null ? ` · ${pct(o.blocked_time.share)}` : ""}</td><td class="num">${n(o.blocked_time)}</td></tr>
    ${t("Human approval latency", o.approval_latency, "Median time a goal waited for a person's approve or reject")}
    ${o.human_override_rate ? r("Human override rate", o.human_override_rate, "A person's merge decisions that rejected a goal the quality gate had passed") : ""}
    ${r("Retry rate", o.retry_rate, "Retries per model run")}
    ${o.tool_call_failure_rate ? r("Tool-call failure rate", o.tool_call_failure_rate, "Tool calls of agent runs (Claude Code, Codex) that returned an error") : ""}
    <tr title="Model cost of merged goals"><td>Cost per accepted goal</td><td class="num">${o.cost_per_accepted_goal.usd == null ? "–" : money(o.cost_per_accepted_goal.usd)}</td><td class="num">${n(o.cost_per_accepted_goal)}</td></tr>
    ${r("Rollback rate", o.rollback_rate, "Deployed improvement contracts that were rolled back")}
    <tr title="Failed goals whose branch was reset to a better earlier iteration"><td>Best-iteration resets</td><td class="num">${Number(o.best_iteration_resets)}</td><td></td></tr>
    ${o.failure_layers ? `<tr title="Confirmed causes of root-cause analyses by the layer that must change (model, prompt, code, orchestration, tools, data, environment)"><td>Root causes by layer</td><td class="num">${Number(o.failure_layers.causes)}</td><td class="num muted">${Object.entries(o.failure_layers.layers).filter(([, v]) => v).map(([k, v]) => `${v} ${esc(k)}`).join(" · ") || "–"} · n=${Number(o.failure_layers.n)}</td></tr>` : ""}
    <tr title="Secret scans, security stages and dependency audits that failed, refused commands and security incidents"><td>Security findings</td><td class="num">${Number(sec.total)}</td><td class="num muted">${sec.secret_scan} secret · ${sec.security_stage} stage · ${sec.dependency_audit} dependency · ${sec.refused_commands} refused · ${sec.incidents} incident(s)</td></tr>
  </table></div>`;
}

// SPEC-01 FR-08: department KPIs, each with its definition, formula, data source, target and interval; values over
// time with the target they were judged against and the records they counted.
const kpiFmt = (unit, v) => (v == null ? "–" : unit === "ratio" ? pct(v) : unit === "USD" ? money(v) : unit === "hours" ? `${Number(v).toFixed(1)} h` : String(Math.round(v * 100) / 100));
function kpisCard(kp) {
  const ev = (v) => Object.entries(v?.evidence || {}).map(([k, ids]) => `${k}: ${ids.join(", ")}`).join("\n");
  const hist = (k) => [...k.history].reverse().slice(-8).map((v) => `<span class="${v.met === false ? "bad" : ""}" title="${esc(`${day(v.period_start)} – ${day(v.period_end)} · n=${v.samples} · target ${kpiFmt(k.unit, v.target)}\n${ev(v)}`)}">${v.value == null ? "·" : kpiFmt(k.unit, v.value)}${v.met === true ? "✓" : v.met === false ? "✗" : ""}</span>`).join(" ");
  const row = (k) => `<tr><td>${esc(k.room_name)}</td><td title="${esc(`${k.definition}\nFormula: ${k.formula}\nData source: ${k.data_source}\nInterval: ${k.interval}`)}">${esc(k.name)}</td>
      <td class="num" style="white-space:nowrap" title="${esc(k.latest ? `n=${k.latest.samples}\n${ev(k.latest)}` : "not measured yet")}">${kpiFmt(k.unit, k.latest?.value)}${k.change != null ? ` <span class="muted">${k.change >= 0 ? "+" : ""}${kpiFmt(k.unit === "ratio" ? "ratio" : "", k.change)}</span>` : ""}</td>
      <td class="num" style="white-space:nowrap">${k.target == null ? "–" : `${k.direction === "higher" ? "≥" : "≤"} ${kpiFmt(k.unit, k.target)}`}</td><td>${k.latest?.met == null ? "" : pill(k.latest.met ? "done" : "failed").replace(/>[^<]+</, `>${k.latest.met ? "met" : "missed"}<`)}</td>
      <td class="muted">${hist(k) || "–"}</td><td class="muted">${esc(k.interval)}</td>
      <td>${me.is_admin ? `<span class="row nowrap">${ib("play", "Measure now", `data-kpi="measure" data-id="${k.id}"`, "sm")}${ib("edit", "Change the target", `data-kpi="target" data-id="${k.id}"`, "sm")}${ib("x", "Remove (disable)", `data-kpi="off" data-id="${k.id}"`, "sm")}</span>` : ""}</td></tr>`;
  return `<div class="card" id="kpi-card"><div class="row nowrap"><h2 class="grow" style="margin:0">Department KPIs</h2>${me.is_admin ? ib("plus", "Add a KPI", 'id="kpi-add"', "sm") : ""}</div>
    <p class="muted">Each room's measurements over its interval, never one score for an agent. Hover a KPI for its definition, formula and data source, a value for the records it counted; each value keeps the target it was judged against.</p>
    <div class="table-wrap"><table><tr><th>Room</th><th>KPI</th><th class="num">Latest</th><th class="num">Target</th><th></th><th>History</th><th>Every</th><th></th></tr>${kp.kpis.map(row).join("") || '<tr><td colspan="8" class="muted">No KPIs defined.</td></tr>'}</table></div></div>`;
}
function kpisWire(kp) {
  const again = () => viewPerformance();
  const run = async (fn) => { try { await fn(); } catch (e) { toast(e.message); } again(); };
  document.querySelectorAll("[data-kpi]").forEach((b) => (b.onclick = () => {
    const id = b.dataset.id, k = kp.kpis.find((x) => String(x.id) === id);
    if (b.dataset.kpi === "measure") run(() => api(`/api/kpis/${id}/measure`, { method: "POST" }));
    else if (b.dataset.kpi === "target") { const v = prompt(`Target for ${k.name} (${k.direction} is better; empty = none)`, k.target ?? ""); if (v === null) return; run(() => api(`/api/kpis/${id}`, { method: "PATCH", body: v.trim() === "" ? { clear_target: true } : { target: Number(v) } })); }
    else if (confirm(`Remove '${k.name}' of ${k.room_name}? Its history is kept.`)) run(() => api(`/api/kpis/${id}`, { method: "PATCH", body: { enabled: false } }));
  }));
  $("#kpi-add") && ($("#kpi-add").onclick = () => {
    const room = prompt("Room key (e.g. development, security, analysis)", "development"); if (!room) return;
    const metric = prompt(`Formula:\n${kp.metrics.map((m) => `${m.key} — ${m.label} (${m.unit}, ${m.direction} is better)`).join("\n")}`, kp.metrics[0].key); if (!metric) return;
    const target = prompt("Target (empty = none)", ""); if (target === null) return;
    const interval = prompt(`Interval: ${kp.intervals.join(", ")}`, "weekly"); if (!interval) return;
    run(() => api("/api/kpis", { method: "POST", body: { room: room.trim(), metric: metric.trim(), interval: interval.trim(), target: target.trim() === "" ? null : Number(target) } }));
  });
}

// `root`: the Evaluation & QA page's Performance tab; the old #/performance route draws into the page itself.
async function viewPerformance(quiet, root = $("#view")) {
  if (quiet && $("#ev-form")) return;
  const [r, defects, certs, evals, rec, kp] = await Promise.all([api("/api/performance"), api("/api/defects?status=open"), api("/api/certifications"), api("/api/evals"), api("/api/recruitment/effectiveness"), api("/api/kpis").catch(() => null)]);
  const c = r.company;
  const k = (label, value) => `<div class="card kpi"><div class="label">${label}</div><div class="value">${value}</div></div>`;
  root.innerHTML = `<div class="grid kpis">${k("Goals finished", `${c.finished} / ${c.goals}`)}${k("Pass rate", pct(c.pass_rate))}${k("Iterations to pass", c.avg_iterations_to_pass ?? "–")}${k("Cost per passed goal", c.cost_per_passed_goal === null ? "–" : money(c.cost_per_passed_goal))}${k("Open defects", c.open_defects)}</div>
    <div class="grid halves" style="margin-top:12px">
      <div class="grid"><div class="card"><h2>Open defects</h2><p class="muted">Detected → assigned (a writing task of the next iteration) → fix submitted → retested (the gate or a retest ran the check again in a fresh process) → closed at the merge. Priority 1 comes first.</p><div class="table-wrap"><table><tr><th>Priority</th><th>Severity</th><th>Status</th><th>Check</th><th>Goal</th><th>Iterations</th></tr>${defects.slice(0, 40).map((d) => `<tr><td>${me.is_admin ? `<select data-dprio="${d.id}" style="width:auto">${[1, 2, 3, 4, 5].map((p) => `<option ${p === d.priority ? "selected" : ""}>${p}</option>`).join("")}</select>` : `P${Number(d.priority)}`}</td><td>${pill(d.severity === "critical" || d.severity === "high" ? "failed" : "pending").replace(/>[^<]+</, `>${esc(d.severity)}<`)}</td><td>${pill(d.status)}${d.assignee ? `<div class="muted">${esc(d.assignee)}</div>` : ""}</td><td>${esc(d.check)}<div class="muted ellipsis" style="max-width:320px">${esc(d.detail.split("\n")[0])}</div>${me.is_admin ? `<div class="row" style="gap:4px;margin-top:4px">${defectActions(d)}</div>` : ""}</td><td><a href="#" data-goal="${d.goal_id}">#${d.goal_id}</a></td><td class="num">${d.first_iteration}–${d.last_iteration}</td></tr>`).join("") || '<tr><td colspan="6" class="muted">No open defects.</td></tr>'}</table></div></div>
      <div class="card"><h2>Rooms</h2><table><tr><th>Room</th><th class="num">Agents</th><th class="num">Tasks</th><th class="num">Completion</th><th class="num">Cost</th></tr>${r.rooms.map((x) => `<tr><td>${esc(x.name)}</td><td class="num">${x.agents}</td><td class="num">${x.tasks}</td><td class="num">${pct(x.completion_rate)}</td><td class="num">${money(x.cost)}</td></tr>`).join("") || '<tr><td colspan="5" class="muted">No work yet.</td></tr>'}</table></div>
      ${kp ? kpisCard(kp) : ""}
      ${operationsCard(r.operations)}
      ${calibrationCard(r.calibration)}
      <div class="card"><h2>Eval harness</h2><p class="muted">Runs a suite of checkable tasks several times in fresh repositories; a task is solved only if every run passes. Compare labels to see whether a model or routing change helped.</p>
        ${me.is_admin ? `<form id="ev-form" class="row"><select id="ev-suite" style="width:auto">${evals.suites.map((s) => `<option>${esc(s.name)}</option>`).join("")}</select><input id="ev-label" placeholder="label, e.g. sonnet-v1" required pattern="[\\w.-]{1,64}" class="grow"><input id="ev-rep" type="number" min="1" max="10" value="1" style="width:70px" title="repeats"><button class="btn primary">Run</button></form>` : ""}
        <div class="events" style="margin-top:8px">${evals.labels.map((l) => `<div class="row"><a href="#" data-label="${esc(l.label)}" class="grow">${esc(l.label)}</a><span class="muted">${l.passed}/${l.runs} runs passed · ${ago(l.at)}</span></div>`).join("") || '<span class="muted">No eval runs yet.</span>'}</div>
        ${evals.labels.length > 1 ? `<div class="row" style="margin-top:8px"><select id="cmp-a" style="width:auto">${evals.labels.map((l) => `<option>${esc(l.label)}</option>`).join("")}</select>→<select id="cmp-b" style="width:auto">${evals.labels.map((l, i) => `<option ${i === 1 ? "selected" : ""}>${esc(l.label)}</option>`).join("")}</select><button class="btn" id="cmp-go">Compare</button></div>` : ""}<div id="ev-out"></div></div></div>
      <div class="grid"><div class="card"><h2>Agents</h2><div class="table-wrap"><table><tr><th>Agent</th><th class="num">Tasks</th><th class="num">Done</th><th class="num">Rework</th><th class="num">Reliable</th><th class="num">Goals passed</th><th class="num">Cost</th><th class="num" title="Weighted Agent Score 0-100: completion, quality, reliability, efficiency minus latency and cost; weights by role">Score</th></tr>${r.agents.map((a) => `<tr><td><a href="#" data-agent="${a.agent_id}">${esc(a.agent)}</a></td><td class="num">${a.tasks}</td><td class="num">${pct(a.completion_rate)}</td><td class="num">${pct(a.rework_rate)}</td><td class="num">${pct(a.reliability)}</td><td class="num">${pct(a.goal_pass_rate)}</td><td class="num">${money(a.cost)}</td><td class="num">${a.score ?? "–"}</td></tr>`).join("") || '<tr><td colspan="8" class="muted">No agent has worked yet.</td></tr>'}</table></div></div>
      <div class="card"><h2>Certification</h2><p class="muted">L0 not evaluated · L2 simple tasks · L3 ordinary tasks alone · L4 advanced · L5 expert across demanding evaluations. Levels need repeated evidence and drop after ${90} days without new evidence.</p><div class="table-wrap"><table><tr><th>Agent</th><th>Skill family</th><th>Level</th><th class="num">Evidence</th></tr>${certs.filter((x) => x.level > 0).map((x) => `<tr><td>${esc(x.agent)}</td><td>${esc(x.skill_family)}</td><td><span class="pill ${x.level >= 3 ? "done" : "info"}" title="${esc(x.meaning)}">L${x.level}</span>${x.expired ? ' <span class="pill pending">expired</span>' : ""}</td><td class="num">${x.evidence.passed}/${x.evidence.outcomes} passed · ${x.evidence.goals} goals</td></tr>`).join("") || '<tr><td colspan="4" class="muted">No evidence yet.</td></tr>'}</table></div></div>
      <div class="card"><h2>Recruitment effectiveness</h2><p class="muted">Recruits that finished probation: ${rec.passed}/${rec.finished} passed (${pct(rec.pass_rate)}). A recruit passes after 3 verified tasks and is retired after 2 failed ones.${rec.qualification_runs ? ` Qualification runs: ${rec.qualified}/${rec.qualification_runs} passed; evaluation cost $${(rec.evaluation_cost_usd || 0).toFixed(2)}.` : ""}</p><div class="table-wrap"><table><tr><th>Recruit</th><th>Role</th><th>Probation</th><th class="num">Passed</th><th class="num">Failed</th></tr>${rec.recruits.map((x) => `<tr><td><a href="#" data-agent="${x.agent_id}">${esc(x.agent)}</a></td><td>${esc(x.role)}</td><td>${esc(x.state)}</td><td class="num">${x.passed_tasks ?? "–"}</td><td class="num">${x.failed_tasks}</td></tr>`).join("") || '<tr><td colspan="5" class="muted">No recruit yet.</td></tr>'}</table></div></div></div></div>`;
  document.querySelectorAll("[data-goal]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); goalModal(Number(a.dataset.goal)); }));
  kp && kpisWire(kp);
  document.querySelectorAll("[data-agent]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); agentModal(Number(a.dataset.agent)); }));
  bindDefectActions(root, () => (current === "evaluation" ? viewEvaluation() : viewPerformance()));
  document.querySelectorAll("[data-label]").forEach((a) => (a.onclick = async (e) => { e.preventDefault(); evalSummary(await api(`/api/evals/${encodeURIComponent(a.dataset.label)}`)); }));
  $("#cmp-go") && ($("#cmp-go").onclick = async () => {
    const r2 = await api(`/api/evals/compare?a=${encodeURIComponent($("#cmp-a").value)}&b=${encodeURIComponent($("#cmp-b").value)}`);
    $("#ev-out").innerHTML = `<h3>${esc(r2.a.label)} → ${esc(r2.b.label)}: pass rate ${pct(r2.a.pass_rate)} → ${pct(r2.b.pass_rate)}, cost ${r2.cost_change >= 0 ? "+" : ""}${money(r2.cost_change)}</h3><table><tr><th>Dimension</th><th>Metric</th><th class="num">${esc(r2.a.label)}</th><th class="num">${esc(r2.b.label)}</th><th class="num">Change</th></tr>${Object.entries(r2.dimensions || {}).map(([d, x]) => `<tr><td>${esc(d.replace("_", " "))}</td><td class="muted">${esc(x.metric.replace(/_/g, " "))}</td><td class="num">${fmtDim(x[r2.a.label])} <span class="muted">n=${x.n[r2.a.label]}</span></td><td class="num">${fmtDim(x[r2.b.label])} <span class="muted">n=${x.n[r2.b.label]}</span></td><td class="num">${x.change == null ? "–" : (x.change > 0 ? "+" : "") + x.change}</td></tr>`).join("")}</table><table>${r2.tasks.map((t) => `<tr><td>${esc(t.task)}</td><td class="num">${t[r2.a.label] ?? "–"}</td><td class="num">${t[r2.b.label] ?? "–"}</td><td class="num">${t.change > 0 ? "+" : ""}${t.change}</td></tr>`).join("")}</table>`;
  });
  $("#ev-form") && ($("#ev-form").onsubmit = async (e) => { e.preventDefault(); try { await api("/api/evals/run", { method: "POST", body: { suite: $("#ev-suite").value, label: $("#ev-label").value, repeats: +$("#ev-rep").value } }); toast("Eval started; results appear as runs finish"); } catch (err) { toast(err.message); } });
}

function evalSummary(s) {
  $("#ev-out").innerHTML = `<h3>${esc(s.label)} (${esc(s.suite)}): ${s.solved}/${s.tasks} solved, ${pct(s.pass_rate)} of ${s.runs} runs, ${money(s.total_cost)}</h3>${dimTable(s.dimensions)}<table><tr><th>Task</th><th class="num">Passed</th><th class="num" title="Runs without scope, boundary or contract violations">Compliant</th><th class="num" title="Runs without a failed security check, secret or refused command">Secure</th><th class="num">Iterations</th><th class="num">Seconds</th><th>Ended</th></tr>${s.by_task.map((t) => `<tr><td>${esc(t.task)}${t.solved ? " " + pill("done") : ""}</td><td class="num">${t.passed}/${t.runs}</td><td class="num">${t.compliant ?? "–"}</td><td class="num">${t.secure ?? "–"}</td><td class="num">${t.avg_iterations}</td><td class="num">${t.avg_seconds}</td><td>${esc(t.end_reasons.join(", "))}</td></tr>`).join("")}</table>`;
}

const fmtDim = (v) => (v == null ? "–" : v);

// S09-R-05: each dimension on its own row with its sample size; no combined score
function dimTable(d) {
  if (!d) return "";
  const rate = (v) => (v == null ? "–" : pct(v));
  const rows = [
    ["Task completion", d.completion, `${rate(d.completion.pass_rate)} passed`],
    ["Contract compliance", d.compliance, `${rate(d.compliance.compliant_rate)} compliant · ${d.compliance.violations} scope/boundary violations · ${d.compliance.contract_failures} failed contracts`],
    ["Tool use", d.tool_use, `${rate(d.tool_use.accuracy)} of ${d.tool_use.tool_calls} tool calls ok · ${d.tool_use.denied_tools} denied · ${d.tool_use.refused_commands} refused commands · ${d.tool_use.failed_runs} failed runs`],
    ["Reliability", d.reliability, `${rate(d.reliability.consistent_rate)} consistent · solved in every repeat ${rate(d.reliability.pass_all_rate)} · in any ${rate(d.reliability.pass_any_rate)} (${d.reliability.repeats} repeats)`],
    ["Security behaviour", d.security, `${rate(d.security.secure_rate)} secure · ${d.security.failed_checks} failed security checks · ${d.security.secrets_found} with secrets · ${d.security.refused_commands} refused commands`],
    ["Resource efficiency", d.efficiency, `${d.efficiency.avg_seconds}s · ${money(d.efficiency.avg_cost)} · ${d.efficiency.avg_tokens} tokens · ${d.efficiency.avg_iterations} iterations per run${d.efficiency.cost_per_passed == null ? "" : ` · ${money(d.efficiency.cost_per_passed)} per pass`}`],
  ];
  return `<table><tr><th>Dimension</th><th class="num">n</th><th>Result</th></tr>${rows.map(([name, x, text]) => `<tr><td>${name}</td><td class="num">${x.n}</td><td>${esc(text)}</td></tr>`).join("")}</table>`;
}

// Its content is the Evaluation & QA page's Performance tab; the route stays for old links, off the sidebar.
registerView({ id: "performance", title: "Performance", icon: "performance", nav: false }, viewPerformance, 5);
