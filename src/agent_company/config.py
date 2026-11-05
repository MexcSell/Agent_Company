"""Runtime settings, read from the environment (and `.env` via python-dotenv)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw else default


@dataclass
class Settings:
    data_dir: Path
    database_url: str
    default_model: str | None
    # "auto" uses the Model Router; "offline" forces the deterministic offline executor.
    executor: str
    max_iterations: int
    max_parallel: int
    require_approval: bool
    enable_claude_code: bool
    enable_codex: bool
    command_timeout: int
    rotation_interval: int
    host: str
    port: int
    quality_commands: list[str] = field(default_factory=list)
    # Proposals agents raise: "user" waits for an administrator, "superiors" lets the GM decide.
    proposal_policy: str = "user"
    max_proposal_goals: int = 3  # goals one goal may spawn through approved proposals
    goal_budget_usd: float = 0.0  # 0 = no limit
    goal_time_budget: int = 0  # seconds, 0 = no limit
    goal_token_budget: int = 0  # model tokens per goal, 0 = no limit (S04-R-09)
    goal_agent_limit: int = 0  # agents working on one goal at once, 0 = no limit (S04-R-09)
    auto_push: bool = False
    allow_preexisting_failures: bool = False
    # How browsers reach the dashboard (transport.py): "https" (default) or "http".
    transport: str = "https"
    public_port: int = 4900  # the port nginx listens on when MANAGE_NGINX=1
    manage_nginx: bool = False  # write the nginx site and reload nginx (only the real server)
    nginx_dir: str = "/etc/nginx"
    study_interval: int = 0  # seconds between study visits of the learning rotation (0 = manual)
    # Transient executor errors (429, 5xx, timeouts) are retried with backoff before the next model (S04-R-06).
    retry_max_attempts: int = 3
    retry_base_seconds: float = 2.0
    retry_max_seconds: float = 60.0
    # A candidate playbook replaces the current one only if its success rate beats the baseline by this
    # many percentage points (S13-D-01); a tie keeps the current version.
    min_improvement: int = 10
    max_goal_depth: int = 3  # generations of proposal goals below a person's order (S04-R-08)
    # What an exhausted goal budget does (S04-R-09): "pause" waits for an administrator, "stop" fails the goal.
    budget_exhausted_action: str = "pause"
    # The same check failing in this many consecutive iterations escalates to the planner (S04-R-06, S04-R-07).
    escalate_after: int = 2
    # A candidate playbook must pass the smoke suite before it is tried on users' goals (S13-D-02).
    candidate_smoke: bool = True
    # A goal waiting for approval longer than this many hours becomes a project blocker (ASEAO05 S05-R-02; 0 = never).
    approval_blocker_hours: int = 24
    # Agents idle longer than this many days are listed for retirement review (SPEC-02 S02-R-02).
    inactive_days: int = 30
    # Share of non-critical staffing decisions that try a new agent without enough evidence yet (SPEC-12 S12-R-03).
    explore_rate: float = 0.1
    # Which recruitments wait for an administrator (SPEC-03 S03-R-05): never | permanent_only (a new agent) | always
    # (also a clone made for a role nobody holds).
    recruit_requires_approval: str = "never"
    # A new recruit of a writing role first runs an eval suite as a candidate (SPEC-03 S03-R-04); off by default: it
    # costs model calls, and the offline simulator fails the smoke suite on purpose.
    qualify_recruits: bool = False
    # Verified self-evolution (selfevolve.py): a candidate is accepted when its weighted benchmark score beats the
    # baseline by this many percent and no metric is worse by more than `improvement_max_regression` percent.
    improvement_min_score: float = 2.0
    improvement_max_regression: int = 25
    # A deployed improvement the safe restart refused (or that came up unhealthy) is reverted automatically.
    improvement_auto_rollback: bool = True
    # Length of a research shift on the rotation (SPEC-12 S12-R-05); 0 = every rotation turn is a shift of its own.
    shift_seconds: int = 0
    # Outside callers may give the company tasks over A2A (G-R-06): the agent card is published and /api/a2a answers.
    a2a_enabled: bool = False
    # Regular expressions the command policy refuses on top of governance.DANGEROUS (SPEC-01 S01-R-05).
    blocked_commands: list[str] = field(default_factory=list)
    # Knowledge ranking weights (SPEC-11 S11-R-03 §8.3); a change must keep recall@5 of evals/retrieval.json (S11-R-08).
    retrieval_w_relevance: float = 3.0
    retrieval_w_technology: float = 0.5
    retrieval_w_verified: float = 0.2
    retrieval_w_author: float = 0.3
    retrieval_w_recency: float = 0.3
    retrieval_w_contradiction: float = 1.0
    # L3 isolation of the commands that run agent-written code (sandbox.py, S14-R-07): off | auto | required.
    sandbox: str = "auto"
    sandbox_memory_mb: int = 4096
    sandbox_tasks: int = 512
    sandbox_cpus: float = 2.0
    sandbox_paths: list[str] = field(default_factory=list)  # extra read-only paths (toolchains)
    sandbox_env: list[str] = field(default_factory=list)  # extra environment variable names passed through

    @property
    def worktrees_dir(self) -> Path:
        return self.data_dir / "worktrees"

    @property
    def workspaces_dir(self) -> Path:
        return self.data_dir / "workspaces"


def load_settings() -> Settings:
    data_dir = Path(os.environ.get("DATA_DIR") or REPO_ROOT / "data").resolve()
    db_url = os.environ.get("DATABASE_URL") or f"sqlite:///{data_dir / 'agent_company.db'}"
    commands = [c.strip() for c in os.environ.get("QUALITY_COMMANDS", "").split(";;") if c.strip()]
    return Settings(
        data_dir=data_dir,
        database_url=db_url,
        default_model=os.environ.get("MODEL") or None,
        executor=(os.environ.get("EXECUTOR") or "auto").lower(),
        max_iterations=_int("MAX_ITERATIONS", 3),
        max_parallel=_int("MAX_PARALLEL", 3),
        require_approval=_bool("REQUIRE_APPROVAL", True),
        enable_claude_code=_bool("ENABLE_CLAUDE_CODE", False),
        enable_codex=_bool("ENABLE_CODEX", False),
        command_timeout=_int("COMMAND_TIMEOUT", 600),
        rotation_interval=_int("ROTATION_INTERVAL", 0),
        host=os.environ.get("HOST") or "127.0.0.1",
        port=_int("PORT", 4900),
        quality_commands=commands,
        proposal_policy=(os.environ.get("PROPOSAL_POLICY") or "user").lower(),
        max_proposal_goals=_int("MAX_PROPOSAL_GOALS", 3),
        goal_budget_usd=float(os.environ.get("GOAL_BUDGET_USD") or 0),
        goal_time_budget=_int("GOAL_TIME_BUDGET", 0),
        goal_token_budget=_int("GOAL_TOKEN_BUDGET", 0),
        goal_agent_limit=_int("GOAL_AGENT_LIMIT", 0),
        auto_push=_bool("AUTO_PUSH", False),
        allow_preexisting_failures=_bool("ALLOW_PREEXISTING_FAILURES", False),
        transport=(os.environ.get("TRANSPORT") or "https").lower(),
        public_port=_int("PUBLIC_PORT", 4900),
        manage_nginx=_bool("MANAGE_NGINX", False),
        nginx_dir=os.environ.get("NGINX_DIR") or "/etc/nginx",
        study_interval=_int("STUDY_INTERVAL", 0),
        retry_max_attempts=_int("RETRY_MAX_ATTEMPTS", 3),
        retry_base_seconds=float(os.environ.get("RETRY_BASE_SECONDS") or 2),
        retry_max_seconds=float(os.environ.get("RETRY_MAX_SECONDS") or 60),
        min_improvement=_int("MIN_IMPROVEMENT", 10),
        max_goal_depth=_int("MAX_GOAL_DEPTH", 3),
        budget_exhausted_action=(os.environ.get("BUDGET_EXHAUSTED_ACTION") or "pause").lower(),
        escalate_after=_int("ESCALATE_AFTER", 2),
        candidate_smoke=_bool("CANDIDATE_SMOKE", True),
        approval_blocker_hours=_int("APPROVAL_BLOCKER_HOURS", 24),
        inactive_days=_int("INACTIVE_DAYS", 30),
        explore_rate=float(os.environ.get("EXPLORE_RATE") or 0.1),
        recruit_requires_approval=(os.environ.get("RECRUIT_REQUIRES_APPROVAL") or "never").lower(),
        qualify_recruits=_bool("QUALIFY_RECRUITS", False),
        improvement_min_score=float(os.environ.get("IMPROVEMENT_MIN_SCORE") or 2),
        improvement_max_regression=_int("IMPROVEMENT_MAX_REGRESSION", 25),
        improvement_auto_rollback=_bool("IMPROVEMENT_AUTO_ROLLBACK", True),
        shift_seconds=_int("SHIFT_SECONDS", 0),
        a2a_enabled=_bool("A2A_ENABLED", False),
        retrieval_w_relevance=float(os.environ.get("RETRIEVAL_W_RELEVANCE") or 3.0),
        retrieval_w_technology=float(os.environ.get("RETRIEVAL_W_TECHNOLOGY") or 0.5),
        retrieval_w_verified=float(os.environ.get("RETRIEVAL_W_VERIFIED") or 0.2),
        retrieval_w_author=float(os.environ.get("RETRIEVAL_W_AUTHOR") or 0.3),
        retrieval_w_recency=float(os.environ.get("RETRIEVAL_W_RECENCY") or 0.3),
        retrieval_w_contradiction=float(os.environ.get("RETRIEVAL_W_CONTRADICTION") or 1.0),
        sandbox=(os.environ.get("SANDBOX") or "auto").lower(),
        sandbox_memory_mb=_int("SANDBOX_MEMORY_MB", 4096),
        sandbox_tasks=_int("SANDBOX_TASKS", 512),
        sandbox_cpus=float(os.environ.get("SANDBOX_CPUS") or 2.0),
    )


_settings: Settings | None = None
_overrides: dict[str, object] = {}


def settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = load_settings()
        for k, v in _overrides.items():
            if hasattr(_settings, k):
                setattr(_settings, k, v)
    return _settings


def set_overrides(values: dict[str, object]) -> None:
    """Apply administrator overrides from the database on top of the environment."""
    global _settings
    _overrides.clear()
    _overrides.update(values)
    _settings = None


def reset_settings() -> None:
    """Re-read the environment and drop overrides (used by tests)."""
    global _settings
    _settings = None
    _overrides.clear()
