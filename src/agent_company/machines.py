"""Machines: Linux and Windows virtual machines on this host's KVM (the Machines page, admins only).

Ported from AXoR 3.0's Machines (/home/thomas/axor-3.0.zip: backend/app/machines.py and
frontend/machines.js; AXoR is the workbench installed on /home/thomas/bit). AXoR runs each machine
as a Kubernetes Deployment; this host has no cluster, so here a machine is one Docker container:
QEMU in a container with /dev/kvm, with AXoR's images, system list, checks and console.
- Linux runs on qemux/qemu. It downloads the distribution's installer or live image at first start,
  and you install it from the console as on a new PC. Arch Linux comes ready-made, and any other ISO
  or disk image can be given by URL.
- Windows runs on dockurr/windows. It downloads Windows from Microsoft and installs it unattended,
  with the account, password and language set here. The editions are unactivated or evaluation
  ones: activate them with your own licence.
The disk is a named volume (ac-machine-<name>-disk) that outlives restarts, edits and a removed
container; deleting the machine deletes it. Settings live in the `machines` table; the Windows
account's password only in data/machines/<name>.env (mode 600), which Windows setup reads.
The container gets /dev/kvm and nothing else from the host: no privileges, no-new-privileges, a few
capabilities dropped, CPU and memory limits a little above the VM's own. The VM's network is
user-mode (passt): it reaches the internet, and (unlike AXoR, which had a network policy) also
whatever the container reaches on this host's networks.
The console: the page's noVNC client (static/vendor/novnc) speaks over a WebSocket to the API, which
relays it to QEMU's VNC port on the container's bridge address. That port is never published on a
host port, but processes on this host can reach the bridge.
Like containers.py, this is the administrators' tool: admin API only, every change and console
audited there; agents and executors never call it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_company.config import settings
from agent_company.containers import MACHINE_LABEL
from agent_company.db import Machine, now, session

PREFIX, LABEL = "ac-machine-", MACHINE_LABEL  # containers.cleanup() never prunes this label
IMAGES = {"linux": "qemux/qemu:7.50", "windows": "dockurr/windows:6.06"}
VNC = 5900
STOP_TIMEOUT = 130  # the image shuts the guest down (ACPI) and waits up to 105 s
NAME = re.compile(r"^[a-z]([a-z0-9-]{0,28}[a-z0-9])?$")
URL = re.compile(r"^https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?/[A-Za-z0-9._~/%+=&?:@,-]{1,480}$")
WIN_USER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,18}[A-Za-z0-9_-]$|^[A-Za-z0-9]$")
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
# id: the image's BOOT (Linux) or VERSION (Windows), the least memory it installs with, suggested memory and disk (GB)
OS = {
    "ubuntu": {"family": "linux", "title": "Ubuntu Desktop", "boot": "ubuntu", "min_ram": 2, "ram": 4, "disk": 32},
    "ubuntus": {"family": "linux", "title": "Ubuntu Server", "boot": "ubuntus", "min_ram": 2, "ram": 2, "disk": 20},
    "debian": {"family": "linux", "title": "Debian", "boot": "debian", "min_ram": 1, "ram": 2, "disk": 20},
    "fedora": {"family": "linux", "title": "Fedora Workstation", "boot": "fedora", "min_ram": 2, "ram": 4, "disk": 32},
    "centos": {"family": "linux", "title": "CentOS Stream", "boot": "centos", "min_ram": 2, "ram": 4, "disk": 32},
    "rocky": {"family": "linux", "title": "Rocky Linux", "boot": "rocky", "min_ram": 2, "ram": 4, "disk": 32},
    "alma": {"family": "linux", "title": "AlmaLinux", "boot": "alma", "min_ram": 2, "ram": 4, "disk": 32},
    "opensuse": {"family": "linux", "title": "openSUSE Leap", "boot": "opensuse", "min_ram": 2, "ram": 4, "disk": 32},
    "arch": {"family": "linux", "title": "Arch Linux", "boot": "arch", "min_ram": 1, "ram": 2, "disk": 20,
             "note": "Ready-made: sign in as arch, password arch."},
    "alpine": {"family": "linux", "title": "Alpine Linux", "boot": "alpine", "min_ram": 1, "ram": 1, "disk": 8},
    "kali": {"family": "linux", "title": "Kali Linux", "boot": "kali", "min_ram": 2, "ram": 4, "disk": 32},
    "mint": {"family": "linux", "title": "Linux Mint", "boot": "mint", "min_ram": 2, "ram": 4, "disk": 32},
    "manjaro": {"family": "linux", "title": "Manjaro", "boot": "manjaro", "min_ram": 2, "ram": 4, "disk": 32},
    "nixos": {"family": "linux", "title": "NixOS", "boot": "nixos", "min_ram": 2, "ram": 4, "disk": 32},
    "custom": {"family": "linux", "title": "Another Linux (an ISO or disk image URL)", "boot": None, "min_ram": 1, "ram": 2, "disk": 32},
    "win11": {"family": "windows", "title": "Windows 11 Pro", "version": "11", "min_ram": 4, "ram": 8, "disk": 64},
    "win11ltsc": {"family": "windows", "title": "Windows 11 LTSC", "version": "11l", "min_ram": 4, "ram": 8, "disk": 64},
    "win10": {"family": "windows", "title": "Windows 10 Pro", "version": "10", "min_ram": 2, "ram": 4, "disk": 64},
    "win2025": {"family": "windows", "title": "Windows Server 2025", "version": "2025", "min_ram": 4, "ram": 8, "disk": 64},
    "win2022": {"family": "windows", "title": "Windows Server 2022", "version": "2022", "min_ram": 2, "ram": 4, "disk": 64},
    "win2019": {"family": "windows", "title": "Windows Server 2019", "version": "2019", "min_ram": 2, "ram": 4, "disk": 64},
}
LANGUAGES = {"en": "English (United States)", "gb": "English (United Kingdom)", "zh": "Chinese (Simplified)", "tw": "Chinese (Traditional)",
             "ja": "Japanese", "ko": "Korean", "de": "German", "fr": "French", "es": "Spanish", "br": "Portuguese (Brazil)",
             "it": "Italian", "nl": "Dutch", "pl": "Polish", "ru": "Russian", "tr": "Turkish", "ar": "Arabic"}
LIMITS = {"cores": 16, "ram": 64, "disk": 500}
CAP_DROP = ["NET_RAW", "MKNOD", "AUDIT_WRITE", "SETFCAP"]  # as AXoR: QEMU, passt and nginx need none of them


class MachineError(Exception):
    """A request that cannot be carried out; `status` is the HTTP status the API answers with."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


