"use strict";
// Agents: the organisation (every layer of the hierarchy on one screen, scaled to fit) beside the agent list
// (the only part that scrolls); the agent network (network.js), Tools & MCP (mcp.py) and Skills & plugins (catalog.py) as tabs.
// The agent card edits model (also another agent's, for the General Manager), plugins and the agent's
// definition file: the body learning rewrites (agentdefs.py). Every configuration change is a version the card's
// history can roll back to; degraded agents (failing runs or model test) are flagged and can be reset (SPEC-02).

let agentsTab = store.get("ac.agents.tab") || "org";

async function viewAgents(quiet) {
  if (quiet && $("#ag-page") && agentsTab !== "org") return;  // the catalog tabs keep their forms
  const tabs = [["org", "Organisation", "sitemap"], ["network", "Agent network", "network"], ["mcp", "Tools & MCP", "plug"], ["catalog", "Skills & plugins", "book"]];
  if (!$("#ag-page") || !quiet) {
    $("#view").innerHTML = `<div class="ag-page" id="ag-page"><div class="ag-tabs" role="tablist">${tabs.map(([k, l, i]) => `<button role="tab" data-agtab="${k}" aria-selected="${agentsTab === k}">${icon(i, 16)} ${l}</button>`).join("")}
      <span class="grow"></span><span id="ag-tools"></span></div><div class="ag-body" id="ag-body"></div></div>`;
    $("#view").classList.add("fit");
    document.querySelectorAll("[data-agtab]").forEach((b) => (b.onclick = () => { agentsTab = b.dataset.agtab; store.set("ac.agents.tab", agentsTab); viewAgents(); }));
  }
  if (agentsTab === "network") { $("#ag-tools").innerHTML = ""; return viewNetwork($("#ag-body")); }
  if (agentsTab === "mcp") return viewMcp();
  if (agentsTab === "catalog") return viewCatalog();
  return viewOrg(quiet);
}

// --- organisation ---------------------------------------------------------------------------
async function viewOrg(quiet) {
  const [all, rooms, health] = await Promise.all([api("/api/agents"), api("/api/rooms"), api("/api/agents/health").catch(() => ({ degraded: [], inactive: [] }))]);
  const showRetired = store.get("ac.retired") === "1";
  const agents = all.filter((a) => showRetired || a.lifecycle !== "retired");
  const retired = all.length - all.filter((a) => a.lifecycle !== "retired").length;
  const attention = health.degraded.length || health.inactive.length ? `<button type="button" class="btn sm" id="a-attn" title="Degraded agents and agents idle long enough to review for retirement">${health.degraded.length ? `${health.degraded.length} degraded` : ""}${health.degraded.length && health.inactive.length ? " · " : ""}${health.inactive.length ? `${health.inactive.length} inactive` : ""}</button>` : "";
  $("#ag-tools").innerHTML = `${attention}<span class="muted">${agents.length} agents · ${all.filter((a) => a.status === "busy").length} working</span>
    ${retired ? `<label class="row" style="margin:0"><input type="checkbox" id="a-retired" style="width:auto" ${showRetired ? "checked" : ""}> retired (${retired})</label>` : ""}
    ${me.is_admin ? ib("sitemap", "Rooms: hierarchy, leads, switches, position history and export", 'id="a-rooms"') : ""}
    ${me.is_admin ? ib("user_add", "Add an agent", 'id="a-new"', "primary") : ""}`;
  const byId = new Map(agents.map((a) => [a.id, a])), kids = new Map();
  agents.forEach((a) => { const p = byId.has(a.superior_id) ? a.superior_id : 0; if (!kids.has(p)) kids.set(p, []); kids.get(p).push(a); });
  const node = (a) => `<div class="org-node ${a.enabled ? "" : "off"} st-${esc(a.status)}" data-aid="${a.id}" title="${esc(a.title)} · ${esc(a.room)}"><span class="avatar">${roleIcon(a.role)}</span><span class="org-txt"><b>${esc(a.name)}</b><small>${esc(a.role.replace(/_/g, " "))} · L${a.level}</small></span><i class="org-dot"></i></div>`;
  // A wide layer (more than three reports) wraps into a square block, so the chart stays roughly square.
  const tree = (a) => {
    const ch = kids.get(a.id) || [];
    const wrap = ch.length > 3 ? ` class="wrap" style="--cols:${Math.ceil(Math.sqrt(ch.length))}"` : "";
    return `<li>${node(a)}${ch.length ? `<ul${wrap}>${ch.map(tree).join("")}</ul>` : ""}</li>`;
  };
  const roots = kids.get(0) || [];
  const body = $("#ag-body");
  const listHtml = rooms.map((r) => { const m = agents.filter((a) => a.room === r.key); return m.length ? `<div class="ag-room"><h3>${esc(r.name)}${r.rotational ? ' <span class="pill">rotational</span>' : ""}${r.enabled ? "" : ' <span class="pill failed" title="Its agents are not staffed until the room is enabled again">room disabled</span>'}</h3>${m.map((a) => `<div class="list-item agent-row" data-aid="${a.id}"><div class="row nowrap"><span class="avatar" style="width:26px;height:26px">${roleIcon(a.role)}</span><strong class="grow">${esc(a.name)}</strong>${a.health === "degraded" && a.lifecycle !== "retired" ? `<span class="pill failed" title="${esc(a.health_reason)}">degraded</span>` : ""}${a.lifecycle === "candidate" ? '<span class="pill pending" title="A recruit in its qualification run (SPEC-03): never staffed for real work until it passes">candidate</span>' : pill(a.lifecycle === "retired" ? "retired" : a.lifecycle === "paused" ? "paused" : a.enabled ? a.status : "disabled")}</div><div class="muted ellipsis">${esc(a.role)} · L${a.level} · ${a.tasks_done} done${a.in_trial ? " · trial" : ""}${a.parent_id ? " · clone" : ""}</div></div>`).join("")}</div>` : ""; }).join("");
  const orgHtml = `<ul class="org-tree">${roots.map(tree).join("")}</ul>`;
  if (!quiet || !$("#org-canvas")) {
    body.innerHTML = `<div class="ag-split"><section class="card ag-org"><div class="row"><h2 class="grow" style="margin:0">Organisation</h2><span class="muted">every layer, top to bottom · click an agent for its card</span></div><div class="org-canvas" id="org-canvas"><div class="org-scale" id="org-scale"></div></div></section>
      <aside class="card ag-list"><h2>Agents</h2><div class="ag-list-body" id="ag-list"></div></aside></div>`;
    new ResizeObserver(fitOrg).observe($("#org-canvas"));
  }
  if ($("#org-scale").dataset.html !== orgHtml) { $("#org-scale").innerHTML = orgHtml; $("#org-scale").dataset.html = orgHtml; fitOrg(); }
  const lst = $("#ag-list"), top = lst.scrollTop;
  if (lst.dataset.html !== listHtml) { lst.innerHTML = listHtml; lst.dataset.html = listHtml; lst.scrollTop = top; }
  body.querySelectorAll("[data-aid]").forEach((el) => (el.onclick = () => agentModal(Number(el.dataset.aid))));
  $("#a-retired") && ($("#a-retired").onchange = (e) => { store.set("ac.retired", e.target.checked ? "1" : "0"); viewOrg(); });
  $("#a-new") && ($("#a-new").onclick = () => newAgentForm(rooms, all));
  $("#a-rooms") && ($("#a-rooms").onclick = () => roomsModal());
  $("#a-attn") && ($("#a-attn").onclick = () => attentionModal(health));
}

