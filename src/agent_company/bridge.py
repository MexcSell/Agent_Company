"""The bridge MCP server's company side (EL-R-09): per-run tokens and the calls of `POST /api/bridge/call`.

The stdlib shim `mcp_servers/bridge.py` runs inside a CLI agent's run and forwards each tool call with the run's
bearer token. The token names one (goal, task, agent); everything a call reads or writes comes from it, never from the
arguments, and a goal or task argument that differs is refused. Writes go only to that goal's blackboard: a progress
note in the role's section, an insight under `reports`, a review request under `messages`. Never approvals, decisions,
proposals, budgets, permissions, grants or merges. Every call, refused ones included, is an `McpCall`.
"""

from __future__ import annotations

import json
import secrets
import time
from typing import Any

from sqlalchemy import delete, select

from agent_company import blackboard
from agent_company.auth import _hash
from agent_company.config import settings
from agent_company.db import BlackboardEntry, BridgeToken, Goal, McpCall, McpGrant, McpServer, Task, log_event, session

TTL = 3 * 3600  # a fallback only: the run's end revokes the token
CALLS_PER_TOKEN = 100
INSIGHTS_PER_TASK = 5
REVIEWS_PER_TASK = 2
MAX_BODY = 65536
TOOLS = ("get_task", "post_progress", "save_insight", "request_review")
LOOPBACK = {"127.0.0.1", "::1"}
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "0.0.0.0", "::", ""}
_SCOPE = {"goal_id", "task_id"}
_ARGS = {"get_task": set(), "post_progress": {"text", "percent"}, "save_insight": {"title", "text", "evidence"},
         "request_review": {"summary", "files", "reviewer"}}


class Refused(Exception):
    def __init__(self, status: int, error: str, scope: bool = False) -> None:
        super().__init__(error)
        self.status, self.error, self.scope = status, error, scope


def server_row(s: Any) -> McpServer | None:
    """The bundled bridge, known by its command line (a custom server may have taken the slug `bridge`)."""
    for row in s.scalars(select(McpServer).where(McpServer.transport == "stdio").order_by(McpServer.id)):
        if list(row.args or []) == ["bridge.py"]:
            return row
    return None


def api_url() -> str | None:
    """The API on loopback, or None when it is not bound there (then the bridge is not attached)."""
    cfg = settings()
    return f"http://127.0.0.1:{cfg.port}" if (cfg.host or "") in _LOCAL_HOSTS else None


def issue(goal_id: int, task_id: int, agent_id: int, ttl: float = TTL) -> str:
    """A new token for one run of one task. Only its hash is stored; the plain token is returned, never logged."""
    token = "acb_" + secrets.token_urlsafe(32)
    t = time.time()
    with session() as s:
        s.execute(delete(BridgeToken).where(BridgeToken.expires_at < t - 86400))
        s.add(BridgeToken(token_hash=_hash(token), goal_id=goal_id, task_id=task_id, agent_id=agent_id,
                          expires_at=t + ttl))
    return token


issue_token = issue


def revoke(token: str) -> None:
    with session() as s:
        row = s.scalars(select(BridgeToken).where(BridgeToken.token_hash == _hash(token or ""))).first()
        if row is not None and row.revoked_at is None:
            row.revoked_at = time.time()


def attach(req: Any, purpose: str, goal_id: int | None, task_id: int | None, agent_id: int) -> str | None:
    """Put a fresh token into the bridge entry of `req.data["mcp"]` (a task run), or drop the entry (anything else).
    Called on every run, so a request reused for a retry never carries a revoked token."""
    data = getattr(req, "data", None)
    if not isinstance(data, dict) or not data.get("mcp"):
        return None
    with session() as s:
        row = server_row(s)
        slug = row.slug if row is not None else None
    config, allowed = data["mcp"]
    servers = dict((config or {}).get("mcpServers") or {})
    if slug is None or slug not in servers:
        return None
    url = api_url()
    if purpose != "task" or task_id is None or goal_id is None or url is None:
        servers.pop(slug)
        data["mcp"] = ({**config, "mcpServers": servers},
                       [n for n in allowed if not n.startswith(f"mcp__{slug}__")])
        return None
    token = issue(goal_id, task_id, agent_id)
    entry = servers[slug]
    servers[slug] = {**entry, "env": {**(entry.get("env") or {}), "AC_BRIDGE_URL": url, "AC_BRIDGE_TOKEN": token}}
    data["mcp"] = ({**config, "mcpServers": servers}, list(allowed))
    return token


# --- the endpoint ---------------------------------------------------------------------------

def _text(args: dict[str, Any], key: str, top: int, required: bool = True) -> str | None:
    v = args.get(key)
    if v is None and not required:
        return None
    if not isinstance(v, str) or not v.strip() or len(v) > top:
        raise Refused(400, f"refused: {key} must be a text of 1-{top} characters")
    return v.strip()


