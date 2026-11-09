// Deliverables (AICO UI-DEL-02): per project the release records stored at each merge (deliverables.py) — Ready or
// Superseded — each with its final (completion) report, source commit, QA summary from the gate and reviews, build
// artifacts (SBOM, changed files) and the Markdown download. Nothing here is generated from agent text.
let delProject = "";

function delCommitUrl(d) {
  const m = /github\.com[/:]([^/]+\/[^/.]+)(?:\.git)?$/.exec(d.remote_url || "");
  return m ? `https://github.com/${m[1]}/commit/${d.merge_commit}` : "";
}

async function viewDeliverables(quiet) {
  const [rows, projs] = await Promise.all([api("/api/deliverables"), api("/api/projects").catch(() => [])]);
  const pname = Object.fromEntries(projs.map((p) => [p.id, p.name]));
  if (!quiet || !$("#del")) {
    $("#view").innerHTML = `<div id="del"><div class="page-head"><div class="page-head-text"><h2 class="page-title">Deliverables</h2><p class="muted page-sub">What the company delivered: one release record per merged goal, stored at the merge with its completion report and checksum.</p></div></div>
      <div class="row pf-tools"><select id="del-project" aria-label="Project"><option value="">Project: all</option>${projs.map((p) => `<option value="${p.id}"${String(delProject) === String(p.id) ? " selected" : ""}>${esc(p.name)}</option>`).join("")}</select></div><div id="del-body"></div></div>`;
    $("#del-project").onchange = (e) => { delProject = e.target.value; viewDeliverables(true); };
  }
  const shown = rows.filter((d) => !delProject || String(d.project_id) === String(delProject));
  const groups = new Map(); shown.forEach((d) => { const k = d.project_id ?? 0; if (!groups.has(k)) groups.set(k, []); groups.get(k).push(d); });
  const card = (d) => { const a = d.acceptance || {}, url = delCommitUrl(d);
    return `<section class="card del-card${d.status === "superseded" ? " old" : ""}"><div class="row nowrap"><span class="kpi-icon" style="margin:0">${icon("check", 16)}</span><a class="grow ellipsis" href="#/goals/${d.goal_id}"><strong>${esc(d.title)}</strong></a><span class="badge ${d.status === "ready" ? "ok" : "neutral"}">${d.status === "ready" ? "Ready" : "Superseded"}</span></div>
      <div class="gm-kv"><span class="muted">Commit</span><span class="mono">${url ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(d.merge_commit.slice(0, 12))}</a>` : esc(d.merge_commit.slice(0, 12))} · ${esc(d.branch)} → ${esc(d.base_branch)}</span>
        <span class="muted">QA</span><span><span class="badge ok">${a.verified || 0} verified</span> <span class="badge warn">${a.partial || 0} partial</span> <span class="badge bad">${a.failed || 0} failed</span></span>
        <span class="muted">Artifacts</span><span>${(d.changed_files || []).length} changed file(s)${d.sbom_path ? " · SBOM" : ""}</span><span class="muted">Approved</span><span>${esc(d.approved_by)} · ${ago(d.created_at)}</span>
        <span class="muted">Checksum</span><span class="mono ellipsis" title="SHA-256 of the stored report">${esc(d.checksum.slice(0, 16))}…</span></div>
      <div class="row del-actions"><button class="btn sm primary" data-report="${d.id}">Final report</button><a class="btn sm" href="/api/deliverables/${d.id}/download" download>Download .md</a>${d.sbom_path ? `<a class="btn sm" href="/api/deliverables/${d.id}/download?what=sbom" download>SBOM</a>` : ""}${(d.changed_files || []).length ? `<button class="btn sm" data-files="${d.id}">Changed files</button>` : ""}</div></section>`; };
  const html = shown.length ? [...groups.entries()].map(([pid, ds]) => `<h3 class="team-h">${esc(pname[pid] || (pid ? `Project #${pid}` : "Without a project"))} <span class="muted">${ds.length} release(s)</span></h3><div class="grid pf-cards">${ds.map(card).join("")}</div>`).join("")
    : `<div class="card empty"><p class="empty-title">No deliverable yet</p><p class="muted">A release record is stored when a goal is merged.</p></div>`;
  const body = $("#del-body");
  if (body.dataset.html === html) return;
  body.innerHTML = html; body.dataset.html = html; iconize(body);
  body.querySelectorAll("[data-report]").forEach((b) => (b.onclick = async () => {
    const d = await api(`/api/deliverables/${b.dataset.report}`);
    modal(`<h2>${icon("book", 18)} Completion report</h2><p class="muted dash-meta">Stored ${ago(d.created_at)} · SHA-256 <span class="mono">${esc(d.checksum)}</span></p><div class="md del-md">${window.WSP?.md ? WSP.md(d.report_md) : `<pre>${esc(d.report_md)}</pre>`}</div>`);
  }));
  body.querySelectorAll("[data-files]").forEach((b) => (b.onclick = () => { const d = shown.find((x) => x.id === Number(b.dataset.files)); openDrawer("Changed files", `<ul class="gm-acc mono">${d.changed_files.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>`); }));
}
registerView({ id: "deliverables", title: "Deliverables", icon: "check" }, viewDeliverables);
