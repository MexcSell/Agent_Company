"""HTTP API and dashboard (FastAPI). Start it with `python -m agent_company serve`."""

from __future__ import annotations

import asyncio
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile, WebSocket
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from starlette.background import BackgroundTask

from agent_company import (
    a2a,
    agentdefs,
    auth,
    blackboard,
    budgets,
    catalog,
    containers,
    decisions,
    evaluation,
    evolution,
    executors,
    experiments,
    files,
    gitsync,
    governance,
    hypotheses,
    kgraph,
    kpis,
    learning,
    loop,
    machines,
    management,
    mcp,
    mentoring,
    network,
    orchestration,
    organization,
    performance,
    policies,
    prefs,
    projects,
    rca,
    recruitment,
    registry,
    requirements,
    research,
    router,
    selfevolve,
    strategies,
    teams,
    terminal,
    training,
    transport,
    usage,
    workflow,
    workspace,
)
from agent_company.config import settings
from agent_company.db import Agent, Decision, Event, Goal, Task, session

STATIC = Path(__file__).parent / "static"
COOKIE = "ac_session"


def bootstrap() -> None:
    """Seed the catalog and load settings. Safe to call while a server is running."""
    registry.seed()
    router.seed()
    strategies.seed()
    projects.seed()
    catalog.seed()
    kpis.seed()
    prefs.load()


def reset_after_restart() -> None:
    """Only for the server that owns the work: nothing is running right after it starts."""
    with session() as s:
        for a in s.scalars(select(Agent).where(Agent.status == "busy")):
            a.status = "idle"
        for a in s.scalars(select(Agent)):
            if a.active_goals:
                a.active_goals = []
        # a research request cut off by the restart runs again (ReportThread picks queued ones up)
        for r in s.scalars(select(research.ResearchReport).where(research.ResearchReport.status == "running")):
            r.status = "queued"


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


