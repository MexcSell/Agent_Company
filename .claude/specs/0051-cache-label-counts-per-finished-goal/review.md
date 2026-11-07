# Review (iteration 3)

## Review the label-count cache against the goal and the iteration-2 findings (qa, done)

I approve this iteration with two minor comments: `VERDICT: PASS`. This answer is the review file: `specs.save_answer` writes it to `.claude/specs/0051-cache-label-counts-per-finished-goal/qa-review-3.md`, so I wrote no files myself.

**Full-suite evidence.** As instructed, I did not run pytest. I am citing the developer's output for task `store_after_outcome`, not a gate result:
- Full suite: `uv run pytest -q` → `1009 passed in 411.05s (0:06:51)`.
- Targeted run: `uv run pytest -q tests/test_label_cache.py tests/test_projects.py tests/test_mission_report.py` → `48 passed in 18.21s`.

The gate result for this iteration's integration stage was not in my inputs. If it reports anything other than all passed, it overrides this citation.

The worktree is at `df3cfa6` with no uncommitted changes. Iteration 3 (`git diff 734a5c4 df3cfa6`) changed only `src/agent_company/loop.py` and `tests/test_label_cache.py`. `mission.py`, `workflow.py:109`, `api.py:2367` and `rca.py:92-99` are unchanged since iteration 2, which I already reviewed.

## Logic verification

**`loop.run_goal` / `loop._store_label_counts`** (`loop.py:1247-1285`) and **`loop._notify_goal`** (`loop.py:1876-1889`)
- **Normal case:**
  1. `_run` sets the final status through `_update_goal`, and `set_status` clears `label_counts` (`workflow.py:109`).
  2. `_notify_goal` sends the notification and runs `rca.after_goal`. It no longer stores counts.
  3. The `finally` block runs `settle_proposals`, `management.sync` and `retire_goal_clones`.
  4. `_chat_outcome` posts `Goal #N: …` (`loop.py:1268`).
  5. Only then does `_store_label_counts` run (`loop.py:1269`). It reloads the goal and, if the status is not in `workflow.ACTIVE` and the origin is not `eval`, calls `mission.store_label_counts`. That runs `_count` (with `report()` in its own session) and then `_write`, which saves the counts in a new session.
  6. On later refreshes, `label_counts` (`mission.py:188-195`) returns the stored dict without calling `report()`, and so does `rca.evidence`.
  - `test_counts_are_stored_after_the_outcome_message` asserts the call order `["outcome", "store"]` and that the stored counts equal `report()`.
  - `test_simple_overview_reads_stored_counts` uses a spy to check that the overview never calls `report()`.
  - This fixes the cause found in iteration 2: `report()` no longer runs before the outcome message is posted.
  - `workflow` (imported at `loop.py:29-31`) and `contextlib` (`loop.py:12`) are imported at module level, so the helper cannot fail with a `NameError` that its catch-all would hide.
- **Edge case:**
  - A goal that ends paused stays in ACTIVE, so `_store_label_counts` stores nothing (`test_a_goal_that_stays_active_is_not_stored`).
  - Resuming or reopening a goal (failed → recovering, paused → queued) goes through `set_status`, which sets `{}` (`test_reopened_goal_clears_the_cache`).
  - If the goal is resumed while `report()` is running, `_write` (`mission.py:213-216`) reloads it inside its write session, sees ACTIVE and writes nothing (`test_store_skipped_when_the_goal_resumes_during_the_count`).
  - A goal cancelled mid-run raises `Halted`, so neither `_chat_outcome` nor the store runs, as before. The same is true for goals that finish later through a person's approval or rejection (`loop.py` `approve`/`reject`). All of these fill the cache on the first read.
