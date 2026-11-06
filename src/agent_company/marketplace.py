"""Agent marketplace (REQ-002): discover, evaluate and import external agents.

  fetch       Each import gets its own new, empty directory `data/marketplace/<id>/src`. A repository URL must be
              https:// (file:// only when `ALLOW_FILE_URLS` is set, by tests); it may carry no user name or password
              and must name a public host. It is cloned with `--depth 1`, no submodules, no hooks (`core.hooksPath`
              = /dev/null, `--template=`), no global/system git config, no LFS or smudge filters, no redirects, a
              timeout and a size cap; `.git` is removed afterwards. An uploaded .zip/.tar.gz is checked member by
              member before anything is written: absolute paths, `..`, symlinks, hard links and special files refuse
              the whole archive; file count and bytes are capped and every file is created with `open("xb")`.
  convert     Text only (json, `yaml.safe_load` without anchors, Markdown front matter): a native
              `agent-package.json`, a Claude Code subagent (`.claude/agents/*.md`), a CrewAI `config/agents.yaml`
              or a skill pack (`SKILL.md`). Nothing from the package is imported, executed or installed, and the
              Python reading it never changes into its directory.
  ceiling     `permission_ceiling(role, requested, granted)`: the role template's permissions intersected with
              what the administrator grants; network is never granted on import; autonomy G2 for a writing role,
              else G1. Requested permissions and the instructions' text are shown, never applied.
  candidate   A `Recruitment` with `source="marketplace"` and a `candidate` agent (never staffed for users' work);
              recruitment.qualify runs its role's eval suite through evaluation.run_suite, whose commands run in
              the L3 sandbox. The record always waits for an administrator (recruitment._ask_or_approve).
  provenance  The definition file (agentdefs) gets `## Imported instructions` (headings demoted) and
              `## Provenance` (source, sha, author, licence, version, eval summary), each a committed version, so
              agentdefs.restore rolls it back.
"""

from __future__ import annotations

import hashlib
import io
import ipaddress
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import tarfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from agent_company import org
from agent_company.config import settings
from agent_company.db import AgentImport, Agent, ModelEntry, Recruitment, log_event, now, session

ALLOW_FILE_URLS = False  # file:// repositories, for offline tests only
MAX_FILES = 500
MAX_BYTES = 20 * 1024 * 1024  # unpacked size of a package
MAX_FILE_BYTES = 2 * 1024 * 1024  # one file the conversion or the screen reads
MAX_RATIO = 100  # unpacked bytes per archive byte
CLONE_TIMEOUT = 60
MANIFEST = "agent-package.json"
LIST_FIELDS = ("skills", "tools", "models", "dependencies", "permissions")
TEXT_LIMITS = {"name": 128, "role": 64, "description": 2000, "instructions": 20000, "version": 32, "author": 128,
               "licence": 64, "source": 512}
LIST_LIMIT, ITEM_LIMIT = 50, 200
KNOWN_PERMISSIONS = {"read", "write", "shell", "network", "mcp", "admin"}
NEVER_ON_IMPORT = {"network", "mcp", "admin"}
AUTONOMY_ORDER = ("G0", "G1", "G2", "G3", "G4")
INJECTION_RE = re.compile(
    r"(?i)(ignore (all |any )?(previous|prior|above) instructions|you (now )?(have|are granted|may use|can use)"
    r"[^.\n]{0,40}\b(bash|shell|mcp|admin|root|sudo|network|autonomy|g[34])\b|autonomy\s*(level\s*)?g[34]\b|"
    r"\b(administrator|admin) (rights|permissions|access)\b|disable (the )?(sandbox|gate|review))")
LICENCES = [("MIT", re.compile(r"(?i)\bMIT License\b|Permission is hereby granted, free of charge")),
            ("Apache-2.0", re.compile(r"(?i)Apache License,? Version 2\.0")),
            ("GPL-3.0", re.compile(r"(?i)GNU GENERAL PUBLIC LICENSE\s+Version 3")),
            ("GPL-2.0", re.compile(r"(?i)GNU GENERAL PUBLIC LICENSE\s+Version 2")),
            ("BSD-3-Clause", re.compile(r"(?i)Redistribution and use in source and binary forms"))]


class ImportRefused(ValueError):
    """The package or its source is refused; nothing was kept."""


# --- storage ------------------------------------------------------------------------------------

def root() -> Path:
    return settings().data_dir / "marketplace"


def src_dir(import_id: int) -> Path:
    return root() / str(int(import_id)) / "src"


