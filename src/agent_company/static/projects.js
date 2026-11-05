"use strict";
// Projects: the project list (left; administrators see every project, others their own), the project's
// General Manager in the middle (a Claude Code conversation in the project's folder, as in the Workspace,
// for administrators; the Orders window, where an order becomes a goal for the agent loop, for everyone),
// and the agents on the right: who works on it and their organisation, top to bottom. Files opens a file
// panel beside them. The GM panels are kept per project, so switching projects never reloads them.

let selectedProject = Number(store.get("ac.project")) || null;
let projectChecked = new Set();
let lastMessageId = 0;
let pendingUploads = [];
let projectMode = store.get("ac.project.mode") || "gm";  // gm | orders
let projectFiles = store.get("ac.project.files") === "1";
const gmPanels = new Map();  // project id -> { el, panel }

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
  modal(`<h2>${icon("projects", 18)} ${p ? "Edit project" : "New project"}</h2><form id="pf">
    <label for="pf-name">Name</label><input id="pf-name" required value="${esc(p?.name || "")}" ${p?.protected ? "disabled" : ""}>
    ${p || !me.is_admin ? "" : `<label for="pf-path">Work folder (git repository; by default a new folder next to Agent Company)</label><div class="row nowrap"><input id="pf-path" class="grow" placeholder="${esc(base)}/my-project">${typeof pickFolder === "function" ? ib("folder", "Browse…", 'type="button" id="pf-browse"') : ""}</div>`}
    <label for="pf-gh">GitHub URL (recommended; approved work is pushed here)</label><input id="pf-gh" placeholder="https://github.com/owner/repo.git" value="${esc(p?.github_url || "")}">
    <label for="pf-desc">Description</label><textarea id="pf-desc">${esc(p?.description || "")}</textarea>
    ${me.is_admin ? `<label for="pf-checks">Quality commands (one per line)</label><textarea id="pf-checks" placeholder="uv run pytest -q">${esc((p?.quality_commands || []).join("\n"))}</textarea>` : ""}
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
    if (!p?.protected) body.name = $("#pf-name").value.trim();
    try {
      if (p) await api(`/api/projects/${p.id}`, { method: "PATCH", body });
      else { const r = await api("/api/projects", { method: "POST", body: { ...body, path: $("#pf-path")?.value.trim() || "" } }); selectedProject = r.id; }
      $("#modal").hidden = true; lastMessageId = 0; viewProjects();
    } catch (err) { toast(err.message); }
  };
}

const KIND_LABEL = { command: "", plan: "Plan", result: "Result", error: "Problem", proposal: "Proposal", standup: "Stand-up", chat: "" };

function messageHtml(m, count) {
  const who = m.author_type === "user" ? esc(m.meta?.author || "You") : m.agent ? esc(m.agent) : "System";
  const files = (m.attachments || []).map((a) => a.content_type.startsWith("image/") ? `<a href="/api/uploads/${a.id}" target="_blank" rel="noopener"><img src="/api/uploads/${a.id}" alt="${esc(a.name)}" class="thumb"></a>` : `<a href="/api/uploads/${a.id}" target="_blank" rel="noopener">${esc(a.name)}</a>`).join(" ");
  return `<div class="msg by-${m.author_type} ${m.kind}" data-mid="${m.id}"><div class="msg-head row nowrap"><strong>${who}</strong>${KIND_LABEL[m.kind] ? ` <span class="pill ${m.kind === "error" ? "failed" : m.kind === "result" ? "done" : "info"}">${KIND_LABEL[m.kind]}</span>` : ""}${m.goal_id ? ` <a href="#" data-goal="${m.goal_id}" class="muted">#${m.goal_id}</a>` : ""}${count > 1 ? ` <span class="pill" title="identical messages collapsed">×${count}</span>` : ""}<span class="muted grow" style="text-align:right">${ago(m.at)}</span><button class="icon-btn" data-copy="${m.id}" title="Copy" aria-label="Copy message">${icon("copy", 14)}</button></div>
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
  box.innerHTML = groups.map((g) => messageHtml(g.m, g.n)).join("") || '<p class="muted">No messages yet. Give the General Manager an order below.</p>';
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
  const mode = gmOk ? projectMode : "orders";
  const main = $("#proj-main");
  const gmKey = `${mode}:${d.gm?.kind}:${d.gm?.model}`;
  if (!incremental || main.dataset.pid !== String(id) || main.dataset.mode !== gmKey) {
    main.dataset.pid = id; main.dataset.mode = gmKey;
    main.innerHTML = `<div class="row nowrap proj-head"><div class="grow"><h2 style="margin:0">${esc(d.name)} ${d.protected ? '<span class="pill">protected</span>' : ""}</h2><div class="muted mono ellipsis">${esc(d.path)}${d.github_url ? " · " + esc(d.github_url) : ""}</div></div>
        ${me.is_admin ? ib("edit", "Edit the project", 'id="p-edit"', "sm") + ib("download", "Write the company catalog into this project's .claude/ (agents, rules, skills, commands, hooks, .mcp.json)", 'id="p-config"', "sm") + ib("git", d.github_url ? "Sync to GitHub" : "Set a GitHub URL first", `id="p-sync" ${d.github_url ? "" : "disabled"}`, "sm") + ib("workspace", "Open the folder in the Workspace", 'id="p-wsp"', "sm") : ""}
        ${ib("folder", projectFiles ? "Hide files" : "Show files", 'id="p-files"', `sm ${projectFiles ? "on" : ""}`)}${ib("stop", "Stop the project's running goals", 'id="p-stop"', "sm danger")}</div>
      <div class="ag-tabs small proj-modes" role="tablist">${gmOk ? `<button role="tab" data-pmode="gm" aria-selected="${mode === "gm"}">${icon("general_manager", 14)} General Manager</button>` : ""}<button role="tab" data-pmode="orders" aria-selected="${mode === "orders"}">${icon("send", 14)} Orders &amp; reports</button>
        <span class="grow"></span><span class="muted gm-is" title="${esc(d.gm?.reason || "")}">GM: ${esc(d.gm?.model || "–")}${cli ? "" : " · orders go to the agent loop"}</span></div>
      <div id="p-props"></div><div class="proj-body" id="proj-body"></div>`;
    main.querySelectorAll("[data-pmode]").forEach((b) => (b.onclick = () => { projectMode = b.dataset.pmode; store.set("ac.project.mode", projectMode); projectMain(id); }));
    $("#p-edit") && ($("#p-edit").onclick = () => projectForm(d));
    $("#p-sync") && ($("#p-sync").onclick = () => sendCommand(id, "/sync"));
    $("#p-stop").onclick = () => sendCommand(id, "/stop");
    $("#p-files").onclick = () => { projectFiles = !projectFiles; store.set("ac.project.files", projectFiles ? "1" : ""); $(".proj").classList.toggle("with-files", projectFiles); applyProjectLayout(); projectMain(id); };
    $("#p-wsp") && ($("#p-wsp").onclick = async () => { show("workspace"); await new Promise((r) => setTimeout(r, 300)); WSP.openRoot?.(d.path).catch((e) => toast(e.message)); });
    $("#p-config") && ($("#p-config").onclick = async () => { if (!confirm(`Write the catalog into ${d.path}/.claude/ (existing files with the same names are replaced)?`)) return; try { const r = await api(`/api/projects/${id}/claude-config`, { method: "POST" }); toast(`${r.written.length} file(s) written`); } catch (e) { toast(e.message); } });
    const body = $("#proj-body");
    if (mode === "gm") {
      const g = gmPanel(d);
      body.replaceChildren(g.el);
    } else {
      body.innerHTML = `<div class="msgs" id="msgs"></div>
        <form id="composer" class="composer wc-composer"><div id="att" class="row"></div>
          <div class="wc-inrow"><textarea id="cmd" rows="3" placeholder="Order for the General Manager, or /standup, /status, /stop, /sync, /help. Enter sends, Shift+Enter adds a line. Paste images to attach."></textarea>
          <div class="wc-side"><label class="icon-btn sm wc-attach" for="file" title="Attach images or files" aria-label="Attach">${icon("attach", 16)}</label><button class="wc-send" title="Send (Enter)" aria-label="Send">${icon("send", 16)}</button></div></div>
          <input type="file" id="file" multiple hidden accept="image/*,text/*,.json,.yaml,.yml,.md,.pdf"><span class="muted" id="p-running"></span></form>`;
      messageCache = []; lastMessageId = 0; pendingUploads = [];
      $("#composer").onsubmit = (e) => { e.preventDefault(); sendCommand(id); };
      $("#cmd").onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); sendCommand(id); } };
      $("#cmd").onpaste = (e) => { const files = [...(e.clipboardData?.files || [])]; if (files.length) { e.preventDefault(); files.forEach(uploadFile); } };
      $("#file").onchange = (e) => { [...e.target.files].forEach(uploadFile); e.target.value = ""; };
    }
  }
  if (mode === "orders" && $("#msgs")) {
    const fresh = await api(`/api/projects/${id}/messages?after=${lastMessageId}`);
    if (fresh.length || !lastMessageId) { messageCache.push(...fresh); lastMessageId = messageCache.length ? messageCache[messageCache.length - 1].id : 0; renderMessages(lastMessageId && incremental); }
    $("#p-running").textContent = `${d.goals.filter((g) => GOAL_ACTIVE.includes(g.status) && g.status !== "paused").length} goal(s) running`;
  }
  const pending = d.proposals.filter((p) => p.status === "pending");
  $("#p-props").innerHTML = pending.length ? `<div class="props"><strong>Proposals waiting</strong>${pending.map((p) => `<div class="row"><span class="grow">${esc(p.title)} <span class="muted">${esc(p.agent || "")} · from #${p.goal_id}</span></span>${me.is_admin ? ib("check", "Approve", `data-prop="${p.id}" data-d="approve"`, "sm primary") + ib("x", "Reject", `data-prop="${p.id}" data-d="reject"`, "sm") : ""}</div>`).join("")}</div>` : "";
  $("#p-props").querySelectorAll("[data-prop]").forEach((b) => (b.onclick = async () => { try { await api(`/api/proposals/${b.dataset.prop}/${b.dataset.d}`, { method: "POST" }); projectMain(id, true); } catch (e) { toast(e.message); } }));
  projectRail(d);
  if (projectFiles) projectFilesPanel(d);
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
  const content = text ?? $("#cmd")?.value.trim();
  if (!content && !pendingUploads.length) return;
  try {
    await api(`/api/projects/${id}/command`, { method: "POST", body: { content, attachments: pendingUploads.map((u) => u.id) } });
    if (text === undefined) { $("#cmd").value = ""; pendingUploads = []; renderAttachments(); }
    else toast(`${text} sent`);
    projectMain(id, true); updateBadges();
  } catch (e) { toast(e.message); }
}

registerView({ id: "projects", title: "Projects", icon: "projects" }, viewProjects, 1);
