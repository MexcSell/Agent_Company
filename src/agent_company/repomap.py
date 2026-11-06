"""Repository map (FW-R-01, after aider's repomap): the definitions of a repository, ranked by how often the other
files use their names, cut to a character budget, for the planner's prompt and a task's package.

Read from a commit (the goal's base branch, like the project standards), never from an agent's worktree: an
agent's edits do not reach the other agents' prompts. Python is parsed with `ast`; JavaScript/TypeScript, Go,
Rust, Java, Ruby and PHP by line patterns. Vendored, generated, minified and large files are skipped. A file's
definitions and identifier counts are cached per commit; the ranking is done per query, so names a task mentions
(or its paths) come first, as aider personalises its map by the files and names in the chat.
"""

from __future__ import annotations

import ast
import re
import subprocess
import threading
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Any

LANG = {".py": "py", ".js": "js", ".mjs": "js", ".cjs": "js", ".jsx": "js", ".ts": "js", ".tsx": "js", ".go": "go",
        ".rs": "rs", ".java": "java", ".kt": "java", ".rb": "rb", ".php": "php"}
SKIP_PARTS = {"node_modules", "vendor", "dist", "build", ".venv", "venv", "__pycache__", "site-packages", "migrations",
              "third_party", "external", ".git"}
MAX_FILE_BYTES, MAX_FILES = 200_000, 3000
PATTERNS = {
    "js": [r"^\s*(?:export\s+(?:default\s+)?)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)\s*\(",
           r"^\s*(?:export\s+(?:default\s+)?)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)",
           r"^\s*(?:export\s+)?(?:interface|type|enum)\s+([A-Za-z_$][\w$]*)",
           r"^\s{0,2}(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:function\b|\([^)]*\)\s*=>|[A-Za-z_$][\w$]*\s*=>)",
           r"^\s{2}(?:static\s+)?(?:async\s+)?(?!(?:if|for|while|switch|catch|function|return|with)\b)([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{\s*$"],
    "go": [r"^func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)\s*[\[(]", r"^type\s+([A-Za-z_]\w*)\s+(?:struct|interface)\b"],
    "rs": [r"^\s*(?:pub(?:\([\w:]+\))?\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+([A-Za-z_]\w*)",
           r"^\s*(?:pub(?:\([\w:]+\))?\s+)?(?:struct|enum|trait|type)\s+([A-Za-z_]\w*)"],
    "java": [r"^\s*(?:public\s+|protected\s+|private\s+|abstract\s+|final\s+|static\s+)*(?:class|interface|enum|record)\s+([A-Za-z_]\w*)",
             r"^\s+(?:public|protected|private)\s+(?:static\s+)?(?:final\s+)?[\w<>\[\],.? ]+\s+([A-Za-z_]\w*)\s*\("],
    "rb": [r"^\s*(?:def|class|module)\s+(?:self\.)?([A-Za-z_][\w:]*[?!]?)"],
    "php": [r"^\s*(?:abstract\s+|final\s+)?(?:class|interface|trait)\s+([A-Za-z_]\w*)",
            r"^\s*(?:public\s+|private\s+|protected\s+|static\s+)*function\s+([A-Za-z_]\w*)"],
}
_COMPILED = {k: [re.compile(p) for p in v] for k, v in PATTERNS.items()}
IDENT = re.compile(r"[A-Za-z_$][\w$]*")
CALL = re.compile(r"([A-Za-z_$][\w$]*)\s*\(")
TYPE_DEF = re.compile(r"\b(?:class|interface|type|struct|enum|trait|module|record)\s")
COMMON = {"main", "init", "run", "get", "set", "test", "setup", "update", "data", "value", "name", "self", "this",
          "start", "stop", "close", "open", "call", "render", "load", "save", "create", "delete", "list",
          "select", "count", "strip", "split", "join", "format", "append", "items", "keys", "values", "write_text",
          "read_text", "refresh", "filter", "find", "send", "parse", "toString"}
_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
_lock = threading.Lock()
CACHE_COMMITS = 8


def _git(repo: str, *args: str, data: bytes | None = None) -> bytes:
    proc = subprocess.run(["git", *args], cwd=repo, input=data, capture_output=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode(errors="replace").strip()[:200])
    return proc.stdout


def _wanted(path: str, size: int) -> bool:
    p = Path(path)
    if p.suffix not in LANG or size > MAX_FILE_BYTES or SKIP_PARTS & set(p.parts[:-1]):
        return False
    name = p.name
    return not (".min." in name or name.endswith((".d.ts", "_pb2.py", ".pb.go", ".generated.ts")))


