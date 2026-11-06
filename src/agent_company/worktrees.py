"""Git Worktree Manager: isolated working directories for concurrent development.

Each goal works on its own branch in its own worktree under `data/worktrees/`.
Executors that change files refuse to run anywhere else (see `ensure_sandboxed`),
so agents never edit the main checkout or anything outside the sandbox.
"""

from __future__ import annotations

import logging
import re
import subprocess
import time
from pathlib import Path

from agent_company.config import settings

log = logging.getLogger(__name__)


class WorktreeError(RuntimeError):
    pass


GIT_ENV = {"GIT_AUTHOR_NAME": "Agent Company", "GIT_AUTHOR_EMAIL": "agents@localhost",
           "GIT_COMMITTER_NAME": "Agent Company", "GIT_COMMITTER_EMAIL": "agents@localhost"}


def identity_env(ident: dict[str, str] | None) -> dict[str, str]:
    """Author/committer variables for a user's Git identity (name and/or email)."""
    env: dict[str, str] = {}
    if ident and ident.get("name"):
        env.update(GIT_AUTHOR_NAME=ident["name"], GIT_COMMITTER_NAME=ident["name"])
    if ident and ident.get("email"):
        env.update(GIT_AUTHOR_EMAIL=ident["email"], GIT_COMMITTER_EMAIL=ident["email"])
    return env


