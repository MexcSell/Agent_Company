"use strict";
// REQ-003 Simple mode: one mission form, a dashboard of the user's goals, and REQ-004 mission reports.
// Uses app.js helpers ($, esc, api, money, dur, pill, toast, goalModal, registerView).

let simpleFiles = [], simplePoll = null;
const LABEL_CLS = { verified: "done", partial: "pending", failed: "failed" };
const labelPill = (l) => `<span class="pill ${LABEL_CLS[l] || ""}" title="from recorded checks and reviews, never from an agent's claim">${esc(l)}</span>`;

async function viewSimple(quiet) {
  const [ov, projs] = await Promise.all([api("/api/simple/overview"), quiet && $("#mission-form") ? null : api("/api/projects")]);
  if (!quiet || !$("#mission-form")) {
    $("#view").innerHTML = `<div class="grid two simple">
      <div class="card"><h2>New mission</h2>${missionForm(projs)}</div>
      <div id="simple-board"></div></div>`;
    wireMissionForm();
  }
  $("#simple-board").innerHTML = simpleBoard(ov);
  $("#simple-board").querySelectorAll("[data-goal]").forEach((b) => (b.onclick = () => goalModal(Number(b.dataset.goal))));
  $("#simple-board").querySelectorAll("[data-report]").forEach((b) => (b.onclick = () => simpleReport(Number(b.dataset.report))));
  clearInterval(simplePoll);  // the live stream refreshes too; this is the 15 s fallback while the page is open
  simplePoll = setInterval(() => { if (current !== "simple") return clearInterval(simplePoll); if (!document.activeElement?.closest("form")) viewSimple(true).catch(() => {}); }, 15000);
}

function missionForm(projs) {
  const list = Array.isArray(projs) ? projs : projs?.projects || [];
  const opts = list.filter((p) => !["archived", "closed"].includes(p.status)).map((p) => `<option value="${Number(p.id)}">${esc(p.name)}</option>`).join("");
  const ta = (id, label, ph = "") => `<label for="${id}">${label}</label><textarea id="${id}" rows="3" placeholder="${esc(ph)}"></textarea>`;
  return `<form id="mission-form">
    <label for="m-project">Project</label><select id="m-project" required>${opts || '<option value="">(no project you can see)</option>'}</select>
    <label for="m-title">Goal</label><input id="m-title" required minlength="3" maxlength="256" placeholder="What should be achieved">
    ${ta("m-desc", "Description", "Details, acceptance criteria")}
    <div class="row"><span class="grow"><label for="m-budget">Budget (USD)</label><input id="m-budget" type="number" min="0.01" max="10000" step="0.01" value="5" required></span>
      <span class="grow"><label for="m-deadline">Deadline</label><input id="m-deadline" type="datetime-local"></span></div>
    ${ta("m-notes", "Experience / notes", "What you already know or tried")}
    ${ta("m-constraints", "Constraints", "What must not change, rules to follow")}
    <h3>Resources</h3>
    ${ta("m-repos", "Repositories (one per line)", "Leave empty for the project's repository")}
    ${ta("m-links", "Links (one per line, http/https)")}
    <label for="m-files">Files</label><input id="m-files" type="file" multiple><div id="m-att" class="row"></div>
    <div class="row" style="margin-top:12px"><button class="btn primary" type="submit">Start mission</button></div></form>`;
}

function renderSimpleFiles() {
  $("#m-att").innerHTML = simpleFiles.map((u, i) => `<span class="pill">${esc(u.name)} <button type="button" class="icon-btn" style="width:18px;height:18px" data-rm="${i}" aria-label="Remove">✕</button></span>`).join("");
  $("#m-att").querySelectorAll("[data-rm]").forEach((b) => (b.onclick = () => { simpleFiles.splice(Number(b.dataset.rm), 1); renderSimpleFiles(); }));
}

