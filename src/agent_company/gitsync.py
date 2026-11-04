"""Git sync: push a project's approved work to its GitHub remote.

Approved goals are merged into the project's branch locally (loop.approve). A sync
then pushes that branch to the project's `github_url`. Only source is uploaded:
missing entries of a source-only `.gitignore` are added (and committed on their
own), and commits containing files over 50 MB are refused.

Every user has their own Git settings (Users page → Git, or "Git" next to the name):
name, email and branch for commits, and GitHub access for pushes:
- a GitHub token (stored encrypted), used over HTTPS through a credential helper that
  reads it from the environment, never from the command line;
- or an SSH key generated here (`data/git-keys/`), whose public half the user adds to
  their GitHub account;
- the token wins if both are set; with neither, the server's own `gh` login is used and
  the result says so.
Whose settings: an automatic push after approval uses the user who ordered the work; a
manual Sync uses the person who clicked it. GitHub URLs are rewritten to HTTPS or SSH
form to match the credential; `origin` keeps the project's URL as entered.

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
TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-]{20,255}$")
TOKEN_PURPOSE = b"ac-git-token"
GITHUB_RE = re.compile(r"^(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
                       r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")
# Answers only `get`; the token comes from the environment of this one git process.
TOKEN_HELPER = '!f() { test "$1" = get && echo username=x-access-token && echo "password=$AC_GIT_TOKEN"; }; f'


def _git(cwd: Path, *args: str, env: dict[str, str] | None = None, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout,
                          env={**os.environ, "GIT_TERMINAL_PROMPT": "0", **(env or {})})


def _credential_args() -> list[str]:
    if shutil.which("gh") and subprocess.run(["gh", "auth", "status"], capture_output=True).returncode == 0:
        return ["-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential"]
    return []


# --- per-user GitHub access ------------------------------------------------------------


def keys_dir() -> Path:
    d = settings().data_dir / "git-keys"
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    return d


def key_path(user_id: int) -> Path:
    return settings().data_dir / "git-keys" / f"user-{int(user_id)}"


def _token(user_id: int | None) -> str | None:
    from agent_company import transport

    if not user_id:
        return None
    with session() as s:
        u = s.get(User, user_id)
        return transport.decrypt_text(u.git_token, TOKEN_PURPOSE) if u is not None and u.git_token else None


def set_token(user_id: int, token: str) -> None:
    from agent_company import transport

    token = (token or "").strip()
    if not TOKEN_RE.match(token):
        raise ValueError("That does not look like a GitHub token (letters, digits, _ and -, 20-255 characters).")
    with session() as s:
        u = s.get(User, user_id)
        if u is None:
            raise KeyError(user_id)
        u.git_token = transport.encrypt_text(token, TOKEN_PURPOSE)


def clear_token(user_id: int) -> None:
    with session() as s:
        u = s.get(User, user_id)
        if u is None:
            raise KeyError(user_id)
        u.git_token = ""


def create_ssh_key(user_id: int) -> str:
    """Generate the user's SSH key (once) and return its public half for GitHub."""
    path = key_path(user_id)
    if not path.exists():
        keys_dir()
        with session() as s:
            u = s.get(User, user_id)
            if u is None:
                raise KeyError(user_id)
            comment = f"{u.git_email or u.name} (Agent Company)"
        r = subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", comment, "-f", str(path)],
                           capture_output=True, text=True, timeout=30)
        if r.returncode:
            raise ValueError(f"ssh-keygen failed: {r.stderr.strip()}")
        os.chmod(path, 0o600)
    return public_key(user_id)


def public_key(user_id: int) -> str:
    pub = key_path(user_id).with_suffix(".pub")
    return pub.read_text().strip() if pub.exists() else ""


def delete_ssh_key(user_id: int) -> None:
    path = key_path(user_id)
    path.unlink(missing_ok=True)
    path.with_suffix(".pub").unlink(missing_ok=True)


def access_status(user_id: int) -> dict[str, Any]:
    tok = _token(user_id)
    return {"token": tok is not None, "token_hint": f"…{tok[-4:]}" if tok else "",
            "ssh_key": public_key(user_id)}


def _github(url: str) -> tuple[str, str] | None:
    m = GITHUB_RE.match(url.strip())
    return (m.group(1), m.group(2)) if m else None