# --- Docker -----------------------------------------------------------------------------------


def _docker(*args: str, timeout: float = 30, merge: bool = False) -> subprocess.CompletedProcess:
    """`docker <args>` (`merge`: stderr into stdout, in order); tests replace this with a fake Docker."""
    try:
        return subprocess.run(["docker", *args], stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT if merge else subprocess.PIPE, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess(["docker", *args], 127, "", str(e))


_ready_at = 0.0


def _ready() -> None:
    global _ready_at
    if time.monotonic() - _ready_at < 10:
        return
    if not shutil.which("docker"):
        raise MachineError(503, "Docker isn't installed, so machines aren't available: install it on the System page.")
    r = _docker("version", "--format", "{{.Server.Version}}", timeout=15)
    if r.returncode != 0:
        raise MachineError(503, "Docker isn't running, so machines aren't available: start it on the System page.")
    _ready_at = time.monotonic()


def _kvm() -> dict[str, bool]:
    return {"present": os.path.exists("/dev/kvm"), "usable": os.access("/dev/kvm", os.R_OK | os.W_OK)}


def _host() -> dict[str, Any]:
    mem = 0
    try:
        mem = int(re.search(r"^MemTotal:\s+(\d+) kB", Path("/proc/meminfo").read_text(), re.M).group(1)) * 1024  # type: ignore[union-attr]
    except (OSError, AttributeError):
        pass
    return {"cpus": os.cpu_count() or 1, "memory": mem}


def _containers() -> dict[str, dict[str, Any]]:
    """Every machine container, by machine name (docker inspect)."""
    ids = _docker("ps", "-aq", "--filter", f"label={LABEL}").stdout.split()
    if not ids:
        return {}
    try:
        items = json.loads(_docker("inspect", *ids).stdout or "[]")
    except json.JSONDecodeError:
        return {}
    return {c["Config"]["Labels"][LABEL]: c for c in items if LABEL in ((c.get("Config") or {}).get("Labels") or {})}


def _ip(c: dict[str, Any]) -> str:
    net = c.get("NetworkSettings") or {}
    return net.get("IPAddress") or next((n.get("IPAddress") for n in (net.get("Networks") or {}).values() if n.get("IPAddress")), "")


def _vnc_up(ip: str) -> bool:
    """AXoR's readiness probe: QEMU's VNC port accepts connections once the VM runs."""
    if not ip:
        return False
    try:
        with socket.create_connection((ip, VNC), timeout=0.4):
            return True
    except OSError:
        return False


_stats: dict[str, Any] = {"at": 0.0, "busy": False, "data": {}}


def _size(text: str) -> int:
    m = re.match(r"([\d.]+)\s*([KMGT]?i?B)", text.strip())
    if not m:
        return 0
    unit = {"B": 1, "KB": 1e3, "MB": 1e6, "GB": 1e9, "TB": 1e12, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "TiB": 1024**4}
    return int(float(m.group(1)) * unit.get(m.group(2), 1))


def _usage() -> dict[str, dict[str, float]]:
    """CPU (cores) and memory (bytes) in use per running machine. `docker stats` takes seconds,
    so it refreshes in the background at most every 10 s and callers get the last numbers."""
    def refresh() -> None:
        data: dict[str, dict[str, float]] = {}
        out = _docker("stats", "--no-stream", "--format", "{{json .}}", timeout=30).stdout
        for line in out.splitlines():
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            name = str(d.get("Name") or "")
            if name.startswith(PREFIX):
                data[name[len(PREFIX):]] = {"cpu": float(str(d.get("CPUPerc") or "0").rstrip("%") or 0) / 100,
                                            "memory": _size(str(d.get("MemUsage") or "").split("/")[0])}
        _stats.update(at=time.monotonic(), busy=False, data=data)

    if not _stats["busy"] and time.monotonic() - _stats["at"] > 10:
        _stats["busy"] = True
        threading.Thread(target=refresh, daemon=True, name="machines-stats").start()
    return _stats["data"]


# --- long actions: one at a time per machine, in the background -------------------------------

_lock = threading.Lock()
_busy: dict[str, dict[str, Any]] = {}  # name -> {"action", "since", "step"}
_threads: dict[str, threading.Thread] = {}
_notes: dict[str, deque] = {}  # name -> recent actions and failures, for the Overview
_errors: dict[str, str] = {}  # name -> why its last action failed


def _note(name: str, text: str, warn: bool = False) -> None:
    _notes.setdefault(name, deque(maxlen=30)).appendleft({"at": time.time(), "text": text[:500], "warn": warn})


def _step(name: str, text: str, *args: str, timeout: float = 120) -> subprocess.CompletedProcess:
    if name in _busy:
        _busy[name]["step"] = text
    r = _docker(*args, timeout=timeout)
    if r.returncode != 0:
        raise MachineError(502, f"{text} failed: {(r.stderr or r.stdout).strip()[-300:] or f'exit code {r.returncode}'}")
    return r


def _background(name: str, action: str, work: Any) -> None:
    with _lock:
        if name in _busy:
            raise MachineError(409, f"{name} is busy ({_busy[name]['action']}): wait for it to finish.")
        _busy[name] = {"action": action, "since": time.time(), "step": ""}
    _errors.pop(name, None)

    def run() -> None:
        try:
            work()
        except Exception as e:  # noqa: BLE001 - an action must always end and say why it failed
            _errors[name] = str(e)
            _note(name, f"{action} failed: {e}", warn=True)
        finally:
            _busy.pop(name, None)

    t = threading.Thread(target=run, daemon=True, name=f"machine-{name}")
    _threads[name] = t
    t.start()


def wait(name: str, timeout: float | None = None) -> None:
    """Block until the machine's current action ends (tests, CLI)."""
    t = _threads.get(name)
    if t:
        t.join(timeout)


# --- containers ---------------------------------------------------------------------------------


def env_path(name: str) -> Path:
    return settings().data_dir / "machines" / f"{name}.env"


def _run_args(name: str, s: dict[str, Any]) -> list[str]:
    """`docker run` for a machine: the same environment AXoR gave the pod."""
    osd = OS[s["os"]]
    win = osd["family"] == "windows"
    env = {"CPU_CORES": str(s["cores"]), "RAM_SIZE": f"{s['ram']}G", "DISK_SIZE": f"{s['disk']}G",
           "NETWORK": "user",  # passt: needs no NET_ADMIN or /dev/net/tun
           "RAM_CHECK": "N"}  # the container's memory limit is the VM's plus room for QEMU
    if win:
        env.update({"VERSION": osd["version"], "LANGUAGE": s.get("language") or "en"})
    else:
        env["BOOT"] = s.get("url") or osd["boot"]
    args = ["run", "-d", "--name", PREFIX + name, "--hostname", name, "--label", f"{LABEL}={name}",
            "--device", "/dev/kvm", "--stop-timeout", str(STOP_TIMEOUT), "--restart", "unless-stopped",
            "--cpus", str(min(s["cores"] + 0.5, float(os.cpu_count() or 1))), "--memory", f"{(s['ram'] + 1) * 1024}m",
            "--security-opt", "no-new-privileges", *[x for c in CAP_DROP for x in ("--cap-drop", c)],
            "-v", f"{PREFIX}{name}-disk:/storage"]
    if win and env_path(name).exists():  # read by Windows setup only: the password lives in Windows after that
        args += ["--env-file", str(env_path(name))]
    for k, v in sorted(env.items()):
        args += ["-e", f"{k}={v}"]
    return args + [IMAGES[osd["family"]]]


def _create(name: str, s: dict[str, Any]) -> None:
    """Pull the image if needed, make the disk, start the container."""
    image = IMAGES[OS[s["os"]]["family"]]
    if _docker("image", "inspect", image, timeout=30).returncode != 0:
        _step(name, f"Downloading {image}", "pull", image, timeout=3600)
    if _docker("volume", "inspect", f"{PREFIX}{name}-disk").returncode != 0:
        _step(name, "Making the disk", "volume", "create", "--label", f"{LABEL}={name}", f"{PREFIX}{name}-disk")
    _step(name, "Starting", *_run_args(name, s))


def _remove_container(name: str) -> None:
    if _docker("container", "inspect", PREFIX + name).returncode == 0:
        _step(name, "Shutting down", "stop", "-t", str(STOP_TIMEOUT), PREFIX + name, timeout=STOP_TIMEOUT + 60)
        _step(name, "Removing the old container", "rm", PREFIX + name)


# --- machines -----------------------------------------------------------------------------------


def _row(name: str) -> dict[str, Any]:
    if not NAME.match(name or ""):
        raise MachineError(404, "There's no such machine.")
    with session() as s:
        m = s.get(Machine, name)
        if m is None:
            raise MachineError(404, f"There's no machine called {name}.")
        return {"name": m.name, "settings": dict(m.settings or {}), "running": m.running, "change": m.change,
                "created_by": m.created_by, "created": _epoch(m.created_at)}


def _rows() -> list[dict[str, Any]]:
    with session() as s:
        return [{"name": m.name, "settings": dict(m.settings or {}), "running": m.running, "change": m.change,
                 "created_by": m.created_by, "created": _epoch(m.created_at)}
                for m in s.query(Machine).order_by(Machine.name)]


def _ts(value: Any) -> float | None:
    """Docker's times (nanoseconds, UTC; year 1 for "never") as epoch seconds."""
    m = re.match(r"(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)", str(value or ""))
    if not m or m.group(1) == "0001":
        return None
    return datetime(*map(int, m.groups()), tzinfo=UTC).timestamp()


def _epoch(dt: datetime | None) -> float | None:
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).timestamp() if dt else None


