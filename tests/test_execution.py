"""Execution-quality features taken from /home/thomas/bit and the SPEC14 security spec."""


import pytest
from sqlalchemy import select

from agent_company import checks, executors, loop, prefs, router, usage, worktrees
from agent_company.db import Run, session


def repo_with(tmp_path, files):
    repo = tmp_path / "proj"
    repo.mkdir()
    worktrees.git(repo, "init", "-q", "-b", "main")
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    worktrees.git(repo, "add", ".")
    worktrees.git(repo, "commit", "-q", "-m", "init")
    return repo


def wt_with_change(tmp_path, base_files, change):
    repo = repo_with(tmp_path, base_files)
    wt, _, base = worktrees.create(repo, "t")
    worktrees.write_files(wt, change)
    worktrees.commit_all(wt, "change")
    return wt, base


def test_secret_scan_flags_added_credentials_not_placeholders(tmp_path):
    wt, base = wt_with_change(tmp_path, {"a.py": "x = 1\n"}, {
        "a.py": 'x = 1\nKEY = "sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYz0123"\n',
        "conf.py": 'password = "example-password-here"\n',
        "k.pem": "-----BEGIN RSA PRIVATE KEY-----\nMIIE\n"})
    hits = checks.scan_secrets(wt, base)
    assert hits == ["a.py: Anthropic key", "k.pem: private key"]


def test_syntax_check_of_changed_files(tmp_path):
    wt, base = wt_with_change(tmp_path, {"ok.py": "a = 1\n"}, {
        "ok.py": "a = 2\n", "bad.py": "def f(:\n", "bad.json": "{oops}", "good.json": "[1]", "bad.toml": "x = ",
        "bad.sh": "if then fi\n"})
    errs = checks.syntax_errors(wt, worktrees.changed_files(wt, base))
    bad = sorted(e.split(":")[0] for e in errs)
    assert bad == ["bad.json", "bad.py", "bad.sh", "bad.toml"]


def test_scope_and_protected_tests(tmp_path):
    wt, base = wt_with_change(tmp_path, {"src/app.py": "a = 1\n", "tests/test_app.py": "def test(): pass\n"},
                              {"src/app.py": "a = 2\n", "docs/x.md": "hi", "tests/test_app.py": "def test(): 1\n",
                               "tests/test_new.py": "def test_new(): pass\n"})
    v = checks.scope_violations(wt, base, ["src/*", "tests/*"], protect_tests=True)
    assert any("docs/x.md: outside" in x for x in v)
    assert any("tests/test_app.py: existing test changed" in x for x in v)
    assert not any("test_new.py" in x for x in v), "new tests are welcome"
    assert checks.scope_violations(wt, base, [], protect_tests=False) == []


def test_is_test_file():
    assert checks.is_test_file("tests/x.py") and checks.is_test_file("pkg/test_a.py")
    assert checks.is_test_file("web/a.spec.ts") and checks.is_test_file("x_test.go")
    assert not checks.is_test_file("django/test/client.py") and not checks.is_test_file("src/app.py")


def test_extract_check_commands():
    text = ("Fix the bug. Run `uv run pytest tests/test_x.py -q` and `ruff check .`; do not run `rm -rf /`.\n"
            "```bash\n$ npm test\nnpm run lint\n```")
    assert checks.extract_check_commands(text) == ["npm test", "npm run lint", "uv run pytest tests/test_x.py -q",
                                                   "ruff check ."]
    gid = loop.create_goal("Fix it; check with `pytest -q`", extract_checks=True)
    assert loop._goal(gid).quality_commands == ["pytest -q"]
    gid = loop.create_goal("Fix it; check with `pytest -q`")  # untrusted author: nothing is extracted
    assert loop._goal(gid).quality_commands == []


def test_gate_includes_static_checks(tmp_path):
    repo = repo_with(tmp_path, {"README.md": "x"})
    gid = loop.create_goal("Add a worklog", repo_path=str(repo), allowed_paths=["docs/*"])
    out = loop.run_goal(gid)
    names = {c["check"]: c for c in out["report"]["checks"]}
    assert names["secret scan"]["passed"] and names["syntax"]["passed"]
    assert not names["scope"]["passed"] and "WORKLOG.md: outside" in names["scope"]["detail"]
    assert out["status"] == "failed"


