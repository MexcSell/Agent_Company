"""SPEC-01 FR-08 (S01-R-06): department KPIs with definition, formula, target, interval, data source, responsible
room and historical values with their evidence."""

import secrets
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_company import kpis, registry
from agent_company.api import create_app
from agent_company.db import Agent, AuditEvent, Defect, Goal, Incident, Kpi, KpiValue, Lesson, Room, Run, Task, now, session


def _kpi(room: str, metric: str) -> dict:
    return next(k for k in kpis.list_kpis() if k["room"] == room and k["metric"] == metric)


def _goal(status: str, agent_id: int, task_status: str = "done", at=None) -> int:
    at = at or now()
    with session() as s:
        g = Goal(title=f"g {status}", description="", status=status, updated_at=at)
        s.add(g)
        s.flush()
        s.add(Task(goal_id=g.id, iteration=1, key="build", title="build", role="developer", agent_id=agent_id,
                   status=task_status, finished_at=at))
        return g.id


def test_defaults_follow_the_specification_and_are_not_added_again():
    rows = {(k["room"], k["metric"]) for k in kpis.list_kpis()}
    assert {("development", "goal_delivery_rate"), ("development", "defect_rate"),
            ("security", "incident_resolution_hours"), ("study_room", "skill_improvement")} <= rows
    k = _kpi("development", "defect_rate")
    assert k["direction"] == "lower" and k["interval"] == "weekly" and k["data_source"] == "defects, tasks"
    assert k["definition"] and k["formula"] and k["target"] == 0.5 and k["history"] == []
    kpis.update(k["id"], "user:luca", enabled=False)
    kpis.seed()
    assert ("development", "defect_rate") not in {(x["room"], x["metric"]) for x in kpis.list_kpis()}
    assert any(x["id"] == k["id"] for x in kpis.list_kpis(include_disabled=True))


def test_delivery_and_defect_rate_are_measured_with_their_evidence_and_compared_over_time():
    dev = registry.staff("developer").id
    old = now() - timedelta(days=10)  # outside the weekly period
    _goal("approved", dev, at=old)
    g1, g2, g3 = _goal("approved", dev), _goal("approved", dev), _goal("failed", dev, "failed")
    with session() as s:
        s.add(Defect(goal_id=g3, check="pytest", severity="high"))
    d = _kpi("development", "goal_delivery_rate")
    v = kpis.measure(d["id"], "user:luca")
    assert v["value"] == pytest.approx(2 / 3) and v["samples"] == 3 and v["met"] is False and v["target"] == 0.8
    assert v["evidence"] == {"goals": sorted([g1, g2, g3])}
    r = kpis.measure(_kpi("development", "defect_rate")["id"])
    assert r["value"] == pytest.approx(1 / 3) and r["met"] is True and len(r["evidence"]["defects"]) == 1

    _goal("approved", dev)  # one more accepted goal, then a new measurement
    kpis.update(d["id"], "user:luca", target=0.7)
    v2 = kpis.measure(d["id"])
    assert v2["value"] == pytest.approx(3 / 4) and v2["met"] is True and v2["target"] == 0.7
    k = kpis.get(d["id"])
    assert [x["target"] for x in k["history"]] == [0.7, 0.8]  # each value keeps the target it was judged against
    assert k["change"] == pytest.approx(3 / 4 - 2 / 3, abs=1e-6)
    cmp = kpis.compare(d["id"])
    assert [x["value"] for x in cmp["series"]] == [pytest.approx(2 / 3), pytest.approx(3 / 4)]
    assert cmp["series"][1]["change"] == pytest.approx(3 / 4 - 2 / 3, abs=1e-6) and (cmp["met"], cmp["judged"]) == (1, 2)


def test_a_period_without_records_has_no_value_and_is_not_judged():
    v = kpis.measure(_kpi("development", "goal_delivery_rate")["id"])
    assert v["value"] is None and v["met"] is None and v["samples"] == 0


