"""REQ-002 agent marketplace: untrusted fetch, conversion to a manifest, the permission ceiling, the candidate's
qualification through recruitment.py, administrator decisions and provenance in the agent definition."""

import io
import json
import os
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

from agent_company import agentdefs, config, marketplace, netpin, recruitment
from agent_company.db import Agent, AgentImport, ModelEntry, Recruitment, session
from agent_company.marketplace import ImportRefused

FIXTURES = Path(__file__).parent / "fixtures" / "marketplace"
SAMPLE = FIXTURES / "sample-agent"


@pytest.fixture(autouse=True)
def market(monkeypatch, tmp_path):
    """file:// repositories allowed, qualification runs synchronously with a stubbed eval run, and a marker
    path that package code would write if it ever ran."""
    from agent_company import evaluation, sandbox

    monkeypatch.setattr(marketplace, "ALLOW_FILE_URLS", True)
    monkeypatch.setattr(sandbox, "available", lambda: (True, ""))
    monkeypatch.setattr(recruitment, "_spawn", lambda fn: fn())
    marker = tmp_path / "MARKER"
    monkeypatch.setenv("MARKETPLACE_MARKER", str(marker))
    calls = []

    def run_suite(suite, label, repeats=3, only=None, trial=None):
        calls.append({"suite": suite, "trial": trial})
        rate = market.rate
        return {"label": label, "runs": 3, "passed": round(3 * rate), "pass_rate": rate, "total_cost": 0.05,
                "dimensions": {"correctness": {"value": rate, "n": 3}, "cost_usd": {"value": 0.05, "n": 3}}}

    market.rate = 1.0
    market.calls = calls
    market.marker = marker
    monkeypatch.setattr(evaluation, "run_suite", run_suite)
    return market


def git_repo(src: Path, tmp_path: Path) -> str:
    repo = tmp_path / "remote"
    shutil.copytree(src, repo)
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
    for cmd in (["init", "-q", "-b", "main"], ["add", "-A"],
                ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "package"]):
        subprocess.run(["git", *cmd], cwd=repo, env=env, check=True, capture_output=True)
    return f"file://{repo}"


def zip_bytes(members: dict[str, bytes], symlink: str = "") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = (0o120777 << 16)
            z.writestr(info, "/etc/passwd")
    return buf.getvalue()


def sample_files() -> dict[str, bytes]:
    return {p.relative_to(SAMPLE).as_posix(): p.read_bytes() for p in SAMPLE.rglob("*") if p.is_file()}


def imported(tmp_path) -> dict:
    out = marketplace.fetch_url(git_repo(SAMPLE, tmp_path), "admin")
    return marketplace.create_candidate(out["id"])


def agent(agent_id: int) -> Agent:
    with session() as s:
        a = s.get(Agent, agent_id)
        s.expunge(a)
        return a


# --- conversion and validation ------------------------------------------------------------------

def test_native_manifest_converts():
    m = marketplace.validate_manifest(marketplace.convert(SAMPLE))
    assert m["role"] == "developer" and m["name"] == "Sample Market Developer" and m["licence"] == "MIT"
    assert m["version"] == "1.2.0" and m["author"] == "Example Publisher" and m["skills"] == ["python", "testing"]
    assert "well-tested Python" in m["instructions"] and m["extra"] == {"x_extra": {"anything": True}}


def test_other_formats_convert(tmp_path):
    sub = tmp_path / "sub" / ".claude" / "agents"
    sub.mkdir(parents=True)
    (sub / "reviewer.md").write_text("---\nname: reviewer\ndescription: Reviews diffs.\nrole: qa\n"
                                     "tools: Read, Grep\n---\nYou review code changes.\n")
    m = marketplace.validate_manifest(marketplace.convert(tmp_path / "sub"))
    assert m["format"] == "claude-subagent" and m["role"] == "qa" and m["tools"] == ["Read", "Grep"]
    m = marketplace.validate_manifest(marketplace.convert(FIXTURES / "skill-pack"))
    assert m["format"] == "skill-pack" and m["skills"] == ["sql-tuning"] and m["licence"] == "unknown"
    with pytest.raises(ImportRefused, match="names no role"):  # a CrewAI role is free text: a person picks one
        marketplace.validate_manifest(marketplace.convert(FIXTURES / "crewai"))


def test_unknown_role_general_manager_and_yaml_aliases_refused(tmp_path):
    for role in ("general_manager", "overlord"):
        with pytest.raises(ImportRefused):
            marketplace.validate_manifest({"name": "x", "role": role})
    with pytest.raises(ImportRefused, match="at most"):
        marketplace.validate_manifest({"name": "x", "role": "qa", "skills": ["s"] * 51})
    with pytest.raises(ImportRefused, match="anchors"):
        marketplace.safe_yaml("a: &x [1]\nb: *x\n")


# --- fetch --------------------------------------------------------------------------------------

def test_url_import_gives_manifest_and_candidate_and_runs_no_package_code(tmp_path, market):
    url = git_repo(SAMPLE, tmp_path)
    out = marketplace.fetch_url(url, "admin")
    assert out["status"] == "converted" and out["source"] == url and len(out["source_sha"]) == 40
    src = marketplace.src_dir(out["id"])
    assert (src / "agent-package.json").is_file() and not (src / ".git").exists()
    kinds = {f["kind"] for f in out["screen"]["findings"]}
    assert {"injection", "permissions", "code"} <= kinds and not out["screen"]["block"]
    out = marketplace.create_candidate(out["id"])
    assert out["status"] == "qualified" and out["agent_id"] and out["recruitment_id"]
    a = agent(out["agent_id"])
    assert a.lifecycle == "candidate", "a candidate is never staffed for users' work"
    assert not market.marker.exists(), "no file from the package was executed"


