"use strict";
// Models: the catalog (left) and which role uses which model (right); each list scrolls by itself.
// Add model opens a dialog: a model by hand (API model, Claude Code CLI, possibly at another address or
// pinned to a session whose conversation every run forks, Codex)
// or one picked from a provider's catalog.

let providersList = [];
const CLEARANCES = ["public", "internal", "confidential", "restricted", "secret"];
async function viewModels(quiet) {
  if (quiet && $("#mdl-page")) return;
  const [m, provs] = await Promise.all([api("/api/models"), api("/api/providers")]);
  providersList = provs;
  const admin = me.is_admin || me.permissions?.manage_models;
  $("#view").classList.add("fit");
  $("#view").innerHTML = `<div class="mdl-page" id="mdl-page"><section class="card ag-scroll"><div class="row nowrap"><h2 class="grow" style="margin:0">Model catalog</h2>${admin ? ib("plus", "Add a model", 'id="m-add"', "primary") : ""}</div>
      <p class="muted">Executor mode: <code>${esc(m.executor)}</code>${m.default_model ? ` · MODEL: <code>${esc(m.default_model)}</code>` : ""}. Unavailable models fall back to MODEL, then to the offline simulator.</p>
      <div class="mdl-list">${m.models.map((x) => `<div class="mdl-item ${x.available ? "" : "off"}"><div class="row nowrap"><span class="mdl-kind k-${esc(x.kind)}">${esc(x.kind === "claude_code" ? "Claude Code" : x.kind === "codex" ? "Codex" : x.kind === "offline" ? "offline" : "API")}</span><strong class="grow">${esc(x.name)}</strong>
          ${x.available ? pill("ok") : ""}${admin && x.available ? ib("bolt", "Test this model", `data-test="${x.id}"`, "sm") : ""}${admin && x.kind !== "offline" ? ib("trash", "Delete", `data-del="${x.id}"`, "sm danger") : ""}</div>
        <div class="muted mono ellipsis">${esc(x.model_id || "default model")}${x.base_url ? ` · ${esc(x.base_url)}` : ""}${x.session_id ? ` · session ${esc(x.session_id.slice(0, 8))}` : ""}</div>
        <div class="muted">${x.input_cost || x.output_cost ? `$${x.input_cost} / $${x.output_cost} per 1M tokens` : "no per-token price"}${x.available ? "" : ` · <span class="mdl-why">${esc(x.unavailable_reason)}</span>`}</div>
        <div class="row nowrap muted" title="The most classified content this model may receive (SPEC-14): hosted models default to internal, local ones to restricted; secret only when set here">Receives up to ${admin && x.kind !== "offline" ? `<select data-clear="${x.id}" style="width:auto">${["", ...CLEARANCES].map((c) => `<option value="${c}" ${(x.clearance_set ? x.clearance : "") === c ? "selected" : ""}>${c || `default (${x.clearance})`}</option>`).join("")}</select>` : `<b>${esc(x.clearance)}</b>${x.kind === "offline" ? " (sends nothing anywhere)" : ""}`}</div></div>`).join("")}</div></section>
    <section class="card ag-scroll"><h2>Routes: which role uses which model</h2><p class="muted">Retry model: used after a failed quality gate (escalation). An agent's own model (its card) comes first.</p>
      <table class="mdl-routes"><tr><th>Role</th><th>Model</th><th>Retry model</th></tr>${Object.entries(m.routes).map(([role, name]) => `<tr><td>${esc(role.replace(/_/g, " "))}</td><td>${admin ? `<select data-role="${esc(role)}">${m.models.map((x) => `<option ${x.name === name ? "selected" : ""}>${esc(x.name)}</option>`).join("")}</select>` : esc(name)}</td><td>${admin ? `<select data-esc="${esc(role)}"><option value="">same model</option>${m.models.map((x) => `<option ${x.name === m.escalations[role] ? "selected" : ""}>${esc(x.name)}</option>`).join("")}</select>` : esc(m.escalations[role] || "same model")}</td></tr>`).join("")}</table></section></div>`;
  document.querySelectorAll("[data-del]").forEach((b) => (b.onclick = async () => { if (!confirm("Delete this model? Routes using it move to another model.")) return; try { await api(`/api/models/${b.dataset.del}`, { method: "DELETE" }); viewModels(); } catch (e) { toast(e.message); } }));
  const saveRoute = async (role) => { try { await api(`/api/routes/${role}`, { method: "PUT", body: { model_name: document.querySelector(`[data-role="${role}"]`).value, escalation: document.querySelector(`[data-esc="${role}"]`).value } }); toast("Route saved"); } catch (e) { toast(e.message); } };
  document.querySelectorAll("[data-role]").forEach((s) => (s.onchange = () => saveRoute(s.dataset.role)));
  document.querySelectorAll("[data-esc]").forEach((s) => (s.onchange = () => saveRoute(s.dataset.esc)));
  document.querySelectorAll("[data-test]").forEach((b) => (b.onclick = async () => { b.disabled = true; b.classList.add("busy"); try { const r = await api(`/api/models/${b.dataset.test}/test`, { method: "POST" }); toast(r.ok ? `OK in ${r.seconds}s: ${r.reply.slice(0, 60)} (${r.input_tokens}/${r.output_tokens} tokens)` : `Failed: ${r.error}`); } catch (e) { toast(e.message); } b.disabled = false; b.classList.remove("busy"); }));
  $("#m-add") && ($("#m-add").onclick = addModelDialog);
  document.querySelectorAll("[data-clear]").forEach((s) => (s.onchange = async () => {
    if (s.value === "secret" && !confirm("Clear this model for secret content? Secret orders and projects will then be sent to it.")) { viewModels(); return; }
    try { await api(`/api/models/${s.dataset.clear}`, { method: "PATCH", body: { clearance: s.value } }); toast("Saved"); viewModels(); } catch (e) { toast(e.message); }
  }));
}