function attentionModal(h) {
  const row = (a, extra) => `<div class="list-item agent-row" data-aid="${a.id}"><div class="row nowrap"><span class="avatar" style="width:26px;height:26px">${roleIcon(a.role)}</span><strong class="grow">${esc(a.name)}</strong>${extra}</div></div>`;
  modal(`<h2>Agents that need attention</h2>
    ${h.degraded.length ? `<h3>Degraded (not staffed until reset or given another model)</h3>${h.degraded.map((a) => row(a, `<span class="muted ellipsis">${esc(a.health_reason)}</span>`)).join("")}` : ""}
    ${h.inactive.length ? `<h3>Inactive (review for retirement)</h3>${h.inactive.map((a) => row(a, `<span class="muted">${a.never_active ? "never worked" : "idle"} ${a.idle_days} days</span>`)).join("")}` : ""}`);
  document.querySelectorAll("#modal [data-aid]").forEach((el) => (el.onclick = () => agentModal(Number(el.dataset.aid))));
}
// The whole hierarchy on one screen: scale the tree down (never up past 1) to fit the panel.
function fitOrg() {
  const c = $("#org-canvas"), s = $("#org-scale");
  if (!c || !s) return;
  s.style.transform = "none";
  const k = Math.min(1, (c.clientWidth - 8) / Math.max(1, s.scrollWidth), (c.clientHeight - 8) / Math.max(1, s.scrollHeight));
  s.style.transform = `scale(${k})`;
}

function newAgentForm(rooms, agents) {
  modal(`<h2>${icon("user_add", 18)} New agent</h2><form id="na"><label>Role (an existing one, or a new specialist role)</label><input id="na-role" required placeholder="data_engineer">
    <label>Title (optional)</label><input id="na-title" placeholder="Data Engineer">
    <label>Room</label><select id="na-room"><option value="">default for the role</option>${rooms.map((r) => `<option value="${esc(r.key)}">${esc(r.name)}</option>`).join("")}</select>
    <label>Reports to</label><select id="na-sup"><option value="">General Manager</option>${agents.filter((a) => a.lifecycle === "active").map((a) => `<option value="${a.id}">${esc(a.name)}</option>`).join("")}</select>
    <label>Skills (name=level, comma separated)</label><input id="na-skills" placeholder="sql=6, python=5">
    <label>Mission</label><input id="na-goal">
    <label class="row" style="margin-top:8px"><input type="checkbox" id="na-writes" style="width:auto"> May change files (write and shell access)</label>
    <div class="row" style="margin-top:12px"><button class="btn icon primary" title="Create" aria-label="Create">${icon("check", 16)}</button></div></form>`);
  $("#na").onsubmit = async (e) => {
    e.preventDefault();
    const skills = Object.fromEntries($("#na-skills").value.split(",").map((x) => x.split("=").map((y) => y.trim())).filter((x) => x[0]).map(([k, v]) => [k, Number(v) || 5]));
    try { await api("/api/agents", { method: "POST", body: { role: $("#na-role").value, title: $("#na-title").value, room: $("#na-room").value, superior_id: Number($("#na-sup").value) || null, skills, goal: $("#na-goal").value, writes: $("#na-writes").checked } }); $("#modal").hidden = true; viewAgents(); }
    catch (err) { toast(err.message); }
  };
}

