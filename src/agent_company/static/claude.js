"use strict";
// Workspace Claude Code panel: conversations with Claude Code (thinking, tool calls with their
// results, answers, run summary) above a prompt box. Each prompt runs Claude Code in the
// conversation's folder (the explorer's, when it starts) with every tool allowed; follow-ups
// resume the same session. Images can be pasted, dropped or attached; every answer has a copy button.
// Tabs: one per conversation of this panel, each with its own draft and a short title Claude
// writes from the first prompt (+ opens another on the far right and keeps the others). Other
// Claude Code sessions on the host (VS Code, terminal, SDK) get no tab of their own: they stay in
// the sessions window, and one opened from there has a tab while it is selected; an ended one
// can be continued in place. In the Workspace only the conversations of this browser session have tabs (the one
// selected last, those working and those opened since the tab was opened); earlier ones stay in the sessions window
// (History: Open, Continue). A conversation still working after a server restart is followed
// live in its own tab, never shown twice. The drawer answers /status, /tasks and /agents (typed
// in the prompt box or opened with the header button) for the selected tab: model, context,
// background commands, todos, subagents and agent types; with a Models-page model that has its own sign-in chosen,
// its sign-in's account, usage, sessions and agents. A conversation opens at its end (its latest prompt and answer).
// The sessions window (clock button, /history or /resume) lists the running sessions (open,
// stop) and every earlier one with a transcript (open, continue in a tab, delete).
window.WSP = window.WSP || {};
(() => {
  const W = window.WSP;
  // A panel instance: the Workspace has one (W.initClaude); the Projects page makes one per project, talking to
  // the project's General Manager (opt.projectId) in the project's folder (opt.root), with its own tabs.
  function makePanel(opt = {}) {
  const K = opt.key || "ac.wsp.claude";
  const ROOT = opt.root || (() => W.root());
  const C = { el: null, convs: [], tab: "", drafts: {}, open: new Set(), history: [], hpos: -1, model: store.get(K + ".model") || (opt.model ? `m:${opt.model}` : "default"), effort: store.get(K + ".effort") || "default",
    sessions: [], names: {}, view: { sid: "", events: [], next: 0, d: null }, info: "", cli: null, dtimer: 0, following: "",
    closed: new Set(), hist: { open: false, tab: "running", q: "", items: [], total: 0, at: 0, timer: 0 },
    logs: new Map(), provider: opt.provider || store.get(K + ".provider") || "claude", models: {}, usage: { d: null, at: 0, busy: false, err: "" }, act: { d: null, at: 0, busy: false, err: "" } };
  try { C.closed = new Set(JSON.parse(store.get(K + ".closed") || "[]")); } catch { /* ignore */ }
  const ENTRY = { "claude-vscode": "VS Code", cli: "Terminal", "sdk-cli": "SDK", "sdk-ts": "SDK", "sdk-py": "SDK", "claude-desktop": "Desktop", "claude-jetbrains": "JetBrains" };
  const PLACEHOLDER = opt.projectId ? "Tell the General Manager what you want…  Enter sends · Shift+Enter new line · paste or drop images"
    : "Ask Claude Code to read, change, run or explain anything in this folder…  Enter sends · Shift+Enter new line · paste or drop images · /status /tasks /agents /history";
  const newConv = (o = {}) => ({ id: `c${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`, session: "", cwd: "", title: "", runs: [], provider: C.provider, ...o });
  const PROV = { claude: { label: "Claude Code", logo: "✳" }, codex: { label: "Codex", logo: "◎" }, copilot: { label: "Copilot", logo: "⬡" } };
  const provOf = (c) => (c?.provider && PROV[c.provider] ? c.provider : "claude");
  // The levels as Claude Code names them; the menu's tooltip says what they are.
  const EFFORT = { default: "Default effort", low: "low", medium: "medium", high: "high", xhigh: "xhigh", max: "max" };
  // The chosen effort if this conversation's agent has it, else none (its CLI's own setting).
  const effortOf = (c) => (C.effort !== "default" && (C.models[provOf(c)]?.efforts || []).includes(C.effort) ? C.effort : "");
  const saveConvs = () => store.set(K + ".convs", JSON.stringify(C.convs.map((c) => ({ id: c.id, session: c.session, cwd: c.cwd, title: c.title, provider: provOf(c),
    runs: c.runs.filter((r) => !r.divider && !r.transcript).map((r) => r.id).slice(-30) }))));
  const saveClosed = () => store.set(K + ".closed", JSON.stringify([...C.closed]));
  const conv = (id) => C.convs.find((c) => c.id === id);
  const cur = () => conv(C.tab);
  const convOfSid = (sid) => (sid ? C.convs.find((c) => c.session === sid) : undefined);
  const running = (c) => c?.runs.find((r) => r.status === "running");
  const live = (sid) => (sid ? C.sessions.find((s) => s.session_id === sid) : undefined);
  // A conversation working in a process the panel does not drive (a prompt that outlived a server
  // restart): its tab follows the transcript live and can stop it.
  const following = (c) => !!(c && c.session && !c.runs.some((r) => !r.transcript && !r.divider) && live(c.session)?.status === "busy");
  const busy = (c) => !!(running(c) || following(c));
  const draft = (id = C.tab) => (C.drafts[id] ||= { text: "", uploads: [] });
  const convTitle = (c) => c.title || firstLine(c.runs.find((r) => r.prompt)?.prompt, 40) || "New conversation";
  const cwdOf = (c = cur()) => (c ? c.cwd || ROOT() : C.view.d?.cwd || C.names[C.tab]?.cwd || "");
  // The conversations this browser tab worked with (sessionStorage: a new browser tab or window starts empty).
  const SKEY = K + ".shown";
  const touched = (() => { try { return new Set(JSON.parse(sessionStorage.getItem(SKEY) || "[]")); } catch { return new Set(); } })();
  const touch = (id) => { if (!opt.workspace || touched.has(id)) return; touched.add(id); try { sessionStorage.setItem(SKEY, JSON.stringify([...touched])); } catch { /* private mode */ } };
  const shownConv = (c) => !opt.workspace || c.id === C.tab || busy(c) || touched.has(c.id);
  // Where the selection goes when its tab closes: another shown conversation, else an empty one.
  function nextShown() {
    const c = C.convs.find((x) => x.id !== C.tab && shownConv(x)) || C.convs.find((x) => x.id !== C.tab && !x.session && !x.runs.length);
    if (c) return c.id;
    const n = newConv();
    C.convs.push(n); saveConvs();
    return n.id;
  }

  const init = async (el) => {
    C.el = el;
    el.innerHTML = `<div class="wc-head"><span class="wc-agent"><span class="wc-logo" aria-hidden="true">✳</span><select id="wc-prov" class="wc-prov" aria-label="Agent" title="Which agent this conversation talks to"></select><span class="wc-dot" title="idle"></span></span><span class="grow"></span>
        <span class="wc-mp"><button type="button" class="wc-mbtn" id="wc-mbtn" aria-haspopup="menu" aria-expanded="false" title="Model and effort for the next prompt"><span class="wc-mname"></span><span class="wc-meff"></span>${W.svg("M6 9l6 6 6-6")}</button>
          <div class="wc-menu" id="wc-menu" role="menu" aria-label="Model and effort" hidden></div></span>
        <button class="icon-btn sm" id="wc-compact" title="Compact this conversation: Claude Code summarises it to cut token use (/compact)">${W.svg("M12 3v6M9 6l3 3 3-3M12 21v-6M9 18l3-3 3 3M4 12h16")}</button>
        <button class="icon-btn sm" id="wc-hist-btn" title="Sessions: running now and history (/history)" aria-expanded="false">${W.svg("M12 7v5l3 2M3.5 12a8.5 8.5 0 1 0 2.5-6M3 4v4h4")}</button>
        <button class="icon-btn sm" id="wc-info-btn" title="Status, tasks and agents (/status, /tasks, /agents)" aria-expanded="false">${W.svg("M3 12h4l3-8 4 16 3-8h4")}</button>
        <button class="icon-btn sm" id="wc-new" title="New conversation in a new tab (the others stay open)">${W.svg("M12 5v14M5 12h14")}</button></div>
      <div class="wc-tabs" id="wc-tabs" role="tablist" aria-label="Claude Code conversations and sessions"></div>
      <div class="wc-info" id="wc-info" hidden>
        <div class="wc-info-tabs" role="tablist">${["status", "tasks", "agents"].map((s) => `<button type="button" data-info="${s}" role="tab">/${s}</button>`).join("")}<span class="grow"></span>
          <button type="button" class="icon-btn sm" id="wc-info-close" title="Close">×</button></div>
        <div class="wc-info-body" id="wc-info-body"></div>
      </div>
      <div class="wc-hist" id="wc-hist" role="dialog" aria-label="Claude Code sessions" hidden>
        <div class="wc-hist-head"><div class="wc-hist-tabs" role="tablist">${["running", "history"].map((t) => `<button type="button" role="tab" data-hist="${t}">${t === "running" ? "Running" : "History"} <span class="wc-count" data-count="${t}"></span></button>`).join("")}</div>
          <input type="search" id="wc-hist-q" placeholder="Search title, folder, prompt, id…" aria-label="Search sessions">
          <button type="button" class="icon-btn sm" id="wc-hist-reload" title="Refresh">${W.svg("M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7")}</button>
          <button type="button" class="icon-btn sm" id="wc-hist-close" title="Close (Esc)">×</button></div>
        <div class="wc-hist-body" id="wc-hist-body"></div>
      </div>
      <div class="wc-logs" id="wc-logs"></div>
      <div class="wc-ro muted" id="wc-ro" hidden></div>
      <form class="wc-composer" id="wc-form">
        <div class="wc-att" id="wc-att"></div>
        <div class="wc-inrow"><textarea id="wc-input" rows="3" placeholder="${PLACEHOLDER}"></textarea>
          <div class="wc-side"><label class="icon-btn sm wc-attach" for="wc-file" title="Attach images or files" aria-label="Attach images or files">${W.svg("M21 12.5l-8.5 8.5a6 6 0 0 1-8.5-8.5L13 3.5a4 4 0 0 1 5.7 5.7L9.6 18.3a2 2 0 0 1-2.8-2.8L15 7.3")}</label>
            <button type="submit" class="wc-send" id="wc-send" title="Send (Enter)" aria-label="Send"></button></div></div>
          <input type="file" id="wc-file" multiple hidden accept="image/*,text/*,.json,.yaml,.yml,.md,.pdf">
      </form>`;
    const ta = el.querySelector("#wc-input");
    el.querySelector("#wc-form").onsubmit = (e) => { e.preventDefault(); busy(cur()) ? stop() : send(); };
    ta.onkeydown = (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); if (!busy(cur())) send(); }
      else if (e.key === "Escape" && busy(cur())) { e.preventDefault(); stop(); }
      else if (e.key === "ArrowUp" && !ta.value && C.history.length) { e.preventDefault(); C.hpos = C.history.length - 1; ta.value = C.history[C.hpos]; }
      else if (e.key === "ArrowUp" && C.hpos > 0 && ta.value === C.history[C.hpos]) { e.preventDefault(); ta.value = C.history[--C.hpos]; }
      else if (e.key === "ArrowDown" && C.hpos >= 0 && ta.value === C.history[C.hpos]) { e.preventDefault(); C.hpos++; ta.value = C.history[C.hpos] || ""; if (C.hpos >= C.history.length) C.hpos = -1; }
    };
    ta.oninput = () => { draft().text = ta.value; ta.style.height = "auto"; ta.style.height = `${Math.min(260, Math.max(64, ta.scrollHeight))}px`; };
    ta.onpaste = (e) => { const files = [...(e.clipboardData?.files || [])]; if (files.length) { e.preventDefault(); files.forEach(upload); } };
    el.querySelector("#wc-file").onchange = (e) => { [...e.target.files].forEach(upload); e.target.value = ""; };
    el.addEventListener("dragover", (e) => { e.preventDefault(); el.classList.add("drop"); });
    el.addEventListener("dragleave", (e) => { if (!el.contains(e.relatedTarget)) el.classList.remove("drop"); });
    el.addEventListener("drop", (e) => {
      e.preventDefault(); el.classList.remove("drop");
      const files = [...e.dataTransfer.files];
      if (files.length && !e.dataTransfer.types.includes("text/x-paths")) { files.forEach(upload); return; }
      const text = e.dataTransfer.getData("text/plain");
      if (text) mention(text.split("\n"));
    });
    el.querySelector("#wc-new").onclick = () => { selectTab(blankConv().id); ta.focus(); };
    const mbtn = el.querySelector("#wc-mbtn"), menu = el.querySelector("#wc-menu");
    mbtn.onclick = () => openMenu(menu.hidden);
    menu.addEventListener("click", (e) => {
      const b = e.target.closest("[data-model],[data-effort]");
      if (!b || b.disabled) return;
      if (b.dataset.model) { C.model = b.dataset.model; store.set(K + ".model", C.model); openMenu(false); mbtn.focus(); }
      else { C.effort = b.dataset.effort; store.set(K + ".effort", C.effort); }
      renderProvider();
    });
    menu.addEventListener("keydown", (e) => {
      const items = [...menu.querySelectorAll("button:not(:disabled)")], i = items.indexOf(document.activeElement);
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); openMenu(false); mbtn.focus(); }
      else if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); items[(i + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length]?.focus(); }
    });
    document.addEventListener("pointerdown", (e) => { if (!menu.hidden && !e.target.closest(".wc-mp")) openMenu(false); });
    el.querySelector("#wc-prov").onchange = (e) => setProvider(e.target.value);
    el.querySelector("#wc-compact").onclick = () => compact();
    // Copy buttons, and keep track of which blocks the user expanded.
    el.addEventListener("click", (e) => {
      const b = e.target.closest("[data-copy]");
      if (!b) return;
      const [rid, n] = b.dataset.copy.split(":");
      const run = rid === "session" ? sessionRun() : C.convs.flatMap((c) => c.runs).find((r) => r.id === rid);
      if (!run) return;
      if (n === "prompt") return W.copyText(run.prompt);
      if (n === "all") return W.copyText(run.events.filter((x) => x.kind === "text").map((x) => x.text).join("\n\n") || resultOf(run)?.text || "");
      const ev = run.events.find((x) => x.n === Number(n));
      W.copyText(ev?.kind === "tool_use" ? JSON.stringify(ev.input, null, 2) : ev?.text || "");
    });
    el.addEventListener("click", (e) => {
      const pin = !e.target.closest("button, a") && e.target.closest("[data-pin]");
      if (pin && !getSelection()?.toString()) pin.classList.toggle("open");
    });
    el.addEventListener("toggle", (e) => { const k = e.target.dataset?.k; if (k) e.target.open ? C.open.add(k) : C.open.delete(k); }, true);
    if (opt.workspace) W.onRoot = () => { renderCwd(); renderTabs(); };
    el.querySelector("#wc-hist-btn").onclick = () => openHist(!C.hist.open);
    el.querySelector("#wc-hist-close").onclick = () => openHist(false);
    el.querySelector("#wc-hist-reload").onclick = () => loadHist();
    el.querySelectorAll("[data-hist]").forEach((b) => (b.onclick = () => { C.hist.tab = b.dataset.hist; renderHist(); }));
    const hq = el.querySelector("#wc-hist-q");
    let hqTimer = 0;
    hq.oninput = () => { C.hist.q = hq.value; renderHist(); clearTimeout(hqTimer); hqTimer = setTimeout(loadHist, 300); };
    el.querySelector("#wc-hist").addEventListener("keydown", (e) => { if (e.key === "Escape") { e.stopPropagation(); openHist(false); } });
    // A button does its action; a click elsewhere on a row selects it: an ended conversation shows in a tab with the
    // prompt box ready to continue it, a running session shows live.
    el.querySelector("#wc-hist-body").addEventListener("click", (e) => {
      const b = e.target.closest("[data-hact]");
      if (b) return histAction(b.dataset.hact, b.dataset.sid);
      const r = e.target.closest("[data-hrow]");
      if (!r?.dataset.hrow || getSelection()?.toString()) return;
      histAction(r.dataset.hstatus === "ended" ? "resume" : "open", r.dataset.hrow);
    });
    el.querySelector("#wc-info-btn").onclick = () => openInfo(C.info ? "" : "status");
    el.querySelector("#wc-info-close").onclick = () => openInfo("");
    el.querySelectorAll("[data-info]").forEach((b) => (b.onclick = () => openInfo(b.dataset.info)));
    el.querySelector("#wc-info-body").addEventListener("click", (e) => {
      const sid = e.target.closest("[data-open]")?.dataset.open, c = convOfSid(sid);
      if (c) selectTab(c.id); else if (others().some((s) => s.session_id === sid)) selectTab(sid);
    });
    const tabs = el.querySelector("#wc-tabs");
    tabs.addEventListener("click", (e) => {
      const x = e.target.closest("[data-close]");
      if (x) return closeTab(x.dataset.close);
      const t = e.target.closest("[data-tab]"); if (t) selectTab(t.dataset.tab);
    });
    tabs.addEventListener("auxclick", (e) => { const t = e.button === 1 && e.target.closest("[data-tab]"); if (t) { e.preventDefault(); closeTab(t.dataset.tab); } });
    tabs.addEventListener("keydown", (e) => {
      const t = e.target.closest("[data-tab]");
      if (!t || e.target.closest("[data-close]")) return;
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); selectTab(t.dataset.tab); }
      else if (e.key === "Delete") { e.preventDefault(); closeTab(t.dataset.tab); }
    });

    // The conversations kept in this browser; before tabs there was one (session + run ids).
    let saved = null;
    try { saved = JSON.parse(store.get(K + ".convs") || "null"); } catch { /* ignore */ }
    if (!Array.isArray(saved)) {
      let ids = [];
      try { ids = JSON.parse(store.get(K + ".runs") || "[]"); } catch { /* ignore */ }
      saved = [{ session: store.get(K + ".session") || "", cwd: store.get(K + ".cwd") || "", runs: ids }];
    }
    const ids = new Map();
    for (const x of saved) {
      if (!x || typeof x !== "object") continue;
      const c = newConv({ session: String(x.session || ""), cwd: String(x.cwd || ""), title: String(x.title || ""), provider: PROV[x.provider] ? x.provider : "claude" });
      if (x.id && !conv(String(x.id))) c.id = String(x.id);
      C.convs.push(c); ids.set(c, Array.isArray(x.runs) ? x.runs : []);
    }
    if (!C.convs.length) C.convs.push(newConv());
    const last = store.get(K + ".tab") || "";
    C.tab = conv(last) ? last : C.convs[0].id;
    touch(C.tab);
    saveConvs();
    renderTabs(); renderCwd(); renderBusy(); renderLog();
    pollSessions().then(showTab);

    try {
      const info = await api("/api/workspace/claude");
      C.cli = info;
      (info.providers || []).forEach((p) => (C.models[p.id] = p));
      renderProvider();
    } catch (e) { toast(e.message); }
    await Promise.all(C.convs.map((c) => loadConv(c, ids.get(c) || [])));
    // Conversations without a tab are kept for a while (one may start working again), the oldest are forgotten:
    // their transcripts stay in the sessions window.
    const hidden = C.convs.filter((c) => !shownConv(c));
    // New tabs are added on the right: the oldest hidden ones come first.
    if (hidden.length > 30) { const drop = new Set(hidden.slice(0, hidden.length - 30)); C.convs = C.convs.filter((c) => !drop.has(c)); drop.forEach((c) => dropLog(c.id)); saveConvs(); }
    C.history = C.history.map((h, i) => (typeof h === "string" ? { prompt: h, at: Infinity, i } : { ...h, i }))
      .sort((a, b) => a.at - b.at || a.i - b.i).map((h) => h.prompt);
  };

  async function loadConv(c, ids) {
    const runs = await Promise.all(ids.map((id) => api(`/api/workspace/claude/${encodeURIComponent(id)}`).catch(() => null)));
    c.runs = runs.filter(Boolean);
    C.history.push(...c.runs.filter((r) => r.prompt).map((r) => ({ prompt: r.prompt, at: r.started })));
    if (!c.title) c.title = c.runs.find((r) => r.title)?.title || "";
    const r = running(c);
    if (!c.runs.length && c.session) await showTranscript(c);  // the server restarted, or a resumed conversation
    else renderLog(r ? true : undefined, c);
    if (r) poll(r);
    else if (!c.title && c.runs.length) wantTitle(c, c.runs[0]);
    saveConvs(); renderTabs(); renderBusy();
  }
  // The current conversation when it is still empty, else a new tab on the far right.
  function blankConv() {
    const c = cur();
    if (c && !c.session && !c.runs.length) return c;
    const n = newConv();
    C.convs.push(n); saveConvs();
    return n;
  }

  // --- which agent a conversation talks to (Claude Code, Codex, Copilot) and its models -------------------
  function renderProvider() {
    if (!C.el) return;
    const c = cur(), p = c ? provOf(c) : "claude", info = C.models[p];
    const ps = C.el.querySelector("#wc-prov");
    const locked = !c || !!c.session || busy(c) || !!opt.provider;
    // Locked, it lists only its agent: a select is as wide as its longest option.
    ps.innerHTML = Object.entries(PROV).filter(([k]) => !locked || k === p).map(([k, v]) => { const m = C.models[k]; return `<option value="${k}" ${m && !m.available ? "disabled" : ""}>${v.label}${m && !m.available ? " (not installed)" : ""}</option>`; }).join("");
    ps.value = p; ps.disabled = locked; fitSelect(ps);
    ps.title = c?.session ? "This conversation stays with its agent: start a new conversation for another" : "Which agent this conversation talks to";
    C.el.querySelector(".wc-head .wc-logo").textContent = PROV[p].logo;
    C.el.querySelector(".wc-head .wc-logo").dataset.p = p;
    const m = modelOf(c), eff = effortOf(c);
    const btn = C.el.querySelector("#wc-mbtn");
    btn.querySelector(".wc-mname").textContent = m.label === PROV[p].label ? prettyModel(m.note) || m.label : m.label;
    btn.querySelector(".wc-meff").textContent = eff;
    btn.querySelector(".wc-meff").hidden = !eff;
    btn.title = `Model: ${m.label}${m.note ? ` (${m.note})` : ""} · effort: ${EFFORT[eff || "default"]}. Applies to the next prompt`;
    btn.disabled = !info;
    if (!C.el.querySelector("#wc-menu").hidden) renderMenu();
    renderInfo();  // /status, /tasks and /agents follow the chosen agent and model
  }
  // Models: the CLI's own aliases, then the Models page's models of this agent (`m:<name>`: the server runs them with
  // their model id, their own sign-in or address). A conversation can change model between prompts.
  const ALIAS = { default: "Default", opus: "Opus", sonnet: "Sonnet", haiku: "Haiku" };
  function modelsOf(p) {
    const info = C.models[p];
    return [...(info?.models || ["default"]).map((v) => ({ v, label: ALIAS[v] || v, note: v === "default" ? `${PROV[p].label}'s own setting` : "", ok: true })),
      ...(info?.catalog || []).map((x) => ({ v: `m:${x.name}`, label: x.name, note: x.model_id, ok: x.available, why: x.reason, router: true, account: x.account || "",
        tag: [opt.model === x.name ? "the GM's model" : "", x.account ? `sign-in: ${x.account}` : x.remote ? "other address" : ""].filter(Boolean).join(" · ") }))];
  }
  // A select as wide as its chosen option, not its longest one, so the agent's name, chevron and dot stay together.
  let measure = null;
  function fitSelect(s) {
    const cs = getComputedStyle(s);
    measure ||= document.createElement("canvas").getContext("2d");
    measure.font = cs.fontSize ? `${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}` : "700 13px sans-serif";
    s.style.width = `${Math.ceil(measure.measureText(s.options[s.selectedIndex]?.text || "").width) + (s.disabled ? 2 : 28)}px`;
  }
  // claude-opus-5-5 → Opus 5.5 (the chip beside an agent called "Claude Code" should not say "Claude Code" again).
  const prettyModel = (id) => { const x = /^claude-([a-z]+)-(\d+)(?:-(\d{1,2}))?(?:-\d{8})?$/.exec(id || ""); return x ? `${x[1][0].toUpperCase()}${x[1].slice(1)} ${x[2]}${x[3] ? "." + x[3] : ""}` : id; };
  const modelOf = (c) => { const list = modelsOf(c ? provOf(c) : "claude"); return list.find((x) => x.v === C.model && x.ok) || list[0]; };
  function renderMenu() {
    const c = cur(), p = c ? provOf(c) : "claude", list = modelsOf(p), sel = modelOf(c).v;
    const efforts = C.models[p]?.efforts || ["default"], eff = effortOf(c) || "default";
    const item = (x) => `<button type="button" role="menuitemradio" aria-checked="${x.v === sel}" data-model="${esc(x.v)}"${x.ok ? "" : ` disabled title="${esc(x.why)}"`}>
      <span class="wc-mi-check" aria-hidden="true">${x.v === sel ? W.svg("M5 12l5 5L20 7") : ""}</span><span class="wc-mi-name">${esc(x.label)}</span>
      <span class="wc-mi-note">${esc(x.ok ? x.note : x.why)}</span>${x.tag ? `<span class="wc-mi-tag">${esc(x.tag)}</span>` : ""}</button>`;
    const mine = list.filter((x) => x.router);
    C.el.querySelector("#wc-menu").innerHTML = `<div class="wc-mhead">Model</div>${list.filter((x) => !x.router).map(item).join("")}
      ${mine.length ? `<div class="wc-mhead">Models page</div>${mine.map(item).join("")}` : ""}
      ${efforts.length > 1 ? `<div class="wc-mhead">Effort <span>how long it thinks before answering</span></div>
        <div class="wc-seg" role="group" aria-label="Effort" style="grid-template-columns: repeat(${efforts.length <= 4 ? efforts.length : 3}, 1fr)">${efforts.map((e) => `<button type="button" data-effort="${e}" aria-pressed="${e === eff}">${e === "default" ? "Default" : e}</button>`).join("")}</div>` : ""}
      <div class="wc-mfoot">For the next prompt: the conversation keeps its history.</div>`;
  }
  function openMenu(open) {
    const menu = C.el.querySelector("#wc-menu");
    menu.hidden = !open;
    C.el.querySelector("#wc-mbtn").setAttribute("aria-expanded", String(open));
    if (open) { renderMenu(); (menu.querySelector('[aria-checked="true"]') || menu.querySelector("button"))?.focus(); }
  }
  function setProvider(p) {
    const c = cur();
    if (!c || c.session) return renderProvider();
    c.provider = p; C.provider = p; store.set(K + ".provider", p); saveConvs(); renderProvider(); renderTabs();
    if (!c.runs.length) renderLog();
  }
  // Compact: Claude Code summarises the conversation so far (`/compact`), so later prompts send far fewer tokens.
  function compact() {
    const c = cur();
    if (!c?.session) return toast("Nothing to compact yet");
    if (busy(c)) return toast("Wait until the conversation is idle");
    send("/compact");
  }

  function renderCwd() {
    const cwd = cwdOf();
    const el = C.el?.querySelector("#wc-cwd");
    if (el) { el.textContent = cwd ? `in ${cwd.split("/").slice(-2).join("/")}` : ""; el.title = cwd; }
  }
  const focus = () => C.el?.querySelector("#wc-input")?.focus();
  const mention = (paths) => {
    const ta = C.el.querySelector("#wc-input"), here = cwdOf() === ROOT();
    const text = paths.filter(Boolean).map((p) => (p.startsWith("/") ? `@${here ? W.rel(p) : p}` : p)).join(" ");
    ta.setRangeText((ta.selectionStart && ta.value[ta.selectionStart - 1] !== " " ? " " : "") + text + " ", ta.selectionStart, ta.selectionEnd, "end");
    ta.focus(); ta.oninput();
  };

  // --- uploads (per tab) ------------------------------------------------------------------
  async function upload(file) {
    const list = draft().uploads;
    const fd = new FormData(); fd.append("file", file);
    const temp = { id: null, name: file.name, pending: true, local: file.type.startsWith("image/") ? URL.createObjectURL(file) : null };
    list.push(temp); renderAtt();
    try {
      const r = await fetch("/api/uploads", { method: "POST", body: fd, credentials: "same-origin" });
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || r.statusText);
      Object.assign(temp, data, { pending: false });
    } catch (e) { toast(`${file.name}: ${e.message}`); if (list.includes(temp)) list.splice(list.indexOf(temp), 1); }
    renderAtt();
  }
  function renderAtt() {
    const box = C.el.querySelector("#wc-att"), list = draft().uploads;
    box.innerHTML = list.map((u, i) => `<span class="wc-chip${u.pending ? " pending" : ""}">${u.local ? `<img src="${u.local}" alt="">` : W.fileIcon(u.name, false, 16)}<span>${esc(u.name)}</span><button type="button" data-rm="${i}" aria-label="Remove ${esc(u.name)}">×</button></span>`).join("");
    box.querySelectorAll("[data-rm]").forEach((b) => (b.onclick = () => { list.splice(Number(b.dataset.rm), 1); renderAtt(); }));
  }

  // --- running prompts ------------------------------------------------------------------
  // The Send/Stop button and prompt box follow the selected tab; the header dot shows any work.
  function renderBusy() {
    if (!C.el) return;
    const c = cur(), b = busy(c);
    const btn = C.el.querySelector("#wc-send"), ta = C.el.querySelector("#wc-input");
    btn.innerHTML = b ? W.svg("M7 7h10v10H7z") : W.svg("M4 12l16-8-6 16-2-7z"); btn.classList.toggle("stop", b);
    btn.title = b ? "Stop (Esc)" : "Send (Enter)"; btn.setAttribute("aria-label", b ? "Stop" : "Send");
    const cmp = C.el.querySelector("#wc-compact"); if (cmp) cmp.disabled = !c?.session || b || provOf(c) !== "claude";
    const any = C.convs.some(busy);
    const dot = C.el.querySelector(".wc-head .wc-dot"); dot.classList.toggle("busy", any); dot.title = any ? "working" : "idle";
    const s = c ? null : live(C.tab);  // another tab's session, still open where it was started
    ta.disabled = btn.disabled = !!s;
    ta.placeholder = s ? `This session is open in ${ENTRY[s.entrypoint] || s.entrypoint || "another Claude Code process"} (pid ${s.pid}): write to it there, or stop it from the sessions window to continue it here.`
      : c ? PLACEHOLDER : "Continue this conversation here: Claude Code resumes it in its folder…";
  }
  async function send(override) {
    const ta = C.el.querySelector("#wc-input");
    const prompt = (override ?? ta.value).trim();
    // `claude -p` cannot run interactive slash commands: the panel answers these itself.
    const slash = prompt.match(/^\/(status|tasks|agents|history|resume)$/i);
    if (slash) {
      ta.value = ""; ta.oninput();
      const cmd = slash[1].toLowerCase();
      if (cmd === "history" || cmd === "resume") { C.hist.tab = cmd === "resume" ? "history" : C.hist.tab; return openHist(true); }
      return openInfo(cmd);
    }
    const d = override ? { text: "", uploads: [] } : draft();
    if (d.uploads.some((u) => u.pending)) return toast("Wait for the attachments to finish uploading");
    if (!prompt && !d.uploads.length) return;
    let c = cur();
    if (!c) {  // an ended session in its own tab: continue it in place
      if (live(C.tab)) return toast("That session is still open where it was started");
      c = adopt(C.tab);
    }
    const cwd = c.cwd || ROOT();
    if (!cwd) return toast("Open a folder in the explorer first");
    const m = modelOf(c);
    try {
      const run = await api("/api/workspace/claude", { method: "POST", body: { prompt: prompt || "Look at the attached image(s).", cwd, session_id: c.session, title: c.title,
        model: m.router || m.v === "default" ? "" : m.v, router_model: m.router ? m.v.slice(2) : "", effort: effortOf(c), attachments: d.uploads.map((u) => u.id), provider: provOf(c), project_id: opt.projectId?.() ?? null } });
      c.session = run.session_id; c.cwd = run.cwd; c.title = c.title || run.title || "";
      C.history.push(run.prompt); C.hpos = -1;
      c.runs.push(run); saveConvs();
      d.text = ""; d.uploads = [];
      if (C.tab === c.id && !override) { ta.value = ""; ta.oninput(); renderAtt(); }
      renderLog(true, c);
      renderTabs(); renderBusy(); poll(run);
    } catch (e) { toast(e.message); }
  }
  // Turn the selected session tab (ended, not this panel's) into a conversation of the panel.
  function adopt(sid) {
    const s = C.names[sid] || {}, d = C.view.sid === sid ? C.view.d : null;
    const c = newConv({ session: sid, cwd: d?.cwd || s.cwd || "", title: s.title || histRow(sid)?.title || d?.title || "" });
    if (d) c.runs.push(transcriptRun(sid, C.view.events, d.partial_history, c.title));
    C.drafts[c.id] = draft(sid); delete C.drafts[sid];
    if (C.closed.delete(sid)) saveClosed();
    C.convs.push(c); C.tab = c.id; store.set(K + ".tab", c.id); touch(c.id); saveConvs();
    C.view = { sid: "", events: [], next: 0, d: null }; C.following = "";
    renderTabs(); renderCwd();
    return c;
  }
  async function stop(c = cur()) {
    const run = running(c);
    if (run) return api(`/api/workspace/claude/${run.id}/stop`, { method: "POST" }).catch((e) => toast(e.message));
    if (!following(c)) return;
    try { await api(`/api/workspace/claude-sessions/${encodeURIComponent(c.session)}/stop`, { method: "POST" }); toast("Stopping…"); }
    catch (e) { toast(e.message); }
  }
  async function poll(run) {
    const c = C.convs.find((x) => x.runs.includes(run));
    if (!c) return;  // its tab was closed
    try {
      const d = await api(`/api/workspace/claude/${run.id}?after=${run.next}`);
      run.events.push(...d.events); run.next = d.next; run.status = d.status; run.session_id = d.session_id; run.ended = d.ended;
      if (d.title && !run.title) { run.title = d.title; if (!c.title) { c.title = d.title; saveConvs(); renderTabs(); } }
      if (d.events.length || d.status !== "running") renderRun(run);
    } catch (e) {
      if (e.status === 404) {  // the server restarted: the conversation goes on in its transcript
        c.runs = []; saveConvs(); renderTabs(); renderBusy();
        if (c.session) showTranscript(c); else renderLog(undefined, c);
        return;
      }
      if (e.status !== 401) { setTimeout(() => poll(run), 2000); return; }
    }
    if (run.status === "running") { setTimeout(() => poll(run), 600); return; }
    if (run.session_id) c.session = run.session_id;
    saveConvs(); renderTabs(); renderBusy(); renderRun(run);
    if (!c.title) wantTitle(c, run);
    W.refreshExplorer?.(); W.reloadCleanTabs?.();
  }
  // The title is written by a quick separate call: it may come after a short answer.
  function wantTitle(c, run, tries = 0) {
    if (c.title || tries > 15) return;
    setTimeout(async () => {
      if (c.title || !C.convs.includes(c)) return;
      const d = await api(`/api/workspace/claude/${run.id}?after=${run.next || 0}`).catch(() => null);
      if (d?.title) { c.title = run.title = d.title; saveConvs(); renderTabs(); } else if (d) wantTitle(c, run, tries + 1);
    }, 2000);
  }

  // --- rendering --------------------------------------------------------------------------
  const TOOL_TONE = { Bash: "green", Read: "blue", Edit: "orange", MultiEdit: "orange", Write: "orange", NotebookEdit: "orange", Glob: "teal", Grep: "teal", LS: "teal",
    WebFetch: "purple", WebSearch: "purple", Task: "pink", TodoWrite: "yellow" };
  const resultOf = (run) => run.events.find((e) => e.kind === "result");
  const fileLink = (p) => (p ? `<a href="#" data-open="${esc(p)}" class="mono">${esc(W.rel(p))}</a>` : "");
  const firstLine = (s, n = 110) => { const l = String(s || "").trim().split("\n")[0]; return l.length > n ? l.slice(0, n) + "…" : l; };

  function toolSummary(e) {
    const i = e.input || {};
    switch (e.name) {
      case "Bash": return `<code>${esc(firstLine(i.command, 140))}</code>${i.description ? ` <span class="muted">${esc(i.description)}</span>` : ""}`;
      case "Read": case "Write": case "Edit": case "MultiEdit": case "NotebookEdit": return fileLink(i.file_path || i.notebook_path) + (i.offset ? ` <span class="muted">from line ${esc(i.offset)}</span>` : "");
      case "Grep": return `<code>${esc(i.pattern)}</code>${i.path ? ` <span class="muted">in ${esc(W.rel(i.path))}</span>` : ""}${i.glob ? ` <span class="muted">${esc(i.glob)}</span>` : ""}`;
      case "Glob": return `<code>${esc(i.pattern)}</code>`;
      case "LS": return fileLink(i.path);
      case "WebFetch": return `<span class="mono">${esc(firstLine(i.url, 90))}</span>`;
      case "WebSearch": return `<span>${esc(i.query)}</span>`;
      case "Task": return `<span>${esc(i.description || firstLine(i.prompt))}</span>`;
      case "TodoWrite": return `<span>${(i.todos || []).filter((t) => t.status === "completed").length}/${(i.todos || []).length} done</span>`;
      default: return `<span class="muted mono">${esc(firstLine(JSON.stringify(i), 100))}</span>`;
    }
  }
  function diff(oldS, newS) {
    const o = String(oldS || "").split("\n"), n = String(newS || "").split("\n");
    return `<pre class="cc-diff">${o.map((l) => `<span class="t-del">- ${esc(l)}</span>`).join("\n")}\n${n.map((l) => `<span class="t-add">+ ${esc(l)}</span>`).join("\n")}</pre>`;
  }
  function toolBody(e) {
    const i = e.input || {};
    if (i._truncated) return `<pre class="code">${esc(i._truncated)}</pre>`;
    switch (e.name) {
      case "Bash": return W.codeBlock(i.command || "", "shell");
      case "Edit": return diff(i.old_string, i.new_string);
      case "MultiEdit": return (i.edits || []).map((x) => diff(x.old_string, x.new_string)).join("");
      case "Write": return W.codeBlock(String(i.content || "").slice(0, 20000), W.kindOf(i.file_path || ""));
      case "TodoWrite": return `<ul class="cc-todos">${(i.todos || []).map((t) => `<li class="${esc(t.status)}"><span>${t.status === "completed" ? "✓" : t.status === "in_progress" ? "◐" : "○"}</span>${esc(t.content)}</li>`).join("")}</ul>`;
      case "Read": return "";
      default: return W.codeBlock(JSON.stringify(i, null, 2), "json");
    }
  }
  function eventHtml(run, e, results, isLast) {
    const k = `${run.id}:${e.n}`, open = C.open.has(k) ? " open" : "";
    const sub = e.parent ? " cc-sub" : "";
    switch (e.kind) {
      case "init": return e === run.events.find((x) => x.kind === "init") ? `<div class="cc-meta${sub}">${esc(e.model || "Claude")} · ${e.tools} tools · ${esc(W.rel(e.cwd) || e.cwd)}</div>` : "";
      case "thinking": return `<details class="cc-think${sub}" data-k="${k}"${open || (isLast && run.status === "running" ? " open" : "")}><summary><span class="cc-think-label">Thinking</span><span class="muted">${esc(firstLine(e.text, 90))}</span></summary><div class="cc-think-body">${esc(e.text)}</div></details>`;
      case "text": return `<div class="cc-text${sub}"><div class="md">${W.md(e.text, ROOT())}</div><div class="cc-actions"><button class="cc-copy" data-copy="${k}" title="Copy">${W.svg("M8 8h12v12H8zM4 16V4h12")} Copy</button></div></div>`;
      case "tool_use": {
        const r = results.get(e.id), tone = TOOL_TONE[e.name] || "grey";
        const state = r ? (r.is_error ? '<span class="cc-st err" title="failed">✗</span>' : '<span class="cc-st ok" title="done">✓</span>') : run.status === "running" ? '<span class="spin" title="running"></span>' : '<span class="cc-st muted">·</span>';
        const body = toolBody(e);
        const res = r ? `<div class="cc-res${r.is_error ? " err" : ""}"><div class="cc-res-head">${r.is_error ? "Error" : "Output"}<button class="cc-copy" data-copy="${run.id}:${r.n}">Copy</button></div><pre>${esc(r.text || "(no output)")}</pre></div>` : "";
        return `<details class="cc-tool tone-${tone}${sub}" data-k="${k}"${open}><summary><span class="cc-tname">${esc(e.name)}</span><span class="cc-tsum">${toolSummary(e)}</span>${state}</summary><div class="cc-tbody">${body}${res}</div></details>`;
      }
      case "tool_result": return "";
      case "result": {
        const secs = (e.duration_ms / 1000).toFixed(1);
        const shown = run.events.some((x) => x.kind === "text" && !x.parent);
        return `${!shown && e.text ? `<div class="cc-text"><div class="md">${W.md(e.text, ROOT())}</div></div>` : ""}<div class="cc-result${e.is_error ? " err" : ""}"><span>${e.is_error ? "✗ Failed" : "✓ Done"}</span><span class="muted">${e.turns} turn${e.turns === 1 ? "" : "s"} · ${secs}s${e.cost ? ` · $${e.cost.toFixed(4)}` : ""}</span><span class="grow"></span><button class="cc-copy" data-copy="${run.id}:all">${W.svg("M8 8h12v12H8zM4 16V4h12")} Copy answer</button></div>`;
      }
      case "prompt": return pinHtml(e.text, k);
      case "notice": return `<div class="cc-notice ${e.status === "completed" ? "ok" : e.status === "failed" || e.status === "killed" ? "err" : ""}"><span>${e.status === "completed" ? "✓" : e.status === "failed" ? "✗" : "■"}</span> Background task <code>${esc(e.task_id)}</code> ${esc(e.status)}${e.text && e.text !== e.status ? ` <span class="muted">${esc(firstLine(e.text, 160))}</span>` : ""}</div>`;
      case "compact": return `<div class="cc-divider cc-compact"><span>Conversation compacted${e.pre_tokens ? `: ${fmtK(e.pre_tokens)} → ${fmtK(e.post_tokens)} tokens` : ""}</span></div>`;
      case "error": return `<div class="cc-error">${esc(e.text)}</div>`;
      case "log": return `<div class="cc-logline mono">${esc(e.text)}</div>`;
      default: return "";
    }
  }
  // The prompt a turn answers: one line, pinned to the top of the log while its turn is in view (a click shows all of it).
  const pinHtml = (text, copyKey, imgs = "") => `<div class="cc-user cc-pin" data-pin title="Show the whole prompt">
    <div class="cc-user-text">${esc(text)}</div>${imgs ? `<div class="cc-imgs">${imgs}</div>` : ""}<button class="cc-copy ghost" data-copy="${copyKey}" title="Copy prompt">${W.svg("M8 8h12v12H8zM4 16V4h12")}</button></div>`;
  // What the agent did on the way (thinking, tool calls, logs, subagents) folds into one line per stretch; what it says,
  // problems and the result always show.
  const isStep = (e) => ["thinking", "tool_use", "tool_result", "log"].includes(e.kind) || (e.kind === "text" && e.parent);
  function stepsHtml(run, group, results, live) {
    const tools = group.filter((e) => e.kind === "tool_use");
    if (!tools.length && !group.some((e) => e.kind === "thinking" || e.kind === "log" || e.kind === "text")) return "";
    const failed = tools.filter((e) => results.get(e.id)?.is_error).length;
    const names = [...new Set(tools.map((e) => e.name))];
    const now = live ? [...group].reverse().find((e) => e.kind === "tool_use" || e.kind === "thinking") : null;
    const k = `${run.id}:steps:${group[0].n}`;
    const label = tools.length ? `${tools.length} step${tools.length === 1 ? "" : "s"}` : "Thinking";
    return `<details class="cc-steps" data-k="${k}"${C.open.has(k) ? " open" : ""}><summary>${live ? '<span class="spin"></span>' : `<span class="cc-steps-chev">${W.svg("M9 6l6 6-6 6")}</span>`}
      <span class="cc-steps-n">${label}</span>${failed ? `<span class="cc-st err">${failed} failed</span>` : ""}
      <span class="cc-steps-sum">${now ? (now.kind === "tool_use" ? `${esc(now.name)} ${toolSummary(now)}` : "Thinking…") : esc(names.slice(0, 5).join(", ") + (names.length > 5 ? "…" : ""))}</span></summary>
      <div class="cc-steps-body">${group.map((e) => eventHtml(run, e, results, false)).join("")}</div></details>`;
  }
  // A turn's events: stretches of steps folded, everything else as it is.
  function turnHtml(run, events, results, live) {
    let out = "", group = [];
    const flush = (last) => { if (group.length) out += stepsHtml(run, group, results, last && live); group = []; };
    for (const e of events) {
      if (isStep(e)) { group.push(e); continue; }
      flush(false); out += eventHtml(run, e, results, false);
    }
    flush(true);
    return out;
  }
  // A transcript's events by turn, each under its prompt; a turn whose prompt was not loaded says so.
  function turnsHtml(run, events, results, live) {
    const turns = [];
    for (const e of events) {
      if (e.kind === "prompt") turns.push({ p: e, ev: [] });
      else (turns[turns.length - 1] || (turns[0] = { p: null, ev: [] })).ev.push(e);
    }
    return turns.map((t, i) => `<div class="cc-turn">${t.p ? pinHtml(t.p.text, `${run.id}:${t.p.n}`) : t.ev.some((e) => !isStep(e) || e.kind === "tool_use") ? '<div class="cc-user cc-pin muted"><div class="cc-user-text">Earlier prompt not loaded</div></div>' : ""}${turnHtml(run, t.ev, results, live && i === turns.length - 1)}</div>`).join("");
  }
  function runHtml(run) {
    if (run.divider) return `<div class="cc-divider"><span>${esc(run.divider)}</span></div>`;
    const results = new Map(run.events.filter((e) => e.kind === "tool_result").map((e) => [e.tool_use_id, e]));
    if (run.transcript) {  // an earlier conversation, resumed: its transcript, then the new prompts
      const events = turnsHtml(run, run.events, results, false);
      return `<div class="cc-divider"><span>Earlier in this conversation${run.title ? ` · ${esc(run.title)}` : ""}</span></div>${run.partial ? '<div class="cc-divider"><span>Earlier history not loaded</span></div>' : ""}${events || '<p class="muted">No earlier conversation found.</p>'}<div class="cc-divider"><span>Continue below</span></div>`;
    }
    const imgs = (run.images || []).map((i) => `<a href="/api/uploads/${esc(i.id)}" target="_blank" rel="noopener"><img src="/api/uploads/${esc(i.id)}" alt="${esc(i.name)}" class="cc-thumb"></a>`).join("");
    const events = turnHtml(run, run.events, results, run.status === "running");
    const working = run.status === "running" ? `<div class="cc-working"><span class="spin"></span> Working… <span class="muted">Esc to stop</span></div>` : run.status === "stopped" && !run.events.some((e) => e.kind === "error") ? '<div class="cc-error">Stopped.</div>' : "";
    return `${pinHtml(run.prompt, `${run.id}:prompt`, imgs)}${events}${working}`;
  }
  // One log element per tab, kept while the tab is open: switching tabs only shows another element, and a
  // conversation working in a background tab keeps rendering into its own.
  function logOf(id = C.tab) {
    let el = C.logs.get(id);
    if (!el) {
      el = document.createElement("div"); el.className = "wc-log"; el.setAttribute("aria-live", "polite"); el.hidden = id !== C.tab;
      C.el.querySelector("#wc-logs").append(el); C.logs.set(id, el);
    }
    return el;
  }
  // A tab shown again opens at its end, unless the user had scrolled up in it to read (then it stays there).
  function showLog() {
    const log = logOf();
    C.logs.forEach((el, id) => {
      if (id !== C.tab && !el.hidden) el.dataset.end = el.scrollHeight - el.scrollTop - el.clientHeight < 80 ? "1" : "";
      el.hidden = id !== C.tab;
    });
    if (log.dataset.end !== "") log.scrollTop = log.scrollHeight;
  }
  function dropLog(id) { C.logs.get(id)?.remove(); C.logs.delete(id); }
  function renderLog(scroll, c = cur()) {
    if (!c || following(c)) { if (c === cur()) renderSession(); return; }
    if (c === cur()) C.el.querySelector("#wc-ro").hidden = true;
    const log = logOf(c.id);
    if (!c.runs.length) {
      if (C.cli && !C.cli.available) {
        log.innerHTML = `<div class="cc-error">The <code>claude</code> command is not installed on the server. Install Claude Code (<code>npm i -g @anthropic-ai/claude-code</code>) and sign in with <code>claude</code> in the terminal below.</div>`;
        return;
      }
      const gm = !!opt.projectId, p = provOf(c);
      const ideas = gm ? ["Which agents does this project need? Staff them.", "Set up this project's .claude/ configuration (agents, rules, skills, hooks)", "Start a goal: add tests for the main module and make them pass", "Status: what is the team working on?"]
        : ["Explain how this project is structured", "Run the tests and fix what fails", "Find TODOs and summarise them", "Review the latest git changes"];
      log.innerHTML = `<div class="cc-empty"><div class="wc-logo big" data-p="${p}">${gm ? "★" : PROV[p].logo}</div><h3>${gm ? "General Manager" : PROV[p].label}</h3><p class="muted">${gm
        ? `The company's General Manager (${esc(PROV[p].label)}), in this project's folder. Tell it what you want: it decides which agents, skills and plugins are needed, keeps <code>.claude/</code> up to date, and runs the agent loop until the goal is met.`
        : "Works in the folder open in the explorer, with full access: it can read and edit files, run commands and search the web."}</p>
        <div class="cc-suggest">${ideas.map((s) => `<button type="button" class="btn sm">${esc(s)}</button>`).join("")}</div></div>`;
      log.querySelectorAll(".cc-suggest button").forEach((b) => (b.onclick = () => { const ta = C.el.querySelector("#wc-input"); ta.value = b.dataset.say || b.textContent; ta.oninput(); ta.focus(); }));
      return;
    }
    log.innerHTML = c.runs.map((r) => `<div class="cc-run" data-run="${esc(r.id)}">${runHtml(r)}</div>`).join("");
    // At the end: a conversation opens on its latest prompt and answer; `false` keeps where the user scrolled.
    if (scroll !== false) log.scrollTop = log.scrollHeight;
  }
  function renderRun(run) {
    const c = C.convs.find((x) => x.runs.includes(run));
    if (!c || following(c)) return;
    const log = logOf(c.id);
    const el = log.querySelector(`[data-run="${CSS.escape(run.id)}"]`);
    if (!el) return renderLog(true, c);
    const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 80;
    el.innerHTML = runHtml(run);
    if (atBottom) log.scrollTop = log.scrollHeight;
  }
  // --- sessions on the host: tabs, live view, /status /tasks /agents -------------------------
  const fmtK = (n) => (n >= 1e9 ? `${(n / 1e9).toFixed(2)}B` : n >= 1e6 ? `${(n / 1e6).toFixed(2)}M` : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n || 0));
  const dur = (s) => (s < 60 ? `${Math.floor(s)}s` : s < 3600 ? `${Math.floor(s / 60)} min` : s < 86400 ? `${Math.floor(s / 3600)} h` : `${Math.floor(s / 86400)} d`);
  const since = (t) => (t ? dur(Math.max(0, Date.now() / 1000 - t)) : "");
  // The selected tab's session: a conversation's own, else the session tab's.
  const viewSid = () => { const c = cur(); return c ? c.session : C.tab; };
  const sessionRun = () => ({ id: "session", events: C.view.events, status: C.view.d?.status === "busy" ? "running" : "done" });
  // Sessions that can be opened in a tab of their own: every live one no conversation of this panel holds.
  const others = () => (opt.workspace ? C.sessions.filter((s) => !convOfSid(s.session_id)) : []);  // host sessions: Workspace only
  // Drop the oldest events but keep the prompt the first kept ones answer: a long turn never loses its prompt.
  function keepTail(events, max) {
    const cut = events.length - max, lead = events.slice(0, cut).reverse().find((e) => e.kind === "prompt");
    events.splice(0, cut);
    if (lead && events[0]?.kind !== "prompt") events.unshift(lead);
  }
  const transcriptRun = (sid, events, partial, title) => ({ id: `resume-${sid}`, transcript: true, session_id: sid, title: title || "", prompt: "", events, status: "done", partial });

  async function pollSessions() {
    if (!C.el) return;
    if (!C.el.isConnected || document.hidden) { setTimeout(pollSessions, 5000); return; }  // resumes when shown again
    {
      try {
        C.sessions = await api("/api/workspace/claude-sessions");
        C.sessions.forEach((s) => (C.names[s.session_id] = s));
        // A conversation the panel has no prompt of (restored after a restart) takes its transcript's title;
        // the panel's own prompts get theirs from Claude, which comes a moment after the first prompt.
        C.convs.forEach((c) => { const t = live(c.session)?.title; if (!c.title && t && !c.runs.some((r) => !r.transcript && !r.divider)) { c.title = t; saveConvs(); } });
        // A closed tab stays closed while its session runs; forget it once the session ended.
        const ids = new Set(C.sessions.map((s) => s.session_id));
        if ([...C.closed].some((id) => !ids.has(id))) { C.closed = new Set([...C.closed].filter((id) => ids.has(id))); saveClosed(); }
        checkFollow(); renderTabs(); renderBusy();
        if (C.hist.open) renderHist();
      } catch { /* signed out or offline: try again later */ }
    }
    setTimeout(pollSessions, 3000);
  }
  // Start or stop following the selected conversation's session; once it is done, show its transcript.
  function checkFollow() {
    const c = cur(), f = c && following(c) ? c.session : "";
    if (f === C.following) return;
    const was = C.following;
    C.following = f;
    if (!f && c && was && was === c.session) showTranscript(c); else renderLog();
    renderInfo(); pollDetail();
  }

  // --- sessions window: running now and history -----------------------------------------------
  const size = (n) => (n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);
  const histRow = (sid) => C.hist.items.find((r) => r.session_id === sid);
  function openHist(show) {
    const H = C.hist;
    H.open = show;
    C.el.querySelector("#wc-hist").hidden = !show;
    C.el.querySelector("#wc-hist-btn").setAttribute("aria-expanded", String(show));
    clearTimeout(H.timer);
    if (!show) return;
    renderHist(); loadHist();
    C.el.querySelector("#wc-hist-q").focus();
  }
  async function loadHist() {
    const H = C.hist;
    clearTimeout(H.timer);
    if (!H.open || !C.el?.isConnected) return;
    try {
      const q = H.q.trim();
      const d = await api(`/api/workspace/claude-history?limit=300${q ? `&q=${encodeURIComponent(q)}` : ""}`);
      if (q !== H.q.trim()) return;  // typed on meanwhile: the newer request will render
      H.items = d.items; H.total = d.total; H.at = Date.now(); H.error = "";
    } catch (e) { H.error = e.message; }
    renderHist();
    H.timer = setTimeout(loadHist, 15000);
  }
  // Running: every live session (this panel's conversations first). History: every session with a transcript.
  function runningRows() {
    const q = C.hist.q.trim().toLowerCase();
    const rows = C.sessions.map((s) => {
      const h = histRow(s.session_id) || {}, c = convOfSid(s.session_id);
      return { ...h, ...s, title: c ? convTitle(c) : s.title || h.title || s.name, panel: !!c, last_prompt: h.last_prompt || "" };
    });
    for (const c of C.convs) {  // just started: not in Claude Code's registry yet
      const run = running(c);
      if (run && !rows.some((r) => r.session_id === c.session)) rows.unshift({ session_id: c.session, title: convTitle(c), cwd: run.cwd, status: "busy", panel: true, started: run.started, entrypoint: "sdk-cli" });
    }
    rows.sort((a, b) => b.panel - a.panel);
    return q ? rows.filter((r) => [r.title, r.cwd, r.last_prompt, r.session_id, r.git_branch].some((x) => String(x || "").toLowerCase().includes(q))) : rows;
  }
  function renderHist() {
    const H = C.hist, body = C.el?.querySelector("#wc-hist-body");
    if (!body || !H.open) return;
    const running = runningRows();
    C.el.querySelector('[data-count="running"]').textContent = running.length || "";
    C.el.querySelector('[data-count="history"]').textContent = H.at ? H.total : "";
    C.el.querySelectorAll("[data-hist]").forEach((b) => { b.classList.toggle("on", b.dataset.hist === H.tab); b.setAttribute("aria-selected", String(b.dataset.hist === H.tab)); });
    // Icon buttons from the start: rendered as text, the page's iconize pass would swap them a moment later, on
    // every refresh of this list.
    const HICON = { open: "eye", resume: "play", stop: "stop", delete: "trash" };
    const btn = (act, sid, label, cls = "", title = "") => `<button type="button" class="btn sm icon ${cls}" data-hact="${act}" data-sid="${esc(sid)}" title="${esc(title || label)}" aria-label="${esc(label)}">${icon(HICON[act], 16)}</button>`;
    const row = (r, acts) => `<li class="wc-hrow${r.status === "ended" ? " ended" : ""}${r.session_id && r.session_id === viewSid() ? " on" : ""}" data-hrow="${esc(r.session_id || "")}" data-hstatus="${esc(r.status || "")}" title="${r.status === "ended" ? "Show this conversation and continue it here" : "Show this session"}">
        <span class="wc-dot${r.status === "busy" ? " busy" : r.status === "ended" ? " off" : ""}"></span>
        <div class="wc-hmain"><div class="wc-htitle"><b>${esc(r.title || r.session_id.slice(0, 8))}</b>${r.panel ? '<span class="wc-badge">tab in this panel</span>' : ""}${r.entrypoint && !r.panel ? `<span class="wc-badge">${esc(ENTRY[r.entrypoint] || r.entrypoint)}</span>` : ""}${r.status !== "ended" ? `<span class="wc-badge ${esc(r.status)}">${r.status === "busy" ? "working" : esc(r.status)}</span>` : ""}</div>
          <div class="muted mono wc-hsub" title="${esc(r.cwd || "")}">${esc(r.cwd || "")}${r.git_branch ? ` · ⎇ ${esc(r.git_branch)}` : ""}</div>
          ${r.last_prompt ? `<div class="wc-hprompt" title="${esc(r.last_prompt)}">${esc(firstLine(r.last_prompt, 160))}</div>` : ""}
          <div class="muted wc-hmeta">${[r.updated ? `active ${since(r.updated)} ago` : "", r.started && r.status !== "ended" ? `up ${since(r.started)}` : "", r.size ? size(r.size) : "", r.pid ? `pid ${r.pid}` : "", r.session_id ? `<span class="mono">${esc(r.session_id.slice(0, 8))}</span>` : ""].filter(Boolean).join(" · ")}</div>
          <div class="wc-hacts">${acts}</div></div></li>`;
    let html;
    if (H.tab === "running") {
      html = running.length ? `<ul class="wc-hlist">${running.map((r) => row(r, btn("open", r.session_id, "Open") + btn("stop", r.session_id, "Stop", "danger", r.panel ? "Stop the running prompt" : "End this Claude Code process"))).join("")}</ul>`
        : `<p class="muted wc-hempty">${H.q ? "No running session matches." : "No Claude Code session is running on this host."}</p>`;
    } else {
      const items = H.items;
      html = H.error ? `<div class="cc-error">${esc(H.error)}</div>` : !H.at ? '<div class="cc-working"><span class="spin"></span> Loading…</div>'
        : items.length ? `<ul class="wc-hlist">${items.map((r) => {
          const c = convOfSid(r.session_id);
          const acts = c ? btn("open", r.session_id, "Open", "", "Show its tab") : btn("open", r.session_id, "Open", "", "Show the conversation in a tab")
            + (r.status === "ended" ? btn("resume", r.session_id, "Continue", "primary", "Continue this conversation in a new tab of this panel") : "");
          return row({ ...r, title: c ? convTitle(c) : r.title, panel: !!c }, acts + (r.status === "ended" && !busy(c) ? btn("delete", r.session_id, "Delete", "danger", "Delete the transcript") : ""));
        }).join("")}</ul>${H.total > items.length ? `<p class="muted wc-hempty">Showing ${items.length} of ${H.total}: search to narrow down.</p>` : ""}`
        : `<p class="muted wc-hempty">${H.q ? "No session matches." : "No earlier sessions yet."}</p>`;
    }
    // Compared with the last markup, not body.innerHTML: the browser serialises SVG differently, which would
    // rebuild the list on every poll.
    if (H.html !== html) { H.html = html; const top = body.scrollTop; body.innerHTML = html; body.scrollTop = top; }
  }
  async function histAction(act, sid) {
    const r = histRow(sid) || C.sessions.find((s) => s.session_id === sid) || {};
    const c = convOfSid(sid);
    const label = (c && convTitle(c)) || r.title || r.name || sid.slice(0, 8);
    if (act === "open") {
      openHist(false);
      if (c) return selectTab(c.id);
      if (C.closed.delete(sid)) saveClosed();
      C.names[sid] = { ...(C.names[sid] || { name: label, cwd: r.cwd, entrypoint: r.entrypoint, session_id: sid }), title: C.names[sid]?.title || r.title || "" };
      return selectTab(sid);
    }
    if (act === "stop") {
      if (c && running(c)) return stop(c);
      if (!confirm(`Stop the Claude Code session "${label}"${r.pid ? ` (pid ${r.pid})` : ""}?\n\nIts process gets SIGTERM, as when its window is closed. Unsaved work in it may be lost; the transcript stays and can be continued.`)) return;
      try { await api(`/api/workspace/claude-sessions/${encodeURIComponent(sid)}/stop`, { method: "POST" }); toast(`Stopped ${label}`); }
      catch (e) { toast(e.message); }
      C.sessions = await api("/api/workspace/claude-sessions").catch(() => C.sessions);
      checkFollow(); renderTabs(); renderBusy(); renderHist(); setTimeout(loadHist, 1500);
      return;
    }
    if (act === "delete") {
      if (busy(c)) return toast("Stop the running prompt first");
      if (!confirm(`Delete the session "${label}"?\n\nIts transcript (${r.size ? size(r.size) : "the conversation"}), subagent transcripts and file checkpoints are removed from ~/.claude. This cannot be undone.`)) return;
      try { await api(`/api/workspace/claude-history/${encodeURIComponent(sid)}`, { method: "DELETE" }); toast(`Deleted ${label}`); }
      catch (e) { return toast(e.message); }
      C.hist.items = C.hist.items.filter((x) => x.session_id !== sid); C.hist.total = Math.max(0, C.hist.total - 1);
      if (C.closed.delete(sid)) saveClosed();
      if (c) dropConv(c); else if (C.tab === sid) selectTab(nextShown());
      renderHist();
      return;
    }
    if (act === "resume") return resume(r);
  }
  // Continue an ended session in a tab of its own (the current one, while it is still empty):
  // its conversation shows, and the next prompt runs with --resume in its folder.
  function resume(r) {
    const have = convOfSid(r.session_id);
    if (have) { openHist(false); return selectTab(have.id); }
    if (r.status && r.status !== "ended") return toast("That session is still running: open it, or stop it first");
    const c = blankConv();
    Object.assign(c, { session: r.session_id, cwd: r.cwd || c.cwd || "", title: r.title || "" });
    saveConvs(); openHist(false);
    if (C.tab === c.id) { showTranscript(c); renderTabs(); renderCwd(); } else selectTab(c.id);
    toast(`Continue: ${r.title || r.session_id.slice(0, 8)}`);
    C.el.querySelector("#wc-input").focus();
  }
  // A conversation that has no prompt of this server process: its conversation from the transcript, at its end.
  async function showTranscript(c) {
    const sid = c.session;
    let d = null;
    try { d = await api(`/api/workspace/claude-sessions/${encodeURIComponent(sid)}?after=0`); } catch { /* deleted or never written */ }
    if (c.session !== sid || c.runs.some((r) => !r.transcript && !r.divider)) return;  // a prompt was sent meanwhile
    if (!c.title && d?.title) { c.title = d.title; saveConvs(); renderTabs(); }
    c.runs = [transcriptRun(sid, d?.events || [], d?.partial_history, c.title)];
    renderLog(true, c);
  }
  function renderTabs() {
    const box = C.el?.querySelector("#wc-tabs");
    if (!box) return;
    const list = others().filter((s) => s.session_id === C.tab);  // only the host session being viewed
    if (!cur() && C.tab && !list.some((s) => s.session_id === C.tab)) list.push({ ...(C.names[C.tab] || { name: C.tab.slice(0, 8) }), session_id: C.tab, status: "ended" });
    const tab = (id, label, status, extra, title) => `<div role="tab" tabindex="0" data-tab="${esc(id)}" aria-selected="${C.tab === id}" class="wc-tab${C.tab === id ? " on" : ""}${status === "ended" ? " ended" : ""}" title="${esc(title)}"><span class="wc-dot${status === "busy" ? " busy" : status === "ended" ? " off" : ""}"></span><span class="wc-tab-name">${esc(label)}</span>${extra ? `<span class="wc-tab-kind">${esc(extra)}</span>` : ""}<button type="button" class="wtab-x" data-close="${esc(id)}" title="Close tab${conv(id) ? "" : " (the session keeps running)"}" aria-label="Close ${esc(label)}">×</button></div>`;
    box.innerHTML = C.convs.filter(shownConv).map((c) => tab(c.id, convTitle(c), busy(c) ? "busy" : "idle", provOf(c) === "claude" ? "" : PROV[provOf(c)].label, `${convTitle(c)}\n${c.cwd || ROOT() || ""}${busy(c) ? "\nWorking" : ""}${c.session ? `\nsession ${c.session.slice(0, 8)}` : ""}`)).join("")
      + list.map((s) => tab(s.session_id, s.title || s.name, s.status, ENTRY[s.entrypoint] || s.entrypoint || "", `${s.title ? `${s.title}\n` : ""}${s.cwd || ""}\n${s.status === "busy" ? "Working" : s.status === "ended" ? "Ended" : "Idle"}${s.pid ? ` · pid ${s.pid}` : ""}${s.version ? ` · v${s.version}` : ""}`)).join("");
  }
  const showTab = () => C.el.querySelector("#wc-tabs .wc-tab.on")?.scrollIntoView({ block: "nearest", inline: "nearest" });
  function dropConv(c) {
    C.convs = C.convs.filter((x) => x !== c); delete C.drafts[c.id]; dropLog(c.id);
    if (!C.convs.length) C.convs.push(newConv());
    saveConvs();
    if (C.tab === c.id) selectTab(nextShown()); else renderTabs();
  }
  // Closing a conversation drops it from the panel (its transcript stays in the history); closing a
  // session tab only hides it here: the session itself (VS Code, terminal, SDK) keeps running.
  function closeTab(id) {
    const c = conv(id);
    if (c) {
      if (busy(c)) {
        if (!confirm(`"${convTitle(c)}" is still working. Stop it and close the tab?`)) return;
        stop(c);
      }
      return dropConv(c);
    }
    C.closed.add(id); saveClosed(); dropLog(id);
    if (C.tab === id) selectTab(nextShown()); else renderTabs();
  }
  function selectTab(id) {
    if (id === C.tab) return;
    const ta = C.el.querySelector("#wc-input");
    draft().text = ta.value;
    C.tab = id;
    if (conv(id)) { store.set(K + ".tab", id); touch(id); }
    C.view = { sid: "", events: [], next: 0, d: null };
    ta.value = draft().text; ta.oninput(); renderAtt();
    const c = cur();
    C.following = c && following(c) ? c.session : "";
    const fresh = !C.logs.has(id) || !logOf(id).childElementCount;
    showLog();
    if (fresh || !c) renderLog();  // a tab rendered before is shown as it is: no reload
    renderTabs(); renderCwd(); renderBusy(); renderInfo(); pollDetail(); renderProvider();
    showTab();
    if (c?.session && !c.runs.length) showTranscript(c);  // never loaded in this page yet
  }
  function openInfo(section) {
    C.info = section;
    const box = C.el.querySelector("#wc-info");
    box.hidden = !section;
    C.el.querySelector("#wc-info-btn").setAttribute("aria-expanded", String(!!section));
    box.querySelectorAll("[data-info]").forEach((b) => { b.classList.toggle("on", b.dataset.info === section); b.setAttribute("aria-selected", String(b.dataset.info === section)); });
    renderInfo();
    if (section) pollDetail();
  }

  // Session tabs, a followed conversation and the drawer read the session's transcript.
  const watching = () => { const c = cur(); return !c || following(c); };
  async function pollDetail() {
    clearTimeout(C.dtimer);
    if (!C.el) return;
    if (!C.el.isConnected) { C.dtimer = setTimeout(pollDetail, 5000); return; }
    const sid = viewSid();
    if (!sid || (!watching() && !C.info)) {
      renderInfo();
      if (C.info) C.dtimer = setTimeout(pollDetail, 2000);  // the first prompt will create the session
      return;
    }
    if (C.view.sid !== sid) C.view = { sid, events: [], next: 0, d: null };
    const v = C.view;
    if (!document.hidden) {
      try {
        let d = await api(`/api/workspace/claude-sessions/${encodeURIComponent(sid)}?after=${v.next}`);
        if (v !== C.view) return;
        if (d.next < v.next) { v.events = []; d = await api(`/api/workspace/claude-sessions/${encodeURIComponent(sid)}?after=0`); }  // transcript reopened
        const changed = d.events.length > 0 || !v.d || v.d.status !== d.status || (d.status === "busy" && d.current?.name !== v.d.current?.name);
        v.events.push(...d.events);
        if (v.events.length > 600) keepTail(v.events, 600);
        v.next = d.next; v.d = d; v.missing = false;
        if (d.title && C.names[sid] && !C.names[sid].title) C.names[sid].title = d.title;
        if (changed && watching()) { renderSession(); if (!cur()) renderBusy(); }
      } catch (e) {
        if (v !== C.view) return;
        if (e.status === 404) { v.missing = true; if (watching()) renderSession(); }
      }
      renderInfo();
    }
    C.dtimer = setTimeout(pollDetail, v.d?.status === "busy" ? 1500 : 4000);
  }

  function activity(d) {
    const c = d.current;
    if (!c) return d.status === "busy" ? "Working…" : "";
    if (!c.name) return `Working…${c.since ? ` <span class="muted">${since(c.since)}</span>` : ""}`;
    return `Running <b>${esc(c.name)}</b> <span class="cc-tsum">${toolSummary(c)}</span>${c.since ? ` <span class="muted">${since(c.since)}</span>` : ""}`;
  }
  // A session tab, or a conversation followed from its transcript: the conversation, live.
  function renderSession() {
    if (!watching()) return;
    const log = logOf(), v = C.view, d = v.d, c = cur(), sid = viewSid();
    const s = C.names[sid] || {}, proc = live(sid), cwd = d?.cwd || s.cwd || "";
    const kind = esc(ENTRY[s.entrypoint] || s.entrypoint || "Claude Code");
    const ro = C.el.querySelector("#wc-ro");
    ro.hidden = false;
    ro.innerHTML = c ? "Still working in its own process (it outlived a server restart): following it live. Stop ends it; afterwards you can continue here."
      : proc ? `${kind} session in <span class="mono" title="${esc(cwd)}">${esc(cwd)}</span>: follow it here, write to it where it runs.`
      : `Ended ${kind} session in <span class="mono" title="${esc(cwd)}">${esc(cwd)}</span>: a prompt continues it here.`;
    if (!d) {
      log.innerHTML = v.missing ? `<div class="cc-empty"><div class="wc-logo big">✳</div><h3>${esc(s.title || s.name || "Session")}</h3><p class="muted">No transcript was found for this session yet.</p></div>` : `<div class="cc-working"><span class="spin"></span> Loading…</div>`;
      return;
    }
    const first = !log.querySelector(".cc-run.cc-live");
    const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 80;
    const run = sessionRun();
    const results = new Map(run.events.filter((e) => e.kind === "tool_result").map((e) => [e.tool_use_id, e]));
    const events = turnsHtml(run, run.events, results, d.status === "busy");
    const tail = d.status === "busy" ? `<div class="cc-working"><span class="spin"></span> ${activity(d)}</div>` : d.status === "ended" ? '<div class="cc-divider"><span>Session ended</span></div>' : "";
    log.innerHTML = `<div class="cc-run cc-live">${d.partial_history ? '<div class="cc-divider"><span>Earlier history not loaded</span></div>' : ""}${events || '<p class="muted">No conversation yet.</p>'}${tail}</div>`;
    // It opens at its end, and stays there while the user follows it (not when they scrolled up to read).
    if (first || atBottom) log.scrollTop = log.scrollHeight;
  }

  const ST = { running: ["spin", ""], completed: ["ok", "✓"], failed: ["err", "✗"], killed: ["err", "■"], stopped: ["muted", "■"], ended: ["muted", "·"] };
  const state = (s) => { const [cls, ch] = ST[s] || ["muted", "·"]; return cls === "spin" ? '<span class="spin" title="running"></span>' : `<span class="cc-st ${cls}" title="${esc(s)}">${ch}</span>`; };
  const kv = (rows) => `<dl class="wc-kv">${rows.filter((r) => r[1]).map(([k, val]) => `<dt>${esc(k)}</dt><dd>${val}</dd>`).join("")}</dl>`;

  function infoStatus(d) {
    const s = d?.process || C.names[viewSid()] || {};
    const c = cur(), p = c ? provOf(c) : "claude", m = c ? modelOf(c) : null;
    // The chosen model's own sign-in once /status has read it, else the host's (Claude Code only).
    const acct = p !== "claude" ? {} : homeModel() ? (C.usage.key === homeModel() && C.usage.d?.account) || { method: `${homeModel()}'s own sign-in` } : C.cli?.account || {};
    const agent = C.models[p];
    const chosen = m ? `${esc(m.label)}${m.note && m.router ? ` <span class="mono muted">${esc(m.note)}</span>` : ""}${m.tag ? ` <span class="wc-badge">${esc(m.tag)}</span>` : ""}` : "";
    const status = c && !following(c) ? (busy(c) ? "busy" : "idle") : d?.status || "";
    return kv([
      ["Status", status ? `<span class="wc-badge ${esc(status)}">${esc(status === "busy" ? "working" : status)}</span>${status === "busy" && d ? ` ${activity(d)}` : ""}` : ""],
      ["Session", d || s.name ? `${esc(s.name || "")} <span class="mono muted">${esc(viewSid() || "")}</span>` : ""],
      ["Started from", s.entrypoint || d?.entrypoint ? `${esc(ENTRY[s.entrypoint || d.entrypoint] || s.entrypoint || d.entrypoint)}${s.pid ? ` <span class="muted">· pid ${s.pid}${s.started ? ` · up ${since(s.started)}` : ""}</span>` : ""}` : ""],
      ["Folder", d?.cwd ? `<span class="mono">${esc(d.cwd)}</span>${d.git_branch ? ` <span class="wc-badge">⎇ ${esc(d.git_branch)}</span>` : ""}` : ""],
      ["Agent", c ? esc(PROV[p].label) : ""],
      ["Model", c ? `${chosen} <span class="muted">for the next prompt</span>${d?.model ? `<br><span class="mono">${esc(d.model)}</span> <span class="muted">last used</span>` : ""}` : d?.model ? `<span class="mono">${esc(d.model)}</span>` : ""],
      ["Effort", c && effortOf(c) ? esc(effortOf(c)) : ""],
      ["Permissions", d?.permission_mode ? esc(d.permission_mode) : ""],
      ["Context", d?.context_tokens ? `${fmtK(d.context_tokens)} tokens <span class="muted">in the last request</span>` : ""],
      ["Output", d?.output_tokens ? `${fmtK(d.output_tokens)} tokens${d.partial_history ? ' <span class="muted">(recent history)</span>' : ""}` : ""],
      ["Last prompt", d?.last_prompt ? `<span class="wc-clamp">${esc(firstLine(d.last_prompt, 300))}</span>` : ""],
      ["Last activity", d?.last_activity ? `${since(d.last_activity)} ago` : ""],
      [c && p !== "claude" ? PROV[p].label : "Claude Code", c && p !== "claude" ? esc(agent?.version || "") || (agent && !agent.available ? "not installed" : "")
        : esc(d?.version || C.cli?.version || "") || (C.cli && !C.cli.available ? "not installed" : "")],
      ["Account", acct.method ? `${esc(acct.method)}${acct.email ? ` · ${esc(acct.email)}` : ""}${acct.organization ? ` <span class="muted">(${esc(acct.organization)})</span>` : ""}` : ""],
    ]) + (!d && c ? '<p class="muted">Send a prompt to start a conversation; its status shows here.</p>' : "") + infoUsage();
  }
  // The chosen Models-page model when it has its own sign-in: /status, /tasks and /agents show that sign-in's.
  const homeModel = () => { const c = cur(), m = modelOf(c); return m.router && m.account ? m.v.slice(2) : ""; };
  const homeQ = (sep) => (homeModel() ? `${sep}model=${encodeURIComponent(homeModel())}` : "");
  // Tokens and cost today and this week, every Claude Code session on the host (or of the chosen model's sign-in);
  // read again every 30 s and when the model changes.
  const left = (s) => { const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60); return d ? `${d} d ${h} h` : h ? `${h} h ${m} min` : `${m} min`; };
  const money2 = (v) => `$${v >= 100 ? Math.round(v).toLocaleString() : v.toFixed(2)}`;
  function infoUsage() {
    const U = C.usage, key = homeModel();
    if (cur() && provOf(cur()) !== "claude") return "";
    if (U.key !== key) { U.d = null; U.at = 0; U.err = ""; }
    if (!U.busy && Date.now() - U.at > 30000) {
      U.busy = true; U.key = key;
      api(`/api/workspace/claude-usage${homeQ("?")}`).then((d) => { if (U.key === key) { U.d = d; U.err = ""; } }).catch((e) => { U.err = e.message; })
        .finally(() => { U.busy = false; if (U.key === key) U.at = Date.now(); renderInfo(); });
    }
    const head = `<h4>Usage <span class="muted">${key ? `every Claude Code session of ${esc(key)}'s sign-in` : "every Claude Code session on this host"}</span></h4>`;
    if (!U.d) return head + `<p class="muted">${U.err ? esc(U.err) : '<span class="spin"></span> Counting tokens…'}</p>`;
    const now = Date.now();
    const row = (label, w) => {
      const at = Date.parse(w.resets_at), rest = Math.max(0, (at - now) / 1000);
      const parts = `Input ${fmt.format(w.input_tokens)} · output ${fmt.format(w.output_tokens)} · cache read ${fmt.format(w.cache_read_tokens)} · cache write ${fmt.format(w.cache_write_tokens)} · ${fmt.format(w.messages)} replies`
        + (w.models.length ? `\n${w.models.map((m) => `${m.model}: ${fmtK(m.tokens)} tokens${m.priced ? `, ${money2(m.cost)}` : ", no price"}`).join("\n")}` : "");
      const when = new Date(at).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" });
      return `<tr><th>${label}</th><td title="${esc(parts)}"><b>${fmtK(w.tokens)}</b> <span class="muted">tokens</span></td>
        <td title="Cost at API prices${w.unpriced_tokens ? `; ${fmt.format(w.unpriced_tokens)} tokens of models without a price are not counted` : ""}"><b>${money2(w.cost)}</b>${w.unpriced_tokens ? "+" : ""}</td>
        <td title="Resets ${esc(new Date(at).toLocaleString())}">resets ${esc(when)} <span class="muted">· in ${left(rest)}</span></td></tr>`;
    };
    return head + planLimits(U.d.plan) + `<table class="wc-usage"><tbody>${row("Today", U.d.daily)}${row("This week", U.d.weekly)}</tbody></table>
      <p class="muted wc-usage-note">Cost at the API prices in Models (cache reads 0.1×, cache writes 1.25–2× input): a Claude subscription is not billed per token. Day from 00:00 UTC, week from Monday 00:00 UTC. Hover a row for the breakdown.</p>`;
  }
  // The Claude plan's limits, as Claude Code's /usage shows them: the 5-hour session and the week (limitCards, app.js).
  function planLimits(p) {
    if (!p) return "";
    if (!p.available) return `<p class="muted wc-usage-note">Plan limits: ${esc(p.reason || "unavailable")}.</p>`;
    return limitCards(p.limits);
  }

  // Every live Claude Code session on the host, for /agents and /tasks; read again every 4 s while one is open.
  function hostActivity() {
    const A = C.act, key = homeModel();
    if (A.key !== key) { A.d = null; A.at = 0; A.err = ""; }
    if (!A.busy && Date.now() - A.at > 4000) {
      A.busy = true; A.key = key;
      const cwd = cur()?.cwd || C.view.d?.cwd || ROOT() || "";
      api(`/api/workspace/claude-activity?cwd=${encodeURIComponent(cwd)}${homeQ("&")}`).then((d) => { if (A.key === key) { A.d = d; A.err = ""; } }).catch((e) => { A.err = e.message; })
        .finally(() => { A.busy = false; if (A.key === key) A.at = Date.now(); renderInfo(); });
    }
    return A.d;
  }
  const hostRow = (r, extra) => {
    const sid = r.session_id, here = sid === viewSid(), c = convOfSid(sid), openable = !here && (c || others().some((s) => s.session_id === sid));
    return `<li class="wc-host${openable ? " link" : ""}"${openable ? ` data-open="${esc(sid)}" title="Open this session"` : ""}><span class="wc-dot${r.status === "busy" ? " busy" : ""}"></span><div>
      <div><b>${esc(c ? convTitle(c) : r.title || sid.slice(0, 8))}</b>${here ? ' <span class="wc-badge">this tab</span>' : ""}${r.entrypoint ? ` <span class="wc-badge">${esc(ENTRY[r.entrypoint] || r.entrypoint)}</span>` : ""} <span class="muted">${r.status === "busy" ? "working" : esc(r.status)}</span></div>
      <div class="muted"><span class="mono">${esc(r.cwd)}</span>${extra ? ` · ${extra}` : ""}</div></div></li>`;
  };
  const plural = (n, one, many) => `${n} ${n === 1 ? one : many || `${one}s`}`;
  function hostAgents() {
    const A = hostActivity();
    const where = homeModel() ? `Claude Code with ${esc(homeModel())}'s sign-in` : "Claude Code on this host";
    if (!A) return `<h4>${where}</h4><p class="muted">${C.act.err ? esc(C.act.err) : '<span class="spin"></span> Looking…'}</p>`;
    const rows = [...A.sessions].sort((a, b) => (b.status === "busy") - (a.status === "busy"));
    return `<h4>${where} <span class="muted">${plural(rows.length, "session")} · ${A.busy} working · ${plural(A.agents_running, "subagent")} running</span></h4>`
      + (rows.length ? `<ul class="wc-list">${rows.map((r) => hostRow(r, [r.agents ? `${plural(r.agents, "subagent")}${r.agents_running.length ? `, ${r.agents_running.length} running` : ""}` : "", ...r.agents_running.map((a) => `<span class="spin"></span> ${esc(a.description || a.type)}`)].filter(Boolean).join(" · "))).join("")}</ul>`
        : '<p class="muted">No Claude Code session is running on this host.</p>');
  }
  function hostTasks() {
    const A = hostActivity();
    if (!A) return `<h4>Elsewhere on this host</h4><p class="muted">${C.act.err ? esc(C.act.err) : '<span class="spin"></span> Looking…'}</p>`;
    const rows = A.sessions.filter((r) => r.session_id !== viewSid() && (r.tasks_running.length || r.agents_running.length || r.todos_open));
    const head = `<h4>Elsewhere on this host <span class="muted">${plural(A.sessions.length, "Claude Code session")} · ${plural(A.tasks_running, "background task")} running</span></h4>`;
    if (!rows.length) return head + '<p class="muted">No other session runs background work or has open todos.</p>';
    return head + `<ul class="wc-list">${rows.map((r) => hostRow(r, [
      ...r.tasks_running.map((t) => `<span class="spin"></span> ${esc(t.description || firstLine(t.command, 60))}`),
      r.agents_running.length ? plural(r.agents_running.length, "subagent") + " running" : "",
      r.todos_open ? plural(r.todos_open, "open todo") : ""].filter(Boolean).join(" · "))).join("")}</ul>`;
  }
  // No transcript in this tab yet: loading, or no session at all.
  const noSession = () => (viewSid() && !C.view.missing ? '<p class="muted"><span class="spin"></span> Reading the session…</p>'
    : `<p class="muted">${cur() ? "This conversation has no session yet: its first prompt starts one." : "No transcript was found for this session."}</p>`);
  function infoTasks(d) {
    if (!d) return noSession() + hostTasks();
    const tasks = [...d.tasks].reverse(), bgAgents = d.agents.filter((a) => a.background).reverse();
    const out = [];
    if (tasks.length || bgAgents.length) {
      out.push(`<h4>Background tasks <span class="muted">${tasks.concat(bgAgents).filter((t) => t.status === "running").length} running</span></h4><ul class="wc-list">${tasks.map((t) => `<li>${state(t.status)}<div><div><b>${esc(t.description || firstLine(t.command, 80))}</b> <span class="muted mono">${esc(t.id)}</span></div>${t.description ? `<code class="wc-cmd">${esc(firstLine(t.command, 200))}</code>` : ""}<div class="muted">${esc(t.status)} · started ${since(t.started)} ago${t.ended ? ` · took ${dur(t.ended - t.started)}` : ""}${t.summary ? ` · ${esc(firstLine(t.summary, 160))}` : ""}</div></div></li>`).join("")}${bgAgents.map((a) => `<li>${state(a.status)}<div><div><b>${esc(a.description || a.type)}</b> <span class="wc-badge">agent · ${esc(a.type)}</span></div><div class="muted">${esc(a.status)} · started ${since(a.started)} ago</div></div></li>`).join("")}</ul>`);
    }
    if (d.todos.length) out.push(`<h4>Todo list <span class="muted">${d.todos.filter((t) => t.status === "completed").length}/${d.todos.length} done</span></h4><ul class="cc-todos">${d.todos.map((t) => `<li class="${esc(t.status)}"><span>${t.status === "completed" ? "✓" : t.status === "in_progress" ? "◐" : "○"}</span>${esc(t.content)}</li>`).join("")}</ul>`);
    if (d.task_list.length) out.push(`<h4>Tasks</h4><ul class="cc-todos">${d.task_list.map((t) => `<li class="${esc(t.status)}"><span>${t.status === "completed" ? "✓" : t.status === "in_progress" ? "◐" : "○"}</span>#${esc(t.id)} ${esc(t.subject)}</li>`).join("")}</ul>`);
    return (out.join("") || '<p class="muted">No background tasks or todos in this session.</p>') + hostTasks();
  }
  function infoAgents(d) {
    if (!d) {  // still the agents defined for this folder and the types Claude Code offers
      const A = C.act.d, out = [noSession(), hostAgents()];
      if (A?.configured_agents.length) out.push(customAgents(A.configured_agents));
      if (A?.agent_types.length) out.push(agentTypes(A.agent_types));
      return out.join("");
    }
    const order = { running: 0 };
    const agents = [...d.agents].reverse().sort((a, b) => (order[a.status] ?? 1) - (order[b.status] ?? 1));
    const out = [`<h4>Subagents <span class="muted">${agents.filter((a) => a.status === "running").length} running · ${agents.length} in view</span></h4>`
      + (agents.length ? `<ul class="wc-list">${agents.map((a) => `<li>${state(a.status)}<div><div><b>${esc(a.description || a.type)}</b> <span class="wc-badge">${esc(a.type)}</span>${a.background ? ' <span class="wc-badge">background</span>' : ""}</div><div class="muted">${esc(a.status)}${a.model ? ` · <span class="mono">${esc(a.model)}</span>` : ""}${a.tokens ? ` · ${fmtK(a.tokens)} tokens` : ""}${a.tools ? ` · ${a.tools} tool calls` : ""} · ${a.ended ? `took ${dur(a.ended - a.started)}` : `started ${since(a.started)} ago`}</div></div></li>`).join("")}</ul>` : '<p class="muted">No subagents started in this session.</p>')];
    out.push(hostAgents());
    if (d.configured_agents.length) out.push(customAgents(d.configured_agents));
    const types = d.agent_types.length ? d.agent_types : C.act.d?.agent_types || [];
    if (types.length) out.push(agentTypes(types));
    return out.join("");
  }
  const customAgents = (list) => `<h4>Custom agents</h4><ul class="wc-list">${list.map((a) => `<li><span class="cc-st muted">◆</span><div><div><b>${esc(a.name)}</b>${a.model ? ` <span class="wc-badge">${esc(a.model)}</span>` : ""}</div><div class="muted">${esc(a.description)}</div></div></li>`).join("")}</ul>`;
  const agentTypes = (types) => `<h4>Available agent types</h4><div class="wc-chips">${types.map((t) => `<span class="wc-badge">${esc(t)}</span>`).join("")}</div>`;
  function renderInfo() {
    if (!C.info || !C.el) return;
    const body = C.el.querySelector("#wc-info-body");
    const d = C.view.sid === viewSid() ? C.view.d : null;
    const html = C.info === "tasks" ? infoTasks(d) : C.info === "agents" ? infoAgents(d) : infoStatus(d);
    if (body.innerHTML !== html) body.innerHTML = html;
  }
  return { init, focus, mention, el: () => C.el, newConversation: () => { selectTab(blankConv().id); focus(); } };
  }
  W.makeClaudePanel = makePanel;
  W.initClaude = (el) => {
    const panel = makePanel({ workspace: true });
    W.focusClaude = panel.focus; W.claudeMention = panel.mention;
    return panel.init(el);
  };
})();
