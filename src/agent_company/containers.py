"""Docker and virtual machines on the host: detect, install, start and stop; clean up Docker.

Taken from /home/thomas/bit, which runs its eval tasks in Docker (Harbor) and keeps the host
tidy with `scripts/docker-cleanup.sh`; that script is `cleanup()` here. VMs are LXD instances
(`lxc launch <image> <name> --vm`, or system containers without `--vm`), backed by KVM.

Like the Workspace terminal, these are the administrator's own tools, not goal-loop agents:
access to the Docker socket or LXD is root-equivalent. They are reached only from admin API
endpoints and the CLI, every action is audited there, and executors never call them. They do
not make agent runs isolated (S14-R-07 L3 stays open); they only provide the machinery.

Long actions (installing, launching, pruning) run as background jobs, one at a time, logged to
`data/logs/containers.log`; `jobs()` reports them and `wait()` blocks until one ends.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from agent_company.config import settings

# Ubuntu ships /usr/sbin/lxc as a shim that installs the LXD snap when run: never call it to probe.
LXC = "/snap/bin/lxc"
LXD = "/snap/bin/lxd"
NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
IMAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
AGE_RE = re.compile(r"^\d{1,5}[smh]$")
MEMORY_RE = re.compile(r"^\d{1,6}(MiB|GiB)$")

_lock = threading.Lock()  # one job at a time, like bit's flock around builds
_jobs: dict[str, dict[str, Any]] = {}
_threads: dict[str, threading.Thread] = {}


class HostError(ValueError):
    """A request that cannot be carried out (missing tool, bad name, job already running)."""


def _privileged(cmd: list[str]) -> list[str]:
    return cmd if os.geteuid() == 0 else ["sudo", "-n", *cmd]


def _run(cmd: list[str], timeout: float = 30) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess(cmd, 127, "", str(e))


def _json_lines(text: str) -> list[dict[str, Any]]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def _can_elevate() -> bool:
    return os.geteuid() == 0 or (shutil.which("sudo") is not None and _run(["sudo", "-n", "true"], 10).returncode == 0)


# --- status -----------------------------------------------------------------------------------


def docker_status() -> dict[str, Any]:
    out: dict[str, Any] = {"installed": shutil.which("docker") is not None, "running": False, "version": None,
                           "enabled": None, "containers": 0, "containers_running": 0, "images": 0, "disk": []}
    if not out["installed"]:
        return out
    if shutil.which("systemctl"):
        out["enabled"] = _run(["systemctl", "is-enabled", "docker"]).stdout.strip() == "enabled"
    r = _run(["docker", "info", "--format", "{{json .}}"])
    if r.returncode != 0:
        out["error"] = (r.stderr or r.stdout).strip()[-300:]
        return out
    try:
        info = json.loads(r.stdout)
    except json.JSONDecodeError:
        return out
    out.update(running=True, version=info.get("ServerVersion"), containers=info.get("Containers", 0),
               containers_running=info.get("ContainersRunning", 0), images=info.get("Images", 0))
    out["disk"] = [{k: d.get(k) for k in ("Type", "TotalCount", "Active", "Size", "Reclaimable")}
                   for d in _json_lines(_run(["docker", "system", "df", "--format", "json"], 60).stdout)]
    return out


def _cpu_virt() -> bool:
    try:
        return bool(re.search(r"^flags\s*:.*\b(vmx|svm)\b", Path("/proc/cpuinfo").read_text(), re.M))
    except OSError:
        return False


def vm_status() -> dict[str, Any]:
    out: dict[str, Any] = {"kvm": os.path.exists("/dev/kvm"), "cpu_virtualization": _cpu_virt(),
                           "snap": shutil.which("snap") is not None, "lxd_installed": os.path.exists(LXD),
                           "lxd_initialized": False, "instances": []}
    if not out["lxd_installed"]:
        return out
    pools = _run([LXC, "storage", "list", "--format", "csv"])
    out["lxd_initialized"] = pools.returncode == 0 and bool(pools.stdout.strip())
    r = _run([LXC, "list", "--format", "json"])
    if r.returncode != 0:
        out["error"] = (r.stderr or r.stdout).strip()[-300:]
        return out
    try:
        items = json.loads(r.stdout)
    except json.JSONDecodeError:
        items = []
    for i in items:
        addrs = []
        for net in ((i.get("state") or {}).get("network") or {}).values():
            addrs += [a["address"] for a in net.get("addresses", []) if a.get("family") == "inet" and a.get("scope") == "global"]
        cfg = i.get("config") or {}
        out["instances"].append({"name": i.get("name"), "type": i.get("type"), "status": i.get("status"),
                                 "image": cfg.get("image.description", ""), "ipv4": addrs,
                                 "cpus": cfg.get("limits.cpu", ""), "memory": cfg.get("limits.memory", "")})
    return out


def status() -> dict[str, Any]:
    """What the System page shows: Docker, VM support and LXD instances, and recent jobs."""
    return {"docker": docker_status(), "vm": vm_status(), "root": _can_elevate(), "jobs": jobs()}


# --- jobs -------------------------------------------------------------------------------------


def log_path() -> Path:
    p = settings().data_dir / "logs" / "containers.log"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def jobs() -> list[dict[str, Any]]:
    return sorted((dict(j) for j in _jobs.values()), key=lambda j: j["started"], reverse=True)[:10]


def wait(job_id: str, timeout: float | None = None) -> dict[str, Any]:
    t = _threads.get(job_id)
    if t:
        t.join(timeout)
    return dict(_jobs[job_id])


def _start(kind: str, target: str, steps: list[list[str]], timeout: float = 1800) -> dict[str, Any]:
    """Run `steps` in order in the background; stop at the first that fails."""
    if not _lock.acquire(blocking=False):
        raise HostError("another Docker/VM job is still running; wait for it to finish")
    job = {"id": uuid.uuid4().hex[:12], "kind": kind, "target": target, "status": "running",
           "started": time.time(), "ended": None, "output": "", "commands": [" ".join(c) for c in steps]}
    _jobs[job["id"]] = job

    def work() -> None:
        try:
            with log_path().open("a") as log:
                log.write(f"\n== {time.strftime('%Y-%m-%d %H:%M:%S')} {kind} {target}\n")
                for cmd in steps:
                    log.write(f"$ {' '.join(cmd)}\n")
                    log.flush()
                    r = _run(cmd, timeout)
                    text = (r.stdout + r.stderr).strip()
                    log.write(text + f"\nexit={r.returncode}\n")
                    job["output"] = (job["output"] + f"$ {' '.join(cmd)}\n{text}\n")[-4000:]
                    if r.returncode != 0:
                        job["status"] = "failed"
                        return
            job["status"] = "done"
        except Exception as e:  # noqa: BLE001 - a job must always end with a status
            job["status"], job["output"] = "failed", job["output"] + str(e)
        finally:
            job["ended"] = time.time()
            _lock.release()

    t = threading.Thread(target=work, name=f"containers-{kind}", daemon=True)
    _threads[job["id"]] = t
    t.start()
    return dict(job)


def _need_root() -> None:
    if not _can_elevate():
        raise HostError("installing and starting services needs root (run the service as root, "
                        "or allow its user password-less sudo)")


# --- Docker -----------------------------------------------------------------------------------


def install_docker() -> dict[str, Any]:
    """Install Docker from the distribution (apt: docker.io) and start it at boot."""
    if shutil.which("docker"):
        raise HostError("Docker is already installed")
    if not shutil.which("apt-get"):
        raise HostError("automatic installation needs apt-get (Debian/Ubuntu); install Docker by hand")
    _need_root()
    env = ["env", "DEBIAN_FRONTEND=noninteractive"]
    return _start("docker_install", "docker", [
        _privileged([*env, "apt-get", "update", "-q"]),
        _privileged([*env, "apt-get", "install", "-y", "-q", "docker.io"]),
        _privileged(["systemctl", "enable", "--now", "docker"]),
    ])


def docker_service(action: str) -> dict[str, Any]:
    """Start, stop or restart the Docker daemon (start also enables it at boot)."""
    if action not in ("start", "stop", "restart"):
        raise HostError("action must be start, stop or restart")
    if not shutil.which("docker"):
        raise HostError("Docker is not installed")
    _need_root()
    cmd = ["systemctl", "enable", "--now", "docker"] if action == "start" else ["systemctl", action, "docker", "docker.socket"]
    return _start(f"docker_{action}", "docker", [_privileged(cmd)], timeout=120)


def cleanup(apply: bool = False, all_images: bool = False, volumes: bool = False,
            older_than: str = "24h") -> dict[str, Any]:
    """bit's docker-cleanup.sh. Dry run (a report) by default; `apply` starts the prune job.

    Running containers and the images they use are never removed; named volumes are kept.
    `all_images` also removes tagged images no container uses (they must be pulled or built again).
    """
    if not AGE_RE.match(older_than):
        raise HostError("older_than must look like 24h, 90m or 3600s")
    d = docker_status()
    if not d["running"]:
        raise HostError("Docker is not running")
    stopped = [{"name": c.get("Names"), "image": c.get("Image"), "status": c.get("Status")}
               for c in _json_lines(_run(["docker", "ps", "-a", "--filter", "status=exited", "--filter",
                                          "status=created", "--format", "json"], 60).stdout)]
    if all_images:
        ids = _run(["docker", "ps", "-aq"], 60).stdout.split()
        used = set(_run(["docker", "inspect", "--format", "{{.Image}}", *ids], 60).stdout.split()) if ids else set()
        images = [{"id": i["ID"][7:19] if i["ID"].startswith("sha256:") else i["ID"][:12],
                   "name": f"{i.get('Repository')}:{i.get('Tag')}", "size": i.get("Size")}
                  for i in _json_lines(_run(["docker", "images", "--no-trunc", "--format", "json"], 60).stdout)
                  if i.get("ID") not in used]
    else:
        images = [{"id": i.get("ID"), "name": "<none>", "size": i.get("Size")}
                  for i in _json_lines(_run(["docker", "images", "--filter", "dangling=true", "--format", "json"], 60).stdout)]
    networks = len(_run(["docker", "network", "ls", "-q", "--filter", "type=custom"]).stdout.split())
    cache = next((x["Reclaimable"] for x in d["disk"] if x["Type"] == "Build Cache"), "0B")
    report: dict[str, Any] = {"disk": d["disk"], "stopped_containers": stopped, "images": images,
                              "all_images": all_images, "custom_networks": networks, "build_cache_reclaimable": cache,
                              "older_than": older_than, "applied": False}
    if volumes:
        report["unused_anonymous_volumes"] = len(_run(["docker", "volume", "ls", "-qf", "dangling=true"]).stdout.split())
    if not apply:
        return report
    until = f"until={older_than}"
    steps = [["docker", "container", "prune", "-f", "--filter", until],
             ["docker", "image", "prune", *(["-a"] if all_images else []), "-f", "--filter", until],
             ["docker", "network", "prune", "-f", "--filter", until],
             ["docker", "builder", "prune", "-f", "--filter", until]]
    if volumes:  # without --all, volume prune only removes anonymous volumes; it takes no "until" filter
        steps.append(["docker", "volume", "prune", "-f"])
    report.update(applied=True, job=_start("docker_cleanup", "docker", steps))
    return report


# --- virtual machines (LXD) -------------------------------------------------------------------


def install_lxd() -> dict[str, Any]:
    """Install LXD (snap) if needed and initialise it with defaults (storage pool and bridge)."""
    if not os.path.exists("/dev/kvm"):
        raise HostError("/dev/kvm is missing: enable virtualization (VT-x/AMD-V) in the firmware first")
    if not os.path.exists(LXD) and not shutil.which("snap"):
        raise HostError("installing LXD needs snapd")
    _need_root()
    steps = [] if os.path.exists(LXD) else [_privileged(["snap", "install", "lxd"])]
    if not vm_status()["lxd_initialized"]:
        steps.append(_privileged([LXD, "init", "--auto"]))
    if not steps:
        raise HostError("LXD is already installed and initialised")
    return _start("lxd_install", "lxd", steps)


def _instance(name: str) -> str:
    if not NAME_RE.match(name or ""):
        raise HostError("name: lower-case letters, digits and '-', starting with a letter (max 63)")
    return name


def _need_lxd() -> None:
    v = vm_status()
    if not (v["lxd_installed"] and v["lxd_initialized"]):
        raise HostError("LXD is not installed and initialised yet")


def launch(name: str, image: str = "ubuntu:24.04", vm: bool = True, cpus: int = 2, memory: str = "4GiB",
           disk: str = "") -> dict[str, Any]:
    """Create and start a VM (or a system container with vm=False). The image downloads on first use."""
    _instance(name)
    if not IMAGE_RE.match(image or "") or image.startswith("-"):
        raise HostError("image: e.g. ubuntu:24.04 or images:debian/12")
    if not 1 <= cpus <= (os.cpu_count() or 1):
        raise HostError(f"cpus must be between 1 and {os.cpu_count()}")
    if not MEMORY_RE.match(memory):
        raise HostError("memory: e.g. 4GiB or 512MiB")
    if disk and not re.match(r"^\d{1,5}GiB$", disk):
        raise HostError("disk: e.g. 20GiB")
    _need_lxd()
    if any(i["name"] == name for i in vm_status()["instances"]):
        raise HostError(f"an instance named {name} already exists")
    cmd = [LXC, "launch", image, name, *(["--vm"] if vm else []),
           "-c", f"limits.cpu={cpus}", "-c", f"limits.memory={memory}"]
    if disk:
        cmd += ["-d", f"root,size={disk}"]
    return _start("vm_launch", name, [_privileged(cmd)])


def instance_action(name: str, action: str) -> dict[str, Any]:
    """Start, stop, restart or delete an LXD instance (delete stops it first)."""
    _instance(name)
    if action not in ("start", "stop", "restart", "delete"):
        raise HostError("action must be start, stop, restart or delete")
    _need_lxd()
    if not any(i["name"] == name for i in vm_status()["instances"]):
        raise HostError(f"no instance named {name}")
    cmd = [LXC, action, name, *(["--force"] if action in ("stop", "delete") else [])]
    return _start(f"vm_{action}", name, [_privileged(cmd)], timeout=300)