// Rooms (SPEC-01 S01-R-01, R-02, R-04): parent room, lead and switch per room, the position history, export.
async function roomsModal(edit) {
  const [rooms, agents, hist] = await Promise.all([api("/api/rooms"), api("/api/agents"), api("/api/org/positions?limit=50")]);
  const names = new Map(agents.map((a) => [a.id, a.name])), byKey = new Map(rooms.map((r) => [r.key, r]));
  const path = (r) => { const out = []; for (let x = r; x && !out.includes(x.key); x = byKey.get(x.parent)) out.unshift(x.key); return out.slice(0, -1).map((k) => byKey.get(k).name).join(" › "); };
  const leads = agents.filter((a) => a.lifecycle === "active");
  const form = (r) => `<form id="rm-f" class="card"><h3>${r ? `Edit ${esc(r.name)}` : "New room"}</h3>
    ${r ? "" : '<label>Key (lowercase, e.g. platform_group)</label><input id="rm-key" required pattern="[a-z][a-z0-9_]{1,63}">'}
    <label>Name</label><input id="rm-name" required value="${esc(r?.name || "")}">
    <label>Division</label><select id="rm-div">${["management", "development", "learning", "research", "security", "business"].map((d) => `<option ${r?.division === d ? "selected" : ""}>${d}</option>`).join("")}</select>
    <label>Parent room</label><select id="rm-parent"><option value="">none (top level)</option>${rooms.filter((x) => x.key !== r?.key).map((x) => `<option value="${esc(x.key)}" ${r?.parent === x.key ? "selected" : ""}>${esc(x.name)}</option>`).join("")}</select>
    <label>Lead</label><select id="rm-lead"><option value="">nobody</option>${leads.map((a) => `<option value="${a.id}" ${r?.lead_agent_id === a.id ? "selected" : ""}>${esc(a.name)}</option>`).join("")}</select>
    ${r ? '<label>Reason (kept in the position history when the lead changes)</label><input id="rm-reason">' : ""}
    <label>Description</label><input id="rm-desc" value="${esc(r?.description || "")}">
    <label title="Covers the room's agents and those of its sub-rooms; when used up, they take no work until 00:00 UTC and goals that need them wait">Daily budget, $ (0 = none)${r?.spent_today_usd != null ? ` · $${r.spent_today_usd.toFixed(2)} used today` : ""}</label><input id="rm-budget" type="number" min="0" step="0.01" value="${r?.daily_budget_usd || 0}">
    ${r ? `<label class="row" style="margin-top:8px"><input type="checkbox" id="rm-on" style="width:auto" ${r.enabled ? "checked" : ""}> Enabled (a disabled room's agents are not staffed; refused while its agents work on a goal)</label>` : ""}
    <div class="row" style="margin-top:12px"><button class="btn primary">Save</button><button type="button" class="btn" id="rm-cancel">Cancel</button></div></form>`;
  modal(`<div class="row"><h2 class="grow">${icon("sitemap", 18)} Rooms</h2>${ib("plus", "New room", 'id="rm-new"')}
      <a class="btn" href="/api/org/export?format=json" download="organisation.json">JSON</a><a class="btn" href="/api/org/export?format=csv&table=agents" download>Agents CSV</a><a class="btn" href="/api/org/export?format=csv&table=rooms" download>Rooms CSV</a></div>
    ${edit !== undefined ? form(byKey.get(edit)) : ""}
    <table><tr><th>Room</th><th>Within</th><th>Lead</th><th class="num">Agents</th><th></th><th></th></tr>${rooms.map((r) => `<tr><td>${esc(r.name)}</td><td class="muted">${esc(path(r))}</td><td>${esc(names.get(r.lead_agent_id) || "")}</td><td class="num">${r.agents}</td><td>${r.enabled ? "" : '<span class="pill failed">disabled</span>'}${r.daily_budget_usd ? ` <span class="muted" title="Spent today of the daily budget (with its sub-rooms)">$${(r.spent_today_usd || 0).toFixed(2)} / $${r.daily_budget_usd.toFixed(2)}</span>` : ""}</td><td>${ib("edit", "Edit", `data-rk="${esc(r.key)}"`)}</td></tr>`).join("")}</table>
    <h3>Position history</h3>${hist.length ? `<table><tr><th>Position</th><th>From</th><th>To</th><th>Reason</th><th>By</th><th>When</th></tr>${hist.map((h) => `<tr><td>${esc(h.position)}</td><td>${esc(h.previous_agent || "")}</td><td>${esc(h.new_agent || "")}</td><td>${esc(h.reason)}</td><td class="muted">${esc(h.actor)}</td><td class="muted">${esc(h.effective_at.slice(0, 16).replace("T", " "))}</td></tr>`).join("")}</table>` : '<p class="muted">No lead or General Manager changes yet.</p>'}`);
  $("#rm-new").onclick = () => roomsModal(null);
  document.querySelectorAll("[data-rk]").forEach((b) => (b.onclick = () => roomsModal(b.dataset.rk)));
  if (!$("#rm-f")) return;
  $("#rm-cancel").onclick = () => roomsModal();
  $("#rm-f").onsubmit = async (e) => {
    e.preventDefault();
    const r = byKey.get(edit), body = { name: $("#rm-name").value, division: $("#rm-div").value, description: $("#rm-desc").value, parent_key: $("#rm-parent").value || null, lead_agent_id: Number($("#rm-lead").value) || null, daily_budget_usd: +$("#rm-budget").value || 0 };
    try {
      if (r) {
        const cur = { name: r.name, division: r.division, description: r.description, parent_key: r.parent, lead_agent_id: r.lead_agent_id, daily_budget_usd: r.daily_budget_usd || 0 };
        const changes = Object.fromEntries(Object.entries(body).filter(([k, v]) => v !== cur[k]));
        if ($("#rm-on").checked !== r.enabled) changes.enabled = $("#rm-on").checked;
        if ("lead_agent_id" in changes) changes.reason = $("#rm-reason").value;
        await api(`/api/rooms/${encodeURIComponent(r.key)}`, { method: "PATCH", body: changes });
      } else await api("/api/rooms", { method: "POST", body: { key: $("#rm-key").value, ...body } });
      roomsModal(); viewAgents();
    } catch (err) { toast(err.message); }
  };
}

// --- definition candidates: a study rewrite an administrator applies after seeing the diff (G-R-19) ---
function diffHtml(text) {
  return text.split("\n").map((ln) => {
    const cls = ln.startsWith("+") && !ln.startsWith("+++") ? "diff-add" : ln.startsWith("-") && !ln.startsWith("---") ? "diff-del" : ln.startsWith("@@") ? "diff-hunk" : "";
    return cls ? `<span class="${cls}">${esc(ln)}</span>` : esc(ln);
  }).join("\n");
}

async function definitionCandidate(id) {
  const box = $("#a-cand");
  if (!box) return;
  let c;
  try {
    const pending = await api(`/api/agents/${id}/definition/candidates?status=pending`);
    if (!pending.length) return;
    c = await api(`/api/definition-candidates/${pending[0].id}`);
  } catch (e) { box.innerHTML = `<p class="muted">${esc(e.message)}</p>`; return; }
  box.innerHTML = `<h3>Pending candidate #${c.id}</h3>
    <p class="muted">A study visit proposed new ${esc(Object.keys(c.sections || {}).join(" and "))} with ${esc(c.model)} (${esc(c.reason)}, ${ago(c.created_at)}). The file changes only when you apply it.</p>
    ${c.stale ? `<p class="diff-stale">${esc(c.stale_note)}</p>` : ""}
    <pre class="mono diff">${c.diff ? diffHtml(c.diff) : '<span class="muted">No change to the file.</span>'}</pre>
    <div class="row" style="margin-top:8px">${ib("check", "Apply the candidate: one commit", 'id="a-cand-apply"', "primary")}${ib("x", "Discard the candidate", 'id="a-cand-discard"', "danger")}<span class="grow"></span></div>`;
  $("#a-cand-apply").onclick = async () => {
    try { const r = await api(`/api/definition-candidates/${c.id}/apply`, { method: "POST" }); toast(`Candidate #${r.id} applied`); agentModal(id, "definition"); } catch (e) { toast(e.message); }
  };
  $("#a-cand-discard").onclick = async () => {
    const note = prompt("Why discard it? (optional)", "");
    if (note === null) return;
    try { await api(`/api/definition-candidates/${c.id}/discard`, { method: "POST", body: { note } }); toast(`Candidate #${c.id} discarded`); agentModal(id, "definition"); } catch (e) { toast(e.message); }
  };
}

