// Agent Evolution and Organization Evolution (AICO UI-EVO-01…04, UI-ORG-01…04).
// Agent Evolution: every candidate change of an agent — a definition rewrite from a study visit (agentdefs) or an
// experiment (experiments.py: playbook, route, strategy, retrieval) — with where it stands on Baseline → Candidate →
// Experiment → Regression/safety → Human approval → Deploy/rollback, its measured comparison (sample sizes,
// regressions; "not measured" otherwise) and Apply/Discard where a person decides; the former Evolution page is the
// tab "Learning & experiments". Organization Evolution: the versions organization.py records at each structural
// change, two of them compared side by side, and Restore (administrators; rooms only, no agent is deleted).
let evoTab = store.get("ac.evo.tab") || "candidates", orgA = null, orgB = null;
const EVO_STEPS = ["Baseline", "Candidate", "Experiment", "Regression / safety", "Human approval", "Deploy / rollback"];

// `skip`: steps this kind of change does not run (shown as "not run", never as passed)
function evoStepper(at, failed = -1, skip = []) {
  return `<ol class="stepper evo-steps">${EVO_STEPS.map((s, i) => `<li class="${skip.includes(i) ? "skip" : i === failed ? "failed" : i < at || at >= EVO_STEPS.length ? "done" : i === at ? "current" : ""}">${s}${skip.includes(i) ? "<small>not run</small>" : ""}</li>`).join("")}</ol>`;
}
// where an experiment stands; a regression or a rejection marks its step failed
function expStep(e) {
  const reg = /regress/i.test(e.decision || "");
  return { proposed: [1], running: [2], review: [4], canary: [5], rolled_out: [6], applied: [6], rolled_back: [5, 5],
    rejected: reg ? [3, 3] : [4, 4] }[e.status] || [1];
}
function candStep(c) { return { pending: [4], applied: [6], discarded: [4, 4], superseded: [4, 4] }[c.status] || [1]; }
function expFigures(e) {
  const b = e.results?.baseline, c = e.results?.candidate, pct = (v) => (v == null ? "not measured" : Math.round(v * 100) + "%");
  if (!b && !c) return '<p class="muted dash-meta">Not measured yet.</p>';
  const row = (l, f) => `<tr><td>${l}</td><td class="num">${b ? f(b) : "–"}</td><td class="num">${c ? f(c) : "–"}</td></tr>`;
  const regs = e.results?.comparison?.regressions || e.results?.regressions;
  return `<table class="evo-cmp"><tr><th></th><th class="num">Baseline</th><th class="num">Candidate</th></tr>${row("Pass rate", (x) => `${pct(x.pass_rate)} <span class="muted">n=${x.runs ?? "?"}</span>`)}${row("Cost per pass", (x) => (x.cost_per_passed == null ? "–" : money(x.cost_per_passed)))}${row("Avg seconds", (x) => x.avg_seconds ?? "–")}${row("Tool-call accuracy", (x) => pct(x.dimensions?.tool_use?.accuracy))}</table>
    ${Array.isArray(regs) ? (regs.length ? `<p class="dash-meta"><span class="badge bad">regressions</span> ${esc(regs.join(", "))}</p>` : '<p class="dash-meta"><span class="badge ok">no regression</span></p>') : ""}`;
}

