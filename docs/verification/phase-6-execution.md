# Verification phase 6: project task execution

Date: 2026-11-09. Question: does a project that receives an assignment produce a working result, with task records
and an explicit task state machine, and is a task never completed just because code was generated? Statuses as the
plan defines them: PASS, FAIL, BLOCKED, PARTIAL, NOT TESTED.

## Live run: TEST-PROJECT-003, Simple Calculator

`docs/verification/phase6_live.py`: project #2 registered on this server with the plan's requirements (addition,
subtraction, multiplication, division, division-by-zero handling), stack `python`, room `development`, and the
developer and QA on its general topic. The plan's order went to the project's General Manager through its command
window, as the owner gives orders; goal #332 ran on this server's agents and models (runs 2121–2129). Evidence:
`data/verification/phase6/run.json`; a checkout of the project's main branch: `data/worktrees/phase6-check-20261109-004004`.
The project was archived at the end.

### 6.1 / 6.2 The project and the order

| Step | What the records show | Result |
|------|-----------------------|--------|
| Register TEST-PROJECT-003 | Project #2 with its description, stack, room and topic agents; its own repository | PASS |
| PM receives the order | The order became goal #332 in the project's general topic. The project's own GM planned it ("the project's own manager", V21) | PASS |
| Analyse, plan, divide, assign | The GM wrote a requirements analysis and a plan of 3 tasks. **spec** (architect, priority 0, 10 min): the interface, the error type and division by zero. **implement** (developer, priority 1, 20 min): `calculator.py` with unit tests. **review** (QA, priority 2, 10 min): an independent review | PASS |
| The registered requirements reached the GM | **No.** This run started before the V23 fix: the project's description reached no agent, and the GM planned from the order text alone ("the goal names no interface, operations or acceptance criteria"). It still covered all five requirements, because "simple calculator" implies them | FAIL → fixed (V23), not re-run live |

### 6.3 Task records

| Field | Expected | Records (tasks spec / implement / review) | Result |
|-------|----------|-------------------------------------------|--------|
| Task ID | Unique | 3 task rows, each with its own id | PASS |
| Project ID | Correct | Goal #332 → project #2 | PASS |
| Assigned agent | Correct | Software Architect, Backend Developer, QA Engineer; the sender of each is the GM (`planned_by`) | PASS |
| Task description | Complete | The contract's description, with acceptance conditions (4, 9 and 3) | PASS |
| Priority | Defined | 0, 1, 2 | PASS |
| Status at creation | Pending | `planned` (first stage-log entry) | PASS |
| Estimated time | Recorded | `estimated_minutes` 10, 20, 10, from the planner | PASS |
| Created time | Recorded | `created_at` 23:32:36 (V17), then started and finished times | PASS |

### 6.4 Execution

| Agent | What the platform checked | Result |
|-------|---------------------------|--------|
| Architect | Spec saved by the loop as `.claude/specs/0332-…/spec.md` (10,961 characters), naming `CalculatorError` and division by zero | PASS |
| Developer | 9/9 conditions, run by the gate in L3: both files, `unittest discover`, the library API, `calculator.py 2 * 3` → 6, `7 / 2` → 3.5, `1 / 0` → exit 1 with "Error: division by zero" and no output, `1 +` → usage error exit 2, nothing outside scope | PASS |
| QA | Ran the tests, reported FINDING lines and an OUTCOME, VERDICT: PASS | PASS |

### 6.5 State machine

Every live task went planned → assigned → in_progress → review → approved → completed (when the goal was
approved). Offline (`tests/test_phase6_execution.py`, 11 cases):

