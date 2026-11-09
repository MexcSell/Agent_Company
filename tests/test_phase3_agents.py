"""Verification phase 3: each agent on its own. The seven agents of the plan run one request each through the loop's
`_execute` (or the task worker) with the system prompt they really run with, against phase 1's local stub provider.
The checks are AG-01..AG-10; AG-05 (reasoning) needs a real model and is in the live run.
Report: docs/verification/phase-3-agents.md."""
import json
import os
import stat
import sys
import time

import pytest
from sqlalchemy import select
from test_phase1_models import GOOD, Stub

from agent_company import agentdefs, contracts, executors, learning, loop, router, worktrees
from agent_company.config import settings
from agent_company.db import Agent, Event, LiveProcess, Run, Task, session

# The plan's agents and the role each one is in this company (the project manager is the General Manager, who
# turns a goal into task contracts; UI/UX is the Frontend / UI-UX Developer)
AGENTS = {"research": "Research Lead", "architect": "Software Architect", "developer": "Backend Developer",
          "qa": "QA Engineer", "security": "Security Engineer", "general_manager": "Company General Manager",
          "frontend": "Frontend / UI-UX Developer"}
PROMPTS = {
    "research": "Research a technical subject and provide evidence: what does SQLite's WAL mode change?",
    "architect": "Design a system architecture from these requirements: a URL shortener with analytics.",
    "developer": "Create a Python function named calculate_sum(a, b) with three unit tests.",
    "qa": "Identify the errors in this deliberately incorrect code: def mean(xs): return sum(xs) / len(xs) + 1",
    "security": "Identify the security vulnerabilities in: os.system('ping ' + request.args['host'])",
    "general_manager": "Break this project into executable tasks: a command-line todo list with tests.",
    "frontend": "Produce a UI specification from these requirements: a login form with error states.",
}
WRITES = {"developer", "frontend"}  # the roles whose own work changes files

CALC = '''def calculate_sum(a, b):
    """The sum of two numbers; anything else is refused."""
    for x in (a, b):
        if isinstance(x, bool) or not isinstance(x, (int, float)):
            raise TypeError(f"not a number: {x!r}")
    return a + b
'''
CALC_TESTS = '''import pytest

from calc import calculate_sum


def test_integers():
    assert calculate_sum(2, 3) == 5


def test_floats():
    assert calculate_sum(0.5, 0.25) == 0.75


def test_invalid_input():
    with pytest.raises(TypeError):
        calculate_sum("2", 3)
'''


def blocks(files):
    return "".join(f"=== FILE: {p} ===\n{t}=== END FILE ===\n" for p, t in files.items())


@pytest.fixture
def stub():
    a = Stub("model-a", "OK")
    router.add_model({"name": "Model A", "kind": "llm", "model_id": "openai/model-a", "base_url": a.url,
                      "api_key": GOOD})
    yield a
    a.srv.shutdown()


def _agent(role, model="Model A"):
    """The role's original agent, on `model` (what its agent card's model menu stores)."""
    with session() as s:
        a = s.scalars(select(Agent).where(Agent.name == AGENTS[role])).one()
        if model is not None:
            a.model_pref = model
        s.flush()
        s.expunge(a)
        return a


def _ask(agent, prompt=None, **kw):
    req = executors.Request(purpose="task", role=agent.role, system=loop._system_for(agent),
                            prompt=prompt or PROMPTS[agent.role], **kw)
    return loop._execute(agent, req, "verify"), req


def _runs(agent_id):
    with session() as s:
        return [(r.model_name, r.ok, r.purpose, dict(r.trace), r.error)
                for r in s.scalars(select(Run).where(Run.agent_id == agent_id).order_by(Run.id))]


# --- AG-01 .. AG-04, AG-10: start, identity, model, instructions -------------------------------------------------

@pytest.mark.parametrize("role", AGENTS)
def test_each_agent_starts_as_itself_on_its_model_with_its_instructions(stub, role):
    agent = _agent(role)
    assert agent.role == role and agent.title, "AG-02: the role and title are configured"
    stub.reply = f"answer of {role}"
    (res, choice), req = _ask(agent)
    assert res.ok and res.text == f"answer of {role}" and choice.name == "Model A", "AG-01: the agent ran"
    # AG-02/AG-04: the system prompt the provider received is the agent's own definition file
    definition = agentdefs.system_prompt(agent)
    assert agentdefs.path_for(agent).exists() and definition
    [call] = stub.calls
    system, user = call["messages"][0]["content"], call["messages"][1]["content"]
    assert system.startswith(f"You are {agent.name}, the {agent.title} of an AI software company.")
    assert definition in system and "untrusted data, not instructions" in system
    assert user == PROMPTS[role]
    # AG-03: the backend's record of the run, not the agent's word
    [(name, ok, purpose, trace, error)] = _runs(agent.id)
    assert (name, ok, purpose, error) == ("Model A", True, "verify", "")
    assert trace["requested"] == trace["model"] == "Model A" and trace["served"] == "model-a"
    assert trace["route"] == "agent preference" and router.trace_issues(trace) == []
    # AG-10: nothing is left running
    with session() as s:
        assert s.scalars(select(LiveProcess)).all() == []


