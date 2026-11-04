"use strict";
// Projects: project list (multi-select), command window with the GM, running-agents rail.

let selectedProject = Number(store.get("ac.project")) || null;
let projectChecked = new Set();
let lastMessageId = 0;
let pendingUploads = [];

async function viewProjects(quiet) {
  const list = await api("/api/projects");
  if (!list.some((p) => p.id === selectedProject)) selectedProject = (list.find((p) => !p.protected) || list[0])?.id ?? null;
  if (!quiet || !$("#proj-list")) {
    $("#view").innerHTML = `<div class="proj">
      <aside class="card proj-side"><div class="row nowrap"><h2 class="grow" style="margin:0">Projects</h2>${me.is_admin ? `<button class="btn sm primary" id="p-new">New</button>` : ""}</div>
        <div class="list" id="proj-list" style="margin-top:8px"></div>
        ${me.is_admin ? `<div class="row" style="margin-top:8px"><button class="btn sm danger" id="p-del" disabled>Delete selected</button></div>` : ""}</aside>
      <section class="card proj-main" id="proj-main"><p class="muted">Create a project to start.</p></section>
      <aside class="card proj-rail" id="proj-rail"></aside></div>`;
    applyProjectLayout();
    $("#p-new") && ($("#p-new").onclick = () => projectForm());
    $("#p-del") && ($("#p-del").onclick = deleteProjects);
    lastMessageId = 0;
  }
  $("#proj-list").innerHTML = list.map((p) => `<div class="list-item ${p.id === selectedProject ? "selected" : ""}" data-id="${p.id}">
    <div class="row nowrap">${me.is_admin && !p.protected ? `<input type="checkbox" data-check="${p.id}" ${projectChecked.has(p.id) ? "checked" : ""} style="width:auto" aria-label="Select ${esc(p.name)}">` : ""}
    <span class="avatar" style="width:26px;height:26px">${icon(p.protected ? "settings" : "goals", 16)}</span><strong class="grow">${esc(p.name)}</strong>
    ${p.running_goals ? `<span class="pill running" title="running goals">${p.running_goals}</span>` : ""}</div>
    <div class="muted ellipsis">${p.protected ? "platform · protected" : esc(p.github_url || p.path)}${p.running_agents ? ` · ${p.running_agents} agent(s) working` : ""}</div></div>`).join("") || '<p class="muted">No projects.</p>';
  $("#proj-list").querySelectorAll(".list-item").forEach((el) => (el.onclick = (e) => {
    if (e.target.matches("input[type=checkbox]")) return;
    if (e.ctrlKey || e.metaKey) { toggleCheck(Number(el.dataset.id)); return; }
    selectedProject = Number(el.dataset.id); store.set("ac.project", selectedProject); lastMessageId = 0; viewProjects(true);
  }));
  $("#proj-list").querySelectorAll("[data-check]").forEach((c) => (c.onchange = () => toggleCheck(Number(c.dataset.check))));
  if ($("#p-del")) $("#p-del").disabled = projectChecked.size === 0;
  if (selectedProject !== null) await projectMain(selectedProject, quiet && lastMessageId > 0);
}

function toggleCheck(id) {
  if (projectChecked.has(id)) projectChecked.delete(id); else projectChecked.add(id);
  viewProjects(true);
}

async function deleteProjects() {
  if (!projectChecked.size || !confirm(`Delete ${projectChecked.size} project(s)? Running goals are cancelled; repositories on disk are kept.`)) return;
  try { await api("/api/projects/delete", { method: "POST", body: { ids: [...projectChecked] } }); projectChecked.clear(); viewProjects(); } catch (e) { toast(e.message); }
}

