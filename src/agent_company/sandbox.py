"""L3 isolation for the commands that execute agent-written code (SPEC-14 S14-R-07, SELF-EVOLUTION SE-R-22).

Quality commands, milestone checks and code-improvement benchmarks run code the agents wrote. With the sandbox
they run under bubblewrap (namespaces, as containers use them) instead of directly on the host:

  filesystem   read-only: the system directories (`/usr`, a few files of `/etc`) and the toolchains (uv's
               Pythons, the server user's `~/.local/bin`, the server's virtualenv, `sandbox_paths`); writable: the
               work directory. The worktree's `.git` and its git directories are read-only, so a check cannot plant
               a hook that host-side git runs later. `data/`, `.env`, other worktrees, `/home` and the server user's
               home are not there (`_secrets` are hidden even below a bound directory); HOME is
               `data/sandbox/home`, used by sandboxed runs only (their tool caches, never the host's).
  network      none. The exceptions are the prepare step (`PREPARE`): a fixed install command for a lock file
               (`uv sync --frozen`, `npm ci --ignore-scripts`), run with network when the lock file changed since
               the last prepare of that directory, so offline checks find their dependencies; and the dependency
               audit (`dependencies.audit`: `pip-audit`, `npm audit` with fixed arguments, the worktree read-only).
  processes    own PID, IPC and UTS namespaces, every capability dropped, a new session, killed with its parent.
  limits       memory (no swap), tasks and CPU through a transient systemd scope when `systemd-run` works;
               otherwise the run notes "no resource limits". The timeout is the caller's.
  environment  only `ENV_KEEP` names and prefixes (never a key, token, secret or password), plus `sandbox_env`.
               The host run (L2) gets the same filtered environment.

Modes (setting `sandbox`, a governance key; a project may only make it stricter; A2A goals always `required`):
  off       the command runs on the host in the worktree (L2)
  auto      L3 when bubblewrap works, else L2 with the reason noted on the result (default)
  required  L3 or the command does not run (it fails with the reason)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from agent_company.config import settings

MODES = ("off", "auto", "required")
ETC = ("ld.so.cache", "ld.so.conf", "ld.so.conf.d", "ssl", "ca-certificates", "alternatives", "passwd", "group",
       "nsswitch.conf", "localtime", "timezone", "mime.types", "gitconfig", "python3", "python3.12", "python3.13")
NET_ETC = ("resolv.conf", "hosts", "gai.conf")  # name resolution, only for the prepare step
ENV_KEEP = ("LANG", "LANGUAGE", "LC_", "TERM", "TZ", "USER", "LOGNAME", "SHELL", "UV_", "PIP_", "PYTHON", "NODE_",
            "CARGO_", "RUSTUP_", "GO", "JAVA_HOME", "CI")
SECRETISH = re.compile(r"(?i)KEY|TOKEN|SECRET|PASSW|CREDENTIAL|AUTH|COOKIE|SESSION|PRIVATE")
# bwrap (or the systemd-run scope around it) could not start the command: a fault of the host, not of the work
INFRA_RE = re.compile(
    r"^(bwrap: (Can't find source path|No permissions to create new namespace|setting up uid map"
    r"|Creating new namespace failed|execvp)"
    r"|Failed to (start transient scope unit|create bus connection|connect to bus))")
STARTUP_LINE = re.compile(r"^(bwrap: |Failed to (start transient scope unit|create bus connection|connect to bus))")
# lock file -> (tool, install command, directory it fills): re-run when the lock changed or the directory is gone
PREPARE = {"uv.lock": ("uv", "uv sync --frozen", ".venv"),
           "package-lock.json": ("npm", "npm ci --ignore-scripts", "node_modules")}

_probe: tuple[bool, str] | None = None
_limits_probe: bool | None = None


@dataclass
class Outcome:
    returncode: int
    output: str
    isolation: str  # "L3" sandboxed, "L2" on the host in the work directory, "" did not run
    note: str = ""  # why it fell back, what it could not apply, the prepare step's result
    timed_out: bool = False
    infra: str = ""  # the start-up line when the sandbox itself did not start (`startup_failure`); keep it last


def startup_failure(rc: int, output: str, timed_out: bool) -> str:
    """The matched start-up line when bwrap (or the systemd-run scope around it) could not start the command, else "".
    Only output made solely of start-up lines counts: a command that started and printed anything is its own failure."""
    if rc == 0 or timed_out:
        return ""
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    if not lines or len(lines) > 5 or not all(STARTUP_LINE.match(ln) for ln in lines):
        return ""
    return next((ln[:300] for ln in lines if INFRA_RE.match(ln)), "")


def reset() -> None:
    """Forget the probes (tests, or after bubblewrap was installed)."""
    global _probe, _limits_probe
    _probe = _limits_probe = None


def home() -> Path:
    d = settings().data_dir / "sandbox" / "home"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _toolchain() -> list[Path]:
    """Read-only paths a sandboxed command may use beyond /usr: uv's Python builds, the server user's tool
    binaries, the server's own virtualenv (benchmarks run its interpreter) and `sandbox_paths`."""
    user = Path.home()
    paths = [user / ".local" / "share" / "uv" / "python", user / ".local" / "bin", Path(sys.prefix),
             Path(sys.base_prefix)]
    paths += [Path(p) for p in settings().sandbox_paths]
    out: list[Path] = []
    for p in paths:
        p = p.resolve() if p.exists() else p
        if p.exists() and not p.is_relative_to("/usr") and p != Path("/") and not any(p.is_relative_to(q) for q in out):
            out.append(p)
    return out


