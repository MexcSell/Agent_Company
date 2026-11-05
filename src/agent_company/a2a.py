"""A2A interoperability (G-R-06): the company as an A2A 1.0 agent.

`GET /.well-known/agent-card.json` publishes an agent card (the enabled roles as skills); `POST /api/a2a` takes
JSON-RPC 2.0 calls from authenticated callers: `SendMessage` (a new task becomes a goal), `GetTask`, `ListTasks`
and `CancelTask`. Streaming, push notifications and the extended card are not offered.

Who may call: an A2A client an administrator issued (a bearer token, only its SHA-256 is stored), while the
`a2a_enabled` setting is on and no E5 stop is active. Issuing a client needs the measured enablement gate
(G-R-04) or an audited override. A client's tasks become goals of its project, created on behalf of the
administrator who issued it, with `origin="a2a"`: no repository, quality commands or permits from the caller,
no commands taken from its text, and the result always waits for a person's approval before it is merged.
A client only sees its own tasks; another client's task is "not found". A result classified above the client's
clearance is withheld.

Deviation from the specification: a goal runs for minutes to hours, so `SendMessage` answers at once with the
submitted task (as if `configuration.returnImmediately` were true); callers poll `GetTask`.
"""

from __future__ import annotations

import secrets
import uuid
from typing import Any

from sqlalchemy import select

from agent_company import learning
from agent_company.db import A2AClient, A2ATask, Agent, Goal, Project, now, session

VERSION = "1.0"
STATES = {"queued": "TASK_STATE_SUBMITTED", "planning": "TASK_STATE_WORKING", "running": "TASK_STATE_WORKING",
          "recovering": "TASK_STATE_WORKING", "paused": "TASK_STATE_WORKING",
          "awaiting_approval": "TASK_STATE_WORKING", "approved": "TASK_STATE_COMPLETED",
          "failed": "TASK_STATE_FAILED", "rejected": "TASK_STATE_REJECTED", "cancelled": "TASK_STATE_CANCELED"}
TERMINAL = {"TASK_STATE_COMPLETED", "TASK_STATE_FAILED", "TASK_STATE_REJECTED", "TASK_STATE_CANCELED"}
WAITING = {"paused": "Paused: waiting for an administrator (budget, cost or an emergency stop).",
           "awaiting_approval": "The work passed the quality gate and waits for a person's approval before it is "
                                "merged."}
MAX_TEXT = 20_000

# JSON-RPC and A2A error codes (specification §5.4, §9.5)
PARSE, INVALID_REQUEST, NO_METHOD, INVALID_PARAMS, INTERNAL = -32700, -32600, -32601, -32602, -32603
TASK_NOT_FOUND, NOT_CANCELABLE, NO_PUSH, UNSUPPORTED, CONTENT_TYPE, VERSION_NOT_SUPPORTED = (
    -32001, -32002, -32003, -32004, -32005, -32009)
UNAUTHENTICATED = -32040  # in the A2A range; the specification leaves the code of an authentication failure open
REASONS = {TASK_NOT_FOUND: "TASK_NOT_FOUND", NOT_CANCELABLE: "TASK_NOT_CANCELABLE",
           NO_PUSH: "PUSH_NOTIFICATION_NOT_SUPPORTED", UNSUPPORTED: "UNSUPPORTED_OPERATION",
           CONTENT_TYPE: "CONTENT_TYPE_NOT_SUPPORTED", VERSION_NOT_SUPPORTED: "VERSION_NOT_SUPPORTED",
           UNAUTHENTICATED: "UNAUTHENTICATED"}
PUSH_METHODS = {"CreateTaskPushNotificationConfig", "GetTaskPushNotificationConfig",
                "ListTaskPushNotificationConfigs", "DeleteTaskPushNotificationConfig"}
UNSUPPORTED_METHODS = {"SendStreamingMessage", "SubscribeToTask", "GetExtendedAgentCard"}


class RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code, self.message = code, message

    def body(self, rid: Any) -> dict[str, Any]:
        err: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.code in REASONS:
            err["data"] = [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": REASONS[self.code],
                            "domain": "a2a-protocol.org"}]
        return {"jsonrpc": "2.0", "id": rid, "error": err}


# --- clients (administrators) -------------------------------------------------------------------

def _hash(token: str) -> str:
    from agent_company.auth import _hash as sha

    return sha(token)


def client_dict(c: A2AClient, projects: dict[int, str] | None = None) -> dict[str, Any]:
    return {"id": c.id, "name": c.name, "project_id": c.project_id, "project": (projects or {}).get(c.project_id),
            "clearance": c.clearance, "revoked": c.revoked, "created_by": c.created_by,
            "created_at": c.created_at.isoformat(),
            "last_used_at": c.last_used_at.isoformat() if c.last_used_at else None}


