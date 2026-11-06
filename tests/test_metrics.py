import secrets

from fastapi.testclient import TestClient

from agent_company import governance, loop, usage
from agent_company.api import create_app


def test_metrics_need_a_session_or_the_token_and_count_what_exists():
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as c:
        anon = TestClient(c.app, client=("127.0.0.1", 50001))
        assert anon.get("/metrics").status_code == 401
        c.post("/api/auth/register", json={"name": "admin", "device_secret": secrets.token_urlsafe(32)})
        loop.create_goal("A", quality_commands=["true"])
        loop.create_goal("B", quality_commands=["true"])
        usage.record(model_name="Offline simulator", kind="offline", input_tokens=10, output_tokens=5, cost=0.5)
        governance.open_incident("policy_violation", "SEV-2", "x", "test")
        r = c.get("/metrics")
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain; version=0.0.4")
        body = r.text
        assert "# TYPE agent_company_goals gauge" in body and 'agent_company_goals{status="queued"} 2' in body
        assert 'agent_company_runs_total{kind="offline",ok="true"} 1' in body
        assert 'agent_company_tokens_total{kind="offline",direction="input"} 10' in body
        assert 'agent_company_cost_usd_total{kind="offline"} 0.5' in body
        assert 'agent_company_incidents_open{severity="SEV-2"} 1' in body
        assert 'agent_company_emergency_stop{level="E5"} 0' in body
        assert all(line.startswith(("# HELP", "# TYPE", "agent_company_")) for line in body.strip().splitlines())
        assert "\nA" not in body and '"A"' not in body, "no goal titles in labels"
        # A scraper with the token; a wrong or revoked token is refused.
        assert c.get("/api/metrics/token").json() == {"set": False, "hint": ""}
        token = c.post("/api/metrics/token").json()["token"]
        assert anon.get("/metrics", headers={"Authorization": f"Bearer {token}"}).status_code == 200
        assert anon.get("/metrics", headers={"Authorization": "Bearer acm_wrong"}).status_code == 401
        assert c.get("/api/metrics/token").json()["set"] is True
        c.delete("/api/metrics/token")
        assert anon.get("/metrics", headers={"Authorization": f"Bearer {token}"}).status_code == 401
        assert {"metrics_token_create", "metrics_token_revoke"} <= {a["action"] for a in c.get("/api/audit").json()["records"]}
