"use strict";
// Projects: the project list (left; administrators see every project, others their own), the project's
// General Manager in the middle (a Claude Code conversation in the project's folder, as in the Workspace,
// for administrators; the Orders window, where an order becomes a goal for the agent loop, for everyone),
// and the agents on the right: who works on it and their organisation, top to bottom. Files opens a file
// panel beside them. The GM panels are kept per project, so switching projects never reloads them.
// The Orders window has topics (ASEAO05 S05-R-07): conversation lanes, one tab each; an order's goal reports in
// the lane it was sent from. The search box looks through every topic of the project.

let selectedProject = Number(store.get("ac.project")) || null;
let projectChecked = new Set();
let lastMessageId = 0;
let pendingUploads = [];
let projectMode = store.get("ac.project.mode") || "gm";  // gm | orders | plan
let projectFiles = store.get("ac.project.files") === "1";
const gmPanels = new Map();  // project id -> { el, panel }
let projectSearch = "";
let projectTopics = [];
const topicOf = (id) => store.get(`ac.project.topic.${id}`) || "general";
const CLASS_RANK = { public: 0, internal: 1, confidential: 2, restricted: 3, secret: 4 };

async function viewProjects(quiet) {
  // The selected project's details load alongside the list: one round trip instead of two.
  const guess = selectedProject, early = guess !== null ? api(`/api/projects/${guess}`).catch(() => null) : null;
  const list = await api("/api/projects");
  if (!list.some((p) => p.id === selectedProject)) selectedProject = (list.find((p) => !p.protected) || list[0])?.id ?? null;
  if (!quiet || !$("#proj-list")) {
    $("#view").innerHTML = `<div class="proj${projectFiles ? " with-files" : ""}">
      <aside class="card proj-side"><div class="row nowrap"><h2 class="grow" style="margin:0">Projects</h2>${me.is_admin || me.permissions?.create_projects ? ib("plus", "New project", 'id="p-new"', "primary sm") : ""}</div>
        <div class="list" id="proj-list" style="margin-top:8px"></div>
        ${me.is_admin ? `<div class="row" style="margin-top:8px">${ib("trash", "Delete the selected projects", 'id="p-del" disabled', "danger sm")}</div>` : ""}</aside>
      <section class="card proj-main" id="proj-main"><p class="muted">Create a project to start.</p></section>
      <aside class="card proj-rail" id="proj-rail"></aside>
      <aside class="card proj-files" id="proj-files" ${projectFiles ? "" : "hidden"}></aside></div>`;
    applyProjectLayout();
    $("#p-new") && ($("#p-new").onclick = () => projectForm());
    $("#p-del") && ($("#p-del").onclick = deleteProjects);
    lastMessageId = 0;
  }
  $("#proj-list").innerHTML = list.map((p) => `<div class="list-item ${p.id === selectedProject ? "selected" : ""}" data-id="${p.id}">
    <div class="row nowrap">${me.is_admin && !p.protected ? `<input type="checkbox" data-check="${p.id}" ${projectChecked.has(p.id) ? "checked" : ""} style="width:auto" aria-label="Select ${esc(p.name)}">` : ""}
    <span class="avatar proj-av" style="width:26px;height:26px">${icon(p.protected ? "settings" : "projects", 16)}</span><strong class="grow">${esc(p.name)}</strong>
    ${p.running_goals ? `<span class="pill running" title="running goals">${p.running_goals}</span>` : ""}</div>
    <div class="muted ellipsis">${p.protected ? "platform · protected" : esc(p.github_url || p.path)}${p.running_agents ? ` · ${p.running_agents} agent(s) working` : ""}</div></div>`).join("") || '<p class="muted">No projects yet.</p>';
  $("#proj-list").querySelectorAll(".list-item").forEach((el) => (el.onclick = (e) => {
    if (e.target.matches("input[type=checkbox]")) return;
    if (e.ctrlKey || e.metaKey) { toggleCheck(Number(el.dataset.id)); return; }
    selectedProject = Number(el.dataset.id); store.set("ac.project", selectedProject); lastMessageId = 0; viewProjects(true);
  }));
  $("#proj-list").querySelectorAll("[data-check]").forEach((c) => (c.onchange = () => toggleCheck(Number(c.dataset.check))));
  if ($("#p-del")) $("#p-del").disabled = projectChecked.size === 0;
  if (selectedProject !== null) await projectMain(selectedProject, quiet, selectedProject === guess ? early : null);  // projectMain keeps what is already shown
}

function toggleCheck(id) {
  if (projectChecked.has(id)) projectChecked.delete(id); else projectChecked.add(id);
  viewProjects(true);
}

async function deleteProjects() {
  if (!projectChecked.size || !confirm(`Delete ${projectChecked.size} project(s)? Running goals are cancelled; repositories on disk are kept.`)) return;
  try { await api("/api/projects/delete", { method: "POST", body: { ids: [...projectChecked] } }); projectChecked.clear(); viewProjects(); } catch (e) { toast(e.message); }
}