function addModelDialog() {
  modal(`<h2>${icon("models", 18)} Add model</h2><div class="ag-tabs small" role="tablist"><button role="tab" data-amt="manual" aria-selected="true">By hand</button><button role="tab" data-amt="catalog" aria-selected="false">From a provider's catalog</button></div>
    <form id="m-form"><label>Name</label><input id="m-name" required><label>Kind</label><select id="m-kind"><option value="llm">API model (CrewAI / LiteLLM id)</option><option value="claude_code">Claude Code CLI (here or at another address)</option><option value="codex">Codex CLI</option></select>
      <label>Model id (e.g. openai/gpt-5, ollama/qwen3-coder, openrouter/deepseek/deepseek-chat; Claude Code: claude-opus-5-5)</label><input id="m-id">
      <label id="m-url-l">Base URL (an OpenAI-compatible server, or for Claude Code another Anthropic-compatible address: passed as ANTHROPIC_BASE_URL)</label><input id="m-url" placeholder="http://127.0.0.1:8080/v1">
      <label>Environment variable with this model's key (optional)</label><input id="m-env" placeholder="LOCAL_LLM_KEY">
      <div id="m-sid-box" hidden><label>Claude Code session id (optional: every run starts from a copy of this session's conversation; the session itself is not changed)</label><input id="m-sid" class="mono" placeholder="3067d536-a81c-492b-9302-1b000247bdea"></div>
      <div class="row"><div class="grow"><label>$/1M input</label><input id="m-in" type="number" step="0.01" value="0"></div><div class="grow"><label>$/1M output</label><input id="m-out" type="number" step="0.01" value="0"></div></div>
      <div class="row"><div class="grow"><label>Daily token limit (0 = none)</label><input id="m-dl" type="number" value="0"></div><div class="grow"><label>Weekly token limit</label><input id="m-wl" type="number" value="0"></div></div>
      <div class="row" style="margin-top:10px"><button class="btn icon primary" title="Add" aria-label="Add">${icon("check", 16)}</button></div></form>
    <div id="m-cat" hidden><div class="row"><select id="or-prov" style="width:auto"><option value="openrouter">OpenRouter</option>${providersList.map((p) => `<option value="${p.id}">${esc(p.label)}${p.key_set ? "" : " (no key)"}</option>`).join("")}</select><input id="or-q" class="grow" placeholder="Search (e.g. qwen, deepseek, llama)"><label class="row" style="margin:0"><input type="checkbox" id="or-free" style="width:auto"> free only</label>${ib("search", "Search", 'id="or-go"', "primary")}</div><div id="or-res" class="table-wrap" style="margin-top:8px;max-height:55vh;overflow:auto"></div></div>`);
  $("#m-kind").onchange = () => { $("#m-sid-box").hidden = $("#m-kind").value !== "claude_code"; };
  document.querySelectorAll("[data-amt]").forEach((b) => (b.onclick = () => { const cat = b.dataset.amt === "catalog"; $("#m-form").hidden = cat; $("#m-cat").hidden = !cat; document.querySelectorAll("[data-amt]").forEach((x) => x.setAttribute("aria-selected", String(x === b))); }));
  $("#m-form").onsubmit = async (e) => { e.preventDefault(); try { await api("/api/models", { method: "POST", body: { name: $("#m-name").value, kind: $("#m-kind").value, model_id: $("#m-id").value, input_cost: +$("#m-in").value, output_cost: +$("#m-out").value, daily_token_limit: +$("#m-dl").value, weekly_token_limit: +$("#m-wl").value, base_url: $("#m-url").value.trim(), api_key_env: $("#m-env").value.trim(), session_id: $("#m-kind").value === "claude_code" ? $("#m-sid").value.trim() : "" } }); toast("Model added"); $("#modal").hidden = true; viewModels(); } catch (err) { toast(err.message); } };
  const search = async () => {
    $("#or-res").innerHTML = '<div class="cc-working"><span class="spin"></span> Loading…</div>';
    try {
      const prov = $("#or-prov").value;
      const rows = prov === "openrouter" ? await api(`/api/openrouter/models?q=${encodeURIComponent($("#or-q").value)}&free=${$("#or-free").checked}`) : await api(`/api/providers/${prov}/models?q=${encodeURIComponent($("#or-q").value)}`);
      $("#or-res").innerHTML = `<table><tr><th>Model</th><th class="num">$/1M in</th><th class="num">$/1M out</th><th class="num">Context</th><th></th></tr>${rows.slice(0, 60).map((r) => `<tr><td>${esc(r.name)}<div class="muted mono">${esc(r.id)}</div></td><td class="num">${r.input_cost}</td><td class="num">${r.output_cost}</td><td class="num">${r.context_length ? fmt.format(r.context_length) : "-"}</td><td>${ib("plus", "Add", `data-or="${esc(r.id)}"`, "sm primary")}</td></tr>`).join("")}</table>`;
      document.querySelectorAll("[data-or]").forEach((b) => (b.onclick = async () => { try { await api(prov === "openrouter" ? "/api/openrouter/models" : `/api/providers/${prov}/models`, { method: "POST", body: { id: b.dataset.or } }); toast("Added"); b.disabled = true; viewModels(); } catch (e) { toast(e.message); } }));
    } catch (e) { $("#or-res").innerHTML = `<span class="muted">${esc(e.message)}</span>`; }
  };
  $("#or-go").onclick = search; $("#or-q").onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); search(); } };
}

registerView({ id: "models", title: "Models", icon: "models" }, viewModels);