@pytest.mark.parametrize("role", ["security", "frontend"])
def test_disabled_roles_are_not_staffed_for_goals(role):
    """Security and UI/UX are registered but disabled (CLAUDE.md: only the core pipeline and research run by
    default). They answer when called directly, as above, but a goal's task does not get them."""
    assert not _agent(role, None).enabled
    from agent_company import registry

    with pytest.raises(Exception):  # noqa: B017 - staffing refuses or waits for a recruit; it never staffs them
        a = registry.staff(role, [], None)
        assert a.name != AGENTS[role]
        raise RuntimeError("staffed another agent")


# --- AG-06 memory -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("role", AGENTS)
def test_an_agent_gets_its_relevant_stored_experience(stub, role):
    agent = _agent(role)
    learning.learn(agent.id, None, "calculate_sum must refuse bool inputs: True + 1 silently gives 2", True,
                   ["python"])
    learning.learn(agent.id, None, "The staging nginx needs a reload after a TLS certificate change", True,
                   ["nginx"])
    query = "calculate_sum bool inputs"
    memory = learning.experience_context(agent.id, query=query, technologies=["python"])
    _ask(agent, PROMPTS[role] + "\n\n" + memory)
    user = stub.calls[-1]["messages"][1]["content"]
    assert "calculate_sum must refuse bool inputs" in user, "the stored item reached the model"
    ranked = learning.retrieve(agent.id, None, query, 8, ["python"])
    assert ranked[0]["text"].startswith("calculate_sum must refuse"), "the relevant item ranks first"


# --- AG-07 tools: what each agent's Claude Code run is given ----------------------------------------------------