async function viewAgentEvolution(quiet) {
  if (!quiet || !$("#evo")) {
    $("#view").innerHTML = `<div id="evo"><div class="page-head"><div class="page-head-text"><h2 class="page-title">Agent Evolution</h2><p class="muted page-sub">How agents change: each candidate is compared with its baseline and reaches the company only after a person approves it.</p></div></div>
      <div class="ag-tabs" role="tablist">${[["candidates", "Candidates", "evolution"], ["learning", "Learning & experiments", "book"]].map(([k, l, i]) => `<button role="tab" data-evt="${k}" aria-selected="${evoTab === k}">${icon(i, 16)} ${l}</button>`).join("")}</div><div id="evo-body" class="qa-body"></div></div>`;
    $("#view").querySelectorAll("[data-evt]").forEach((b) => (b.onclick = () => { evoTab = b.dataset.evt; store.set("ac.evo.tab", evoTab); viewAgentEvolution(); }));
  }
  const box = $("#evo-body");
  if (evoTab === "learning") { if (quiet && box.dataset.tab === "learning") return; box.dataset.tab = "learning"; box.dataset.html = ""; return viewEvolution(quiet, box); }
  box.dataset.tab = "candidates";
  const manage = me.is_admin || me.permissions?.manage_agents;
  const [cands, ex, agents] = await Promise.all([manage ? api("/api/definition-candidates").catch(() => []) : [], api("/api/experiments").catch(() => ({ experiments: [] })), api("/api/agents")]);
  const names = Object.fromEntries(agents.map((a) => [a.id, a]));
  const open = cands.filter((c) => c.status === "pending"), done = cands.filter((c) => c.status !== "pending").slice(0, 10);
  const exps = ex.experiments.slice(0, 30);
  const cCard = (c) => { const [at, f] = candStep(c), a = names[c.agent_id];
    return `<section class="card evo-card"><div class="row nowrap"><span class="avatar">${roleIcon(a?.role)}</span><div class="grow" style="min-width:0"><strong>${esc(a?.name || `agent #${c.agent_id}`)}</strong><div class="muted dash-meta">Definition rewrite #${c.id} · v${c.base_version} → v${c.base_version + 1} · ${esc(c.model)} · ${ago(c.created_at)}</div></div>${pill(c.status)}</div>
      ${evoStepper(at, f ?? -1, [2, 3])}<p class="dash-meta" style="margin:0">${esc(c.reason || "")}${c.note ? ` · ${esc(c.note)}` : ""}</p><p class="muted dash-meta" style="margin:0">Sections: ${esc(Object.keys(c.sections || {}).join(", ") || "–")}. A study rewrite runs no experiment: its diff is the evidence a person reviews.</p>
      ${c.status === "pending" && manage ? `<div class="row nowrap"><button class="btn sm primary" data-cand="${c.id}">Review diff</button><a class="btn sm" href="#/agents/${c.agent_id}">Agent</a></div>` : ""}</section>`; };
  const eCard = (e) => { const [at, f] = expStep(e);
    return `<section class="card evo-card"><div class="row nowrap"><span class="kpi-icon" style="margin:0">${icon("performance", 16)}</span><div class="grow" style="min-width:0"><strong>Experiment #${e.id}: ${esc(e.target)}${e.agent ? ` · ${esc(e.agent)}` : e.role ? ` · ${esc(e.role)}` : ""}</strong><div class="muted dash-meta">${esc(e.hypothesis || "")} · ${esc(e.origin)} · ${ago(e.created_at)}</div></div>${pill(e.status)}</div>
      ${evoStepper(at, f ?? -1)}${expFigures(e)}${e.decision ? `<p class="dash-meta" style="margin:0"><strong>Decision:</strong> ${esc(e.decision)}${e.reviewed_by ? ` · ${esc(e.reviewed_by)}` : ""}</p>` : ""}</section>`; };
  const html = `${manage ? `<h3 class="team-h">Waiting for review <span class="muted">${open.length}</span></h3>${open.length ? `<div class="grid evo-grid">${open.map(cCard).join("")}</div>` : '<p class="muted">No definition rewrite waits for review.</p>'}` : ""}
    <h3 class="team-h">Experiments <span class="muted">${exps.length}</span></h3>${exps.length ? `<div class="grid evo-grid">${exps.map(eCard).join("")}</div>` : '<p class="muted">No experiment yet.</p>'}
    ${done.length ? `<h3 class="team-h">Decided rewrites</h3><div class="grid evo-grid">${done.map(cCard).join("")}</div>` : ""}`;
  if (box.dataset.html === html) return;
  box.innerHTML = html; box.dataset.html = html; iconize(box);
  box.querySelectorAll("[data-cand]").forEach((b) => (b.onclick = async () => {
    const c = await api(`/api/definition-candidates/${b.dataset.cand}`), a = names[c.agent_id];
    openDrawer(`${a?.name || "Agent"}: candidate #${c.id}`, `${evoStepper(4, -1, [2, 3])}<p class="muted dash-meta">Current definition v${c.base_version} against the file the candidate would write. ${esc(c.reason || "")}</p>${c.stale ? `<p class="diff-stale">${esc(c.stale_note)}</p>` : ""}
      <pre class="mono diff">${c.diff ? diffHtml(c.diff) : '<span class="muted">No change to the file.</span>'}</pre>
      <div class="row nowrap" style="margin-top:8px"><button class="btn primary" id="evo-apply">Apply (one commit)</button><button class="btn danger" id="evo-discard">Discard</button><a class="btn" href="#/agents/${c.agent_id}">Versions &amp; restore</a></div>`);
    $("#evo-apply").onclick = async () => { try { await api(`/api/definition-candidates/${c.id}/apply`, { method: "POST" }); toast(`Candidate #${c.id} applied`); closeDrawer(); box.dataset.html = ""; viewAgentEvolution(true); updateBadges(); } catch (e) { toast(e.message); } };
    $("#evo-discard").onclick = async () => { const note = prompt("Why discard it? (optional)", ""); if (note === null) return; try { await api(`/api/definition-candidates/${c.id}/discard`, { method: "POST", body: { note } }); toast(`Candidate #${c.id} discarded`); closeDrawer(); box.dataset.html = ""; viewAgentEvolution(true); } catch (e) { toast(e.message); } };
  }));
}

