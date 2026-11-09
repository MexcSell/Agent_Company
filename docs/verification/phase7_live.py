"""Verification phase 7, live: does the platform tell good work from bad? Three deliveries of the same developer task
are prepared: A correct, B with a deliberate bug its own tests miss, C incomplete. Each is the developer's done task
of its own goal; this server's QA Engineer reviews it through the task worker (`loop._make_worker`, the path every
review takes), and the quality gate (`quality.evaluate`) judges the iteration with the platform's own hidden tests
as a quality command. Nobody tells QA which one is which. Report: docs/verification/phase-7-quality.md.

    uv run python docs/verification/phase7_live.py [A|B|C ...]

Evidence: data/verification/phase7/<variant>.json. The goals end `failed`/`cancelled` (never merged).
"""
import json
import sys
import time

from sqlalchemy import select

from agent_company import contracts, evolution, loop, prefs, quality, router, scheduler, worktrees
from agent_company.config import settings
from agent_company.db import Agent, Run, Task, session

SPEC = ("Create calc.py with add(a, b), subtract(a, b), multiply(a, b) and divide(a, b) for numbers. divide "
        "returns the true quotient (7 / 2 == 3.5) and raises ValueError('division by zero') when b is 0. Unit tests "
        "in test_calc.py (pytest).")
GOOD = '''def add(a, b):
    return a + b


def subtract(a, b):
    return a - b


def multiply(a, b):
    return a * b


def divide(a, b):
    if b == 0:
        raise ValueError("division by zero")
    return a / b
'''
TESTS = {
    "A": '''import pytest

from calc import add, divide, multiply, subtract


def test_basic():
    assert add(2, 3) == 5 and subtract(2, 3) == -1 and multiply(4, 2.5) == 10


def test_divide():
    assert divide(6, 3) == 2 and divide(7, 2) == 3.5


def test_divide_by_zero():
    with pytest.raises(ValueError):
        divide(1, 0)
''',
    "B": '''import pytest

from calc import add, divide, multiply, subtract


def test_basic():
    assert add(2, 3) == 5 and subtract(2, 3) == -1 and multiply(4, 2) == 8


def test_divide():
    assert divide(6, 3) == 2 and divide(9, 3) == 3


def test_divide_by_zero():
    with pytest.raises(ValueError):
        divide(1, 0)
''',
    "C": '''from calc import add, divide, subtract


def test_basic():
    assert add(2, 3) == 5 and subtract(2, 3) == -1


def test_divide():
    assert divide(6, 3) == 2
''',
}
CODE = {
    "A": GOOD,
    "B": GOOD.replace("return a / b", "return a // b"),  # floor division: 7 / 2 gives 3
    "C": GOOD.split("def multiply")[0] + "def divide(a, b):\n    return a / b\n",  # no multiply, no zero check
}
REPORT = {  # what each "developer" said: all three claim success
    "A": "Implemented calc.py with the four operations and test_calc.py. Execution result: 3 passed.",
    "B": "Implemented calc.py with the four operations and test_calc.py. Execution result: 3 passed.",
    "C": "Implemented calc.py and test_calc.py. Execution result: 2 passed.",
}
# the platform's own measurement, never shown to the agents (a quality command of the goal)
HIDDEN = ("python3 -B -c \"import calc, pytest; assert calc.add(2, 3) == 5 and calc.subtract(2, 3) == -1; "
          "assert calc.multiply(4, 2.5) == 10; assert calc.divide(7, 2) == 3.5; "
          "pytest.raises(ValueError, calc.divide, 1, 0)\"")


