"""Persistence: SQLAlchemy models and session handling.

SQLite under `data/` is the default so the project runs with no setup. Set
`DATABASE_URL=postgresql+psycopg://user:pass@host/db` (and `uv sync --extra postgres`)
to use PostgreSQL; the schema is the same.
"""

from __future__ import annotations

import re
import threading
from contextlib import contextmanager
from datetime import UTC, date, datetime
from typing import Any, Iterator

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
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
    # SPEC-01 S01-R-01 (organization.py): hierarchy, switch and lead, edited by administrators
    parent_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)  # a disabled room's agents are never staffed
    lead_agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    # SPEC-04 S04-R-09: what the room's agents and those of its sub-rooms may spend per UTC day; 0 = no limit
    daily_budget_usd: Mapped[float] = mapped_column(Float, default=0.0)


class PositionChange(Base):
    """Who held a position and since when (SPEC-01 S01-R-02): `room:<key>:lead` or `project:<id>:gm`, so decisions
    stay attached to the position when the agent holding it changes."""

    __tablename__ = "position_changes"
    id: Mapped[int] = mapped_column(primary_key=True)
    position: Mapped[str] = mapped_column(String(96), index=True)
    previous_agent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    new_agent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(128), default="system")
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


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
    autonomy: Mapped[str] = mapped_column(String(4), default="G3")  # G0..G4, executors.AUTONOMY (S14-R-02)
    model_pref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="idle")  # idle|busy|disabled
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    xp: Mapped[int] = mapped_column(Integer, default=0)
    level: Mapped[int] = mapped_column(Integer, default=1)
    tasks_done: Mapped[int] = mapped_column(Integer, default=0)
    tasks_failed: Mapped[int] = mapped_column(Integer, default=0)
    playbook: Mapped[str] = mapped_column(Text, default="")
    # active|paused|retired (SPEC02 §6); candidate = a recruit in its qualification run (SPEC-03 S03-R-04)
    lifecycle: Mapped[str] = mapped_column(String(16), default="active")
    playbook_version: Mapped[int] = mapped_column(Integer, default=0)
    experiment: Mapped[dict[str, Any]] = mapped_column(default=dict)  # running candidate playbook trial
    plugins: Mapped[list[Any]] = mapped_column(default=list)  # names of catalog plugins/skills it may use
    definition_version: Mapped[int] = mapped_column(Integer, default=0)  # its agent file (agentdefs.py)
    config_version: Mapped[int] = mapped_column(Integer, default=0)  # latest AgentVersion (SPEC-02 S02-R-01)
    # S02-R-03: how many goals it may work on at once, and a daily cost quota for it and its clones (0 = none)
    max_concurrent: Mapped[int] = mapped_column(Integer, default=1)
    daily_cost_quota_usd: Mapped[float] = mapped_column(Float, default=0.0)
    active_goals: Mapped[list[Any]] = mapped_column(default=list)  # one entry per task it is working on now
    health: Mapped[str] = mapped_column(String(16), default="ok")  # ok|degraded (S02-R-02)
    health_reason: Mapped[str] = mapped_column(Text, default="")
    health_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # runs before: ignored
    # S02-FR-18 automatic recovery: when it became degraded, the last probe of its model and how many failed
    degraded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recovery_probe_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recovery_probes: Mapped[int] = mapped_column(Integer, default=0)
    # SPEC-03 S03-R-04: a recruit's first tasks are always reviewed; 0 = not (or no longer) on probation
    probation_tasks_left: Mapped[int] = mapped_column(Integer, default=0)
    probation_failures: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentVersion(Base):
    """A snapshot of an agent's configuration after each change: the lifecycle history and what a rollback
    restores (SPEC-02 S02-R-01). The playbook has its own versions (PlaybookVersion)."""

    __tablename__ = "agent_versions"
    __table_args__ = (UniqueConstraint("agent_id", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict[str, Any]] = mapped_column(default=dict)
    changed_by: Mapped[str] = mapped_column(String(128), default="system")
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PlaybookVersion(Base):
    __tablename__ = "playbook_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    playbook: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class DefinitionCandidate(Base):
    """A study visit's rewrite of an agent's learned sections, applied to its definition file only by a person
    who saw the diff (agentdefs.py, G-R-19)."""

    __tablename__ = "definition_candidates"
    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"), index=True)
    sections: Mapped[dict[str, Any]] = mapped_column(default=dict)  # {"Working method": ..., "Knowledge notes": ...}
    base_version: Mapped[int] = mapped_column(Integer, default=0)  # the agent's definition_version when made
    model: Mapped[str] = mapped_column(String(128), default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|applied|discarded|superseded
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    decided_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


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


def _defect_priority(ctx: Any) -> int:
    """A new defect's priority follows its severity until an administrator changes it (S09-R-03)."""
    return {"critical": 1, "high": 2, "medium": 3, "low": 4}.get(ctx.get_current_parameters().get("severity"), 5)


class Defect(Base):
    """A failed quality-gate check, tracked until a later iteration passes it (ASEAO09 §11)."""

    __tablename__ = "defects"
    __table_args__ = (UniqueConstraint("goal_id", "check"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    goal_id: Mapped[int] = mapped_column(ForeignKey("goals.id", ondelete="CASCADE"), index=True)
    check: Mapped[str] = mapped_column(String(256))
    severity: Mapped[str] = mapped_column(String(16))  # critical|high|medium|low|info
    status: Mapped[str] = mapped_column(String(16), default="detected")
    # open: detected|reopened|assigned|fix_submitted; retested (the gate passed it again) -> closed at the merge;
    # set by an administrator: accepted_risk|deferred|duplicate|not_reproducible (S09-R-03)
    priority: Mapped[int | None] = mapped_column(Integer, default=_defect_priority)  # 1 (first) .. 5
    assignee_agent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duplicate_of: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # the last fresh-process run of the check: how (gate|retest), passed, command, commit, exit_code, duration_s
    verification: Mapped[dict[str, Any]] = mapped_column(default=dict)
    resolution: Mapped[str] = mapped_column(Text, default="")  # an administrator's reason
    history: Mapped[list[Any]] = mapped_column(default=list)  # [{at, from, to, by, note}]
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
    # S09-R-05: what the run did besides passing, read from the gate's history, the runs and refused commands:
    # compliance, tool_use, security (each a small dict); {} for runs recorded before
    dimensions: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Experiment(Base):
    """A baseline-vs-candidate comparison on a fixed eval suite (SPEC-13 S13-R-05): propose -> baseline run ->
    candidate run on the same suite and repeats -> compare (sample sizes kept) -> review -> canary -> rollout
    or rollback. Playbook candidates of the evolution loop are experiments too (evolution.py)."""

    __tablename__ = "experiments"
    id: Mapped[int] = mapped_column(primary_key=True)
    target: Mapped[str] = mapped_column(String(24))  # playbook|route|strategy (a workflow template, S04-R-13)
    agent_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)  # playbook experiments
    role: Mapped[str] = mapped_column(String(64), default="")  # route experiments; the goal kind of a strategy
    baseline_version: Mapped[str] = mapped_column(String(128), default="")  # "v3" or the route's model
    # {"playbook": text}, {"model": name} or {"kind", "base_version", "changes", "body", "reasons", "evidence"}
    candidate: Mapped[dict[str, Any]] = mapped_column(default=dict)
    hypothesis: Mapped[str] = mapped_column(Text, default="")
    dataset: Mapped[dict[str, Any]] = mapped_column(default=dict)  # suite, repeats
    results: Mapped[dict[str, Any]] = mapped_column(default=dict)  # baseline, candidate, comparison, canary
    parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)  # EL-R-07: started from
    eval_log: Mapped[list[Any]] = mapped_column(default=list)  # EL-R-07: append-only evaluation log, experiments._log
    status: Mapped[str] = mapped_column(String(16), default="proposed")
    # proposed|baseline|candidate|review|canary|rolled_out|rolled_back|rejected|failed;
    # a competition's losers end rejected (EL-R-07)
    origin: Mapped[str] = mapped_column(String(16), default="evolution")  # evolution|user
    decision: Mapped[str] = mapped_column(Text, default="")
    reviewed_by: Mapped[str] = mapped_column(String(128), default="")
    created_by: Mapped[str] = mapped_column(String(128), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class LearningPlan(Base):
    """A plan to close one capability gap of one agent (SPEC-13 S13-R-02): practice exercises, then a separate
    final evaluation, both eval-suite goals in scratch repositories, scored only by the harness (S13-R-03)."""

    __tablename__ = "learning_plans"
    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(Integer, index=True)
    capability: Mapped[str] = mapped_column(String(64))
    gap: Mapped[str] = mapped_column(String(32), default="")  # the gap category it came from, if any
    current_level: Mapped[int] = mapped_column(Integer, default=0)  # certification L0-L5
    target_level: Mapped[int] = mapped_column(Integer, default=1)
    objectives: Mapped[list[Any]] = mapped_column(default=list)
    resources: Mapped[list[Any]] = mapped_column(default=list)  # knowledge item ids
    practice_suite: Mapped[str] = mapped_column(String(128), default="")
    final_suite: Mapped[str] = mapped_column(String(128))
    repeats: Mapped[int] = mapped_column(Integer, default=1)
    pass_mark: Mapped[float] = mapped_column(Float, default=0.8)
    status: Mapped[str] = mapped_column(String(16), default="planned")  # planned|running|passed|failed|cancelled
    results: Mapped[dict[str, Any]] = mapped_column(default=dict)  # practice, final (harness summaries)
    recommendation: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(128), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class ImprovementContract(Base):
    """Verified self-evolution of the platform's own code (selfevolve.py): one capability, a fixed benchmark taken
    at creation, candidate goals measured against the baseline commit, approval, deployment, monitoring and
    rollback."""

    __tablename__ = "improvement_contracts"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(256))
    objective: Mapped[str] = mapped_column(Text, default="")
    component: Mapped[str] = mapped_column(String(32))
    layer: Mapped[str] = mapped_column(String(16), default="code")  # model|prompt|code|orchestration|tools|data|environment
    kind: Mapped[str] = mapped_column(String(16), default="algorithmic")  # behavioral|algorithmic|architectural
    # the research proposal (§5): limitation, solution, affected files, benefits, risks, tests, targets
    proposal: Mapped[dict[str, Any]] = mapped_column(default=dict)
    repo_path: Mapped[str] = mapped_column(Text, default="")
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    base_branch: Mapped[str] = mapped_column(String(128), default="")
    allowed_paths: Mapped[list[Any]] = mapped_column(default=list)
    benchmark: Mapped[dict[str, Any]] = mapped_column(default=dict)  # manifest + the script's text and sha256
    acceptance: Mapped[dict[str, Any]] = mapped_column(default=dict)  # min_score, max_regression (fixed at creation)
    baseline_commit: Mapped[str] = mapped_column(String(64), default="")
    baseline: Mapped[dict[str, Any]] = mapped_column(default=dict)  # metrics, classes, runs
    candidates: Mapped[list[Any]] = mapped_column(default=list)  # goal_id, decision_id, commit, report, verdict
    status: Mapped[str] = mapped_column(String(16), default="draft")
    # draft|baseline|building|verifying|verified|rejected|failed|monitoring|stable|rolled_back|cancelled
    deployment: Mapped[dict[str, Any]] = mapped_column(default=dict)  # goal_id, sha, at, actor, check
    monitor: Mapped[dict[str, Any]] = mapped_column(default=dict)  # post-deploy benchmark, alerts
    rollback_sha: Mapped[str] = mapped_column(String(64), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Relationship(Base):
    __tablename__ = "relationships"
    __table_args__ = (UniqueConstraint("a_id", "b_id", "kind"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    a_id: Mapped[int] = mapped_column(ForeignKey("agents.id"), index=True)
    b_id: Mapped[int] = mapped_column(ForeignKey("agents.id"), index=True)
    # network.KINDS (SPEC-12 S12-R-01); symmetric kinds store the lower id in a_id, directed ones read
    # "a <kind> b" (a reviewed_by b, a mentored_by b, a depends_on b, a handover_to b, a supervises b)
    kind: Mapped[str] = mapped_column(String(32))
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    evidence: Mapped[list[Any]] = mapped_column(default=list)  # goal/task/plan ids that already changed the weight
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|expired (expired: not used)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Lesson(Base):
    __tablename__ = "lessons"
    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"), index=True)
    goal_id: Mapped[int | None] = mapped_column(ForeignKey("goals.id"), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(String(16))  # success|failure|research|analysis (rca.py)|imported (REQ-005)
    # a knowledge item (SPEC-11 S11-R-01): kind, evidence class, provenance, review status, classification
    kind: Mapped[str] = mapped_column(String(16), default="episodic")
    # episodic|semantic|procedural|architectural|research|operational|capability|organizational|negative
    evidence_class: Mapped[str] = mapped_column(String(12), default="MEASURED")
    # FACT|MEASURED|INFERENCE|HYPOTHESIS|ASSUMPTION|PREDICTION|UNKNOWN
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    task_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sources: Mapped[list[Any]] = mapped_column(default=list)  # goal:, task:, commit:, url: references
    technologies: Mapped[list[Any]] = mapped_column(default=list)
    status: Mapped[str] = mapped_column(String(12), default="approved")
    # draft|approved|rejected|quarantined|superseded|expired
    status_reason: Mapped[str] = mapped_column(Text, default="")
    reviewed_by: Mapped[str] = mapped_column(String(128), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    supersedes_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    review_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    classification: Mapped[str] = mapped_column(String(16), default="internal")
    # the structured experience report (S11-R-02): objective, context_refs, actions, outcome, evidence_refs,
    # failures, successful_approach, reusable_lesson, limitations; observable facts only
    report: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Mentoring(Base):
    """A mentor helping a learner through one learning plan (SPEC-12 S12-R-04). The mentor reviews the practice
    results; certification stays the final evaluation's harness verdict (SPEC-13 S13-R-03)."""

    __tablename__ = "mentorings"
    id: Mapped[int] = mapped_column(primary_key=True)
    mentor_id: Mapped[int] = mapped_column(Integer, index=True)
    learner_id: Mapped[int] = mapped_column(Integer, index=True)
    skill: Mapped[str] = mapped_column(String(64))
    plan_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    objectives: Mapped[list[Any]] = mapped_column(default=list)
    gap_assessment: Mapped[dict[str, Any]] = mapped_column(default=dict)
    materials: Mapped[list[Any]] = mapped_column(default=list)  # knowledge item ids
    exercises: Mapped[dict[str, Any]] = mapped_column(default=dict)  # the practice suite's harness summary
    feedback: Mapped[list[Any]] = mapped_column(default=list)  # {by, text, at, source}
    final_evaluation: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|completed|failed|cancelled
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class KgEdge(Base):
    """An edge of the knowledge graph (SPEC-11 S11-R-07); for now CONTRADICTS and SUPERSEDES between knowledge
    items. `evidence` holds why the edge exists and, for a contradiction, its review state and resolution."""

    __tablename__ = "kg_edges"
    id: Mapped[int] = mapped_column(primary_key=True)
    src_type: Mapped[str] = mapped_column(String(16))  # agent|project|goal|task|knowledge|source|decision|...
    src_id: Mapped[str] = mapped_column(String(64), index=True)
    rel: Mapped[str] = mapped_column(String(24), index=True)  # CONTRADICTS|SUPERSEDES|...
    dst_type: Mapped[str] = mapped_column(String(16))
    dst_id: Mapped[str] = mapped_column(String(64), index=True)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    evidence: Mapped[dict[str, Any]] = mapped_column(default=dict)
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
    session_id: Mapped[str] = mapped_column(String(64), default="")  # claude_code: every run forks this session
    # claude_code: its own Claude Code sign-in in data/claude-accounts/<account>/ (CLAUDE_CONFIG_DIR); "" = the host's
    account: Mapped[str] = mapped_column(String(64), default="")
    # the highest classification it may receive (SPEC-14 S14-R-05); "" = by locality (router.clearance)
    clearance: Mapped[str] = mapped_column(String(16), default="")
    # G-R-18: what routing and data handling depend on, as a person verified it (router.TERMS):
    # billing, context_window, tool_calling, trains_on_prompts, commercial_use, terms_url, verified_at
    terms: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # an API key entered on the Models page, encrypted (transport.encrypt_text); never sent back, only its hint
    api_key_enc: Mapped[str] = mapped_column(Text, default="")
    # the last Test: ok, error, checked_at and what the provider reports (plan or key usage, reset time)
    status: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # a CLI model: whose sign-in it was registered with (method, login or email, plan, at); never a token
    signin: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # a plan or quota limit the provider reported on a run ("You've hit your session limit · resets 9:50pm"): the
    # router skips the model until then and the role's fallbacks take the work (router.mark_limited)
    limited_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    limited_reason: Mapped[str] = mapped_column(Text, default="")


class Route(Base):
    __tablename__ = "routes"
    role: Mapped[str] = mapped_column(String(64), primary_key=True)
    model_name: Mapped[str] = mapped_column(String(128))
    escalation: Mapped[str] = mapped_column(String(128), default="")  # model for retries after a failed gate
    # models that take the role's work, in order, when the route's model is at its usage limit or fails
    fallback: Mapped[str] = mapped_column(String(512), default="")  # comma-separated model names


class Goal(Base):
    __tablename__ = "goals"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(256))
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="queued")
    # queued|planning|running|recovering|paused|awaiting_approval|approved|rejected|failed|cancelled (workflow.py)
    repo_path: Mapped[str] = mapped_column(Text, default="")
    base_branch: Mapped[str] = mapped_column(String(128), default="")
    branch: Mapped[str] = mapped_column(String(128), default="")
    worktree: Mapped[str] = mapped_column(Text, default="")
    iteration: Mapped[int] = mapped_column(Integer, default=0)
    max_iterations: Mapped[int] = mapped_column(Integer, default=3)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # REQ-003, reported only
    urls: Mapped[list[Any]] = mapped_column(default=list)
    quality_commands: Mapped[list[Any]] = mapped_column(default=list)
    result: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    origin: Mapped[str] = mapped_column(String(16), default="user")  # user|proposal|eval|improvement
    parent_goal_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attachments: Mapped[list[Any]] = mapped_column(default=list)
    allowed_paths: Mapped[list[Any]] = mapped_column(default=list)  # globs; empty = anywhere
    protect_tests: Mapped[bool] = mapped_column(Boolean, default=False)
    baseline: Mapped[dict[str, Any]] = mapped_column(default=dict)  # command -> {"passed", "detail"}
    history: Mapped[list[Any]] = mapped_column(default=list)  # per iteration: commit, score, passed
    # what a restart needs to resume the current iteration without re-running finished tasks (S04-R-05)
    checkpoint: Mapped[dict[str, Any]] = mapped_column(default=dict)
    permits: Mapped[list[Any]] = mapped_column(default=list)  # change boundaries lifted: dependencies, config
    # the cost the order or the planner expected; spending twice that pauses the goal (S14-R-03). 0 = none
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    # the goal's own budget (S04-R-09, budgets.py); 0 = the setting (goal_budget_usd, goal_token_budget,
    # goal_agent_limit). A child goal's budget is capped by its parent's remainder when it is created.
    budget_usd: Mapped[float] = mapped_column(Float, default=0.0)
    budget_tokens: Mapped[int] = mapped_column(Integer, default=0)
    max_active_agents: Mapped[int] = mapped_column(Integer, default=0)  # tasks (agents) working at once
    # the project's conversation lane the order came from; its reports go there and its goals run in order
    topic: Mapped[str] = mapped_column(String(64), default="general")
    requirement_keys: Mapped[list[Any]] = mapped_column(default=list)  # S08-R-01: project requirements it implements
    # S04-R-11: bugfix|feature|refactor|research|docs ("" until the order or the planner names one) and the
    # workflow template (strategy) version it runs under, a snapshot with its source (strategies.py)
    kind: Mapped[str] = mapped_column(String(16), default="")
    strategy: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # S09-R-02: risk, reasons, mandatory gate stages, reviewers, sensitive paths, files with earlier failures
    test_strategy: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # the order's classification (its attachments, its project at creation); the project's current one may raise it
    classification: Mapped[str] = mapped_column(String(16), default="internal")
    # eval goals only (SPEC-13): experiment_id + side, role -> model overrides, trainee_id (the agent staffed for
    # its role), plan_id, hide_checks (a final evaluation: its checks never reach a prompt or the worktree)
    trial: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # cached mission.report labels of a finished goal; empty = not cached (mission.label_counts)
    label_counts: Mapped[dict[str, Any]] = mapped_column(default=dict)
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
    # public|internal|confidential|restricted|secret (SPEC-14 S14-R-05): what may leave the project
    classification: Mapped[str] = mapped_column(String(16), default="internal")
    # registration fields and lifecycle (ASEAO05 S05-R-06)
    stack: Mapped[list[Any]] = mapped_column(default=list)  # technologies, e.g. ["python", "fastapi"]
    required_skills: Mapped[list[Any]] = mapped_column(default=list)
    rooms: Mapped[list[Any]] = mapped_column(default=list)  # participating rooms; empty = every room
    budget_usd: Mapped[float] = mapped_column(Float, default=0.0)  # caps the cost of all its goals; 0 = none
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    priority: Mapped[str] = mapped_column(String(16), default="normal")  # low|normal|high|critical
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|paused|archived|cancelled|completed
    status_reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    # its own, stricter rules (SPEC-01 S01-R-05, policies.PROJECT_KEYS): {"goal_budget_usd": 2.0, ...}
    policy: Mapped[dict[str, Any]] = mapped_column(default=dict)


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
    topic: Mapped[str] = mapped_column(String(64), default="general", index=True)  # conversation lane (S05-R-07)
    content: Mapped[str] = mapped_column(Text, default="")
    attachments: Mapped[list[Any]] = mapped_column(default=list)
    meta: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class Topic(Base):
    """A conversation lane of a project's command window (ASEAO05 S05-R-07). `general` exists implicitly."""

    __tablename__ = "topics"
    __table_args__ = (UniqueConstraint("project_id", "name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text, default="")
    agent_ids: Mapped[list[Any]] = mapped_column(default=list)  # agents assigned to the lane
    summary: Mapped[str] = mapped_column(Text, default="")  # the latest /summary
    summary_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


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
    tag: Mapped[str] = mapped_column(String(16), default="")  # research = from an approved report (S06-R-06)
    report_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # ASEAO10 S10-R-01: follow_up (becomes a goal) | product | new_project (becomes a project, only with its case)
    kind: Mapped[str] = mapped_column(String(16), default="follow_up")
    case: Mapped[dict[str, Any]] = mapped_column(default=dict)  # projects.CASE_FIELDS
    spawned_project_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ProjectHandover(Base):
    """What a project's new General Manager receives when the old one is replaced (ASEAO05 S05-R-09): the open
    goals, open blockers and open action items at that moment. Never rewritten."""

    __tablename__ = "project_handovers"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    from_agent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    to_agent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    package: Mapped[dict[str, Any]] = mapped_column(default=dict)
    actor: Mapped[str] = mapped_column(String(128), default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Milestone(Base):
    """A project milestone with deliverables, required goals and acceptance criteria (management.py, ASEAO05 §7)."""

    __tablename__ = "milestones"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(256))
    description: Mapped[str] = mapped_column(Text, default="")
    target_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deliverables: Mapped[list[Any]] = mapped_column(default=list)
    goal_ids: Mapped[list[Any]] = mapped_column(default=list)  # goals that must be approved first
    # [{"kind": "command"|"manual", "text", "mandatory", "met", "detail", "checked_at"}]
    criteria: Mapped[list[Any]] = mapped_column(default=list)
    owner_agent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open|completed|cancelled
    overdue_notified: Mapped[bool] = mapped_column(Boolean, default=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Schedule(Base):
    """A goal a project orders on a schedule (schedules.py, FW-R-04, after OpenHands automations)."""

    __tablename__ = "schedules"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    details: Mapped[str] = mapped_column(Text, default="")
    checks: Mapped[list[Any]] = mapped_column(default=list)  # quality commands; empty = the project's
    kind: Mapped[str] = mapped_column(String(16), default="")  # workflow template; "" = the planner picks
    interval: Mapped[str] = mapped_column(String(32))  # hourly | daily HH:MM | weekly <day> HH:MM (UTC)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_goal_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")  # what the last due run did (created #n, skipped: why)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Risk(Base):
    """A risk or blocker of a project (management.py, ASEAO05 §8). Automatic ones carry the `cause` that opened
    them and close when it no longer holds."""

    __tablename__ = "risks"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    goal_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(8), default="risk")  # risk|blocker
    title: Mapped[str] = mapped_column(String(256))
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(24), default="technical")
    severity: Mapped[str] = mapped_column(String(8), default="medium")  # low|medium|high|critical
    probability: Mapped[float | None] = mapped_column(Float, nullable=True)  # risks only, 0..1
    impact: Mapped[str] = mapped_column(Text, default="")
    mitigation: Mapped[str] = mapped_column(Text, default="")
    owner_agent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="open")  # open|mitigated|resolved|closed
    cause: Mapped[str] = mapped_column(String(128), default="", index=True)  # "" = entered by hand
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Insight(Base):
    """Something an agent discovered that may be worth acting on (management.py, ASEAO05 §9)."""

    __tablename__ = "insights"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    goal_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    task_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    agent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(256))
    description: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[list[Any]] = mapped_column(default=list)
    sources: Mapped[list[Any]] = mapped_column(default=list)
    expected_benefit: Mapped[str] = mapped_column(Text, default="")
    estimated_cost: Mapped[str] = mapped_column(String(128), default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.5)  # the agent's estimate
    related_tasks: Mapped[list[Any]] = mapped_column(default=list)
    status: Mapped[str] = mapped_column(String(12), default="recorded")
    # recorded|analyzed|reviewed|accepted|rejected|implemented|evaluated
    decision: Mapped[str] = mapped_column(Text, default="")
    decided_by: Mapped[str] = mapped_column(String(128), default="")
    result: Mapped[str] = mapped_column(Text, default="")
    knowledge_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    proposal_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ActionItem(Base):
    """Upcoming work of a project (management.py, ASEAO05 §10). Generated ones carry the `source` they came from
    (proposal:, defect:, risk:, criterion:) and close when it is settled."""

    __tablename__ = "action_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(256))
    description: Mapped[str] = mapped_column(Text, default="")
    responsible_agent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=3)  # 1 highest .. 5 lowest
    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    depends_on: Mapped[list[Any]] = mapped_column(default=list)  # action item ids
    expected_output: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(12), default="open")  # open|in_progress|done|dropped
    goal_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    milestone_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(64), default="", index=True)  # "" = entered by hand
    overdue_notified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Upload(Base):
    __tablename__ = "uploads"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    path: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(String(128), default="")
    size: Mapped[int] = mapped_column(Integer, default=0)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    classification: Mapped[str] = mapped_column(String(16), default="internal")  # that of the order it came with
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
    # the task's lifecycle beside its execution status (ASEAO05 S05-R-08, workflow.TASK_STAGES) and its changes
    stage: Mapped[str] = mapped_column(String(16), default="created")
    stage_log: Mapped[list[Any]] = mapped_column(default=list)
    output: Mapped[str] = mapped_column(Text, default="")
    gate: Mapped[dict[str, Any]] = mapped_column(default=dict)
    model_name: Mapped[str] = mapped_column(String(128), default="")
    session_id: Mapped[str] = mapped_column(String(64), default="")  # Claude Code session, for resuming
    # EL-R-06: the critic runs on this task's diff, {attempt, model, provider, verdict, points, reason, run_id};
    # NULL on rows from before the column (readers use `t.critic or []`)
    critic: Mapped[list[Any]] = mapped_column(default=list)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    goal_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # The signed-in user whose request made the run (S04-R-09); NULL for agent work. No foreign key: a migrated
    # database gets the column from _add_missing_columns, which adds no constraints.
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    model_name: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(16))
    purpose: Mapped[str] = mapped_column(String(32), default="task")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float] = mapped_column(Float, default=0.0)
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str] = mapped_column(Text, default="")
    # what the tool gateway granted (SPEC-14 S14-R-06..R-08): isolation L0..L2, the tools it was given, the
    # permissions it used, what was withheld, and the tool calls the CLI refused
    isolation: Mapped[str] = mapped_column(String(4), default="")
    tools: Mapped[list[Any]] = mapped_column(default=list)
    permissions: Mapped[list[Any]] = mapped_column(default=list)
    dropped: Mapped[list[Any]] = mapped_column(default=list)
    denied_tools: Mapped[list[Any]] = mapped_column(default=list)
    # every call a CLI run made (S04-R-12, S14-R-08): {tool, input (short, redacted), ok}, at most 200
    tool_calls: Mapped[list[Any]] = mapped_column(default=list)
    # which model did the work and why (verification phase 2): requested (the agent's assigned model: its own
    # model, else its role's route), model_id, provider, base_url, route (why this one: preference, route,
    # fallback, escalation), served (the model the provider itself reported; "" when it reports none)
    trace: Mapped[dict[str, Any]] = mapped_column(default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    goal_id: Mapped[int | None] = mapped_column(ForeignKey("goals.id"), nullable=True, index=True)
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # the goal iteration the event belongs to (gate events, S04-R-12); NULL on rows written before the column existed
    iteration: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


_ITER = re.compile(r"iteration (\d+):")


def _as_int(v: Any) -> int | None:
    """An int-like value as int (7, "7"), else None; a bool is not an iteration."""
    if isinstance(v, bool):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def event_iteration(e: Event) -> int | None:
    """An event's iteration: its column, else `data["iteration"]`, else the one in its message (older rows)."""
    if e.iteration is not None:
        return e.iteration
    it = _as_int((e.data or {}).get("iteration"))
    if it is not None:
        return it
    m = _ITER.search(e.message or "")
    return int(m.group(1)) if m else None


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
    # change detection (ASEAO06 S06-R-02): a refetch with another hash is a new version, the old one is kept
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    # provenance (G-R-10): the publisher's date and licence, from a feed entry or the page's meta tags
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    license: Mapped[str] = mapped_column(String(256), default="")
    # the language the page or feed declares (S06-FR-04): primary subtag, lower case; '' when it declares none
    language: Mapped[str] = mapped_column(String(16), default="")


class DocumentVersion(Base):
    """An earlier version of a collected page, kept when a refetch found it changed (S06-R-02)."""

    __tablename__ = "document_versions"
    __table_args__ = (UniqueConstraint("document_id", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    title: Mapped[str] = mapped_column(Text, default="")
    text: Mapped[str] = mapped_column(Text, default="")
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
    # the request's scope and terms (ASEAO06 S06-R-01)
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    goal_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)  # its report goes on the blackboard
    keywords: Mapped[list[Any]] = mapped_column(default=list)
    source_preferences: Mapped[str] = mapped_column(String(16), default="any")  # official|academic|open_source|any
    priority: Mapped[str] = mapped_column(String(16), default="normal")  # low|normal|high|critical
    budget_usd: Mapped[float] = mapped_column(Float, default=0.0)  # model cost over all its runs; 0 = no limit
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # not run after it
    recurring: Mapped[str] = mapped_column(String(16), default="")  # hourly|daily|weekly|monthly, empty = once
    # filters on the material (S06-FR-04): publication dates (inclusive, UTC days) and primary language subtags,
    # empty = any. Migrated rows read languages as NULL.
    published_after: Mapped[date | None] = mapped_column(Date, nullable=True)
    published_before: Mapped[date | None] = mapped_column(Date, nullable=True)
    languages: Mapped[list[Any]] = mapped_column(default=list)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    runs: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    # its review (ASEAO06 S06-R-03/R-04): draft -> in_review -> approved|rejected; "" = written before reviews.
    # Only an approved report reaches knowledge, the search and its goal.
    review: Mapped[str] = mapped_column(String(16), default="")
    claims: Mapped[list[Any]] = mapped_column(default=list)  # [{n, claim, evidence: [...], status, review}]
    approved_report: Mapped[str] = mapped_column(Text, default="")  # the report with only the claims that held
    reviewer_agent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reviewed_by: Mapped[str] = mapped_column(String(128), default="")
    review_provider: Mapped[str] = mapped_column(String(32), default="")  # the reviewer's model family
    review_note: Mapped[str] = mapped_column(Text, default="")
    # the research lead's own tool loop (research_agent.gather): each round's plan, refused tasks and tool results
    tool_steps: Mapped[list[Any]] = mapped_column(default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Hypothesis(Base):
    """A statement to test (ASEAO07 S07-R-03): why it is believed, the evidence for and against, how it is tested,
    what was expected and what happened. proposed -> investigating -> testing -> supported|rejected|inconclusive
    -> archived (hypotheses.py). An experiment's hypothesis is one of these (SPEC-13)."""

    __tablename__ = "hypotheses"
    id: Mapped[int] = mapped_column(primary_key=True)
    statement: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text, default="")
    supporting: Mapped[list[Any]] = mapped_column(default=list)  # [{"type", "id", "note", "by", "at"}]
    contradicting: Mapped[list[Any]] = mapped_column(default=list)
    test_method: Mapped[str] = mapped_column(Text, default="")
    expected_outcome: Mapped[str] = mapped_column(Text, default="")
    actual_outcome: Mapped[str] = mapped_column(Text, default="")
    test_result: Mapped[dict[str, Any]] = mapped_column(default=dict)  # {"source", "passed", ...}; empty = untested
    knowledge_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # the item its conclusion became
    status: Mapped[str] = mapped_column(String(16), default="proposed", index=True)
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    goal_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    experiment_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    agent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[str] = mapped_column(String(128), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Recruitment(Base):
    """A recruitment request (SPEC-03 S03-R-03): a task needed a role nobody holds. The job description (versioned in
    `history`), the capability gap, the decision reuse|clone|recruit with its reason, the approval (S03-R-05) and the
    agent it produced. pending -> approved | rejected | cancelled; with `qualify_recruits` a new recruit is first
    `qualifying` (S03-R-04: a candidate runs an eval suite, then pending or approved, or rejected). recruitment.py."""

    __tablename__ = "recruitments"
    id: Mapped[int] = mapped_column(primary_key=True)
    goal_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    task_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    requested_role: Mapped[str] = mapped_column(String(64), index=True)
    required_skills: Mapped[list[Any]] = mapped_column(default=list)  # skills nobody holds enough of (gap > 0)
    preferred_skills: Mapped[list[Any]] = mapped_column(default=list)  # skills of the task the workforce has
    job_description: Mapped[dict[str, Any]] = mapped_column(default=dict)
    jd_version: Mapped[int] = mapped_column(Integer, default=1)
    history: Mapped[list[Any]] = mapped_column(default=list)  # earlier job descriptions [{version, job_description, ...}]
    template: Mapped[str] = mapped_column(String(64), default="")  # the org.ROLES template it was built from
    gap: Mapped[dict[str, Any]] = mapped_column(default=dict)  # S03-R-01 per skill: required, available, gap
    decision: Mapped[str] = mapped_column(String(16), default="recruit")  # reuse|clone|recruit
    reason: Mapped[str] = mapped_column(Text, default="")
    lifecycle: Mapped[str] = mapped_column(String(16), default="permanent")  # temporary|permanent
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    pending_permissions: Mapped[list[Any]] = mapped_column(default=list)  # shell/network for a new role (S03-R-05)
    agent_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    evaluation_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    # S03-R-04 qualification run: {suite, status queued|running|passed|failed|skipped, label, pass_rate, runs, ...}
    qualification: Mapped[dict[str, Any]] = mapped_column(default=dict)
    source: Mapped[str] = mapped_column(String(16), default="")  # "" (staffing) | marketplace (REQ-002)
    decided_by: Mapped[str] = mapped_column(String(128), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentImport(Base):
    """REQ-002 marketplace: an external agent package fetched as untrusted data into data/marketplace/<id>/src,
    converted to a manifest, screened, qualified as a recruitment candidate and decided by an administrator."""

    __tablename__ = "agent_imports"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(8), default="url")  # url|upload
    source: Mapped[str] = mapped_column(Text, default="")  # the URL or the uploaded file's name
    source_sha: Mapped[str] = mapped_column(String(80), default="")  # commit sha (url) or sha256 (upload)
    manifest: Mapped[dict[str, Any]] = mapped_column(default=dict)
    licence: Mapped[str] = mapped_column(String(64), default="unknown")
    author: Mapped[str] = mapped_column(String(128), default="")
    version: Mapped[str] = mapped_column(String(32), default="")
    # fetched|converted|qualifying|qualified|failed|approved|rejected
    status: Mapped[str] = mapped_column(String(16), default="fetched", index=True)
    screen: Mapped[dict[str, Any]] = mapped_column(default=dict)  # findings and block (secret values never stored)
    ceiling: Mapped[dict[str, Any]] = mapped_column(default=dict)  # permissions/autonomy the candidate may hold
    eval: Mapped[dict[str, Any]] = mapped_column(default=dict)  # the qualification result with its dimensions
    recruitment_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    agent_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    created_by: Mapped[str] = mapped_column(String(128), default="")
    decided_by: Mapped[str] = mapped_column(String(128), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Kpi(Base):
    """A department KPI (SPEC-01 FR-08, kpis.py): definition, a formula from `kpis.METRICS` with its data source,
    target, direction, measurement interval and the responsible room; values are kept in `kpi_values`."""

    __tablename__ = "kpis"
    id: Mapped[int] = mapped_column(primary_key=True)
    room_key: Mapped[str] = mapped_column(String(64), index=True)  # the responsible department
    name: Mapped[str] = mapped_column(String(128))
    definition: Mapped[str] = mapped_column(Text, default="")
    metric: Mapped[str] = mapped_column(String(48))  # kpis.METRICS key: the formula and its data source
    target: Mapped[float | None] = mapped_column(Float, nullable=True)
    direction: Mapped[str] = mapped_column(String(8), default="higher")  # higher|lower is better
    interval: Mapped[str] = mapped_column(String(16), default="weekly")  # daily|weekly|monthly
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str] = mapped_column(String(128), default="system")
    last_measured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class KpiValue(Base):
    """One measurement of a KPI over a period, with the target in force then and the records it counted."""

    __tablename__ = "kpi_values"
    id: Mapped[int] = mapped_column(primary_key=True)
    kpi_id: Mapped[int] = mapped_column(ForeignKey("kpis.id", ondelete="CASCADE"), index=True)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    value: Mapped[float | None] = mapped_column(Float, nullable=True)  # None: nothing to measure in the period
    samples: Mapped[int] = mapped_column(Integer, default=0)
    target: Mapped[float | None] = mapped_column(Float, nullable=True)
    met: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    evidence: Mapped[dict[str, Any]] = mapped_column(default=dict)  # {"tasks": [ids], ...}, capped
    measured_by: Mapped[str] = mapped_column(String(128), default="system")
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Requirement(Base):
    """A project requirement (ASEAO08 S08-R-01): key, text, kind, acceptance criteria, a version with the earlier
    ones in `history`, and status draft -> approved -> implemented -> verified (retired from any). requirements.py."""

    __tablename__ = "requirements"
    __table_args__ = (UniqueConstraint("project_id", "key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(Integer, index=True)
    key: Mapped[str] = mapped_column(String(32))  # REQ-001, or the document's own ID (S07-R-01)
    text: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16), default="functional")  # functional|non_functional|story|use_case
    acceptance: Mapped[list[Any]] = mapped_column(default=list)
    # S08-FR-03 template of a story {actors, want, benefit} or a use case {actors, preconditions, main_flow,
    # alternatives, postconditions}; {} for the other kinds
    details: Mapped[dict[str, Any]] = mapped_column(default=dict)
    subject: Mapped[str] = mapped_column(String(128), default="")  # what it is about, for conflict detection
    version: Mapped[int] = mapped_column(Integer, default=1)
    history: Mapped[list[Any]] = mapped_column(default=list)  # earlier versions [{version, text, kind, acceptance, ...}]
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    source: Mapped[str] = mapped_column(String(256), default="")  # message, document or proposal it came from
    conflicts: Mapped[list[Any]] = mapped_column(default=list)  # [{"key", "decision_id"}]
    created_by: Mapped[str] = mapped_column(String(128), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class RequirementLink(Base):
    """Traceability (S08-R-01): a task, check, merge or decision that worked on one version of a requirement."""

    __tablename__ = "requirement_links"
    id: Mapped[int] = mapped_column(primary_key=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("requirements.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    kind: Mapped[str] = mapped_column(String(16))  # goal|task|check|merge|decision
    ref: Mapped[str] = mapped_column(String(512), default="")  # task:5, the check's command, the merge commit
    goal_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    iteration: Mapped[int] = mapped_column(Integer, default=0)
    passed: Mapped[bool] = mapped_column(Boolean, default=True)
    detail: Mapped[str] = mapped_column(Text, default="")
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
    # monitoring rules (S06-R-02): hourly|daily|weekly|monthly|manual, "" = the global rotation interval
    interval: Mapped[str] = mapped_column(String(16), default="")
    subject: Mapped[str] = mapped_column(Text, default="")  # words a change must mention to be an event; "" = any
    notify_room: Mapped[str] = mapped_column(String(64), default="")  # whose agents hear of a relevant change
    not_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # Retry-After


class ResearchShift(Base):
    """One agent's shift on the research rotation (SPEC-12 S12-R-05, ASEAO06 S06-R-05). `state` holds the shift's
    objective, visited sources, findings, open questions and known problems; the next shift starts from it, so an
    agent can be replaced without losing them. `handover` is the package this shift received."""

    __tablename__ = "research_shifts"
    id: Mapped[int] = mapped_column(primary_key=True)
    room_key: Mapped[str] = mapped_column(String(64), default="rotation")
    phase: Mapped[str] = mapped_column(String(16))  # acquisition|verification|analysis|organisation|review
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|handed_over|replaced
    state: Mapped[dict[str, Any]] = mapped_column(default=dict)
    handover: Mapped[dict[str, Any]] = mapped_column(default=dict)
    previous_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class A2AClient(Base):
    """An outside caller allowed to give the company tasks over A2A (G-R-06). Only the token's SHA-256 is kept.
    Its tasks become goals of `project_id`, created on behalf of the administrator who issued it."""

    __tablename__ = "a2a_clients"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    clearance: Mapped[str] = mapped_column(String(16), default="internal")  # the highest level its answers may carry
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class A2ATask(Base):
    """An A2A task and the goal it became; only the client that sent it can read or cancel it."""

    __tablename__ = "a2a_tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("a2a_clients.id"), index=True)
    goal_id: Mapped[int] = mapped_column(Integer, index=True)
    context_id: Mapped[str] = mapped_column(String(128), index=True)
    history: Mapped[list[Any]] = mapped_column(default=list)  # the caller's messages
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


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
    # REQ-003: the dashboard this user sees: simple|advanced; "" (accounts from before it existed) = advanced
    ui_mode: Mapped[str] = mapped_column(String(16), default="")


class UserSession(Base):
    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Delegation(Base):
    """A right an administrator delegated to a user for one project until an expiry (SPEC-01 S01-R-03, policies.py)."""

    __tablename__ = "delegations"
    id: Mapped[int] = mapped_column(primary_key=True)
    permission: Mapped[str] = mapped_column(String(32))  # policies.DELEGABLE
    grantee_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    granted_by: Mapped[str] = mapped_column(String(128))
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class PolicyVersion(Base):
    """A version of the governance settings of a scope: `company` or `project:<id>` (SPEC-01 S01-R-05)."""

    __tablename__ = "policy_versions"
    __table_args__ = (UniqueConstraint("scope", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(String(32), index=True)
    version: Mapped[int] = mapped_column(Integer)
    value: Mapped[dict[str, Any]] = mapped_column(default=dict)  # {"overrides": {...}, "values": {...}}
    author: Mapped[str] = mapped_column(String(128))
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Strategy(Base):
    """A version of the workflow template (orchestration strategy) of one goal kind (SPEC-04 S04-R-11, §20.4): roles,
    default checks, iteration limit, execution mode and review policy. strategies.py; old versions are kept."""

    __tablename__ = "strategies"
    __table_args__ = (UniqueConstraint("kind", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    version: Mapped[int] = mapped_column(Integer)
    body: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(16), default="approved")  # approved|retired
    author: Mapped[str] = mapped_column(String(128), default="system")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class GoalMember(Base):
    """A change of a goal's team (SPEC-04 S04-R-11, §8.4): an agent joined (first staffed), was added or released, or
    a role was requested (its recruitment waits). Rows are never changed, so earlier assignments stay. teams.py."""

    __tablename__ = "goal_members"
    id: Mapped[int] = mapped_column(primary_key=True)
    goal_id: Mapped[int] = mapped_column(Integer, index=True)
    agent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    role: Mapped[str] = mapped_column(String(64), default="")
    action: Mapped[str] = mapped_column(String(16))  # joined|added|released|requested
    reason: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(128), default="system")
    iteration: Mapped[int] = mapped_column(Integer, default=0)
    task_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Setting(Base):
    """Administrator overrides of the `.env` settings (see prefs.py)."""

    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(default=dict)  # {"v": <value>}
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Machine(Base):
    """A virtual machine on the host's KVM (machines.py): its settings and whether it should run."""

    __tablename__ = "machines"
    name: Mapped[str] = mapped_column(String(30), primary_key=True)
    settings: Mapped[dict[str, Any]] = mapped_column(default=dict)  # os, url, cores, ram, disk, username, language
    running: Mapped[bool] = mapped_column(Boolean, default=True)  # wanted: started or stopped by an administrator
    change: Mapped[str] = mapped_column(String(200), default="")  # who made or last changed it, and what
    created_by: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class McpServer(Base):
    """An MCP server agents may use (mcp.py): stdio command or Streamable HTTP URL.

    Header and environment *values* are secrets, stored encrypted in `secrets`; the API shows names only.
    """

    __tablename__ = "mcp_servers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    slug: Mapped[str] = mapped_column(String(32), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    transport: Mapped[str] = mapped_column(String(8), default="stdio")  # stdio|http
    command: Mapped[str] = mapped_column(String(256), default="")
    args: Mapped[list[Any]] = mapped_column(default=list)
    url: Mapped[str] = mapped_column(String(500), default="")
    secrets: Mapped[str] = mapped_column(Text, default="")  # encrypted {"env": {...}, "headers": {...}}
    secret_names: Mapped[dict[str, Any]] = mapped_column(default=dict)  # {"env": [names], "headers": [names]}
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    everyone: Mapped[bool] = mapped_column(Boolean, default=False)  # every agent may use it (else only granted ones; a new server: none)
    tools: Mapped[list[Any]] = mapped_column(default=list)  # [{name, description, read_only, allowed}]
    status: Mapped[str] = mapped_column(String(16), default="untested")  # untested|ok|error
    last_error: Mapped[str] = mapped_column(Text, default="")
    tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class McpGrant(Base):
    """Which agent may use which MCP server (servers not open to every agent)."""

    __tablename__ = "mcp_grants"
    __table_args__ = (UniqueConstraint("server_id", "agent_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("mcp_servers.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)


class McpCall(Base):
    """Every tool call made through the catalog (test calls from the dashboard included)."""

    __tablename__ = "mcp_calls"
    id: Mapped[int] = mapped_column(primary_key=True)
    server_id: Mapped[int] = mapped_column(Integer, index=True)
    agent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actor: Mapped[str] = mapped_column(String(128), default="")
    tool: Mapped[str] = mapped_column(String(128))
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class BridgeToken(Base):
    """A per-run bearer token of the bridge MCP server (EL-R-09), bound to (goal, task, agent). Only its SHA-256 is kept."""

    __tablename__ = "bridge_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    goal_id: Mapped[int] = mapped_column(Integer, index=True)
    task_id: Mapped[int] = mapped_column(Integer)
    agent_id: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[float] = mapped_column(Float)  # epoch seconds (SQLite hands datetimes back without a zone)
    revoked_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    calls: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class CatalogItem(Base):
    """Skills, plugins, rules, commands and hooks the company shares (catalog.py): the management page
    the General Manager and agents take from, and add to when something is missing."""

    __tablename__ = "catalog"
    __table_args__ = (UniqueConstraint("kind", "name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)  # skill|plugin|rule|command|hook
    name: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text, default="")
    content: Mapped[str] = mapped_column(Text, default="")  # SKILL.md / rule text / command prompt / hook spec
    source: Mapped[str] = mapped_column(String(256), default="")  # where it came from (marketplace, URL, agent)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
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


class Decision(Base):
    """A recorded decision with its alternatives and evidence (decisions.py, SPEC-04 S04-R-02)."""

    __tablename__ = "decisions"
    id: Mapped[int] = mapped_column(primary_key=True)
    goal_id: Mapped[int | None] = mapped_column(ForeignKey("goals.id", ondelete="CASCADE"), nullable=True, index=True)
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    topic: Mapped[str] = mapped_column(String(256))
    alternatives: Mapped[list[Any]] = mapped_column(default=list)
    recommendation: Mapped[str] = mapped_column(Text, default="")
    reasoning: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[list[Any]] = mapped_column(default=list)  # blackboard/task/source references
    assumptions: Mapped[list[Any]] = mapped_column(default=list)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)  # an estimate, labelled as such
    # ASEAO07 S07-R-01: the decision report's question, criteria, limitations, why the confidence is what it is,
    # and independent reviews [{by, agent_id, verdict, note, at}]
    question: Mapped[str] = mapped_column(Text, default="")
    criteria: Mapped[list[Any]] = mapped_column(default=list)
    limitations: Mapped[list[Any]] = mapped_column(default=list)
    confidence_note: Mapped[str] = mapped_column(Text, default="")
    reviews: Mapped[list[Any]] = mapped_column(default=list)
    adr_path: Mapped[str] = mapped_column(String(256), default="")  # ASEAO08 S08-R-02: docs/adr/NNNN-title.md
    category: Mapped[str] = mapped_column(String(16), default="technical")
    requires_human_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="submitted")
    # draft|submitted|under_review|approved|rejected|escalated|expired|cancelled
    escalation_level: Mapped[int] = mapped_column(Integer, default=0)  # S04-R-07: 1 agent .. 5 human
    created_by_agent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approver: Mapped[str] = mapped_column(String(128), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class BlackboardEntry(Base):
    """Shared, versioned context of one goal (blackboard.py, SPEC-04 S04-R-01). Rows are never overwritten:
    a change is a new version, written only if it is based on the latest one."""

    __tablename__ = "blackboard"
    __table_args__ = (UniqueConstraint("goal_id", "section", "key", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    goal_id: Mapped[int] = mapped_column(ForeignKey("goals.id", ondelete="CASCADE"), index=True)
    section: Mapped[str] = mapped_column(String(16))  # objective|planning|research|engineering|qa|reports|messages
    key: Mapped[str] = mapped_column(String(128))
    version: Mapped[int] = mapped_column(Integer, default=1)
    author_agent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    task_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content: Mapped[str] = mapped_column(Text, default="")
    sources: Mapped[list[Any]] = mapped_column(default=list)
    meta: Mapped[dict[str, Any]] = mapped_column(default=dict)  # messages: from/to role, kind, delivered agent ids
    review_status: Mapped[str] = mapped_column(String(16), default="draft")  # draft|reviewed|rejected
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class GoalTransition(Base):
    """One change of a goal's workflow state (workflow.py, SPEC-04 S04-R-04)."""

    __tablename__ = "goal_transitions"
    id: Mapped[int] = mapped_column(primary_key=True)
    goal_id: Mapped[int] = mapped_column(ForeignKey("goals.id", ondelete="CASCADE"), index=True)
    from_status: Mapped[str] = mapped_column(String(24))
    to_status: Mapped[str] = mapped_column(String(24))
    reason: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(128), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class LiveProcess(Base):
    """An executor subprocess (Claude Code, Codex) while it runs, so an emergency stop from any process
    (the server or the `stop` CLI) can terminate it (SPEC-14 S14-R-01)."""

    __tablename__ = "live_processes"
    pid: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    program: Mapped[str] = mapped_column(String(64))  # argv[0], checked before a kill (PIDs are reused)
    kind: Mapped[str] = mapped_column(String(16))
    agent_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    goal_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Incident(Base):
    """A security incident (SPEC-14 S14-R-04): opened by the behaviour rules or by an administrator."""

    __tablename__ = "incidents"
    id: Mapped[int] = mapped_column(primary_key=True)
    category: Mapped[str] = mapped_column(String(32))  # governance.INCIDENT_CATEGORIES
    severity: Mapped[str] = mapped_column(String(8), default="SEV-3")  # SEV-1 (worst) .. SEV-4
    status: Mapped[str] = mapped_column(String(16), default="open")
    # open|contained|investigating|remediated|closed
    title: Mapped[str] = mapped_column(String(256))
    rule: Mapped[str] = mapped_column(String(64), default="")  # the behaviour rule that opened it, "" = manual
    goal_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    agent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evidence: Mapped[list[Any]] = mapped_column(default=list)  # frozen refs: {"type": "event|run|audit|task", "id"}
    root_cause: Mapped[str] = mapped_column(Text, default="")
    actions: Mapped[list[Any]] = mapped_column(default=list)  # [{"action", "actor", "at", "note"}]
    opened_by: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Operation(Base):
    """Idempotency key of a non-repeatable step (`goal:<id>:<iteration>:merge`), SPEC-04 S04-R-05."""

    __tablename__ = "operations"
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="started")  # started|done
    result: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


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
            if url.startswith("sqlite"):
                _knowledge_fts(_engine)
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
            added = set()
            for col in table.columns:
                if col.name in have:
                    continue
                ddl = col.type.compile(dialect=eng.dialect)
                default = ""
                if col.default is not None and getattr(col.default, "is_scalar", False):
                    v = col.default.arg
                    default = f" DEFAULT {int(v) if isinstance(v, bool) else repr(v) if isinstance(v, str) else v}"
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {ddl}{default}'))
                added.add(col.name)
            # An added column's indexes (index=True: ix_<table>_<column>, the name create_all gives them).
            for idx in table.indexes:
                if added & {c.name for c in idx.columns}:
                    idx.create(conn, checkfirst=True)
        # A column whose default is computed (list, dict) cannot be a DDL default: rows that existed when it was added
        # got NULL although the model says it never is (2026-11-06: projects.stack broke the first real goal). They
        # get the default, also in databases migrated before this.
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            for col in table.columns:
                d = col.default
                if col.nullable or d is None or getattr(d, "is_scalar", True) or not callable(d.arg):
                    continue
                conn.execute(table.update().where(col.is_(None)).values({col.name: d.arg(None)}))


def _knowledge_fts(eng: Engine) -> None:
    """The FTS5 index over knowledge items (SPEC-11 S11-R-03), kept current by triggers. A new index is filled
    from the rows already there."""
    from sqlalchemy import text

    with eng.begin() as conn:
        new = conn.execute(text("SELECT 1 FROM sqlite_master WHERE name = 'lessons_fts'")).first() is None
        conn.execute(text("CREATE VIRTUAL TABLE IF NOT EXISTS lessons_fts USING fts5("
                          "text, technologies, content='lessons', content_rowid='id', tokenize='porter unicode61')"))
        conn.execute(text("CREATE TRIGGER IF NOT EXISTS lessons_fts_ai AFTER INSERT ON lessons BEGIN "
                          "INSERT INTO lessons_fts(rowid, text, technologies) "
                          "VALUES (new.id, new.text, new.technologies); END"))
        conn.execute(text("CREATE TRIGGER IF NOT EXISTS lessons_fts_ad AFTER DELETE ON lessons BEGIN "
                          "INSERT INTO lessons_fts(lessons_fts, rowid, text, technologies) "
                          "VALUES ('delete', old.id, old.text, old.technologies); END"))
        conn.execute(text("CREATE TRIGGER IF NOT EXISTS lessons_fts_au AFTER UPDATE OF text, technologies ON lessons "
                          "BEGIN INSERT INTO lessons_fts(lessons_fts, rowid, text, technologies) "
                          "VALUES ('delete', old.id, old.text, old.technologies); "
                          "INSERT INTO lessons_fts(rowid, text, technologies) "
                          "VALUES (new.id, new.text, new.technologies); END"))
        if new:
            conn.execute(text("INSERT INTO lessons_fts(lessons_fts) VALUES ('rebuild')"))


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
    ev = Event(kind=kind, message=message, goal_id=goal_id, agent_id=agent_id, data=data,
               iteration=_as_int(data.get("iteration")))
    if s is not None:
        s.add(ev)
        return
    with session() as own:
        own.add(ev)