function projectForm(p = null) {
  modal(`<h2>${p ? "Edit project" : "New project"}</h2><form id="pf">
    <label for="pf-name">Name</label><input id="pf-name" required value="${esc(p?.name || "")}" ${p?.protected ? "disabled" : ""}>
    ${p ? "" : `<label for="pf-path">Work folder (git repository; empty = new workspace)</label><div class="row"><input id="pf-path" class="grow" placeholder="/home/you/project">${typeof pickFolder === "function" ? `<button class="btn" type="button" id="pf-browse">Browse…</button>` : ""}</div>`}
    <label for="pf-gh">GitHub URL (recommended; approved work is pushed here)</label><input id="pf-gh" placeholder="https://github.com/owner/repo.git" value="${esc(p?.github_url || "")}">
    <label for="pf-desc">Description</label><textarea id="pf-desc">${esc(p?.description || "")}</textarea>
    <label for="pf-checks">Quality commands (one per line)</label><textarea id="pf-checks" placeholder="uv run pytest -q">${esc((p?.quality_commands || []).join("\n"))}</textarea>
    <div class="row" style="margin-top:12px"><button class="btn primary">${p ? "Save" : "Create"}</button></div></form>`);
  $("#pf-browse") && ($("#pf-browse").onclick = async () => { const dir = await pickFolder($("#pf-path").value); if (dir) $("#pf-path").value = dir; });
  $("#pf").onsubmit = async (e) => {
    e.preventDefault();
    const body = { github_url: $("#pf-gh").value.trim(), description: $("#pf-desc").value, quality_commands: $("#pf-checks").value.split("\n").map((x) => x.trim()).filter(Boolean) };
    if (!p?.protected) body.name = $("#pf-name").value.trim();
    try {
      if (p) await api(`/api/projects/${p.id}`, { method: "PATCH", body });
      else { const r = await api("/api/projects", { method: "POST", body: { ...body, path: $("#pf-path").value.trim() } }); selectedProject = r.id; }
      $("#modal").hidden = true; lastMessageId = 0; viewProjects();
    } catch (err) { toast(err.message); }
  };
}

const KIND_LABEL = { command: "", plan: "Plan", result: "Result", error: "Problem", proposal: "Proposal", standup: "Stand-up", chat: "" };