// --- the agent card -------------------------------------------------------------------------
async function agentModal(id, tab = "profile") {
  const [a, m, versions, all, cat] = await Promise.all([api(`/api/agents/${id}`), api("/api/models"), api(`/api/agents/${id}/versions`), api("/api/agents"), api("/api/catalog")]);
  const skills = Object.entries(a.skills || {}).sort((x, y) => y[1] - x[1]).map(([k, v]) => `<span class="pill">${esc(k)} ${v}</span>`).join(" ");
  const admin = me.is_admin || me.permissions?.manage_agents;
  const isGm = a.role === "general_manager" && !a.parent_id;
  const others = all.filter((x) => x.id !== a.id && x.lifecycle === "active");
  const modelSelect = `<select id="a-model"><option value="">Role route (${esc(m.routes[a.role] || "default")})</option>
    <optgroup label="Models">${m.models.map((x) => `<option value="${esc(x.name)}" ${x.name === a.model_pref ? "selected" : ""}>${esc(x.name)}${x.available ? "" : " (unavailable)"}</option>`).join("")}</optgroup>
    <optgroup label="Another agent (uses its model)">${others.map((x) => `<option value="agent:${x.id}" ${a.model_pref === `agent:${x.id}` ? "selected" : ""}>${esc(x.name)}</option>`).join("")}</optgroup></select>`;
  const plugins = cat.filter((i) => ["plugin", "skill"].includes(i.kind));
  const tabs = [["profile", "Profile"], ["definition", "Definition"], ["history", "Playbook & history"], ["links", "Relationships"], ["config", "Versions"]];
  modal(`<div class="row"><div class="avatar">${roleIcon(a.role)}</div><div class="grow"><h2 style="margin:0">${esc(a.name)}</h2><div class="muted">${esc(a.title)} · ${esc(a.room)} · configuration v${a.config_version} · definition v${a.definition_version}</div></div>${a.health === "degraded" ? '<span class="pill failed">degraded</span>' : ""}${pill(a.enabled ? a.status : "disabled")}</div>
    <div class="ag-tabs small" role="tablist">${tabs.map(([k, l]) => `<button role="tab" data-mtab="${k}" aria-selected="${tab === k}">${l}</button>`).join("")}</div><div id="a-tab"></div>`);
  document.querySelectorAll("[data-mtab]").forEach((b) => (b.onclick = () => agentModal(id, b.dataset.mtab)));
  const box = $("#a-tab");
  if (tab === "definition") {
    const d = await api(`/api/agents/${id}/definition`);
    box.innerHTML = `<p class="muted">The agent runs with this file (<code>${esc(d.path)}</code>): learning rewrites its <b>Working method</b> and <b>Knowledge notes</b>, the card's settings rewrite the rest. Every change is a commit.</p>
      <textarea id="a-def" class="mono" rows="18" ${admin ? "" : "readonly"}>${esc(d.text)}</textarea>
      <div class="row" style="margin-top:8px">${admin ? ib("save", "Save the definition", 'id="a-def-save"', "primary") + ib("book", "Study now: propose a new working method from new knowledge and lessons", 'id="a-study"') : ""}<span class="grow"></span></div>
      <div id="a-cand"></div>
      <h3>History</h3><div class="events">${d.history.map((h) => `<div><span class="k">${esc(h.commit)}</span>${esc(h.message)} <span class="muted">${ago(h.at)}</span></div>`).join("") || '<span class="muted">No commits yet.</span>'}</div>`;
    $("#a-def-save") && ($("#a-def-save").onclick = async () => { try { await api(`/api/agents/${id}/definition`, { method: "PUT", body: { text: $("#a-def").value } }); toast("Definition saved"); agentModal(id, "definition"); } catch (e) { toast(e.message); } });
    $("#a-study") && ($("#a-study").onclick = async () => { toast("Studying…"); try { const r = await api(`/api/learning/study?agent_id=${id}`, { method: "POST" }); toast(r.candidate_id ? `Candidate #${r.candidate_id} ready for review (${r.model})` : `No change: ${r.detail || ""}`); agentModal(id, "definition"); } catch (e) { toast(e.message); } });
    if (admin) await definitionCandidate(id); // G-R-19: a study rewrite waits for a person who saw the diff
    return;
  }
  if (tab === "config") {
    const cv = await api(`/api/agents/${id}/config-versions`);
    const show = (snap) => Object.entries(snap).map(([k, v]) => `${k}: ${typeof v === "object" && v !== null ? JSON.stringify(v) : v}`).join("\n");
    box.innerHTML = `<p class="muted">Each change of the agent's configuration (skills, permissions, model, superior, room, enabled, lifecycle, mission, plugins) is a version. Rolling back restores that version's configuration as a new version; the playbook has its own versions (Playbook &amp; history).</p>
      ${cv.map((v, i) => `<details class="cv"><summary class="row nowrap"><strong>v${v.version}</strong><span class="grow">${esc(v.reason)} <span class="muted">· ${esc(v.changed_by)} · ${ago(v.at)}</span></span>${admin && i > 0 && a.lifecycle !== "retired" ? ib("refresh", `Roll back to v${v.version}`, `data-rollback="${v.version}"`, "sm") : ""}</summary><pre>${esc(show(v.snapshot))}</pre></details>`).join("") || '<span class="muted">No versions yet.</span>'}`;
    document.querySelectorAll("[data-rollback]").forEach((b) => (b.onclick = async (e) => {
      e.preventDefault();
      if (!confirm(`Restore the configuration of v${b.dataset.rollback}?`)) return;
      try { await api(`/api/agents/${id}/rollback?version=${b.dataset.rollback}`, { method: "POST" }); toast("Rolled back"); agentModal(id, "config"); if (current === "agents") viewAgents(); } catch (err) { toast(err.message); }
    }));
    return;
  }
  if (tab === "links") return relationshipsTab(box, a, all, admin);
  if (tab === "history") {
    box.innerHTML = `<h3>Playbook v${a.playbook_version}</h3><pre>${esc(a.playbook || "Empty: the agent learns from finished work.")}</pre>
      ${a.trial_playbook ? `<h3>Trial playbook (${a.trial_outcomes.length} task(s) so far)</h3><pre>${esc(a.trial_playbook)}</pre>` : ""}
      ${versions.length ? `<h3>Versions</h3>${versions.map((v) => `<div class="row" style="margin-top:6px"><strong>v${v.version}</strong><span class="muted grow">${esc(v.reason)} · ${ago(v.at)}</span>${admin && v.version !== a.playbook_version ? ib("refresh", `Restore v${v.version}`, `data-restore="${v.version}"`, "sm") : ""}</div>`).join("")}` : ""}
      ${a.handovers.length ? `<h3>Handovers</h3><div class="events">${a.handovers.map((h) => h.project_id ? `<div>project #${Number(h.project_id)} · General Manager ${h.to === a.id ? "taken over" : "handed over"}: ${(h.package.goals || []).length} open goal(s), ${(h.package.blockers || []).length} blocker(s), ${(h.package.action_items || []).length} action item(s) <span class="muted">${ago(h.at)}</span></div>` : `<div>goal #${h.goal_id} · ${esc(h.task)}: ${esc(h.package.from_agent || "")} → ${esc(h.package.to_agent || "")} <span class="muted">${ago(h.at)}</span></div>`).join("")}</div>` : ""}
      <h3>Recent lessons</h3><div class="events">${a.lessons.map((l) => `<div>${pill(l.outcome)} ${esc(l.text)}</div>`).join("") || '<span class="muted">None yet.</span>'}</div>`;
    document.querySelectorAll("[data-restore]").forEach((b) => (b.onclick = async () => { await api(`/api/agents/${id}/versions/${b.dataset.restore}/restore`, { method: "POST" }); agentModal(id, "history"); }));
    return;
  }
  box.innerHTML = `<div class="grid two" style="margin-top:8px"><div>
      ${a.quota_reason ? `<div class="props"><strong>Out of quota</strong> ${esc(a.quota_reason)}. Goals that need it wait until 00:00 UTC.</div>` : ""}
      ${a.health === "degraded" ? `<div class="props"><strong>Degraded</strong> ${esc(a.health_reason)} It is not staffed until its health is reset or its model changed. ${admin ? ib("check", "Reset its health (its earlier runs stop counting)", 'id="a-heal"', "sm") : ""}</div>` : ""}
      <h3>Mission</h3><p>${esc(a.goal)}</p><p class="muted">${esc(a.backstory)}</p>
      <h3>Skills</h3><div class="row">${skills || '<span class="muted">none</span>'}</div>
      <h3>Permissions</h3><div class="row">${(a.permissions || []).map((p) => `<span class="pill">${esc(p)}</span>`).join(" ")} <span class="pill info" title="${esc((a.autonomy_levels || {})[a.autonomy] || "")}">autonomy ${esc(a.autonomy)}</span></div>
      <p class="muted">${esc((a.autonomy_levels || {})[a.autonomy] || "")}. Every run passes the tool gateway: changing files needs <em>write</em> and G2, commands need <em>shell</em>, granted MCP tools need G3.</p>
      <h3>Certification</h3><p><span class="pill ${a.certification.level >= 3 ? "done" : "info"}">L${a.certification.level}</span> ${esc(a.certification.meaning)} <span class="muted">(${a.certification.evidence.passed}/${a.certification.evidence.outcomes} passed over ${a.certification.evidence.goals} goals${a.certification.expired ? "; expired" : ""})</span></p>
      <h3>Stats</h3><table><tr><td>Level / XP</td><td class="num">${a.level} / ${a.xp}</td></tr><tr><td>Tasks done / failed</td><td class="num">${a.tasks_done} / ${a.tasks_failed}</td></tr><tr><td>Runs</td><td class="num">${a.usage.runs}</td></tr><tr><td>Input / output tokens</td><td class="num">${fmt.format(a.usage.input_tokens)} / ${fmt.format(a.usage.output_tokens)}</td></tr><tr><td>Cost</td><td class="num">${money(a.usage.cost)}</td></tr><tr><td>Spent today (with clones)</td><td class="num">${money(a.spent_today)}${a.daily_cost_quota_usd ? ` of ${money(a.daily_cost_quota_usd)}` : ""}</td></tr><tr><td>Last active</td><td class="num">${ago(a.last_active_at)}</td></tr><tr><td>Open tasks · active in the last 24 h</td><td class="num">${a.workload.open_tasks} · ${a.workload.hours_24h} h</td></tr><tr><td>Last handover</td><td class="num">${a.workload.last_handover ? `${a.workload.last_handover.direction} (${esc(a.workload.last_handover.kind)}, ${esc(a.workload.last_handover.status.replace(/_/g, " "))}) ${ago(a.workload.last_handover.at)}` : "none"}</td></tr></table>
    </div><div>
      ${admin ? `<h3>Settings</h3><label>Model${isGm ? " — the General Manager: Claude Code by default; pick an API model, a Claude Code at another address (Models → Add), or another agent" : " (overrides the role route)"}</label>${modelSelect}
        <div class="row"><div class="grow"><label for="a-conc">Goals at once</label><input id="a-conc" type="number" min="1" max="8" value="${a.max_concurrent}"></div>
          <div class="grow"><label for="a-quota">Daily cost quota, $ (0 = none; covers its clones; resets 00:00 UTC)</label><input id="a-quota" type="number" min="0" step="0.01" value="${a.daily_cost_quota_usd}"></div></div>
        ${me.is_admin ? `<label>Permissions and autonomy (administrators only)</label><div class="row">${["read", "write", "shell", "network"].map((p) => `<label class="plug"><input type="checkbox" data-perm="${p}" ${(a.permissions || []).includes(p) ? "checked" : ""}><span>${p}</span></label>`).join("")}
          <select id="a-autonomy" aria-label="Autonomy level">${Object.entries(a.autonomy_levels || {}).map(([k, v]) => `<option value="${k}" ${k === a.autonomy ? "selected" : ""}>${k} — ${esc(v)}</option>`).join("")}</select></div>` : ""}
        <label>Plugins and skills it uses (from Agents → Skills & plugins)</label><div class="plug-picks">${plugins.map((p) => `<label class="plug"><input type="checkbox" data-plug="${esc(p.name)}" ${(a.plugins || []).includes(p.name) ? "checked" : ""}><span>${esc(p.name)}</span><small>${esc(p.kind)}</small></label>`).join("") || '<span class="muted">The catalog is empty.</span>'}</div>
        <div class="row" style="margin-top:10px">${ib("save", "Save", 'id="a-save"', "primary")}
        ${a.lifecycle !== "retired" ? `${ib("clone", "Clone", 'id="a-clone"')}${a.lifecycle === "candidate" ? "" : ib(a.lifecycle === "paused" ? "play" : "pause", a.lifecycle === "paused" ? "Resume" : "Pause", 'id="a-pause"')}${a.parent_id ? ib("merge", "Merge into original", 'id="a-merge"') : ""}${ib("trash", "Retire", 'id="a-retire"', "danger")}` : '<span class="muted">Retired agents keep their history.</span>'}</div>` : ""}
      <h3>Related agents (shared experience)</h3><div class="events">${a.related.map((r) => `<div>${esc(r.name)} <span class="muted">${esc(r.role)} · ${r.weight.toFixed(2)}</span></div>`).join("") || '<span class="muted">None.</span>'}</div>
      <h3>Reports to it</h3><div class="events">${a.subordinates.map((r) => `<div>${esc(r.name)} <span class="muted">${esc(r.role)}</span></div>`).join("") || '<span class="muted">Nobody.</span>'}</div>
    </div></div>`;
  if (!admin) return;
  const act = async (path, body, sure) => { if (sure && !confirm(sure)) return; try { await api(`/api/agents/${id}/${path}`, { method: "POST", body }); toast("Done"); if (current === "agents") viewAgents(); agentModal(id); } catch (e) { toast(e.message); } };
  $("#a-save").onclick = async () => {
    const plugins2 = [...document.querySelectorAll("[data-plug]:checked")].map((c) => c.dataset.plug);
    const body = { model_pref: $("#a-model").value || null, plugins: plugins2, max_concurrent: +$("#a-conc").value, daily_cost_quota_usd: +$("#a-quota").value, expected_version: a.config_version };
    if ($("#a-autonomy")) {
      const perms = [...document.querySelectorAll("[data-perm]:checked")].map((c) => c.dataset.perm);
      if (perms.slice().sort().join() !== (a.permissions || []).slice().sort().join() || $("#a-autonomy").value !== a.autonomy) Object.assign(body, { permissions: perms, autonomy: $("#a-autonomy").value });
    }
    try { await api(`/api/agents/${id}`, { method: "PATCH", body }); toast("Saved"); agentModal(id); if (current === "agents") viewAgents(); } catch (e) { toast(e.message); }
  };
  $("#a-heal") && ($("#a-heal").onclick = () => act("health/reset"));
  $("#a-clone") && ($("#a-clone").onclick = () => act("clone"));
  $("#a-pause") && ($("#a-pause").onclick = () => act("lifecycle", { state: a.lifecycle === "paused" ? "active" : "paused" }));
  $("#a-retire") && ($("#a-retire").onclick = () => act("lifecycle", { state: "retired" }, "Retire this agent permanently? Its history is kept."));
  $("#a-merge") && ($("#a-merge").onclick = () => act("merge", undefined, "Fold this clone's experience into its original and retire it?"));
}

