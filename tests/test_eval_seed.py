"""S09-R-05: seed caches stay ignored while source and protected config stay visible."""

import os
import subprocess
import sys

import pytest

from agent_company import evaluation, worktrees


def test_seed_ignores_generated_files():
    repo = evaluation._seed_repo("t", {"name": "x", "files": {"stats.py": "def f():\n    return 1\n"}}, 0)
    assert ".gitignore" in worktrees.git(repo, "ls-files").splitlines()
    assert (repo / ".gitignore").read_text() == "\n".join(evaluation.SEED_IGNORES) + "\n"
    for rel in ("__pycache__/stats.cpython-312.pyc", "pkg/__pycache__/m.pyc", "a.pyc",
                ".pytest_cache/v/cache/lastfailed"):
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"cache")
    assert worktrees.git(repo, "status", "--porcelain", "--untracked-files=all") == ""


@pytest.mark.parametrize("existing", ["dist/", "dist/\n__pycache__/\n*.pyc\n.pytest_cache/\n"])
def test_seed_preserves_existing_ignore_without_duplicates(existing):
    repo = evaluation._seed_repo("t", {"name": "x", "files": {".gitignore": existing}}, 0)
    text = (repo / ".gitignore").read_text()
    assert text.startswith(existing)
    assert text.splitlines()[0] == "dist/"
    for line in evaluation.SEED_IGNORES:
        assert text.splitlines().count(line) == 1
    assert text.endswith("\n")
    assert worktrees.git(repo, "status", "--porcelain", "--untracked-files=all") == ""


def test_seed_keeps_source_and_protected_config_visible():
    repo = evaluation._seed_repo("t", {"name": "x"}, 0)
    (repo / "new_module.py").write_text("value = 1\n")
    (repo / ".env").write_text("TEST_VALUE=1\n")
    assert set(worktrees.git(repo, "status", "--porcelain", "--untracked-files=all").splitlines()) == {
        "?? .env", "?? new_module.py",
    }


def test_seed_ignores_real_import_bytecode():
    repo = evaluation._seed_repo("t", {"name": "x", "files": {"stats.py": "def f():\n    return 1\n"}}, 0)
    env = os.environ.copy()
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    env.pop("PYTHONPYCACHEPREFIX", None)
    subprocess.run([sys.executable, "-c", "import sys; sys.dont_write_bytecode = False; import stats"],
                   cwd=repo, env=env, check=True, capture_output=True)
    assert list((repo / "__pycache__").glob("stats.*.pyc"))
    assert worktrees.git(repo, "status", "--porcelain", "--untracked-files=all") == ""
