"""Verification phase 1: model registration. Each case goes through the real path (router -> LLMExecutor ->
crewai/OpenAI client -> HTTP) against a local OpenAI-compatible stub that checks the key, the path and the model.
Report: docs/verification/phase-1-models.md."""
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from agent_company import executors, router
from agent_company.db import AuditEvent, ModelEntry, Route, session

GOOD = "sk-phase1-0123456789abcdefWXYZ"


class Stub:
    """A provider: /v1/models and /v1/chat/completions, key `GOOD`, one model `stub-model`; `mode` ok|429|quota|slow."""

    def __init__(self):
        stub = self
        self.mode, self.calls = "ok", []

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body):
                b = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def _refused(self):
                if not self.path.startswith("/v1/"):
                    self._send(404, {"error": {"message": "not found"}})
                elif self.headers.get("Authorization") != f"Bearer {GOOD}":
                    self._send(401, {"error": {"message": "Incorrect API key provided", "code": "invalid_api_key"}})
                else:
                    return False
                return True

            def do_GET(self):
                if not self._refused():
                    self._send(200, {"object": "list", "data": [{"id": "stub-model", "object": "model"}]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                stub.calls.append(body)
                if self._refused():
                    return
                if body.get("model") != "stub-model":
                    return self._send(404, {"error": {"message": f"The model `{body.get('model')}` does not exist",
                                                      "code": "model_not_found"}})
                if stub.mode == "slow":
                    time.sleep(5)
                if stub.mode == "429":  # OpenAI's per-minute limit: transient
                    return self._send(429, {"error": {"message": "Rate limit reached for stub-model in organization "
                                                      "org-x on requests per min (RPM): Limit 3. Please try again in "
                                                      "1.2s.", "code": "rate_limit_exceeded"}})
                if stub.mode == "quota":  # the account's quota: not transient
                    return self._send(429, {"error": {"message": "You exceeded your current quota",
                                                      "type": "insufficient_quota", "code": "insufficient_quota"}})
                self._send(200, {"id": "c1", "object": "chat.completion", "created": 1, "model": "stub-model",
                                 "choices": [{"index": 0, "finish_reason": "stop",
                                              "message": {"role": "assistant", "content": "OK"}}],
                                 "usage": {"prompt_tokens": 9, "completion_tokens": 1, "total_tokens": 10}})

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.srv.server_port}/v1"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()


@pytest.fixture
def stub():
    s = Stub()
    yield s
    s.srv.shutdown()


def _add(stub, name="Stub", **kw):
    return router.add_model({"name": name, "kind": "llm", "model_id": "openai/stub-model", "base_url": stub.url,
                             "api_key": GOOD, **kw})


def _row(model_id):
    with session() as s:
        m = s.get(ModelEntry, model_id)
        s.expunge(m)
        return m


# --- 1.1 / 1.2 registration and the stored record ----------------------------------------------

def test_registration_stores_the_record_with_the_key_encrypted(stub):
    m = _add(stub, input_cost=0.5, output_cost=1.5)
    row = _row(m["id"])
    assert (row.name, row.kind, row.model_id, row.base_url, row.enabled) == (
        "Stub", "llm", "openai/stub-model", stub.url, True)
    assert row.api_key_enc and GOOD not in row.api_key_enc, "the key is stored encrypted"
    assert m["key_hint"] == "sk-...WXYZ" and GOOD not in json.dumps(m)
    assert m["available"] is True and m["status"] == {}, "saved is not tested: no status until a Test runs"
    with pytest.raises(ValueError):
        _add(stub)  # a second model of that name
    for bad in ({"kind": "llm"}, {"name": "x", "kind": "gpt"}, {"name": "x", "kind": "llm"},
                {"name": "x", "kind": "llm", "model_id": "openai/m", "base_url": "ftp://h/v1"},
                {"name": "x", "kind": "llm", "model_id": "openai/m", "api_key": "two words"}):
        with pytest.raises(ValueError):
            router.add_model(bad)


# --- 1.3 a valid key connects ------------------------------------------------------------------

def test_a_valid_model_answers_and_the_result_is_recorded(stub):
    m = _add(stub)
    r = router.test_model(m["id"])
    assert r["ok"] and r["reply"].strip() == "OK" and r["output_tokens"] >= 1
    assert stub.calls[-1]["model"] == "stub-model", "the request reached the provider"
    st = _row(m["id"]).status
    assert st["ok"] is True and st["checked_at"] and "serves stub-model" in st["detail"]


# --- 1.4 failures are identified, never reported as active --------------------------------------

@pytest.mark.parametrize("change, expect", [
    ({"api_key": "sk-wrong-0123456789abcdef"}, "AuthenticationError"),
    ({"base_url": "http://127.0.0.1:1/v1"}, "Connection"),          # nothing listens there
    ({"model_id": "openai/no-such-model"}, "not found"),
])
def test_a_wrong_key_endpoint_or_model_fails_the_test(stub, change, expect):
    m = _add(stub, **change)
    assert m["available"] is True, "configuration alone looks fine"
    r = router.test_model(m["id"])
    assert not r["ok"] and expect in r["error"]
    assert GOOD not in r["error"] and "sk-wrong" not in r["error"], "the key never appears in an error"
    assert _row(m["id"]).status["ok"] is False


def test_a_wrong_path_on_the_right_host_fails_the_test(stub):
    m = _add(stub, base_url=stub.url.removesuffix("/v1") + "/nope")
    r = router.test_model(m["id"])
    assert not r["ok"] and _row(m["id"]).status["ok"] is False


def test_a_disabled_model_is_refused_and_never_routed(stub):
    m = _add(stub)
    router.set_route("analyst", "Stub")
    assert router.resolve("analyst").name == "Stub"
    router.update_model(m["id"], {"enabled": False})
    n = len(stub.calls)
    r = router.test_model(m["id"])
    assert not r["ok"] and r["error"] == "disabled" and len(stub.calls) == n, "no request is sent"
    choice = router.resolve("analyst")
    assert choice.name != "Stub" and "Stub: disabled" in choice.reason


def test_a_timeout_is_identified(stub, monkeypatch):
    monkeypatch.setitem(executors.TOOL_CONTRACTS, "llm", executors.ToolContract(("text", "file_blocks"), 1))
    stub.mode = "slow"
    r = router.test_model(_add(stub)["id"])
    assert not r["ok"] and "timed out" in r["error"].lower()


def test_a_per_minute_rate_limit_is_transient_but_a_quota_benches_the_model(stub):
    stub.mode = "429"
    m = _add(stub)
    r = router.test_model(m["id"])
    assert not r["ok"] and "RateLimitError" in r["error"]
    assert _row(m["id"]).limited_until is None, "a per-minute 429 is retried, not a plan limit"
    stub.mode = "quota"
    r = router.test_model(m["id"])
    assert not r["ok"] and "insufficient_quota" in r["error"]
    assert _row(m["id"]).limited_until is not None, "a used-up quota: the router skips it until the reset"
    router.set_route("analyst", "Stub")
    assert "usage limit reached" in router.resolve("analyst").reason
    stub.mode = "ok"
    assert router.test_model(m["id"])["ok"] and _row(m["id"]).limited_until is None, "a passing Test lifts it"


@pytest.mark.parametrize("error, limit", [
    ("Rate limit reached for gpt-4o in organization org-x on tokens per min (TPM): Limit 30000. "
     "Please try again in 1.2s.", False),
    ("Rate limit exceeded for requests", False),
    ("Error code: 429 - rate_limit_exceeded", False),
    ("You've hit your session limit · resets 9:50pm (Europe/Berlin)", True),
    ("Weekly limit reached for Claude Opus", True),
    ("insufficient_quota", True),
    ("Your credit balance is too low to access the Anthropic API", True),
])
def test_limit_messages_are_told_apart(error, limit):
    assert router.is_limit_error(error) is limit


# --- removal -----------------------------------------------------------------------------------

def test_a_removed_model_is_no_longer_selectable_anywhere(stub):
    m = _add(stub)
    router.set_route("analyst", "Stub", escalation="Stub")
    router.set_route("qa", router.DEFAULT_ROUTE_OTHER, escalation="Stub")
    router.set_fallback("developer", ["Stub"])
    router.delete_model(m["id"])
    assert "Stub" not in {x["name"] for x in router.list_models()}
    assert router.list_routes()["analyst"] != "Stub"
    assert "Stub" not in router.list_escalations().values(), "no route escalates to a removed model"
    assert "Stub" not in router.list_fallbacks().get("developer", [])
    with session() as s:
        assert not s.query(Route).filter((Route.model_name == "Stub") | (Route.escalation == "Stub")).count()
    assert all(c.name != "Stub" for c in router.resolve_chain("analyst", escalate=True))
    with pytest.raises(ValueError):
        router.set_route("analyst", "Stub")
    with pytest.raises(KeyError):
        router.test_model(m["id"])


# --- the API: who may do it, what is audited, the key never leaves ------------------------------

def test_the_api_registers_tests_and_removes_with_audit(stub):
    from agent_company.api import create_app

    app = TestClient(create_app(start_background=False), client=("127.0.0.1", 50000))
    app.post("/api/auth/register", json={"name": "admin", "device_secret": secrets.token_urlsafe(32)})
    guest_secret = secrets.token_urlsafe(32)
    guest = TestClient(app.app)
    guest.post("/api/auth/register", json={"name": "guest", "device_secret": guest_secret})
    body = {"name": "Stub", "kind": "llm", "model_id": "openai/stub-model", "base_url": stub.url, "api_key": GOOD}
    assert guest.post("/api/models", json=body).status_code in (401, 403)
    mid = app.post("/api/models", json=body).json()["id"]
    assert guest.post(f"/api/models/{mid}/test").status_code in (401, 403)
    assert app.post(f"/api/models/{mid}/test").json()["ok"] is True
    for path in ("/api/models", "/api/models/info"):
        assert GOOD not in app.get(path).text
    assert app.delete(f"/api/models/{mid}").json() == {"ok": True}
    assert all(x["name"] != "Stub" for x in app.get("/api/models").json()["models"])
    with session() as s:
        actions = [a.action for a in s.query(AuditEvent).order_by(AuditEvent.id)]
        assert GOOD not in json.dumps([[a.before, a.after] for a in s.query(AuditEvent)], default=str)
    assert {"model_add", "model_test", "model_delete"} <= set(actions)