// Relationships with their kind, where they come from (inferred from profiles, observed in verified work,
// corrected by an administrator) and their evidence; an administrator can set a weight or expire one (SPEC-12).
const REL_KINDS = ["similar", "collaborated", "complementary_skill", "research_partner", "testing_partner", "reviewed_by", "mentored_by", "depends_on", "handover_to", "supervises", "knowledge_contributor"];
async function relationshipsTab(box, a, all, admin) {
  const [rels, ments] = await Promise.all([api(`/api/agents/${a.id}/relationships`), api(`/api/mentoring?agent_id=${a.id}`)]);
  const ment = (m) => `<details class="cv"><summary class="row nowrap"><strong>#${m.id}</strong><span class="grow">${esc(m.mentor)} mentors ${esc(m.learner)} on <b>${esc(m.skill)}</b> <span class="muted">· plan #${m.plan_id} · L${m.gap_assessment.current_level ?? "?"} → L${m.gap_assessment.target_level ?? "?"}</span></span>${pill(m.status)}</summary>
    <p class="muted">Practice: ${m.exercises.runs ? `${m.exercises.passed}/${m.exercises.runs} passed` : "not run yet"} · materials ${m.materials.map((k) => `K${k}`).join(", ") || "none"}${m.final_evaluation.status ? ` · final evaluation ${esc(m.final_evaluation.status)}` : ""}</p>
    <div class="events">${m.feedback.map((f) => `<div><span class="k">${esc(f.by)}</span>${esc(f.text)} <span class="muted">${ago(f.at)}</span></div>`).join("") || '<span class="muted">No feedback yet.</span>'}</div>
    ${admin && m.status === "active" ? `<div class="row" style="margin-top:6px"><input class="grow" data-mfb="${m.id}" maxlength="4000" placeholder="Advice for the learner (not a grade)">${ib("send", "Add feedback", `data-mfbsend="${m.id}"`, "sm")}</div>` : ""}</details>`;
  const phrase = (r) => r.directed ? `${esc(r.a)} <b>${esc(r.kind.replace(/_/g, " "))}</b> ${esc(r.b)}` : `<b>${esc(r.kind.replace(/_/g, " "))}</b> with ${esc(r.a_id === a.id ? r.b : r.a)}`;
  box.innerHTML = `<p class="muted">Inferred links come from profiles (role, skills, the specialists its peers work with, a mentor candidate); observed ones only from verified work (gate results, reviews, dependencies, handovers, knowledge it used). Expired links are kept but no longer used.</p>
    <table class="rel-table"><tr><th>Relationship</th><th>Origin</th><th class="num">Weight</th><th>Evidence</th>${admin ? "<th></th>" : ""}</tr>
    ${rels.map((r) => `<tr class="${r.status === "expired" ? "off" : ""}"><td>${phrase(r)}${r.status === "expired" ? ' <span class="pill">expired</span>' : ""}</td><td>${pill(r.origin)}</td><td class="num">${r.weight.toFixed(2)}</td><td class="muted ellipsis" style="max-width:220px" title="${esc(r.evidence.join("\n"))}">${esc(r.evidence.slice(-3).join(", "))}</td>${admin ? `<td>${ib("edit", "Correct this relationship", `data-rel="${r.id}"`, "sm")}</td>` : ""}</tr>`).join("") || `<tr><td colspan="5" class="muted">No relationships yet.</td></tr>`}</table>
    <h3>Mentoring</h3><p class="muted">A mentor reviews the practice results of a learning plan; only the final evaluation certifies.</p>${ments.map(ment).join("") || '<span class="muted">None.</span>'}
    ${admin ? `<h3>Correct or add a relationship</h3><form id="rel-f"><div class="row"><div class="grow"><label for="rel-o">Other agent</label><select id="rel-o">${all.filter((x) => x.id !== a.id).map((x) => `<option value="${x.id}">${esc(x.name)}</option>`).join("")}</select></div>
      <div class="grow"><label for="rel-k" id="rel-kl">Kind (directed kinds read: ${esc(a.name)} → other)</label><select id="rel-k">${REL_KINDS.map((k) => `<option>${k}</option>`).join("")}</select></div></div>
      <div class="row"><div class="grow"><label for="rel-w">Weight 0–1 (empty: unchanged)</label><input id="rel-w" type="number" min="0" max="1" step="0.05"></div><div class="grow"><label for="rel-s">Status</label><select id="rel-s"><option value="">unchanged</option><option>active</option><option>expired</option></select></div></div>
      <label for="rel-r">Reason (audited)</label><input id="rel-r" required maxlength="500">
      <div class="row" style="margin-top:8px"><button class="btn icon primary" title="Save the correction" aria-label="Save the correction">${icon("save", 16)}</button></div></form>` : ""}`;
  if (!admin) return;
  box.querySelectorAll("[data-mfbsend]").forEach((b) => (b.onclick = async () => {
    const text = box.querySelector(`[data-mfb="${b.dataset.mfbsend}"]`).value.trim();
    if (!text) return;
    try { await api(`/api/mentoring/${b.dataset.mfbsend}/feedback`, { method: "POST", body: { text } }); toast("Feedback added"); agentModal(a.id, "links"); } catch (err) { toast(err.message); }
  }));
  box.querySelectorAll("[data-rel]").forEach((b) => (b.onclick = () => {
    const r = rels.find((x) => x.id === Number(b.dataset.rel));
    $("#rel-o").value = String(r.a_id === a.id ? r.b_id : r.a_id); $("#rel-k").value = r.kind; $("#rel-w").value = r.weight; $("#rel-s").value = r.status; $("#rel-r").focus();
    // a directed link that points at this agent is corrected from its source, so its direction is kept
    $("#rel-f").dataset.rev = r.directed && r.a_id !== a.id ? "1" : "";
    $("#rel-kl").textContent = $("#rel-f").dataset.rev ? `Kind (directed: other → ${a.name})` : `Kind (directed kinds read: ${a.name} → other)`;
  }));
  ["#rel-o", "#rel-k"].forEach((sel) => ($(sel).onchange = () => { $("#rel-f").dataset.rev = ""; $("#rel-kl").textContent = `Kind (directed kinds read: ${a.name} → other)`; }));
  $("#rel-f").onsubmit = async (e) => {
    e.preventDefault();
    const w = $("#rel-w").value;
    const rev = $("#rel-f").dataset.rev === "1", other = Number($("#rel-o").value);
    try { await api(`/api/agents/${rev ? other : a.id}/relationships`, { method: "POST", body: { other_id: rev ? a.id : other, kind: $("#rel-k").value, weight: w === "" ? null : Number(w), status: $("#rel-s").value || null, reason: $("#rel-r").value } }); toast("Corrected"); agentModal(a.id, "links"); } catch (err) { toast(err.message); }
  };
}