- **Error case:**
  - If the database is locked during the store, `mission.store_label_counts` catches the error and logs it.
  - If `_goal` or the store raises anyway, `_store_label_counts` catches it and logs inside `contextlib.suppress`. `run_goal` still returns the same `out`, and the goal's status was committed earlier (`test_a_raising_store_does_not_change_the_outcome` asserts `awaiting_approval` and the error event).
  - If the write fails on a first read, `label_counts` (`mission.py:197-201`) logs the error and still returns the computed counts (`test_failed_store_still_returns_the_counts`, `test_failed_store_and_failed_log_still_return_the_counts`).
  - The overview's `except Exception` → `labels={}` (`api.py:2367-2368`) keeps the GET at 200 (`test_overview_survives_a_raising_label_count`). The fail-open read asked for in K495 holds.

**Nothing stores counts for an active goal:**
- `label_counts` raises `ValueError` before computing anything for an active goal.
- `_write` checks ACTIVE inside the write session.
- `_store_label_counts` checks ACTIVE before calling the store.
- `store_label_counts` on an active goal fails inside `report()` and stores nothing.

**Single-connection rule:** no write happens inside an open `session()`.
- `run_goal` runs the store after all its sessions are closed.
- `label_counts` closes its read session before `_count` and `_write`.
- The log calls run outside any session.

## Checklist
- **Contract:** met. The counts are stored when the goal finishes, after the outcome message, and the overview and RCA read the stored dict.
- **Interfaces:** `delivered[].labels` keeps the shape `{verified, partial, failed}` or `{}`. `label_counts` raises `KeyError`/`ValueError` like `report()`. `_notify_goal`'s signature is unchanged.
- **Error handling:** fail-open at every layer.
- **Edge cases:** paused, resumed, cancelled, approved by a person, zero tasks and older goals are all covered.
- **Tests:**
  - Iteration 3 adds three order and outcome tests and updates (a).
  - The `tests/test_projects.py` race is fixed both by `wait_outcome` and by the new order.
- **Dependencies:** none added.
- **Security:** no new inputs.
- **Performance:** the outcome message is no longer delayed by `report()`.
- **Docs:** the CLAUDE.md line ("stored when the goal finishes …") is still accurate.

**Developer's eval-origin decision:** I agree with keeping the `origin != "eval"` check. `_notify_goal` already returned early for eval goals, eval goals are excluded from the overview (`Goal.origin != "eval"`), and RCA skips eval goals.

FINDING: src/agent_company/loop.py:1269 | For a failed goal whose RCA trigger holds, `rca.after_goal` (inside `_notify_goal`, before the store) calls `rca.evidence` → `label_counts`, which builds and stores the counts. `_store_label_counts` then builds `report()` a second time and overwrites them with the same values. This only costs one extra report on an analysed failure | Optional: in `_store_label_counts`, skip the store when `g.label_counts` is already non-empty, since any status change clears it.
FINDING: src/agent_company/loop.py:1273-1285, src/agent_company/mission.py:225-229 | The "label counts of goal #N not stored" log message is written in two places | Optional: have `_store_label_counts` call `mission._log_not_stored` instead of repeating the message.

OUTCOME: APPROVED_WITH_COMMENTS. The iteration-2 finding is fixed: the counts are now stored after `_chat_outcome`, so `report()` no longer delays the outcome message, and `_notify_goal` no longer stores. The normal, edge and error cases above follow from the code, and each has a test. No path stores counts for an active goal, and the overview's fail-open read from K495 is unchanged. The full-suite evidence is the developer's `store_after_outcome` run (`1009 passed in 411.05s`), not a run of mine. Both findings are optional performance or readability points, not defects.

NEXT: The gate's integration stage confirms `uv run pytest -q`. Then a person approves the merge of `agents/goal-51`.
MESSAGE fact to developer: QA approved iteration 3 with two optional comments, `src/agent_company/loop.py:1269` (a second `report()` after RCA already stored the counts) and `loop.py:1283` (the error log message repeated from `mission._log_not_stored`); neither blocks the merge.

VERDICT: PASS
