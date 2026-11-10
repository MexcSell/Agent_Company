"""Discord as an external knowledge source (G5 Control Hub UI-G5-KN-02; link 6af21f34 "Add Discord Channel").

An administrator adds a bot token (stored encrypted, only a hint leaves the server), the connector discovers the
servers and channels the bot can see, the administrator selects channels, and syncs import their messages — forum
posts as threads, active and archived — as research documents: one per message, with server, channel, thread,
message and author ids, timestamp, attachments, link and parent, marked as unverified community discussion. Syncs are
incremental (a cursor per channel or thread), bounded per run, and respect Discord's rate limits.

The state is what Discord answered, never assumed: `invalid_token`, `no_access` (the bot is not in the server or may
not read the channel), `missing_intent` (the Message Content intent is off, so text arrives empty), `rate_limited`,
`error`; `connected` only after Discord confirmed it. A failed sync is an Event (`source_sync`, ok False) the Command
Center lists. Read-only: the bot never posts, and personal accounts (self-bots) are not supported.

Every request goes to discord.com through `netpin.pinned_client` (public addresses only, no proxy, no redirects).
Which agents may read the imported messages is the connector's `agent_access` (none|research|all), applied in
`research.search` (`hidden_urls`).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from agent_company.db import DiscordConnector, Document, log_event, now, session

API = "https://discord.com/api/v10"
PURPOSE = b"discord-bot-token"
STATES = ("unconfigured", "connected", "invalid_token", "no_access", "missing_intent", "rate_limited", "error")
ACCESS = ("none", "research", "all")
TEXT_TYPES = {0: "text", 5: "announcement", 15: "forum", 10: "thread", 11: "thread", 12: "thread"}
FORUM = 15
MAX_PER_CHANNEL = 500  # messages one sync imports from one channel or thread; the cursor continues next time
MAX_THREADS = 50
HISTORY = 20
INTENT_FLAGS = (1 << 18) | (1 << 19)  # GATEWAY_MESSAGE_CONTENT and its limited form (application flags)
_SNOWFLAKE = re.compile(r"^\d{5,25}$")
TOPICS = [  # link 6af21f34 §4: classification of imported discussions (a keyword rule, marked as such)
    ("competition announcement", re.compile(r"\b(competition|announce|launch|deadline|prize|leaderboard)\b", re.I)),
    ("errors and troubleshooting", re.compile(r"\b(error|exception|traceback|fail(ed|s|ing)?|crash|bug|broken)\b", re.I)),
    ("infrastructure", re.compile(r"\b(gpu|server|docker|vps|kubernetes|deploy|node|cuda|ram|disk)\b", re.I)),
    ("miner strategy", re.compile(r"\b(miner|mining|validator|incentive|emission|stake|subnet)\b", re.I)),
    ("question", re.compile(r"\?\s*$|^(how|why|what|where|when|can|does|is|anyone)\b", re.I | re.M)),
    ("technical solution", re.compile(r"\b(fix(ed)?|solution|solved|workaround|use|try|config)\b", re.I)),
    ("update", re.compile(r"\b(update|release|version|changelog|v\d+\.\d+)\b", re.I)),
]


class DiscordError(Exception):
    def __init__(self, state: str, detail: str, retry_after: float | None = None) -> None:
        super().__init__(detail)
        self.state, self.detail, self.retry_after = state, detail, retry_after


def _request(token: str, path: str, params: dict[str, Any] | None = None) -> tuple[int, Any, dict[str, str]]:
    """GET one Discord API path with the bot token: (status, JSON or None, headers). Tests replace this."""
    from agent_company import netpin

    with netpin.pinned_client(timeout=20) as c:
        r = c.get(API + path, params=params or {}, headers={
            "Authorization": f"Bot {token}", "User-Agent": "DiscordBot (agent-company, 1.0)"})
    try:
        body = r.json()
    except ValueError:
        body = None
    return r.status_code, body, dict(r.headers)


def _get(token: str, path: str, params: dict[str, Any] | None = None) -> Any:
    status, body, headers = _request(token, path, params)
    if status == 200:
        return body
    msg = (body or {}).get("message", "") if isinstance(body, dict) else ""
    if status == 401:
        raise DiscordError("invalid_token", "Discord refused the bot token (401). Create a new token in the "
                           "Developer Portal and enter it again.")
    if status in (403, 404):
        raise DiscordError("no_access", f"Discord answered {status} for {path.split('?')[0]}: {msg or 'no access'}. "
                           "The bot is not in this server or may not view this channel and read its history; ask the "
                           "server's administrators to invite it with View Channels and Read Message History.")
    if status == 429:
        retry = float((body or {}).get("retry_after") or headers.get("retry-after") or 60)
        raise DiscordError("rate_limited", f"Discord rate limit: retry after {retry:.0f} s.", retry)
    raise DiscordError("error", f"Discord answered {status}: {msg or 'unexpected response'}")


def _token(c: DiscordConnector) -> str:
    from agent_company import transport

    token = transport.decrypt_text(c.token_enc, PURPOSE)
    if not token:
        raise DiscordError("unconfigured", "No bot token is stored for this source.")
    return token


def topic(text: str) -> str:
    return next((name for name, rx in TOPICS if rx.search(text or "")), "discussion")


def public(c: DiscordConnector) -> dict[str, Any]:
    """What a browser sees of a connector: never the token."""
    return {"id": c.id, "name": c.name, "token_hint": c.token_hint, "has_token": bool(c.token_enc),
            "guild_id": c.guild_id, "guild_name": c.guild_name, "channels": c.channels or [],
            "discovered": c.discovered or {}, "enabled": c.enabled, "interval_minutes": c.interval_minutes,
            "agent_access": c.agent_access, "state": c.state, "state_detail": c.state_detail,
            "imported": c.imported, "history": c.history or [],
            "last_sync_at": c.last_sync_at.isoformat() if c.last_sync_at else None,
            "not_before": c.not_before.isoformat() if c.not_before else None,
            "created_by": c.created_by, "created_at": c.created_at.isoformat() if c.created_at else None}


def list_connectors() -> list[dict[str, Any]]:
    with session() as s:
        return [public(c) for c in s.scalars(select(DiscordConnector).order_by(DiscordConnector.id))]


def get(cid: int) -> dict[str, Any]:
    with session() as s:
        c = s.get(DiscordConnector, cid)
        if c is None:
            raise KeyError(cid)
        return public(c)


def _check(body: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "name" in body:
        name = " ".join(str(body["name"] or "").split())[:128]
        if not name:
            raise ValueError("a name is required")
        out["name"] = name
    if "guild_id" in body:
        gid = str(body["guild_id"] or "").strip()
        if gid and not _SNOWFLAKE.match(gid):
            raise ValueError("a server id is a Discord snowflake (digits)")
        out["guild_id"] = gid
    if "interval_minutes" in body:
        n = int(body["interval_minutes"] or 0)
        if n and not 5 <= n <= 7 * 24 * 60:
            raise ValueError("interval_minutes is 0 (by hand) or 5..10080")
        out["interval_minutes"] = n
    if "agent_access" in body:
        if body["agent_access"] not in ACCESS:
            raise ValueError(f"agent_access is one of {', '.join(ACCESS)}")
        out["agent_access"] = body["agent_access"]
    if "enabled" in body:
        out["enabled"] = bool(body["enabled"])
    if "channels" in body:
        ids = [str(x) for x in body["channels"] or []]
        if any(not _SNOWFLAKE.match(x) for x in ids) or len(ids) > 50:
            raise ValueError("channels is a list of at most 50 channel ids")
        out["channels"] = ids
    return out


def create(body: dict[str, Any], actor: str) -> dict[str, Any]:
    from agent_company import transport

    fields = _check({"name": body.get("name") or "Discord", **{k: v for k, v in body.items() if k != "channels"}})
    token = str(body.get("token") or "").strip()
    with session() as s:
        c = DiscordConnector(created_by=actor, **fields)
        if token:
            c.token_enc, c.token_hint = transport.encrypt_text(token, PURPOSE), "…" + token[-4:]
        s.add(c)
        s.flush()
        return public(c)


def update(cid: int, body: dict[str, Any]) -> dict[str, Any]:
    from agent_company import transport

    fields = _check(body)
    with session() as s:
        c = s.get(DiscordConnector, cid)
        if c is None:
            raise KeyError(cid)
        if "channels" in fields:  # only channels the last test discovered in the selected server
            known = {str(x["id"]): x for x in (c.discovered or {}).get("channels") or []}
            unknown = [x for x in fields["channels"] if x not in known]
            if unknown:
                raise ValueError(f"unknown channel(s) {', '.join(unknown[:5])}: test the connection to discover them")
            fields["channels"] = [{"id": x, "name": known[x]["name"], "type": known[x]["type"]}
                                  for x in fields["channels"]]
        if fields.get("guild_id", c.guild_id) != c.guild_id:  # another server: its channels must be discovered again
            c.channels, c.cursors, c.guild_name, c.state = [], {}, "", "unconfigured"
            c.discovered = {"guilds": (c.discovered or {}).get("guilds", [])}
        for k, v in fields.items():
            setattr(c, k, v)
        token = str(body.get("token") or "").strip()
        if token:
            c.token_enc, c.token_hint, c.state = transport.encrypt_text(token, PURPOSE), "…" + token[-4:], "unconfigured"
            c.state_detail = "A new token was entered; test the connection."
        return public(c)


def delete(cid: int, purge: bool = False) -> dict[str, Any]:
    """Disconnect: the connector and its token go; its imported messages stay unless `purge`."""
    with session() as s:
        c = s.get(DiscordConnector, cid)
        if c is None:
            raise KeyError(cid)
        removed = 0
        if purge:  # document_versions go with their document (ON DELETE CASCADE)
            for d in s.scalars(select(Document).where(Document.source_url.like(f"discord://{cid}/%"))):
                s.delete(d)
                removed += 1
        s.delete(c)
    return {"deleted": cid, "documents_removed": removed}


def _fail(cid: int, e: DiscordError) -> None:
    with session() as s:
        c = s.get(DiscordConnector, cid)
        if c is not None:
            c.state, c.state_detail = e.state, e.detail[:1000]
            if e.retry_after:
                c.not_before = now() + timedelta(seconds=e.retry_after)


def test(cid: int) -> dict[str, Any]:
    """Ask Discord who the token is, whether the Message Content intent is on, which servers the bot is in and, for
    the chosen server, which text, announcement and forum channels it can see."""
    with session() as s:
        c = s.get(DiscordConnector, cid)
        if c is None:
            raise KeyError(cid)
        try:
            token = _token(c)
        except DiscordError as e:
            c.state, c.state_detail = e.state, e.detail
            return public(c)
        guild_id = c.guild_id
    try:
        me = _get(token, "/users/@me")
        app = _get(token, "/oauth2/applications/@me")
        guilds = [{"id": str(g["id"]), "name": g.get("name", "")} for g in _get(token, "/users/@me/guilds") or []]
        channels: list[dict[str, Any]] = []
        guild_name = ""
        if guild_id:
            if guild_id not in {g["id"] for g in guilds}:
                raise DiscordError("no_access", f"The bot ({me.get('username', '?')}) is not a member of server "
                                   f"{guild_id}. Only the server's administrators can invite it; until then nothing "
                                   "is imported from it.")
            guild_name = next(g["name"] for g in guilds if g["id"] == guild_id)
            for ch in _get(token, f"/guilds/{guild_id}/channels") or []:
                if ch.get("type") in (0, 5, FORUM):
                    channels.append({"id": str(ch["id"]), "name": ch.get("name", ""), "type": TEXT_TYPES[ch["type"]]})
    except DiscordError as e:
        _fail(cid, e)
        return get(cid)
    intent = bool(int((app or {}).get("flags") or 0) & INTENT_FLAGS)
    with session() as s:
        c = s.get(DiscordConnector, cid)
        c.discovered = {"guilds": guilds, "channels": channels, "bot": me.get("username", ""), "intent": intent,
                        "at": now().isoformat()}
        c.guild_name = guild_name or c.guild_name
        if not intent:
            c.state, c.state_detail = "missing_intent", ("The bot's Message Content intent is off: messages would "
                                                         "arrive without their text. Turn it on in the Developer "
                                                         "Portal (Bot → Privileged Gateway Intents).")
        elif not guild_id:
            c.state, c.state_detail = "unconfigured", f"Token valid ({me.get('username', '?')}); choose a server."
        else:
            c.state, c.state_detail = "connected", (f"{me.get('username', '?')} can see {len(channels)} channel(s) in "
                                                    f"{guild_name}.")
        return public(c)


def _page(c: dict[str, Any], channel: dict[str, Any], m: dict[str, Any], thread: dict[str, Any] | None) -> dict:
    where = thread["id"] if thread else channel["id"]
    author = m.get("author") or {}
    content = (m.get("content") or "").strip()
    files = [f"{a.get('filename', 'file')} <{a.get('url', '')}>" for a in m.get("attachments") or []]
    ref = (m.get("message_reference") or {}).get("message_id")
    when = m.get("timestamp") or ""
    head = ["Unverified community discussion from Discord — not an authoritative source.",
            f"Server: {c['guild_name']} ({c['guild_id']}) · Channel: #{channel['name']} ({channel['id']})"
            + (f" · Thread: {thread.get('name', '')} ({thread['id']})" if thread else ""),
            f"Message: {m['id']} · Author: {author.get('username', '?')} ({author.get('id', '?')}) · Posted: {when}",
            f"Topic (keyword rule): {topic(content)}"]
    if ref:
        head.append(f"Reply to: https://discord.com/channels/{c['guild_id']}/{where}/{ref}")
    body = "\n".join(head) + "\n\n" + (content or "(no text)") + ("\n\nAttachments:\n" + "\n".join(files) if files else "")
    try:
        published = datetime.fromisoformat(when.replace("Z", "+00:00")) if when else None
    except ValueError:
        published = None
    title = f"#{channel['name']}" + (f" / {thread.get('name', '')}" if thread else "") + f" · {author.get('username', '?')}"
    return {"url": f"https://discord.com/channels/{c['guild_id']}/{where}/{m['id']}", "title": title[:300],
            "text": body, "published_at": published}


def _messages(token: str, where: str, after: str | None) -> list[dict[str, Any]]:
    """Messages after the cursor, oldest first, at most MAX_PER_CHANNEL."""
    out: list[dict[str, Any]] = []
    cursor = after or "0"
    while len(out) < MAX_PER_CHANNEL:
        batch = _get(token, f"/channels/{where}/messages", {"limit": 100, "after": cursor}) or []
        if not batch:
            break
        batch.sort(key=lambda m: int(m["id"]))
        out += batch
        cursor = batch[-1]["id"]
        if len(batch) < 100:
            break
    return out[:MAX_PER_CHANNEL]


def _threads(token: str, guild_id: str, forum_id: str) -> list[dict[str, Any]]:
    active = (_get(token, f"/guilds/{guild_id}/threads/active") or {}).get("threads") or []
    archived = (_get(token, f"/channels/{forum_id}/threads/archived/public", {"limit": MAX_THREADS}) or {}).get(
        "threads") or []
    seen, out = set(), []
    for t in [*active, *archived]:
        if str(t.get("parent_id")) == forum_id and t["id"] not in seen:
            seen.add(t["id"])
            out.append({"id": str(t["id"]), "name": t.get("name", "")})
    return out[:MAX_THREADS]


def sync(cid: int, actor: str = "system") -> dict[str, Any]:
    """Import the selected channels' new messages. Never reports success Discord did not give."""
    from agent_company import research

    with session() as s:
        c = s.get(DiscordConnector, cid)
        if c is None:
            raise KeyError(cid)
        snap = {"guild_id": c.guild_id, "guild_name": c.guild_name or c.guild_id, "channels": list(c.channels or []),
                "cursors": dict(c.cursors or {})}
        token = c.token_enc
    imported, detail, ok, error = 0, "", True, None
    try:
        if not token:
            raise DiscordError("unconfigured", "No bot token is stored for this source.")
        if not snap["guild_id"] or not snap["channels"]:
            raise DiscordError("unconfigured", "Choose a server and at least one channel first.")
        with session() as s:
            token = _token(s.get(DiscordConnector, cid))
        cursors = snap["cursors"]
        empty = total = 0
        for ch in snap["channels"]:
            places = [(None, ch["id"])] if ch["type"] != "forum" else [
                (t, t["id"]) for t in _threads(token, snap["guild_id"], ch["id"])]
            for thread, where in places:
                msgs = _messages(token, where, cursors.get(where))
                if not msgs:
                    continue
                total += len(msgs)
                empty += sum(1 for m in msgs if not (m.get("content") or "").strip() and not m.get("attachments"))
                pages = [_page(snap, ch, m, thread) for m in msgs]
                imported += research.store_pages(pages, f"discord://{cid}/{ch['id']}")["new"]
                cursors[where] = msgs[-1]["id"]
                with session() as s:  # the cursor moves only after its messages are stored
                    c = s.get(DiscordConnector, cid)
                    c.cursors = dict(cursors)
        if total and empty == total:
            raise DiscordError("missing_intent", f"{total} message(s) arrived without text: the Message Content "
                               "intent is off. Turn it on in the Developer Portal; their ids were recorded.")
        detail = f"{imported} new message(s) from {len(snap['channels'])} channel(s)"
    except DiscordError as e:
        ok, error, detail = False, e, e.detail
    with session() as s:
        c = s.get(DiscordConnector, cid)
        c.last_sync_at = now()
        c.imported = (c.imported or 0) + imported
        c.history = ([{"at": now().isoformat(), "ok": ok, "imported": imported, "detail": detail[:300],
                       "by": actor}] + list(c.history or []))[:HISTORY]
        if error is not None:
            c.state, c.state_detail = error.state, error.detail[:1000]
            if error.retry_after:
                c.not_before = now() + timedelta(seconds=error.retry_after)
        else:
            c.state, c.state_detail, c.not_before = "connected", detail, None
        name = c.name
        out = public(c)
    log_event("source_sync", f"Discord {name}: {detail}"[:500], ok=ok, connector=cid, imported=imported)
    return out | {"ok": ok, "new": imported}