def git(cwd: Path | str, *args: str, check: bool = True, env: dict[str, str] | None = None) -> str:
    import os

    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          env={**os.environ, **GIT_ENV, **(env or {})}, timeout=120)
    if check and proc.returncode != 0:
        raise WorktreeError(f"git {' '.join(args)}: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout.strip()


def ensure_sandboxed(path: Path | str) -> Path:
    """Return the resolved path if it is inside the worktrees directory, else raise."""
    p = Path(path).resolve()
    root = settings().worktrees_dir.resolve()
    if p != root and root not in p.parents:
        raise WorktreeError(f"refusing to run outside the sandbox: {p}")
    return p


def new_workspace(name: str) -> Path:
    """Create an empty git repository for a goal that has no repo of its own."""
    path = settings().workspaces_dir / _safe(name)
    path.mkdir(parents=True, exist_ok=True)
    if not (path / ".git").exists():
        git(path, "init", "-q", "-b", "main")
        (path / "README.md").write_text(f"# {name}\n\nWorkspace created by Agent Company.\n")
        git(path, "add", "README.md")
        git(path, "commit", "-q", "-m", "Initial workspace")
    return path


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-")[:60] or "work"


def create(repo: Path | str, name: str, base: str | None = None) -> tuple[Path, str, str]:
    """Add a worktree on a new branch. Returns (path, branch, base_branch)."""
    repo = Path(repo).resolve()
    if not (repo / ".git").exists():
        raise WorktreeError(f"not a git repository: {repo}")
    base = base or git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    branch = f"agents/{_safe(name)}"
    path = settings().worktrees_dir / _safe(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if _worktree_of(repo, path):  # this goal's own worktree: resumed as it is
            return path, branch, base
        # Something else under this name (another repository's worktree, a leftover of a deleted database): it is
        # moved aside, never deleted, and the goal gets a fresh worktree.
        aside = path.with_name(f"{path.name}.stale-{int(time.time())}")
        path.rename(aside)
        log.warning("moved %s aside to %s: not a worktree of %s", path, aside, repo)
    git(repo, "worktree", "prune")
    exists = git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", check=False)
    git(repo, "worktree", "add", "-q", *([str(path), branch] if exists else ["-b", branch, str(path), base]))
    return path, branch, base


def _worktree_of(repo: Path, path: Path) -> bool:
    """Whether `path` is a working worktree registered in `repo`."""
    listed = {Path(w.get("worktree", "")).resolve() for w in list_worktrees(repo)}
    return path.resolve() in listed and bool(git(path, "rev-parse", "--git-dir", check=False))


# Generated files never belong in a goal's commit (bytecode, caches, dependencies, attachments).
GENERATED = ["__pycache__", "*.pyc", ".pytest_cache", ".ruff_cache", ".mypy_cache", "node_modules", ".venv",
             ".attachments", ".DS_Store"]


def commit_all(path: Path | str, message: str, author: dict[str, str] | None = None) -> str | None:
    """Commit every source change in the worktree. Returns the commit sha or None if clean."""
    path = ensure_sandboxed(path)
    excludes = [f":(exclude,glob)**/{g}" for g in GENERATED] + [f":(exclude,glob)**/{g}/**" for g in GENERATED]
    git(path, "add", "-A", "--", ".", *excludes)
    if not git(path, "diff", "--cached", "--name-only"):
        return None
    git(path, "commit", "-q", "-m", message, env=identity_env(author))
    return git(path, "rev-parse", "HEAD")


def reset_to(path: Path | str, commit: str) -> list[str]:
    """Put the worktree back to `commit`: uncommitted edits and new files go; generated files, attachments and
    git-ignored files stay. Returns the source paths that were dirty."""
    path = ensure_sandboxed(path)
    excludes = [f":(exclude,glob)**/{g}" for g in GENERATED] + [f":(exclude,glob)**/{g}/**" for g in GENERATED]
    changed = git(path, "diff", "HEAD", "--name-only", "--", ".", *excludes).splitlines()
    new = git(path, "ls-files", "--others", "--exclude-standard", "--", ".", *excludes).splitlines()
    dirty = sorted({p for p in changed + new if p})
    git(path, "reset", "-q", "--hard", commit)
    git(path, "clean", "-fdq", *(x for g in GENERATED for x in ("-e", g)))
    return dirty


def diff_stat(path: Path | str, base: str) -> str:
    return git(ensure_sandboxed(path), "diff", "--stat", f"{base}...HEAD", check=False)


def changed_files(path: Path | str, base: str) -> list[str]:
    out = git(ensure_sandboxed(path), "diff", "--name-only", f"{base}...HEAD", check=False)
    return [line for line in out.splitlines() if line]


def merge(repo: Path | str, branch: str, base: str, author: dict[str, str] | None = None) -> str:
    """Merge the goal branch into its base branch in the main repository (local only)."""
    repo = Path(repo).resolve()
    current = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    if current != base:
        raise WorktreeError(f"{repo} is on {current}, not {base}; switch it to {base} to merge")
    if git(repo, "status", "--porcelain", "--untracked-files=no"):
        raise WorktreeError(f"{repo} has uncommitted changes; commit or stash them first")
    try:
        git(repo, "merge", "--no-ff", "-q", "-m", f"Merge {branch}", branch, env=identity_env(author))
    except WorktreeError as e:
        # A conflict must never leave the person's repository half-merged: put it back as it was.
        git(repo, "merge", "--abort", check=False)
        raise WorktreeError(f"merging {branch} into {base} failed and was rolled back; the repository is "
                            f"unchanged. Resolve it on the branch first. ({e})") from e
    return git(repo, "rev-parse", "HEAD")


def remove(repo: Path | str, path: Path | str) -> None:
    path = ensure_sandboxed(path)
    git(Path(repo), "worktree", "remove", "--force", str(path), check=False)


def list_worktrees(repo: Path | str) -> list[dict[str, str]]:
    out = git(Path(repo), "worktree", "list", "--porcelain", check=False)
    items: list[dict[str, str]] = []
    cur: dict[str, str] = {}
    for line in out.splitlines() + [""]:
        if not line:
            if cur:
                items.append(cur)
            cur = {}
            continue
        key, _, val = line.partition(" ")
        cur[key] = val
    return items


def write_files(root: Path | str, files: dict[str, str]) -> list[str]:
    """Write files into a sandboxed worktree, refusing any path that escapes it."""
    root = ensure_sandboxed(root)
    written = []
    for rel, content in files.items():
        target = (root / rel).resolve()
        if root not in target.parents or ".git" in target.relative_to(root).parts:
            raise WorktreeError(f"refusing to write outside the worktree: {rel}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        written.append(str(target.relative_to(root)))
    return written