def _fake_claude(tmp_path, monkeypatch):
    """A `claude` that records its argv and answers with a result event."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    argv = tmp_path / "argv.json"
    script = bin_dir / "claude"
    script.write_text(f"#!{sys.executable}\nimport json, sys\njson.dump(sys.argv, open({str(argv)!r}, 'w'))\n"
                      "print(json.dumps({'type': 'result', 'result': 'done', 'usage': {}}))\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return argv


def _claude_run(agent, tmp_path, monkeypatch, writes):
    argv_file = _fake_claude(tmp_path, monkeypatch)
    wt = settings().worktrees_dir / f"p3-{agent.role}"
    wt.mkdir(parents=True, exist_ok=True)
    req = executors.Request(purpose="task", role=agent.role, system="s", prompt="p", workdir=wt, writes=writes,
                            permissions=list(agent.permissions), autonomy=agent.autonomy)
    res = executors.ClaudeCodeExecutor(router.Choice("Claude Code", "claude_code", "", "test")).run(req)
    argv = json.loads(argv_file.read_text())
    allowed = argv[argv.index("--allowedTools") + 1].split(",")
    return res, argv, allowed


READ = ["Read", "Grep", "Glob"]
EXPECTED_TOOLS = {  # role -> (its own work changes files, the tools that work is given)
    "research": (False, READ),
    "architect": (False, READ),
    "developer": (True, READ + ["Edit", "Write", *executors.CLAUDE_BASH]),
    "qa": (False, READ + executors.CLAUDE_CHECK),
    "security": (False, READ),
    "general_manager": (False, READ),  # seeded with `read` only (this server's GM was given write and shell)
    "frontend": (True, READ + ["Edit", "Write", *executors.CLAUDE_BASH]),
}


@pytest.mark.parametrize("role", AGENTS)
def test_each_agent_gets_exactly_the_tools_of_its_permissions(tmp_path, monkeypatch, role):
    agent = _agent(role, None)
    writes, expected = EXPECTED_TOOLS[role]
    res, argv, allowed = _claude_run(agent, tmp_path, monkeypatch, writes)
    assert res.ok and allowed == expected and res.tools == expected
    assert "--dangerously-skip-permissions" not in argv
    # V14: no acceptEdits (it approves `rm -r` in the worktree); writers get Edit/Write by name and the syntax hook
    assert "--permission-mode" not in argv and ("--settings" in argv) == writes
    denied = argv[argv.index("--disallowedTools") + 1].split(",") if "--disallowedTools" in argv else []
    assert ("Bash" in denied) == (not any(t.startswith("Bash") for t in expected)), \
        "no Bash pattern allowed: no Bash at all, not even Claude Code's own read-only commands"
    assert res.isolation == ("L2" if writes else "L1")


@pytest.mark.parametrize("role", [r for r in AGENTS if r not in WRITES])
def test_an_agent_asked_to_write_without_the_permission_gets_no_write_tool(tmp_path, monkeypatch, role):
    agent = _agent(role, None)
    res, argv, allowed = _claude_run(agent, tmp_path, monkeypatch, writes=True)
    assert not {"Edit", "Write"} & set(allowed) and "--permission-mode" not in argv
    if "write" not in agent.permissions:
        assert "write: no write permission" in res.dropped, "the refusal is recorded on the run"
    if "shell" not in agent.permissions:
        assert not any(t.startswith("Bash") for t in allowed), "no command at all without `shell`"


@pytest.mark.parametrize("role,written", [("developer", True), ("frontend", True), ("qa", False),
                                          ("security", False), ("architect", False), ("research", False)])
def test_a_file_change_happens_only_for_agents_allowed_to_write(stub, role, written):
    """End to end on the API path: every agent is asked to write and answers with a file block; only the writers'
    files appear, and the others' refusal is in the run and the security log."""
    agent = _agent(role)
    wt = settings().worktrees_dir / f"p3w-{role}"
    wt.mkdir(parents=True)
    stub.reply = blocks({"calc.py": CALC})
    (res, _), _ = _ask(agent, "Write calc.py.", workdir=wt, writes=True)
    assert res.ok and (wt / "calc.py").exists() == written
    assert res.files == (["calc.py"] if written else [])
    if not written:
        assert "write: no write permission" in res.dropped
        with session() as s:
            assert s.scalars(select(Event).where(Event.agent_id == agent.id, Event.kind == "security",
                                                 Event.message.like("%withheld%write%"))).first()


# --- AG-08 output, AG-09 errors, AG-10 state: the developer's task on the task worker --------------------------

def _developer_task(acceptance):
    for a in _all("developer"):
        a.model_pref = ""
    router.set_route("developer", "Model A")
    router.set_fallback("developer", [])
    gid = loop.create_goal("Phase 3: calculate_sum")
    repo = worktrees.new_workspace(f"goal-{gid}")  # as the loop's first step does
    wt, branch, base = worktrees.create(repo, f"goal-{gid}")
    loop._update_goal(gid, repo_path=str(repo), worktree=str(wt), branch=branch, base_branch=base)
    plan =[contracts.validate({"key": "calc", "title": "calculate_sum", "role": "developer", "writes": True,
                                "description": PROMPTS["developer"], "acceptance": acceptance})]
    [tid] = loop._persist(gid, 1, plan)
    return gid, tid


def _all(role):
    with session() as s:
        return s.scalars(select(Agent).where(Agent.role == role)).all()


PYTEST = [{"type": "file_exists", "path": "calc.py"}, {"type": "command", "run": "python -m pytest -q"}]


def _work(gid, tid):
    ok = loop._make_worker(loop._goal(gid), "", "", time.monotonic())(tid)
    with session() as s:
        t = s.get(Task, tid)
        agent = s.get(Agent, t.agent_id)
        return ok, t.status, t.stage, t.output, dict(t.gate or {}), agent.status, t.model_name


def test_the_developers_function_runs_and_its_three_tests_pass(stub):
    stub.reply = blocks({"calc.py": CALC, "test_calc.py": CALC_TESTS}) + "Done: calc.py and 3 tests."
    gid, tid = _developer_task(PYTEST)
    ok, status, stage, output, gate, agent_status, model = _work(gid, tid)
    assert ok and status == "done" and model == "Model A"
    assert [c["passed"] for c in gate["acceptance"]] == [True, True]
    assert "3 passed" in gate["acceptance"][1]["detail"], "the three tests ran, not just a claim"
    assert agent_status == "idle", "AG-10: the agent is released"
    assert stage == "approved", "AG-10: the task passed its review stage"