def _status(row: dict[str, Any], c: dict[str, Any] | None) -> tuple[str, str | None]:
    busy = _busy.get(row["name"])
    if busy:
        step = busy.get("step") or ""
        if busy["action"] == "delete":
            return "deleting", None
        return ("stopping" if busy["action"] == "stop" else "starting"), (step + "…" if step else None)
    err = _errors.get(row["name"])
    if c is None:
        return ("failing", err) if err else ("stopped", None)
    st = c.get("State") or {}
    if st.get("Restarting"):
        return "failing", f"It keeps stopping (exit code {st.get('ExitCode')}){' out of memory' if st.get('OOMKilled') else ''}: see the Logs."
    if st.get("Running"):
        return ("running", None) if _vnc_up(_ip(c)) else ("starting", None)
    if st.get("Status") == "created":
        return ("failing", err) if err else ("starting", None)
    if row["running"] and st.get("ExitCode"):
        return "failing", err or f"It stopped with exit code {st.get('ExitCode')}{', out of memory' if st.get('OOMKilled') else ''}: see the Logs."
    return "stopped", None


def _out(row: dict[str, Any], c: dict[str, Any] | None, usage: dict[str, dict[str, float]]) -> dict[str, Any]:
    s = row["settings"]
    status, msg = _status(row, c)
    osd = OS.get(s.get("os"), {})
    st = (c or {}).get("State") or {}
    u = usage.get(row["name"], {}) if st.get("Running") else {}
    return {"name": row["name"], "status": status, "message": msg, "os": s.get("os"), "title": osd.get("title", s.get("os")),
            "family": osd.get("family"), "cores": s.get("cores"), "ram": s.get("ram"), "disk": s.get("disk"),
            "cpu": round(u.get("cpu", 0.0), 3), "memory": int(u.get("memory", 0)), "created": row["created"],
            "started": _ts(st.get("StartedAt")) if st.get("Running") else None}


