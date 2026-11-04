import json
import secrets
import socket
import stat
import subprocess
import threading

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from agent_company import machines
from agent_company.api import create_app
from agent_company.db import Machine, session

PW = "s3cret-Windows-pw"


class FakeDocker:
    """A Docker that keeps containers, volumes and images in memory; nothing touches the real host."""

    def __init__(self, monkeypatch):
        self.calls: list[list[str]] = []
        self.containers: dict[str, dict] = {}
        self.volumes: set[str] = set()
        self.images = {"qemux/qemu:7.50"}
        self.vnc = True
        monkeypatch.setattr(machines, "_docker", self.run)
        monkeypatch.setattr(machines.shutil, "which", lambda n: "/usr/bin/docker")
        monkeypatch.setattr(machines, "_ready_at", 0.0)
        monkeypatch.setattr(machines, "_vnc_up", lambda ip: self.vnc and bool(ip))
        monkeypatch.setattr(machines, "_kvm", lambda: {"present": True, "usable": True})
        monkeypatch.setattr(machines, "_stats", {"at": 0.0, "busy": True, "data": {}})  # no `docker stats`
        for d in (machines._busy, machines._notes, machines._errors, machines._threads):
            d.clear()

    def run(self, *args, timeout=30, merge=False):
        a = list(args)
        self.calls.append(a)
        ok = lambda out="": subprocess.CompletedProcess(a, 0, out, "")  # noqa: E731
        no = lambda msg="Error: No such object": subprocess.CompletedProcess(a, 1, "", msg)  # noqa: E731
        c = self.containers.get(a[-1])
        if a[0] == "version":
            return ok("29.1")
        if a[:2] == ["ps", "-aq"]:
            return ok("\n".join(x["Id"] for x in self.containers.values()))
        if a[0] == "inspect":
            return ok(json.dumps([x for x in self.containers.values() if x["Id"] in a[1:]]))
        if a[:2] == ["container", "inspect"]:
            return ok() if c else no()
        if a[:2] == ["image", "inspect"]:
            return ok() if a[2] in self.images else no()
        if a[0] == "pull":
            self.images.add(a[1])
            return ok()
        if a[:2] == ["volume", "inspect"]:
            return ok() if a[2] in self.volumes else no()
        if a[:2] == ["volume", "create"]:
            self.volumes.add(a[-1])
            return ok()
        if a[:2] == ["volume", "rm"]:
            if any(f"{a[2]}:/storage" in x["Args"] for x in self.containers.values()):
                return no("volume is in use")
            self.volumes.discard(a[2])
            return ok()
        if a[0] == "run":
            name = a[a.index("--name") + 1]
            if name in self.containers:
                return no("Conflict. The container name is already in use")
            self.containers[name] = {
                "Id": "c" + secrets.token_hex(6), "Args": a, "RestartCount": 0,
                "Config": {"Labels": {a[a.index("--label") + 1].split("=")[0]: a[a.index("--label") + 1].split("=")[1]}},
                "State": {"Status": "running", "Running": True, "Restarting": False, "ExitCode": 0,
                          "StartedAt": "2026-11-04T10:00:00.123456789Z", "FinishedAt": "0001-01-01T00:00:00Z"},
                "NetworkSettings": {"IPAddress": "127.0.0.1"}}
            return ok(name)
        if a[0] in ("start", "restart") and c:
            c["State"].update(Status="running", Running=True)
            return ok()
        if a[0] == "stop" and c:
            c["State"].update(Status="exited", Running=False, ExitCode=0)
            return ok()
        if a[0] == "rm" and c:
            if c["State"]["Running"] and "-f" not in a:
                return no("stop the container before removing")
            del self.containers[a[-1]]
            return ok()
        if a[0] == "logs":
            stamp = "2026-11-04T10:00:00.000000001Z " if "--timestamps" in a else ""
            return ok(f"{stamp}Booting\n{stamp}\x1b[32mDownloading Ubuntu Desktop...\x1b[0m\n")
        return no(f"unexpected: {a}")