// --- Tools & MCP ----------------------------------------------------------------------------
async function viewMcp() {
  const [d, agents, calls] = await Promise.all([api("/api/mcp"), api("/api/agents"), me.is_admin ? api("/api/mcp/calls") : Promise.resolve([])]);
  $("#ag-tools").innerHTML = me.is_admin ? ib("plus", "Add a server", 'id="mcp-new"', "primary") : "";
  const st = (s) => `<span class="pill ${s.status === "ok" ? "ok" : s.status === "error" ? "failed" : "pending"}">${esc(s.status)}</span>`;
  $("#ag-body").innerHTML = `<div class="ag-split"><section class="card ag-scroll"><h2>MCP servers</h2><p class="muted">Servers the agents may use. Claude Code agents get the servers they are granted, with only the allowed tools. Secrets (header and environment values) never leave the server. Every call is logged.</p>
      ${d.servers.map((s) => `<div class="mcp-srv card"><div class="row nowrap"><strong class="grow">${esc(s.name)}</strong>${st(s)}<span class="pill">${esc(s.transport)}</span><span class="pill">${s.everyone ? "every agent" : `${s.grants.length} agent(s)`}</span>
          ${me.is_admin ? ib("refresh", "Test: connect and list the tools", `data-mtest="${s.id}"`, "sm") + ib("edit", "Edit", `data-medit="${s.id}"`, "sm") + ib("trash", "Delete", `data-mdel="${s.id}"`, "sm danger") : ""}</div>
        <div class="muted mono ellipsis">${esc(s.transport === "http" ? s.url : [s.command, ...s.args].join(" "))}${s.env.length || s.headers.length ? ` · secrets: ${esc([...s.env, ...s.headers].join(", "))}` : ""}</div>
        ${s.error ? `<div class="cc-error">${esc(s.error)}</div>` : ""}
        ${s.tools.length ? `<details><summary>${s.tools.filter((t) => t.allowed).length} of ${s.tools.length} tools allowed</summary><div class="mcp-tools">${s.tools.map((t) => `<label class="row nowrap"><input type="checkbox" data-mtool="${s.id}" value="${esc(t.name)}" ${t.allowed ? "checked" : ""} ${me.is_admin ? "" : "disabled"} style="width:auto"><code>${esc(t.name)}</code>${t.read_only ? ' <span class="pill ok">read-only</span>' : ""}${t.changed ? ' <span class="pill pending" title="Its description or input schema changed since it was allowed: tick it again if the change is expected">changed</span>' : ""}<span class="muted ellipsis grow">${esc(t.description)}</span></label>`).join("")}</div>
          ${me.is_admin ? `<div class="row">${ib("save", "Save allowed tools", `data-msave="${s.id}"`, "sm primary")}<span class="muted">Who may use it:</span><select data-mgrant="${s.id}" multiple size="3" style="width:auto;min-width:200px">${agents.filter((a) => a.lifecycle === "active").map((a) => `<option value="${a.id}" ${s.grants.includes(a.id) ? "selected" : ""}>${esc(a.name)}</option>`).join("")}</select><label class="row" style="margin:0"><input type="checkbox" data-mevery="${s.id}" ${s.everyone ? "checked" : ""} style="width:auto"> every agent</label>${ib("check", "Save who may use it", `data-mgsave="${s.id}"`, "sm")}</div>` : ""}</details>` : ""}</div>`).join("") || '<p class="muted">No servers yet: add one from the catalog.</p>'}</section>
    <aside class="card ag-scroll"><h2>Catalog</h2><p class="muted">One click: added, tested and ready to grant.</p>
      ${d.catalog.map((c) => `<div class="mcp-cat"><div class="row nowrap"><strong class="grow">${esc(c.name)}</strong><span class="pill">${esc(c.transport)}</span>${c.added ? pill("ok").replace("ok", "added") : me.is_admin ? ib("plus", `Add ${c.name}`, `data-mcat="${esc(c.key)}"`, "sm primary") : ""}</div><div class="muted">${esc(c.about)}</div></div>`).join("")}
      ${calls.length ? `<h3>Recent calls</h3><div class="events">${calls.slice(0, 30).map((c) => `<div>${c.ok ? "✓" : "✗"} <span class="k">${esc(c.server)}</span>${esc(c.tool)} <span class="muted">${esc(c.actor)} · ${c.duration}s · ${ago(c.at)}</span></div>`).join("")}</div>` : ""}</aside></div>`;
  const run = async (fn, ok) => { try { await fn(); if (ok) toast(ok); viewMcp(); } catch (e) { toast(e.message); viewMcp(); } };
  $("#mcp-new") && ($("#mcp-new").onclick = () => mcpForm());
  document.querySelectorAll("[data-mtest]").forEach((b) => (b.onclick = () => { b.disabled = true; run(() => api(`/api/mcp/servers/${b.dataset.mtest}/test`, { method: "POST" }), "Connected"); }));
  document.querySelectorAll("[data-medit]").forEach((b) => (b.onclick = () => mcpForm(d.servers.find((s) => s.id === Number(b.dataset.medit)))));
  document.querySelectorAll("[data-mdel]").forEach((b) => (b.onclick = () => confirm("Delete this server and its grants?") && run(() => api(`/api/mcp/servers/${b.dataset.mdel}`, { method: "DELETE" }), "Deleted")));
  document.querySelectorAll("[data-msave]").forEach((b) => (b.onclick = () => run(() => api(`/api/mcp/servers/${b.dataset.msave}/tools`, { method: "PUT", body: { allowed: [...document.querySelectorAll(`[data-mtool="${b.dataset.msave}"]:checked`)].map((c) => c.value) } }), "Saved")));
  document.querySelectorAll("[data-mgsave]").forEach((b) => (b.onclick = () => { const id = b.dataset.mgsave; run(() => api(`/api/mcp/servers/${id}/grants`, { method: "PUT", body: { agent_ids: [...document.querySelector(`[data-mgrant="${id}"]`).selectedOptions].map((o) => Number(o.value)), everyone: document.querySelector(`[data-mevery="${id}"]`).checked } }), "Saved"); }));
  document.querySelectorAll("[data-mcat]").forEach((b) => (b.onclick = () => {
    const c = d.catalog.find((x) => x.key === b.dataset.mcat);
    if (!c.fields.length) return run(() => api(`/api/mcp/catalog/${c.key}`, { method: "POST", body: { values: {} } }), `Added ${c.name}`);
    modal(`<h2>${icon("plug", 18)} Add ${esc(c.name)}</h2><p class="muted">${esc(c.about)}</p><form id="mc-f">${c.fields.map((f) => `<label>${esc(f.label)}${f.optional ? " (optional)" : ""}</label><input type="password" autocomplete="off" data-mf="${esc(f.key)}" ${f.optional ? "" : "required"}>`).join("")}
      <div class="row" style="margin-top:12px"><button class="btn icon primary" title="Add" aria-label="Add">${icon("check", 16)}</button></div></form>`);
    $("#mc-f").onsubmit = (e) => { e.preventDefault(); const values = Object.fromEntries([...document.querySelectorAll("[data-mf]")].map((i) => [i.dataset.mf, i.value])); $("#modal").hidden = true; run(() => api(`/api/mcp/catalog/${c.key}`, { method: "POST", body: { values } }), `Added ${c.name}`); };
  }));
}
function mcpForm(s = null) {
  const pairs = (names) => (names || []).map((n) => `${n}=`).join("\n");
  modal(`<h2>${icon("plug", 18)} ${s ? `Edit ${esc(s.name)}` : "New MCP server"}</h2><form id="ms-f">
    <label>Name</label><input id="ms-name" required value="${esc(s?.name || "")}">
    <label>Transport</label><select id="ms-tr"><option value="stdio" ${s?.transport === "stdio" ? "selected" : ""}>Local command (stdio)</option><option value="http" ${s?.transport === "http" ? "selected" : ""}>Remote (Streamable HTTP)</option></select>
    <div id="ms-stdio"><label>Command</label><input id="ms-cmd" value="${esc(s?.command || "")}" placeholder="npx"><label>Arguments (one per line)</label><textarea id="ms-args" rows="3">${esc((s?.args || []).join("\n"))}</textarea>
      <label>Environment (NAME=value per line; an empty value keeps the stored one)</label><textarea id="ms-env" rows="2" class="mono">${esc(pairs(s?.env))}</textarea></div>
    <div id="ms-http"><label>URL</label><input id="ms-url" value="${esc(s?.url || "")}" placeholder="https://example.com/mcp"><label>Headers (Name=value per line; empty keeps the stored value)</label><textarea id="ms-hdr" rows="2" class="mono">${esc(pairs(s?.headers))}</textarea></div>
    <label class="row" style="margin-top:8px"><input type="checkbox" id="ms-every" style="width:auto" ${s?.everyone === false ? "" : "checked"}> Every agent may use it</label>
    <div class="row" style="margin-top:12px"><button class="btn icon primary" title="Save" aria-label="Save">${icon("save", 16)}</button></div></form>`);
  const sync = () => { const h = $("#ms-tr").value === "http"; $("#ms-http").hidden = !h; $("#ms-stdio").hidden = h; };
  $("#ms-tr").onchange = sync; sync();
  const kv = (t) => Object.fromEntries(t.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => [l.split("=")[0].trim(), l.slice(l.indexOf("=") + 1)]));
  $("#ms-f").onsubmit = async (e) => {
    e.preventDefault();
    const body = { name: $("#ms-name").value, transport: $("#ms-tr").value, command: $("#ms-cmd").value, args: $("#ms-args").value.split("\n").map((x) => x.trim()).filter(Boolean),
      url: $("#ms-url").value, env: kv($("#ms-env").value), headers: kv($("#ms-hdr").value), everyone: $("#ms-every").checked, enabled: true };
    try { const r = await api(s ? `/api/mcp/servers/${s.id}` : "/api/mcp/servers", { method: s ? "PUT" : "POST", body }); $("#modal").hidden = true; await api(`/api/mcp/servers/${r.id}/test`, { method: "POST" }).catch((x) => toast(x.message)); viewMcp(); }
    catch (err) { toast(err.message); }
  };
}