def catalog() -> dict[str, Any]:
    return {"os": {k: {x: v[x] for x in ("family", "title", "min_ram", "ram", "disk")} | ({"note": v["note"]} if "note" in v else {})
                   for k, v in OS.items()},
            "languages": LANGUAGES, "limits": LIMITS}


def list_machines() -> dict[str, Any]:
    _ready()
    cons, usage = _containers(), _usage()
    items = [_out(r, cons.get(r["name"]), usage) for r in _rows()]
    return {"items": items, "kvm": _kvm(), "host": _host(), **catalog()}


def _progress(name: str) -> str | None:
    """The machine's last log line while it sets up (downloading, installing), for the Overview."""
    r = _docker("logs", "--tail", "30", PREFIX + name)
    lines = [ANSI.sub("", ln).replace("\r", "").strip(" ❯") for ln in (r.stdout + "\n" + r.stderr).splitlines()]
    return next((ln[:300] for ln in reversed(lines) if ln and not set(ln) <= set("-=#.")), None)


def view(name: str) -> dict[str, Any]:
    _ready()
    row = _row(name)
    c = _containers().get(name)
    out = _out(row, c, _usage())
    if out["status"] == "starting" and c and (c.get("State") or {}).get("Running"):
        out["progress"] = _progress(name)
    s = row["settings"]
    st = (c or {}).get("State") or {}
    return {**out, "settings": {k: s.get(k) for k in ("os", "url", "cores", "ram", "disk", "username", "language")},
            "image": IMAGES.get(out["family"] or "linux"), "note": OS.get(s.get("os"), {}).get("note"),
            "volume": {"name": f"{PREFIX}{name}-disk"}, "change": row["change"],
            "container": {"name": PREFIX + name, "id": (c or {}).get("Id", "")[:12], "state": st.get("Status") or "none",
                          "restarts": (c or {}).get("RestartCount", 0), "exit_code": st.get("ExitCode"),
                          "started": _ts(st.get("StartedAt")), "finished": _ts(st.get("FinishedAt")),
                          "ip": _ip(c) if c else "", "error": st.get("Error") or ""} if c else None,
            "busy": _busy.get(name, {}).get("action"), "events": list(_notes.get(name, []))}


