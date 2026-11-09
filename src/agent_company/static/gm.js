// General Manager page (AICO UI-GM-01…06): the owner's primary control surface. Left: the conversations (projects
// and their topics); centre: the project's command window with the GM (projects.js `projectMain`: orders, plan,
// and for administrators the GM's own Claude Code/Codex console); right: what the conversation is about — the plan
// waiting for approval, the focused goal's requirements, plan, team, status and completion report (cards built from
// stored records, never from the agent's text), open decisions, this project's approvals and its agents.
let gmProject = Number(store.get("ac.gm.project")) || null, gmFocus = null, gmSearch = "";
// UI-GM-02: "New project…", the conversation where the GM analyses an idea and proposes a project
let gmNew = store.get("ac.gm.new") === "1";
const gmMayPropose = () => me.is_admin || !!me.permissions?.create_projects;
function gmSetNew(on) { gmNew = on; store.set("ac.gm.new", on ? "1" : "0"); }
const GM_TASK_TONE = { done: "ok", skipped: "neutral", failed: "bad", cancelled: "neutral", running: "info", queued: "neutral" };

function gmCard(title, body, extra = "") {
  return `<section class="gm-card"><div class="card-head"><h3>${title}</h3>${extra}</div>${body}</section>`;
}
function gmEmpty(text) { return `<p class="muted dash-meta" style="margin:0">${text}</p>`; }

async function viewGeneralManager(quiet) {
  const list = await api("/api/projects");
  if (!list.some((p) => p.id === gmProject)) gmProject = (list.find((p) => !p.protected) || list[0])?.id ?? null;
  if (gmProject !== null) store.set("ac.gm.project", gmProject);
  if (!quiet || !$("#gmp")) {
    $("#view").innerHTML = `<div class="gmp" id="gmp">
      <aside class="card gmp-side"><div class="row nowrap"><h2 class="grow" style="margin:0">Conversations</h2>${me.is_admin || me.permissions?.create_projects ? ib("plus", "New project", 'id="gm-new"', "primary sm") : ""}</div>
        <input type="search" id="gm-search" placeholder="Search projects and topics" aria-label="Search conversations" value="${esc(gmSearch)}">
        <div id="gm-convs" class="gm-convs"></div></aside>
      <section class="card proj-main" id="proj-main"><div class="empty"><p class="empty-title">No project yet</p><p class="muted">Create a project to talk to the General Manager about it.</p></div></section>
      <aside class="card gmp-ctx"><div id="gm-ctx"></div><div id="proj-rail" class="gmp-team"></div></aside></div>`;
    $("#gm-new") && ($("#gm-new").onclick = () => projectForm());
    let t = 0;
    $("#gm-search").oninput = () => { clearTimeout(t); t = setTimeout(() => { gmSearch = $("#gm-search").value.trim().toLowerCase(); gmConversations(list); }, 200); };
    lastMessageId = 0; messageCache = [];
    // a click on a message that belongs to a goal focuses the context panel on that goal (UI-GM-03, UI-GM-05)
    $("#proj-main").addEventListener("click", (e) => {
      if (e.target.closest("a,button,input,textarea,select")) return;
      const el = e.target.closest(".msg[data-mid]"), m = el && messageCache.find((x) => x.id === Number(el.dataset.mid));
      if (m?.goal_id) { gmFocus = m.goal_id; document.querySelectorAll("#msgs .msg.focused").forEach((x) => x.classList.remove("focused")); el.classList.add("focused"); gmContext(gmProject); }
    });
  }
  await gmConversations(list);
  if (gmNew && gmMayPropose()) return gmNewProject();
  if (gmProject === null) return;
  if (current === "general-manager") setCrumbs([{ label: list.find((p) => p.id === gmProject)?.name || "", href: "#/general-manager/" + gmProject }]);
  await Promise.all([projectMain(gmProject, quiet), gmContext(gmProject)]);
  $("#p-files")?.remove();  // the GM page has no file pane; the Projects page keeps it
  if (current === "general-manager") setCrumbs([{ label: list.find((p) => p.id === gmProject)?.name || "", href: "#/general-manager/" + gmProject }]);
}

