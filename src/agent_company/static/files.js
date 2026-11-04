"use strict";
// Folder picker, file preview modal and Markdown rendering used across the dashboard (projects, research).
// The File Manager page itself is the Workspace without Claude Code (workspace.js).

const FILE_ICON = { folder: "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z", python: "M8 6l-6 6 6 6M16 6l6 6-6 6", javascript: "M8 6l-6 6 6 6M16 6l6 6-6 6", typescript: "M8 6l-6 6 6 6M16 6l6 6-6 6", markdown: "M4 6h16v12H4zM7 15V9l2.5 3L12 9v6M15 9v6l2-2 2 2", json: "M8 4c-2 0-2 2-2 4s-2 4-2 4 2 2 2 4 0 4 2 4M16 4c2 0 2 2 2 4s2 4 2 4-2 2-2 4 0 4-2 4", image: "M4 5h16v14H4zM8 10a1.5 1.5 0 1 0 0-.1M20 16l-5-5-8 8", shell: "M4 17l6-5-6-5M12 19h8", file: "M6 2h9l5 5v15H6zM14 2v6h6" };
const fileIcon = (k) => `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${FILE_ICON[k] || FILE_ICON.file}"/></svg>`;
let panes = [];          // [{path, selected:Set}]
let fileClipboard = null; // {paths, cut}

async function viewFiles() {
  const roots = await api("/api/files/roots");
  if (!panes.length) panes = [{ path: store.get("ac.files") || roots[0]?.path, selected: new Set() }];
  $("#view").innerHTML = `<div class="row" style="margin-bottom:8px"><select id="f-root" style="width:auto">${roots.map((r) => `<option value="${esc(r.path)}">${esc(r.name)} · ${esc(r.path)}</option>`).join("")}</select>
    <button class="btn" id="f-split" title="Open another pane">+ Pane</button><span class="muted grow">Ctrl/Shift+click selects several · drag onto a folder to move · double-click opens</span></div><div class="fpanes" id="fpanes"></div>`;
  $("#f-root").value = roots.some((r) => r.path === panes[0].path) ? panes[0].path : $("#f-root").value;
  $("#f-root").onchange = () => { panes[0] = { path: $("#f-root").value, selected: new Set() }; renderPanes(); };
  $("#f-split").onclick = () => { if (panes.length < 3) { panes.push({ path: panes[panes.length - 1].path, selected: new Set() }); renderPanes(); } };
  renderPanes();
}

async function renderPanes() {
  $("#fpanes").style.gridTemplateColumns = `repeat(${panes.length}, minmax(0, 1fr))`;
  $("#fpanes").innerHTML = panes.map((_, i) => `<div class="card fpane" data-pane="${i}"></div>`).join("");
  await Promise.all(panes.map((_, i) => renderPane(i)));
}

