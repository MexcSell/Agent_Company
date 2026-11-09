"use strict";
// Notifications, settings and audit views.

// The Notifications page is notifications.js (UI-NOTE-01).

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
    <div class="row" style="margin-top:8px"><label for="tls-domains" class="muted">Domain names</label>
      <input id="tls-domains" class="grow" placeholder="agent-company.internal, dash.lan" value="${esc((tls.domains || []).join(", "))}">
      <button class="btn primary" id="tls-domains-save">Save</button></div>
    <p class="muted">The names are added to the server certificate and to nginx. Every client computer needs a DNS record or a hosts-file entry that points the name to this server's IP address (see the ReadMe, “Own domain name”). Access by IP address keeps working. Leave the field empty to remove all names.</p>
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
  const g = navGroups(), item = v => `<button type="button" class="nav-item" data-v="${esc(v.id)}">${icon(v.icon,18)}${esc(v.title)}</button>`;
  const col = x => `<div class="nav-col"><input data-group-title maxlength="40" value="${esc(x.title)}" disabled><button data-shift="-1" disabled aria-label="Move group left">←</button><button data-shift="1" disabled aria-label="Move group right">→</button><div class="nav-list" data-group="${x.key}">${x.items.map(item).join("")}</div></div>`;
  return `<div class="card nav-card"><h2>Navigation</h2>${ib("edit","Edit",'id="nav-edit"')}${ib("save","Save for every user",'id="nav-save" disabled')}${ib("refresh","Reset",'id="nav-reset"')}<p class="muted">Rename or reorder groups. Drag pages between groups or to Hidden. Notifications, Audit, Settings and Users stay in the top bar.</p><div class="nav-cols" id="nav-cols">${g.groups.map(col).join("")}<div class="nav-col"><h3>Hidden</h3><div class="nav-list" data-group="hidden">${g.hidden.map(item).join("")}</div></div></div></div>`;
}
function wireNavCard() {
  const box = $("#nav-cols"); if (!box) return;
  $("#nav-edit").onclick = () => {
    box.classList.add("editing"); box.querySelectorAll("input, [data-shift]").forEach(el => el.disabled = false);
    const changed = () => $("#nav-save").disabled = false;
    wireNavDrag(box, changed); box.oninput = changed;
    box.querySelectorAll("[data-shift]").forEach(b => b.onclick = () => {
      const col = b.parentElement, next = Number(b.dataset.shift) < 0 ? col.previousElementSibling : col.nextElementSibling;
      if (!next || !next.querySelector('[data-group-title]')) return;
      box.insertBefore(Number(b.dataset.shift) < 0 ? col : next, Number(b.dataset.shift) < 0 ? next : col); changed();
    });
  };
  $("#nav-save").onclick = async () => { await saveNav(readNav(box)); viewSettings(); };
  $("#nav-reset").onclick = async () => { await saveNav(navDefault); viewSettings(); };
}

