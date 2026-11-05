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
