"use strict";
// Workspace - the administrators' home page, laid out like an editor with Claude Code:
// explorer (left) | file viewer over terminal (centre) | Claude Code (right).
// The DOM is built once and re-attached when the view is shown again, so terminals,
// open files and the conversation survive switching views and live refreshes.
window.WSP = window.WSP || {};
(() => {
  const W = window.WSP;
  const read = (k, d) => { try { return JSON.parse(store.get(k) || "null") ?? d; } catch { return d; } };
  // A list kept in this browser; anything else stored under the key must not stop the Workspace from registering.
  const readList = (k) => { const v = read(k, []); return Array.isArray(v) ? v : []; };
  const S = (W.state = {
    root: store.get("ac.wsp.root") || "", roots: [], dirs: new Map(), items: new Map(), open: new Set(readList("ac.wsp.open")),
    sel: new Set(), anchor: null, clip: null, tabs: [], active: null, el: null,
    sizes: { left: 260, right: 400, term: 260, ...read("ac.wsp.sizes", {}) }, termHidden: store.get("ac.wsp.termHidden") === "1",
    win: { min: new Set(readList("ac.wsp.min")), max: store.get("ac.wsp.max") || null }, git: {}, filter: "", panes: [], noClaude: false,
    id: "main", box: null, orient: store.get("ac.wsp.orient") === "h" ? "h" : "v", focus: null, dragFrom: null, offline: false,
  });
  const saveOpen = () => store.set("ac.wsp.open", JSON.stringify([...S.open].slice(-300)));
  const parentOf = (p) => p.slice(0, p.lastIndexOf("/")) || "/";
  const baseName = (p) => p.slice(p.lastIndexOf("/") + 1);
  W.rel = (p) => (S.root && p.startsWith(S.root + "/") ? p.slice(S.root.length + 1) : p);
  W.root = () => S.root;

  // --- file icons: VS Code's default theme (Seti, vendor/seti) --------------------------------
  // As VS Code picks them: the file name, then its extensions (longest first: "test.js" before "js"),
  // then the language VS Code gives the extension, else the plain file icon. Coloured for the theme.
  const LANG = {
    py: "python", pyi: "python", pyw: "python", js: "javascript", mjs: "javascript", cjs: "javascript", jsx: "javascriptreact",
    ts: "typescript", mts: "typescript", cts: "typescript", tsx: "typescriptreact", json: "json", jsonc: "jsonc", jsonl: "jsonl",
    webmanifest: "json", "code-workspace": "jsonc", md: "markdown", mdx: "markdown", markdown: "markdown", mkd: "markdown",
    sh: "shellscript", bash: "shellscript", zsh: "shellscript", ksh: "shellscript", bashrc: "shellscript", zshrc: "shellscript",
    bash_profile: "shellscript", profile: "shellscript", yml: "yaml", yaml: "yaml", html: "html", htm: "html", xhtml: "html",
    css: "css", scss: "scss", less: "less", xml: "xml", xsd: "xml", xsl: "xml", plist: "xml", csproj: "xml", sql: "sql",
    go: "go", rs: "rust", rb: "ruby", gemspec: "ruby", rake: "ruby", java: "java", c: "c", cpp: "cpp", cc: "cpp", cxx: "cpp",
    "c++": "cpp", cs: "csharp", php: "php", swift: "swift", lua: "lua", dart: "dart", pl: "perl", pm: "perl",
    clj: "clojure", cljs: "clojure", coffee: "coffeescript", fs: "fsharp", fsx: "fsharp", groovy: "groovy", jl: "julia",
    tex: "latex", m: "objective-c", mm: "objective-cpp", bat: "bat", cmd: "bat", ps1: "powershell", psm1: "powershell",
    psd1: "powershell", properties: "properties", dockerfile: "dockerfile", containerfile: "dockerfile", mk: "makefile",
    hbs: "handlebars", handlebars: "handlebars", pug: "jade", env: "dotenv", gitignore: "ignore", ignore: "ignore",
    cu: "cuda-cpp", erl: "erlang", ex: "elixir", hs: "haskell", tf: "terraform", vue: "vue", jinja: "jinja", j2: "jinja",
  };
  const LANG_NAMES = { dockerfile: "dockerfile", containerfile: "dockerfile", makefile: "makefile", gnumakefile: "makefile",
    "docker-compose.yml": "dockercompose", "docker-compose.yaml": "dockercompose", "compose.yml": "dockercompose", "compose.yaml": "dockercompose",
    ".env": "dotenv", ".gitignore": "ignore", ".dockerignore": "ignore", gemfile: "ruby", rakefile: "ruby" };
    const IMG = new Set(["png", "jpg", "jpeg", "gif", "webp", "bmp", "ico", "avif", "tif", "tiff"]), AUD = new Set(["mp3", "wav", "ogg", "flac", "m4a", "aac"]), VID = new Set(["mp4", "webm", "mov", "mkv", "avi"]);
  // Folders: one yellow, drawn 10% larger than file icons.
  const FOLDER_COLOUR = "#f2b705", FOLDER_SCALE = 1.1;
  function setiIcon(lower) {
    const T = window.SETI;
    if (!T) return null;
    let id = T.n[lower];
    if (!id) {
      const parts = lower.split(".");  // ".bashrc" → ["", "bashrc"]: its extension is "bashrc", as in VS Code
      for (let i = 1; i < parts.length && !id; i++) id = T.e[parts.slice(i).join(".")];
    }
    if (!id) {
      const lang = LANG_NAMES[lower] || (lower.startsWith(".env") ? "dotenv" : "") || (lower.includes(".") ? LANG[lower.slice(lower.lastIndexOf(".") + 1)] : "");
      id = lang && T.l[lang];
    }
    return T.d[id] || T.d[T.f];
  }
  W.fileIcon = (name, dir, size = 16, open = false) => {
    const lower = name.toLowerCase();
    if (dir) {
      const c = FOLDER_COLOUR, px = Math.round(size * FOLDER_SCALE * 10) / 10;
      return `<svg class="fico fdir" width="${px}" height="${px}" viewBox="0 0 24 24" aria-hidden="true">${open
        ? `<path d="M3 7a2 2 0 0 1 2-2h4l2 2h7a2 2 0 0 1 2 2v1H8.5a2 2 0 0 0-1.9 1.4L4 19H5a2 2 0 0 1-2-2z" fill="${c}" opacity=".7"/><path d="M6.6 11.4A2 2 0 0 1 8.5 10H22l-2.6 8.2A2 2 0 0 1 17.5 20H4z" fill="${c}"/>`
        : `<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" fill="${c}"/>`}</svg>`;
    }
    const [ch, dark, light] = setiIcon(lower) || ["e023", "#d4d7d6", "#bfc2c1"];
    // The glyphs are drawn at 150% of the row's text in VS Code, centred in the icon's box.
    return `<span class="fico seti" style="--sd:${dark};--sl:${light};width:${size}px;height:${size}px;font-size:${Math.round(size * 1.5)}px" aria-hidden="true">&#x${ch};</span>`;
  };
  const KIND = { py: "python", js: "javascript", mjs: "javascript", jsx: "javascript", ts: "typescript", tsx: "typescript", json: "json", ipynb: "json", md: "markdown", mdx: "markdown",
    html: "html", htm: "html", vue: "html", svelte: "html", xml: "xml", css: "css", scss: "css", less: "css", sh: "shell", bash: "shell", zsh: "shell", toml: "toml", ini: "toml", cfg: "toml", env: "env",
    yaml: "yaml", yml: "yaml", sql: "sql", go: "go", rs: "rust", rb: "ruby", java: "java", c: "c", h: "c", cpp: "cpp", hpp: "cpp", cs: "csharp", php: "php", kt: "kotlin", swift: "swift",
    lua: "lua", r: "r", dart: "dart", scala: "scala", diff: "diff", patch: "diff", log: "log", csv: "csv", tsv: "csv", pdf: "pdf", docx: "docx", svg: "svg",
    xlsx: "sheet", xls: "sheet", pptx: "slides", zip: "archive", tar: "archive", gz: "archive", tgz: "archive", bz2: "archive", xz: "archive", "7z": "archive", rar: "archive" };
  W.kindOf = (name) => {
    const lower = name.toLowerCase(), ext = lower.includes(".") ? lower.slice(lower.lastIndexOf(".") + 1) : "";
    if (lower === "dockerfile") return "docker";
    if (lower === "makefile") return "make";
    return KIND[ext] || (IMG.has(ext) ? "image" : AUD.has(ext) ? "audio" : VID.has(ext) ? "video" : "file");
  };

  // --- context menu -----------------------------------------------------------------------
  let menuEl = null;
  const closeMenu = () => { menuEl?.remove(); menuEl = null; };
  W.menu = (x, y, items) => {
    closeMenu();
    menuEl = document.createElement("div");
    menuEl.className = "ctx";
    menuEl.setAttribute("role", "menu");
    menuEl.innerHTML = items.filter(Boolean).map((it, i) => (it === "-" ? '<div class="ctx-sep"></div>'
      : `<button role="menuitem" data-i="${i}" class="${it.danger ? "danger" : ""}" ${it.disabled ? "disabled" : ""}><span>${esc(it.label)}</span><kbd>${esc(it.key || "")}</kbd></button>`)).join("");
    document.body.append(menuEl);
    const list = items.filter(Boolean);
    menuEl.querySelectorAll("button").forEach((b) => (b.onclick = () => { closeMenu(); list[Number(b.dataset.i)].act(); }));
    const r = menuEl.getBoundingClientRect();
    menuEl.style.left = `${Math.max(4, Math.min(x, innerWidth - r.width - 4))}px`;
    menuEl.style.top = `${Math.max(4, Math.min(y, innerHeight - r.height - 4))}px`;
    menuEl.querySelector("button:not([disabled])")?.focus();
  };
  document.addEventListener("pointerdown", (e) => { if (menuEl && !menuEl.contains(e.target)) closeMenu(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeMenu(); });
  addEventListener("blur", closeMenu);

  // --- layout ---------------------------------------------------------------------------
  // The explorer's buttons; every explorer window has them (the first one in the head of the column).
  const PANE_BTNS = `<button class="icon-btn sm" data-x="newfile" title="New file">${svg("M6 2h9l5 5v15H6zM14 2v6h6M12 11v6M9 14h6")}</button>
        <button class="icon-btn sm" data-x="newdir" title="New folder">${svg("M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM12 10v6M9 13h6")}</button>
        <button class="icon-btn sm" data-x="upload" title="Upload files">${svg("M12 16V4M7 9l5-5 5 5M4 20h16")}</button>
        <button class="icon-btn sm" data-x="refresh" title="Refresh">${svg("M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7")}</button>
        <button class="icon-btn sm" data-x="collapse" title="Collapse folders">${svg("M8 4l4 4 4-4M8 20l4-4 4 4")}</button>
        <button class="icon-btn sm" data-x="filter" title="Filter files by name (Ctrl+F in the list)">${svg("M3 5h18l-7 8v6l-4 2v-8z")}</button>`;
  const FILTER = '<div class="wx-filter" hidden><input type="search" placeholder="Filter by name…" aria-label="Filter files"></div>';
  const LAYOUT = `
    <aside class="wx" aria-label="Files">
      <div class="wx-head">${PANE_BTNS}
        <button class="icon-btn sm" data-x="split" title="Split: open another file window (+)">${svg("M12 5v14M5 12h14")}</button>
        <button class="icon-btn sm" data-x="orient" id="wx-orient" hidden></button>
        <span class="grow"></span>${winBtns("explorer")}</div>
      ${strip("explorer", "files", svg("M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"))}
      <div class="wx-split" id="wx-split">
        <section class="wx-pane wx-main">${FILTER}
          <div class="wx-root"><select id="wx-roots" aria-label="Folder"></select><button class="icon-btn sm" id="wx-close-root" title="Close this folder" hidden>✕</button></div>
          <div class="wx-tree" id="wx-tree" tabindex="0" role="tree" aria-label="Files"></div></section>
      </div>
      <div class="wx-status muted" id="wx-status"></div>
      <input type="file" id="wx-file" multiple hidden>
    </aside>
    <div class="wgut v" data-g="left" role="separator" aria-orientation="vertical" title="Drag to resize"></div>
    <section class="wm">
      <div class="we">${strip("editor", "Editor")}<div class="we-bar"><div class="we-tabs" id="we-tabs" role="tablist"></div><span class="we-acts">${winBtns("editor")}</span></div><div class="we-hosts" id="we-hosts"></div></div>
      <div class="wgut h" data-g="term" role="separator" aria-orientation="horizontal" title="Drag to resize"></div>
      <div class="wt" id="wt"></div>
    </section>
    <div class="wgut v" data-g="right" role="separator" aria-orientation="vertical" title="Drag to resize"></div>
    <aside class="wc" id="wc" aria-label="Claude Code"></aside>`;
  // Every window of the Workspace can be minimised (a slim strip) or maximised (fills the Workspace).
  function winBtns(pane) {
    return `<button class="icon-btn sm win-btn" data-win="min" data-pane="${pane}" title="Minimise">${svg("M7 5l5 5 5-5M4 15h16v4H4z")}</button>`
      + `<button class="icon-btn sm win-btn" data-win="max" data-pane="${pane}" title="Maximise / restore">${svg("M14 4h6v6M20 4l-7 7M10 20H4v-6M4 20l7-7")}</button>`;
  }
  function strip(pane, label, html) {
    return `<button class="win-strip" data-win="restore" data-pane="${pane}" title="Restore ${label}"><span>${html || label}</span></button>`;
  }
  W.winBtns = winBtns; W.winStrip = strip;
  function svg(d) { return `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${d}"/></svg>`; }
  W.svg = svg;

  function applySizes() {
    const narrow = matchMedia("(max-width: 900px)").matches;
    const { min } = S.win, max = S.win.max;
    S.el.classList.toggle("narrow", narrow);
    ["explorer", "editor", "terminal", "claude"].forEach((p) => { S.el.classList.toggle(`min-${p}`, min.has(p) && max !== p); S.el.classList.toggle(`max-${p}`, max === p); });
    S.el.classList.toggle("no-claude", S.noClaude);
    S.el.querySelectorAll(".win-btn[data-win=max]").forEach((b) => (b.innerHTML = svg(max === b.dataset.pane ? "M4 14h6v6M3 21l7-7M20 10h-6V4M21 3l-7 7" : "M14 4h6v6M20 4l-7 7M10 20H4v-6M4 20l7-7")));
    const termOff = S.termHidden || max === "editor";
    // Editor and terminal both minimised: the centre column shrinks to a strip and a side window takes the room.
    const midMin = !max && !narrow && min.has("editor") && (min.has("terminal") || termOff);
    const grow = midMin ? (!S.noClaude && !min.has("claude") ? "claude" : !min.has("explorer") ? "explorer" : "") : "";
    S.el.classList.toggle("mid-min", midMin);
    const left = grow === "explorer" ? "minmax(0, 1fr)" : min.has("explorer") ? "34px" : `${S.sizes.left}px`;
    const right = S.noClaude ? "0px" : grow === "claude" ? "minmax(0, 1fr)" : min.has("claude") ? "34px" : `${S.sizes.right}px`;
    const mid = grow ? "34px" : midMin ? "minmax(34px, 1fr)" : "minmax(240px, 1fr)";
    // Maximised: the other windows are display:none and leave the grid, so the one left fills a single column
    // (with five columns it would land in the first, 0px wide one).
    S.el.style.gridTemplateColumns = narrow ? "1fr" : max ? "minmax(0, 1fr)"
      : `${left} 5px ${mid} ${S.noClaude ? "0px" : "5px"} ${right}`;
    const rows = max === "terminal" ? "34px 0 minmax(0, 1fr)" : midMin ? (termOff ? "minmax(0, 1fr) 0 0" : "minmax(0, 1fr) 0 minmax(0, 1fr)")
      : min.has("editor") && termOff ? "24px 0 0" : termOff ? "minmax(0, 1fr) 0 0"
      : min.has("terminal") && min.has("editor") ? "24px 0 24px" : min.has("terminal") ? "minmax(0, 1fr) 0 24px"
      : min.has("editor") ? "24px 0 minmax(0, 1fr)" : `minmax(120px, 1fr) 5px ${S.sizes.term}px`;
    S.el.querySelector(".wm").style.gridTemplateRows = rows;
    S.el.querySelector(".wm").classList.toggle("term-hidden", termOff);
    W.fitTerminals?.();
    requestAnimationFrame(() => W.fitTerminals?.());  // once the new grid is laid out
  }
  W.win = (pane, action) => {
    const w = S.win;
    if (action === "max") { w.max = w.max === pane ? null : pane; w.min.delete(pane); }
    else if (action === "min") { if (w.max === pane) w.max = null; w.min.has(pane) ? w.min.delete(pane) : w.min.add(pane); if (pane === "terminal" && S.termHidden) W.toggleTerminal(true); }
    else { w.min.delete(pane); if (w.max && w.max !== pane) w.max = null; }
    store.set("ac.wsp.min", JSON.stringify([...w.min])); store.set("ac.wsp.max", w.max || "");
    applySizes();
  };
  W.toggleTerminal = (show = S.termHidden) => { S.termHidden = !show; store.set("ac.wsp.termHidden", S.termHidden ? "1" : ""); applySizes(); if (show) W.focusTerminal?.(); };
  W.maximizeTerminal = () => { S.termHidden = false; W.win("terminal", "max"); };
  // Side by side, the file windows may take most of the width; stacked, the column stays narrow.
  const sideBySide = () => S.orient === "h" && S.panes.length > 0;
  function wireGutters() {
    S.el.querySelectorAll(".wgut").forEach((g) => (g.onpointerdown = (e) => {
      e.preventDefault();
      g.setPointerCapture(e.pointerId);
      g.classList.add("active");
      const which = g.dataset.g, start = { x: e.clientX, y: e.clientY, ...S.sizes };
      const total = S.el.clientWidth, mh = S.el.querySelector(".wm").clientHeight;
      g.onpointermove = (ev) => {
        if (which === "left") S.sizes.left = Math.max(160, Math.min(total * (sideBySide() ? 0.7 : 0.4), start.left + ev.clientX - start.x));
        if (which === "right") S.sizes.right = Math.max(280, Math.min(total * 0.6, start.right - (ev.clientX - start.x)));
        if (which === "term") S.sizes.term = Math.max(80, Math.min(mh - 120, start.term - (ev.clientY - start.y)));
        applySizes();
      };
      g.onpointerup = () => { g.onpointermove = null; g.classList.remove("active"); store.set("ac.wsp.sizes", JSON.stringify(S.sizes)); };
    }));
    addEventListener("resize", () => S.el?.isConnected && applySizes());
  }

  // --- explorer windows ---------------------------------------------------------------------
  // The first window is the project's (its state is S itself); "+" opens more, each with its own folder, open
  // folders, selection and filter, and the same code: one clipboard, menus, keys and drag and drop across them all.
  // A list stays as it was while the server cannot be reached; only a real answer (gone, forbidden) empties it.
  const status = (t) => { const el = S.el?.querySelector("#wx-status"); if (el) el.textContent = t || ""; };
  const unreachable = (e) => !e?.status || e.status >= 502;
  const allPanes = () => [S, ...S.panes];
  const treeEl = (T) => T.box.querySelector(".wx-tree");
  const renderAll = () => allPanes().forEach((T) => renderTree(T));
  const refreshAll = () => Promise.all(allPanes().filter((T) => T.box?.isConnected).map((T) => refresh(T)));  // not while another page shows
  async function loadDir(T, path) {
    const d = await api(`/api/files/list?path=${encodeURIComponent(path)}`);
    const items = d.items.filter((it) => it.name !== ".git");
    T.dirs.set(path, items);
    items.forEach((it) => S.items.set(it.path, it));
    return items;
  }
  async function loadRoots() {
    S.roots = await api("/api/files/roots");
    if (!S.roots.some((r) => r.path === S.root)) S.root = (S.roots.find((r) => r.name !== "data") || S.roots[0])?.path || "";
    renderRoots();
    await refresh(S);
  }
  const rootOptions = (more) => S.roots.map((r) => `<option value="${esc(r.path)}">${esc(r.name)} · ${esc(r.path)}</option>`).join("") + `<option value="__open">${more}</option>`;
  function renderRoots() {
    const sel = S.el.querySelector("#wx-roots");
    sel.innerHTML = rootOptions("＋ Open folder…");
    sel.value = S.root;
    S.el.querySelector("#wx-close-root").hidden = !S.roots.find((r) => r.path === S.root)?.opened;
    S.panes.forEach(renderPaneRoots);
  }
  function renderPaneRoots(T) {
    const sel = T.box.querySelector("select");
    sel.innerHTML = rootOptions("＋ Open folder from / …");
    sel.value = T.root;
  }
  async function setRoot(path) {
    S.root = path; store.set("ac.wsp.root", path); S.sel.clear(); S.anchor = null;
    renderRoots(); await refresh(S); W.onRoot?.(path);
  }
  // Show `path` in the explorer, opening it as a root first if needed (resuming a session in its folder).
  W.openRoot = async (path) => {
    if (!S.roots.some((r) => r.path === path)) {
      const r = await api("/api/files/roots", { method: "POST", body: { path } });
      S.roots = await api("/api/files/roots"); path = r.path;
    }
    await setRoot(path);
  };
  async function openFolder() {
    const dir = await pickFolder(S.root || "");
    if (!dir) { renderRoots(); return; }
    try { const r = await api("/api/files/roots", { method: "POST", body: { path: dir } }); S.roots = await api("/api/files/roots"); await setRoot(r.path); } catch (e) { toast(e.message); renderRoots(); }
  }
  const saveState = (T) => (T === S ? saveOpen() : savePanes());
  // Only folders whose whole chain is open are listed (an open folder inside a closed one is not visible).
  const shown = (T, p) => { for (let d = parentOf(p); d.length > T.root.length; d = parentOf(d)) if (!T.open.has(d)) return false; return true; };
  async function refresh(T = S) {
    if (!T.root) return renderTree(T);
    const dirs = [T.root, ...[...T.open].filter((p) => p.startsWith(T.root + "/") && shown(T, p))];
    const fresh = new Map(T.dirs);
    let rootOk = false, lost = false;
    api(`/api/files/git-status?path=${encodeURIComponent(T.root)}`).then((g) => {
      if (JSON.stringify(g) !== JSON.stringify(T.git)) { T.git = g; renderTree(T); }
    }).catch(() => {});
    await Promise.all(dirs.map(async (d) => {
      try { fresh.set(d, (await api(`/api/files/list?path=${encodeURIComponent(d)}`)).items.filter((it) => it.name !== ".git")); if (d === T.root) rootOk = true; }
      catch (e) { if (unreachable(e)) { lost = true; return; } fresh.delete(d); if (d !== T.root) T.open.delete(d); }
    }));
    if (lost) { S.offline = true; S.idle = 0; return; }  // the last list stays as it is, without a notice; the next refresh (10 s) retries
    S.offline = false;
    T.missing = !rootOk;
    if (!rootOk) { T.dirs.clear(); return renderTree(T); }
    const before = JSON.stringify([...T.dirs.entries()].filter(([k]) => fresh.has(k)).map(([k, v]) => [k, v.map((i) => i.name + i.size + i.modified)]));
    T.dirs = fresh;
    fresh.forEach((items) => items.forEach((it) => S.items.set(it.path, it)));
    const after = JSON.stringify([...T.dirs.entries()].map(([k, v]) => [k, v.map((i) => i.name + i.size + i.modified)]));
    if (T === S) S.idle = before === after ? (S.idle || 0) + 1 : 0;
    if (before !== after || !treeEl(T).querySelector(".tr")) renderTree(T);
  }
  W.refreshExplorer = refreshAll;

  function visibleRows(T) {
    const rows = [];
    const walk = (dir, depth) => {
      for (const it of T.dirs.get(dir) || []) {
        rows.push({ it, depth });
        if (it.dir && T.open.has(it.path) && T.dirs.has(it.path)) walk(it.path, depth + 1);
      }
    };
    if (T.root) walk(T.root, 0);
    return rows;
  }
  function renderTree(T = S) {
    const tree = treeEl(T);
    if (!T.root) { tree.innerHTML = T === S ? `<div class="wx-empty"><p class="muted">No folder open.</p><button class="btn primary" data-x="openfolder">Open folder</button></div>` : '<div class="wx-empty muted">Choose a folder above.</div>'; return; }
    if (T.missing) { tree.innerHTML = '<div class="wx-empty muted">This folder is not available.</div>'; return; }
    let rows = visibleRows(T);
    const q = T.filter.trim().toLowerCase();
    if (q) {  // keep matching rows and the folders that lead to them
      const keep = new Set();
      rows.forEach(({ it }) => { if (it.name.toLowerCase().includes(q)) { keep.add(it.path); for (let d = parentOf(it.path); d.length > T.root.length; d = parentOf(d)) keep.add(d); } });
      rows = rows.filter(({ it }) => keep.has(it.path));
    }
    const cut = new Set(S.clip?.cut ? S.clip.paths : []);
    const G = { M: "modified", A: "added", D: "deleted", U: "untracked", R: "renamed", C: "copied", dot: "contains changes" };
    tree.innerHTML = rows.map(({ it, depth }) => {
      const open = it.dir && T.open.has(it.path), g = T.git[it.path];
      return `<div class="tr${T.sel.has(it.path) ? " sel" : ""}${cut.has(it.path) ? " cut" : ""}${S.active === it.path ? " act" : ""}${g ? ` g-${g}` : ""}" role="treeitem" data-path="${esc(it.path)}"${it.dir ? ` data-dir="1" aria-expanded="${open}"` : ""} draggable="true" style="--d:${depth}"
        title="${esc(it.name)}${it.dir ? "" : ` · ${W.size(it.size)}`}${g ? ` · ${G[g]}` : ""}"><span class="chev${open ? " open" : ""}">${it.dir ? "›" : ""}</span>${W.fileIcon(it.name, it.dir, 16, open)}<span class="tn">${esc(it.name)}</span>${g ? `<span class="gd">${g === "dot" ? "●" : g}</span>` : ""}</div>`;
    }).join("") || `<div class="wx-empty muted">${q ? "No file matches the filter." : "Empty folder. Drop files here to upload."}</div>`;
  }

  async function toggleDir(T, path, force) {
    const open = force ?? !T.open.has(path);
    // Unreachable: the folder stays open and the next refresh lists it.
    if (open) { T.open.add(path); if (!T.dirs.has(path)) { try { await loadDir(T, path); } catch (e) { if (!unreachable(e)) { toast(e.message); T.open.delete(path); } } } }
    else T.open.delete(path);
    saveState(T); renderTree(T);
  }
  async function reveal(T, path) {
    if (!T.root || !path.startsWith(T.root + "/")) return;
    const parts = path.slice(T.root.length + 1).split("/");
    let cur = T.root;
    for (const part of parts.slice(0, -1)) { cur += "/" + part; T.open.add(cur); if (!T.dirs.has(cur)) { try { await loadDir(T, cur); } catch { break; } } }
    saveState(T); renderTree(T);
    treeEl(T).querySelector(`.tr[data-path="${CSS.escape(path)}"]`)?.scrollIntoView({ block: "nearest" });
  }

  const targetDir = (T, row) => (row ? (row.dataset.dir ? row.dataset.path : parentOf(row.dataset.path)) : T.root);
  const selection = (T, row) => (row && T.sel.has(row.dataset.path) ? [...T.sel] : row ? [row.dataset.path] : [...T.sel]);

  // The first window's own controls: its head buttons, the folder select and the upload input.
  function wireExplorer() {
    S.el.querySelector(".wx-head").addEventListener("click", (e) => { const b = e.target.closest("[data-x]"); if (b) explorerAction(S, b.dataset.x); });
    S.el.querySelector(".wx-main").addEventListener("click", (e) => { const b = e.target.closest("[data-x]"); if (b) explorerAction(S, b.dataset.x); });
    S.el.querySelector("#wx-roots").onchange = (e) => (e.target.value === "__open" ? openFolder() : setRoot(e.target.value));
    S.el.querySelector("#wx-close-root").onclick = async () => {
      if (!confirm(`Close ${S.root} in the explorer? Nothing on disk changes.`)) return;
      await api(`/api/files/roots?path=${encodeURIComponent(S.root)}`, { method: "DELETE" }).catch((e) => toast(e.message));
      S.root = ""; await loadRoots();
    };
    S.el.querySelector("#wx-file").onchange = async (e) => { const T = S.uploadPane || S; await uploadFiles(T, [...e.target.files], S.uploadDir || T.root); e.target.value = ""; };
    wireTree(S);
    renderOrient();
  }
  // One file window's list: the same for the first window and every added one.
  function wireTree(T) {
    const tree = treeEl(T);
    tree.addEventListener("focusin", () => (S.focus = T));
    tree.onclick = (e) => {
      if (e.target.closest("[data-x]")) return;  // "Open folder" in an empty window
      const row = e.target.closest(".tr");
      if (!row) { T.sel.clear(); renderTree(T); return; }
      const path = row.dataset.path;
      const rows = [...tree.querySelectorAll(".tr")].map((r) => r.dataset.path);
      if (e.shiftKey && T.anchor) {
        const [a, b] = [rows.indexOf(T.anchor), rows.indexOf(path)].sort((x, y) => x - y);
        if (!(e.ctrlKey || e.metaKey)) T.sel.clear();
        rows.slice(a, b + 1).forEach((p) => T.sel.add(p));
      } else if (e.ctrlKey || e.metaKey) { T.sel.has(path) ? T.sel.delete(path) : T.sel.add(path); T.anchor = path; }
      else {
        T.sel.clear(); T.sel.add(path); T.anchor = path;
        if (row.dataset.dir) return toggleDir(T, path);
        W.openPath(path);
      }
      renderTree(T);
    };
    tree.oncontextmenu = (e) => {
      e.preventDefault();
      const row = e.target.closest(".tr");
      if (row && !T.sel.has(row.dataset.path)) { T.sel.clear(); T.sel.add(row.dataset.path); T.anchor = row.dataset.path; renderTree(T); }
      if (!row) { T.sel.clear(); renderTree(T); }
      W.menu(e.clientX, e.clientY, menuFor(T, row ? tree.querySelector(`.tr[data-path="${CSS.escape(row.dataset.path)}"]`) : null));
    };
    const fq = T.box.querySelector(".wx-filter input");
    fq.oninput = () => { T.filter = fq.value; renderTree(T); };
    fq.onkeydown = (e) => { if (e.key === "Escape") { e.preventDefault(); toggleFilter(T, false); tree.focus(); } };
    tree.onkeydown = (e) => {
      const sel = [...T.sel], one = sel.length === 1 ? sel[0] : null, mod = e.ctrlKey || e.metaKey;
      if (mod && e.key.toLowerCase() === "f") { e.preventDefault(); toggleFilter(T, true); return; }
      if (e.key === "Delete" && sel.length) { e.preventDefault(); act.delete(T, sel); }
      else if (e.key === "F2" && one) { e.preventDefault(); act.rename(T, one); }
      else if (e.key === "Enter" && one) { e.preventDefault(); S.items.get(one)?.dir ? toggleDir(T, one) : W.openPath(one); }
      else if (mod && e.key.toLowerCase() === "c" && sel.length) { e.preventDefault(); act.clip(sel, false); }
      else if (mod && e.key.toLowerCase() === "x" && sel.length) { e.preventDefault(); act.clip(sel, true); }
      else if (mod && e.key.toLowerCase() === "v" && S.clip) { e.preventDefault(); act.paste(T, one && S.items.get(one)?.dir ? one : one ? parentOf(one) : T.root); }
      else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const rows = [...tree.querySelectorAll(".tr")].map((r) => r.dataset.path);
        const i = Math.max(-1, rows.indexOf(T.anchor)) + (e.key === "ArrowDown" ? 1 : -1);
        if (rows[i]) { T.sel.clear(); T.sel.add(rows[i]); T.anchor = rows[i]; renderTree(T); tree.querySelector(".tr.sel")?.scrollIntoView({ block: "nearest" }); }
      } else if ((e.key === "ArrowRight" || e.key === "ArrowLeft") && one && S.items.get(one)?.dir) { e.preventDefault(); toggleDir(T, one, e.key === "ArrowRight"); }
    };

    // Drag and drop: rows move within a window and copy into another one (Ctrl/Alt copies, Shift moves);
    // files and folders from the computer upload.
    tree.ondragstart = (e) => {
      const row = e.target.closest(".tr");
      if (!row) return;
      const paths = selection(T, row);
      S.dragFrom = T;
      e.dataTransfer.setData("text/x-paths", JSON.stringify(paths));
      e.dataTransfer.setData("text/plain", paths.join("\n"));
      e.dataTransfer.effectAllowed = "copyMove";
    };
    tree.ondragend = () => (S.dragFrom = null);
    const copying = (e) => e.ctrlKey || e.altKey || (!!S.dragFrom && S.dragFrom !== T && !e.shiftKey);
    let hover = null;
    const mark = (el) => { if (hover !== el) { hover?.classList.remove("drop"); hover = el; hover?.classList.add("drop"); } };
    tree.ondragover = (e) => {
      e.preventDefault();
      const dir = targetDir(T, e.target.closest(".tr"));
      const internal = e.dataTransfer.types.includes("text/x-paths");
      e.dataTransfer.dropEffect = internal ? (copying(e) ? "copy" : "move") : "copy";
      mark(dir === T.root ? tree : tree.querySelector(`.tr[data-path="${CSS.escape(dir)}"]`) || tree);
    };
    tree.ondragleave = (e) => { if (!tree.contains(e.relatedTarget)) mark(null); };
    tree.ondrop = async (e) => {
      e.preventDefault(); mark(null);
      const dir = targetDir(T, e.target.closest(".tr"));
      const raw = e.dataTransfer.getData("text/x-paths");
      const copy = copying(e);
      S.dragFrom = null;
      if (raw) return moveTo(T, JSON.parse(raw), dir, copy);
      // Entries must be taken before the first await: the DataTransfer empties after the event.
      const entries = [...(e.dataTransfer.items || [])].map((i) => (i.kind === "file" ? i.webkitGetAsEntry?.() : null)).filter(Boolean);
      const filesList = [...e.dataTransfer.files];
      if (entries.length) await uploadEntries(T, entries, dir);
      else if (filesList.length) await uploadFiles(T, filesList, dir);
    };
  }

  function explorerAction(T, x) {
    const one = T.sel.size === 1 ? [...T.sel][0] : null;
    const dir = one ? (S.items.get(one)?.dir ? one : parentOf(one)) : T.root;
    if (x === "openfolder") return openFolder();
    if (x === "split") return addPane();
    if (x === "orient") return toggleOrient();
    if (x === "close") return closePane(T);
    if (!T.root) return toast("Open a folder first");
    if (x === "newfile") return act.create(T, dir, false);
    if (x === "newdir") return act.create(T, dir, true);
    if (x === "upload") return act.upload(T, dir);
    if (x === "refresh") return refresh(T).then(() => S.offline || toast("Refreshed"));
    if (x === "collapse") { T.open.clear(); saveState(T); renderTree(T); }
    if (x === "filter") return toggleFilter(T);
  }
  function toggleFilter(T, show) {
    const box = T.box.querySelector(".wx-filter"), on = show ?? box.hidden;
    box.hidden = !on;
    if (on) box.querySelector("input").focus(); else { T.filter = ""; box.querySelector("input").value = ""; renderTree(T); }
  }

  function menuFor(T, row) {
    const sel = row ? selection(T, row) : [];
    const path = row?.dataset.path, isDir = !!row?.dataset.dir, many = sel.length > 1;
    const dir = targetDir(T, row);
    const lower = (path || "").toLowerCase();
    const archive = /\.(zip|tar|tgz|tar\.(gz|bz2|xz)|tbz2|txz)$/.test(lower);
    const runnable = { py: "python3", sh: "bash", js: "node", mjs: "node", rb: "ruby", go: "go run", php: "php", pl: "perl", ts: "npx tsx" }[lower.split(".").pop()];
    const q = (p) => `'${p.replace(/'/g, "'\\''")}'`;
    return [
      row && !isDir && !many && { label: "Open", key: "Enter", act: () => W.openPath(path) },
      row && !isDir && !many && runnable && { label: `Run in terminal (${runnable})`, act: () => W.sendToTerminal(`cd ${q(parentOf(path))} && ${runnable} ${q(baseName(path))}\r`) },
      (!row || isDir) && { label: "New file…", act: () => act.create(T, dir, false) },
      (!row || isDir) && { label: "New folder…", act: () => act.create(T, dir, true) },
      (!row || isDir) && { label: "Upload files here…", act: () => act.upload(T, dir) },
      { label: "Open in terminal", act: () => W.newTerminal(dir) },
      row && { label: many ? `Ask Claude about ${sel.length} items` : "Ask Claude about this", act: () => W.claudeMention(sel) },
      "-",
      row && { label: "Cut", key: "Ctrl+X", act: () => act.clip(sel, true) },
      row && { label: "Copy", key: "Ctrl+C", act: () => act.clip(sel, false) },
      { label: S.clip ? `Paste ${S.clip.paths.length} item(s)` : "Paste", key: "Ctrl+V", disabled: !S.clip, act: () => act.paste(T, dir) },
      row && !many && { label: "Duplicate", act: () => act.duplicate(path) },
      "-",
      row && { label: many ? "Copy paths" : "Copy path", act: () => W.copyText(sel.join("\n"), "Path copied") },
      row && { label: many ? "Copy relative paths" : "Copy relative path", act: () => W.copyText(sel.map(W.rel).join("\n"), "Path copied") },
      !row && { label: "Copy folder path", act: () => W.copyText(T.root, "Path copied") },
      "-",
      row && { label: many || isDir ? "Download as .zip" : "Download", act: () => act.download(sel) },
      row && { label: "Compress to .zip", act: () => act.compress(T, sel) },
      row && !many && archive && { label: "Extract here", act: () => act.extract(T, path) },
      "-",
      row && !many && { label: "Rename…", key: "F2", act: () => act.rename(T, path) },
      row && { label: many ? `Delete ${sel.length} items` : "Delete", key: "Del", danger: true, act: () => act.delete(T, sel) },
      !row && { label: "Refresh", act: () => refresh(T) },
    ].filter((x, i, arr) => x && !(x === "-" && (i === 0 || arr.slice(0, i).filter(Boolean).pop() === "-")));
  }

  // VS Code-style inline input in the tree (new file, new folder, rename): Enter accepts, Esc cancels.
  function inlineInput(T, { depth, after, value = "", folder = false, onDone }) {
    const tree = treeEl(T);
    const row = document.createElement("div");
    row.className = "tr tr-input"; row.style.setProperty("--d", depth);
    row.innerHTML = `<span class="chev"></span>${W.fileIcon(value || (folder ? "folder" : "file"), folder, 16)}<input class="tr-edit" spellcheck="false" autocomplete="off" aria-label="${folder ? "Folder" : "File"} name">`;
    if (after) after.after(row); else tree.prepend(row);
    const input = row.querySelector("input");
    input.value = value;
    const dot = value.lastIndexOf(".");
    input.focus(); input.setSelectionRange(0, dot > 0 && !folder ? dot : value.length);
    let done = false;
    const finish = (ok) => { if (done) return; done = true; const v = input.value.trim(); row.remove(); if (ok && v && v !== value) onDone(v); else renderTree(T); };
    input.onkeydown = (e) => { e.stopPropagation(); if (e.key === "Enter") { e.preventDefault(); finish(true); } else if (e.key === "Escape") { e.preventDefault(); finish(false); } };
    input.onblur = () => finish(true);
    input.onclick = (e) => e.stopPropagation();
  }
  const depthOf = (T, path) => (path === T.root ? -1 : path.slice(T.root.length + 1).split("/").length - 1);
  // Every change is shown in every window (two windows may list the same folder).
  const act = {
    async create(T, dir, folder) {
      if (dir !== T.root && !T.open.has(dir)) await toggleDir(T, dir, true);
      const dirRow = dir === T.root ? null : treeEl(T).querySelector(`.tr[data-path="${CSS.escape(dir)}"]`);
      inlineInput(T, { depth: depthOf(T, dir) + 1, after: dirRow, folder, onDone: (name) => act.createNamed(T, dir, name, folder) });
    },
    async createNamed(T, dir, name, folder) {
      try {
        const r = await api("/api/files/create", { method: "POST", body: { path: `${dir}/${name}`, folder } });
        if (dir !== T.root) T.open.add(dir);
        await refreshAll(); await reveal(T, r.path);
        if (!folder) W.openPath(r.path);
      } catch (e) { toast(e.message); }
    },
    upload(T, dir) { S.uploadDir = dir; S.uploadPane = T; S.el.querySelector("#wx-file").click(); },
    async rename(T, path) {
      const row = treeEl(T).querySelector(`.tr[data-path="${CSS.escape(path)}"]`);
      if (row) { row.hidden = true; inlineInput(T, { depth: depthOf(T, path), after: row, value: baseName(path), folder: !!row.dataset.dir, onDone: (n) => act.renameTo(T, path, n) }); return; }
      const name = prompt("New name", baseName(path));
      if (name && name !== baseName(path)) act.renameTo(T, path, name);
    },
    async renameTo(T, path, name) {
      try { const r = await api("/api/files/move", { method: "POST", body: { src: path, dest: `${parentOf(path)}/${name}` } }); retarget(path, r.path); T.sel.clear(); T.sel.add(r.path); await refreshAll(); } catch (e) { toast(e.message); }
    },
    async duplicate(path) {
      const name = baseName(path), dot = name.lastIndexOf(".") > 0 ? name.lastIndexOf(".") : name.length;
      const siblings = new Set(allPanes().flatMap((T) => (T.dirs.get(parentOf(path)) || []).map((i) => i.name)));
      let n = 1, cand;
      do { cand = `${name.slice(0, dot)} copy${n > 1 ? " " + n : ""}${name.slice(dot)}`; n++; } while (siblings.has(cand));
      try { await api("/api/files/move", { method: "POST", body: { src: path, dest: `${parentOf(path)}/${cand}`, copy: true } }); await refreshAll(); } catch (e) { toast(e.message); }
    },
    clip(paths, cut) { S.clip = { paths, cut }; toast(`${paths.length} item(s) ${cut ? "cut" : "copied"}`); renderAll(); },
    async paste(T, dir) {
      if (!S.clip) return;
      await moveTo(T, S.clip.paths, dir, !S.clip.cut);
      if (S.clip.cut) S.clip = null;
      renderAll();
    },
    download(paths) {
      const one = paths.length === 1 && !S.items.get(paths[0])?.dir;
      const a = document.createElement("a");
      a.href = one ? W.rawUrl(paths[0], true) : `/api/files/archive?${paths.map((p) => `paths=${encodeURIComponent(p)}`).join("&")}`;
      a.download = ""; document.body.append(a); a.click(); a.remove();
    },
    async compress(T, paths) {
      status("Compressing…");
      try { const r = await api("/api/files/compress", { method: "POST", body: { paths } }); await refreshAll(); await reveal(T, r.path); toast(`Created ${baseName(r.path)} (${W.size(r.size)})`); } catch (e) { toast(e.message); }
      status("");
    },
    async extract(T, path) {
      status("Extracting…");
      try { const r = await api("/api/files/extract", { method: "POST", body: { path } }); T.open.add(r.path); await refreshAll(); await reveal(T, r.path); toast(`Extracted to ${baseName(r.path)}`); } catch (e) { toast(e.message); }
      status("");
    },
    async delete(T, paths) {
      if (!confirm(paths.length === 1 ? `Delete ${baseName(paths[0])}?${S.items.get(paths[0])?.dir ? " The folder is deleted with everything in it." : ""}` : `Delete ${paths.length} items? Folders are deleted with their contents.`)) return;
      try {
        await api("/api/files/delete", { method: "POST", body: { paths } });
        paths.forEach((p) => S.tabs.filter((t) => t.path === p || t.path.startsWith(p + "/")).forEach((t) => closeTab(t, true)));
        allPanes().forEach((x) => x.sel.clear()); await refreshAll();
      } catch (e) { toast(e.message); }
    },
  };

  async function moveTo(T, paths, dest, copy) {
    let done = 0;
    for (const p of paths) {
      if (p === dest || (!copy && parentOf(p) === dest) || dest.startsWith(p + "/")) continue;
      try { const r = await api("/api/files/move", { method: "POST", body: { src: p, dest, copy } }); if (!copy) retarget(p, r.path); done++; } catch (e) { toast(e.message); }
    }
    if (done) { if (dest !== T.root) T.open.add(dest); saveState(T); allPanes().forEach((x) => x.sel.clear()); await refreshAll(); toast(`${copy ? "Copied" : "Moved"} ${done} item(s)`); }
  }
  function retarget(from, to) {
    for (const t of S.tabs) if (t.path === from || t.path.startsWith(from + "/")) { t.path = to + t.path.slice(from.length); t.name = baseName(t.path); }
    if (S.active && (S.active === from || S.active.startsWith(from + "/"))) S.active = to + S.active.slice(from.length);
    renderTabs();
  }

  async function uploadOne(file, dir) {
    const fd = new FormData(); fd.append("file", file);
    const r = await fetch(`/api/files/upload?folder=${encodeURIComponent(dir)}`, { method: "POST", body: fd, credentials: "same-origin" });
    if (!r.ok) throw new Error(`${file.name}: ${(await r.json().catch(() => ({}))).detail || r.statusText}`);
  }
  async function uploadFiles(T, list, dir) {
    if (!list.length) return;
    const existing = new Set((T.dirs.get(dir) || []).map((i) => i.name));
    const clash = list.filter((f) => existing.has(f.name));
    if (clash.length && !confirm(`Replace ${clash.length} existing file(s) in ${baseName(dir)}? (${clash.slice(0, 3).map((f) => f.name).join(", ")}${clash.length > 3 ? "…" : ""})`)) return;
    let n = 0;
    for (const f of list) {
      status(`Uploading ${++n}/${list.length}: ${f.name}`);
      try { await uploadOne(f, dir); } catch (e) { toast(e.message); }
    }
    status(""); if (dir !== T.root) T.open.add(dir);
    await refreshAll(); toast(`Uploaded ${list.length} file(s)`);
  }
  async function uploadEntries(T, entries, dir) {
    let n = 0;
    const walk = async (entry, into) => {
      if (entry.isFile) {
        const f = await new Promise((ok, fail) => entry.file(ok, fail));
        status(`Uploading ${++n}: ${entry.fullPath}`);
        try { await uploadOne(f, into); } catch (e) { toast(e.message); }
      } else if (entry.isDirectory) {
        const sub = `${into}/${entry.name}`;
        await api("/api/files/create", { method: "POST", body: { path: sub, folder: true } }).catch(() => {});
        const reader = entry.createReader();
        let batch;
        do { batch = await new Promise((ok, fail) => reader.readEntries(ok, fail)); for (const e of batch) await walk(e, sub); } while (batch.length);
      }
    };
    const existing = new Set((T.dirs.get(dir) || []).map((i) => i.name));
    const clash = entries.filter((e) => existing.has(e.name));
    if (clash.length && !confirm(`Replace or merge ${clash.length} existing item(s) in ${baseName(dir)}?`)) return;
    for (const e of entries) await walk(e, dir);
    status(""); if (dir !== T.root) T.open.add(dir);
    await refreshAll(); toast(`Uploaded ${n} file(s)`);
  }

  // --- more windows ("+"), stacked or side by side ------------------------------------------
  // An added window chooses its folder from the registered roots, or any folder from "/" through the picker.
  function addPane(root, open = []) {
    const T = { id: Math.random().toString(36).slice(2, 8), root: root || S.root, dirs: new Map(), open: new Set(open), sel: new Set(), anchor: null, filter: "", git: {}, box: null };
    S.panes.push(T); buildPane(T); savePanes(); renderOrient(); renderTree(T); refresh(T);
    if (sideBySide()) widenForSide();
  }
  const savePanes = () => store.set("ac.wsp.panes", JSON.stringify(S.panes.map((T) => ({ root: T.root, open: [...T.open].slice(-200) }))));
  function buildPane(T) {
    const box = document.createElement("section");
    box.className = "wx-pane"; box.dataset.pane = T.id;
    box.innerHTML = `<div class="wx-ph"><select aria-label="Folder"></select></div><div class="wx-ph-btns">${PANE_BTNS}<span class="grow"></span>
        <button class="icon-btn sm" data-x="close" title="Close this window">${svg("M6 6l12 12M18 6L6 18")}</button></div>${FILTER}
      <div class="wx-tree" tabindex="0" role="tree" aria-label="Files"></div>`;
    S.el.querySelector("#wx-split").append(box);
    T.box = box;
    box.querySelector(".wx-ph-btns").addEventListener("click", (e) => { const b = e.target.closest("[data-x]"); if (b) explorerAction(T, b.dataset.x); });
    box.querySelector("select").onchange = async (e) => {
      let root = e.target.value;
      if (root === "__open") {
        const dir = await pickFolder("/");
        root = "";
        if (dir) { try { const r = await api("/api/files/roots", { method: "POST", body: { path: dir } }); S.roots = await api("/api/files/roots"); root = r.path; } catch (x) { toast(x.message); } }
      }
      if (root) Object.assign(T, { root, missing: false, anchor: null, git: {} });
      if (root) { T.open.clear(); T.dirs.clear(); T.sel.clear(); }
      renderRoots(); savePanes(); renderTree(T); refresh(T);
    };
    renderPaneRoots(T); wireTree(T);
  }
  function closePane(T) {
    if (T === S) return;
    S.panes = S.panes.filter((x) => x !== T); T.box.remove(); savePanes(); renderOrient();
    if (S.focus === T) S.focus = S;
    applySizes();
  }
  // "v": one window above the other; "h": side by side.
  function renderOrient() {
    const b = S.el.querySelector("#wx-orient"), side = S.orient === "h";
    S.el.querySelector(".wx").classList.toggle("side", side);
    b.hidden = !S.panes.length;
    b.title = side ? "Windows side by side: show them one above the other" : "Windows one above the other: show them side by side";
    b.innerHTML = svg(side ? "M3 4h18v16H3zM12 4v16" : "M3 4h18v16H3zM3 12h18");
  }
  function widenForSide() {
    const total = S.el.clientWidth || innerWidth, want = Math.min(total * 0.6, 230 * (S.panes.length + 1));
    if (S.sizes.left < want) { S.sizes.left = Math.round(want); store.set("ac.wsp.sizes", JSON.stringify(S.sizes)); }
    applySizes();
  }
  function toggleOrient() {
    S.orient = S.orient === "h" ? "v" : "h"; store.set("ac.wsp.orient", S.orient);
    renderOrient();
    if (sideBySide()) widenForSide();
    else { S.sizes.left = Math.min(S.sizes.left, Math.round((S.el.clientWidth || innerWidth) * 0.4)); applySizes(); }
  }

  // --- editor tabs ------------------------------------------------------------------------
  const viewActions = {
    changed: () => renderTabs(),
    async save(tab) {
      try {
        await api("/api/files/write", { method: "PUT", body: { path: tab.path, text: tab.text } });
        tab.dirty = false; if (tab.file) tab.file.text = tab.text;
        toast(`Saved ${tab.name}`); renderTabs(); W.renderView(tab, tab.host, viewActions); refreshAll();
      } catch (e) { toast(e.message); }
    },
  };
  W.openPath = async (path, line) => {
    const item = S.items.get(path);
    if (item?.dir) { await reveal(S, path + "/x"); return toggleDir(S, path, true); }
    let tab = S.tabs.find((t) => t.path === path);
    if (!tab) {
      const name = baseName(path), kind = item?.kind && item.kind !== "file" ? item.kind : W.kindOf(name);
      tab = { path, name, kind, mode: W.defaultMode(kind) };
      tab.host = document.createElement("div"); tab.host.className = "we-host";
      S.el.querySelector("#we-hosts").append(tab.host);
      S.tabs.push(tab);
      W.renderView(tab, tab.host, viewActions);
    }
    activate(tab); saveTabs();
    if (line) setTimeout(() => tab.host.querySelector(".codev")?.scrollTo({ top: (line - 3) * 19.5 }), 200);
  };
  const saveTabs = () => store.set("ac.wsp.tabs", JSON.stringify({ open: S.tabs.map((t) => t.path), active: S.active }));
  function activate(tab) {
    S.active = tab?.path || null;
    S.tabs.forEach((t) => (t.host.hidden = t !== tab));
    S.el.querySelector("#we-hosts").classList.toggle("empty", !tab);
    renderTabs();
    S.el.querySelectorAll(".tr.act").forEach((r) => r.classList.remove("act"));
    if (tab) S.el.querySelector(`.tr[data-path="${CSS.escape(tab.path)}"]`)?.classList.add("act");
  }
  function closeTab(tab, force) {
    if (!force && tab.dirty && !confirm(`${tab.name} has unsaved changes. Close it anyway?`)) return;
    const i = S.tabs.indexOf(tab);
    S.tabs.splice(i, 1); tab.host.remove(); tab.pdf?.destroy?.(); saveTabs();
    if (S.active === tab.path) activate(S.tabs[Math.min(i, S.tabs.length - 1)] || null); else renderTabs();
  }
  function renderTabs() {
    const bar = S.el.querySelector("#we-tabs");
    bar.innerHTML = S.tabs.map((t, i) => `<div class="wtab${t.path === S.active ? " on" : ""}" role="tab" aria-selected="${t.path === S.active}" data-i="${i}" title="${esc(t.path)}">${W.fileIcon(t.name, false, 14)}<span>${esc(t.name)}</span><button class="wtab-x${t.dirty ? " dirty" : ""}" data-close="${i}" aria-label="Close ${esc(t.name)}">${t.dirty ? "●" : "×"}</button></div>`).join("");
    bar.querySelectorAll(".wtab").forEach((el) => {
      const t = S.tabs[Number(el.dataset.i)];
      el.onclick = (e) => { if (e.target.closest("[data-close]")) closeTab(t); else activate(t); };
      el.onauxclick = (e) => { if (e.button === 1) { e.preventDefault(); closeTab(t); } };
      el.oncontextmenu = (e) => { e.preventDefault(); W.menu(e.clientX, e.clientY, [
        { label: "Close", act: () => closeTab(t) },
        { label: "Close others", act: () => S.tabs.filter((x) => x !== t).forEach((x) => closeTab(x)) },
        { label: "Close all", act: () => [...S.tabs].forEach((x) => closeTab(x)) }, "-",
        { label: "Copy path", act: () => W.copyText(t.path, "Path copied") },
        { label: "Reveal in explorer", act: () => reveal(S, t.path) },
        { label: "Ask Claude about this", act: () => W.claudeMention([t.path]) }]); };
    });
    if (!S.tabs.length) S.el.querySelector("#we-hosts").innerHTML = welcome();
    else S.el.querySelector(".we-welcome")?.remove();
  }
  function welcome() {
    return `<div class="we-welcome"><div class="we-logo">${icon("files", 44)}</div><h2>Workspace</h2>
      <ul class="muted"><li>Click a file in the explorer to view it: code, Markdown, PDF, Word, images, CSV, JSON…</li>
      <li>Drag files or folders from your computer onto the explorer to upload them</li><li>Right-click for copy, move, rename, compress, download, extract…</li>
      <li>Drop explorer items onto the terminal or the Claude box to insert their paths</li>
      <li><kbd>Ctrl</kbd>+<kbd>\`</kbd> terminal · <kbd>Ctrl</kbd>+<kbd>L</kbd> Claude · <kbd>Ctrl</kbd>+<kbd>S</kbd> save while editing</li></ul></div>`;
  }
  // Reload tabs whose files changed on disk (after a Claude run, for instance).
  W.dirtyTabs = () => S.tabs.some((t) => t.dirty);
  W.reloadCleanTabs = () => S.tabs.forEach((t) => { if (!t.dirty) { t.last = t.file; t.file = null; t.text = null; t.pdf = null; t.stamp = Date.now(); W.renderView(t, t.host, viewActions); } });

  // --- view ------------------------------------------------------------------------------
  async function viewWorkspace(quiet, noClaude = false) {
    $("#view").classList.add("flush");
    if (S.el) {
      S.noClaude = noClaude;
      if (S.el.parentNode !== $("#view")) { $("#view").replaceChildren(S.el); refreshAll(); }
      applySizes();
      return;
    }
    S.noClaude = noClaude;
    S.el = document.createElement("div");
    S.el.className = "wsp";
    S.el.innerHTML = LAYOUT;
    $("#view").replaceChildren(S.el);
    S.box = S.el.querySelector(".wx-main");
    applySizes(); wireGutters(); wireExplorer(); renderTabs();
    W.initTerminals(S.el.querySelector("#wt"));
    W.initClaude(S.el.querySelector("#wc")).then(() => {
      const head = S.el.querySelector(".wc-head");
      head?.insertAdjacentHTML("beforeend", winBtns("claude"));
      S.el.querySelector("#wc").insertAdjacentHTML("afterbegin", strip("claude", "Claude Code"));
      applySizes();
    });
    S.el.addEventListener("click", (e) => { const b = e.target.closest("[data-win]"); if (b) { e.stopPropagation(); W.win(b.dataset.pane, b.dataset.win); } });
    await loadRoots();
    for (const x of readList("ac.wsp.panes")) {  // a window's folder and open folders; before: only the folder
      const root = typeof x === "string" ? x : x?.root, open = Array.isArray(x?.open) ? x.open.filter((p) => typeof p === "string") : [];
      if (S.roots.some((r) => r.path === root)) addPane(root, open);
    }
    const saved = read("ac.wsp.tabs", { open: [] });
    for (const p of saved.open || []) await W.openPath(p);
    if (saved.active && S.tabs.some((t) => t.path === saved.active)) activate(S.tabs.find((t) => t.path === saved.active));
    // Poll gently: every 10 s while things change, up to 60 s when nothing did; never while hidden. Coming back
    // to the page refreshes at once.
    const tick = () => {
      if (S.el.isConnected && !document.hidden && !menuEl) refreshAll().catch(() => {});
      setTimeout(tick, Math.min(60000, 10000 * (1 + Math.floor((S.idle || 0) / 3))));
    };
    setTimeout(tick, 10000);
    addEventListener("focus", () => { if (S.el.isConnected) { S.idle = 0; refreshAll().catch(() => {}); } });
    document.addEventListener("keydown", (e) => {
      if (!S.el.isConnected) return;
      if (e.ctrlKey && e.key === "`") { e.preventDefault(); W.toggleTerminal(true); }
      else if (e.ctrlKey && e.key.toLowerCase() === "l" && !e.target.closest(".xterm")) { e.preventDefault(); W.focusClaude?.(); }
    });
  }

  registerView({ id: "workspace", title: "Workspace", icon: "workspace", admin: true }, (q) => viewWorkspace(q, false), 0);
})();
