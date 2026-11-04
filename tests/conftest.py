import pytest

from agent_company import config, db, router, scheduler

KEY_VARS = ["MODEL", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY", "GEMINI_API_KEY",
            "GOOGLE_API_KEY", "OLLAMA_API_BASE", "QUALITY_COMMANDS", "ENABLE_CLAUDE_CODE", "ENABLE_CODEX",
            "REQUIRE_APPROVAL", "MAX_ITERATIONS", "ALLOW_PRIVATE_CRAWL", "DATABASE_URL"]


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    for k in KEY_VARS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("EXECUTOR", "auto")
    config.reset_settings()
    db.reset_engine()
    scheduler._global_slots = None
    router._catalog = None
    from agent_company import api
    from agent_company.api import bootstrap

    api._signups.clear()  # the registration rate limit is per process; start each test fresh
    bootstrap()
    yield tmp_path
    db.reset_engine()
    config.reset_settings()