// Appearance: theme and accent are kept per browser (the top bar's theme button, Screen format); the
// Components preview shows each shared component once, with sample text only.
function appearanceCard() {
  return `<div class="card" style="margin-bottom:12px"><div class="row"><h2 class="grow" style="margin:0">Appearance</h2>
      ${ib("max", "Screen format and accent", 'id="s-layout"')}${ib("eye", "Components", 'id="s-components"')}</div>
    <p class="muted">Kept in this browser: theme <strong>${esc(themePref())}</strong> (the top bar's theme button cycles dark, light, system) and accent <strong>${esc(accentPref())}</strong> (Screen format). Components shows the shared page parts once.</p></div>`;
}
function wireAppearanceCard() {
  $("#s-layout") && ($("#s-layout").onclick = layoutPicker);
  $("#s-components") && ($("#s-components").onclick = componentsPreview);
}
function componentsPreview() {
  const sample = '<span class="badge neutral">Sample</span>';
  modal(`<div class="cmp-preview"><h2>Components ${sample}</h2><p class="muted">Every figure and text below is a sample, not live data.</p>
    <section><h3>Page head</h3><header class="page-head"><div class="page-head-text"><h2 class="page-title">Page title</h2><p class="page-sub muted">Subtitle (sample)</p></div>
      <div class="page-actions"><button type="button" class="btn keep-text">Secondary</button><button type="button" class="btn primary keep-text">Primary action</button></div></header></section>
    <section><h3>KPIs</h3><div class="grid kpis">
      <div class="card kpi"><span class="kpi-icon">${icon("goals", 18)}</span><div class="value">12</div><div class="label">Sample value</div></div>
      <div class="card kpi"><span class="kpi-icon">${icon("projects", 18)}</span><div class="value">3</div><div class="label">Sample with trend</div><div class="kpi-trend up">+1 (sample)</div></div>
      <button type="button" class="card kpi"><span class="kpi-icon">${icon("agents", 18)}</span><div class="value">7</div><div class="label">Sample, clickable</div><div class="kpi-trend down">−2 (sample)</div></button></div></section>
    <section><h3>Card</h3><section class="card"><div class="card-head"><h3>Card title</h3><a class="view-all" href="#" data-noop>View all</a></div><p class="muted" style="margin:0">Card content (sample).</p></section></section>
    <section><h3>Badges</h3><div class="row"><span class="badge ok">ok</span><span class="badge warn">warn</span><span class="badge bad">bad</span><span class="badge info">info</span><span class="badge neutral">neutral</span></div></section>
    <section><h3>Progress</h3><div class="grid" style="max-width:360px">${[["", 40], ["ok", 75], ["bad", 20]].map(([c, v]) => `<div class="progress ${c}" role="progressbar" aria-label="Sample progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${v}"><span style="width:${v}%"></span></div>`).join("")}</div></section>
    <section><h3>Stepper</h3><ol class="stepper"><li class="done">Plan</li><li class="current" aria-current="step">Build</li><li>Review</li><li class="failed">Merge</li></ol></section>
    <section><h3>Timeline</h3><ul class="timeline"><li class="ok"><time>sample time</time><div>A sample event that passed</div></li><li class="warn"><time>sample time</time><div>A sample event waiting</div></li><li class="bad"><time>sample time</time><div>A sample event that failed</div></li><li class="info"><time>sample time</time><div>A sample note</div></li></ul></section>
    <section><h3>Empty state</h3><div class="empty card">${icon("dashboard", 28)}<p class="empty-title">Nothing here yet</p><p class="muted">A sample empty state.</p></div></section>
    <section><h3>Tabs</h3><div class="tabs" role="tablist" id="cmp-tabs"><button type="button" class="tab active" role="tab" aria-selected="true">Overview</button><button type="button" class="tab" role="tab" aria-selected="false">Runs</button><button type="button" class="tab" role="tab" aria-selected="false">Settings</button></div></section>
    <section><h3>Drawer</h3><button type="button" class="btn keep-text" id="cmp-drawer">Open drawer</button></section></div>`);
  $("#modal-body [data-noop]").onclick = (e) => e.preventDefault();
  document.querySelectorAll("#cmp-tabs .tab").forEach((t) => (t.onclick = () => document.querySelectorAll("#cmp-tabs .tab").forEach((x) => { x.classList.toggle("active", x === t); x.setAttribute("aria-selected", String(x === t)); })));
  // The drawer sits under the modal: close the preview first, reopen it when the drawer closes.
  $("#cmp-drawer").onclick = () => { $("#modal").hidden = true; openDrawer("Example", '<p>A sample drawer: a right-side panel. Close it with ✕ or Escape.</p>', { onClose: componentsPreview }); };
}

// UI-SET-01: Settings sections, in order; a settings key goes to the first section whose pattern matches it.
const SET_SECTIONS = [["appearance", "Appearance", null], ["navigation", "Navigation", null], ["connection", "Connection", null],
  ["governance", "Governance & approvals", /approv|auto_merge|proposal|recruit|qualify|goal_depth|a2a|blocked_commands|task_critic|candidate_smoke|auto_push/],
  ["limits", "Limits & budgets", /budget|limit|cost|max_|timeout|stuck|quota|iteration|parallel/],
  ["security", "Sandbox & security", /sandbox|dependency|test_stages|classification|secret/],
  ["models", "Models & executors", /^enable_|executor|model|router|codex|copilot|claude/],
  ["knowledge", "Knowledge, research & learning", /retrieval|research|repo_map|rotation|study|learn|knowledge|benchmark|mcp|kpi|inactive|improvement/],
  ["other", "Other settings", /./], ["versions", "Policy versions", null], ["metrics", "Metrics", null]];
