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
  const MODE_LABEL = { code: "Code", preview: "Preview", source: "Source", table: "Table", pretty: "Formatted", image: "Image", edit: "Edit" };
  // The toolbar's icons (24px stroke paths); each button keeps its name as tooltip and accessible label.
  const ICON = {
    code: "M8 6l-6 6 6 6M16 6l6 6-6 6", source: "M8 6l-6 6 6 6M16 6l6 6-6 6", edit: "M4 20h4L19 9l-4-4L4 16zM14 6l4 4",
    preview: "M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zM12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z",
    table: "M3 5h18v14H3zM3 10h18M3 15h18M9 5v14M15 5v14", image: "M3 5h18v14H3zM3 16l5-5 4 4 3-3 6 6M15 9h.01",
    pretty: "M8 4H7a2 2 0 0 0-2 2v4l-2 2 2 2v4a2 2 0 0 0 2 2h1M16 4h1a2 2 0 0 1 2 2v4l2 2-2 2v4a2 2 0 0 1-2 2h-1",
    find: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM21 21l-5-5", wrap: "M3 6h18M3 12h15a3 3 0 0 1 0 6h-4M16 16l-2 2 2 2M3 18h7",
    "zoom-out": "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM21 21l-5-5M8 11h6", "zoom-in": "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM21 21l-5-5M8 11h6M11 8v6",
    "zoom-fit": "M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5", "copy-path": "M8 8h12v12H8zM4 16V4h12", reload: "M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7",
    download: "M12 13v8M8 17l4 4 4-4M4.4 15.3A7 7 0 1 1 15.7 8h1.8a4.5 4.5 0 0 1 2.4 8.3",
    up: "M6 15l6-6 6 6", down: "M6 9l6 6 6-6", close: "M6 6l12 12M18 6L6 18",
  };
  const ico = (k) => W.svg(ICON[k]);
  const ibtn = (act, label, extra = "") => `<button type="button" class="icon-btn sm" data-act="${act}" title="${label}" aria-label="${label.replace(/ \(.*\)$/, "")}"${extra}>${ico(act)}</button>`;
  const BINARY_KINDS = new Set(["image", "pdf", "docx", "audio", "video", "archive", "sheet", "slides"]);

  W.defaultMode = (kind) => (TEXT_MODES[kind] ? TEXT_MODES[kind][0] : "code");

  // tab: {path, name, kind, mode, file, text, dirty, zoom}; host: element to fill.
  W.renderView = async function renderView(tab, host, actions) {
    const dir = tab.path.slice(0, tab.path.lastIndexOf("/")) || "/";
    const modes = TEXT_MODES[tab.kind] || (BINARY_KINDS.has(tab.kind) ? [] : ["code"]);
    const textual = !BINARY_KINDS.has(tab.kind);
    if (textual && !tab.file) {
      // While the server cannot be reached, a file shown before stays as it was (its last copy) and a new one keeps
      // "Loading…" and tries again; only a real answer (gone, forbidden, too large) shows an error.
      if (!tab.last) host.innerHTML = '<div class="wv-empty muted">Loading…</div>';
      try { tab.file = await api(`/api/files/read?path=${encodeURIComponent(tab.path)}`); tab.last = null; }
      catch (e) {
        if (e.status && e.status < 502) { tab.last = null; host.innerHTML = `<div class="wv-empty">${esc(e.message)}</div>`; return; }
        // parentNode, not isConnected: a tab of a Workspace that is not on screen keeps trying; a closed one stops.
        if (!tab.last) { setTimeout(() => { if (host.parentNode && !tab.file) renderView(tab, host, actions); }, 3000); return; }
        tab.file = tab.last; tab.last = null;
        if (host.querySelector(".wv-body")) { if (!tab.file.binary && tab.text == null) tab.text = tab.file.text; return; }  // shown as it was
      }
      if (!tab.file.binary && tab.text == null) tab.text = tab.file.text;
    }
    const binaryText = textual && tab.file?.binary;
    const editable = textual && !binaryText;
    const crumbs = (W.rel ? W.rel(tab.path) : tab.path).split("/").filter(Boolean);
    host.innerHTML = `<div class="wv-bar"><div class="wv-crumbs mono" title="${esc(tab.path)}">${crumbs.map((c, k) => `<span${k === crumbs.length - 1 ? ' class="last"' : ""}>${esc(c)}</span>`).join('<i>›</i>')}</div>
      <div class="wv-actions">${editable ? [...modes, "edit"].map((m) => `<button type="button" class="seg${tab.mode === m ? " on" : ""}" data-mode="${m}" title="${MODE_LABEL[m]}" aria-label="${MODE_LABEL[m]}" aria-pressed="${tab.mode === m}">${ico(m)}</button>`).join("") : ""}
      ${tab.mode === "edit" ? `<button class="btn sm primary" data-act="save" ${tab.dirty ? "" : "disabled"}>Save</button>` : ""}
      ${searchable(tab) ? ibtn("find", "Find in this file (Ctrl+F)", ` aria-pressed="${!!tab.find?.open}"`) : ""}
      ${["code", "source"].includes(tab.mode) ? ibtn("wrap", "Word wrap") : ""}
      ${["image", "pdf"].includes(tab.kind) || (tab.kind === "svg" && tab.mode === "image") ? `${ibtn("zoom-out", "Zoom out")}<span class="muted zoom">${tab.zoom ? `${Math.round(tab.zoom * 100)}%` : "Fit"}</span>${ibtn("zoom-in", "Zoom in")}${ibtn("zoom-fit", "Fit")}` : ""}
      ${ibtn("copy-path", "Copy path")}${ibtn("reload", "Reload")}<a class="icon-btn sm" href="${rawUrl(tab.path, true)}" title="Download" aria-label="Download" download>${ico("download")}</a></div></div>
      ${searchable(tab) && tab.find?.open ? `<div class="wv-find" role="search"><input type="search" class="wv-find-q" placeholder="Find in file" aria-label="Find in file" spellcheck="false">
        <span class="muted wv-find-n" aria-live="polite"></span>${ibtn("up", "Previous match (Shift+Enter)")}${ibtn("down", "Next match (Enter)")}${ibtn("close", "Close (Esc)")}</div>` : ""}
      <div class="wv-body" tabindex="-1"></div>`;
    const body = host.querySelector(".wv-body");
    host.querySelectorAll("[data-mode]").forEach((b) => (b.onclick = () => { tab.mode = b.dataset.mode; renderView(tab, host, actions); }));
    host.querySelectorAll("[data-act]").forEach((b) => (b.onclick = async () => {
      const a = b.dataset.act;
      if (a === "save") return actions.save(tab);
      if (a === "find") return openFind(tab, host, actions, !tab.find?.open);
      if (a === "up" || a === "down") return stepFind(tab, host, a === "up" ? -1 : 1);
      if (a === "close") return openFind(tab, host, actions, false);
      if (a === "wrap") { tab.wrap = !tab.wrap; body.querySelector(".codev")?.classList.toggle("wrap", tab.wrap); return; }
      if (a === "copy-path") return W.copyText(tab.path, "Path copied");
      if (a === "reload") { if (tab.dirty && !confirm("Discard unsaved changes?")) return; tab.last = tab.dirty ? null : tab.file; tab.file = null; tab.text = null; tab.dirty = false; tab.pdf = null; actions.changed(tab); return renderView(tab, host, actions); }
      if (a.startsWith("zoom")) { tab.zoom = a === "zoom-fit" ? null : Math.min(8, Math.max(0.1, (tab.zoom || 1) * (a === "zoom-in" ? 1.25 : 0.8))); return renderView(tab, host, actions); }
    }));

    wireFind(tab, host, actions);
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

  // --- find in the file: every match highlighted (CSS Custom Highlight API), Enter steps through them ---------------
  const searchable = (tab) => !BINARY_KINDS.has(tab.kind) && !tab.file?.binary || tab.kind === "docx";
  function openFind(tab, host, actions, open) {
    tab.find = { q: "", i: 0, ...tab.find, open };
    if (!open) clearFind();
    W.renderView(tab, host, actions).then(() => { if (open) host.querySelector(".wv-find-q")?.select(); else host.querySelector(".wv-body")?.focus?.(); });
  }
  function clearFind() { if (window.CSS?.highlights) { CSS.highlights.delete("wv-find"); CSS.highlights.delete("wv-find-cur"); } }
  function wireFind(tab, host, actions) {
    if (!host.dataset.findKeys) {  // Ctrl+F inside the viewer finds in the file (the explorer's Ctrl+F filters its tree)
      host.dataset.findKeys = "1";
      host.addEventListener("keydown", (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "f" && searchable(host._tab)) { e.preventDefault(); if (host._tab.find?.open) host.querySelector(".wv-find-q")?.select(); else openFind(host._tab, host, host._actions, true); }
        else if (e.key === "F3" && host._tab.find?.q) { e.preventDefault(); stepFind(host._tab, host, e.shiftKey ? -1 : 1); }
      });
    }
    host._tab = tab; host._actions = actions;
    clearFind();
    const q = host.querySelector(".wv-find-q");
    if (!q) return;
    q.value = tab.find.q;
    q.oninput = () => { tab.find.q = q.value; tab.find.i = 0; runFind(tab, host, true); };
    q.onkeydown = (e) => {
      if (e.key === "Enter") { e.preventDefault(); stepFind(tab, host, e.shiftKey ? -1 : 1); }
      else if (e.key === "Escape") { e.preventDefault(); openFind(tab, host, actions, false); }
    };
    requestAnimationFrame(() => runFind(tab, host, false));  // once the file's view is in place
  }
  // Ranges of every match of the query in the rendered view (text split over highlighted tokens is joined first).
  function matchRanges(root, q) {
    const nodes = [], ranges = [];
    let text = "";
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let n; (n = walker.nextNode());) { if (n.parentElement.closest(".ln, .wv-status")) continue; nodes.push([n, text.length]); text += n.data; }
    const hay = text.toLowerCase(), needle = q.toLowerCase();
    const at = (pos) => { let lo = 0, hi = nodes.length - 1; while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (nodes[mid][1] <= pos) lo = mid; else hi = mid - 1; } return nodes[lo]; };
    for (let i = hay.indexOf(needle); i >= 0 && ranges.length < 10000; i = hay.indexOf(needle, i + needle.length)) {
      const [sn, so] = at(i), [en, eo] = at(i + needle.length - 1), r = document.createRange();
      r.setStart(sn, i - so); r.setEnd(en, i + needle.length - eo);
      ranges.push(r);
    }
    return ranges;
  }
  function runFind(tab, host, scroll) {
    const f = tab.find, body = host.querySelector(".wv-body"), n = host.querySelector(".wv-find-n");
    if (!f?.open || !body || !n) return;
    clearFind();
    const ta = body.querySelector("textarea");
    if (!f.q) { n.textContent = ""; f.count = 0; return; }
    if (ta) {  // the editor: positions in its text; the current match is selected when stepped to
      const hay = ta.value.toLowerCase(), needle = f.q.toLowerCase();
      f.pos = [];
      for (let i = hay.indexOf(needle); i >= 0 && f.pos.length < 10000; i = hay.indexOf(needle, i + needle.length)) f.pos.push(i);
      f.count = f.pos.length;
    } else {
      f.ranges = matchRanges(body, f.q); f.count = f.ranges.length;
    }
    if (f.i >= f.count) f.i = 0;
    n.textContent = f.count ? `${f.i + 1} of ${f.count}${f.count >= 10000 ? "+" : ""}` : "No results";
    n.classList.toggle("bad", !f.count);
    if (!f.count) return;
    if (ta) { if (scroll) showInEditor(ta, f, false); return; }
    if (window.CSS?.highlights && window.Highlight) {
      CSS.highlights.set("wv-find", new Highlight(...f.ranges));
      CSS.highlights.set("wv-find-cur", new Highlight(f.ranges[f.i]));
    }
    if (scroll) scrollToRange(body, f.ranges[f.i]);
  }
  function stepFind(tab, host, dir) {
    const f = tab.find;
    if (!f?.count) return runFind(tab, host, true);
    f.i = (f.i + dir + f.count) % f.count;
    const ta = host.querySelector(".wv-body textarea");
    if (ta) { host.querySelector(".wv-find-n").textContent = `${f.i + 1} of ${f.count}`; return showInEditor(ta, f, true); }
    runFind(tab, host, true);
  }
  function showInEditor(ta, f, select) {
    const pos = f.pos[f.i], line = ta.value.slice(0, pos).split("\n").length - 1;
    ta.scrollTop = Math.max(0, line * parseFloat(getComputedStyle(ta).lineHeight || "19.5") - ta.clientHeight / 2);
    if (select) { ta.focus({ preventScroll: true }); ta.setSelectionRange(pos, pos + f.q.length); }
  }
  function scrollToRange(body, r) {
    const rect = r.getBoundingClientRect(), box = body.getBoundingClientRect();
    if (!rect.width && !rect.height) { r.startContainer.parentElement?.scrollIntoView({ block: "center" }); return; }
    if (rect.top < box.top + 30 || rect.bottom > box.bottom - 10) body.scrollTop += rect.top - box.top - box.height / 2;
    if (rect.left < box.left + 60 || rect.right > box.right - 20) body.scrollLeft += rect.left - box.left - box.width / 2;
  }

  function infoCard(tab, msg) {
    return `<div class="wv-info"><div class="wv-info-icon">${W.fileIcon ? W.fileIcon(tab.name, false, 40) : ""}</div><strong>${esc(tab.name)}</strong>
      <span class="muted">${esc(msg)}${tab.file?.size != null ? ` · ${W.size(tab.file.size)}` : ""}</span><a class="btn keep-text" href="${rawUrl(tab.path, true)}" download>${ico("download")} Download</a></div>`;
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
