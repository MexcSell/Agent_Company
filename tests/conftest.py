import pytest

from agent_company import config, db, router, scheduler

KEY_VARS = ["MODEL", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY", "GEMINI_API_KEY",
            "GOOGLE_API_KEY", "OLLAMA_API_BASE", "QUALITY_COMMANDS", "ENABLE_CLAUDE_CODE", "ENABLE_CODEX",
            "REQUIRE_APPROVAL", "MAX_ITERATIONS", "ALLOW_PRIVATE_CRAWL", "DATABASE_URL",
            # Shells opened from the Workspace inherit the server's environment (MANAGE_NGINX=1):
            # a test must never write the real nginx site or reload nginx.
            "TRANSPORT", "PORT", "PUBLIC_PORT", "TLS_HOSTS"]


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    for k in KEY_VARS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("EXECUTOR", "auto")
    monkeypatch.setenv("MANAGE_NGINX", "0")
    monkeypatch.setenv("NGINX_DIR", str(tmp_path / "nginx"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))  # never read the host's Claude Code sessions
    config.reset_settings()
    db.reset_engine()
    scheduler._global_slots = None
    router._catalog = None
    from agent_company import loop

    monkeypatch.setattr(loop, "_sleep", lambda seconds: None)  # retry backoff never waits in tests
    from agent_company import evolution

    evolution._smoking.clear()  # smoke trials never start in the background; tests call smoke_trial themselves
    monkeypatch.setattr(evolution, "_spawn", lambda fn: None)
    from agent_company import experiments, training

    experiments._running.clear()  # experiments and learning plans never start in the background either
    training._running.clear()
    monkeypatch.setattr(experiments, "_spawn", lambda fn: None)
    monkeypatch.setattr(training, "_spawn", lambda fn: None)
    from agent_company import api
    from agent_company.api import bootstrap

    from agent_company import workspace

    workspace._sessions_cache = (0.0, [])
    workspace._watch.clear()
    workspace._history_cache.clear()
    api._signups.clear()  # the registration rate limit is per process; start each test fresh
    bootstrap()
    yield tmp_path
    db.reset_engine()
    config.reset_settings()
