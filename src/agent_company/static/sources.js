"use strict";
// Knowledge → External sources (G5-CONTROL-HUB.md UI-G5-KN-01/02): the research room's web sources and Discord
// connectors (/api/research/sources, /api/discord). A Discord source shows the state Discord answered — never a
// pretended success — its server and channels, sync switch and interval, who among the agents may read it, the last
// syncs and the imported messages. The bot token goes to the server once and is shown only as a hint.
const DC_STATE = { connected: ["Connected", "ok"], unconfigured: ["Not set up", "neutral"], invalid_token: ["Invalid token", "bad"],
  no_access: ["No access", "bad"], missing_intent: ["Message Content intent off", "warn"], rate_limited: ["Rate limited", "warn"], error: ["Error", "bad"] };
const DC_ACCESS = { none: "No agent (people only)", research: "The research room", all: "Every agent (task packages and planning)" };

async function viewSources() {
  const [web, dcs] = await Promise.all([api("/api/research/sources").catch(() => ({ sources: [] })), api("/api/discord")]);
  $("#view").innerHTML = `<div class="page-head"><div class="page-head-text"><h2 class="page-title">Knowledge &amp; Memory</h2><p class="muted page-sub">External sources feed the knowledge pipeline: collect → screen → classify → index → retrieve. Each source decides which agents may read it.</p></div>
      <div class="row nowrap"><button class="btn primary keep-text" id="dc-new">${icon("plus", 16)} Connect Discord</button></div></div>${kTabs("sources")}
    <section class="card"><div class="card-head"><h3>Discord</h3><span class="muted dash-meta">${dcs.length} source${dcs.length === 1 ? "" : "s"}</span></div>
      ${dcs.length ? `<div class="grid dash-row two-wide" style="margin-top:0">${dcs.map(dcCard).join("")}</div>` : `<div class="empty"><p class="empty-title">No Discord source</p><p class="muted">Connect a bot to read a server's channels and forum posts as knowledge. The bot must be invited by the server's administrators.</p></div>`}</section>
    <section class="card" style="margin-top:var(--space-3)"><div class="card-head"><h3>Websites</h3><span class="muted dash-meta">visited by the research rotation</span></div>
      ${(web.sources || []).length ? `<div class="table-wrap"><table><thead><tr><th>Source</th><th>Interval</th><th>Last visit</th><th>State</th><th></th></tr></thead><tbody>${web.sources.map((x) => `<tr><td class="ellipsis"><a href="${esc(x.url)}" target="_blank" rel="noopener noreferrer">${esc(x.url)}</a>${x.subject ? `<div class="muted dash-meta">watching: ${esc(x.subject)}</div>` : ""}</td><td>${esc(x.interval || "rotation")}</td><td>${x.last_run_at ? ago(x.last_run_at) : "never"}</td><td><span class="badge ${x.enabled ? "ok" : "neutral"}">${x.enabled ? "on" : "off"}</span></td><td><button class="btn sm keep-text" data-wrun="${x.id}">Visit now</button></td></tr>`).join("")}</tbody></table></div>`
        : `<p class="muted dash-meta">No website source. Add one on the Research page.</p>`}</section>`;
  kWireTabs();
  $("#dc-new").onclick = () => dcForm(null);
  $("#view").querySelectorAll("[data-wrun]").forEach((b) => (b.onclick = async () => {
    try { const r = await api(`/api/research/sources/${b.dataset.wrun}/run`, { method: "POST" }); toast(r.ran === false ? r.detail : "Visited"); viewSources(); } catch (e) { toast(e.message); }
  }));
  $("#view").querySelectorAll("[data-dc]").forEach((b) => (b.onclick = () => dcAction(b.dataset.dc, Number(b.dataset.id), dcs.find((x) => x.id === Number(b.dataset.id)))));
}