def _texts(args: dict[str, Any], key: str, most: int, top: int) -> list[str]:
    v = args.get(key)
    if v is None:
        return []
    if not isinstance(v, list) or len(v) > most or any(not isinstance(x, str) or len(x) > top for x in v):
        raise Refused(400, f"refused: {key} must be a list of at most {most} texts of up to {top} characters")
    return [x.strip() for x in v if x.strip()]


def _check(tool: str, args: Any) -> dict[str, Any]:
    """Validated arguments of one tool (§1.3): exactly its keys, sizes bounded."""
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise Refused(400, "refused: arguments must be an object")
    unknown = set(args) - _ARGS[tool] - _SCOPE
    if unknown:
        raise Refused(400, f"refused: unknown argument {sorted(unknown)[0][:64]!r}")
    for k in _SCOPE & set(args):
        if not isinstance(args[k], int) or isinstance(args[k], bool):
            raise Refused(400, f"refused: {k} must be an integer")
    out: dict[str, Any] = {}
    if tool == "post_progress":
        out["text"] = _text(args, "text", 4000)
        p = args.get("percent")
        if p is not None and (not isinstance(p, int) or isinstance(p, bool) or not 0 <= p <= 100):
            raise Refused(400, "refused: percent must be an integer from 0 to 100")
        out["percent"] = p
    elif tool == "save_insight":
        out["title"], out["text"] = _text(args, "title", 200), _text(args, "text", 2000)
        out["evidence"] = _texts(args, "evidence", 5, 300)
    elif tool == "request_review":
        out["summary"] = _text(args, "summary", 1000)
        out["files"] = _texts(args, "files", 20, 300)
        reviewer = args.get("reviewer", "qa")
        if reviewer not in ("qa", "security"):
            raise Refused(400, "refused: reviewer must be qa or security")
        out["reviewer"] = reviewer
    return out


def _count(s: Any, goal_id: int, section: str, prefix: str) -> int:
    keys = s.scalars(select(BlackboardEntry.key).where(BlackboardEntry.goal_id == goal_id,
                                                       BlackboardEntry.section == section,
                                                       BlackboardEntry.key.startswith(prefix, autoescape=True)))
    return len(set(keys))


def _resolve(authorization: Any, tool: Any, arguments: Any, ctx: dict[str, Any]) -> dict[str, Any]:
    """Checks #2-#11 of §4 and what the call needs, in one session. Raises Refused."""
    scheme, _, token = (authorization or "").partition(" ") if isinstance(authorization, str) else ("", "", "")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        raise Refused(401, "refused: no bridge token")
    h = _hash(token)
    with session() as s:
        row = s.scalars(select(BridgeToken).where(BridgeToken.token_hash == h)).first()
        if row is None:
            raise Refused(401, "refused: unknown token")
        ctx.update(agent_id=row.agent_id, goal_id=row.goal_id)
        if row.revoked_at is not None:
            raise Refused(401, "refused: token revoked")
        if row.expires_at <= time.time():
            raise Refused(401, "refused: token expired")
        srv = server_row(s)
        if srv is not None:
            ctx["server_id"] = srv.id
        if srv is None or not srv.enabled:
            raise Refused(403, "refused: the bridge server is not added or turned off")
        if tool not in TOOLS or not any(t.get("name") == tool and t.get("allowed") for t in srv.tools or []):
            raise Refused(403, f"refused: tool {ctx['tool']} is not allowed on the bridge")
        granted = s.scalars(select(McpGrant).where(McpGrant.server_id == srv.id,
                                                   McpGrant.agent_id == row.agent_id)).first()
        if not srv.everyone and granted is None:
            raise Refused(403, "refused: this agent has no grant for the bridge")
        args = _check(tool, arguments)
        given = arguments or {}
        if "goal_id" in given and given["goal_id"] != row.goal_id:
            raise Refused(403, f"refused: goal {given['goal_id']} is not this run's goal", scope=True)
        if "task_id" in given and given["task_id"] != row.task_id:
            raise Refused(403, f"refused: task {given['task_id']} is not this run's task", scope=True)
        task, goal = s.get(Task, row.task_id), s.get(Goal, row.goal_id)
        if task is None or goal is None or task.goal_id != goal.id:
            raise Refused(403, "refused: this run's task no longer exists")
        if row.calls >= CALLS_PER_TOKEN:
            raise Refused(429, f"refused: the call budget of this run reached ({CALLS_PER_TOKEN})")
        n = 0
        if tool == "save_insight":
            n = _count(s, goal.id, "reports", f"insight:{task.key}:") + 1
            if n > INSIGHTS_PER_TASK:
                raise Refused(429, f"refused: the insight budget of this run reached ({INSIGHTS_PER_TASK} per task)")
        elif tool == "request_review":
            n = _count(s, goal.id, "messages", f"{task.key}:review:") + 1
            if n > REVIEWS_PER_TASK:
                raise Refused(429, f"refused: the review budget of this run reached ({REVIEWS_PER_TASK} per task)")
        row.calls += 1
        contract = task.contract or {}
        return {"args": args, "n": n, "goal_id": goal.id, "task_id": task.id, "agent_id": row.agent_id,
                "key": task.key, "role": task.role, "task_title": task.title, "goal_title": goal.title,
                "allowed_paths": list(goal.allowed_paths or []),
                "description": str(contract.get("description") or "")[:8000],
                "acceptance": contract.get("acceptance") or []}


