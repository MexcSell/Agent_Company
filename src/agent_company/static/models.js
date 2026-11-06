"use strict";
// Models: the catalog (left) and which role uses which model (right); each list scrolls by itself.
// Each model shows its type (API, CLI, Local), an active switch, Test (a short prompt plus the provider's current
// usage), and its information as the Claude Code panel shows a session's: status, model, CLI, account, usage windows.

const CLEARANCES = ["public", "internal", "confidential", "restricted", "secret"];
// G-R-18: a model's terms as a person verified them; "free" and "subscription" are not the same as API access.
const BILLINGS = { api: "API, per token", subscription: "subscription (plan limits)", free_tier: "free tier (rate-limited)", trial: "trial credits", local: "runs here" };
const yn = (v) => (v === "yes" ? "yes" : v === "no" ? "no" : "unknown");
function termsLine(x) {
  const t = x.terms || {};
  const parts = [t.billing ? BILLINGS[t.billing] : "billing unknown", t.context_window ? `${fmt.format(t.context_window)} tokens context` : "", t.tool_calling ? `tool calling ${yn(t.tool_calling)}` : "", t.trains_on_prompts ? `may train on prompts: ${yn(t.trains_on_prompts)}` : "", t.commercial_use ? `commercial use ${yn(t.commercial_use)}` : ""].filter(Boolean);
  return `<div class="muted" title="Terms a person verified against the provider's current pages (G-R-18)">${esc(parts.join(" · "))}${t.terms_url ? ` · <a href="${esc(t.terms_url)}" target="_blank" rel="noopener noreferrer">terms</a>` : ""}${x.terms_due ? ` · <span class="pill pending" title="${t.verified_at ? "Last verified " + esc(t.verified_at.slice(0, 10)) + ": more than 90 days ago" : "Never verified by a person"}">verify terms</span>` : ` · verified ${esc((t.verified_at || "").slice(0, 10))}`}</div>`;
}
let modelsList = [];
let modelsInfo = {};  // /api/models/info by model id: kept across re-renders, read again after each
const STATUS_BADGE = { available: "idle", unavailable: "warning", inactive: "ended" };
// One model as the Claude Code panel shows a session: status, model, CLI version, account, then its usage windows.
function modelInfoHtml(x, info) {
  const st = info?.status || (!x.enabled ? "inactive" : x.available ? "available" : "unavailable");
  const reason = info ? info.reason : x.available ? "" : x.unavailable_reason;
  const a = info?.account || {}, t = x.status || {};
  const rows = [
    ["Status", `<span class="wc-badge ${STATUS_BADGE[st]}">${esc(st)}</span>${reason ? ` <span class="muted">${esc(reason)}</span>` : ""}`],
    ["Model", `<span class="mono">${esc(x.model_id || "default model")}</span>${x.session_id ? ` <span class="muted">· forks session ${esc(x.session_id.slice(0, 8))}</span>` : ""}`],
    [info?.cli?.label || "", esc(info?.cli?.version || "")],
    ["Server", x.base_url ? `<span class="mono">${esc(x.base_url)}</span>` : ""],
    ["Account", a.method ? `${esc(a.method)}${a.email ? ` · ${esc(a.email)}` : ""}${a.organization || a.plan || a.note ? ` <span class="muted">(${esc(a.organization || a.note || `${a.plan} plan`)})</span>` : ""}${x.account ? ` <span class="muted">· sign-in ${esc(x.account)}</span>` : ""}` : ""],
    ["Last test", t.checked_at ? `${ago(t.checked_at)}: ${t.ok ? "OK" : `<span class="mdl-why">${esc(t.error || "failed")}</span>`}${t.detail ? ` <span class="muted">· ${esc(t.detail)}</span>` : ""}` : ""],
  ];
  const kv = `<dl class="wc-kv">${rows.filter((r) => r[0] && r[1]).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join("")}</dl>`;
  if (x.kind === "offline") return kv;
  if (!info) return kv + '<h4>Usage</h4><p class="muted"><span class="spin"></span> Reading usage…</p>';
  const tk = info.tokens || {};
  return kv + `<h4>Usage${info.scope ? ` <span class="muted">${esc(info.scope)}</span>` : ""}</h4>`
    + (info.limits?.length ? limitCards(info.limits) : "")
    + (info.note ? `<p class="muted wc-usage-note">${esc(info.note)}</p>` : "")
    + `<p class="muted wc-usage-note">This server: ${fmt.format(tk.today || 0)} tokens today · ${fmt.format(tk.week || 0)} this week${x.input_cost || x.output_cost ? ` · $${x.input_cost} / $${x.output_cost} per 1M tokens` : ""}</p>`;
}
async function loadModelsInfo() {
  try { modelsInfo = await api("/api/models/info"); } catch (e) { return; }
  for (const x of modelsList) { const el = document.getElementById(`mi-${x.id}`); if (el) el.innerHTML = modelInfoHtml(x, modelsInfo[x.id]); }
}
const TYPE_LABEL = { claude_code: "Claude Code", codex: "Codex", copilot: "Copilot" };
async function viewModels(quiet) {
  if (quiet && $("#mdl-page")) return;
  const m = await api("/api/models");
  modelsList = m.models;
  const admin = me.is_admin || me.permissions?.manage_models;
  $("#view").classList.add("fit");
  $("#view").innerHTML = `<div class="mdl-page" id="mdl-page"><section class="card ag-scroll"><div class="row nowrap"><h2 class="grow" style="margin:0">Model catalog</h2>${admin ? ib("plus", "Add a model", 'id="m-add"', "primary") : ""}</div>
      <p class="muted">Executor mode: <code>${esc(m.executor)}</code>${m.default_model ? ` · MODEL: <code>${esc(m.default_model)}</code>` : ""}. Unavailable models fall back to MODEL, then to the offline simulator.</p>
      <div class="mdl-list">${m.models.map((x) => { const real = x.kind !== "offline"; return `<div class="mdl-item ${x.available ? "" : "off"}">
        <div class="row nowrap"><span class="mdl-kind k-${esc(x.kind)}" title="Type">${esc(x.type === "CLI" ? "CLI · " + TYPE_LABEL[x.kind] : x.type)}</span><strong class="ellipsis" data-name-of="${x.id}">${esc(x.name)}</strong>${admin && real && !x.builtin ? `<button type="button" class="icon-btn sm" title="Rename" aria-label="Rename" data-rename="${x.id}">${icon("edit", 14)}</button>` : ""}<span class="grow"></span>
          ${admin && real ? `<label class="mdl-switch" title="${x.enabled ? "Active: routes and agents may use it" : "Inactive: never used"}"><input type="checkbox" data-active="${x.id}" ${x.enabled ? "checked" : ""}><span></span></label>` : ""}${admin && real ? ib("bolt", "Test: send a short prompt and get the current usage and status", `data-test="${x.id}"`, "sm") : ""}${admin && real ? ib("settings", "Terms, name and limits", `data-terms="${x.id}"`, "sm") : ""}${admin && real ? ib("trash", "Delete", `data-del="${x.id}"`, "sm danger") : ""}</div>
        <div class="mdl-info" id="mi-${x.id}">${modelInfoHtml(x, modelsInfo[x.id])}</div>
        ${real ? termsLine(x) : ""}
        <div class="row nowrap muted" title="The most classified content this model may receive (SPEC-14): hosted models default to internal, local ones to restricted; secret only when set here">Receives up to ${admin && real ? `<select data-clear="${x.id}" style="width:auto">${["", ...CLEARANCES].map((c) => `<option value="${c}" ${(x.clearance_set ? x.clearance : "") === c ? "selected" : ""}>${c || `default (${x.clearance})`}</option>`).join("")}</select>` : `<b>${esc(x.clearance)}</b>${real ? "" : " (sends nothing anywhere)"}`}</div></div>`; }).join("")}</div></section>
    <section class="card ag-scroll"><h2>Routes: which role uses which model</h2><p class="muted">Retry model: used after a failed quality gate (escalation). Fallbacks: the models, in order, that take the role's work when its model hits a usage limit or fails. An agent's own model (its card) comes first.</p>
      <datalist id="mdl-names">${m.models.filter((x) => x.kind !== "offline").map((x) => `<option value="${esc(x.name)}">`).join("")}</datalist>
      <table class="mdl-routes"><tr><th>Role</th><th>Model</th><th>Retry model</th><th>Fallbacks</th></tr>${Object.entries(m.routes).map(([role, name]) => `<tr><td>${esc(role.replace(/_/g, " "))}</td><td>${admin ? `<select data-role="${esc(role)}">${m.models.map((x) => `<option ${x.name === name ? "selected" : ""}>${esc(x.name)}</option>`).join("")}</select>` : esc(name)}</td><td>${admin ? `<select data-esc="${esc(role)}"><option value="">same model</option>${m.models.map((x) => `<option ${x.name === m.escalations[role] ? "selected" : ""}>${esc(x.name)}</option>`).join("")}</select>` : esc(m.escalations[role] || "same model")}</td><td>${admin ? `<input data-fb="${esc(role)}" list="mdl-names" placeholder="e.g. Codex CLI" value="${esc(((m.fallbacks || {})[role] || []).join(", "))}">` : esc(((m.fallbacks || {})[role] || []).join(", ") || "none")}</td></tr>`).join("")}</table></section></div>`;
  document.querySelectorAll("[data-del]").forEach((b) => (b.onclick = async () => { if (!confirm("Delete this model? Routes using it move to another model.")) return; try { await api(`/api/models/${b.dataset.del}`, { method: "DELETE" }); viewModels(); } catch (e) { toast(e.message); } }));
  const saveRoute = async (role) => { try { await api(`/api/routes/${role}`, { method: "PUT", body: { model_name: document.querySelector(`[data-role="${role}"]`).value, escalation: document.querySelector(`[data-esc="${role}"]`).value, fallback: document.querySelector(`[data-fb="${role}"]`).value.split(",").map((x) => x.trim()).filter(Boolean) } }); toast("Route saved"); } catch (e) { toast(e.message); } };
  document.querySelectorAll("[data-role]").forEach((s) => (s.onchange = () => saveRoute(s.dataset.role)));
  document.querySelectorAll("[data-esc]").forEach((s) => (s.onchange = () => saveRoute(s.dataset.esc)));
  document.querySelectorAll("[data-fb]").forEach((s) => (s.onchange = () => saveRoute(s.dataset.fb)));
  document.querySelectorAll("[data-test]").forEach((b) => (b.onclick = async () => { b.disabled = true; b.classList.add("busy"); try { const r = await api(`/api/models/${b.dataset.test}/test`, { method: "POST" }); toast(r.ok ? `OK in ${r.seconds}s: ${r.reply.slice(0, 60)} (${r.input_tokens}/${r.output_tokens} tokens)` : `Failed: ${r.error}`); viewModels(); } catch (e) { toast(e.message); } b.disabled = false; b.classList.remove("busy"); }));
  document.querySelectorAll("[data-active]").forEach((c) => (c.onchange = async () => { try { await api(`/api/models/${c.dataset.active}`, { method: "PATCH", body: { enabled: c.checked } }); viewModels(); } catch (e) { toast(e.message); c.checked = !c.checked; } }));
  document.querySelectorAll("[data-rename]").forEach((b) => (b.onclick = () => renameInline(b.dataset.rename)));
  $("#m-add") && ($("#m-add").onclick = addModelDialog);
  document.querySelectorAll("[data-terms]").forEach((b) => (b.onclick = () => termsDialog(modelsList.find((x) => String(x.id) === b.dataset.terms))));
  loadModelsInfo();
  document.querySelectorAll("[data-clear]").forEach((s) => (s.onchange = async () => {
    if (s.value === "secret" && !confirm("Clear this model for secret content? Secret orders and projects will then be sent to it.")) { viewModels(); return; }
    try { await api(`/api/models/${s.dataset.clear}`, { method: "PATCH", body: { clearance: s.value } }); toast("Saved"); viewModels(); } catch (e) { toast(e.message); }
  }));
}

