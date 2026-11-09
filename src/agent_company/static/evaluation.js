// Evaluation & QA (AICO UI-QA-01…04): what the quality gate recorded — executions passed and failed per day, pass
// rate, stages, flaky checks (the same commit passed and failed), defects by priority — the latest evaluation of each
// goal by area (functional, integration, security, final acceptance, static), and the Performance tab (defects with
// their lifecycle, rooms, KPIs, operations, eval harness and benchmarks, agents, certification; performance.js).
let qaTab = store.get("ac.qa.tab") || "overview", qaPeriod = store.get("ac.qa.period") || "7d";
const QA_AREAS = [["functional", "Functional"], ["integration", "Integration"], ["security", "Security"], ["acceptance", "Final acceptance"], ["static", "Static"]];

function qaBars(days) {  // passed vs failed executions per day, stacked; the pass rate in each bar's title
  if (!days.length) return gmEmpty("No gate ran in this period.");
  const max = Math.max(1, ...days.map((d) => d.passed + d.failed)), W = 28, G = 10, H = 140;
  return `<div class="qa-chart"><svg width="${days.length * (W + G)}" height="${H + 22}" role="img" aria-label="Checks passed and failed per day">${days.map((d, i) => {
    const hp = (d.passed / max) * H, hf = (d.failed / max) * H, x = i * (W + G), rate = d.passed + d.failed ? Math.round((d.passed / (d.passed + d.failed)) * 100) : 0;
    return `<g><title>${d.day}: ${d.passed} passed, ${d.failed} failed (${rate}%) · ${d.gates_passed}/${d.gates} gates passed</title><rect x="${x}" y="${H - hp - hf}" width="${W}" height="${hf}" rx="3" fill="var(--bad)"/><rect x="${x}" y="${H - hp}" width="${W}" height="${hp}" rx="3" fill="var(--ok)"/><text x="${x + W / 2}" y="${H + 16}" text-anchor="middle" class="qa-axis">${d.day.slice(5)}</text></g>`; }).join("")}</svg></div>
    <div class="row wf-legend"><span class="badge ok">passed</span><span class="badge bad">failed</span><span class="muted dash-meta">per day; hover a bar for its pass rate</span></div>`;
}

