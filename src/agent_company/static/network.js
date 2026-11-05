// Agent network (SPEC-12 S12-R-07): overview counts, the relationship graph with server-side filters and pages
// (GET /api/network/graph), bounded depth around one agent, zoom and pan. Edges: colour and line style by origin
// (observed solid, inferred dashed, corrected dotted), width by weight, an arrow for directed kinds. The table under
// the graph is the same page as text. Corrections stay on the agent card's Relationships tab. A tab of the Agents page.
const NET = { agent_id: "", depth: 1, room: "", skill: "", kind: "", origin: "", include_expired: false, offset: 0, limit: 100 };
const NET_ORIGINS = ["observed", "inferred", "corrected"];

async function viewNetwork(box) {
  const [o, agents, rooms] = await Promise.all([api("/api/network/overview"), api("/api/agents"), api("/api/rooms")]);
  const k = (label, value, title = "") => `<div class="card kpi" title="${esc(title)}"><div class="label">${label}</div><div class="value">${value}</div></div>`;
  const links = o.links.observed + o.links.inferred + o.links.corrected;
  box.classList.add("ag-scroll");
  box.innerHTML = `<div class="grid kpis">
      ${k("Active agents", `${o.active_agents}<span class="muted" style="font-size:14px"> / ${o.agents}</span>`)}
      ${k("Active links", links, `${o.links.observed} observed · ${o.links.inferred} inferred · ${o.links.corrected} corrected · ${o.expired_links} expired`)}
      ${k("Current teams", o.teams, o.team_sizes.length ? `agents per team: ${o.team_sizes.join(", ")}` : "no goal is being worked on")}
      ${k("Certified capabilities", o.certified_capabilities, "passed final evaluations (SPEC-13)")}
      ${k("Handovers (7 days)", o.handovers_7d, "task and research-shift handovers")}
      ${k("Mentorings", o.mentorings, "active mentoring records")}
      ${k("Agents with no free slot", o.overloaded.length, o.overloaded.map((x) => x.name).join(", "))}</div>
    <div class="card" style="margin-top:12px"><h2>Collaboration graph</h2>
      <p class="muted">Without an agent: every link, strongest first, where either end matches the room and skill. With one: the links within the chosen steps of it, nearest first, where the agent reached matches. Click an agent to centre on it; scroll to zoom, drag to pan.</p>
      <form id="net-f" class="row" style="flex-wrap:wrap">
        <select id="net-a" style="width:auto" title="Centre"><option value="">Every agent</option>${agents.map((a) => `<option value="${a.id}">${esc(a.name)}</option>`).join("")}</select>
        <select id="net-d" style="width:auto" title="Steps from the agent">${[1, 2].map((d) => `<option value="${d}">${d} step${d > 1 ? "s" : ""}</option>`).join("")}</select>
        <select id="net-r" style="width:auto" title="Room"><option value="">Every room</option>${rooms.map((r) => `<option value="${esc(r.key)}">${esc(r.name)}</option>`).join("")}</select>
        <input id="net-s" placeholder="skill or technology" style="max-width:170px">
        <select id="net-k" style="width:auto" title="Relationship kind"><option value="">Every kind</option>${REL_KINDS.map((x) => `<option>${x}</option>`).join("")}</select>
        <select id="net-o" style="width:auto" title="Origin"><option value="">Every origin</option>${NET_ORIGINS.map((x) => `<option>${x}</option>`).join("")}</select>
        <label class="row nowrap" style="margin:0"><input type="checkbox" id="net-x" style="width:auto"> expired</label>
        <button class="btn">Show</button></form>
      <div id="net-out" style="margin-top:10px"></div></div>`;
  const f = { "#net-a": "agent_id", "#net-d": "depth", "#net-r": "room", "#net-s": "skill", "#net-k": "kind", "#net-o": "origin" };
  Object.entries(f).forEach(([sel, key]) => ($(sel).value = NET[key]));
  $("#net-x").checked = NET.include_expired;
  $("#net-f").onsubmit = (e) => {
    e.preventDefault();
    Object.entries(f).forEach(([sel, key]) => (NET[key] = $(sel).value.trim()));
    NET.include_expired = $("#net-x").checked; NET.offset = 0; netLoad();
  };
  netLoad();
}

