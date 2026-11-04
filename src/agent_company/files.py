"""File manager (administrators): browse, view, edit, create, rename, move, delete,
upload, compress, extract and download files inside the project folders, the data
directory and folders an administrator opened in the Workspace ("Open folder").

Every path is resolved and must stay under one of the roots (no `..`, no symlink
escapes), `.git` internals are read-only, and files over 2 MB are not opened as
text. The folder picker used when creating a project may list folder *names*
anywhere, but never file contents outside the roots.
"""

from __future__ import annotations

import mimetypes
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from sqlalchemy import select

from agent_company.config import settings
from agent_company.db import Project, Setting, session

MAX_TEXT = 2 * 1024 * 1024
KINDS = {".py": "python", ".js": "javascript", ".mjs": "javascript", ".ts": "typescript", ".tsx": "typescript",
         ".jsx": "javascript", ".json": "json", ".md": "markdown", ".mdx": "markdown", ".html": "html",
         ".css": "css", ".sh": "shell", ".toml": "toml", ".yaml": "yaml", ".yml": "yaml", ".sql": "sql",
         ".go": "go", ".rs": "rust", ".rb": "ruby", ".java": "java", ".txt": "text", ".csv": "csv",
         ".lock": "text", ".cfg": "text", ".ini": "text", ".env": "env", ".gitignore": "text",
         ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp", ".cc": "cpp", ".cs": "csharp", ".php": "php",
         ".kt": "kotlin", ".swift": "swift", ".lua": "lua", ".r": "r", ".dart": "dart", ".scala": "scala",
         ".vue": "html", ".svelte": "html", ".xml": "xml", ".diff": "diff", ".patch": "diff", ".log": "log",
         ".tsv": "csv", ".ipynb": "json", ".bash": "shell", ".zsh": "shell", ".ps1": "shell",
         "dockerfile": "docker", "makefile": "make", ".dockerignore": "text",
         ".pdf": "pdf", ".docx": "docx", ".svg": "svg", ".xlsx": "sheet", ".xls": "sheet", ".pptx": "slides",
         ".zip": "archive", ".tar": "archive", ".gz": "archive", ".tgz": "archive", ".bz2": "archive",
         ".xz": "archive", ".7z": "archive", ".rar": "archive"}
EXTRA_ROOTS = "files.extra_roots"  # Setting key: folders opened in the Workspace
ARCHIVES = (".zip", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")


class FileError(ValueError):
    pass


def _extra_roots() -> list[str]:
    with session() as s:
        row = s.get(Setting, EXTRA_ROOTS)
        return list((row.value or {}).get("v") or []) if row else []


def roots() -> list[dict[str, str]]:
    with session() as s:
        out = [{"name": p.name, "path": str(Path(p.path).resolve())} for p in s.scalars(select(Project)) if p.path]
    out.append({"name": "data", "path": str(settings().data_dir.resolve())})
    out += [{"name": Path(r).name or r, "path": r, "opened": "1"} for r in _extra_roots()]
    seen, uniq = set(), []
    for r in out:
        if r["path"] not in seen and Path(r["path"]).exists():
            seen.add(r["path"])
            uniq.append(r)
    return uniq


def add_root(path: str) -> dict[str, str]:
    """Open a folder anywhere on the host as a file-manager root (Workspace "Open folder")."""
    p = Path(path or "").expanduser().resolve()
    if not path or not p.is_dir():
        raise FileError(f"{p} is not a folder")
    with session() as s:
        row = s.get(Setting, EXTRA_ROOTS)
        current = list((row.value or {}).get("v") or []) if row else []
        if str(p) not in current:
            current.append(str(p))
        if row is None:
            s.add(Setting(key=EXTRA_ROOTS, value={"v": current}))
        else:
            row.value = {"v": current}
    return {"name": p.name or str(p), "path": str(p)}


def remove_root(path: str) -> None:
    target = str(Path(path).expanduser().resolve())
    with session() as s:
        row = s.get(Setting, EXTRA_ROOTS)
        if row is not None:
            row.value = {"v": [r for r in (row.value or {}).get("v") or [] if r != target]}


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
    known = KINDS.get(p.suffix.lower()) or KINDS.get(p.name.lower())
    if known:
        return known
    mime = mimetypes.guess_type(p.name)[0] or ""
    for prefix in ("image", "audio", "video"):
        if mime.startswith(prefix + "/"):
            return prefix
    return "file"


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


def _unique(p: Path, keep_suffix: bool = True) -> Path:
    """`p`, or "name (2).ext", "name (3).ext"… if taken (folders: "name (2)")."""
    if not p.exists():
        return p
    if not keep_suffix:
        stem, suffix = p.name, ""
    else:
        stem, suffix = (p.name[: -len(".tar.gz")], ".tar.gz") if p.name.endswith(".tar.gz") else (p.stem, p.suffix)
    n = 2
    while (p.parent / f"{stem} ({n}){suffix}").exists():
        n += 1
    return p.parent / f"{stem} ({n}){suffix}"


def _zip_into(items: list[Path], target: Path) -> None:
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for item in items:
            if item.is_dir():
                z.write(item, arcname=item.name)
                for f in sorted(item.rglob("*")):
                    if f == target or f.is_symlink():
                        continue
                    if f.is_dir() or f.is_file():
                        z.write(f, arcname=str(Path(item.name) / f.relative_to(item)))
            else:
                z.write(item, arcname=item.name)


def compress(paths: list[str], name: str = "") -> dict[str, Any]:
    """Zip files and folders into an archive next to the first one."""
    items = [resolve(p) for p in paths]
    if not items:
        raise FileError("nothing to compress")
    folder = items[0].parent
    default = f"{items[0].name}.zip" if len(items) == 1 else "Archive.zip"
    safe = Path(name).name if name else default
    if not safe.lower().endswith(".zip"):
        safe += ".zip"
    target = _writable(resolve(str(_unique(folder / safe)), must_exist=False))
    _zip_into(items, target)
    return {"path": str(target), "size": target.stat().st_size}


def download_archive(paths: list[str]) -> tuple[Path, str]:
    """A temporary zip of `paths` for download; the caller deletes it after sending."""
    items = [resolve(p) for p in paths]
    if not items:
        raise FileError("nothing to download")
    tmp_dir = settings().data_dir / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".zip", dir=tmp_dir)
    os.close(fd)
    _zip_into(items, Path(tmp))
    return Path(tmp), (f"{items[0].name}.zip" if len(items) == 1 else "download.zip")