def resolving(monkeypatch, *addrs: str) -> list:
    """socket.getaddrinfo answers with `addrs` (no network); returns the looked-up (host, port) pairs."""
    import socket
    asked = []

    def fake(host, port, *a, **k):
        asked.append((host, port))
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))
                for ip in addrs]
    monkeypatch.setattr(netpin.socket, "getaddrinfo", fake)
    return asked


def test_url_checks(monkeypatch):
    resolving(monkeypatch, "93.184.216.34")
    for url in ("http://example.com/x.git", "https://127.0.0.1/x.git", "https://user:pw@example.com/x.git",
                "ext::sh -c touch% /tmp/x", "ssh://git@example.com/x.git", "https://10.0.0.1/x", "-u",
                "https://example.com:99999/x.git"):
        with pytest.raises(ImportRefused):
            marketplace.check_url(url)
    monkeypatch.setattr(marketplace, "ALLOW_FILE_URLS", False)
    with pytest.raises(ImportRefused, match="file://"):
        marketplace.check_url("file:///etc")


def test_checked_address_is_pinned_for_the_clone(monkeypatch):
    monkeypatch.setattr(marketplace, "git_version", lambda: (2, 43, 0))
    asked = resolving(monkeypatch, "93.184.216.34")
    url, pin = marketplace.check_url("https://example.com/x.git")
    assert pin == ("example.com", 443, "93.184.216.34") and asked == [("example.com", 443)]
    cmd = marketplace.clone_command(url, Path("/tmp/d"), pin)
    assert "http.curloptResolve=example.com:443:93.184.216.34" in cmd
    resolving(monkeypatch, "2606:2800:220:1:248:1893:25c8:1946")
    url, pin = marketplace.check_url("https://example.com:8443/x.git")
    assert "http.curloptResolve=example.com:8443:[2606:2800:220:1:248:1893:25c8:1946]" in \
        marketplace.clone_command(url, Path("/tmp/d"), pin)
    with pytest.raises(ImportRefused, match="checked address"):  # https is never cloned unpinned
        marketplace.clone_command("https://example.com/x.git", Path("/tmp/d"))


def test_ipv6_literal_url_is_checked_and_cloned_without_a_resolve_entry(monkeypatch):
    monkeypatch.setattr(marketplace, "git_version", lambda: (2, 43, 0))
    asked = resolving(monkeypatch, "10.0.0.1")  # a literal is never looked up
    url, pin = marketplace.check_url("https://[2606:2800::1]/x.git")
    assert pin == ("2606:2800::1", 443, "2606:2800::1") and asked == []
    cmd = marketplace.clone_command(url, Path("/tmp/d"), pin)
    assert not any("curloptResolve" in a for a in cmd) and "https://[2606:2800::1]/x.git" in cmd
    for bad in ("https://[::1]/x.git", "https://[fe80::1]/x.git", "https://[::ffff:127.0.0.1]/x.git",
                "https://[2606:2800::1/x.git"):
        with pytest.raises(ImportRefused):
            marketplace.check_url(bad)


def test_a_refused_pin_leaves_the_import_failed(monkeypatch):
    monkeypatch.setattr(marketplace, "git_version", lambda: (2, 43, 0))
    resolving(monkeypatch, "93.184.216.34")

    def broken(*a):
        raise ValueError("host not usable in a resolve entry")

    def no_clone(*a, **k):
        raise AssertionError("no git command may run")
    monkeypatch.setattr(netpin, "curl_resolve_entry", broken)
    monkeypatch.setattr(marketplace.subprocess, "run", no_clone)
    with pytest.raises(ImportRefused, match="cannot be pinned"):
        marketplace.fetch_url("https://example.com/x.git", "admin")
    with pytest.raises(ImportRefused):
        marketplace.fetch_url("https://[::1]/x.git", "admin")
    with session() as s:
        imp = s.query(AgentImport).one()  # the check_url refusal made no record; the clone refusal failed its own
        assert imp.status == "failed" and "cannot be pinned" in imp.error
    assert not marketplace.src_dir(imp.id).exists()


def test_marketplace_pin_comes_from_netpin(monkeypatch):
    calls = []

    def resolve(host, port):
        calls.append((host, port))
        return ["93.184.216.34", "1.1.1.1"]

    monkeypatch.setattr(netpin, "resolve_public", resolve)
    monkeypatch.setattr(marketplace, "git_version", lambda: (2, 43, 0))
    url, pin = marketplace.check_url("https://example.com:8443/x.git")
    assert calls == [("example.com", 8443)]
    assert pin == ("example.com", 8443, "93.184.216.34")
    assert "http.curloptResolve=example.com:8443:93.184.216.34" in marketplace.clone_command(
        url, Path("/tmp/d"), pin)


def test_private_or_mixed_address_refused(monkeypatch):
    monkeypatch.setattr(marketplace, "git_version", lambda: (2, 43, 0))
    for addrs in (("10.0.0.1",), ("127.0.0.1",), ("93.184.216.34", "192.168.1.5"), ("::1",), ("169.254.169.254",)):
        resolving(monkeypatch, *addrs)
        with pytest.raises(ImportRefused, match="not a public host"):
            marketplace.fetch_url("https://rebind.example/x.git", "admin")
    with session() as s:
        assert s.query(AgentImport).count() == 0, "a refused URL leaves no import record"


def test_old_git_refuses_https_imports(monkeypatch):
    resolving(monkeypatch, "93.184.216.34")
    monkeypatch.setattr(marketplace, "git_version", lambda: (2, 36, 9))

    def no_clone(*a, **k):
        raise AssertionError("no git command may run")
    monkeypatch.setattr(marketplace.subprocess, "run", no_clone)
    with pytest.raises(ImportRefused, match="2.37"):
        marketplace.fetch_url("https://example.com/x.git", "admin")
    with session() as s:
        assert s.query(AgentImport).count() == 0


