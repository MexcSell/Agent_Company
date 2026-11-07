# Tasks (iteration 1)



## 1. Specify order-only dependencies, worktree preconditions and partial QA runs
- key: `spec` · role: architect · writes: no

Write a short technical specification (your answer is stored as the spec file) for this change. Read these first: src/agent_company/scheduler.py (run_dag, lines ~80-125: a failed or skipped dependency skips its dependents), src/agent_company/contracts.py (TaskContract, Condition, validate_plan, topo_order), src/agent_company/loop.py (PLAN_INSTRUCTIONS ~line 517; _make_worker ~line 923, where `deps` is built from depends_on outputs; the *_SKIP prefixes ~line 1676), src/agent_company/quality.py (REVIEW_ROLES) and src/agent_company/specs.py check_plan. Background: K237, goal #25. `threat_model` depended on the DNS-rebinding task, so when that task failed, threat_model and qa_review were skipped in every iteration. Specify:
(1) A new contract field `after: list[str]` (default []). Its keys must exist in the plan. The cycle check covers depends_on and after together. The task waits until those tasks are finished (done, failed or skipped) and then runs whatever their outcome.
(2) A new contract field `requires: list[Condition]` (default []). Only `file_exists` (path) and `contains` (value + path, read from that file in the worktree) are allowed; anything else is a ContractError. The conditions are checked in the goal's worktree right before the run. If one fails, the task is set to `skipped` with a new prefix `REQUIRES_SKIP = "skipped (precondition): "` naming the unmet condition, and no agent runs. Paths must be relative and stay inside the worktree (reject absolute paths and `..`).
(3) Review-role tasks (role in quality.REVIEW_ROLES) with depends_on no longer get skipped when only some dependencies failed. They run once every dependency has finished and at least one is done. They are skipped only when all of them failed or were skipped. The reviewer's package gets a line listing the failed or skipped dependency keys and an instruction to review only what was delivered.
(4) A failed task still fails the iteration exactly as today. State that a review of partial work never turns a failed iteration into a pass.
(5) Add the new fields to PLAN_INSTRUCTIONS in one or two lines.
(6) A test plan for new tests in tests/test_task_dependencies.py. It must cover at least: the K237 scenario (build done, dns_fix failed; threat_model with after=[dns_fix] and requires=browser.py + a test name runs; qa_review with depends_on=[build, dns_fix, threat_model] runs); a precondition that is not met skips with REQUIRES_SKIP; a review whose dependencies all failed is skipped; non-review tasks keep the old skip behaviour; validate_plan rejects an unknown `after` key, an after/depends_on cycle and an unsupported `requires` type.
List the exact files and functions to change. Label each claim as FACT (read from code) or INFERENCE. Do not write code.

Acceptance: {"type": "file_exists", "path": ".claude/specs/0085-make-threat-model-depend-only-on-the-exi/spec.md"}; {"type": "contains", "value": "REQUIRES_SKIP"}; {"type": "contains", "value": "REVIEW_ROLES"}; {"type": "min_length", "value": 1500}

## 2. Implement `after`, `requires` and partial review runs with regression tests
- key: `implement` · role: developer · writes: yes · after: spec

Implement the specification in .claude/specs/0085-make-threat-model-depend-only-on-the-exi/spec.md (from task `spec`). Keep the change small and in the existing style. Changes:
- contracts.py: add `after: list[str]` and `requires: list[Condition]` to TaskContract (defaulted). In validate_plan, reject unknown `after` keys and cycles over depends_on+after (call topo_order on their union). In validate, allow `requires` only of type file_exists or contains with a relative `path` without `..`.
- scheduler.py: in run_dag, a task is ready when its depends_on are done (review roles: all finished, at least one done) and its `after` tasks are finished. Skip a task only on a failed/skipped depends_on, and for review roles only when all of them failed or were skipped. Read `after` and the role from the task's contract, in the same session query. Update the module docstring.
- loop.py: add `REQUIRES_SKIP`. In the worker, check `requires` in the worktree before staffing; if one is not met, set `skipped` with REQUIRES_SKIP and the reason, log_event, and return False without running an agent. Give a review task a line naming its failed/skipped dependencies. Add the new fields to PLAN_INSTRUCTIONS.
- New tests/test_task_dependencies.py with the cases from the spec's test plan, including the K237 scenario (build done, dns_fix failed, threat_model with after+requires runs, qa_review runs). Use run_dag with a fake worker as tests/test_budgets.py does, plus validate_plan unit tests. Tests must run offline.
Do not change the pass/fail judgment of an iteration. Run the acceptance commands before you finish. If you are blocked, write a `BLOCKER:` line instead of guessing.

Acceptance: {"type": "file_exists", "path": "tests/test_task_dependencies.py"}; {"type": "command", "run": "uv run pytest -q tests/test_task_dependencies.py", "requirement_keys": ["S07-R-02", "G-R-01"], "stage": "unit"}; {"type": "command", "run": "uv run pytest -q tests/test_contracts.py tests/test_budgets.py tests/test_loop.py", "requirement_keys": ["G-R-01", "S05-R-08"], "stage": "integration"}

## 3. Review the dependency change against the spec and K237
- key: `qa_review` · role: qa · writes: no · after: implement

Review the diff of this goal's branch against .claude/specs/0085-make-threat-model-depend-only-on-the-exi/spec.md. Run `uv run pytest -q tests/test_task_dependencies.py tests/test_contracts.py tests/test_budgets.py tests/test_loop.py` and report the result. Trace the data flow through run_dag and the worker for three cases. Normal case (K237 scenario): build done, dns_fix failed; threat_model with after=[dns_fix] and requires on browser.py and a test name runs; qa_review runs. Edge case: a requires path that is missing, or absolute or with `..`; an after/depends_on cycle; a resumed iteration where some tasks are already finished. Error case: a review whose dependencies all failed is skipped; a non-review task with a failed dependency is still skipped; a failed task still fails the iteration. Check that old plans without the new fields behave exactly as before. List each problem as a FINDING line with file:line, then an OUTCOME line, and end with "VERDICT: PASS" or "VERDICT: FAIL".

Acceptance: {"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}; {"type": "contains", "value": "OUTCOME"}