def _python_defs(text: str) -> list[tuple[int, str, str, int]]:
    """(line, name, signature line, depth) of a module's classes, functions and methods."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    lines = text.splitlines()
    out = []

    def visit(nodes: list[ast.stmt], depth: int) -> None:
        for n in nodes:
            if isinstance(n, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                line = lines[n.lineno - 1].strip() if n.lineno <= len(lines) else n.name
                out.append((n.lineno, n.name, line, depth))
                if isinstance(n, ast.ClassDef) and depth == 0:
                    visit(n.body, 1)

    visit(tree.body, 0)
    return out


def _pattern_defs(text: str, lang: str) -> list[tuple[int, str, str, int]]:
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        if len(line) > 300:
            continue
        for rx in _COMPILED[lang]:
            m = rx.match(line)
            if m:
                out.append((i, m.group(1), line.strip(), 1 if line[:1] in {" ", "\t"} else 0))
                break
    return out


def _index(repo: str, rev: str) -> dict[str, Any]:
    """Definitions and identifier counts of every source file at `rev` (cached per commit)."""
    commit = _git(repo, "rev-parse", "--verify", f"{rev}^{{commit}}").decode().strip()
    with _lock:
        if commit in _cache:
            _cache.move_to_end(commit)
            return _cache[commit]
    files = []
    for row in _git(repo, "ls-tree", "-r", "-l", "-z", commit).split(b"\0"):
        if not row:
            continue
        meta, _, path = row.decode(errors="replace").partition("\t")
        parts = meta.split()
        if len(parts) == 4 and parts[1] == "blob" and parts[3].isdigit() and _wanted(path, int(parts[3])):
            files.append((path, parts[2]))
    files = files[:MAX_FILES]
    defs: dict[str, list[tuple[int, str, str, int]]] = {}
    idents: dict[str, Counter[str]] = {}
    calls: dict[str, Counter[str]] = {}
    if files:
        out = _git(repo, "cat-file", "--batch", data="".join(f"{sha}\n" for _, sha in files).encode())
        pos = 0
        for path, _sha in files:
            nl = out.index(b"\n", pos)
            size = int(out[pos:nl].split()[2])
            text = out[nl + 1:nl + 1 + size].decode(errors="replace")
            pos = nl + 1 + size + 1
            lang = LANG[Path(path).suffix]
            found = _python_defs(text) if lang == "py" else _pattern_defs(text, lang)
            if found:
                defs[path] = found
            idents[path] = Counter(IDENT.findall(text))
            calls[path] = Counter(CALL.findall(text))
    data = {"commit": commit, "defs": defs, "idents": idents, "calls": calls}
    with _lock:
        _cache[commit] = data
        while len(_cache) > CACHE_COMMITS:
            _cache.popitem(last=False)
    return data


def rank(data: dict[str, Any], query: str = "") -> list[tuple[float, str, int, str, int]]:
    """Every definition as (score, path, line, signature, depth), best first. As in aider's graph, a name scores by
    the other files that use it (each by the square root of its uses), shared by the files that define it; names
    and paths the query mentions are boosted; private, very common names and tests count less."""
    words = set(IDENT.findall(query or ""))
    q = (query or "").lower()
    names = {name for found in data["defs"].values() for _, name, _, _ in found}
    # A function is used where it is called, a class or type wherever its name appears: plain words that happen to
    # be names ("text", "state") do not count as uses of a function of that name.
    used: dict[str, Counter[str]] = {"calls": Counter(), "idents": Counter()}
    for kind, counter in used.items():
        for c in data[kind].values():
            for name in names & c.keys():
                counter[name] += c[name] ** 0.5
    definers = Counter(name for found in data["defs"].values() for name in {n for _, n, _, _ in found})
    out = []
    for path, found in data["defs"].items():
        own = {k: data[k].get(path, Counter()) for k in used}
        stem = Path(path).stem.lower()
        mentioned_file = path.lower() in q or (len(stem) >= 3 and re.search(rf"\b{re.escape(stem)}\b", q) is not None)
        test = "test" in Path(path).name.lower() or "tests" in Path(path).parts
        for line, name, sig, depth in found:
            kind = "idents" if TYPE_DEF.search(sig) else "calls"
            score = max(0.0, used[kind][name] - own[kind][name] ** 0.5) / definers[name]
            if name.startswith("_") or name.lower() in COMMON or len(name) <= 4:
                score *= 0.2
            if depth and not sig.lstrip().startswith(("def ", "async def ", "async ", "static ", "public ", "fn ", "pub ")):
                score *= 0.3  # a helper inside a function, not a method
            if test:
                score *= 0.3
            if name in words:
                score = score * 10 + 50
            if mentioned_file:
                score = score * 5 + 20
            score += 0.01 if depth == 0 else 0.0
            out.append((score, path, line, sig if len(sig) <= 140 else sig[:137] + "...", depth))
    out.sort(key=lambda d: (-d[0], d[1], d[2]))
    return out


def render(ranked: list[tuple[float, str, int, str, int]], budget: int) -> str:
    """The best definitions grouped by file (files in the order of their best definition), within `budget` chars."""
    chosen: dict[str, list[tuple[int, str, int]]] = {}
    order: list[str] = []
    used = 0
    for _score, path, line, sig, depth in ranked:
        head = 0 if path in chosen else len(path) + 2
        cost = head + len(sig) + 3 + 2 * depth
        if used + cost > budget:
            continue
        if path not in chosen:
            chosen[path] = []
            order.append(path)
        chosen[path].append((line, sig, depth))
        used += cost
        if used >= budget - 40:
            break
    return "\n".join(f"{p}:\n" + "\n".join(f"{'  ' * (d + 1)}{s}" for _, s, d in sorted(chosen[p])) for p in order)


def repo_map(repo: str | Path, rev: str = "HEAD", query: str = "", budget: int = 4000) -> str:
    """The map of `repo` at `rev` for a task described by `query`, at most `budget` characters ("" if none)."""
    if budget <= 0 or not repo or not (Path(repo) / ".git").exists():
        return ""
    try:
        data = _index(str(repo), rev)
    except (RuntimeError, OSError, subprocess.SubprocessError, ValueError, IndexError):
        return ""
    return render(rank(data, query), budget)