def test_baseline_marks_preexisting_failures(tmp_path):
    repo = repo_with(tmp_path, {"README.md": "x"})
    gid = loop.create_goal("Do it", repo_path=str(repo), quality_commands=["test -f MISSING"], max_iterations=1)
    out = loop.run_goal(gid)
    g = loop._goal(gid)
    assert g.baseline["test -f MISSING"]["passed"] is False
    row = next(c for c in out["report"]["checks"] if c["check"].startswith("command"))
    assert row["preexisting"] and not row["passed"] and out["status"] == "failed"
    prefs.set_value("allow_preexisting_failures", True)
    gid = loop.create_goal("Do it", repo_path=str(repo), quality_commands=["test -f MISSING"], max_iterations=1)
    assert loop.run_goal(gid)["status"] == "awaiting_approval"


def test_keeps_the_best_iteration(monkeypatch):
    # iteration 1 passes every check but the command; iteration 2's reviewer also fails -> lower score
    calls = {"n": 0}
    real = executors.OfflineExecutor._task

    def task(self, req):
        if req.role == "qa":
            calls["n"] += 1
            if calls["n"] == 2:
                return "Looks wrong.\nVERDICT: FAIL"
        return real(self, req)

    monkeypatch.setattr(executors.OfflineExecutor, "_task", task)
    gid = loop.create_goal("Two tries", quality_commands=["false"], max_iterations=2)
    loop.run_goal(gid)
    g = loop._goal(gid)
    assert [h["iteration"] for h in g.history] == [1, 2] and g.history[0]["score"] > g.history[1]["score"]
    assert "Kept iteration 1" in g.result
    assert worktrees.git(g.worktree, "rev-parse", "HEAD") == g.history[0]["commit"]


def pricey(monkeypatch, cost=0.01):
    """Pretend every model call costs `cost`."""
    real = usage.record

    def record(**kw):
        kw["cost"] = cost
        return real(**kw)

    monkeypatch.setattr(usage, "record", record)


def test_an_exhausted_budget_pauses_the_goal_and_resume_checks_it_again(monkeypatch):
    from agent_company.db import Goal, Task

    monkeypatch.setattr(loop, "enqueue", lambda gid: None)
    prefs.set_value("goal_budget_usd", 0.01)
    pricey(monkeypatch)
    gid = loop.create_goal("Budgeted", quality_commands=["test -f WORKLOG.md"])
    out = loop.run_goal(gid)
    assert out["status"] == "paused" and "cost budget" in out["budget"]
    with session() as s:
        g = s.get(Goal, gid)
        assert g.status == "paused" and g.checkpoint["paused_for"] == "budget"
        assert not g.history, "an iteration cut short by the budget is not judged"
        assert any(t.output.startswith(loop.BUDGET_SKIP) for t in s.scalars(select(Task).where(Task.goal_id == gid)))
    with pytest.raises(ValueError, match="still over budget"):
        loop.resume(gid, actor="user:admin")
    prefs.set_value("goal_budget_usd", 100.0)
    loop.resume(gid, actor="user:admin")
    out = loop.run_goal(gid)
    assert out["status"] == "awaiting_approval" and out["iterations"] == 1
    with session() as s:
        assert {t.status for t in s.scalars(select(Task).where(Task.goal_id == gid))} == {"done"}
        assert "paused_for" not in s.get(Goal, gid).checkpoint


def test_a_benchmark_run_over_budget_ends_instead_of_pausing(monkeypatch):
    from agent_company import evaluation

    prefs.set_value("goal_budget_usd", 0.01)
    pricey(monkeypatch)
    gid = loop.create_goal("Benchmark", quality_commands=["test -f WORKLOG.md"], origin="eval")
    assert loop.run_goal(gid)["status"] == "failed", "nobody resumes an eval goal"
    assert evaluation._end_reason(loop._goal(gid)) == "out_of_budget"


def test_a_missing_checkpoint_commit_does_not_fail_the_resume():
    from agent_company.db import Event, Goal

    gid = loop.create_goal("Fail open", quality_commands=["test -f WORKLOG.md"])
    loop.run_goal(gid)
    with session() as s:
        g = s.get(Goal, gid)
        g.checkpoint = {**g.checkpoint, "commit": "0" * 40}
    loop._reconcile(gid)
    with session() as s:
        assert any("could not reset the worktree" in e.message
                   for e in s.scalars(select(Event).where(Event.goal_id == gid)))


