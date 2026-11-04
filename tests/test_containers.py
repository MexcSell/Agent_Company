import json
import secrets
import subprocess
import threading

import pytest
from fastapi.testclient import TestClient

from agent_company import containers, governance
from agent_company.api import create_app

DF = "\n".join(json.dumps(x) for x in [
    {"Type": "Images", "TotalCount": "3", "Active": "1", "Size": "2GB", "Reclaimable": "1GB (50%)"},
    {"Type": "Build Cache", "TotalCount": "9", "Active": "0", "Size": "5GB", "Reclaimable": "4GB"}])


class FakeHost:
    """Answers the commands containers.py runs; records them. Nothing touches the real host."""

    def __init__(self, monkeypatch, docker=True, lxd=False, root=True):
        self.calls: list[list[str]] = []
        self.instances: list[dict] = []
        self.block = threading.Event()
        self.block.set()
        tools = {"docker": docker, "systemctl": True, "apt-get": True, "snap": True, "sudo": True}
        monkeypatch.setattr(containers.shutil, "which", lambda n: f"/usr/bin/{n}" if tools.get(n) else None)
        real_exists = containers.os.path.exists
        monkeypatch.setattr(containers.os.path, "exists",
                            lambda p: {containers.LXD: lxd, "/dev/kvm": True}.get(p, real_exists(p)))
        monkeypatch.setattr(containers.os, "geteuid", lambda: 0 if root else 1000)
        monkeypatch.setattr(containers, "_run", self.run)
        monkeypatch.setattr(containers, "_df_cache", (0.0, []))

    def run(self, cmd, timeout=30):
        self.calls.append(cmd)
        out, rc = "", 0
        if cmd[:2] == ["sudo", "-n"] and cmd[2:] == ["true"]:
            rc = 1  # no password-less sudo
        elif cmd[:2] == ["docker", "info"]:
            out = json.dumps({"ServerVersion": "29.1", "Containers": 2, "ContainersRunning": 1, "Images": 3})
        elif cmd[:3] == ["docker", "system", "df"]:
            out = DF
        elif cmd[:2] == ["docker", "ps"] and "-q" not in " ".join(cmd):
            out = "\n".join(json.dumps(x) for x in [
                {"Names": "old", "Image": "busybox", "Status": "Exited (0) 3 days ago", "Labels": ""},
                {"Names": "ac-machine-dev", "Image": "qemux/qemu:7.50", "Status": "Exited (0) 2 days ago",
                 "Labels": "agent-company.machine=dev"}])
        elif cmd[:2] == ["docker", "ps"]:
            out = "c1\nc2"
        elif cmd[:2] == ["docker", "inspect"]:
            out = "sha256:" + "a" * 64
        elif cmd[:2] == ["docker", "images"]:
            out = "\n".join(json.dumps(x) for x in [
                {"ID": "sha256:" + "a" * 64, "Repository": "used", "Tag": "1", "Size": "1GB"},
                {"ID": "sha256:" + "b" * 64, "Repository": "unused", "Tag": "2", "Size": "2GB"}])
        elif cmd[:3] == [containers.LXC, "storage", "list"]:
            out = "default,dir,CREATED\n"
        elif cmd[:2] == [containers.LXC, "list"]:
            out = json.dumps(self.instances)
        elif "prune" in cmd or "launch" in cmd:
            self.block.wait(5)
        return subprocess.CompletedProcess(cmd, rc, out, "")


def test_status_polls_reuse_the_disk_scan(monkeypatch):
    host = FakeHost(monkeypatch)
    containers.status()
    assert containers.status()["docker"]["disk"][1]["Reclaimable"] == "4GB"
    assert sum(c[:3] == ["docker", "system", "df"] for c in host.calls) == 1
    containers.cleanup()  # the clean-up report always scans afresh
    assert sum(c[:3] == ["docker", "system", "df"] for c in host.calls) == 2


def test_cleanup_dry_run_lists_and_never_prunes(monkeypatch):
    host = FakeHost(monkeypatch)
    r = containers.cleanup()
    assert r["applied"] is False and [c["name"] for c in r["stopped_containers"]] == ["old"]  # a stopped machine stays
    assert r["build_cache_reclaimable"] == "4GB"
    assert not any("prune" in c for c in host.calls)
    r = containers.cleanup(all_images=True)
    assert [i["name"] for i in r["images"]] == ["unused:2"]  # the image a container uses is kept
    with pytest.raises(containers.HostError):
        containers.cleanup(older_than="1d; rm -rf /")


