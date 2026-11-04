"""Static checks the Quality Gate runs on a goal's change.

  secrets   added lines that look like credentials (SPEC14 secret scanning)
  syntax    changed files parse (Python, JSON, TOML, shell; JS/Ruby/PHP/Go when the tool exists)
  scope     changed files stay inside the goal's allowed paths; existing tests are not edited
            when the goal protects them (scope rules, as in /home/thomas/bit)

Also: test-file detection and extraction of the check commands a goal's text names.
"""

from __future__ import annotations

import ast
import fnmatch
import json
import re
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import Any

from agent_company import worktrees

SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("private key", re.compile(r"-----BEGIN (RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY")),
    ("AWS access key", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("Anthropic key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("OpenRouter key", re.compile(r"\bsk-or-v1-[A-Za-z0-9]{20,}")),
    ("OpenAI key", re.compile(r"\bsk-(proj-)?[A-Za-z0-9]{32,}")),
    ("GitHub token", re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{40,}")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("Stripe live key", re.compile(r"\b(sk|rk)_live_[0-9a-zA-Z]{20,}")),
    ("hard-coded credential",
     re.compile(r"""(?i)\b(password|passwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token)\b\s*[:=]\s*["'][^"'\s]{12,}["']""")),
]
PLACEHOLDER = re.compile(r"(?i)(example|placeholder|changeme|dummy|xxx+|your[_-]|<[^>]+>|\.\.\.|test|fake)")

TEST_FILE = re.compile(
    r"^(test|spec)/|(^|/)(tests|__tests__)/"
    r"|(^|/)test_[^/]*\.py$|_test\.(py|go|rb)$|_spec\.rb$|\.(test|spec)\.(js|jsx|ts|tsx|mjs|cjs)$"
)

CHECK_COMMAND = re.compile(
    r"^(uv run |poetry run |python3? -m )?(pytest|ruff (check|format --check)|mypy|flake8|tox|manage\.py test)\b"
    r"|^(go (test|vet)|npm (test|run (test|lint|build|typecheck))|pnpm (test|run)|yarn (test|lint)|cargo (test|check|clippy)"
    r"|make (test|check|lint)|bundle exec (rspec|rake)|rspec|npx (vitest|jest|playwright test|tsc))\b"
)


def is_test_file(rel: str) -> bool:
    return bool(TEST_FILE.search(rel))


def extract_check_commands(text: str, limit: int = 4) -> list[str]:
    """Test/lint commands the text names in backticks or shell blocks (`pytest tests/x`, ```npm test```)."""
    found: list[str] = []
    fenced = re.compile(r"```(?:bash|sh|shell|console)?[ \t]*\n(.*?)```", re.S)
    spans = [line for block in fenced.findall(text) for line in block.splitlines()]
    spans += re.findall(r"`([^`\n]+)`", fenced.sub("", text))
    for raw in spans:
        cmd = " ".join(raw.strip().removeprefix("$ ").split())
        if cmd and CHECK_COMMAND.search(cmd) and cmd not in found:
            found.append(cmd)
    return found[:limit]


def _added_lines(worktree: Path, base: str) -> list[tuple[str, str]]:
    diff = worktrees.git(worktree, "diff", "--unified=0", "--no-color", f"{base}...HEAD", check=False)
    out, current = [], ""
    for line in diff.splitlines():
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else ""
        elif line.startswith("+") and not line.startswith("+++") and current:
            out.append((current, line[1:]))
    return out


def scan_secrets(worktree: Path, base: str) -> list[str]:
    hits = []
    for path, line in _added_lines(worktree, base):
        for name, pattern in SECRET_PATTERNS:
            m = pattern.search(line)
            if m and not PLACEHOLDER.search(m.group(0)):
                hits.append(f"{path}: {name}")
                break
    return sorted(set(hits))


def _tool(cmd: list[str], cwd: Path) -> str | None:
    if not shutil.which(cmd[0]):
        return None
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=60)
    return None if p.returncode == 0 else (p.stderr or p.stdout).strip()[-400:]


def syntax_errors(worktree: Path, files: list[str]) -> list[str]:
    errs = []
    for rel in files:
        path = worktree / rel
        if not path.is_file() or path.stat().st_size > 2_000_000:
            continue
        suffix = path.suffix.lower()
        try:
            if suffix in {".py", ".pyi"}:
                ast.parse(path.read_text(), filename=rel)
            elif suffix == ".json":
                json.loads(path.read_text())
            elif suffix == ".toml":
                tomllib.loads(path.read_text())
            else:
                tool = {".sh": ["bash", "-n", rel], ".bash": ["bash", "-n", rel], ".js": ["node", "--check", rel],
                        ".mjs": ["node", "--check", rel], ".cjs": ["node", "--check", rel], ".rb": ["ruby", "-c", rel],
                        ".php": ["php", "-l", rel], ".go": ["gofmt", "-e", "-l", rel]}.get(suffix)
                problem = _tool(tool, worktree) if tool else None
                if problem:
                    errs.append(f"{rel}: {problem}")
        except (SyntaxError, ValueError, UnicodeDecodeError) as e:
            errs.append(f"{rel}: {type(e).__name__}: {e}")
    return errs


def _changed_with_status(worktree: Path, base: str) -> list[tuple[str, str]]:
    out = worktrees.git(worktree, "diff", "--name-status", "--no-renames", f"{base}...HEAD", check=False)
    rows = []
    for line in out.splitlines():
        status, _, path = line.partition("\t")
        if path:
            rows.append((status[:1], path))
    return rows


def scope_violations(worktree: Path, base: str, allowed: list[str], protect_tests: bool) -> list[str]:
    problems = []
    for status, path in _changed_with_status(worktree, base):
        if path.startswith((".claude/", ".mcp.json")):  # the goal's spec artifacts and Claude Code configuration
            continue
        if allowed and not any(fnmatch.fnmatch(path, pat) or path.startswith(pat.rstrip("*").rstrip("/") + "/")
                               for pat in allowed):
            problems.append(f"{path}: outside the allowed paths ({', '.join(allowed)})")
        elif protect_tests and status in {"M", "D"} and is_test_file(path):
            problems.append(f"{path}: existing test {'changed' if status == 'M' else 'deleted'} (tests are protected)")
    return problems


def run_static(worktree: Path, base: str, allowed: list[str], protect_tests: bool) -> list[dict[str, Any]]:
    changed = [p for s, p in _changed_with_status(worktree, base) if s != "D"]
    secrets = scan_secrets(worktree, base)
    syntax = syntax_errors(worktree, changed)
    scope = scope_violations(worktree, base, allowed, protect_tests)
    return [
        {"check": "secret scan", "passed": not secrets, "detail": "; ".join(secrets) or "no credentials added"},
        {"check": "syntax", "passed": not syntax, "detail": "\n".join(syntax) or f"{len(changed)} changed file(s) parse"},
        {"check": "scope", "passed": not scope, "detail": "\n".join(scope) or "all changes in scope"},
    ]