def create_client(name: str, project_id: int | None, created_by: int, clearance: str = "internal"
                  ) -> tuple[dict[str, Any], str]:
    """A new client and its token (shown once). Its project may not be classified above its clearance."""
    name = name.strip()
    if not 3 <= len(name) <= 64:
        raise ValueError("a name of 3 to 64 characters")
    learning.check_level(clearance)
    token = "a2a_" + secrets.token_urlsafe(32)
    with session() as s:
        if s.scalars(select(A2AClient).where(A2AClient.name == name)).first() is not None:
            raise ValueError("a client with that name exists")
        project = s.get(Project, project_id) if project_id is not None else None
        if project_id is not None and project is None:
            raise KeyError(f"project {project_id}")
        if project is not None and learning.rank(project.classification) > learning.rank(clearance):
            raise ValueError(f"the project is {project.classification}; the client is cleared for {clearance}")
        c = A2AClient(name=name, token_hash=_hash(token), created_by=created_by, project_id=project_id,
                      clearance=clearance)
        s.add(c)
        s.flush()
        return client_dict(c, {project.id: project.name} if project else {}), token


def list_clients() -> list[dict[str, Any]]:
    with session() as s:
        names = dict(s.execute(select(Project.id, Project.name)).all())
        return [client_dict(c, names) for c in s.scalars(select(A2AClient).order_by(A2AClient.id))]


def revoke(client_id: int) -> dict[str, Any]:
    with session() as s:
        c = s.get(A2AClient, client_id)
        if c is None:
            raise KeyError(f"client {client_id}")
        c.revoked = True
        return client_dict(c)


def authenticate(authorization: str | None) -> dict[str, Any]:
    """The client a bearer token belongs to; raises RpcError(UNAUTHENTICATED)."""
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise RpcError(UNAUTHENTICATED, "a bearer token is required")
    with session() as s:
        c = s.scalars(select(A2AClient).where(A2AClient.token_hash == _hash(token.strip()))).first()
        if c is None or c.revoked:
            raise RpcError(UNAUTHENTICATED, "unknown or revoked token")
        c.last_used_at = now()
        return {"id": c.id, "name": c.name, "project_id": c.project_id, "created_by": c.created_by,
                "clearance": c.clearance}


# --- the agent card ------------------------------------------------------------------------------