@pytest.fixture
def admin():
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as c:
        c.post("/api/auth/register", json={"name": "admin", "device_secret": secrets.token_urlsafe(32)})
        yield c


def made(client, body, wait=True):
    r = client.post("/api/machines", json=body)
    assert r.status_code == 200, r.text
    if wait:
        machines.wait(body["name"], 5)
    return r.json()


def test_make_a_linux_machine_with_kvm_and_its_own_disk(admin, monkeypatch):
    dk = FakeDocker(monkeypatch)
    d = admin.get("/api/machines").json()
    assert d["items"] == [] and d["kvm"]["present"] and "win11" in d["os"] and d["os"]["arch"]["note"]
    out = made(admin, {"name": "dev-box", "os": "ubuntu", "cores": 2, "ram": 4, "disk": 32}, wait=False)
    assert out["name"] == "dev-box" and out["status"] in ("starting", "running")
    machines.wait("dev-box", 5)
    v = admin.get("/api/machines/dev-box").json()
    assert v["status"] == "running" and v["title"] == "Ubuntu Desktop" and v["family"] == "linux"
    assert v["settings"] == {"os": "ubuntu", "url": None, "cores": 2, "ram": 4, "disk": 32, "username": None, "language": None}
    assert v["container"]["name"] == "ac-machine-dev-box" and v["started"] and v["change"] == "admin created it"
    run = dk.containers["ac-machine-dev-box"]["Args"]
    assert ["--device", "/dev/kvm"] == run[run.index("--device"):run.index("--device") + 2]
    assert "ac-machine-dev-box-disk:/storage" in run and run[-1] == "qemux/qemu:7.50"
    for env in ("BOOT=ubuntu", "NETWORK=user", "RAM_CHECK=N", "CPU_CORES=2", "RAM_SIZE=4G", "DISK_SIZE=32G"):
        assert env in run
    assert "--privileged" not in run and "no-new-privileges" in run and "NET_RAW" in run and "-p" not in run
    assert run[run.index("--memory") + 1] == "5120m" and run[run.index("--restart") + 1] == "unless-stopped"
    assert "ac-machine-dev-box-disk" in dk.volumes
    items = admin.get("/api/machines").json()["items"]
    assert [(x["name"], x["status"], x["cores"]) for x in items] == [("dev-box", "running", 2)]
    assert admin.get("/api/machines/dev-box/logs").json()["text"] == "10:00:00 Booting\n10:00:00 Downloading Ubuntu Desktop..."
    # Taken names, bad names and impossible settings are refused before anything runs.
    bad = [({"name": "dev-box", "os": "ubuntu"}, 409), ({"name": "Dev_Box", "os": "ubuntu"}, 400),
           ({"name": "x", "os": "beos"}, 400), ({"name": "x", "os": "ubuntu", "ram": 1}, 400),
           ({"name": "x", "os": "custom"}, 400), ({"name": "x", "os": "custom", "url": "file:///etc/passwd"}, 400),
           ({"name": "x", "os": "win10", "ram": 4, "disk": 64}, 400),
           ({"name": "x", "os": "win10", "ram": 4, "disk": 16, "password": PW}, 400),
           ({"name": "x", "os": "win10", "ram": 4, "disk": 64, "password": PW, "username": "Administrator"}, 400),
           ({"name": "x", "os": "ubuntu", "cores": 99}, 422)]
    for body, code in bad:
        assert admin.post("/api/machines", json=body).status_code == code, body
    assert set(dk.containers) == {"ac-machine-dev-box"}


