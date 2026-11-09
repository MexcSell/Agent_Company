// Notifications (AICO UI-NOTE-01): All / Unread / Action needed, a category filter (approval, blocker, failure,
// completion, quality, evolution, security, notice; governance.category), repeated notifications grouped "×n", and a
// right pane with the selected item, its link and actions. "Action needed" starts with the approvals queue (UI-APPR-01).
let noteTab = "all", noteCat = "", noteSel = null;
const NOTE_CAT = { approval: ["Approval", "warn", "check"], blocker: ["Blocker", "warn", "pause"], failure: ["Failure", "bad", "emergency"],
  completion: ["Completion", "ok", "check"], quality: ["Quality", "info", "performance"], evolution: ["Evolution", "info", "evolution"],
  security: ["Security", "bad", "shield"], notice: ["Notice", "neutral", "bell"] };

async function viewNotifications(quiet) {
  const [n, q] = await Promise.all([api("/api/notifications"), api("/api/approvals").catch(() => ({ items: [] }))]);
  if (!quiet || !$("#nt")) {
    $("#view").innerHTML = `<div id="nt"><div class="page-head"><div class="page-head-text"><h2 class="page-title">Notifications</h2><p class="muted page-sub">What happened and what waits for you. Select one for its details and actions.</p></div>
      <div class="page-actions"><button class="btn" id="n-read">${icon("check", 16)} Mark all read</button></div></div>
      ${me.is_admin ? `<details class="card nt-notice"><summary>Post a notice to every user</summary><form id="n-form" class="row" style="margin-top:8px"><input id="n-title" class="grow" required placeholder="Notice"><select id="n-level" style="width:auto"><option>info</option><option>success</option><option>warning</option><option>error</option></select><button class="btn primary">Post</button></form></details>` : ""}
      <div class="nt"><section class="card nt-list"><div class="row nowrap nt-tools"><div class="ag-tabs small" role="tablist" id="nt-tabs"></div><span class="grow"></span><select id="nt-cat" aria-label="Category"><option value="">All categories</option>${Object.entries(NOTE_CAT).map(([k, [l]]) => `<option value="${k}">${l}</option>`).join("")}</select></div><div id="nt-items"></div></section>
        <aside class="card nt-pane" id="nt-pane"></aside></div></div>`;
    $("#nt-cat").onchange = (e) => { noteCat = e.target.value; viewNotifications(true); };
    $("#n-read").onclick = async () => { await api("/api/notifications/read", { method: "POST" }); viewNotifications(true); updateBadges(); };
    if (me.is_admin) $("#n-form").onsubmit = async (e) => { e.preventDefault(); try { await api("/api/notifications", { method: "POST", body: { title: $("#n-title").value, level: $("#n-level").value } }); $("#n-title").value = ""; viewNotifications(true); updateBadges(); } catch (err) { toast(err.message); } };
  }
  const items = n.items.filter((x) => !noteCat || x.category === noteCat);
  const counts = { all: items.length, unread: items.filter((x) => !x.read).length, action: items.filter((x) => x.action).length + q.items.length };
  const tabs = [["all", "All"], ["unread", "Unread"], ["action", "Action needed"]].map(([k, l]) => `<button role="tab" data-nt="${k}" aria-selected="${noteTab === k}">${l} <span class="badge ${k === "action" && counts.action ? "bad" : "neutral"}">${counts[k]}</span></button>`).join("");
  const shown = items.filter((x) => noteTab === "all" || (noteTab === "unread" ? !x.read : x.action));
  const groups = []; shown.forEach((x) => { const g = groups[groups.length - 1]; if (g && g.x.title === x.title && g.x.category === x.category) { g.n++; g.ids.push(x.id); } else groups.push({ x, n: 1, ids: [x.id] }); });
  const row = ({ x, n: count, ids }) => { const [label, tone, ic] = NOTE_CAT[x.category] || NOTE_CAT.notice;
    return `<button class="nt-item${x.read ? " read" : ""}${noteSel === x.id ? " selected" : ""}" data-nid="${x.id}" data-ids="${ids.join(",")}"><span class="kpi-icon" style="margin:0">${icon(ic, 16)}</span><span class="grow" style="min-width:0;text-align:left"><strong class="ellipsis" style="display:block">${esc(x.title)}</strong><span class="muted dash-meta ellipsis" style="display:block">${esc(label)} · ${esc(x.source)} · ${ago(x.at)}</span></span>${count > 1 ? `<span class="badge neutral" title="the same notification ${count} times">×${count}</span>` : ""}${x.action ? '<span class="badge warn">action</span>' : ""}<span class="badge ${tone}">${esc(x.level)}</span></button>`; };
  const queue = noteTab === "action" && q.items.length ? `<h3 class="team-h" style="margin-top:0">Waiting for you <span class="muted">${q.items.length}</span></h3><div class="dash-list" style="margin-bottom:16px">${q.items.slice(0, 20).map((a) => `<div class="row nowrap nt-appr"><span class="badge warn">${esc((DASH_KIND[a.kind] || [a.kind])[0])}</span><span class="grow ellipsis">${esc(a.title)}${a.project ? ` <span class="muted">· ${esc(a.project)}</span>` : ""}</span><span class="muted dash-meta">${ago(a.at)}</span>${a.link ? `<a class="btn sm" href="${esc(a.link)}">Review</a>` : ""}</div>`).join("")}</div><h3 class="team-h">Notifications</h3>` : "";
  const html = queue + (groups.length ? `<div class="nt-rows">${groups.map(row).join("")}</div>` : `<div class="empty"><p class="empty-title">${noteTab === "unread" ? "All read" : "Nothing here"}</p></div>`);
  if ($("#nt-tabs").dataset.html !== tabs) { $("#nt-tabs").innerHTML = tabs; $("#nt-tabs").dataset.html = tabs; $("#nt-tabs").querySelectorAll("[data-nt]").forEach((b) => (b.onclick = () => { noteTab = b.dataset.nt; viewNotifications(true); })); }
  const box = $("#nt-items");
  if (box.dataset.html !== html) {
    box.innerHTML = html; box.dataset.html = html; iconize(box);
    box.querySelectorAll("[data-nid]").forEach((b) => (b.onclick = async () => {
      noteSel = Number(b.dataset.nid);
      const unread = b.dataset.ids.split(",").map(Number).filter((id) => !n.items.find((x) => x.id === id)?.read);
      if (unread.length) { try { await api("/api/notifications/read", { method: "POST", body: unread }); updateBadges(); } catch { /* shown as unread */ } }
      viewNotifications(true);
    }));
  }
  const sel = n.items.find((x) => x.id === noteSel);
  const [label, tone] = sel ? NOTE_CAT[sel.category] || NOTE_CAT.notice : [];
  const appr = sel?.goal_id ? q.items.filter((a) => a.id === sel.goal_id && ["goal", "plan"].includes(a.kind)) : [];
  const pane = sel ? `<div class="row nowrap"><span class="badge ${tone}">${esc(label)}</span><span class="badge neutral">${esc(sel.level)}</span><span class="grow"></span><span class="muted dash-meta">${new Date(sel.at + (/Z$|[+-]\d\d:\d\d$/.test(sel.at) ? "" : "Z")).toLocaleString()}</span></div>
      <h3 class="nt-title">${esc(sel.title)}</h3>${sel.body ? `<p style="white-space:pre-wrap">${esc(sel.body)}</p>` : ""}
      <div class="gm-kv"><span class="muted">Source</span><span>${esc(sel.source)}</span>${sel.goal_id ? `<span class="muted">Goal</span><span><a href="#/goals/${sel.goal_id}">#${sel.goal_id}</a></span>` : ""}${sel.project_id ? `<span class="muted">Project</span><span><a href="#/projects/${sel.project_id}">#${sel.project_id}</a></span>` : ""}<span class="muted">Action</span><span>${sel.action ? "needed: open it and decide" : "none"}</span></div>
      <div class="row" style="margin-top:12px;gap:8px">${sel.goal_id ? `<a class="btn primary" href="#/goals/${sel.goal_id}">Open goal</a>` : ""}${appr.map((a) => `<a class="btn" href="${esc(a.link)}">${a.kind === "plan" ? "Review the plan" : "Review the merge"}</a>`).join("")}${sel.project_id ? `<a class="btn" href="#/general-manager/${sel.project_id}">Ask the GM</a>` : ""}${me.is_admin ? `<button class="btn danger" id="nt-del">Delete</button>` : ""}</div>`
    : `<div class="empty"><p class="empty-title">Select a notification</p><p class="muted">Its details, the object it is about and what you can do appear here.</p></div>`;
  const p = $("#nt-pane");
  if (p.dataset.html !== pane) {
    p.innerHTML = pane; p.dataset.html = pane;
    $("#nt-del") && ($("#nt-del").onclick = async () => { await api(`/api/notifications/${sel.id}`, { method: "DELETE" }); noteSel = null; viewNotifications(true); updateBadges(); });
  }
}
registerView({ id: "notifications", title: "Notifications", icon: "bell" }, viewNotifications);