def _new_import(kind: str, source: str, actor: str) -> tuple[int, Path]:
    with session() as s:
        imp = AgentImport(kind=kind, source=source[:512], created_by=actor[:128], status="fetched")
        s.add(imp)
        s.flush()
        iid = imp.id
    d = src_dir(iid)
    if d.exists():  # a stale directory of a wiped database: never reuse it
        shutil.rmtree(d.parent)
    d.mkdir(parents=True)
    return iid, d


def _fail(import_id: int, error: str) -> None:
    with session() as s:
        imp = s.get(AgentImport, import_id)
        if imp is not None:
            imp.status, imp.error = "failed", error[:2000]
            log_event("marketplace", f"import #{import_id} failed: {error}"[:500], s=s)
    shutil.rmtree(src_dir(import_id), ignore_errors=True)


# --- URL fetch ----------------------------------------------------------------------------------

def _is_public_host(host: str) -> bool:
    try:
        addrs = {ipaddress.ip_address(host)}
    except ValueError:
        try:
            addrs = {ipaddress.ip_address(i[4][0].split("%")[0]) for i in socket.getaddrinfo(host, 443)}
        except (OSError, UnicodeError):
            return False
    return bool(addrs) and all(a.is_global for a in addrs)


def check_url(url: str) -> str:
    """The URL when it may be cloned; raises ImportRefused otherwise."""
    url = (url or "").strip()
    if not url or len(url) > 512 or any(c.isspace() or ord(c) < 32 for c in url) or url.startswith("-"):
        raise ImportRefused("give a repository URL without spaces")
    parts = urlsplit(url)
    if parts.scheme == "file":
        if not ALLOW_FILE_URLS:
            raise ImportRefused("file:// URLs are not allowed")
        if parts.netloc or not parts.path.startswith("/"):
            raise ImportRefused("a file:// URL names an absolute local path")
        return url
    if parts.scheme != "https":
        raise ImportRefused(f"only https:// repositories can be imported, not {parts.scheme or url}")
    if "@" in parts.netloc or parts.username or parts.password:
        raise ImportRefused("a repository URL must not carry a user name or password")
    host = parts.hostname or ""
    if not host or not _is_public_host(host):
        raise ImportRefused(f"{host or url} is not a public host")
    return url


def _git_env() -> dict[str, str]:
    keep = {k: os.environ[k] for k in ("PATH", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR")
            if k in os.environ}
    return {**keep, "HOME": "/nonexistent", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/bin/false", "GIT_LFS_SKIP_SMUDGE": "1",
            "GIT_ALLOW_PROTOCOL": "file:https" if ALLOW_FILE_URLS else "https"}


def clone_command(url: str, dest: Path) -> list[str]:
    allow = ["-c", "protocol.allow=never", "-c", "protocol.https.allow=always"]
    if url.startswith("file:"):
        allow += ["-c", "protocol.file.allow=always"]
    return ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.symlinks=false", "-c", "core.fsmonitor=false",
            "-c", "http.followRedirects=false", "-c", "filter.lfs.smudge=", "-c", "filter.lfs.process=",
            "-c", "filter.lfs.required=false", "-c", "submodule.recurse=false", *allow,
            "clone", "--depth", "1", "--single-branch", "--no-recurse-submodules", "--template=", "--quiet",
            "--", url, str(dest)]


def fetch_url(url: str, actor: str) -> dict[str, Any]:
    """Clone a repository into its own new directory, then convert and screen it."""
    url = check_url(url)
    iid, d = _new_import("url", url, actor)
    try:
        d.rmdir()  # git clone creates it; the parent is ours and empty
        r = subprocess.run(clone_command(url, d), cwd=d.parent, env=_git_env(), capture_output=True, text=True,
                           timeout=CLONE_TIMEOUT)
        if r.returncode != 0:
            raise ImportRefused(f"git clone failed: {r.stderr.strip()[-300:]}")
        sha = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "rev-parse", "HEAD"], cwd=d,
                             env=_git_env(), capture_output=True, text=True, timeout=20).stdout.strip()
        shutil.rmtree(d / ".git", ignore_errors=True)
        _check_tree(d)
    except subprocess.TimeoutExpired:
        _fail(iid, "git clone timed out")
        raise ImportRefused("git clone timed out") from None
    except ImportRefused as e:
        _fail(iid, str(e))
        raise
    return _after_fetch(iid, sha[:80])