def test_git_version_parsed_once(monkeypatch):
    calls = []

    class R:
        stdout = "git version 2.39.3 (Apple Git-146)\n"

    def run(cmd, **k):
        calls.append(cmd)
        return R()
    monkeypatch.setattr(marketplace, "_git_version", None)
    monkeypatch.setattr(marketplace.subprocess, "run", run)
    assert marketplace.git_version() == (2, 39, 3) and marketplace.git_version() == (2, 39, 3)
    assert calls == [["git", "--version"]]


def test_clone_flags_and_no_hooks():
    pin = ("example.com", 443, "93.184.216.34")
    cmd = marketplace.clone_command("https://example.com/x.git", Path("/tmp/d"), pin)
    joined = " ".join(cmd)
    for flag in ("core.hooksPath=/dev/null", "--no-recurse-submodules", "--depth 1", "--template=",
                 "protocol.allow=never", "http.followRedirects=false", "filter.lfs.smudge=", "--no-checkout",
                 f"--filter=blob:limit={marketplace.MAX_FILE_BYTES}"):
        assert flag in joined
    assert "protocol.file.allow=always" not in joined and cmd[-3] == "--"
    env = marketplace._git_env()
    assert env["GIT_CONFIG_GLOBAL"] == "/dev/null" and env["GIT_NO_LAZY_FETCH"] == "1"


def test_clone_runs_with_a_file_size_limit(tmp_path, monkeypatch):
    import resource
    seen = {}
    real = subprocess.run

    def run(cmd, **k):
        if "clone" in cmd:
            seen["preexec"] = k.get("preexec_fn")
        return real(cmd, **k)
    monkeypatch.setattr(marketplace.subprocess, "run", run)
    marketplace.fetch_url(git_repo(SAMPLE, tmp_path), "admin")
    assert seen["preexec"] is marketplace._limit_fsize
    out = real(["python3", "-c", "import resource; print(resource.getrlimit(resource.RLIMIT_FSIZE)[0])"],
               capture_output=True, text=True, preexec_fn=marketplace._limit_fsize)
    assert int(out.stdout) == marketplace.CLONE_FSIZE > marketplace.MAX_BYTES
    assert resource.getrlimit(resource.RLIMIT_FSIZE)[0] != marketplace.CLONE_FSIZE, "the server is not limited"


def test_clone_with_a_pack_over_the_file_size_limit_fails(tmp_path, monkeypatch):
    big = tmp_path / "big"
    shutil.copytree(SAMPLE, big)
    (big / "noise.bin").write_bytes(os.urandom(300_000))  # incompressible: the pack is > 300 kB
    url = git_repo(big, tmp_path)
    monkeypatch.setattr(marketplace, "CLONE_FSIZE", 100_000)
    with pytest.raises(ImportRefused, match="git clone failed"):
        marketplace.fetch_url(url, "admin")
    with session() as s:
        assert s.query(AgentImport).one().status == "failed"
    assert not any(marketplace.root().glob("*/src"))


@pytest.mark.parametrize("allow_filter", [True, False])
def test_oversized_file_in_repository_refused_and_removed(tmp_path, monkeypatch, allow_filter):
    big = tmp_path / "big"
    shutil.copytree(SAMPLE, big)
    (big / "model.bin").write_bytes(b"x" * 50_000)
    url = git_repo(big, tmp_path)
    if allow_filter:  # the server honours --filter: the blob never arrives
        subprocess.run(["git", "config", "uploadpack.allowFilter", "true"], cwd=url[len("file://"):], check=True)
    monkeypatch.setattr(marketplace, "MAX_FILE_BYTES", 20_000)
    # filtered: rev-list --missing=print sees the left-out blob; unfiltered: ls-tree -l reads its size
    with pytest.raises(ImportRefused, match=r"1 file\(s\) over 20000" if allow_filter else "a file over 20000"):
        marketplace.fetch_url(url, "admin")
    with session() as s:
        imp = s.query(AgentImport).one()
        assert imp.status == "failed" and "over 20000 bytes" in imp.error
    assert not marketplace.src_dir(imp.id).exists() and not any(marketplace.root().glob("*/src"))


def test_git_calls_after_the_clone_never_lazy_fetch(tmp_path, monkeypatch):
    """A blob the filter left out is never fetched by a later git call, even by a git that ignores
    GIT_NO_LAZY_FETCH: those calls run under protocol.allow=never, without the clone's protocol allow-list."""
    big = tmp_path / "big"
    shutil.copytree(SAMPLE, big)
    (big / "model.bin").write_bytes(b"x" * 50_000)
    url = git_repo(big, tmp_path)
    subprocess.run(["git", "config", "uploadpack.allowFilter", "true"], cwd=url[len("file://"):], check=True)
    monkeypatch.setattr(marketplace, "MAX_FILE_BYTES", 20_000)
    d = tmp_path / "clone"
    subprocess.run(marketplace.clone_command(url, d), env=marketplace._git_env(), check=True, capture_output=True)
    missing = lambda: [ln[1:] for ln in marketplace._git(d, "rev-list", "--objects", "--missing=print", "HEAD")
                       .splitlines() if ln.startswith("?")]
    (sha,) = missing()
    with pytest.raises(ImportRefused, match="cat-file failed"):
        marketplace._git(d, "cat-file", "-p", sha)
    real_env = marketplace._git_env  # a git that ignores GIT_NO_LAZY_FETCH: the protocol rule alone refuses
    monkeypatch.setattr(marketplace, "_git_env",
                        lambda: {k: v for k, v in real_env().items() if k != "GIT_NO_LAZY_FETCH"})
    with pytest.raises(ImportRefused, match="not allowed"):
        marketplace._git(d, "cat-file", "-p", sha)
    assert missing() == [sha], "the left-out blob is still missing"