def _secrets() -> list[Path]:
    """Paths never shown inside the sandbox, even below a bound directory."""
    from agent_company.config import REPO_ROOT

    return [settings().data_dir.resolve(), (REPO_ROOT / "data").resolve(), REPO_ROOT / ".env",
            REPO_ROOT / ".git", Path.home() / ".claude", Path.home() / ".codex", Path.home() / ".ssh"]


def _git_dirs(workdir: Path) -> list[Path]:
    """The work directory's `.git` and, for a linked worktree, its git directory and the common one."""
    dot = workdir / ".git"
    if not dot.exists():
        return []
    out = [dot]
    try:
        p = subprocess.run(["git", "rev-parse", "--absolute-git-dir", "--git-common-dir"], cwd=workdir, text=True,
                           capture_output=True, timeout=30)
        for line in p.stdout.splitlines() if p.returncode == 0 else []:
            d = (workdir / line).resolve()
            if d.is_dir() and d not in out and not d.is_relative_to(workdir):
                out.append(d)
    except (OSError, subprocess.SubprocessError):
        pass
    return out


def env_for(extra: dict[str, str] | None = None, home_dir: Path | str | None = None) -> dict[str, str]:
    """The environment of a checked command: allow-listed names, never one that looks like a credential."""
    keep = tuple(ENV_KEEP) + tuple(settings().sandbox_env)
    env = {k: v for k, v in os.environ.items() if k.startswith(keep) and not SECRETISH.search(k)}
    env.update({k: os.environ[k] for k in settings().sandbox_env if k in os.environ})  # named exactly by an admin
    user = Path.home()
    env["PATH"] = ":".join([str(user / ".local" / "bin"), str(Path(sys.executable).parent), "/usr/local/sbin",
                            "/usr/local/bin", "/usr/sbin", "/usr/bin", "/sbin", "/bin"])
    env["HOME"] = str(home_dir or user)
    env.setdefault("LANG", "C.UTF-8")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(extra or {})
    return env