def _check_tree(d: Path) -> None:
    """A cloned tree: no symlinks or special files survive, and its size is capped."""
    files = size = 0
    for p in sorted(d.rglob("*"), key=lambda x: -len(x.parts)):
        st = p.lstat()
        if stat.S_ISLNK(st.st_mode) or not (stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode)):
            p.unlink()  # core.symlinks=false writes them as files already; anything else is dropped
            continue
        if stat.S_ISREG(st.st_mode):
            files, size = files + 1, size + st.st_size
    if files > MAX_FILES or size > MAX_BYTES:
        raise ImportRefused(f"the repository is too large ({files} files, {size} bytes; limits {MAX_FILES} "
                            f"files, {MAX_BYTES} bytes)")


# --- upload -------------------------------------------------------------------------------------

def _member_path(name: str) -> PurePosixPath:
    if not name or "\\" in name or "\x00" in name:
        raise ImportRefused(f"refused archive member {name!r}")
    p = PurePosixPath(name)
    if p.is_absolute() or name.startswith("/") or re.match(r"^[A-Za-z]:", name) or ".." in p.parts:
        raise ImportRefused(f"refused archive member {name!r}: it would be written outside the package")
    return p


def _zip_members(z: zipfile.ZipFile) -> list[tuple[PurePosixPath, int, Any]]:
    out = []
    for info in z.infolist():
        p = _member_path(info.filename)
        mode = info.external_attr >> 16
        if info.is_dir():
            continue
        if stat.S_IFMT(mode) not in (0, stat.S_IFREG):  # 0: written without a Unix file type
            raise ImportRefused(f"refused archive member {info.filename!r}: links and special files are not allowed")
        out.append((p, info.file_size, info))
    return out


def _tar_members(t: tarfile.TarFile) -> list[tuple[PurePosixPath, int, Any]]:
    out = []
    for m in t.getmembers():
        p = _member_path(m.name)
        if m.isdir():
            continue
        if not m.isfile():
            raise ImportRefused(f"refused archive member {m.name!r}: links and special files are not allowed")
        out.append((p, m.size, m))
    return out


