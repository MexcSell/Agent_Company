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

function kCard(i, extra = "") {
  return `<div class="card" style="margin:0;padding:8px 10px"><div class="row"><strong>K${i.id}</strong>${kStatusPill(i.status)}<span class="pill">${esc(i.kind)}</span><span class="pill" title="evidence class">${esc(i.evidence_class)}</span>${i.version > 1 ? `<span class="pill info">v${i.version}</span>` : ""}<span class="grow"></span><span class="muted">${esc(i.agent || "?")} · ${ago(i.at)}</span></div>
    <div style="margin:6px 0">${esc(i.text)}</div>
    <div class="muted">${(i.sources || []).map(kSource).join(" · ") || "no source"}${(i.technologies || []).length ? ` · ${i.technologies.map((t) => `<span class="pill">${esc(t)}</span>`).join(" ")}` : ""} · ${esc(i.classification)}</div>
    ${i.status_reason ? `<div class="muted">${esc(i.status_reason)}${i.reviewed_by ? ` (${esc(i.reviewed_by)})` : ""}</div>` : ""}${extra}</div>`;
}

async function viewKnowledge() {
  const qs = new URLSearchParams({ q: kState.q, status: kState.status, kind: kState.kind, limit: "300" });
  const [items, pairs, queue] = await Promise.all([api(`/api/knowledge?${qs}`), api("/api/knowledge/contradictions"), api("/api/knowledge?status=draft,quarantined,rejected&limit=50")]);
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
          <div class="row" style="margin-top:6px"><span class="muted grow">#${p.id} · found ${ago(p.at)}</span><button class="btn sm keep-text" data-kres="${p.id}" data-how="keep_old">Older holds</button><button class="btn sm keep-text" data-kres="${p.id}" data-how="keep_both">Both hold</button><button class="btn sm keep-text" data-kres="${p.id}" data-how="keep_new">Newer holds</button></div></div>`).join("") || '<span class="muted">No open contradictions.</span>'}</div>
      <div class="card"><h2>Review queue <span class="muted">${queue.length}</span></h2><p class="muted">Drafts wait for approval; quarantined items hold credential-like text (delete them); rejected items stay out of prompts.</p>
        <div class="grid">${queue.map((i) => kCard(i, `<div class="row" style="margin-top:6px">${actions(i)}</div>`)).join("") || '<span class="muted">Nothing to review.</span>'}</div></div></div>
      <div class="card"><h2>Items <span class="muted">${items.length}${items.length >= 300 ? "+" : ""}</span></h2><div class="table-wrap"><table><tr><th>#</th><th>Item</th><th>Status</th><th>Source</th><th></th></tr>
        ${items.map((i) => `<tr><td class="num">K${i.id}${i.version > 1 ? `<div class="muted">v${i.version}</div>` : ""}</td><td>${esc(i.text)}<div class="muted"><span class="pill">${esc(i.kind)}</span> ${esc(i.evidence_class)} · ${esc(i.agent || "?")} · ${ago(i.at)}${(i.technologies || []).length ? " · " + esc(i.technologies.join(", ")) : ""}</div>${i.contradicted_by.length ? `<div class="pill pending" title="an open contradiction">disputed by ${i.contradicted_by.map((c) => "K" + c).join(", ")}</div>` : ""}</td>
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
  v.querySelectorAll("[data-kres]").forEach((b) => (b.onclick = () => {
    const rationale = prompt(`${b.textContent}: why?`);
    if (rationale && rationale.trim()) call(`/api/knowledge/contradictions/${b.dataset.kres}/resolve`, { method: "POST", body: { resolution: b.dataset.how, rationale } });
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