function wireMissionForm() {
  simpleFiles = [];
  $("#m-files").onchange = async (e) => {
    for (const file of e.target.files) {
      const fd = new FormData(); fd.append("file", file); fd.append("classification", "internal");
      try {
        const res = await fetch("/api/uploads", { method: "POST", body: fd, credentials: "same-origin" });
        const data = await res.json(); if (!res.ok) throw new Error(data.detail || res.statusText);
        simpleFiles.push(data);
      } catch (err) { toast(`${file.name}: ${err.message}`); }
    }
    e.target.value = ""; renderSimpleFiles();
  };
  $("#mission-form").onsubmit = async (e) => {
    e.preventDefault();
    const lines = (id) => $(id).value.split("\n").map((x) => x.trim()).filter(Boolean);
    const dl = $("#m-deadline").value;
    const body = {
      project_id: Number($("#m-project").value), title: $("#m-title").value.trim(), description: $("#m-desc").value.trim(),
      budget_usd: Number($("#m-budget").value), deadline: dl ? new Date(dl).toISOString() : null,
      notes: $("#m-notes").value.trim(), constraints: $("#m-constraints").value.trim(),
      resources: { repositories: lines("#m-repos"), links: lines("#m-links"), files: simpleFiles.map((u) => u.id) },
    };
    try {
      const r = await api("/api/simple/missions", { method: "POST", body });
      toast(`Mission started: goal #${Number(r.id)}`);
      $("#mission-form").reset(); simpleFiles = []; renderSimpleFiles();
      viewSimple(true);
    } catch (err) { toast(err.message); }
  };
}

function simpleCard(c, extra = "") {
  const pct = Math.round((c.progress || 0) * 100);
  const cost = c.budget_usd ? `${money(c.cost_usd)} of ${money(c.budget_usd)}` : `${money(c.cost_usd)} (no budget)`;
  const over = c.budget_usd && c.cost_usd > c.budget_usd;
  const time = c.deadline ? (c.overdue ? `<span style="color:var(--danger,#c53030)">overdue</span> (deadline ${esc(c.deadline.slice(0, 16).replace("T", " "))})` : `${dur(c.remaining_s)} left of the deadline`) : `${dur(c.elapsed_s)} since the order`;
  return `<div class="card" style="margin:6px 0;padding:8px 10px">
    <div class="row">${pill(c.status)}<strong class="grow">#${Number(c.id)} ${esc(c.title)}</strong><span class="muted">${esc(c.project || "")}</span>
      <button class="btn" data-goal="${Number(c.id)}">Open</button></div>
    <div class="row"><progress max="100" value="${pct}" class="grow" title="${Number(c.tasks_done)}/${Number(c.tasks)} task(s) of the current iteration done"></progress><span class="muted">${pct}%</span></div>
    <div class="muted">Cost: <span${over ? ' style="color:var(--danger,#c53030)"' : ""}>${cost}</span> · Time: ${time}</div>
    ${(c.blockers || []).map((b) => `<div class="muted">Blocker: ${esc(b)}</div>`).join("")}${c.paused_for ? `<div class="muted">Paused: ${esc(c.paused_for.replace(/_/g, " "))}</div>` : ""}${extra}</div>`;
}

function simpleBoard(ov) {
  const sec = (title, rows, empty) => `<div class="card"><h2>${title} <span class="muted">(${rows.length})</span></h2>${rows.join("") || `<p class="muted">${empty}</p>`}</div>`;
  const delivered = (ov.delivered || []).map((c) => simpleCard(c, `<div class="row">${Object.entries(c.labels || {}).map(([k, n]) => `${labelPill(k)} ${Number(n)}`).join(" ")}<span class="grow"></span><button class="btn" data-report="${Number(c.id)}">Mission report</button></div>`));
  const decisions = (ov.decisions || []).map((d) => `<div class="row">${pill(d.status)}<span class="grow">Decision #${Number(d.id)}: ${esc(d.topic)}</span><button class="btn" data-goal="${Number(d.goal_id)}">Open goal</button></div>`);
  return sec("Running", (ov.running || []).map((c) => simpleCard(c)), "No goal is running.")
    + sec("Waiting for approval", [...(ov.approvals || []).map((c) => simpleCard(c)), ...decisions], "Nothing waits for you.")
    + sec("Blocked", (ov.blocked || []).map((c) => simpleCard(c)), "Nothing is blocked.")
    + sec("Delivered", delivered, "No finished goal yet.");
}