def _extract(data: bytes, filename: str, dest: Path) -> None:
    low = filename.lower()
    if low.endswith(".zip"):
        arch: Any = zipfile.ZipFile(io.BytesIO(data))
        members, opener = _zip_members(arch), arch.open
    elif low.endswith((".tar.gz", ".tgz", ".tar")):
        arch = tarfile.open(fileobj=io.BytesIO(data), mode="r:*")
        members, opener = _tar_members(arch), arch.extractfile
    else:
        raise ImportRefused("upload a .zip, .tar.gz or .tgz package")
    with arch:
        declared = sum(n for _, n, _ in members)
        if len(members) > MAX_FILES or declared > MAX_BYTES or declared > MAX_RATIO * max(len(data), 1024):
            raise ImportRefused(f"the archive is too large ({len(members)} files, {declared} bytes unpacked)")
        written = 0
        for p, _, ref in members:  # every member was checked above: nothing is written before
            target = dest.joinpath(*p.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            src = opener(ref)
            with src, open(target, "xb") as out:
                while chunk := src.read(65536):
                    written += len(chunk)
                    if written > MAX_BYTES:
                        raise ImportRefused("the archive unpacks to more than its declared size")
                    out.write(chunk)


def fetch_upload(path_or_bytes: str | Path | bytes, filename: str, actor: str) -> dict[str, Any]:
    """Unpack an uploaded package into its own new directory, then convert and screen it."""
    data = path_or_bytes if isinstance(path_or_bytes, bytes) else Path(path_or_bytes).read_bytes()
    name = PurePosixPath((filename or "package").replace("\\", "/")).name[:200]
    if len(data) > MAX_BYTES:
        raise ImportRefused("the upload is too large")
    iid, d = _new_import("upload", name, actor)
    try:
        _extract(data, name, d)
    except (ImportRefused, zipfile.BadZipFile, tarfile.TarError, OSError, EOFError) as e:
        _fail(iid, str(e) if isinstance(e, ImportRefused) else f"unreadable archive: {type(e).__name__}")
        raise e if isinstance(e, ImportRefused) else ImportRefused(f"unreadable archive: {e}") from e
    return _after_fetch(iid, hashlib.sha256(data).hexdigest())


# --- conversion ---------------------------------------------------------------------------------

def _read(p: Path) -> str:
    st = p.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_FILE_BYTES:
        raise ImportRefused(f"{p.name} is not a readable file")
    return p.read_text(encoding="utf-8", errors="replace")


def safe_yaml(text: str) -> Any:
    """yaml.safe_load without anchors or aliases (no billion-laughs expansion)."""
    try:
        for tok in yaml.scan(text):
            if isinstance(tok, (yaml.AnchorToken, yaml.AliasToken)):
                raise ImportRefused("YAML anchors and aliases are not allowed")
        return yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ImportRefused(f"invalid YAML: {e}") from None


def _front_matter(text: str) -> tuple[dict[str, Any], str]:
    if text.startswith("---\n") and "\n---" in text[4:]:
        head, body = text[4:].split("\n---", 1)
        meta = safe_yaml(head)
        return (meta if isinstance(meta, dict) else {}), body.lstrip("-").lstrip("\n")
    return {}, text


def _listify(v: Any) -> list[Any]:
    if v is None:
        return []
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    return list(v) if isinstance(v, (list, tuple)) else [v]


def detect_licence(d: Path) -> str:
    for name in ("LICENSE", "LICENSE.md", "LICENSE.txt", "LICENCE", "COPYING"):
        p = d / name
        if p.is_file() and not p.is_symlink():
            text = _read(p)[:20000]
            return next((spdx for spdx, rx in LICENCES if rx.search(text)), "unknown")
    return "unknown"


def convert(d: Path) -> dict[str, Any]:
    """A manifest from the package's text files (never executed). Raises ImportRefused when none fits."""
    d = Path(d)
    native = d / MANIFEST
    if native.is_file():
        try:
            m = json.loads(_read(native))
        except json.JSONDecodeError as e:
            raise ImportRefused(f"{MANIFEST} is not valid JSON: {e}") from None
        if not isinstance(m, dict):
            raise ImportRefused(f"{MANIFEST} is not an object")
        m = dict(m)
        ref = m.get("instructions_file")
        if not m.get("instructions") and isinstance(ref, str):
            p = (d / ref)
            if ".." in PurePosixPath(ref).parts or PurePosixPath(ref).is_absolute() or not p.is_file():
                raise ImportRefused("instructions_file names a file outside the package")
            m["instructions"] = _read(p)
        m.setdefault("format", "native")
    elif (agents := sorted((d / ".claude" / "agents").glob("*.md")) if (d / ".claude" / "agents").is_dir() else []):
        meta, body = _front_matter(_read(agents[0]))
        m = {"format": "claude-subagent", "name": meta.get("name") or agents[0].stem,
             "description": meta.get("description", ""), "role": meta.get("role", ""),
             "tools": _listify(meta.get("tools")), "models": _listify(meta.get("model")), "instructions": body}
    elif (d / "config" / "agents.yaml").is_file():
        data = safe_yaml(_read(d / "config" / "agents.yaml"))
        if not isinstance(data, dict) or not data:
            raise ImportRefused("config/agents.yaml has no agent")
        key, spec = next(iter(data.items()))
        spec = spec if isinstance(spec, dict) else {}
        m = {"format": "crewai", "name": str(key), "role": spec.get("x_role", ""),
             "description": str(spec.get("role", "")),
             "instructions": f"Goal: {spec.get('goal', '')}\n\nBackstory: {spec.get('backstory', '')}".strip(),
             "models": _listify(spec.get("llm"))}
    elif (d / "SKILL.md").is_file():
        meta, body = _front_matter(_read(d / "SKILL.md"))
        name = meta.get("name") or "skill-pack"
        m = {"format": "skill-pack", "name": name, "description": meta.get("description", ""),
             "role": meta.get("role", ""), "skills": [name], "instructions": body}
    else:
        raise ImportRefused(f"no {MANIFEST}, .claude/agents/*.md, config/agents.yaml or SKILL.md in the package")
    if not m.get("licence") and not m.get("license"):
        m["licence"] = detect_licence(d)
    return m


def validate_manifest(m: dict[str, Any]) -> dict[str, Any]:
    """The normalised manifest (schema version 1). Unknown keys are kept under `extra` and never used."""
    if not isinstance(m, dict):
        raise ImportRefused("the manifest is not an object")
    m = dict(m)
    if "license" in m and "licence" not in m:
        m["licence"] = m.pop("license")
    known = {*TEXT_LIMITS, *LIST_FIELDS, "format", "instructions_file", "schema", "autonomy", "extra"}
    out: dict[str, Any] = {"schema": 1, "format": str(m.get("format") or "native")[:32]}
    for k, limit in TEXT_LIMITS.items():
        v = m.get(k, "")
        if not isinstance(v, (str, int, float)) or isinstance(v, bool):
            raise ImportRefused(f"{k} must be text")
        v = str(v).strip()
        if len(v) > limit:
            raise ImportRefused(f"{k} is longer than {limit} characters")
        out[k] = v
    for k in LIST_FIELDS:
        v = m.get(k) or []
        if not isinstance(v, list) or len(v) > LIST_LIMIT:
            raise ImportRefused(f"{k} must be a list of at most {LIST_LIMIT} entries")
        items = []
        for x in v:
            if not isinstance(x, (str, int, float)) or isinstance(x, bool) or len(str(x)) > ITEM_LIMIT:
                raise ImportRefused(f"{k} entries are short texts")
            items.append(str(x).strip())
        out[k] = items
    out["autonomy"] = str(m.get("autonomy") or "")[:4]
    out["extra"] = {k: m[k] for k in m if k not in known}
    role = out["role"]
    if not role:
        raise ImportRefused("the package names no role: give one of " + ", ".join(sorted(org.ROLES)))
    if role == "general_manager":
        raise ImportRefused("a General Manager cannot be imported")
    if role not in org.ROLES:
        raise ImportRefused(f"unknown role {role!r}")
    if not out["name"]:
        raise ImportRefused("the package names no agent")
    out["licence"] = out["licence"] or "unknown"
    return out


# --- permission ceiling and screen ---------------------------------------------------------------

def permission_ceiling(role: str, requested: list[str] | None = None, granted: list[str] | None = None
                       ) -> dict[str, Any]:
    """What a candidate of `role` may hold: the role template's permissions without NEVER_ON_IMPORT, intersected
    with `granted` (all of them when None). `requested` never widens it; what it asks beyond is `not_granted`."""
    spec = org.ROLES.get(role)
    if spec is None or role == "general_manager":
        raise ImportRefused(f"unknown role {role!r}")
    base = [p for p in spec.permissions if p not in NEVER_ON_IMPORT]
    perms = [p for p in base if granted is None or p in set(granted)]
    autonomy = "G2" if "write" in base else "G1"
    asked = [str(p) for p in (requested or [])]
    return {"permissions": perms, "autonomy": autonomy, "requested": asked,
            "not_granted": [p for p in asked if p not in perms]}


def clamp_autonomy(value: str, ceiling: str) -> str:
    v = value if value in AUTONOMY_ORDER else ceiling
    return min(v, ceiling, key=AUTONOMY_ORDER.index)


def screen(manifest: dict[str, Any], d: Path) -> dict[str, Any]:
    """Findings a person reads before approving. A secret blocks the import; its value is never stored."""
    from agent_company.checks import PLACEHOLDER, SECRET_PATTERNS

    findings: list[dict[str, str]] = []
    files = [p for p in sorted(Path(d).rglob("*")) if p.is_file() and not p.is_symlink()][:MAX_FILES]
    texts = [(str(p.relative_to(d)), p) for p in files]
    for rel, p in texts:
        try:
            text = _read(p)
        except (ImportRefused, OSError):
            continue
        for label, rx in SECRET_PATTERNS:
            m = rx.search(text)
            if m and not PLACEHOLDER.search(m.group(0)):
                findings.append({"kind": "secret", "file": rel, "detail": f"looks like a {label}"})
        if (m := INJECTION_RE.search(text)) is not None:
            findings.append({"kind": "injection", "file": rel, "detail": m.group(0)[:120]})
        if p.suffix in {".py", ".sh", ".js", ".ts"} or p.name in {"setup.py", "Makefile", "package.json"}:
            findings.append({"kind": "code", "file": rel, "detail": "code is kept as data and never run on the host"})
    ceiling = permission_ceiling(manifest["role"], manifest.get("permissions"))
    if ceiling["not_granted"]:
        findings.append({"kind": "permissions", "file": MANIFEST,
                         "detail": "requested beyond the ceiling: " + ", ".join(ceiling["not_granted"])})
    if manifest.get("autonomy") and clamp_autonomy(manifest["autonomy"], ceiling["autonomy"]) != manifest["autonomy"]:
        findings.append({"kind": "permissions", "file": MANIFEST,
                         "detail": f"requested autonomy {manifest['autonomy']}, ceiling {ceiling['autonomy']}"})
    if manifest.get("licence", "unknown") == "unknown":
        findings.append({"kind": "licence", "file": "", "detail": "no licence found"})
    return {"findings": findings[:200], "block": any(f["kind"] == "secret" for f in findings),
            "acknowledge": any(f["kind"] in {"injection", "permissions"} for f in findings)}


def _after_fetch(import_id: int, sha: str) -> dict[str, Any]:
    d = src_dir(import_id)
    try:
        manifest = validate_manifest(convert(d))
        found = screen(manifest, d)
    except ImportRefused as e:
        with session() as s:
            imp = s.get(AgentImport, import_id)
            imp.source_sha = sha
        _fail(import_id, str(e))
        raise
    ceiling = permission_ceiling(manifest["role"], manifest["permissions"])
    with session() as s:
        imp = s.get(AgentImport, import_id)
        imp.source_sha, imp.manifest, imp.screen, imp.ceiling = sha, manifest, found, ceiling
        imp.licence, imp.author, imp.version = manifest["licence"], manifest["author"], manifest["version"]
        imp.status = "failed" if found["block"] else "converted"
        if found["block"]:
            imp.error = "the screen found a secret in the package"
        log_event("marketplace", f"import #{import_id} of '{manifest['name']}' ({manifest['role']}): {imp.status}",
                  s=s)
    if found["block"]:
        shutil.rmtree(d, ignore_errors=True)
        raise ImportRefused("the package contains what looks like a secret; it was not imported")
    return get(import_id)


# --- candidate ----------------------------------------------------------------------------------

# An imported agent's skills start here: registry._rank counts a missing skill as 0 (`skills.get(w, 0)`), so a
# claimed skill at 0 gives the package no staffing weight; only eval or probation results raise it.
IMPORTED_SKILL_LEVEL = 0


def _catalog_model(s: Session, model: str) -> str:
    """An administrator's model choice at approve: a Model Router catalog entry, else ValueError."""
    if model not in set(s.scalars(select(ModelEntry.name))):
        raise ValueError(f"{model!r} is not a model of the router catalog")
    return model


def create_candidate(import_id: int) -> dict[str, Any]:
    """A recruitment record (source marketplace) and its candidate agent at the ceiling; its qualification run
    starts (the role's eval suite through evaluation.run_suite, commands in the L3 sandbox)."""
    from agent_company import agentdefs, recruitment, registry

    with registry._staff_lock, session() as s:
        imp = s.get(AgentImport, import_id)
        if imp is None:
            raise KeyError(import_id)
        if imp.status != "converted":
            raise ValueError(f"import #{imp.id} is {imp.status}")
        m, ceiling = imp.manifest, imp.ceiling
        role_skills = list(org.ROLES[m["role"]].skills)  # the package's own skills never choose the suite
        suite = recruitment.qualification_suite([m["role"], *role_skills])
        writes = org.ROLES[m["role"]].writes
        jd = {"title": m["name"][:100], "role": m["role"], "summary": (m["description"] or
                                                                      f"Imported {m['role']} agent")[:400],
              "responsibilities": [f"Work as a {m['role'].replace('_', ' ')} imported from the marketplace"],
              "expertise": role_skills or [m["role"]], "requested_skills": m["skills"][:20], "tools": [], "permissions": ceiling["permissions"],
              "reporting_manager": None, "room": org.ROLES[m["role"]].room, "evaluation_criteria":
                  [f"passes {recruitment.QUALIFY_PASS_RATE:.0%} of the {suite or 'role'} suite"],
              "budget": {}, "lifecycle": "permanent"}
        rec = Recruitment(requested_role=m["role"], job_description=jd, template=m["role"], decision="recruit",
                          reason=f"marketplace import #{imp.id} from {imp.source}"[:2000], lifecycle="permanent",
                          source="marketplace", status="qualifying" if suite and writes else "pending",
                          qualification={"suite": suite, "status": "queued"} if suite and writes else
                          {"status": "skipped", "reason": "no eval suite matches the role" if writes
                           else "the suites measure writing work"})
        s.add(rec)
        s.flush()
        agent = registry._recruit(s, rec)
        name, n = m["name"][:120], 1
        while s.scalars(select(Agent).where(Agent.name == name, Agent.id != agent.id)).first():
            n += 1
            name = f"{m['name'][:110]} #{n}"
        agent.name, agent.title = name, m["name"][:128]
        agent.lifecycle = "candidate"  # never staffed for users' work (registry staffs active agents only)
        agent.permissions = list(ceiling["permissions"])
        agent.autonomy = clamp_autonomy(m.get("autonomy") or "", ceiling["autonomy"])
        agent.model_pref = None  # the role route applies; the manifest's models are shown, the administrator picks
        agent.skills = {k: IMPORTED_SKILL_LEVEL for k in [*role_skills, *m["skills"][:20]]} or \
            {m["role"]: IMPORTED_SKILL_LEVEL}
        registry.record_version(s, agent, "marketplace", f"candidate from marketplace import #{imp.id}")
        rec.agent_id = agent.id
        imp.recruitment_id, imp.agent_id = rec.id, agent.id
        # no eval ran (a role that doesn't write, or no suite matches it): approve needs accept_unevaluated
        imp.status = "qualifying" if rec.status == "qualifying" else "unevaluated"
        if imp.status == "unevaluated":
            imp.eval = dict(rec.qualification)
        if rec.status == "pending":
            from agent_company.governance import notify

            recruitment._ask_or_approve(s, rec, notify)
        log_event("marketplace", f"import #{imp.id}: candidate {agent.name} (recruitment #{rec.id})", s=s,
                  agent_id=agent.id, recruitment_id=rec.id)
        agent_id, rec_id, qualifying = agent.id, rec.id, rec.status == "qualifying"
        instructions = m["instructions"]
    agentdefs.sync(agent_id, f"candidate from marketplace import #{import_id}")
    if instructions:
        agentdefs.write_section(agent_id, agentdefs.IMPORTED, agentdefs.demote(instructions),
                                f"imported instructions (marketplace #{import_id})", limit=20000)
    write_provenance(import_id)
    if qualifying:
        recruitment.start_qualification(rec_id)
    return get(import_id)


def on_qualified(s: Session, r: Recruitment) -> None:
    """recruitment._finish: the qualification verdict on the import record (inside the caller's session)."""
    imp = s.scalars(select(AgentImport).where(AgentImport.recruitment_id == r.id)).first()
    if imp is None:
        return
    q = r.qualification or {}
    imp.eval = {k: q.get(k) for k in ("suite", "status", "pass_rate", "runs", "passed_runs", "cost_usd", "needed",
                                       "dimensions", "error", "finished_at") if k in q}
    imp.status = "qualified" if q.get("status") == "passed" else "failed"
    if imp.status == "failed":
        imp.error = (r.note or "not qualified")[:2000]


def eval_summary(ev: dict[str, Any]) -> str:
    if not ev:
        return "not run"
    if ev.get("status") == "skipped":
        return f"skipped ({ev.get('reason', '')})"
    dims = ev.get("dimensions") or {}
    parts = [f"{k}: {v.get('value', v.get('mean', ''))} (n={v.get('n', '?')})" if isinstance(v, dict) else f"{k}: {v}"
             for k, v in dims.items()]
    head = (f"{ev.get('status', '?')} on the {ev.get('suite', '?')} suite, pass rate "
            f"{float(ev.get('pass_rate') or 0):.0%} of {ev.get('runs', 0)} runs")
    return head + (f"; {'; '.join(parts)}" if parts else "")


def write_provenance(import_id: int) -> bool:
    from agent_company import agentdefs

    with session() as s:
        imp = s.get(AgentImport, import_id)
        if imp is None or imp.agent_id is None:
            return False
        ev = imp.eval or (s.get(Recruitment, imp.recruitment_id).qualification if imp.recruitment_id else {})
        lines = [f"- import: #{imp.id} ({imp.kind}), status {imp.status}",
                 f"- source: {imp.source}", f"- sha: {imp.source_sha or 'unknown'}",
                 f"- author: {imp.author or 'unknown'}", f"- licence: {imp.licence}",
                 f"- version: {imp.version or 'unknown'}", f"- eval: {eval_summary(ev or {})}",
                 *([f"- unevaluated, accepted by {ev['accepted_unevaluated_by']}"]
                   if (ev or {}).get("accepted_unevaluated_by") else []),
                 f"- requested skills: {', '.join((imp.manifest or {}).get('skills') or []) or 'none'} "
                 f"(shown, not granted)",
                 f"- requested models: {', '.join((imp.manifest or {}).get('models') or []) or 'none'} "
                 f"(shown, not applied)",
                 f"- granted: {', '.join((imp.ceiling or {}).get('permissions') or []) or 'none'}, autonomy "
                 f"{(imp.ceiling or {}).get('autonomy', '')}"]
        agent_id = imp.agent_id
    return agentdefs.write_section(agent_id, "Provenance", agentdefs.demote("\n".join(lines)),
                                   f"provenance of marketplace import #{import_id}")


# --- administrator decisions --------------------------------------------------------------------

def approve(import_id: int, actor: str, grant: list[str] | None = None, *, admin: bool = False,
            autonomy: str = "", accept_licence: bool = False, acknowledge: bool = False,
            accept_unevaluated: bool = False, model: str = "") -> dict[str, Any]:
    """An administrator enables the candidate. `grant` is intersected with the ceiling; a licence that is unknown,
    screen findings that ask for acknowledgement and an import no eval ran for must be confirmed. `model`, a router
    catalog entry the administrator picks, becomes the agent's model; without it the role route applies."""
    from agent_company import recruitment, registry

    if not admin or not actor or actor in {"policy", "qualification", "system"}:
        raise PermissionError("only an administrator approves a marketplace import")
    with session() as s:
        imp = s.get(AgentImport, import_id)
        if imp is None:
            raise KeyError(import_id)
        passed = imp.status == "qualified" and (imp.eval or {}).get("status") == "passed"
        if imp.recruitment_id is None or not (passed or imp.status == "unevaluated"):
            raise ValueError(f"import #{imp.id} is {imp.status}; only a qualified one can be approved")
        if imp.status == "unevaluated" and not accept_unevaluated:
            raise ValueError(f"import #{imp.id} ran no eval suite: accept it unevaluated explicitly")
        if imp.licence == "unknown" and not accept_licence:
            raise ValueError("the package has no known licence: accept it explicitly")
        if (imp.screen or {}).get("acknowledge") and not acknowledge:
            raise ValueError("the screen found injected instructions or permission requests: acknowledge them")
        ceiling = permission_ceiling(imp.manifest["role"], imp.manifest.get("permissions"),
                                     list(grant) if grant is not None else None)
        a = s.get(Agent, imp.agent_id)
        if model:
            a.model_pref = _catalog_model(s, model)
        if imp.status == "unevaluated":
            imp.eval = {**(imp.eval or {}), "accepted_unevaluated_by": actor[:128], "accepted_at": now().isoformat()}
        a.permissions = ceiling["permissions"]
        a.autonomy = clamp_autonomy(autonomy or a.autonomy, ceiling["autonomy"])
        registry.record_version(s, a, actor, f"granted {', '.join(ceiling['permissions']) or 'nothing'} "
                                             f"(marketplace import #{imp.id})")
        imp.ceiling = {**ceiling, "autonomy": a.autonomy}
        rec_id = imp.recruitment_id
    out = recruitment.approve(rec_id, actor, grant=False)
    with session() as s:
        imp = s.get(AgentImport, import_id)
        imp.status, imp.decided_by, imp.decided_at = "approved", actor[:128], now()
        log_event("marketplace", f"import #{imp.id} approved by {actor}", s=s, agent_id=imp.agent_id)
    write_provenance(import_id)
    return {**get(import_id), "recruitment": out}


def reject(import_id: int, actor: str, reason: str = "", *, admin: bool = False) -> dict[str, Any]:
    """Retire the candidate (through recruitment.reject) and delete the fetched package."""
    from agent_company import recruitment

    if not admin or not actor or actor in {"policy", "qualification", "system"}:
        raise PermissionError("only an administrator rejects a marketplace import")
    with session() as s:
        imp = s.get(AgentImport, import_id)
        if imp is None:
            raise KeyError(import_id)
        if imp.status in {"approved", "rejected"}:
            raise ValueError(f"import #{imp.id} is {imp.status}")
        rec = s.get(Recruitment, imp.recruitment_id) if imp.recruitment_id else None
        waiting = rec is not None and rec.status in recruitment.WAITING
        if rec is not None and not waiting:
            recruitment._retire_candidate(s, rec, f"marketplace import #{imp.id} rejected by {actor}")
        rec_id = rec.id if waiting else None
    if rec_id is not None:
        recruitment.reject(rec_id, actor, reason)
    with session() as s:
        imp = s.get(AgentImport, import_id)
        imp.status, imp.decided_by, imp.decided_at = "rejected", actor[:128], now()
        imp.error = reason[:2000]
        log_event("marketplace", f"import #{imp.id} rejected by {actor}", s=s, agent_id=imp.agent_id)
    shutil.rmtree(src_dir(import_id).parent, ignore_errors=True)
    return get(import_id)


# --- reading ------------------------------------------------------------------------------------

def _row(imp: AgentImport) -> dict[str, Any]:
    return {"id": imp.id, "kind": imp.kind, "source": imp.source, "source_sha": imp.source_sha,
            "manifest": imp.manifest or {}, "licence": imp.licence, "author": imp.author, "version": imp.version,
            "status": imp.status, "screen": imp.screen or {}, "ceiling": imp.ceiling or {}, "eval": imp.eval or {},
            "recruitment_id": imp.recruitment_id, "agent_id": imp.agent_id, "created_by": imp.created_by,
            "decided_by": imp.decided_by, "error": imp.error, "created_at": imp.created_at.isoformat(),
            "decided_at": imp.decided_at.isoformat() if imp.decided_at else None}


def list_imports(status: str = "") -> list[dict[str, Any]]:
    with session() as s:
        q = select(AgentImport).order_by(AgentImport.id.desc())
        if status:
            q = q.where(AgentImport.status == status)
        return [_row(i) for i in s.scalars(q)]


def get(import_id: int) -> dict[str, Any]:
    with session() as s:
        imp = s.get(AgentImport, import_id)
        if imp is None:
            raise KeyError(import_id)
        return _row(imp)