def test_spending_twice_the_estimate_pauses_the_goal(monkeypatch):
    """S14-R-03: cost of one goal > 2x its estimate → pause; resuming accepts the cost so far."""
    from agent_company.db import Goal

    monkeypatch.setattr(loop, "enqueue", lambda gid: None)
    pricey(monkeypatch)
    gid = loop.create_goal("Estimated", quality_commands=["test -f WORKLOG.md"], estimated_cost_usd=0.01)
    out = loop.run_goal(gid)
    assert out["status"] == "paused" and "estimate" in out["budget"]
    with session() as s:
        assert s.get(Goal, gid).checkpoint["paused_for"] == "cost_overrun"
    spent = usage.goal_spend(gid)
    loop.resume(gid, actor="user:admin")
    with session() as s:
        assert s.get(Goal, gid).estimated_cost_usd == pytest.approx(spent)
    assert loop.run_goal(gid)["status"] == "awaiting_approval"


def test_the_cost_overrun_factor_is_a_setting(monkeypatch):
    """The owner raised the brake to 3x the estimate: a goal between 2x and 3x keeps running."""
    from agent_company.db import Goal

    monkeypatch.setattr(loop, "enqueue", lambda gid: None)
    gid = loop.create_goal("Estimated", quality_commands=["test -f WORKLOG.md"], estimated_cost_usd=1.0)
    g = loop._goal(gid)
    monkeypatch.setattr(usage, "goal_spend", lambda goal_id: 2.5)
    with pytest.raises(loop.CostOverrun, match="2× the estimate"):
        loop._budget_note(g, 0.0)
    prefs.set_value("cost_overrun_factor", 3)
    loop._budget_note(g, 0.0)
    monkeypatch.setattr(usage, "goal_spend", lambda goal_id: 3.5)
    with pytest.raises(loop.CostOverrun, match="3× the estimate"):
        loop._budget_note(g, 0.0)
    with pytest.raises(ValueError):
        prefs.set_value("cost_overrun_factor", 0.5)
    with session() as s:
        assert s.get(Goal, gid).estimated_cost_usd == 1.0


def test_the_api_takes_an_estimate_and_refuses_to_resume_a_goal_still_over_budget(monkeypatch):
    import secrets

    from fastapi.testclient import TestClient

    from agent_company.api import create_app

    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as app:
        app.app.state.queue = type("Q", (), {"submit": lambda self, gid: None})()
        app.post("/api/auth/register", json={"name": "luca", "device_secret": secrets.token_urlsafe(32)})
        gid = app.post("/api/goals", json={"title": "Estimated order", "estimated_cost_usd": 0.5}).json()["id"]
        assert app.get(f"/api/goals/{gid}").json()["estimated_cost_usd"] == 0.5
        prefs.set_value("goal_budget_usd", 0.01)
        pricey(monkeypatch)
        assert loop.run_goal(gid)["status"] == "paused"
        assert app.get(f"/api/goals/{gid}").json()["paused_for"] == "budget"
        r = app.post(f"/api/goals/{gid}/resume")
        assert r.status_code == 400 and "still over budget" in r.json()["detail"]
        assert app.put("/api/settings/budget_exhausted_action", json={"value": "never"}).status_code == 400


def test_the_planner_sets_the_estimate_unless_the_order_did(monkeypatch):
    from agent_company.db import Goal

    real = executors.OfflineExecutor._plan

    def plan(self, req):
        import json

        return json.dumps({**json.loads(real(self, req)), "estimated_cost_usd": 3.5})

    monkeypatch.setattr(executors.OfflineExecutor, "_plan", plan)
    planned = loop.create_goal("Planner estimate", quality_commands=["test -f WORKLOG.md"])
    ordered = loop.create_goal("Ordered estimate", quality_commands=["test -f WORKLOG.md"], estimated_cost_usd=1.0)
    loop.run_goal(planned)
    loop.run_goal(ordered)
    with session() as s:
        assert s.get(Goal, planned).estimated_cost_usd == 3.5
        assert s.get(Goal, ordered).estimated_cost_usd == 1.0


def test_cost_budget_stops_the_goal():
    prefs.set_value("budget_exhausted_action", "stop")
    prefs.set_value("goal_budget_usd", 0.01)
    gid = loop.create_goal("Expensive", quality_commands=["false"], max_iterations=3)
    real = usage.record

    def pricey(**kw):  # pretend every model call costs a cent
        kw["cost"] = 0.01
        return real(**kw)

    import agent_company.loop as L

    orig = L.usage.record
    L.usage.record = pricey
    try:
        out = loop.run_goal(gid)
    finally:
        L.usage.record = orig
    assert out["status"] == "failed" and "budget" in out.get("budget", "")
    assert "cost budget" in loop._goal(gid).result
    with session() as s:
        assert s.scalars(select(Run).where(Run.goal_id == gid)).first() is not None