const SET_AUTONOMY = { G0: "observe: no runs", G1: "recommend: read-only", G2: "low-risk: the goal's worktree and test commands", G3: "approved workflows: G2 and granted MCP tools", G4: "bounded autonomy" };
async function viewSettings(quiet) {
  if (quiet && $("#s-table")) return;
  const [all, tls, versions, mtok] = await Promise.all([api("/api/settings"), api("/api/tls"), api("/api/policy/versions").catch(() => []), api("/api/metrics/token").catch(() => null)]);
  const rows = all.filter((r) => r.key !== "transport" && r.key !== "tls_domains");
  const input = (r) => r.type === "bool" ? `<select data-k="${r.key}"><option value="true" ${r.value ? "selected" : ""}>on</option><option value="false" ${r.value ? "" : "selected"}>off</option></select>`
    : r.choices.length ? `<select data-k="${r.key}">${r.choices.map((c) => `<option ${c === r.value ? "selected" : ""}>${esc(c)}</option>`).join("")}</select>`
    : r.type === "list" ? `<textarea data-k="${r.key}" rows="2">${esc((r.value || []).join("\n"))}</textarea>`
    : `<input data-k="${r.key}" value="${esc(r.value)}" ${r.type === "str" ? "" : 'type="number" step="any"'}>`;
  // UI-SET-01: the page in sections with a menu; the settings table is split by what a key governs (SET_SECTIONS)
  const secOf = (k) => SET_SECTIONS.find(([, , re]) => re && re.test(k))?.[0] || "other";
  const table = (sec) => { const rs = rows.filter((r) => secOf(r.key) === sec); return rs.length ? `<div class="table-wrap"><table>${rs.map((r) => `<tr><td><strong>${esc(r.key)}</strong><div class="muted">${esc(r.description)}</div></td><td style="min-width:200px">${input(r)}</td><td class="row nowrap">${ib("save", "Save", `data-save="${r.key}"`, "sm")}${r.overridden ? ib("refresh", "Reset to .env", `data-reset="${r.key}"`, "sm") : ""}</td></tr>`).join("")}</table></div>` : '<p class="muted">No setting here.</p>'; };
  const sections = SET_SECTIONS.filter(([k]) => k !== "appearance" && k !== "navigation" && k !== "connection" && k !== "metrics" && k !== "versions").map(([k, title]) => `<section class="card set-sec" id="set-${k}"><h2>${title}</h2>${k === "governance" ? `<p class="muted">Agents' autonomy levels (set per agent on its card): ${Object.entries(SET_AUTONOMY).map(([g, d]) => `<strong>${g}</strong> ${esc(d)}`).join(" · ")}. A project may only make these rules stricter (Projects → Edit).</p>` : ""}${table(k)}</section>`).join("");
  $("#view").innerHTML = `<div class="set-page"><nav class="set-nav card" aria-label="Settings sections">${SET_SECTIONS.map(([k, title]) => `<a href="#" data-sec="${k}">${title}</a>`).join("")}</nav><div class="set-main">
    <section class="set-sec" id="set-appearance">${appearanceCard()}</section><section class="set-sec" id="set-navigation">${navCard()}</section><section class="set-sec" id="set-connection">${connectionCard(tls, all.find((r) => r.key === "transport"))}</section>${sections}<span id="s-table" hidden></span>
    <section class="set-sec" id="set-versions"><div class="card"><h2>Policy versions</h2><p class="muted">Every change of a governance setting (approval, budgets, recruitment approval, proposals, command rules, …) is kept as a version with its author. Restoring one is a change of its own. Projects add stricter rules on their edit form.</p>
    ${versions.length ? `<div class="table-wrap"><table><tr><th class="num">Version</th><th>Change</th><th>By</th><th>When</th><th></th></tr>${versions.map((v, i) => `<tr><td class="num">${v.version}</td><td>${esc(v.note)}</td><td>${esc(v.author)}</td><td>${ago(v.created_at)}</td><td>${i ? `<button class="btn sm" data-restore="${v.version}">Restore</button>` : '<span class="muted">in force</span>'}</td></tr>`).join("")}</table></div>` : '<p class="muted">No governance setting changed yet.</p>'}</div>
    ${mtok ? `<div class="card set-sec" id="set-metrics"><h2>Metrics for Prometheus</h2><p class="muted"><code>GET /metrics</code> answers goals, tasks, runs, tool calls, tokens, cost, gate results, open incidents, agents and stops in the Prometheus text format: for an administrator's session, or a scraper sending <code>Authorization: Bearer &lt;token&gt;</code>. The token is shown once; only its hash is kept. No label carries a title, a prompt or a path.</p>
      <div class="row"><span class="grow">${mtok.set ? `Token set (…${esc(mtok.hint)})` : '<span class="muted">No token: only administrators\' sessions can read it.</span>'}</span>
      <button class="btn sm" id="mt-new">${mtok.set ? "Replace token" : "Create token"}</button>${mtok.set ? '<button class="btn sm danger" id="mt-del">Revoke</button>' : ""}</div></div>` : ""}</div></div>`;
  document.querySelectorAll("[data-sec]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); document.getElementById("set-" + a.dataset.sec)?.scrollIntoView({ behavior: "smooth", block: "start" }); }));
  wireNavCard();
  wireAppearanceCard();
  $("#mt-new") && ($("#mt-new").onclick = async () => {
    if (mtok.set && !confirm("Replace the metrics token? Scrapers using the old one stop working.")) return;
    try { const r = await api("/api/metrics/token", { method: "POST" }); prompt("The metrics token (shown only now): copy it into your Prometheus scrape config", r.token); viewSettings(); } catch (e) { toast(e.message); }
  });
  $("#mt-del") && ($("#mt-del").onclick = async () => { if (!confirm("Revoke the metrics token?")) return; await api("/api/metrics/token", { method: "DELETE" }).catch((e) => toast(e.message)); viewSettings(); });
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
  $("#tls-domains-save").onclick = async () => {
    const value = $("#tls-domains").value.trim();  // empty: no own names (DELETE clears the tls_domains setting)
    try {
      await api("/api/settings/tls_domains", value ? { method: "PUT", body: { value } } : { method: "DELETE" });
      toast(value ? "Domain names saved" : "Domain names removed");
      viewSettings();
    } catch (err) { toast(err.message); }
  };
}

async function viewAudit() {
  const a = await api("/api/audit");
  $("#view").innerHTML = `<div class="card"><div class="row"><h2 class="grow" style="margin:0">Audit log</h2>${a.chain.ok ? pill("ok") + `<span class="muted">hash chain intact over ${a.chain.records} record(s)</span>` : `<span class="pill failed">chain broken at #${a.chain.broken_at}</span>`}<button class="btn keep-text" id="audit-sec">Security incidents</button></div>
    <div class="table-wrap" style="margin-top:8px"><table><tr><th>#</th><th>When</th><th>Actor</th><th>Action</th><th>Target</th><th>Change</th><th>Hash</th></tr>
    ${a.records.map((r) => `<tr><td class="num">${r.id}</td><td>${ago(r.created_at)}</td><td>${esc(r.actor)}</td><td>${r.severity === "high" ? '<span class="pill failed">high</span> ' : ""}${esc(r.action)}</td><td>${esc(r.target)}</td><td class="mono">${esc(JSON.stringify(r.after))}</td><td class="mono muted">${esc(r.hash)}</td></tr>`).join("")}</table></div></div>`;
  $("#audit-sec").onclick = () => show("security");  // incidents moved to the Security view (S15-R-03)
}

// `root`: the Agent Evolution page's "Learning & experiments" tab (evolution.js) or the page itself.
async function viewEvolution(quiet, root = $("#view")) {
  const [ev, agents] = await Promise.all([api("/api/evolution"), api("/api/agents")]);
  const roles = [...new Set(agents.filter((a) => a.parent_id && a.lifecycle !== "retired").map((a) => a.role))];
  root.innerHTML = `<div class="grid two"><div class="grid">
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
  $("#ev-study") && ($("#ev-study").onclick = async (e) => { e.currentTarget.classList.add("busy"); try { const r = await api("/api/learning/study", { method: "POST" }); toast(r.changed ? `${r.studied}: ${r.lines} line(s) rewritten with ${r.model}` : `${r.studied || "No agent"}: no change ${r.detail ? `(${r.detail})` : ""}`); viewEvolution(false, root); } catch (err) { toast(err.message); } });
  api("/api/events").then((evs) => { const box = $("#ev-learning"); if (box) box.innerHTML = evs.filter((x) => x.kind === "learning").slice(-12).reverse().map((x) => `<div><span class="k">learning</span>${esc(x.message)} <span class="muted">${ago(x.at)}</span></div>`).join("") || '<span class="muted">No study visit yet.</span>'; }).catch(() => {});
  $("#ev-cycle") && ($("#ev-cycle").onclick = async () => { const r = await api("/api/evolution/cycle", { method: "POST" }); toast(`${r.experiments_started} trial(s) started`); viewEvolution(false, root); });
  $("#ev-cons") && ($("#ev-cons").onclick = async () => { const r = await api("/api/agents/consolidate", { method: "POST", body: { role: $("#ev-role").value } }); toast(`${r.length} clone(s) merged`); viewEvolution(false, root); });
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
    const act = !me.is_admin ? "" : r.source === "marketplace" ? '<span class="muted">decide on Agents → Marketplace</span>' : r.status === "qualifying" ? `<button class="btn sm" data-rrej="${r.id}" data-rcancel="1">Cancel</button>`
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
  if (!box) { ($("#evo-body") || $("#view")).insertAdjacentHTML("beforeend", '<div id="lp" class="grid" style="margin-top:12px"></div>'); box = $("#lp"); }
  let ex, gaps, plans, orch, suites;
  try { [ex, gaps, plans, orch, suites] = await Promise.all([api("/api/experiments"), api("/api/learning/gaps"), api("/api/learning/plans"), api("/api/strategies/evaluation"), api("/api/learning/suites")]); } catch (e) { box.innerHTML = `<div class="card"><h2>Experiments</h2><span class="muted">${esc(e.message)}</span></div>`; return; }
  const may = me.is_admin || me.permissions?.manage_agents;
  const pct = (v) => (typeof v === "number" ? `${Math.round(v * 100)}%` : "–");
  const tone = { rolled_out: "approved", canary: "running", baseline: "running", candidate: "running", review: "pending", rolled_back: "rejected", rejected: "rejected", failed: "failed", proposed: "pending", passed: "done", running: "running", planned: "pending", cancelled: "disabled" };
  const tag = (st) => pill(tone[st] || "pending").replace(/>[^<]+</, `>${esc(st)}<`);
  const side = (r) => (!r ? "–" : r.mrr_all != null ? `<span style="white-space:nowrap">MRR ${r.mrr_all.toFixed(2)} <span class="muted">n=${r.runs}</span></span><div class="muted" style="white-space:nowrap" title="recall@5 of evals/retrieval.json; MRR of the fixed benchmark">recall ${pct(r.recall)} · ${r.benchmark?.metrics ? `bench ${Number(r.benchmark.metrics.mrr).toFixed(2)}` : "no bench"}</div>`
    : `${pct(r.pass_rate)} <span class="muted">n=${r.runs ?? "?"}</span>`);
  const exp = (e) => {
    const r = e.results || {}, c = r.canary;
    const changes = (c) => Object.entries(c || {}).map(([k, v]) => `${k}=${Array.isArray(v) ? v.join(",") : v}`).join(", ");
    const what = e.target === "route" ? `${esc(e.role)} route: ${esc(e.baseline_version || "MODEL")} → ${esc(e.candidate.model)}`
      : e.target === "strategy" ? `${esc(e.role)} template ${esc(e.baseline_version)} → ${esc(changes(e.candidate.changes) || "candidate")}`
      : e.target === "retrieval" ? `ranking weights → ${esc(changes(e.candidate.changes))}`
      : `${esc(e.agent || `agent ${e.agent_id}`)} playbook ${esc(e.baseline_version)} → candidate`;
    const strat = e.target === "strategy" || e.target === "retrieval";
    const acts = may && (!strat || me.is_admin) ? [
      e.status === "proposed" ? `<button class="btn sm primary" data-ex="start" data-id="${e.id}">Run both sides</button>` : "",
      e.status === "review" && e.target === "route" && (me.is_admin || me.permissions?.manage_models) ? `<button class="btn sm primary" data-ex="apply" data-id="${e.id}">Apply the route</button>` : "",
      e.status === "review" && strat && r.comparison?.better ? `<button class="btn sm primary" data-ex="apply" data-id="${e.id}">Apply the ${e.target === "retrieval" ? "weights" : "template"}</button>` : "",
      e.status === "rolled_out" && e.target === "retrieval" && r.deployed ? `<button class="btn sm ghost" data-ex="rollback" data-id="${e.id}">Roll back</button>` : "",
      ["proposed", "review"].includes(e.status) ? `<button class="btn sm ghost" data-ex="reject" data-id="${e.id}">Reject</button>` : "",
    ].join("") : "";
    return `<tr><td class="num">${e.id}</td><td>${what}${e.hypothesis ? `<div class="muted ellipsis">${esc(e.hypothesis)}</div>` : ""}</td><td>${esc(e.dataset.suite || "")}${e.dataset.budget_usd ? `<div class="muted">budget ${money(e.dataset.budget_usd)}</div>` : ""}${e.dataset.source_commit ? `<div class="muted" title="The platform's commit it was set up on">@${esc(e.dataset.source_commit.slice(0, 8))}</div>` : ""}</td><td class="num">${side(r.baseline)}</td><td class="num">${side(r.candidate)}</td>
      <td>${c ? `${Object.values(c.outcomes || {}).map((o) => (o ? "✓" : "✗")).join(" ") || "…"} <span class="muted">/ ${c.window} vs ${pct(c.baseline)}</span>` : ""}</td><td>${tag(e.status)}<div class="muted ellipsis">${esc(e.decision || "")}</div></td><td>${acts}</td></tr>`;
  };
  const plan = (p) => `<div class="row">${tag(p.status)}<span class="grow"><strong>#${p.id}</strong> ${esc(p.agent || p.agent_id)}: ${esc(p.capability)} L${p.current_level} → L${p.target_level}
      <span class="muted">${esc(p.practice_suite || "no practice")} → ${esc(p.final_suite)} (pass ${pct(p.pass_mark)})${p.results.final ? ` · final ${pct(p.results.final.pass_rate)}` : ""}</span>${p.recommendation ? `<div class="muted ellipsis">${esc(p.recommendation)}</div>` : ""}</span>
      ${may && p.status === "planned" ? `<button class="btn sm primary" data-lp="start" data-id="${p.id}">Start</button>` : ""}${may && ["planned", "running"].includes(p.status) ? `<button class="btn sm ghost" data-lp="cancel" data-id="${p.id}">Cancel</button>` : ""}</div>`;
  box.innerHTML = `<div class="card"><div class="row nowrap"><h2 class="grow" style="margin:0">Experiments (${ex.experiments.length})</h2>${may ? '<button class="btn" id="ex-new">New experiment</button>' : ""}</div>
      <p class="muted">A candidate playbook, route or workflow template runs the same eval suite with the same repeats as the current version; the sample sizes are shown. A playbook candidate goes on by the automatic rule (better by <code>min_improvement</code>), a route or a template only when an administrator applies it. The deployed candidate then has a canary on live work and is rolled back as soon as it can no longer reach the baseline. Ranking weights are measured on the retrieval evals and the fixed retrieval benchmark (live work has no relevance labels, so there is no canary); an administrator applies them and can roll them back.</p>
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
      <div class="events">${plans.map(plan).join("") || '<span class="muted">No plans yet.</span>'}</div>
      <h3 style="margin-top:12px">Exercise suites</h3><p class="muted">Practice and final sets by skill. Generated ones are written by the instructor and kept only when each check fails on the starting files and passes with the reference solution; a generated final set needs an administrator's approval before plans use it.</p>
      <div class="events">${suites.filter((x) => ["practice", "final"].includes(x.kind)).map((x) => `<div class="row"><span class="k">${esc(x.kind)}</span><span class="grow">${esc(x.name)} <span class="muted">${esc(x.skills.join(", "))} · ${x.tasks.length} task(s)${x.generated ? ` · by ${esc(x.by || "?")}${x.dropped ? `, ${x.dropped} dropped` : ""}` : ""}</span></span>${x.approved ? "" : me.is_admin ? `<button class="btn sm primary" data-sapprove="${esc(x.name)}">Approve</button>` : '<span class="muted">awaits approval</span>'}</div>`).join("") || '<span class="muted">No suites.</span>'}</div>
      ${me.is_admin ? '<div class="row" style="margin-top:6px"><button class="btn sm" id="ex-gen">Generate exercises</button></div>' : ""}</div></div>`;
  box.querySelectorAll("[data-agent]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); agentModal(Number(a.dataset.agent)); }));
  const run = async (b, path, body) => { b.classList.add("busy"); try { await api(path, { method: "POST", body }); } catch (e) { toast(e.message); } learningPanel(); };
  box.querySelectorAll("[data-ex]").forEach((b) => (b.onclick = () => {
    const what = b.dataset.ex;
    const target = ex.experiments.find((x) => String(x.id) === b.dataset.id)?.target;
    if (what === "apply" && !confirm(target === "strategy" ? "Save the candidate as the template's new version? Its canary restores the previous version if live goals get worse." : target === "retrieval" ? "Set the candidate ranking weights? Roll back restores the current ones." : "Make the candidate model this role's route? Its canary restores the old route if live work gets worse.")) return;
    if (what === "rollback" && !confirm("Restore the ranking weights this experiment replaced?")) return;
    run(b, `/api/experiments/${b.dataset.id}/${what}`, what === "reject" ? { note: "" } : undefined);
  }));
  box.querySelectorAll("[data-rec]").forEach((b) => (b.onclick = () => run(b, `/api/strategies/${b.dataset.rec}/recommend`)));
  box.querySelectorAll("[data-lp]").forEach((b) => (b.onclick = () => run(b, `/api/learning/plans/${b.dataset.id}/${b.dataset.lp}`)));
  box.querySelectorAll("[data-sapprove]").forEach((b) => (b.onclick = () => { if (confirm(`Use ${b.dataset.sapprove} as a final evaluation set that certifies agents?`)) run(b, `/api/learning/suites/${encodeURIComponent(b.dataset.sapprove)}/approve`); }));
  $("#ex-gen") && ($("#ex-gen").onclick = async () => {
    const skill = prompt("Skill to write exercises for", "python"); if (!skill) return;
    const kind = (prompt("Kind: practice or final", "practice") || "").trim(); if (!kind) return;
    try { await api("/api/learning/suites", { method: "POST", body: { skill: skill.trim(), kind, count: 3 } }); toast("The instructor is writing the exercises; a notification follows."); } catch (e) { toast(e.message); }
  });
  box.querySelectorAll("[data-gap]").forEach((b) => (b.onclick = () => { const g = gaps[Number(b.dataset.gap)]; run(b, "/api/learning/plans", { agent_id: g.agent_id, capability: g.skill }); }));
  $("#ex-new") && ($("#ex-new").onclick = async () => {
    const target = prompt(`Target: playbook, route${me.is_admin ? ", strategy or retrieval" : ""}`, "route"); if (!target) return;
    const body = { target: target.trim() };
    if (body.target === "route") { body.role = prompt("Role", "developer") || ""; body.candidate = prompt("Candidate model (catalog name)") || ""; }
    else if (body.target === "strategy") { body.role = prompt("Goal kind (feature, bugfix, refactor, research, docs)", "feature") || ""; body.candidate = prompt("Template fields to change (JSON)", '{"max_iterations": 4}') || ""; }
    else if (body.target === "retrieval") { body.candidate = prompt("Ranking weights to change (relevance, technology, verified, author, recency, contradiction)", "recency=0.6") || ""; }
    else { body.agent_id = Number(prompt("Agent id")); body.candidate = prompt("Candidate playbook (DO:/AVOID: lines)") || ""; }
    body.hypothesis = prompt("Hypothesis", "") || "";
    if (body.target !== "retrieval") body.suite = prompt("Suite", "smoke") || "smoke";
    if (!["playbook", "retrieval"].includes(body.target)) { const b = prompt("Budget in USD for its eval runs (0 = no limit; it stops once both sides together cost that much)", "0"); if (b === null) return; body.budget_usd = Number(b) || 0; }
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
// The Evolution page itself is evolution.js (Agent Evolution: candidates, then this content as a tab).
registerView({ id: "settings", title: "Settings", icon: "settings", admin: true }, viewSettings);
registerView({ id: "audit", title: "Audit log", icon: "audit", admin: true }, viewAudit);
