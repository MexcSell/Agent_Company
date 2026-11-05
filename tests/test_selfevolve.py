"""Verified self-evolution of the platform's code (selfevolve.py, docs/advanced_self_improving_agent_guide.pdf)."""

import json
import secrets

import pytest
from fastapi.testclient import TestClient

from agent_company import decisions, governance, loop, selfevolve, worktrees
from agent_company.config import REPO_ROOT
from agent_company.db import Goal, session

BENCH = """import json, os, mylib
print(json.dumps({"source": os.path.dirname(mylib.__file__),
                  "metrics": {"accuracy": mylib.ACC, "latency_ms": mylib.LAT}, "classes": {"all": {"acc": mylib.ACC}}}))
"""


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A small repository with a package, a fixed benchmark and tests; work runs in the foreground."""
    r = tmp_path / "platform"
    (r / "src" / "mylib").mkdir(parents=True)
    (r / "benchmarks").mkdir()
    (r / "tests").mkdir()
    (r / "src" / "mylib" / "__init__.py").write_text("ACC = 0.5\nLAT = 10\n")
    (r / "benchmarks" / "fake.py").write_text(BENCH)
    (r / "benchmarks" / "fake.json").write_text(json.dumps({
        "name": "fake", "component": "memory", "script": "fake.py", "repeats": 2,
        "weights": {"accuracy": 3, "latency_ms": -1}, "guard": ["accuracy"], "floors": {"latency_ms": 5}}))
    (r / "tests" / "test_x.py").write_text("def test_x():\n    assert True\n")
    worktrees.git(r, "init", "-q", "-b", "main")
    worktrees.git(r, "add", "-A")
    worktrees.git(r, "commit", "-q", "-m", "init")
    monkeypatch.setattr(selfevolve, "_spawn", lambda fn: fn())
    return r


def _contract(repo, **kw):
    return selfevolve.create("Better accuracy", "Raise accuracy", "memory", "fake", repo_path=str(repo),
                             allowed_paths=kw.pop("allowed_paths", ["src/mylib/*", "tests/*"]), **kw)


def _fake_agents(monkeypatch, files: dict[str, str]):
    """Instead of the goal loop: the candidate branch gets `files`, the goal passes its gate."""
    def enqueue(gid: int) -> None:
        g = loop._goal(gid)
        path, branch, base = worktrees.create(g.repo_path, f"goal-{gid}")
        worktrees.write_files(path, files)
        worktrees.commit_all(path, "candidate")
        with session() as s:
            row = s.get(Goal, gid)
            row.worktree, row.branch, row.base_branch, row.status = str(path), branch, base, "awaiting_approval"
        loop._notify_goal(gid, "awaiting_approval")
    monkeypatch.setattr(loop, "enqueue", enqueue)


def test_protected_control_plane_is_never_a_contract_target(repo):
    with pytest.raises(ValueError, match="protected control plane"):
        selfevolve.create("x", "y", "security", "fake", repo_path=str(repo))
    with pytest.raises(ValueError, match="reach the protected control plane"):
        _contract(repo, allowed_paths=["src/agent_company/*"])
    with pytest.raises(ValueError, match="reach the protected control plane"):
        _contract(repo, allowed_paths=["benchmarks/*"])
    with pytest.raises(ValueError, match="not fixed by changing code"):
        _contract(repo, layer="model")
    assert selfevolve.is_protected("src/agent_company/auth.py")
    assert selfevolve.is_protected("src/agent_company/selfevolve.py") and selfevolve.is_protected("benchmarks/x.py")
    assert not selfevolve.is_protected("src/agent_company/learning.py")


def test_accepted_candidate_deploys_monitors_and_rolls_back(repo, monkeypatch):
    c = _contract(repo)
    assert c["benchmark"]["sha256"] and c["acceptance"]["min_score"] == 2.0 and c["status"] == "draft"
    _fake_agents(monkeypatch, {"src/mylib/__init__.py": "ACC = 0.8\nLAT = 10\n",
                               "tests/test_new.py": "def test_new():\n    assert True\n"})
    selfevolve.start(c["id"])
    c = selfevolve.get(c["id"])
    assert c["baseline"]["metrics"] == {"accuracy": 0.5, "latency_ms": 10.0} and c["baseline"]["runs"] == 2
    cand = c["candidates"][0]
    assert c["status"] == "verified" and cand["verdict"] == "accepted"
    rep = cand["report"]
    assert rep["score_pct"] == pytest.approx(100 * 3 * 0.6 / 4) and rep["accepted"]
    assert set(rep["changed_files"]) == {"src/mylib/__init__.py", "tests/test_new.py"}
    # the coding agent is not the judge: the human decision record holds the merge until deploy
    assert decisions.blocking(cand["goal_id"])
    with pytest.raises(ValueError, match="decision"):
        loop.approve(cand["goal_id"])
    out = selfevolve.deploy(c["id"], "luca")
    assert out["status"] == "monitoring" and out["deployment"]["sha"] == out["merge_commit"]
    assert "ACC = 0.8" in (repo / "src" / "mylib" / "__init__.py").read_text()
    selfevolve._monitor(c["id"])  # not supervised here: the deployed commit is benchmarked again
    c = selfevolve.get(c["id"])
    assert c["status"] == "stable" and c["monitor"]["metrics"]["accuracy"] == 0.8
    assert "not supervised" in c["deployment"]["check"]
    c = selfevolve.rollback(c["id"], "luca", "test")
    assert c["status"] == "rolled_back" and c["rollback_sha"]
    assert "ACC = 0.5" in (repo / "src" / "mylib" / "__init__.py").read_text()


def test_worse_candidate_is_rejected_and_its_goal_too(repo, monkeypatch):
    c = _contract(repo)
    _fake_agents(monkeypatch, {"src/mylib/__init__.py": "ACC = 0.4\nLAT = 1\n"})
    selfevolve.start(c["id"])
    c = selfevolve.get(c["id"])
    cand = c["candidates"][0]
    assert c["status"] == "rejected" and cand["verdict"] == "rejected"
    assert any("critical regression: accuracy" in p for p in cand["report"]["problems"])
    assert loop._goal(cand["goal_id"]).status == "rejected"
    with pytest.raises(ValueError):
        selfevolve.deploy(c["id"], "luca")


def test_candidate_cannot_redefine_success(repo, monkeypatch):
    """Editing the benchmark changes nothing (the contract's copy runs) and touching it rejects the candidate."""
    c = _contract(repo)
    cheat = BENCH.replace("mylib.ACC,", "1.0,")
    _fake_agents(monkeypatch, {"src/mylib/__init__.py": "ACC = 0.5\nLAT = 10\n# tidy\n", "benchmarks/fake.py": cheat})
    selfevolve.start(c["id"])
    rep = selfevolve.get(c["id"])["candidates"][0]["report"]
    assert rep["metrics"][0]["candidate"] == 0.5
    assert any("protected control plane" in p for p in rep["problems"]) and not rep["accepted"]


def test_another_candidate_and_the_best_one_is_deployed(repo, monkeypatch):
    c = _contract(repo)
    _fake_agents(monkeypatch, {"src/mylib/__init__.py": "ACC = 0.6\nLAT = 10\n"})
    selfevolve.start(c["id"])
    _fake_agents(monkeypatch, {"src/mylib/__init__.py": "ACC = 0.9\nLAT = 10\n"})
    selfevolve.start(c["id"])  # §14: a second candidate against the same fixed benchmark
    c = selfevolve.get(c["id"])
    first, second = c["candidates"]
    assert first["verdict"] == second["verdict"] == "accepted"
    assert "candidate 2 scored higher" in selfevolve.merge_block(first["goal_id"])
    selfevolve.deploy(c["id"], "luca")
    assert "ACC = 0.9" in (repo / "src" / "mylib" / "__init__.py").read_text()


def test_refused_restart_is_rolled_back_automatically(repo, monkeypatch):
    from agent_company import supervisor

    c = _contract(repo)
    _fake_agents(monkeypatch, {"src/mylib/__init__.py": "ACC = 0.9\nLAT = 10\n"})
    selfevolve.start(c["id"])
    c = selfevolve.deploy(c["id"], "luca")
    later = c["deployment"]["at"] + 5
    monkeypatch.setattr(supervisor, "read_state", lambda: {"running": True, "last_error": "syntax check failed",
                                                           "last_error_at": later, "last_restart": None})
    selfevolve._monitor(c["id"])
    c = selfevolve.get(c["id"])
    assert c["status"] == "rolled_back" and "refused" in c["note"]
    assert "ACC = 0.5" in (repo / "src" / "mylib" / "__init__.py").read_text()


def test_failed_goal_fails_the_candidate(repo, monkeypatch):
    c = _contract(repo)

    def enqueue(gid):
        with session() as s:
            s.get(Goal, gid).status = "failed"
    monkeypatch.setattr(loop, "enqueue", enqueue)
    selfevolve.start(c["id"])
    assert selfevolve.advance() >= 1  # the hook missed it (e.g. a restart): the monitor picks it up
    c = selfevolve.get(c["id"])
    assert c["status"] == "failed" and c["candidates"][0]["verdict"] == "failed"


def test_compare_weights_floors_and_guards():
    bench = {"weights": {"acc": 2, "dup": -1, "lat": -1}, "guard": ["acc"], "floors": {"dup": 0.05, "lat": 5}}
    ok = selfevolve.compare({"acc": 0.5, "dup": 0.0, "lat": 1}, {"acc": 0.6, "dup": 0.0, "lat": 1.5}, bench,
                            {"min_score": 2, "max_regression": 25})
    assert ok["accepted"] and ok["score_pct"] == pytest.approx(100 * (2 * 0.2 - 0.1) / 4)
    bad = selfevolve.compare({"acc": 0.5, "dup": 0.0, "lat": 1}, {"acc": 0.9, "dup": 0.1, "lat": 1}, bench,
                             {"min_score": 2, "max_regression": 25})
    assert not bad["accepted"] and any(p.startswith("regression: dup") for p in bad["problems"])
    flat = selfevolve.compare({"acc": 0.5}, {"acc": 0.5}, {"weights": {"acc": 1}}, {"min_score": 2})
    assert not flat["accepted"] and "below the required" in flat["problems"][0]


def test_layer_diagnosis_and_weaknesses():
    assert selfevolve.diagnose_layer("HTTP 429 rate limit from provider")["layer"] == "model"
    assert selfevolve.diagnose_layer("Traceback ... KeyError: 'x'")["layer"] == "code"
    assert selfevolve.diagnose_layer("skipped: a dependency failed")["layer"] == "orchestration"
    assert selfevolve.diagnose_layer("permission denied: /etc")["layer"] == "environment"
    gid = loop.create_goal("Find notes")
    with session() as s:
        g = s.get(Goal, gid)
        g.status, g.result = "failed", 'Traceback: File "src/agent_company/learning.py", line 9\nTypeError: bad'
    w = selfevolve.weaknesses()
    assert w[0]["layer"] == "code" and w[0]["component"] == "memory" and w[0]["contract_possible"]


def test_agent_score_weights_by_role():
    m = {"tasks": 4, "role": "developer", "completion_rate": 1.0, "goal_pass_rate": 1.0, "reliability": 1.0,
         "rework_rate": 0.0, "avg_task_seconds": 0, "cost": 0}
    assert selfevolve.agent_score(m) == 100.0
    worse = selfevolve.agent_score(m | {"goal_pass_rate": 0.5, "cost": 2.0})
    assert worse < 100 and selfevolve.agent_score({"tasks": 0}) is None


def test_retrieval_benchmark_runs_on_this_checkout(tmp_path):
    bench = selfevolve.load_benchmark(REPO_ROOT, "retrieval")
    assert bench["component"] == "memory" and "hit_at_3" in bench["guard"]
    out = selfevolve.run_benchmark(REPO_ROOT, bench, tmp_path)
    assert set(bench["weights"]) <= set(out["metrics"]) and 0 < out["metrics"]["hit_at_3"] <= 1
    assert {"exact", "concept", "recent", "document", "complex"} <= set(out["classes"])


def test_api_is_admin_only_and_audited(repo):
    with TestClient(selfevolve_app(), client=("127.0.0.1", 50000)) as c:
        r = c.post("/api/auth/register", json={"name": "luca", "device_secret": secrets.token_urlsafe(32)})
        assert r.status_code == 200
        o = c.get("/api/improvement").json()
        assert any(x["name"] == "security" and x["protected"] for x in o["components"])
        assert any(b["name"] == "retrieval" for b in o["benchmarks"])
        r = c.post("/api/improvement/contracts", json={"title": "t", "objective": "o", "component": "security",
                                                       "benchmark": "retrieval"})
        assert r.status_code == 400 and "protected" in r.json()["detail"]
        assert c.post("/api/improvement/contracts/999/deploy").status_code == 404
        # on the platform project (this checkout): writing a contract only reads the repository
        r = c.post("/api/improvement/contracts", json={"title": "Retrieval", "objective": "Rank better",
                                                       "component": "memory", "benchmark": "retrieval"})
        assert r.status_code == 200 and r.json()["allowed_paths"] == ["src/agent_company/learning.py", "tests/*"]
        assert c.post(f"/api/improvement/contracts/{r.json()['id']}/cancel").json()["status"] == "cancelled"
        assert [a["action"] for a in governance.list_audit(action="improvement_create")]
        other = TestClient(c.app)
        assert other.get("/api/improvement").status_code in (401, 403)


def selfevolve_app():
    from agent_company.api import create_app

    return create_app(start_background=False)
