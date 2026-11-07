# Review (iteration 1)

## Review gate-event iteration persistence against the goal (qa, done)

Status: I've finished reading the code. Both attempts to run the tests were refused for approval, so this review rests on reading the code only. Here is the report.

## Logic verification

**`db.log_event` / `db.event_iteration` / `db._as_int` (db.py:796-822, 1588-1589)**
- **Normal case (a new gate event in iteration 2):** loop.py:1443 calls `log_event("gate", "iteration 2: …", iteration=2, passed=…, checks=…, commit=…)`. `_as_int(2)` returns 2, so `Event.iteration` is 2, and `data["iteration"]` is still 2 because `**data` is unchanged. `event_iteration(e)` returns the column value, 2.
- **Edge case (a legacy row with only a message):** the column is NULL and there is no `data["iteration"]`. `_ITER` matches "iteration 3:" in the message, so the result is 3. The logic is the same as the old `mission._ITER` fallback, so old rows resolve as before.
- **Error case (no iteration anywhere):** each fallback returns None and nothing raises. `_as_int(None)` hits `TypeError`, which is caught. `True` is rejected as a bool. A non-numeric string hits `ValueError`, which is caught.
- **Root cause:** fixed, not hidden. The iteration is now a structured column, and parsing the message is left only as a fallback for legacy rows.

**`loop._run_steps` baseline (loop.py:1350-1352):** `iteration=0` reaches the column and `data`. The event has no `checks`, so `_gates` and the trace still skip it, which matches the behaviour before the change.

**`loop._keep_best` (loop.py:1812-1827):** `last` and `best` come from `g.history` entries that the function already indexes with `["iteration"]`, so no new KeyError is possible. The column gets the failed (last) iteration and `data` gets `kept_iteration`. The message is byte-identical, so `performance.py:422` (`Event.message.like("Kept iteration %")`) still counts these events.

**`loop.trace` (loop.py:2078):** each gate entry gets `"iteration": event_iteration(e)`. A new row gives the column value, a legacy row gives the parsed number, and a row with no iteration gives `None`. Before the change the trace exposed no iteration at all, so this adds a field and does not remove any.

**`mission._gates` (mission.py:37-46):**
- Normal case: the column value 2 becomes the key in `out`.
- Legacy row: the parsed message value is the key.
- No iteration: `continue`, with no exception.

The `int(it)` cast was dropped, which is safe because `event_iteration` only returns an int or None.

**Migration (`db._add_missing_columns`, db.py:1498-1516):** the new column is nullable and has no default, so the code issues `ALTER TABLE "events" ADD COLUMN "iteration" INTEGER` and existing rows stay NULL. The second loop skips the column because it is nullable, so there is no backfill. `tests/test_gate_iteration.py:89-102` covers this.

## Checklist
- **Contract satisfied:** yes, judging by the code. The column exists, all three writers set it (main gate at loop.py:1443-1445 through `iteration=iteration`, baseline 0, keep-best `last` plus `kept_iteration`), the readers use `event_iteration`, and `data["iteration"]` is preserved.
- **Interfaces compatible:** yes. The `log_event` signature is unchanged and trace entries only gain a key.
- **Error handling:** bad or missing values become None and nothing raises.
- **Edge cases:** a float such as `2.5` is silently truncated to 2 by `int()`. No caller passes a float, so this is cosmetic.
- **Tests:** the 7 new tests cover writing, precedence, a legacy row, no iteration, the trace and the migration. They look sufficient, but I could not run them.
- **Dependencies:** none added.
- **Security:** no change.
- **Performance:** the column is indexed on new databases only (see the finding below).
- **Docs:** none are needed for an internal field.

## Execution evidence
I ran `uv run pytest -q tests/test_gate_iteration.py tests/test_mission_report.py tests/test_loop.py` and also tried `.venv/bin/python -m pytest -q …`. Both were refused with "This command requires approval". No test has been run independently in this QA session; the only claim that they pass is the developer's own report.

FINDING: tests/test_gate_iteration.py:1-102, src/agent_company/loop.py:1351, src/agent_company/loop.py:1826, src/agent_company/mission.py:42 | The required pytest command has not been run independently: Python execution was refused for QA, so approving now would rest only on the developer's claim (the same failure as K378/K391) | A person, or a QA session with Python allowed, runs `uv run pytest -q tests/test_gate_iteration.py tests/test_mission_report.py tests/test_loop.py` in data/worktrees/goal-45 and confirms the summary line shows 0 failed
FINDING: src/agent_company/db.py:797 | On existing databases `_add_missing_columns` adds `events.iteration` without its index, so iteration filters there do a full scan (the developer noted this too; queries still work) | Add `CREATE INDEX IF NOT EXISTS ix_events_iteration ON events (iteration)` after the column loop, or drop `index=True` until the column is queried
FINDING: src/agent_company/loop.py:2078 | The trace `gates` comprehension line is about 140 characters, longer than the wrapped lines around it; this is style only | Wrap it after `"iteration": event_iteration(e),`

BLOCKER: Python and pytest execution need approval in this session, so I could not get independent evidence that the tests pass.
NEXT: Run the three test files (plus tests/test_performance.py for the "Kept iteration" count) with Python allowed. If they pass, this can be approved with the index comment.
MESSAGE request to developer: Optionally create the index for migrated databases in db._add_missing_columns (db.py:1498); the logic otherwise matches the goal on code review.

OUTCOME: ESCALATED. The code looks correct on review and I found no logic defect, but the required test command could not be run independently.

VERDICT: FAIL
