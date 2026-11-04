"""HTTP API and dashboard (FastAPI). Start it with `python -m agent_company serve`."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile, WebSocket
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from starlette.background import BackgroundTask

from agent_company import (
    auth,
    containers,
    evaluation,
    evolution,
    files,
    gitsync,
    governance,
    loop,
    performance,
    prefs,
    projects,
    registry,
    research,
    router,
    terminal,
    transport,
    usage,
    workspace,
)
from agent_company.config import settings
from agent_company.db import Agent, Event, Goal, Task, session

STATIC = Path(__file__).parent / "static"
COOKIE = "ac_session"


def bootstrap() -> None:
    """Seed the catalog and load settings. Safe to call while a server is running."""
    registry.seed()
    router.seed()
    projects.seed()
    prefs.load()


def reset_after_restart() -> None:
    """Only for the server that owns the work: nothing is running right after it starts."""
    with session() as s:
        for a in s.scalars(select(Agent).where(Agent.status == "busy")):
            a.status = "idle"


# --- auth dependencies --------------------------------------------------------------

def _client_ip(conn: Request | WebSocket) -> str | None:
    return conn.client.host if conn.client else None


def _session_token(conn: Request | WebSocket) -> str | None:
    """The session token in the sealed cookie, if it belongs to this browser (and IP, over HTTP)."""
    return transport.unseal(conn.cookies.get(COOKIE), conn.headers.get("user-agent"), _client_ip(conn))


def _wrong_scheme(conn: Request | WebSocket) -> bool:
    """Behind nginx (X-Forwarded-Proto set), a request on the scheme the Settings page did not choose."""
    proto = conn.headers.get("x-forwarded-proto")
    return proto is not None and proto != settings().transport


def current_user(request: Request) -> dict[str, Any]:
    u = auth.user_for_token(_session_token(request))
    if u is None:
        raise HTTPException(401, "sign in required")
    return u


def self_or_admin(user_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    """A user's own Git settings, or any user's for an administrator."""
    if user["id"] != user_id and not user["is_admin"]:
        raise HTTPException(403, "only your own Git settings")
    return user


def admin_user(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if not user["is_admin"]:
        raise HTTPException(403, "administrators only")
    return user


def allowed(perm: str):  # noqa: ANN201
    """Dependency: administrators, or users granted `perm` (Users page → Permissions)."""
    def check(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        if not auth.can(user, perm):
            raise HTTPException(403, f"needs the '{perm}' permission")
        return user

    return check


def _set_cookie(request: Request, response: Response, token: str) -> None:
    """Sealed (AES-GCM) and bound to the browser; over plain HTTP also bound to the client IP."""
    https = request.url.scheme == "https"
    value = transport.seal(token, request.headers.get("user-agent"), None if https else _client_ip(request))
    response.set_cookie(COOKIE, value, max_age=auth.SESSION_DAYS * 86400, httponly=True, samesite="lax",
                        secure=https, path="/")


_signups: dict[str, deque[float]] = defaultdict(deque)
_signup_lock = threading.Lock()


def _rate_limit(request: Request, limit: int = 10, window: int = 900) -> None:
    ip = request.client.host if request.client else "?"
    t = time.time()
    with _signup_lock:
        q = _signups[ip]
        while q and q[0] < t - window:
            q.popleft()
        if len(q) >= limit:
            raise HTTPException(429, "too many registrations; try again later")
        q.append(t)


# --- request bodies -------------------------------------------------------------------

class RegisterBody(BaseModel):
    name: str
    device_secret: str
    device_info: dict[str, Any] = Field(default_factory=dict)


class DeviceBody(BaseModel):
    device_secret: str


class GoalBody(BaseModel):
    title: str = Field(min_length=3, max_length=256)
    description: str = ""
    repo_path: str = ""
    urls: list[str] = Field(default_factory=list)
    quality_commands: list[str] = Field(default_factory=list)
    max_iterations: int | None = Field(default=None, ge=1, le=10)
    allowed_paths: list[str] = Field(default_factory=list)
    protect_tests: bool = False


class CrawlBody(BaseModel):
    url: str
    depth: int = Field(default=1, ge=0, le=3)
    max_pages: int = Field(default=20, ge=1, le=200)


class SourceBody(CrawlBody):
    pass


class ResearchBody(BaseModel):
    question: str = Field(min_length=5, max_length=2000)
    urls: list[str] = Field(default_factory=list, max_length=10)


class SettingBody(BaseModel):
    value: Any


class NoticeBody(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    body: str = ""
    level: str = "info"


def _audit(user: dict[str, Any], action: str, target: Any = "", before: dict[str, Any] | None = None,
           after: dict[str, Any] | None = None, severity: str = "info") -> None:
    governance.audit(f"user:{user['name']}", action, str(target), before, after, severity)


class CleanupBody(BaseModel):
    apply: bool = False
    all_images: bool = False
    volumes: bool = False
    older_than: str = "24h"


class VmBody(BaseModel):
    name: str = Field(min_length=1, max_length=63)
    image: str = "ubuntu:24.04"
    vm: bool = True
    cpus: int = 2
    memory: str = "4GiB"
    disk: str = ""


class AgentBody(BaseModel):
    role: str = Field(min_length=2, max_length=64)
    title: str = ""
    skills: dict[str, int] = Field(default_factory=dict)
    room: str = ""
    superior_id: int | None = None
    goal: str = ""
    writes: bool = False


class WriteBody(BaseModel):
    path: str
    text: str


class CreateBody(BaseModel):
    path: str
    folder: bool = False


class MoveBody(BaseModel):
    model_config = {"populate_by_name": True}
    src: str
    dest: str
    keep_source: bool = Field(default=False, alias="copy")


class PathsBody(BaseModel):
    paths: list[str]


class PathBody(BaseModel):
    path: str


class CompressBody(BaseModel):
    paths: list[str] = Field(min_length=1)
    name: str = ""


class TerminalBody(BaseModel):
    cwd: str = ""
    cols: int = Field(default=120, ge=2, le=1000)
    rows: int = Field(default=32, ge=2, le=500)


class ClaudeBody(BaseModel):
    prompt: str = Field(min_length=1, max_length=100_000)
    cwd: str = ""
    session_id: str = ""
    model: str = ""
    attachments: list[str] = Field(default_factory=list, max_length=10)


def ws_admin(ws: WebSocket) -> dict[str, Any] | None:
    """WebSocket auth: an administrator's session cookie and a same-origin page.

    Browsers do not apply CORS to WebSockets, so without the Origin check any site the
    administrator visits could open a shell here with their cookie.
    """
    from urllib.parse import urlparse

    origin = ws.headers.get("origin")
    hosts = {ws.headers.get("host"), *(h.strip() for h in ws.headers.get("x-forwarded-host", "").split(",") if h.strip())}
    if origin and urlparse(origin).netloc not in hosts:  # X-Forwarded-Host: behind a reverse proxy
        return None
    if _wrong_scheme(ws):
        return None
    user = auth.user_for_token(_session_token(ws))
    return user if user and user["is_admin"] else None


class EvalBody(BaseModel):
    suite: str
    label: str = Field(pattern=r"^[\w.-]{1,64}$")
    repeats: int = Field(default=1, ge=1, le=10)
    tasks: list[str] = Field(default_factory=list)


class StatusBody(BaseModel):
    status: str


class LifecycleBody(BaseModel):
    state: str


class RoleBody(BaseModel):
    role: str


class ProjectBody(BaseModel):
    name: str
    path: str = ""
    github_url: str = ""
    description: str = ""
    quality_commands: list[str] = Field(default_factory=list)


class CommandBody(BaseModel):
    content: str = ""
    attachments: list[str] = Field(default_factory=list)


class IdsBody(BaseModel):
    ids: list[int]


class GitIdentityBody(BaseModel):
    name: str = ""
    email: str = ""
    branch: str = ""


class GitTokenBody(BaseModel):
    token: str = Field(min_length=1, max_length=255)


class RouteBody(BaseModel):
    model_name: str
    escalation: str | None = None


class OpenRouterBody(BaseModel):
    id: str
    name: str | None = None


def create_app(start_background: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):  # noqa: ANN202
        bootstrap()
        app.state.queue = None
        app.state.rotation = None
        if start_background:
            try:  # nginx in front (MANAGE_NGINX=1): certificates and the site for the chosen scheme
                transport.apply(settings().transport)
            except Exception as e:  # noqa: BLE001 - the app itself must still come up
                print(f"[transport] nginx not configured: {e}", flush=True)
            reset_after_restart()
            app.state.queue = loop.GoalQueue(workers=2)
            app.state.queue.start()
            if settings().rotation_interval > 0:
                app.state.rotation = research.RotationThread(settings().rotation_interval)
                app.state.rotation.start()
        yield
        if app.state.rotation:
            app.state.rotation.stop.set()
        terminal.close_all()
        workspace.stop_all()

    app = FastAPI(title="Agent Company", version="0.2.0", lifespan=lifespan)

    @app.middleware("http")
    async def _scheme_gate(request: Request, call_next):  # noqa: ANN001, ANN202
        """Requests on the other scheme get a hand-off page (pages) or 421 (API), never data."""
        if _wrong_scheme(request) and request.url.path not in transport.EXEMPT:
            target = settings().transport
            if request.url.path.startswith("/api/"):
                return JSONResponse({"code": "wrong_scheme", "detail": f"This server uses {target.upper()} now.",
                                     "scheme": target}, status_code=421)
            return HTMLResponse(transport.handoff_page(target), headers={"Cache-Control": "no-store"})
        return await call_next(request)

    @app.exception_handler(auth.AuthError)
    async def _auth_error(_: Request, e: auth.AuthError) -> JSONResponse:
        return JSONResponse({"code": e.code, "detail": e.message}, status_code=e.status)

    @app.exception_handler(PermissionError)
    async def _forbidden(_: Request, e: PermissionError) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=403)

    @app.exception_handler(KeyError)
    async def _not_found(_: Request, e: KeyError) -> JSONResponse:
        return JSONResponse({"detail": f"not found: {e}"}, status_code=404)

    @app.exception_handler(files.FileError)
    async def _file_error(_: Request, e: files.FileError) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=400)

    @app.exception_handler(ValueError)
    async def _bad(_: Request, e: ValueError) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=400)

    # --- public ---
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True}

    @app.post("/api/auth/register")
    def register(body: RegisterBody, request: Request, response: Response) -> dict[str, Any]:
        _rate_limit(request)
        local = bool(request.client) and request.client.host in ("127.0.0.1", "::1")
        user, token = auth.register(body.name, body.device_secret, body.device_info, may_become_admin=local)
        if token:
            _set_cookie(request, response, token)
        return {"user": user, "signed_in": token is not None}

    @app.post("/api/auth/device")
    def device(body: DeviceBody, request: Request, response: Response) -> dict[str, Any]:
        user, token = auth.device_login(body.device_secret)
        _set_cookie(request, response, token)
        return {"user": user}

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response) -> dict[str, Any]:
        auth.logout(_session_token(request))
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @app.get("/api/auth/me")
    def me(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        return user

    # --- users (admin) ---
    @app.get("/api/users")
    def users(_: dict[str, Any] = Depends(admin_user)) -> list[dict[str, Any]]:
        return auth.list_users()

    @app.post("/api/users/{user_id}/{action}")
    def user_action(user_id: int, action: str, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        out = auth.admin_action(admin["id"], user_id, action)
        _audit(admin, f"user_{action}", out["name"], after={"status": out["status"], "is_admin": out["is_admin"]},
               severity="high" if action == "make_admin" else "info")
        return out

    @app.delete("/api/users/{user_id}")
    def user_delete(user_id: int, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        auth.delete_user(admin["id"], user_id)
        _audit(admin, "user_delete", user_id)
        return {"ok": True}

    # --- overview ---
    @app.get("/api/overview")
    def overview(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        with session() as s:
            by_status = dict(s.execute(select(Goal.status, func.count()).group_by(Goal.status)).all())
            agents = dict(s.execute(select(Agent.status, func.count()).where(Agent.enabled)
                                    .group_by(Agent.status)).all())
            pending = s.scalar(select(func.count()).select_from(auth.User)
                               .where(auth.User.status == "pending")) if user["is_admin"] else 0
            docs = s.scalar(select(func.count()).select_from(research.Document))
            proposals = s.scalar(select(func.count()).select_from(projects.Proposal)
                                 .where(projects.Proposal.status == "pending")) if user["is_admin"] else 0
        return {"goals": by_status, "agents": agents, "pending_users": pending, "documents": docs,
                "usage": usage.summary()["totals"], "unread": governance.unread_count(user),
                "pending_proposals": proposals}

    # --- agents ---
    @app.get("/api/rooms")
    def rooms(_: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
        return registry.list_rooms()

    @app.get("/api/agents")
    def agents(_: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
        return registry.list_agents()

    @app.get("/api/agents/{agent_id}")
    def agent(agent_id: int, _: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        return registry.agent_detail(agent_id) | {"certification": performance.certification(agent_id)}

    @app.patch("/api/agents/{agent_id}")
    def agent_update(agent_id: int, changes: dict[str, Any], admin: dict[str, Any] = Depends(allowed("manage_agents"))
                     ) -> dict[str, Any]:
        out = registry.update_agent(agent_id, changes)
        _audit(admin, "agent_update", out["name"], after=changes)
        return out

    @app.post("/api/agents")
    def agent_create(body: AgentBody, admin: dict[str, Any] = Depends(allowed("manage_agents"))) -> dict[str, Any]:
        out = registry.create_agent(body.role, body.title, body.skills, body.room, body.superior_id, body.goal,
                                    body.writes)
        _audit(admin, "agent_create", out["name"], after={"role": out["role"], "room": out["room"]})
        return out

    @app.post("/api/agents/consolidate")
    def agents_consolidate(body: RoleBody, admin: dict[str, Any] = Depends(allowed("manage_agents"))) -> list[dict[str, Any]]:
        out = evolution.consolidate(body.role)
        _audit(admin, "agents_consolidate", body.role, after={"merged": len(out)})
        return out

    @app.post("/api/agents/{agent_id}/clone")
    def agent_clone(agent_id: int, admin: dict[str, Any] = Depends(allowed("manage_agents"))) -> dict[str, Any]:
        out = registry.clone_agent(agent_id)
        _audit(admin, "agent_clone", out["name"], after={"from": agent_id})
        return out

    @app.post("/api/agents/{agent_id}/merge")
    def agent_merge(agent_id: int, admin: dict[str, Any] = Depends(allowed("manage_agents"))) -> dict[str, Any]:
        out = evolution.merge_clone(agent_id)
        if out is None:
            raise HTTPException(400, "only an idle, unretired clone can be merged")
        _audit(admin, "agent_merge", out["clone"], after=out)
        return out

    @app.post("/api/agents/{agent_id}/lifecycle")
    def agent_lifecycle(agent_id: int, body: LifecycleBody, admin: dict[str, Any] = Depends(allowed("manage_agents"))
                        ) -> dict[str, Any]:
        out = registry.set_lifecycle(agent_id, body.state)
        _audit(admin, "agent_lifecycle", out["name"], after={"state": body.state})
        return out

    @app.get("/api/agents/{agent_id}/versions")
    def agent_versions(agent_id: int, _: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
        return evolution.versions(agent_id)

    @app.post("/api/agents/{agent_id}/versions/{version}/restore")
    def agent_restore(agent_id: int, version: int, admin: dict[str, Any] = Depends(allowed("manage_agents"))) -> dict[str, Any]:
        out = evolution.restore(agent_id, version)
        _audit(admin, "playbook_restore", agent_id, after=out)
        return out

    # --- live events (Server-Sent Events) ---
    @app.get("/api/stream")
    async def stream(request: Request, _: dict[str, Any] = Depends(current_user)) -> Any:
        import asyncio

        from fastapi.responses import StreamingResponse

        from agent_company.db import Message, Notification

        def marks() -> tuple[int, int, int, int]:
            with session() as s:
                return (s.scalar(select(func.max(Event.id))) or 0, s.scalar(select(func.max(Message.id))) or 0,
                        s.scalar(select(func.max(Notification.id))) or 0,
                        s.scalar(select(func.count()).select_from(Goal).where(Goal.status == "running")) or 0)

        async def gen():  # noqa: ANN202
            last, idle = None, 0
            while not await request.is_disconnected():
                now_marks = await asyncio.to_thread(marks)
                if now_marks != last:
                    last, idle = now_marks, 0
                    yield f"data: {{\"event\": {now_marks[0]}, \"message\": {now_marks[1]}, \"notification\": {now_marks[2]}}}\n\n"
                else:
                    idle += 1
                    if idle % 15 == 0:
                        yield ": keep-alive\n\n"
                await asyncio.sleep(1)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # --- system ---
    @app.get("/api/system")
    def system_status(_: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        from agent_company import system

        return system.status()

    @app.post("/api/system/restart")
    def system_restart(admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        from agent_company import supervisor

        if not supervisor.request_restart(f"restart requested by {admin['name']}"):
            raise HTTPException(409, "the service is not running under the supervisor "
                                     "(start it with `python -m agent_company supervise`)")
        _audit(admin, "system_restart", "service", severity="high")
        return {"requested": True}

    # --- Docker and virtual machines (administrators' tools, never agents') ---
    def _host(call: Any) -> dict[str, Any]:
        try:
            return call()
        except containers.HostError as e:
            raise HTTPException(409, str(e)) from e

    @app.get("/api/containers")
    def containers_status(_: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        return containers.status()

    @app.post("/api/containers/docker/install")
    def docker_install(admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        out = _host(containers.install_docker)
        _audit(admin, "docker_install", "docker", after={"job": out["id"]}, severity="high")
        return out

    @app.post("/api/containers/docker/{action}")
    def docker_service(action: str, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        out = _host(lambda: containers.docker_service(action))
        _audit(admin, f"docker_{action}", "docker", after={"job": out["id"]}, severity="high")
        return out

    @app.post("/api/containers/cleanup")
    def docker_cleanup(body: CleanupBody, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        out = _host(lambda: containers.cleanup(**body.model_dump()))
        if body.apply:
            _audit(admin, "docker_cleanup", "docker", after={**body.model_dump(), "job": out["job"]["id"]},
                   severity="high")
        return out

    @app.post("/api/containers/lxd/install")
    def lxd_install(admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        out = _host(containers.install_lxd)
        _audit(admin, "lxd_install", "lxd", after={"job": out["id"]}, severity="high")
        return out

    @app.post("/api/containers/vms")
    def vm_launch(body: VmBody, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        out = _host(lambda: containers.launch(**body.model_dump()))
        _audit(admin, "vm_launch", body.name, after={**body.model_dump(), "job": out["id"]}, severity="high")
        return out

    @app.post("/api/containers/vms/{name}/{action}")
    def vm_action(name: str, action: str, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        out = _host(lambda: containers.instance_action(name, action))
        _audit(admin, f"vm_{action}", name, after={"job": out["id"]},
               severity="high" if action == "delete" else "info")
        return out

    # --- file manager (administrators) ---
    @app.get("/api/files/roots")
    def file_roots(_: dict[str, Any] = Depends(admin_user)) -> list[dict[str, str]]:
        return files.roots()

    @app.get("/api/files/list")
    def file_list(path: str, _: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        return files.listing(path)

    @app.get("/api/files/read")
    def file_read(path: str, _: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        return files.read(path)

    @app.get("/api/files/raw")
    def file_raw(path: str, download: bool = False, admin: dict[str, Any] = Depends(admin_user)) -> FileResponse:
        p = files.resolve(path)
        if p.is_dir():
            raise HTTPException(400, "that is a folder; download it as a zip")
        if download:
            _audit(admin, "file_download", str(p))
        return FileResponse(p, filename=p.name if download else None,
                            content_disposition_type="attachment" if download else "inline",
                            headers={"X-Content-Type-Options": "nosniff",
                                     "Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'"})

    @app.get("/api/files/archive")
    def file_archive(paths: list[str] = Query(...), admin: dict[str, Any] = Depends(admin_user)) -> FileResponse:
        tmp, name = files.download_archive(paths)
        _audit(admin, "file_download", ", ".join(paths)[:256], after={"zip": name})
        return FileResponse(tmp, filename=name, media_type="application/zip",
                            background=BackgroundTask(tmp.unlink, missing_ok=True))

    @app.post("/api/files/compress")
    def file_compress(body: CompressBody, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        out = files.compress(body.paths, body.name)
        _audit(admin, "file_compress", out["path"], after={"from": body.paths[:20]})
        return out

    @app.post("/api/files/extract")
    def file_extract(body: PathBody, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        out = files.extract(body.path)
        _audit(admin, "file_extract", body.path, after=out)
        return out

    @app.post("/api/files/roots")
    def file_root_add(body: PathBody, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, str]:
        out = files.add_root(body.path)
        _audit(admin, "folder_open", out["path"], severity="high")
        return out

    @app.delete("/api/files/roots")
    def file_root_remove(path: str, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        files.remove_root(path)
        _audit(admin, "folder_close", path)
        return {"ok": True}

    @app.put("/api/files/write")
    def file_write(body: WriteBody, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        out = files.write(body.path, body.text)
        _audit(admin, "file_write", out["path"], after={"size": out["size"]})
        return out

    @app.post("/api/files/create")
    def file_create(body: CreateBody, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        out = files.create(body.path, body.folder)
        _audit(admin, "file_create", out["path"])
        return out

    @app.post("/api/files/move")
    def file_move(body: MoveBody, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        out = files.move(body.src, body.dest, body.keep_source)
        _audit(admin, "file_copy" if body.keep_source else "file_move", body.src, after={"to": out["path"]})
        return out

    @app.post("/api/files/delete")
    def file_delete(body: PathsBody, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        done = files.delete(body.paths)
        _audit(admin, "file_delete", ", ".join(done)[:256], severity="high")
        return {"deleted": done}

    @app.post("/api/files/upload")
    async def file_upload(folder: str, file: UploadFile = File(...), admin: dict[str, Any] = Depends(admin_user)
                          ) -> dict[str, Any]:
        data = await file.read(50 * 1024 * 1024 + 1)
        if len(data) > 50 * 1024 * 1024:
            raise HTTPException(413, "files may be at most 50 MB")
        out = files.upload(folder, file.filename or "file", data)
        _audit(admin, "file_upload", out["path"], after={"size": out["size"]})
        return out

    @app.get("/api/files/folders")
    def file_folders(path: str = "", _: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        return files.folders(path)

    # --- workspace: terminals (administrators; the admin's own console, not an agent) ---
    @app.get("/api/terminal/sessions")
    def terminal_list(_: dict[str, Any] = Depends(admin_user)) -> list[dict[str, Any]]:
        return terminal.list_sessions()

    @app.post("/api/terminal/sessions")
    async def terminal_open(body: TerminalBody, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        sess = terminal.create(body.cwd, admin["name"], body.cols, body.rows)
        _audit(admin, "terminal_open", sess.cwd, after={"id": sess.id}, severity="high")
        return sess.to_dict()

    @app.delete("/api/terminal/sessions/{sid}")
    def terminal_close(sid: str, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        terminal.get(sid)
        terminal.close(sid)
        _audit(admin, "terminal_close", sid)
        return {"ok": True}

    @app.websocket("/api/terminal/sessions/{sid}/ws")
    async def terminal_ws(ws: WebSocket, sid: str) -> None:
        import asyncio
        import json as _json

        from starlette.websockets import WebSocketDisconnect

        if ws_admin(ws) is None:
            await ws.close(code=4403)
            return
        sess = terminal.SESSIONS.get(sid)
        if sess is None:
            await ws.close(code=4404)
            return
        await ws.accept()
        q = sess.attach()

        async def outgoing() -> None:
            if sess.buffer:
                await ws.send_bytes(bytes(sess.buffer))
            if not sess.alive:
                await ws.send_text(_json.dumps({"t": "exit", "code": sess.exit_code}))
                return
            while True:
                data = await q.get()
                if data is None:
                    await ws.send_text(_json.dumps({"t": "exit", "code": sess.exit_code}))
                    return
                while not q.empty():  # coalesce bursts into one frame
                    more = q.get_nowait()
                    if more is None:
                        q.put_nowait(None)
                        break
                    data += more
                await ws.send_bytes(data)

        async def incoming() -> None:
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    return
                if msg.get("bytes"):
                    sess.write(msg["bytes"])
                elif msg.get("text"):
                    try:
                        m = _json.loads(msg["text"])
                    except ValueError:
                        continue
                    if m.get("t") == "in":
                        sess.write(str(m.get("data", "")).encode())
                    elif m.get("t") == "resize":
                        sess.resize(int(m.get("cols") or 80), int(m.get("rows") or 24))

        # Either side ending (shell exit, browser gone, send to a closed socket) ends the
        # connection; the shell itself keeps running for the next attach.
        tasks = [asyncio.create_task(outgoing()), asyncio.create_task(incoming())]
        try:
            _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
        finally:
            sess.detach(q)
            for t in tasks:
                if t.done() and not t.cancelled():
                    t.exception()  # retrieved, so asyncio does not log it
            try:
                await ws.close()
            except (RuntimeError, WebSocketDisconnect):
                pass

    # --- workspace: Claude Code panel (administrators) ---
    @app.get("/api/workspace/claude")
    def claude_info(_: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        return workspace.available()

    @app.post("/api/workspace/claude")
    def claude_start(body: ClaudeBody, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        images = projects.uploads(body.attachments) if body.attachments else []
        try:
            run = workspace.start(body.prompt, body.cwd, admin["name"], body.session_id, body.model, images)
        except RuntimeError as e:
            raise HTTPException(503, str(e)) from e
        _audit(admin, "workspace_claude", run.cwd, after={"run": run.id, "prompt": body.prompt[:200],
                                                         "resume": body.session_id or None}, severity="high")
        return run.to_dict()

    @app.get("/api/workspace/claude/{run_id}")
    def claude_events(run_id: str, after: int = 0, _: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        return workspace.get(run_id).to_dict(max(0, after))

    @app.post("/api/workspace/claude/{run_id}/stop")
    def claude_stop(run_id: str, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        run = workspace.stop(run_id)
        _audit(admin, "workspace_claude_stop", run.cwd, after={"run": run_id})
        return {"status": run.status}

    @app.get("/api/defects")
    def defects(status: str = "", goal_id: int | None = None, _: dict[str, Any] = Depends(current_user)
                ) -> list[dict[str, Any]]:
        return performance.list_defects(status, goal_id)

    @app.post("/api/defects/{defect_id}/status")
    def defect_status(defect_id: int, body: StatusBody, admin: dict[str, Any] = Depends(admin_user)
                      ) -> dict[str, Any]:
        out = performance.set_defect_status(defect_id, body.status)
        _audit(admin, "defect_status", defect_id, after={"status": body.status})
        return out

    @app.get("/api/certifications")
    def certifications(_: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
        return performance.certifications()

    @app.get("/api/performance")
    def performance_report(_: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        return performance.report()

    @app.get("/api/evals")
    def evals(_: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        return {"suites": evaluation.list_suites(), "labels": evaluation.labels()}

    @app.get("/api/evals/compare")
    def evals_compare(a: str, b: str, _: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        return evaluation.compare(a, b)

    @app.get("/api/evals/{label}")
    def eval_summary(label: str, _: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        return evaluation.summary(label)

    @app.post("/api/evals/run")
    def eval_run(body: EvalBody, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        evaluation.load_suite(body.suite)  # validate before starting
        _audit(admin, "eval_run", body.label, after={"suite": body.suite, "repeats": body.repeats})

        def run() -> None:
            try:
                evaluation.run_suite(body.suite, body.label, body.repeats, body.tasks or None)
            except Exception as e:  # noqa: BLE001 - reported as an event
                from agent_company.db import log_event

                log_event("error", f"eval {body.label} failed: {e}")

        threading.Thread(target=run, daemon=True, name=f"eval-{body.label}").start()
        return {"started": body.label}

    @app.get("/api/evolution")
    def evolution_overview(_: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        return evolution.overview()

    @app.post("/api/evolution/cycle")
    def evolution_cycle(admin: dict[str, Any] = Depends(allowed("manage_agents"))) -> dict[str, Any]:
        return {"experiments_started": evolution.cycle()}

    # --- goals ---
    @app.get("/api/goals")
    def goals(user: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
        with session() as s:
            seen = _visible_project_ids(s, user)
            return [_goal_dict(g) for g in s.scalars(select(Goal).order_by(Goal.id.desc()).limit(200))
                    if seen is None or g.project_id is None or g.project_id in seen]

    @app.post("/api/goals")
    def goal_create(body: GoalBody, request: Request, user: dict[str, Any] = Depends(allowed("run"))
                    ) -> dict[str, Any]:
        if (body.repo_path or body.quality_commands) and not user["is_admin"]:
            raise HTTPException(403, "only administrators can set a repository or quality commands")
        gid = loop.create_goal(body.title, body.description, body.repo_path, body.urls,
                               body.quality_commands, body.max_iterations, created_by=user["id"],
                               allowed_paths=body.allowed_paths, protect_tests=body.protect_tests,
                               extract_checks=user["is_admin"])
        if body.repo_path or body.quality_commands:
            _audit(user, "goal_create_privileged", gid,
                   after={"repo_path": body.repo_path, "quality_commands": body.quality_commands})
        q = request.app.state.queue
        if q is not None:
            q.submit(gid)
        else:
            threading.Thread(target=_run_quietly, args=(gid,), daemon=True).start()
        return {"id": gid}

    @app.get("/api/goals/{goal_id}")
    def goal(goal_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        with session() as s:
            g = s.get(Goal, goal_id)
            seen = _visible_project_ids(s, user)
            if g is None or (seen is not None and g.project_id is not None and g.project_id not in seen):
                raise KeyError(goal_id)
            d = _goal_dict(g)
            d["tasks"] = [{"id": t.id, "iteration": t.iteration, "key": t.key, "title": t.title, "role": t.role,
                           "agent_id": t.agent_id, "status": t.status, "model": t.model_name,
                           "depends_on": t.depends_on, "output": t.output[-8000:], "gate": t.gate,
                           "contract": t.contract}
                          for t in s.scalars(select(Task).where(Task.goal_id == goal_id).order_by(Task.id))]
            d["events"] = [_event_dict(e) for e in s.scalars(
                select(Event).where(Event.goal_id == goal_id).order_by(Event.id))]
        d["defects"] = performance.list_defects(goal_id=goal_id)
        return d

    @app.post("/api/goals/{goal_id}/approve")
    def goal_approve(goal_id: int, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        out = loop.approve(goal_id)
        _audit(admin, "goal_approve", goal_id, after=out)
        return out

    @app.post("/api/goals/{goal_id}/reject")
    def goal_reject(goal_id: int, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        loop.reject(goal_id)
        _audit(admin, "goal_reject", goal_id)
        return {"ok": True}

    @app.post("/api/goals/{goal_id}/cancel")
    def goal_cancel(goal_id: int, user: dict[str, Any] = Depends(allowed("run"))) -> dict[str, Any]:
        with session() as s:
            g = s.get(Goal, goal_id)
            seen = _visible_project_ids(s, user)
            if g is None or (seen is not None and g.project_id is not None and g.project_id not in seen):
                raise KeyError(goal_id)
        loop.cancel(goal_id)
        return {"ok": True}

    @app.get("/api/events")
    def events(after: int = 0, goal_id: int | None = None, user: dict[str, Any] = Depends(current_user)
               ) -> list[dict[str, Any]]:
        with session() as s:
            q = select(Event).where(Event.id > after)
            if goal_id is not None:
                q = q.where(Event.goal_id == goal_id)
            rows = s.scalars(q.order_by(Event.id.desc()).limit(200)).all()
            seen = _visible_project_ids(s, user)
            if seen is not None:
                hidden = set(s.scalars(select(Goal.id).where(Goal.project_id.is_not(None),
                                                             Goal.project_id.not_in(seen or {-1}))))
                rows = [e for e in rows if e.goal_id not in hidden]
            return [_event_dict(e) for e in reversed(rows)]

    # --- models ---
    @app.get("/api/models")
    def models(_: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        return {"models": router.list_models(), "routes": router.list_routes(),
                "escalations": router.list_escalations(),
                "default_model": settings().default_model, "executor": settings().executor}

    @app.post("/api/models")
    def model_add(data: dict[str, Any], admin: dict[str, Any] = Depends(allowed("manage_models"))) -> dict[str, Any]:
        out = router.add_model(data)
        _audit(admin, "model_add", out["name"], after={"model_id": out["model_id"], "kind": out["kind"]})
        return out

    @app.patch("/api/models/{model_id}")
    def model_update(model_id: int, data: dict[str, Any], admin: dict[str, Any] = Depends(allowed("manage_models"))
                     ) -> dict[str, Any]:
        out = router.update_model(model_id, data)
        _audit(admin, "model_update", out["name"], after=data)
        return out

    @app.delete("/api/models/{model_id}")
    def model_delete(model_id: int, admin: dict[str, Any] = Depends(allowed("manage_models"))) -> dict[str, Any]:
        router.delete_model(model_id)
        _audit(admin, "model_delete", model_id)
        return {"ok": True}

    @app.post("/api/models/{model_id}/test")
    def model_test(model_id: int, admin: dict[str, Any] = Depends(allowed("manage_models"))) -> dict[str, Any]:
        return router.test_model(model_id)

    @app.put("/api/routes/{role}")
    def route_set(role: str, body: RouteBody, admin: dict[str, Any] = Depends(allowed("manage_models"))) -> dict[str, Any]:
        before = router.list_routes().get(role)
        router.set_route(role, body.model_name, body.escalation)
        _audit(admin, "route_set", role, before={"model": before},
               after={"model": body.model_name, "escalation": body.escalation})
        return {"role": role, "model_name": body.model_name}

    @app.get("/api/openrouter/models")
    def openrouter_models(q: str = "", free: bool = False, _: dict[str, Any] = Depends(current_user)
                          ) -> list[dict[str, Any]]:
        try:
            return router.openrouter_catalog(q, free)
        except httpx.HTTPError as e:
            raise HTTPException(502, f"OpenRouter catalog unavailable: {e}") from e

    @app.post("/api/openrouter/models")
    def openrouter_add(body: OpenRouterBody, _: dict[str, Any] = Depends(allowed("manage_models"))) -> dict[str, Any]:
        return router.add_openrouter_model(body.id, body.name)

    @app.get("/api/providers")
    def provider_list(_: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
        return router.providers()

    @app.get("/api/providers/{provider}/models")
    def provider_models(provider: str, q: str = "", _: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
        try:
            return router.provider_catalog(provider, q)
        except httpx.HTTPError as e:
            raise HTTPException(502, f"model list unavailable: {e}") from e

    @app.post("/api/providers/{provider}/models")
    def provider_add(provider: str, body: OpenRouterBody, admin: dict[str, Any] = Depends(allowed("manage_models"))
                     ) -> dict[str, Any]:
        out = router.add_provider_model(provider, body.id, body.name)
        _audit(admin, "model_add", out["name"], after={"model_id": out["model_id"], "base_url": out["base_url"]})
        return out

    @app.get("/api/usage")
    def usage_summary(_: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        return usage.summary()

    # --- research ---
    @app.post("/api/research/crawl")
    def crawl(body: CrawlBody, _: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        with session() as s:
            lead = s.scalars(select(Agent).where(Agent.role == "research")).first()
            lead_id = lead.id if lead else None
        return research.collect(body.url, body.depth, body.max_pages, agent_id=lead_id)

    @app.post("/api/research/requests")
    def research_request(body: ResearchBody, user: dict[str, Any] = Depends(allowed("run"))) -> dict[str, Any]:
        rid = research.request_report(body.question, body.urls, user["id"])

        def run() -> None:
            try:
                research.run_report(rid)
            except Exception:  # noqa: BLE001 - recorded on the report
                pass

        threading.Thread(target=run, daemon=True, name=f"report-{rid}").start()
        return {"id": rid}

    @app.get("/api/research/requests")
    def research_reports(_: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
        return research.list_reports()

    @app.get("/api/research/search")
    def search(q: str, limit: int = 10, _: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
        return research.search(q, min(limit, 50))

    @app.get("/api/research/documents")
    def documents(_: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
        with session() as s:
            return [{"id": d.id, "url": d.url, "title": d.title, "chars": len(d.text),
                     "source": d.source_url, "at": d.created_at.isoformat(),
                     "category": d.category or research.classify_source(d.url), "flags": d.flags or []}
                    for d in s.scalars(select(research.Document).order_by(research.Document.id.desc()).limit(300))]

    @app.get("/api/research/sources")
    def sources(_: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        return {"sources": research.list_sources(), "roster": research.rotation_roster(),
                "interval": settings().rotation_interval}

    @app.post("/api/research/sources")
    def source_add(body: SourceBody, _: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        return research.add_source(body.url, body.depth, body.max_pages)

    @app.post("/api/research/rotate")
    def rotate(_: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        return research.rotation_tick() or {"ran": False, "detail": "no enabled source or no research agent"}

    # --- projects and the command window ---
    @app.get("/api/projects")
    def projects_list(user: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
        return projects.list_projects(user)

    @app.post("/api/projects")
    def project_create(body: ProjectBody, admin: dict[str, Any] = Depends(allowed("create_projects"))
                       ) -> dict[str, Any]:
        if not admin["is_admin"] and (body.path or body.quality_commands):
            raise HTTPException(403, "only administrators can choose a work folder or quality commands")
        out = projects.create(body.name, body.path, body.github_url, body.description, body.quality_commands,
                              created_by=admin["id"])
        granted = admin["permissions"].get("projects", "all")
        if not admin["is_admin"] and granted != "all":
            auth.set_permissions(admin["id"], {"projects": [*granted, out["id"]]})
        _audit(admin, "project_create", out["name"], after={"path": out["path"], "github_url": out["github_url"]})
        return out

    @app.patch("/api/projects/{project_id}")
    def project_update(project_id: int, changes: dict[str, Any], admin: dict[str, Any] = Depends(admin_user)
                       ) -> dict[str, Any]:
        out = projects.update(project_id, changes)
        _audit(admin, "project_update", out["name"], after=changes)
        return out

    @app.post("/api/projects/delete")
    def project_delete(body: IdsBody, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        done = projects.delete(body.ids)
        _audit(admin, "project_delete", ",".join(map(str, done)))
        return {"deleted": done}

    @app.get("/api/projects/{project_id}")
    def project_detail(project_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        p = projects.get(project_id, user)
        with session() as s:
            goals = [_goal_dict(g) for g in s.scalars(select(Goal).where(Goal.project_id == p.id)
                                                       .order_by(Goal.id.desc()).limit(50))]
        return projects.project_dict(p) | {"members": projects.members(p.id), "goals": goals,
                                           "proposals": [x for x in projects.list_proposals(user)
                                                         if x["project_id"] == p.id][:30]}

    @app.get("/api/projects/{project_id}/messages")
    def project_messages(project_id: int, after: int = 0, user: dict[str, Any] = Depends(current_user)
                         ) -> list[dict[str, Any]]:
        projects.get(project_id, user)
        return projects.messages(project_id, after)

    @app.post("/api/projects/{project_id}/command")
    def project_command(project_id: int, body: CommandBody, user: dict[str, Any] = Depends(current_user)
                        ) -> dict[str, Any]:
        return projects.command(project_id, user, body.content, body.attachments)

    @app.post("/api/projects/{project_id}/sync")
    def project_sync(project_id: int, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        projects.get(project_id, admin)
        return {"message": gitsync.sync_project(project_id, admin)}

    @app.post("/api/uploads")
    async def upload(file: UploadFile = File(...), user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        data = await file.read(projects.MAX_UPLOAD + 1)
        return projects.save_upload(file.filename or "file", data, file.content_type or "", user["id"])

    @app.get("/api/uploads/{uid}")
    def upload_get(uid: str, _: dict[str, Any] = Depends(current_user)) -> FileResponse:
        path, ctype = projects.upload_path(uid)
        return FileResponse(path, media_type=ctype, headers={"X-Content-Type-Options": "nosniff",
                                                             "Content-Security-Policy": "default-src 'none'"})

    @app.get("/api/proposals")
    def proposals_list(status: str = "", user: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
        return projects.list_proposals(user, status)

    @app.post("/api/proposals/{proposal_id}/{decision}")
    def proposal_decide(proposal_id: int, decision: str, admin: dict[str, Any] = Depends(admin_user)
                        ) -> dict[str, Any]:
        if decision not in {"approve", "reject"}:
            raise HTTPException(404, "unknown decision")
        out = projects.decide(proposal_id, decision == "approve", admin["name"])
        _audit(admin, f"proposal_{decision}", proposal_id, after={"status": out["status"],
                                                                 "goal": out.get("spawned_goal_id")})
        return out

    @app.put("/api/users/{user_id}/permissions")
    def user_permissions(user_id: int, perms: dict[str, Any], admin: dict[str, Any] = Depends(admin_user)
                         ) -> dict[str, Any]:
        out = auth.set_permissions(user_id, perms)
        _audit(admin, "user_permissions", out["name"], after=out["permissions"], severity="high")
        return out

    @app.get("/api/users/{user_id}/git")
    def user_git_get(user_id: int, _: dict[str, Any] = Depends(self_or_admin)) -> dict[str, Any]:
        return {"identity": gitsync.identity_for(user_id), **gitsync.access_status(user_id)}

    @app.put("/api/users/{user_id}/git")
    def user_git(user_id: int, body: GitIdentityBody, actor: dict[str, Any] = Depends(self_or_admin)
                 ) -> dict[str, Any]:
        out = gitsync.set_identity(user_id, body.name, body.email, body.branch)
        _audit(actor, "user_git_identity", user_id, after=out)
        return out

    @app.put("/api/users/{user_id}/git/token")
    def user_git_token(user_id: int, body: GitTokenBody, actor: dict[str, Any] = Depends(self_or_admin)
                       ) -> dict[str, Any]:
        gitsync.set_token(user_id, body.token)
        _audit(actor, "user_git_token_set", user_id, severity="high")  # never the value
        return gitsync.access_status(user_id)

    @app.delete("/api/users/{user_id}/git/token")
    def user_git_token_clear(user_id: int, actor: dict[str, Any] = Depends(self_or_admin)) -> dict[str, Any]:
        gitsync.clear_token(user_id)
        _audit(actor, "user_git_token_removed", user_id)
        return gitsync.access_status(user_id)

    @app.post("/api/users/{user_id}/git/ssh-key")
    def user_git_key(user_id: int, actor: dict[str, Any] = Depends(self_or_admin)) -> dict[str, Any]:
        gitsync.create_ssh_key(user_id)
        _audit(actor, "user_git_ssh_key_created", user_id, severity="high")
        return gitsync.access_status(user_id)

    @app.delete("/api/users/{user_id}/git/ssh-key")
    def user_git_key_delete(user_id: int, actor: dict[str, Any] = Depends(self_or_admin)) -> dict[str, Any]:
        gitsync.delete_ssh_key(user_id)
        _audit(actor, "user_git_ssh_key_deleted", user_id)
        return gitsync.access_status(user_id)

    @app.post("/api/users/{user_id}/git/test")
    def user_git_test(user_id: int, _: dict[str, Any] = Depends(self_or_admin)) -> dict[str, Any]:
        return gitsync.test_access(user_id)

    # --- settings, notifications, audit ---
    @app.get("/api/tls/ca.crt")
    def tls_ca() -> FileResponse:
        """The company CA (public: a computer needs it before it can trust the site)."""
        return FileResponse(transport.ca_path(), media_type="application/x-x509-ca-cert",
                            filename="agent-company-ca.crt")

    @app.get("/api/tls")
    def tls_info(request: Request, _: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        return {**transport.info(), "current_scheme": request.url.scheme,
                "behind_proxy": request.headers.get("x-forwarded-proto") is not None}

    @app.get("/api/settings")
    def settings_get(_: dict[str, Any] = Depends(admin_user)) -> list[dict[str, Any]]:
        return prefs.describe()

    @app.put("/api/settings/{key}")
    def settings_put(key: str, body: SettingBody, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        before = getattr(settings(), key, None)
        value = prefs.set_value(key, body.value)
        _audit(admin, "setting_set", key, before={"value": before}, after={"value": value})
        return {"key": key, "value": value}

    @app.delete("/api/settings/{key}")
    def settings_clear(key: str, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        prefs.clear(key)
        _audit(admin, "setting_clear", key)
        return {"ok": True}

    @app.get("/api/notifications")
    def notifications(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        return governance.list_notifications(user)

    @app.post("/api/notifications/read")
    def notifications_read(ids: list[int] | None = None, user: dict[str, Any] = Depends(current_user)
                           ) -> dict[str, Any]:
        governance.mark_read(user, ids)
        return {"ok": True}

    @app.post("/api/notifications")
    def notice(body: NoticeBody, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        """An administrator's notice to every user."""
        level = body.level if body.level in {"info", "success", "warning", "error"} else "info"
        governance.notify(body.title, body.body, level=level, source=f"notice:{admin['name']}")
        _audit(admin, "notice_post", body.title)
        return {"ok": True}

    @app.delete("/api/notifications/{nid}")
    def notification_delete(nid: int, _: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        governance.delete_notification(nid)
        return {"ok": True}

    @app.get("/api/audit")
    def audit_list(action: str = "", _: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        return {"records": governance.list_audit(action=action), "chain": governance.verify_audit()}

    # --- dashboard ---
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    return app


def _visible_project_ids(s: Any, user: dict[str, Any]) -> set[int] | None:
    """None for administrators (everything), else the ids of the projects this user may see."""
    if user["is_admin"]:
        return None
    return set(s.scalars(projects.visible(user).with_only_columns(projects.Project.id)))


def _run_quietly(goal_id: int) -> None:
    try:
        loop.run_goal(goal_id)
    except Exception:  # noqa: BLE001 - recorded on the goal
        pass


def _goal_dict(g: Goal) -> dict[str, Any]:
    return {"id": g.id, "title": g.title, "description": g.description, "status": g.status,
            "iteration": g.iteration, "max_iterations": g.max_iterations, "repo_path": g.repo_path,
            "branch": g.branch, "base_branch": g.base_branch, "worktree": g.worktree, "urls": g.urls,
            "quality_commands": g.quality_commands, "result": g.result, "project_id": g.project_id,
            "origin": g.origin, "parent_goal_id": g.parent_goal_id, "allowed_paths": g.allowed_paths,
            "protect_tests": g.protect_tests, "baseline": g.baseline, "history": g.history,
            "created_at": g.created_at.isoformat(), "updated_at": g.updated_at.isoformat()}


def _event_dict(e: Event) -> dict[str, Any]:
    return {"id": e.id, "goal_id": e.goal_id, "agent_id": e.agent_id, "kind": e.kind, "message": e.message,
            "data": e.data, "at": e.created_at.isoformat()}
