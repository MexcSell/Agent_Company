"use strict";
/* Machines view (admins only): Linux and Windows virtual machines on this host's KVM (machines.py).
   Taken from AXoR 3.0's Machines page (frontend/machines.js). A list on the left; the selected machine on
   the right with Console (its screen, through noVNC and the API's relay), Overview (system, resources,
   disk, container, activity) and Logs (its download and setup). New and Edit open a form. Polls faster
   while a machine sets up, stops or is being deleted. */
const Machines = (() => {
  const API = "/api/machines";
  const ST = { running: ["Running", "ok"], starting: ["Setting up", "run"], failing: ["Failing", "bad"],
    stopping: ["Stopping", "run"], stopped: ["Stopped", "off"], deleting: ["Deleting", "run"] };
  const FAMILY = { linux: "Linux", windows: "Windows" };
  const I = {
    plus: "M12 5v14M5 12h14", monitor: "M3 4h18v12H3zM8 20h8M12 16v4",
    chip: "M7 7h10v10H7zM9 3v4M15 3v4M9 17v4M15 17v4M3 9h4M3 15h4M17 9h4M17 15h4",
    chips: "M3 7h18v10H3zM7 7v10M11 7v10M15 7v10M5 17v3M19 17v3", server: "M4 4h16v6H4zM4 14h16v6H4zM8 7h.01M8 17h.01",
    alert: "M12 3l10 18H2zM12 10v4M12 17h.01", reset: "M3 12a9 9 0 1 0 3-6.7M3 4v5h5", stop: "M6 6h12v12H6z", play: "M7 4l13 8-13 8z",
    edit: "M4 20h4L19 9l-4-4L4 16zM14 6l4 4", trash: "M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13",
    keyboard: "M2 6h20v12H2zM6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10", shrink: "M4 14h6v6M20 10h-6V4M14 10l7-7M3 21l7-7",
    expand: "M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7", close: "M6 6l12 12M18 6L6 18", clock: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 7v5l3 2",
    info: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 11v6M12 7h.01", terminal: "M4 4h16v16H4zM8 9l3 3-3 3M13 15h3",
    bell: "M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10 21a2 2 0 0 0 4 0",
  };
  const ic = (n) => `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${I[n] || I.info}"/></svg>`;
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const fmtNum = (n, d) => Number(n || 0).toLocaleString(undefined, { maximumFractionDigits: d });
  const err = (e) => toast(e.message);
  const bytes = (n) => (!n ? "–" : n < 1048576 ? `${fmtNum(n / 1024, 0)} KB` : n < 1073741824 ? `${fmtNum(n / 1048576, 0)} MB` : `${fmtNum(n / 1073741824, 2)} GB`);
  const agoS = (ts) => { const s = Date.now() / 1000 - ts; return s < 60 ? "just now" : s < 3600 ? `${Math.floor(s / 60)} min ago` : `${Math.floor(s / 3600)} h ago`; };
  const since = (ts) => (!ts ? "–" : Date.now() / 1000 - ts < 86400 ? agoS(ts) : new Date(ts * 1000).toLocaleDateString());
  const dot = (st) => `<span class="mc-dot mc-${(ST[st] || [, "off"])[1]}" role="img" aria-label="${esc((ST[st] || [st])[0])}"></span>`;
  const statusPill = (st) => `<span class="mc-pill mc-${(ST[st] || [, "off"])[1]}">${esc((ST[st] || [st])[0])}</span>`;
  const loading = (t) => `<div class="muted mc-loading"><span class="spin"></span> ${esc(t)}</div>`;
  const put = (el, html) => { if (el && el.dataset.html !== html) { el.innerHTML = html; el.dataset.html = html; } };
  const root = () => document.getElementById("machines");
  const busy = (x) => x && ["starting", "stopping", "deleting"].includes(x.status);
  const lead = "Linux and Windows virtual machines on this server's KVM: pick a system and use its screen right here.";
  let d = null, sel = null, cur = null, tab = "console", timer = null, logTail = 300;
  // Screen settings per machine, kept in this browser (Edit → Screen): applied at the next Connect, no restart.
  const DISPLAY = { quality: 6, compression: 2, interval: 0, viewOnly: false, dot: false, scale: "fit", clipboard: true, bell: true, background: "#000000" };
  const display = (name) => { try { return { ...DISPLAY, ...JSON.parse(store.get(`ac.machines.display.${name}`) || "{}") }; } catch { return { ...DISPLAY }; } };
  const PRESETS = { sharp: { quality: 9, compression: 0, interval: 0 }, balanced: { quality: 6, compression: 2, interval: 0 }, fast: { quality: 3, compression: 6, interval: 100 }, saver: { quality: 1, compression: 9, interval: 500 } };
  // the console: noVNC's RFB on a WebSocket to the API's relay
  let RFB = null, rfb = null, conn = { name: null, state: "idle", note: "" }, fit = true;

  async function open() {
    wire();
    if (!d) root().innerHTML = `<div class="mc-headline"><p class="muted">${lead}</p></div>${loading("Loading machines…")}`;
    await load();
    schedule();
  }
  function close() { clearTimeout(timer); disconnect(); }
  async function load() {
    if (!root()) return;
    try { d = await api(API); }
    catch (e) { d = null; disconnect(); root().innerHTML = `<p class="muted">${lead}</p><div class="card mc-empty">${esc(e.message)}</div>`; return; }
    if (!d.items.some((x) => x.name === sel)) sel = store.get("ac.machines.sel") || null;
    if (!d.items.some((x) => x.name === sel)) sel = d.items[0]?.name ?? null;
    cur = sel ? await api(`${API}/${encodeURIComponent(sel)}`).catch(() => cur) : null;
    render();
  }
  function schedule() {
    clearTimeout(timer);
    const hot = d?.items.some(busy) || busy(cur);
    timer = setTimeout(async () => { if (!root()?.isConnected) return close(); if (!document.hidden) await load(); schedule(); }, hot ? 3000 : 15000);
  }
  async function select(name) {
    disconnect();
    sel = name; store.set("ac.machines.sel", name); cur = null; render();
    cur = await api(`${API}/${encodeURIComponent(name)}`).catch((e) => (err(e), null)); render();
  }

  // ---------- page ----------
  function render() {
    if (!d || !root()) return;
    if (!$(".mc-main", root())) root().innerHTML = `<div class="mc-headline"><p class="muted">${lead}</p></div>
      <div data-part="kvm"></div>
      <div class="grid kpis mc-stats" data-part="stats"></div>
      <div class="mc-layout">
        <aside class="card mc-side"><div class="mc-side-head"><b>Machines</b><span class="grow"></span>
          <button type="button" class="btn sm primary" data-mc="new">${ic("plus")} New</button></div>
          <div class="mc-list" data-part="list"></div></aside>
        <div class="card mc-main" data-part="main"></div>
      </div>`;
    const on = d.items.filter((x) => x.status !== "stopped"), run = d.items.filter((x) => x.status === "running").length;
    put($("[data-part=kvm]", root()), !d.kvm.present ? `<div class="mc-err">${ic("alert")}<span>This server has no <code>/dev/kvm</code>, so machines can't start. Turn on virtualization (VT-x / AMD-V) in the firmware, or load the <code>kvm</code> module.</span></div>`
      : !d.kvm.usable ? `<div class="mc-err">${ic("alert")}<span>The service can't open <code>/dev/kvm</code>: run it as root or add its user to the <code>kvm</code> group.</span></div>` : "");
    const tile = (i, l, v, sub) => `<div class="card kpi"><div class="label">${ic(i)} ${l}</div><div class="value">${v}</div><div class="muted mc-sub">${sub}</div></div>`;
    put($("[data-part=stats]", root()), [
      tile("monitor", "Machines", `${run}/${d.items.length}`, `${d.items.length - run} not running`),
      tile("chip", "CPU", `${on.reduce((a, x) => a + (x.cores || 0), 0)}`, `virtual cores; ${fmtNum(d.items.reduce((a, x) => a + x.cpu, 0), 2)} in use`),
      tile("chips", "Memory", `${on.reduce((a, x) => a + (x.ram || 0), 0)} GB`, `given to machines; ${bytes(d.items.reduce((a, x) => a + x.memory, 0))} in use`),
      tile("server", "This server", `${d.host.cpus} CPUs`, `${bytes(d.host.memory)} memory · KVM ${d.kvm.present ? "available" : "missing"}`),
    ].join(""));
    put($("[data-part=list]", root()), d.items.map((x) => `<button type="button" class="mc-item" data-sel="${esc(x.name)}" aria-current="${x.name === sel}">
        ${dot(x.status)}<span class="mc-item-main"><b>${esc(x.name)}</b><small class="muted">${esc(x.title || x.os)} · ${x.cores} CPU · ${x.ram} GB</small></span></button>`).join("")
      || `<div class="mc-empty">No machines yet.<br><button type="button" class="btn sm primary" data-mc="new">${ic("plus")} New machine</button></div>`);
    renderMain();
  }

  function renderMain() {
    const el = $("[data-part=main]", root()); if (!el) return;
    if (!sel) return put(el, `<div class="mc-empty">A machine is a whole computer: Ubuntu, Debian, Fedora, Rocky, Arch and other Linux systems, or Windows 10, 11 and Server. Make one, then use its screen in the Console.</div>`);
    if (!cur) return put(el, loading("Loading…"));
    const x = cur, on = !["stopped", "stopping", "deleting"].includes(x.status), held = !!x.busy;
    const TABS = [["console", "Console", "monitor"], ["overview", "Overview", "info"], ["logs", "Logs", "terminal"]];
    if (!TABS.some((t) => t[0] === tab)) tab = "console";
    const head = `<div class="mc-head"><div class="mc-title"><h2>${esc(x.name)}</h2>${statusPill(x.status)}
        <span class="mc-fam mc-${esc(x.family || "linux")}">${esc(x.title || x.os)}</span></div>
      <div class="mc-acts">
        <button type="button" class="btn sm" data-mc="restart" ${!held && (x.status === "running" || x.status === "failing") ? "" : "disabled"}>${ic("reset")} Restart</button>
        <button type="button" class="btn sm" data-mc="${on ? "stop" : "start"}" ${held ? "disabled" : ""}>${ic(on ? "stop" : "play")} ${on ? "Stop" : "Start"}</button>
        <button type="button" class="btn sm" data-mc="edit" ${held ? "disabled" : ""}>${ic("edit")} Edit</button>
        <button type="button" class="icon-btn mc-danger" data-mc="delete" title="Delete" aria-label="Delete ${esc(x.name)}" ${held ? "disabled" : ""}>${ic("trash")}</button></div></div>
      ${x.message ? `<div class="mc-err">${ic(x.status === "failing" ? "alert" : "clock")}<span>${esc(x.message)}</span></div>` : ""}
      <div class="mc-tabs" role="tablist">${TABS.map(([k, l, i]) => `<button type="button" role="tab" data-tab="${k}" aria-selected="${tab === k}">${ic(i)} ${l}</button>`).join("")}</div>`;
    const live = tab === "console" && x.status === "running";
    // keep the console's screen (and the log's scroll position) through polls: only the head is redrawn
    if (el.dataset.showing === x.name && el.dataset.showtab === tab && el.dataset.live === String(live) && ["console", "logs"].includes(tab) && $(".mc-head", el)) {
      el.querySelectorAll(":scope > .mc-err, :scope > .mc-tabs").forEach((n) => n.remove());
      const h = $(".mc-head", el), tmp = document.createElement("div");
      tmp.innerHTML = head; h.replaceWith(...tmp.children); el.dataset.html = "";
      if (tab === "console" && !live) put($(".mc-wait", el), waitHtml());
    } else {  // a new screen element: the old connection goes with the old one (a manual disconnect stays one)
      const was = conn;
      disconnect();
      if (live && was.state === "manual" && was.name === x.name) conn = was;
      put(el, head + `<div class="mc-body ${live ? "mc-console" : ""}">${{ console: consoleTab, overview, logs: logsTab }[tab]()}</div>`);
    }
    el.dataset.showing = x.name; el.dataset.showtab = tab; el.dataset.live = String(live);
    if (tab === "logs" && !$(".mc-log", el).dataset.loaded) loadLogs();
    // The console connects only when Connect is pressed (never on its own when the tab opens).
    if (live && conn.name !== x.name && conn.state !== "on" && conn.state !== "connecting") conn = { name: x.name, state: "manual", note: "Not connected: press Connect." };
    if (live) showConn();
  }

  // ---------- console ----------
  function consoleTab() {
    const x = cur;
    if (x.status !== "running") return `<div class="mc-wait">${waitHtml()}</div>`;
    return `<div class="mc-bar"><span class="mc-state" data-part="conn" role="status"></span><span class="grow"></span>
        <span class="muted mc-dispinfo" data-part="dispinfo"></span>
        <button type="button" class="btn icon sm" data-mc="cad" title="Send Ctrl+Alt+Del" aria-label="Send Ctrl+Alt+Del">${ic("keyboard")}</button>
        <button type="button" class="btn icon sm" data-mc="fit" aria-pressed="${fit}" title="${fit ? "Fitted to the page: show at its own size" : "At its own size: fit to the page"}" aria-label="Fit or 100%">${ic(fit ? "shrink" : "expand")}</button>
        <button type="button" class="btn icon sm" data-mc="full" title="Full screen" aria-label="Full screen">${ic("expand")}</button>
        <button type="button" class="btn icon sm" data-mc="screen" title="Screen settings: quality, compression, refresh interval…" aria-label="Screen settings">${ic("chip")}</button>
        <button type="button" class="btn sm mc-connbtn" data-mc="conn"></button></div>
      <div class="mc-screen ${fit ? "" : "mc-native"}" data-part="screen"></div>
      <p class="muted mc-note">Click the screen to type into the machine. ${x.family === "windows"
        ? `Windows signs in as <b>${esc(x.settings.username || "")}</b> with the password set when it was made; the first start installs it, which takes 10 to 20 minutes after the download.`
        : esc(x.note || "A new machine boots its system's installer or live image: install it from here as on a new PC.")}</p>`;
  }
  function waitHtml() {
    const x = cur;
    if (["stopped", "stopping"].includes(x.status)) return `<div class="mc-empty">${ic("monitor")}<p>${esc(x.name)} is ${x.status === "stopping" ? "shutting down" : "stopped"}.</p>
      ${x.status === "stopped" ? `<button type="button" class="btn primary" data-mc="start">${ic("play")} Start</button>` : ""}</div>`;
    if (x.status === "deleting") return `<div class="mc-empty">${ic("trash")}<p>Deleting ${esc(x.name)} and its disk…</p></div>`;
    if (x.status === "failing") return `<div class="mc-empty">${ic("alert")}<p>It isn't running: see the message above and the Logs.</p></div>`;
    return `<div class="mc-empty">${ic("clock")}<p>Setting up ${esc(x.title || "")}. The screen opens here once the virtual machine runs.</p>
      ${x.progress ? `<code class="mc-progress">${esc(x.progress)}</code>` : ""}<p class="muted">The first start downloads the system${x.family === "windows" ? " from Microsoft (several GB)" : ""}.</p></div>`;
  }
  async function connect() {
    const x = cur, name = x.name;
    disconnect();
    conn = { name, state: "connecting", note: "" }; showConn();
    try {
      await api(`${API}/${encodeURIComponent(name)}/console`);
      if (!RFB) { RFB = (await import("/static/vendor/novnc/core/rfb.js")).default; throttle(RFB); }
    } catch (e) {
      if (conn.name === name) { conn = { name, state: "manual", note: e.message }; showConn(); }
      return;
    }
    const scr = $("[data-part=screen]", root());
    if (!scr || conn.name !== name || cur?.name !== name || tab !== "console") return;
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${API}/${encodeURIComponent(name)}/console`, ["binary"]);
    ws.addEventListener("close", (e) => { if (e.code >= 4000 && conn.name === name) conn.note = e.reason; });
    const o = display(name);
    const r = new RFB(scr, ws, { shared: true });
    rfb = r;
    fit = o.scale !== "native";
    r.scaleViewport = o.scale === "fit"; r.clipViewport = false; r.resizeSession = o.scale === "remote"; r.focusOnClick = true; r.background = o.background || "#000";
    r.qualityLevel = o.quality; r.compressionLevel = o.compression; r.viewOnly = !!o.viewOnly; r.showDotCursor = !!o.dot;
    r._sock._acDelay = Math.max(0, Number(o.interval) || 0);  // read by the throttled fbUpdateRequest below
    if (o.clipboard) {
      r.addEventListener("clipboard", (e) => { navigator.clipboard?.writeText(e.detail.text).catch(() => {}); });
      scr.addEventListener("paste", (e) => { const t = e.clipboardData?.getData("text"); if (t && rfb === r) r.clipboardPasteFrom(t); });
    }
    if (o.bell) r.addEventListener("bell", () => { try { new AudioContext().resume(); } catch { /* no audio */ } toast(`${name}: bell`); });
    r.addEventListener("connect", () => { if (rfb === r) { conn.state = "on"; conn.note = ""; showConn(); r.focus({ preventScroll: true }); } });
    r.addEventListener("disconnect", (e) => {
      if (rfb !== r) return;
      rfb = null;
      conn = { name, state: "manual", note: conn.note || (e.detail.clean ? "Disconnected." : "The connection dropped: the machine may have restarted.") };
      showConn();
    });
    r.addEventListener("desktopname", (e) => { if (rfb === r) { conn.desktop = e.detail.name; showConn(); } });
  }
  // "Seconds between screen transmissions": noVNC asks for the next update as soon as one arrives; with an interval
  // set, the next request waits that long, so fewer frames cross the network (wrapped here, the vendored file is untouched).
  function throttle(R) {
    const orig = R.messages.fbUpdateRequest;
    R.messages.fbUpdateRequest = function (sock, incremental, ...rest) {
      const wait = sock?._acDelay || 0;
      if (!incremental || !wait) return orig.call(this, sock, incremental, ...rest);
      setTimeout(() => { try { if (sock._websocket?.readyState === 1) { orig.call(this, sock, incremental, ...rest); sock.flush?.(); } } catch { /* closed */ } }, wait);
    };
  }
  function disconnect() {
    const r = rfb; rfb = null;
    if (r) try { r.disconnect(); } catch { /* already closed */ }
    conn = { name: null, state: "idle", note: "" };
  }
  function showConn() {
    const s = $("[data-part=conn]", root() || document), b = $("[data-mc=conn]", root() || document);
    if (!s || !b) return;
    const on = conn.state === "on";
    s.innerHTML = on ? `${dot("running")} Connected${conn.desktop ? ` <small class="muted">${esc(conn.desktop)}</small>` : ""}`
      : conn.state === "connecting" ? `${dot("starting")} Connecting…` : `${dot("stopped")} ${esc(conn.note || "Not connected.")}`;
    b.innerHTML = on || conn.state === "connecting" ? `${ic("close")} Disconnect` : `${ic("play")} Connect`;
    b.classList.toggle("primary", !(on || conn.state === "connecting"));
    const o = display(conn.name || cur?.name || ""), info = $("[data-part=dispinfo]", root() || document);
    if (info) info.textContent = `quality ${o.quality} · compression ${o.compression}${o.interval ? ` · every ${(o.interval / 1000).toFixed(2)}s` : ""}${o.viewOnly ? " · view only" : ""}`;
    $$("[data-mc=cad], [data-mc=full]", root()).forEach((x) => (x.disabled = !on));
  }

  // ---------- overview and logs ----------
  function overview() {
    const x = cur, s = x.settings, c = x.container;
    const fact = (l, v) => `<div><span>${l}</span><b>${v}</b></div>`;
    const lang = d?.languages?.[s.language] || s.language;
    const facts = [
      fact("System", `${esc(x.title || s.os)} <small class="muted">${esc(FAMILY[x.family] || "")}</small>`),
      ...(s.url ? [fact("Image", `<code>${esc(s.url)}</code>`)] : []),
      fact("CPU", `${s.cores} cores <small class="muted">${fmtNum(x.cpu, 2)} in use</small>`),
      fact("Memory", `${s.ram} GB <small class="muted">${bytes(x.memory)} in use, with QEMU</small>`),
      fact("Disk", `${s.disk} GB <small class="muted">volume ${esc(x.volume.name)}</small>`),
      ...(x.family === "windows" ? [fact("Windows account", `${esc(s.username || "")} <small class="muted">${esc(lang || "")}</small>`)] : []),
      fact("Network", `Internet <small class="muted">user-mode networking through its container; only the console reaches its screen</small>`),
      fact("Runs on", `<code>${esc(x.image || "")}</code>`),
      fact("Started", since(x.started)),
      ...(x.change ? [fact("Last change", esc(x.change))] : []),
    ];
    return `${x.status === "starting" && x.progress ? `<div class="mc-now">${ic("clock")}<span><b>Setting up:</b> ${esc(x.progress)}</span></div>` : ""}
      <div class="mc-facts">${facts.join("")}</div>
      <h3>${ic("server")} Container</h3>
      ${c ? `<div class="table-wrap"><table class="mc-table"><thead><tr><th>Container</th><th>State</th><th class="num">Restarts</th><th>Address</th><th>Started</th></tr></thead><tbody>
        <tr><td><code>${esc(c.name)}</code> <small class="muted mono">${esc(c.id)}</small></td><td>${esc(c.state)}${c.state === "exited" && c.exit_code != null ? ` <small class="muted">(exit ${esc(c.exit_code)})</small>` : ""}${c.error ? `<small class="mc-problem">${esc(c.error)}</small>` : ""}</td>
          <td class="num">${c.restarts}</td><td class="mono">${esc(c.ip || "–")}</td><td>${since(c.started)}</td></tr>
        </tbody></table></div>` : `<div class="mc-empty">No container: it's stopped (Start makes one on the same disk).</div>`}
      <h3>${ic("bell")} Activity</h3>
      ${x.events.length ? `<ul class="mc-events">${x.events.slice(0, 12).map((e) => `<li class="${e.warn ? "warn" : ""}"><span class="muted">${since(e.at)}</span><span>${esc(e.text)}</span></li>`).join("")}</ul>`
        : `<p class="muted">Nothing since the server started.</p>`}`;
  }
  function logsTab() {
    return `<div class="mc-logbar"><select data-part="tail" aria-label="Lines">${[100, 300, 1000, 5000].map((n) => `<option ${n === logTail ? "selected" : ""}>${n}</option>`).join("")}</select>
        <button type="button" class="btn sm" data-mc="logs">${ic("reset")} Refresh</button>
        <span class="muted">The download, setup and QEMU's own messages, oldest first.</span></div>
      <pre class="mc-log" tabindex="0">Reading logs…</pre>`;
  }
  async function loadLogs() {
    const pre = $(".mc-log", root() || document); if (!pre) return;
    pre.dataset.loaded = 1;
    try { const r = await api(`${API}/${encodeURIComponent(sel)}/logs?tail=${logTail}`); pre.textContent = r.text || "No output yet."; pre.scrollTop = pre.scrollHeight; }
    catch (e) { pre.textContent = e.message; }
  }

  // ---------- dialogs ----------
  function dialog(html, onSubmit, o = {}) {
    modal(`<form id="mc-form" class="mc-form">${html}<div class="row mc-form-btns"><button type="submit" class="btn ${o.danger ? "danger" : "primary"}">${esc(o.submit || "Save")}</button><button type="button" class="btn" data-cancel>Cancel</button></div></form>`);
    const f = $("#mc-form");
    f.querySelector("[data-cancel]").onclick = () => ($("#modal").hidden = true);
    f.onsubmit = async (e) => {
      e.preventDefault();
      const b = f.querySelector("[type=submit]"); b.disabled = true;
      try { await onSubmit(f); } catch (x) { err(x); } finally { b.disabled = false; }
    };
    return f;
  }
  async function act(a) {
    const x = cur;
    const go = async () => {
      if (a !== "start") disconnect();
      await api(`${API}/${encodeURIComponent(x.name)}/action`, { method: "POST", body: { action: a } });
      toast(`${{ stop: "Shutting down", start: "Starting", restart: "Restarting" }[a]} ${x.name}`); await load(); schedule();
    };
    if (a === "start") return go().catch(err);
    dialog(`<h2>${ic(a === "stop" ? "stop" : "reset")} ${a === "stop" ? "Stop" : "Restart"} ${esc(x.name)}?</h2>
      <p class="muted">${a === "stop" ? "The system is asked to shut down (up to about two minutes), then the machine stops. Its disk stays; start it again any time."
        : "The system is asked to shut down, then the machine starts again. Unsaved work in it is lost."}</p>`,
      async () => { $("#modal").hidden = true; await go(); }, { submit: a === "stop" ? "Stop" : "Restart", danger: a === "stop" });
  }
  function remove() {
    const x = cur;
    dialog(`<h2>${ic("trash")} Delete ${esc(x.name)}?</h2><p class="muted">The machine and its ${x.settings.disk} GB disk are deleted, with everything installed or saved on it. This can't be undone.</p>
      <label class="row"><input type="checkbox" name="sure"> Delete ${esc(x.name)} and its disk</label>`,
      async (f) => {
        if (!f.elements.sure.checked) throw new Error("Tick the box to confirm.");
        disconnect();
        await api(`${API}/${encodeURIComponent(x.name)}`, { method: "DELETE" });
        $("#modal").hidden = true; toast(`Deleting ${x.name}`); await load(); schedule();
      }, { submit: "Delete", danger: true });
  }
  function fullScreen() {
    const s = $("[data-part=screen]", root());
    if (s?.requestFullscreen) s.requestFullscreen().then(() => rfb?.focus()).catch(() => toast("This browser won't show it full screen."));
  }

  // ---------- the form ----------
  function form(x) {
    if (!d) return;
    const OS = d.os, L = d.limits, s = x?.settings || {};
    const v = { os: "ubuntu", cores: 2, ram: OS.ubuntu.ram, disk: OS.ubuntu.disk, username: "admin", language: "en", url: "", ...s };
    const opts = (fam) => Object.entries(OS).filter(([, o]) => o.family === fam).map(([k, o]) => `<option value="${k}" ${k === v.os ? "selected" : ""}>${esc(o.title)}</option>`).join("");
    const f = dialog(`<h2>${ic("monitor")} ${x ? `Edit ${esc(x.name)}` : "New machine"}</h2>
      <label>Name</label><input name="name" value="${esc(x?.name || "")}" ${x ? "readonly" : "required"} pattern="[a-z]([a-z0-9\\-]{0,28}[a-z0-9])?" placeholder="dev-box" autocapitalize="none" spellcheck="false" maxlength="30">
        <small class="muted">Lowercase letters, digits and dashes. It's also the machine's host name.</small>
      <label>Operating system</label><select name="os" ${x ? "disabled" : ""}><optgroup label="Linux">${opts("linux")}</optgroup><optgroup label="Windows">${opts("windows")}</optgroup></select>
        <small class="muted" data-part="os-note"></small>
      <div data-part="url"><label>Image address</label><input name="url" value="${esc(v.url)}" ${x ? "readonly" : ""} placeholder="https://example.org/distro.iso" autocapitalize="none" spellcheck="false" maxlength="500">
        <small class="muted">An ISO, or a disk image (qcow2, img, raw, vhd, vhdx, vmdk; also zipped). It's downloaded at the first start.</small></div>
      <div data-part="win">
        <div class="mc-two"><div><label>Windows account</label><input name="username" value="${esc(v.username)}" ${x ? "readonly" : ""} maxlength="20" autocapitalize="none" spellcheck="false" autocomplete="off"></div>
          ${x ? `<div><label>Language</label><input value="${esc(d.languages[v.language] || v.language)}" readonly></div>`
              : `<div><label>Password</label><input name="password" type="password" minlength="4" maxlength="127" autocomplete="new-password"></div>`}</div>
        ${x ? `<small class="muted">Set when Windows was installed. Change them in Windows.</small>` : `<label>Language</label><select name="language">${Object.entries(d.languages).map(([k, l]) => `<option value="${k}" ${k === v.language ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>`}
      </div>
      <div class="mc-three"><div><label>CPU cores</label><input name="cores" type="number" min="1" max="${L.cores}" value="${v.cores}"></div>
        <div><label>Memory (GB)</label><input name="ram" type="number" min="1" max="${L.ram}" value="${v.ram}"></div>
        <div><label>Disk (GB)</label><input name="disk" type="number" min="8" max="${L.disk}" value="${v.disk}" ${x ? "readonly" : ""}></div></div>
      <p class="muted">${x ? "Saving restarts the machine (its system is asked to shut down first). The disk's size is set when it's made." : "The disk's size can't change later. The machine reaches the internet through its container's network."}</p>
      ${x ? screenFields(x.name) : ""}`,
      async (f) => {
        const el = f.elements, os = x ? v.os : el.os.value, win = OS[os].family === "windows";
        if (x && f.dataset.screenOnly === "1") { saveScreen(x.name, f); $("#modal").hidden = true; toast("Screen settings saved: they apply at the next Connect"); return; }
        if (x) saveScreen(x.name, f);
        const body = { name: el.name.value.trim(), os, url: el.url.value.trim(), cores: +el.cores.value, ram: +el.ram.value, disk: +el.disk.value,
          username: el.username.value.trim(), password: x || !win ? null : el.password.value, language: x ? v.language : el.language.value };
        if (!body.name) throw new Error("Name the machine.");
        if (!x && win && body.password.length < 4) throw new Error("Give the Windows account a password (at least 4 characters).");
        const r = await api(x ? `${API}/${encodeURIComponent(x.name)}` : API, { method: x ? "PUT" : "POST", body });
        $("#modal").hidden = true; toast(x ? `Saved ${r.name}` : `Made ${r.name}: setting it up`);
        if (x) disconnect();
        sel = r.name; store.set("ac.machines.sel", sel); tab = x ? tab : "console"; await load(); schedule();
      }, { submit: x ? "Save" : "Make machine" });
    const el = f.elements;
    if (x) wireScreen(f);
    const sync = (fresh) => {
      const os = x ? v.os : el.os.value, o = OS[os], win = o.family === "windows";
      $("[data-part=url]", f).hidden = os !== "custom";
      $("[data-part=win]", f).hidden = !win;
      el.ram.min = o.min_ram;
      if (fresh) { el.ram.value = o.ram; el.disk.value = o.disk; el.disk.min = win ? 20 : 8; }
      $("[data-part=os-note]", f).textContent = win
        ? "Downloaded from Microsoft and installed unattended. Windows 10 and 11 run unactivated, Server as a 180-day evaluation: activate them in Windows with your own licence."
        : os === "custom" ? "Any distribution: boot its installer or a ready-made disk image." : o.note || "Boots the installer (or live system). Install it from the Console as on a new PC.";
    };
    if (!x) el.os.onchange = () => sync(true);
    sync(false);
    setTimeout(() => (x ? el.cores : el.name).focus(), 30);
  }

  // ---------- screen settings (transfer speed and more) ----------
  function screenFields(name) {
    const o = display(name), sel = (k, opts) => opts.map(([v, l]) => `<option value="${v}" ${String(o[k]) === String(v) ? "selected" : ""}>${l}</option>`).join("");
    return `<fieldset class="mc-screenset"><legend>${ic("monitor")} Screen (this browser; applies at the next Connect, no restart)</legend>
      <div class="row">${Object.keys(PRESETS).map((k) => `<button type="button" class="btn sm" data-preset="${k}">${k[0].toUpperCase() + k.slice(1)}</button>`).join("")}<span class="muted">presets</span></div>
      <div class="mc-three"><div><label>Image quality <b data-out="quality">${o.quality}</b>/9</label><input type="range" name="s_quality" min="0" max="9" value="${o.quality}"><small class="muted">Lower = smaller JPEG frames, faster.</small></div>
        <div><label>Compression <b data-out="compression">${o.compression}</b>/9</label><input type="range" name="s_compression" min="0" max="9" value="${o.compression}"><small class="muted">Higher = less data, more CPU.</small></div>
        <div><label>Send the screen every</label><select name="s_interval">${sel("interval", [[0, "as fast as possible"], [50, "0.05 s (20/s)"], [100, "0.1 s (10/s)"], [250, "0.25 s (4/s)"], [500, "0.5 s"], [1000, "1 s"], [2000, "2 s"], [5000, "5 s"]])}</select><small class="muted">Longer = fewer frames on the network.</small></div></div>
      <div class="mc-three"><div><label>Scaling</label><select name="s_scale">${sel("scale", [["fit", "Fit to the page"], ["native", "Own size (scroll)"], ["remote", "Resize the machine's screen"]])}</select></div>
        <div><label>Background</label><input type="color" name="s_background" value="${esc(o.background)}"></div>
        <div class="mc-checks"><label class="row"><input type="checkbox" name="s_viewOnly" ${o.viewOnly ? "checked" : ""}> View only (no keyboard or mouse)</label>
          <label class="row"><input type="checkbox" name="s_dot" ${o.dot ? "checked" : ""}> Show a dot when the cursor is hidden</label>
          <label class="row"><input type="checkbox" name="s_clipboard" ${o.clipboard ? "checked" : ""}> Share the clipboard</label>
          <label class="row"><input type="checkbox" name="s_bell" ${o.bell ? "checked" : ""}> Notify on the machine's bell</label></div></div></fieldset>`;
  }
  function wireScreen(f) {
    f.querySelectorAll("input[type=range]").forEach((r) => (r.oninput = () => { const out = f.querySelector(`[data-out="${r.name.slice(2)}"]`); if (out) out.textContent = r.value; }));
    f.querySelectorAll("[data-preset]").forEach((b) => (b.onclick = () => { const p = PRESETS[b.dataset.preset]; f.elements.s_quality.value = p.quality; f.elements.s_compression.value = p.compression; f.elements.s_interval.value = p.interval; f.querySelectorAll("input[type=range]").forEach((r) => r.oninput()); }));
  }
  function saveScreen(name, f) {
    const e = f.elements;
    if (!e.s_quality) return;
    store.set(`ac.machines.display.${name}`, JSON.stringify({ quality: +e.s_quality.value, compression: +e.s_compression.value, interval: +e.s_interval.value, scale: e.s_scale.value,
      background: e.s_background.value, viewOnly: e.s_viewOnly.checked, dot: e.s_dot.checked, clipboard: e.s_clipboard.checked, bell: e.s_bell.checked }));
    if (rfb && conn.name === name) {  // what can change live does
      rfb.qualityLevel = +e.s_quality.value; rfb.compressionLevel = +e.s_compression.value; rfb.viewOnly = e.s_viewOnly.checked; rfb.showDotCursor = e.s_dot.checked;
      rfb._sock._acDelay = +e.s_interval.value; showConn();
    }
  }
  function screenDialog(x) {
    const f = dialog(`<h2>${ic("monitor")} Screen of ${esc(x.name)}</h2>${screenFields(x.name)}`, async (form) => { saveScreen(x.name, form); $("#modal").hidden = true; toast("Saved"); }, { submit: "Save" });
    wireScreen(f);
  }

  function wire() {
    const v = root();
    if (!v || v.dataset.wired) return;
    v.dataset.wired = "1";
    v.addEventListener("click", (e) => {
      const b = e.target.closest("[data-mc], [data-sel], [data-tab]"); if (!b) return;
      if (b.dataset.sel) return b.dataset.sel !== sel && select(b.dataset.sel);
      if (b.dataset.tab) { tab = b.dataset.tab; return renderMain(); }
      const a = b.dataset.mc;
      if (a === "new") form(null);
      else if (a === "edit") form(cur);
      else if (a === "screen") screenDialog(cur);
      else if (a === "delete") remove();
      else if (["restart", "stop", "start"].includes(a)) act(a);
      else if (a === "logs") loadLogs();
      else if (a === "cad") { rfb?.sendCtrlAltDel(); rfb?.focus({ preventScroll: true }); }
      else if (a === "full") fullScreen();
      else if (a === "fit") {
        fit = !fit; if (rfb) rfb.scaleViewport = fit;
        $("[data-part=screen]", root())?.classList.toggle("mc-native", !fit);
        b.setAttribute("aria-pressed", fit); b.innerHTML = `${ic(fit ? "shrink" : "expand")} ${fit ? "Fit" : "100%"}`;
      }
      else if (a === "conn") {
        if (conn.state === "on" || conn.state === "connecting") { const n = cur?.name; disconnect(); conn = { name: n, state: "manual", note: "Disconnected." }; showConn(); }
        else connect();
      }
    });
    v.addEventListener("change", (e) => { if (e.target.dataset.part === "tail") { logTail = +e.target.value; loadLogs(); } });
  }
  window.addEventListener("pagehide", disconnect);

  // The dashboard re-renders the current view on live events: this page keeps its own polling and
  // its console, so a quiet refresh leaves it alone.
  async function view(quiet) {
    if (quiet && root()?.isConnected) return;
    $("#view").innerHTML = `<div id="machines" class="mc-page"></div>`;
    await open();
  }
  return { view, close };
})();
registerView({ id: "machines", title: "Machines", icon: "machines", admin: true, leave: Machines.close }, Machines.view,
  VIEWS.findIndex((v) => v.id === "system") + 1 || VIEWS.length);
