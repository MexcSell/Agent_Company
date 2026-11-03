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
    )


_settings: Settings | None = None


def settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings


def reset_settings() -> None:
    """Re-read the environment (used by tests)."""
    global _settings
    _settings = None