async function gmConversations(list) {
  const topics = gmProject !== null ? await api(`/api/projects/${gmProject}/topics`).catch(() => []) : [];
  const q = gmSearch, cur = topicOf(gmProject);
  const shown = list.filter((p) => !q || p.name.toLowerCase().includes(q) || (p.id === gmProject && topics.some((t) => t.name.toLowerCase().includes(q))));
  const fresh = gmMayPropose() && (!q || "new project".includes(q)) ? `<div class="gm-conv${gmNew ? " selected" : ""}" data-new="1" title="The General Manager analyses an idea and proposes a project; nothing is built"><div class="row nowrap"><span class="avatar proj-av" style="width:26px;height:26px">${icon("plus", 16)}</span><strong class="grow ellipsis">New project…</strong></div></div>` : "";
  const html = fresh + shown.map((p) => {
    const on = p.id === gmProject && !gmNew;
    const ts = on ? topics.filter((t) => (!t.archived || me.is_admin) && (!q || t.name.toLowerCase().includes(q) || p.name.toLowerCase().includes(q))) : [];
    return `<div class="gm-conv${on ? " selected" : ""}" data-pid="${p.id}"><div class="row nowrap"><span class="avatar proj-av" style="width:26px;height:26px">${icon(p.protected ? "settings" : "projects", 16)}</span><strong class="grow ellipsis">${esc(p.name)}</strong>${p.running_goals ? `<span class="badge info">${p.running_goals} live</span>` : ""}</div>
      ${ts.length ? `<div class="gm-topics">${ts.map((t) => `<button class="gm-topic${t.name === cur ? " active" : ""}" data-topic="${esc(t.name)}">#${esc(t.name)}${t.unread ? ` <span class="badge bad">${t.unread}</span>` : ""}</button>`).join("")}</div>` : ""}</div>`;
  }).join("") || (fresh ? "" : gmEmpty(q ? "No conversation matches." : "No project yet."));
  const box = $("#gm-convs");
  if (!box || box.dataset.html === html) return;
  box.innerHTML = html; box.dataset.html = html;
  box.querySelectorAll(".gm-conv").forEach((el) => (el.onclick = (e) => {
    if (el.dataset.new) { if (!gmNew) { gmSetNew(true); writeHash("#/general-manager/new"); viewGeneralManager(true); } return; }
    const pid = Number(el.dataset.pid), topic = e.target.closest("[data-topic]")?.dataset.topic;
    if (topic) store.set(`ac.project.topic.${pid}`, topic);
    if (pid === gmProject && !topic && !gmNew) return;
    gmSetNew(false); gmProject = pid; gmFocus = null; store.set("ac.gm.project", pid); lastMessageId = 0; messageCache = [];
    writeHash("#/general-manager/" + pid); viewGeneralManager(true);
  }));
}

