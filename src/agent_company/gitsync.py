"""Git sync: push a project's approved work to its GitHub remote.

Approved goals are merged into the project's branch locally (loop.approve). A sync
then pushes that branch to the project's `github_url`. Only source is uploaded:
missing entries of a source-only `.gitignore` are added (and committed on their
own), and commits containing files over 50 MB are refused. The user who ordered
the work can have their own Git identity and branch (Users page → Git).

Nothing here force-pushes, and the main checkout's uncommitted work is never staged.
Pushing runs only from `/sync`, the Sync button, or after approval with AUTO_PUSH on.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from sqlalchemy import select

from agent_company.config import settings
from agent_company.db import Project, User, log_event, session

MAX_FILE_BYTES = 50 * 1024 * 1024
SOURCE_IGNORES = ["node_modules/", ".venv/", "venv/", "__pycache__/", "*.pyc", ".env", ".env.*", "*.log",
                  "dist/", "build/", ".DS_Store", "data/", ".pytest_cache/", ".ruff_cache/", "coverage/"]
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _git(cwd: Path, *args: str, env: dict[str, str] | None = None, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout,
                          env={**os.environ, "GIT_TERMINAL_PROMPT": "0", **(env or {})})


def _credential_args() -> list[str]:
    if shutil.which("gh") and subprocess.run(["gh", "auth", "status"], capture_output=True).returncode == 0:
        return ["-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential"]
    return []


def ensure_gitignore(repo: Path) -> list[str]:
    f = repo / ".gitignore"
    current = f.read_text() if f.exists() else ""
    have = {line.strip() for line in current.splitlines()}
    missing = [x for x in SOURCE_IGNORES if x not in have and x.rstrip("/") not in have]
    if missing:
        sep = "" if not current or current.endswith("\n") else "\n"
        f.write_text(f"{current}{sep}{chr(10) if current else ''}# source-only uploads (Agent Company)\n"
                     + "\n".join(missing) + "\n")
    return missing


def identity_for(user_id: int | None) -> dict[str, str]:
    if not user_id:
        return {}
    with session() as s:
        u = s.get(User, user_id)
        if u is None:
            return {}
        return {k: v for k, v in {"name": u.git_name, "email": u.git_email, "branch": u.git_branch}.items() if v}


def set_identity(user_id: int, name: str, email: str, branch: str) -> dict[str, str]:
    if email and not EMAIL_RE.match(email):
        raise ValueError("email does not look like an address")
    if branch and subprocess.run(["git", "check-ref-format", "--branch", branch], capture_output=True).returncode:
        raise ValueError("branch is not a valid branch name")
    if len(name) > 128:
        raise ValueError("name is too long")
    with session() as s:
        u = s.get(User, user_id)
        if u is None:
            raise KeyError(user_id)
        u.git_name, u.git_email, u.git_branch = name.strip(), email.strip(), branch.strip()
    return identity_for(user_id)


def _large_files(repo: Path, rev_range: str) -> list[str]:
    """Paths of blobs over 50 MB in the commits about to be pushed (one rev-list, one cat-file)."""
    objects = _git(repo, "rev-list", "--objects", rev_range).stdout
    sized = subprocess.run(["git", "cat-file", "--batch-check=%(objecttype) %(objectsize) %(rest)"], cwd=repo,
                           input=objects, capture_output=True, text=True, timeout=300).stdout
    big = []
    for line in sized.splitlines():
        kind, _, rest = line.partition(" ")
        size, _, path = rest.partition(" ")
        if kind == "blob" and size.isdigit() and int(size) > MAX_FILE_BYTES and path:
            big.append(path)
    return big


def push(project_id: int, user_id: int | None = None) -> dict[str, Any]:
    """Push the project's current branch (or the user's branch) to origin. Returns a log dict."""
    with session() as s:
        p = s.get(Project, project_id)
        if p is None:
            raise KeyError(project_id)
        repo, url, name = Path(p.path), p.github_url, p.name
    steps: list[str] = []
    if not (repo / ".git").exists():
        return {"status": "error", "message": f"{repo} is not a git repository", "steps": steps}
    missing = ensure_gitignore(repo)
    if missing:
        _git(repo, "add", ".gitignore")
        if _git(repo, "diff", "--cached", "--quiet").returncode:
            ident = identity_for(user_id)
            env = ({"GIT_AUTHOR_NAME": ident["name"], "GIT_COMMITTER_NAME": ident["name"]} if "name" in ident else {}) | (
                {"GIT_AUTHOR_EMAIL": ident["email"], "GIT_COMMITTER_EMAIL": ident["email"]} if "email" in ident else {})
            r = _git(repo, "commit", "-q", "-m", "Add source-only .gitignore entries", "--", ".gitignore", env=env)
            steps.append(f"gitignore: added {', '.join(missing)}" + ("" if r.returncode == 0 else f" ({r.stderr.strip()})"))
    if not url:
        return {"status": "noop", "message": "No GitHub URL set for this project; work stays local.", "steps": steps}
    remote = _git(repo, "remote", "get-url", "origin")
    if remote.returncode:
        _git(repo, "remote", "add", "origin", url)
        steps.append(f"remote origin -> {url}")
    elif remote.stdout.strip() != url:
        _git(repo, "remote", "set-url", "origin", url)
        steps.append(f"remote origin changed to {url}")
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    target = identity_for(user_id).get("branch") or branch
    _git(repo, *_credential_args(), "fetch", "-q", "origin")  # best effort: learn what the remote has
    known = _git(repo, "rev-parse", "--verify", "-q", f"origin/{target}").returncode == 0
    rev_range = f"origin/{target}..HEAD" if known else "HEAD"
    ahead = _git(repo, "rev-list", "--count", rev_range).stdout.strip()
    if known and ahead == "0":
        return {"status": "noop", "message": f"{name}: already up to date on {target}.", "steps": steps}
    big = _large_files(repo, rev_range)
    if big:
        return {"status": "error", "message": "Refused: commits contain files over 50 MB: " + ", ".join(big[:10]),
                "steps": steps}
    r = _git(repo, *_credential_args(), "push", "origin", f"HEAD:refs/heads/{target}", timeout=300)
    steps.append(f"git push origin HEAD:{target}\n{(r.stdout + r.stderr).strip()[-2000:]}")
    ok = r.returncode == 0
    msg = (f"Pushed {ahead} commit(s) of {name} to {target}." if ok else
           f"Push failed: {(r.stderr or r.stdout).strip().splitlines()[-1] if (r.stderr or r.stdout).strip() else 'unknown'}")
    log_event("git", msg)
    return {"status": "pushed" if ok else "error", "message": msg, "steps": steps}


def sync_project(project_id: int, user: dict[str, Any]) -> str:
    from agent_company.governance import audit

    if not user.get("is_admin"):
        return "Only administrators can push to GitHub."
    out = push(project_id, user["id"])
    audit(f"user:{user['name']}", "git_push", str(project_id), after={"status": out["status"], "message": out["message"]},
          severity="high" if out["status"] == "pushed" else "info")
    return out["message"] + ("\n" + "\n".join(out["steps"]) if out["steps"] else "")


def auto_push(goal_project_id: int | None, user_id: int | None) -> None:
    if settings().auto_push and goal_project_id:
        with session() as s:
            if s.scalars(select(Project).where(Project.id == goal_project_id, Project.github_url != "")).first():
                out = push(goal_project_id, user_id)
                from agent_company.projects import post

                post(goal_project_id, out["message"], kind="result")