async function projectForm(p = null) {
  const base = p ? "" : await api("/api/projects-defaults").then((d) => d.base).catch(() => "");
  const rooms = p && me.is_admin ? await api("/api/rooms").catch(() => []) : [];
  const rules = p && me.is_admin && !p.protected ? await api(`/api/projects/${p.id}/policy`).catch(() => null) : null;
  const gms = p && me.is_admin ? (await api("/api/agents").catch(() => [])).filter((a) => a.lifecycle === "active" && (a.role === "general_manager" || a.id === p.manager_id)) : [];
  modal(`<h2>${icon("projects", 18)} ${p ? "Edit project" : "New project"}</h2><form id="pf">
    <label for="pf-name">Name</label><input id="pf-name" required value="${esc(p?.name || "")}" ${p?.protected ? "disabled" : ""}>
    ${p || !me.is_admin ? "" : `<label for="pf-path">Work folder (git repository; by default a new folder next to Agent Company)</label><div class="row nowrap"><input id="pf-path" class="grow" placeholder="${esc(base)}/my-project">${typeof pickFolder === "function" ? ib("folder", "Browse…", 'type="button" id="pf-browse"') : ""}</div>`}
    <label for="pf-gh">GitHub URL (recommended; approved work is pushed here)</label><input id="pf-gh" placeholder="https://github.com/owner/repo.git" value="${esc(p?.github_url || "")}">
    <label for="pf-desc">Description</label><textarea id="pf-desc">${esc(p?.description || "")}</textarea>
    ${me.is_admin ? `<label for="pf-checks">Quality commands (one per line)</label><textarea id="pf-checks" placeholder="uv run pytest -q">${esc((p?.quality_commands || []).join("\n"))}</textarea>` : ""}
    ${me.is_admin && p ? `<div class="row"><div class="grow"><label for="pf-stack">Stack (comma-separated)</label><input id="pf-stack" placeholder="python, fastapi" value="${esc((p.stack || []).join(", "))}"></div>
      <div class="grow"><label for="pf-skills">Required skills (comma-separated)</label><input id="pf-skills" placeholder="api, testing" value="${esc((p.required_skills || []).join(", "))}"></div></div>
      <label>Participating rooms (none checked: every room; the planner only uses these rooms' roles)</label><div class="plug-picks">${rooms.filter((r) => r.enabled || (p.rooms || []).includes(r.key)).map((r) => `<label class="plug"><input type="checkbox" data-room="${esc(r.key)}" ${(p.rooms || []).includes(r.key) ? "checked" : ""}><span>${esc(r.name)}${r.enabled ? "" : " (disabled)"}</span></label>`).join("")}</div>
      ${gms.length > 1 ? `<div class="row"><div class="grow"><label for="pf-gm">General Manager (a change is kept in the position history)</label><select id="pf-gm">${gms.map((a) => `<option value="${a.id}" ${a.id === p.manager_id ? "selected" : ""}>${esc(a.name)}</option>`).join("")}</select></div>
      <div class="grow"><label for="pf-gm-why">Reason for a change</label><input id="pf-gm-why"></div></div>` : ""}
      <div class="row"><div class="grow"><label for="pf-budget">Budget, $ (caps all its goals; 0 = none; $${(p.spent_usd || 0).toFixed(2)} used)</label><input id="pf-budget" type="number" min="0" step="0.01" value="${p.budget_usd || 0}"></div>
      <div class="grow"><label for="pf-deadline">Deadline</label><input id="pf-deadline" type="date" value="${esc((p.deadline || "").slice(0, 10))}"></div>
      <div class="grow"><label for="pf-prio">Priority (queue order)</label><select id="pf-prio">${["low", "normal", "high", "critical"].map((c) => `<option ${(p.priority || "normal") === c ? "selected" : ""}>${c}</option>`).join("")}</select></div></div>` : ""}
    ${rules ? `<h3>Stricter rules for this project</h3><p class="muted">Empty or 0 = the company rule. A project may only tighten them (Settings → Policy versions keeps every change).</p>
      <div class="row"><div class="grow"><label for="pr-cost">Goal budget, $ (company: ${rules.company.goal_budget_usd || "none"})</label><input id="pr-cost" type="number" min="0" step="0.01" value="${rules.own.goal_budget_usd || ""}"></div>
      <div class="grow"><label for="pr-tok">Goal tokens (company: ${rules.company.goal_token_budget || "none"})</label><input id="pr-tok" type="number" min="0" value="${rules.own.goal_token_budget || ""}"></div>
      <div class="grow"><label for="pr-ag">Agents at once (company: ${rules.company.goal_agent_limit || "none"})</label><input id="pr-ag" type="number" min="0" value="${rules.own.goal_agent_limit || ""}"></div></div>
      <div class="row"><label class="row grow"><input type="checkbox" id="pr-appr" style="width:auto" ${rules.effective.require_approval ? "checked" : ""} ${rules.company.require_approval ? "disabled" : ""}> Merges wait for a person</label>
      <div class="grow"><label for="pr-rec">Recruitment approval</label><select id="pr-rec">${["never", "permanent_only", "always"].map((c, i, all) => all.indexOf(rules.company.recruit_requires_approval) <= i ? `<option ${rules.effective.recruit_requires_approval === c ? "selected" : ""}>${c}</option>` : "").join("")}</select></div>
      <div class="grow"><label for="pr-sbx" title="Quality commands of this project's goals run agent code: required = only in the L3 sandbox">Sandbox for agent code</label><select id="pr-sbx">${["off", "auto", "required"].map((c, i, all) => all.indexOf(rules.company.sandbox) <= i ? `<option ${rules.effective.sandbox === c ? "selected" : ""}>${c}</option>` : "").join("")}</select></div></div>` : ""}
    ${me.is_admin && p ? `<label for="pf-class">Classification (what agents may carry to other projects: public and internal lessons only; secret ones are not stored)</label><select id="pf-class">${["public", "internal", "confidential", "restricted", "secret"].map((c) => `<option ${(p.classification || "internal") === c ? "selected" : ""}>${c}</option>`).join("")}</select>` : ""}
    <div class="row" style="margin-top:12px"><button class="btn icon primary" title="${p ? "Save" : "Create"}" aria-label="${p ? "Save" : "Create"}">${icon(p ? "save" : "check", 16)}</button></div></form>`);
  // The folder follows the name until it is edited: <folder above Agent Company>/<name>.
  let edited = false;
  const slug = (n) => n.trim().replace(/[^\w.-]+/g, "-").replace(/^-+|-+$/g, "");
  if ($("#pf-path")) {
    $("#pf-path").oninput = () => (edited = true);
    $("#pf-name").oninput = () => { if (!edited) $("#pf-path").value = $("#pf-name").value.trim() ? `${base}/${slug($("#pf-name").value)}` : ""; };
  }
  $("#pf-browse") && ($("#pf-browse").onclick = async () => { const dir = await pickFolder($("#pf-path").value || base); if (dir) { $("#pf-path").value = dir; edited = true; } });
  $("#pf").onsubmit = async (e) => {
    e.preventDefault();
    const body = { github_url: $("#pf-gh").value.trim(), description: $("#pf-desc").value };
    if ($("#pf-checks")) body.quality_commands = $("#pf-checks").value.split("\n").map((x) => x.trim()).filter(Boolean);
    if ($("#pf-class")) body.classification = $("#pf-class").value;
    if ($("#pf-prio")) {
      const list = (v) => v.split(",").map((x) => x.trim()).filter(Boolean);
      Object.assign(body, { stack: list($("#pf-stack").value), required_skills: list($("#pf-skills").value), priority: $("#pf-prio").value,
        rooms: [...document.querySelectorAll("[data-room]:checked")].map((c) => c.dataset.room), budget_usd: +$("#pf-budget").value || 0,
        deadline: $("#pf-deadline").value || null });
    }
    if ($("#pf-gm") && Number($("#pf-gm").value) !== p.manager_id) Object.assign(body, { manager_id: Number($("#pf-gm").value), manager_reason: $("#pf-gm-why").value });
    if (!p?.protected) body.name = $("#pf-name").value.trim();
    try {
      if (p) await api(`/api/projects/${p.id}`, { method: "PATCH", body });
      if (rules) await api(`/api/projects/${p.id}/policy`, { method: "PUT", body: { goal_budget_usd: +$("#pr-cost").value || 0, goal_token_budget: +$("#pr-tok").value || 0,
        goal_agent_limit: +$("#pr-ag").value || 0, require_approval: $("#pr-appr").checked && !rules.company.require_approval, recruit_requires_approval: $("#pr-rec").value, sandbox: $("#pr-sbx").value } });
      else { const r = await api("/api/projects", { method: "POST", body: { ...body, path: $("#pf-path")?.value.trim() || "" } }); selectedProject = r.id; }
      $("#modal").hidden = true; lastMessageId = 0; viewProjects();
    } catch (err) { toast(err.message); }
  };
}

const LIFE = { active: [["paused", "Pause"], ["completed", "Complete"], ["archived", "Archive"], ["cancelled", "Cancel"]],
  paused: [["active", "Resume"], ["archived", "Archive"], ["cancelled", "Cancel"]], archived: [["active", "Restore"]],
  completed: [["archived", "Archive"]], cancelled: [] };

const KIND_LABEL = { command: "", plan: "Plan", result: "Result", error: "Problem", proposal: "Proposal", standup: "Stand-up", chat: "" };

function messageHtml(m, count) {
  const who = m.author_type === "user" ? esc(m.meta?.author || "You") : m.agent ? esc(m.agent) : "System";
  const files = (m.attachments || []).map((a) => a.content_type.startsWith("image/") ? `<a href="/api/uploads/${a.id}" target="_blank" rel="noopener"><img src="/api/uploads/${a.id}" alt="${esc(a.name)}" class="thumb"></a>` : `<a href="/api/uploads/${a.id}" target="_blank" rel="noopener">${esc(a.name)}</a>`).join(" ");
  return `<div class="msg by-${m.author_type} ${m.kind}" data-mid="${m.id}"><div class="msg-head row nowrap"><strong>${who}</strong>${KIND_LABEL[m.kind] ? ` <span class="pill ${m.kind === "error" ? "failed" : m.kind === "result" ? "done" : "info"}">${KIND_LABEL[m.kind]}</span>` : ""}${m.goal_id ? ` <a href="#" data-goal="${m.goal_id}" class="muted">#${m.goal_id}</a>` : ""}${projectSearch && m.topic ? ` <span class="pill">${esc(m.topic)}</span>` : ""}${m.meta?.classification ? ` <span class="pill pending" title="only models cleared for it receive this order">${esc(m.meta.classification)}</span>` : ""}${count > 1 ? ` <span class="pill" title="identical messages collapsed">×${count}</span>` : ""}<span class="muted grow" style="text-align:right">${ago(m.at)}</span><button class="icon-btn" data-copy="${m.id}" title="Copy" aria-label="Copy message">${icon("copy", 14)}</button></div>
    <div class="msg-body">${esc(m.content)}</div>${files ? `<div class="row">${files}</div>` : ""}</div>`;
}

let messageCache = [];
function renderMessages(append) {
  // Identical consecutive messages from the same author collapse into one with a count.
  const groups = [];
  for (const m of messageCache) {
    const last = groups[groups.length - 1];
    if (last && last.m.content === m.content && last.m.author_type === m.author_type && last.m.agent_id === m.agent_id && last.m.kind === m.kind) last.n++;
    else groups.push({ m, n: 1 });
  }
  const box = $("#msgs");
  if (!box) return;
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  box.innerHTML = groups.map((g) => messageHtml(g.m, g.n)).join("") || (projectSearch ? '<p class="muted">No message in this project contains that.</p>' : '<p class="muted">No messages yet. Give the General Manager an order below.</p>');
  if (atBottom || !append) box.scrollTop = box.scrollHeight;
  box.querySelectorAll("[data-copy]").forEach((b) => (b.onclick = () => { const m = messageCache.find((x) => x.id === Number(b.dataset.copy)); navigator.clipboard?.writeText(m.content).then(() => toast("Copied"), () => toast("Copy failed")); }));
  box.querySelectorAll("[data-goal]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); goalModal(Number(a.dataset.goal)); }));
}

// The General Manager as Claude Code in the project's folder (administrators): one panel per project, kept.
function gmPanel(d) {
  const prov = d.gm?.kind === "codex" ? "codex" : "claude", key = `${d.id}:${prov}`;
  let g = gmPanels.get(key);
  if (!g) {
    const el = document.createElement("aside");
    el.className = "wc proj-gm";
    const panel = WSP.makeClaudePanel({ key: `ac.proj.${d.id}.gm${prov === "claude" ? "" : "." + prov}`, root: () => d.path, projectId: () => d.id, provider: prov });
    g = { el, panel, ready: panel.init(el) };
    gmPanels.set(key, g);
  }
  return g;
}

async function projectMain(id, incremental, early = null) {
  const d = (early && await early) || await api(`/api/projects/${id}`);
  // The GM conversation is a CLI agent (Claude Code or Codex, whichever the GM resolves to now) and the
  // administrators' tool (CLAUDE.md); with an API model as GM, or for other users, orders go to the agent loop.
  const cli = ["claude_code", "codex"].includes(d.gm?.kind);
  const gmOk = me.is_admin && cli;
  const mode = projectMode === "plan" ? "plan" : gmOk ? projectMode : "orders";
  const main = $("#proj-main");
  const gmKey = `${mode}:${d.gm?.kind}:${d.gm?.model}`;
  if (!incremental || main.dataset.pid !== String(id) || main.dataset.mode !== gmKey) {
    main.dataset.pid = id; main.dataset.mode = gmKey;
    main.innerHTML = `<div class="row nowrap proj-head"><div class="grow"><h2 style="margin:0">${esc(d.name)} ${d.protected ? '<span class="pill">protected</span>' : ""}${d.status !== "active" ? ` <span class="pill pending" title="${esc(d.status_reason || "")}">${esc(d.status)}</span>` : ""}${d.priority && d.priority !== "normal" ? ` <span class="pill info">${esc(d.priority)}</span>` : ""}${d.deadline ? ` <span class="pill ${d.overdue ? "failed" : ""}" title="deadline">${esc(d.deadline.slice(0, 10))}${d.overdue ? " overdue" : ""}</span>` : ""}${d.budget_usd ? ` <span class="pill ${d.spent_usd >= d.budget_usd ? "failed" : ""}" title="budget used">${money(d.spent_usd)} / ${money(d.budget_usd)}</span>` : ""}</h2><div class="muted mono ellipsis">${esc(d.path)}${d.github_url ? " · " + esc(d.github_url) : ""}</div></div>
        ${me.is_admin ? ib("edit", "Edit the project", 'id="p-edit"', "sm") + ib("download", "Write the company catalog into this project's .claude/ (agents, rules, skills, commands, hooks, .mcp.json)", 'id="p-config"', "sm") + ib("git", d.github_url ? "Sync to GitHub" : "Set a GitHub URL first", `id="p-sync" ${d.github_url ? "" : "disabled"}`, "sm") + ib("workspace", "Open the folder in the Workspace", 'id="p-wsp"', "sm") : ""}
        ${ib("folder", projectFiles ? "Hide files" : "Show files", 'id="p-files"', `sm ${projectFiles ? "on" : ""}`)}${ib("stop", "Stop the project's running goals", 'id="p-stop"', "sm danger")}${(me.is_admin || d.created_by === me.id) && !d.protected && LIFE[d.status]?.length ? `<select id="p-life" aria-label="Project status" title="Pause, resume, archive, complete or cancel the project" style="width:auto"><option value="">${esc(d.status)}…</option>${LIFE[d.status].map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}</select>` : ""}</div>
      <div class="ag-tabs small proj-modes" role="tablist">${gmOk ? `<button role="tab" data-pmode="gm" aria-selected="${mode === "gm"}">${icon("general_manager", 14)} General Manager</button>` : ""}<button role="tab" data-pmode="orders" aria-selected="${mode === "orders"}">${icon("send", 14)} Orders &amp; reports</button><button role="tab" data-pmode="plan" aria-selected="${mode === "plan"}">${icon("goals", 14)} Plan</button>
        <span class="grow"></span><span class="muted gm-is" title="${esc(d.gm?.reason || "")}">GM: ${esc(d.gm?.model || "–")}${cli ? "" : " · orders go to the agent loop"}</span></div>
      <div id="p-props"></div><div class="proj-body" id="proj-body"></div>`;
    main.querySelectorAll("[data-pmode]").forEach((b) => (b.onclick = () => { projectMode = b.dataset.pmode; store.set("ac.project.mode", projectMode); projectMain(id); }));
    $("#p-edit") && ($("#p-edit").onclick = () => projectForm(d));
    $("#p-sync") && ($("#p-sync").onclick = () => sendCommand(id, "/sync"));
    $("#p-stop").onclick = () => sendCommand(id, "/stop");
    $("#p-life") && ($("#p-life").onchange = async (e) => {
      const to = e.target.value; if (!to) return;
      const reason = prompt(`${to === "active" ? "Resume" : to[0].toUpperCase() + to.slice(1)} the project${to === "cancelled" ? " (its open goals are cancelled)" : to === "paused" ? " (its open goals are paused)" : ""}. Reason (optional):`, "");
      if (reason === null) { e.target.value = ""; return; }
      try { await api(`/api/projects/${id}/status`, { method: "POST", body: { status: to, reason } }); toast("Done"); projectMain(id); } catch (err) { toast(err.message); e.target.value = ""; }
    });
    $("#p-files").onclick = () => { projectFiles = !projectFiles; store.set("ac.project.files", projectFiles ? "1" : ""); $(".proj").classList.toggle("with-files", projectFiles); applyProjectLayout(); projectMain(id); };
    $("#p-wsp") && ($("#p-wsp").onclick = async () => { show("workspace"); await new Promise((r) => setTimeout(r, 300)); WSP.openRoot?.(d.path).catch((e) => toast(e.message)); });
    $("#p-config") && ($("#p-config").onclick = async () => { if (!confirm(`Write the catalog into ${d.path}/.claude/ (existing files with the same names are replaced)?`)) return; try { const r = await api(`/api/projects/${id}/claude-config`, { method: "POST" }); toast(`${r.written.length} file(s) written`); } catch (e) { toast(e.message); } });
    const body = $("#proj-body");
    const resetMessages = () => { messageCache = []; lastMessageId = 0; };
    if (mode === "gm") {
      const g = gmPanel(d);
      body.replaceChildren(g.el);
    } else if (mode === "plan") {
      body.innerHTML = '<div class="plan" id="plan"><p class="muted">Loading…</p></div>';
    } else {
      body.innerHTML = `<div class="row nowrap topic-bar"><div class="ag-tabs small grow" role="tablist" id="topics"></div>
          <input type="search" id="p-search" class="topic-search" placeholder="Search all topics" aria-label="Search this project's messages" value="${esc(projectSearch)}"></div>
        <details class="topic-summary" id="topic-summary" hidden><summary class="muted"></summary><div class="msg-body"></div></details>
        <div class="msgs" id="msgs"></div>
        <form id="composer" class="composer wc-composer"><div id="att" class="row"></div>
          <div class="wc-inrow"><textarea id="cmd" rows="3" placeholder="Order for the General Manager, a DECISION: subject :: choice line, or /standup, /status, /summary, /stop, /sync, /help. Enter sends, Shift+Enter adds a line. Paste images to attach."></textarea>
          <div class="wc-side"><label class="icon-btn sm wc-attach" for="file" title="Attach images or files" aria-label="Attach">${icon("attach", 16)}</label><button class="wc-send" title="Send (Enter)" aria-label="Send">${icon("send", 16)}</button></div></div>
          <input type="file" id="file" multiple hidden accept="image/*,text/*,.json,.yaml,.yml,.md,.pdf"><div class="row nowrap"><span class="muted grow" id="p-running"></span>
          <label class="muted row nowrap" style="margin:0" title="How classified this order and its attachments are: only models cleared for it receive them (Models). It cannot be lower than the project's.">Classification <select id="cmd-class" style="width:auto">${["", "confidential", "restricted", "secret"].filter((c) => !c || CLASS_RANK[c] > CLASS_RANK[d.classification || "internal"]).map((c) => `<option value="${c}">${c || `project's (${esc(d.classification || "internal")})`}</option>`).join("")}</select></label></div></form>`;
      resetMessages(); pendingUploads = [];
      let searchTimer = 0;
      $("#p-search").oninput = () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { projectSearch = $("#p-search").value.trim(); resetMessages(); projectMain(id, true); }, 300); };
      $("#composer").onsubmit = (e) => { e.preventDefault(); sendCommand(id); };
      $("#cmd").onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); sendCommand(id); } };
      $("#cmd").onpaste = (e) => { const files = [...(e.clipboardData?.files || [])]; if (files.length) { e.preventDefault(); files.forEach(uploadFile); } };
      $("#file").onchange = (e) => { [...e.target.files].forEach(uploadFile); e.target.value = ""; };
    }
  }
  if (mode === "orders" && $("#msgs")) {
    const qs = projectSearch ? `q=${encodeURIComponent(projectSearch)}` : `after=${lastMessageId}&topic=${encodeURIComponent(topicOf(id))}`;
    let fresh;
    [projectTopics, fresh] = await Promise.all([api(`/api/projects/${id}/topics`), api(`/api/projects/${id}/messages?${qs}`)]);
    if (!projectTopics.some((t) => t.name === topicOf(id) && (!t.archived || me.is_admin))) {  // gone or archived meanwhile
      store.set(`ac.project.topic.${id}`, "general"); messageCache = []; lastMessageId = 0;
      if (!projectSearch) fresh = await api(`/api/projects/${id}/messages?topic=general`);
    }
    renderTopics(id);
    if (projectSearch) { messageCache = fresh; renderMessages(false); }
    else if (fresh.length || !lastMessageId) { messageCache.push(...fresh); lastMessageId = messageCache.length ? messageCache[messageCache.length - 1].id : 0; renderMessages(lastMessageId && incremental); }
    $("#p-running").textContent = `${d.goals.filter((g) => GOAL_ACTIVE.includes(g.status) && g.status !== "paused").length} goal(s) running`;
  }
  if (mode === "plan" && $("#plan")) await projectPlan(id);
  const pending = d.proposals.filter((p) => p.status === "pending");
  $("#p-props").innerHTML = pending.length ? `<div class="props"><strong>Proposals waiting</strong>${pending.map((p) => `<div class="row"><span class="grow">${esc(p.title)} <span class="muted">${esc(p.agent || "")} · from #${p.goal_id}</span></span>${me.is_admin ? ib("check", "Approve", `data-prop="${p.id}" data-d="approve"`, "sm primary") + ib("x", "Reject", `data-prop="${p.id}" data-d="reject"`, "sm") : ""}</div>`).join("")}</div>` : "";
  $("#p-props").querySelectorAll("[data-prop]").forEach((b) => (b.onclick = async () => { try { await api(`/api/proposals/${b.dataset.prop}/${b.dataset.d}`, { method: "POST" }); projectMain(id, true); } catch (e) { toast(e.message); } }));
  projectRail(d);
  if (projectFiles) projectFilesPanel(d);
}

