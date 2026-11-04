"""Persistence: SQLAlchemy models and session handling.

SQLite under `data/` is the default so the project runs with no setup. Set
`DATABASE_URL=postgresql+psycopg://user:pass@host/db` (and `uv sync --extra postgres`)
to use PostgreSQL; the schema is the same.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Iterator

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from agent_company.config import settings


def now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class Room(Base):
    """A department of the company (development, research, security, ...)."""

    __tablename__ = "rooms"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    division: Mapped[str] = mapped_column(String(32))  # management|development|learning|research|security|business
    description: Mapped[str] = mapped_column(Text, default="")
    rotational: Mapped[bool] = mapped_column(Boolean, default=False)


class Agent(Base):
    __tablename__ = "agents"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    role: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(128))
    room_key: Mapped[str] = mapped_column(String(64), index=True)
    superior_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    goal: Mapped[str] = mapped_column(Text, default="")
    backstory: Mapped[str] = mapped_column(Text, default="")
    skills: Mapped[dict[str, Any]] = mapped_column(default=dict)  # skill -> level (1..10)
    permissions: Mapped[list[Any]] = mapped_column(default=list)  # read, write, shell, network
    model_pref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="idle")  # idle|busy|disabled
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    xp: Mapped[int] = mapped_column(Integer, default=0)
    level: Mapped[int] = mapped_column(Integer, default=1)
    tasks_done: Mapped[int] = mapped_column(Integer, default=0)
    tasks_failed: Mapped[int] = mapped_column(Integer, default=0)
    playbook: Mapped[str] = mapped_column(Text, default="")
    lifecycle: Mapped[str] = mapped_column(String(16), default="active")  # active|paused|retired (SPEC02 §6)
    playbook_version: Mapped[int] = mapped_column(Integer, default=0)
    experiment: Mapped[dict[str, Any]] = mapped_column(default=dict)  # running candidate playbook trial
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PlaybookVersion(Base):
    __tablename__ = "playbook_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    playbook: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class EvolutionEvent(Base):
    """Observe -> diagnose -> generate -> experiment -> evaluate -> promote/rollback."""

    __tablename__ = "evolution_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    stage: Mapped[str] = mapped_column(String(16))
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    title: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Handover(Base):
    """Context passed when a task moves to another agent (SPEC12 §10)."""

    __tablename__ = "handovers"
    id: Mapped[int] = mapped_column(primary_key=True)
    goal_id: Mapped[int] = mapped_column(ForeignKey("goals.id", ondelete="CASCADE"), index=True)
    task_key: Mapped[str] = mapped_column(String(64))
    from_agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    to_agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    package: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(16), default="accepted")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Defect(Base):
    """A failed quality-gate check, tracked until a later iteration passes it (ASEAO09 §11)."""

    __tablename__ = "defects"
    __table_args__ = (UniqueConstraint("goal_id", "check"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    goal_id: Mapped[int] = mapped_column(ForeignKey("goals.id", ondelete="CASCADE"), index=True)
    check: Mapped[str] = mapped_column(String(256))
    severity: Mapped[str] = mapped_column(String(16))  # critical|high|medium|low|info
    status: Mapped[str] = mapped_column(String(16), default="detected")
    # detected|reopened|closed|accepted_risk|deferred
    detail: Mapped[str] = mapped_column(Text, default="")
    first_iteration: Mapped[int] = mapped_column(Integer, default=1)
    last_iteration: Mapped[int] = mapped_column(Integer, default=1)
    closed_iteration: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class EvalRun(Base):
    """One run of one benchmark task (eval harness)."""

    __tablename__ = "eval_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(64), index=True)
    suite: Mapped[str] = mapped_column(String(128))
    task: Mapped[str] = mapped_column(String(128))
    repeat: Mapped[int] = mapped_column(Integer)
    goal_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(24), default="")
    end_reason: Mapped[str] = mapped_column(String(64), default="")
    iterations: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float] = mapped_column(Float, default=0.0)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    seconds: Mapped[float] = mapped_column(Float, default=0.0)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Relationship(Base):
    __tablename__ = "relationships"
    __table_args__ = (UniqueConstraint("a_id", "b_id", "kind"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    a_id: Mapped[int] = mapped_column(ForeignKey("agents.id"), index=True)
    b_id: Mapped[int] = mapped_column(ForeignKey("agents.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # similar|collaborated
    weight: Mapped[float] = mapped_column(Float, default=1.0)


class Lesson(Base):
    __tablename__ = "lessons"
    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"), index=True)
    goal_id: Mapped[int | None] = mapped_column(ForeignKey("goals.id"), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(String(16))  # success|failure|research
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ModelEntry(Base):
    __tablename__ = "models"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    kind: Mapped[str] = mapped_column(String(16))  # llm|claude_code|codex|offline
    model_id: Mapped[str] = mapped_column(String(256))  # e.g. anthropic/claude-sonnet-5-5
    input_cost: Mapped[float] = mapped_column(Float, default=0.0)  # USD per 1M tokens
    output_cost: Mapped[float] = mapped_column(Float, default=0.0)
    daily_token_limit: Mapped[int] = mapped_column(Integer, default=0)  # 0 = unlimited
    weekly_token_limit: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    base_url: Mapped[str] = mapped_column(String(512), default="")  # OpenAI-compatible server (llama-server, LM Studio)
    api_key_env: Mapped[str] = mapped_column(String(64), default="")  # env var holding this model's key


class Route(Base):
    __tablename__ = "routes"
    role: Mapped[str] = mapped_column(String(64), primary_key=True)
    model_name: Mapped[str] = mapped_column(String(128))
    escalation: Mapped[str] = mapped_column(String(128), default="")  # model for retries after a failed gate


class Goal(Base):
    __tablename__ = "goals"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(256))
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="queued")
    # queued|running|awaiting_approval|approved|rejected|failed|cancelled
    repo_path: Mapped[str] = mapped_column(Text, default="")
    base_branch: Mapped[str] = mapped_column(String(128), default="")
    branch: Mapped[str] = mapped_column(String(128), default="")
    worktree: Mapped[str] = mapped_column(Text, default="")
    iteration: Mapped[int] = mapped_column(Integer, default=0)
    max_iterations: Mapped[int] = mapped_column(Integer, default=3)
    urls: Mapped[list[Any]] = mapped_column(default=list)
    quality_commands: Mapped[list[Any]] = mapped_column(default=list)
    result: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    origin: Mapped[str] = mapped_column(String(16), default="user")  # user|proposal|eval
    parent_goal_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attachments: Mapped[list[Any]] = mapped_column(default=list)
    allowed_paths: Mapped[list[Any]] = mapped_column(default=list)  # globs; empty = anywhere
    protect_tests: Mapped[bool] = mapped_column(Boolean, default=False)
    baseline: Mapped[dict[str, Any]] = mapped_column(default=dict)  # command -> {"passed", "detail"}
    history: Mapped[list[Any]] = mapped_column(default=list)  # per iteration: commit, score, passed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    path: Mapped[str] = mapped_column(Text, default="")  # git repository the agents work on
    github_url: Mapped[str] = mapped_column(String(512), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    quality_commands: Mapped[list[Any]] = mapped_column(default=list)
    protected: Mapped[bool] = mapped_column(Boolean, default=False)
    manager_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Message(Base):
    """The project command window: orders, GM replies, plans, results, proposals."""

    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    goal_id: Mapped[int | None] = mapped_column(ForeignKey("goals.id", ondelete="SET NULL"), nullable=True)
    author_type: Mapped[str] = mapped_column(String(16))  # user|agent|system
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id", ondelete="SET NULL"), nullable=True)
    kind: Mapped[str] = mapped_column(String(16), default="chat")  # chat|command|plan|result|proposal|standup|error
    content: Mapped[str] = mapped_column(Text, default="")
    attachments: Mapped[list[Any]] = mapped_column(default=list)
    meta: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class Proposal(Base):
    """Follow-up work an agent raised; approved proposals become goals."""

    __tablename__ = "proposals"
    id: Mapped[int] = mapped_column(primary_key=True)
    goal_id: Mapped[int] = mapped_column(ForeignKey("goals.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id", ondelete="SET NULL"), nullable=True)
    title: Mapped[str] = mapped_column(String(256))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|approved|rejected
    decided_by: Mapped[str] = mapped_column(String(128), default="")
    spawned_goal_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Upload(Base):
    __tablename__ = "uploads"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    path: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(String(128), default="")
    size: Mapped[int] = mapped_column(Integer, default=0)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    goal_id: Mapped[int] = mapped_column(ForeignKey("goals.id"), index=True)
    iteration: Mapped[int] = mapped_column(Integer)
    key: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(64))
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    contract: Mapped[dict[str, Any]] = mapped_column(default=dict)
    depends_on: Mapped[list[Any]] = mapped_column(default=list)
    priority: Mapped[int] = mapped_column(Integer, default=5)  # lower runs first
    writes: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|running|done|failed|skipped
    output: Mapped[str] = mapped_column(Text, default="")
    gate: Mapped[dict[str, Any]] = mapped_column(default=dict)
    model_name: Mapped[str] = mapped_column(String(128), default="")
    session_id: Mapped[str] = mapped_column(String(64), default="")  # Claude Code session, for resuming
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    goal_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    model_name: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(16))
    purpose: Mapped[str] = mapped_column(String(32), default="task")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float] = mapped_column(Float, default=0.0)
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    goal_id: Mapped[int | None] = mapped_column(ForeignKey("goals.id"), nullable=True, index=True)
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Document(Base):
    """Knowledge collected by the research rooms."""

    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(String(2048), unique=True)
    title: Mapped[str] = mapped_column(Text, default="")
    text: Mapped[str] = mapped_column(Text, default="")
    source_url: Mapped[str] = mapped_column(String(2048), default="")
    collected_by: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    category: Mapped[str] = mapped_column(String(32), default="")  # kind of source (ASEAO06 §9)
    flags: Mapped[list[Any]] = mapped_column(default=list)  # prompt-injection signals (SPEC14 §9)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ResearchReport(Base):
    """A research request and the cited report the research room wrote for it (ASEAO06 §5-6)."""

    __tablename__ = "research_reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    question: Mapped[str] = mapped_column(Text)
    urls: Mapped[list[Any]] = mapped_column(default=list)
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|running|done|failed
    report: Mapped[str] = mapped_column(Text, default="")
    sources: Mapped[list[Any]] = mapped_column(default=list)
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    requested_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Source(Base):
    """A site the external-data room visits on rotation."""

    __tablename__ = "sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(String(2048), unique=True)
    depth: Mapped[int] = mapped_column(Integer, default=1)
    max_pages: Mapped[int] = mapped_column(Integer, default=20)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|active|rejected|disabled
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    device_hash: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    device_info: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    permissions: Mapped[dict[str, Any]] = mapped_column(default=dict)  # see auth.DEFAULT_PERMISSIONS
    git_name: Mapped[str] = mapped_column(String(128), default="")
    git_email: Mapped[str] = mapped_column(String(256), default="")
    git_branch: Mapped[str] = mapped_column(String(128), default="")
    git_token: Mapped[str] = mapped_column(Text, default="")  # GitHub token, encrypted (gitsync.set_token)


class UserSession(Base):
    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Setting(Base):
    """Administrator overrides of the `.env` settings (see prefs.py)."""

    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(default=dict)  # {"v": <value>}
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    level: Mapped[str] = mapped_column(String(16), default="info")  # info|success|warning|error
    title: Mapped[str] = mapped_column(String(256))
    body: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(64), default="system")
    goal_id: Mapped[int | None] = mapped_column(ForeignKey("goals.id", ondelete="SET NULL"), nullable=True)
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # None = everyone; else one user
    admin_only: Mapped[bool] = mapped_column(Boolean, default=False)
    read_by: Mapped[list[Any]] = mapped_column(default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class AuditEvent(Base):
    """Append-only, hash-chained record of administrative and security actions."""

    __tablename__ = "audit"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(64), index=True)
    target: Mapped[str] = mapped_column(String(256), default="")
    before: Mapped[dict[str, Any]] = mapped_column(default=dict)
    after: Mapped[dict[str, Any]] = mapped_column(default=dict)
    correlation_id: Mapped[str] = mapped_column(String(64), default="")
    severity: Mapped[str] = mapped_column(String(16), default="info")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    prev_hash: Mapped[str] = mapped_column(String(64), default="")
    hash: Mapped[str] = mapped_column(String(64), default="")


_engine: Engine | None = None
_factory: sessionmaker[Session] | None = None
_lock = threading.Lock()


def engine() -> Engine:
    global _engine, _factory
    with _lock:
        if _engine is None:
            url = settings().database_url
            kwargs: dict[str, Any] = {}
            if url.startswith("sqlite"):
                settings().data_dir.mkdir(parents=True, exist_ok=True)
                kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
            _engine = create_engine(url, **kwargs)
            if url.startswith("sqlite"):

                @event.listens_for(_engine, "connect")
                def _pragmas(conn, _record):  # noqa: ANN001
                    cur = conn.cursor()
                    cur.execute("PRAGMA journal_mode=WAL")
                    cur.execute("PRAGMA foreign_keys=ON")
                    cur.close()

            Base.metadata.create_all(_engine)
            _add_missing_columns(_engine)
            _factory = sessionmaker(_engine, expire_on_commit=False)
        return _engine


def _add_missing_columns(eng: Engine) -> None:
    """create_all never alters existing tables: add columns introduced since the DB was made."""
    from sqlalchemy import inspect, text

    insp = inspect(eng)
    with eng.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                ddl = col.type.compile(dialect=eng.dialect)
                default = ""
                if col.default is not None and getattr(col.default, "is_scalar", False):
                    v = col.default.arg
                    default = f" DEFAULT {int(v) if isinstance(v, bool) else repr(v) if isinstance(v, str) else v}"
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {ddl}{default}'))


@contextmanager
def session() -> Iterator[Session]:
    engine()
    assert _factory is not None
    s = _factory()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def reset_engine() -> None:
    """Drop the cached engine (used by tests that switch DATABASE_URL)."""
    global _engine, _factory
    with _lock:
        if _engine is not None:
            _engine.dispose()
        _engine = None
        _factory = None


def log_event(
    kind: str,
    message: str,
    goal_id: int | None = None,
    agent_id: int | None = None,
    s: Session | None = None,
    **data: Any,
) -> None:
    """Record an event. Pass `s` when called inside an open session (SQLite allows one writer)."""
    ev = Event(kind=kind, message=message, goal_id=goal_id, agent_id=agent_id, data=data)
    if s is not None:
        s.add(ev)
        return
    with session() as own:
        own.add(ev)
