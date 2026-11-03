"""HTTP API and dashboard (FastAPI). Start it with `python -m agent_company serve`."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from agent_company import auth, loop, registry, research, router, usage
from agent_company.config import settings
from agent_company.db import Agent, Event, Goal, Task, session

STATIC = Path(__file__).parent / "static"
COOKIE = "ac_session"


def bootstrap() -> None:
    registry.seed()
    router.seed()
    with session() as s:  # nothing is running after a restart
        for a in s.scalars(select(Agent).where(Agent.status == "busy")):
            a.status = "idle"


# --- auth dependencies --------------------------------------------------------------

def current_user(request: Request) -> dict[str, Any]:
    u = auth.user_for_token(request.cookies.get(COOKIE))
    if u is None:
        raise HTTPException(401, "sign in required")
    return u


def admin_user(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if not user["is_admin"]:
        raise HTTPException(403, "administrators only")
    return user


def _set_cookie(request: Request, response: Response, token: str) -> None:
    response.set_cookie(COOKIE, token, max_age=auth.SESSION_DAYS * 86400, httponly=True, samesite="lax",
                        secure=request.url.scheme == "https", path="/")


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


class CrawlBody(BaseModel):
    url: str
    depth: int = Field(default=1, ge=0, le=3)
    max_pages: int = Field(default=20, ge=1, le=200)


class SourceBody(CrawlBody):
    pass


class RouteBody(BaseModel):
    model_name: str


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
            app.state.queue = loop.GoalQueue(workers=2)
            app.state.queue.start()
            if settings().rotation_interval > 0:
                app.state.rotation = research.RotationThread(settings().rotation_interval)
                app.state.rotation.start()
        yield
        if app.state.rotation:
            app.state.rotation.stop.set()

    app = FastAPI(title="Agent Company", version="0.2.0", lifespan=lifespan)

    @app.exception_handler(auth.AuthError)
    async def _auth_error(_: Request, e: auth.AuthError) -> JSONResponse:
        return JSONResponse({"code": e.code, "detail": e.message}, status_code=e.status)

    @app.exception_handler(KeyError)
    async def _not_found(_: Request, e: KeyError) -> JSONResponse:
        return JSONResponse({"detail": f"not found: {e}"}, status_code=404)

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
        user, token = auth.register(body.name, body.device_secret, body.device_info)
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
        auth.logout(request.cookies.get(COOKIE))
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
        return auth.admin_action(admin["id"], user_id, action)

    @app.delete("/api/users/{user_id}")
    def user_delete(user_id: int, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        auth.delete_user(admin["id"], user_id)
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
        return {"goals": by_status, "agents": agents, "pending_users": pending, "documents": docs,
                "usage": usage.summary()["totals"]}

    # --- agents ---
    @app.get("/api/rooms")
    def rooms(_: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
        return registry.list_rooms()

    @app.get("/api/agents")
    def agents(_: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
        return registry.list_agents()

    @app.get("/api/agents/{agent_id}")
    def agent(agent_id: int, _: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        return registry.agent_detail(agent_id)

    @app.patch("/api/agents/{agent_id}")
    def agent_update(agent_id: int, changes: dict[str, Any], _: dict[str, Any] = Depends(admin_user)
                     ) -> dict[str, Any]:
        return registry.update_agent(agent_id, changes)

    # --- goals ---
    @app.get("/api/goals")
    def goals(_: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
        with session() as s:
            return [_goal_dict(g) for g in s.scalars(select(Goal).order_by(Goal.id.desc()).limit(200))]

    @app.post("/api/goals")
    def goal_create(body: GoalBody, request: Request, user: dict[str, Any] = Depends(current_user)
                    ) -> dict[str, Any]:
        if (body.repo_path or body.quality_commands) and not user["is_admin"]:
            raise HTTPException(403, "only administrators can set a repository or quality commands")
        gid = loop.create_goal(body.title, body.description, body.repo_path, body.urls,
                               body.quality_commands, body.max_iterations, created_by=user["id"])
        q = request.app.state.queue
        if q is not None:
            q.submit(gid)
        else:
            threading.Thread(target=_run_quietly, args=(gid,), daemon=True).start()
        return {"id": gid}

    @app.get("/api/goals/{goal_id}")
    def goal(goal_id: int, _: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        with session() as s:
            g = s.get(Goal, goal_id)
            if g is None:
                raise KeyError(goal_id)
            d = _goal_dict(g)
            d["tasks"] = [{"id": t.id, "iteration": t.iteration, "key": t.key, "title": t.title, "role": t.role,
                           "agent_id": t.agent_id, "status": t.status, "model": t.model_name,
                           "depends_on": t.depends_on, "output": t.output[-8000:], "gate": t.gate,
                           "contract": t.contract}
                          for t in s.scalars(select(Task).where(Task.goal_id == goal_id).order_by(Task.id))]
            d["events"] = [_event_dict(e) for e in s.scalars(
                select(Event).where(Event.goal_id == goal_id).order_by(Event.id))]
            return d

    @app.post("/api/goals/{goal_id}/approve")
    def goal_approve(goal_id: int, _: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        return loop.approve(goal_id)

    @app.post("/api/goals/{goal_id}/reject")
    def goal_reject(goal_id: int, _: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        loop.reject(goal_id)
        return {"ok": True}

    @app.post("/api/goals/{goal_id}/cancel")
    def goal_cancel(goal_id: int, _: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        loop.cancel(goal_id)
        return {"ok": True}

    @app.get("/api/events")
    def events(after: int = 0, goal_id: int | None = None, _: dict[str, Any] = Depends(current_user)
               ) -> list[dict[str, Any]]:
        with session() as s:
            q = select(Event).where(Event.id > after)
            if goal_id is not None:
                q = q.where(Event.goal_id == goal_id)
            rows = s.scalars(q.order_by(Event.id.desc()).limit(200)).all()
            return [_event_dict(e) for e in reversed(rows)]

    # --- models ---
    @app.get("/api/models")
    def models(_: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        return {"models": router.list_models(), "routes": router.list_routes(),
                "default_model": settings().default_model, "executor": settings().executor}

    @app.post("/api/models")
    def model_add(data: dict[str, Any], _: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        return router.add_model(data)

    @app.patch("/api/models/{model_id}")
    def model_update(model_id: int, data: dict[str, Any], _: dict[str, Any] = Depends(admin_user)
                     ) -> dict[str, Any]:
        return router.update_model(model_id, data)

    @app.delete("/api/models/{model_id}")
    def model_delete(model_id: int, _: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        router.delete_model(model_id)
        return {"ok": True}

    @app.put("/api/routes/{role}")
    def route_set(role: str, body: RouteBody, _: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        router.set_route(role, body.model_name)
        return {"role": role, "model_name": body.model_name}

    @app.get("/api/openrouter/models")
    def openrouter_models(q: str = "", free: bool = False, _: dict[str, Any] = Depends(current_user)
                          ) -> list[dict[str, Any]]:
        try:
            return router.openrouter_catalog(q, free)
        except httpx.HTTPError as e:
            raise HTTPException(502, f"OpenRouter catalog unavailable: {e}") from e

    @app.post("/api/openrouter/models")
    def openrouter_add(body: OpenRouterBody, _: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        return router.add_openrouter_model(body.id, body.name)

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

    @app.get("/api/research/search")
    def search(q: str, limit: int = 10, _: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
        return research.search(q, min(limit, 50))

    @app.get("/api/research/documents")
    def documents(_: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
        with session() as s:
            return [{"id": d.id, "url": d.url, "title": d.title, "chars": len(d.text),
                     "source": d.source_url, "at": d.created_at.isoformat()}
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

    # --- dashboard ---
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    return app


def _run_quietly(goal_id: int) -> None:
    try:
        loop.run_goal(goal_id)
    except Exception:  # noqa: BLE001 - recorded on the goal
        pass


def _goal_dict(g: Goal) -> dict[str, Any]:
    return {"id": g.id, "title": g.title, "description": g.description, "status": g.status,
            "iteration": g.iteration, "max_iterations": g.max_iterations, "repo_path": g.repo_path,
            "branch": g.branch, "base_branch": g.base_branch, "worktree": g.worktree, "urls": g.urls,
            "quality_commands": g.quality_commands, "result": g.result,
            "created_at": g.created_at.isoformat(), "updated_at": g.updated_at.isoformat()}


def _event_dict(e: Event) -> dict[str, Any]:
    return {"id": e.id, "goal_id": e.goal_id, "agent_id": e.agent_id, "kind": e.kind, "message": e.message,
            "data": e.data, "at": e.created_at.isoformat()}
