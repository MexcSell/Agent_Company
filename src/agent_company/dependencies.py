"""Dependency control (ASEAO08 S08-R-05): what a goal's change does to its dependencies, an audit of them at the
gate, and a record at the merge.

- `changes` compares every dependency manifest and lock file the change touched (`checks.MANIFESTS`) between the
  merge base and the tested commit. Both sides are read from git objects, never from the working tree, so a
  manifest that is a symlink to `.env` or `data/` shows nothing of them. Lock files give exact versions, so a
  transitive update counts too.
- `audit` runs `pip-audit` and `npm audit` when they are installed: fixed argument lists (manifest paths come from
  the agent's diff, so no shell), the worktree read-only, in the L3 sandbox with network (they query the advisory
  databases) as the goal's sandbox mode allows. `pip-audit` reads pinned requirement files with `--no-deps
  --disable-pip` and a `uv.lock` through `uv export --frozen` (offline); it never builds a project, so no
  agent-written code runs. A vulnerability in a dependency this change added or changed fails the gate's
  `security` stage (setting `dependency_audit`: `block`); one in an untouched dependency is listed but passes, so
  an old advisory cannot fail every goal. `warn` lists them without failing, `off` runs no audit. An audit that
  could not run (tool missing, network down, unpinned requirements, sandbox required and unavailable) adds no gate
  row, only a note, so it never shows as a pass.
- `on_merged` records the merged changes (the last iteration's) as a `dependencies` event.

SBOM generation is not here (open, P2).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path, PurePosixPath
from typing import Any

from agent_company import worktrees
from agent_company.config import settings
from agent_company.db import log_event

MODES = ("off", "warn", "block")
PY_LOCKS = {"uv.lock", "poetry.lock"}
TOML_LOCKS = PY_LOCKS | {"Cargo.lock"}
_REQ = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*(.*)$")
_GO_REQ = re.compile(r"^\s*([^\s()]+)\s+(v[^\s]+)")


def ecosystem(path: str) -> str:
    name = PurePosixPath(path).name
    if name in {"package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml"}:
        return "npm"
    if name.startswith("Cargo."):
        return "cargo"
    if name.startswith("go."):
        return "go"
    if name.startswith("Gemfile"):
        return "ruby"
    if name.startswith("composer."):
        return "php"
    return "python"


def normalize(name: str, eco: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower() if eco == "python" else name


def _pep508(lines: list[Any]) -> dict[str, str]:
    out = {}
    for line in lines:
        if not isinstance(line, str):
            continue  # an include-group table of [dependency-groups]
        m = _REQ.match(line.split("#", 1)[0])
        if m:
            out[m.group(1)] = m.group(3).strip()
    return out


def parse(path: str, text: str) -> dict[str, str] | None:
    """Dependency name -> version or specifier, per format; None when the format is not understood."""
    name = PurePosixPath(path).name
    try:
        if re.fullmatch(r"requirements[^/]*\.(txt|in)", name):
            lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith(("#", "-"))]
            return _pep508(lines)
        if name == "package.json":
            data = json.loads(text)
            out: dict[str, str] = {}
            for sec in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
                out |= {k: str(v) for k, v in (data.get(sec) or {}).items()}
            return out
        if name == "package-lock.json":
            data = json.loads(text)
            pkgs = data.get("packages") or {}
            if pkgs:
                return {k.rsplit("node_modules/", 1)[-1]: str(v.get("version", "")) for k, v in pkgs.items()
                        if k and isinstance(v, dict)}
            return {k: str(v.get("version", "")) for k, v in (data.get("dependencies") or {}).items()
                    if isinstance(v, dict)}
        if name in TOML_LOCKS:
            return {p["name"]: str(p.get("version", "")) for p in tomllib.loads(text).get("package", [])
                    if isinstance(p, dict) and p.get("name")}
        if name == "pyproject.toml":
            data = tomllib.loads(text)
            proj = data.get("project") or {}
            out = _pep508(list(proj.get("dependencies") or []))
            for group in (proj.get("optional-dependencies") or {}).values():
                out |= _pep508(list(group))
            for group in (data.get("dependency-groups") or {}).values():
                out |= _pep508(list(group))
            poetry = (data.get("tool") or {}).get("poetry") or {}
            tables = [poetry.get("dependencies") or {}, poetry.get("dev-dependencies") or {},
                      *[(g or {}).get("dependencies") or {} for g in (poetry.get("group") or {}).values()]]
            for t in tables:
                out |= {k: str(v if not isinstance(v, dict) else v.get("version", "")) for k, v in t.items()
                        if k != "python"}
            return out
        if name == "Cargo.toml":
            data = tomllib.loads(text)
            out = {}
            for sec in ("dependencies", "dev-dependencies", "build-dependencies"):
                out |= {k: str(v if not isinstance(v, dict) else v.get("version", v.get("git", "")))
                        for k, v in (data.get(sec) or {}).items()}
            return out
        if name == "go.mod":
            out, block = {}, False
            for line in text.splitlines():
                s = line.split("//", 1)[0].strip()
                if s.startswith("require ("):
                    block = True
                    continue
                if block and s == ")":
                    block = False
                    continue
                m = _GO_REQ.match(s[len("require "):] if s.startswith("require ") else s if block else "")
                if m:
                    out[m.group(1)] = m.group(2)
            return out
    except (ValueError, tomllib.TOMLDecodeError, AttributeError, TypeError):
        return None
    return None  # setup.py, setup.cfg, Pipfile, yarn/pnpm locks, Gemfile, composer: changed, not itemised


def _show(worktree: Path, rev: str, path: str) -> str | None:
    """A file's content at a commit (a symlink's target name, never the target), None when it is not there."""
    p = subprocess.run(["git", "show", f"{rev}:{path}"], cwd=worktree, capture_output=True, text=True, timeout=60,
                       env={**os.environ, **worktrees.GIT_ENV})
    return p.stdout if p.returncode == 0 else None


def changes(worktree: Path | str, base: str) -> list[dict[str, Any]]:
    """Each added, removed or changed dependency between the merge base and HEAD; a manifest whose format is not
    itemised appears once with `change: "changed"` and no name, a manifest that is a symlink as `"symlink"`."""
    from agent_company.checks import MANIFESTS

    worktree = worktrees.ensure_sandboxed(worktree)
    paths = [p for p in worktrees.changed_files(worktree, base) if MANIFESTS.search(p)]
    if not paths:
        return []
    mb = worktrees.git(worktree, "merge-base", base, "HEAD", check=False) or base
    out: list[dict[str, Any]] = []
    for path in paths:
        eco = ecosystem(path)
        if worktrees.git(worktree, "ls-tree", "HEAD", "--", path, check=False).startswith("120000"):
            out.append({"manifest": path, "ecosystem": eco, "name": "", "before": "", "after": "", "change": "symlink"})
            continue  # never followed: it could point at .env or data/
        before_text, after_text = _show(worktree, mb, path), _show(worktree, "HEAD", path)
        before = parse(path, before_text) if before_text is not None else {}
        after = parse(path, after_text) if after_text is not None else {}
        if before is None or after is None:
            out.append({"manifest": path, "ecosystem": eco, "name": "", "before": "", "after": "",
                        "change": "added" if before_text is None else "removed" if after_text is None else "changed"})
            continue
        for name in sorted(set(before) | set(after)):
            b, a = before.get(name), after.get(name)
            if b == a:
                continue
            out.append({"manifest": path, "ecosystem": eco, "name": name, "before": b or "", "after": a or "",
                        "change": "added" if b is None else "removed" if a is None else "changed"})
    return out[:500]


# --- audit --------------------------------------------------------------------------------------------------------

def _which(tool: str) -> str | None:
    from agent_company import sandbox

    return shutil.which(tool, path=sandbox.env_for()["PATH"])


def _run(argv: list[str], cwd: Path, mode: str, timeout: int, network: bool,
         readonly: list[Path] | None = None) -> tuple[int, str, str]:
    """(exit code, output, isolation) of a fixed command, the worktree read-only. Tests replace this."""
    from agent_company import sandbox

    res = sandbox.run(argv, cwd, timeout, mode=mode, writable=[], readonly=[cwd, *(readonly or [])], network=network)
    if not res.isolation:
        return 126, res.note, ""
    return res.returncode, res.output, res.isolation


def _json(text: str) -> Any:
    start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
    if start < 0:
        raise ValueError("no JSON")
    return json.JSONDecoder().raw_decode(text[start:])[0]


def _pip_vulns(text: str) -> list[dict[str, Any]]:
    data = _json(text)
    deps = data.get("dependencies") if isinstance(data, dict) else data
    if not isinstance(deps, list):
        raise ValueError("unexpected pip-audit output")
    return [{"name": d.get("name", ""), "version": d.get("version", ""), "id": v.get("id", ""),
             "severity": "unknown", "fix": ", ".join(v.get("fix_versions") or [])}
            for d in deps if isinstance(d, dict) for v in d.get("vulns") or [] if isinstance(v, dict)]


def _npm_vulns(text: str) -> list[dict[str, Any]]:
    data = _json(text)
    if not isinstance(data, dict) or "vulnerabilities" not in data:
        raise ValueError(str(data.get("error", {}).get("summary", "unexpected npm audit output"))
                         if isinstance(data, dict) and isinstance(data.get("error"), dict) else "unexpected npm audit output")
    out = []
    for name, v in (data.get("vulnerabilities") or {}).items():
        via = [x for x in v.get("via") or [] if isinstance(x, dict)]
        fix = v.get("fixAvailable")
        out.append({"name": name, "version": v.get("range", ""), "severity": v.get("severity", "unknown"),
                    "id": ", ".join(str(x.get("url") or x.get("title") or "") for x in via)[:300]
                          or "via " + ", ".join(str(x) for x in v.get("via") or [] if isinstance(x, str)),
                    "fix": (f"{fix.get('name')} {fix.get('version')}" if isinstance(fix, dict) else
                            "available" if fix else "none")})
    return out


def _plan(worktree: Path, changed: list[dict[str, Any]]) -> tuple[list[tuple[str, str, Path, str]], list[str]]:
    """(tool, directory, input, kind) audits for the changed manifests, and notes on those that cannot be audited."""
    runs: list[tuple[str, str, Path, str]] = []
    notes: list[str] = []
    dirs: dict[tuple[str, str], set[str]] = {}
    for c in changed:
        dirs.setdefault((c["ecosystem"], str(PurePosixPath(c["manifest"]).parent)), set()).add(c["manifest"])
    for (eco, d), manifests in sorted(dirs.items()):
        here = worktree / d
        if eco == "python":
            if (here / "uv.lock").is_file() and not (here / "uv.lock").is_symlink():
                runs.append(("pip-audit", d, here / "uv.lock", "uv.lock"))
            for m in sorted(manifests):
                f = worktree / m
                if re.fullmatch(r"requirements[^/]*\.txt", f.name):
                    if f.is_symlink() or not f.is_file():
                        notes.append(f"{m}: not audited (a symlink or removed)")
                    else:
                        runs.append(("pip-audit", d, f, "requirements"))
            if not any(r[1] == d and r[0] == "pip-audit" for r in runs):
                notes.append(f"{d}: Python dependencies changed ({', '.join(sorted(manifests))}) but there is no "
                             "uv.lock or pinned requirements file to audit without building the project")
        elif eco == "npm":
            lock = here / "package-lock.json"
            if lock.is_file() and not lock.is_symlink():
                runs.append(("npm", d, lock, "package-lock"))
            else:
                notes.append(f"{d}: npm dependencies changed but there is no package-lock.json to audit")
        else:
            notes.append(f"{d}: {eco} dependencies changed; no audit tool for {eco} is run")
    return runs, notes


def audit(worktree: Path | str, changed: list[dict[str, Any]], mode: str | None = None,
          timeout: int | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """Gate rows (stage `security`) for the audits that ran, and notes on what was not audited."""
    policy = settings().dependency_audit
    if policy == "off" or not changed:
        return [], ([] if not changed else ["dependency audit off (setting dependency_audit)"])
    worktree = worktrees.ensure_sandboxed(worktree)
    timeout = timeout or settings().command_timeout
    runs, notes = _plan(worktree, changed)
    touched = {(c["ecosystem"], normalize(c["name"], c["ecosystem"])) for c in changed
               if c["name"] and c["change"] in {"added", "changed"}}
    rows = []
    for tool, d, source, kind in runs:
        exe = _which("pip-audit" if tool == "pip-audit" else "npm")
        if not exe:
            notes.append(f"{d}: {tool} is not installed; not audited")
            continue
        cwd = worktree / d
        eco = "npm" if tool == "npm" else "python"
        with tempfile.TemporaryDirectory(prefix="dep-audit-") as tmp:
            if kind == "uv.lock":
                uv = _which("uv")
                if not uv:
                    notes.append(f"{d}: uv is not installed; uv.lock not audited")
                    continue
                rc, out, _ = _run([uv, "export", "--frozen", "--no-hashes", "--no-emit-project", "--format",
                                   "requirements-txt"], cwd, mode or settings().sandbox, timeout, network=False)
                if rc != 0:
                    notes.append(f"{d}: uv export failed (exit {rc}): {out.strip()[-200:]}")
                    continue
                req = Path(tmp) / "requirements.txt"
                req.write_text(out)
                argv, extra = [exe, "-r", str(req), "--no-deps", "--disable-pip", "-f", "json",
                               "--progress-spinner", "off"], [Path(tmp)]
            elif kind == "requirements":
                argv, extra = [exe, "-r", str(source), "--no-deps", "--disable-pip", "-f", "json",
                               "--progress-spinner", "off"], []
            else:
                argv, extra = [exe, "audit", "--package-lock-only", "--json"], []
            rc, out, isolation = _run(argv, cwd, mode or settings().sandbox, timeout, network=True, readonly=extra)
        try:
            vulns = _pip_vulns(out) if tool == "pip-audit" else _npm_vulns(out)
        except (ValueError, AttributeError, TypeError) as e:
            notes.append(f"{d}: {tool} did not report (exit {rc}): {(out.strip() or str(e))[-300:]}")
            continue
        introduced = [v for v in vulns if (eco, normalize(v["name"], eco)) in touched]
        lines = [f"{v['name']} {v['version']}: {v['id']} ({v['severity']}; fix: {v['fix'] or 'none'})"
                 for v in introduced]
        older = [f"{v['name']} {v['version']}: {v['id']}" for v in vulns if v not in introduced]
        detail = ("; ".join(lines) or "no known vulnerability in the added or changed dependencies") + (
            f". Already present before this change: {'; '.join(older[:10])}" if older else "")
        rows.append({"check": f"dependency audit {tool} {d}", "passed": not introduced or policy == "warn",
                     "stage": "security", "detail": detail[:1500], "tool": tool, "isolation": isolation,
                     "vulnerabilities": introduced[:50], "preexisting_vulnerabilities": len(older),
                     **({"warning": True} if introduced and policy == "warn" else {})})
    return rows, notes


def check(worktree: Path | str, base: str, mode: str | None = None) -> dict[str, Any]:
    """The gate's dependency step: {changes, notes, rows}. Never raises: a failure becomes a note."""
    try:
        changed = changes(worktree, base)
        rows, notes = audit(worktree, changed, mode)
    except Exception as e:  # noqa: BLE001 - the audit is advisory infrastructure; the gate must still finish
        return {"changes": [], "notes": [f"dependency check failed: {type(e).__name__}: {e}"[:300]], "rows": []}
    return {"changes": changed, "notes": notes, "rows": rows}


def on_merged(goal_id: int, sha: str) -> list[dict[str, Any]]:
    """Record the dependency changes the merge brought in (those of the goal's last gated iteration)."""
    from agent_company.db import Goal, session

    with session() as s:
        g = s.get(Goal, goal_id)
        last = (g.history or [{}])[-1] if g is not None and g.history else {}
    changed = list((last.get("dependencies") or {}).get("changes") or [])
    if changed:
        names = ", ".join(f"{c['name'] or c['manifest']} {c['change']}" for c in changed[:20])
        log_event("dependencies", f"merged {len(changed)} dependency change(s) ({sha[:10]}): {names}"[:1000],
                  goal_id=goal_id, commit=sha, changes=changed)
    return changed