def test_other_formulas():
    with session() as s:
        s.add(Incident(category="tool_misuse", title="x", status="closed", created_at=now() - timedelta(hours=6),
                       updated_at=now()))
        s.add(Incident(category="tool_misuse", title="y", status="open"))
    v = kpis.measure(_kpi("security", "incident_resolution_hours")["id"])
    assert v["value"] == pytest.approx(6, abs=0.01) and v["met"] is True and len(v["evidence"]["incidents"]) == 1

    dev = registry.staff("developer")
    with session() as s:
        s.add(Run(model_name="m", kind="task", agent_id=dev.id, cost=0.5, started_at=now()))
    k = kpis.create("development", "utilization", 0.5, actor="user:luca")
    v = kpis.measure(k["id"])
    assert 0 < v["value"] <= 1 and v["evidence"]["agents"] == [dev.id]
    _goal("approved", dev.id)
    assert kpis.measure(kpis.create("development", "cost_per_task")["id"])["value"] == pytest.approx(0.5)

    researcher = registry.staff("researcher")
    with session() as s:
        room = researcher.room_key
        s.add(Lesson(agent_id=researcher.id, text="a finding", outcome="research", kind="research", status="approved"))
        s.add(Lesson(agent_id=researcher.id, text="a draft", outcome="research", kind="research", status="draft"))
    v = kpis.measure(kpis.create(room, "verified_findings", 1)["id"])
    assert v["value"] == 1 and v["met"] is True


def test_definitions_are_validated():
    with pytest.raises(ValueError, match="metric is one of"):
        kpis.create("development", "happiness")
    with pytest.raises(ValueError, match="interval is one of"):
        kpis.create("development", "defect_rate", interval="hourly")
    with pytest.raises(KeyError):
        kpis.create("nowhere", "defect_rate")
    k = kpis.create("development", "task_success_rate", 0.9, name="Build success", interval="daily")
    assert (k["name"], k["direction"], k["interval"]) == ("Build success", "higher", "daily")
    assert kpis.update(k["id"], "user:luca", clear_target=True)["target"] is None


def test_measure_due_once_per_interval_and_only_for_enabled_staffed_rooms():
    with session() as s:
        staffed = set(s.scalars(select(Agent.room_key).where(Agent.enabled, Agent.lifecycle == "active")))
    measured = [k for k in kpis.list_kpis() if k["room"] in staffed]
    assert measured and len(measured) < len(kpis.list_kpis())  # security, study room ... have no enabled roles
    assert kpis.measure_due() == len(measured)
    assert all(k["history"] == [] for k in kpis.list_kpis() if k["room"] not in staffed)
    assert kpis.measure_due() == 0
    assert kpis.measure_due(now() + timedelta(days=8)) >= 1  # weekly ones are due again
    with session() as s:
        s.scalars(select(Room).where(Room.key == "development")).one().enabled = False
        k = s.scalars(select(Kpi).where(Kpi.room_key == "development")).first()
        k.last_measured_at = None
        kid = k.id
        before = len(s.scalars(select(KpiValue).where(KpiValue.kpi_id == kid)).all())
    kpis.measure_due()
    with session() as s:
        assert s.get(Kpi, kid).last_measured_at is None
        assert len(s.scalars(select(KpiValue).where(KpiValue.kpi_id == kid)).all()) == before


def test_api_lists_for_everyone_and_lets_administrators_define_and_measure():
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as c:
        c.post("/api/auth/register", json={"name": "luca", "device_secret": secrets.token_urlsafe(32)})
        r = c.get("/api/kpis").json()
        assert r["kpis"] and {m["key"] for m in r["metrics"]} == set(kpis.METRICS) and r["intervals"]
        k = c.post("/api/kpis", json={"room": "development", "metric": "task_success_rate", "target": 0.9}).json()
        assert k["room"] == "development" and k["unit"] == "ratio"
        assert c.post("/api/kpis", json={"room": "development", "metric": "nope"}).status_code == 400
        v = c.post(f"/api/kpis/{k['id']}/measure").json()
        assert v["value"] is None and v["measured_by"] == "user:luca"
        out = c.patch(f"/api/kpis/{k['id']}", json={"target": 0.8, "interval": "monthly"}).json()
        assert (out["target"], out["interval"]) == (0.8, "monthly")
        detail = c.get(f"/api/kpis/{k['id']}").json()
        assert len(detail["history"]) == 1 and detail["comparison"]["judged"] == 0
        assert c.get("/api/kpis/9999").status_code == 404
        with session() as s:
            actions = {a.action for a in s.scalars(select(AuditEvent))}
        assert {"kpi_create", "kpi_measure", "kpi_update"} <= actions