function dcCard(c) {
  const [label, tone] = DC_STATE[c.state] || [c.state, "neutral"];
  return `<section class="card dc-card"><div class="card-head"><h3 class="ellipsis">${esc(c.name)}</h3><span class="badge ${tone}">${label}</span></div>
    ${c.state_detail ? `<p class="dash-meta ${["bad", "warn"].includes(tone) ? "dc-why" : "muted"}">${esc(c.state_detail)}</p>` : ""}
    <div class="gm-kv"><span class="muted">Server</span><span>${c.guild_id ? `${esc(c.guild_name || "not reached yet")} <span class="mono muted">${esc(c.guild_id)}</span>` : "not chosen"}</span>
      <span class="muted">Channels</span><span>${c.channels.length ? c.channels.map((x) => `${x.type === "forum" ? "forum " : "#"}${esc(x.name)}`).join(" · ") : "none selected"}</span>
      <span class="muted">Sync</span><span>${c.enabled ? `every ${c.interval_minutes} min` : "by hand"}${c.not_before ? ` · paused until ${ago(c.not_before).replace(" ago", "")}` : ""}</span>
      <span class="muted">Agents</span><span>${esc(DC_ACCESS[c.agent_access] || c.agent_access)}</span>
      <span class="muted">Imported</span><span>${fmt.format(c.imported)} message(s)${c.last_sync_at ? ` · last sync ${ago(c.last_sync_at)}` : ""}</span>
      <span class="muted">Token</span><span class="mono">${c.has_token ? esc(c.token_hint) : "none"}</span></div>
    ${c.history.length ? `<ul class="timeline">${c.history.slice(0, 3).map((h) => `<li class="${h.ok ? "ok" : "bad"}">${esc(h.detail)}<time>${ago(h.at)} · ${esc(h.by || "")}</time></li>`).join("")}</ul>` : ""}
    <div class="row" style="margin-top:8px"><button class="btn sm keep-text" data-dc="test" data-id="${c.id}">Test connection</button><button class="btn sm keep-text primary" data-dc="sync" data-id="${c.id}"${c.channels.length ? "" : " disabled"}>Sync now</button><button class="btn sm keep-text" data-dc="edit" data-id="${c.id}">Configure</button><button class="btn sm keep-text" data-dc="messages" data-id="${c.id}">Messages</button><button class="btn sm keep-text danger" data-dc="delete" data-id="${c.id}">Disconnect</button></div></section>`;
}

async function dcAction(what, id, c) {
  try {
    if (what === "test") { const r = await api(`/api/discord/${id}/test`, { method: "POST" }); toast(`${(DC_STATE[r.state] || [r.state])[0]}: ${r.state_detail}`); return viewSources(); }
    if (what === "sync") { toast("Syncing…"); const r = await api(`/api/discord/${id}/sync`, { method: "POST" }); toast(r.ok ? `${r.new} new message(s)` : `Sync failed: ${r.state_detail}`); return viewSources(); }
    if (what === "edit") return dcForm(c);
    if (what === "messages") return dcMessages(c);
    if (what === "delete") {
      if (!confirm(`Disconnect ${c.name}? Its token is deleted and it stops syncing.`)) return;
      const purge = c.imported > 0 && confirm(`Also delete its ${c.imported} imported message(s)?\n\nCancel keeps them, readable by people only.`);
      await api(`/api/discord/${id}?purge=${purge}`, { method: "DELETE" }); toast("Disconnected"); return viewSources();
    }
  } catch (e) { toast(e.message); }
}

