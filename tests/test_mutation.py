"""Offline EL-R-08 acceptance checks using real git repositories and sandbox runs."""

import shlex
import sys

import pytest

from agent_company import evaluation, worktrees
from agent_company.config import settings

CHECK = f"{shlex.quote(sys.executable)} -m pytest -q -p no:cacheprovider"


def _commit(repo):
    worktrees.git(repo, "add", "-A")
    worktrees.git(repo, "commit", "-qm", "seed")
    return worktrees.git(repo, "rev-parse", "HEAD")


def _seed(tmp_path):
    repo = tmp_path / "seed"
    repo.mkdir()
    worktrees.git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("seed\n")
    base = _commit(repo)
    (repo / "adult.py").write_text("def is_adult(age):\n    assert age >= 0\n    return age >= 18\n")
    (repo / "test_adult.py").write_text(
        "import pytest\nfrom adult import is_adult\n\n"
        "def test_adult():\n    assert is_adult(18)\n    assert not is_adult(17)\n\n"
        "def test_negative():\n    with pytest.raises(AssertionError):\n        is_adult(-1)\n")
    _commit(repo)
    return repo, base


def _clean(repo):
    assert not list(settings().worktrees_dir.glob("mutation-*"))
    assert "mutation-" not in worktrees.git(repo, "worktree", "list", "--porcelain")


@pytest.fixture
def measured(tmp_path):
    repo, base = _seed(tmp_path)
    head = worktrees.git(repo, "rev-parse", "HEAD")
    status = worktrees.git(repo, "status", "--porcelain")
    result = evaluation.mutation_test(repo, base, checks=[CHECK], mode="auto")
    _clean(repo)
    assert worktrees.git(repo, "rev-parse", "HEAD") == head
    assert worktrees.git(repo, "status", "--porcelain") == status
    return result


def test_removed_assertion_is_caught(measured):
    assert "error" not in measured, measured
    assert measured["baseline"][0]["ok"]
    removed = next(m for m in measured["mutations"] if m["kind"] == "remove_assert")
    assert (removed["path"], removed["line"]) == ("adult.py", 2)
    assert CHECK in removed["caught_by"]
    assert measured["per_check"][CHECK]["share"] > 0
    assert measured["sites"] == 3
    assert all(m["path"] == "adult.py" for m in measured["mutations"])
    assert measured["level"] in ("L3", "L2")


def test_flip_boundary_and_survivor(measured):
    flips = {m["line"]: m for m in measured["mutations"] if m["kind"] == "flip_boundary"}
    assert CHECK in flips[3]["caught_by"]
    assert flips[2]["survived"]
    assert not flips[2]["caught_by"]
    assert 0 < measured["per_check"][CHECK]["share"] < 1


def test_failing_baseline(tmp_path):
    repo, base = _seed(tmp_path)
    (repo / "adult.py").write_text("def is_adult(age):\n    assert age >= 0\n    return age > 18\n")
    _commit(repo)
    result = evaluation.mutation_test(repo, base, checks=[CHECK], mode="auto")
    assert result["error"]
    assert result["mutations"] == []
    assert result["per_check"] == {}
    assert result["overall"]["mutations"] == 0
    _clean(repo)


def test_no_python_sites(tmp_path):
    repo, _ = _seed(tmp_path)
    base = worktrees.git(repo, "rev-parse", "HEAD")
    (repo / "notes.txt").write_text("notes\n")
    _commit(repo)
    result = evaluation.mutation_test(repo, base, checks=[CHECK], mode="auto")
    assert "error" not in result
    assert result["sites"] == 0
    assert result["mutations"] == []
    assert result["per_check"][CHECK]["share"] is None
    _clean(repo)


def test_cap(tmp_path):
    repo, base = _seed(tmp_path)
    result = evaluation.mutation_test(repo, base, checks=[CHECK], max_mutations=1, mode="auto")
    assert result["truncated"] is True
    assert result["sites"] == 3
    assert len(result["mutations"]) == 1
    first = result["mutations"][0]
    assert (first["path"], first["line"], first["kind"]) == ("adult.py", 2, "remove_assert")
    _clean(repo)


def test_timeout_counts_as_caught_and_infra_is_excluded(tmp_path, monkeypatch):
    repo, base = _seed(tmp_path)
    outcomes = iter([
        evaluation.sandbox.Outcome(0, "", "L3"),
        evaluation.sandbox.Outcome(124, "timeout", "L3", timed_out=True),
        evaluation.sandbox.Outcome(1, "", "L3", infra="startup failure"),
        evaluation.sandbox.Outcome(0, "", "L2"),
    ])
    monkeypatch.setattr(evaluation.sandbox, "run", lambda *a, **kw: next(outcomes))
    result = evaluation.mutation_test(repo, base, checks=[CHECK], mode="auto")
    assert result["per_check"][CHECK] == {"mutations": 2, "caught": 1, "share": 0.5}
    assert result["overall"] == {"mutations": 2, "caught": 1, "share": 0.5, "infra": 1}
    assert result["mutations"][0]["timed_out"] == [CHECK]
    assert result["mutations"][1]["infra"] == [CHECK]
    assert not result["mutations"][1]["survived"]
    assert result["level"] == "L2"
    _clean(repo)


def test_exception_removes_worktree(tmp_path, monkeypatch):
    repo, base = _seed(tmp_path)
    calls = 0

    def run(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise RuntimeError("check interrupted")
        return evaluation.sandbox.Outcome(0, "", "L2")

    monkeypatch.setattr(evaluation.sandbox, "run", run)
    with pytest.raises(RuntimeError, match="check interrupted"):
        evaluation.mutation_test(repo, base, checks=[CHECK], mode="auto")
    _clean(repo)