def logs(name: str, tail: int = 300) -> dict[str, Any]:
    _ready()
    _row(name)
    r = _docker("logs", "--timestamps", "--tail", str(int(tail)), PREFIX + name, merge=True)
    text = r.stdout if r.returncode == 0 else ""
    lines = sorted(((t, ANSI.sub("", m).replace("\r", "")) for t, _, m in (ln.partition(" ") for ln in text.splitlines())),
                   key=lambda x: x[0])
    return {"text": "\n".join(f"{t[11:19]} {m}" for t, m in lines[-int(tail):])[-400_000:]}


def _validate(b: dict[str, Any], old: dict[str, Any], create: bool) -> dict[str, Any]:
    """AXoR's checks; returns the settings to keep (never the password)."""
    name = b.get("name") or ""
    if not NAME.match(name):
        raise MachineError(400, "Names are 1-30 lowercase letters, digits or dashes, starting with a letter.")
    if old and (b.get("os") != old.get("os") or b.get("disk") != old.get("disk")):
        raise MachineError(400, "A machine's system and disk are set when it's made. Make a new machine for another one.")
    osd = OS.get(b.get("os") or "")
    if not osd:
        raise MachineError(400, "Pick an operating system from the list.")
    cores, ram, disk = int(b.get("cores") or 2), int(b.get("ram") or 4), int(b.get("disk") or 32)
    if not (1 <= cores <= LIMITS["cores"] and 1 <= ram <= LIMITS["ram"] and 8 <= disk <= LIMITS["disk"]):
        raise MachineError(400, f"Cores 1-{LIMITS['cores']}, memory 1-{LIMITS['ram']} GB, disk 8-{LIMITS['disk']} GB.")
    if ram < osd["min_ram"]:
        raise MachineError(400, f"{osd['title']} needs at least {osd['min_ram']} GB of memory.")
    win = osd["family"] == "windows"
    if disk < 20 and win:
        raise MachineError(400, "Windows needs a disk of at least 20 GB (64 GB is comfortable).")
    out: dict[str, Any] = {"os": b["os"], "cores": cores, "ram": ram, "disk": disk}
    # what the system's setup reads (the image, the Windows account and language) is kept from when it was made
    if old:
        out.update({k: old[k] for k in ("url", "username", "language") if k in old})
    elif win:
        user = (b.get("username") or "").strip() or "admin"
        if not WIN_USER.match(user) or user.lower() in ("administrator", "guest", "none", "defaultaccount", "system", "wdagutilityaccount"):
            raise MachineError(400, "The Windows account is 1-20 letters, digits, dots, dashes or spaces, and not a built-in name such as Administrator.")
        pw = b.get("password")
        if pw is None or not 4 <= len(pw) <= 127 or re.search(r"[\x00-\x1f\x7f]", pw):
            raise MachineError(400, "Set a password for the Windows account: 4-127 characters, without control characters.")
        out.update({"username": user, "language": b.get("language") if b.get("language") in LANGUAGES else "en"})
    elif b["os"] == "custom":
        url = (b.get("url") or "").strip()
        if not URL.match(url):
            raise MachineError(400, "Give the image's address, e.g. https://example.org/distro.iso (an ISO, or a qcow2, img, raw, vhd, vhdx or vmdk disk).")
        out["url"] = url
    return out


