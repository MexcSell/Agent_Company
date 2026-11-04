"""File manager (administrators): browse, view, edit, create, rename, move, delete and
upload files inside the project folders and the data directory.

Every path is resolved and must stay under one of the roots (no `..`, no symlink
escapes), `.git` internals are read-only, and files over 2 MB are not opened as
text. The folder picker used when creating a project may list folder *names*
anywhere, but never file contents outside the roots.
"""

from __future__ import annotations

import mimetypes
import shutil
from pathlib import Path
from typing import Any

from sqlalchemy import select

from agent_company.config import settings
from agent_company.db import Project, session

MAX_TEXT = 2 * 1024 * 1024
KINDS = {".py": "python", ".js": "javascript", ".mjs": "javascript", ".ts": "typescript", ".tsx": "typescript",
         ".jsx": "javascript", ".json": "json", ".md": "markdown", ".mdx": "markdown", ".html": "html",
         ".css": "css", ".sh": "shell", ".toml": "toml", ".yaml": "yaml", ".yml": "yaml", ".sql": "sql",
         ".go": "go", ".rs": "rust", ".rb": "ruby", ".java": "java", ".txt": "text", ".csv": "csv",
         ".lock": "text", ".cfg": "text", ".ini": "text", ".env": "env", ".gitignore": "text"}


class FileError(ValueError):
    pass


def roots() -> list[dict[str, str]]:
    with session() as s:
        out = [{"name": p.name, "path": str(Path(p.path).resolve())} for p in s.scalars(select(Project)) if p.path]
    out.append({"name": "data", "path": str(settings().data_dir.resolve())})
    seen, uniq = set(), []
    for r in out:
        if r["path"] not in seen and Path(r["path"]).exists():
            seen.add(r["path"])
            uniq.append(r)
    return uniq


def resolve(path: str, must_exist: bool = True) -> Path:
    if not path or "\0" in path:
        raise FileError("no path given")
    p = Path(path).expanduser()
    p = p.resolve() if p.exists() or must_exist else p.parent.resolve() / p.name
    allowed = [Path(r["path"]) for r in roots()]
    if not any(p == r or r in p.parents for r in allowed):
        raise FileError(f"{p} is outside the project folders")
    if must_exist and not p.exists():
        raise FileError(f"{p} does not exist")
    return p


def _writable(p: Path) -> Path:
    if ".git" in p.parts:
        raise FileError("files inside .git cannot be changed here")
    return p


def kind_of(p: Path) -> str:
    if p.is_dir():
        return "folder"
    return KINDS.get(p.suffix.lower()) or KINDS.get(p.name.lower()) or (
        "image" if (mimetypes.guess_type(p.name)[0] or "").startswith("image/") else "file")


def listing(path: str) -> dict[str, Any]:
    p = resolve(path)
    if not p.is_dir():
        raise FileError(f"{p} is not a folder")
    items = []
    for child in sorted(p.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
        try:
            st = child.stat()
        except OSError:
            continue
        items.append({"name": child.name, "path": str(child), "kind": kind_of(child), "dir": child.is_dir(),
                      "size": st.st_size if child.is_file() else None, "modified": st.st_mtime})
    parent = str(p.parent) if any(Path(r["path"]) in p.parents for r in roots()) else None
    return {"path": str(p), "parent": parent, "items": items}


def read(path: str) -> dict[str, Any]:
    p = resolve(path)
    if p.is_dir():
        raise FileError("that is a folder")
    size = p.stat().st_size
    if size > MAX_TEXT:
        return {"path": str(p), "kind": kind_of(p), "size": size, "binary": True, "text": None}
    data = p.read_bytes()
    try:
        if b"\x00" in data:
            raise UnicodeDecodeError("utf-8", data, 0, 1, "NUL byte")
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return {"path": str(p), "kind": kind_of(p), "size": size, "binary": True, "text": None}
    return {"path": str(p), "kind": kind_of(p), "size": size, "binary": False, "text": text}


def write(path: str, text: str) -> dict[str, Any]:
    p = _writable(resolve(path, must_exist=False))
    if p.is_dir():
        raise FileError("that is a folder")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return {"path": str(p), "size": p.stat().st_size}


def create(path: str, folder: bool = False) -> dict[str, Any]:
    p = _writable(resolve(path, must_exist=False))
    if p.exists():
        raise FileError(f"{p.name} already exists")
    if folder:
        p.mkdir(parents=True)
    else:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()
    return {"path": str(p)}


def move(src: str, dest: str, copy: bool = False) -> dict[str, Any]:
    s = _writable(resolve(src))
    d = resolve(dest, must_exist=False)
    if d.is_dir():
        d = d / s.name
    d = _writable(resolve(str(d), must_exist=False))
    if d.exists():
        raise FileError(f"{d.name} already exists there")
    if s in d.parents:
        raise FileError("a folder cannot be moved into itself")
    if copy:
        (shutil.copytree if s.is_dir() else shutil.copy2)(s, d)
    else:
        shutil.move(str(s), str(d))
    return {"path": str(d)}


def delete(paths: list[str]) -> list[str]:
    done = []
    protected = {Path(r["path"]) for r in roots()}
    for raw in paths:
        p = _writable(resolve(raw))
        if p in protected:
            raise FileError(f"{p} is a project root and cannot be deleted here")
        shutil.rmtree(p) if p.is_dir() and not p.is_symlink() else p.unlink()
        done.append(str(p))
    return done


def upload(folder: str, name: str, data: bytes) -> dict[str, Any]:
    d = resolve(folder)
    if not d.is_dir():
        raise FileError("uploads go into a folder")
    safe = Path(name).name
    if not safe or safe in {".", ".."}:
        raise FileError("bad file name")
    target = _writable(resolve(str(d / safe), must_exist=False))
    target.write_bytes(data)
    return {"path": str(target), "size": len(data)}


def folders(path: str = "") -> dict[str, Any]:
    """Folder names only (for the project folder picker); works outside the roots."""
    p = Path(path or Path.home()).expanduser().resolve()
    if not p.is_dir():
        raise FileError(f"{p} is not a folder")
    subs = []
    try:
        for c in sorted(p.iterdir(), key=lambda c: c.name.lower()):
            if c.is_dir() and not c.name.startswith("."):
                subs.append({"name": c.name, "path": str(c), "git": (c / ".git").exists()})
    except PermissionError:
        pass
    return {"path": str(p), "parent": str(p.parent) if p.parent != p else None, "folders": subs[:500],
            "git": (p / ".git").exists()}