def _run(tool: str, c: dict[str, Any]) -> str:
    """The call itself: a read of the token's task, or one blackboard write on the token's goal."""
    a, who = c["args"], {"author_agent_id": c["agent_id"], "task_id": c["task_id"]}
    if tool == "get_task":
        return json.dumps({"goal": {"id": c["goal_id"], "title": c["goal_title"], "allowed_paths": c["allowed_paths"]},
                           "task": {"id": c["task_id"], "key": c["key"], "title": c["task_title"], "role": c["role"],
                                    "description": c["description"], "acceptance": c["acceptance"]}},
                          ensure_ascii=False, default=str)
    if tool == "post_progress":
        section, key = blackboard.SECTION_OF_ROLE.get(c["role"], "engineering"), f"progress:{c['key']}"
        v = blackboard.put(c["goal_id"], section, key, a["text"], **who,
                           meta={"via": "bridge", "kind": "progress", "percent": a["percent"]})
    elif tool == "save_insight":
        section, key = "reports", f"insight:{c['key']}:{c['n']}"
        body = f"{a['title']}\n\n{a['text']}" + "".join(f"\n- evidence: {e}" for e in a["evidence"])
        v = blackboard.put(c["goal_id"], section, key, body, **who,
                           meta={"via": "bridge", "kind": "insight", "title": a["title"], "evidence": a["evidence"]})
    else:
        section, key = "messages", f"{c['key']}:review:{c['n']}"
        body = a["summary"] + (f"\nFiles: {', '.join(a['files'])}" if a["files"] else "")
        v = blackboard.put(c["goal_id"], section, key, body, **who,
                           meta={"from": c["role"], "to": a["reviewer"], "kind": "request", "delivered": [],
                                 "asked": 1, "via": "bridge"})
    return f"posted {section}/{key} v{v}"


def handle(authorization: str | None, tool: Any, arguments: Any, forwarded: bool = False,
           peer: str | None = None) -> tuple[int, dict[str, Any]]:
    """One call of `POST /api/bridge/call`: (HTTP status, answer). Writes exactly one McpCall, whatever happens."""
    started = time.monotonic()
    ctx: dict[str, Any] = {"server_id": 0, "agent_id": None, "goal_id": None,
                           "tool": tool[:128] if isinstance(tool, str) and tool else "?"}
    status, out, scope = 200, {}, False
    try:
        if forwarded or peer not in LOOPBACK:
            raise Refused(403, "refused: the bridge is reachable on loopback only")
        c = _resolve(authorization, tool, arguments, ctx)
        out = {"ok": True, "text": _run(tool, c)}
    except Refused as e:
        status, out, scope = e.status, {"ok": False, "error": e.error}, e.scope
    except Exception as e:  # noqa: BLE001 - the agent gets no stack; the event names the kind of failure only
        status, out = 500, {"ok": False, "error": "refused: the bridge failed on the server"}
        log_event("error", f"bridge call {ctx['tool']} failed: {type(e).__name__}", goal_id=ctx["goal_id"],
                  agent_id=ctx["agent_id"])
    _log(ctx, status == 200, out.get("error", ""), time.monotonic() - started, scope)
    return status, out


def refuse(status: int, error: str) -> tuple[int, dict[str, Any]]:
    """A request the API refused before `handle` (body too large or not JSON), logged the same way."""
    ctx = {"server_id": 0, "agent_id": None, "goal_id": None, "tool": "?"}
    _log(ctx, False, error, 0.0, False)
    return status, {"ok": False, "error": error}


def _log(ctx: dict[str, Any], ok: bool, error: str, duration: float, scope: bool) -> None:
    with session() as s:
        if not ctx["server_id"]:
            srv = server_row(s)
            ctx["server_id"] = srv.id if srv is not None else 0
        s.add(McpCall(server_id=ctx["server_id"], agent_id=ctx["agent_id"], actor="bridge", tool=ctx["tool"], ok=ok,
                      duration=duration, error=error[:2000]))
        if scope:
            log_event("security", f"bridge call {ctx['tool']} named another goal or task: {error[:300]}", s=s,
                      goal_id=ctx["goal_id"], agent_id=ctx["agent_id"], rule="bridge_scope")
