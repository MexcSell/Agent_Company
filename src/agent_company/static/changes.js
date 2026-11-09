// Change History (AICO UI-HIST-01, administrators): one timeline of the company's changes — agent configuration,
// playbooks, definition rewrites, organisation versions, governance settings, model routes, merges, self-evolution —
// filtered by kind, agent, project and period, each linked to where its diff and restore action live (changes.py).
let chFilter = { kind: "", agent: "", project: "", days: "30" }, chOffset = 0;
const CH_TONE = { agent_config: "info", playbook: "info", definition: "info", organization: "warn", governance: "warn", route: "neutral", merge: "ok", self_evolution: "bad" };

async function viewChanges(quiet) {
  const qs = new URLSearchParams({ days: chFilter.days, offset: chOffset, limit: 50, ...(chFilter.kind ? { kind: chFilter.kind } : {}), ...(chFilter.agent ? { agent_id: chFilter.agent } : {}), ...(chFilter.project ? { project_id: chFilter.project } : {}) });
  const [r, agents, projs] = await Promise.all([api(`/api/changes?${qs}`), api("/api/agents").catch(() => []), api("/api/projects").catch(() => [])]);
  if (!quiet || !$("#ch")) {
    const sel = (k, label, opts) => `<select data-ch="${k}" aria-label="${label}"><option value="">${label}: all</option>${opts.map(([v, l]) => `<option value="${esc(v)}"${String(chFilter[k]) === String(v) ? " selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
    $("#view").innerHTML = `<div id="ch"><div class="page-head"><div class="page-head-text"><h2 class="page-title">Change History</h2><p class="muted page-sub">Every change to the company in one timeline, each with the page that shows its diff and restores it.</p></div></div>
      <div class="row pf-tools">${sel("kind", "Kind", r.kinds.map((k) => [k.kind, k.label]))}${sel("agent", "Agent", agents.filter((a) => a.lifecycle !== "retired").map((a) => [a.id, a.name]))}${sel("project", "Project", projs.map((p) => [p.id, p.name]))}
        <select data-ch="days" aria-label="Period">${[["1", "Last 24 hours"], ["7", "Last 7 days"], ["30", "Last 30 days"], ["365", "Last year"]].map(([v, l]) => `<option value="${v}"${chFilter.days === v ? " selected" : ""}>${l}</option>`).join("")}</select></div><div id="ch-body"></div></div>`;
    $("#view").querySelectorAll("[data-ch]").forEach((el) => (el.onchange = () => { chFilter[el.dataset.ch] = el.value; chOffset = 0; viewChanges(true); }));
  }
  const html = r.items.length ? `<section class="card"><ol class="timeline ch-list">${r.items.map((x) => `<li class="${CH_TONE[x.kind] || "info"}"><div class="row nowrap"><span class="badge ${CH_TONE[x.kind] || "info"}">${esc(x.label)}</span><span class="grow ellipsis">${x.link ? `<a href="${esc(x.link)}">${esc(x.title)}</a>` : esc(x.title)}</span></div>
      <time>${esc(x.actor || "system")} · ${ago(x.at)}${x.restore ? ` · restore: ${esc(x.restore)}` : ""}</time></li>`).join("")}</ol>
      <div class="row" style="margin-top:12px"><span class="muted grow">${r.offset + 1}–${r.offset + r.items.length} of ${r.total}</span>${r.offset ? '<button class="btn sm" id="ch-prev">Newer</button>' : ""}${r.offset + r.items.length < r.total ? '<button class="btn sm" id="ch-next">Older</button>' : ""}</div></section>`
    : `<div class="card empty"><p class="empty-title">No change in this period</p><p class="muted">Widen the period or clear a filter.</p></div>`;
  const body = $("#ch-body");
  if (body.dataset.html === html) return;
  body.innerHTML = html; body.dataset.html = html;
  $("#ch-prev") && ($("#ch-prev").onclick = () => { chOffset = Math.max(0, chOffset - 50); viewChanges(true); });
  $("#ch-next") && ($("#ch-next").onclick = () => { chOffset += 50; viewChanges(true); });
}
registerView({ id: "changes", title: "Change History", icon: "audit", admin: true }, viewChanges);
