import httpx
import pytest

from agent_company import router, usage
from agent_company.config import reset_settings


def test_falls_back_to_offline_without_credentials():
    c = router.resolve("architect")
    assert c.kind == "offline" and "ANTHROPIC_API_KEY" in c.reason
    gm = router.resolve("general_manager")
    assert router.list_routes()["general_manager"] == "Claude Code", "the GM is Claude Code by default"
    assert gm.kind == "offline" and "ENABLE_CLAUDE_CODE" in gm.reason


def test_gm_can_use_another_agents_model(monkeypatch):
    from agent_company import registry

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    other = registry.create_agent("data_engineer")
    registry.update_agent(other["id"], {"model_pref": "Claude Haiku 5.5"})
    c = router.resolve("general_manager", model_pref=f"agent:{other['id']}")
    assert c.name == "Claude Haiku 5.5" and "data engineer" in c.reason.lower()


def test_uses_route_when_key_present(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    assert router.resolve("architect").name == "Claude Opus 5.5"
    assert router.resolve("qa").name == "Claude Sonnet 5.5"
    # developer routes to Claude Code, which is off by default -> the most suitable usable model for a lead role (V7)
    dev = router.resolve("developer")
    assert dev.kind == "llm" and dev.reason.startswith(router.SUITABLE + "developer (lead tier;")
    assert "ENABLE_CLAUDE_CODE" in dev.reason
    assert router.resolve("developer", model_pref="Claude Haiku 5.5").name == "Claude Haiku 5.5"


def test_model_env_is_the_fallback(monkeypatch):
    monkeypatch.setenv("MODEL", "openai/gpt-test")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    reset_settings()
    router.seed()
    c = router.resolve("general_manager")
    assert c.model_id == "openai/gpt-test" and c.reason == "MODEL in .env"


def test_executor_offline_forces_offline(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("EXECUTOR", "offline")
    reset_settings()
    assert router.resolve("general_manager").kind == "offline"


def test_usage_limit_skips_model(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    m = next(x for x in router.list_models() if x["name"] == "Claude Opus 5.5")
    router.update_model(m["id"], {"daily_token_limit": 100})
    usage.record(model_name="Claude Opus 5.5", kind="llm", input_tokens=80, output_tokens=30)
    gm = router.resolve("general_manager")  # its route (Claude Code) is off: the most suitable model, not Opus (V7)
    assert gm.kind == "llm" and gm.name != "Claude Opus 5.5" and gm.reason.startswith(router.SUITABLE)
    s = usage.summary()
    opus = next(x for x in s["models"] if x["name"] == "Claude Opus 5.5")
    assert opus["daily"]["tokens"] == 110 and opus["daily"]["limit"] == 100
    assert abs(opus["daily"]["cost"] - (80 * 4 + 30 * 20) / 1e6) < 1e-12
    assert 0 < s["resets"]["daily_in_seconds"] <= 86400


def test_openrouter_catalog_and_add(monkeypatch):
    def handler(req):
        return httpx.Response(200, json={"data": [
            {"id": "qwen/qwen3-coder:free", "name": "Qwen3 Coder (free)", "context_length": 262144,
             "pricing": {"prompt": "0", "completion": "0"}},
            {"id": "deepseek/deepseek-chat", "name": "DeepSeek V3", "context_length": 128000,
             "pricing": {"prompt": "0.0000003", "completion": "0.0000012"}}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    rows = router.openrouter_catalog(client=client)
    assert rows[0]["free"] and rows[1]["input_cost"] == 0.3
    assert [r["id"] for r in router.openrouter_catalog("deep")] == ["deepseek/deepseek-chat"]
    assert len(router.openrouter_catalog(free_only=True)) == 1
    m = router.add_openrouter_model("deepseek/deepseek-chat")
    assert m["model_id"] == "openrouter/deepseek/deepseek-chat" and m["output_cost"] == 1.2
    assert m["terms"] == {"billing": "api", "context_window": 128000} and m["terms_due"], "from the catalog, unverified"
    assert router.add_openrouter_model("qwen/qwen3-coder:free")["terms"]["billing"] == "free_tier"
    assert m["available"] is False
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    router.set_route("research", m["name"])
    assert router.resolve("research").model_id == "openrouter/deepseek/deepseek-chat"


def test_delete_model_moves_routes():
    m = router.add_model({"name": "Local", "kind": "llm", "model_id": "ollama/qwen3"})
    router.set_route("analyst", "Local")
    router.delete_model(m["id"])
    assert router.list_routes()["analyst"] == router.DEFAULT_ROUTE_OTHER


def test_rename_model_carries_routes_agents_and_usage():
    import pytest

    from agent_company import registry

    m = router.add_model({"name": "Local", "kind": "llm", "model_id": "ollama/qwen3"})
    router.set_route("analyst", "Local", escalation="Local")
    agent = registry.create_agent("data_engineer")
    registry.update_agent(agent["id"], {"model_pref": "Local"})
    usage.record(model_name="Local", kind="llm", input_tokens=5, output_tokens=5)
    with pytest.raises(ValueError):
        router.update_model(m["id"], {"name": "Claude Opus 5.5"})
    with pytest.raises(ValueError):
        router.update_model(m["id"], {"name": "  "})
    assert router.update_model(m["id"], {"name": " Qwen here "})["name"] == "Qwen here"
    assert router.list_routes()["analyst"] == "Qwen here" and router.list_escalations()["analyst"] == "Qwen here"
    assert next(a for a in registry.list_agents() if a["id"] == agent["id"])["model_pref"] == "Qwen here"
    assert usage.tokens_since("Qwen here", usage.day_start()) == 10, "its quota keeps counting today's runs"
    opus = next(x for x in router.list_models() if x["name"] == "Claude Opus 5.5")
    with pytest.raises(ValueError):
        router.update_model(opus["id"], {"name": "Opus"})
    assert router.update_model(opus["id"], {"name": "Claude Opus 5.5"})["name"] == "Claude Opus 5.5", "unchanged is fine"


def test_openai_compatible_provider_catalogs(monkeypatch):
    def handler(req):
        assert req.headers["authorization"] == "Bearer gsk-test"
        return httpx.Response(200, json={"data": [
            {"id": "llama-3.3-70b-versatile", "context_window": 131072},
            {"id": "whisper-large-v3", "context_window": 448},
            {"id": "llama-guard-4-12b"}]})

    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    router._provider_cache.clear()
    rows = router.provider_catalog("groq", client=client)
    assert [r["id"] for r in rows] == ["llama-3.3-70b-versatile"] and rows[0]["context_length"] == 131072
    m = router.add_provider_model("groq", "llama-3.3-70b-versatile")
    assert m["model_id"] == "openai/llama-3.3-70b-versatile" and m["base_url"].startswith("https://api.groq.com")
    assert m["available"] is True and m["api_key_env"] == "GROQ_API_KEY"
    monkeypatch.delenv("GROQ_API_KEY")
    assert next(x for x in router.list_models() if x["id"] == m["id"])["available"] is False
    router._provider_cache.clear()
    try:
        router.provider_catalog("mistral")
        raise AssertionError("a key is required")
    except ValueError as e:
        assert "MISTRAL_API_KEY" in str(e)
    assert {p["id"] for p in router.providers()} == {"groq", "mistral", "cohere", "sambanova"}


def test_model_terms_are_validated_merged_and_verified_by_a_person():
    """G-R-18: billing, context, tool calling, data terms, licence page and when a person last checked them."""
    import pytest

    m = router.add_model({"name": "Local", "kind": "llm", "model_id": "ollama/qwen3", "terms": {"billing": "local"}})
    assert m["terms"] == {"billing": "local"} and m["terms_due"], "never verified"
    for bad in ({"billing": "unlimited"}, {"context_window": -1}, {"tool_calling": "maybe"},
                {"terms_url": "file:///etc/passwd"}, {"price": 0}):
        with pytest.raises(ValueError):
            router.update_model(m["id"], {"terms": bad})
    out = router.update_model(m["id"], {"terms": {"context_window": 32768, "trains_on_prompts": "no",
                                                  "terms_url": "https://ollama.com/library/qwen3", "verified": True}})
    assert out["terms"]["billing"] == "local" and out["terms"]["context_window"] == 32768, "merged, not replaced"
    assert out["terms"]["verified_at"] and out["terms_due"] is False
    out = router.update_model(m["id"], {"terms": {"trains_on_prompts": ""}})
    assert "trains_on_prompts" not in out["terms"], "empty clears a field"
    from agent_company.db import ModelEntry, session

    with session() as s:
        row = s.get(ModelEntry, m["id"])
        row.terms = {**row.terms, "verified_at": "2026-01-01T00:00:00+00:00"}
    assert next(x for x in router.list_models() if x["id"] == m["id"])["terms_due"], "older than 90 days"


def test_own_api_key_is_encrypted_hinted_and_used(monkeypatch):
    import pytest

    from agent_company.db import ModelEntry, session

    key = "sk-or-v1-0123456789abcdefYSUA"
    m = router.add_model({"name": "Own key", "kind": "llm", "model_id": "openrouter/qwen/qwen3-coder:free",
                          "api_key": key})
    assert m["available"] is True, "its own key stands in for OPENROUTER_API_KEY"
    assert m["key_set"] and m["key_hint"] == "sk-...YSUA" and key not in str(m)
    with session() as s:
        stored = s.get(ModelEntry, m["id"]).api_key_enc
    assert stored and key not in stored, "stored encrypted"
    assert router.key_for("Own key") == key
    assert router.model_type(_entry(m["id"])) == "API"
    router.update_model(m["id"], {"api_key": ""})
    assert router.key_for("Own key") == "" and not next(x for x in router.list_models() if x["id"] == m["id"])["available"]
    with pytest.raises(ValueError):
        router.add_model({"name": "CLI with key", "kind": "codex", "api_key": key})
    with pytest.raises(ValueError):
        router.update_model(m["id"], {"api_key": "two words"})


def _entry(model_id):
    from agent_company.db import ModelEntry, session

    with session() as s:
        m = s.get(ModelEntry, model_id)
        s.expunge(m)
        return m


def test_model_types_and_the_offline_simulator_can_be_switched_off():
    local = router.add_model({"name": "Here", "kind": "llm", "model_id": "openai/qwen", "base_url": "http://127.0.0.1:8080/v1"})
    types = {x["name"]: x["type"] for x in router.list_models()}
    assert types["Here"] == "Local" and types["Claude Opus 5.5"] == "API" and types["Codex CLI"] == "CLI"
    assert types[router.OFFLINE] == "offline" and local["type"] == "Local"
    off = next(x for x in router.list_models() if x["kind"] == "offline")
    assert router.update_model(off["id"], {"enabled": False})["enabled"] is False  # tests/test_simulator_switch.py
    assert router.update_model(local["id"], {"enabled": False})["available"] is False


def test_copilot_model_needs_its_switch_and_cli_and_runs_only_granted_tools(monkeypatch, tmp_path):
    import subprocess

    from agent_company import executors
    from agent_company.config import settings

    m = router.add_model({"name": "GitHub Copilot", "kind": "copilot"})
    assert m["type"] == "CLI" and "ENABLE_COPILOT" in m["unavailable_reason"]
    monkeypatch.setenv("ENABLE_COPILOT", "1")
    reset_settings()
    assert settings().enable_copilot
    monkeypatch.setattr(router.shutil, "which", lambda exe: None)
    assert "not found" in next(x for x in router.list_models() if x["id"] == m["id"])["unavailable_reason"]
    seen = []

    def spawn(self, cmd, req, cwd, env=None, **kw):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "done", "")

    monkeypatch.setattr(executors.Executor, "_spawn", spawn)
    monkeypatch.setattr(executors, "ensure_sandboxed", lambda p: p)
    ex = executors.for_choice(router.Choice(name="GitHub Copilot", kind="copilot", model_id="", reason="t"))
    req = executors.Request(purpose="task", role="developer", system="s", prompt="p", workdir=tmp_path, writes=True,
                            permissions=["read", "write"], autonomy="G2")
    req.grant = executors.gate(req, "copilot")
    assert req.grant.writes and not req.grant.shell, "Copilot writes without a shell"
    res = ex._run(req)
    assert res.ok and res.text == "done"
    cmd = seen[-1]
    assert cmd[:2] == ["copilot", "-p"] and ["--allow-tool", "write"] == cmd[3:5]
    assert not any(c.startswith("shell(") and c != "shell(git push)" for c in cmd)
    req.grant = executors.gate(executors.Request(purpose="task", role="qa", system="s", prompt="p", workdir=tmp_path,
                                                 permissions=["read"], autonomy="G2"), "copilot")
    ex._run(req)
    assert "write" not in seen[-1], "a read-only run allows no tool"


def test_openrouter_sort_by_performance_and_text_models_only():
    def handler(req):
        return httpx.Response(200, json={"data": [
            {"id": "a/small:free", "name": "Small", "context_length": 8192, "pricing": {"prompt": "0", "completion": "0"}},
            {"id": "b/agent:free", "name": "Agent", "context_length": 262144, "supported_parameters": ["tools", "reasoning"],
             "top_provider": {"max_completion_tokens": 65536}, "pricing": {"prompt": "0", "completion": "0"}},
            {"id": "c/video", "name": "Video", "context_length": 131072, "pricing": {"prompt": "0", "completion": "0"},
             "architecture": {"output_modalities": ["video"]}}]})

    rows = router.openrouter_catalog(client=httpx.Client(transport=httpx.MockTransport(handler)), sort="performance")
    assert [r["id"] for r in rows] == ["b/agent:free", "a/small:free"], "best first; a video model is no chat model"
    assert rows[0]["tools"] and rows[0]["reasoning"] and rows[0]["score"] > rows[1]["score"]
    assert [r["id"] for r in router.openrouter_catalog(free_only=True)] == ["a/small:free", "b/agent:free"], "by price"


def test_openrouter_key_status_and_codex_limits(tmp_path):
    import json
    import time

    def handler(req):
        assert req.headers["authorization"] == "Bearer sk-k"
        return httpx.Response(200, json={"data": {"usage": 3.0, "usage_daily": 0.5, "limit": 2.0,
                                                  "limit_reset": "daily", "is_free_tier": False}})

    st = router.openrouter_key_status("sk-k", httpx.Client(transport=httpx.MockTransport(handler)))
    w = st["limits"][0]
    assert w["used_percent"] == 25.0 and 0 < w["resets_at"] - time.time() <= 86400, "today's use of the daily limit"
    assert router.openrouter_key_status("")["source"] == "error"
    day = tmp_path / "sessions" / "2026" / "11" / "06"
    day.mkdir(parents=True)
    rl = {"primary": {"used_percent": 50.0, "window_minutes": 43200, "resets_at": time.time() + 3600},
          "secondary": None, "plan_type": "free"}
    (day / "rollout-x.jsonl").write_text(json.dumps({"type": "event_msg", "payload": {"type": "token_count",
                                                                                       "rate_limits": rl}}) + "\n")
    st = router.codex_limits(tmp_path)
    assert st["plan"] == "free" and st["limits"][0]["used_percent"] == 50.0 and st["limits"][0]["label"] == "30-day limit"
    assert "no plan limits" in router.codex_limits(tmp_path / "none")["detail"]


def test_codex_status_reads_the_models_own_sign_in(monkeypatch, tmp_path):
    """A Test of a Codex model with its own account stores that account's plan windows, not the server's ~/.codex."""
    import json
    import time

    def write(home, plan, pct):
        day = home / "sessions" / "2026" / "11" / "08"
        day.mkdir(parents=True)
        rl = {"primary": {"used_percent": pct, "window_minutes": 300, "resets_at": time.time() + 3600},
              "secondary": None, "plan_type": plan}
        (day / "rollout-x.jsonl").write_text(json.dumps({"type": "event_msg", "payload": {"type": "token_count",
                                                                                           "rate_limits": rl}}) + "\n")

    write(tmp_path / "host", "plus", 40.0)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "host"))
    write(router.codex_home("acct"), "free", 5.0)
    own = router.provider_status(router.Choice("Own", "codex", "", "test", account="acct"))
    host = router.provider_status(router.Choice("Host", "codex", "", "test"))
    assert own["plan"] == "free" and own["limits"][0]["used_percent"] == 5.0
    assert host["plan"] == "plus" and host["limits"][0]["used_percent"] == 40.0


def test_model_info_shows_status_cli_account_and_usage_windows(monkeypatch, tmp_path):
    import base64
    import json
    import os
    import time
    from pathlib import Path

    from agent_company.db import ModelEntry, session

    m = router.add_model({"name": "Ringed", "kind": "llm", "model_id": "openai/x", "daily_token_limit": 200})
    usage.record(model_name="Ringed", kind="llm", input_tokens=150, output_tokens=10)
    info = router.models_info()[m["id"]]
    assert info["status"] == "unavailable" and "OPENAI_API_KEY" in info["reason"]
    assert info["account"] == {"method": "the server's key", "email": "OPENAI_API_KEY"}
    w = info["limits"][0]
    assert (w["label"], w["percent"], w["severity"]) == ("Daily token limit", 80.0, "warning")
    assert info["tokens"] == {"today": 160, "week": 160}
    # Codex: whose ChatGPT sign-in (never a token) and the plan's windows as Codex last reported them
    home = Path(os.environ["CODEX_HOME"])
    home.mkdir(parents=True)
    claims = base64.urlsafe_b64encode(json.dumps({"email": "dev@example.com", "https://api.openai.com/auth": {
        "chatgpt_plan_type": "plus"}}).encode()).decode().rstrip("=")
    (home / "auth.json").write_text(json.dumps({"auth_mode": "chatgpt", "tokens": {"id_token": f"h.{claims}.sig",
                                                                                  "access_token": "secret-access"}}))
    day = home / "sessions" / "2026" / "11" / "06"
    day.mkdir(parents=True)
    rl = {"primary": {"used_percent": 18.0, "window_minutes": 300, "resets_at": time.time() + 3540},
          "secondary": {"used_percent": 79.0, "window_minutes": 10080, "resets_at": time.time() + 84000},
          "plan_type": "plus"}
    (day / "rollout-a.jsonl").write_text(json.dumps({"payload": {"rate_limits": rl}}) + "\n")
    codex = next(x for x in router.list_models() if x["kind"] == "codex")
    info = router.models_info()[codex["id"]]
    assert info["account"] == {"method": "ChatGPT account", "email": "dev@example.com", "plan": "plus"}
    assert [(x["label"], x["percent"], x["severity"]) for x in info["limits"]] == [
        ("5-hour session", 18.0, "normal"), ("Weekly limit", 79.0, "warning")]
    assert "plus" in info["scope"] and info["cli"]["label"] == "Codex" and "secret-access" not in json.dumps(info)
    with session() as s:
        s.get(ModelEntry, codex["id"]).enabled = False
    assert router.models_info()[codex["id"]]["status"] == "inactive"
    off = next(x for x in router.list_models() if x["kind"] == "offline")
    assert router.models_info()[off["id"]]["status"] == "available"


def test_testing_an_unavailable_model_keeps_its_status():
    m = router.add_model({"name": "Codex here", "kind": "codex"})
    r = router.test_model(m["id"])
    assert not r["ok"] and "ENABLE_CODEX" in r["error"]
    st = next(x for x in router.list_models() if x["id"] == m["id"])["status"]
    assert st["ok"] is False and st["source"] == "codex" and st["checked_at"]


def test_a_refused_key_reads_plainly():
    client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(401, json={"error": "no"})))
    st = router.provider_status(router.Choice("m", "llm", "openrouter/x/y", ""), "sk-bad", client)
    assert st == {"source": "error", "detail": "usage not available: the key was refused (HTTP 401)"}


def test_auto_routes_assign_models_usable_now(monkeypatch):
    """Assign models: the strongest leads, a mid model reviews, the cheapest collects; a limited model only
    comes last among the fallbacks, and with no usable model nothing changes."""
    before = router.list_routes()
    with pytest.raises(ValueError, match="no model is usable"):
        router.auto_routes()
    assert router.list_routes() == before
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    plan = router.auto_routes()
    assert plan["architect"]["model"] == "Claude Fable 5.1" and plan["architect"]["escalation"] == ""
    assert plan["qa"]["model"] == "Claude Sonnet 5.5" and plan["qa"]["escalation"] == "Claude Fable 5.1"
    assert plan["research"]["model"] == plan["sales"]["model"] == "Claude Haiku 5.5"
    assert router.list_routes()["qa"] == "Claude Sonnet 5.5"
    assert router.list_fallbacks()["qa"] == plan["qa"]["fallback"] and "Claude Sonnet 5.5" not in plan["qa"]["fallback"]
    router.mark_limited("Claude Fable 5.1", "You've hit your session limit · resets 9:50pm")
    plan = router.auto_routes()
    assert plan["general_manager"]["model"] == "Claude Opus 5.5"
    assert plan["general_manager"]["fallback"][-1] == "Claude Fable 5.1"
    assert all(p["model"] != "Claude Fable 5.1" for p in plan.values())


def test_auto_routes_put_agents_on_their_roles_model(monkeypatch):
    """Assign models clears an agent's own model (or another agent's), so its card and its runs show the role's
    assigned model; each change is a configuration version."""
    from agent_company import registry

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    qa = next(a for a in registry.list_agents() if a["role"] == "qa")
    arch = next(a for a in registry.list_agents() if a["role"] == "architect")
    registry.update_agent(qa["id"], {"model_pref": "Claude Haiku 5.5"})
    registry.update_agent(arch["id"], {"model_pref": f"agent:{qa['id']}"})
    version = registry.agent_detail(qa["id"])["config_version"]
    plan = router.auto_routes("admin")
    assert qa["name"] in plan["qa"]["agents_reset"] and arch["name"] in plan["architect"]["agents_reset"]
    after = registry.agent_detail(qa["id"])
    assert after["model_pref"] is None and registry.agent_detail(arch["id"])["model_pref"] is None
    assert after["config_version"] == version + 1
    assert router.resolve("qa").name == plan["qa"]["model"]
    assert router.auto_routes()["qa"]["agents_reset"] == []


def test_auto_routes_review_in_another_family(monkeypatch):
    from agent_company.db import ModelEntry, session

    monkeypatch.setenv("ENABLE_CLAUDE_CODE", "1")
    monkeypatch.setenv("ENABLE_CODEX", "1")
    reset_settings()
    monkeypatch.setattr(router.shutil, "which", lambda name: f"/usr/bin/{name}")
    with session() as s:
        for m in s.query(ModelEntry).filter(ModelEntry.kind == "llm"):
            m.enabled = False
    plan = router.plan_routes()
    assert plan["developer"]["model"] == "Claude Code" and plan["qa"]["model"] == "Codex CLI"
    assert plan["qa"]["escalation"] == "Claude Code"
