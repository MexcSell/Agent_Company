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


def test_cost_budget_stops_the_goal():
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
    assert tried == ["Claude Sonnet 5.5", "Claude Opus 5.5"], "preference first, then the route"
    assert not res.ok and choice.kind == "llm", "a real failure stands; no simulated stand-in"
    with session() as s:
        assert [r.ok for r in s.scalars(select(Run).where(Run.goal_id == gid))] == [False, False]


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

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return Proc(cmd)

    monkeypatch.setattr(executors.subprocess, "run", fake_run)
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
