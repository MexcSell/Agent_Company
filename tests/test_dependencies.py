"""ASEAO08 S08-R-05: dependency changes, the audit at the gate, the record at the merge."""

import json
import secrets

from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_company import dependencies, executors, loop, performance, prefs, sandbox, worktrees
from agent_company.api import create_app
from agent_company.db import Event, Goal, session

PIP_VULN = json.dumps({"dependencies": [
    {"name": "requests", "version": "2.5.0", "vulns": [{"id": "PYSEC-2018-28", "fix_versions": ["2.20.0"]}]},
    {"name": "urllib3", "version": "1.0", "vulns": [{"id": "PYSEC-OLD-1", "fix_versions": []}]},
    {"name": "six", "version": "1.16.0", "vulns": []}], "fixes": []})
NPM_VULN = json.dumps({"auditReportVersion": 2, "vulnerabilities": {
    "left-pad": {"name": "left-pad", "severity": "high", "range": "<1.3.0",
                 "via": [{"source": 1, "title": "ReDoS", "url": "https://github.com/advisories/GHSA-x"}],
                 "fixAvailable": {"name": "left-pad", "version": "1.3.0"}}}})


def test_parse_each_format():
    assert dependencies.parse("requirements.txt", "# pinned\n-r base.txt\nRequests[socks]==2.5.0 ; python_version>'3'\n"
                              "six\n") == {"Requests": "==2.5.0 ; python_version>'3'", "six": ""}
    py = dependencies.parse("pyproject.toml", '[project]\ndependencies = ["fastapi>=0.1"]\n'
                            '[project.optional-dependencies]\ndev = ["pytest"]\n'
                            '[dependency-groups]\nlint = ["ruff==0.5", {include-group = "dev"}]\n')
    assert py == {"fastapi": ">=0.1", "pytest": "", "ruff": "==0.5"}
    assert dependencies.parse("web/package.json", '{"dependencies": {"left-pad": "^1.0.0"}, '
                              '"devDependencies": {"jest": "29"}}') == {"left-pad": "^1.0.0", "jest": "29"}
    assert dependencies.parse("package-lock.json", json.dumps({"packages": {
        "": {"name": "app"}, "node_modules/left-pad": {"version": "1.0.0"},
        "node_modules/a/node_modules/b": {"version": "2.0.0"}}})) == {"left-pad": "1.0.0", "b": "2.0.0"}
    assert dependencies.parse("uv.lock", '[[package]]\nname = "six"\nversion = "1.16.0"\n') == {"six": "1.16.0"}
    assert dependencies.parse("Cargo.toml", '[dependencies]\nserde = { version = "1" }\nrand = "0.8"\n') == {
        "serde": "1", "rand": "0.8"}
    assert dependencies.parse("go.mod", "module x\n\nrequire (\n\tgithub.com/a/b v1.2.3 // indirect\n)\n"
                              "require golang.org/x/net v0.1.0\n") == {"github.com/a/b": "v1.2.3",
                                                                      "golang.org/x/net": "v0.1.0"}
    assert dependencies.parse("package.json", "{broken") is None
    assert dependencies.parse("setup.py", "setup()") is None


def repo(env, files):
    r = env / "deps"
    r.mkdir()
    worktrees.git(r, "init", "-q", "-b", "main")
    for rel, text in files.items():
        (r / rel).parent.mkdir(parents=True, exist_ok=True)
        (r / rel).write_text(text)
    worktrees.git(r, "add", ".")
    worktrees.git(r, "commit", "-q", "-m", "init")
    wt, _, base = worktrees.create(r, "deps")
    return wt, base


