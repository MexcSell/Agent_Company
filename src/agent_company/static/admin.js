"use strict";
// Notifications, settings and audit views.

async function viewNotifications() {
  const n = await api("/api/notifications");
  $("#view").innerHTML = `${me.is_admin ? `<div class="card" style="margin-bottom:12px"><h2>Post a notice to every user</h2><form id="n-form" class="row"><input id="n-title" class="grow" required placeholder="Notice"><select id="n-level" style="width:auto"><option>info</option><option>warning</option><option>success</option><option>error</option></select><button class="btn icon primary" title="Post" aria-label="Post">${icon("send", 16)}</button></form></div>` : ""}
    <div class="card"><div class="row"><h2 class="grow" style="margin:0">Notifications</h2>${ib("check", "Mark all read", `id="n-read" ${n.unread ? "" : "disabled"}`)}</div>
    <div class="events" style="margin-top:8px">${n.items.map((x) => `<div class="row" style="${x.read ? "opacity:.65" : ""}">${pill(x.level === "error" ? "failed" : x.level === "warning" ? "pending" : x.level === "success" ? "done" : "info")}<strong>${esc(x.title)}</strong><span class="muted grow">${esc(x.body)}</span>${x.goal_id ? `<a href="#" data-goal="${x.goal_id}">goal #${x.goal_id}</a>` : ""}<span class="muted">${ago(x.at)}</span>${me.is_admin ? `${ib("trash", "Delete", `data-del="${x.id}"`, "sm ghost")}` : ""}</div>`).join("") || '<span class="muted">No notifications.</span>'}</div></div>`;
  $("#n-read").onclick = async () => { await api("/api/notifications/read", { method: "POST" }); viewNotifications(); updateBadges(); };
  document.querySelectorAll("[data-goal]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); goalModal(Number(a.dataset.goal)); }));
  document.querySelectorAll("[data-del]").forEach((b) => (b.onclick = async () => { await api(`/api/notifications/${b.dataset.del}`, { method: "DELETE" }); viewNotifications(); updateBadges(); }));
  if (me.is_admin) $("#n-form").onsubmit = async (e) => { e.preventDefault(); try { await api("/api/notifications", { method: "POST", body: { title: $("#n-title").value, level: $("#n-level").value } }); viewNotifications(); updateBadges(); } catch (err) { toast(err.message); } };
}

// Connection: HTTPS (own CA, default) or HTTP (sealed cookie); see transport.py.
function connectionCard(tls, row) {
  const on = (m) => (tls.mode === m ? "selected" : "");
  const where = tls.behind_proxy ? `You are connected over <strong>${esc(tls.current_scheme.toUpperCase())}</strong>.` : "You are connected to the app directly (not through nginx); switching does not move this tab.";
  return `<div class="card" style="margin-bottom:12px"><h2>Connection</h2>
    <p class="muted">${where} HTTPS encrypts everything. HTTP sends pages in the clear and only seals the session cookie (encrypted, tied to the browser and its IP).</p>
    <details><summary>How the session cookie is protected</summary><p class="muted">The cookie holds the session token sealed with AES-256-GCM (key in <code>data/secret.key</code>), bound to this browser's User-Agent and, over HTTP, to its IP address, so a copied cookie works nowhere else. It is HttpOnly (scripts cannot read it), SameSite=Lax (not sent with cross-site posts), Secure on HTTPS, and expires after 30 days. Every answer also carries X-Content-Type-Options, X-Frame-Options, Referrer-Policy and Permissions-Policy headers; API answers are never cached.</p></details>
    <div class="row"><select id="tls-mode" style="width:auto"><option value="https" ${on("https")}>HTTPS (recommended)</option><option value="http" ${on("http")}>HTTP</option></select>
      <button class="btn primary" id="tls-save">Switch</button>${row && row.overridden ? '<button class="btn" id="tls-reset">Reset to .env</button>' : ""}
      <a class="btn" href="/api/tls/ca.crt" download="agent-company-ca.crt">Download certificate (CA)</a></div>
    ${tls.managed ? "" : '<p class="muted">nginx is not managed by this server (MANAGE_NGINX=0): the setting is saved but nothing is reconfigured.</p>'}
    <table style="margin-top:8px"><tr><td class="muted">CA</td><td>${esc(tls.ca_name)} · valid until ${esc(tls.ca_valid_until)}</td></tr>
      <tr><td class="muted">CA fingerprint (SHA-256)</td><td class="mono" style="word-break:break-all">${esc(tls.ca_fingerprint)}</td></tr>
      <tr><td class="muted">Server certificate</td><td>${tls.cert_valid_until ? `valid until ${esc(tls.cert_valid_until)} (renewed automatically at startup) · ${tls.hosts.map(esc).join(", ")}` : "not created yet"}</td></tr></table>
    <details style="margin-top:8px"><summary>Install the certificate once per computer (keeps HTTPS fast)</summary>
      <p class="muted">Without it the browser warns on every visit and Chrome turns off its cache, so every page reloads all scripts. Install, then restart the browser.</p><ul>
      <li><b>Windows</b>: open the file → Install Certificate → Current User → Place all certificates in → <i>Trusted Root Certification Authorities</i>.</li>
      <li><b>macOS</b>: open the file (Keychain Access, System) → double-click <i>Agent Company CA</i> → Trust → <i>Always Trust</i>.</li>
      <li><b>Linux, Chrome/Edge</b>: Settings → Privacy and security → Security → Manage certificates → Authorities → Import, tick <i>Trust this certificate for identifying websites</i>.</li>
      <li><b>Firefox</b>: Settings → Privacy &amp; Security → Certificates → View Certificates → Authorities → Import, tick <i>identify websites</i>.</li></ul></details></div>`;
}

async function switchTransport(mode, reset) {
  if (!confirm(`Switch every computer to ${mode ? mode.toUpperCase() : "the .env setting"}? This tab reconnects; other browsers follow on their next visit.`)) return;
  let now = mode;
  try {
    if (reset) { await api("/api/settings/transport", { method: "DELETE" }); now = (await api("/api/tls")).mode; }
    else await api("/api/settings/transport", { method: "PUT", body: { value: mode } });
  } catch (e) { toast(e.message); return; }
  const tls = await api("/api/tls").catch(() => null);
  if (tls && !tls.behind_proxy) { toast(`Saved: ${now.toUpperCase()}`); return viewSettings(); }
  if (now === "http" && location.protocol === "https:") {
    const s = deviceSecret(false);
    location.href = `http://${location.host}${location.pathname}${s ? "#ac-device=" + encodeURIComponent(s) : ""}`;
  } else if (now === "https" && location.protocol === "http:") location.reload();
  else viewSettings();
}

// Navigation: the rail's two groups (and hidden icons), the same for every user. Edit, then drag.
function navCard() {
  const g = navGroups(), item = (v) => `<button type="button" class="nav-item" data-v="${v.id}" title="${esc(v.title)}">${icon(v.icon, 18)}<span>${esc(v.title)}</span></button>`;
  const col = (key, label, hint, list) => `<div class="nav-col"><h3>${label}</h3><p class="muted">${hint}</p><div class="nav-list" data-group="${key}">${list.map(item).join("")}</div></div>`;
  return `<div class="card nav-card" style="margin-bottom:12px"><div class="row"><h2 class="grow" style="margin:0">Navigation</h2>
      ${ib("edit", "Edit: drag icons between the lists", 'id="nav-edit"')}${ib("save", "Save for every user", 'id="nav-save" disabled', "primary")}${ib("refresh", "Back to the default order", 'id="nav-reset"')}</div>
    <p class="muted">The icon rail on the left: the top group, then the bottom group. Press Edit, then drag icons to reorder them, move them between groups, or to Hidden (drag back to add them again). The rail's own edit button (bottom of the rail) does the same in place.</p>
    <div class="nav-cols" id="nav-cols">${col("top", "Top", "Work", g.top)}${col("bottom", "Bottom", "Tools and administration", g.bottom)}${col("hidden", "Hidden", "Not on the rail", g.hidden)}</div></div>`;
}
function wireNavCard() {
  const box = $("#nav-cols");
  if (!box) return;
  let lay = null;
  $("#nav-edit").onclick = () => { box.classList.add("editing"); wireNavDrag(box, (l) => { lay = l; $("#nav-save").disabled = false; }); toast("Drag the icons, then save"); };
  $("#nav-save").onclick = async () => { if (lay) { await saveNav(lay); box.classList.remove("editing"); $("#nav-save").disabled = true; } };
  $("#nav-reset").onclick = async () => { if (confirm("Restore the default order for every user?")) { await saveNav(NAV_DEFAULT); viewSettings(); } };
}

async function viewSettings(quiet) {
  if (quiet && $("#s-table")) return;
  const [all, tls, versions] = await Promise.all([api("/api/settings"), api("/api/tls"), api("/api/policy/versions").catch(() => [])]);
  const rows = all.filter((r) => r.key !== "transport");
  const input = (r) => r.type === "bool" ? `<select data-k="${r.key}"><option value="true" ${r.value ? "selected" : ""}>on</option><option value="false" ${r.value ? "" : "selected"}>off</option></select>`
    : r.choices.length ? `<select data-k="${r.key}">${r.choices.map((c) => `<option ${c === r.value ? "selected" : ""}>${esc(c)}</option>`).join("")}</select>`
    : r.type === "list" ? `<textarea data-k="${r.key}" rows="2">${esc((r.value || []).join("\n"))}</textarea>`
    : `<input data-k="${r.key}" value="${esc(r.value)}" ${r.type === "str" ? "" : 'type="number" step="any"'}>`;
  $("#view").innerHTML = navCard() + connectionCard(tls, all.find((r) => r.key === "transport")) + `<div class="card"><h2>Settings</h2><p class="muted">Saved settings override <code>.env</code> and apply immediately. Reset returns a setting to its <code>.env</code> value.</p>
    <div class="table-wrap"><table id="s-table">${rows.map((r) => `<tr><td><strong>${esc(r.key)}</strong><div class="muted">${esc(r.description)}</div></td><td style="min-width:200px">${input(r)}</td><td class="row nowrap">${ib("save", "Save", `data-save="${r.key}"`, "sm primary")}${r.overridden ? ib("refresh", "Reset to the .env value", `data-reset="${r.key}"`, "sm") : ""}</td></tr>`).join("")}</table></div></div>
    <div class="card"><h2>Policy versions</h2><p class="muted">Every change of a governance setting (approval, budgets, recruitment approval, proposals, command rules, …) is kept as a version with its author. Restoring one is a change of its own. Projects add stricter rules on their edit form.</p>
    ${versions.length ? `<div class="table-wrap"><table><tr><th class="num">Version</th><th>Change</th><th>By</th><th>When</th><th></th></tr>${versions.map((v, i) => `<tr><td class="num">${v.version}</td><td>${esc(v.note)}</td><td>${esc(v.author)}</td><td>${ago(v.created_at)}</td><td>${i ? `<button class="btn sm" data-restore="${v.version}">Restore</button>` : '<span class="muted">in force</span>'}</td></tr>`).join("")}</table></div>` : '<p class="muted">No governance setting changed yet.</p>'}</div>`;
  wireNavCard();
  document.querySelectorAll("[data-restore]").forEach((b) => (b.onclick = async () => {
    if (!confirm(`Restore the governance settings of version ${b.dataset.restore}?`)) return;
    try { await api("/api/policy/restore", { method: "POST", body: { scope: "company", version: Number(b.dataset.restore) } }); toast("Restored"); viewSettings(); } catch (e) { toast(e.message); }
  }));
  document.querySelectorAll("[data-save]").forEach((b) => (b.onclick = async () => {
    const el = document.querySelector(`[data-k="${b.dataset.save}"]`);
    try { await api(`/api/settings/${b.dataset.save}`, { method: "PUT", body: { value: el.value } }); toast("Saved"); viewSettings(); } catch (e) { toast(e.message); }
  }));
  document.querySelectorAll("[data-reset]").forEach((b) => (b.onclick = async () => { await api(`/api/settings/${b.dataset.reset}`, { method: "DELETE" }); viewSettings(); }));
  $("#tls-save").onclick = () => switchTransport($("#tls-mode").value, false);
  $("#tls-reset") && ($("#tls-reset").onclick = () => switchTransport(null, true));
}

async function viewAudit() {
  const a = await api("/api/audit");
  $("#view").innerHTML = `<div class="card"><div class="row"><h2 class="grow" style="margin:0">Audit log</h2>${a.chain.ok ? pill("ok") + `<span class="muted">hash chain intact over ${a.chain.records} record(s)</span>` : `<span class="pill failed">chain broken at #${a.chain.broken_at}</span>`}<button class="btn keep-text" id="audit-sec">Security incidents</button></div>
    <div class="table-wrap" style="margin-top:8px"><table><tr><th>#</th><th>When</th><th>Actor</th><th>Action</th><th>Target</th><th>Change</th><th>Hash</th></tr>
    ${a.records.map((r) => `<tr><td class="num">${r.id}</td><td>${ago(r.created_at)}</td><td>${esc(r.actor)}</td><td>${r.severity === "high" ? '<span class="pill failed">high</span> ' : ""}${esc(r.action)}</td><td>${esc(r.target)}</td><td class="mono">${esc(JSON.stringify(r.after))}</td><td class="mono muted">${esc(r.hash)}</td></tr>`).join("")}</table></div></div>`;
  $("#audit-sec").onclick = () => show("security");  // incidents moved to the Security view (S15-R-03)
}

async function viewEvolution() {
  const [ev, agents] = await Promise.all([api("/api/evolution"), api("/api/agents")]);
  const roles = [...new Set(agents.filter((a) => a.parent_id && a.lifecycle !== "retired").map((a) => a.role))];
  $("#view").innerHTML = `<div class="grid two"><div class="grid">
    <div class="card"><h2>How agents evolve</h2><p class="muted">Lessons are curated into versioned playbooks. An agent whose recent success rate drops below 50% tries a failure-aware playbook for 3 tasks; it is promoted only if it beats the old success rate by at least <code>min_improvement</code> points (Settings; a tie keeps the old one), otherwise rolled back. An agent failing more than 70% of its last 10 tasks is flagged for review. After a goal, idle clones merge back into their original.</p>
      ${me.is_admin ? `<div class="row"><button class="btn" id="ev-cycle">Diagnose all agents now</button>${roles.length ? `<select id="ev-role" style="width:auto">${roles.map((r) => `<option>${esc(r)}</option>`).join("")}</select><button class="btn" id="ev-cons">Merge idle clones</button>` : ""}</div>` : ""}</div>
    <div class="card"><div class="row nowrap"><h2 class="grow" style="margin:0">Learning rotation</h2>${me.is_admin || me.permissions?.manage_agents ? ib("book", "Study now: the next agent rewrites its definition from new knowledge and its lessons", 'id="ev-study"', "primary") : ""}</div>
      <p class="muted">Learning changes the agents themselves: each agent is a versioned definition file (Agents → an agent → Definition). One agent at a time reads what the research rooms collected and its own lessons, and rewrites its working method; every change is a commit. Runs every <code>study_interval</code> seconds (Settings; 0 = only on demand).</p>
      <div class="events" id="ev-learning"></div></div>
    <div class="card" id="ev-recruit"></div>
    ${(ev.duplicates || []).length ? `<div class="card"><h2>Near-duplicate agents (${ev.duplicates.length})</h2><p class="muted">Same role and almost the same skills: candidates to merge or retire, so the registry does not sprawl.</p>${ev.duplicates.map((d) => `<div class="row"><a href="#" data-agent="${d.a.id}">${esc(d.a.name)}</a><span class="muted">≈</span><a href="#" data-agent="${d.b.id}" class="grow">${esc(d.b.name)}</a><span class="muted">${esc(d.role)} · ${Math.round(d.similarity * 100)}%</span></div>`).join("")}</div>` : ""}
    <div class="card"><h2>Running trials (${ev.experiments.length})</h2>${ev.experiments.map((x) => `<div class="row"><a href="#" data-agent="${x.agent_id}" class="grow">${esc(x.agent)}</a><span class="muted">baseline ${Math.round((x.baseline || 0) * 100)}% · ${x.outcomes.map((o) => (o ? "✓" : "✗")).join(" ") || "no tasks yet"}</span></div>`).join("") || '<span class="muted">None.</span>'}</div></div>
    <div class="card"><h2>Evolution events</h2><div class="events">${ev.events.map((e) => `<div><span class="k">${esc(e.stage)}</span>${esc(e.title)} <span class="muted">${ago(e.at)}</span></div>`).join("") || '<span class="muted">Nothing yet: events appear as agents finish tasks.</span>'}</div></div></div>`;
  document.querySelectorAll("[data-agent]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); agentModal(Number(a.dataset.agent)); }));
  $("#ev-study") && ($("#ev-study").onclick = async (e) => { e.currentTarget.classList.add("busy"); try { const r = await api("/api/learning/study", { method: "POST" }); toast(r.changed ? `${r.studied}: ${r.lines} line(s) rewritten with ${r.model}` : `${r.studied || "No agent"}: no change ${r.detail ? `(${r.detail})` : ""}`); viewEvolution(); } catch (err) { toast(err.message); } });
  api("/api/events").then((evs) => { const box = $("#ev-learning"); if (box) box.innerHTML = evs.filter((x) => x.kind === "learning").slice(-12).reverse().map((x) => `<div><span class="k">learning</span>${esc(x.message)} <span class="muted">${ago(x.at)}</span></div>`).join("") || '<span class="muted">No study visit yet.</span>'; }).catch(() => {});
  $("#ev-cycle") && ($("#ev-cycle").onclick = async () => { const r = await api("/api/evolution/cycle", { method: "POST" }); toast(`${r.experiments_started} trial(s) started`); viewEvolution(); });
  $("#ev-cons") && ($("#ev-cons").onclick = async () => { const r = await api("/api/agents/consolidate", { method: "POST", body: { role: $("#ev-role").value } }); toast(`${r.length} clone(s) merged`); viewEvolution(); });
  learningPanel();
  recruitmentPanel();
  if (me.is_admin) codeEvolution();
}

// Recruitment (SPEC-03): requests with their job description and gap, approval by an administrator, probation.
async function recruitmentPanel() {
  const box = $("#ev-recruit");
  if (!box) return;
  const recs = await api("/api/recruitments");
  const pending = recs.filter((r) => r.status === "pending" || r.status === "qualifying" || (r.status === "approved" && r.pending_permissions.length));
  const row = (r) => {
    const jd = r.job_description || {};
    const gap = Object.entries(r.gap || {}).map(([k, v]) => `${esc(k)} ${v.available}/${v.required}`).join(" · ");
    const q = r.qualification || {};
    const qual = q.status ? ` · qualification: ${esc(q.status)}${q.suite ? ` (${esc(q.suite)}${q.pass_rate != null ? ` ${Math.round(q.pass_rate * 100)}%` : ""})` : ""}${r.evaluation_cost_usd ? ` $${r.evaluation_cost_usd.toFixed(2)}` : ""}` : "";
    const act = !me.is_admin ? "" : r.status === "qualifying" ? `<button class="btn sm" data-rrej="${r.id}" data-rcancel="1">Cancel</button>`
      : (r.status === "pending" || r.pending_permissions.length)
      ? `<button class="btn sm primary" data-rapp="${r.id}">${r.status === "pending" ? "Approve" : `Grant ${esc(r.pending_permissions.join(", "))}`}</button>${r.status === "pending" ? `<button class="btn sm" data-rrej="${r.id}">Reject</button>` : ""}` : "";
    return `<div class="row" title="${esc((jd.responsibilities || []).join("\n"))}"><span class="grow"><b>#${r.id}</b> ${esc(r.decision)} · ${esc(jd.title || r.requested_role)}${r.agent ? ` → <a href="#" data-agent="${r.agent_id}">${esc(r.agent)}</a>` : ""}${r.goal_id ? ` · <a href="#" data-goal="${r.goal_id}">goal #${r.goal_id}</a>` : ""}<div class="muted ellipsis" style="max-width:420px" title="${esc(r.note || "")}">${esc(r.reason)}${gap ? ` · gap: ${gap}` : ""}${qual}</div></span>${pill(r.status === "approved" ? "done" : r.status === "pending" || r.status === "qualifying" ? "pending" : "failed").replace(/>[^<]+</, `>${esc(r.status)}<`)}${act}</div>`;
  };
  box.innerHTML = `<h2>Recruitment (${pending.length} waiting)</h2><p class="muted">A task that needs a role nobody holds becomes a recruitment request with a job description. With <code>qualify_recruits</code> on, a new recruit of a writing role first runs an eval suite as a candidate (never staffed for real work); failing rejects it. Setting <code>recruit_requires_approval</code> decides which wait for an administrator; the goal pauses until then. A recruit's first 3 tasks are reviewed by QA; 2 failures during probation retire it.</p>
    <div class="events">${[...pending, ...recs.filter((r) => !pending.includes(r)).slice(0, 12)].map(row).join("") || '<span class="muted">No recruitment yet.</span>'}</div>`;
  box.querySelectorAll("[data-agent]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); agentModal(Number(a.dataset.agent)); }));
  box.querySelectorAll("[data-goal]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); goalModal(Number(a.dataset.goal)); }));
  box.querySelectorAll("[data-rapp]").forEach((b) => (b.onclick = async () => { try { const r = await api(`/api/recruitments/${b.dataset.rapp}/approve`, { method: "POST", body: { grant: true } }); toast(r.resumed ? `Approved; goal #${r.goal_id} resumed` : "Approved"); recruitmentPanel(); } catch (err) { toast(err.message); } }));
  box.querySelectorAll("[data-rrej]").forEach((b) => (b.onclick = async () => { const cancel = !!b.dataset.rcancel; const reason = prompt(cancel ? "Cancel the qualification run and retire the candidate? Why? (the planner reads it)" : "Why is this recruitment rejected? (the planner reads it)") ; if (reason === null) return; try { await api(`/api/recruitments/${b.dataset.rrej}/reject`, { method: "POST", body: { reason, status: cancel ? "cancelled" : "rejected" } }); toast(cancel ? "Cancelled; the goal re-plans without it" : "Rejected; the goal re-plans without it"); recruitmentPanel(); } catch (err) { toast(err.message); } }));
}

// SPEC-13: experiments (baseline vs candidate on one suite), capability gaps, learning plans.
async function learningPanel() {
  let box = $("#lp");
  if (!box) { $("#view").insertAdjacentHTML("beforeend", '<div id="lp" class="grid" style="margin-top:12px"></div>'); box = $("#lp"); }
  let ex, gaps, plans, orch;
  try { [ex, gaps, plans, orch] = await Promise.all([api("/api/experiments"), api("/api/learning/gaps"), api("/api/learning/plans"), api("/api/strategies/evaluation")]); } catch (e) { box.innerHTML = `<div class="card"><h2>Experiments</h2><span class="muted">${esc(e.message)}</span></div>`; return; }
  const may = me.is_admin || me.permissions?.manage_agents;
  const pct = (v) => (typeof v === "number" ? `${Math.round(v * 100)}%` : "–");
  const tone = { rolled_out: "approved", canary: "running", baseline: "running", candidate: "running", review: "pending", rolled_back: "rejected", rejected: "rejected", failed: "failed", proposed: "pending", passed: "done", running: "running", planned: "pending", cancelled: "disabled" };
  const tag = (st) => pill(tone[st] || "pending").replace(/>[^<]+</, `>${esc(st)}<`);
  const side = (r) => (r ? `${pct(r.pass_rate)} <span class="muted">n=${r.runs ?? "?"}</span>` : "–");
  const exp = (e) => {
    const r = e.results || {}, c = r.canary;
    const changes = (c) => Object.entries(c || {}).map(([k, v]) => `${k}=${Array.isArray(v) ? v.join(",") : v}`).join(", ");
    const what = e.target === "route" ? `${esc(e.role)} route: ${esc(e.baseline_version || "MODEL")} → ${esc(e.candidate.model)}`
      : e.target === "strategy" ? `${esc(e.role)} template ${esc(e.baseline_version)} → ${esc(changes(e.candidate.changes) || "candidate")}`
      : `${esc(e.agent || `agent ${e.agent_id}`)} playbook ${esc(e.baseline_version)} → candidate`;
    const strat = e.target === "strategy";
    const acts = may && (!strat || me.is_admin) ? [
      e.status === "proposed" ? `<button class="btn sm primary" data-ex="start" data-id="${e.id}">Run both sides</button>` : "",
      e.status === "review" && e.target === "route" && (me.is_admin || me.permissions?.manage_models) ? `<button class="btn sm primary" data-ex="apply" data-id="${e.id}">Apply the route</button>` : "",
      e.status === "review" && strat && r.comparison?.better ? `<button class="btn sm primary" data-ex="apply" data-id="${e.id}">Apply the template</button>` : "",
      ["proposed", "review"].includes(e.status) ? `<button class="btn sm ghost" data-ex="reject" data-id="${e.id}">Reject</button>` : "",
    ].join("") : "";
    return `<tr><td class="num">${e.id}</td><td>${what}${e.hypothesis ? `<div class="muted ellipsis">${esc(e.hypothesis)}</div>` : ""}</td><td>${esc(e.dataset.suite || "")}</td><td class="num">${side(r.baseline)}</td><td class="num">${side(r.candidate)}</td>
      <td>${c ? `${Object.values(c.outcomes || {}).map((o) => (o ? "✓" : "✗")).join(" ") || "…"} <span class="muted">/ ${c.window} vs ${pct(c.baseline)}</span>` : ""}</td><td>${tag(e.status)}<div class="muted ellipsis">${esc(e.decision || "")}</div></td><td>${acts}</td></tr>`;
  };
  const plan = (p) => `<div class="row">${tag(p.status)}<span class="grow"><strong>#${p.id}</strong> ${esc(p.agent || p.agent_id)}: ${esc(p.capability)} L${p.current_level} → L${p.target_level}
      <span class="muted">${esc(p.practice_suite || "no practice")} → ${esc(p.final_suite)} (pass ${pct(p.pass_mark)})${p.results.final ? ` · final ${pct(p.results.final.pass_rate)}` : ""}</span>${p.recommendation ? `<div class="muted ellipsis">${esc(p.recommendation)}</div>` : ""}</span>
      ${may && p.status === "planned" ? `<button class="btn sm primary" data-lp="start" data-id="${p.id}">Start</button>` : ""}${may && ["planned", "running"].includes(p.status) ? `<button class="btn sm ghost" data-lp="cancel" data-id="${p.id}">Cancel</button>` : ""}</div>`;
  box.innerHTML = `<div class="card"><div class="row nowrap"><h2 class="grow" style="margin:0">Experiments (${ex.experiments.length})</h2>${may ? '<button class="btn" id="ex-new">New experiment</button>' : ""}</div>
      <p class="muted">A candidate playbook, route or workflow template runs the same eval suite with the same repeats as the current version; the sample sizes are shown. A playbook candidate goes on by the automatic rule (better by <code>min_improvement</code>), a route or a template only when an administrator applies it. The deployed candidate then has a canary on live work and is rolled back as soon as it can no longer reach the baseline.</p>
      <div class="table-wrap"><table><tr><th class="num">#</th><th>Change</th><th>Suite</th><th class="num">Baseline</th><th class="num">Candidate</th><th>Canary</th><th>Status</th><th></th></tr>${ex.experiments.map(exp).join("") || '<tr><td colspan="8" class="muted">None yet: evolution candidates and your experiments appear here.</td></tr>'}</table></div>
      <details><summary class="muted">Self-improvement levels</summary>${ex.autonomy.map((a) => `<div class="row"><span class="k">${esc(a.level)}</span><span class="grow">${esc(a.what)}</span><span class="muted">${esc(a.control)}</span></div>`).join("")}</details></div>
    <div class="card"><h2>Orchestration strategies</h2><p class="muted">How each workflow template version did on live goals that ran under its rules (eval and platform goals left out), each figure with its sample size. A rule that fires on the current version's evidence becomes a strategy experiment; a candidate that weakens checks, reviews, protected tests, scope or authority is rejected. An administrator applies a better candidate; its canary restores the previous version when live goals get worse.</p>
      <div class="table-wrap"><table><tr><th>Template</th><th class="num">Goals</th><th class="num">Accepted</th><th class="num">1st pass</th><th class="num">Out of iterations</th><th class="num">Iterations</th><th class="num">Minutes</th><th class="num">Cost / accepted</th><th class="num">Failed runs</th><th class="num">Conflicts</th><th>Bottlenecks</th></tr>
      ${orch.flatMap((k) => (k.versions.length ? k.versions : [k.current]).map((v) => `<tr><td>${esc(k.kind)} v${esc(v.version ?? "?")}${v.current ? ' <span class="muted">in force</span>' : ""}</td><td class="num">${v.n}</td><td class="num">${pct(v.acceptance_rate)}</td><td class="num">${pct(v.first_pass_rate)}</td><td class="num">${v.n ? v.at_limit : "–"}</td><td class="num">${v.avg_iterations ?? "–"}</td><td class="num">${v.avg_minutes ?? "–"}</td><td class="num">${v.cost_per_accepted != null ? `$${v.cost_per_accepted.toFixed(4)}` : "–"}</td><td class="num">${v.failed_runs_per_goal ?? "–"}</td><td class="num">${pct(v.conflict_rate)}</td>
        <td class="muted">${esc([...(v.bottlenecks?.failed_checks || []).map((b) => `${b.check} ×${b.goals}`), ...(v.bottlenecks?.failing_roles || []).map((b) => `${b.role}: ${b.failed_runs} failed runs`)].join(" · "))}</td></tr>`)).join("")}</table></div>
      <div class="events">${orch.map((k) => `<div class="row"><span class="k">${esc(k.kind)}</span><span class="grow">${esc(k.recommendation.reasons.join("; "))}${k.open_experiment ? ` <span class="muted">· experiment #${k.open_experiment} open</span>` : ""}</span>${me.is_admin && Object.keys(k.recommendation.changes).length && !k.open_experiment ? `<button class="btn sm" data-rec="${esc(k.kind)}">Propose</button>` : ""}</div>`).join("")}</div></div>
    <div class="grid two"><div class="card"><h2>Capability gaps (${gaps.length})</h2><p class="muted">Per agent and skill, from verified knowledge, gate outcomes, eval runs and recency. Found gaps are raised by “Diagnose all agents now”.</p>
      <div class="events">${gaps.map((g, i) => `<div class="row"><span class="k">${esc(g.category.replace(/_/g, " "))}</span><span class="grow"><a href="#" data-agent="${g.agent_id}">${esc(g.agent)}</a> · ${esc(g.skill)} <span class="muted">${esc(g.detail)} · readiness ${pct(g.readiness)}</span></span>${may && !g.plan_id && g.skill !== "*" ? `<button class="btn sm" data-gap="${i}">Plan</button>` : g.plan_id ? `<span class="muted">plan #${g.plan_id}</span>` : ""}</div>`).join("") || '<span class="muted">No gaps.</span>'}</div></div>
    <div class="card"><h2>Learning plans (${plans.length})</h2><p class="muted">Practice exercises, then a separate final evaluation whose checks the agents never see. Both run as eval goals in scratch repositories; only the final evaluation's result changes a certified level.</p>
      <div class="events">${plans.map(plan).join("") || '<span class="muted">No plans yet.</span>'}</div></div></div>`;
  box.querySelectorAll("[data-agent]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); agentModal(Number(a.dataset.agent)); }));
  const run = async (b, path, body) => { b.classList.add("busy"); try { await api(path, { method: "POST", body }); } catch (e) { toast(e.message); } learningPanel(); };
  box.querySelectorAll("[data-ex]").forEach((b) => (b.onclick = () => {
    const what = b.dataset.ex;
    const target = ex.experiments.find((x) => String(x.id) === b.dataset.id)?.target;
    if (what === "apply" && !confirm(target === "strategy" ? "Save the candidate as the template's new version? Its canary restores the previous version if live goals get worse." : "Make the candidate model this role's route? Its canary restores the old route if live work gets worse.")) return;
    run(b, `/api/experiments/${b.dataset.id}/${what}`, what === "reject" ? { note: "" } : undefined);
  }));
  box.querySelectorAll("[data-rec]").forEach((b) => (b.onclick = () => run(b, `/api/strategies/${b.dataset.rec}/recommend`)));
  box.querySelectorAll("[data-lp]").forEach((b) => (b.onclick = () => run(b, `/api/learning/plans/${b.dataset.id}/${b.dataset.lp}`)));
  box.querySelectorAll("[data-gap]").forEach((b) => (b.onclick = () => { const g = gaps[Number(b.dataset.gap)]; run(b, "/api/learning/plans", { agent_id: g.agent_id, capability: g.skill }); }));
  $("#ex-new") && ($("#ex-new").onclick = async () => {
    const target = prompt("Target: playbook, route or strategy", "route"); if (!target) return;
    const body = { target: target.trim() };
    if (body.target === "route") { body.role = prompt("Role", "developer") || ""; body.candidate = prompt("Candidate model (catalog name)") || ""; }
    else if (body.target === "strategy") { body.role = prompt("Goal kind (feature, bugfix, refactor, research, docs)", "feature") || ""; body.candidate = prompt("Template fields to change (JSON)", '{"max_iterations": 4}') || ""; }
    else { body.agent_id = Number(prompt("Agent id")); body.candidate = prompt("Candidate playbook (DO:/AVOID: lines)") || ""; }
    body.hypothesis = prompt("Hypothesis", "") || "";
    body.suite = prompt("Suite", "smoke") || "smoke";
    try { await api("/api/experiments", { method: "POST", body }); toast("Experiment proposed"); } catch (e) { toast(e.message); }
    learningPanel();
  });
  if (ex.experiments.some((e) => ["baseline", "candidate"].includes(e.status)) || plans.some((p) => p.status === "running")) setTimeout(() => $("#lp") === box && learningPanel(), 10000);
}

// Verified self-evolution of the platform's own code (selfevolve.py): contracts, old-vs-new benchmark, deploy, rollback.
async function codeEvolution() {
  const view = $("#view");
  let box = $("#ce");
  if (!box) { view.insertAdjacentHTML("afterbegin", '<div id="ce" class="grid" style="margin-bottom:12px"></div>'); box = $("#ce"); }
  let o;
  try { o = await api("/api/improvement"); } catch (e) { box.innerHTML = `<div class="card"><h2>Code evolution</h2><span class="muted">${esc(e.message)}</span></div>`; return; }
  const n = (v) => (typeof v === "number" ? (Math.abs(v) >= 100 ? v.toFixed(0) : +v.toFixed(4)) : esc(v ?? "–"));
  const verdict = (v) => pill({ accepted: "done", deployed: "approved", building: "running", verifying: "running", rejected: "rejected", failed: "failed" }[v] || "pending").replace(/>[^<]+</, `>${esc(v)}<`);
  const report = (r) => !r ? "" : `<div class="table-wrap"><table><tr><th>Metric</th><th class="num">Baseline</th><th class="num">Candidate</th><th class="num">Change</th><th class="num">Weight</th></tr>
    ${r.metrics.map((m) => `<tr><td>${esc(m.metric)}${m.guard ? ' <span class="muted" title="guarded: must not get worse">🛡</span>' : ""}</td><td class="num">${n(m.baseline)}</td><td class="num">${n(m.candidate)}</td><td class="num" style="color:var(--${m.status === "better" ? "ok" : m.status === "worse" ? "bad" : "muted"})">${m.change_pct > 0 ? "+" : ""}${m.change_pct}%</td><td class="num">${m.weight}</td></tr>`).join("")}</table></div>
    <p><strong>Weighted score ${r.score_pct > 0 ? "+" : ""}${r.score_pct}%</strong> <span class="muted">(needs ${r.required_pct}% · ${r.runs} run(s) per side · ${r.changed_files.length} file(s): ${esc(r.changed_files.slice(0, 6).join(", "))})</span></p>
    ${r.problems.length ? `<ul>${r.problems.map((p) => `<li class="muted">${esc(p)}</li>`).join("")}</ul>` : ""}`;
  const contract = (c) => {
    const busy = ["baseline", "building", "verifying"].includes(c.status);
    const last = [...c.candidates].reverse().find((x) => x.report);
    const actions = [
      ["draft", "verified", "rejected", "failed"].includes(c.status) ? `<button class="btn sm${c.status === "draft" ? " primary" : ""}" data-ce="start" data-id="${c.id}">${c.candidates.length ? "Build another candidate" : "Measure baseline and build"}</button>` : "",
      c.status === "verified" ? `<button class="btn sm primary" data-ce="deploy" data-id="${c.id}">Approve and deploy</button>` : "",
      ["monitoring", "stable"].includes(c.status) ? `<button class="btn sm danger" data-ce="rollback" data-id="${c.id}">Roll back</button>` : "",
      !["monitoring", "stable", "rolled_back", "cancelled"].includes(c.status) ? `<button class="btn sm ghost" data-ce="cancel" data-id="${c.id}">Cancel</button>` : "",
    ].join("");
    return `<details ${busy || ["verified", "monitoring"].includes(c.status) ? "open" : ""}><summary class="row"><strong class="grow">#${c.id} ${esc(c.title)}</strong><span class="muted">${esc(c.component_label)} · ${esc(c.layer)} · ${esc(c.kind)}</span>${pill({ verified: "done", stable: "approved", monitoring: "running", rolled_back: "rejected", draft: "pending" }[c.status] || c.status).replace(/>[^<]+</, `>${esc(c.status.replace("_", " "))}<`)}</summary>
      <div style="padding:6px 0 10px 12px"><p>${esc(c.objective)}</p>
      <p class="muted">Benchmark <code>${esc(c.benchmark.name)}</code> (sha ${esc((c.benchmark.sha256 || "").slice(0, 10))}, ${c.benchmark.repeats} run(s)) · allowed: <code>${esc(c.allowed_paths.join(" "))}</code>${c.high_risk ? ' · <span class="pill failed">high risk</span>' : ""}</p>
      ${Object.keys(c.baseline.metrics || {}).length ? `<p class="muted">Baseline at ${esc(c.baseline_commit.slice(0, 10))}: ${Object.entries(c.baseline.metrics).map(([k, v]) => `${esc(k)} ${n(v)}`).join(" · ")}</p>` : ""}
      ${c.candidates.map((x) => `<div class="row">${verdict(x.verdict)}<span>candidate ${x.n}</span><a href="#" data-goal="${x.goal_id}">goal #${x.goal_id}</a>${x.report ? `<span class="muted">score ${x.report.score_pct > 0 ? "+" : ""}${x.report.score_pct}%</span>` : ""}${x.reason ? `<span class="muted ellipsis">${esc(x.reason.split("\n")[0])}</span>` : ""}</div>`).join("")}
      ${last ? `<h3 style="margin:8px 0 4px">Old vs new (candidate ${last.n})</h3>${report(last.report)}` : ""}
      ${c.deployment.sha ? `<p>Deployed <code>${esc(c.deployment.sha.slice(0, 10))}</code> by ${esc(c.deployment.actor)} · ${esc(c.deployment.check || "")}</p>` : ""}
      ${(c.monitor.problems || []).length ? `<p style="color:var(--bad)">After deployment: ${c.monitor.problems.map(esc).join("; ")}</p>` : c.monitor.metrics ? `<p class="muted">After deployment: ${Object.entries(c.monitor.metrics).map(([k, v]) => `${esc(k)} ${n(v)}`).join(" · ")}</p>` : ""}
      ${c.rollback_sha ? `<p class="muted">Reverted by <code>${esc(c.rollback_sha.slice(0, 10))}</code></p>` : ""}${c.note ? `<p class="muted">${esc(c.note)}</p>` : ""}
      <div class="row">${actions}</div></div></details>`;
  };
  const weak = o.weaknesses.map((w, i) => `<div class="row">${pill(w.layer === "code" ? "failed" : "pending").replace(/>[^<]+</, `>${esc(w.layer)}<`)}<span class="grow"><strong>${w.count}×</strong> ${esc(w.component || "no component")} <span class="muted">${esc(w.reasons.join("; "))} — ${esc(w.action)}</span><div class="muted ellipsis">${w.examples.map((x) => `${esc(x.source)}: ${esc(x.text)}`).join(" · ")}</div></span>${w.contract_possible ? `<button class="btn sm" data-weak="${i}">Write contract</button>` : ""}</div>`).join("") || '<span class="muted">No failures in the last 30 days.</span>';
  box.innerHTML = `<div class="card"><div class="row nowrap"><h2 class="grow" style="margin:0">Code evolution</h2><button class="btn primary" id="ce-new">New improvement contract</button></div>
      <p class="muted">Verified self-evolution of this platform's own source code. A contract names one capability, the files that may change and a fixed benchmark. The baseline is measured, a candidate goal changes the code in a worktree (tests protected), then the platform itself — not the coding agent — runs the same benchmark on the old and new commit. A candidate is accepted only with a weighted score at least ${o.acceptance.min_score}% better, no guarded metric worse and no metric more than ${o.acceptance.max_regression}% worse. Deploying needs your approval; the safe restart swaps the code in${o.acceptance.auto_rollback ? " and a refused restart is reverted automatically" : ""}; the benchmark is re-run on the deployed commit.</p>
      ${o.contracts.map(contract).join("") || '<span class="muted">No contracts yet. Start with one capability, such as research retrieval (benchmark <code>retrieval</code>).</span>'}</div>
    <div class="grid two"><div class="card"><h2>Weaknesses by layer</h2><p class="muted">Recent failures, defects and errors sorted into the layer that caused them. Model and environment problems are not fixed by changing code.</p><div class="events">${weak}</div></div>
    <div class="card"><h2>Control plane</h2><p class="muted">Protected components are never named in a contract; a candidate touching them is rejected.</p>${o.components.map((c) => `<div class="row">${pill(c.protected ? "disabled" : c.high_risk ? "pending" : "active").replace(/>[^<]+</, `>${c.protected ? "protected" : c.high_risk ? "high risk" : "evolvable"}<`)}<span class="grow">${esc(c.label)} <span class="muted">${esc(c.improves || c.paths.map((p) => p.replace("src/agent_company/", "")).join(", "))}</span></span></div>`).join("")}</div></div>`;
  const act = async (b) => {
    const id = b.dataset.id, what = b.dataset.ce;
    let body;
    if (what === "deploy" && !confirm("Merge the best verified candidate into the platform and restart safely?")) return;
    if (what === "cancel" && !confirm("Cancel this contract and its running candidate?")) return;
    if (what === "rollback") { const reason = prompt("Why roll back? (the merge is reverted with a new commit)"); if (reason === null) return; body = { reason }; }
    b.classList.add("busy");
    try { await api(`/api/improvement/contracts/${id}/${what}`, { method: "POST", body }); toast({ start: "Started", deploy: "Deployed", rollback: "Rolled back", cancel: "Cancelled" }[what]); } catch (e) { toast(e.message); }
    codeEvolution();
  };
  box.querySelectorAll("[data-ce]").forEach((b) => (b.onclick = () => act(b)));
  box.querySelectorAll("[data-goal]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); goalModal(Number(a.dataset.goal)); }));
  $("#ce-new").onclick = () => contractForm(o);
  box.querySelectorAll("[data-weak]").forEach((b) => (b.onclick = () => { const w = o.weaknesses[Number(b.dataset.weak)]; contractForm(o, { component: w.component, layer: w.layer, limitation: `${w.count} recent failure(s): ${w.reasons.join("; ")}. ${w.examples.map((x) => `${x.source}: ${x.text}`).join(" | ")}` }); }));
  if (o.contracts.some((c) => ["baseline", "building", "verifying"].includes(c.status))) setTimeout(() => $("#ce") === box && codeEvolution(), 10000);
}

function contractForm(o, pre = {}) {
  const opts = (xs, sel) => xs.map(([v, l]) => `<option value="${esc(v)}" ${v === sel ? "selected" : ""}>${esc(l)}</option>`).join("");
  const field = (id, label, ph = "") => `<label>${label}<textarea id="cf-${id}" rows="2" placeholder="${esc(ph)}">${esc(pre[id] || "")}</textarea></label>`;
  const comps = o.components.filter((c) => !c.protected);
  modal(`<h2>New improvement contract</h2><form id="cf" class="grid">
    <label>Title<input id="cf-title" required placeholder="Improve research retrieval accuracy and reduce redundant searches"></label>
    <label>Objective (one capability)<textarea id="cf-objective" rows="2" required placeholder="Raise hit@3 and precision on concept and complex queries without returning duplicates"></textarea></label>
    <div class="row"><label class="grow">Component<select id="cf-component">${opts(comps.map((c) => [c.name, `${c.label}${c.high_risk ? " (high risk)" : ""}`]), pre.component || "memory")}</select></label>
      <label>Layer<select id="cf-layer">${opts(o.layers.filter((l) => !["model", "environment"].includes(l)).map((l) => [l, l]), pre.layer || "code")}</select></label>
      <label>Kind<select id="cf-kind">${opts(o.kinds.map((k) => [k, k]), "algorithmic")}</select></label>
      <label>Benchmark<select id="cf-benchmark">${opts(o.benchmarks.map((b) => [b.name, b.name]), "retrieval")}</select></label></div>
    <p class="muted" id="cf-bench"></p>
    <h3 style="margin:4px 0">Research proposal</h3>
    ${field("limitation", "Limitation", "Exact keyword overlap; no synonyms, no duplicate removal, zero-score items fill the results")}
    ${field("solution", "Proposed solution", "Normalise and stem terms, drop near-duplicates, skip items with no relevance, rank by BM25")}
    ${field("affected_files", "Affected files", "src/agent_company/learning.py, tests/test_retrieval_bm25.py")}
    ${field("benefits", "Expected benefits")}${field("risks", "Risks")}${field("tests", "Tests to add")}${field("targets", "Performance targets", "hit@3 ≥ 0.95, duplicate rate 0, latency within 25%")}
    <label>Allowed paths (optional, space-separated; default: the component's files and new tests)<input id="cf-paths"></label>
    <div class="row"><span class="grow muted">The benchmark and the acceptance criteria are fixed when the contract is written.</span><button class="btn primary">Write contract</button></div></form>`);
  const showBench = () => { const b = o.benchmarks.find((x) => x.name === $("#cf-benchmark").value); $("#cf-bench").textContent = b ? `${b.description} · weights ${Object.entries(b.weights).map(([k, w]) => `${k} ${w}`).join(", ")} · guarded ${b.guard.join(", ") || "none"}` : "No benchmark in benchmarks/."; };
  $("#cf-benchmark").onchange = showBench;
  showBench();
  $("#cf").onsubmit = async (e) => {
    e.preventDefault();
    const proposal = Object.fromEntries(["limitation", "solution", "affected_files", "benefits", "risks", "tests", "targets"].map((k) => [k, $(`#cf-${k}`).value.trim()]).filter(([, v]) => v));
    try {
      await api("/api/improvement/contracts", { method: "POST", body: { title: $("#cf-title").value.trim(), objective: $("#cf-objective").value.trim(), component: $("#cf-component").value, layer: $("#cf-layer").value, kind: $("#cf-kind").value, benchmark: $("#cf-benchmark").value, proposal, allowed_paths: $("#cf-paths").value.split(/\s+/).filter(Boolean) } });
      $("#modal").hidden = true; toast("Contract written"); codeEvolution();
    } catch (err) { toast(err.message); }
  };
}

async function viewSystem() {
  const [st, em, sc] = await Promise.all([api("/api/system"), api("/api/emergency"), api("/api/stops")]);
  const gb = (n) => (n ? `${(n / 1073741824).toFixed(1)} GB` : "–");
  const sup = st.supervisor;
  const row = (k, v) => `<tr><td class="muted">${k}</td><td>${v}</td></tr>`;
  $("#view").innerHTML = `<div class="grid halves">
    <div class="card"><h2>Host</h2><table>${row("System", esc(st.host.system))}${row("CPUs / load", `${st.host.cpus} · ${st.host.load.join(" / ")}`)}${row("Memory free", `${gb(st.host.memory_available)} of ${gb(st.host.memory_total)}`)}${row("Disk free (data)", `${gb(st.host.disk_free)} of ${gb(st.host.disk_total)}`)}</table></div>
    <div class="card"><h2>Service</h2><table>${row("Process", `pid ${st.service.pid} · Python ${esc(st.service.python)} · up ${dur(st.service.uptime_seconds)}`)}${row("Data", `<code>${esc(st.service.data_dir)}</code> · ${esc(st.service.database)}`)}${row("Executor", esc(st.service.executor))}${row("Work", `${st.work.running_tasks} task(s) running · ${st.work.busy_agents} agent(s) busy · ${Object.entries(st.work.goals).map(([k, v]) => `${v} ${k.replace("_", " ")}`).join(", ") || "no goals"}`)}</table></div>
    <div class="card"><h2>Repository</h2><table>${row("Path", `<code>${esc(st.repository.path)}</code>`)}${row("Branch", esc(st.repository.branch))}${row("Last commit", esc(st.repository.head))}${row("Uncommitted changes", st.repository.uncommitted_changes ? `<span class="pill pending">${st.repository.uncommitted_changes}</span> approved platform goals cannot merge until these are committed` : "none")}</table></div>
    <div class="card"><h2>Safe restart</h2>${sup && sup.running ? `<table>${row("Supervisor", `pid ${sup.pid} · server pid ${sup.child_pid} · ${sup.restarts} restart(s)`)}${row("Last restart", sup.last_restart ? ago(new Date(sup.last_restart * 1000).toISOString()) : "never")}${sup.pending ? row("Pending", esc(sup.pending)) : ""}${sup.last_error ? row("Last problem", `<span class="pill failed">refused</span> ${esc(sup.last_error)}`) : ""}</table>
      <p class="muted">Code changes are checked, tried on a side port and swapped in once no goal is running.</p>${ib("refresh", "Restart safely", 'id="sys-restart"', "primary")}`
      : `<p class="muted">Not supervised. Start the service with <code>uv run python -m agent_company supervise</code> to get automatic, safe restarts when the code changes (for example after approving a platform goal).</p>`}</div></div>
    <div class="card emergency ${em.on ? "on" : ""}" style="margin-top:12px"><div class="row"><span class="em-icon">${icon("emergency", 22)}</span><div class="grow"><h2 style="margin:0">Emergency stop</h2>
      <p class="muted" style="margin:2px 0 0">${em.on ? `On since ${ago(em.at)} by ${esc(em.actor)}: ${esc(em.reason)}. No agent run starts; new goals are cancelled. The Workspace still works.` : "Stops all autonomous work at once: running and queued goals are cancelled and no agent run starts until you lift it. The Workspace (your own tools) is not affected."}</p></div>
      ${em.on ? ib("play", "Lift the emergency stop", 'id="em-lift"', "primary") : ib("emergency", "Emergency stop", 'id="em-stop"', "danger")}</div>
      <h3 style="margin:12px 0 4px">Narrower stops</h3>
      <p class="muted" style="margin:0 0 6px">E0 one executor (${sc.executors.join(", ")}), E1 one agent (paused, its runs ended), E2 one goal (cancelled), E3 one project (its goals cancelled, new ones refused), E4 one room (its agents paused). The command line works without the server: <code>python -m agent_company stop --level E1 --scope &lt;agent id&gt;</code>.</p>
      ${sc.stops.map((x) => `<div class="row"><span class="pill failed">${esc(x.level)}</span><span class="grow">${esc(sc.levels[x.level])} <b>${esc(x.scope)}</b> · ${esc(x.reason)} <span class="muted">${esc(x.actor)}, ${ago(x.at)}</span></span><button class="btn" data-lift="${esc(x.level)}" data-scope="${esc(x.scope)}">Lift</button></div>`).join("") || '<span class="muted">None active.</span>'}
      <div class="row" style="margin-top:6px"><select id="st-level" style="width:auto">${["E0", "E1", "E2", "E3", "E4"].map((l) => `<option value="${l}">${l} ${esc(sc.levels[l])}</option>`).join("")}</select><input id="st-scope" placeholder="executor, agent id, goal id, project id or room key" class="grow"><button class="btn danger" id="st-add">Stop</button></div></div>
    <div class="grid halves" id="sys-host" style="margin-top:12px"><div class="card"><h2>Docker and virtual machines</h2><span class="muted">Checking…</span></div></div>`;
  $("#em-stop") && ($("#em-stop").onclick = async () => { const reason = prompt("Why stop all autonomous work?"); if (!reason) return; try { const r = await api("/api/emergency", { method: "POST", body: { on: true, reason } }); toast(`Emergency stop: ${r.cancelled_goals.length} goal(s) cancelled`); viewSystem(); } catch (e) { toast(e.message); } });
  $("#em-lift") && ($("#em-lift").onclick = async () => { if (!confirm("Lift the emergency stop?")) return; await api("/api/emergency", { method: "POST", body: { on: false } }); viewSystem(); });
  $("#st-add").onclick = async () => {
    const scope = $("#st-scope").value.trim(); if (!scope) return toast("Name what to stop");
    const reason = prompt("Why?"); if (!reason) return;
    try { const r = await api("/api/stops", { method: "POST", body: { level: $("#st-level").value, scope, reason } }); toast(`${r.level} stop: ${r.paused_agents.length} agent(s) paused, ${r.cancelled_goals.length} goal(s) cancelled`); viewSystem(); } catch (e) { toast(e.message); }
  };
  document.querySelectorAll("[data-lift]").forEach((b) => (b.onclick = async () => { if (!confirm(`Lift the ${b.dataset.lift} stop of ${b.dataset.scope}?`)) return; try { await api("/api/stops/lift", { method: "POST", body: { level: b.dataset.lift, scope: b.dataset.scope } }); viewSystem(); } catch (e) { toast(e.message); } }));
  $("#sys-restart") && ($("#sys-restart").onclick = async () => { try { await api("/api/system/restart", { method: "POST" }); toast("Restart requested"); } catch (e) { toast(e.message); } });
  hostCards();
}

// Docker and LXD virtual machines on this host (administrator's tools; agents never reach them).
async function hostCards() {
  const box = $("#sys-host");
  if (!box) return;
  let h;
  try { h = await api("/api/containers"); } catch (e) { box.innerHTML = `<div class="card"><h2>Docker and virtual machines</h2><span class="muted">${esc(e.message)}</span></div>`; return; }
  const d = h.docker, v = h.vm, busy = h.jobs.some((j) => j.status === "running");
  const row = (k, val) => `<tr><td class="muted">${k}</td><td>${val}</td></tr>`;
  const dis = busy ? "disabled" : "";
  const dockerBody = !d.installed
    ? `<p class="muted">Docker is not installed.</p><button class="btn primary" data-host="docker/install" ${dis}>Install Docker</button>`
    : `<table>${row("Daemon", d.running ? `${pill("active")} ${esc(d.version || "")}` : `${pill("failed")} stopped${d.error ? ` <span class="muted">${esc(d.error)}</span>` : ""}`)}${row("Start at boot", d.enabled ? "yes" : "no")}${d.running ? row("Containers", `${d.containers_running} running of ${d.containers} · ${d.images} images`) : ""}${d.disk.map((x) => row(esc(x.Type), `${esc(x.Size)} · reclaimable ${esc(x.Reclaimable)}`)).join("")}</table>
      <div class="row" style="margin-top:8px">${d.running ? `<button class="btn" data-host="docker/restart" data-sure="Restart Docker? Running containers restart too." ${dis}>Restart</button><button class="btn danger" data-host="docker/stop" data-sure="Stop Docker? Every running container on this host stops, including other people's." ${dis}>Stop</button><button class="btn" id="dk-clean" ${dis}>Clean up…</button>` : `<button class="btn primary" data-host="docker/start" ${dis}>Start Docker</button>`}</div>`;
  const vmReady = v.lxd_installed && v.lxd_initialized;
  const vmBody = `<table>${row("KVM", v.kvm ? `${pill("active")} /dev/kvm` : `${pill("failed")} missing${v.cpu_virtualization ? "" : " (no VT-x/AMD-V)"}`)}${row("LXD", vmReady ? pill("active") : v.lxd_installed ? "installed, not initialised" : "not installed")}</table>
    ${vmReady ? `${v.instances.length ? `<div class="table-wrap"><table>${v.instances.map((i) => `<tr><td><strong>${esc(i.name)}</strong><div class="muted">${esc(i.type)} · ${esc(i.image)}${i.cpus ? ` · ${esc(i.cpus)} CPU` : ""}${i.memory ? ` · ${esc(i.memory)}` : ""}</div></td><td>${pill(i.status === "Running" ? "active" : "paused")} ${esc(i.status)}<div class="muted">${i.ipv4.map(esc).join(", ")}</div></td><td class="row">${i.status === "Running" ? `<button class="btn sm" data-host="vms/${esc(i.name)}/stop" ${dis}>Stop</button>` : `<button class="btn sm" data-host="vms/${esc(i.name)}/start" ${dis}>Start</button>`}<button class="btn sm danger" data-host="vms/${esc(i.name)}/delete" data-sure="Delete ${esc(i.name)} and its disk?" ${dis}>Delete</button></td></tr>`).join("")}</table></div>` : '<p class="muted">No instances yet.</p>'}
      <form id="vm-form" class="row" style="margin-top:8px"><input id="vm-name" required placeholder="name" pattern="[a-z][a-z0-9\\-]{0,62}" style="width:120px"><input id="vm-image" value="ubuntu:24.04" style="width:130px"><select id="vm-kind" style="width:auto"><option value="vm">VM</option><option value="ct">container</option></select><input id="vm-cpus" type="number" min="1" value="2" style="width:60px" title="CPUs"><input id="vm-mem" value="4GiB" style="width:70px" title="Memory"><button class="btn primary" ${dis}>Launch</button></form>`
    : v.kvm ? `<button class="btn primary" data-host="lxd/install" style="margin-top:8px" ${dis}>${v.lxd_installed ? "Initialise LXD" : "Install LXD"}</button>` : ""}`;
  const jobs = h.jobs.length ? `<div class="card"><h2>Jobs</h2><div class="events">${h.jobs.map((j) => `<details><summary>${pill(j.status)} ${esc(j.kind.replace("_", " "))} <strong>${esc(j.target)}</strong> <span class="muted">${ago(new Date(j.started * 1000).toISOString())}</span></summary><pre style="white-space:pre-wrap;max-height:240px;overflow:auto">${esc(j.output || j.commands.join("\n"))}</pre></details>`).join("")}</div><p class="muted">Full log: <code>data/logs/containers.log</code></p></div>` : "";
  box.innerHTML = `<div class="card"><h2>Docker</h2>${dockerBody}</div><div class="card"><h2>Virtual machines (LXD)</h2>${vmBody}</div>${jobs}`;
  const run = async (path, body) => { try { await api(`/api/containers/${path}`, { method: "POST", body }); toast("Started"); hostCards(); } catch (e) { toast(e.message); } };
  box.querySelectorAll("[data-host]").forEach((b) => (b.onclick = () => { if (b.dataset.sure && !confirm(b.dataset.sure)) return; run(b.dataset.host); }));
  $("#vm-form") && ($("#vm-form").onsubmit = (e) => { e.preventDefault(); run("vms", { name: $("#vm-name").value.trim(), image: $("#vm-image").value.trim(), vm: $("#vm-kind").value === "vm", cpus: Number($("#vm-cpus").value), memory: $("#vm-mem").value.trim() }); });
  $("#dk-clean") && ($("#dk-clean").onclick = () => cleanupModal());
  if (busy) setTimeout(() => $("#sys-host") === box && hostCards(), 3000);
}

async function cleanupModal() {
  const opts = () => ({ all_images: $("#cl-all").checked, volumes: $("#cl-vol").checked, older_than: $("#cl-age").value.trim() });
  const show = async () => {
    $("#cl-out").innerHTML = '<span class="muted">Checking…</span>';
    try {
      const r = await api("/api/containers/cleanup", { method: "POST", body: opts() });
      const list = (xs, f) => xs.length ? `<ul>${xs.slice(0, 50).map((x) => `<li>${f(x)}</li>`).join("")}${xs.length > 50 ? `<li class="muted">… ${xs.length - 50} more</li>` : ""}</ul>` : '<p class="muted">none</p>';
      $("#cl-out").innerHTML = `<h3>Stopped containers (${r.stopped_containers.length})</h3>${list(r.stopped_containers, (c) => `${esc(c.name)} <span class="muted">[${esc(c.image)}] ${esc(c.status)}</span>`)}
        <h3>${r.all_images ? "Images no container uses" : "Dangling images"} (${r.images.length})</h3>${list(r.images, (i) => `${esc(i.name)} <span class="muted">${esc(i.id)} · ${esc(i.size)}</span>`)}
        <p>Custom networks: ${r.custom_networks} (only those with no container are removed) · Build cache reclaimable: ${esc(r.build_cache_reclaimable)}${r.unused_anonymous_volumes !== undefined ? ` · Unused anonymous volumes: ${r.unused_anonymous_volumes}` : ""}</p>
        <p class="muted">Only things older than ${esc(r.older_than)} are removed. Running containers, the images they use and named volumes are never touched.</p>`;
    } catch (e) { $("#cl-out").innerHTML = `<p class="muted">${esc(e.message)}</p>`; }
  };
  modal(`<h2>Clean up Docker</h2><div class="row"><label class="row"><input type="checkbox" id="cl-all"> also tagged images no container uses (must be pulled or built again)</label></div>
    <div class="row"><label class="row"><input type="checkbox" id="cl-vol"> also unused anonymous volumes</label></div>
    <div class="row">Older than <input id="cl-age" value="24h" style="width:70px"><button class="btn" id="cl-check">Check again</button><button class="btn danger" id="cl-apply">Remove</button></div><div id="cl-out"></div>`);
  $("#cl-check").onclick = show;
  $("#cl-all").onchange = show;
  $("#cl-vol").onchange = show;
  $("#cl-apply").onclick = async () => {
    if (!confirm("Remove what is listed?")) return;
    try { await api("/api/containers/cleanup", { method: "POST", body: { ...opts(), apply: true } }); toast("Clean-up started"); $("#modal").hidden = true; hostCards(); } catch (e) { toast(e.message); }
  };
  show();
}

registerView({ id: "system", title: "System", icon: "system", admin: true }, viewSystem);
registerView({ id: "evolution", title: "Evolution", icon: "evolution" }, viewEvolution, 4);
registerView({ id: "notifications", title: "Notifications", icon: "bell" }, viewNotifications);
registerView({ id: "settings", title: "Settings", icon: "settings", admin: true }, viewSettings);
registerView({ id: "audit", title: "Audit log", icon: "audit", admin: true }, viewAudit);
