"use strict";
// Notifications, settings and audit views.

async function viewNotifications() {
  const n = await api("/api/notifications");
  $("#view").innerHTML = `${me.is_admin ? `<div class="card" style="margin-bottom:12px"><h2>Post a notice to every user</h2><form id="n-form" class="row"><input id="n-title" class="grow" required placeholder="Notice"><select id="n-level" style="width:auto"><option>info</option><option>warning</option><option>success</option><option>error</option></select><button class="btn primary">Post</button></form></div>` : ""}
    <div class="card"><div class="row"><h2 class="grow" style="margin:0">Notifications</h2><button class="btn" id="n-read" ${n.unread ? "" : "disabled"}>Mark all read</button></div>
    <div class="events" style="margin-top:8px">${n.items.map((x) => `<div class="row" style="${x.read ? "opacity:.65" : ""}">${pill(x.level === "error" ? "failed" : x.level === "warning" ? "pending" : x.level === "success" ? "done" : "info")}<strong>${esc(x.title)}</strong><span class="muted grow">${esc(x.body)}</span>${x.goal_id ? `<a href="#" data-goal="${x.goal_id}">goal #${x.goal_id}</a>` : ""}<span class="muted">${ago(x.at)}</span>${me.is_admin ? `<button class="btn sm ghost" data-del="${x.id}" title="Delete">✕</button>` : ""}</div>`).join("") || '<span class="muted">No notifications.</span>'}</div></div>`;
  $("#n-read").onclick = async () => { await api("/api/notifications/read", { method: "POST" }); viewNotifications(); updateBadges(); };
  document.querySelectorAll("[data-goal]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); selectedGoal = Number(a.dataset.goal); show("goals"); }));
  document.querySelectorAll("[data-del]").forEach((b) => (b.onclick = async () => { await api(`/api/notifications/${b.dataset.del}`, { method: "DELETE" }); viewNotifications(); updateBadges(); }));
  if (me.is_admin) $("#n-form").onsubmit = async (e) => { e.preventDefault(); try { await api("/api/notifications", { method: "POST", body: { title: $("#n-title").value, level: $("#n-level").value } }); viewNotifications(); updateBadges(); } catch (err) { toast(err.message); } };
}

// Connection: HTTPS (own CA, default) or HTTP (sealed cookie); see transport.py.
function connectionCard(tls, row) {
  const on = (m) => (tls.mode === m ? "selected" : "");
  const where = tls.behind_proxy ? `You are connected over <strong>${esc(tls.current_scheme.toUpperCase())}</strong>.` : "You are connected to the app directly (not through nginx); switching does not move this tab.";
  return `<div class="card" style="margin-bottom:12px"><h2>Connection</h2>
    <p class="muted">${where} HTTPS encrypts everything. HTTP sends pages in the clear and only seals the session cookie (encrypted, tied to the browser and its IP).</p>
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

async function viewSettings(quiet) {
  if (quiet && $("#s-table")) return;
  const [all, tls] = await Promise.all([api("/api/settings"), api("/api/tls")]);
  const rows = all.filter((r) => r.key !== "transport");
  const input = (r) => r.type === "bool" ? `<select data-k="${r.key}"><option value="true" ${r.value ? "selected" : ""}>on</option><option value="false" ${r.value ? "" : "selected"}>off</option></select>`
    : r.choices.length ? `<select data-k="${r.key}">${r.choices.map((c) => `<option ${c === r.value ? "selected" : ""}>${esc(c)}</option>`).join("")}</select>`
    : r.type === "list" ? `<textarea data-k="${r.key}" rows="2">${esc((r.value || []).join("\n"))}</textarea>`
    : `<input data-k="${r.key}" value="${esc(r.value)}" ${r.type === "str" ? "" : 'type="number" step="any"'}>`;
  $("#view").innerHTML = connectionCard(tls, all.find((r) => r.key === "transport")) + `<div class="card"><h2>Settings</h2><p class="muted">Saved settings override <code>.env</code> and apply immediately. Reset returns a setting to its <code>.env</code> value.</p>
    <div class="table-wrap"><table id="s-table">${rows.map((r) => `<tr><td><strong>${esc(r.key)}</strong><div class="muted">${esc(r.description)}</div></td><td style="min-width:200px">${input(r)}</td><td class="row"><button class="btn sm primary" data-save="${r.key}">Save</button>${r.overridden ? `<button class="btn sm" data-reset="${r.key}">Reset</button>` : ""}</td></tr>`).join("")}</table></div></div>`;
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
  $("#view").innerHTML = `<div class="card"><div class="row"><h2 class="grow" style="margin:0">Audit log</h2>${a.chain.ok ? pill("ok") + `<span class="muted">hash chain intact over ${a.chain.records} record(s)</span>` : `<span class="pill failed">chain broken at #${a.chain.broken_at}</span>`}</div>
    <div class="table-wrap" style="margin-top:8px"><table><tr><th>#</th><th>When</th><th>Actor</th><th>Action</th><th>Target</th><th>Change</th><th>Hash</th></tr>
    ${a.records.map((r) => `<tr><td class="num">${r.id}</td><td>${ago(r.created_at)}</td><td>${esc(r.actor)}</td><td>${r.severity === "high" ? '<span class="pill failed">high</span> ' : ""}${esc(r.action)}</td><td>${esc(r.target)}</td><td class="mono">${esc(JSON.stringify(r.after))}</td><td class="mono muted">${esc(r.hash)}</td></tr>`).join("")}</table></div></div>`;
}

async function viewEvolution() {
  const [ev, agents] = await Promise.all([api("/api/evolution"), api("/api/agents")]);
  const roles = [...new Set(agents.filter((a) => a.parent_id && a.lifecycle !== "retired").map((a) => a.role))];
  $("#view").innerHTML = `<div class="grid two"><div class="grid">
    <div class="card"><h2>How agents evolve</h2><p class="muted">Lessons are curated into versioned playbooks. An agent whose recent success rate drops below 50% tries a failure-aware playbook for 3 tasks; it is promoted only if it does at least as well as before, otherwise rolled back. After a goal, idle clones merge back into their original.</p>
      ${me.is_admin ? `<div class="row"><button class="btn" id="ev-cycle">Diagnose all agents now</button>${roles.length ? `<select id="ev-role" style="width:auto">${roles.map((r) => `<option>${esc(r)}</option>`).join("")}</select><button class="btn" id="ev-cons">Merge idle clones</button>` : ""}</div>` : ""}</div>
    <div class="card"><h2>Running trials (${ev.experiments.length})</h2>${ev.experiments.map((x) => `<div class="row"><a href="#" data-agent="${x.agent_id}" class="grow">${esc(x.agent)}</a><span class="muted">baseline ${Math.round((x.baseline || 0) * 100)}% · ${x.outcomes.map((o) => (o ? "✓" : "✗")).join(" ") || "no tasks yet"}</span></div>`).join("") || '<span class="muted">None.</span>'}</div></div>
    <div class="card"><h2>Evolution events</h2><div class="events">${ev.events.map((e) => `<div><span class="k">${esc(e.stage)}</span>${esc(e.title)} <span class="muted">${ago(e.at)}</span></div>`).join("") || '<span class="muted">Nothing yet: events appear as agents finish tasks.</span>'}</div></div></div>`;
  document.querySelectorAll("[data-agent]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); agentModal(Number(a.dataset.agent)); }));
  $("#ev-cycle") && ($("#ev-cycle").onclick = async () => { const r = await api("/api/evolution/cycle", { method: "POST" }); toast(`${r.experiments_started} trial(s) started`); viewEvolution(); });
  $("#ev-cons") && ($("#ev-cons").onclick = async () => { const r = await api("/api/agents/consolidate", { method: "POST", body: { role: $("#ev-role").value } }); toast(`${r.length} clone(s) merged`); viewEvolution(); });
}

async function viewSystem() {
  const st = await api("/api/system");
  const gb = (n) => (n ? `${(n / 1073741824).toFixed(1)} GB` : "–");
  const sup = st.supervisor;
  const row = (k, v) => `<tr><td class="muted">${k}</td><td>${v}</td></tr>`;
  $("#view").innerHTML = `<div class="grid halves">
    <div class="card"><h2>Host</h2><table>${row("System", esc(st.host.system))}${row("CPUs / load", `${st.host.cpus} · ${st.host.load.join(" / ")}`)}${row("Memory free", `${gb(st.host.memory_available)} of ${gb(st.host.memory_total)}`)}${row("Disk free (data)", `${gb(st.host.disk_free)} of ${gb(st.host.disk_total)}`)}</table></div>
    <div class="card"><h2>Service</h2><table>${row("Process", `pid ${st.service.pid} · Python ${esc(st.service.python)} · up ${dur(st.service.uptime_seconds)}`)}${row("Data", `<code>${esc(st.service.data_dir)}</code> · ${esc(st.service.database)}`)}${row("Executor", esc(st.service.executor))}${row("Work", `${st.work.running_tasks} task(s) running · ${st.work.busy_agents} agent(s) busy · ${Object.entries(st.work.goals).map(([k, v]) => `${v} ${k.replace("_", " ")}`).join(", ") || "no goals"}`)}</table></div>
    <div class="card"><h2>Repository</h2><table>${row("Path", `<code>${esc(st.repository.path)}</code>`)}${row("Branch", esc(st.repository.branch))}${row("Last commit", esc(st.repository.head))}${row("Uncommitted changes", st.repository.uncommitted_changes ? `<span class="pill pending">${st.repository.uncommitted_changes}</span> approved platform goals cannot merge until these are committed` : "none")}</table></div>
    <div class="card"><h2>Safe restart</h2>${sup && sup.running ? `<table>${row("Supervisor", `pid ${sup.pid} · server pid ${sup.child_pid} · ${sup.restarts} restart(s)`)}${row("Last restart", sup.last_restart ? ago(new Date(sup.last_restart * 1000).toISOString()) : "never")}${sup.pending ? row("Pending", esc(sup.pending)) : ""}${sup.last_error ? row("Last problem", `<span class="pill failed">refused</span> ${esc(sup.last_error)}`) : ""}</table>
      <p class="muted">Code changes are checked, tried on a side port and swapped in once no goal is running.</p><button class="btn primary" id="sys-restart">Restart safely</button>`
      : `<p class="muted">Not supervised. Start the service with <code>uv run python -m agent_company supervise</code> to get automatic, safe restarts when the code changes (for example after approving a platform goal).</p>`}</div></div>
    <div class="grid halves" id="sys-host" style="margin-top:12px"><div class="card"><h2>Docker and virtual machines</h2><span class="muted">Checking…</span></div></div>`;
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
