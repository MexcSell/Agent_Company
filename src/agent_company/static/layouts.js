"use strict";
// Screen formats: each preset reshapes the shell (scale, density, corners, rail position and
// labels, width, floating cards) and the Projects page (panel order, sizes, hidden panels,
// orientation). Chosen per browser; panel widths dragged by hand override the preset's sizes.

const LAYOUT_BASE = { scale: 1, density: "cozy", radius: 8, rail: "left", labels: false, maxWidth: null, floating: false,
  project: { orientation: "horizontal", order: ["list", "chat", "agents"], sizes: { list: 20, chat: 55, agents: 25 }, hide: [] } };
const L = (id, name, hint, over = {}) => ({ ...LAYOUT_BASE, ...over, id, name, hint, project: { ...LAYOUT_BASE.project, ...(over.project || {}) } });
const LAYOUTS = [
  L("standard", "Standard", "Editor style"),
  L("compact", "Compact", "More on screen", { scale: 0.93, density: "compact", radius: 6 }),
  L("comfortable", "Comfortable", "Roomy spacing", { scale: 1.05, density: "comfortable", radius: 12 }),
  L("focus", "Focus", "Command window only", { project: { hide: ["list", "agents"] } }),
  L("zen", "Zen", "Centred, quiet", { maxWidth: 1240, project: { hide: ["list"], sizes: { chat: 70, agents: 30 } } }),
  L("wide-chat", "Wide chat", "Conversation first", { project: { sizes: { list: 16, chat: 66, agents: 18 } } }),
  L("agent-ops", "Agent ops", "Agents panel wide", { project: { sizes: { list: 18, chat: 42, agents: 40 } } }),
  L("mirror", "Mirror", "Everything flipped", { rail: "right", project: { order: ["agents", "chat", "list"] } }),
  L("command-center", "Command centre", "Three equal columns", { project: { sizes: { list: 33, chat: 34, agents: 33 } } }),
  L("stacked", "Stacked", "Panels top to bottom", { project: { orientation: "vertical" } }),
  L("presentation", "Presentation", "Large, for screens", { scale: 1.2, density: "comfortable", radius: 14 }),
  L("large-text", "Large text", "Accessibility", { scale: 1.12, density: "comfortable" }),
  L("dense", "Dense data", "Maximum density", { scale: 0.86, density: "compact", radius: 4 }),
  L("ultrawide", "Ultrawide", "21:9 displays", { labels: true, project: { sizes: { list: 14, chat: 58, agents: 28 } } }),
  L("laptop", "Laptop 13\"", "Small screens", { scale: 0.95, project: { sizes: { list: 22, chat: 54, agents: 24 } } }),
  L("tablet", "Tablet", "Touch friendly", { rail: "bottom", density: "comfortable", scale: 1.04, project: { orientation: "vertical" } }),
  L("mobile", "Mobile", "Single column", { rail: "bottom", density: "comfortable", project: { orientation: "vertical", hide: ["list"] } }),
  L("cinema", "Cinema", "Centred 1440 px", { maxWidth: 1440, radius: 14, floating: true }),
  L("floating", "Floating cards", "Panels as cards", { floating: true, radius: 14 }),
  L("labeled", "Labelled rail", "Rail with names", { labels: true }),
  L("square", "Square", "No rounded corners", { radius: 0, density: "compact" }),
];
const DENSITY = { compact: { pad: "9px", gap: "8px", view: "10px" }, cozy: { pad: "14px", gap: "12px", view: "16px" }, comfortable: { pad: "18px", gap: "16px", view: "22px" } };

function currentLayout() { return LAYOUTS.find((l) => l.id === store.get("ac.layout")) || LAYOUTS[0]; }

function applyLayout() {
  const l = currentLayout(), d = DENSITY[l.density], root = document.documentElement.style;
  root.setProperty("--pad", d.pad); root.setProperty("--gap", d.gap); root.setProperty("--view-pad", d.view);
  root.setProperty("--radius", `${l.radius}px`); root.setProperty("--max-width", l.maxWidth ? `${l.maxWidth}px` : "100%");
  const shell = $("#app");
  shell.dataset.rail = l.rail; shell.dataset.labels = l.labels ? "1" : ""; shell.dataset.floating = l.floating ? "1" : "";
  shell.style.zoom = l.scale === 1 ? "" : String(l.scale);
}