// The mission report as HTML (also used by the goal view in app.js).
function missionReportHtml(d) {
  const t = d.tasks, c = d.cost, tm = d.time, v = d.verification;
  const met = tm.met === true ? "met" : tm.met === false ? "missed" : "no deadline";
  const list = (rows) => rows.length ? `<ul>${rows.map((x) => `<li>${esc(typeof x === "string" ? x : x.text)}</li>`).join("")}</ul>` : '<p class="muted">none</p>';
  return `<div class="row"><h3 class="grow" style="margin:0">Mission report: #${Number(d.goal_id)} ${esc(d.title)}</h3>${pill(d.status)}
      <a class="btn" href="/api/goals/${Number(d.goal_id)}/report?format=md" target="_blank" rel="noopener">Markdown</a></div>
    <h4>Objective</h4><pre>${esc(d.objective)}</pre>
    <h4>Plan</h4>${d.plan.map((p) => `<div class="muted">Iteration ${Number(p.iteration)}: ${p.tasks.map((x) => `${esc(x.key)} (${esc(x.role)})`).join(", ")}</div>`).join("") || '<p class="muted">none</p>'}
    <h4>Tasks</h4><p class="muted">${Number(t.done)} done · ${Number(t.failed)} failed · retried: ${esc(t.retried.join(", ") || "none")} · ${Object.entries(t.labels).map(([k, n]) => `${esc(k)} ${Number(n)}`).join(" · ")}</p>
    ${t.items.map((i) => `<details><summary>${labelPill(i.label)} <strong>${esc(i.key)}</strong> ${esc(i.title)} <span class="muted">${esc(i.role)} · iteration ${Number(i.iteration)} · ${esc(i.status)} · ${Number(i.runs)} run(s)</span></summary>
      ${i.evidence.map((e) => `<div class="muted">${e.passed ? "✓" : "✗"} ${esc(e.kind)} ${esc(e.check)}${e.detail ? ` — ${esc(e.detail)}` : ""}</div>`).join("") || '<div class="muted">No recorded check or review.</div>'}</details>`).join("")}
    <h4>Verification</h4>${v.checks.map((x) => `<div class="muted">${x.passed ? "✓" : "✗"} ${esc(x.check)} <span>(iteration ${Number(x.iteration)})</span></div>`).join("")}
    ${v.reviews.map((r) => `<div class="muted">${r.passed ? "✓" : "✗"} review by ${esc(r.reviewer)} (${esc(r.role)}) of ${esc(r.reviewed.join(", ") || "-")} (iteration ${Number(r.iteration)})</div>`).join("") || (v.checks.length ? "" : '<p class="muted">none recorded</p>')}
    <h4>Cost</h4><p class="muted">${c.budget_usd ? `${money(c.usd)} of ${money(c.budget_usd)}` : `${money(c.usd)} (no budget)`} · ${Number(c.tokens).toLocaleString()} tokens</p>
    <h4>Time</h4><p class="muted">${tm.duration != null ? dur(tm.duration) : "-"} (${dur(tm.active)} of agent work) · deadline ${esc(tm.deadline ? tm.deadline.slice(0, 16).replace("T", " ") : "-")}: ${met}</p>
    <h4>Remaining limitations</h4>${list(d.limitations)}
    <h4>Lessons</h4>${list(d.lessons)}
    <h4>Recommendations</h4>${list(d.recommendations)}`;
}

async function simpleReport(id) {
  modal(`<div id="mission-report"><div class="cc-working"><span class="spin"></span> Loading the report of goal #${Number(id)}…</div></div>`);
  try { $("#mission-report").innerHTML = missionReportHtml(await api(`/api/goals/${Number(id)}/report`)); }
  catch (e) { $("#mission-report").innerHTML = `<p class="muted">${esc(e.message)}</p>`; }
}

registerView({ id: "simple", title: "Mission", icon: "dashboard" }, viewSimple);