def test_cleanup_apply_prunes_with_age_filter(monkeypatch):
    host = FakeHost(monkeypatch)
    r = containers.cleanup(apply=True, all_images=True, volumes=True, older_than="48h")
    job = containers.wait(r["job"]["id"], 5)
    assert job["status"] == "done"
    prunes = [c for c in host.calls if "prune" in c]
    assert ["docker", "image", "prune", "-a", "-f", "--filter", "until=48h"] in prunes
    assert ["docker", "volume", "prune", "-f"] in prunes  # anonymous volumes only: no --all
    assert ["docker", "container", "prune", "-f", "--filter", "until=48h",
            "--filter", "label!=agent-company.machine"] in prunes  # the Machines page's containers are kept
    assert all("--all" not in c for c in prunes)
    assert "containers.log" in str(containers.log_path()) and "until=48h" in containers.log_path().read_text()


def test_install_docker_needs_root_and_a_missing_docker(monkeypatch):
    FakeHost(monkeypatch, docker=True)
    with pytest.raises(containers.HostError, match="already installed"):
        containers.install_docker()
    FakeHost(monkeypatch, docker=False, root=False)
    with pytest.raises(containers.HostError, match="needs root"):
        containers.install_docker()
    host = FakeHost(monkeypatch, docker=False, root=True)
    job = containers.wait(containers.install_docker()["id"], 5)
    assert job["status"] == "done"
    assert any(c[-1] == "docker.io" for c in host.calls)
    assert ["systemctl", "enable", "--now", "docker"] in host.calls


def test_vm_probe_never_runs_the_lxc_installer_shim(monkeypatch):
    host = FakeHost(monkeypatch, lxd=False)
    v = containers.vm_status()
    assert v["kvm"] and not v["lxd_installed"] and host.calls == []
    with pytest.raises(containers.HostError, match="not installed"):
        containers.launch("dev")


def test_launch_validates_and_runs_one_job_at_a_time(monkeypatch):
    host = FakeHost(monkeypatch, lxd=True)
    for bad in ("Dev", "-rf", "a;b", "x" * 70):
        with pytest.raises(containers.HostError):
            containers.launch(bad)
    with pytest.raises(containers.HostError):
        containers.launch("dev", image="--force")
    with pytest.raises(containers.HostError):
        containers.launch("dev", memory="4G; reboot")
    host.block.clear()
    first = containers.launch("dev", cpus=1, memory="2GiB")
    with pytest.raises(containers.HostError, match="still running"):
        containers.launch("dev2")
    host.block.set()
    assert containers.wait(first["id"], 5)["status"] == "done"
    assert [containers.LXC, "launch", "ubuntu:24.04", "dev", "--vm", "-c", "limits.cpu=1",
            "-c", "limits.memory=2GiB"] in host.calls
    host.instances = [{"name": "dev", "type": "virtual-machine", "status": "Running", "config": {},
                       "state": {"network": {"enp5s0": {"addresses": [
                           {"family": "inet", "scope": "global", "address": "10.1.2.3"}]}}}}]
    assert containers.vm_status()["instances"][0]["ipv4"] == ["10.1.2.3"]
    with pytest.raises(containers.HostError, match="already exists"):
        containers.launch("dev")
    containers.wait(containers.instance_action("dev", "delete")["id"], 5)
    assert [containers.LXC, "delete", "dev", "--force"] in host.calls
    with pytest.raises(containers.HostError, match="no instance"):
        containers.instance_action("gone", "stop")


def test_api_is_admin_only_audited_and_reports_refusals(monkeypatch):
    FakeHost(monkeypatch, lxd=True)
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as app:
        assert app.get("/api/containers").status_code == 401
        app.post("/api/auth/register", json={"name": "admin", "device_secret": secrets.token_urlsafe(32)})
        st = app.get("/api/containers").json()
        assert st["docker"]["running"] and st["vm"]["lxd_initialized"]
        assert app.post("/api/containers/docker/install").status_code == 409  # already installed
        assert app.post("/api/containers/cleanup", json={}).json()["applied"] is False
        r = app.post("/api/containers/vms", json={"name": "box", "vm": False, "cpus": 1, "memory": "1GiB"})
        assert r.status_code == 200
        containers.wait(r.json()["id"], 5)
        assert app.post("/api/containers/vms/box/explode").status_code == 409
    actions = [e["action"] for e in governance.list_audit()]
    assert "vm_launch" in actions and "docker_cleanup" not in actions  # dry runs change nothing
