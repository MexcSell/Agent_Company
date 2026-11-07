# Review (iteration 1)

## Review the blocker pause against the goal (qa, done)

## Logic verification

**Test run.** I could not run `uv run pytest -q tests/test_blocker_pause.py tests/test_sandbox_infra.py`. The harness asked for approval and none was given; `.venv/bin/python -m pytest` with the same files was also refused. The developer reports 35 passed, but that is their result, not mine. Everything below comes from reading the code and tracing it by hand.

**Changed code (commit 213cc98):** `loop.py:1039-1048` (worker branch), `loop.py:1461-1463` (check in `_run_steps`), `loop.py:1678` (`BLOCKER_SKIP`), `loop.py:1795-1812` (`_out_for_blocker`), and `tests/test_blocker_pause.py`.

### (1) Normal case: a writing task with a BLOCKER line and no changed files
- `loop.py:1001`: `_checkpoint` commits and returns `changed`. With no change, `git diff --name-only before head` is empty, so `changed == []` (`loop.py:892-900`).
- `loop.py:1031-1038`: the infra check runs first. With no infra failure, it carries on.
- `loop.py:1040`: `contracts.blockers(output)` runs only when `contract.writes and changed == []`. It uses `_BLOCKER_LINE` (`contracts.py:277,290-293`).
- `loop.py:1042-1048`: the task gets `status="skipped"`, `stage="blocked"`, output `BLOCKER_SKIP + "; ".join(blocked)`. The gate keeps `blockers`, so `management.py:399` still lists them. An event is logged and the worker returns False.
- `scheduler.py:108-110`: tasks that depend on it are set to `skipped` with "skipped: a dependency failed", so the review never runs.
- `loop.py:1452-1463`: after the DAG, the order is infra → quota → recruitment → `_skipped_with(..., BLOCKER_SKIP)` (`loop.py:1685-1690` returns the text after the prefix) → `_out_for_blocker`. This all happens before `_budget_skipped` (1464), `write_outputs` (1469) and `quality.evaluate` (1473).
- `loop.py:1804-1811`: the goal status becomes `paused` with reason `blocker: …`. The checkpoint keeps `iteration`/`planned` and gains `paused_for="blocker"` and `paused_why`. An event is logged and a warning `notify` is sent. `_record_history` is not called, so `g.history` stays empty, and the test checks this.
- **Root cause:** fixed. Before this change the BLOCKER line was only stored in the gate (`loop.py:1054`), and the gate and the next iteration ran anyway.

### (2) Edge cases: the old behaviour stays
- **BLOCKER line and a changed file:** `changed != []`, so `blocked = []` and the task reaches the old `done`/`failed` path at `loop.py:1049`. Covered by `tests/test_blocker_pause.py:82-90`.
- **Empty diff and no BLOCKER line:** `blockers()` returns `[]`, so the old path runs. Covered by `tests/test_blocker_pause.py:93-100`.
- **Read-only task with a BLOCKER line:** `contract.writes` is False, so `blocked = []`. Covered by `tests/test_blocker_pause.py:103-109`.
- **Checkpoint failure:** `_checkpoint` returns `("", None)` (`loop.py:894-896`), and `None == []` is False, so the old path runs. This is the deviation the developer described. It is correct, but no test covers it.
- **Saved spec answer:** the loop commits the answer itself (`loop.py:1016-1020`), but `changed` stays `[]`. So a BLOCKER inside a saved spec answer still pauses the goal, even though a file was committed. I accept this: the file is the agent's text, not a build change. It is not documented, though.

### (3) Resume and errors
- **Resume:** `resume()` at `loop.py:2114-2127` checks the budget again and removes `paused_for`/`paused_why`/`resume_after`, but keeps `iteration` and `planned`. Then `_run_steps` sees `cp["iteration"] == first and planned` and no `evaluated`, so `resume=True` (`loop.py:1391`). `_requeue_unfinished` (`loop.py:1410`, `1845-1856`) puts both the BLOCKER-skipped task and the review skipped as a dependent back in the queue. The same iteration runs again; no new iteration is planned. Covered by `tests/test_blocker_pause.py:69-79` (`evaluated == [1]`, `iteration == 1`).
- **Resume with the blocker still there:** the task pauses again in the same iteration. That is the intended result.
- **Eval-origin goal:** `loop.py:1801-1803` sets status `failed` with result `Stopped: blocker: …`, with no checkpoint flag and no notification. Covered by `tests/test_blocker_pause.py:112-119`.
- **Precedence:** infra and quota are checked before the blocker in both places. In the worker, the infra check is at `loop.py:1032` and the blocker check at `1040`. In `_run_steps`, infra is at 1452, quota at 1455 and recruitment at 1458, all before the blocker at 1461.