async function gmContext(pid) {
  const [d, q, dash] = await Promise.all([api(`/api/projects/${pid}`), api("/api/approvals").catch(() => ({ items: [] })), api("/api/dashboard?period=7d").catch(() => null)]);
  const goals = d.goals || [], topic = topicOf(pid);
  const waiting = goals.filter((g) => g.status === "paused" && g.paused_for === "plan");
  const asking = goals.filter((g) => g.status === "paused" && g.paused_for === "clarification");
  if (!goals.some((g) => g.id === gmFocus)) gmFocus = (asking[0] || waiting[0] || goals.find((g) => (g.topic || "general") === topic) || goals[0])?.id || null;
  const g = gmFocus ? await api(`/api/goals/${gmFocus}`).catch(() => null) : null;
  const finished = g && ["approved", "failed", "cancelled", "rejected"].includes(g.status);
  const rep = finished ? await api(`/api/goals/${g.id}/report`).catch(() => null) : null;
  const proj = dash?.projects.find((p) => p.id === pid);
  const [phase, tone] = proj ? DASH_PHASE[proj.phase] || [proj.phase, "neutral"] : [d.status, "neutral"];
  const parts = [];
  // the project at a glance
  parts.push(gmCard(esc(d.name), `<div class="row nowrap"><span class="badge ${tone}">${esc(phase)}</span><span class="grow"></span><strong>${proj ? Math.round(proj.progress * 100) : 0}%</strong></div>
    <div class="progress" style="margin:8px 0"><span style="width:${proj ? Math.round(proj.progress * 100) : 0}%"></span></div>
    <div class="muted dash-meta">GM: ${esc(d.gm?.model || "–")} · ${goals.filter((x) => GOAL_ACTIVE.includes(x.status)).length} goal(s) open${proj?.next_milestone ? ` · next: ${esc(proj.next_milestone.name)}` : ""}</div>`));
  // UI-GM-04: the GM's clarification questions, each with an answer box; the goal is planned again with the answers
  for (const w of asking) {
    const qs = (await api(`/api/goals/${w.id}`).catch(() => null))?.questions || [];
    parts.push(`<section class="gm-card gm-await"><div class="card-head"><h3>The General Manager asks</h3><span class="badge warn">goal #${w.id}</span></div>
      <p style="margin:0 0 8px"><a href="#/goals/${w.id}">${esc(w.title)}</a></p>
      ${qs.map((q, i) => `<label class="dash-meta" style="display:block;margin:6px 0 2px">${i + 1}. ${esc(q)}</label><textarea rows="2" data-answer="${w.id}" aria-label="Answer ${i + 1}"></textarea>`).join("")}
      <div class="row nowrap" style="margin-top:6px"><button class="btn sm primary" data-answers="${w.id}">Send answers</button></div></section>`);
  }
  // UI-GM-04: plans that wait for a person
  for (const w of waiting) {
    parts.push(`<section class="gm-card gm-await"><div class="card-head"><h3>Awaiting plan approval</h3><span class="badge warn">goal #${w.id}</span></div>
      <p style="margin:0 0 8px"><a href="#/goals/${w.id}">${esc(w.title)}</a></p>
      <div class="row nowrap"><button class="btn sm primary" data-plan-ok="${w.id}">Approve plan</button><button class="btn sm" data-plan-rev="${w.id}">Request revision</button></div></section>`);
  }
  if (g) {
    const it = g.tasks.filter((t) => t.iteration === g.iteration), reqs = g.requirement_keys || [];
    const acc = it.flatMap((t) => (t.contract?.acceptance || []).map((a) => (typeof a === "string" ? a : a.text || a.description || JSON.stringify(a))));
    parts.push(gmCard(`Goal #${g.id}`, `<p style="margin:0 0 6px"><a href="#/goals/${g.id}">${esc(g.title)}</a></p><div class="row nowrap">${pill(g.status)}<span class="muted dash-meta grow">iteration ${g.iteration} of ${g.max_iterations}${g.kind ? " · " + esc(g.kind) : ""}</span></div>${g.digest ? `<p class="muted dash-meta" style="margin:6px 0 0">Status: ${g.digest.tasks_done}/${g.digest.tasks} task(s) passed · ${money(g.digest.cost)} · ${fmt.format(g.digest.tokens)} tokens · ${esc(g.digest.merge)}</p>` : ""}`, ib("refresh", "Focus the newest goal", 'data-refocus="1"', "sm")));
    // Requirement analysis: the goal's objective, requirement keys and the plan's acceptance criteria
    parts.push(gmCard("Requirement analysis", `<div class="gm-kv"><span class="muted">Objective</span><span>${esc((g.description || g.title).slice(0, 400))}</span>
      <span class="muted">Requirements</span><span>${reqs.length ? reqs.map((k) => `<span class="badge info">${esc(k)}</span>`).join(" ") : '<span class="muted">none linked</span>'}</span>
      <span class="muted">Allowed paths</span><span class="mono">${esc((g.allowed_paths || []).join(", ") || "any")}</span></div>
      ${acc.length ? `<details><summary class="muted dash-meta">${acc.length} acceptance criteria</summary><ul class="gm-acc">${acc.slice(0, 20).map((a) => `<li>${esc(String(a).slice(0, 200))}</li>`).join("")}</ul></details>` : ""}`));
    // Plan: this iteration's tasks with role, dependencies and status (stage from the lifecycle)
    parts.push(gmCard(`Plan · iteration ${g.iteration}`, it.length ? `<ol class="gm-plan">${it.map((t) => `<li><div class="row nowrap"><span class="badge ${GM_TASK_TONE[t.status] || "neutral"}">${esc(t.stage || t.status)}</span><strong class="grow ellipsis" title="${esc(t.title)}">${esc(t.title)}</strong></div><div class="muted dash-meta">${esc(t.role)}${t.depends_on?.length ? ` · after ${t.depends_on.map(esc).join(", ")}` : ""}${t.model ? ` · ${esc(t.model)}` : ""}</div></li>`).join("")}</ol>` : gmEmpty(g.status === "planning" ? "The General Manager is planning…" : "No plan stored for this iteration.")));
    // Team: who joined, was added or released (teams.py)
    const team = new Map(); (g.team || []).forEach((m) => team.set(m.agent_id || "role:" + m.role, m));
    parts.push(gmCard("Team", team.size ? `<div class="gm-team">${[...team.values()].map((m) => `<span class="badge ${m.action === "released" ? "neutral" : "info"}" title="${esc(m.reason || "")}">${esc(m.agent || m.role)} · ${esc(m.role)}${m.action === "released" ? " (released)" : ""}</span>`).join(" ")}</div>` : gmEmpty("No agent staffed yet.")));
    // Completion report: labels from the gate and reviews only (UI-D-08)
    if (rep) {
      const l = rep.tasks.labels;
      parts.push(gmCard("Completion report", `<div class="row nowrap"><span class="badge ok">${l.verified} verified</span><span class="badge warn">${l.partial} partial</span><span class="badge bad">${l.failed} failed</span></div>
        <div class="muted dash-meta" style="margin-top:6px">${money(rep.cost.usd)} · ${fmt.format(rep.cost.tokens)} tokens${rep.time.duration ? ` · ${dur(rep.time.duration)}` : ""}</div>
        ${rep.limitations?.length ? `<details><summary class="muted dash-meta">${rep.limitations.length} limitation(s)</summary><ul class="gm-acc">${rep.limitations.slice(0, 10).map((x) => `<li>${esc(x)}</li>`).join("")}</ul></details>` : ""}`, `<a class="view-all" href="/api/goals/${g.id}/report?format=md" target="_blank" rel="noopener">Markdown →</a>`));
    }
    const open = (g.decisions || []).filter((x) => x.requires_human_approval && !["approved", "rejected", "cancelled", "expired"].includes(x.status));
    if (open.length) parts.push(gmCard("Decisions for you", `<div class="dash-list">${open.map((x) => `<div><strong>${esc(x.topic)}</strong><div class="muted dash-meta">${esc((x.recommendation || "").slice(0, 160))}</div>${me.is_admin ? `<div class="row nowrap" style="margin-top:4px"><button class="btn sm primary" data-dec-ok="${x.id}">Approve</button><button class="btn sm" data-dec-no="${x.id}">Reject</button></div>` : ""}</div>`).join("")}</div>`));
  } else parts.push(gmCard("No goal yet", gmEmpty("Give the General Manager an order: it plans a goal, staffs a team and reports here.")));
  const mine = q.items.filter((x) => x.project_id === pid && x.kind !== "plan");
  if (mine.length) parts.push(gmCard(`Waiting for you <span class="badge bad">${mine.length}</span>`, `<div class="dash-list">${mine.slice(0, 6).map((x) => `<div class="row nowrap"><span class="grow ellipsis">${esc(x.title)}</span>${x.link ? `<a class="btn sm" href="${esc(x.link)}">Review</a>` : ""}</div>`).join("")}</div>`));
  const html = parts.join("");
  const box = $("#gm-ctx");
  if (!box || box.dataset.html === html) return;
  if ([...box.querySelectorAll("[data-answer]")].some((x) => x.value.trim() || x === document.activeElement)) return;  // keep a half-written answer
  box.innerHTML = html; box.dataset.html = html; iconize(box);
  box.querySelector("[data-refocus]") && (box.querySelector("[data-refocus]").onclick = () => { gmFocus = null; gmContext(pid); });
  box.querySelectorAll("[data-plan-ok]").forEach((b) => (b.onclick = async () => { try { await api(`/api/goals/${b.dataset.planOk}/plan/approve`, { method: "POST" }); toast("Plan approved: the goal runs it now"); gmContext(pid); updateBadges(); } catch (e) { toast(e.message); } }));
  box.querySelectorAll("[data-answers]").forEach((b) => (b.onclick = async () => {
    const answers = [...box.querySelectorAll(`[data-answer="${b.dataset.answers}"]`)].map((x) => x.value.trim());
    if (!answers.some(Boolean)) return toast("Answer at least one question.");
    try { await api(`/api/goals/${b.dataset.answers}/questions/answer`, { method: "POST", body: { answers } }); toast("Answers sent; the General Manager plans again"); box.querySelectorAll("[data-answer]").forEach((x) => (x.value = "")); document.activeElement?.blur(); gmContext(pid); } catch (e) { toast(e.message); }
  }));
  box.querySelectorAll("[data-plan-rev]").forEach((b) => (b.onclick = async () => {
    const note = prompt("What should the revised plan change? (the planner reads it)");
    if (!note || !note.trim()) return;
    try { await api(`/api/goals/${b.dataset.planRev}/plan/revise`, { method: "POST", body: { note } }); toast("Sent back to the planner"); gmContext(pid); } catch (e) { toast(e.message); }
  }));
  box.querySelectorAll("[data-dec-ok],[data-dec-no]").forEach((b) => (b.onclick = async () => {
    const ok = !!b.dataset.decOk, note = ok ? "" : prompt("Why is it rejected?");
    if (!ok && note === null) return;
    try { await api(`/api/decisions/${b.dataset.decOk || b.dataset.decNo}/decide`, { method: "POST", body: { approve: ok, note: note || "" } }); toast(ok ? "Approved" : "Rejected"); gmContext(pid); } catch (e) { toast(e.message); }
  }));
}
// UI-GM-02 (FR-003.5): a message to "New project…" is a read-only research goal of the GM that ends in a documented
// PROPOSAL[new_project] (S10-R-01); a person reads the case here and decides whether it becomes a project.
async function gmNewProject() {
  const main = $("#proj-main"), ctx = $("#gm-ctx");
  if (!main) return;
  if (current === "general-manager") setCrumbs([{ label: "New project…", href: "#/general-manager/new" }]);
  if (main.dataset.pid !== "new") {
    main.dataset.pid = "new"; main.dataset.mode = "new";
    main.innerHTML = `<div class="proj-head"><h2 style="margin:0">New project…</h2><p class="muted" style="margin:4px 0 0">Describe an idea. The General Manager analyses it (purpose, users, requirements, feasibility, cost) and proposes a project with its documented case. Nothing is built and no file is written; you decide whether it becomes a project.</p></div>
      <form id="gm-an-form" style="margin-top:10px"><textarea id="gm-an-text" rows="5" maxlength="8000" placeholder="What should the project do, for whom, and why?" aria-label="Describe the project"></textarea>
        <div class="row nowrap" style="margin-top:6px"><span class="muted dash-meta grow">A read-only research goal; the General Manager may ask questions first.</span><button class="btn primary keep-text">Ask the General Manager</button></div></form>
      <h3 style="margin:16px 0 6px">Analyses</h3><div id="gm-an-list" class="dash-list"></div>`;
    $("#gm-an-form").onsubmit = async (e) => {
      e.preventDefault();
      const text = $("#gm-an-text").value.trim();
      if (!text) return toast("Describe the project first.");
      try { const r = await api("/api/project-analyses", { method: "POST", body: { text } }); $("#gm-an-text").value = ""; toast(`The General Manager analyses it (goal #${r.goal_id})`); gmNewList(); } catch (err) { toast(err.message); }
    };
    if (ctx) {
      delete ctx.dataset.html;  // gmContext draws again when a project is chosen
      ctx.innerHTML = gmCard("How it works", `<ol class="gm-acc"><li>You describe the idea; the General Manager plans a read-only analysis.</li><li>If something is unclear it asks; answer on the analysis.</li><li>The analysis ends with a proposal: concept, target user, expected value, feasibility, requirements and estimated cost.</li><li>A person approves a complete case; the project is created with its requirements as a draft. Rejecting it ends it.</li></ol>`);
    }
    $("#proj-rail") && ($("#proj-rail").innerHTML = "");
  }
  await gmNewList();
}