async function netLoad() {
  const q = Object.fromEntries(Object.entries(NET).filter(([key, v]) => v !== "" && !(key === "include_expired" && !v)));
  let g;
  try { g = await api(`/api/network/graph?${new URLSearchParams(q)}`); } catch (err) { return toast(err.message); }
  const out = $("#net-out");
  if (!out) return;
  const names = Object.fromEntries(g.nodes.map((n) => [n.id, n]));
  const who = (id) => `<a href="#" data-netnode="${id}">${esc(names[id]?.name || `agent ${id}`)}</a>`;
  const ev = (e) => esc(e.evidence.join("\n") || "no evidence recorded");
  out.innerHTML = `<div class="row" style="margin-bottom:6px"><div class="net-legend grow">
      ${NET_ORIGINS.map((x) => `<span><svg width="30" height="8"><line x1="1" y1="4" x2="29" y2="4" class="net-edge ${x}" style="stroke-width:2.5"/></svg>${x}</span>`).join("")}
      <span>→ directed kind (reads: from → to)</span><span>width = weight</span><span>dashed circle = not active</span></div>
      ${g.offset > 0 ? ib("chevron_left", "Previous page", 'id="net-prev"', "sm") : ""}<span class="muted">${g.total ? `${g.offset + 1}–${g.offset + g.edges.length} of ${g.total} link(s)` : "no links"}</span>${g.offset + g.limit < g.total ? ib("chevron_right", "Next page", 'id="net-next"', "sm") : ""}
      ${g.agent_id ? `<button class="btn sm" type="button" id="net-card">Open ${esc(names[g.agent_id]?.name || "")}</button>` : ""}</div>
    ${netSvg(g)}
    <div class="table-wrap" style="margin-top:10px"><table><tr>${g.agent_id ? "<th>Step</th>" : ""}<th>From</th><th>Kind</th><th>To</th><th>Origin</th><th class="num">Weight</th><th>Evidence</th></tr>
      ${g.edges.map((e) => `<tr>${g.agent_id ? `<td class="num">${e.depth}</td>` : ""}<td>${who(e.a_id)}</td><td><code>${esc(e.kind)}</code>${e.status === "expired" ? " " + pill("expired") : ""}</td><td>${who(e.b_id)}</td><td>${esc(e.origin)}</td><td class="num">${e.weight}</td><td class="muted" title="${ev(e)}">${e.evidence.length ? esc(String(e.evidence[e.evidence.length - 1]).slice(0, 60)) + (e.evidence.length > 1 ? ` (+${e.evidence.length - 1})` : "") : "—"}</td></tr>`).join("") || `<tr><td colspan="7" class="muted">No links match.</td></tr>`}</table></div>`;
  out.querySelectorAll("a[data-netnode]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); netCentre(Number(a.dataset.netnode)); }));
  $("#net-prev") && ($("#net-prev").onclick = () => { NET.offset = Math.max(0, NET.offset - NET.limit); netLoad(); });
  $("#net-next") && ($("#net-next").onclick = () => { NET.offset += NET.limit; netLoad(); });
  $("#net-card") && ($("#net-card").onclick = () => agentModal(g.agent_id));
  netPanZoom(out.querySelector("svg.net-svg"));
}

function netCentre(id) {
  NET.agent_id = String(id); NET.offset = 0;
  if ($("#net-a")) $("#net-a").value = NET.agent_id;
  netLoad();
}

// Positions: around the centred agent, one ring per step; without one, a circle grouped by specialty (top skill),
// so specialists of one kind sit together.
function netLayout(g, W, H) {
  const pos = {}, cx = W / 2, cy = H / 2;
  const ring = (list, r, start = -Math.PI / 2) => list.forEach((n, i) => { const t = start + (2 * Math.PI * i) / list.length; pos[n.id] = [cx + r * Math.cos(t), cy + r * Math.sin(t)]; });
  const bySpecialty = (a, b) => (a.top_skill || "~").localeCompare(b.top_skill || "~") || a.room.localeCompare(b.room) || a.id - b.id;
  if (g.agent_id) {
    pos[g.agent_id] = [cx, cy];
    const r1 = g.nodes.filter((n) => n.depth === 1).sort(bySpecialty), r2 = g.nodes.filter((n) => n.depth === 2).sort(bySpecialty);
    ring(r1, r2.length ? 150 : 210); ring(r2, 255, -Math.PI / 2 + Math.PI / Math.max(2, r2.length));
  } else ring([...g.nodes].sort(bySpecialty), Math.min(W, H) / 2 - 50);
  return pos;
}

function netSvg(g) {
  const W = 1000, H = 600, pos = netLayout(g, W, H);
  const marker = (o) => `<marker id="net-arr-${o}" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" style="fill:var(--net-${o})"/></marker>`;
  // several links between one pair bend apart, so each stays visible
  const pairs = {}, nth = new Map();
  g.edges.forEach((e) => { const k = [e.a_id, e.b_id].sort((x, y) => x - y).join("-"); nth.set(e, (pairs[k] = (pairs[k] || 0) + 1) - 1); });
  const edges = g.edges.map((e) => {
    const a = pos[e.a_id], b = pos[e.b_id];
    if (!a || !b) return "";
    const n = pairs[[e.a_id, e.b_id].sort((x, y) => x - y).join("-")], lo = Math.min(e.a_id, e.b_id);
    // the bend is measured on the pair's own direction (low id → high id), so opposite edges do not cancel out
    const [p, q] = e.a_id === lo ? [a, b] : [b, a];
    const dx = q[0] - p[0], dy = q[1] - p[1], len = Math.hypot(dx, dy) || 1, bend = (nth.get(e) - (n - 1) / 2) * 34;
    const mx = (a[0] + b[0]) / 2 - (dy / len) * bend, my = (a[1] + b[1]) / 2 + (dx / len) * bend;
    // stop the curve at the node's rim so an arrow is visible
    const cut = (from, to) => { const ux = to[0] - from[0], uy = to[1] - from[1], l = Math.hypot(ux, uy) || 1; return [from[0] + (ux / l) * 15, from[1] + (uy / l) * 15]; };
    const [x1, y1] = cut(a, [mx, my]), [x2, y2] = cut(b, [mx, my]);
    const d = `M${x1.toFixed(1)} ${y1.toFixed(1)} Q${mx.toFixed(1)} ${my.toFixed(1)} ${x2.toFixed(1)} ${y2.toFixed(1)}`;
    const tip = `${g.nodes.find((x) => x.id === e.a_id)?.name} ${e.kind}${e.directed ? " →" : " ↔"} ${g.nodes.find((x) => x.id === e.b_id)?.name}\n${e.origin} · weight ${e.weight}${e.status === "expired" ? " · expired" : ""}\n${e.evidence.slice(-5).join("\n")}`;
    return `<g class="link"><title>${esc(tip)}</title><path class="net-edge ${e.origin}${e.status === "expired" ? " expired" : ""}" d="${d}" style="stroke-width:${(1 + e.weight * 3).toFixed(2)}"${e.directed ? ` marker-end="url(#net-arr-${e.origin})"` : ""}/><path class="hit" d="${d}"/></g>`;
  }).join("");
  const nodes = g.nodes.map((n) => {
    const [x, y] = pos[n.id] || [W / 2, H / 2];
    const off = !n.enabled || n.lifecycle !== "active";
    return `<g class="node${n.id === g.agent_id ? " root" : ""}${off ? " off" : ""}" data-netnode="${n.id}" transform="translate(${x.toFixed(1)},${y.toFixed(1)})"><title>${esc(`${n.name} · ${n.role} · ${n.room.replace(/_/g, " ")}${n.top_skill ? ` · specialty ${n.top_skill}` : ""}${off ? ` · ${n.enabled ? n.lifecycle : "disabled"}` : ""}`)}</title><circle r="13"/><text y="29">${esc(n.name.length > 22 ? n.name.slice(0, 21) + "…" : n.name)}</text>${n.top_skill ? `<text class="sub" y="42">${esc(n.top_skill)}</text>` : ""}</g>`;
  }).join("");
  return `<svg class="net-svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="Agent relationship graph; the table below lists the same links"><defs>${NET_ORIGINS.map(marker).join("")}</defs><g>${edges}${nodes}</g></svg>`;
}

// Wheel zooms around the pointer, dragging pans; both change the viewBox, so text stays crisp.
function netPanZoom(svg) {
  if (!svg) return;
  let vb = svg.viewBox.baseVal, drag = null, moved = false;
  const pt = (e) => { const r = svg.getBoundingClientRect(); return [vb.x + ((e.clientX - r.left) / r.width) * vb.width, vb.y + ((e.clientY - r.top) / r.height) * vb.height]; };
  svg.addEventListener("wheel", (e) => {
    e.preventDefault();
    const [px, py] = pt(e), f = e.deltaY > 0 ? 1.15 : 1 / 1.15;
    const w = Math.min(4000, Math.max(150, vb.width * f)), h = (w * vb.height) / vb.width;
    vb.x = px - ((px - vb.x) * w) / vb.width; vb.y = py - ((py - vb.y) * h) / vb.height; vb.width = w; vb.height = h;
  }, { passive: false });
  svg.addEventListener("pointerdown", (e) => { drag = [e.clientX, e.clientY, vb.x, vb.y]; moved = false; });
  svg.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const r = svg.getBoundingClientRect(), dx = e.clientX - drag[0], dy = e.clientY - drag[1];
    if (Math.abs(dx) + Math.abs(dy) > 3) { moved = true; svg.classList.add("panning"); }
    vb.x = drag[2] - (dx / r.width) * vb.width; vb.y = drag[3] - (dy / r.height) * vb.height;
  });
  const end = () => { drag = null; svg.classList.remove("panning"); };
  svg.addEventListener("pointerup", end); svg.addEventListener("pointerleave", end);
  svg.querySelectorAll(".node").forEach((n) => n.addEventListener("click", () => { if (!moved) netCentre(Number(n.dataset.netnode)); }));
}