def build(argv: list[str], cwd: Path, *, writable: list[Path], readonly: list[Path] | None = None,
          network: bool = False, env: dict[str, str] | None = None, limits: bool | None = None) -> list[str]:
    """The bubblewrap command line (wrapped in a systemd scope with limits when `limits`)."""
    s = settings()
    cmd = ["bwrap", "--die-with-parent", "--new-session", "--cap-drop", "ALL", "--unshare-pid", "--unshare-ipc",
           "--unshare-uts", "--unshare-cgroup-try"]
    if not network:
        cmd.append("--unshare-net")
    cmd += ["--ro-bind", "/usr", "/usr"]
    for name in ("bin", "sbin", "lib", "lib32", "lib64", "libx32"):
        p = Path("/") / name
        if p.is_symlink():
            cmd += ["--symlink", os.readlink(p), str(p)]
        elif p.is_dir():
            cmd += ["--ro-bind", str(p), str(p)]
    for name in ETC + (NET_ETC if network else ()):
        cmd += ["--ro-bind-try", f"/etc/{name}", f"/etc/{name}"]
    cmd += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--tmpfs", "/run",
            "--bind", str(home()), str(Path.home())]
    bound = _toolchain() + [Path(p).resolve() for p in readonly or []]
    for p in bound:
        cmd += ["--ro-bind", str(p), str(p)]
    masked = [p for p in dict.fromkeys(_secrets()) if p.exists() and any(p != b and p.is_relative_to(b) for b in bound)]
    for p in masked:  # e.g. a benchmark of the main checkout: its data/ and .env stay hidden
        cmd += ["--tmpfs", str(p)] if p.is_dir() else ["--ro-bind", "/dev/null", str(p)]
    for p in bound:  # a path asked for by name below a hidden one (a benchmark script in data/)
        if any(p.is_relative_to(m) for m in masked):
            cmd += ["--ro-bind", str(p), str(p)]
    for p in writable:
        cmd += ["--bind", str(p), str(p)]
        for g in _git_dirs(p):
            cmd += ["--ro-bind", str(g), str(g)]
    cmd += ["--chdir", str(cwd), "--clearenv"]
    for k, v in (env or {}).items():
        cmd += ["--setenv", k, v]
    cmd += ["--", *argv]
    if limits if limits is not None else limits_available():
        cmd = ["systemd-run", "--scope", "--quiet", "--collect", "-p", f"MemoryMax={s.sandbox_memory_mb}M",
               "-p", "MemorySwapMax=0", "-p", f"TasksMax={s.sandbox_tasks}",
               "-p", f"CPUQuota={int(s.sandbox_cpus * 100)}%", "--", *cmd]
    return cmd


def available() -> tuple[bool, str]:
    """(usable, reason): bubblewrap is installed and can start a sandbox here (probed once)."""
    global _probe
    if _probe is None:
        if not shutil.which("bwrap"):
            _probe = (False, "bubblewrap (bwrap) is not installed")
        else:
            try:
                p = subprocess.run(build(["/bin/true"], Path("/"), writable=[], limits=False), capture_output=True,
                                   text=True, timeout=30)
                _probe = (True, "") if p.returncode == 0 else (False, f"bwrap failed: {p.stderr.strip()[:200]}")
            except (OSError, subprocess.SubprocessError) as e:
                _probe = (False, f"bwrap failed: {e}")
    return _probe


def limits_available() -> bool:
    """A transient systemd scope can set memory, task and CPU limits (probed once)."""
    global _limits_probe
    if _limits_probe is None:
        _limits_probe = False
        if shutil.which("systemd-run"):
            try:
                _limits_probe = subprocess.run(["systemd-run", "--scope", "--quiet", "--collect", "-p", "TasksMax=8",
                                                "--", "/bin/true"], capture_output=True, timeout=30).returncode == 0
            except (OSError, subprocess.SubprocessError):
                pass
    return _limits_probe


def status() -> dict[str, object]:
    ok, why = available()
    s = settings()
    return {"mode": s.sandbox, "available": ok, "reason": why, "limits": limits_available(),
            "memory_mb": s.sandbox_memory_mb, "tasks": s.sandbox_tasks, "cpus": s.sandbox_cpus,
            "home": str(home()), "toolchain": [str(p) for p in _toolchain()]}


def mode_for(goal=None, project_id: int | None = None, s=None) -> str:  # noqa: ANN001 - a Goal row
    """The sandbox mode for a goal's or project's commands: the company setting, made stricter by the project's
    rule (`policies.effective`); work that came in over A2A and a trial that asks for it (a marketplace
    candidate's eval run, REQ-002) are always `required`, whatever the settings say."""
    from agent_company import policies

    if goal is not None:
        if goal.origin == "a2a" or (goal.trial or {}).get("sandbox") == "required":
            return "required"
        project_id = goal.project_id
    return policies.effective("sandbox", project_id, s=s)