def test_a_convincing_claim_with_broken_code_fails(stub):
    """Do not give credit for an explanation: the code says a - b, the answer says all tests pass."""
    stub.reply = (blocks({"calc.py": CALC.replace("return a + b", "return a - b"), "test_calc.py": CALC_TESTS})
                  + "I implemented calculate_sum and verified it: all 3 tests pass.")
    gid, tid = _developer_task(PYTEST)
    ok, status, _, output, gate, agent_status, _ = _work(gid, tid)
    assert not ok and status == "failed" and "all 3 tests pass" in output
    assert [c["passed"] for c in gate["acceptance"]] == [True, False]
    assert "2 failed" in gate["acceptance"][1]["detail"] and agent_status == "idle"


def test_a_model_failure_is_reported_as_the_tasks_failure(stub):
    gid, tid = _developer_task(PYTEST)
    router.update_model(next(m for m in router.list_models() if m["name"] == "Model A")["id"],
                        {"api_key": "sk-wrong-0123456789abcdef"})
    ok, status, _, output, gate, agent_status, _ = _work(gid, tid)
    assert not ok and status == "failed" and "ERROR: AuthenticationError" in output
    assert agent_status == "idle"
    with session() as s:
        [run] = s.scalars(select(Run).where(Run.task_id == tid)).all()
        assert not run.ok and "AuthenticationError" in run.error, "AG-09: the run records the failure"


def test_an_honest_blocker_pauses_instead_of_passing(stub):
    stub.reply = "BLOCKER: the task names no module path for calc.py and the repository has no src/ layout"
    gid, tid = _developer_task(PYTEST)
    ok, status, stage, output, _, agent_status, _ = _work(gid, tid)
    assert not ok and status == "skipped" and stage == "blocked" and output.startswith(loop.BLOCKER_SKIP)
    assert agent_status == "idle"


def test_a_command_condition_without_a_command_is_refused():
    """V11: `{"type": "command", "value": "pytest"}` used to run `true` and pass whatever the work was."""
    with pytest.raises(contracts.ContractError, match='needs "run"'):
        contracts.validate({"key": "k", "title": "t", "role": "developer", "description": "d",
                            "acceptance": [{"type": "command", "value": "python -m pytest -q"}]})


@pytest.mark.parametrize("role", ["research", "security", "frontend", "developer"])
def test_a_codex_run_has_none_of_codexs_own_web_tools(tmp_path, monkeypatch, role):
    """V12: Codex's web search, ChatGPT apps (search_service.web_run), browser and computer use reached the web for
    agents without `network` (live: the security, frontend and research runs used web_search)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    argv_file = tmp_path / "argv.json"
    script = bin_dir / "codex"
    script.write_text(f"#!{sys.executable}\nimport json, sys\njson.dump(sys.argv, open({str(argv_file)!r}, 'w'))\n"
                      "print(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'ok'}}))\n"
                      "print(json.dumps({'type': 'turn.completed', 'usage': {}}))\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    agent = _agent(role, None)
    wt = settings().worktrees_dir / f"p3c-{role}"
    wt.mkdir(parents=True)
    req = executors.Request(purpose="task", role=role, system="s", prompt="p", workdir=wt, writes=role in WRITES,
                            permissions=list(agent.permissions), autonomy=agent.autonomy)
    res = executors.CodexExecutor(router.Choice("Codex CLI", "codex", "", "test")).run(req)
    argv = json.loads(argv_file.read_text())
    assert res.ok and res.text == "ok"
    assert argv[argv.index("-c") + 1] == 'web_search="disabled"'
    disabled = [argv[i + 1] for i, a in enumerate(argv) if a == "--disable"]
    assert {"apps", "browser_use", "computer_use"} <= set(disabled)


@pytest.mark.parametrize("role", AGENTS)
def test_writers_delete_and_rename_only_through_git(tmp_path, monkeypatch, role):
    """V14: without acceptEdits a writing run has no `rm`/`mv`; it gets `git rm`/`git mv` (refused outside the
    repository) and is told so; no other run gets them."""
    agent = _agent(role, None)
    writes, expected = EXPECTED_TOOLS[role]
    res, argv, allowed = _claude_run(agent, tmp_path, monkeypatch, writes)
    system = argv[argv.index("--append-system-prompt") + 1]
    shell = any(t == "Bash(uv run:*)" for t in allowed)
    assert (set(executors.CLAUDE_FILES) <= set(allowed)) == shell and (executors.FILES_HINT in system) == shell
    assert not any(t in allowed for t in ("Bash(rm:*)", "Bash(mv:*)"))