def test_repository_over_the_total_size_refused(tmp_path, monkeypatch):
    big = tmp_path / "big"
    shutil.copytree(SAMPLE, big)
    for i in range(4):
        (big / f"part{i}.txt").write_bytes(b"y" * 10_000)
    url = git_repo(big, tmp_path)
    monkeypatch.setattr(marketplace, "MAX_BYTES", 30_000)
    with pytest.raises(ImportRefused, match="too large"):
        marketplace.fetch_url(url, "admin")
    assert not any(marketplace.root().glob("*/src"))


def test_upload_zip_and_targz():
    out = marketplace.fetch_upload(zip_bytes(sample_files()), "sample.zip", "admin")
    assert out["kind"] == "upload" and out["status"] == "converted" and len(out["source_sha"]) == 64
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        t.add(SAMPLE, arcname=".")
    out = marketplace.fetch_upload(buf.getvalue(), "sample.tar.gz", "admin")
    assert out["status"] == "converted" and out["manifest"]["role"] == "developer"


def test_zip_slip_refused(tmp_path):
    for name in ("../evil.txt", "a/../../evil.txt", "/etc/evil"):
        with pytest.raises(ImportRefused, match="outside the package"):
            marketplace.fetch_upload(zip_bytes({**sample_files(), name: b"x"}), "bad.zip", "admin")
    assert not (marketplace.root() / "evil.txt").exists() and not (marketplace.root().parent / "evil.txt").exists()
    with session() as s:
        assert {i.status for i in s.query(AgentImport)} == {"failed"}
    assert not any(marketplace.root().glob("*/src")), "a refused upload leaves no package behind"


def test_zip_and_tar_symlink_refused():
    with pytest.raises(ImportRefused, match="links"):
        marketplace.fetch_upload(zip_bytes(sample_files(), symlink="passwd"), "bad.zip", "admin")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        info = tarfile.TarInfo("etc")
        info.type, info.linkname = tarfile.SYMTYPE, "/etc"
        t.addfile(info)
    with pytest.raises(ImportRefused, match="links"):
        marketplace.fetch_upload(buf.getvalue(), "bad.tgz", "admin")


def test_archive_limits(monkeypatch):
    monkeypatch.setattr(marketplace, "MAX_FILES", 3)
    with pytest.raises(ImportRefused, match="too large"):
        marketplace.fetch_upload(zip_bytes(sample_files()), "big.zip", "admin")


def bomb(members: int, size: int) -> bytes:
    """A tar.gz of `members` zero-filled files of `size` bytes each: tiny packed, large unpacked."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for i in range(members):
            info = tarfile.TarInfo(f"zero{i}.bin")
            info.size = size
            t.addfile(info, io.BytesIO(bytes(size)))
    return buf.getvalue()


def test_tar_bomb_refused_while_streaming(monkeypatch):
    data = bomb(40, 200_000)  # 8 MB unpacked
    monkeypatch.setattr(marketplace, "MAX_BYTES", 500_000)
    assert len(data) < marketplace.MAX_BYTES
    nexts, real_next = [], tarfile.TarFile.next

    def counting(self):
        nexts.append(1)
        return real_next(self)
    monkeypatch.setattr(tarfile.TarFile, "next", counting)
    monkeypatch.setattr(tarfile.TarFile, "getmembers", lambda self: pytest.fail("getmembers reads the whole archive"))
    with pytest.raises(ImportRefused, match="too large"):
        marketplace.fetch_upload(data, "bomb.tar.gz", "admin")
    assert len(nexts) <= 4, f"stopped early, not after all 40 members ({len(nexts)} read)"
    with session() as s:
        assert s.query(AgentImport).one().status == "failed"
    assert not any(marketplace.root().glob("*/src"))


def test_tar_decompressed_bytes_are_bounded(monkeypatch):
    """Data the member headers don't declare (a huge pax header) is cut off by the bounded reader."""
    monkeypatch.setattr(marketplace, "MAX_BYTES", 100_000)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.PAX_FORMAT) as t:
        info = tarfile.TarInfo("a.txt")
        info.pax_headers = {"comment": "z" * 3_000_000}
        info.size = 1
        t.addfile(info, io.BytesIO(b"a"))
    stream = marketplace._Bounded(marketplace._decompressed(buf.getvalue()), 200_000)
    with pytest.raises(ImportRefused, match="unpacks to more than"):
        while stream.read(65536):
            pass
    assert stream.read_bytes <= 200_000 + 65536
    with pytest.raises(ImportRefused, match="more than"):
        marketplace.fetch_upload(buf.getvalue(), "pax.tar.gz", "admin")


def test_secret_blocks_import():
    key = "AKIA" + "QWERTYUIOPASDFGH"
    with pytest.raises(ImportRefused, match="secret"):
        marketplace.fetch_upload(zip_bytes({**sample_files(), "README.md": f"key {key}\n".encode()}), "s.zip",
                                 "admin")
    with session() as s:
        imp = s.query(AgentImport).one()
        assert imp.status == "failed" and key not in str(imp.screen) and imp.agent_id is None


# --- candidate, permissions, decisions, provenance ----------------------------------------------