async function renderPane(i) {
  const pane = panes[i];
  const el = document.querySelector(`[data-pane="${i}"]`);
  let d;
  try { d = await api(`/api/files/list?path=${encodeURIComponent(pane.path)}`); } catch (e) { el.innerHTML = `<p class="muted">${esc(e.message)}</p>`; return; }
  pane.path = d.path; if (i === 0) store.set("ac.files", d.path);
  const sel = pane.selected;
  el.innerHTML = `<div class="row nowrap"><strong class="grow mono" title="${esc(d.path)}">${esc(d.path)}</strong>${panes.length > 1 ? `<button class="icon-btn" data-close="${i}" aria-label="Close pane">✕</button>` : ""}</div>
    <div class="row ftools"><button class="btn sm" data-a="up" ${d.parent ? "" : "disabled"}>Up</button><button class="btn sm" data-a="newfile">New file</button><button class="btn sm" data-a="newdir">New folder</button><label class="btn sm">Upload<input type="file" multiple hidden data-a="upload"></label>
      <button class="btn sm" data-a="rename" ${sel.size === 1 ? "" : "disabled"}>Rename</button><button class="btn sm" data-a="copy" ${sel.size ? "" : "disabled"}>Copy</button><button class="btn sm" data-a="cut" ${sel.size ? "" : "disabled"}>Cut</button><button class="btn sm" data-a="paste" ${fileClipboard ? "" : "disabled"}>Paste${fileClipboard ? ` (${fileClipboard.paths.length})` : ""}</button><button class="btn sm danger" data-a="delete" ${sel.size ? "" : "disabled"}>Delete</button></div>
    <div class="flist">${d.items.map((it, n) => `<div class="fitem ${sel.has(it.path) ? "selected" : ""}" draggable="true" data-n="${n}" data-path="${esc(it.path)}" data-dir="${it.dir}"><span class="ficon k-${it.kind}">${fileIcon(it.kind)}</span><span class="grow ellipsis">${esc(it.name)}</span><span class="muted">${it.dir ? "" : fmtSize(it.size)}</span></div>`).join("") || '<p class="muted">Empty folder.</p>'}</div>`;
  const items = d.items;
  let anchor = null;
  el.querySelectorAll(".fitem").forEach((row) => {
    const it = items[Number(row.dataset.n)];
    row.onclick = (e) => {
      if (e.shiftKey && anchor !== null) { const [a, b] = [anchor, Number(row.dataset.n)].sort((x, y) => x - y); for (let k = a; k <= b; k++) sel.add(items[k].path); }
      else if (e.ctrlKey || e.metaKey) { sel.has(it.path) ? sel.delete(it.path) : sel.add(it.path); anchor = Number(row.dataset.n); }
      else { sel.clear(); sel.add(it.path); anchor = Number(row.dataset.n); }
      renderPane(i);
    };
    row.ondblclick = () => { if (it.dir) { pane.path = it.path; sel.clear(); renderPane(i); } else openFile(it.path, it.kind, i); };
    row.ondragstart = (e) => { const paths = sel.has(it.path) ? [...sel] : [it.path]; e.dataTransfer.setData("text/x-paths", JSON.stringify(paths)); };
    if (it.dir) {
      row.ondragover = (e) => { e.preventDefault(); row.classList.add("drop"); };
      row.ondragleave = () => row.classList.remove("drop");
      row.ondrop = async (e) => { e.preventDefault(); row.classList.remove("drop"); await moveTo(JSON.parse(e.dataTransfer.getData("text/x-paths") || "[]"), it.path, false); };
    }
  });
  el.ondragover = (e) => e.preventDefault();
  el.ondrop = async (e) => { if (e.target.closest(".fitem[data-dir=true]")) return; e.preventDefault(); const raw = e.dataTransfer.getData("text/x-paths"); if (raw) await moveTo(JSON.parse(raw), pane.path, false); };
  el.querySelector("[data-close]") && (el.querySelector("[data-close]").onclick = () => { panes.splice(i, 1); renderPanes(); });
  el.querySelectorAll("[data-a]").forEach((b) => {
    const a = b.dataset.a;
    if (a === "upload") { b.onchange = async (e) => { for (const f of e.target.files) { const fd = new FormData(); fd.append("file", f); const r = await fetch(`/api/files/upload?folder=${encodeURIComponent(pane.path)}`, { method: "POST", body: fd, credentials: "same-origin" }); if (!r.ok) toast(`${f.name}: ${(await r.json()).detail}`); } renderPanes(); }; return; }
    b.onclick = () => fileAction(a, i, d);
  });
}