def tick() -> list[int]:
    """Sync every enabled connector whose interval is due (ScheduleThread, every minute)."""
    due = []
    t = now()
    with session() as s:
        for c in s.scalars(select(DiscordConnector).where(DiscordConnector.enabled)):
            if not c.interval_minutes or (c.not_before and _aware(c.not_before) > t):
                continue
            if c.last_sync_at is None or _aware(c.last_sync_at) + timedelta(minutes=c.interval_minutes) <= t:
                due.append(c.id)
    for cid in due:
        sync(cid, "schedule")
    return due


def _aware(t: datetime) -> datetime:
    return t if t.tzinfo else t.replace(tzinfo=now().tzinfo)


def messages(cid: int, limit: int = 50, q: str = "") -> list[dict[str, Any]]:
    with session() as s:
        if s.get(DiscordConnector, cid) is None:
            raise KeyError(cid)
        query = select(Document).where(Document.source_url.like(f"discord://{cid}/%"))
        if q.strip():
            query = query.where(Document.text.ilike(f"%{q.strip()[:100]}%"))
        rows = s.scalars(query.order_by(Document.published_at.desc(), Document.id.desc()).limit(max(1, min(limit, 200))))
        return [{"id": d.id, "url": d.url, "title": d.title, "text": d.text[:1200], "flags": list(d.flags or []),
                 "published_at": d.published_at.isoformat() if d.published_at else None} for d in rows]


def readable(audience: str) -> set[int] | None:
    """The connectors whose documents this audience may read (None = every one): `agents` (task packages,
    planning) read connectors with access `all`; `research` (the research room's tools) also `research`; `people`
    read all. A disconnected connector's kept messages are read by people only."""
    if audience == "people":
        return None
    allowed = {"all"} if audience == "agents" else {"all", "research"}
    with session() as s:
        return {cid for cid, acc in s.execute(select(DiscordConnector.id, DiscordConnector.agent_access))
                if acc in allowed}


def visible(source_url: str, readable_ids: set[int] | None) -> bool:
    if readable_ids is None or not (source_url or "").startswith("discord://"):
        return True
    cid = source_url[len("discord://"):].split("/", 1)[0]
    return cid.isdigit() and int(cid) in readable_ids