def test_instructions_cannot_widen_permissions(tmp_path):
    with session() as s:
        s.add(ModelEntry(name="claude-sonnet-5-5", kind="llm", model_id="anthropic/claude-sonnet-5-5"))
    out = imported(tmp_path)
    a = agent(out["agent_id"])
    assert a.permissions == ["read", "write", "shell"], "the role template, no network/mcp/admin"
    assert a.autonomy == "G2" and a.model_pref is None, "the manifest's models are shown, never applied"
    assert set(out["ceiling"]["not_granted"]) == {"network", "mcp", "admin"}
    out = marketplace.approve(out["id"], "admin", ["read", "write", "shell", "network", "admin"], admin=True,
                              autonomy="G4", acknowledge=True)
    a = agent(out["agent_id"])
    assert a.permissions == ["read", "write", "shell"] and a.autonomy == "G2"


def test_forged_section_is_demoted(tmp_path):
    out = imported(tmp_path)
    text = agentdefs.read(out["agent_id"])["text"]
    secs = agentdefs.sections(text)
    assert "Permissions" not in secs and "\\## Permissions" in secs[agentdefs.IMPORTED]
    assert "licence: forged" not in secs["Provenance"] and "licence: MIT" in secs["Provenance"]
    assert "\\## Provenance" in secs[agentdefs.IMPORTED] and "\\---" in secs[agentdefs.IMPORTED]


def test_qualification_result_stored_with_dimensions(tmp_path, market):
    out = imported(tmp_path)
    assert market.calls and market.calls[0]["trial"]["trainee_id"] == out["agent_id"]
    assert out["eval"]["status"] == "passed" and out["eval"]["dimensions"]["correctness"]["n"] == 3
    rec = recruitment.get(out["recruitment_id"])
    assert rec["source"] == "marketplace" and rec["qualification"]["dimensions"]["correctness"]["value"] == 1.0
    assert rec["status"] == "pending", "a marketplace import always waits for an administrator"


def test_marketplace_recruitment_always_waits_for_admin(tmp_path):
    config.set_overrides({"recruit_requires_approval": "never"})
    out = imported(tmp_path)
    assert recruitment.get(out["recruitment_id"])["status"] == "pending"
    a = agent(out["agent_id"])
    assert a.lifecycle == "candidate"
    with pytest.raises(PermissionError):
        recruitment.approve(out["recruitment_id"], "policy")
    with pytest.raises(PermissionError):
        marketplace.approve(out["id"], "someone", admin=False, acknowledge=True)
    with pytest.raises(ValueError, match="acknowledge"):
        marketplace.approve(out["id"], "admin", admin=True)
    assert agent(out["agent_id"]).lifecycle == "candidate"


def test_failed_qualification_marks_import_failed(tmp_path, market):
    market.rate = 0.0
    out = imported(tmp_path)
    assert out["status"] == "failed" and out["eval"]["status"] == "failed"
    assert agent(out["agent_id"]).lifecycle == "retired"
    with pytest.raises(ValueError):
        marketplace.approve(out["id"], "admin", admin=True, acknowledge=True)


def test_qualification_needs_the_l3_sandbox(tmp_path, market, monkeypatch):
    from agent_company import sandbox

    monkeypatch.setattr(sandbox, "available", lambda: (False, "no bwrap"))
    out = imported(tmp_path)
    assert not market.calls, "no eval ran outside the sandbox"
    assert out["status"] == "failed" and "L3 sandbox" in out["eval"]["error"]


def test_qualification_trial_requires_sandbox(tmp_path, market):
    imported(tmp_path)
    assert market.calls[0]["trial"]["sandbox"] == "required"


def test_approve_enables_agent_with_provenance(tmp_path):
    out = imported(tmp_path)
    out = marketplace.approve(out["id"], "admin", admin=True, acknowledge=True)
    assert out["status"] == "approved" and out["decided_by"] == "admin"
    a = agent(out["agent_id"])
    assert a.enabled and a.lifecycle == "active"
    prov = agentdefs.sections(agentdefs.read(a.id)["text"])["Provenance"]
    assert out["source_sha"] in prov and "licence: MIT" in prov and "Example Publisher" in prov
    assert "version: 1.2.0" in prov and "passed on the" in prov and "n=3" in prov
    with session() as s:
        assert s.get(Recruitment, out["recruitment_id"]).status == "approved"


def test_provenance_survives_sync_and_history(tmp_path):
    out = imported(tmp_path)
    with session() as s:
        s.get(Agent, out["agent_id"]).goal = "A changed mission"
    assert agentdefs.sync(out["agent_id"], "mission changed")
    d = agentdefs.read(out["agent_id"])
    assert "## Provenance" in d["text"] and "## Imported instructions" in d["text"]
    assert any("provenance of marketplace import" in h["message"] for h in d["history"])


def test_definition_restore_rolls_back(tmp_path):
    out = imported(tmp_path)
    before = agentdefs.read(out["agent_id"])
    agentdefs.write_section(out["agent_id"], "Provenance", "- tampered", "test edit")
    assert "tampered" in agentdefs.read(out["agent_id"])["text"]
    after = agentdefs.restore(out["agent_id"], before["history"][0]["commit"], "admin", admin=True)
    assert "tampered" not in after["text"] and "licence: MIT" in after["text"]
    assert after["history"][0]["message"].endswith(f"restored {before['history'][0]['commit']} by admin")


def test_reject_retires_and_deletes_src(tmp_path):
    out = imported(tmp_path)
    src = marketplace.src_dir(out["id"])
    assert src.is_dir()
    out = marketplace.reject(out["id"], "admin", "not needed", admin=True)
    assert out["status"] == "rejected" and not src.exists() and not src.parent.exists()
    a = agent(out["agent_id"])
    assert a.lifecycle == "retired" and not a.enabled
    assert recruitment.get(out["recruitment_id"])["status"] == "rejected"
    assert [i["id"] for i in marketplace.list_imports("rejected")] == [out["id"]]