def card(base_url: str) -> dict[str, Any]:
    """The public agent card: the company and the roles that are enabled now, as skills."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        app_version = version("agent-company")
    except PackageNotFoundError:
        app_version = "0"
    with session() as s:
        roles: dict[str, Agent] = {}
        for a in s.scalars(select(Agent).where(Agent.enabled, Agent.lifecycle == "active").order_by(Agent.id)):
            roles.setdefault(a.role, a)
    skills = [{"id": role, "name": a.title or role, "description": (a.goal or a.title or role)[:500],
               "tags": sorted((a.skills or {}).keys())[:10] or [role]} for role, a in sorted(roles.items())]
    return {
        "name": "Agent Company",
        "description": ("An AI software-development company: a General Manager plans, specialists design, write and "
                        "review code; every result passes a quality gate and waits for a person's approval. Send a "
                        "text message describing the work; the answer is a task to poll with GetTask (SendMessage "
                        "returns at once, as with returnImmediately)."),
        "supportedInterfaces": [{"url": base_url.rstrip("/") + "/api/a2a", "protocolBinding": "JSONRPC",
                                 "protocolVersion": VERSION}],
        "provider": {"organization": "Agent Company", "url": base_url.rstrip("/") + "/"},
        "version": str(app_version),
        "capabilities": {"streaming": False, "pushNotifications": False, "extendedAgentCard": False},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": skills,
        "securitySchemes": {"bearer": {"httpAuthSecurityScheme": {
            "scheme": "Bearer", "description": "A token an administrator of the company issued to you."}}},
        "securityRequirements": [{"bearer": []}],
    }


# --- tasks ---------------------------------------------------------------------------------------

def _text(message: dict[str, Any]) -> str:
    parts = message.get("parts")
    if not isinstance(parts, list) or not parts:
        raise RpcError(INVALID_PARAMS, "message.parts must be a non-empty list")
    texts = []
    for p in parts:
        if not isinstance(p, dict):
            raise RpcError(INVALID_PARAMS, "each part is an object")
        if "text" in p:
            texts.append(str(p["text"]))
        elif "data" in p or "raw" in p or "url" in p:
            raise RpcError(CONTENT_TYPE, "only text parts are accepted")
    text = "\n".join(texts).strip()
    if len(text) < 5:
        raise RpcError(INVALID_PARAMS, "describe the work in a text part")
    return text[:MAX_TEXT]


def _task(s, t: A2ATask, client: dict[str, Any], history_length: int | None = None,  # noqa: ANN001
          artifacts: bool = True) -> dict[str, Any]:
    g = s.get(Goal, t.goal_id)
    status = g.status if g is not None else "cancelled"
    state = STATES.get(status, "TASK_STATE_WORKING")
    note = WAITING.get(status, "")
    out_artifacts = []
    if g is not None and state in TERMINAL and g.result:
        if learning.rank(learning.goal_level(s, g)) > learning.rank(client["clearance"]):
            note = f"The result is classified above this client's clearance ({client['clearance']}); ask the company."
        elif state == "TASK_STATE_COMPLETED":
            out_artifacts = [{"artifactId": f"{t.id}-result", "name": "Result",
                              "description": f"goal #{g.id}, merged into {g.base_branch or 'the base branch'}",
                              "parts": [{"text": g.result[:MAX_TEXT], "mediaType": "text/plain"}]}]
        else:
            note = g.result[:2000]
    st: dict[str, Any] = {"state": state, "timestamp": (g.updated_at if g is not None else t.created_at)
                          .strftime("%Y-%m-%dT%H:%M:%SZ")}
    if note:
        st["message"] = {"messageId": f"{t.id}-status", "role": "ROLE_AGENT", "parts": [{"text": note}],
                         "taskId": t.id, "contextId": t.context_id}
    task: dict[str, Any] = {"id": t.id, "contextId": t.context_id, "status": st}
    history = list(t.history or [])
    if history_length is None or history_length > 0:
        task["history"] = history[-history_length:] if history_length else history
    if artifacts and out_artifacts:
        task["artifacts"] = out_artifacts
    return task


def _own(s, client: dict[str, Any], task_id: Any) -> A2ATask:  # noqa: ANN001
    t = s.get(A2ATask, str(task_id)) if task_id else None
    if t is None or t.client_id != client["id"]:  # another client's task does not exist for this one
        raise RpcError(TASK_NOT_FOUND, "task not found")
    return t


def send_message(client: dict[str, Any], params: dict[str, Any]) -> tuple[dict[str, Any], int | None]:
    """A new task: the message becomes a goal. Returns the result and the goal to queue."""
    from agent_company import governance, loop

    message = params.get("message")
    if not isinstance(message, dict) or not message.get("messageId"):
        raise RpcError(INVALID_PARAMS, "params.message with a messageId is required")
    if message.get("role") not in (None, "ROLE_USER"):
        raise RpcError(INVALID_PARAMS, "a message to this agent has role ROLE_USER")
    if message.get("taskId"):
        with session() as s:
            t = _own(s, client, message["taskId"])
            if message.get("contextId") and message["contextId"] != t.context_id:
                raise RpcError(INVALID_PARAMS, "contextId does not match the task")
            state = _task(s, t, client, 0)["status"]["state"]
        raise RpcError(UNSUPPORTED, f"the task is {state}; follow-up messages are not supported: send a new message"
                       if state not in TERMINAL else f"the task is {state} and takes no more messages")
    text = _text(message)
    why = governance.stopped(project_id=client["project_id"])
    if why:
        raise RpcError(UNSUPPORTED, f"the company is not taking work now: {why}")
    first = text.splitlines()[0].strip()
    title = first[:200] if len(first) >= 5 else text[:200]
    gid = loop.create_goal(title, f"Ordered over A2A by {client['name']}:\n\n{text}", created_by=client["created_by"],
                           project_id=client["project_id"], origin="a2a", extract_checks=False)
    context = str(message.get("contextId") or uuid.uuid4())[:128]
    stored = {"messageId": str(message["messageId"])[:128], "role": "ROLE_USER", "parts": [{"text": text}],
              "contextId": context}
    with session() as s:
        t = A2ATask(id=str(uuid.uuid4()), client_id=client["id"], goal_id=gid, context_id=context,
                    history=[stored | {"taskId": ""}])
        s.add(t)
        s.flush()
        t.history = [stored | {"taskId": t.id}]
        result = {"task": _task(s, t, client)}
    governance.audit(f"a2a:{client['name']}", "a2a_task", f"goal {gid}",
                     after={"task_id": result["task"]["id"], "title": title})
    return result, gid


def get_task(client: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    hl = params.get("historyLength")
    if hl is not None and (not isinstance(hl, int) or hl < 0):
        raise RpcError(INVALID_PARAMS, "historyLength is a non-negative integer")
    with session() as s:
        return _task(s, _own(s, client, params.get("id")), client, hl)


def list_tasks(client: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """The client's tasks, newest first, in pages (pageToken = the offset)."""
    size = params.get("pageSize", 50)
    if not isinstance(size, int) or not 1 <= size <= 100:
        raise RpcError(INVALID_PARAMS, "pageSize is 1 to 100")
    try:
        offset = int(params.get("pageToken") or 0)
    except ValueError as e:
        raise RpcError(INVALID_PARAMS, "unknown pageToken") from e
    want = params.get("status")
    if want is not None and want not in set(STATES.values()):
        raise RpcError(INVALID_PARAMS, "status is a TaskState")
    with session() as s:
        q = select(A2ATask).where(A2ATask.client_id == client["id"])
        if params.get("contextId"):
            q = q.where(A2ATask.context_id == str(params["contextId"]))
        tasks = [_task(s, t, client, params.get("historyLength", 0), bool(params.get("includeArtifacts")))
                 for t in s.scalars(q.order_by(A2ATask.created_at.desc(), A2ATask.id))]
    if want:
        tasks = [t for t in tasks if t["status"]["state"] == want]
    tasks.sort(key=lambda t: t["status"]["timestamp"], reverse=True)
    page = tasks[offset:offset + size]
    return {"tasks": page, "nextPageToken": str(offset + size) if offset + size < len(tasks) else "",
            "pageSize": size, "totalSize": len(tasks)}