def save(user: str, b: dict[str, Any], create: bool) -> dict[str, Any]:
    """Make a machine, or change its CPU and memory (which restarts it)."""
    _ready()
    name = b.get("name") or ""
    if create:
        if not NAME.match(name):
            raise MachineError(400, "Names are 1-30 lowercase letters, digits or dashes, starting with a letter.")
        with session() as s:
            if s.get(Machine, name) is not None:
                raise MachineError(409, f"There's already a machine called {name}.")
        if _docker("container", "inspect", PREFIX + name).returncode == 0:
            raise MachineError(409, f"A container called {PREFIX}{name} already exists: remove it or pick another name.")
        if name in _busy:
            raise MachineError(409, f"The disk of an earlier machine called {name} is still being deleted. Try again in a minute.")
        old: dict[str, Any] = {}
    else:
        old = _row(name)["settings"]
    new = _validate(b, old, create)
    changed = [k for k in new if new[k] != old.get(k)]
    if old and not changed:
        return view(name)  # nothing to restart for
    osd = OS[new["os"]]
    cause = f"{user} created it" if create else f"{user} changed {', '.join(changed)}"
    if create:
        if osd["family"] == "windows":
            p = env_path(name)
            p.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(f"USERNAME={new['username']}\nPASSWORD={b['password']}\n")
        with session() as s:
            s.add(Machine(name=name, settings=new, running=True, change=cause, created_by=user))
        _note(name, f"Made: {osd['title']}, {new['cores']} cores, {new['ram']} GB, {new['disk']} GB disk")
        _background(name, "create", lambda: _create(name, new))
    else:
        with session() as s:
            m = s.get(Machine, name)
            assert m is not None
            m.settings, m.change, m.updated_at = new, cause[:200], now()
            running = m.running
        _note(name, cause)

        def apply() -> None:  # a new container on the same disk: "saving restarts the machine"
            _remove_container(name)
            if running:
                _create(name, new)

        _background(name, "update", apply)
    return view(name)