async function viewOrgEvolution(quiet) {
  const [vs, agents] = await Promise.all([api("/api/org/versions"), api("/api/agents").catch(() => [])]);
  const names = Object.fromEntries(agents.map((a) => [a.id, a.name]));
  if (orgB == null || !vs.some((v) => v.version === orgB)) orgB = vs[0]?.version ?? null;
  if (orgA == null || !vs.some((v) => v.version === orgA) || orgA === orgB) orgA = vs[1]?.version ?? orgB;
  const cmp = orgA != null && orgB != null && orgA !== orgB ? await api(`/api/org/compare?a=${orgA}&b=${orgB}`) : null;
  const val = (f, v) => (v == null ? "–" : ["lead", "superior"].includes(f) ? esc(names[v] || `#${v}`) : typeof v === "boolean" ? (v ? "yes" : "no") : esc(String(v)));
  const part = (title, p, label) => { const n = p.added.length + p.removed.length + p.changed.length;
    return `<section class="card"><div class="card-head"><h3>${title}</h3><span class="muted">${n} change(s)</span></div>${n ? `<ul class="org-diff">${p.added.map((x) => `<li class="add">+ ${esc(label(x))}</li>`).join("")}${p.removed.map((x) => `<li class="del">− ${esc(label(x))}</li>`).join("")}
      ${p.changed.map((x) => `<li class="chg">~ ${esc(x.name || x.key)}: ${Object.entries(x.fields).map(([f, [o, nv]]) => `${esc(f)} <span class="del">${val(f, o)}</span> → <span class="add">${val(f, nv)}</span>`).join("; ")}</li>`).join("")}</ul>` : gmEmpty("No difference.")}</section>`; };
  const html = `<div id="orgv"><div class="page-head"><div class="page-head-text"><h2 class="page-title">Organization Evolution</h2><p class="muted page-sub">Every structural change of the company is a version: rooms (parent, lead, enabled, budget) and agents (room, superior, enabled). Compare two; an administrator restores the rooms of an earlier one.</p></div></div>
    <div class="grid evo-org"><section class="card"><div class="card-head"><h3>Versions</h3><span class="muted">${vs.length}</span></div><ol class="timeline">${vs.map((v) => `<li class="${v.reason.startsWith("restored") ? "warn" : "info"}"><div class="row nowrap"><strong class="grow">v${v.version}</strong>${me.is_admin && v.version !== vs[0].version ? `<button class="btn sm" data-restore="${v.version}">Restore</button>` : ""}<button class="btn sm${v.version === orgA ? " on" : ""}" data-a="${v.version}" title="Compare from">A</button><button class="btn sm${v.version === orgB ? " on" : ""}" data-b="${v.version}" title="Compare to">B</button></div><div class="dash-meta">${esc(v.reason)}</div><time>${esc(v.actor)} · ${ago(v.at)} · ${v.rooms} rooms · ${v.agents} agents</time></li>`).join("")}</ol></section>
      <div><h3 class="team-h">${cmp ? `v${orgA} → v${orgB}` : "Pick two versions (A and B) to compare"}</h3>${cmp ? `<div class="grid evo-grid">${part("Rooms", cmp.rooms, (x) => `${x.name || x.key} (${x.key})`)}${part("Agents", cmp.agents, (x) => `${x.name} · ${x.role || ""} in ${x.room || "–"}`)}</div>` : ""}
      <p class="muted dash-meta">Proposals to change the structure come from the General Manager and agents as proposals and are applied only by a person (Rooms on Teams &amp; Agents).</p></div></div></div>`;
  const view = $("#view");
  if ($("#orgv")?.dataset.html === html) return;
  view.innerHTML = html; $("#orgv").dataset.html = html; iconize(view);
  view.querySelectorAll("[data-a]").forEach((b) => (b.onclick = () => { orgA = Number(b.dataset.a); $("#orgv").dataset.html = ""; viewOrgEvolution(true); }));
  view.querySelectorAll("[data-b]").forEach((b) => (b.onclick = () => { orgB = Number(b.dataset.b); $("#orgv").dataset.html = ""; viewOrgEvolution(true); }));
  view.querySelectorAll("[data-restore]").forEach((b) => (b.onclick = async () => {
    if (!confirm(`Restore the rooms of version ${b.dataset.restore}? Parents, leads, switches and budgets are set back; no agent is deleted. The result is a new version.`)) return;
    try { const r = await api(`/api/org/versions/${b.dataset.restore}/restore`, { method: "POST" }); toast(`Restored: ${r.changed.length} room(s) changed${r.skipped.length ? `, ${r.skipped.length} skipped` : ""}`); $("#orgv").dataset.html = ""; viewOrgEvolution(true); } catch (e) { toast(e.message); }
  }));
}
registerView({ id: "evolution", title: "Agent Evolution", icon: "evolution" }, viewAgentEvolution);
registerView({ id: "organization", title: "Organization Evolution", icon: "sitemap" }, viewOrgEvolution);