// The name becomes a field in place: Enter saves (routes and agents using it follow), Escape keeps the old one.
function renameInline(id) {
  const el = document.querySelector(`[data-name-of="${id}"]`), x = modelsList.find((m) => String(m.id) === id);
  if (!el || !x) return;
  el.outerHTML = `<input class="mdl-rename" data-name-of="${id}" value="${esc(x.name)}" aria-label="Model name">`;
  const inp = document.querySelector(`input[data-name-of="${id}"]`);
  let done = false;
  const finish = async (save) => {
    if (done) return; done = true;
    const name = inp.value.trim();
    if (save && name && name !== x.name) { try { await api(`/api/models/${id}`, { method: "PATCH", body: { name } }); toast("Renamed"); } catch (e) { toast(e.message); } }
    viewModels();
  };
  inp.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); finish(true); } else if (e.key === "Escape") finish(false); };
  inp.onblur = () => finish(true);
  inp.focus(); inp.select();
}

function termsDialog(x) {
  const t = x.terms || {};
  const sel = (id, opts, v) => `<select id="${id}">${opts.map(([k, label]) => `<option value="${k}" ${(v || "") === k ? "selected" : ""}>${esc(label)}</option>`).join("")}</select>`;
  const tri = (id, v) => sel(id, [["", "unknown"], ["yes", "yes"], ["no", "no"]], v);
  modal(`<h2>${icon("models", 18)} Edit ${esc(x.name)}</h2>
    <form id="t-form"><label>Name${x.builtin ? " (built in: keeps its name)" : " (routes and agents using it follow the new name)"}</label><input id="t-name" required value="${esc(x.name)}" ${x.builtin ? "disabled" : ""}>
      <h3 style="margin:14px 0 4px">Terms</h3><p class="muted" style="margin-top:0">Check these on the provider's current pages: free tiers, limits and data terms change. Nothing here is filled in from a model's own claims.</p>
      <label>Billing</label>${sel("t-bill", [["", "unknown"], ...Object.entries(BILLINGS)], t.billing)}
      <label>Context window (tokens, 0 = unknown)</label><input id="t-ctx" type="number" min="0" step="1" value="${Number(t.context_window || 0)}">
      <div class="row"><div class="grow"><label>Tool calling</label>${tri("t-tools", t.tool_calling)}</div><div class="grow"><label>Provider may retain or train on prompts</label>${tri("t-train", t.trains_on_prompts)}</div><div class="grow"><label>Commercial use allowed</label>${tri("t-com", t.commercial_use)}</div></div>
      <label>Terms or pricing page</label><input id="t-url" placeholder="https://…" value="${esc(t.terms_url || "")}">
      <label class="row" style="margin-top:8px"><input type="checkbox" id="t-ver" style="width:auto"> I checked these against the provider's current terms now${t.verified_at ? ` (last: ${esc(t.verified_at.slice(0, 10))})` : ""}</label>
      <div class="row" style="margin-top:10px"><button class="btn icon primary" title="Save" aria-label="Save">${icon("check", 16)}</button></div></form>`);
  $("#t-form").onsubmit = async (e) => { e.preventDefault(); try { await api(`/api/models/${x.id}`, { method: "PATCH", body: { ...(x.builtin ? {} : { name: $("#t-name").value.trim() }), terms: { billing: $("#t-bill").value, context_window: Math.max(0, Math.floor(+$("#t-ctx").value || 0)), tool_calling: $("#t-tools").value, trains_on_prompts: $("#t-train").value, commercial_use: $("#t-com").value, terms_url: $("#t-url").value.trim(), verified: $("#t-ver").checked } } }); toast("Saved"); $("#modal").hidden = true; viewModels(); } catch (err) { toast(err.message); } };
}