| Path | Pass condition | Evidence | Result |
|------|----------------|----------|--------|
| Pending → Assigned → In progress → Under review → Completed | The stage log of a passing task, in that order | `test_the_task_lifecycle_of_a_passing_goal` | PASS |
| Code generated, not completed | A wrong factorial with "all tests pass": review → failed, status failed | `test_generated_code_alone_does_not_complete_a_task` | PASS |
| Failed → Retry | The next iteration's task starts `reopened`, then is assigned, run, reviewed and approved | `test_a_failed_task_is_retried_in_the_next_iteration_as_reopened` | PASS |
| Blocked / Reassigned | A model at its limit: blocked, then the same task runs on resume; a blocker pauses the goal (phase 4, 1b and 2b). Reassignment is the next iteration's staffing | phase 4 tests | PASS |
| Cancelled → Cancelled | **Was not final:** goal #111's task went in_progress → cancelled → review → failed when its worker came back after the cancel (live DB, the one illegal move among 3,531 recorded). Task stages now have a transition table (`workflow.TASK_TRANSITIONS`): an illegal move is refused and logged, and the worker's result is still recorded | `test_a_task_of_a_cancelled_goal_stays_cancelled_when_its_worker_finishes` (fails without the table), `test_an_illegal_task_move_is_refused_and_logged` (5 moves) | FAIL → PASS (V24) |

The table was built from every move recorded on this server and the documented lifecycle, and checked by running
the whole test suite with illegal moves raising (`AGENT_COMPANY_STRICT_STAGES=1`). A first table missed three
forward skips the code takes (planned → in_progress, planned → approved, created → completed); the final one let
1,328 of 1,329 tests pass with no illegal move. The one left is `test_task_stages.py`, whose setup marks a
cancelled task failed (the move V24 refuses); without strict mode it passes, the stage staying `cancelled`.

### 6.6 The deliverable

| Check | Evidence | Result |
|-------|----------|--------|
| Source files exist | Project #2's main branch (merged by the owner policy): `calculator.py`, `tests/test_calculator.py`, `README.md`, the spec folder | PASS |
| The application runs | Run in L3 on this checkout, beyond the agents' own checks: `12 + 30` → 42, `10 - 15` → -5, `6 * 7` → 42, `1 / 4` → 0.25, `5 / 0` → "Error: division by zero" (exit 1), `abc + 1` → "Error: invalid number: abc" (exit 1), `1e308 * 10` → `CalculatorError: result out of range` | PASS |
| The tests pass | `python3 -B -m pytest -q` in L3: 27 passed, 10 subtests passed | PASS |
| Stored in the correct project | The project's own repository (`data/workspaces/project-2`), nothing in any other | PASS |
| The final report is accurate | The mission report (`mission.report`): 3 tasks done, each `verified` only from its acceptance results, none failed or retried. That matches the task records | PASS |
| Task status correct | All three `completed`, the goal `approved` | PASS |

Total 682 s; nothing left running (`LiveProcess` empty).

## Findings

- **V23 (fixed):** a project's description, where TEST-PROJECT-003's requirements were registered, reached no agent.
  The planner got the stack, rooms, deadline, priority, budget and formal requirements (S08-R-01), not the
  description. `loop._project_context` now gives it as "Description (as registered)", capped at 2,000 characters
  (`test_the_projects_registered_requirements_reach_the_planner`).
- **V24 (fixed):** task stages had no transition table, so a late worker moved a cancelled task on (goal #111, task
  691). `workflow.TASK_TRANSITIONS` makes `completed` and `cancelled` final and refuses every move outside the
  lifecycle; a refused move is an event in the goal's log.
- **V25 (open, low):** a goal run by a process that ends while a background thread still works on it stays in
  `planning` until the next server restart. The live scripts of phases 4 and 6 ended right after a goal finished.
  The QA Engineer's playbook experiment #20 then started its smoke trial in the script's process, which left eval
  goals #331 and #333 behind (#333 also left a process row). Both were cancelled by hand; the server's own sweep
  restarts the trial. The live scripts no longer start smoke trials. A CLI command that runs a goal in its own
  process has the same exposure; a fix would record the owning process on the goal so the watcher can recover it.

## Phase 6 acceptance

| Criterion | Result |
|-----------|--------|
| 6.1 Project registered | PASS |
| 6.2 Assignment given to the PM | PASS (requirements in the description: after V23) |
| 6.3 Task records | PASS |
| 6.4 Execution by developer and QA | PASS |
| 6.5 Explicit state machine | PASS after V24 |
| 6.6 A working calculator, not a description of one | PASS |

## Phase 7 starts from here

Phase 7 (quality and evaluation): three deliberately correct, buggy and incomplete tasks for QA to judge, the
evaluation dimensions, evaluator independence and the correction cycle.