def test_windows_password_stays_out_of_the_db_api_and_audit(admin, monkeypatch):
    dk = FakeDocker(monkeypatch)
    made(admin, {"name": "win", "os": "win11", "cores": 4, "ram": 8, "disk": 64, "username": "luca", "password": PW,
                 "language": "de"})
    assert "dockurr/windows:6.06" in dk.images  # pulled because it was missing
    run = dk.containers["ac-machine-win"]["Args"]
    env = machines.env_path("win")
    assert run[run.index("--env-file") + 1] == str(env) and "VERSION=11" in run and "LANGUAGE=de" in run
    assert not any(PW in x for x in run) and env.read_text() == f"USERNAME=luca\nPASSWORD={PW}\n"
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    v = admin.get("/api/machines/win")
    assert PW not in v.text and v.json()["settings"]["username"] == "luca"
    with session() as s:
        assert PW not in json.dumps(s.get(Machine, "win").settings)
    assert PW not in admin.get("/api/audit").text
    # The account and language are kept from when it was made; the system and disk can't change.
    assert admin.put("/api/machines/win", json={"name": "win", "os": "win10", "ram": 8, "disk": 64}).status_code == 400
    assert admin.put("/api/machines/win", json={"name": "win", "os": "win11", "ram": 8, "disk": 80}).status_code == 400
    assert admin.put("/api/machines/win", json={"name": "other", "os": "win11", "ram": 8, "disk": 64}).status_code == 400


def test_edit_start_stop_restart_and_a_lost_container(admin, monkeypatch):
    dk = FakeDocker(monkeypatch)
    made(admin, {"name": "box", "os": "debian", "cores": 2, "ram": 2, "disk": 20})
    first = dk.containers["ac-machine-box"]["Id"]
    # Saving the same settings restarts nothing; new CPU or memory recreate the container on the same disk.
    n = len(dk.calls)
    admin.put("/api/machines/box", json={"name": "box", "os": "debian", "cores": 2, "ram": 2, "disk": 20})
    assert not any(c[0] in ("stop", "rm", "run") for c in dk.calls[n:])
    admin.put("/api/machines/box", json={"name": "box", "os": "debian", "cores": 4, "ram": 3, "disk": 20})
    machines.wait("box", 5)
    run = dk.containers["ac-machine-box"]["Args"]
    assert dk.containers["ac-machine-box"]["Id"] != first and "CPU_CORES=4" in run and "RAM_SIZE=3G" in run
    assert [c[0] for c in dk.calls[n:] if c[0] in ("stop", "rm", "run")] == ["stop", "rm", "run"]
    assert "changed cores, ram" in admin.get("/api/machines/box").json()["change"]

    assert admin.post("/api/machines/box/action", json={"action": "stop"}).status_code == 200
    machines.wait("box", 5)
    assert admin.get("/api/machines/box").json()["status"] == "stopped"
    assert admin.post("/api/machines/box/action", json={"action": "restart"}).status_code == 409
    assert admin.get("/api/machines/box/console").status_code == 409
    assert admin.post("/api/machines/box/action", json={"action": "explode"}).status_code == 400
    admin.post("/api/machines/box/action", json={"action": "start"})
    machines.wait("box", 5)
    assert admin.get("/api/machines/box").json()["status"] == "running"
    dk.vnc = False  # QEMU not listening yet: still setting up, no console
    v = admin.get("/api/machines/box").json()
    assert v["status"] == "starting" and v["progress"] == "Downloading Ubuntu Desktop..."
    assert "still starting" in admin.get("/api/machines/box/console").json()["detail"]
    dk.vnc = True
    admin.post("/api/machines/box/action", json={"action": "restart"})
    machines.wait("box", 5)
    assert ["restart", "-t", "130", "ac-machine-box"] in dk.calls
    # A container removed behind its back (docker rm): the machine is stopped, and Start makes it again.
    del dk.containers["ac-machine-box"]
    admin.post("/api/machines/box/action", json={"action": "stop"})
    machines.wait("box", 5)
    assert admin.get("/api/machines/box").json()["status"] == "stopped"
    admin.post("/api/machines/box/action", json={"action": "start"})
    machines.wait("box", 5)
    assert admin.get("/api/machines/box").json()["status"] == "running" and "CPU_CORES=4" in dk.containers["ac-machine-box"]["Args"]
    # A crash-looping VM shows as failing.
    dk.containers["ac-machine-box"]["State"].update(Restarting=True, Running=False, ExitCode=1)
    v = admin.get("/api/machines/box").json()
    assert v["status"] == "failing" and "exit code 1" in v["message"]