async function viewEvaluation(quiet) {
  if (!quiet || !$("#qa")) {
    $("#view").innerHTML = `<div id="qa"><div class="page-head"><div class="page-head-text"><h2 class="page-title">Evaluation &amp; QA</h2><p class="muted page-sub">What the quality gate verified: checks, reviews, defects and benchmarks. Nothing here comes from an agent's own report.</p></div>
      <div class="page-actions"><select id="qa-period" aria-label="Period">${Object.entries(DASH_PERIODS).map(([k, l]) => `<option value="${k}"${k === qaPeriod ? " selected" : ""}>${l}</option>`).join("")}</select></div></div>
      <div class="ag-tabs" role="tablist">${[["overview", "Overview", "dashboard"], ["goals", "Latest evaluation", "check"], ["performance", "Performance & benchmarks", "performance"]].map(([k, l, i]) => `<button role="tab" data-qat="${k}" aria-selected="${qaTab === k}">${icon(i, 16)} ${l}</button>`).join("")}</div><div id="qa-body" class="qa-body"></div></div>`;
    $("#qa-period").onchange = (e) => { qaPeriod = e.target.value; store.set("ac.qa.period", qaPeriod); $("#qa-body").dataset.html = ""; viewEvaluation(true); };
    $("#view").querySelectorAll("[data-qat]").forEach((b) => (b.onclick = () => { qaTab = b.dataset.qat; store.set("ac.qa.tab", qaTab); viewEvaluation(); }));
  }
  const box = $("#qa-body");
  if (qaTab === "performance") { $("#qa-period").hidden = true; return viewPerformance(quiet, box); }
  $("#qa-period").hidden = false;
  const r = await api(`/api/qa?period=${qaPeriod}`), x = r.executions;
  const pct = (a, b) => (b ? Math.round((a / b) * 100) + "%" : "–");
  const html = qaTab === "goals" ? (r.goals.length ? `<div class="card"><div class="table-wrap"><table class="qa-table"><tr><th>Goal</th><th>Gate</th>${QA_AREAS.map(([, l]) => `<th>${l}</th>`).join("")}<th>Status</th><th>When</th></tr>
      ${r.goals.map((g) => `<tr><td><a href="#/goals/${g.goal_id}">#${g.goal_id} ${esc(g.title)}</a><div class="muted dash-meta">iteration ${g.iteration ?? "–"}${g.commit ? ` · <span class="mono">${esc(String(g.commit).slice(0, 10))}</span>` : ""}</div></td><td><span class="badge ${g.passed ? "ok" : "bad"}">${g.passed ? "passed" : "failed"}</span></td>
        ${QA_AREAS.map(([k]) => { const a = g.areas[k]; return `<td>${a ? `<span class="badge ${a.failed ? "bad" : "ok"}" title="${esc(a.failing.map((f) => `${f.check}: ${f.detail}`).join("\n"))}">${a.passed}/${a.passed + a.failed}</span>` : '<span class="muted">–</span>'}</td>`; }).join("")}
        <td>${pill(g.status)}</td><td class="muted dash-meta">${ago(g.at)}</td></tr>${g.passed ? "" : `<tr class="qa-fail"><td colspan="${QA_AREAS.length + 4}">${Object.values(g.areas).flatMap((a) => a.failing).slice(0, 6).map((f) => `<div class="dash-meta"><span class="badge bad">failed</span> <strong>${esc(f.check)}</strong> <span class="muted">${esc(f.detail)}</span></div>`).join("")}</td></tr>`}`).join("")}</table></div>
      <p class="muted dash-meta">Functional = acceptance checks and unit tests; Integration = integration-stage commands; Security = secrets, dependency audit and security checks; Final acceptance = review verdicts. A cell is passed / run.</p></div>`
      : `<div class="card empty"><p class="empty-title">No gate ran in this period</p></div>`)
    : `<div class="grid dash-kpis">${[["check", x.total, "Test executions"], ["check", x.passed, "Passed"], ["emergency", x.failed, "Failed"], ["pause", x.blocked, "Blocked tasks"], ["goals", x.pending, "Goals pending a gate"], ["performance", pct(r.gates.passed, r.gates.total), `Gate pass rate (${r.gates.passed}/${r.gates.total})`]]
        .map(([i, v, l]) => `<div class="card kpi"><span class="kpi-icon">${icon(i, 18)}</span><div class="value">${v}</div><div class="label">${l}</div></div>`).join("")}</div>
      <div class="grid dash-row"><section class="card"><div class="card-head"><h3>Passed vs failed</h3></div>${qaBars(r.days)}</section>
        <section class="card"><div class="card-head"><h3>By stage</h3></div>${r.stages.length ? `<div class="dash-bars">${r.stages.map((s) => { const n = s.passed + s.failed; return `<div class="dash-bar"><span>${esc(s.stage)}</span><div class="dash-bar-track" title="${s.passed} passed · ${s.failed} failed"><span class="ok" style="width:${(s.passed / Math.max(1, n)) * 100}%"></span><span class="bad" style="width:${(s.failed / Math.max(1, n)) * 100}%"></span></div><strong>${pct(s.passed, n)}</strong></div>`; }).join("")}</div>` : gmEmpty("No checks ran.")}</section>
        <section class="card"><div class="card-head"><h3>Flaky checks</h3></div>${r.flaky.length ? `<div class="gm-team">${r.flaky.map((f) => `<span class="badge warn">${esc(f)}</span>`).join("")}</div><p class="muted dash-meta">Each passed and failed on the same commit of a goal.</p>` : gmEmpty("None: no check changed its result on the same commit.")}
          <div class="card-head" style="margin-top:16px"><h3>Open defects</h3><a class="view-all" href="#" data-qat2="performance">Manage →</a></div>${r.defects.open ? `<div class="gm-team">${Object.entries(r.defects.by_priority).map(([p, n]) => `<span class="badge ${p === "P1" ? "bad" : p === "P2" ? "warn" : "neutral"}">${p}: ${n}</span>`).join("")}</div>` : gmEmpty("No open defect.")}</section></div>`;
  if (box.dataset.html === html) return;
  box.innerHTML = html; box.dataset.html = html; iconize(box);
  box.querySelectorAll("[data-qat2]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); qaTab = a.dataset.qat2; store.set("ac.qa.tab", qaTab); viewEvaluation(); }));
}
registerView({ id: "evaluation", title: "Evaluation & QA", icon: "check" }, viewEvaluation);