def test_changes_are_read_from_git_objects(env):
    wt, base = repo(env, {"requirements.txt": "requests==2.4.0\nsix==1.16.0\n",
                          "web/package.json": '{"dependencies": {"a": "1"}}', "setup.py": "setup()\n"})
    (wt / "requirements.txt").write_text("requests==2.5.0\nflask==3.0.0\n")
    (wt / "web" / "package.json").write_text('{"dependencies": {"a": "1", "left-pad": "1.0.0"}}')
    (wt / "setup.py").write_text("setup(name='x')\n")
    worktrees.commit_all(wt, "deps")
    (wt / "requirements.txt").write_text("evil==6.6.6\n")  # not committed: the tested commit is what counts
    got = {(c["manifest"], c["name"]): (c["change"], c["before"], c["after"]) for c in dependencies.changes(wt, base)}
    assert got == {("requirements.txt", "flask"): ("added", "", "==3.0.0"),
                   ("requirements.txt", "requests"): ("changed", "==2.4.0", "==2.5.0"),
                   ("requirements.txt", "six"): ("removed", "==1.16.0", ""),
                   ("web/package.json", "left-pad"): ("added", "", "1.0.0"),
                   ("setup.py", ""): ("changed", "", "")}


def test_a_symlinked_manifest_shows_nothing_of_its_target(env):
    secret = env / "outside.txt"
    secret.write_text("TOKEN=hunter2\n")
    wt, base = repo(env, {"a.txt": "x"})
    (wt / "requirements.txt").symlink_to(secret)
    worktrees.commit_all(wt, "link")
    changed = dependencies.changes(wt, base)
    assert "hunter2" not in json.dumps(changed) and changed[0]["change"] == "symlink"
    runs, notes = dependencies._plan(wt, changed)
    assert not runs and any("symlink" in n for n in notes)


def stub(monkeypatch, outputs, calls):
    monkeypatch.setattr(dependencies, "_which", lambda tool: f"/usr/bin/{tool}")

    def run(argv, cwd, mode, timeout, network, readonly=None):
        assert isinstance(argv, list), "a fixed argument list, never a shell string"
        calls.append((argv, network))
        tool = "npm" if argv[0].endswith("npm") else "uv" if argv[0].endswith("uv") else "pip-audit"
        return (0 if tool == "uv" else 1), outputs[tool], "L3"

    monkeypatch.setattr(dependencies, "_run", run)


def test_a_vulnerability_in_an_added_dependency_fails_one_in_an_untouched_one_passes(env, monkeypatch):
    calls = []
    stub(monkeypatch, {"pip-audit": PIP_VULN, "npm": NPM_VULN}, calls)
    wt, base = repo(env, {"requirements.txt": "requests==2.20.0\nurllib3==1.0\n",
                          "web/package.json": "{}", "web/package-lock.json": "{}"})
    (wt / "requirements.txt").write_text("requests==2.5.0\nurllib3==1.0\n")
    (wt / "web" / "package.json").write_text('{"dependencies": {"left-pad": "1.0.0"}}')
    worktrees.commit_all(wt, "deps")
    rows, notes = dependencies.audit(wt, dependencies.changes(wt, base))
    by = {r["check"]: r for r in rows}
    pip, npm = by["dependency audit pip-audit ."], by["dependency audit npm web"]
    assert not pip["passed"] and pip["stage"] == "security" and [v["name"] for v in pip["vulnerabilities"]] == ["requests"]
    assert "PYSEC-2018-28" in pip["detail"] and "2.20.0" in pip["detail"]
    assert "Already present before this change: urllib3" in pip["detail"] and pip["preexisting_vulnerabilities"] == 1
    assert not npm["passed"] and "GHSA-x" in npm["detail"] and "1.3.0" in npm["detail"]
    assert all(network for _, network in calls)
    pip_argv = next(argv for argv, _ in calls if argv[0].endswith("pip-audit"))
    assert ["--no-deps", "--disable-pip"] == [a for a in pip_argv if a in {"--no-deps", "--disable-pip"}]
    assert performance.severity_of(pip["check"]) == "high", "high fails the gate; critical would block approve"

    prefs.set_value("dependency_audit", "warn")
    rows, _ = dependencies.audit(wt, dependencies.changes(wt, base))
    assert all(r["passed"] and r["warning"] for r in rows)
    prefs.set_value("dependency_audit", "off")
    calls.clear()
    assert dependencies.audit(wt, dependencies.changes(wt, base))[0] == [] and calls == []