def _execute(cmd: list[str], cwd: Path, env: dict[str, str] | None, timeout: int) -> tuple[int, str, bool]:
    proc = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, env=env, start_new_session=True)
    try:
        out, _ = proc.communicate(timeout=timeout)
        return proc.returncode, out or "", False
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        out, _ = proc.communicate()
        return -9, out or "", True


def _prepare(workdir: Path, env: dict[str, str], timeout: int) -> tuple[str, str]:
    """Install the lock file's dependencies with network, inside the sandbox: once per lock-file version, again
    when what it installed is gone (a milestone check's checkout is recreated each time). Returns (notes, infra):
    `infra` is the start-up line when the sandbox did not start; no further lock file is tried then."""
    notes = []
    state = home().parent / "prepared.json"
    done = json.loads(state.read_text()) if state.exists() else {}
    for lock, (tool, command, fills) in PREPARE.items():
        f = workdir / lock
        if not f.is_file() or not shutil.which(tool):
            continue
        key, digest = f"{workdir}:{lock}", hashlib.sha256(f.read_bytes()).hexdigest()
        if done.get(key) == digest and (workdir / fills).exists():  # a recreated checkout has to install again
            continue
        rc, out, timed_out = _execute(build(["/bin/sh", "-c", command], workdir, writable=[workdir], network=True,
                                            env=env), workdir, None, timeout)
        infra = startup_failure(rc, out, timed_out)
        if infra:
            notes.append(f"prepare `{command}`: sandbox did not start: {infra}")
            state.write_text(json.dumps(done))
            return "; ".join(notes), infra
        if rc == 0:
            done[key] = digest
            notes.append(f"prepared: {command}")
        else:
            notes.append(f"prepare `{command}` {'timed out' if timed_out else f'exit {rc}'}: {out.strip()[-300:]}")
    state.write_text(json.dumps(done))
    return "; ".join(notes), ""


def run(argv: list[str], cwd: Path, timeout: int, *, mode: str | None = None, writable: list[Path] | None = None,
        readonly: list[Path] | None = None, env: dict[str, str] | None = None, prepare: bool = False,
        network: bool = False) -> Outcome:
    """Run `argv` in `cwd` at L3 (or L2 as the mode allows). `writable` defaults to `[cwd]`; `env` replaces the
    filtered default. `prepare` installs lock-file dependencies first (see `PREPARE`). `network` is for the
    platform's own fixed commands only (the dependency audit, S08-R-05), never for agent-written code."""
    mode = mode or settings().sandbox
    cwd = Path(cwd).resolve()
    writable = [Path(p).resolve() for p in (writable if writable is not None else [cwd])]
    ok, why = available() if mode != "off" else (False, "sandbox off")
    if not ok:
        if mode == "required":
            return Outcome(126, "", "", f"not run: the sandbox is required and unavailable ({why})")
        rc, out, timed_out = _execute(argv, cwd, env if env is not None else env_for(), timeout)
        return Outcome(rc, out, "L2", "" if mode == "off" else f"ran on the host: {why}", timed_out)
    env = env if env is not None else env_for({"UV_OFFLINE": "1", "UV_PYTHON_DOWNLOADS": "never"})
    notes = [] if limits_available() else ["no resource limits (systemd-run unavailable)"]
    if prepare:
        note, infra = _prepare(cwd, {k: v for k, v in env.items() if k != "UV_OFFLINE"}, timeout)
        notes.append(note)
        if infra:  # the same bwrap would fail again: the command is not started
            return Outcome(1, "", "L3", "; ".join(n for n in notes if n), infra=infra)
    rc, out, timed_out = _execute(build(argv, cwd, writable=writable, readonly=readonly, env=env, network=network),
                                  cwd, None, timeout)
    return Outcome(rc, out, "L3", "; ".join(n for n in notes if n), timed_out,
                   infra=startup_failure(rc, out, timed_out))