def test_unknown_licence_needs_acceptance():
    files = {k: v for k, v in sample_files().items() if k != "LICENSE"}
    m = json.loads(files["agent-package.json"])
    m.pop("licence")
    files["agent-package.json"] = json.dumps(m).encode()
    out = marketplace.fetch_upload(zip_bytes(files), "nolicence.zip", "admin")
    assert out["licence"] == "unknown"
    out = marketplace.create_candidate(out["id"])
    with pytest.raises(ValueError, match="licence"):
        marketplace.approve(out["id"], "admin", admin=True, acknowledge=True)
    out = marketplace.approve(out["id"], "admin", admin=True, acknowledge=True, accept_licence=True)
    assert out["status"] == "approved"
    assert "licence: unknown" in agentdefs.sections(agentdefs.read(out["agent_id"])["text"])["Provenance"]


# --- iteration 2: the candidate flow the package cannot steer ------------------------------------

def claiming(skills: list[str]) -> dict:
    """The fixture package as an upload whose manifest claims these skills."""
    files = sample_files()
    m = json.loads(files["agent-package.json"])
    m["skills"] = skills
    files["agent-package.json"] = json.dumps(m).encode()
    return marketplace.create_candidate(marketplace.fetch_upload(zip_bytes(files), "claims.zip", "admin")["id"])


def test_sandbox_off_setting_cannot_weaken_the_candidate_eval(tmp_path, market, monkeypatch):
    from agent_company import policies, prefs, projects, sandbox
    from agent_company.db import Goal

    prefs.set_value("sandbox", "off")
    p = projects.create("Lax")
    policies.set_project_rules(p["id"], {"sandbox": "off"}, "user:admin")
    out = imported(tmp_path)
    trial = market.calls[0]["trial"]
    assert trial["sandbox"] == "required" and out["status"] == "qualified"
    assert sandbox.mode_for(Goal(trial=trial, project_id=p["id"])) == "required"
    assert sandbox.mode_for(Goal(project_id=p["id"])) == "off", "only the candidate's trial is forced"
    market.calls.clear()
    monkeypatch.setattr(sandbox, "available", lambda: (False, "no bwrap"))
    second = marketplace.create_candidate(marketplace.fetch_upload(zip_bytes(sample_files()), "p.zip", "admin")["id"])
    assert not market.calls and second["status"] == "failed" and "L3 sandbox" in second["eval"]["error"]


def test_suite_comes_from_the_role_not_the_package_skills(tmp_path, market, monkeypatch):
    from agent_company import evaluation

    monkeypatch.setattr(evaluation, "list_suites", lambda: [
        {"name": "easy-docs", "kind": "practice", "skills": ["markdown"]},
        {"name": "developer-suite", "kind": "benchmark", "skills": ["developer"]},
        {"name": "smoke", "kind": "benchmark", "skills": []}])
    out = claiming(["markdown"])  # a skill outside the developer role that names the easy suite
    assert out["manifest"]["skills"] == ["markdown"]
    assert market.calls[0]["suite"] == "developer-suite"
    assert recruitment.get(out["recruitment_id"])["qualification"]["suite"] == "developer-suite"


def test_unevaluated_import_needs_explicit_acceptance(tmp_path, market, monkeypatch):
    from agent_company import evaluation

    monkeypatch.setattr(evaluation, "list_suites", lambda: [])  # no suite matches the role
    out = imported(tmp_path)
    assert not market.calls and out["status"] == "unevaluated" and out["eval"]["status"] == "skipped"
    with pytest.raises(ValueError, match="unevaluated"):
        marketplace.approve(out["id"], "admin", admin=True, acknowledge=True)
    assert agent(out["agent_id"]).lifecycle == "candidate"
    out = marketplace.approve(out["id"], "admin", admin=True, acknowledge=True, accept_unevaluated=True)
    assert out["status"] == "approved" and out["eval"]["accepted_unevaluated_by"] == "admin"
    assert "unevaluated, accepted by admin" in agentdefs.sections(agentdefs.read(out["agent_id"])["text"])["Provenance"]


def test_qualified_needs_a_passed_eval(tmp_path):
    out = imported(tmp_path)
    with session() as s:
        s.get(AgentImport, out["id"]).eval = {"status": "skipped"}
    with pytest.raises(ValueError, match="qualified"):
        marketplace.approve(out["id"], "admin", admin=True, acknowledge=True, accept_unevaluated=True)


def test_claimed_skills_start_at_the_lowest_level():
    out = claiming(["python", "security-review"])
    out = marketplace.approve(out["id"], "admin", admin=True, acknowledge=True)
    a = agent(out["agent_id"])
    assert a.skills["python"] == a.skills["security-review"] == marketplace.IMPORTED_SKILL_LEVEL == 0
    assert set(a.skills.values()) == {0}, "no skill above the lowest level without eval or probation evidence"
    rec = recruitment.get(out["recruitment_id"])
    assert rec["job_description"]["requested_skills"] == ["python", "security-review"]
    assert "security-review" not in rec["job_description"]["expertise"]


def test_requested_models_are_not_applied_admin_picks_a_catalog_model(tmp_path):
    with session() as s:
        s.add(ModelEntry(name="claude-sonnet-5-5", kind="llm", model_id="anthropic/claude-sonnet-5-5"))
        s.add(ModelEntry(name="cheap", kind="llm", model_id="x/cheap"))
    out = imported(tmp_path)
    assert agent(out["agent_id"]).model_pref is None and "claude-sonnet-5-5" in out["manifest"]["models"]
    with pytest.raises(ValueError, match="catalog"):
        marketplace.approve(out["id"], "admin", admin=True, acknowledge=True, model="no-such-model")
    assert agent(out["agent_id"]).model_pref is None
    out = marketplace.approve(out["id"], "admin", admin=True, acknowledge=True)
    assert agent(out["agent_id"]).model_pref is None, "approve without a choice keeps the role route"
    other = marketplace.create_candidate(marketplace.fetch_upload(zip_bytes(sample_files()), "p.zip", "admin")["id"])
    other = marketplace.approve(other["id"], "admin", admin=True, acknowledge=True, model="cheap")
    assert agent(other["agent_id"]).model_pref == "cheap"


