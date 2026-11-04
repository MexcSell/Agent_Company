"use strict";
// Workspace Claude Code panel: the conversation (thinking, tool calls with their results,
// answers, run summary) on top and the prompt box below. Each prompt runs Claude Code in
// the explorer's folder with every tool allowed; follow-ups resume the same session.
// Images can be pasted, dropped or attached; every answer has a copy button.
window.WSP = window.WSP || {};
(() => {
  const W = window.WSP;
  const C = { el: null, runs: [], uploads: [], busy: false, open: new Set(), history: [], hpos: -1,
    session: store.get("ac.wsp.claude.session") || "", sessionCwd: store.get("ac.wsp.claude.cwd") || "", model: store.get("ac.wsp.claude.model") || "default" };
  const saveRuns = () => store.set("ac.wsp.claude.runs", JSON.stringify(C.runs.map((r) => r.id).slice(-30)));
  const saveSession = () => { store.set("ac.wsp.claude.session", C.session); store.set("ac.wsp.claude.cwd", C.sessionCwd); };

  W.initClaude = async (el) => {
    C.el = el;
    el.innerHTML = `<div class="wc-head"><span class="wc-logo" aria-hidden="true">✳</span><strong>Claude Code</strong><span class="wc-dot" title="idle"></span><span class="grow"></span>
        <select id="wc-model" aria-label="Model" title="Model"></select>
        <button class="icon-btn sm" id="wc-new" title="New conversation">${W.svg("M12 5v14M5 12h14")}</button></div>
      <div class="wc-log" id="wc-log" aria-live="polite"></div>
      <form class="wc-composer" id="wc-form">
        <div class="wc-att" id="wc-att"></div>
        <textarea id="wc-input" rows="3" placeholder="Ask Claude Code to read, change, run or explain anything in this folder…  Enter sends · Shift+Enter new line · paste or drop images"></textarea>
        <div class="wc-row"><label class="icon-btn sm" for="wc-file" title="Attach images or files">${W.svg("M21 12.5l-8.5 8.5a6 6 0 0 1-8.5-8.5L13 3.5a4 4 0 0 1 5.7 5.7L9.6 18.3a2 2 0 0 1-2.8-2.8L15 7.3")}</label>
          <input type="file" id="wc-file" multiple hidden accept="image/*,text/*,.json,.yaml,.yml,.md,.pdf">
          <span class="wc-cwd muted mono grow" id="wc-cwd"></span>
          <button type="submit" class="btn primary sm" id="wc-send">Send</button></div>
      </form>`;
    const ta = el.querySelector("#wc-input");
    el.querySelector("#wc-form").onsubmit = (e) => { e.preventDefault(); C.busy ? stop() : send(); };
    ta.onkeydown = (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); if (!C.busy) send(); }
      else if (e.key === "Escape" && C.busy) { e.preventDefault(); stop(); }
      else if (e.key === "ArrowUp" && !ta.value && C.history.length) { e.preventDefault(); C.hpos = C.history.length - 1; ta.value = C.history[C.hpos]; }
      else if (e.key === "ArrowUp" && C.hpos > 0 && ta.value === C.history[C.hpos]) { e.preventDefault(); ta.value = C.history[--C.hpos]; }
      else if (e.key === "ArrowDown" && C.hpos >= 0 && ta.value === C.history[C.hpos]) { e.preventDefault(); C.hpos++; ta.value = C.history[C.hpos] || ""; if (C.hpos >= C.history.length) C.hpos = -1; }
    };
    ta.oninput = () => { ta.style.height = "auto"; ta.style.height = `${Math.min(260, Math.max(64, ta.scrollHeight))}px`; };
    ta.onpaste = (e) => { const files = [...(e.clipboardData?.files || [])]; if (files.length) { e.preventDefault(); files.forEach(upload); } };
    el.querySelector("#wc-file").onchange = (e) => { [...e.target.files].forEach(upload); e.target.value = ""; };
    el.addEventListener("dragover", (e) => { e.preventDefault(); el.classList.add("drop"); });
    el.addEventListener("dragleave", (e) => { if (!el.contains(e.relatedTarget)) el.classList.remove("drop"); });
    el.addEventListener("drop", (e) => {
      e.preventDefault(); el.classList.remove("drop");
      const files = [...e.dataTransfer.files];
      if (files.length && !e.dataTransfer.types.includes("text/x-paths")) { files.forEach(upload); return; }
      const text = e.dataTransfer.getData("text/plain");
      if (text) W.claudeMention(text.split("\n"));
    });
    el.querySelector("#wc-new").onclick = () => {
      if (C.busy) return toast("Stop the running prompt first");
      C.session = ""; C.sessionCwd = ""; C.runs = []; saveSession(); saveRuns(); renderLog(); ta.focus();
    };
    const sel = el.querySelector("#wc-model");
    sel.onchange = () => { C.model = sel.value; store.set("ac.wsp.claude.model", C.model); };
    // Copy buttons, and keep track of which blocks the user expanded.
    el.addEventListener("click", (e) => {
      const b = e.target.closest("[data-copy]");
      if (!b) return;
      const [rid, n] = b.dataset.copy.split(":");
      const run = C.runs.find((r) => r.id === rid);
      if (!run) return;
      if (n === "prompt") return W.copyText(run.prompt);
      if (n === "all") return W.copyText(run.events.filter((x) => x.kind === "text").map((x) => x.text).join("\n\n") || resultOf(run)?.text || "");
      const ev = run.events.find((x) => x.n === Number(n));
      W.copyText(ev?.kind === "tool_use" ? JSON.stringify(ev.input, null, 2) : ev?.text || "");
    });
    el.addEventListener("toggle", (e) => { const k = e.target.dataset?.k; if (k) e.target.open ? C.open.add(k) : C.open.delete(k); }, true);
    W.onRoot = () => renderCwd();
    renderCwd();

    try {
      const info = await api("/api/workspace/claude");
      sel.innerHTML = info.models.map((m) => `<option value="${m}">${m === "default" ? "Default model" : m[0].toUpperCase() + m.slice(1)}</option>`).join("");
      sel.value = info.models.includes(C.model) ? C.model : "default";
      if (!info.available) el.querySelector("#wc-log").innerHTML = `<div class="cc-error">The <code>claude</code> command is not installed on the server. Install Claude Code (<code>npm i -g @anthropic-ai/claude-code</code>) and sign in with <code>claude</code> in the terminal below.</div>`;
    } catch (e) { toast(e.message); }
    let ids = [];
    try { ids = JSON.parse(store.get("ac.wsp.claude.runs") || "[]"); } catch { /* ignore */ }
    const runs = await Promise.all(ids.map((id) => api(`/api/workspace/claude/${id}`).catch(() => null)));
    C.runs = runs.filter(Boolean);
    C.history = C.runs.map((r) => r.prompt);
    if (C.runs.length || !el.querySelector(".cc-error")) renderLog();
    const running = C.runs.find((r) => r.status === "running");
    if (running) { setBusy(true); poll(running); }
  };

  function renderCwd() {
    const cwd = W.root();
    const el = C.el?.querySelector("#wc-cwd");
    if (el) { el.textContent = cwd ? `in ${cwd.split("/").slice(-2).join("/")}` : "no folder open"; el.title = cwd; }
  }
  W.focusClaude = () => C.el?.querySelector("#wc-input")?.focus();
  W.claudeMention = (paths) => {
    const ta = C.el.querySelector("#wc-input");
    const text = paths.filter(Boolean).map((p) => (p.startsWith("/") ? `@${W.rel(p)}` : p)).join(" ");
    ta.setRangeText((ta.selectionStart && ta.value[ta.selectionStart - 1] !== " " ? " " : "") + text + " ", ta.selectionStart, ta.selectionEnd, "end");
    ta.focus(); ta.oninput();
  };

  // --- uploads ------------------------------------------------------------------------
  async function upload(file) {
    const fd = new FormData(); fd.append("file", file);
    const temp = { id: null, name: file.name, pending: true, local: file.type.startsWith("image/") ? URL.createObjectURL(file) : null };
    C.uploads.push(temp); renderAtt();
    try {
      const r = await fetch("/api/uploads", { method: "POST", body: fd, credentials: "same-origin" });
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || r.statusText);
      Object.assign(temp, data, { pending: false });
    } catch (e) { toast(`${file.name}: ${e.message}`); C.uploads.splice(C.uploads.indexOf(temp), 1); }
    renderAtt();
  }
  function renderAtt() {
    const box = C.el.querySelector("#wc-att");
    box.innerHTML = C.uploads.map((u, i) => `<span class="wc-chip${u.pending ? " pending" : ""}">${u.local ? `<img src="${u.local}" alt="">` : W.fileIcon(u.name, false, 16)}<span>${esc(u.name)}</span><button type="button" data-rm="${i}" aria-label="Remove ${esc(u.name)}">×</button></span>`).join("");
    box.querySelectorAll("[data-rm]").forEach((b) => (b.onclick = () => { C.uploads.splice(Number(b.dataset.rm), 1); renderAtt(); }));
  }

  // --- running prompts ------------------------------------------------------------------
  function setBusy(b) {
    C.busy = b;
    const btn = C.el.querySelector("#wc-send");
    btn.textContent = b ? "Stop" : "Send"; btn.classList.toggle("danger", b); btn.classList.toggle("primary", !b);
    const dot = C.el.querySelector(".wc-dot"); dot.classList.toggle("busy", b); dot.title = b ? "working" : "idle";
  }
  async function send() {
    const ta = C.el.querySelector("#wc-input");
    const prompt = ta.value.trim();
    if (C.uploads.some((u) => u.pending)) return toast("Wait for the attachments to finish uploading");
    if (!prompt && !C.uploads.length) return;
    if (!W.root()) return toast("Open a folder in the explorer first");
    if (C.session && C.sessionCwd !== W.root()) { C.session = ""; C.runs.push({ id: `div-${Date.now()}`, divider: `New conversation in ${W.root()}`, events: [] }); }
    try {
      const run = await api("/api/workspace/claude", { method: "POST", body: { prompt: prompt || "Look at the attached image(s).", cwd: W.root(), session_id: C.session,
        model: C.model === "default" ? "" : C.model, attachments: C.uploads.map((u) => u.id) } });
      C.sessionCwd = W.root(); saveSession();
      C.history.push(run.prompt); C.hpos = -1;
      C.runs.push(run); saveRuns();
      ta.value = ""; ta.oninput(); C.uploads = []; renderAtt();
      renderLog(true); setBusy(true); poll(run);
    } catch (e) { toast(e.message); }
  }
  async function stop() {
    const run = C.runs.find((r) => r.status === "running");
    if (run) await api(`/api/workspace/claude/${run.id}/stop`, { method: "POST" }).catch((e) => toast(e.message));
  }
  async function poll(run) {
    try {
      const d = await api(`/api/workspace/claude/${run.id}?after=${run.next}`);
      run.events.push(...d.events); run.next = d.next; run.status = d.status; run.session_id = d.session_id; run.ended = d.ended;
      if (d.events.length || d.status !== "running") renderRun(run);
    } catch (e) {
      if (e.status === 404) run.status = "failed";
      else if (e.status !== 401) { setTimeout(() => poll(run), 2000); return; }
    }
    if (run.status === "running") { setTimeout(() => poll(run), 600); return; }
    if (run.session_id) { C.session = run.session_id; saveSession(); }
    setBusy(false); renderRun(run);
    W.refreshExplorer?.(); W.reloadCleanTabs?.();
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
      case "text": return `<div class="cc-text${sub}"><div class="md">${W.md(e.text, W.root())}</div><div class="cc-actions"><button class="cc-copy" data-copy="${k}" title="Copy">${W.svg("M8 8h12v12H8zM4 16V4h12")} Copy</button></div></div>`;
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
        return `${!shown && e.text ? `<div class="cc-text"><div class="md">${W.md(e.text, W.root())}</div></div>` : ""}<div class="cc-result${e.is_error ? " err" : ""}"><span>${e.is_error ? "✗ Failed" : "✓ Done"}</span><span class="muted">${e.turns} turn${e.turns === 1 ? "" : "s"} · ${secs}s${e.cost ? ` · $${e.cost.toFixed(4)}` : ""}</span><span class="grow"></span><button class="cc-copy" data-copy="${run.id}:all">${W.svg("M8 8h12v12H8zM4 16V4h12")} Copy answer</button></div>`;
      }
      case "error": return `<div class="cc-error">${esc(e.text)}</div>`;
      case "log": return `<div class="cc-logline mono">${esc(e.text)}</div>`;
      default: return "";
    }
  }
  function runHtml(run) {
    if (run.divider) return `<div class="cc-divider"><span>${esc(run.divider)}</span></div>`;
    const results = new Map(run.events.filter((e) => e.kind === "tool_result").map((e) => [e.tool_use_id, e]));
    const imgs = (run.images || []).map((i) => `<a href="/api/uploads/${esc(i.id)}" target="_blank" rel="noopener"><img src="/api/uploads/${esc(i.id)}" alt="${esc(i.name)}" class="cc-thumb"></a>`).join("");
    const events = run.events.map((e, i) => eventHtml(run, e, results, i === run.events.length - 1)).join("");
    const working = run.status === "running" ? `<div class="cc-working"><span class="spin"></span> Working… <span class="muted">Esc to stop</span></div>` : run.status === "stopped" && !run.events.some((e) => e.kind === "error") ? '<div class="cc-error">Stopped.</div>' : "";
    return `<div class="cc-user"><div class="cc-user-text">${esc(run.prompt)}</div>${imgs ? `<div class="cc-imgs">${imgs}</div>` : ""}<button class="cc-copy ghost" data-copy="${run.id}:prompt" title="Copy prompt">${W.svg("M8 8h12v12H8zM4 16V4h12")}</button></div>${events}${working}`;
  }
  function renderLog(scroll) {
    const log = C.el.querySelector("#wc-log");
    if (!C.runs.length) {
      log.innerHTML = `<div class="cc-empty"><div class="wc-logo big">✳</div><h3>Claude Code</h3><p class="muted">Works in the folder open in the explorer, with full access: it can read and edit files, run commands and search the web.</p>
        <div class="cc-suggest">${["Explain how this project is structured", "Run the tests and fix what fails", "Find TODOs and summarise them", "Review the latest git changes"].map((s) => `<button type="button" class="btn sm">${esc(s)}</button>`).join("")}</div></div>`;
      log.querySelectorAll(".cc-suggest button").forEach((b) => (b.onclick = () => { const ta = C.el.querySelector("#wc-input"); ta.value = b.textContent; ta.focus(); }));
      return;
    }
    log.innerHTML = C.runs.map((r) => `<div class="cc-run" data-run="${esc(r.id)}">${runHtml(r)}</div>`).join("");
    if (scroll !== false) log.scrollTop = log.scrollHeight;
  }
  function renderRun(run) {
    const log = C.el.querySelector("#wc-log");
    const el = log.querySelector(`[data-run="${CSS.escape(run.id)}"]`);
    if (!el) return renderLog();
    const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 80;
    el.innerHTML = runHtml(run);
    if (atBottom) log.scrollTop = log.scrollHeight;
  }
})();