def cancel_task(client: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    from agent_company import loop

    with session() as s:
        t = _own(s, client, params.get("id"))
        state, goal_id = _task(s, t, client, 0)["status"]["state"], t.goal_id
    if state == "TASK_STATE_CANCELED":
        return get_task(client, {"id": t.id})  # cancelling twice is idempotent
    if state in TERMINAL:
        raise RpcError(NOT_CANCELABLE, f"the task is {state}")
    loop.cancel(goal_id, actor=f"a2a:{client['name']}")
    with session() as s:  # loop.cancel leaves finished work waiting for approval alone; the caller withdraws it
        waiting = (g := s.get(Goal, goal_id)) is not None and g.status == "awaiting_approval"
    if waiting:
        from agent_company import workflow

        workflow.transition(goal_id, "cancelled", "withdrawn by its A2A caller", f"a2a:{client['name']}")
    return get_task(client, {"id": t.id})


def handle(body: Any, authorization: str | None, version: str | None) -> tuple[dict[str, Any], int | None, int]:
    """One JSON-RPC request: (response body, goal to queue, HTTP status)."""
    rid = body.get("id") if isinstance(body, dict) else None
    try:
        if not isinstance(body, dict) or body.get("jsonrpc") != "2.0" or not isinstance(body.get("method"), str):
            raise RpcError(INVALID_REQUEST, "a JSON-RPC 2.0 request object is required")
        try:
            client = authenticate(authorization)
        except RpcError as e:
            return e.body(rid), None, 401
        if (version or "").strip() != VERSION:
            raise RpcError(VERSION_NOT_SUPPORTED, f"this server speaks A2A {VERSION} (send the A2A-Version header)")
        method, params = body["method"], body.get("params") or {}
        if not isinstance(params, dict):
            raise RpcError(INVALID_PARAMS, "params is an object")
        goal = None
        if method == "SendMessage":
            result, goal = send_message(client, params)
        elif method == "GetTask":
            result = get_task(client, params)
        elif method == "ListTasks":
            result = list_tasks(client, params)
        elif method == "CancelTask":
            result = cancel_task(client, params)
        elif method in PUSH_METHODS:
            raise RpcError(NO_PUSH, "push notifications are not supported")
        elif method in UNSUPPORTED_METHODS:
            raise RpcError(UNSUPPORTED, f"{method} is not supported")
        else:
            raise RpcError(NO_METHOD, f"unknown method {method}")
        return {"jsonrpc": "2.0", "id": rid, "result": result}, goal, 200
    except RpcError as e:
        return e.body(rid), None, 200
    except (ValueError, KeyError) as e:
        return RpcError(INVALID_PARAMS, str(e)).body(rid), None, 200
    except Exception as e:  # noqa: BLE001 - a JSON-RPC caller gets a JSON-RPC error, never an HTML 500
        from agent_company.db import log_event

        log_event("error", f"A2A request failed: {e}"[:1000])
        return RpcError(INTERNAL, "internal error").body(rid), None, 200