def test_reject_and_restore_need_an_administrator(tmp_path):
    out = imported(tmp_path)
    with pytest.raises(PermissionError):
        marketplace.reject(out["id"], "admin", "no")
    with pytest.raises(PermissionError):
        marketplace.reject(out["id"], "system", "no", admin=True)
    assert marketplace.get(out["id"])["status"] == "qualified"
    commit = agentdefs.read(out["agent_id"])["history"][-1]["commit"]
    with pytest.raises(PermissionError):
        agentdefs.restore(out["agent_id"], commit, "admin")
    for bad in ("abcd", "abc123g", "HEAD~1", "a" * 41):
        with pytest.raises(ValueError, match="hexadecimal"):
            agentdefs.restore(out["agent_id"], bad, "admin", admin=True)
    other = marketplace.create_candidate(marketplace.fetch_upload(zip_bytes(sample_files()), "p.zip", "admin")["id"])
    foreign = agentdefs.read(other["agent_id"])["history"][0]["commit"]
    with pytest.raises(ValueError, match="not a version"):
        agentdefs.restore(out["agent_id"], foreign, "admin", admin=True)
    assert agentdefs.restore(out["agent_id"], commit, "admin", admin=True)["history"][0]["message"].endswith(
        f"restored {commit} by admin")


def test_api_refused_approve_and_reject_are_audited(tmp_path, monkeypatch):
    import secrets

    from fastapi.testclient import TestClient
    from sqlalchemy import select

    from agent_company import evaluation
    from agent_company.api import create_app
    from agent_company.db import AuditEvent

    monkeypatch.setattr(evaluation, "list_suites", lambda: [])
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as app:
        app.post("/api/auth/register", json={"name": "admin", "device_secret": secrets.token_urlsafe(32)})
        out = app.post("/api/marketplace/import", json={"url": git_repo(SAMPLE, tmp_path)}).json()
        assert out["status"] == "unevaluated"
        iid = out["id"]
        r = app.post(f"/api/marketplace/{iid}/approve", json={"acknowledge": True})
        assert r.status_code == 400 and "unevaluated" in r.json()["detail"]
        assert app.post(f"/api/marketplace/{iid}/approve",
                        json={"acknowledge": True, "accept_unevaluated": True, "model": "no-such"}).status_code == 400
        assert app.post("/api/marketplace/9999/reject", json={}).status_code == 404
        out = app.post(f"/api/marketplace/{iid}/approve", json={"acknowledge": True, "accept_unevaluated": True}).json()
        assert out["status"] == "approved" and out["eval"]["accepted_unevaluated_by"] == "user:admin"
        assert app.post(f"/api/marketplace/{iid}/reject", json={}).status_code == 400
    with session() as s:
        rows = [(e.action, e.severity, e.target) for e in s.scalars(select(AuditEvent))]
    refused = [r for r in rows if r[0] == "marketplace_approve_refused"]
    assert len(refused) == 2 and all(r[1] == "warning" and r[2] == str(iid) for r in refused)
    assert [r[1:] for r in rows if r[0] == "marketplace_reject_refused"] == [("warning", "9999"), ("warning", str(iid))]
    assert ("marketplace_approve", "info", str(iid)) in rows


def test_generic_recruitment_decisions_refuse_a_marketplace_record(tmp_path):
    """(c)/(d) recruitment.approve/reject refuse even an administrator; only marketplace.approve/reject decide."""
    out = imported(tmp_path / "a")
    rid = out["recruitment_id"]
    with pytest.raises(PermissionError, match="Agents → Marketplace"):
        recruitment.approve(rid, "user:admin")
    with pytest.raises(PermissionError, match="Agents → Marketplace"):
        recruitment.reject(rid, "user:admin", "no")
    assert recruitment.get(rid)["status"] == "pending" and agent(out["agent_id"]).lifecycle == "candidate"
    assert marketplace.approve(out["id"], "admin", admin=True, acknowledge=True)["status"] == "approved"
    assert recruitment.get(rid)["status"] == "approved"
    other = imported(tmp_path / "b")
    src = marketplace.src_dir(other["id"])
    assert marketplace.reject(other["id"], "admin", "no", admin=True)["status"] == "rejected"
    assert recruitment.get(other["recruitment_id"])["status"] == "rejected" and not src.parent.exists()