// --- Skills & plugins (the management page the GM adds to) ----------------------------------
async function viewCatalog() {
  const items = await api("/api/catalog");
  const can = me.is_admin || me.permissions?.manage_agents;
  $("#ag-tools").innerHTML = can ? ib("plus", "Add a skill, plugin, rule, command or hook", 'id="cat-new"', "primary") : "";
  const KIND = { plugin: "Plugins", skill: "Skills", rule: "Rules", command: "Commands", hook: "Hooks (fail-open)" };
  $("#ag-body").innerHTML = `<div class="cat-grid">${Object.entries(KIND).map(([k, label]) => `<section class="card ag-scroll"><h2>${label} <span class="muted">${items.filter((i) => i.kind === k).length}</span></h2>
    ${items.filter((i) => i.kind === k).map((i) => `<div class="cat-item ${i.enabled ? "" : "off"}"><div class="row nowrap"><strong class="grow">${k === "command" ? "/" : ""}${esc(i.name)}</strong>${i.builtin ? "" : `<span class="pill">${esc(i.created_by || "added")}</span>`}${can ? ib("edit", "Edit", `data-cedit="${i.id}"`, "sm") + ib("trash", "Delete", `data-cdel="${i.id}"`, "sm danger") : ""}</div><div class="muted">${esc(i.description)}</div></div>`).join("") || '<p class="muted">None.</p>'}</section>`).join("")}</div>
    <p class="muted" style="margin-top:8px">Agents take what they need from here; the General Manager adds what is missing (<code>gm add</code>). Projects → ${icon("download", 12)} writes it into a project's <code>.claude/</code>.</p>`;
  $("#cat-new") && ($("#cat-new").onclick = () => catalogForm());
  document.querySelectorAll("[data-cedit]").forEach((b) => (b.onclick = () => catalogForm(items.find((i) => i.id === Number(b.dataset.cedit)))));
  document.querySelectorAll("[data-cdel]").forEach((b) => (b.onclick = async () => { if (!confirm("Delete this item?")) return; await api(`/api/catalog/${b.dataset.cdel}`, { method: "DELETE" }); viewCatalog(); }));
}
function catalogForm(i = null) {
  modal(`<h2>${icon("book", 18)} ${i ? `Edit ${esc(i.name)}` : "Add to the catalog"}</h2><form id="cf">
    <label>Kind</label><select id="cf-kind" ${i ? "disabled" : ""}>${["skill", "plugin", "rule", "command", "hook"].map((k) => `<option ${i?.kind === k ? "selected" : ""}>${k}</option>`).join("")}</select>
    <label>Name</label><input id="cf-name" required value="${esc(i?.name || "")}" ${i ? "readonly" : ""} placeholder="kubernetes">
    <label>Description</label><input id="cf-desc" value="${esc(i?.description || "")}">
    <label>Content (SKILL.md body, rule text, command prompt)</label><textarea id="cf-content" rows="10" class="mono">${esc(i?.content || "")}</textarea>
    <div class="row" style="margin-top:12px"><button class="btn icon primary" title="Save" aria-label="Save">${icon("save", 16)}</button></div></form>`);
  $("#cf").onsubmit = async (e) => {
    e.preventDefault();
    try {
      if (i) await api(`/api/catalog/${i.id}`, { method: "PATCH", body: { description: $("#cf-desc").value, content: $("#cf-content").value } });
      else await api("/api/catalog", { method: "POST", body: { kind: $("#cf-kind").value, name: $("#cf-name").value, description: $("#cf-desc").value, content: $("#cf-content").value } });
      $("#modal").hidden = true; viewCatalog();
    } catch (err) { toast(err.message); }
  };
}

registerView({ id: "agents", title: "Agents", icon: "agents" }, viewAgents);
