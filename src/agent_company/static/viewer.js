"use strict";
// Workspace viewer: syntax colouring per language, a Markdown renderer, and per-type file
// views - code (line numbers, edit + Ctrl+S), Markdown, CSV tables, JSON, images and SVG,
// PDF (pdf.js), DOCX (mammoth), audio and video. Exposes helpers on window.WSP.
window.WSP = window.WSP || {};
(() => {
  const W = window.WSP;
  const rawUrl = (p, download) => `/api/files/raw?path=${encodeURIComponent(p)}${download ? "&download=1" : ""}`;
  W.rawUrl = rawUrl;
  W.size = (n) => (n == null ? "" : n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`);

  const loaded = {};
  W.loadScript = (src) => (loaded[src] ||= new Promise((ok, fail) => {
    const s = document.createElement("script");
    s.src = src; s.onload = ok;
    s.onerror = () => { delete loaded[src]; fail(new Error(`could not load ${src}`)); };
    document.head.append(s);
  }));
  W.loadCss = (href) => {
    if (document.querySelector(`link[href="${href}"]`)) return;
    const l = document.createElement("link"); l.rel = "stylesheet"; l.href = href; document.head.append(l);
  };
  W.copyText = async (text, label = "Copied") => {
    try { await navigator.clipboard.writeText(text); toast(label); return; } catch { /* not a secure context: fall back */ }
    const t = document.createElement("textarea");
    t.value = text; t.style.cssText = "position:fixed;opacity:0"; document.body.append(t); t.select();
    try { document.execCommand("copy"); toast(label); } catch { toast("Copy failed"); }
    t.remove();
  };
  W.isDark = () => (document.documentElement.dataset.theme ? document.documentElement.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches);

  // --- syntax colouring ------------------------------------------------------------
  const words = (s) => `\\b(?:${s.trim().split(/\s+/).join("|")})\\b`;
  const STR = String.raw`"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'`;
  const BACKTICK = "`(?:\\\\.|[^`\\\\])*`";
  const NUM = String.raw`\b(?:0x[\da-fA-F_]+|0b[01_]+|\d[\d_]*(?:\.\d[\d_]*)?(?:[eE][+-]?\d+)?)\b`;
  const FN = String.raw`\b[A-Za-z_$][\w$]*(?=\s*\()`;
  const TYPE = String.raw`\b[A-Z][A-Za-z0-9_]*\b`;
  const OP = String.raw`=>|->|[=+\-*/%<>!&|^~?]+`;
  const CLIKE_KW = words(`abstract as async await break case catch class const continue default defer delete do else enum export extends
    final finally for fn from func function go goto if impl implements import in instanceof interface internal let loop match mod module
    move mut namespace new override package private protected pub public readonly return sealed select static struct super switch throw
    throws trait try type typeof unsafe use using var void where while with yield def end elsif unless require begin rescue local then`);
  const CLIKE_LIT = words("true false null undefined nil NaN Infinity this self None");
  const clikeBody = [["str", BACKTICK + "|" + STR], ["deco", String.raw`@[\w.]+`], ["kw", CLIKE_KW], ["lit", CLIKE_LIT], ["num", NUM], ["fn", FN], ["type", TYPE], ["op", OP]];
  const LANGS = {
    clike: [["com", String.raw`\/\/.*|\/\*[\s\S]*?\*\/`], ...clikeBody],
    hashlike: [["com", String.raw`(?<![\w$])#(?![{!]).*`], ...clikeBody],
    lua: [["com", String.raw`--\[\[[\s\S]*?\]\]|--.*`], ...clikeBody],
    python: [["com", "#.*"], ["str", String.raw`\b[rRbBfFuU]{0,2}(?:"""[\s\S]*?"""|'''[\s\S]*?''')`], ["str", String.raw`\b[rRbBfFuU]{1,2}(?:${STR})|${STR}`],
      ["deco", String.raw`@[\w.]+`], ["fn", String.raw`(?<=\bdef\s+)\w+`], ["type", String.raw`(?<=\bclass\s+)\w+`],
      ["kw", words("and as assert async await break class continue def del elif else except finally for from global if import in is lambda match case nonlocal not or pass raise return try while with yield")],
      ["lit", words("True False None self cls")],
      ["builtin", words("print len range enumerate zip map filter sorted reversed list dict set frozenset tuple str int float bool bytes open isinstance issubclass super type sum min max any all abs repr getattr setattr hasattr iter next round format input id hash vars dir callable")],
      ["num", NUM], ["fn", FN], ["type", TYPE], ["op", OP]],
    shell: [["com", String.raw`(?<![\w$#])#.*`], ["str", STR], ["var", String.raw`\$\{[^}\n]*\}|\$\(|\$[\w@#?$!*]+`],
      ["kw", words("if then else elif fi for while until do done case esac in function return local export source alias unset set shift exit break continue sudo")],
      ["fn", String.raw`(?<=^[ \t]*|[|;&][ \t]*|\b(?:then|do|else)[ \t]+)[\w./-]+`], ["attr", String.raw`(?<=\s)--?[\w-]+`], ["num", NUM], ["op", String.raw`&&|\|\||[|><;&]`]],
    sql: { ci: true, rules: [["com", String.raw`--.*|\/\*[\s\S]*?\*\/`], ["str", STR],
      ["kw", words("select from where and or not insert into values update set delete create table index view drop alter add column primary key foreign references join left right inner outer full on group by order having limit offset as distinct union all case when then else end is null like in between exists returning with default unique check begin commit rollback transaction")],
      ["fn", FN], ["num", NUM], ["op", String.raw`[=<>!*+\-/%]+`]] },
    json: [["key", String.raw`"(?:\\.|[^"\\\n])*"(?=\s*:)`], ["str", STR], ["lit", words("true false null")], ["num", String.raw`-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b`], ["punct", String.raw`[{}\[\],:]`]],
    yaml: [["com", String.raw`(?<!\S)#.*`], ["key", String.raw`^[ \t]*(?:- )?[^\s#:'"\-][^:#\n]*?(?=:(?:\s|$))`], ["str", STR], ["var", String.raw`[&*][\w-]+|\$\{[^}\n]*\}`],
      ["lit", words("true false null yes no on off")], ["num", NUM], ["punct", String.raw`^[ \t]*-(?= )|[\[\]{},|>]`]],
    toml: [["com", String.raw`^\s*[#;].*|(?<=\s)#.*`], ["head", String.raw`^\s*\[[^\]\n]*\]+`], ["key", String.raw`^\s*(?:export\s+)?[\w.\-"']+(?=\s*=)`],
      ["str", String.raw`"""[\s\S]*?"""|` + STR], ["lit", words("true false")], ["num", NUM], ["var", String.raw`\$\{[^}\n]*\}|\$\w+`]],
    html: [["com", String.raw`<!--[\s\S]*?-->`], ["tag", String.raw`<\/?[\w:.-]+|\/?>|<!DOCTYPE[^>]*>`], ["attr", String.raw`(?<=\s)[\w:@.#\-]+(?=\s*=)`], ["str", STR], ["lit", String.raw`&#?\w+;`]],
    css: [["com", String.raw`\/\*[\s\S]*?\*\/`], ["str", STR], ["kw", String.raw`@[\w-]+|!important`],
      ["num", String.raw`#[\da-fA-F]{3,8}\b|-?(?:\b\d+\.?\d*|\.\d+)(?:px|em|rem|%|vh|vw|vmin|vmax|s|ms|deg|fr|ch|ex)?\b`],
      ["prop", String.raw`(?<=[{;\s])[\w-]+(?=\s*:[^:])`], ["fn", FN], ["sel", String.raw`[.#][A-Za-z_][\w-]*|::?[\w-]+`], ["punct", "[{}();:,]"]],
    markdown: [["head", "^#{1,6} .*"], ["com", "^>.*"], ["str", "^(?:```|~~~).*|`[^`\\n]+`"], ["kw", String.raw`^\s*(?:[-*+]|\d+[.)])\s`],
      ["strong", String.raw`\*\*[^*\n]+\*\*|__[^_\n]+__`], ["em", String.raw`(?<![*\w])\*[^*\n]+\*(?![*\w])`], ["attr", String.raw`!?\[[^\]\n]*\]\([^)\n]*\)`], ["lit", String.raw`^\s*(?:-{3,}|\*{3,})\s*$`]],
    diff: [["head", String.raw`^(?:diff|index|---|\+\+\+) .*`], ["hunk", "^@@.*"], ["add", String.raw`^\+.*`], ["del", "^-.*"]],
    log: [["time", String.raw`\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?|\b\d{2}:\d{2}:\d{2}(?:[.,]\d+)?\b`],
      ["err", String.raw`\b(?:ERROR|ERR|FATAL|CRITICAL|CRIT|FAIL(?:ED|URE)?|Traceback|Exception|panic|error)\b`], ["warn", String.raw`\b(?:WARN(?:ING)?|DEPRECATED|warning)\b`],
      ["info", String.raw`\b(?:INFO|NOTICE|OK|SUCCESS|PASS(?:ED)?)\b`], ["dbg", String.raw`\b(?:DEBUG|TRACE)\b`], ["str", STR], ["attr", String.raw`https?:\/\/\S+`], ["num", NUM]],
    docker: [["com", String.raw`^\s*#.*`], ["kw", String.raw`^\s*(?:FROM|RUN|CMD|LABEL|EXPOSE|ENV|ADD|COPY|ENTRYPOINT|VOLUME|USER|WORKDIR|ARG|ONBUILD|STOPSIGNAL|HEALTHCHECK|SHELL)\b|\bAS\b`],
      ["str", STR], ["var", String.raw`\$\{[^}\n]*\}|\$\w+`], ["attr", String.raw`(?<=\s)--[\w-]+`], ["num", NUM]],
    make: [["com", "#.*"], ["fn", String.raw`^[\w.%/\- ]+(?=:(?!=))`], ["var", String.raw`\$[({][^)}\n]*[)}]|\$[@<^?*]`], ["kw", words("ifeq ifneq ifdef ifndef else endif include define endef export")], ["str", STR]],
  };
  const LANG_OF = { python: "python", py: "python", javascript: "clike", js: "clike", jsx: "clike", typescript: "clike", ts: "clike", tsx: "clike", java: "clike", c: "clike", cpp: "clike",
    csharp: "clike", cs: "clike", go: "clike", rust: "clike", rs: "clike", kotlin: "clike", swift: "clike", php: "clike", dart: "clike", scala: "clike",
    ruby: "hashlike", rb: "hashlike", r: "hashlike", lua: "lua", shell: "shell", sh: "shell", bash: "shell", zsh: "shell", console: "shell",
    sql: "sql", json: "json", jsonc: "clike", yaml: "yaml", yml: "yaml", toml: "toml", ini: "toml", env: "toml", html: "html", xml: "html", svg: "html", vue: "html",
    css: "css", scss: "css", less: "css", markdown: "markdown", md: "markdown", diff: "diff", patch: "diff", log: "log", docker: "docker", dockerfile: "docker", make: "make", makefile: "make" };
  const compiled = {};
  function compile(lang) {
    const id = LANG_OF[String(lang || "").toLowerCase()];
    if (!id) return null;
    if (compiled[id]) return compiled[id];
    const spec = LANGS[id], rules = spec.rules || spec;
    const re = new RegExp(rules.map(([, src], i) => `(?<g${i}>${src})`).join("|"), "gm" + (spec.ci ? "i" : ""));
    return (compiled[id] = { re, classes: rules.map(([c]) => c) });
  }
  W.hl = (text, lang) => {
    const c = text.length < 600000 ? compile(lang) : null;
    if (!c) return esc(text);
    let out = "", last = 0;
    for (const m of text.matchAll(c.re)) {
      if (!m[0]) continue;
      let cls = "x";
      for (let k = 0; k < c.classes.length; k++) if (m.groups[`g${k}`] !== undefined) { cls = c.classes[k]; break; }
      out += esc(text.slice(last, m.index)) + `<span class="t-${cls}">${esc(m[0])}</span>`;
      last = m.index + m[0].length;
    }
    return out + esc(text.slice(last));
  };

  // --- Markdown ----------------------------------------------------------------------
  const joinPath = (base, rel) => {
    if (rel.startsWith("/")) return rel;
    const parts = (base || "").split("/").filter(Boolean);
    for (const seg of rel.split("/")) { if (seg === "..") parts.pop(); else if (seg && seg !== ".") parts.push(seg); }
    return "/" + parts.join("/");
  };
  const isExternal = (u) => /^(https?:|mailto:|data:image\/)/i.test(u);
  function emphasis(t) {
    return t.replace(/\*\*\*(?=\S)([\s\S]+?)\*\*\*/g, "<strong><em>$1</em></strong>")
      .replace(/\*\*(?=\S)([\s\S]+?)\*\*|__(?=\S)([\s\S]+?)__/g, (_, a, b) => `<strong>${a ?? b}</strong>`)
      .replace(/(?<![\w*])\*(?=\S)([^*\n]+?)\*(?![\w*])|(?<![\w_])_(?=\S)([^_\n]+?)_(?![\w_])/g, (_, a, b) => `<em>${a ?? b}</em>`)
      .replace(/~~(?=\S)([\s\S]+?)~~/g, "<del>$1</del>");
  }
  function inline(t, base) {
    const slots = [];
    const keep = (html) => { slots.push(html); return `\u0000${slots.length - 1}\u0000`; };
    t = t.replace(/(`+)([\s\S]*?[^`])\1(?!`)/g, (_, __, c) => keep(`<code>${esc(c.trim())}</code>`));
    t = t.replace(/!\[([^\]]*)\]\(\s*<?([^)\s>]+)>?(?:\s+"([^"]*)")?\s*\)/g, (_, alt, src, title) => {
      const url = isExternal(src) ? src : base != null ? rawUrl(joinPath(base, src)) : "";
      return keep(url ? `<img src="${esc(url)}" alt="${esc(alt)}"${title ? ` title="${esc(title)}"` : ""} loading="lazy">` : esc(alt));
    });
    t = t.replace(/\[([^\]]+)\]\(\s*<?([^)\s>]+)>?(?:\s+"[^"]*")?\s*\)/g, (_, label, href) => {
      const text = emphasis(esc(label));
      if (/^#/.test(href)) return keep(`<a href="${esc(href)}">${text}</a>`);
      if (isExternal(href)) return keep(`<a href="${esc(href)}" target="_blank" rel="noopener noreferrer">${text}</a>`);
      if (base != null && !/^[a-z]+:/i.test(href)) return keep(`<a href="#" data-open="${esc(joinPath(base, href.split("#")[0]))}">${text}</a>`);
      return keep(text);
    });
    t = t.replace(/<(https?:\/\/[^>\s]+)>|(?<![\w"'=(/])https?:\/\/[^\s<>)\]]+[^\s<>)\].,;:!?'"]/g, (m, a) => { const u = a || m; return keep(`<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(u)}</a>`); });
    t = emphasis(esc(t)).replace(/ {2,}\n|\\\n/g, "<br>").replace(/\n/g, " ");
    return t.replace(/\u0000(\d+)\u0000/g, (_, n) => slots[Number(n)]);
  }
  W.codeBlock = (code, lang) => `<div class="cblock"><div class="cbar"><span>${esc(lang || "text")}</span><button type="button" class="cbtn" data-copy-code>Copy</button></div><pre class="code"><code>${W.hl(code, lang)}</code></pre></div>`;
  const indentOf = (l) => l.match(/^\s*/)[0].replace(/\t/g, "    ").length;
  const LIST_RE = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
  const isBlockStart = (l) => /^\s*(```|~~~|#{1,6}\s|>|([-*_])(\s*\2){2,}\s*$)/.test(l) || LIST_RE.test(l) || /^\s*\|.*\|\s*$/.test(l);
  const CALLOUT = { NOTE: "note", TIP: "tip", IMPORTANT: "important", WARNING: "warning", CAUTION: "caution" };

  W.md = function md(src, base) {
    const lines = String(src).replace(/\r\n?/g, "\n").split("\n");
    const out = [];
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      let m = line.match(/^\s*(`{3,}|~{3,})\s*([\w+#.-]*)/);
      if (m) {
        const buf = [];
        i++;
        while (i < lines.length && !lines[i].trim().startsWith(m[1])) buf.push(lines[i++]);
        i++;
        out.push(W.codeBlock(buf.join("\n"), m[2]));
        continue;
      }
      if (!line.trim()) { i++; continue; }
      if ((m = line.match(/^(#{1,6})\s+(.*?)\s*#*\s*$/))) {
        const slug = m[2].toLowerCase().replace(/[^\w\s-]/g, "").trim().replace(/\s+/g, "-");
        out.push(`<h${m[1].length} id="${esc(slug)}">${inline(m[2], base)}</h${m[1].length}>`); i++; continue;
      }
      if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) { out.push("<hr>"); i++; continue; }
      if (/^\s*>/.test(line)) {
        const buf = [];
        while (i < lines.length && /^\s*>/.test(lines[i])) buf.push(lines[i++].replace(/^\s*> ?/, ""));
        const c = buf[0]?.match(/^\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*$/i);
        if (c) out.push(`<div class="callout ${CALLOUT[c[1].toUpperCase()]}"><div class="callout-title">${esc(c[1][0].toUpperCase() + c[1].slice(1).toLowerCase())}</div>${md(buf.slice(1).join("\n"), base)}</div>`);
        else out.push(`<blockquote>${md(buf.join("\n"), base)}</blockquote>`);
        continue;
      }
      if (/^\s*\|.*\|\s*$/.test(line) && /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(lines[i + 1] || "")) {
        const cells = (l) => l.trim().replace(/^\||\|$/g, "").split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, "|"));
        const head = cells(line), align = cells(lines[i + 1]).map((c) => (c.endsWith(":") ? (c.startsWith(":") ? "center" : "right") : ""));
        i += 2;
        const rows = [];
        while (i < lines.length && /^\s*\|.*\|?\s*$/.test(lines[i]) && lines[i].includes("|")) rows.push(cells(lines[i++]));
        const td = (tag, c, k) => `<${tag}${align[k] ? ` style="text-align:${align[k]}"` : ""}>${inline(c, base)}</${tag}>`;
        out.push(`<div class="table-wrap"><table><thead><tr>${head.map((c, k) => td("th", c, k)).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${r.map((c, k) => td("td", c, k)).join("")}</tr>`).join("")}</tbody></table></div>`);
        continue;
      }
      if (LIST_RE.test(line)) {
        const base0 = indentOf(line), ordered = /^\s*\d/.test(line), start = ordered ? parseInt(line, 10) || 1 : 1;
        const items = [];
        while (i < lines.length) {
          const l = lines[i], mm = l.match(LIST_RE);
          if (mm && indentOf(l) === base0) { if (/^\d/.test(mm[2]) !== ordered) break; items.push([mm[3]]); i++; continue; }
          if (items.length && l.trim() && indentOf(l) > base0) { items[items.length - 1].push(l); i++; continue; }
          if (items.length && !l.trim() && i + 1 < lines.length && lines[i + 1].trim() && indentOf(lines[i + 1]) > base0) { items[items.length - 1].push(""); i++; continue; }
          if (items.length && l.trim() && !isBlockStart(l) && indentOf(l) <= base0 && !mm) { items[items.length - 1][0] += "\n" + l.trim(); i++; continue; }
          break;
        }
        const tag = ordered ? "ol" : "ul";
        out.push(`<${tag}${ordered && start !== 1 ? ` start="${start}"` : ""}>${items.map((it) => {
          let first = it[0];
          const task = first.match(/^\[([ xX])\]\s+([\s\S]*)$/);
          if (task) first = task[2];
          const rest = it.slice(1);
          const minIndent = Math.min(...rest.filter((r) => r.trim()).map(indentOf), 99);
          const nested = rest.length ? md(rest.map((r) => r.slice(Math.min(minIndent, r.length))).join("\n"), base) : "";
          return `<li${task ? ' class="task"' : ""}>${task ? `<input type="checkbox" disabled${task[1] !== " " ? " checked" : ""}> ` : ""}${inline(first, base)}${nested}</li>`;
        }).join("")}</${tag}>`);
        continue;
      }
      const buf = [line];
      i++;
      while (i < lines.length && lines[i].trim() && !isBlockStart(lines[i])) buf.push(lines[i++]);
      out.push(`<p>${inline(buf.join("\n"), base)}</p>`);
    }
    return out.join("\n");
  };

  // Copy buttons on code blocks and links to workspace files, anywhere on the page.
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-copy-code]");
    if (b) { W.copyText(b.closest(".cblock").querySelector("pre").innerText); return; }
    const a = e.target.closest("a[data-open]");
    if (a && W.openPath) { e.preventDefault(); W.openPath(a.dataset.open); }
  });

  // --- CSV ------------------------------------------------------------------------------
  function parseCsv(text, sep) {
    const rows = [];
    let row = [], cell = "", q = false;
    for (let i = 0; i < text.length; i++) {
      const ch = text[i];
      if (q) {
        if (ch === '"') { if (text[i + 1] === '"') { cell += '"'; i++; } else q = false; } else cell += ch;
      } else if (ch === '"' && !cell) q = true;
      else if (ch === sep) { row.push(cell); cell = ""; }
      else if (ch === "\n" || ch === "\r") { if (ch === "\r" && text[i + 1] === "\n") i++; row.push(cell); rows.push(row); row = []; cell = ""; if (rows.length > 5000) break; }
      else cell += ch;
    }
    if (cell || row.length) { row.push(cell); rows.push(row); }
    return rows;
  }
  function csvTable(text, sep) {
    const rows = parseCsv(text, sep);
    if (!rows.length) return '<p class="muted">Empty file.</p>';
    const num = (v) => /^-?[\d,.]+%?$/.test(v.trim()) && v.trim() !== "";
    const [head, ...body] = rows;
    return `<div class="csv-wrap"><table class="csv"><thead><tr><th class="rn">#</th>${head.map((h, k) => `<th class="c${k % 8}">${esc(h)}</th>`).join("")}</tr></thead>
      <tbody>${body.map((r, n) => `<tr><td class="rn">${n + 1}</td>${r.map((c, k) => `<td class="c${k % 8}${num(c) ? " num" : ""}">${esc(c)}</td>`).join("")}</tr>`).join("")}</tbody></table>
      ${rows.length > 5000 ? '<p class="muted">Showing the first 5000 rows.</p>' : ""}</div>`;
  }

  // --- code view ----------------------------------------------------------------------
  function codeView(text, lang, wrap) {
    const n = text.split("\n").length;
    let gutter = "";
    for (let k = 1; k <= n; k++) gutter += k + "\n";
    return `<div class="codev${wrap ? " wrap" : ""}"><pre class="ln" aria-hidden="true">${gutter}</pre><pre class="src">${W.hl(text, lang)}</pre></div>`;
  }

  // --- views ------------------------------------------------------------------------
  const TEXT_MODES = { markdown: ["preview", "source"], csv: ["table", "source"], json: ["pretty", "source"], svg: ["image", "source"] };
  const MODE_LABEL = { preview: "Preview", source: "Source", table: "Table", pretty: "Formatted", image: "Image", edit: "Edit" };
  const BINARY_KINDS = new Set(["image", "pdf", "docx", "audio", "video", "archive", "sheet", "slides"]);

  W.defaultMode = (kind) => (TEXT_MODES[kind] ? TEXT_MODES[kind][0] : "code");

  // tab: {path, name, kind, mode, file, text, dirty, zoom}; host: element to fill.
  W.renderView = async function renderView(tab, host, actions) {
    const dir = tab.path.slice(0, tab.path.lastIndexOf("/")) || "/";
    const modes = TEXT_MODES[tab.kind] || (BINARY_KINDS.has(tab.kind) ? [] : ["code"]);
    const textual = !BINARY_KINDS.has(tab.kind);
    if (textual && !tab.file) {
      host.innerHTML = '<div class="wv-empty muted">Loading…</div>';
      try { tab.file = await api(`/api/files/read?path=${encodeURIComponent(tab.path)}`); } catch (e) { host.innerHTML = `<div class="wv-empty">${esc(e.message)}</div>`; return; }
      if (!tab.file.binary && tab.text == null) tab.text = tab.file.text;
    }
    const binaryText = textual && tab.file?.binary;
    const editable = textual && !binaryText;
    const crumbs = (W.rel ? W.rel(tab.path) : tab.path).split("/").filter(Boolean);
    host.innerHTML = `<div class="wv-bar"><div class="wv-crumbs mono" title="${esc(tab.path)}">${crumbs.map((c, k) => `<span${k === crumbs.length - 1 ? ' class="last"' : ""}>${esc(c)}</span>`).join('<i>›</i>')}</div>
      <div class="wv-actions">${editable ? [...modes, "edit"].map((m) => `<button class="seg${tab.mode === m ? " on" : ""}" data-mode="${m}">${MODE_LABEL[m] || "Code"}</button>`).join("") : ""}
      ${tab.mode === "edit" ? `<button class="btn sm primary" data-act="save" ${tab.dirty ? "" : "disabled"}>Save</button>` : ""}
      ${["code", "source"].includes(tab.mode) ? `<button class="icon-btn sm" data-act="wrap" title="Word wrap">↩</button>` : ""}
      ${["image", "pdf"].includes(tab.kind) || (tab.kind === "svg" && tab.mode === "image") ? `<button class="icon-btn sm" data-act="zoom-out" title="Zoom out">−</button><span class="muted zoom">${tab.zoom ? `${Math.round(tab.zoom * 100)}%` : "Fit"}</span><button class="icon-btn sm" data-act="zoom-in" title="Zoom in">+</button><button class="icon-btn sm" data-act="zoom-fit" title="Fit">⤢</button>` : ""}
      <button class="icon-btn sm" data-act="copy-path" title="Copy path">⧉</button><button class="icon-btn sm" data-act="reload" title="Reload">⟳</button><a class="icon-btn sm" href="${rawUrl(tab.path, true)}" title="Download" download>⤓</a></div></div>
      <div class="wv-body"></div>`;
    const body = host.querySelector(".wv-body");
    host.querySelectorAll("[data-mode]").forEach((b) => (b.onclick = () => { tab.mode = b.dataset.mode; renderView(tab, host, actions); }));
    host.querySelectorAll("[data-act]").forEach((b) => (b.onclick = async () => {
      const a = b.dataset.act;
      if (a === "save") return actions.save(tab);
      if (a === "wrap") { tab.wrap = !tab.wrap; body.querySelector(".codev")?.classList.toggle("wrap", tab.wrap); return; }
      if (a === "copy-path") return W.copyText(tab.path, "Path copied");
      if (a === "reload") { if (tab.dirty && !confirm("Discard unsaved changes?")) return; tab.file = null; tab.text = null; tab.dirty = false; tab.pdf = null; actions.changed(tab); return renderView(tab, host, actions); }
      if (a.startsWith("zoom")) { tab.zoom = a === "zoom-fit" ? null : Math.min(8, Math.max(0.1, (tab.zoom || 1) * (a === "zoom-in" ? 1.25 : 0.8))); return renderView(tab, host, actions); }
    }));

    if (binaryText) {
      body.innerHTML = infoCard(tab, tab.file.size > 2 * 1024 * 1024 ? "This file is larger than 2 MB." : "This is a binary file.");
      return;
    }
    if (tab.mode === "edit") {
      body.innerHTML = `<textarea class="wv-edit mono" spellcheck="false"></textarea>`;
      const ta = body.querySelector("textarea");
      ta.value = tab.text;
      ta.oninput = () => { tab.text = ta.value; if (!tab.dirty) { tab.dirty = true; actions.changed(tab); host.querySelector('[data-act="save"]')?.removeAttribute("disabled"); } };
      ta.onkeydown = (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); actions.save(tab); }
        else if (e.key === "Tab" && !e.shiftKey) { e.preventDefault(); ta.setRangeText(tab.kind === "python" ? "    " : "  ", ta.selectionStart, ta.selectionEnd, "end"); ta.oninput(); }
      };
      ta.focus();
      return;
    }
    const k = tab.kind;
    if (k === "markdown" && tab.mode === "preview") { body.innerHTML = `<article class="md wv-md">${W.md(tab.text, dir)}</article>`; return; }
    if (k === "csv" && tab.mode === "table") { body.innerHTML = csvTable(tab.text, tab.path.toLowerCase().endsWith(".tsv") ? "\t" : ","); return; }
    if (k === "json" && tab.mode === "pretty") {
      try { body.innerHTML = codeView(JSON.stringify(JSON.parse(tab.text), null, 2), "json", tab.wrap); } catch (e) { body.innerHTML = `<div class="wv-note bad">Not valid JSON: ${esc(e.message)}</div>${codeView(tab.text, "json", tab.wrap)}`; }
      return;
    }
    if (k === "svg" && tab.mode === "image") { body.innerHTML = imageHtml(tab); wireImage(tab, body, host); return; }
    if (textual) { body.innerHTML = codeView(tab.text, k, tab.wrap); return; }
    if (k === "image") { body.innerHTML = imageHtml(tab); wireImage(tab, body, host); return; }
    if (k === "audio") { body.innerHTML = `<div class="wv-media"><audio controls src="${rawUrl(tab.path)}"></audio></div>`; return; }
    if (k === "video") { body.innerHTML = `<div class="wv-media"><video controls src="${rawUrl(tab.path)}"></video></div>`; return; }
    if (k === "pdf") return pdfView(tab, body, host);
    if (k === "docx") return docxView(tab, body);
    body.innerHTML = infoCard(tab, k === "archive" ? "Archive. Extract it from the explorer's context menu, or download it." : "This file type has no preview.");
  };

  function infoCard(tab, msg) {
    return `<div class="wv-info"><div class="wv-info-icon">${W.fileIcon ? W.fileIcon(tab.name, false, 40) : ""}</div><strong>${esc(tab.name)}</strong>
      <span class="muted">${esc(msg)}${tab.file?.size != null ? ` · ${W.size(tab.file.size)}` : ""}</span><a class="btn" href="${rawUrl(tab.path, true)}" download>Download</a></div>`;
  }
  function imageHtml(tab) {
    const style = tab.zoom ? `style="width:auto;max-width:none;max-height:none;zoom:${tab.zoom}"` : "";
    return `<div class="wv-image checker"><img src="${rawUrl(tab.path)}${tab.stamp ? `&v=${tab.stamp}` : ""}" alt="${esc(tab.name)}" ${style}></div><div class="wv-status muted"></div>`;
  }
  function wireImage(tab, body) {
    const img = body.querySelector("img"), status = body.querySelector(".wv-status");
    img.onload = () => { status.textContent = `${img.naturalWidth} × ${img.naturalHeight} px`; };
    img.onerror = () => { status.textContent = "Could not display this image."; };
  }

  async function pdfView(tab, body) {
    body.innerHTML = '<div class="wv-empty muted">Loading PDF…</div>';
    try {
      await W.loadScript("/static/vendor/pdf.min.js");
      const lib = window.pdfjsLib;
      lib.GlobalWorkerOptions.workerSrc = "/static/vendor/pdf.worker.min.js";
      // isEvalSupported:false closes pdf.js 3.x's font-eval code path (CVE-2024-4367).
      tab.pdf ||= await lib.getDocument({ url: rawUrl(tab.path), withCredentials: true, isEvalSupported: false }).promise;
      const doc = tab.pdf;
      body.innerHTML = `<div class="wv-pdf"></div><div class="wv-status muted">${doc.numPages} page(s)</div>`;
      const holder = body.querySelector(".wv-pdf");
      const width = holder.clientWidth || 800;
      const first = await doc.getPage(1);
      const base = first.getViewport({ scale: 1 });
      const scale = tab.zoom || Math.min(2, Math.max(0.5, (width - 32) / base.width));
      const ratio = window.devicePixelRatio || 1;
      const io = new IntersectionObserver((entries) => entries.forEach(async (en) => {
        if (!en.isIntersecting || en.target.dataset.done) return;
        en.target.dataset.done = "1"; io.unobserve(en.target);
        const page = await doc.getPage(Number(en.target.dataset.page));
        const vp = page.getViewport({ scale: scale * ratio });
        const canvas = en.target.querySelector("canvas");
        canvas.width = vp.width; canvas.height = vp.height;
        await page.render({ canvasContext: canvas.getContext("2d"), viewport: vp }).promise;
      }), { root: holder.closest(".wv-body"), rootMargin: "600px" });
      for (let p = 1; p <= doc.numPages; p++) {
        const div = document.createElement("div");
        div.className = "pdf-page"; div.dataset.page = p;
        div.style.width = `${base.width * scale}px`; div.style.height = `${base.height * scale}px`;
        div.innerHTML = `<canvas style="width:100%;height:100%"></canvas><span class="pdf-n">${p}</span>`;
        holder.append(div); io.observe(div);
      }
    } catch (e) { body.innerHTML = `<div class="wv-empty">Could not open the PDF: ${esc(e.message)}</div>`; }
  }

  async function docxView(tab, body) {
    body.innerHTML = '<div class="wv-empty muted">Converting document…</div>';
    try {
      await W.loadScript("/static/vendor/mammoth.browser.min.js");
      const res = await fetch(rawUrl(tab.path), { credentials: "same-origin" });
      if (!res.ok) throw new Error(res.statusText);
      const out = await window.mammoth.convertToHtml({ arrayBuffer: await res.arrayBuffer() });
      body.innerHTML = `<div class="docx-desk"><article class="docx-page">${out.value || '<p class="muted">(empty document)</p>'}</article></div>`;
      body.querySelectorAll("a[href]").forEach((a) => {
        if (!/^(https?:|mailto:|#)/i.test(a.getAttribute("href"))) a.removeAttribute("href");
        else if (!a.getAttribute("href").startsWith("#")) { a.target = "_blank"; a.rel = "noopener noreferrer"; }
      });
      body.querySelectorAll("img").forEach((img) => { if (!/^data:image\//.test(img.getAttribute("src") || "")) img.remove(); });
    } catch (e) { body.innerHTML = infoCard(tab, `Could not convert this document (${e.message}). Only .docx is supported.`); }
  }
})();