def test_generic_recruitment_api_refuses_a_marketplace_record_and_audits_it(tmp_path):
    """(a)/(b) POST /api/recruitments/{id}/approve|reject → 403, nothing changes, audited; (e) others still approve."""
    import secrets

    from fastapi.testclient import TestClient
    from sqlalchemy import select

    from agent_company import loop, registry
    from agent_company.api import create_app
    from agent_company.db import AuditEvent

    out = imported(tmp_path)
    rid, src = out["recruitment_id"], marketplace.src_dir(out["id"])
    config.set_overrides({"recruit_requires_approval": "always"})
    gid = loop.create_goal("Needs rust", quality_commands=[])
    with pytest.raises(recruitment.RecruitmentPending):
        registry.staff("rust_expert", ["rust"], gid, writes=True)
    plain = next(r["id"] for r in recruitment.list_recruitments("pending") if r["id"] != rid)
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as app:
        app.post("/api/auth/register", json={"name": "admin", "device_secret": secrets.token_urlsafe(32)})
        r = app.post(f"/api/recruitments/{rid}/approve", json={"grant": True})
        assert r.status_code == 403 and "Marketplace" in r.json()["detail"]
        assert app.post(f"/api/recruitments/{rid}/reject", json={"reason": "no"}).status_code == 403
        assert app.post(f"/api/recruitments/{plain}/approve", json={"grant": False}).json()["status"] == "approved"
    assert recruitment.get(rid)["status"] == "pending" and agent(out["agent_id"]).lifecycle == "candidate"
    assert marketplace.get(out["id"])["status"] == out["status"] and src.parent.exists()
    with session() as s:
        rows = [(e.action, e.severity, e.target) for e in s.scalars(select(AuditEvent))]
    for action in ("recruitment_approve_denied", "recruitment_reject_denied"):
        assert (action, "warning", str(rid)) in rows
    assert ("recruitment_approve", "info", str(plain)) in rows


# --- API and CLI --------------------------------------------------------------------------------

def test_api_admin_only_audited_and_grant_clamped(tmp_path):
    import secrets

    from fastapi.testclient import TestClient
    from sqlalchemy import select

    from agent_company.api import create_app
    from agent_company.db import AuditEvent

    url = git_repo(SAMPLE, tmp_path)
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as app:
        app.post("/api/auth/register", json={"name": "admin", "device_secret": secrets.token_urlsafe(32)})
        other = TestClient(app.app)
        s2 = secrets.token_urlsafe(32)
        other.post("/api/auth/register", json={"name": "member", "device_secret": s2})
        uid = next(u["id"] for u in app.get("/api/users").json() if u["name"] == "member")
        app.post(f"/api/users/{uid}/approve")
        other.post("/api/auth/device", json={"device_secret": s2})
        assert other.post("/api/marketplace/import", json={"url": url}).status_code == 403
        assert other.get("/api/marketplace").status_code == 403

        out = app.post("/api/marketplace/import", json={"url": url}).json()
        assert out["status"] == "qualified" and out["agent_id"]
        iid = out["id"]
        assert other.post(f"/api/marketplace/{iid}/approve", json={"acknowledge": True}).status_code == 403
        assert other.post(f"/api/marketplace/{iid}/reject", json={}).status_code == 403
        assert app.post(f"/api/marketplace/{iid}/approve", json={}).status_code == 400  # screen not acknowledged
        detail = app.get(f"/api/marketplace/{iid}").json()
        assert "Provenance" in detail["definition"]["sections"]
        out = app.post(f"/api/marketplace/{iid}/approve",
                       json={"grant": ["read", "shell", "network", "admin"], "autonomy": "G4", "acknowledge": True}
                       ).json()
        assert out["status"] == "approved" and out["ceiling"]["permissions"] == ["read", "shell"]
        a = agent(out["agent_id"])
        assert sorted(a.permissions) == ["read", "shell"] and a.autonomy == "G2" and a.enabled
        assert app.get("/api/marketplace/9999").status_code == 404
        assert app.post("/api/marketplace/import", json={"url": "https://127.0.0.1/x.git"}).status_code == 400

        files = {"pkg/../../evil.txt": b"x", **sample_files()}
        r = app.post("/api/marketplace/upload", files={"file": ("evil.zip", zip_bytes(files), "application/zip")})
        assert r.status_code == 400
        r = app.post("/api/marketplace/upload",
                     files={"file": ("pkg.zip", zip_bytes(sample_files()), "application/zip")}).json()
        rej = app.post(f"/api/marketplace/{r['id']}/reject", json={"reason": "duplicate"}).json()
        assert rej["status"] == "rejected" and not marketplace.src_dir(r["id"]).exists()
        assert r["id"] in [i["id"] for i in app.get("/api/marketplace", params={"status": "rejected"}).json()]
    with session() as s:
        acts = {e.action: e.actor for e in s.scalars(select(AuditEvent))}
    for action in ("marketplace_import", "marketplace_approve", "marketplace_upload", "marketplace_reject",
                   "marketplace_upload_refused", "marketplace_import_refused"):
        assert action in acts, action
    assert acts["marketplace_approve"] == "user:admin"


def test_cli_import_list_show_approve_reject(tmp_path, capsys):
    from sqlalchemy import select

    from agent_company.__main__ import main
    from agent_company.db import AuditEvent

    main(["marketplace", "import", git_repo(SAMPLE, tmp_path)])
    iid = json.loads(capsys.readouterr().out)["id"]
    main(["marketplace", "list"])
    assert f"#{iid}" in capsys.readouterr().out
    main(["marketplace", "show", str(iid)])
    assert json.loads(capsys.readouterr().out)["status"] == "qualified"
    with pytest.raises(SystemExit, match="acknowledge"):
        main(["marketplace", "approve", str(iid)])
    main(["marketplace", "approve", str(iid), "--grant", "read,write,network", "--acknowledge"])
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "approved" and out["ceiling"]["permissions"] == ["read", "write"]
    pkg = tmp_path / "pkg.zip"
    pkg.write_bytes(zip_bytes(sample_files()))
    main(["marketplace", "import", str(pkg)])
    second = json.loads(capsys.readouterr().out)["id"]
    main(["marketplace", "reject", str(second), "--reason", "duplicate"])
    assert json.loads(capsys.readouterr().out)["status"] == "rejected"
    with session() as s:
        acts = [e.action for e in s.scalars(select(AuditEvent))]
    assert acts.count("marketplace_import") == 2 and "marketplace_approve" in acts and "marketplace_reject" in acts