function dcForm(c) {
  const d = c?.discovered || {}, guilds = d.guilds || [], chans = d.channels || [], picked = new Set((c?.channels || []).map((x) => x.id));
  openDrawer(c ? `Configure ${c.name}` : "Connect Discord", `<form id="dc-form">
    ${c ? "" : `<ol class="dash-meta dc-steps"><li>Create an application and a bot in the Discord Developer Portal.</li><li>Turn on the <strong>Message Content</strong> intent (Bot → Privileged Gateway Intents).</li><li>Ask the server's administrators to invite it with <strong>View Channels</strong> and <strong>Read Message History</strong> only — never Administrator.</li><li>Enter the token here, then Test connection and choose the channels.</li></ol><p class="muted dash-meta">Personal accounts (self-bots) are not supported. The bot only reads.</p>`}
    <label for="dc-name">Name</label><input id="dc-name" maxlength="128" required value="${esc(c?.name || "")}" placeholder="Ridges community">
    <label for="dc-token">Bot token${c?.has_token ? ` <span class="muted">(stored ${esc(c.token_hint)}; leave empty to keep it)</span>` : ""}</label><input id="dc-token" type="password" autocomplete="off" ${c ? "" : "required"} placeholder="${c?.has_token ? "unchanged" : "the bot's token"}">
    <label for="dc-guild">Server</label>${guilds.length ? `<select id="dc-guild"><option value="">choose…</option>${guilds.map((g) => `<option value="${esc(g.id)}"${g.id === c?.guild_id ? " selected" : ""}>${esc(g.name)} (${esc(g.id)})</option>`).join("")}</select>` : `<input id="dc-guild" inputmode="numeric" value="${esc(c?.guild_id || "")}" placeholder="server id (Developer Mode → Copy Server ID)">`}
    ${c && chans.length && c.guild_id ? `<label>Channels <span class="muted">(text, announcement and forum channels the bot can see)</span></label><div class="dc-chans">${chans.map((x) => `<label class="row nowrap"><input type="checkbox" data-chan="${esc(x.id)}" style="width:auto"${picked.has(x.id) ? " checked" : ""}> ${x.type === "forum" ? "" : "#"}${esc(x.name)} <span class="muted dash-meta">${esc(x.type)}</span></label>`).join("")}</div>` : c ? `<p class="muted dash-meta">Test the connection to discover the server's channels.</p>` : ""}
    <label for="dc-access">Agents that may read it</label><select id="dc-access">${Object.entries(DC_ACCESS).map(([k, l]) => `<option value="${k}"${k === (c?.agent_access || "none") ? " selected" : ""}>${esc(l)}</option>`).join("")}</select>
    <p class="muted dash-meta">Imported messages are unverified community discussion: they are marked so wherever an agent sees them.</p>
    <div class="mi-pair"><div><label for="dc-every">Sync every (minutes, 0 = by hand)</label><input id="dc-every" type="number" min="0" max="10080" value="${c ? c.interval_minutes : 60}"></div>
      <div><label class="row nowrap" style="margin-top:28px"><input type="checkbox" id="dc-on" style="width:auto"${c?.enabled ? " checked" : ""}> Sync on schedule</label></div></div>
    <div class="row" style="margin-top:12px"><button class="btn primary keep-text">${c ? "Save" : "Connect"}</button></div></form>`);
  $("#dc-form").onsubmit = async (e) => {
    e.preventDefault();
    const body = { name: $("#dc-name").value.trim(), guild_id: $("#dc-guild").value.trim(), agent_access: $("#dc-access").value,
      interval_minutes: Number($("#dc-every").value) || 0, enabled: $("#dc-on").checked };
    const token = $("#dc-token").value.trim();
    if (token) body.token = token;
    if (c && document.querySelector("[data-chan]") && body.guild_id === c.guild_id) body.channels = [...document.querySelectorAll("[data-chan]:checked")].map((x) => x.dataset.chan);
    try {
      const r = c ? await api(`/api/discord/${c.id}`, { method: "PATCH", body }) : await api("/api/discord", { method: "POST", body });
      $("#dc-token").value = "";
      const t = await api(`/api/discord/${r.id}/test`, { method: "POST" });
      toast(`${(DC_STATE[t.state] || [t.state])[0]}: ${t.state_detail}`);
      closeDrawer(); viewSources();
      if (t.discovered?.channels?.length && !t.channels.length) dcForm(t);  // choose the channels next
    } catch (err) { toast(err.message); }
  };
}

async function dcMessages(c, q = "") {
  const rows = await api(`/api/discord/${c.id}/messages?limit=100&q=${encodeURIComponent(q)}`);
  openDrawer(`${c.name}: imported messages`, `<form id="dc-q" class="row"><input id="dc-qt" class="grow" value="${esc(q)}" placeholder="Search the imported messages"><button class="btn keep-text">Search</button></form>
    ${rows.length ? `<ul class="timeline">${rows.map((m) => `<li class="${m.flags.length ? "warn" : "info"}"><a href="${esc(m.url)}" target="_blank" rel="noopener noreferrer">${esc(m.title)}</a><div class="dash-meta dc-msg">${esc(m.text.split("\n\n").slice(1).join("\n\n").slice(0, 500))}</div><time>${m.published_at ? ago(m.published_at) : ""}${m.flags.length ? ` · flagged: ${esc(m.flags.join(", "))}` : ""}</time></li>`).join("")}</ul>` : `<p class="muted">No message${q ? " matches" : " imported yet"}.</p>`}`);
  $("#dc-q").onsubmit = (e) => { e.preventDefault(); dcMessages(c, $("#dc-qt").value.trim()); };
}