def extract(path: str) -> dict[str, Any]:
    """Unpack a zip or tar archive into a new folder beside it (no paths outside that folder)."""
    src = resolve(path)
    lower = src.name.lower()
    suffix = next((a for a in sorted(ARCHIVES, key=len, reverse=True) if lower.endswith(a)), None)
    if suffix is None or src.is_dir():
        raise FileError("only .zip and .tar(.gz/.bz2/.xz) archives can be extracted")
    dest = _writable(resolve(str(_unique(src.parent / src.name[: -len(suffix)], keep_suffix=False)), must_exist=False))
    try:
        if suffix == ".zip":  # shutil's zip unpacker drops absolute and ".." member names itself
            shutil.unpack_archive(str(src), str(dest))
        else:
            shutil.unpack_archive(str(src), str(dest), filter="data")
    except (shutil.ReadError, zipfile.BadZipFile, OSError, ValueError) as e:
        shutil.rmtree(dest, ignore_errors=True)
        raise FileError(f"could not extract {src.name}: {e}") from e
    return {"path": str(dest)}


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


def git_status(path: str) -> dict[str, str]:
    """VS Code-style decorations for the explorer: {absolute path: M|A|D|U|R|C} for changed files under `path`
    (a folder inside a git work tree), and "dot" for folders that contain changes. Empty outside git."""
    import subprocess

    p = resolve(path)
    try:
        top = subprocess.run(["git", "-C", str(p), "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                             timeout=10)
        if top.returncode != 0:
            return {}
        root = Path(top.stdout.strip())
        out = subprocess.run(["git", "-C", str(root), "status", "--porcelain=v1", "-z", "--untracked-files=normal"],
                             capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.TimeoutExpired):
        return {}
    marks: dict[str, str] = {}
    parts = out.split("\0")
    i = 0
    while i < len(parts):
        entry = parts[i]
        i += 1
        if len(entry) < 4:
            continue
        xy, rel = entry[:2], entry[3:]
        if xy[0] in "RC":
            i += 1  # the original name follows a rename or copy
        code = "U" if xy == "??" else "D" if "D" in xy else "A" if "A" in xy else xy[0] if xy[0] in "RC" else "M"
        full = root / rel.rstrip("/")
        marks[str(full)] = code
        for parent in full.parents:
            if parent == root or root not in parent.parents:
                break
            marks.setdefault(str(parent), "dot")
    return {k: v for k, v in marks.items() if k == str(p) or k.startswith(str(p) + "/")}
