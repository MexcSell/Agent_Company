"use strict";
// Workspace - the administrators' home page, laid out like an editor with Claude Code:
// explorer (left) | file viewer over terminal (centre) | Claude Code (right).
// The DOM is built once and re-attached when the view is shown again, so terminals,
// open files and the conversation survive switching views and live refreshes.
window.WSP = window.WSP || {};
(() => {
  const W = window.WSP;
  const read = (k, d) => { try { return JSON.parse(store.get(k) || "null") ?? d; } catch { return d; } };
  const S = (W.state = {
    root: store.get("ac.wsp.root") || "", roots: [], dirs: new Map(), items: new Map(), open: new Set(read("ac.wsp.open", [])),
    sel: new Set(), anchor: null, clip: null, tabs: [], active: null, el: null,
    sizes: { left: 260, right: 400, term: 260, ...read("ac.wsp.sizes", {}) }, termHidden: store.get("ac.wsp.termHidden") === "1",
    win: { min: new Set(read("ac.wsp.min", [])), max: store.get("ac.wsp.max") || null }, git: {}, filter: "", panes: [], noClaude: false,
  });
  const saveOpen = () => store.set("ac.wsp.open", JSON.stringify([...S.open].slice(-300)));
  const parentOf = (p) => p.slice(0, p.lastIndexOf("/")) || "/";
  const baseName = (p) => p.slice(p.lastIndexOf("/") + 1);
  W.rel = (p) => (S.root && p.startsWith(S.root + "/") ? p.slice(S.root.length + 1) : p);
  W.root = () => S.root;

  // --- icons by extension ---------------------------------------------------------------
  const B = (label, color) => ({ label, color });
  const EXT = {
    py: B("PY", "#3776ab"), pyi: B("PY", "#3776ab"), ipynb: B("NB", "#f37626"), js: B("JS", "#d4a800"), mjs: B("JS", "#d4a800"), cjs: B("JS", "#d4a800"),
    ts: B("TS", "#3178c6"), tsx: B("TSX", "#2f9fd1"), jsx: B("JSX", "#2f9fd1"), json: B("{}", "#c0a000"), jsonc: B("{}", "#c0a000"),
    md: B("M↓", "#2b7bb9"), mdx: B("MDX", "#2b7bb9"), html: B("<>", "#e34c26"), htm: B("<>", "#e34c26"), css: B("#", "#5b4cc4"), scss: B("#", "#c6538c"), less: B("#", "#1d365d"),
    sh: B("$_", "#3d9a2c"), bash: B("$_", "#3d9a2c"), zsh: B("$_", "#3d9a2c"), ps1: B("PS", "#2671be"), bat: B("BAT", "#6d8086"),
    toml: B("TML", "#9c4221"), yaml: B("YML", "#cb171e"), yml: B("YML", "#cb171e"), ini: B("INI", "#6d8086"), cfg: B("CFG", "#6d8086"), env: B("ENV", "#c9a800"),
    sql: B("SQL", "#e38c00"), db: B("DB", "#5c6bc0"), sqlite: B("DB", "#5c6bc0"), go: B("GO", "#00a7d0"), rs: B("RS", "#c4643c"), rb: B("RB", "#cc342d"),
    java: B("JV", "#b07219"), kt: B("KT", "#a97bff"), swift: B("SW", "#f05138"), c: B("C", "#5c6bc0"), h: B("H", "#7e57c2"), cpp: B("C++", "#f34b7d"), hpp: B("H++", "#f34b7d"),
    cs: B("C#", "#178600"), php: B("PHP", "#777bb3"), lua: B("LUA", "#000080"), r: B("R", "#198ce7"), dart: B("DRT", "#00b4ab"), scala: B("SC", "#c22d40"),
    vue: B("V", "#41b883"), svelte: B("S", "#ff3e00"), xml: B("XML", "#0060ac"), svg: B("SVG", "#e8a300"), txt: B("TXT", "#7a7a7a"), log: B("LOG", "#7a7a7a"),
    csv: B("CSV", "#1d6f42"), tsv: B("TSV", "#1d6f42"), xlsx: B("XL", "#1d6f42"), xls: B("XL", "#1d6f42"), docx: B("W", "#2b579a"), doc: B("W", "#2b579a"),
    pptx: B("P", "#d24726"), ppt: B("P", "#d24726"), pdf: B("PDF", "#e5252a"), lock: B("LCK", "#7a7a7a"), diff: B("±", "#41b883"), patch: B("±", "#41b883"),
    zip: B("ZIP", "#a67c00"), tar: B("TAR", "#a67c00"), gz: B("GZ", "#a67c00"), tgz: B("TGZ", "#a67c00"), bz2: B("BZ", "#a67c00"), xz: B("XZ", "#a67c00"), "7z": B("7Z", "#a67c00"), rar: B("RAR", "#a67c00"),
    exe: B("EXE", "#6d8086"), bin: B("BIN", "#6d8086"), so: B("SO", "#6d8086"), wasm: B("WA", "#654ff0"), ttf: B("Aa", "#e05050"), woff: B("Aa", "#e05050"), woff2: B("Aa", "#e05050"),
  };
  const NAMES = { dockerfile: B("DK", "#2496ed"), makefile: B("MK", "#6d8086"), ".gitignore": B("GIT", "#f05032"), ".gitattributes": B("GIT", "#f05032"), license: B("©", "#c9a000"),
    "readme.md": B("i", "#2b7bb9"), ".env": B("ENV", "#c9a800"), "package.json": B("NPM", "#cb3837"), "pyproject.toml": B("PY", "#3776ab"), "uv.lock": B("UV", "#de5fe9"), "claude.md": B("✳", "#d97757") };
  const GLYPH = {
    image: ["#26a69a", "M4 5h16v14H4zM8.5 9a1.5 1.5 0 1 0 0 .01M20 16l-5-5-8 8"],
    audio: ["#e91e63", "M9 18V6l10-2v12M9 18a3 3 0 1 1-3-3 3 3 0 0 1 3 3zM19 16a3 3 0 1 1-3-3 3 3 0 0 1 3 3z"],
    video: ["#ff5722", "M3 6h13v12H3zM16 10l5-3v10l-5-3"],
  };
  const IMG = new Set(["png", "jpg", "jpeg", "gif", "webp", "bmp", "ico", "avif", "tif", "tiff"]), AUD = new Set(["mp3", "wav", "ogg", "flac", "m4a", "aac"]), VID = new Set(["mp4", "webm", "mov", "mkv", "avi"]);
  const FOLDER = { src: "#4f8fe8", lib: "#4f8fe8", app: "#4f8fe8", test: "#43a047", tests: "#43a047", spec: "#43a047", docs: "#2aa6d6", doc: "#2aa6d6", ".git": "#f05032", ".github": "#6d8086",
    node_modules: "#7cb342", static: "#fb8c00", public: "#fb8c00", assets: "#fb8c00", images: "#26a69a", data: "#8e5cc8", scripts: "#3d9a2c", bin: "#3d9a2c", config: "#6d8086", ".venv": "#3776ab", venv: "#3776ab", dist: "#a67c00", build: "#a67c00" };
  W.fileIcon = (name, dir, size = 16, open = false) => {
    const lower = name.toLowerCase();
    if (dir) {
      const c = FOLDER[lower] || "#d9a53f";
      return `<svg class="fico" width="${size}" height="${size}" viewBox="0 0 24 24" aria-hidden="true">${open
        ? `<path d="M3 7a2 2 0 0 1 2-2h4l2 2h7a2 2 0 0 1 2 2v1H8.5a2 2 0 0 0-1.9 1.4L4 19H5a2 2 0 0 1-2-2z" fill="${c}" opacity=".7"/><path d="M6.6 11.4A2 2 0 0 1 8.5 10H22l-2.6 8.2A2 2 0 0 1 17.5 20H4z" fill="${c}"/>`
        : `<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" fill="${c}"/>`}</svg>`;
    }
    const ext = lower.includes(".") ? lower.slice(lower.lastIndexOf(".") + 1) : "";
    const g = IMG.has(ext) ? GLYPH.image : AUD.has(ext) ? GLYPH.audio : VID.has(ext) ? GLYPH.video : null;
    if (g) return `<svg class="fico" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="${g[0]}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${g[1]}"/></svg>`;
    const b = NAMES[lower] || (lower.startsWith(".env") ? NAMES[".env"] : null) || EXT[ext] || (lower.startsWith("readme") ? NAMES["readme.md"] : null);
    if (!b) return `<svg class="fico" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="#8a94a3" stroke-width="1.8" stroke-linejoin="round" aria-hidden="true"><path d="M6 2h9l5 5v15H6zM14 2v6h6"/></svg>`;
    return `<span class="fbadge${b.label.length > 2 ? " l3" : ""}" style="--c:${b.color};--s:${size}px" aria-hidden="true">${esc(b.label)}</span>`;
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
  const LAYOUT = `
    <aside class="wx" aria-label="Explorer">
      <div class="wx-head"><span class="wx-title">Explorer</span>
        <button class="icon-btn sm" data-x="newfile" title="New file">${svg("M6 2h9l5 5v15H6zM14 2v6h6M12 11v6M9 14h6")}</button>
        <button class="icon-btn sm" data-x="newdir" title="New folder">${svg("M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM12 10v6M9 13h6")}</button>
        <button class="icon-btn sm" data-x="upload" title="Upload files">${svg("M12 16V4M7 9l5-5 5 5M4 20h16")}</button>
        <button class="icon-btn sm" data-x="refresh" title="Refresh">${svg("M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7")}</button>
        <button class="icon-btn sm" data-x="collapse" title="Collapse folders">${svg("M8 4l4 4 4-4M8 20l4-4 4 4")}</button>
        <button class="icon-btn sm" data-x="filter" title="Filter files by name (Ctrl+F in the explorer)">${svg("M3 5h18l-7 8v6l-4 2v-8z")}</button>
        <button class="icon-btn sm" data-x="split" title="Split: add another explorer pane (+)">${svg("M12 5v14M5 12h14")}</button>
        ${winBtns("explorer")}</div>
      <div class="wx-filter" id="wx-filter" hidden><input id="wx-filter-q" type="search" placeholder="Filter by name…" aria-label="Filter files"></div>
      ${strip("explorer", "Explorer")}
      <div class="wx-root"><select id="wx-roots" aria-label="Folder"></select><button class="icon-btn sm" id="wx-close-root" title="Close this folder" hidden>✕</button></div>
      <div class="wx-tree" id="wx-tree" tabindex="0" role="tree" aria-label="Files"></div>
      <div class="wx-status muted" id="wx-status"></div>
      <div class="wx-panes" id="wx-panes"></div>
      <input type="file" id="wx-file" multiple hidden>
    </aside>
    <div class="wgut v" data-g="left" role="separator" aria-orientation="vertical" title="Drag to resize"></div>
    <section class="wm">
      <div class="we"><div class="we-bar"><div class="we-tabs" id="we-tabs" role="tablist"></div><span class="we-acts">${winBtns("editor")}</span></div><div class="we-hosts" id="we-hosts"></div></div>
      <div class="wgut h" data-g="term" role="separator" aria-orientation="horizontal" title="Drag to resize"></div>
      <div class="wt" id="wt"></div>
    </section>
    <div class="wgut v" data-g="right" role="separator" aria-orientation="vertical" title="Drag to resize"></div>
    <aside class="wc" id="wc" aria-label="Claude Code"></aside>`;
  // Every window of the Workspace can be minimised (a slim strip) or maximised (fills the Workspace).
  function winBtns(pane) {
    return `<button class="icon-btn sm win-btn" data-win="min" data-pane="${pane}" title="Minimise">${svg("M5 19h14")}</button>`
      + `<button class="icon-btn sm win-btn" data-win="max" data-pane="${pane}" title="Maximise / restore">${svg("M4 4h16v16H4z")}</button>`;
  }
  function strip(pane, label) {
    return `<button class="win-strip" data-win="restore" data-pane="${pane}" title="Restore ${label}"><span>${label}</span></button>`;
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
    S.el.querySelectorAll(".win-btn[data-win=max]").forEach((b) => (b.innerHTML = svg(max === b.dataset.pane ? "M8 8h12v12H8zM4 16V4h12" : "M4 4h16v16H4z")));
    const left = max === "explorer" ? "minmax(0, 1fr)" : max ? "0px" : min.has("explorer") ? "34px" : `${S.sizes.left}px`;
    const right = S.noClaude ? "0px" : max === "claude" ? "minmax(0, 1fr)" : max ? "0px" : min.has("claude") ? "34px" : `${S.sizes.right}px`;
    const mid = max === "explorer" || max === "claude" ? "0px" : "minmax(240px, 1fr)";
    S.el.style.gridTemplateColumns = narrow ? "1fr" : `${left} ${max ? "0px" : "5px"} ${mid} ${max || S.noClaude ? "0px" : "5px"} ${right}`;
    const termOff = S.termHidden || max === "editor";
    const rows = max === "terminal" ? "34px 0 minmax(0, 1fr)" : termOff ? "minmax(0, 1fr) 0 0"
      : min.has("terminal") ? "minmax(0, 1fr) 0 32px" : min.has("editor") ? `34px 5px minmax(0, 1fr)` : `minmax(120px, 1fr) 5px ${S.sizes.term}px`;
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
  function wireGutters() {
    S.el.querySelectorAll(".wgut").forEach((g) => (g.onpointerdown = (e) => {
      e.preventDefault();
      g.setPointerCapture(e.pointerId);
      g.classList.add("active");
      const which = g.dataset.g, start = { x: e.clientX, y: e.clientY, ...S.sizes };
      const total = S.el.clientWidth, mh = S.el.querySelector(".wm").clientHeight;
      g.onpointermove = (ev) => {
        if (which === "left") S.sizes.left = Math.max(160, Math.min(total * 0.4, start.left + ev.clientX - start.x));
        if (which === "right") S.sizes.right = Math.max(280, Math.min(total * 0.6, start.right - (ev.clientX - start.x)));
        if (which === "term") S.sizes.term = Math.max(80, Math.min(mh - 120, start.term - (ev.clientY - start.y)));
        applySizes();
      };
      g.onpointerup = () => { g.onpointermove = null; g.classList.remove("active"); store.set("ac.wsp.sizes", JSON.stringify(S.sizes)); };
    }));
    addEventListener("resize", () => S.el?.isConnected && applySizes());
  }

  // --- explorer -----------------------------------------------------------------------------
  const status = (t) => { const el = S.el?.querySelector("#wx-status"); if (el) el.textContent = t || ""; };
  async function loadDir(path) {
    const d = await api(`/api/files/list?path=${encodeURIComponent(path)}`);
    const items = d.items.filter((it) => it.name !== ".git");
    S.dirs.set(path, items);
    items.forEach((it) => S.items.set(it.path, it));
    return items;
  }
  async function loadRoots() {
    S.roots = await api("/api/files/roots");
    if (!S.roots.some((r) => r.path === S.root)) S.root = (S.roots.find((r) => r.name !== "data") || S.roots[0])?.path || "";
    renderRoots();
    await refresh();
  }
  function renderRoots() {
    const sel = S.el.querySelector("#wx-roots");
    sel.innerHTML = S.roots.map((r) => `<option value="${esc(r.path)}">${esc(r.name)} · ${esc(r.path)}</option>`).join("") + '<option value="__open">＋ Open folder…</option>';
    sel.value = S.root;
    S.el.querySelector("#wx-close-root").hidden = !S.roots.find((r) => r.path === S.root)?.opened;
  }
  async function setRoot(path) {
    S.root = path; store.set("ac.wsp.root", path); S.sel.clear(); S.anchor = null;
    renderRoots(); await refresh(); W.onRoot?.(path);
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
  // Only folders whose whole chain is open are listed (an open folder inside a closed one is not visible).
  const shown = (p) => { for (let d = parentOf(p); d.length > S.root.length; d = parentOf(d)) if (!S.open.has(d)) return false; return true; };
  async function refresh() {
    if (!S.root) return renderTree();
    const dirs = [S.root, ...[...S.open].filter((p) => p.startsWith(S.root + "/") && shown(p))];
    const fresh = new Map(S.dirs);
    let rootOk = false;
    api(`/api/files/git-status?path=${encodeURIComponent(S.root)}`).then((g) => {
      if (JSON.stringify(g) !== JSON.stringify(S.git)) { S.git = g; renderTree(); }
    }).catch(() => {});
    await Promise.all(dirs.map(async (d) => { try { fresh.set(d, (await api(`/api/files/list?path=${encodeURIComponent(d)}`)).items.filter((it) => it.name !== ".git")); if (d === S.root) rootOk = true; } catch { fresh.delete(d); if (d !== S.root) S.open.delete(d); } }));
    if (!rootOk) { status("This folder is not available."); S.dirs.clear(); return renderTree(); }
    const before = JSON.stringify([...S.dirs.entries()].filter(([k]) => fresh.has(k)).map(([k, v]) => [k, v.map((i) => i.name + i.size + i.modified)]));
    S.dirs = fresh;
    fresh.forEach((items) => items.forEach((it) => S.items.set(it.path, it)));
    const after = JSON.stringify([...S.dirs.entries()].map(([k, v]) => [k, v.map((i) => i.name + i.size + i.modified)]));
    S.idle = before === after ? (S.idle || 0) + 1 : 0;
    if (before !== after || !S.el.querySelector("#wx-tree .tr")) renderTree();
  }
  W.refreshExplorer = refresh;

  function visibleRows() {
    const rows = [];
    const walk = (dir, depth) => {
      for (const it of S.dirs.get(dir) || []) {
        rows.push({ it, depth });
        if (it.dir && S.open.has(it.path) && S.dirs.has(it.path)) walk(it.path, depth + 1);
      }
    };
    if (S.root) walk(S.root, 0);
    return rows;
  }
  function renderTree() {
    const tree = S.el.querySelector("#wx-tree");
    if (!S.root) { tree.innerHTML = `<div class="wx-empty"><p class="muted">No folder open.</p><button class="btn primary" data-x="openfolder">Open folder</button></div>`; return; }
    let rows = visibleRows();
    const q = S.filter.trim().toLowerCase();
    if (q) {  // keep matching rows and the folders that lead to them
      const keep = new Set();
      rows.forEach(({ it }) => { if (it.name.toLowerCase().includes(q)) { keep.add(it.path); for (let d = parentOf(it.path); d.length > S.root.length; d = parentOf(d)) keep.add(d); } });
      rows = rows.filter(({ it }) => keep.has(it.path));
    }
    const cut = new Set(S.clip?.cut ? S.clip.paths : []);
    const G = { M: "modified", A: "added", D: "deleted", U: "untracked", R: "renamed", C: "copied", dot: "contains changes" };
    tree.innerHTML = rows.map(({ it, depth }) => {
      const open = it.dir && S.open.has(it.path), g = S.git[it.path];
      return `<div class="tr${S.sel.has(it.path) ? " sel" : ""}${cut.has(it.path) ? " cut" : ""}${S.active === it.path ? " act" : ""}${g ? ` g-${g}` : ""}" role="treeitem" data-path="${esc(it.path)}"${it.dir ? ` data-dir="1" aria-expanded="${open}"` : ""} draggable="true" style="--d:${depth}"
        title="${esc(it.name)}${it.dir ? "" : ` · ${W.size(it.size)}`}${g ? ` · ${G[g]}` : ""}"><span class="chev${open ? " open" : ""}">${it.dir ? "›" : ""}</span>${W.fileIcon(it.name, it.dir, 16, open)}<span class="tn">${esc(it.name)}</span>${g ? `<span class="gd">${g === "dot" ? "●" : g}</span>` : ""}</div>`;
    }).join("") || `<div class="wx-empty muted">${q ? "No file matches the filter." : "Empty folder. Drop files here to upload."}</div>`;
  }

  async function toggleDir(path, force) {
    const open = force ?? !S.open.has(path);
    if (open) { S.open.add(path); if (!S.dirs.has(path)) { try { await loadDir(path); } catch (e) { toast(e.message); S.open.delete(path); } } }
    else S.open.delete(path);
    saveOpen(); renderTree();
  }
  async function reveal(path) {
    if (!S.root || !path.startsWith(S.root + "/")) return;
    const parts = path.slice(S.root.length + 1).split("/");
    let cur = S.root;
    for (const part of parts.slice(0, -1)) { cur += "/" + part; S.open.add(cur); if (!S.dirs.has(cur)) { try { await loadDir(cur); } catch { break; } } }
    saveOpen(); renderTree();
    S.el.querySelector(`.tr[data-path="${CSS.escape(path)}"]`)?.scrollIntoView({ block: "nearest" });
  }

  const targetDir = (row) => (row ? (row.dataset.dir ? row.dataset.path : parentOf(row.dataset.path)) : S.root);
  const selection = (row) => (row && S.sel.has(row.dataset.path) ? [...S.sel] : row ? [row.dataset.path] : [...S.sel]);

  function wireExplorer() {
    const tree = S.el.querySelector("#wx-tree");
    S.el.querySelector(".wx").addEventListener("click", (e) => { const b = e.target.closest("[data-x]"); if (b) explorerAction(b.dataset.x); });
    S.el.querySelector("#wx-roots").onchange = (e) => (e.target.value === "__open" ? openFolder() : setRoot(e.target.value));
    S.el.querySelector("#wx-close-root").onclick = async () => {
      if (!confirm(`Close ${S.root} in the explorer? Nothing on disk changes.`)) return;
      await api(`/api/files/roots?path=${encodeURIComponent(S.root)}`, { method: "DELETE" }).catch((e) => toast(e.message));
      S.root = ""; await loadRoots();
    };
    S.el.querySelector("#wx-file").onchange = async (e) => { await uploadFiles([...e.target.files], S.uploadDir || S.root); e.target.value = ""; };

    tree.onclick = (e) => {
      const row = e.target.closest(".tr");
      if (!row) { S.sel.clear(); renderTree(); return; }
      const path = row.dataset.path;
      const rows = [...tree.querySelectorAll(".tr")].map((r) => r.dataset.path);
      if (e.shiftKey && S.anchor) {
        const [a, b] = [rows.indexOf(S.anchor), rows.indexOf(path)].sort((x, y) => x - y);
        if (!(e.ctrlKey || e.metaKey)) S.sel.clear();
        rows.slice(a, b + 1).forEach((p) => S.sel.add(p));
      } else if (e.ctrlKey || e.metaKey) { S.sel.has(path) ? S.sel.delete(path) : S.sel.add(path); S.anchor = path; }
      else {
        S.sel.clear(); S.sel.add(path); S.anchor = path;
        if (row.dataset.dir) return toggleDir(path);
        W.openPath(path);
      }
      renderTree();
    };
    tree.oncontextmenu = (e) => {
      e.preventDefault();
      const row = e.target.closest(".tr");
      if (row && !S.sel.has(row.dataset.path)) { S.sel.clear(); S.sel.add(row.dataset.path); S.anchor = row.dataset.path; renderTree(); }
      if (!row) { S.sel.clear(); renderTree(); }
      W.menu(e.clientX, e.clientY, menuFor(row ? S.el.querySelector(`.tr[data-path="${CSS.escape(row.dataset.path)}"]`) : null));
    };
    const fq = S.el.querySelector("#wx-filter-q");
    fq.oninput = () => { S.filter = fq.value; renderTree(); };
    fq.onkeydown = (e) => { if (e.key === "Escape") { e.preventDefault(); toggleFilter(false); tree.focus(); } };
    tree.onkeydown = (e) => {
      const sel = [...S.sel], one = sel.length === 1 ? sel[0] : null, mod = e.ctrlKey || e.metaKey;
      if (mod && e.key.toLowerCase() === "f") { e.preventDefault(); toggleFilter(true); return; }
      if (e.key === "Delete" && sel.length) { e.preventDefault(); act.delete(sel); }
      else if (e.key === "F2" && one) { e.preventDefault(); act.rename(one); }
      else if (e.key === "Enter" && one) { e.preventDefault(); S.items.get(one)?.dir ? toggleDir(one) : W.openPath(one); }
      else if (mod && e.key.toLowerCase() === "c" && sel.length) { e.preventDefault(); act.clip(sel, false); }
      else if (mod && e.key.toLowerCase() === "x" && sel.length) { e.preventDefault(); act.clip(sel, true); }
      else if (mod && e.key.toLowerCase() === "v" && S.clip) { e.preventDefault(); act.paste(one && S.items.get(one)?.dir ? one : one ? parentOf(one) : S.root); }
      else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const rows = [...tree.querySelectorAll(".tr")].map((r) => r.dataset.path);
        const i = Math.max(-1, rows.indexOf(S.anchor)) + (e.key === "ArrowDown" ? 1 : -1);
        if (rows[i]) { S.sel.clear(); S.sel.add(rows[i]); S.anchor = rows[i]; renderTree(); tree.querySelector(".tr.sel")?.scrollIntoView({ block: "nearest" }); }
      } else if ((e.key === "ArrowRight" || e.key === "ArrowLeft") && one && S.items.get(one)?.dir) { e.preventDefault(); toggleDir(one, e.key === "ArrowRight"); }
    };

    // Drag and drop: rows move (Ctrl/Alt copies); files and folders from the computer upload.
    tree.ondragstart = (e) => {
      const row = e.target.closest(".tr");
      if (!row) return;
      const paths = selection(row);
      e.dataTransfer.setData("text/x-paths", JSON.stringify(paths));
      e.dataTransfer.setData("text/plain", paths.join("\n"));
      e.dataTransfer.effectAllowed = "copyMove";
    };
    let hover = null;
    const mark = (el) => { if (hover !== el) { hover?.classList.remove("drop"); hover = el; hover?.classList.add("drop"); } };
    tree.ondragover = (e) => {
      e.preventDefault();
      const row = e.target.closest(".tr");
      const dir = targetDir(row);
      const internal = e.dataTransfer.types.includes("text/x-paths");
      e.dataTransfer.dropEffect = internal ? (e.ctrlKey || e.altKey ? "copy" : "move") : "copy";
      mark(dir === S.root ? tree : S.el.querySelector(`.tr[data-path="${CSS.escape(dir)}"]`) || tree);
    };
    tree.ondragleave = (e) => { if (!tree.contains(e.relatedTarget)) mark(null); };
    tree.ondrop = async (e) => {
      e.preventDefault(); mark(null);
      const dir = targetDir(e.target.closest(".tr"));
      const raw = e.dataTransfer.getData("text/x-paths");
      if (raw) return moveTo(JSON.parse(raw), dir, e.ctrlKey || e.altKey);
      // Entries must be taken before the first await: the DataTransfer empties after the event.
      const entries = [...(e.dataTransfer.items || [])].map((i) => (i.kind === "file" ? i.webkitGetAsEntry?.() : null)).filter(Boolean);
      const filesList = [...e.dataTransfer.files];
      if (entries.length) await uploadEntries(entries, dir);
      else if (filesList.length) await uploadFiles(filesList, dir);
    };
  }

  function explorerAction(x) {
    const one = S.sel.size === 1 ? [...S.sel][0] : null;
    const dir = one ? (S.items.get(one)?.dir ? one : parentOf(one)) : S.root;
    if (x === "openfolder") return openFolder();
    if (!S.root) return toast("Open a folder first");
    if (x === "newfile") return act.create(dir, false);
    if (x === "newdir") return act.create(dir, true);
    if (x === "upload") return act.upload(dir);
    if (x === "refresh") return refresh().then(() => toast("Refreshed"));
    if (x === "collapse") { S.open.clear(); saveOpen(); renderTree(); }
    if (x === "filter") return toggleFilter();
    if (x === "split") return addPane();
  }
  function toggleFilter(show) {
    const box = S.el.querySelector("#wx-filter"), on = show ?? box.hidden;
    box.hidden = !on;
    if (on) box.querySelector("input").focus(); else { S.filter = ""; box.querySelector("input").value = ""; renderTree(); }
  }

  function menuFor(row) {
    const sel = row ? selection(row) : [];
    const path = row?.dataset.path, isDir = !!row?.dataset.dir, many = sel.length > 1;
    const dir = targetDir(row);
    const lower = (path || "").toLowerCase();
    const archive = /\.(zip|tar|tgz|tar\.(gz|bz2|xz)|tbz2|txz)$/.test(lower);
    const runnable = { py: "python3", sh: "bash", js: "node", mjs: "node", rb: "ruby", go: "go run", php: "php", pl: "perl", ts: "npx tsx" }[lower.split(".").pop()];
    const q = (p) => `'${p.replace(/'/g, "'\\''")}'`;
    return [
      row && !isDir && !many && { label: "Open", key: "Enter", act: () => W.openPath(path) },
      row && !isDir && !many && runnable && { label: `Run in terminal (${runnable})`, act: () => W.sendToTerminal(`cd ${q(parentOf(path))} && ${runnable} ${q(baseName(path))}\r`) },
      (!row || isDir) && { label: "New file…", act: () => act.create(dir, false) },
      (!row || isDir) && { label: "New folder…", act: () => act.create(dir, true) },
      (!row || isDir) && { label: "Upload files here…", act: () => act.upload(dir) },
      { label: "Open in terminal", act: () => W.newTerminal(dir) },
      row && { label: many ? `Ask Claude about ${sel.length} items` : "Ask Claude about this", act: () => W.claudeMention(sel) },
      "-",
      row && { label: "Cut", key: "Ctrl+X", act: () => act.clip(sel, true) },
      row && { label: "Copy", key: "Ctrl+C", act: () => act.clip(sel, false) },
      { label: S.clip ? `Paste ${S.clip.paths.length} item(s)` : "Paste", key: "Ctrl+V", disabled: !S.clip, act: () => act.paste(dir) },
      row && !many && { label: "Duplicate", act: () => act.duplicate(path) },
      "-",
      row && { label: many ? "Copy paths" : "Copy path", act: () => W.copyText(sel.join("\n"), "Path copied") },
      row && { label: many ? "Copy relative paths" : "Copy relative path", act: () => W.copyText(sel.map(W.rel).join("\n"), "Path copied") },
      !row && { label: "Copy folder path", act: () => W.copyText(S.root, "Path copied") },
      "-",
      row && { label: many || isDir ? "Download as .zip" : "Download", act: () => act.download(sel) },
      row && { label: "Compress to .zip", act: () => act.compress(sel) },
      row && !many && archive && { label: "Extract here", act: () => act.extract(path) },
      "-",
      row && !many && { label: "Rename…", key: "F2", act: () => act.rename(path) },
      row && { label: many ? `Delete ${sel.length} items` : "Delete", key: "Del", danger: true, act: () => act.delete(sel) },
      !row && { label: "Refresh", act: () => refresh() },
    ].filter((x, i, arr) => x && !(x === "-" && (i === 0 || arr.slice(0, i).filter(Boolean).pop() === "-")));
  }

  // VS Code-style inline input in the tree (new file, new folder, rename): Enter accepts, Esc cancels.
  function inlineInput({ dir, depth, after, value = "", folder = false, onDone }) {
    const tree = S.el.querySelector("#wx-tree");
    const row = document.createElement("div");
    row.className = "tr tr-input"; row.style.setProperty("--d", depth);
    row.innerHTML = `<span class="chev"></span>${W.fileIcon(value || (folder ? "folder" : "file"), folder, 16)}<input class="tr-edit" spellcheck="false" autocomplete="off" aria-label="${folder ? "Folder" : "File"} name">`;
    if (after) after.after(row); else tree.prepend(row);
    const input = row.querySelector("input");
    input.value = value;
    const dot = value.lastIndexOf(".");
    input.focus(); input.setSelectionRange(0, dot > 0 && !folder ? dot : value.length);
    let done = false;
    const finish = (ok) => { if (done) return; done = true; const v = input.value.trim(); row.remove(); if (ok && v && v !== value) onDone(v); else renderTree(); };
    input.onkeydown = (e) => { e.stopPropagation(); if (e.key === "Enter") { e.preventDefault(); finish(true); } else if (e.key === "Escape") { e.preventDefault(); finish(false); } };
    input.onblur = () => finish(true);
    input.onclick = (e) => e.stopPropagation();
  }
  const depthOf = (path) => (path === S.root ? -1 : path.slice(S.root.length + 1).split("/").length - 1);
  const act = {
    async create(dir, folder) {
      if (dir !== S.root && !S.open.has(dir)) await toggleDir(dir, true);
      const dirRow = dir === S.root ? null : S.el.querySelector(`.tr[data-path="${CSS.escape(dir)}"]`);
      inlineInput({ dir, depth: depthOf(dir) + 1, after: dirRow, folder, onDone: (name) => act.createNamed(dir, name, folder) });
    },
    async createNamed(dir, name, folder) {
      try {
        const r = await api("/api/files/create", { method: "POST", body: { path: `${dir}/${name}`, folder } });
        if (dir !== S.root) S.open.add(dir);
        await refresh(); await reveal(r.path);
        if (!folder) W.openPath(r.path);
      } catch (e) { toast(e.message); }
    },
    upload(dir) { S.uploadDir = dir; S.el.querySelector("#wx-file").click(); },
    async rename(path) {
      const row = S.el.querySelector(`.tr[data-path="${CSS.escape(path)}"]`);
      if (row) { row.hidden = true; inlineInput({ depth: depthOf(path), after: row, value: baseName(path), folder: !!row.dataset.dir, onDone: (n) => act.renameTo(path, n) }); return; }
      const name = prompt("New name", baseName(path));
      if (name && name !== baseName(path)) act.renameTo(path, name);
    },
    async renameTo(path, name) {
      try { const r = await api("/api/files/move", { method: "POST", body: { src: path, dest: `${parentOf(path)}/${name}` } }); retarget(path, r.path); S.sel.clear(); S.sel.add(r.path); await refresh(); } catch (e) { toast(e.message); }
    },
    async duplicate(path) {
      const name = baseName(path), dot = name.lastIndexOf(".") > 0 ? name.lastIndexOf(".") : name.length;
      const siblings = new Set((S.dirs.get(parentOf(path)) || []).map((i) => i.name));
      let n = 1, cand;
      do { cand = `${name.slice(0, dot)} copy${n > 1 ? " " + n : ""}${name.slice(dot)}`; n++; } while (siblings.has(cand));
      try { await api("/api/files/move", { method: "POST", body: { src: path, dest: `${parentOf(path)}/${cand}`, copy: true } }); await refresh(); } catch (e) { toast(e.message); }
    },
    clip(paths, cut) { S.clip = { paths, cut }; toast(`${paths.length} item(s) ${cut ? "cut" : "copied"}`); renderTree(); },
    async paste(dir) {
      if (!S.clip) return;
      await moveTo(S.clip.paths, dir, !S.clip.cut);
      if (S.clip.cut) S.clip = null;
      renderTree();
    },
    download(paths) {
      const one = paths.length === 1 && !S.items.get(paths[0])?.dir;
      const a = document.createElement("a");
      a.href = one ? W.rawUrl(paths[0], true) : `/api/files/archive?${paths.map((p) => `paths=${encodeURIComponent(p)}`).join("&")}`;
      a.download = ""; document.body.append(a); a.click(); a.remove();
    },
    async compress(paths) {
      status("Compressing…");
      try { const r = await api("/api/files/compress", { method: "POST", body: { paths } }); await refresh(); await reveal(r.path); toast(`Created ${baseName(r.path)} (${W.size(r.size)})`); } catch (e) { toast(e.message); }
      status("");
    },
    async extract(path) {
      status("Extracting…");
      try { const r = await api("/api/files/extract", { method: "POST", body: { path } }); S.open.add(r.path); await refresh(); await reveal(r.path); toast(`Extracted to ${baseName(r.path)}`); } catch (e) { toast(e.message); }
      status("");
    },
    async delete(paths) {
      if (!confirm(paths.length === 1 ? `Delete ${baseName(paths[0])}?${S.items.get(paths[0])?.dir ? " The folder is deleted with everything in it." : ""}` : `Delete ${paths.length} items? Folders are deleted with their contents.`)) return;
      try {
        await api("/api/files/delete", { method: "POST", body: { paths } });
        paths.forEach((p) => S.tabs.filter((t) => t.path === p || t.path.startsWith(p + "/")).forEach((t) => closeTab(t, true)));
        S.sel.clear(); await refresh();
      } catch (e) { toast(e.message); }
    },
  };
  W.explorerAct = act;

  async function moveTo(paths, dest, copy) {
    let done = 0;
    for (const p of paths) {
      if (p === dest || (!copy && parentOf(p) === dest) || dest.startsWith(p + "/")) continue;
      try { const r = await api("/api/files/move", { method: "POST", body: { src: p, dest, copy } }); if (!copy) retarget(p, r.path); done++; } catch (e) { toast(e.message); }
    }
    if (done) { S.open.add(dest); S.sel.clear(); await refresh(); toast(`${copy ? "Copied" : "Moved"} ${done} item(s)`); }
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
  async function uploadFiles(list, dir) {
    if (!list.length) return;
    const existing = new Set((S.dirs.get(dir) || []).map((i) => i.name));
    const clash = list.filter((f) => existing.has(f.name));
    if (clash.length && !confirm(`Replace ${clash.length} existing file(s) in ${baseName(dir)}? (${clash.slice(0, 3).map((f) => f.name).join(", ")}${clash.length > 3 ? "…" : ""})`)) return;
    let n = 0;
    for (const f of list) {
      status(`Uploading ${++n}/${list.length}: ${f.name}`);
      try { await uploadOne(f, dir); } catch (e) { toast(e.message); }
    }
    status(""); if (dir !== S.root) S.open.add(dir);
    await refresh(); toast(`Uploaded ${list.length} file(s)`);
  }
  async function uploadEntries(entries, dir) {
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
    const existing = new Set((S.dirs.get(dir) || []).map((i) => i.name));
    const clash = entries.filter((e) => existing.has(e.name));
    if (clash.length && !confirm(`Replace or merge ${clash.length} existing item(s) in ${baseName(dir)}?`)) return;
    for (const e of entries) await walk(e, dir);
    status(""); if (dir !== S.root) S.open.add(dir);
    await refresh(); toast(`Uploaded ${n} file(s)`);
  }

  // --- more explorer panes ("+"): the first explorer is the project's and stays; each added pane has its own
  // folder (chosen from the registered roots, or any folder from "/" through the picker) and can be closed.
  function addPane(root) {
    const pane = { id: Math.random().toString(36).slice(2, 8), root: root || S.root, open: new Set(), dirs: new Map() };
    S.panes.push(pane); savePanes(); renderPane(pane); loadPane(pane);
  }
  const savePanes = () => store.set("ac.wsp.panes", JSON.stringify(S.panes.map((p) => p.root)));
  function paneEl(pane) {
    let el = S.el.querySelector(`.wx-pane[data-pane="${pane.id}"]`);
    if (!el) {
      el = document.createElement("section");
      el.className = "wx-pane"; el.dataset.pane = pane.id;
      el.innerHTML = `<div class="wx-pane-head"><select aria-label="Folder"></select>
        <button class="icon-btn sm" data-pa="refresh" title="Refresh">${svg("M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7")}</button>
        <button class="icon-btn sm" data-pa="collapse" title="Collapse folders">${svg("M8 4l4 4 4-4M8 20l4-4 4 4")}</button>
        <button class="icon-btn sm" data-pa="close" title="Close this pane">${svg("M6 6l12 12M18 6L6 18")}</button></div><div class="wx-pane-tree" tabindex="0" role="tree"></div>`;
      S.el.querySelector("#wx-panes").append(el);
      el.querySelector("select").onchange = async (e) => {
        if (e.target.value === "__open") {
          const dir = await pickFolder("/");
          if (dir) { try { const r = await api("/api/files/roots", { method: "POST", body: { path: dir } }); S.roots = await api("/api/files/roots"); renderRoots(); pane.root = r.path; } catch (x) { toast(x.message); } }
        } else pane.root = e.target.value;
        pane.open.clear(); pane.dirs.clear(); savePanes(); renderPane(pane); loadPane(pane);
      };
      el.querySelector(".wx-pane-head").onclick = (e) => {
        const b = e.target.closest("[data-pa]"); if (!b) return;
        if (b.dataset.pa === "close") { S.panes = S.panes.filter((x) => x !== pane); savePanes(); el.remove(); }
        if (b.dataset.pa === "refresh") { pane.dirs.clear(); loadPane(pane); }
        if (b.dataset.pa === "collapse") { pane.open.clear(); renderPane(pane); }
      };
      el.querySelector(".wx-pane-tree").onclick = async (e) => {
        const row = e.target.closest(".tr"); if (!row) return;
        const path = row.dataset.path;
        if (row.dataset.dir) { if (pane.open.has(path)) pane.open.delete(path); else { pane.open.add(path); if (!pane.dirs.has(path)) await loadPane(pane, path); } renderPane(pane); }
        else W.openPath(path);
      };
      el.querySelector(".wx-pane-tree").oncontextmenu = (e) => {
        const row = e.target.closest(".tr"); if (!row) return;
        e.preventDefault();
        const path = row.dataset.path, dir = row.dataset.dir ? path : parentOf(path);
        W.menu(e.clientX, e.clientY, [!row.dataset.dir && { label: "Open", act: () => W.openPath(path) }, { label: "Open in terminal", act: () => W.newTerminal(dir) },
          { label: "Copy path", act: () => W.copyText(path, "Path copied") }, { label: "Ask Claude about this", act: () => W.claudeMention?.([path]) }]);
      };
      el.querySelector(".wx-pane-tree").ondragstart = (e) => { const row = e.target.closest(".tr"); if (row) { e.dataTransfer.setData("text/x-paths", JSON.stringify([row.dataset.path])); e.dataTransfer.setData("text/plain", row.dataset.path); } };
    }
    return el;
  }
  async function loadPane(pane, path = pane.root) {
    try { const d = await api(`/api/files/list?path=${encodeURIComponent(path)}`); pane.dirs.set(path, d.items.filter((it) => it.name !== ".git")); }
    catch (e) { pane.dirs.set(path, []); if (path === pane.root) toast(e.message); }
    renderPane(pane);
  }
  function renderPane(pane) {
    const el = paneEl(pane), sel = el.querySelector("select");
    sel.innerHTML = S.roots.map((r) => `<option value="${esc(r.path)}">${esc(r.name)} · ${esc(r.path)}</option>`).join("") + '<option value="__open">＋ Open folder from / …</option>';
    sel.value = pane.root;
    const rows = [];
    const walk = (dir, depth) => { for (const it of pane.dirs.get(dir) || []) { rows.push({ it, depth }); if (it.dir && pane.open.has(it.path)) walk(it.path, depth + 1); } };
    walk(pane.root, 0);
    el.querySelector(".wx-pane-tree").innerHTML = rows.map(({ it, depth }) => { const open = it.dir && pane.open.has(it.path);
      return `<div class="tr" data-path="${esc(it.path)}"${it.dir ? ' data-dir="1"' : ""} draggable="true" style="--d:${depth}" title="${esc(it.path)}"><span class="chev${open ? " open" : ""}">${it.dir ? "›" : ""}</span>${W.fileIcon(it.name, it.dir, 16, open)}<span class="tn">${esc(it.name)}</span></div>`; }).join("")
      || '<div class="wx-empty muted">Empty folder.</div>';
  }

  // --- editor tabs ------------------------------------------------------------------------
  const viewActions = {
    changed: () => renderTabs(),
    async save(tab) {
      try {
        await api("/api/files/write", { method: "PUT", body: { path: tab.path, text: tab.text } });
        tab.dirty = false; if (tab.file) tab.file.text = tab.text;
        toast(`Saved ${tab.name}`); renderTabs(); W.renderView(tab, tab.host, viewActions); refresh();
      } catch (e) { toast(e.message); }
    },
  };
  W.openPath = async (path, line) => {
    const item = S.items.get(path);
    if (item?.dir) { await reveal(path + "/x"); return toggleDir(path, true); }
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
        { label: "Reveal in explorer", act: () => reveal(t.path) },
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
  W.reloadCleanTabs = () => S.tabs.forEach((t) => { if (!t.dirty) { t.file = null; t.text = null; t.pdf = null; t.stamp = Date.now(); W.renderView(t, t.host, viewActions); } });

  // --- view ------------------------------------------------------------------------------
  // The Workspace and the File Manager are the same windows: the File Manager hides Claude Code.
  async function viewWorkspace(quiet, noClaude = false) {
    $("#view").classList.add("flush");
    if (S.el) {
      S.noClaude = noClaude;
      if (S.el.parentNode !== $("#view")) { $("#view").replaceChildren(S.el); refresh(); }
      applySizes();
      return;
    }
    S.noClaude = noClaude;
    S.el = document.createElement("div");
    S.el.className = "wsp";
    S.el.innerHTML = LAYOUT;
    $("#view").replaceChildren(S.el);
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
    for (const r of read("ac.wsp.panes", [])) if (S.roots.some((x) => x.path === r)) addPane(r);
    const saved = read("ac.wsp.tabs", { open: [] });
    for (const p of saved.open || []) await W.openPath(p);
    if (saved.active && S.tabs.some((t) => t.path === saved.active)) activate(S.tabs.find((t) => t.path === saved.active));
    // Poll gently: every 10 s while things change, up to 60 s when nothing did; never while hidden. Coming back
    // to the page refreshes at once.
    const tick = () => {
      if (S.el.isConnected && !document.hidden && !menuEl) refresh().catch(() => {});
      setTimeout(tick, Math.min(60000, 10000 * (1 + Math.floor((S.idle || 0) / 3))));
    };
    setTimeout(tick, 10000);
    addEventListener("focus", () => { if (S.el.isConnected) { S.idle = 0; refresh().catch(() => {}); } });
    document.addEventListener("keydown", (e) => {
      if (!S.el.isConnected) return;
      if (e.ctrlKey && e.key === "`") { e.preventDefault(); W.toggleTerminal(true); }
      else if (e.ctrlKey && e.key.toLowerCase() === "l" && !e.target.closest(".xterm")) { e.preventDefault(); W.focusClaude?.(); }
    });
  }

  registerView({ id: "workspace", title: "Workspace", icon: "workspace", admin: true }, (q) => viewWorkspace(q, false), 0);
  W.viewFileManager = (q) => viewWorkspace(q, true);
})();
