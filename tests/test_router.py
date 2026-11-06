import httpx

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
    # developer routes to Claude Code, which is off by default -> next candidate is offline
    assert router.resolve("developer").kind == "offline"
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
    assert router.resolve("general_manager").kind == "offline"
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