const fmtSize = (n) => (n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`);

async function moveTo(paths, dest, copy) {
  try { for (const p of paths) if (p !== dest) await api("/api/files/move", { method: "POST", body: { src: p, dest, copy } }); }
  catch (e) { toast(e.message); }
  panes.forEach((p) => p.selected.clear()); renderPanes();
}

async function fileAction(a, i, d) {
  const pane = panes[i], sel = [...pane.selected];
  try {
    if (a === "up") { pane.path = d.parent; pane.selected.clear(); return renderPane(i); }
    if (a === "newfile" || a === "newdir") { const name = prompt(a === "newfile" ? "File name" : "Folder name"); if (!name) return; await api("/api/files/create", { method: "POST", body: { path: `${pane.path}/${name}`, folder: a === "newdir" } }); }
    if (a === "rename") { const old = sel[0], name = prompt("New name", old.split("/").pop()); if (!name) return; await api("/api/files/move", { method: "POST", body: { src: old, dest: `${old.slice(0, old.lastIndexOf("/"))}/${name}` } }); pane.selected.clear(); }
    if (a === "copy" || a === "cut") { fileClipboard = { paths: sel, cut: a === "cut" }; toast(`${sel.length} item(s) ${a === "cut" ? "cut" : "copied"}`); }
    if (a === "paste") { await moveTo(fileClipboard.paths, pane.path, !fileClipboard.cut); if (fileClipboard.cut) fileClipboard = null; return; }
    if (a === "delete") { if (!confirm(`Delete ${sel.length} item(s)? Folders are deleted with their contents.`)) return; await api("/api/files/delete", { method: "POST", body: { paths: sel } }); pane.selected.clear(); }
  } catch (e) { toast(e.message); }
  renderPanes();
}

// --- viewer / editor -------------------------------------------------------------------
const KEYWORDS = /\b(def|class|return|if|elif|else|for|while|import|from|as|with|try|except|finally|raise|async|await|lambda|yield|None|True|False|null|true|false|function|const|let|var|new|export|default|switch|case|break|continue|interface|type|extends|implements|public|private|static|func|package|struct|fn|impl|use|mut|select|insert|update|delete|where|join|on|group|order|by|limit|and|or|not|in|is)\b/g;
function highlight(text, kind) {
  let h = esc(text);
  if (["text", "csv", "file", "env"].includes(kind)) return h;
  const slots = [];
  const keep = (cls) => (m) => { slots.push(`<span class="${cls}">${m}</span>`); return `\u0000${slots.length - 1}\u0000`; };
  h = h.replace(/(&quot;|&#39;|`)(?:\\.|(?!\1)[^\\\n])*\1/g, keep("tk-str"));
  h = h.replace(kind === "python" || kind === "shell" || kind === "yaml" || kind === "toml" ? /#.*$/gm : kind === "sql" ? /--.*$/gm : /\/\/.*$|\/\*[\s\S]*?\*\//gm, keep("tk-com"));
  h = h.replace(/\b\d+(\.\d+)?\b/g, keep("tk-num"));
  h = h.replace(KEYWORDS, keep("tk-kw"));
  return h.replace(/\u0000(\d+)\u0000/g, (_, n) => slots[Number(n)]);
}
function renderMarkdown(md) {
  const blocks = [];
  let src = md.replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) => { blocks.push(`<pre class="code">${highlight(code, lang === "py" ? "python" : lang || "text")}</pre>`); return `\u0001${blocks.length - 1}\u0001`; });
  src = esc(src);
  const inline = (t) => t.replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>").replace(/\*([^*]+)\*/g, "<em>$1</em>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+|[^)\s:]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  const out = [];
  let list = null;
  for (const line of src.split("\n")) {
    const m = line.match(/^(#{1,6})\s+(.*)$/), li = line.match(/^\s*[-*]\s+(.*)$/), ol = line.match(/^\s*\d+\.\s+(.*)$/);
    if ((li || ol) && !list) { list = li ? "ul" : "ol"; out.push(`<${list}>`); }
    if (!(li || ol) && list) { out.push(`</${list}>`); list = null; }
    if (m) out.push(`<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`);
    else if (li || ol) out.push(`<li>${inline((li || ol)[1])}</li>`);
    else if (/^\|.*\|$/.test(line.trim())) out.push(`<div class="mono">${inline(line)}</div>`);
    else if (line.trim()) out.push(`<p>${inline(line)}</p>`);
  }
  if (list) out.push(`</${list}>`);
  return out.join("\n").replace(/\u0001(\d+)\u0001/g, (_, n) => blocks[Number(n)]);
}

async function openFile(path, kind, paneIndex) {
  if (kind === "image") { modal(`<h2 class="mono">${esc(path)}</h2><img src="/api/files/raw?path=${encodeURIComponent(path)}" alt="" style="max-width:100%">`); return; }
  const f = await api(`/api/files/read?path=${encodeURIComponent(path)}`);
  if (f.binary) { modal(`<h2 class="mono">${esc(path)}</h2><p class="muted">Binary or large file (${fmtSize(f.size)}); not shown.</p>`); return; }
  const view = (mode) => {
    $("#modal-body").innerHTML = `<div class="row nowrap"><h2 class="grow mono ellipsis" style="margin:0">${esc(path)}</h2>${f.kind === "markdown" ? `<button class="btn sm" data-m="${mode === "md" ? "code" : "md"}">${mode === "md" ? "Source" : "Preview"}</button>` : ""}<button class="btn sm" data-m="edit">Edit</button></div>
      ${mode === "edit" ? `<textarea id="f-text" class="mono" style="min-height:60vh;margin-top:8px">${esc(f.text)}</textarea><div class="row" style="margin-top:8px"><button class="btn primary" id="f-save">Save</button><button class="btn" data-m="code">Cancel</button></div>`
        : mode === "md" ? `<div class="md">${renderMarkdown(f.text)}</div>` : `<pre class="code">${highlight(f.text, f.kind)}</pre>`}`;
    $("#modal-body").querySelectorAll("[data-m]").forEach((b) => (b.onclick = () => view(b.dataset.m)));
    $("#f-save") && ($("#f-save").onclick = async () => { try { await api("/api/files/write", { method: "PUT", body: { path, text: $("#f-text").value } }); f.text = $("#f-text").value; toast("Saved"); view(f.kind === "markdown" ? "md" : "code"); renderPanes(); } catch (e) { toast(e.message); } });
  };
  modal(""); view(f.kind === "markdown" ? "md" : "code");
}

// Folder picker for project creation: folder names anywhere, admin only.
function pickFolder(start) {
  return new Promise((resolve) => {
    const holder = document.createElement("div");
    holder.className = "modal"; holder.innerHTML = `<div class="modal-card" style="width:min(640px,100%)"><div id="pf-body"></div></div>`;
    document.body.append(holder);
    const close = (v) => { holder.remove(); resolve(v); };
    const load = async (p) => {
      let d; try { d = await api(`/api/files/folders?path=${encodeURIComponent(p || "")}`); } catch (e) { toast(e.message); return; }
      holder.querySelector("#pf-body").innerHTML = `<h2>Choose the work folder</h2><div class="row nowrap"><code class="grow ellipsis">${esc(d.path)}</code>${d.git ? pill("git") : ""}</div>
        <div class="flist" style="max-height:50vh;margin-top:8px">${d.parent ? `<div class="fitem" data-p="${esc(d.parent)}">${fileIcon("folder")} ..</div>` : ""}${d.folders.map((f) => `<div class="fitem" data-p="${esc(f.path)}">${fileIcon("folder")}<span class="grow">${esc(f.name)}</span>${f.git ? '<span class="pill">git</span>' : ""}</div>`).join("")}</div>
        <div class="row" style="margin-top:10px"><button class="btn primary" id="pf-ok">Use this folder</button><button class="btn" id="pf-cancel">Cancel</button></div>`;
      holder.querySelectorAll("[data-p]").forEach((el) => (el.ondblclick = el.onclick = () => load(el.dataset.p)));
      holder.querySelector("#pf-ok").onclick = () => close(d.path);
      holder.querySelector("#pf-cancel").onclick = () => close(null);
    };
    load(start);
  });
}

// The File Manager is the Workspace's explorer, preview and terminal (workspace.js), without Claude Code; its "+"
// adds explorer panes. The pane code above stays for reference by nothing else and is not registered.
registerView({ id: "files", title: "File Manager", icon: "folder", admin: true }, (q) => WSP.viewFileManager(q));