def test_an_audit_that_cannot_run_adds_a_note_not_a_row(env, monkeypatch):
    calls = []
    stub(monkeypatch, {"pip-audit": "ERROR: requirement six is not pinned", "npm": ""}, calls)
    wt, base = repo(env, {"requirements.txt": "six\n", "pyproject.toml": "[project]\nname='x'\n"})
    (wt / "requirements.txt").write_text("six\nflask\n")
    (wt / "pyproject.toml").write_text("[project]\nname='x'\ndependencies=['httpx']\n")
    worktrees.commit_all(wt, "deps")
    rows, notes = dependencies.audit(wt, dependencies.changes(wt, base))
    assert rows == [] and any("did not report" in n for n in notes)
    monkeypatch.setattr(dependencies, "_which", lambda tool: None)
    rows, notes = dependencies.audit(wt, dependencies.changes(wt, base))
    assert rows == [] and any("not installed" in n for n in notes)


def test_a_uv_lock_is_exported_offline_then_audited(env, monkeypatch):
    calls = []
    stub(monkeypatch, {"uv": "requests==2.5.0\n", "pip-audit": PIP_VULN}, calls)
    lock = '[[package]]\nname = "requests"\nversion = "{}"\n'
    wt, base = repo(env, {"uv.lock": lock.format("2.20.0"), "pyproject.toml": "[project]\nname='x'\n"})
    (wt / "uv.lock").write_text(lock.format("2.5.0"))
    worktrees.commit_all(wt, "lock")
    rows, _ = dependencies.audit(wt, dependencies.changes(wt, base))
    assert [r["passed"] for r in rows] == [False], "a transitive version change in the lock counts"
    (export, export_net), (audit_argv, audit_net) = calls
    assert export[1:3] == ["export", "--frozen"] and not export_net and audit_net
    assert "-r" in audit_argv


def test_the_gate_fails_the_security_stage_and_the_merge_records_the_changes(env, monkeypatch):
    calls = []
    stub(monkeypatch, {"pip-audit": PIP_VULN}, calls)
    real = executors.OfflineExecutor._task

    def task(self, req):
        out = real(self, req)
        if req.role == "developer":
            out += "\n=== FILE: requirements.txt ===\nrequests==2.5.0\n=== END FILE ===\n"
        return out

    monkeypatch.setattr(executors.OfflineExecutor, "_task", task)
    r = env / "app"
    r.mkdir()
    worktrees.git(r, "init", "-q", "-b", "main")
    (r / "requirements.txt").write_text("requests==2.20.0\n")
    worktrees.git(r, "add", ".")
    worktrees.git(r, "commit", "-q", "-m", "init")
    gid = loop.create_goal("Add a feature", repo_path=str(r), max_iterations=1, permits=["dependencies"])
    out = loop.run_goal(gid)
    assert out["status"] == "failed"
    row = next(c for c in out["report"]["checks"] if c["check"] == "dependency audit pip-audit .")
    assert not row["passed"]
    assert next(s for s in out["report"]["stages"] if s["stage"] == "security")["status"] == "fail"
    assert any(d["check"] == row["check"] and d["severity"] == "high" for d in performance.list_defects("open", gid))
    with session() as s:
        hist = s.get(Goal, gid).history[-1]["dependencies"]
    assert hist["changes"][0]["name"] == "requests" and "rows" not in hist

    prefs.set_value("dependency_audit", "warn")
    gid = loop.create_goal("Add it again", repo_path=str(r), max_iterations=1, permits=["dependencies"])
    assert loop.run_goal(gid)["status"] == "awaiting_approval"
    loop.approve(gid, actor="user:admin")
    with session() as s:
        ev = s.scalars(select(Event).where(Event.goal_id == gid, Event.kind == "dependencies")).one()
    assert ev.data["changes"][0]["after"] == "==2.5.0" and "requests changed" in ev.message
    with session() as s:  # the SBOM of the merged commit
        sb = s.scalars(select(Event).where(Event.goal_id == gid, Event.kind == "sbom")).one()
    doc = json.loads(dependencies.sbom_path(sb.data["commit"]).read_text())
    assert [c["purl"] for c in doc["components"]] == ["pkg:pypi/requests@2.5.0"]
    assert doc["metadata"]["component"]["version"] == sb.data["commit"]
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as c:
        c.post("/api/auth/register", json={"name": "luca", "device_secret": secrets.token_urlsafe(32)})
        r = c.get(f"/api/goals/{gid}/sbom")
        assert r.status_code == 200 and r.json()["serialNumber"] == doc["serialNumber"]
        assert "attachment" in r.headers["content-disposition"]
        assert c.get(f"/api/goals/{gid - 1}/sbom").status_code == 404, "a goal that was never merged has none"


