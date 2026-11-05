"""Architecture Decision Records (ASEAO08 S08-R-02, AT-005)."""

import pytest

from agent_company import adr, decisions, executors, loop, worktrees


@pytest.fixture(autouse=True)
def no_queue(monkeypatch):
    monkeypatch.setattr(loop, "enqueue", lambda gid: None)


def with_decisions(monkeypatch, items):
    real = executors.standard_plan

    def plan(goal):
        return real(goal) | {"decisions": items}

    monkeypatch.setattr(executors, "standard_plan", plan)


def storage(choice, **kw):
    return {"topic": "Storage engine", "question": "Where are the todos kept?", "recommendation": choice,
            "alternatives": ["SQLite", "JSON file"], "criteria": ["no server", "concurrent writes"],
            "reasoning": f"{choice} fits a single-user CLI", "evidence": [{"ref": "README.md", "class": "FACT"}],
            "assumptions": ["one user"], "limitations": ["no network sync"], "confidence": 0.7,
            "category": "technical"} | kw


def repo(tmp_path):
    r = tmp_path / "proj"
    r.mkdir()
    worktrees.git(r, "init", "-q", "-b", "main")
    (r / "README.md").write_text("todo\n")
    worktrees.git(r, "add", ".")
    worktrees.git(r, "commit", "-q", "-m", "init")
    return r


def test_a_technology_choice_becomes_one_adr_and_one_decision(monkeypatch):
    with_decisions(monkeypatch, [storage("SQLite"),
                                 {"topic": "Run order", "recommendation": "spec first", "category": "operational"}])
    gid = loop.create_goal("Build a todo CLI", quality_commands=["test -f WORKLOG.md"], allowed_paths=["WORKLOG.md"])
    out = loop.run_goal(gid)
    assert out["status"] == "awaiting_approval", out["report"]["feedback"]
    rows = {d["topic"]: d for d in decisions.list_decisions(gid)}
    d = rows["Storage engine"]
    assert d["adr_path"] == f"docs/adr/{d['id']:04d}-storage-engine.md" and rows["Run order"]["adr_path"] == ""
    wt = loop._goal(gid).worktree
    text = (worktrees.ensure_sandboxed(wt) / d["adr_path"]).read_text()
    for part in ("Status: Accepted", f"Decision record: #{d['id']}", "## Options", "- JSON file", "## Criteria",
                 "## Decision\n\nSQLite", "- README.md (FACT)", "Limitations:", "- no network sync"):
        assert part in text
    assert d["adr_path"] in worktrees.git(wt, "ls-files", "docs/adr"), "committed with the plan"
    scope = next(c for c in out["report"]["checks"] if c["check"] == "scope")
    assert scope["passed"], "the goal's own ADR is in scope"
    assert adr.paths(gid) == [d["adr_path"]]


def test_a_later_choice_supersedes_the_earlier_adr(monkeypatch, tmp_path):
    from agent_company import projects

    r = repo(tmp_path)
    p = projects.create("Todo", path=str(r))
    with_decisions(monkeypatch, [storage("JSON file")])
    first = loop.create_goal("Build a todo CLI", project_id=p["id"], repo_path=str(r),
                             quality_commands=["test -f WORKLOG.md"])
    assert loop.run_goal(first)["status"] == "awaiting_approval"
    loop.approve(first)
    old = decisions.list_decisions(first)[0]["adr_path"]
    with_decisions(monkeypatch, [storage("SQLite")])
    second = loop.create_goal("Keep todos in SQLite", project_id=p["id"], repo_path=str(r),
                              quality_commands=["test -f WORKLOG.md"], allowed_paths=["WORKLOG.md"])
    out = loop.run_goal(second)
    assert out["status"] == "awaiting_approval", out["report"]["feedback"]
    new = decisions.list_decisions(second)[0]["adr_path"]
    assert new != old
    wt = worktrees.ensure_sandboxed(loop._goal(second).worktree)
    assert f"Supersedes: [{old[9:-3]}]({old[9:]})" in (wt / new).read_text()
    assert f"Status: Superseded by {new[9:-3]}" in (wt / old).read_text()
    assert set(adr.paths(second)) == {old, new} and next(
        c for c in out["report"]["checks"] if c["check"] == "scope")["passed"]


def test_a_decision_for_a_person_is_proposed_until_decided(monkeypatch):
    with_decisions(monkeypatch, [storage("SQLite", category="critical")])
    gid = loop.create_goal("Build a todo CLI", quality_commands=["test -f WORKLOG.md"])
    assert loop.run_goal(gid)["status"] == "awaiting_approval"
    d = decisions.list_decisions(gid)[0]
    wt = loop._goal(gid).worktree
    f = worktrees.ensure_sandboxed(wt) / d["adr_path"]
    assert "Status: Proposed (waits for a person)" in f.read_text()
    decisions.decide(d["id"], True, "user:admin")
    assert "Status: Accepted" in f.read_text()
    assert not worktrees.git(wt, "status", "--porcelain", "--", d["adr_path"]), "committed on the waiting branch"
    assert "Status: Accepted" in worktrees.git(wt, "show", f"HEAD:{d['adr_path']}")


def test_the_same_choice_again_reuses_the_adr(monkeypatch):
    with_decisions(monkeypatch, [storage("SQLite")])
    gid = loop.create_goal("Build a todo CLI", quality_commands=["test -f WORKLOG.md"])
    loop.run_goal(gid)
    first = decisions.list_decisions(gid)[0]
    again = decisions.from_plan(gid, [storage("SQLite")], None)[0]
    assert adr.write(gid, [again["id"]]) == []
    assert decisions.get(again["id"])["adr_path"] == first["adr_path"]