def test_a_failed_action_says_why(admin, monkeypatch):
    dk = FakeDocker(monkeypatch)
    real = dk.run
    monkeypatch.setattr(machines, "_docker", lambda *a, **k: subprocess.CompletedProcess(list(a), 1, "", "pull access denied")
                        if a[0] == "pull" else real(*a, **k))
    made(admin, {"name": "w", "os": "win10", "ram": 4, "disk": 64, "password": PW})
    v = admin.get("/api/machines/w").json()
    assert v["status"] == "failing" and "Downloading dockurr/windows:6.06 failed: pull access denied" in v["message"]
    assert v["events"][0]["warn"]


def test_delete_removes_container_disk_password_and_row(admin, monkeypatch):
    dk = FakeDocker(monkeypatch)
    made(admin, {"name": "gone", "os": "win2022", "ram": 4, "disk": 64, "password": PW})
    assert machines.env_path("gone").exists()
    assert admin.delete("/api/machines/gone").status_code == 200
    machines.wait("gone", 5)
    assert dk.containers == {} and "ac-machine-gone-disk" not in dk.volumes and not machines.env_path("gone").exists()
    assert admin.get("/api/machines").json()["items"] == []
    assert admin.get("/api/machines/gone").status_code == 404 and admin.delete("/api/machines/gone").status_code == 404
    actions = [r["action"] for r in admin.get("/api/audit").json()["records"]]
    assert {"machine_create", "machine_delete"} <= set(actions)


def test_machines_are_admin_only_and_need_docker(admin, monkeypatch):
    FakeDocker(monkeypatch)
    m, s = TestClient(admin.app), secrets.token_urlsafe(32)
    m.post("/api/auth/register", json={"name": "member", "device_secret": s})
    uid = next(u["id"] for u in admin.get("/api/users").json() if u["name"] == "member")
    admin.post(f"/api/users/{uid}/approve")
    m.post("/api/auth/device", json={"device_secret": s})
    assert m.get("/api/machines").status_code == 403
    assert m.post("/api/machines", json={"name": "x", "os": "ubuntu"}).status_code == 403
    monkeypatch.setattr(machines.shutil, "which", lambda n: None)
    monkeypatch.setattr(machines, "_ready_at", 0.0)
    r = admin.get("/api/machines")
    assert r.status_code == 503 and "Docker isn't installed" in r.json()["detail"]


def test_console_relays_vnc_and_checks_origin(admin, monkeypatch):
    FakeDocker(monkeypatch)
    made(admin, {"name": "screen", "os": "alpine", "ram": 1, "disk": 8})
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    monkeypatch.setattr(machines, "VNC", srv.getsockname()[1])
    got: list[bytes] = []

    def qemu() -> None:  # QEMU's VNC server: greets, then reads what the browser sends
        conn, _ = srv.accept()
        conn.sendall(b"RFB 003.008\n")
        got.append(conn.recv(100))
        conn.close()

    threading.Thread(target=qemu, daemon=True).start()
    host = "testserver"
    with admin.websocket_connect("/api/machines/screen/console", subprotocols=["binary"],
                                 headers={"origin": f"http://{host}"}) as ws:
        assert ws.receive_bytes() == b"RFB 003.008\n"
        ws.send_bytes(b"RFB 003.008\n")
        with pytest.raises(WebSocketDisconnect):
            ws.receive_bytes()  # QEMU hung up: the relay closes
    assert got == [b"RFB 003.008\n"]
    assert "machine_console" in [r["action"] for r in admin.get("/api/audit").json()["records"]]
    srv.close()
    # Another site can't open the console with the administrator's cookie.
    with admin.websocket_connect("/api/machines/screen/console", headers={"origin": "http://evil.example"}) as ws:
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_bytes()
    assert e.value.code == 4403
    with admin.websocket_connect("/api/machines/nope/console", headers={"origin": f"http://{host}"}) as ws:
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_bytes()
    assert e.value.code == 4404
