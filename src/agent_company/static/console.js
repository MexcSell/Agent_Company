"use strict";
// Workspace terminal: xterm.js tabs attached to server-side shells over WebSockets.
// Shells keep running when the page changes; re-attaching replays their recent output.
window.WSP = window.WSP || {};
(() => {
  const W = window.WSP;
  const T = { el: null, list: [], active: null, ready: null };

  const THEMES = {
    dark: { background: "#0d1117", foreground: "#d6dde6", cursor: "#58a6ff", selectionBackground: "#264f78", black: "#484f58", red: "#ff7b72", green: "#3fb950",
      yellow: "#d29922", blue: "#58a6ff", magenta: "#bc8cff", cyan: "#39c5cf", white: "#b1bac4", brightBlack: "#6e7681", brightRed: "#ffa198", brightGreen: "#56d364",
      brightYellow: "#e3b341", brightBlue: "#79c0ff", brightMagenta: "#d2a8ff", brightCyan: "#56d4dd", brightWhite: "#f0f6fc" },
    light: { background: "#ffffff", foreground: "#1f2328", cursor: "#0969da", selectionBackground: "#b6d7ff", black: "#24292f", red: "#cf222e", green: "#116329",
      yellow: "#7d4e00", blue: "#0969da", magenta: "#8250df", cyan: "#1b7c83", white: "#6e7781", brightBlack: "#57606a", brightRed: "#a40e26", brightGreen: "#1a7f37",
      brightYellow: "#633c01", brightBlue: "#218bff", brightMagenta: "#a475f9", brightCyan: "#3192aa", brightWhite: "#8c959f" },
  };
  const theme = () => THEMES[W.isDark() ? "dark" : "light"];

  function load() {
    W.loadCss("/static/vendor/xterm.css");
    return (T.ready ||= W.loadScript("/static/vendor/xterm.js").then(() => W.loadScript("/static/vendor/xterm-addon-fit.js")));
  }

  W.initTerminals = async (el) => {
    T.el = el;
    el.innerHTML = `${W.winStrip ? W.winStrip("terminal", "Terminal") : ""}<div class="wt-bar"><div class="wt-tabs" role="tablist"></div>
      <button class="icon-btn sm" data-t="new" title="New terminal">${W.svg("M12 5v14M5 12h14")}</button>
      <button class="icon-btn sm" data-t="reconnect" title="Reconnect the terminal">${W.svg("M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7")}</button>
      <button class="icon-btn sm" data-t="clear" title="Clear">${W.svg("M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13")}</button>
      ${W.winBtns ? W.winBtns("terminal") : ""}
      <button class="icon-btn sm" data-t="hide" title="Hide panel (Ctrl+\`)">${W.svg("M6 9l6 6 6-6")}</button></div>
      <div class="wt-hosts"></div>`;
    el.querySelector(".wt-bar").onclick = (e) => {
      const b = e.target.closest("[data-t]");
      if (!b) return;
      if (b.dataset.t === "new") W.newTerminal(W.root());
      if (b.dataset.t === "reconnect") reconnect(T.active);
      if (b.dataset.t === "clear") T.active?.term.clear();
      if (b.dataset.t === "max") W.maximizeTerminal();
      if (b.dataset.t === "hide") W.toggleTerminal(false);
    };
    // Drop explorer items (or any text) onto the terminal to type their paths.
    el.addEventListener("dragover", (e) => { if (e.dataTransfer.types.includes("text/plain")) e.preventDefault(); });
    el.addEventListener("drop", (e) => {
      const text = e.dataTransfer.getData("text/plain");
      if (!text || !T.active) return;
      e.preventDefault();
      T.active.send(text.split("\n").map((p) => (/^[\w./-]+$/.test(p) ? p : `'${p.replace(/'/g, "'\\''")}'`)).join(" ") + " ");
      T.active.term.focus();
    });
    new MutationObserver(() => T.list.forEach((t) => (t.term.options.theme = theme()))).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    // Coming back to the tab (or the network): lost connections reconnect by themselves.
    const wake = () => { if (!document.hidden) T.list.filter((t) => t.lost && t.lost !== 4404).forEach((t) => reconnect(t, true)); };
    document.addEventListener("visibilitychange", wake); addEventListener("online", wake);
    new ResizeObserver(() => W.fitTerminals()).observe(el);
    try {
      await load();
      const existing = (await api("/api/terminal/sessions")).filter((s) => s.alive);
      for (const s of existing) attach(s);
      if (!existing.length) await W.newTerminal(W.root());
      else activate(T.list[0]);
    } catch (e) { el.querySelector(".wt-hosts").innerHTML = `<p class="muted" style="padding:10px">Terminal unavailable: ${esc(e.message)}</p>`; }
  };

  W.newTerminal = async (cwd) => {
    if (W.state.termHidden) W.toggleTerminal(true);
    try {
      await load();
      const s = await api("/api/terminal/sessions", { method: "POST", body: { cwd: cwd || "", cols: 120, rows: 30 } });
      activate(attach(s));
    } catch (e) { toast(e.message); }
  };
  W.sendToTerminal = async (text) => {
    if (!T.active || !T.active.alive) await W.newTerminal(W.root());
    if (W.state.termHidden) W.toggleTerminal(true);
    T.active?.send(text); T.active?.term.focus();
  };
  W.focusTerminal = () => T.active?.term.focus();
  W.fitTerminals = () => {
    if (!T.active || !T.el?.isConnected || T.active.host.hidden || !T.active.host.offsetParent) return;  // minimised: keep its size
    try { T.active.fit.fit(); } catch { /* not visible yet */ }
  };

  function attach(s) {
    const host = document.createElement("div");
    host.className = "wt-host";
    T.el.querySelector(".wt-hosts").append(host);
    const term = new window.Terminal({ fontFamily: 'ui-monospace, "Cascadia Code", "JetBrains Mono", Menlo, Consolas, monospace', fontSize: 13, lineHeight: 1.15,
      cursorBlink: true, scrollback: 5000, theme: theme(), allowProposedApi: false, macOptionIsMeta: true, rightClickSelectsWord: true });
    const fit = new window.FitAddon.FitAddon();
    term.loadAddon(fit);
    term.open(host);
    const t = { id: s.id, cwd: s.cwd, title: (s.cwd.split("/").pop() || "/"), host, term, fit, ws: null, alive: true, retry: 0 };
    t.send = (data) => { if (t.ws?.readyState === 1) t.ws.send(JSON.stringify({ t: "in", data })); };
    term.onData((d) => t.send(d));
    term.onBinary((d) => { if (t.ws?.readyState === 1) t.ws.send(Uint8Array.from(d, (c) => c.charCodeAt(0))); });
    term.onResize(({ cols, rows }) => { if (t.ws?.readyState === 1) t.ws.send(JSON.stringify({ t: "resize", cols, rows })); });
    term.onTitleChange((title) => { if (title) { t.title = title.replace(/^.*?:\s*/, "").split("/").pop() || title; renderTabs(); } });
    // Ctrl+C copies when text is selected; Ctrl+V pastes (Ctrl+Shift+C/V always do).
    term.attachCustomKeyEventHandler((e) => {
      if (e.type !== "keydown") return true;
      const k = e.key.toLowerCase();
      if ((e.ctrlKey || e.metaKey) && k === "c" && (e.shiftKey || term.hasSelection())) { W.copyText(term.getSelection(), "Copied"); term.clearSelection(); return false; }
      if ((e.ctrlKey || e.metaKey) && k === "v") return false; // let the browser paste event reach xterm
      if (e.ctrlKey && k === "`") return false;
      return true;
    });
    connect(t);
    T.list.push(t);
    renderTabs();
    return t;
  }

  function connect(t) {
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/terminal/sessions/${t.id}/ws`);
    ws.binaryType = "arraybuffer";
    t.ws = ws;
    let first = true;
    ws.onopen = () => { t.retry = 0; t.lost = 0; t.alive = true; renderTabs(); try { t.fit.fit(); } catch { /* hidden */ } ws.send(JSON.stringify({ t: "resize", cols: t.term.cols, rows: t.term.rows })); };
    ws.onmessage = (ev) => {
      if (typeof ev.data === "string") {
        const m = JSON.parse(ev.data);
        if (m.t === "exit") { t.alive = false; t.term.write(`\r\n\x1b[2m[process exited${m.code != null ? ` with code ${m.code}` : ""} · press Enter to close]\x1b[0m\r\n`); renderTabs(); armClose(t); }
        return;
      }
      if (first) { first = false; t.term.reset(); }
      t.term.write(new Uint8Array(ev.data));
    };
    ws.onclose = (ev) => {
      if (!t.alive || t.closed) return;
      if (ev.code === 4404 || ev.code === 4403 || t.retry > 8) {
        const why = ev.code === 4403 ? "refused: sign in as an administrator, and if a proxy is in front, forward the Host or X-Forwarded-Host header"
          : ev.code === 4404 ? "this shell no longer exists on the server" : "the server is not reachable";
        t.alive = false; t.lost = ev.code === 4404 ? 4404 : ev.code || 1;
        t.term.write(`\r\n\x1b[31m[terminal disconnected: ${why}]\x1b[0m \x1b[2m· press Enter or the reconnect button${ev.code === 4404 ? " to open a new shell here" : " to reconnect"}\x1b[0m\r\n`);
        renderTabs(); armReconnect(t); return;
      }
      t.retry++;
      setTimeout(() => { if (!t.closed) connect(t); }, Math.min(8000, 500 * 2 ** t.retry));
    };
  }
  // Reconnect a lost terminal: the same shell when the server still has it, else a new one in the same folder.
  async function reconnect(t, quiet) {
    if (!t) return;
    if (t.lost === 4404) {
      const i = T.list.indexOf(t);
      await W.newTerminal(t.cwd);
      close(t);
      if (i >= 0 && T.list.length) activate(T.list[T.list.length - 1]);
      return;
    }
    if (!t.lost && t.ws?.readyState === 1) { if (!quiet) toast("Terminal is connected"); return; }
    t.retry = 0; t.lost = 0; t.alive = true; t.closed = false;
    t.term.write("\r\n\x1b[2m[reconnecting…]\x1b[0m\r\n");
    try { t.ws?.close(); } catch { /* already closed */ }
    connect(t); renderTabs();
  }
  function armReconnect(t) {
    const d = t.term.onData((data) => { if (data === "\r" && t.lost) { d.dispose(); reconnect(t); } });
  }
  function armClose(t) {
    const d = t.term.onData((data) => { if (data === "\r") { d.dispose(); close(t); } });
  }

  function activate(t) {
    T.active = t || null;
    T.list.forEach((x) => (x.host.hidden = x !== t));
    renderTabs();
    if (t) requestAnimationFrame(() => { W.fitTerminals(); t.term.focus(); });
  }
  async function close(t) {
    t.closed = true;
    t.ws?.close();
    if (t.alive) await api(`/api/terminal/sessions/${t.id}`, { method: "DELETE" }).catch(() => {});
    else api(`/api/terminal/sessions/${t.id}`, { method: "DELETE" }).catch(() => {});
    t.term.dispose(); t.host.remove();
    const i = T.list.indexOf(t);
    T.list.splice(i, 1);
    if (T.active === t) activate(T.list[Math.min(i, T.list.length - 1)]);
    else renderTabs();
  }
  function renderTabs() {
    const bar = T.el.querySelector(".wt-tabs");
    bar.innerHTML = T.list.map((t, i) => `<div class="wt-tab${t === T.active ? " on" : ""}${t.alive ? "" : " dead"}${t.lost ? " lost" : ""}" role="tab" data-i="${i}" title="${esc(t.cwd)}${t.lost ? " · disconnected: click to reconnect" : ""}">${W.svg("M4 17l6-5-6-5M12 19h8")}<span>${esc(t.title)}</span><button class="wtab-x" data-close="${i}" aria-label="Kill terminal">×</button></div>`).join("")
      || '<span class="muted wt-none">Terminal</span>';
    bar.querySelectorAll(".wt-tab").forEach((el) => {
      const t = T.list[Number(el.dataset.i)];
      el.onclick = (e) => { if (e.target.closest("[data-close]")) { if (!t.alive || confirm("Kill this terminal and everything running in it?")) close(t); } else { activate(t); if (t.lost) reconnect(t); } };
    });
  }
})();