def act(name: str, action: str) -> dict[str, Any]:
    _ready()
    row = _row(name)
    if action not in ("start", "stop", "restart"):
        raise MachineError(400, "Unknown action.")
    c = _containers().get(name)
    running = bool(c and (c.get("State") or {}).get("Running"))
    if action == "restart" and not running:
        raise MachineError(409, "It's stopped: start it instead.")
    with session() as s:
        m = s.get(Machine, name)
        assert m is not None
        m.running = action != "stop"
    _note(name, {"start": "Started", "stop": "Stopped", "restart": "Restarted"}[action])
    if action == "start":
        _background(name, "start", lambda: _step(name, "Starting", "start", PREFIX + name) if c else _create(name, row["settings"]))
    elif action == "stop":
        _background(name, "stop", lambda: c and _step(name, "Shutting down", "stop", "-t", str(STOP_TIMEOUT), PREFIX + name,
                                                      timeout=STOP_TIMEOUT + 60))
    else:
        _background(name, "restart", lambda: _step(name, "Restarting", "restart", "-t", str(STOP_TIMEOUT), PREFIX + name,
                                                   timeout=2 * STOP_TIMEOUT + 60))
    return {"ok": True}


def delete(name: str) -> dict[str, Any]:
    """Delete the machine, its container and its disk."""
    _ready()
    _row(name)

    def work() -> None:
        if _docker("container", "inspect", PREFIX + name).returncode == 0:
            _step(name, "Removing the container", "rm", "-f", PREFIX + name, timeout=STOP_TIMEOUT + 60)
        if _docker("volume", "inspect", f"{PREFIX}{name}-disk").returncode == 0:
            _step(name, "Deleting the disk", "volume", "rm", f"{PREFIX}{name}-disk", timeout=300)
        env_path(name).unlink(missing_ok=True)
        with session() as s:
            m = s.get(Machine, name)
            if m is not None:
                s.delete(m)
        _notes.pop(name, None)
        _errors.pop(name, None)

    _background(name, "delete", work)
    return {"ok": True}


def target(name: str) -> str:
    """Where the console connects: the container's address, once QEMU (and so its VNC port) is up."""
    _ready()
    row = _row(name)
    c = _containers().get(name)
    if not c or not (c.get("State") or {}).get("Running"):
        raise MachineError(409, "The machine is stopped. Start it to use its console." if not row["running"] or not c
                           else "The machine isn't running: see its message and Logs.")
    ip = _ip(c)
    if not _vnc_up(ip):
        raise MachineError(409, "The machine is still starting: the console opens once its virtual machine runs (Logs shows how far it is).")
    return ip
