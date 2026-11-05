"use strict";
// Knowledge (SPEC-15 S15-R-02, SPEC-11): knowledge items with provenance, the review queue, contradicting
// pairs side by side. People review here; agents never do (S11-R-05). The graph explorer walks `kg_edges`
// around one entity (S11-R-07): click a node to centre on it.

const K_STATUSES = [["", "every status"], ["approved", "approved"], ["draft,quarantined,rejected", "review queue"], ["superseded", "superseded"], ["expired", "expired"]];
const K_KINDS = ["", "episodic", "negative", "semantic", "procedural", "architectural", "research", "operational", "capability", "organizational"];
const kState = { q: "", status: "", kind: "" };

// A source reference (goal:12, task:5, commit:abc, knowledge:3, url:…) as a link where there is somewhere to go.
function kSource(ref) {
  const [type, ...rest] = String(ref).split(":"), val = rest.join(":");
  if (type === "goal") return `<a href="#" data-kgoal="${Number(val)}">goal #${Number(val)}</a>`;
  if (type === "knowledge") return `<a href="#" data-kitem="${Number(val)}">K${Number(val)}</a>`;
  if (type === "commit") return `<code title="${esc(val)}">${esc(val.slice(0, 10))}</code>`;
  if (type === "url" || /^https?$/.test(type)) { const u = type === "url" ? val : ref; return `<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(u.replace(/^https?:\/\//, "").slice(0, 40))}</a>`; }
  return `<span class="muted">${esc(ref)}</span>`;
}
const kStatusPill = (s) => `<span class="pill ${s === "approved" ? "done" : s === "quarantined" || s === "rejected" ? "failed" : "pending"}">${esc(s)}</span>`;