function gmAnalysis(a) {
  const asking = a.status === "paused" && a.paused_for === "clarification";
  const props = a.proposals.map((p) => {
    const filled = CASE_FIELDS.filter(([k]) => p.case[k]);
    const ready = !p.case_missing.length, open = p.status === "pending";
    return `<div class="gm-card" style="margin-top:8px"><div class="row nowrap"><strong class="grow ellipsis">${esc(p.title)}</strong>${pill(p.spawned_project_id ? "approved" : p.status)}</div>
      ${p.description ? `<p class="muted" style="margin:4px 0">${esc(p.description)}</p>` : ""}
      ${filled.length ? `<div class="gm-kv">${filled.map(([k, label]) => `<span class="muted">${esc(label.replace(/ \(.*\)$/, ""))}</span><span>${esc(p.case[k])}</span>`).join("")}</div>` : ""}
      ${p.case_missing.length && open ? `<p class="dash-meta" style="margin:6px 0 0"><span class="badge warn">case incomplete</span> missing ${p.case_missing.map((k) => esc((CASE_FIELDS.find(([x]) => x === k) || [k, k])[1].replace(/ \(.*\)$/, ""))).join(", ")}</p>` : ""}
      <div class="row nowrap" style="margin-top:6px">${p.spawned_project_id ? `<button class="btn sm primary" data-an-open="${p.spawned_project_id}">Open the project</button>` : ""}
        ${open && me.is_admin ? `${ready ? `<button class="btn sm primary" data-an-ok="${p.id}">Create the project</button>` : ""}<button class="btn sm" data-an-case="${p.id}">${ready ? "Edit the case" : "Complete the case"}</button><button class="btn sm" data-an-no="${p.id}">Reject</button>` : ""}
        ${open && !me.is_admin ? '<span class="muted dash-meta">An administrator decides it.</span>' : ""}</div></div>`;
  }).join("");
  const done = ["approved", "failed", "cancelled", "rejected"].includes(a.status);
  return `<div class="gm-card${asking ? " gm-await" : ""}"><div class="row nowrap"><strong class="grow ellipsis" title="${esc(a.request)}">${esc(a.title)}</strong>${pill(asking ? "waiting" : a.status)}<a class="dash-meta" href="#/goals/${a.goal_id}">goal #${a.goal_id}</a><span class="muted dash-meta">${ago(a.at)}</span></div>
    ${a.request !== a.title ? `<details><summary class="muted dash-meta">Request</summary><div style="white-space:pre-wrap">${esc(a.request)}</div></details>` : ""}
    ${asking ? `<p class="dash-meta" style="margin:6px 0 0">The General Manager asks:</p>${a.questions.map((q, i) => `<label class="dash-meta" style="display:block;margin:6px 0 2px">${i + 1}. ${esc(q)}</label><textarea rows="2" data-answer="${a.goal_id}" aria-label="Answer ${i + 1}"></textarea>`).join("")}<div class="row nowrap" style="margin-top:6px"><button class="btn sm primary" data-answers="${a.goal_id}">Send answers</button></div>` : ""}
    ${props || (done ? gmEmpty("The analysis ended without a proposal; open the goal for its findings.") : gmEmpty(asking ? "" : "Analysing…"))}</div>`;
}