def goal_approver(goal_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    """Administrators, or a user holding a delegation of `approve_goals` for the goal's project right now (S01-R-03)."""
    if user["is_admin"]:
        return user
    with session() as s:
        g = s.get(Goal, goal_id)
        project_id = g.project_id if g else None
    if not policies.allows(user, "approve_goals", project_id):
        raise HTTPException(403, "administrators, or a delegate for this goal's project")
    return {**user, "delegated": True}


def allowed(perm: str, launch: str = ""):  # noqa: ANN201
    """Dependency: administrators, or users granted `perm` (Users page → Permissions). `launch` names an action that
    starts agent work: a refused one is audited (SPEC-02 AT-014). Other refusals are not, the dashboard of a user
    without a permission meets them all the time."""
    def check(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        if not auth.can(user, perm):
            if launch:
                _audit(user, f"{launch}_denied", "", after={"needs": perm}, severity="warning")
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
    permits: list[str] = Field(default_factory=list)  # change boundaries lifted (admins): dependencies, config
    estimated_cost_usd: float = Field(default=0.0, ge=0, le=10_000)  # twice this pauses the goal (S14-R-03)
    kind: str = ""  # S04-R-11: bugfix|feature|refactor|research|docs; "" = the planner picks


class GoalTeamBody(BaseModel):
    action: str = Field(pattern="^(add|release)$")  # S04-R-11 team resizing
    role: str = Field(default="", max_length=64)
    skills: list[str] = Field(default_factory=list, max_length=10)
    agent_id: int | None = None  # release it, or take a released agent back
    reason: str = Field(default="", max_length=500)


class StrategyBody(BaseModel):
    body: dict[str, Any]  # every template field (strategies.FIELDS)
    note: str = Field(default="", max_length=500)


class GoalBudgetBody(BaseModel):
    budget_usd: float | None = None
    budget_tokens: int | None = None
    max_active_agents: int | None = None


class CrawlBody(BaseModel):
    url: str
    depth: int = Field(default=1, ge=0, le=3)
    max_pages: int = Field(default=20, ge=1, le=200)


class SourceBody(CrawlBody):
    interval: str = ""  # hourly|daily|weekly|monthly|manual; "" = the rotation interval (S06-R-02)
    subject: str = Field(default="", max_length=500)
    notify_room: str = ""


class A2AClientBody(BaseModel):
    name: str = Field(min_length=3, max_length=64)
    project_id: int | None = None
    clearance: str = "internal"
    override_gate: bool = False


class ShiftReplaceBody(BaseModel):
    reason: str = Field(min_length=3, max_length=300)
    agent_id: int | None = None  # None = the next agent on the roster


class ResearchBody(BaseModel):
    question: str = Field(min_length=5, max_length=2000)
    urls: list[str] = Field(default_factory=list, max_length=10)
    # ASEAO06 S06-R-01
    project_id: int | None = None
    goal_id: int | None = None
    keywords: list[str] = Field(default_factory=list, max_length=20)
    source_preferences: str = "any"
    priority: str = "normal"
    budget_usd: float = 0.0
    deadline: str | None = None
    recurring: str = ""


class SettingBody(BaseModel):
    value: Any


class DelegationBody(BaseModel):
    user_id: int
    project_id: int
    expires_at: str
    permission: str = "approve_goals"
    note: str = Field("", max_length=500)


class RestoreBody(BaseModel):
    scope: str = "company"
    version: int


class NoticeBody(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    body: str = ""
    level: str = "info"


def _audit(user: dict[str, Any], action: str, target: Any = "", before: dict[str, Any] | None = None,
           after: dict[str, Any] | None = None, severity: str = "info") -> None:
    governance.audit(f"user:{user['name']}", action, str(target), before, after, severity)


class ProposalCaseBody(BaseModel):
    kind: str | None = None
    case: dict[str, str] = Field(default_factory=dict)


class ContractBody(BaseModel):
    title: str
    objective: str
    component: str
    benchmark: str
    layer: str = "code"
    kind: str = "algorithmic"
    proposal: dict[str, str] = {}
    allowed_paths: list[str] = []


class ReasonBody(BaseModel):
    reason: str = ""


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


class MachineBody(BaseModel):
    name: str = Field(..., max_length=30)
    os: str = Field(..., max_length=20)
    url: str = Field("", max_length=500)  # the image of "custom"
    cores: int = Field(2, ge=1, le=16)
    ram: int = Field(4, ge=1, le=64)  # GB
    disk: int = Field(32, ge=8, le=500)  # GB, set when it's made
    username: str = Field("", max_length=20)  # Windows, when it's made: the account its setup makes
    password: str | None = Field(None, max_length=127)  # Windows, when it's made; never stored in the DB or audit
    language: str = Field("en", max_length=5)  # Windows, when it's made


class MachineActBody(BaseModel):
    action: str


class RoomBody(BaseModel):
    key: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    division: str
    description: str = ""
    parent_key: str | None = None
    lead_agent_id: int | None = None


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
    effort: str = Field(default="", max_length=16)  # low … max; empty: the CLI's own setting
    title: str = Field(default="", max_length=200)
    attachments: list[str] = Field(default_factory=list, max_length=10)
    provider: str = "claude"  # claude | codex | copilot
    project_id: int | None = None  # the project's General Manager: runs in its folder with the GM's instructions


class McpServerBody(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str = ""
    transport: str = "stdio"
    command: str = ""
    args: list[str] = Field(default_factory=list)
    url: str = ""
    env: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    everyone: bool = True
    enabled: bool = True


class McpCatalogBody(BaseModel):
    values: dict[str, str] = Field(default_factory=dict)


class McpToolsBody(BaseModel):
    allowed: list[str] = Field(default_factory=list)


class McpGrantsBody(BaseModel):
    agent_ids: list[int] = Field(default_factory=list)
    everyone: bool | None = None


class McpCallBody(BaseModel):
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class CatalogBody(BaseModel):
    kind: str
    name: str = Field(min_length=1, max_length=64)
    description: str = ""
    content: str = ""
    source: str = ""


class DefinitionBody(BaseModel):
    text: str = Field(min_length=10, max_length=200_000)


class EmergencyBody(BaseModel):
    on: bool
    reason: str = ""


class StopBody(BaseModel):
    level: str = Field(pattern=r"^E[0-5]$")
    scope: str = Field(default="all", max_length=64)
    reason: str = Field(default="", max_length=500)


class IncidentBody(BaseModel):
    category: str
    severity: str = "SEV-3"
    title: str = Field(min_length=3, max_length=256)
    goal_id: int | None = None
    agent_id: int | None = None
    project_id: int | None = None
    note: str = Field(default="", max_length=2000)


class DecideBody(BaseModel):
    approve: bool
    note: str = Field(default="", max_length=2000)


class RequirementBody(BaseModel):
    text: str = Field(min_length=3, max_length=8000)
    kind: str = Field(default="functional", pattern="^(functional|non_functional|story|use_case)$")
    acceptance: list[str] = Field(default_factory=list, max_length=20)
    key: str = Field(default="", max_length=32)
    subject: str = Field(default="", max_length=128)
    source: str = Field(default="", max_length=256)
    # S08-FR-03: a story's {actors, want, benefit} or a use case's {actors, preconditions, main_flow, alternatives,
    # postconditions}; parsed from the text when left out
    details: dict[str, Any] | None = None


class RequirementPatch(BaseModel):
    text: str | None = Field(default=None, max_length=8000)
    kind: str | None = Field(default=None, pattern="^(functional|non_functional|story|use_case)$")
    acceptance: list[str] | None = Field(default=None, max_length=20)
    subject: str | None = Field(default=None, max_length=128)
    source: str | None = Field(default=None, max_length=256)
    note: str = Field(default="", max_length=2000)
    details: dict[str, Any] | None = None


class RequirementStatusBody(BaseModel):
    status: str = Field(pattern="^(draft|approved|implemented|verified|retired)$")
    note: str = Field(default="", max_length=2000)


class DecisionReviewBody(BaseModel):
    verdict: str = Field(pattern="^(agree|concerns|disagree)$")
    note: str = Field(default="", max_length=2000)


class SupersedeBody(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    note: str = Field(default="", max_length=1000)


class ResolveBody(BaseModel):
    resolution: str  # learning.RESOLUTIONS
    rationale: str = Field(min_length=1, max_length=2000)


class IncidentUpdateBody(BaseModel):
    status: str | None = None
    note: str = Field(default="", max_length=2000)
    root_cause: str | None = Field(default=None, max_length=4000)
    contain: str = ""  # pause_agent | cancel_goal | stop_project | stop_all


class UiBody(BaseModel):
    value: Any


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


class HypothesisBody(BaseModel):
    statement: str = Field(min_length=5, max_length=2000)
    reason: str = Field(default="", max_length=4000)
    test_method: str = Field(default="", max_length=2000)
    expected_outcome: str = Field(default="", max_length=2000)
    project_id: int | None = None
    goal_id: int | None = None


class HypothesisEvidenceBody(BaseModel):
    side: str  # supporting|contradicting
    type: str = "note"
    id: Any = None
    note: str = Field(default="", max_length=1000)


class HypothesisResultBody(BaseModel):
    actual_outcome: str = Field(min_length=1, max_length=4000)
    passed: bool | None = None
    source: str = ""


class HypothesisStatusBody(BaseModel):
    status: str
    note: str = Field(default="", max_length=2000)


class ExperimentBody(BaseModel):
    target: str  # playbook|route|strategy|retrieval
    candidate: str  # the playbook text, the catalog model for the route, a JSON object of template fields
    # (a strategy experiment, S04-R-13; `role` is then the goal kind), or ranking weights (`name=value` or JSON)
    agent_id: int | None = None
    role: str = ""
    hypothesis: str = ""
    suite: str = "smoke"
    repeats: int = Field(default=1, ge=1, le=10)
    budget_usd: float = Field(default=0.0, ge=0, le=1000)  # what its eval runs may cost; 0 = no limit (S10-R-02)


class KpiBody(BaseModel):
    room: str = Field(min_length=1, max_length=64)
    metric: str = Field(min_length=1, max_length=48)  # kpis.METRICS
    target: float | None = Field(default=None, ge=0)
    name: str = Field(default="", max_length=128)
    definition: str = Field(default="", max_length=2000)
    direction: str = Field(default="", pattern="^(|higher|lower)$")
    interval: str = Field(default="weekly", pattern="^(daily|weekly|monthly)$")


class KpiPatch(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    definition: str | None = Field(default=None, max_length=2000)
    target: float | None = Field(default=None, ge=0)
    clear_target: bool = False
    direction: str | None = Field(default=None, pattern="^(higher|lower)$")
    interval: str | None = Field(default=None, pattern="^(daily|weekly|monthly)$")
    enabled: bool | None = None


class PlanBody(BaseModel):
    agent_id: int
    capability: str
    target_level: int | None = Field(default=None, ge=1, le=5)
    practice_suite: str = ""
    final_suite: str = ""
    repeats: int = Field(default=1, ge=1, le=10)
    pass_mark: float = Field(default=0.8, gt=0, le=1)


class NoteBody(BaseModel):
    note: str = ""


class FeedbackBody(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class StatusBody(BaseModel):
    status: str


class DefectStatusBody(BaseModel):
    status: str
    note: str = ""
    duplicate_of: int | None = None


class DefectPriorityBody(BaseModel):
    priority: int


class LifecycleBody(BaseModel):
    state: str


class RelationshipBody(BaseModel):
    other_id: int
    kind: str
    weight: float | None = Field(default=None, ge=0, le=1)
    status: str | None = None
    reason: str = Field(min_length=1, max_length=500)


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
    topic: str = "general"
    classification: str = ""  # SPEC-14 S14-R-05: raises the order's level above the project's


class TopicBody(BaseModel):
    name: str
    description: str = ""
    agent_ids: list[int] = Field(default_factory=list)


class RecruitDecisionBody(BaseModel):
    grant: bool = True  # also give the shell/network permissions held back for a new role
    reason: str = ""
    status: str = "rejected"  # rejected | cancelled (a person suspends the recruitment)


class RecruitReviseBody(BaseModel):
    changes: dict[str, Any]


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
            app.state.study = agentdefs.StudyThread()
            app.state.study.start()
            app.state.reports = research.ReportThread()
            app.state.reports.start()
            app.state.selfevolve = selfevolve.Monitor()
            app.state.selfevolve.start()
            app.state.kpis = kpis.KpiThread()
            app.state.kpis.start()
            threading.Thread(target=lambda: agentdefs.sync_all("registered"), daemon=True).start()
        yield
        if app.state.rotation:
            app.state.rotation.stop.set()
        if getattr(app.state, "study", None):
            app.state.study.stop.set()
        if getattr(app.state, "reports", None):
            app.state.reports.stop.set()
        if getattr(app.state, "selfevolve", None):
            app.state.selfevolve.stop.set()
        if getattr(app.state, "kpis", None):
            app.state.kpis.stop.set()
        terminal.close_all()
        workspace.stop_all()

    app = FastAPI(title="Agent Company", version="0.2.0", lifespan=lifespan)

    @app.middleware("http")
    async def _scheme_gate(request: Request, call_next):  # noqa: ANN001, ANN202
        """Requests on the other scheme get a hand-off page (pages) or 421 (API), never data."""
        if _wrong_scheme(request) and request.url.path not in transport.EXEMPT:
            target = settings().transport
            if request.url.path.startswith(("/api/", "/.well-known/")):
                return JSONResponse({"code": "wrong_scheme", "detail": f"This server uses {target.upper()} now.",
                                     "scheme": target}, status_code=421)
            return HTMLResponse(transport.handoff_page(target), headers={"Cache-Control": "no-store"})
        return await call_next(request)

    @app.middleware("http")
    async def _headers(request: Request, call_next):  # noqa: ANN001, ANN202
        """Security headers on every answer; versioned static files (?v=…) are cached for a year."""
        response = await call_next(request)
        h = response.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "SAMEORIGIN")
        h.setdefault("Referrer-Policy", "same-origin")
        h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        path = request.url.path
        if path.startswith("/static/"):
            h["Cache-Control"] = ("public, max-age=31536000, immutable" if request.query_params.get("v")
                                  else "public, max-age=604800" if path.startswith("/static/vendor/") else "no-cache")
        elif path.startswith("/api/uploads/") and "cache-control" not in h:
            h["Cache-Control"] = "private, max-age=3600"  # attachments never change: not fetched again on each re-render
        elif path.startswith("/api/files/raw") and "cache-control" not in h:
            h["Cache-Control"] = "private, no-cache"  # previews revalidate (ETag, cheap 304) so edits show at once
        elif path.startswith("/api/") and "cache-control" not in h:
            h["Cache-Control"] = "no-store"
        return response

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
        return {**user, "delegations": [] if user["is_admin"] else policies.list_delegations(user["id"], active_only=True)}

    # --- delegated rights (S01-R-03) ---
    @app.get("/api/delegations")
    def delegations(_: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        return {"items": policies.list_delegations(), "permissions": policies.DELEGABLE, "max_days": policies.MAX_DAYS}

    @app.post("/api/delegations")
    def delegation_create(body: DelegationBody, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        try:
            out = policies.delegate(f"user:{admin['name']}", body.user_id, body.permission, body.project_id,
                                    body.expires_at, body.note)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        _audit(admin, "delegation_grant", out["id"], after={k: out[k] for k in ("permission", "user", "project_id",
                                                                              "expires_at")}, severity="high")
        return out

    @app.post("/api/delegations/{delegation_id}/revoke")
    def delegation_revoke(delegation_id: int, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        try:
            out = policies.revoke(delegation_id, f"user:{admin['name']}")
        except KeyError as e:
            raise HTTPException(404, "unknown delegation") from e
        _audit(admin, "delegation_revoke", delegation_id, after={"user": out["user"], "project_id": out["project_id"]})
        return out

    # --- versioned policies (S01-R-05) ---
    @app.get("/api/policy/versions")
    def policy_versions(scope: str = "company", _: dict[str, Any] = Depends(admin_user)) -> list[dict[str, Any]]:
        try:
            return policies.versions(scope)
        except (KeyError, ValueError) as e:
            raise HTTPException(400, f"unknown scope {scope}") from e

    @app.post("/api/policy/restore")
    def policy_restore(body: RestoreBody, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        try:
            out = policies.restore(body.scope, body.version, f"user:{admin['name']}")
        except KeyError as e:
            raise HTTPException(404, str(e).strip("'\"")) from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        _audit(admin, "policy_restore", body.scope, after={"restored": body.version, "version": out["version"]},
               severity="high")
        return out

    @app.get("/api/projects/{project_id}/policy")
    def project_policy(project_id: int, _: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        try:
            return policies.project_rules(project_id)
        except KeyError as e:
            raise HTTPException(404, "unknown project") from e

    @app.put("/api/projects/{project_id}/policy")
    def project_policy_set(project_id: int, rules: dict[str, Any], admin: dict[str, Any] = Depends(admin_user)
                           ) -> dict[str, Any]:
        """A project's own rules: stricter than the company's or refused."""
        try:
            before = policies.project_rules(project_id)["own"]
            out = policies.set_project_rules(project_id, rules, f"user:{admin['name']}")
        except KeyError as e:
            raise HTTPException(404, "unknown project") from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        _audit(admin, "project_policy", project_id, before=before, after=out["own"])
        return out

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
                "pending_proposals": proposals, "build": static_build(),
                "stopped": (bool(governance.emergency()) or bool(governance.stops())) if user["is_admin"] else False,
                "stop_level": governance.stop_level()}  # every user sees the level (S15-R-01), not its scope

    # --- agents ---
    @app.get("/api/rooms")
    def rooms(_: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
        return registry.list_rooms()

    @app.post("/api/rooms")
    def room_create(body: RoomBody, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        try:
            out = organization.create_room(body.key, body.name, body.division, body.description, body.parent_key,
                                           body.lead_agent_id, actor=f"user:{admin['name']}")
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        _audit(admin, "room_create", out["key"], after=out)
        return out

    @app.patch("/api/rooms/{key}")
    def room_update(key: str, changes: dict[str, Any], admin: dict[str, Any] = Depends(admin_user)
                    ) -> dict[str, Any]:
        """S01-R-01: name, description, division, parent_key, lead_agent_id, enabled; `reason` for the position
        history when the lead changes."""
        reason = str(changes.pop("reason", "") or "")
        try:
            before = organization.get_room(key)
            out = organization.update_room(key, changes, f"user:{admin['name']}", reason)
        except KeyError as e:
            raise HTTPException(404, f"unknown room {key}") from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        field = {"parent_key": "parent"}
        _audit(admin, "room_update", key, before={k: before.get(field.get(k, k)) for k in changes},
               after={k: out.get(field.get(k, k)) for k in changes})
        return out

    @app.get("/api/org/positions")
    def org_positions(position: str = "", limit: int = 200, _: dict[str, Any] = Depends(current_user)
                      ) -> list[dict[str, Any]]:
        return organization.positions(position, limit)

    @app.get("/api/org/export")
    def org_export(format: str = "json", table: str = "agents", admin: dict[str, Any] = Depends(admin_user)
                   ) -> Any:  # noqa: A002
        """S01-R-04: the organisation as JSON, or one table (rooms, agents, relationships) as CSV."""
        if format not in {"json", "csv"}:
            raise HTTPException(400, "format is json or csv")
        _audit(admin, "org_export", format, after={"table": table} if format == "csv" else None)
        if format == "json":
            return organization.export()
        try:
            text = organization.export_csv(table)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return Response(text, media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="organisation-{table}.csv"'})

    @app.get("/api/agents")
    def agents(_: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
        return registry.list_agents()

    @app.get("/api/agents/health")
    def agents_health(_: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        """Degraded agents and agents idle longer than `inactive_days` (SPEC-02 S02-R-02)."""
        return registry.health_overview()

    @app.get("/api/agents/{agent_id}")
    def agent(agent_id: int, _: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        return registry.agent_detail(agent_id) | {"certification": performance.certification(agent_id),
                                                  "autonomy_levels": executors.AUTONOMY}

    def _enable_gate(agent_id: int, admin: dict[str, Any], override: bool) -> None:
        cur = registry.agent_detail(agent_id)
        if not cur["enabled"] and performance.gated(cur["role"]):  # G-R-04: gated roles need a reliable pipeline
            gate = performance.enablement_gate()
            if not gate["met"] and not (override and admin["is_admin"]):
                raise HTTPException(409, "the pipeline is not measured reliable yet: " + "; ".join(gate["reasons"])
                                    + ". An administrator can override (override_gate).")
            if not gate["met"]:
                _audit(admin, "enablement_gate_override", cur["name"], after={"reasons": gate["reasons"]},
                       severity="high")

    @app.patch("/api/agents/{agent_id}")
    def agent_update(agent_id: int, changes: dict[str, Any], admin: dict[str, Any] = Depends(allowed("manage_agents"))
                     ) -> dict[str, Any]:
        override = bool(changes.pop("override_gate", False))
        expected = changes.pop("expected_version", None)
        authority = {"permissions", "autonomy"} & set(changes)
        if authority and not admin["is_admin"]:  # SPEC-14 S14-R-02: an agent's authority is a human administrator's call
            raise HTTPException(403, f"only an administrator changes an agent's {' and '.join(sorted(authority))}")
        if changes.get("enabled"):
            _enable_gate(agent_id, admin, override)
        try:
            out = registry.update_agent(agent_id, changes, admin["name"], expected)
        except registry.StaleVersion as e:  # SPEC-02 AT-023: a stale edit is refused, not silently lost
            raise HTTPException(409, str(e)) from e
        _audit(admin, "agent_update", out["name"], after=changes, severity="high" if authority else "info")
        agentdefs.sync(agent_id, f"settings changed by {admin['name']}: {', '.join(sorted(changes))}")
        return out

    @app.get("/api/agents/{agent_id}/config-versions")
    def agent_config_versions(agent_id: int, _: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
        return registry.config_versions(agent_id)

    @app.post("/api/agents/{agent_id}/rollback")
    def agent_rollback(agent_id: int, version: int, override_gate: bool = False,
                       admin: dict[str, Any] = Depends(allowed("manage_agents"))) -> dict[str, Any]:
        target = next((v for v in registry.config_versions(agent_id) if v["version"] == version), None)
        if target is None:
            raise HTTPException(404, f"agent {agent_id} has no version {version}")
        before = registry.agent_detail(agent_id)
        if not admin["is_admin"] and any(target["snapshot"].get(k, before.get(k)) != before.get(k)
                                         for k in ("permissions", "autonomy")):
            raise HTTPException(403, "this version has other permissions or another autonomy level: only an "
                                "administrator rolls back to it")
        if target["snapshot"].get("enabled"):
            _enable_gate(agent_id, admin, override_gate)
        out = registry.rollback(agent_id, version, admin["name"])
        _audit(admin, "agent_rollback", out["name"], before={k: before.get(k) for k in ("model_pref", "enabled",
                                                                                          "lifecycle", "permissions",
                                                                                          "autonomy")},
               after={"version": version, "now": out["config_version"]}, severity="high")
        agentdefs.sync(agent_id, f"configuration rolled back to v{version} by {admin['name']}")
        return out

    @app.post("/api/agents/{agent_id}/health/reset")
    def agent_health_reset(agent_id: int, admin: dict[str, Any] = Depends(allowed("manage_agents"))
                           ) -> dict[str, Any]:
        out = registry.reset_health(agent_id, admin["name"])
        _audit(admin, "agent_health_reset", out["name"])
        return out

    @app.get("/api/performance/enablement")
    def performance_enablement(_: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        return performance.enablement_gate()

    @app.post("/api/agents")
    def agent_create(body: AgentBody, admin: dict[str, Any] = Depends(allowed("manage_agents"))) -> dict[str, Any]:
        out = registry.create_agent(body.role, body.title, body.skills, body.room, body.superior_id, body.goal,
                                    body.writes, actor=admin["name"])
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
        out = registry.set_lifecycle(agent_id, body.state, admin["name"])
        _audit(admin, "agent_lifecycle", out["name"], after={"state": body.state})
        return out

    # --- A2A (G-R-06): the agent card and JSON-RPC for issued clients ---
    def _base_url(request: Request) -> str:
        proto = request.headers.get("x-forwarded-proto") or request.url.scheme
        return f"{proto}://{request.headers.get('host') or request.url.netloc}"

    @app.get("/.well-known/agent-card.json")
    def a2a_card(request: Request) -> JSONResponse:
        if not settings().a2a_enabled:
            raise HTTPException(404, "A2A is off")
        return JSONResponse(a2a.card(_base_url(request)), headers={"Cache-Control": "public, max-age=300"})

    @app.post("/api/a2a")
    async def a2a_rpc(request: Request) -> JSONResponse:
        if not settings().a2a_enabled:
            raise HTTPException(404, "A2A is off")
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse(a2a.RpcError(a2a.PARSE, "the body is not JSON").body(None))
        version = request.headers.get("a2a-version") or request.query_params.get("A2A-Version")
        out, goal, status = await asyncio.to_thread(a2a.handle, body, request.headers.get("authorization"), version)
        if goal is not None:
            q = request.app.state.queue
            if q is not None:
                q.submit(goal)
            else:
                threading.Thread(target=_run_quietly, args=(goal,), daemon=True, name=f"goal-{goal}").start()
        headers = {"WWW-Authenticate": 'Bearer realm="a2a"'} if status == 401 else None
        return JSONResponse(out, status_code=status, headers=headers)

    @app.get("/api/a2a/clients")
    def a2a_clients(_: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        return {"clients": a2a.list_clients(), "enabled": settings().a2a_enabled,
                "gate": performance.enablement_gate()}

    @app.post("/api/a2a/clients")
    def a2a_client_create(body: A2AClientBody, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        """Issue a client token; opening the company to outside callers needs the measured gate (G-R-04)."""
        gate = performance.enablement_gate()
        if not gate["met"] and not body.override_gate:
            raise HTTPException(409, "the pipeline is not measured reliable yet: " + "; ".join(gate["reasons"])
                                + ". An administrator can override (override_gate).")
        out, token = a2a.create_client(body.name, body.project_id, admin["id"], body.clearance)
        _audit(admin, "a2a_client_create", out["name"], after={"project_id": out["project_id"],
               "clearance": out["clearance"], "gate_met": gate["met"]}, severity="info" if gate["met"] else "high")
        return out | {"token": token}

    @app.delete("/api/a2a/clients/{client_id}")
    def a2a_client_revoke(client_id: int, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        out = a2a.revoke(client_id)
        _audit(admin, "a2a_client_revoke", out["name"])
        return out

    @app.get("/api/network/graph")
    def network_graph(agent_id: int | None = None, room: str = "", skill: str = "", kind: str = "", origin: str = "",
                      include_expired: bool = False, depth: int = 1, offset: int = 0, limit: int = 100,
                      _: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        """One page of the agent network, filtered on the server (SPEC-12 S12-R-07)."""
        return network.graph(agent_id, room, skill, kind, origin, include_expired, depth, offset, limit)

    @app.get("/api/network/overview")
    def network_overview(_: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        return network.overview()

    @app.get("/api/agents/{agent_id}/relationships")
    def agent_relationships(agent_id: int, _: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
        return network.of_agent(agent_id)

    @app.post("/api/agents/{agent_id}/relationships")
    def agent_relationship_correct(agent_id: int, body: RelationshipBody,
                                   admin: dict[str, Any] = Depends(allowed("manage_agents"))) -> dict[str, Any]:
        """An administrator corrects a link's weight or expires it (SPEC-12 S12-R-06)."""
        before, after = network.correct(agent_id, body.other_id, body.kind, admin["name"], body.reason, body.weight,
                                        body.status)
        _audit(admin, "relationship_correct", f"{after['a']} {after['kind']} {after['b']}",
               before={k: before[k] for k in ("weight", "status")} if before else None,
               after={"weight": after["weight"], "status": after["status"], "reason": body.reason})
        return after

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
                        s.scalar(select(func.count()).select_from(Goal).where(Goal.status.in_(workflow.WORKING))) or 0)

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

    # --- Machines: Linux and Windows VMs on the host's KVM (administrators' tools, never agents') ---
    def _machine(call: Any) -> Any:
        try:
            return call()
        except machines.MachineError as e:
            raise HTTPException(e.status, str(e)) from e

    @app.get("/api/machines")
    def machines_list(_: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        return _machine(machines.list_machines)

    @app.post("/api/machines")
    def machine_create(body: MachineBody, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        out = _machine(lambda: machines.save(admin["name"], body.model_dump(), True))
        _audit(admin, "machine_create", body.name, after=body.model_dump(exclude={"password"}), severity="high")
        return out

    @app.get("/api/machines/{name}")
    def machine_view(name: str, _: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        return _machine(lambda: machines.view(name))

    @app.put("/api/machines/{name}")
    def machine_update(name: str, body: MachineBody, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        if body.name != name:
            raise HTTPException(400, "Machines can't be renamed.")
        out = _machine(lambda: machines.save(admin["name"], body.model_dump(), False))
        _audit(admin, "machine_update", name, after=body.model_dump(include={"cores", "ram"}))
        return out

    @app.delete("/api/machines/{name}")
    def machine_delete(name: str, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        out = _machine(lambda: machines.delete(name))
        _audit(admin, "machine_delete", name, after={"disk": "deleted"}, severity="high")
        return out

    @app.post("/api/machines/{name}/action")
    def machine_action(name: str, body: MachineActBody, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        out = _machine(lambda: machines.act(name, body.action))
        _audit(admin, f"machine_{body.action}", name)
        return out

    @app.get("/api/machines/{name}/logs")
    def machine_logs(name: str, tail: int = 300, _: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        return _machine(lambda: machines.logs(name, max(1, min(tail, 5000))))

    @app.get("/api/machines/{name}/console")
    def machine_console_check(name: str, _: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        """Asked before the WebSocket opens, so "still starting" shows as a message, not a dropped socket."""
        _machine(lambda: machines.target(name))
        return {"ok": True}

    @app.websocket("/api/machines/{name}/console")
    async def machine_console(ws: WebSocket, name: str) -> None:
        """The console: noVNC's RFB stream relayed to QEMU's VNC port. Close codes 4400-4599 carry an HTTP status."""
        import asyncio

        from starlette.concurrency import run_in_threadpool

        offered = [p.strip() for p in ws.headers.get("sec-websocket-protocol", "").split(",")]
        await ws.accept(subprotocol="binary" if "binary" in offered else None)

        async def refuse(status: int, reason: str) -> None:
            await ws.close(code=4000 + status if 400 <= status < 600 else 4500, reason=str(reason)[:120])

        user = ws_admin(ws)  # same-origin page and an administrator's session
        if user is None:
            return await refuse(403, "Administrators only, from this site.")
        try:
            addr = await run_in_threadpool(machines.target, name)
        except machines.MachineError as e:
            return await refuse(e.status, str(e))
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(addr, machines.VNC), 10)
        except (OSError, asyncio.TimeoutError) as e:
            return await refuse(502, f"Can't reach the machine's screen ({e or 'timed out'}).")
        await run_in_threadpool(_audit, user, "machine_console", name, None, None, "high")

        async def up() -> None:  # browser -> QEMU
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    return
                writer.write(msg.get("bytes") or (msg.get("text") or "").encode())
                await writer.drain()

        async def down() -> None:  # QEMU -> browser
            while data := await reader.read(65536):
                await ws.send_bytes(data)

        async def still_admin() -> None:  # signing out (or losing admin) ends the console too
            while True:
                await asyncio.sleep(30)
                if await run_in_threadpool(ws_admin, ws) is None:
                    return

        tasks = [asyncio.create_task(f()) for f in (up, down, still_admin)]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tasks:
                t.cancel()
            writer.close()
            try:
                await ws.close()
            except Exception:  # noqa: BLE001 - the browser went first (RuntimeError or ClientDisconnected)
                pass

    # --- file manager (administrators) ---
    @app.get("/api/files/roots")
    def file_roots(_: dict[str, Any] = Depends(admin_user)) -> list[dict[str, str]]:
        return files.roots()

    @app.get("/api/files/list")
    def file_list(path: str, _: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        return files.listing(path)

    @app.get("/api/files/git-status")
    def files_git_status(path: str, _: dict[str, Any] = Depends(admin_user)) -> dict[str, str]:
        return files.git_status(path)

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
        workspace.warm_usage()
        return workspace.available()

    # /status: tokens and cost today and this week, every Claude Code session on the host, and the plan's limits.
    @app.get("/api/workspace/claude-usage")
    def claude_usage(_: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        return workspace.claude_usage() | {"plan": workspace.claude_limits()}

    # /agents and /tasks: what runs in every live Claude Code session on the host.
    @app.get("/api/workspace/claude-activity")
    def claude_activity(cwd: str = "", _: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        return workspace.host_activity(cwd)

    @app.post("/api/workspace/claude")
    def claude_start(body: ClaudeBody, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        images = projects.uploads(body.attachments) if body.attachments else []
        cwd, system = body.cwd, ""
        if body.project_id is not None:  # the project's General Manager (gm.py), in the project's folder
            from agent_company import gm

            p = projects.project_dict(projects.get(body.project_id, admin))
            cwd, system = p["path"], gm.system_prompt(p)
        try:
            run = workspace.start(body.prompt, cwd, admin["name"], body.session_id, body.model, images,
                                  body.title, body.provider, system, body.effort)
        except RuntimeError as e:
            raise HTTPException(503, str(e)) from e
        _audit(admin, "workspace_claude", run.cwd, after={"run": run.id, "prompt": body.prompt[:200],
                                                         "provider": body.provider, "project": body.project_id,
                                                         "resume": body.session_id or None}, severity="high")
        return run.to_dict()

    # Every Claude Code session on the host (VS Code, terminal, SDK, this panel): viewed, stopped on request.
    @app.get("/api/workspace/claude-sessions")
    def claude_sessions(_: dict[str, Any] = Depends(admin_user)) -> list[dict[str, Any]]:
        return workspace.sessions()

    @app.get("/api/workspace/claude-sessions/{session_id}")
    def claude_session(session_id: str, after: int = 0, _: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        return workspace.session_status(session_id, max(0, after))

    @app.post("/api/workspace/claude-sessions/{session_id}/stop")
    def claude_session_stop(session_id: str, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        out = workspace.stop_session(session_id)
        _audit(admin, "workspace_claude_session_stop", session_id, after=out, severity="high")
        return out

    # History: every session with a transcript, running or ended.
    @app.get("/api/workspace/claude-history")
    def claude_history(q: str = "", limit: int = 200, _: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        return workspace.history(q[:200], max(1, min(limit, 1000)))

    @app.delete("/api/workspace/claude-history/{session_id}")
    def claude_history_delete(session_id: str, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        out = workspace.delete_session(session_id)
        _audit(admin, "workspace_claude_session_delete", session_id, after={"files": len(out["deleted"])},
               severity="high")
        return out

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
    def defect_status(defect_id: int, body: DefectStatusBody, admin: dict[str, Any] = Depends(admin_user)
                      ) -> dict[str, Any]:
        out = performance.set_defect_status(defect_id, body.status, body.note, body.duplicate_of,
                                            actor=admin["name"])
        _audit(admin, "defect_status", defect_id, after={"status": body.status, "note": body.note[:500],
                                                         "duplicate_of": body.duplicate_of})
        return out

    @app.post("/api/defects/{defect_id}/priority")
    def defect_priority(defect_id: int, body: DefectPriorityBody, admin: dict[str, Any] = Depends(admin_user)
                        ) -> dict[str, Any]:
        out = performance.set_defect_priority(defect_id, body.priority, actor=admin["name"])
        _audit(admin, "defect_priority", defect_id, after={"priority": body.priority})
        return out

    @app.post("/api/defects/{defect_id}/retest")
    def defect_retest(defect_id: int, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        out = performance.retest(defect_id, actor=admin["name"])
        _audit(admin, "defect_retest", defect_id, after={"passed": out["verification"].get("passed"),
                                                         "status": out["status"]})
        return out

    @app.get("/api/certifications")
    def certifications(_: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
        return performance.certifications()

    @app.get("/api/performance")
    def performance_report(_: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        return performance.report()

    # --- department KPIs (SPEC-01 FR-08, S01-R-06) ---
    @app.get("/api/kpis")
    def kpis_list(room: str = "", all: bool = False, _: dict[str, Any] = Depends(current_user)  # noqa: A002
                  ) -> dict[str, Any]:
        return {"kpis": kpis.list_kpis(room, include_disabled=all),
                "metrics": [{"key": k, "label": m[0], "formula": m[1], "data_source": m[2], "unit": m[3],
                             "direction": m[4]} for k, m in kpis.METRICS.items()],
                "intervals": list(kpis.INTERVALS)}

    @app.get("/api/kpis/{kpi_id}")
    def kpi_detail(kpi_id: int, _: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        return kpis.get(kpi_id) | {"comparison": kpis.compare(kpi_id)}

    @app.post("/api/kpis")
    def kpi_create(body: KpiBody, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        out = kpis.create(body.room, body.metric, body.target, name=body.name, definition=body.definition,
                          direction=body.direction, interval=body.interval, actor=f"user:{admin['name']}")
        _audit(admin, "kpi_create", out["id"], after={"room": out["room"], "metric": out["metric"],
                                                      "target": out["target"], "interval": out["interval"]})
        return out

    @app.patch("/api/kpis/{kpi_id}")
    def kpi_update(kpi_id: int, body: KpiPatch, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        before = kpis.get(kpi_id, history=0)
        out = kpis.update(kpi_id, f"user:{admin['name']}", **body.model_dump(exclude_none=True))
        keys = ("name", "target", "direction", "interval", "enabled")
        _audit(admin, "kpi_update", kpi_id, before={k: before[k] for k in keys}, after={k: out[k] for k in keys})
        return out

    @app.post("/api/kpis/{kpi_id}/measure")
    def kpi_measure(kpi_id: int, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        out = kpis.measure(kpi_id, f"user:{admin['name']}")
        _audit(admin, "kpi_measure", kpi_id, after={"value": out["value"], "met": out["met"]})
        return out

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
        return evolution.overview() | {"duplicates": registry.duplicates()}

    @app.post("/api/evolution/cycle")
    def evolution_cycle(admin: dict[str, Any] = Depends(allowed("manage_agents"))) -> dict[str, Any]:
        return {"experiments_started": evolution.cycle()}

    # --- hypotheses (ASEAO07 S07-R-03): people propose and conclude them; experiments test theirs ---
    @app.get("/api/hypotheses")
    def hypotheses_list(status: str = "", experiment_id: int | None = None, goal_id: int | None = None,
                        project_id: int | None = None, user: dict[str, Any] = Depends(current_user)
                        ) -> list[dict[str, Any]]:
        with session() as s:
            seen = _visible_project_ids(s, user)
        rows = hypotheses.list_hypotheses(status, experiment_id, goal_id, project_id)
        return [h for h in rows if seen is None or h["project_id"] is None or h["project_id"] in seen]

    @app.post("/api/hypotheses")
    def hypothesis_create(body: HypothesisBody, user: dict[str, Any] = Depends(allowed("run"))) -> dict[str, Any]:
        pid = body.project_id
        with session() as s:
            seen = _visible_project_ids(s, user)
            g = s.get(Goal, body.goal_id) if body.goal_id is not None else None
            if body.goal_id is not None and g is None:
                raise HTTPException(400, f"no goal {body.goal_id}")
            pid = pid if pid is not None else (g.project_id if g else None)
        if seen is not None and pid is not None and pid not in seen:
            raise HTTPException(404, "no such project")
        try:
            h = hypotheses.create(body.statement, body.reason, actor=f"user:{user['name']}",
                                  test_method=body.test_method, expected_outcome=body.expected_outcome,
                                  project_id=pid, goal_id=body.goal_id)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        _audit(user, "hypothesis_create", h["id"], after={"statement": h["statement"]})
        return h

    @app.post("/api/hypotheses/{hypothesis_id}/evidence")
    def hypothesis_evidence(hypothesis_id: int, body: HypothesisEvidenceBody,
                            user: dict[str, Any] = Depends(allowed("run"))) -> dict[str, Any]:
        try:
            h = hypotheses.add_evidence(hypothesis_id, body.side, {"type": body.type, "id": body.id, "note": body.note},
                                        f"user:{user['name']}")
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        _audit(user, "hypothesis_evidence", hypothesis_id, after=body.model_dump())
        return h

    @app.post("/api/hypotheses/{hypothesis_id}/result")
    def hypothesis_result(hypothesis_id: int, body: HypothesisResultBody,
                          admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        actor = f"user:{admin['name']}"
        try:
            h = hypotheses.record_result(hypothesis_id, body.actual_outcome,
                                         {"source": body.source or actor, "passed": body.passed}, actor)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        _audit(admin, "hypothesis_result", hypothesis_id, after=body.model_dump())
        return h

    @app.post("/api/hypotheses/{hypothesis_id}/status")
    def hypothesis_status(hypothesis_id: int, body: HypothesisStatusBody,
                          admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        try:
            before = hypotheses.get(hypothesis_id)["status"]
            h = hypotheses.set_status(hypothesis_id, body.status, f"user:{admin['name']}", body.note)
        except (ValueError, PermissionError) as e:
            raise HTTPException(400, str(e)) from e
        _audit(admin, "hypothesis_status", hypothesis_id, before={"status": before},
               after={"status": h["status"], "note": body.note})
        return h

    # --- experiments, capability gaps, learning plans (SPEC-13) ---
    @app.get("/api/experiments")
    def experiments_list(status: str = "", target: str = "", _: dict[str, Any] = Depends(current_user)
                         ) -> dict[str, Any]:
        return {"experiments": experiments.list_experiments(status, target),
                "autonomy": [{"level": k, "what": v[0], "control": v[1]} for k, v in experiments.AUTONOMY.items()]}

    @app.get("/api/experiments/{exp_id}")
    def experiment_get(exp_id: int, _: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        return experiments.get(exp_id)

    @app.post("/api/experiments")
    def experiment_propose(body: ExperimentBody, admin: dict[str, Any] = Depends(allowed("manage_agents"))
                           ) -> dict[str, Any]:
        if body.target == "route" and not auth.can(admin, "manage_models"):
            raise PermissionError("route experiments need the manage_models permission")
        if body.target == "strategy" and not admin["is_admin"]:  # templates are saved by administrators only
            raise PermissionError("workflow template experiments are for administrators")
        if body.target == "retrieval" and not admin["is_admin"]:  # ranking weights are administrator settings
            raise PermissionError("ranking-weight experiments are for administrators")
        out = experiments.propose(body.target, body.candidate, agent_id=body.agent_id, role=body.role,
                                  hypothesis=body.hypothesis, suite=body.suite, repeats=body.repeats,
                                  created_by=f"user:{admin['name']}", budget_usd=body.budget_usd)
        _audit(admin, "experiment_propose", out["id"], after={"target": out["target"], "role": out["role"],
                                                             "agent_id": out["agent_id"]})
        return out

    @app.post("/api/experiments/{exp_id}/start")
    def experiment_start(exp_id: int, admin: dict[str, Any] = Depends(allowed("manage_agents"))) -> dict[str, Any]:
        out = experiments.start(exp_id)
        _audit(admin, "experiment_start", exp_id)
        return out

    @app.post("/api/experiments/{exp_id}/apply")
    def experiment_apply(exp_id: int, admin: dict[str, Any] = Depends(allowed("manage_models"))) -> dict[str, Any]:
        before = experiments.get(exp_id)
        if before["target"] == "strategy":
            if not admin["is_admin"]:
                raise PermissionError("a workflow template is applied by an administrator")
            out = experiments.apply(exp_id, f"user:{admin['name']}")
            _audit(admin, "experiment_apply", exp_id, before={"strategy": before["baseline_version"]},
                   after={"strategy": out["decision"], "changes": out["candidate"].get("changes")}, severity="high")
            return out
        if before["target"] == "retrieval":
            if not admin["is_admin"]:
                raise PermissionError("ranking weights are applied by an administrator")
            out = experiments.apply(exp_id, f"user:{admin['name']}")
            _audit(admin, "experiment_apply", exp_id, before={"weights": before["candidate"].get("base")},
                   after={"weights": out["candidate"].get("weights")}, severity="high")
            return out
        out = experiments.apply(exp_id, f"user:{admin['name']}")
        _audit(admin, "experiment_apply", exp_id, before={"route": before["baseline_version"]},
               after={"route": out["candidate"].get("model")}, severity="high")
        return out

    @app.post("/api/experiments/{exp_id}/rollback")
    def experiment_rollback(exp_id: int, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        before = experiments.get(exp_id)
        out = experiments.rollback(exp_id, f"user:{admin['name']}")
        _audit(admin, "experiment_rollback", exp_id, before={"weights": before["candidate"].get("weights")},
               after={"previous": ((out["results"] or {}).get("deployed") or {}).get("previous")}, severity="high")
        return out

    @app.post("/api/experiments/{exp_id}/reject")
    def experiment_reject(exp_id: int, body: NoteBody, admin: dict[str, Any] = Depends(allowed("manage_agents"))
                          ) -> dict[str, Any]:
        out = experiments.reject(exp_id, f"user:{admin['name']}", body.note)
        _audit(admin, "experiment_reject", exp_id, after={"note": body.note})
        return out

    @app.get("/api/learning/gaps")
    def learning_gaps(agent_id: int | None = None, _: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
        return training.gaps(agent_id)

    @app.get("/api/learning/metrics")
    def learning_metrics(_: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        return training.metrics()

    @app.get("/api/learning/plans")
    def learning_plans(agent_id: int | None = None, status: str = "", _: dict[str, Any] = Depends(current_user)
                       ) -> list[dict[str, Any]]:
        return training.list_plans(agent_id, status)

    @app.post("/api/learning/plans")
    def learning_plan_create(body: PlanBody, admin: dict[str, Any] = Depends(allowed("manage_agents"))
                             ) -> dict[str, Any]:
        out = training.create_plan(body.agent_id, body.capability, body.target_level, body.practice_suite,
                                   body.final_suite, repeats=body.repeats, pass_mark=body.pass_mark,
                                   created_by=f"user:{admin['name']}")
        _audit(admin, "learning_plan_create", out["id"], after={"agent_id": body.agent_id,
                                                               "capability": out["capability"]})
        return out

    @app.post("/api/learning/plans/{plan_id}/start")
    def learning_plan_start(plan_id: int, admin: dict[str, Any] = Depends(allowed("manage_agents"))
                            ) -> dict[str, Any]:
        out = training.start_plan(plan_id)
        _audit(admin, "learning_plan_start", plan_id)
        return out

    @app.post("/api/learning/plans/{plan_id}/cancel")
    def learning_plan_cancel(plan_id: int, admin: dict[str, Any] = Depends(allowed("manage_agents"))
                             ) -> dict[str, Any]:
        out = training.cancel_plan(plan_id)
        _audit(admin, "learning_plan_cancel", plan_id)
        return out

    @app.get("/api/mentoring")
    def mentoring_list(agent_id: int | None = None, status: str = "", _: dict[str, Any] = Depends(current_user)
                       ) -> list[dict[str, Any]]:
        return mentoring.list_mentorings(agent_id, status)

    @app.post("/api/mentoring/{mentoring_id}/feedback")
    def mentoring_feedback(mentoring_id: int, body: FeedbackBody,
                           admin: dict[str, Any] = Depends(allowed("manage_agents"))) -> dict[str, Any]:
        """Advice for the learner from a person; never a grade (certification is the final evaluation's)."""
        out = mentoring.add_feedback(mentoring_id, f"user:{admin['name']}", body.text)
        _audit(admin, "mentoring_feedback", mentoring_id, after={"chars": len(body.text)})
        return out

    # --- verified self-evolution of the platform's code (selfevolve.py) ---
    def _contract_call(fn: Any, *args: Any) -> dict[str, Any]:
        try:
            return fn(*args)
        except KeyError as e:
            raise HTTPException(404, str(e).strip("'")) from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.get("/api/improvement")
    def improvement_overview(_: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        return selfevolve.overview()

    @app.get("/api/improvement/contracts/{cid}")
    def improvement_contract(cid: int, _: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        return _contract_call(selfevolve.get, cid)

    @app.post("/api/improvement/contracts")
    def improvement_create(body: ContractBody, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        out = _contract_call(lambda: selfevolve.create(
            body.title, body.objective, body.component, body.benchmark, body.layer, body.kind, body.proposal,
            body.allowed_paths or None, created_by=admin["id"]))
        _audit(admin, "improvement_create", out["id"], after={"title": out["title"], "component": out["component"],
                                                               "benchmark": out["benchmark"].get("name"),
                                                               "allowed_paths": out["allowed_paths"]})
        return out

    @app.post("/api/improvement/contracts/{cid}/start")
    def improvement_start(cid: int, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        out = _contract_call(selfevolve.start, cid)
        _audit(admin, "improvement_start", cid, after={"status": out["status"]})
        return out

    @app.post("/api/improvement/contracts/{cid}/deploy")
    def improvement_deploy(cid: int, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        out = _contract_call(selfevolve.deploy, cid, admin["name"])
        _audit(admin, "improvement_deploy", cid, after={"merge_commit": out["merge_commit"]}, severity="high")
        return out

    @app.post("/api/improvement/contracts/{cid}/rollback")
    def improvement_rollback(cid: int, body: ReasonBody, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        out = _contract_call(selfevolve.rollback, cid, admin["name"], body.reason)
        _audit(admin, "improvement_rollback", cid, after={"revert": out["rollback_sha"], "reason": body.reason},
               severity="high")
        return out

    @app.post("/api/improvement/contracts/{cid}/cancel")
    def improvement_cancel(cid: int, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        out = _contract_call(selfevolve.cancel, cid, admin["name"])
        _audit(admin, "improvement_cancel", cid)
        return out

    # --- goals ---
    @app.get("/api/goals")
    def goals(user: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
        with session() as s:
            seen = _visible_project_ids(s, user)
            return [_goal_dict(g) for g in s.scalars(select(Goal).order_by(Goal.id.desc()).limit(200))
                    if seen is None or g.project_id is None or g.project_id in seen]

    @app.post("/api/goals")
    def goal_create(body: GoalBody, request: Request, user: dict[str, Any] = Depends(allowed("run", "goal_create"))
                    ) -> dict[str, Any]:
        if (body.repo_path or body.quality_commands or body.permits) and not user["is_admin"]:
            _audit(user, "goal_create_denied", "", after={"needs": "administrator"}, severity="warning")
            raise HTTPException(403, "only administrators can set a repository, quality commands or permits")
        try:
            gid = loop.create_goal(body.title, body.description, body.repo_path, body.urls,
                                   body.quality_commands, body.max_iterations, created_by=user["id"],
                                   allowed_paths=body.allowed_paths, protect_tests=body.protect_tests,
                                   extract_checks=user["is_admin"], permits=body.permits,
                                   estimated_cost_usd=body.estimated_cost_usd, kind=body.kind)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        if body.repo_path or body.quality_commands or body.permits:
            _audit(user, "goal_create_privileged", gid,
                   after={"repo_path": body.repo_path, "quality_commands": body.quality_commands,
                          "permits": body.permits})
        q = request.app.state.queue
        if q is not None:
            q.submit(gid)
        else:
            threading.Thread(target=_run_quietly, args=(gid,), daemon=True, name=f"goal-{gid}").start()
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
                           "stage": workflow.task_stage(t), "stage_log": t.stage_log or [],
                           "depends_on": t.depends_on, "output": t.output[-8000:], "gate": t.gate,
                           "contract": t.contract}
                          for t in s.scalars(select(Task).where(Task.goal_id == goal_id).order_by(Task.id))]
            d["events"] = [_event_dict(e) for e in s.scalars(
                select(Event).where(Event.goal_id == goal_id).order_by(Event.id))]
        d["defects"] = performance.list_defects(goal_id=goal_id)
        d["transitions"] = workflow.history(goal_id)
        d["decisions"] = decisions.list_decisions(goal_id=goal_id)
        d["blackboard"] = blackboard.read(goal_id)
        d["budget"] = budgets.summary(goal_id)
        d["team"] = teams.history(goal_id)
        d["time"] = management.goal_time(goal_id)  # S05-R-05
        d["rca"] = rca.list_analyses(goal_id=goal_id)  # S07-R-02
        d["digest"] = loop.digest(goal_id)  # G-R-09
        return d

    # --- knowledge items (SPEC-11): lessons with provenance; people review, agents never do ---
    @app.get("/api/knowledge")
    def knowledge_list(status: str = "", agent_id: int | None = None, q: str = "", kind: str = "",
                       limit: int = 200, _: dict[str, Any] = Depends(admin_user)) -> list[dict[str, Any]]:
        return learning.knowledge(status, agent_id, max(1, min(limit, 1000)), q, kind)

    # --- SE-R-21: the ranking procedure per query class, learned offline and guarded by recall@5 ---
    @app.get("/api/knowledge/retrieval")
    def knowledge_retrieval(_: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        guard = learning.evaluate_retrieval()
        return {**learning.strategy_report(), "strategies": learning.STRATEGIES,
                "guard": {"metric": guard["metric"], "value": guard["value"], "cases": len(guard["cases"])}}

    @app.post("/api/knowledge/retrieval/learn")
    def knowledge_retrieval_learn(admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        before = learning.strategy_map()
        out = learning.learn_strategies(f"user:{admin['name']}")
        _audit(admin, "retrieval_strategies_learn", "retrieval", before=before, after=out["applied"])
        return out

    @app.get("/api/knowledge/contradictions")
    def knowledge_contradictions(state: str = "open", _: dict[str, Any] = Depends(admin_user)
                                 ) -> list[dict[str, Any]]:
        return learning.contradictions(state)

    @app.post("/api/knowledge/contradictions/{edge_id}/resolve")
    def knowledge_resolve(edge_id: int, body: ResolveBody, admin: dict[str, Any] = Depends(admin_user)
                          ) -> dict[str, Any]:
        out = learning.resolve(edge_id, body.resolution, f"user:{admin['name']}", body.rationale)
        _audit(admin, "knowledge_resolve", f"contradiction {edge_id}", after={"resolution": body.resolution})
        return out

    @app.get("/api/knowledge/graph")
    def knowledge_graph(entity: str, depth: int = 2, offset: int = 0, limit: int = 100, rel: str = "",
                        _: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        """Edges within `depth` steps of `type:id`, paged (SPEC-11 S11-R-07, KT-019)."""
        return kgraph.neighbourhood(entity, depth, max(0, offset), max(1, min(limit, 500)), rel)

    @app.get("/api/knowledge/{item_id}")
    def knowledge_item(item_id: int, _: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        return learning.item(item_id)

    @app.post("/api/knowledge/{item_id}/supersede")
    def knowledge_supersede(item_id: int, body: SupersedeBody, admin: dict[str, Any] = Depends(admin_user)
                            ) -> dict[str, Any]:
        out = learning.supersede(item_id, body.text, f"user:{admin['name']}", body.note)
        _audit(admin, "knowledge_supersede", item_id, after={"new": out["id"], "status": out["status"]})
        return out

    @app.delete("/api/knowledge/{item_id}")
    def knowledge_delete(item_id: int, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        out = learning.delete(item_id)
        _audit(admin, "knowledge_delete", item_id, after=out, severity="high")
        return out

    @app.post("/api/knowledge/{item_id}/review")
    def knowledge_review(item_id: int, body: DecideBody, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        out = learning.review(item_id, body.approve, f"user:{admin['name']}", body.note)
        _audit(admin, "knowledge_review", item_id, after={"status": out["status"]})
        return out

    @app.get("/api/decisions")
    def decisions_list(goal_id: int | None = None, status: str = "", user: dict[str, Any] = Depends(current_user)
                       ) -> list[dict[str, Any]]:
        with session() as s:
            seen = _visible_project_ids(s, user)
        return [d for d in decisions.list_decisions(goal_id, status)
                if seen is None or d["project_id"] is None or d["project_id"] in seen]

    # --- recruitment (SPEC-03): records, approval, probation, capability gaps ---
    def _recruit_call(fn: Any, *args: Any, **kw: Any) -> dict[str, Any]:
        try:
            return fn(*args, **kw)
        except KeyError as e:
            raise HTTPException(404, f"recruitment {args[0] if args else ''}") from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    def _recruit_admin(user: dict[str, Any], action: str, recruitment_id: int) -> None:
        """Only administrators decide recruitments; a refused attempt is audited (SPEC-03 AT-011)."""
        if not user["is_admin"]:
            _audit(user, f"{action}_denied", recruitment_id, severity="warning")
            raise HTTPException(403, "administrators only")

    @app.get("/api/recruitments")
    def recruitments(status: str = "", goal_id: int | None = None, _: dict[str, Any] = Depends(current_user)
                     ) -> list[dict[str, Any]]:
        return recruitment.list_recruitments(status, goal_id)

    @app.get("/api/recruitments/{recruitment_id}")
    def recruitment_detail(recruitment_id: int, _: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        return _recruit_call(recruitment.get, recruitment_id)

    @app.post("/api/recruitments/{recruitment_id}/approve")
    def recruitment_approve(recruitment_id: int, body: RecruitDecisionBody,
                            user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        _recruit_admin(user, "recruitment_approve", recruitment_id)
        out = _recruit_call(recruitment.approve, recruitment_id, f"user:{user['name']}", grant=body.grant)
        _audit(user, "recruitment_approve", recruitment_id,
               after={"agent_id": out["agent_id"], "grant": body.grant, "resumed": out["resumed"]})
        return out

    @app.post("/api/recruitments/{recruitment_id}/reject")
    def recruitment_reject(recruitment_id: int, body: RecruitDecisionBody,
                           user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        _recruit_admin(user, "recruitment_reject", recruitment_id)
        out = _recruit_call(recruitment.reject, recruitment_id, f"user:{user['name']}", body.reason, body.status)
        _audit(user, "recruitment_reject", recruitment_id, after={"status": out["status"], "reason": body.reason})
        return out

    @app.post("/api/recruitments/{recruitment_id}/revise")
    def recruitment_revise(recruitment_id: int, body: RecruitReviseBody,
                           user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        _recruit_admin(user, "recruitment_revise", recruitment_id)
        out = _recruit_call(recruitment.revise, recruitment_id, body.changes, f"user:{user['name']}")
        _audit(user, "recruitment_revise", recruitment_id, after={"version": out["jd_version"]})
        return out

    @app.get("/api/recruitment/effectiveness")
    def recruitment_effectiveness(_: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        return recruitment.effectiveness()

    @app.get("/api/capability_gap")
    def capability_gap(skills: str, _: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        wanted = [registry._slug(x) for x in skills.split(",") if x.strip()]
        return recruitment.capability_gap({k: 1.0 for k in wanted})

    @app.get("/api/projects/{project_id}/capability_gap")
    def project_capability_gap(project_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        projects.get(project_id, user)
        return recruitment.project_gap(project_id)

    # --- requirements and traceability (ASEAO08 S08-R-01) ---
    def _requirement_seen(requirement_id: int, user: dict[str, Any]) -> dict[str, Any]:
        try:
            r = requirements.get(requirement_id, links=True)
        except KeyError as e:
            raise HTTPException(404, f"requirement {requirement_id}") from e
        projects.get(r["project_id"], user)
        return r

    @app.get("/api/projects/{project_id}/requirements")
    def requirements_list(project_id: int, status: str = "", user: dict[str, Any] = Depends(current_user)
                          ) -> list[dict[str, Any]]:
        projects.get(project_id, user)
        return requirements.list_requirements(project_id, status)

    @app.post("/api/projects/{project_id}/requirements")
    def requirement_create(project_id: int, body: RequirementBody, user: dict[str, Any] = Depends(allowed("run"))
                           ) -> dict[str, Any]:
        projects.get(project_id, user)
        try:
            out = requirements.create(project_id, body.text, body.kind, body.acceptance, key=body.key,
                                      subject=body.subject, source=body.source or f"user:{user['name']}",
                                      actor=f"user:{user['name']}", details=body.details)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        _audit(user, "requirement_create", out["id"], after={"project": project_id, "key": out["key"]})
        return out

    @app.get("/api/projects/{project_id}/traceability")
    def requirements_trace(project_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        projects.get(project_id, user)
        return requirements.traceability(project_id)

    @app.post("/api/projects/{project_id}/requirements/import")
    def requirements_import(project_id: int, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        try:
            out = requirements.import_docs(project_id, f"user:{admin['name']}")
        except KeyError as e:
            raise HTTPException(404, str(e).strip("'")) from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        _audit(admin, "requirements_import", project_id, after=out)
        return out

    @app.get("/api/requirements/{requirement_id}")
    def requirement_detail(requirement_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        return _requirement_seen(requirement_id, user)

    @app.patch("/api/requirements/{requirement_id}")
    def requirement_update(requirement_id: int, body: RequirementPatch, user: dict[str, Any] = Depends(allowed("run"))
                           ) -> dict[str, Any]:
        before = _requirement_seen(requirement_id, user)
        try:
            out = requirements.update(requirement_id, f"user:{user['name']}", body.text, body.kind, body.acceptance,
                                      body.subject, body.source, body.note, details=body.details)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        _audit(user, "requirement_update", requirement_id, before={"version": before["version"],
                                                                   "status": before["status"]},
               after={"version": out["version"], "status": out["status"]})
        return out

    @app.post("/api/requirements/{requirement_id}/status")
    def requirement_status(requirement_id: int, body: RequirementStatusBody,
                           admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        before = _requirement_seen(requirement_id, admin)["status"]
        try:
            out = requirements.set_status(requirement_id, body.status, f"user:{admin['name']}", body.note)
        except (ValueError, PermissionError) as e:
            raise HTTPException(400, str(e)) from e
        _audit(admin, "requirement_status", requirement_id, before={"status": before}, after={"status": out["status"]})
        return out

    def _decision_seen(decision_id: int, user: dict[str, Any]) -> None:
        with session() as s:
            seen = _visible_project_ids(s, user)
            d = s.get(Decision, decision_id)
            if d is None or (seen is not None and d.project_id is not None and d.project_id not in seen):
                raise HTTPException(404, f"decision {decision_id}")

    @app.get("/api/decisions/{decision_id}")
    def decision_report(decision_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        """The decision report (ASEAO07 S07-R-01): evidence by class, limitations, reviews, missing parts."""
        _decision_seen(decision_id, user)
        return decisions.report(decision_id)

    @app.post("/api/decisions/{decision_id}/review")
    def decision_review(decision_id: int, body: DecisionReviewBody, user: dict[str, Any] = Depends(allowed("run"))
                        ) -> dict[str, Any]:
        """An independent review (S07-FR-09); disagreeing sends an automatically approved decision to a person."""
        _decision_seen(decision_id, user)
        try:
            out = decisions.review(decision_id, f"user:{user['name']}", body.verdict, body.note)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        _audit(user, "decision_review", decision_id, after={"verdict": body.verdict, "status": out["status"]},
               severity="high" if body.verdict == "disagree" else "info")
        return out

    @app.post("/api/decisions/{decision_id}/decide")
    def decision_decide(decision_id: int, body: DecideBody, admin: dict[str, Any] = Depends(admin_user)
                        ) -> dict[str, Any]:
        try:
            out = decisions.decide(decision_id, body.approve, f"user:{admin['name']}", body.note)
        except KeyError as e:
            raise HTTPException(404, str(e).strip("'")) from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        _audit(admin, "decision_decide", decision_id, after={"status": out["status"], "topic": out["topic"]},
               severity="high" if out["requires_human_approval"] else "info")
        return out

    @app.post("/api/goals/{goal_id}/approve")
    def goal_approve(goal_id: int, admin: dict[str, Any] = Depends(goal_approver)) -> dict[str, Any]:
        out = loop.approve(goal_id, actor=f"user:{admin['name']}")
        _audit(admin, "goal_approve", goal_id, after={**out, "delegated": bool(admin.get("delegated"))},
               severity="high" if admin.get("delegated") else "info")
        return out

    @app.post("/api/goals/{goal_id}/reject")
    def goal_reject(goal_id: int, admin: dict[str, Any] = Depends(goal_approver)) -> dict[str, Any]:
        loop.reject(goal_id, actor=f"user:{admin['name']}")
        _audit(admin, "goal_reject", goal_id, after={"delegated": bool(admin.get("delegated"))},
               severity="high" if admin.get("delegated") else "info")
        return {"ok": True}

    @app.post("/api/goals/{goal_id}/cancel")
    def goal_cancel(goal_id: int, user: dict[str, Any] = Depends(allowed("run"))) -> dict[str, Any]:
        with session() as s:
            g = s.get(Goal, goal_id)
            seen = _visible_project_ids(s, user)
            if g is None or (seen is not None and g.project_id is not None and g.project_id not in seen):
                raise KeyError(goal_id)
        loop.cancel(goal_id, actor=f"user:{user['name']}")
        return {"ok": True}

    @app.post("/api/goals/{goal_id}/pause")
    def goal_pause(goal_id: int, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        try:
            loop.pause(goal_id, actor=f"user:{admin['name']}")
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        _audit(admin, "goal_pause", goal_id)
        return {"ok": True}

    @app.post("/api/goals/{goal_id}/budget")
    def goal_budget(goal_id: int, body: GoalBudgetBody, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        """Change a goal's budget (S04-R-09): administrators only, audited; a child stays within its parent."""
        try:
            out = budgets.set_budget(goal_id, f"user:{admin['name']}", body.budget_usd, body.budget_tokens,
                                     body.max_active_agents)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        _audit(admin, "goal_budget", goal_id, after=body.model_dump(exclude_none=True))
        return out

    @app.post("/api/goals/{goal_id}/resume")
    def goal_resume(goal_id: int, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        try:
            loop.resume(goal_id, actor=f"user:{admin['name']}")
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        _audit(admin, "goal_resume", goal_id)
        return {"ok": True}

    @app.post("/api/goals/{goal_id}/team")
    def goal_team(goal_id: int, body: GoalTeamBody, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        """Resize a goal's team (S04-R-11): add a role (or take a released agent back), or release an agent."""
        actor = f"user:{admin['name']}"
        try:
            if body.action == "release":
                if body.agent_id is None:
                    raise ValueError("name the agent to release")
                out = teams.release(goal_id, body.agent_id, body.reason, actor)
            else:
                if body.agent_id is None and not body.role.strip():
                    raise ValueError("name a role to add, or a released agent to take back")
                out = teams.add(goal_id, body.role, body.skills, body.reason, actor, agent_id=body.agent_id)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        _audit(admin, f"goal_team_{body.action}", goal_id, after=body.model_dump(exclude_none=True))
        return out

    # --- workflow templates (S04-R-11): versioned per goal kind ---
    @app.get("/api/strategies")
    def strategy_list(_: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
        return strategies.list_strategies()

    @app.post("/api/strategies/{kind}")
    def strategy_save(kind: str, body: StrategyBody, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        try:
            before = strategies.effective(kind)
            out = strategies.save(kind, body.body, f"user:{admin['name']}", body.note)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        _audit(admin, "strategy_save", kind, before=before, after=out)
        return out

    # --- self-evaluation of orchestration (S04-R-13): reports per template version, recommended candidates ---
    @app.get("/api/strategies/evaluation")
    def strategy_evaluation(kind: str = "", _: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
        try:
            return orchestration.evaluate(kind)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/strategies/{kind}/recommend")
    def strategy_recommend(kind: str, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        try:
            out = orchestration.recommend(kind, f"user:{admin['name']}")
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        _audit(admin, "strategy_recommend", kind, after={"recorded": out.get("recorded"), "changes": out["changes"]})
        return out

    @app.post("/api/strategies/{kind}/restore/{version}")
    def strategy_restore(kind: str, version: int, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        try:
            out = strategies.restore(kind, version, f"user:{admin['name']}")
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        _audit(admin, "strategy_restore", f"{kind} v{version}", after={"version": out["version"]})
        return out

    @app.get("/api/goals/{goal_id}/trace")
    def goal_trace(goal_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        with session() as s:
            g = s.get(Goal, goal_id)
            seen = _visible_project_ids(s, user)
            if g is None or (seen is not None and g.project_id is not None and g.project_id not in seen):
                raise KeyError(goal_id)
        return loop.trace(goal_id)

    # --- S08-R-05: software bill of materials (CycloneDX 1.5 JSON) ---
    @app.get("/api/goals/{goal_id}/sbom")
    def goal_sbom(goal_id: int, user: dict[str, Any] = Depends(current_user)) -> Response:
        from agent_company import dependencies

        with session() as s:
            g = s.get(Goal, goal_id)
            seen = _visible_project_ids(s, user)
            if g is None or (seen is not None and g.project_id is not None and g.project_id not in seen):
                raise KeyError(goal_id)
            ev = s.scalars(select(Event).where(Event.goal_id == goal_id, Event.kind == "sbom")
                           .order_by(Event.id.desc())).first()
            sha, repo = ((ev.data or {}).get("commit", "") if ev else ""), g.repo_path
        if not sha:
            raise HTTPException(404, "no SBOM: the goal has not been merged")
        path = dependencies.sbom_path(sha)
        if not path.exists():  # written by an older version or removed: built again from the commit
            path, _ = dependencies.store_sbom(repo, sha)
        return Response(path.read_text(), media_type="application/vnd.cyclonedx+json", headers={
            "Content-Disposition": f'attachment; filename="goal-{goal_id}-{sha[:10]}.cdx.json"'})

    @app.get("/api/projects/{project_id}/sbom")
    def project_sbom(project_id: int, rev: str = "HEAD", user: dict[str, Any] = Depends(current_user)) -> Response:
        import json
        import re

        from agent_company import dependencies, worktrees

        p = projects.get(project_id, user)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/^~-]{0,199}", rev) or ".." in rev:
            raise HTTPException(400, "rev is a branch, tag or commit")
        if not p.path or not (Path(p.path) / ".git").exists():
            raise HTTPException(400, "the project has no git repository")
        try:
            doc = dependencies.sbom(p.path, rev)
        except worktrees.WorktreeError as e:
            raise HTTPException(400, str(e)[:300]) from e
        sha = doc["metadata"]["component"]["version"]
        return Response(json.dumps(doc, indent=2), media_type="application/vnd.cyclonedx+json", headers={
            "Content-Disposition": f'attachment; filename="{re.sub(r"[^A-Za-z0-9._-]+", "-", p.name)}-{sha[:10]}.cdx.json"'})

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
    def research_request(body: ResearchBody, user: dict[str, Any] = Depends(allowed("run", "research_request"))
                         ) -> dict[str, Any]:
        with session() as s:
            seen = _visible_project_ids(s, user)
            g = s.get(Goal, body.goal_id) if body.goal_id is not None else None
            pid = body.project_id if body.project_id is not None else (g.project_id if g else None)
        if seen is not None and pid is not None and pid not in seen:
            raise HTTPException(404, "no such project")
        try:
            out = research.submit(body.question, body.urls, user["id"], project_id=body.project_id,
                                  goal_id=body.goal_id, keywords=body.keywords,
                                  source_preferences=body.source_preferences, priority=body.priority,
                                  budget_usd=body.budget_usd, deadline=body.deadline, recurring=body.recurring)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        if out["duplicate"]:  # an open or recent request asks the same: that one answers it
            return out
        rid = out["id"]

        def run() -> None:
            try:
                research.run_report(rid)
            except Exception:  # noqa: BLE001 - recorded on the report
                pass

        threading.Thread(target=run, daemon=True, name=f"report-{rid}").start()
        return out

    @app.get("/api/research/requests")
    def research_reports(project_id: int | None = None, goal_id: int | None = None,
                         user: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
        with session() as s:
            seen = _visible_project_ids(s, user)
        rows = research.list_reports(project_id=project_id, goal_id=goal_id)
        return [r for r in rows if seen is None or r["project_id"] is None or r["project_id"] in seen]

    @app.post("/api/research/requests/{report_id}/review")
    def research_review(report_id: int, body: DecideBody, admin: dict[str, Any] = Depends(admin_user)
                        ) -> dict[str, Any]:
        """A person approves or rejects a report waiting for its review (ASEAO06 S06-R-04)."""
        try:
            out = research.decide_report(report_id, body.approve, f"user:{admin['name']}", body.note)
        except KeyError as e:
            raise HTTPException(404, "no such report") from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        _audit(admin, "research.review", f"report:{report_id}", after={"review": out["review"], "note": body.note},
               severity="warning" if body.approve else "info")
        return out

    @app.get("/api/research/search")
    def search(q: str, limit: int = 10, _: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
        return research.search(q, min(limit, 50))

    @app.get("/api/research/documents")
    def documents(_: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
        with session() as s:
            return [{"id": d.id, "url": d.url, "title": d.title, "chars": len(d.text),
                     "source": d.source_url, "at": d.created_at.isoformat(),
                     "category": d.category or research.classify_source(d.url), "flags": d.flags or [],
                     "version": d.version or 1}
                    for d in s.scalars(select(research.Document).order_by(research.Document.id.desc()).limit(300))]

    @app.get("/api/research/sources")
    def sources(_: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        current = research.shifts(1)
        return {"sources": research.list_sources(), "roster": research.rotation_roster(),
                "interval": settings().rotation_interval, "shift_seconds": settings().shift_seconds,
                "shift": current[0] if current and current[0]["status"] == "active" else None}

    @app.get("/api/research/shifts")
    def research_shifts(limit: int = 20, _: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
        return research.shifts(limit)

    @app.post("/api/research/shifts/replace")
    def shift_replace(body: ShiftReplaceBody, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        out = research.replace_shift_agent(body.reason, body.agent_id)
        _audit(admin, "shift_replace", f"shift #{out['previous_id']}",
               after={"shift_id": out["id"], "agent_id": out["agent_id"], "reason": body.reason})
        return out

    @app.post("/api/research/sources")
    def source_add(body: SourceBody, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        out = research.add_source(body.url, body.depth, body.max_pages, body.interval, body.subject, body.notify_room)
        _audit(admin, "source_add", out["url"], after={k: out[k] for k in ("interval", "subject", "notify_room")})
        return out

    @app.patch("/api/research/sources/{source_id}")
    def source_update(source_id: int, body: dict[str, Any], admin: dict[str, Any] = Depends(admin_user)
                      ) -> dict[str, Any]:
        out = research.update_source(source_id, body)
        _audit(admin, "source_update", out["url"], after=body)
        return out

    @app.post("/api/research/sources/{source_id}/run")
    def source_run(source_id: int, _: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        return research.rotation_tick(source_id=source_id) or {"ran": False, "detail": "source disabled or unknown, "
                                                                                      "or no research agent"}

    @app.get("/api/research/documents/{document_id}/versions")
    def document_versions(document_id: int, _: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
        return research.document_versions(document_id)

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
        granted = admin["permissions"].get("projects", "own")
        if not admin["is_admin"] and isinstance(granted, list):
            auth.set_permissions(admin["id"], {"projects": [*granted, out["id"]]})
        _audit(admin, "project_create", out["name"], after={"path": out["path"], "github_url": out["github_url"]})
        return out

    @app.patch("/api/projects/{project_id}")
    def project_update(project_id: int, changes: dict[str, Any], admin: dict[str, Any] = Depends(admin_user)
                       ) -> dict[str, Any]:
        out = projects.update(project_id, changes, actor=f"user:{admin['name']}")
        _audit(admin, "project_update", out["name"], after=changes)
        return out

    @app.post("/api/projects/{project_id}/status")
    def project_status(project_id: int, body: dict[str, Any], user: dict[str, Any] = Depends(current_user)
                       ) -> dict[str, Any]:
        """Pause, resume, archive, cancel or complete (ASEAO05 S05-R-06): an administrator or the project's owner."""
        p = projects.get(project_id, user)
        if not user["is_admin"] and p.created_by != user["id"]:
            raise HTTPException(403, "only an administrator or the project's owner changes its status")
        before = p.status or "active"
        out = projects.set_status(project_id, str(body.get("status") or ""), user["name"], str(body.get("reason") or ""))
        _audit(user, "project_status", out["name"], before={"status": before}, after={"status": out["status"],
               "reason": out["status_reason"], "goals": out["goals_changed"]}, severity="high" if out["status"] ==
               "cancelled" else "info")
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
        gm = None
        if p.manager_id:  # who the owner talks to: the GM's model now (Claude Code, Codex, an API model, another agent's)
            from agent_company.db import Agent

            with session() as s:
                a = s.get(Agent, p.manager_id)
                pref = a.model_pref if a else None
            c = router.resolve("general_manager", pref)
            gm = {"model": c.name, "kind": c.kind, "reason": c.reason}
        return projects.project_dict(p) | {"gm": gm, "members": projects.members(p.id), "goals": goals,
                                           "proposals": [x for x in projects.list_proposals(user)
                                                         if x["project_id"] == p.id][:30]}

    @app.get("/api/projects/{project_id}/messages")
    def project_messages(project_id: int, after: int = 0, topic: str = "", q: str = "",
                         user: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
        projects.get(project_id, user)
        return projects.messages(project_id, after, limit=100 if q.strip() else 300, topic=topic, q=q)

    @app.post("/api/projects/{project_id}/command")
    def project_command(project_id: int, body: CommandBody, user: dict[str, Any] = Depends(current_user)
                        ) -> dict[str, Any]:
        try:
            return projects.command(project_id, user, body.content, body.attachments, body.topic, body.classification)
        except PermissionError:  # an order would start agent work: a refused one is audited (SPEC-02 AT-014)
            _audit(user, "project_command_denied", project_id, severity="warning")
            raise

    # --- topics: conversation lanes of the command window (ASEAO05 S05-R-07) ---
    @app.get("/api/projects/{project_id}/topics")
    def project_topics(project_id: int, user: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
        projects.get(project_id, user)
        return projects.topics(project_id)

    @app.post("/api/projects/{project_id}/topics")
    def topic_create(project_id: int, body: TopicBody, user: dict[str, Any] = Depends(current_user)
                     ) -> dict[str, Any]:
        projects.get(project_id, user)
        if not auth.can(user, "run"):
            raise HTTPException(403, "you can read this project but not open topics in it")
        if body.agent_ids and not user["is_admin"]:
            raise HTTPException(403, "only administrators assign agents to a topic")
        out = projects.create_topic(project_id, body.name, body.description, body.agent_ids, user["id"])
        _audit(user, "topic_create", project_id, after={"topic": out["name"], "agents": out["agent_ids"]})
        return out

    @app.patch("/api/projects/{project_id}/topics/{name}")
    def topic_update(project_id: int, name: str, body: dict[str, Any], admin: dict[str, Any] = Depends(admin_user)
                     ) -> dict[str, Any]:
        projects.get(project_id, admin)
        out = projects.update_topic(project_id, name, body)
        _audit(admin, "topic_update", project_id, after={"topic": out["name"], **{k: out[k] for k in body if k in out}})
        return out

    @app.post("/api/projects/{project_id}/sync")
    def project_sync(project_id: int, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        projects.get(project_id, admin)
        return {"message": gitsync.sync_project(project_id, admin)}

    @app.post("/api/uploads")
    async def upload(file: UploadFile = File(...), classification: str = Form("internal"),
                     user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        data = await file.read(projects.MAX_UPLOAD + 1)
        return projects.save_upload(file.filename or "file", data, file.content_type or "", user["id"],
                                    classification)

    @app.get("/api/uploads/{uid}")
    def upload_get(uid: str, _: dict[str, Any] = Depends(current_user)) -> FileResponse:
        path, ctype = projects.upload_path(uid)
        return FileResponse(path, media_type=ctype, headers={"X-Content-Type-Options": "nosniff",
                                                             "Content-Security-Policy": "default-src 'none'"})

    @app.get("/api/proposals")
    def proposals_list(status: str = "", user: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
        return projects.list_proposals(user, status)

    @app.post("/api/proposals/{proposal_id}/case")
    def proposal_case(proposal_id: int, body: ProposalCaseBody, admin: dict[str, Any] = Depends(admin_user)
                      ) -> dict[str, Any]:
        """ASEAO10 S10-R-01: write or complete the documented case of a product or project proposal."""
        out = projects.set_case(proposal_id, body.kind, body.case)
        _audit(admin, "proposal_case", proposal_id, after={"kind": out["kind"], "missing": out["case_missing"]})
        return out

    @app.post("/api/proposals/{proposal_id}/{decision}")
    def proposal_decide(proposal_id: int, decision: str, admin: dict[str, Any] = Depends(admin_user)
                        ) -> dict[str, Any]:
        if decision not in {"approve", "reject"}:
            raise HTTPException(404, "unknown decision")
        out = projects.decide(proposal_id, decision == "approve", admin["name"])
        _audit(admin, f"proposal_{decision}", proposal_id, after={"status": out["status"],
                                                                 "goal": out.get("spawned_goal_id"),
                                                                 "project": out.get("spawned_project_id")})
        return out

    # --- project management: milestones, risks and blockers, insights, action items (ASEAO05) ---
    @app.get("/api/projects/{project_id}/plan")
    def project_plan(project_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        projects.get(project_id, user)
        return management.overview(project_id)

    @app.post("/api/projects/{project_id}/milestones")
    def milestone_create(project_id: int, body: dict[str, Any], admin: dict[str, Any] = Depends(admin_user)
                         ) -> dict[str, Any]:
        fields = dict(body)
        out = management.create_milestone(project_id, str(fields.pop("name", "")), **fields)
        _audit(admin, "milestone_create", out["id"], after={"project": project_id, "name": out["name"]})
        return out

    @app.patch("/api/milestones/{milestone_id}")
    def milestone_update(milestone_id: int, changes: dict[str, Any], admin: dict[str, Any] = Depends(admin_user)
                         ) -> dict[str, Any]:
        out = management.update_milestone(milestone_id, changes)
        _audit(admin, "milestone_update", milestone_id, after=changes)
        return out

    @app.post("/api/milestones/{milestone_id}/check")
    def milestone_check(milestone_id: int, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        out = management.check_milestone(milestone_id)
        _audit(admin, "milestone_check", milestone_id, after={"progress": out["progress"]})
        return out

    @app.post("/api/milestones/{milestone_id}/complete")
    def milestone_complete(milestone_id: int, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        out = management.complete_milestone(milestone_id, admin["name"])
        _audit(admin, "milestone_complete", milestone_id)
        return out

    @app.post("/api/milestones/{milestone_id}/criteria/{index}")
    def milestone_criterion(milestone_id: int, index: int, body: dict[str, Any],
                            admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        out = management.set_criterion(milestone_id, index, bool(body.get("met")), admin["name"])
        _audit(admin, "milestone_criterion", milestone_id, after={"index": index, "met": bool(body.get("met"))})
        return out

    @app.post("/api/projects/{project_id}/risks")
    def risk_create(project_id: int, body: dict[str, Any], admin: dict[str, Any] = Depends(admin_user)
                    ) -> dict[str, Any]:
        fields = dict(body)
        out = management.create_risk(project_id, str(fields.pop("title", "")), **fields)
        _audit(admin, "risk_create", out["id"], after={"project": project_id, "kind": out["kind"], "title": out["title"]})
        return out

    @app.patch("/api/risks/{risk_id}")
    def risk_update(risk_id: int, changes: dict[str, Any], admin: dict[str, Any] = Depends(admin_user)
                    ) -> dict[str, Any]:
        out = management.update_risk(risk_id, changes)
        _audit(admin, "risk_update", risk_id, after=changes)
        return out

    @app.post("/api/projects/{project_id}/action-items")
    def action_item_create(project_id: int, body: dict[str, Any], admin: dict[str, Any] = Depends(admin_user)
                           ) -> dict[str, Any]:
        fields = dict(body)
        out = management.create_item(project_id, str(fields.pop("title", "")), **fields)
        _audit(admin, "action_item_create", out["id"], after={"project": project_id, "title": out["title"]})
        return out

    @app.patch("/api/action-items/{item_id}")
    def action_item_update(item_id: int, changes: dict[str, Any], admin: dict[str, Any] = Depends(admin_user)
                           ) -> dict[str, Any]:
        out = management.update_item(item_id, changes)
        _audit(admin, "action_item_update", item_id, after=changes)
        return out

    @app.post("/api/insights/{insight_id}/status")
    def insight_status(insight_id: int, body: dict[str, Any], admin: dict[str, Any] = Depends(admin_user)
                       ) -> dict[str, Any]:
        out = management.advance_insight(insight_id, str(body.get("status", "")), admin["name"],
                                         str(body.get("decision", "")), str(body.get("result", "")),
                                         bool(body.get("as_proposal")))
        _audit(admin, f"insight_{out['status']}", insight_id,
               after={"knowledge": out["knowledge_id"], "proposal": out["proposal_id"]})
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
        value = prefs.set_value(key, body.value, actor=f"user:{admin['name']}")
        _audit(admin, "setting_set", key, before={"value": before}, after={"value": value})
        return {"key": key, "value": value}

    @app.delete("/api/settings/{key}")
    def settings_clear(key: str, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        prefs.clear(key, actor=f"user:{admin['name']}")
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

    # --- Tools & MCP (mcp.py): administrators manage servers; agents get them through grants ---
    def _mcp(call: Any) -> Any:
        try:
            return call()
        except mcp.McpError as e:
            raise HTTPException(502, str(e)) from e

    @app.get("/api/mcp")
    def mcp_list(_: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        return mcp.list_servers()

    @app.get("/api/mcp/calls")
    def mcp_calls(_: dict[str, Any] = Depends(admin_user)) -> list[dict[str, Any]]:
        return mcp.recent_calls()

    @app.post("/api/mcp/servers")
    def mcp_add(body: McpServerBody, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        out = mcp.save_server(body.model_dump(), admin["name"])
        _audit(admin, "mcp_server_add", out["name"], after={"transport": out["transport"], "url": out["url"],
                                                           "command": out["command"]}, severity="high")
        return out

    @app.put("/api/mcp/servers/{server_id}")
    def mcp_update(server_id: int, body: McpServerBody, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        out = mcp.save_server(body.model_dump(), admin["name"], server_id)
        _audit(admin, "mcp_server_update", out["name"], after={"transport": out["transport"]}, severity="high")
        return out

    @app.delete("/api/mcp/servers/{server_id}")
    def mcp_delete(server_id: int, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        name = mcp.get_server(server_id)["name"]
        mcp.delete_server(server_id)
        _audit(admin, "mcp_server_delete", name)
        return {"ok": True}

    @app.post("/api/mcp/catalog/{key}")
    def mcp_from_catalog(key: str, body: McpCatalogBody, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        out = _mcp(lambda: mcp.add_from_catalog(key, body.values, admin["name"]))
        _audit(admin, "mcp_server_add", out["name"], after={"catalog": key}, severity="high")
        return out

    @app.post("/api/mcp/servers/{server_id}/test")
    def mcp_test(server_id: int, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        out = _mcp(lambda: mcp.test(server_id))
        _audit(admin, "mcp_server_test", out["name"], after={"tools": len(out["tools"])})
        return out

    @app.put("/api/mcp/servers/{server_id}/tools")
    def mcp_tools(server_id: int, body: McpToolsBody, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        out = mcp.set_tools(server_id, body.allowed)
        _audit(admin, "mcp_tools_allow", out["name"], after={"allowed": body.allowed}, severity="high")
        return out

    @app.put("/api/mcp/servers/{server_id}/grants")
    def mcp_grants(server_id: int, body: McpGrantsBody, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        out = mcp.set_grants(server_id, body.agent_ids, body.everyone)
        _audit(admin, "mcp_grants", out["name"], after={"agents": body.agent_ids, "everyone": body.everyone})
        return out

    @app.post("/api/mcp/servers/{server_id}/call")
    def mcp_call(server_id: int, body: McpCallBody, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        _audit(admin, "mcp_tool_call", f"{server_id}:{body.tool}")
        return _mcp(lambda: mcp.call(server_id, body.tool, body.arguments, f"user:{admin['name']}"))

    # --- catalog: skills, plugins, rules, commands, hooks (catalog.py) ---
    @app.get("/api/catalog")
    def catalog_list(kind: str = "", _: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
        return catalog.list_items(kind)

    @app.post("/api/catalog")
    def catalog_add(body: CatalogBody, admin: dict[str, Any] = Depends(allowed("manage_agents"))) -> dict[str, Any]:
        out = catalog.add(body.kind, body.name, body.description, body.content, body.source, f"user:{admin['name']}")
        _audit(admin, "catalog_add", f"{out['kind']}:{out['name']}")
        return out

    @app.patch("/api/catalog/{item_id}")
    def catalog_update(item_id: int, changes: dict[str, Any], admin: dict[str, Any] = Depends(allowed("manage_agents"))
                       ) -> dict[str, Any]:
        out = catalog.update(item_id, changes)
        _audit(admin, "catalog_update", f"{out['kind']}:{out['name']}", after={k: v for k, v in changes.items() if k != "content"})
        return out

    @app.delete("/api/catalog/{item_id}")
    def catalog_delete(item_id: int, admin: dict[str, Any] = Depends(allowed("manage_agents"))) -> dict[str, Any]:
        catalog.delete(item_id)
        _audit(admin, "catalog_delete", item_id)
        return {"ok": True}

    @app.post("/api/projects/{project_id}/claude-config")
    def project_claude_config(project_id: int, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        """Write the catalog (and the active agents) into the project's .claude/ (administrators)."""
        from agent_company.db import Agent

        p = projects.get(project_id, admin)
        with session() as s:
            ids = list(s.scalars(select(Agent.id).where(Agent.enabled, Agent.lifecycle == "active",
                                                        Agent.parent_id.is_(None))))
        written = catalog.export_claude(Path(p.path), ids)
        _audit(admin, "project_claude_config", p.name, after={"written": written[:50]}, severity="high")
        return {"written": written, "path": p.path}

    # --- agent definition files: the agent's body, versioned (agentdefs.py) ---
    @app.get("/api/agents/{agent_id}/definition")
    def agent_definition(agent_id: int, _: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        return agentdefs.read(agent_id)

    @app.put("/api/agents/{agent_id}/definition")
    def agent_definition_put(agent_id: int, body: DefinitionBody, admin: dict[str, Any] = Depends(allowed("manage_agents"))
                             ) -> dict[str, Any]:
        out = agentdefs.write_text(agent_id, body.text, admin["name"])
        _audit(admin, "agent_definition_edit", agent_id, severity="high")
        return out

    @app.post("/api/learning/study")
    def learning_study(agent_id: int | None = None, admin: dict[str, Any] = Depends(allowed("manage_agents"))
                       ) -> dict[str, Any]:
        out = agentdefs.study(agent_id)
        _audit(admin, "learning_study", out.get("studied") or "", after=out)
        return out

    @app.get("/api/hooks")
    def hooks_recent(_: dict[str, Any] = Depends(admin_user)) -> list[dict[str, Any]]:
        from agent_company import hooks

        return hooks.recent()

    # --- emergency stop (S14-R-01, level E5) ---
    @app.get("/api/emergency")
    def emergency_get(_: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        return governance.emergency() or {"on": False}

    @app.post("/api/emergency")
    def emergency_set(body: EmergencyBody, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        if body.on and len(body.reason.strip()) < 3:
            raise HTTPException(400, "give a reason")
        return governance.set_emergency(body.on, f"user:{admin['name']}", body.reason)

    # --- scoped stops E0-E4 (S14-R-01); governance.stop/lift write the audit record themselves ---
    @app.get("/api/stops")
    def stops_get(_: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        return {"E5": governance.emergency(), "stops": governance.stops(), "levels": governance.LEVELS,
                "executors": sorted(governance.EXECUTOR_KINDS)}

    @app.post("/api/stops")
    def stops_set(body: StopBody, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        if len(body.reason.strip()) < 3:
            raise HTTPException(400, "give a reason")
        try:
            return governance.stop(body.level, body.scope, f"user:{admin['name']}", body.reason)
        except (KeyError, ValueError) as e:
            raise HTTPException(400, str(e).strip("'")) from e

    @app.post("/api/stops/lift")
    def stops_lift(body: StopBody, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        try:
            return governance.lift(body.level, body.scope, f"user:{admin['name']}")
        except KeyError as e:
            raise HTTPException(404, str(e).strip("'")) from e

    # --- security view (SPEC-15 S15-R-03) and incidents (S14-R-04) ---
    @app.get("/api/security")
    def security(_: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        return governance.security_summary()

    @app.get("/api/incidents")
    def incidents_list(status: str = "", _: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        return {"items": governance.list_incidents(status), "categories": governance.INCIDENT_CATEGORIES,
                "severities": governance.SEVERITIES, "flow": governance.INCIDENT_FLOW}

    @app.post("/api/incidents")
    def incidents_open(body: IncidentBody, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        evidence = [{"type": "note", "text": body.note}] if body.note else []
        try:
            return governance.open_incident(body.category, body.severity, body.title, f"user:{admin['name']}",
                                            goal_id=body.goal_id, agent_id=body.agent_id,
                                            project_id=body.project_id, evidence=evidence)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/incidents/{incident_id}")
    def incidents_update(incident_id: int, body: IncidentUpdateBody, admin: dict[str, Any] = Depends(admin_user)
                         ) -> dict[str, Any]:
        try:
            return governance.update_incident(incident_id, f"user:{admin['name']}", body.status, body.note,
                                              body.root_cause, body.contain)
        except KeyError as e:
            raise HTTPException(404, str(e).strip("'")) from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    # --- dashboard layout shared by every user (rail order: Settings → Navigation) ---
    @app.get("/api/ui/{key}")
    def ui_get(key: str, _: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        return {"key": key, "value": prefs.get_ui(key)}

    @app.put("/api/ui/{key}")
    def ui_put(key: str, body: UiBody, admin: dict[str, Any] = Depends(admin_user)) -> dict[str, Any]:
        value = prefs.set_ui(key, body.value)
        _audit(admin, "ui_layout", key, after={"value": value})
        return {"key": key, "value": value}

    @app.get("/api/projects-defaults")
    def projects_defaults(_: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        return {"base": str(projects.default_base())}

    # --- dashboard ---
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    def index() -> HTMLResponse:
        return HTMLResponse(_index_html(), headers={"Cache-Control": "no-cache"})

    return app


_INDEX: tuple[float, str, str] = (0.0, "", "")


def _index_html() -> str:
    """index.html with every /static/ file it loads versioned by content hash, so browsers cache them for
    good and fetch a file again only when it changed (no revalidation round trip per file per page load)."""
    import hashlib
    import re

    global _INDEX
    files = list(STATIC.glob("*.js")) + list(STATIC.glob("*.css")) + [STATIC / "index.html"]
    stamp = max(f.stat().st_mtime for f in files)
    if _INDEX[0] == stamp:
        return _INDEX[1]

    def version(m: re.Match[str]) -> str:
        f = STATIC / m.group(2)
        if not f.is_file():
            return m.group(0)
        return f"{m.group(1)}/static/{m.group(2)}?v={hashlib.sha256(f.read_bytes()).hexdigest()[:10]}{m.group(3)}"

    html = re.sub(r'((?:src|href)=")/static/([\w./-]+\.(?:js|css))(")', version, (STATIC / "index.html").read_text())
    # The build a page was loaded from: /api/overview reports the current one, so an open tab reloads itself.
    build = hashlib.sha256(html.encode()).hexdigest()[:12]
    html = html.replace("</head>", f'<meta name="ac-build" content="{build}">\n</head>', 1)
    _INDEX = (stamp, html, build)
    return html


def static_build() -> str:
    _index_html()
    return _INDEX[2]


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
            "protect_tests": g.protect_tests, "permits": g.permits or [], "baseline": g.baseline, "history": g.history,
            "estimated_cost_usd": g.estimated_cost_usd or 0.0, "budget_usd": g.budget_usd or 0.0,
            "budget_tokens": g.budget_tokens or 0, "max_active_agents": g.max_active_agents or 0,
            "paused_for": (g.checkpoint or {}).get("paused_for", ""), "requirement_keys": g.requirement_keys or [],
            "kind": g.kind or "", "strategy": dict(g.strategy or {}), "test_strategy": dict(g.test_strategy or {}),
            "created_at": g.created_at.isoformat(), "updated_at": g.updated_at.isoformat()}


def _event_dict(e: Event) -> dict[str, Any]:
    return {"id": e.id, "goal_id": e.goal_id, "agent_id": e.agent_id, "kind": e.kind, "message": e.message,
            "data": e.data, "at": e.created_at.isoformat()}