// Projects panel arrangement: the preset's, with widths the user dragged taking precedence.
function projectArrangement() {
  const l = currentLayout().project;
  let sizes = { ...l.sizes };
  try { sizes = { ...sizes, ...JSON.parse(store.get(`ac.sizes.${currentLayout().id}`) || "{}") }; } catch { /* ignore */ }
  const order = l.order.filter((k) => !l.hide.includes(k));
  return { order, sizes, vertical: l.orientation === "vertical" || matchMedia("(max-width: 760px)").matches };
}

function applyProjectLayout() {
  const grid = $(".proj");
  if (!grid) return;
  const arr = projectArrangement(), vertical = arr.vertical, sizes = { files: 22, ...arr.sizes };
  const order = grid.classList.contains("with-files") ? [...arr.order, "files"] : arr.order;
  const panes = { list: $(".proj-side"), chat: $(".proj-main"), agents: $(".proj-rail"), files: $(".proj-files") };
  Object.entries(panes).forEach(([k, el]) => { if (el) el.style.display = order.includes(k) ? "" : "none"; });
  if (panes.files) panes.files.hidden = !order.includes("files");
  grid.querySelectorAll(".gutter").forEach((g) => g.remove());
  grid.classList.toggle("vertical", vertical);
  const tracks = [];
  order.forEach((k, i) => {
    panes[k].style.order = String(i * 2);
    tracks.push(`minmax(0, ${sizes[k] || 30}fr)`);
    if (i < order.length - 1 && !vertical) {
      const g = document.createElement("div");
      g.className = "gutter"; g.style.order = String(i * 2 + 1); g.title = "Drag to resize";
      g.setAttribute("role", "separator"); g.setAttribute("aria-orientation", "vertical");
      g.onpointerdown = (e) => startResize(e, grid, order[i], order[i + 1]);
      grid.append(g); tracks.push("6px");
    }
  });
  grid.style.gridTemplateColumns = vertical ? "1fr" : tracks.join(" ");
}

function startResize(e, grid, left, right) {
  e.preventDefault();
  const sizes = { files: 22, ...projectArrangement().sizes };
  const total = sizes[left] + sizes[right];
  const sel = { list: ".proj-side", chat: ".proj-main", agents: ".proj-rail", files: ".proj-files" };
  const a = grid.querySelector(sel[left]).getBoundingClientRect();
  const b = grid.querySelector(sel[right]).getBoundingClientRect();
  const span = a.width + b.width;
  const move = (ev) => {
    const share = Math.min(0.85, Math.max(0.15, (ev.clientX - a.left) / span));
    const saved = { ...sizes, [left]: Math.round(total * share * 10) / 10, [right]: Math.round(total * (1 - share) * 10) / 10 };
    store.set(`ac.sizes.${currentLayout().id}`, JSON.stringify(saved));
    applyProjectLayout();
  };
  const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
  window.addEventListener("pointermove", move); window.addEventListener("pointerup", up);
}

function layoutPicker() {
  const cur = currentLayout().id;
  modal(`<h2>Screen format</h2><p class="muted">Kept in this browser. Dragged panel widths are remembered per format; <a href="#" id="lay-reset">reset widths</a>.</p>
    <div class="grid layouts">${LAYOUTS.map((l) => `<button class="card lay ${l.id === cur ? "selected" : ""}" data-lay="${l.id}"><strong>${esc(l.name)}</strong><span class="muted">${esc(l.hint)}</span>
      <span class="lay-mini" data-rail="${l.rail}">${l.project.order.filter((k) => !l.project.hide.includes(k)).map((k) => `<i style="flex:${l.project.sizes[k] || 30}"></i>`).join("")}</span></button>`).join("")}</div>`);
  document.querySelectorAll("[data-lay]").forEach((b) => (b.onclick = () => { store.set("ac.layout", b.dataset.lay); applyLayout(); applyProjectLayout(); layoutPicker(); }));
  $("#lay-reset").onclick = (e) => { e.preventDefault(); store.del(`ac.sizes.${currentLayout().id}`); applyProjectLayout(); toast("Widths reset"); };
}