def test_the_sbom_lists_locked_and_pinned_dependencies_from_git_objects(env):
    uv_lock = ('version = 1\n[[package]]\nname = "app"\nversion = "0.1.0"\nsource = { editable = "." }\n'
               '[[package]]\nname = "Six"\nversion = "1.16.0"\nsource = { registry = "https://pypi.org/simple" }\n')
    lock = json.dumps({"packages": {"": {"name": "web"}, "node_modules/@types/node": {"version": "20.1.0"},
                                    "node_modules/local": {"link": True, "resolved": "../local"}}})
    wt, _ = repo(env, {
        "pyproject.toml": '[project]\nname = "app"\ndependencies = ["six>=1", "fastapi>=0.1"]\n',
        "uv.lock": uv_lock,
        "tools/requirements.txt": "requests==2.31.0\nflask\n",
        "web/package.json": '{"dependencies": {"@types/node": "^20.0.0"}}',
        "web/package-lock.json": lock,
        "node_modules/x/package.json": '{"dependencies": {"evil": "1.0.0"}}',
        "setup.py": "from setuptools import setup\n"})
    src = env / "deps"
    (src / "requirements.txt").symlink_to(src / "setup.py")
    worktrees.git(src, "add", ".")
    worktrees.git(src, "commit", "-q", "-m", "link")
    (src / "tools" / "requirements.txt").write_text("requests==9.9.9\n")  # not committed: not in the SBOM
    doc = dependencies.sbom(src)
    assert doc["bomFormat"] == "CycloneDX" and doc["specVersion"] == "1.5"
    by = {c["name"]: c for c in doc["components"]}
    assert set(by) == {"Six", "fastapi", "requests", "flask", "@types/node"}, "not the project, node_modules, links"
    assert by["Six"]["purl"] == "pkg:pypi/six@1.16.0", "the lock file's exact version stands for six>=1"
    assert "version" not in by["fastapi"] and by["fastapi"]["purl"] == "pkg:pypi/fastapi"
    assert {"name": "agent-company:specifier", "value": ">=0.1"} in by["fastapi"]["properties"]
    assert by["requests"]["version"] == "2.31.0"
    assert by["@types/node"]["purl"] == "pkg:npm/%40types/node@20.1.0"
    props = doc["metadata"]["properties"]
    assert {"name": "agent-company:unparsed", "value": "setup.py"} in props
    assert dependencies.sbom(src)["serialNumber"] == doc["serialNumber"], "same commit, same serial number"
    path, _ = dependencies.store_sbom(src, "HEAD")
    assert path.parent == env / "data" / "sbom" and path.name.endswith(".cdx.json")


def test_only_the_audit_gets_network_in_the_sandbox(env):
    assert "--unshare-net" in sandbox.build(["/bin/true"], env, writable=[])
    net = sandbox.build(["/bin/true"], env, writable=[], network=True)
    assert "--unshare-net" not in net and "/etc/resolv.conf" in net