## Checklist
- **Contract satisfied:** yes. A writing task with an empty diff and a BLOCKER line skips the review and the gate, pauses the goal, and keeps the iteration.
- **Interfaces compatible:** yes. There is a new constant and helper, and the return dict gains a `blocker` key in the same shape as `infrastructure`/`recruitment`/`quota`. `set_status(stage=...)` is used the same way as in the infra branch.
- **Error handling:** a failed checkpoint (`None`) does not pause the goal, which is correct. A failed run (`res.ok` False) whose output has a BLOCKER line and no change does pause, which is reasonable.
- **Edge cases:** see the `BLOCKER: none` finding below.
- **Tests sufficient:** mostly. All six cases from the brief are there. Missing: the `changed is None` case and a `BLOCKER: none` case. `tests/test_blocker_pause.py:62` only checks `!= "done"`.
- **Tests offline:** yes. `tests/conftest.py:5-23` removes every API key and uses a temporary `DATA_DIR`. The test replaces `_execute` for tasks and stubs `enqueue` (`tests/test_blocker_pause.py:21-22,41`), so nothing reaches a real model.
- **Dependencies:** none added.
- **Security:** no new I/O. The notification text comes from the agent's BLOCKER line, cut to 256 characters by `blockers()`.
- **Performance:** no cost. The check only runs on writing tasks with an empty diff.
- **Docs updated:** yes. The `loop.py` entry in CLAUDE.md describes `BLOCKER_SKIP` and `paused_for="blocker"`.

FINDING: src/agent_company/loop.py:1040 (contracts.py:277) | `_BLOCKER_LINE` also matches placeholder lines such as "BLOCKER: none", "BLOCKER: N/A" or "- BLOCKER: no". The prompt asks for a BLOCKER line (`loop.py:835`), so a writing task that correctly changes nothing (the work was already done) and writes a placeholder now pauses the goal for a person. The pause is cheaper than the old wasted iteration, so this does not block approval | Drop placeholder values (`none|n/?a|no|-`, case-insensitive) in `contracts.blockers()` or at the pause check, and add a test where "BLOCKER: none" with an empty diff does not pause.
FINDING: tests/test_blocker_pause.py:82 | The deviation the developer described (`changed is None` after a failed checkpoint must not pause) has no test, so a later change to `not changed` would pass unnoticed | Add a case that patches `loop._checkpoint` to return `("", None)` for the implement task and asserts the goal is not paused and the task is not `BLOCKER_SKIP`.
FINDING: tests/test_blocker_pause.py:62 | `_tasks(gid)["review"][0] != "done"` would also pass if the review had failed or was still queued, so it doesn't prove the review was skipped as a dependent | Assert `== "skipped"` and that the output starts with "skipped: a dependency failed" (`scheduler.py:110`).
FINDING: src/agent_company/loop.py:1016-1020 | A writing task whose only file is a spec answer the loop saved still has `changed == []`, so a BLOCKER line in that answer pauses the goal even though a file was committed. This behaviour is undocumented | Write it down as intended in the `_out_for_blocker` docstring, or treat a non-empty `saved` as "not an empty diff".

Execution evidence: QA could not run the two test files (approval was refused twice). The verdict below rests on the code trace above and the developer's reported 35 passed.

NEXT: Run `uv run pytest -q tests/test_blocker_pause.py tests/test_sandbox_infra.py` in the gate to confirm the reported 35 passed.
NEXT: Filter out "BLOCKER: none"-style placeholders and add the `changed is None` test.
MESSAGE recommendation to developer: In `contracts.blockers()` (contracts.py:290), ignore placeholder values such as "none"/"n/a" so a no-op writing task does not pause the goal, and add a test in tests/test_blocker_pause.py for that and for `_checkpoint` returning None.

OUTCOME: APPROVED_WITH_COMMENTS
VERDICT: PASS