// Plan tab (ASEAO05): progress, open blockers and risks, milestones with their criteria, the next action items
// in the GM's order, and insights agents reported. Administrators edit; everyone who sees the project reads.
const SEV_PILL = { low: "idle", medium: "pending", high: "failed", critical: "failed" };
const INSIGHT_NEXT = { recorded: "analyzed", analyzed: "reviewed", reviewed: "accepted", accepted: "implemented", implemented: "evaluated" };
const day = (iso) => (iso ? iso.slice(0, 10) : "");

async function projectPlan(id) {
  const box = $("#plan");
  const [d, tr, gap] = await Promise.all([api(`/api/projects/${id}/plan`).catch((e) => ({ error: e.message })),
    api(`/api/projects/${id}/traceability`).catch(() => null), api(`/api/projects/${id}/capability_gap`).catch(() => null)]);
  if (!box || !box.isConnected) return;
  if (d.error) { box.innerHTML = `<p class="muted">${esc(d.error)}</p>`; return; }
  d.requirement_kinds = ["functional", "non_functional", "story", "use_case"];
  const admin = me.is_admin, pct = Math.round(d.progress.progress * 100);
  const risk = (r) => `<div class="list-item"><div class="row nowrap"><span class="pill ${SEV_PILL[r.severity]}">${esc(r.severity)}</span><strong class="grow ellipsis">${esc(r.title)}</strong><span class="muted">${r.kind} · ${esc(r.category.replace(/_/g, " "))}${r.automatic ? " · automatic" : ""}${r.probability != null ? ` · p ${Math.round(r.probability * 100)}%` : ""}</span>
      ${admin && !r.automatic ? (r.status === "open" ? ib("pause", "Mark mitigated", `data-risk="${r.id}" data-st="mitigated"`, "sm") : "") + ib("check", "Resolved", `data-risk="${r.id}" data-st="resolved"`, "sm") : ""}</div>
    ${r.mitigation || r.description ? `<div class="muted">${esc(r.mitigation || r.description)}</div>` : ""}</div>`;
  const crit = (m, c, i) => `<div class="row nowrap plan-crit">${c.kind === "manual" && admin && m.status === "open" ? `<input type="checkbox" data-crit="${m.id}" data-i="${i}" ${c.met ? "checked" : ""} style="width:auto" aria-label="Met">` : `<span class="pill ${c.met ? "done" : c.checked_at ? "failed" : ""}">${c.met ? "met" : c.checked_at ? "unmet" : "unchecked"}</span>`}
      <span class="grow ${c.kind === "command" ? "mono" : ""}" title="${esc(c.detail || "")}">${esc(c.text)}${c.mandatory ? "" : ' <span class="muted">(optional)</span>'}</span></div>`;
  const ms = (m) => `<div class="list-item"><div class="row nowrap"><strong class="grow ellipsis">${esc(m.name)}</strong>${pill(m.status)}${m.overdue ? '<span class="pill failed">overdue</span>' : ""}<span class="muted">${m.target_date ? day(m.target_date) + " · " : ""}${Math.round(m.progress * 100)}%</span>
      ${admin && m.status === "open" ? ib("refresh", "Run the command criteria now", `data-ms="${m.id}" data-act="check"`, "sm") + ib("check", "Complete (re-runs the commands)", `data-ms="${m.id}" data-act="complete"`, "sm primary") + ib("x", "Cancel the milestone", `data-ms="${m.id}" data-act="cancel"`, "sm") : ""}</div>
    ${m.description ? `<div class="muted">${esc(m.description)}</div>` : ""}
    ${m.deliverables.length ? `<div class="muted">Deliverables: ${m.deliverables.map(esc).join(", ")}</div>` : ""}
    ${m.goals.length ? `<div class="row">${m.goals.map((g) => `<a href="#" data-goal="${g.id}">#${g.id}</a> ${pill(g.status)}`).join(" ")}</div>` : ""}
    ${m.criteria.map((c, i) => crit(m, c, i)).join("")}</div>`;
  const item = (a) => `<div class="list-item"><div class="row nowrap"><span class="muted">${a.rank}.</span><strong class="grow ellipsis" title="${esc(a.description || "")}">${esc(a.title)}</strong><span class="pill">P${a.priority}</span>${a.overdue ? '<span class="pill failed">overdue</span>' : ""}${a.blocked_by.length ? `<span class="pill pending" title="waits for item(s) ${a.blocked_by.join(", ")}">waiting</span>` : ""}
      <span class="muted">${a.due_date ? "due " + day(a.due_date) : ""}${a.responsible ? " · " + esc(a.responsible) : ""}${a.goal_id ? ` · <a href="#" data-goal="${a.goal_id}">#${a.goal_id}</a>` : ""}</span>
      ${admin && !a.automatic ? ib("check", "Done", `data-item="${a.id}" data-st="done"`, "sm") + ib("x", "Drop", `data-item="${a.id}" data-st="dropped"`, "sm") : ""}</div></div>`;
  const ins = (i) => `<div class="list-item"><div class="row nowrap"><strong class="grow ellipsis">${esc(i.title)}</strong>${pill(i.status)}<span class="muted">${esc(i.agent || "")} · confidence ${Math.round(i.confidence * 100)}%${i.goal_id ? ` · <a href="#" data-goal="${i.goal_id}">#${i.goal_id}</a>` : ""}</span>
      ${admin && INSIGHT_NEXT[i.status] ? ib(INSIGHT_NEXT[i.status] === "accepted" ? "check" : "play", `Mark ${INSIGHT_NEXT[i.status]}`, `data-ins="${i.id}" data-st="${INSIGHT_NEXT[i.status]}"`, "sm") : ""}${admin && ["recorded", "analyzed", "reviewed"].includes(i.status) ? ib("x", "Reject", `data-ins="${i.id}" data-st="rejected"`, "sm") : ""}</div>
    ${i.description ? `<div class="muted">${esc(i.description)}</div>` : ""}${i.evidence.length ? `<div class="muted">Evidence: ${i.evidence.map(esc).join("; ")}</div>` : ""}</div>`;
  // Requirements and traceability (S08-R-01): implemented (a passing task), tested (a passing check), merged
  const tick = (ok, what) => `<span class="pill ${ok ? "done" : ""}" title="${what} (current version)">${ok ? "✓" : "–"} ${what}</span>`;
  const req = (r) => `<div class="list-item"><div class="row nowrap"><code>${esc(r.key)}</code>${pill(r.status)}<span class="muted grow">${esc(r.kind.replace("_", " "))} · v${r.version}</span>${tick(r.implemented, "implemented")}${tick(r.tested, "tested")}${tick(r.merged.length, "merged")}${r.conflicts.length ? `<span class="pill failed" title="decision(s) ${r.conflicts.map((c) => "#" + c.decision_id).join(", ")}">conflicts ${esc(r.conflicts.map((c) => c.key).join(", "))}</span>` : ""}
      ${admin && r.status === "draft" ? ib("check", "Approve", `data-req="${r.id}" data-st="approved"`, "sm primary") : ""}${admin && r.status !== "retired" ? ib("x", "Retire", `data-req="${r.id}" data-st="retired"`, "sm") : ""}</div>
    <div class="ellipsis" title="${esc(r.text)}">${esc(r.text.split("\n")[0])}</div>${r.failing.length ? `<div class="muted">Failing: ${esc(r.failing.join(" · "))}</div>` : ""}${r.goals.length ? `<div class="row">${r.goals.map((g) => `<a href="#" data-goal="${g}">#${g}</a>`).join(" ")}</div>` : ""}</div>`;
  const reqOpen = box.querySelector("#req-sec")?.open;
  const reqs = tr ? `<section class="plan-sec"><details id="req-sec" ${reqOpen ? "open" : ""}><summary class="row nowrap"><h3 class="grow" style="margin:0;display:inline">Requirements</h3>
      <span class="muted">${tr.counts.total} · ${tr.counts.implemented} implemented · ${tr.counts.tested} tested · ${tr.counts.merged} merged${tr.counts.untested ? ` · <span title="S09-R-06: agreed requirements no passing check covers in their current version: ${esc((tr.untested || []).join(", "))}">${tr.counts.untested} untested</span>` : ""}${tr.counts.conflicts ? ` · ${tr.counts.conflicts} in conflict` : ""}</span>
      ${admin ? ib("plus", "Add requirement", 'data-add="requirement"', "sm") + ib("download", "Import docs/requirements of the project", "data-req-import", "sm") : ""}</summary>
      <div class="list">${tr.requirements.map(req).join("") || '<p class="muted">No requirements yet. Goals name them by key (REQ-001) in their text.</p>'}</div></details></section>` : "";
  // Capability gap (SPEC-03 S03-R-01): available = sum of skill level/5 x free capacity x verification (a recruit's
  // uncertified skill counts half); required = the project's required skills and its open tasks needing each skill
  const caps = gap && gap.skills.length ? `<section class="plan-sec"><div class="row nowrap"><h3 class="grow" style="margin:0">Capability gap</h3><span class="muted">${gap.missing.length ? `${gap.missing.length} skill(s) short` : "covered"} · planning estimates</span></div>
      <div class="list">${gap.skills.map((r) => `<div class="row nowrap"><span class="grow">${esc(r.skill.replace(/_/g, " "))}</span><span class="muted" title="${esc(r.agents.map((a) => `${a.name}: ${a.p} × ${a.c} × ${a.v}`).join("\n"))}">${r.available}/${r.required}</span><span class="pill ${r.gap > 0 ? "failed" : "done"}">${r.gap > 0 ? `${r.gap} more needed` : "covered"}</span></div>`).join("")}</div></section>` : "";
  const section = (title, add, rows, empty) => `<section class="plan-sec"><div class="row nowrap"><h3 class="grow" style="margin:0">${title}</h3>${admin && add ? ib("plus", `Add ${add}`, `data-add="${add}"`, "sm") : ""}</div><div class="list">${rows || `<p class="muted">${empty}</p>`}</div></section>`;
  const html = `<div class="row nowrap"><strong>Progress</strong><div class="plan-bar grow" role="progressbar" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100"><span style="width:${pct}%"></span></div><span class="muted">${pct}% over ${d.progress.parts} goal(s) and milestone(s)</span></div>
    ${section("Blockers &amp; risks", "risk", d.risks.map(risk).join(""), "No open blockers or risks.")}
    ${section("Next", "action item", d.action_items.map(item).join(""), "Nothing waiting.")}
    ${section("Milestones", "milestone", d.milestones.map(ms).join(""), "No milestones yet.")}
    ${section("Insights", "", d.insights.map(ins).join(""), "Agents report insights in their task output.")}${reqs}${caps}
    ${(d.handovers || []).length ? `<section class="plan-sec"><details id="ho-sec" ${box.querySelector("#ho-sec")?.open ? "open" : ""}><summary><h3 style="margin:0;display:inline">General Manager handovers</h3> <span class="muted">${d.handovers.length}</span></summary><div class="list">${d.handovers.map((h) => `<div class="list-item"><div class="row nowrap"><strong class="grow ellipsis">${esc(h.from_agent || "(none)")} → ${esc(h.to_agent || "")}</strong><span class="muted">${esc(h.actor)} · ${day(h.at)}</span></div>
      ${h.reason ? `<div class="muted">${esc(h.reason)}</div>` : ""}<div class="muted">Open goals: ${(h.package.goals || []).map((g) => `<a href="#" data-goal="${Number(g.id)}">#${Number(g.id)}</a> ${esc(g.status)}`).join(", ") || "none"} · blockers: ${esc((h.package.blockers || []).map((r) => r.title).join("; ") || "none")} · action items: ${(h.package.action_items || []).length}${h.package.reassigned ? ` · ${Number(h.package.reassigned)} moved to the new GM` : ""}</div></div>`).join("")}</div></details></section>` : ""}`;
  if (box.dataset.html === html) return;  // the 5-s refresh never rebuilds an unchanged plan
  box.innerHTML = html; box.dataset.html = html;
  const act = async (fn) => { try { await fn(); box.dataset.html = ""; projectPlan(id); } catch (e) { toast(e.message); } };
  box.querySelectorAll("[data-goal]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); goalModal(Number(a.dataset.goal)); }));
  box.querySelectorAll("[data-add]").forEach((b) => (b.onclick = (e) => { e.preventDefault(); planForm(id, b.dataset.add, d); }));
  box.querySelectorAll("[data-req]").forEach((b) => (b.onclick = () => act(() => api(`/api/requirements/${b.dataset.req}/status`, { method: "POST", body: { status: b.dataset.st } }))));
  box.querySelector("[data-req-import]") && (box.querySelector("[data-req-import]").onclick = (e) => { e.preventDefault(); act(() => api(`/api/projects/${id}/requirements/import`, { method: "POST" }).then((r) => toast(`${r.added} new, ${r.changed} changed, ${r.unchanged} unchanged`))); });
  box.querySelectorAll("[data-risk]").forEach((b) => (b.onclick = () => act(() => api(`/api/risks/${b.dataset.risk}`, { method: "PATCH", body: { status: b.dataset.st } }))));
  box.querySelectorAll("[data-item]").forEach((b) => (b.onclick = () => act(() => api(`/api/action-items/${b.dataset.item}`, { method: "PATCH", body: { status: b.dataset.st } }))));
  box.querySelectorAll("[data-crit]").forEach((c) => (c.onchange = () => act(() => api(`/api/milestones/${c.dataset.crit}/criteria/${c.dataset.i}`, { method: "POST", body: { met: c.checked } }))));
  box.querySelectorAll("[data-ms]").forEach((b) => (b.onclick = () => act(() => b.dataset.act === "cancel"
    ? (confirm("Cancel this milestone?") ? api(`/api/milestones/${b.dataset.ms}`, { method: "PATCH", body: { status: "cancelled" } }) : null)
    : api(`/api/milestones/${b.dataset.ms}/${b.dataset.act}`, { method: "POST" }).then((m) => toast(b.dataset.act === "check" ? `${Math.round(m.progress * 100)}% met` : "Milestone completed")))));
  box.querySelectorAll("[data-ins]").forEach((b) => (b.onclick = () => act(() => {
    const body = { status: b.dataset.st };
    if (body.status === "accepted") body.as_proposal = confirm("Accepted insights become knowledge items. Also propose it as follow-up work (a proposal waiting for a decision)?");
    return api(`/api/insights/${b.dataset.ins}/status`, { method: "POST", body });
  })));
}

function planForm(id, what, d) {
  const goals = `<label for="pl-goals">Goals that must be approved (ids, comma-separated)</label><input id="pl-goals" placeholder="12, 15">`;
  const fields = {
    milestone: `<label for="pl-title">Name</label><input id="pl-title" required><label for="pl-desc">Description</label><textarea id="pl-desc"></textarea>
      <label for="pl-date">Target date</label><input id="pl-date" type="date"><label for="pl-deliv">Deliverables (one per line)</label><textarea id="pl-deliv"></textarea>${goals}
      <label for="pl-cmds">Command criteria (one per line; run on the project's current HEAD)</label><textarea id="pl-cmds" class="mono" placeholder="uv run pytest -q"></textarea>
      <label for="pl-manual">Manual criteria (one per line; a person ticks them)</label><textarea id="pl-manual"></textarea>`,
    risk: `<label for="pl-kind">Kind</label><select id="pl-kind"><option>risk</option><option>blocker</option></select><label for="pl-title">Title</label><input id="pl-title" required>
      <label for="pl-cat">Category</label><select id="pl-cat">${d.categories.map((c) => `<option value="${c}">${c.replace(/_/g, " ")}</option>`).join("")}</select>
      <label for="pl-sev">Severity</label><select id="pl-sev">${d.severities.map((c) => `<option ${c === "medium" ? "selected" : ""}>${c}</option>`).join("")}</select>
      <label for="pl-prob">Probability (risks, 0-100 %)</label><input id="pl-prob" type="number" min="0" max="100"><label for="pl-impact">Impact</label><textarea id="pl-impact"></textarea><label for="pl-mit">Mitigation</label><textarea id="pl-mit"></textarea>`,
    requirement: `<label for="pl-title">Requirement</label><textarea id="pl-title" required placeholder="The API answers within 200 ms"></textarea>
      <label for="pl-kind">Kind</label><select id="pl-kind">${d.requirement_kinds.map((k) => `<option value="${k}">${k.replace("_", " ")}</option>`).join("")}</select>
      <label for="pl-key">Key (optional; REQ-001 … by default)</label><input id="pl-key"><label for="pl-subj">Subject (optional; finds contradicting requirements)</label><input id="pl-subj">
      <label for="pl-accept">Acceptance criteria (one per line)</label><textarea id="pl-accept"></textarea>`,
    "action item": `<label for="pl-title">Title</label><input id="pl-title" required><label for="pl-desc">Description</label><textarea id="pl-desc"></textarea>
      <label for="pl-prio">Priority (1 highest - 5)</label><input id="pl-prio" type="number" min="1" max="5" value="3"><label for="pl-date">Due date</label><input id="pl-date" type="date">
      <label for="pl-deps">Waits for action items (ids, comma-separated)</label><input id="pl-deps"><label for="pl-out">Expected output</label><input id="pl-out">`,
  };
  modal(`<h2>${icon("plus", 18)} New ${what}</h2><form id="plf">${fields[what]}<div class="row" style="margin-top:12px"><button class="btn icon primary" title="Create" aria-label="Create">${icon("check", 16)}</button></div></form>`);
  const v = (sel) => $(sel)?.value.trim() || "";
  const lines = (sel) => v(sel).split("\n").map((x) => x.trim()).filter(Boolean);
  const ids = (sel) => v(sel).split(/[\s,]+/).filter(Boolean).map(Number);
  $("#plf").onsubmit = async (e) => {
    e.preventDefault();
    let url, body;
    if (what === "milestone") {
      url = `/api/projects/${id}/milestones`;
      body = { name: v("#pl-title"), description: v("#pl-desc"), target_date: v("#pl-date") || null, deliverables: lines("#pl-deliv"), goal_ids: ids("#pl-goals"),
        criteria: [...lines("#pl-cmds").map((text) => ({ kind: "command", text })), ...lines("#pl-manual").map((text) => ({ kind: "manual", text }))] };
    } else if (what === "requirement") {
      url = `/api/projects/${id}/requirements`;
      body = { text: v("#pl-title"), kind: v("#pl-kind"), key: v("#pl-key"), subject: v("#pl-subj"), acceptance: lines("#pl-accept") };
    } else if (what === "risk") {
      url = `/api/projects/${id}/risks`;
      body = { kind: v("#pl-kind"), title: v("#pl-title"), category: v("#pl-cat"), severity: v("#pl-sev"), impact: v("#pl-impact"), mitigation: v("#pl-mit"), probability: v("#pl-prob") === "" ? null : Number(v("#pl-prob")) / 100 };
    } else {
      url = `/api/projects/${id}/action-items`;
      body = { title: v("#pl-title"), description: v("#pl-desc"), priority: Number(v("#pl-prio") || 3), due_date: v("#pl-date") || null, depends_on: ids("#pl-deps"), expected_output: v("#pl-out") };
    }
    try { await api(url, { method: "POST", body }); $("#modal").hidden = true; if ($("#plan")) { $("#plan").dataset.html = ""; projectPlan(id); } } catch (err) { toast(err.message); }
  };
}

// Right edge: the agents working on the project, then their organisation (top to bottom).
async function projectRail(d) {
  const busy = d.members.filter((a) => a.current_task);
  const all = await api("/api/agents").catch(() => []);
  const byId = new Map(all.map((a) => [a.id, a])), mine = new Set(d.members.map((a) => a.id));
  // The project's org: its members and every superior up to the General Manager.
  const inOrg = new Set();
  d.members.forEach((a) => { for (let x = byId.get(a.id); x && !inOrg.has(x.id); x = byId.get(x.superior_id)) inOrg.add(x.id); });
  const kids = new Map();
  [...inOrg].forEach((id) => { const a = byId.get(id), p = inOrg.has(a.superior_id) ? a.superior_id : 0; if (!kids.has(p)) kids.set(p, []); kids.get(p).push(a); });
  const tree = (a) => `<li><span class="porg ${mine.has(a.id) ? "on" : ""} ${a.status === "busy" ? "busy" : ""}" data-aid="${a.id}">${roleIcon(a.role)}<span>${esc(a.name)}</span></span>${(kids.get(a.id) || []).length ? `<ul>${kids.get(a.id).map(tree).join("")}</ul>` : ""}</li>`;
  const html = `<div class="proj-rail-top"><div class="row nowrap"><h2 class="grow" style="margin:0">Agents</h2><span class="pill ${busy.length ? "running" : ""}">${busy.length} working</span><span class="pill">${d.members.length}</span></div>
    <div class="list" style="margin-top:8px">${d.members.map((a) => `<div class="list-item agent-row" data-aid="${a.id}"><div class="row nowrap"><span class="avatar" style="width:26px;height:26px">${roleIcon(a.role)}</span><strong class="grow">${esc(a.name)}</strong>${pill(a.current_task ? "running" : a.status)}</div>${a.current_task ? `<div class="muted ellipsis">${esc(a.current_task)}</div>` : `<div class="muted">${esc(a.role)} · L${a.level}</div>`}</div>`).join("") || '<p class="muted">Agents appear here when they work on this project.</p>'}</div></div>
    <div class="proj-rail-org"><h3>Organisation</h3><ul class="porg-tree">${(kids.get(0) || []).map(tree).join("")}</ul></div>`;
  const rail = $("#proj-rail");
  if (rail && rail.dataset.html !== html) { rail.innerHTML = html; rail.dataset.html = html; rail.querySelectorAll("[data-aid]").forEach((el) => (el.onclick = () => agentModal(Number(el.dataset.aid)))); }
}

// The file panel: browse the project's folder, open a file in the viewer.
let projectDir = {};
async function projectFilesPanel(d) {
  const box = $("#proj-files");
  if (!box) return;
  const path = projectDir[d.id] && projectDir[d.id].startsWith(d.path) ? projectDir[d.id] : d.path;
  let r;
  try { r = await api(`/api/files/list?path=${encodeURIComponent(path)}`); } catch (e) { box.innerHTML = `<p class="muted">${esc(e.message)}</p>`; return; }
  const html = `<div class="row nowrap"><h2 class="grow" style="margin:0">Files</h2>${path !== d.path ? ib("upload", "Up one folder", 'data-pup="1"', "sm") : ""}</div><div class="muted mono ellipsis">${esc(path.slice(d.path.length) || "/")}</div>
    <div class="pfiles">${r.items.filter((it) => it.name !== ".git").map((it) => `<div class="pfile" data-p="${esc(it.path)}" data-dir="${it.dir ? 1 : ""}" data-kind="${esc(it.kind)}">${WSP.fileIcon ? WSP.fileIcon(it.name, it.dir, 16) : ""}<span class="ellipsis">${esc(it.name)}</span></div>`).join("") || '<p class="muted">Empty.</p>'}</div>`;
  if (box.dataset.html === html) return;
  box.innerHTML = html; box.dataset.html = html;
  box.querySelectorAll(".pfile").forEach((el) => (el.onclick = () => { if (el.dataset.dir) { projectDir[d.id] = el.dataset.p; projectFilesPanel(d); } else openFile(el.dataset.p, el.dataset.kind); }));
  box.querySelector("[data-pup]") && (box.querySelector("[data-pup]").onclick = () => { projectDir[d.id] = path.slice(0, path.lastIndexOf("/")) || d.path; projectFilesPanel(d); });
}

async function uploadFile(file) {
  const fd = new FormData(); fd.append("file", file); fd.append("classification", $("#cmd-class")?.value || "internal");
  try {
    const res = await fetch("/api/uploads", { method: "POST", body: fd, credentials: "same-origin" });
    const data = await res.json(); if (!res.ok) throw new Error(data.detail || res.statusText);
    pendingUploads.push(data); renderAttachments();
  } catch (e) { toast(`${file.name}: ${e.message}`); }
}
function renderAttachments() {
  $("#att").innerHTML = pendingUploads.map((u, i) => `<span class="pill">${esc(u.name)} <button type="button" class="icon-btn" style="width:18px;height:18px" data-rm="${i}" aria-label="Remove">✕</button></span>`).join("");
  $("#att").querySelectorAll("[data-rm]").forEach((b) => (b.onclick = () => { pendingUploads.splice(Number(b.dataset.rm), 1); renderAttachments(); }));
}

// Topic tabs (archived ones only for administrators, who can restore them), "+" to open one, the gear for
// an administrator's topic settings.
function renderTopics(id) {
  const cur = topicOf(id), bar = $("#topics");
  if (!bar) return;
  bar.innerHTML = projectTopics.filter((t) => !t.archived || me.is_admin).map((t) => `<button role="tab" data-topic="${esc(t.name)}" aria-selected="${!projectSearch && t.name === cur}" title="${esc(t.description || t.name)}${t.agents.length ? " · " + esc(t.agents.join(", ")) : ""}">${esc(t.name)}${t.messages ? ` <span class="muted">${t.messages}</span>` : ""}${t.archived ? ' <span class="muted">(archived)</span>' : ""}</button>`).join("")
    + (me.is_admin || me.permissions?.run ? ib("plus", "Open a topic", 'id="t-new"', "sm") : "")
    + (me.is_admin ? ib("settings", "Topic settings: description, assigned agents, archive", 'id="t-edit"', "sm") : "");
  bar.querySelectorAll("[data-topic]").forEach((b) => (b.onclick = () => {
    store.set(`ac.project.topic.${id}`, b.dataset.topic); projectSearch = ""; $("#p-search").value = "";
    messageCache = []; lastMessageId = 0; projectMain(id, true);
  }));
  $("#t-new") && ($("#t-new").onclick = () => topicForm(id));
  $("#t-edit") && ($("#t-edit").onclick = () => topicForm(id, projectTopics.find((t) => t.name === cur)));
  const t = projectTopics.find((x) => x.name === cur), box = $("#topic-summary");
  box.hidden = !t?.summary || !!projectSearch;
  if (t?.summary) { box.querySelector("summary").textContent = `Summary ${ago(t.summary_at)} (/summary renews it)`; box.querySelector(".msg-body").textContent = t.summary; }
}

async function topicForm(id, t = null) {
  const agents = me.is_admin ? (await api("/api/agents")).filter((a) => a.enabled) : [];
  const chosen = new Set(t?.agent_ids || []);
  modal(`<h2>${icon("send", 18)} ${t ? `Topic '${esc(t.name)}'` : "Open a topic"}</h2><form id="tf">
    ${t ? "" : '<label for="tf-name">Name (lowercase letters, digits, spaces, dots, dashes)</label><input id="tf-name" required maxlength="64" placeholder="front end">'}
    <label for="tf-desc">What it is about</label><textarea id="tf-desc">${esc(t?.description || "")}</textarea>
    ${me.is_admin ? `<label>Assigned agents (they report in this topic's /standup)</label><div class="list" style="max-height:220px;overflow:auto">${agents.map((a) => `<label class="row nowrap"><input type="checkbox" data-agent="${a.id}" ${chosen.has(a.id) ? "checked" : ""} style="width:auto"> ${esc(a.name)} <span class="muted">${esc(a.role)}</span></label>`).join("")}</div>` : ""}
    ${t && t.name !== "general" ? `<label class="row nowrap"><input type="checkbox" id="tf-arch" ${t.archived ? "checked" : ""} style="width:auto"> Archived (takes no new messages)</label>` : ""}
    <div class="row" style="margin-top:12px"><button class="btn icon primary" title="Save" aria-label="Save">${icon(t ? "save" : "check", 16)}</button></div></form>`);
  $("#tf").onsubmit = async (e) => {
    e.preventDefault();
    const agent_ids = [...document.querySelectorAll("#tf [data-agent]:checked")].map((c) => Number(c.dataset.agent));
    try {
      if (t) {
        const body = { description: $("#tf-desc").value, agent_ids };
        if ($("#tf-arch")) body.archived = $("#tf-arch").checked;
        await api(`/api/projects/${id}/topics/${encodeURIComponent(t.name)}`, { method: "PATCH", body });
        if (body.archived) store.set(`ac.project.topic.${id}`, "general");
      } else {
        const r = await api(`/api/projects/${id}/topics`, { method: "POST", body: { name: $("#tf-name").value, description: $("#tf-desc").value, ...(me.is_admin ? { agent_ids } : {}) } });
        store.set(`ac.project.topic.${id}`, r.name);
      }
      $("#modal").hidden = true; messageCache = []; lastMessageId = 0; projectMain(id, true);
    } catch (err) { toast(err.message); }
  };
}

async function sendCommand(id, text) {
  const content = text ?? $("#cmd")?.value.trim();
  if (!content && !pendingUploads.length) return;
  try {
    await api(`/api/projects/${id}/command`, { method: "POST", body: { content, attachments: pendingUploads.map((u) => u.id), topic: projectMode === "orders" ? topicOf(id) : "general", classification: text === undefined ? $("#cmd-class")?.value || "" : "" } });
    if (text === undefined) { $("#cmd").value = ""; pendingUploads = []; renderAttachments(); }
    else toast(`${text} sent`);
    projectMain(id, true); updateBadges();
  } catch (e) { toast(e.message); }
}

registerView({ id: "projects", title: "Projects", icon: "projects" }, viewProjects, 1);