def test_budget_wrap_up_note(monkeypatch):
    prefs.set_value("goal_budget_usd", 1.0)
    gid = loop.create_goal("x")
    usage.record(model_name="Offline simulator", kind="offline", cost=0.7, goal_id=gid)
    note = loop._budget_note(loop._goal(gid), __import__("time").monotonic())
    assert "70% of this goal's cost budget" in note


def test_fallback_goes_to_the_next_real_model_never_to_the_simulator(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    tried = []

    class Failing(executors.Executor):
        kind = "llm"

        def _run(self, req):
            tried.append(self.choice.name)
            raise RuntimeError("503 overloaded")

    monkeypatch.setitem(executors.EXECUTORS, "llm", Failing)
    from agent_company import registry

    from agent_company import router

    router.set_route("general_manager", "Claude Opus 5.5")  # the default GM (Claude Code) needs a worktree
    gm = registry.staff("general_manager")
    gm.model_pref = "Claude Sonnet 5.5"
    gid = loop.create_goal("Plan only")
    req = executors.Request(purpose="plan", role="general_manager", system="s", prompt="p", data={"goal": "g"})
    res, choice = loop._execute(gm, req, "plan", goal_id=gid)
    # a 503 is transient: each model is retried (retry_max_attempts = 3) before the next one is tried
    assert tried == ["Claude Sonnet 5.5"] * 3 + ["Claude Opus 5.5"] * 3, "preference first, then the route"
    assert not res.ok and choice.kind == "llm", "a real failure stands; no simulated stand-in"
    with session() as s:
        assert [r.ok for r in s.scalars(select(Run).where(Run.goal_id == gid))] == [False] * 6


def test_simulator_runs_only_when_nothing_real_is_usable():
    from agent_company import registry

    gm = registry.staff("general_manager")
    req = executors.Request(purpose="plan", role="general_manager", system="s", prompt="p", data={"goal": "g"})
    res, choice = loop._execute(gm, req, "plan")
    assert res.ok and choice.kind == "offline"


def test_escalation_and_cross_family(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert router.resolve("qa").name == "Claude Sonnet 5.5"
    assert router.resolve("qa", escalate=True).name == "Claude Opus 5.5"
    # with only Anthropic keys there is no other family: keep the route
    assert router.resolve("qa", avoid_provider="anthropic").name == "Claude Sonnet 5.5"
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    c = router.resolve("qa", avoid_provider="anthropic")
    assert c.provider != "anthropic" and "different family" in c.reason


def test_base_url_models_and_model_test(monkeypatch):
    m = router.add_model({"name": "Local Qwen", "kind": "llm", "model_id": "openai/qwen3-coder",
                          "base_url": "http://127.0.0.1:18100/v1"})
    assert m["available"] is True
    with pytest.raises(ValueError):
        router.add_model({"name": "Bad", "kind": "llm", "model_id": "openai/x", "base_url": "file:///etc"})
    keyed = router.add_model({"name": "Keyed", "kind": "llm", "model_id": "openai/x",
                              "base_url": "http://10.0.0.5:8000/v1", "api_key_env": "LOCAL_KEY"})
    assert keyed["available"] is False and "LOCAL_KEY" in keyed["unavailable_reason"]
    seen = {}

    class Fake(executors.Executor):
        kind = "llm"

        def _run(self, req):
            seen.update(base_url=self.choice.base_url, model=self.choice.model_id)
            return executors.Result(text="OK", input_tokens=5, output_tokens=1)

    monkeypatch.setitem(executors.EXECUTORS, "llm", Fake)
    out = router.test_model(m["id"])
    assert out["ok"] and out["reply"] == "OK" and seen["base_url"] == "http://127.0.0.1:18100/v1"
    offline = next(x for x in router.list_models() if x["kind"] == "offline")
    assert router.test_model(offline["id"])["reply"] == "OK"


def test_claude_code_runs_resume_the_agents_session(monkeypatch, env):
    import json as _json

    from agent_company.config import settings

    calls = []

    class Proc:
        returncode = 0
        stderr = ""

        def __init__(self, cmd):
            self.stdout = _json.dumps({"result": "done", "session_id": "0f9c2b9e-1111-2222-3333-444455556666",
                                       "usage": {"input_tokens": 10, "output_tokens": 5}, "total_cost_usd": 0.01})

    def fake_spawn(self, cmd, req, cwd, env=None, **kw):
        calls.append(cmd)
        return Proc(cmd)

    monkeypatch.setattr(executors.Executor, "_spawn", fake_spawn)
    wt = settings().worktrees_dir / "cc"
    wt.mkdir(parents=True)
    choice = router.Choice("Claude Code", "claude_code", "claude-opus-5-5", "test")
    ex = executors.ClaudeCodeExecutor(choice)
    first = ex.run(executors.Request(purpose="task", role="developer", system="s", prompt="p", workdir=wt, writes=True))
    assert first.ok and first.session_id.startswith("0f9c") and "--resume" not in calls[0]
    ex.run(executors.Request(purpose="task", role="developer", system="s", prompt="p", workdir=wt, writes=True,
                             data={"resume": first.session_id}))
    assert calls[1][calls[1].index("--resume") + 1] == first.session_id
    ex.run(executors.Request(purpose="task", role="developer", system="s", prompt="p", workdir=wt,
                             data={"resume": "x; rm -rf /"}))
    assert "--resume" not in calls[2], "only real session ids are passed on"
    pinned = executors.ClaudeCodeExecutor(router.Choice("Pinned", "claude_code", "", "test",
                                                        session_id="3067d536-a81c-492b-9302-1b000247bdea"))
    pinned.run(executors.Request(purpose="task", role="developer", system="s", prompt="p", workdir=wt))
    assert calls[3][calls[3].index("--resume") + 1].startswith("3067d536") and "--fork-session" in calls[3]
    pinned.run(executors.Request(purpose="task", role="developer", system="s", prompt="p", workdir=wt,
                                 data={"resume": first.session_id}))
    assert calls[4][calls[4].index("--resume") + 1] == first.session_id and "--fork-session" not in calls[4]


def test_claude_code_model_pinned_to_a_session(monkeypatch, tmp_path):
    from agent_company.config import settings

    monkeypatch.setattr(settings(), "enable_claude_code", True)
    monkeypatch.setattr(router.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    sid = "3067d536-a81c-492b-9302-1b000247bdea"
    with pytest.raises(ValueError):
        router.add_model({"name": "Bad", "kind": "claude_code", "session_id": "x; rm -rf /"})
    m = router.add_model({"name": "Pinned", "kind": "claude_code", "model_id": "claude-opus-5-5", "session_id": sid})
    assert m["session_id"] == sid and m["available"] is False and "not found" in m["unavailable_reason"]
    (tmp_path / "projects" / "-home-x").mkdir(parents=True)
    (tmp_path / "projects" / "-home-x" / f"{sid}.jsonl").write_text("{}\n")
    m = next(x for x in router.list_models() if x["name"] == "Pinned")
    assert m["available"] is True
    assert router.resolve("developer", "Pinned").session_id == sid


def test_claude_code_model_with_its_own_account(monkeypatch):
    from agent_company.config import settings

    monkeypatch.setattr(settings(), "enable_claude_code", True)
    monkeypatch.setattr(router.shutil, "which", lambda name: f"/usr/bin/{name}")
    for bad in ("../x", "a/b", "Up", ""):
        with pytest.raises(ValueError):
            router.claude_config_dir(bad)
    with pytest.raises(ValueError):
        router.add_model({"name": "Bad", "kind": "claude_code", "account": "../../etc"})
    with pytest.raises(ValueError):
        router.add_model({"name": "Api", "kind": "llm", "model_id": "x/y", "account": "claude-2"})
    m = router.add_model({"name": "Second", "kind": "claude_code", "account": "claude-2"})
    assert m["account"] == "claude-2" and m["available"] is False and "no Claude Code sign-in" in m["unavailable_reason"]
    home = settings().data_dir / "claude-accounts" / "claude-2"
    home.mkdir(parents=True)
    (home / ".credentials.json").write_text("{}")
    assert next(x for x in router.list_models() if x["name"] == "Second")["available"] is True
    choice = router.resolve("developer", "Second")
    assert choice.account == "claude-2"
    assert executors.claude_env(choice)["CLAUDE_CONFIG_DIR"] == str(home)
    assert executors.claude_env(router.Choice("Host", "claude_code", "", "t")) is None  # the host's own login


def test_resumable_session_lookup():
    from agent_company.db import Task, now

    gid = loop.create_goal("x")
    with session() as s:
        s.add(Task(goal_id=gid, iteration=1, key="b", title="b", role="developer", agent_id=3, status="done",
                   session_id="abcdef12-0000", finished_at=now()))
    assert loop._resumable_session(gid, 3) == "abcdef12-0000"
    assert loop._resumable_session(gid, 4) == ""
    with session() as s:
        t = s.scalars(select(Task).where(Task.goal_id == gid)).first()
        from datetime import timedelta

        t.finished_at = now() - timedelta(hours=2)
    assert loop._resumable_session(gid, 3) == "", "old sessions start fresh"