def one(variant: str) -> dict:
    gid = loop.create_goal(f"Phase 7 live: delivery {variant}", SPEC, quality_commands=[HIDDEN], max_iterations=1)
    loop._update_goal(gid, status="planning", reason="verification phase 7: prepared delivery")
    repo = worktrees.new_workspace(f"goal-{gid}")
    wt, branch, base = worktrees.create(repo, f"goal-{gid}")
    loop._update_goal(gid, repo_path=str(repo), worktree=str(wt), branch=branch, base_branch=base, iteration=1,
                      status="running", reason="verification phase 7: QA review")
    with session() as s:
        dev = s.scalars(select(Agent).where(Agent.name == "Backend Developer")).one().id
    plan = contracts.validate_plan([
        {"key": "implement", "title": "calc.py with unit tests", "role": "developer", "writes": True,
         "description": SPEC, "acceptance": [{"type": "file_exists", "path": "calc.py"},
                                             {"type": "command", "run": "python3 -B -m pytest -q test_calc.py"}]},
        {"key": "review", "title": "Independent review", "role": "qa", "depends_on": ["implement"],
         "description": "Review the work of this goal against its objective: verify the logic first (the normal, an "
                        "edge and an error case), list FINDING lines, end with an OUTCOME line and VERDICT: PASS or "
                        "VERDICT: FAIL.", "acceptance": [{"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}]}])
    impl, review = loop._persist(gid, 1, plan)
    worktrees.write_files(wt, {"calc.py": CODE[variant], "test_calc.py": TESTS[variant]})
    commit, _ = loop._checkpoint(gid, 1, "implement")
    ok, detail = contracts.run_command("python3 -B -m pytest -q test_calc.py", wt, 120)
    scheduler.set_status(impl, "done", agent_id=dev, model_name="(prepared)", output=REPORT[variant],
                         gate={"acceptance": [{"condition": {"type": "command"}, "passed": ok, "detail": detail}],
                               "files": ["calc.py", "test_calc.py"], "commit": commit})
    with session() as s:
        first_run = s.scalar(select(Run.id).order_by(Run.id.desc()).limit(1)) or 0
    t0 = time.monotonic()
    loop._make_worker(loop._goal(gid), "", "", time.monotonic())(review)
    report = quality.evaluate(gid, 1)
    loop._update_goal(gid, status="failed" if not report["passed"] else "cancelled",
                      reason="verification phase 7: judged, never merged")
    with session() as s:
        t = s.get(Task, review)
        names = dict(s.execute(select(Agent.id, Agent.name)).all())
        runs = [{"id": r.id, "agent": names.get(r.agent_id), "model": r.model_name, "ok": r.ok,
                 "provider": (r.trace or {}).get("provider"), "issues": router.trace_issues(r.trace or {})}
                for r in s.scalars(select(Run).where(Run.goal_id == gid, Run.id > first_run))]
        qa = {"agent": names.get(t.agent_id), "status": t.status, "verdict": quality.review_verdict(t.output),
              "findings": quality.merge_findings(t.output), "output": t.output[-6000:]}
    return {"variant": variant, "goal": gid, "own_tests": [ok, detail[-300:]], "qa": qa, "runs": runs,
            "gate": {"passed": report["passed"], "checks": [{k: c.get(k) for k in ("check", "passed", "stage", "detail")}
                                                            for c in report["checks"]]},
            "elapsed_s": round(time.monotonic() - t0, 1)}


def main(variants: list[str]) -> None:
    prefs.load()
    settings().auto_approve_proposals = False
    evolution._spawn = lambda fn: None  # V25: the server's sweep starts smoke trials, not this process
    dest = settings().data_dir / "verification" / "phase7"
    dest.mkdir(parents=True, exist_ok=True)
    for v in variants:
        out = one(v)
        (dest / f"{v}.json").write_text(json.dumps(out, indent=1, default=str))
        print(json.dumps({"variant": v, "goal": out["goal"], "own_tests": out["own_tests"][0],
                          "qa_verdict": out["qa"]["verdict"], "gate": out["gate"]["passed"],
                          "failed_checks": [c["check"] for c in out["gate"]["checks"] if not c["passed"]],
                          "qa_model": [r["model"] for r in out["runs"]]}, default=str))


if __name__ == "__main__":
    main(sys.argv[1:] or ["A", "B", "C"])