// Add model: the type first (API, CLI, Local), then what that type needs. The name follows the chosen model and is
// kept unique ("… (2)") until it is typed by hand.
const CLI_CHOICES = [["codex", "OpenAI Codex"], ["copilot", "GitHub Copilot"], ["claude", "Claude Code"]];
function addModelDialog() {
  let type = "API", picked = null, nameTouched = false, served = [];
  const uniqueName = (base) => { const have = new Set(modelsList.map((m) => m.name)); let n = 1, name = base; while (have.has(name)) name = `${base} (${++n})`; return name; };
  modal(`<h2>${icon("models", 18)} Add model</h2>
    <form id="m-form" autocomplete="off"><label>Type</label><div class="ag-tabs small" role="tablist">${["API", "CLI", "Local"].map((t) => `<button type="button" role="tab" data-type="${t}" aria-selected="${t === type}">${t}</button>`).join("")}</div>
      <label>Name</label><input id="m-name" required placeholder="Chosen from the model; you can change it">
      <div data-for="API"><label>API key</label><input id="m-key" type="password" placeholder="sk-...YSUA" autocomplete="new-password">
        <p class="muted" style="margin:2px 0 0">Stored encrypted with this model and never shown again. Empty: the server's OPENROUTER_API_KEY.</p>
        <label>Model</label><div class="row"><select id="m-prov" style="width:auto"><option value="openrouter">OpenRouter</option><option value="other">Another provider (LiteLLM model id)</option></select></div>
        <div id="m-or"><div class="row" style="margin-top:6px"><input id="or-q" class="grow" placeholder="Search (e.g. qwen, deepseek, llama)"><label class="row" style="margin:0" title="Only models that cost nothing per token (free tiers have daily caps)"><input type="checkbox" id="or-free" style="width:auto" checked> Free</label>
          <select id="or-sort" style="width:auto" title="OpenRouter publishes no benchmark: performance ranks by tool calling, reasoning, context and output length"><option value="performance">Sort by performance</option><option value="price">Sort by price</option></select></div>
          <div id="or-res" class="table-wrap mdl-pick"></div></div>
        <div id="m-other" hidden><input id="m-mid" class="mono" placeholder="openai/gpt-5, groq/llama-3.3-70b-versatile, mistral/mistral-large-latest"></div></div>
      <div data-for="CLI" hidden><label>Model</label><select id="m-cli">${CLI_CHOICES.map(([k, l]) => `<option value="${k}">${esc(l)}</option>`).join("")}</select><div id="cli-st" class="mdl-cli"></div>
        <div id="m-claude" hidden><label>Claude Code account (optional: its own sign-in in <code>data/claude-accounts/&lt;account&gt;/</code>; empty = this host's login)</label><input id="m-acct" class="mono" placeholder="claude-2">
          <label>Claude Code session id (optional: every run starts from a copy of this session)</label><input id="m-sid" class="mono" placeholder="3067d536-a81c-492b-9302-1b000247bdea"></div></div>
      <div data-for="Local" hidden><label>URL</label><div class="row"><input id="m-url" class="grow mono" placeholder="http://127.0.0.1:8080/v1">${ib("search", "Ask the server which models it serves", 'type="button" id="loc-go"')}</div><div id="loc-res" class="muted" style="margin-top:4px">An OpenAI-compatible server: llama-server, LM Studio, vLLM, Ollama (…:11434/v1).</div></div>
      <div class="row" style="margin-top:12px"><button class="btn primary" id="m-addbtn">${icon("check", 16)} Add</button></div></form>`);
  const name = $("#m-name");
  const suggest = (base) => { if (!nameTouched && base) name.value = uniqueName(base); };
  name.oninput = () => { nameTouched = name.value.trim() !== ""; };
  const setType = (t) => {
    type = t;
    document.querySelectorAll("[data-type]").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.type === t)));
    document.querySelectorAll("[data-for]").forEach((d) => (d.hidden = d.dataset.for !== t));
    nameTouched = false; name.value = "";
    if (t === "API") { if (picked) suggest(`OpenRouter · ${picked.name}`); else if (!$("#or-res").innerHTML) search(); }
    if (t === "CLI") cliChanged();
    if (t === "Local") suggest(localBase());
  };
  document.querySelectorAll("[data-type]").forEach((b) => (b.onclick = () => setType(b.dataset.type)));

  // API: OpenRouter's list (free only by default) or a LiteLLM id of another provider
  const search = async () => {
    $("#or-res").innerHTML = '<div class="cc-working"><span class="spin"></span> Loading…</div>';
    try {
      const rows = await api(`/api/openrouter/models?q=${encodeURIComponent($("#or-q").value)}&free=${$("#or-free").checked}&sort=${$("#or-sort").value}`);
      const perf = $("#or-sort").value === "performance";
      $("#or-res").innerHTML = rows.length ? `<table><tr><th>Model</th>${perf ? '<th class="num" title="Capability score from the catalog">Score</th>' : ""}<th class="num">$/1M in</th><th class="num">$/1M out</th><th class="num">Context</th></tr>${rows.slice(0, 100).map((r) => `<tr data-pick="${esc(r.id)}" class="${picked?.id === r.id ? "sel" : ""}"><td>${esc(r.name)}<div class="muted mono">${esc(r.id)}${r.tools ? " · tools" : ""}${r.reasoning ? " · reasoning" : ""}</div></td>${perf ? `<td class="num">${r.score}</td>` : ""}<td class="num">${r.free ? "free" : r.input_cost}</td><td class="num">${r.free ? "free" : r.output_cost}</td><td class="num">${r.context_length ? fmt.format(r.context_length) : "-"}</td></tr>`).join("")}</table>` : '<span class="muted">No model matches.</span>';
      document.querySelectorAll("[data-pick]").forEach((tr) => (tr.onclick = () => {
        picked = rows.find((r) => r.id === tr.dataset.pick);
        document.querySelectorAll("[data-pick]").forEach((x) => x.classList.toggle("sel", x === tr));
        suggest(`OpenRouter · ${picked.name}`);
      }));
    } catch (e) { $("#or-res").innerHTML = `<span class="muted">${esc(e.message)}</span>`; }
  };
  $("#or-q").onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); search(); } };
  $("#or-q").oninput = () => { clearTimeout(search.t); search.t = setTimeout(search, 350); };
  $("#or-free").onchange = search; $("#or-sort").onchange = search;
  $("#m-prov").onchange = () => { const other = $("#m-prov").value === "other"; $("#m-or").hidden = other; $("#m-other").hidden = !other; nameTouched = false; name.value = ""; if (!other && picked) suggest(`OpenRouter · ${picked.name}`); };
  $("#m-mid").oninput = () => suggest($("#m-mid").value.trim());

  // CLI: install and sign-in state of the chosen CLI on the server, and its setup steps
  const cliChanged = async () => {
    const tool = $("#m-cli").value;
    $("#m-claude").hidden = tool !== "claude";
    suggest(CLI_CHOICES.find(([k]) => k === tool)[1]);
    $("#cli-st").innerHTML = '<div class="cc-working"><span class="spin"></span> Checking the server…</div>';
    try {
      const st = await api(`/api/models/cli/${tool}`);
      const mark = (ok, yes, no) => `<span class="${ok ? "ok-t" : "mdl-why"}">${ok ? "✓" : "✗"} ${esc(ok ? yes : no)}</span>`;
      $("#cli-st").innerHTML = `<div class="row" style="gap:12px;flex-wrap:wrap">${mark(st.installed, `installed ${st.version}`, "not installed")}${mark(st.signed_in, "signed in", "not signed in")}${mark(st.enabled, "agents may use it", `off for agents (${st.enable_setting})`)}</div>
        <ol class="mdl-steps">${st.steps.map((x) => { const [cmd, note] = x.split(/\s+#\s+/); return `<li><code>${esc(cmd)}</code><button type="button" class="icon-btn sm" title="Copy" aria-label="Copy" data-copy="${esc(cmd)}">${icon("copy", 13)}</button>${note ? `<div class="muted">${esc(note)}</div>` : ""}</li>`; }).join("")}</ol>
        <p class="muted" style="margin:4px 0">Run these on the server (Workspace → terminal): signing in opens a browser link. The model is added either way and becomes available once the CLI is installed, signed in and on for agents.</p>
        <div class="row">${!st.installed && me.is_admin ? `<button type="button" class="btn" id="cli-inst">${icon("plus", 14)} Install on the server</button>` : ""}${!st.enabled && me.is_admin ? `<button type="button" class="btn" id="cli-en">${icon("check", 14)} Let agents use ${esc(st.label)}</button>` : ""}<button type="button" class="btn" id="cli-re">${icon("refresh", 14)} Check again</button></div>`;
      document.querySelectorAll("[data-copy]").forEach((b) => (b.onclick = () => navigator.clipboard?.writeText(b.dataset.copy).then(() => toast("Copied"))));
      $("#cli-re").onclick = cliChanged;
      $("#cli-en") && ($("#cli-en").onclick = async () => { try { await api(`/api/settings/${st.enable_setting}`, { method: "PUT", body: { value: true } }); cliChanged(); } catch (e) { toast(e.message); } });
      $("#cli-inst") && ($("#cli-inst").onclick = async (e) => {
        const b = e.currentTarget; b.disabled = true; b.classList.add("busy");
        try { const r = await api(`/api/models/cli/${tool}/install`, { method: "POST" }); toast(r.ok ? `Installed ${r.version}` : `npm failed: ${r.output.slice(-200)}`); } catch (err) { toast(err.message); }
        cliChanged();
      });
    } catch (e) { $("#cli-st").innerHTML = `<span class="muted">${esc(e.message)}</span>`; }
  };
  $("#m-cli").onchange = () => { nameTouched = false; cliChanged(); };

  // Local: the address, and which model the server serves
  const localBase = () => { try { const u = new URL($("#m-url").value.trim() || "http://127.0.0.1:8080/v1"); return `Local · ${$("#m-local")?.value || u.host}`; } catch { return "Local"; } };
  const probe = async () => {
    const url = $("#m-url").value.trim() || $("#m-url").placeholder;
    $("#loc-res").innerHTML = '<span class="spin"></span> Asking the server…';
    try {
      served = (await api("/api/models/local_probe", { method: "POST", body: { url } })).models;
      $("#loc-res").innerHTML = served.length ? `<label>Served model</label><select id="m-local">${served.map((x) => `<option>${esc(x)}</option>`).join("")}</select>` : "The server answers but lists no model; it will be asked for its default.";
      $("#m-local") && ($("#m-local").onchange = () => suggest(localBase()));
    } catch (e) { served = []; $("#loc-res").innerHTML = `<span class="mdl-why">${esc(e.message)}</span> The model can still be added; it is tried when used.`; }
    suggest(localBase());
  };
  $("#loc-go").onclick = probe; $("#m-url").onchange = probe;

  $("#m-form").onsubmit = async (e) => {
    e.preventDefault();
    const nm = name.value.trim(), key = $("#m-key").value.trim();
    try {
      if (type === "API" && $("#m-prov").value === "openrouter") {
        if (!picked) { toast("Pick a model from the list"); return; }
        await api("/api/openrouter/models", { method: "POST", body: { id: picked.id, name: nm, api_key: key } });
      } else if (type === "API") {
        if (!$("#m-mid").value.trim()) { toast("Enter the model id"); return; }
        await api("/api/models", { method: "POST", body: { name: nm, kind: "llm", model_id: $("#m-mid").value.trim(), api_key: key } });
      } else if (type === "CLI") {
        const tool = $("#m-cli").value, claude = tool === "claude";
        await api("/api/models", { method: "POST", body: { name: nm, kind: { codex: "codex", copilot: "copilot", claude: "claude_code" }[tool], model_id: "", account: claude ? $("#m-acct").value.trim() : "", session_id: claude ? $("#m-sid").value.trim() : "", terms: { billing: "subscription" } } });
      } else {
        const url = $("#m-url").value.trim() || $("#m-url").placeholder;
        await api("/api/models", { method: "POST", body: { name: nm, kind: "llm", model_id: `openai/${$("#m-local")?.value || "local"}`, base_url: url, terms: { billing: "local" } } });
      }
      toast("Model added"); $("#modal").hidden = true; viewModels();
    } catch (err) { toast(err.message); }
  };
  setType("API");
}

registerView({ id: "models", title: "Models", icon: "models" }, viewModels);