async function gmNewList() {
  const box = $("#gm-an-list");
  if (!box) return;
  const list = await api("/api/project-analyses").catch(() => []);
  const html = list.length ? list.map(gmAnalysis).join("") : gmEmpty("No analysis yet. Describe an idea above.");
  if (box.dataset.html === html) return;
  if ([...box.querySelectorAll("[data-answer]")].some((x) => x.value.trim() || x === document.activeElement)) return;  // keep a half-written answer
  box.innerHTML = html; box.dataset.html = html; iconize(box);
  const call = async (path, body, msg) => { try { const r = await api(path, { method: "POST", body }); toast(msg); gmNewList(); updateBadges(); return r; } catch (e) { toast(e.message); } };
  box.querySelectorAll("[data-answers]").forEach((b) => (b.onclick = () => {
    const answers = [...box.querySelectorAll(`[data-answer="${b.dataset.answers}"]`)].map((x) => x.value.trim());
    if (!answers.some(Boolean)) return toast("Answer at least one question.");
    box.querySelectorAll(`[data-answer="${b.dataset.answers}"]`).forEach((x) => (x.value = "")); document.activeElement?.blur();
    call(`/api/goals/${b.dataset.answers}/questions/answer`, { answers }, "Answers sent; the General Manager plans again");
  }));
  box.querySelectorAll("[data-an-ok]").forEach((b) => (b.onclick = async () => {
    if (!confirm("Create the project from this case? Its requirements become a draft requirement.")) return;
    const r = await call(`/api/proposals/${b.dataset.anOk}/approve`, undefined, "Project created");
    if (r?.spawned_project_id) { gmSetNew(false); gmProject = r.spawned_project_id; store.set("ac.gm.project", gmProject); writeHash("#/general-manager/" + gmProject); viewGeneralManager(); }
  }));
  box.querySelectorAll("[data-an-no]").forEach((b) => (b.onclick = () => confirm("Reject this proposal?") && call(`/api/proposals/${b.dataset.anNo}/reject`, undefined, "Rejected")));
  box.querySelectorAll("[data-an-case]").forEach((b) => (b.onclick = () => {
    const p = list.flatMap((a) => a.proposals).find((x) => x.id === Number(b.dataset.anCase));
    proposalCase(p, null, () => { delete box.dataset.html; gmNewList(); });
  }));
  box.querySelectorAll("[data-an-open]").forEach((b) => (b.onclick = () => { gmSetNew(false); gmProject = Number(b.dataset.anOpen); store.set("ac.gm.project", gmProject); writeHash("#/general-manager/" + gmProject); viewGeneralManager(); }));
}
registerView({ id: "general-manager", title: "General Manager", icon: "general_manager" }, viewGeneralManager);