def access(url: str, user_id: int | None) -> dict[str, Any]:
    """How to reach `url` as this user: {url, args, env, via, secret}. Pure apart from reading settings."""
    gh = _github(url)
    tok = _token(user_id)
    if tok:
        return {"url": f"https://github.com/{gh[0]}/{gh[1]}.git" if gh else url, "secret": tok,
                "args": ["-c", "credential.helper=", "-c", "credential.https://github.com.helper=",
                         "-c", f"credential.helper={TOKEN_HELPER}"],
                "env": {"AC_GIT_TOKEN": tok}, "via": "the user's GitHub token"}
    if user_id and key_path(user_id).exists():
        known = keys_dir() / "known_hosts"
        return {"url": f"git@github.com:{gh[0]}/{gh[1]}.git" if gh else url, "secret": None, "args": [],
                "env": {"GIT_SSH_COMMAND": f"ssh -i {key_path(user_id)} -o IdentitiesOnly=yes -o BatchMode=yes "
                                           f"-o StrictHostKeyChecking=accept-new -o UserKnownHostsFile={known}"},
                "via": "the user's SSH key"}
    return {"url": url, "secret": None, "args": _credential_args(), "env": {}, "via": "the server's GitHub login"}


def test_access(user_id: int) -> dict[str, Any]:
    """Which GitHub account this user's credential belongs to."""
    tok = _token(user_id)
    if tok:
        import httpx

        try:
            r = httpx.get("https://api.github.com/user", timeout=15,
                          headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"})
        except httpx.HTTPError as e:
            return {"ok": False, "via": "token", "message": f"GitHub not reachable: {e}"}
        if r.status_code == 200:
            return {"ok": True, "via": "token", "login": r.json().get("login", ""),
                    "message": f"The token belongs to GitHub user {r.json().get('login', '?')}."}
        return {"ok": False, "via": "token", "message": f"GitHub refused the token ({r.status_code})."}
    if key_path(user_id).exists():
        a = access("git@github.com:x/y.git", user_id)
        r = subprocess.run(["ssh", *a["env"]["GIT_SSH_COMMAND"].split()[1:], "-T", "git@github.com"],
                           capture_output=True, text=True, timeout=30)
        m = re.search(r"Hi ([A-Za-z0-9-]+)", r.stderr + r.stdout)  # GitHub exits 1 even on success
        if m:
            return {"ok": True, "via": "ssh", "login": m.group(1), "message": f"The SSH key belongs to GitHub user {m.group(1)}."}
        return {"ok": False, "via": "ssh", "message": "GitHub did not accept the SSH key yet: add the public key "
                "under GitHub → Settings → SSH and GPG keys."}
    return {"ok": False, "via": "none", "message": "No token or SSH key set: pushes use the server's GitHub login."}


def _scrub(text: str, secret: str | None) -> str:
    return text.replace(secret, "***") if secret else text


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
    a = access(url, user_id)
    steps.append(f"access: {a['via']}")
    # best effort: learn what the remote has (into origin/<target>, so `origin` itself is untouched)
    _git(repo, *a["args"], "fetch", "-q", a["url"], f"+refs/heads/{target}:refs/remotes/origin/{target}", env=a["env"])
    known = _git(repo, "rev-parse", "--verify", "-q", f"origin/{target}").returncode == 0
    rev_range = f"origin/{target}..HEAD" if known else "HEAD"
    ahead = _git(repo, "rev-list", "--count", rev_range).stdout.strip()
    if known and ahead == "0":
        return {"status": "noop", "message": f"{name}: already up to date on {target}.", "steps": steps}
    big = _large_files(repo, rev_range)
    if big:
        return {"status": "error", "message": "Refused: commits contain files over 50 MB: " + ", ".join(big[:10]),
                "steps": steps}
    r = _git(repo, *a["args"], "push", a["url"], f"HEAD:refs/heads/{target}", env=a["env"], timeout=300)
    out = _scrub((r.stdout + r.stderr).strip(), a["secret"])
    steps.append(f"git push {a['url']} HEAD:{target}\n{out[-2000:]}")
    ok = r.returncode == 0
    if ok:
        _git(repo, "update-ref", f"refs/remotes/origin/{target}", "HEAD")
        msg = f"Pushed {ahead} commit(s) of {name} to {target} with {a['via']}."
    else:
        msg = f"Push failed ({a['via']}): {out.splitlines()[-1] if out else 'unknown'}"
        if a["via"] == "the server's GitHub login":
            msg += " Set a GitHub token or SSH key in your Git settings to push with your own account."
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