function messageHtml(m, count) {
  const who = m.author_type === "user" ? esc(m.meta?.author || "You") : m.agent ? esc(m.agent) : "System";
  const files = (m.attachments || []).map((a) => a.content_type.startsWith("image/") ? `<a href="/api/uploads/${a.id}" target="_blank" rel="noopener"><img src="/api/uploads/${a.id}" alt="${esc(a.name)}" class="thumb"></a>` : `<a href="/api/uploads/${a.id}" target="_blank" rel="noopener">${esc(a.name)}</a>`).join(" ");
  return `<div class="msg by-${m.author_type} ${m.kind}" data-mid="${m.id}"><div class="msg-head row nowrap"><strong>${who}</strong>${KIND_LABEL[m.kind] ? ` <span class="pill ${m.kind === "error" ? "failed" : m.kind === "result" ? "done" : "info"}">${KIND_LABEL[m.kind]}</span>` : ""}${m.goal_id ? ` <a href="#" data-goal="${m.goal_id}" class="muted">#${m.goal_id}</a>` : ""}${count > 1 ? ` <span class="pill" title="identical messages collapsed">×${count}</span>` : ""}<span class="muted grow" style="text-align:right">${ago(m.at)}</span><button class="icon-btn" data-copy="${m.id}" title="Copy" aria-label="Copy message">⧉</button></div>
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
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  box.innerHTML = groups.map((g) => messageHtml(g.m, g.n)).join("") || '<p class="muted">No messages yet. Give an order below.</p>';
  if (atBottom || !append) box.scrollTop = box.scrollHeight;
  box.querySelectorAll("[data-copy]").forEach((b) => (b.onclick = () => { const m = messageCache.find((x) => x.id === Number(b.dataset.copy)); navigator.clipboard?.writeText(m.content).then(() => toast("Copied"), () => toast("Copy failed")); }));
  box.querySelectorAll("[data-goal]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); goalModal(Number(a.dataset.goal)); }));
}

async function projectMain(id, incremental) {
  const d = await api(`/api/projects/${id}`);
  if (!incremental || !$("#msgs")) {
    $("#proj-main").innerHTML = `<div class="row nowrap"><div class="grow"><h2 style="margin:0">${esc(d.name)} ${d.protected ? '<span class="pill">protected</span>' : ""}</h2><div class="muted mono ellipsis">${esc(d.path)}${d.github_url ? " · " + esc(d.github_url) : ""}</div></div>
        ${me.is_admin ? `<button class="btn sm" id="p-edit">Edit</button><button class="btn sm" id="p-sync" ${d.github_url ? "" : 'disabled title="Set a GitHub URL first"'}>Sync to GitHub</button>` : ""}<button class="btn sm danger" id="p-stop">Stop</button></div>
      <div id="p-props"></div>
      <div class="msgs" id="msgs"></div>
      <form id="composer" class="composer"><div id="att" class="row"></div>
        <textarea id="cmd" rows="3" placeholder="Order for the General Manager, or /standup, /status, /stop, /sync, /help. Enter sends, Shift+Enter adds a line. Paste images to attach."></textarea>
        <div class="row"><label class="btn sm" for="file">Attach</label><input type="file" id="file" multiple hidden accept="image/*,text/*,.json,.yaml,.yml,.md,.pdf"><span class="muted grow">${d.goals.filter((g) => ["queued", "running"].includes(g.status)).length} goal(s) running</span><button class="btn primary">Send</button></div></form>`;
    messageCache = []; lastMessageId = 0; pendingUploads = [];
    $("#p-edit") && ($("#p-edit").onclick = () => projectForm(d));
    $("#p-sync") && ($("#p-sync").onclick = () => sendCommand(id, "/sync"));
    $("#p-stop").onclick = () => sendCommand(id, "/stop");
    $("#composer").onsubmit = (e) => { e.preventDefault(); sendCommand(id); };
    $("#cmd").onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); sendCommand(id); } };
    $("#cmd").onpaste = (e) => { const files = [...(e.clipboardData?.files || [])]; if (files.length) { e.preventDefault(); files.forEach(uploadFile); } };
    $("#file").onchange = (e) => { [...e.target.files].forEach(uploadFile); e.target.value = ""; };
  }
  const fresh = await api(`/api/projects/${id}/messages?after=${lastMessageId}`);
  if (fresh.length || !incremental) { messageCache.push(...fresh); lastMessageId = messageCache.length ? messageCache[messageCache.length - 1].id : 0; renderMessages(incremental); }
  const pending = d.proposals.filter((p) => p.status === "pending");
  $("#p-props").innerHTML = pending.length ? `<div class="props"><strong>Proposals waiting</strong>${pending.map((p) => `<div class="row"><span class="grow">${esc(p.title)} <span class="muted">${esc(p.agent || "")} · from #${p.goal_id}</span></span>${me.is_admin ? `<button class="btn sm primary" data-prop="${p.id}" data-d="approve">Approve</button><button class="btn sm" data-prop="${p.id}" data-d="reject">Reject</button>` : ""}</div>`).join("")}</div>` : "";
  $("#p-props").querySelectorAll("[data-prop]").forEach((b) => (b.onclick = async () => { try { await api(`/api/proposals/${b.dataset.prop}/${b.dataset.d}`, { method: "POST" }); projectMain(id, true); } catch (e) { toast(e.message); } }));
  const busy = d.members.filter((a) => a.current_task);
  $("#proj-rail").innerHTML = `<div class="row nowrap"><h2 class="grow" style="margin:0">Agents</h2><span class="pill ${busy.length ? "running" : ""}">${busy.length} working</span><span class="pill">${d.members.length}</span></div>
    <div class="list" style="margin-top:8px">${d.members.map((a) => `<div class="list-item agent-row" data-aid="${a.id}"><div class="row nowrap"><span class="avatar" style="width:26px;height:26px">${roleIcon(a.role)}</span><strong class="grow">${esc(a.name)}</strong>${pill(a.current_task ? "running" : a.status)}</div>${a.current_task ? `<div class="muted ellipsis">${esc(a.current_task)}</div>` : `<div class="muted">${esc(a.role)} · L${a.level}</div>`}</div>`).join("") || '<p class="muted">Agents appear here when they work on this project.</p>'}</div>`;
  $("#proj-rail").querySelectorAll("[data-aid]").forEach((el) => (el.onclick = () => agentModal(Number(el.dataset.aid))));
}

async function uploadFile(file) {
  const fd = new FormData(); fd.append("file", file);
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

async function sendCommand(id, text) {
  const content = text ?? $("#cmd").value.trim();
  if (!content && !pendingUploads.length) return;
  try {
    await api(`/api/projects/${id}/command`, { method: "POST", body: { content, attachments: pendingUploads.map((u) => u.id) } });
    if (text === undefined) { $("#cmd").value = ""; pendingUploads = []; renderAttachments(); }
    projectMain(id, true); updateBadges();
  } catch (e) { toast(e.message); }
}

registerView({ id: "projects", title: "Projects", icon: "projects" }, viewProjects, 1);
