"""Merges refused only for a dirty checkout are retried once it is clean; a merge without approval records what
approve() records (requirements, dependencies, improvement deployment)."""

from pathlib import Path

import pytest

from agent_company import loop, ownerpolicy, prefs, requirements, worktrees
from agent_company.db import Goal, session


@pytest.fixture(autouse=True)
def no_queue(monkeypatch):
    monkeypatch.setattr(loop, "enqueue", lambda gid: None)


def goal(gid):
    with session() as s:
        return s.get(Goal, gid)


def dirty(repo):
    tracked = worktrees.git(repo, "ls-files").splitlines()[0]
    path = Path(repo) / tracked
    path.write_text(path.read_text() + "\nlocal edit\n")
    return tracked


def test_auto_merge_refused_for_a_dirty_checkout_is_retried_once_it_is_clean():
    gid = loop.create_goal("Merge me later", quality_commands=["test -f WORKLOG.md"])
    assert loop.run_goal(gid)["status"] == "awaiting_approval"
    repo = goal(gid).repo_path
    tracked = dirty(repo)
    prefs.set_value("auto_merge", True)
    assert ownerpolicy.on_awaiting_approval(gid)["merged"] is False
    assert ownerpolicy.retry_refused() == [], "still dirty: no second attempt"
    assert goal(gid).status == "awaiting_approval"
    worktrees.git(repo, "checkout", "--", tracked)
    assert ownerpolicy.retry_refused() == [gid]
    assert goal(gid).status == "approved"
    assert ownerpolicy.retry_refused() == []


def test_a_goal_without_approval_is_merged_on_retry_even_with_auto_merge_off(monkeypatch):
    prefs.set_value("require_approval", False)
    merge, touched = loop._merge, []

    def edit_then_merge(gid):  # someone edits the checkout while the goal runs
        touched.append(dirty(goal(gid).repo_path))
        monkeypatch.setattr(loop, "_merge", merge)
        return merge(gid)

    monkeypatch.setattr(loop, "_merge", edit_then_merge)
    gid = loop.create_goal("No approval needed", quality_commands=["test -f WORKLOG.md"])
    assert loop.run_goal(gid)["status"] == "awaiting_approval"
    assert "automatic merge was refused" in goal(gid).result
    assert ownerpolicy.retry_refused() == []
    worktrees.git(goal(gid).repo_path, "checkout", "--", touched[0])
    assert ownerpolicy.retry_refused() == [gid]
    assert goal(gid).status == "approved"


def test_a_refusal_for_another_reason_is_not_retried():
    gid = loop.create_goal("Waits for a person", quality_commands=["test -f WORKLOG.md"])
    assert loop.run_goal(gid)["status"] == "awaiting_approval"
    prefs.set_value("auto_merge", True)
    assert ownerpolicy.retry_refused() == [], "no refusal recorded: the person merges it"
    assert goal(gid).status == "awaiting_approval"


def test_a_merge_without_approval_records_requirements_and_dependencies(monkeypatch):
    seen = []
    monkeypatch.setattr(requirements, "on_merged", lambda gid, sha: seen.append(("requirements", gid, sha)))
    from agent_company import dependencies

    monkeypatch.setattr(dependencies, "on_merged", lambda gid, sha: seen.append(("dependencies", gid, sha)))
    prefs.set_value("require_approval", False)
    gid = loop.create_goal("Merged straight away", quality_commands=["test -f WORKLOG.md"])
    assert loop.run_goal(gid)["status"] == "approved"
    sha = loop._merged_sha(gid)
    assert sha and [x[0] for x in seen] == ["requirements", "dependencies"]
    assert all(x[1:] == (gid, sha) for x in seen)


def test_a_failing_record_after_the_merge_does_not_fail_the_goal(monkeypatch):
    def broken(gid, sha):
        raise RuntimeError("disk full")

    monkeypatch.setattr(requirements, "on_merged", broken)
    prefs.set_value("require_approval", False)
    gid = loop.create_goal("Merged anyway", quality_commands=["test -f WORKLOG.md"])
    assert loop.run_goal(gid)["status"] == "approved"
    assert goal(gid).status == "approved"


def test_a_retry_refused_for_another_reason_stops_and_notifies_once(monkeypatch):
    from sqlalchemy import select

    from agent_company import decisions
    from agent_company.db import Notification

    gid = loop.create_goal("Decide later", quality_commands=["test -f WORKLOG.md"])
    assert loop.run_goal(gid)["status"] == "awaiting_approval"
    repo = goal(gid).repo_path
    tracked = dirty(repo)
    prefs.set_value("auto_merge", True)
    assert ownerpolicy.on_awaiting_approval(gid)["merged"] is False
    worktrees.git(repo, "checkout", "--", tracked)
    monkeypatch.setattr(decisions, "blocking", lambda g: [{"id": 7, "topic": "Database"}])  # a person must decide now
    assert ownerpolicy.retry_refused() == []
    assert ownerpolicy.retry_refused() == [], "stopped: not tried again every minute"

    def refusals():
        with session() as s:
            return len(s.scalars(select(Notification).where(
                Notification.title == f"Automatic merge of goal #{gid} refused")).all())

    assert refusals() == 2, "the dirty-tree refusal and the decision, nothing per minute after"