// The structured experience report (S11-R-02): observed facts from the harness, the teacher's approach and limits.
function kReport(i) {
  const r = i.report || {};
  if (!Object.keys(r).length) return "";
  const list = (xs) => (xs || []).length ? `<ul style="margin:2px 0 4px 18px;padding:0">${xs.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : "";
  const line = (label, v) => v ? `<div><span class="muted">${label}:</span> ${esc(v)}</div>` : "";
  return `<details class="muted" style="margin-top:4px"><summary>Experience report · ${esc(r.outcome || "?")}</summary>
    ${line("Objective", r.objective)}${(r.actions || []).length ? `<div class="muted">Actions:</div>${list(r.actions)}` : ""}
    ${(r.failures || []).length ? `<div class="muted">Failures:</div>${list(r.failures)}` : ""}${line("What worked", r.successful_approach)}
    ${line("Limitations", r.limitations)}${(r.context_refs || []).length ? `<div><span class="muted">Context:</span> ${r.context_refs.map(kSource).join(" · ")}</div>` : ""}
    ${(r.evidence_refs || []).length ? `<div><span class="muted">Evidence:</span> ${r.evidence_refs.map(kSource).join(" · ")}</div>` : ""}</details>`;
}

function kCard(i, extra = "") {
  return `<div class="card" style="margin:0;padding:8px 10px"><div class="row"><strong>K${i.id}</strong>${kStatusPill(i.status)}<span class="pill">${esc(i.kind)}</span><span class="pill" title="evidence class">${esc(i.evidence_class)}</span>${i.version > 1 ? `<span class="pill info">v${i.version}</span>` : ""}<span class="grow"></span><span class="muted">${esc(i.agent || "?")} · ${ago(i.at)}</span></div>
    <div style="margin:6px 0">${esc(i.text)}</div>
    <div class="muted">${(i.sources || []).map(kSource).join(" · ") || "no source"}${(i.technologies || []).length ? ` · ${i.technologies.map((t) => `<span class="pill">${esc(t)}</span>`).join(" ")}` : ""} · ${esc(i.classification)}</div>
    ${i.status_reason ? `<div class="muted">${esc(i.status_reason)}${i.reviewed_by ? ` (${esc(i.reviewed_by)})` : ""}</div>` : ""}${kReport(i)}${extra}</div>`;
}

async function viewKnowledge() {
  const qs = new URLSearchParams({ q: kState.q, status: kState.status, kind: kState.kind, limit: "300" });
  const [items, pairs, queue, hyps, rs] = await Promise.all([api(`/api/knowledge?${qs}`), api("/api/knowledge/contradictions"), api("/api/knowledge?status=draft,quarantined,rejected&limit=50"), api("/api/hypotheses"), api("/api/knowledge/retrieval").catch(() => null)]);
  const actions = (i) => [
    i.status !== "approved" && i.status !== "quarantined" && i.status !== "superseded" ? ib("check", "Approve", `data-kok="${i.id}"`, "sm primary") : "",
    i.status === "approved" ? ib("x", "Reject", `data-kno="${i.id}"`, "sm") : "",
    i.status !== "superseded" && i.status !== "quarantined" ? ib("edit", "Write a new version (supersedes this one)", `data-ksup="${i.id}"`, "sm") : "",
    ib("sitemap", "Show its graph", `data-kgraph="knowledge:${i.id}"`, "sm"),
    ib("trash", "Delete the item and its links", `data-kdel="${i.id}"`, "sm danger")].join("");
  $("#view").innerHTML = `<div class="card"><form id="k-search" class="row"><input id="k-q" class="grow" placeholder="Search the text or a technology…" value="${esc(kState.q)}">
      <select id="k-status" style="width:auto">${K_STATUSES.map(([v, l]) => `<option value="${v}" ${kState.status === v ? "selected" : ""}>${l}</option>`).join("")}</select>
      <select id="k-kind" style="width:auto">${K_KINDS.map((v) => `<option value="${v}" ${kState.kind === v ? "selected" : ""}>${v || "every kind"}</option>`).join("")}</select><button class="btn">Search</button></form>
      <form id="k-gform" class="row" style="margin-top:6px"><input id="k-gent" class="grow" placeholder="Graph around an entity: agent:3, goal:12, knowledge:5, technology:python, skill:testing…"><button class="btn">Graph</button></form>
      <p class="muted" style="margin:6px 0 0">Lessons agents learned, with the goal, task and commit they came from. Screened before storage (credential-like text is quarantined, text that instructs agents rejected); only approved, current items reach prompts.</p></div>
    <div class="grid halves" style="margin-top:12px"><div class="grid">
      <div class="card"><h2>Contradictions <span class="muted">${pairs.length} open</span></h2><p class="muted">Two items say opposite things about the same subject. Both are kept and rank lower until you decide; the rationale is recorded.</p>
        ${pairs.map((p) => `<div style="margin:10px 0"><div class="grid two">${p.old ? kCard(p.old) : '<span class="muted">(deleted)</span>'}${p.new ? kCard(p.new) : '<span class="muted">(deleted)</span>'}</div>
          ${p.judge ? `<div class="muted" style="margin-top:6px" title="${esc(p.judge.model || "")}">Judge (${esc(p.judge.agent || "instructor")}): <b>${esc(p.judge.verdict)}</b>${p.judge.reason ? " — " + esc(p.judge.reason) : ""}</div>` : ""}
          <div class="row" style="margin-top:6px"><span class="muted grow">#${p.id} · found ${ago(p.at)}</span><button class="btn sm keep-text" data-kres="${p.id}" data-how="keep_old">Older holds</button><button class="btn sm keep-text" data-kres="${p.id}" data-how="keep_both">Both hold</button><button class="btn sm keep-text" data-kres="${p.id}" data-how="keep_new">Newer holds</button></div></div>`).join("") || '<span class="muted">No open contradictions.</span>'}</div>
      <div class="card"><h2>Review queue <span class="muted">${queue.length}</span></h2><p class="muted">Drafts wait for approval; quarantined items hold credential-like text (delete them); rejected items stay out of prompts.</p>
        <div class="grid">${queue.map((i) => kCard(i, `<div class="row" style="margin-top:6px">${actions(i)}</div>`)).join("") || '<span class="muted">Nothing to review.</span>'}</div></div>
      ${hypCard(hyps)}${rs ? rankCard(rs) : ""}</div>
      <div class="card"><h2>Items <span class="muted">${items.length}${items.length >= 300 ? "+" : ""}</span></h2><div class="table-wrap"><table><tr><th>#</th><th>Item</th><th>Status</th><th>Source</th><th></th></tr>
        ${items.map((i) => `<tr><td class="num">K${i.id}${i.version > 1 ? `<div class="muted">v${i.version}</div>` : ""}</td><td>${esc(i.text)}<div class="muted"><span class="pill">${esc(i.kind)}</span> ${esc(i.evidence_class)} · ${esc(i.agent || "?")} · ${ago(i.at)}${(i.technologies || []).length ? " · " + esc(i.technologies.join(", ")) : ""}</div>${i.contradicted_by.length ? `<div class="pill pending" title="an open contradiction">disputed by ${i.contradicted_by.map((c) => "K" + c).join(", ")}</div>` : ""}${kReport(i)}</td>
          <td>${kStatusPill(i.status)}<div class="muted">${esc(i.classification)}</div></td><td class="muted">${(i.sources || []).map(kSource).join("<br>")}</td><td class="nowrap">${actions(i)}</td></tr>`).join("") || '<tr><td colspan="5" class="muted">No items match.</td></tr>'}</table></div></div></div>`;
  $("#k-gform").onsubmit = (e) => { e.preventDefault(); const ent = $("#k-gent").value.trim(); if (ent) kGraph(ent); };
  $("#k-search").onsubmit = (e) => { e.preventDefault(); kState.q = $("#k-q").value.trim(); kState.status = $("#k-status").value; kState.kind = $("#k-kind").value; viewKnowledge(); };
  ["#k-status", "#k-kind"].forEach((sel) => ($(sel).onchange = () => $("#k-search").requestSubmit()));
  const call = async (path, opts) => { try { await api(path, opts); viewKnowledge(); } catch (err) { toast(err.message); } };
  const v = $("#view");
  v.querySelectorAll("[data-kgoal]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); goalModal(Number(a.dataset.kgoal)); }));
  v.querySelectorAll("[data-kitem]").forEach((a) => (a.onclick = async (e) => { e.preventDefault(); try { modal(kCard(await api(`/api/knowledge/${a.dataset.kitem}`))); } catch (err) { toast(err.message); } }));
  v.querySelectorAll("[data-kok]").forEach((b) => (b.onclick = () => { const note = prompt("Note (optional)"); if (note !== null) call(`/api/knowledge/${b.dataset.kok}/review`, { method: "POST", body: { approve: true, note } }); }));
  v.querySelectorAll("[data-kno]").forEach((b) => (b.onclick = () => { const note = prompt("Why reject it?"); if (note) call(`/api/knowledge/${b.dataset.kno}/review`, { method: "POST", body: { approve: false, note } }); }));
  v.querySelectorAll("[data-kgraph]").forEach((b) => (b.onclick = () => kGraph(b.dataset.kgraph)));
  v.querySelectorAll("[data-kdel]").forEach((b) => (b.onclick = () => confirm(`Delete K${b.dataset.kdel} and its links? This cannot be undone.`) && call(`/api/knowledge/${b.dataset.kdel}`, { method: "DELETE" })));
  v.querySelectorAll("[data-ksup]").forEach((b) => (b.onclick = () => {
    const i = [...items, ...queue].find((x) => x.id === Number(b.dataset.ksup));
    modal(`<h2>New version of K${i.id}</h2><p class="muted">The old version stays as superseded; the new one keeps its provenance and is screened like any lesson.</p><form id="ksf"><label for="ksf-t">Text</label><textarea id="ksf-t" rows="4" maxlength="1000" required>${esc(i.text)}</textarea><label for="ksf-n">Why (optional)</label><input id="ksf-n" maxlength="1000">
      <div class="row" style="margin-top:12px"><button class="btn icon primary" title="Save" aria-label="Save">${icon("save", 16)}</button></div></form>`);
    $("#ksf").onsubmit = async (e) => { e.preventDefault(); try { await api(`/api/knowledge/${i.id}/supersede`, { method: "POST", body: { text: $("#ksf-t").value, note: $("#ksf-n").value } }); $("#modal").hidden = true; viewKnowledge(); } catch (err) { toast(err.message); } };
  }));
  hypBind(v, call);
  const rl = v.querySelector("#k-rlearn");
  if (rl) rl.onclick = async () => { rl.classList.add("busy"); try { const r = await api("/api/knowledge/retrieval/learn", { method: "POST" }); toast(Object.entries(r.applied).map(([c, s]) => `${c}=${s}`).join(", ") || "balanced for every class"); } catch (err) { toast(err.message); } viewKnowledge(); };
  v.querySelectorAll("[data-kres]").forEach((b) => (b.onclick = () => {
    const rationale = prompt(`${b.textContent}: why?`);
    if (rationale && rationale.trim()) call(`/api/knowledge/contradictions/${b.dataset.kres}/resolve`, { method: "POST", body: { resolution: b.dataset.how, rationale } });
  }));
}

// SE-R-21: which ranking procedure each query class uses, measured per strategy on evals/retrieval-classes.json.
function rankCard(r) {
  const strats = Object.keys(r.strategies);
  const cell = (m, cls, s) => { if (!m) return "<td></td>"; const v = m.mrr.toFixed(2); return `<td class="num" title="recall@5 ${m.recall}">${(r.current[cls] || "balanced") === s ? `<strong>${v}</strong>` : v}</td>`; };
  return `<div class="card"><div class="row nowrap"><h2 class="grow" style="margin:0">Ranking per query class</h2>${me.is_admin ? '<button class="btn sm keep-text" id="k-rlearn">Learn</button>' : ""}</div>
    <p class="muted">Each query is classed by its form (exact name, document, recent, complex question, concept) and ranked by the procedure learned for its class: MRR per strategy on <code>${esc(r.eval)}</code> (${r.cases} cases; the classifier agrees with ${r.classifier.agree}/${r.classifier.n} labels). A choice is kept only while ${esc(r.guard.metric)} of the retrieval eval holds (now ${r.guard.value}); the fixed benchmark measures it independently.</p>
    <div class="table-wrap"><table><tr><th>Class</th><th class="num">n</th>${strats.map((s) => `<th class="num" title="${esc(r.strategies[s])}">${esc(s)}</th>`).join("")}<th>In use</th></tr>
    ${Object.entries(r.table).map(([cls, row]) => `<tr><td>${esc(cls)}</td><td class="num">${row.balanced?.n ?? ""}</td>${strats.map((s) => cell(row[s], cls, s)).join("")}<td>${esc(r.current[cls] || "balanced")}${r.choice[cls] && r.choice[cls] !== r.current[cls] ? ` <span class="muted">(learn: ${esc(r.choice[cls])})</span>` : ""}</td></tr>`).join("")}</table></div></div>`;
}

// Hypotheses (ASEAO07 S07-R-03): statements under test, with evidence for and against. Experiments test their own;
// only a recorded test result (or a person's stated reason) makes one supported.
const HYP_CLASS = { supported: "done", rejected: "failed", inconclusive: "pending", testing: "info", investigating: "info" };
function hypCard(hyps) {
  const live = hyps.filter((h) => h.status !== "archived");
  const row = (h) => `<div class="card" style="margin:6px 0;padding:8px 10px"><div class="row"><span class="pill ${HYP_CLASS[h.status] || ""}">${esc(h.status)}</span><strong class="grow">H${h.id} ${esc(h.statement)}</strong>${h.experiment_id ? `<span class="muted">experiment #${Number(h.experiment_id)}</span>` : ""}</div>
    ${h.reason ? `<div class="muted">Why: ${esc(h.reason)}</div>` : ""}${h.test_method ? `<div class="muted">Test: ${esc(h.test_method)}${h.expected_outcome ? ` · expects ${esc(h.expected_outcome)}` : ""}</div>` : ""}
    <div class="muted">${h.supporting.length} for · ${h.contradicting.length} against${h.actual_outcome ? ` · result: ${esc(h.actual_outcome)}${h.test_result?.source ? ` (${esc(h.test_result.source)})` : ""}` : " · no test result yet"}${h.note ? ` · ${esc(h.note)}` : ""} · ${esc(h.created_by)} · ${ago(h.created_at)}</div>
    ${me.is_admin ? `<div class="row" style="margin-top:6px">${h.status !== "archived" ? `<button class="btn sm keep-text" data-hev="${h.id}">Evidence</button>` : ""}${["investigating", "testing", "inconclusive"].includes(h.status) ? `<button class="btn sm keep-text" data-hres="${h.id}">Result</button>` : ""}${h.next.map((to) => `<button class="btn sm keep-text" data-hst="${h.id}" data-to="${to}">${esc(to)}</button>`).join("")}</div>` : ""}</div>`;
  return `<div class="card"><h2>Hypotheses <span class="muted">${live.length} open or concluded</span></h2><p class="muted">Statements to test. Experiments test their own hypothesis; an agent can never mark one supported without a recorded test result.</p>
    ${me.is_admin ? `<form id="h-new" class="row"><input id="h-st" class="grow" minlength="5" required placeholder="Hypothesis: smaller tasks pass the gate more often"><input id="h-why" placeholder="why (optional)" style="max-width:200px"><button class="btn">Add</button></form>` : ""}
    ${live.map(row).join("") || '<span class="muted">No hypotheses yet.</span>'}</div>`;
}
function hypBind(v, call) {
  const form = v.querySelector("#h-new");
  if (form) form.onsubmit = (e) => { e.preventDefault(); call("/api/hypotheses", { method: "POST", body: { statement: $("#h-st").value, reason: $("#h-why").value } }); };
  v.querySelectorAll("[data-hst]").forEach((b) => (b.onclick = () => {
    const note = prompt(`${b.dataset.to}: note${b.dataset.to === "supported" ? " (required without a test result)" : " (optional)"}`);
    if (note !== null) call(`/api/hypotheses/${b.dataset.hst}/status`, { method: "POST", body: { status: b.dataset.to, note } });
  }));
  v.querySelectorAll("[data-hres]").forEach((b) => (b.onclick = () => {
    const outcome = prompt("What did the test show?"); if (!outcome) return;
    const held = confirm("Did the hypothesis hold? OK = yes, Cancel = no");
    call(`/api/hypotheses/${b.dataset.hres}/result`, { method: "POST", body: { actual_outcome: outcome, passed: held } });
  }));
  v.querySelectorAll("[data-hev]").forEach((b) => (b.onclick = () => {
    const note = prompt("Evidence (a URL, goal #, or a sentence)"); if (!note) return;
    const side = confirm("Does it support the hypothesis? OK = supports, Cancel = contradicts") ? "supporting" : "contradicting";
    call(`/api/hypotheses/${b.dataset.hev}/evidence`, { method: "POST", body: { side, type: /^https?:/.test(note) ? "url" : "note", id: /^https?:/.test(note) ? note : null, note } });
  }));
}

const KG_RELS = ["", "CREATED", "USED", "DEPENDS_ON", "SUPPORTS", "CONTRADICTS", "DERIVED_FROM", "APPLIES_TO", "SUPERSEDES", "VERIFIED_BY", "EXPERIENCED_IN", "ASSIGNED_TO", "COLLABORATED_WITH"];
async function kGraph(entity, depth = 2, offset = 0, rel = "") {
  const limit = 60;
  let g;
  try { g = await api(`/api/knowledge/graph?${new URLSearchParams({ entity, depth, offset, limit, rel })}`); } catch (err) { return toast(err.message); }
  const nodes = new Map(g.nodes.map((n) => [n.key, n]));
  const node = (k) => { const n = nodes.get(k) || { key: k, type: k.split(":")[0], label: k }; return `<a href="#" data-kgnode="${esc(k)}" title="${esc(k)}${n.status ? " · " + esc(n.status) : ""}"><span class="pill">${esc(n.type)}</span> ${esc(String(n.label).slice(0, 70))}</a>`; };
  const root = nodes.get(g.root);
  modal(`<h2>${icon("sitemap", 18)} Graph around ${esc(g.root)}</h2><p class="muted">${root ? esc(root.label) : ""} · ${g.total} edge(s) within ${g.depth} step(s), nearest first. Click a node to centre on it.</p>
    <div class="row"><label for="kg-d" style="margin:0">Depth</label><select id="kg-d" style="width:auto">${[1, 2, 3].map((d) => `<option ${d === g.depth ? "selected" : ""}>${d}</option>`).join("")}</select>
      <label for="kg-r" style="margin:0">Relation</label><select id="kg-r" style="width:auto">${KG_RELS.map((r) => `<option value="${r}" ${r === rel ? "selected" : ""}>${r || "every relation"}</option>`).join("")}</select><span class="grow"></span>
      ${offset > 0 ? ib("chevron_left", "Previous page", 'id="kg-prev"', "sm") : ""}<span class="muted">${g.total ? `${offset + 1}–${offset + g.edges.length} of ${g.total}` : ""}</span>${offset + limit < g.total ? ib("chevron_right", "Next page", 'id="kg-next"', "sm") : ""}</div>
    <div class="table-wrap"><table><tr><th>Step</th><th>From</th><th>Relation</th><th>To</th><th class="num">Weight</th></tr>
    ${g.edges.map((e) => `<tr><td class="num">${e.depth}</td><td>${node(e.src)}</td><td><code>${esc(e.rel)}</code></td><td>${node(e.dst)}</td><td class="num" title="${esc(JSON.stringify(e.evidence))}">${e.weight}</td></tr>`).join("") || '<tr><td colspan="5" class="muted">No edges: nothing links to this entity yet.</td></tr>'}</table></div>`);
  const m = $("#modal");
  m.querySelectorAll("[data-kgnode]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); kGraph(a.dataset.kgnode, g.depth, 0, rel); }));
  $("#kg-d").onchange = () => kGraph(entity, Number($("#kg-d").value), 0, rel);
  $("#kg-r").onchange = () => kGraph(entity, g.depth, 0, $("#kg-r").value);
  $("#kg-prev") && ($("#kg-prev").onclick = () => kGraph(entity, g.depth, Math.max(0, offset - limit), rel));
  $("#kg-next") && ($("#kg-next").onclick = () => kGraph(entity, g.depth, offset + limit, rel));
}

registerView({ id: "knowledge", title: "Knowledge", icon: "book", admin: true }, viewKnowledge);
