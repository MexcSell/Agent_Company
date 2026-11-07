# Review (iteration 2)

## Review the label-count cache against the goal and the iteration-1 findings (qa, done)

I checked all four iteration-1 findings against the code and found no defect, so this is APPROVED_WITH_COMMENTS with `VERDICT: PASS`. The environment refused the pytest command again, so nothing below has been run by me.

BLOCKER: environment. `uv run pytest -q tests/test_label_cache.py tests/test_mission_report.py tests/test_simple_mode.py tests/test_rca.py tests/test_projects.py` was refused ("This command requires approval"), so I have no summary line to quote. The developer reported "61 passed" for the first four files, "12 passed" for `tests/test_projects.py` and "1006 passed" for the full suite, but those are their figures, not something I saw. As the task says, I am not counting this as a code defect. Everything below comes from reading `git diff b06b418 -- src tests CLAUDE.md` and the code around it.

## Logic verification

**`mission.label_counts` / `_count` / `_write` / `store_label_counts`** (`src/agent_company/mission.py:182-238`)
- **Normal case:**
  - The goal reaches its final status through `loop._update_goal`, which calls `workflow.set_status` (`workflow.py:109`) and clears `label_counts` in the same session.
  - Then `_notify_goal` (`loop.py:1875`, outside any session) calls `store_label_counts`.
  - `store_label_counts` runs `_count`, where `report()` opens and closes its own session. It then runs `_write`, which opens a new session, sees a final status and saves `{verified, partial, failed}`.
  - `rca.after_goal` runs after that, and `rca.evidence` (`rca.py:92-99`) reads the stored dict before opening its own session.
  - On each overview refresh, `label_counts` finds a non-empty dict and returns it without calling `report()`. `test_simple_overview_reads_stored_counts` uses a spy on `report()` to check this.
  - This fixes the cause: finished goals are no longer rebuilt on every 15-second refresh.
- **Edge case:**
  - An older goal with `{}`: the session closes, then `_count` runs once and `_write` saves the result. The next read is served from the row (`test_old_goal_filled_on_first_read_then_served_from_the_row`).
  - A goal with zero tasks stores `{0,0,0}`, which counts as cached.
- **Error case:**
  - If `_write` raises (for example "database is locked"), `label_counts` catches it, `_log_not_stored` logs it (that logging is guarded too), and the computed counts are still returned. `test_failed_store_still_returns_the_counts` and `test_failed_store_and_failed_log_still_return_the_counts` cover this.
  - An unknown goal raises `KeyError`; an active goal raises `ValueError`.
  - `store_label_counts` never raises. The goal's status is already committed before it runs, so the outcome is unaffected.

**Finding (1): `/api/simple/overview`** (`api.py:2367-2368`)
- `except Exception` now falls back to `labels={}`, so a failing read or store can no longer return a 500 (`test_overview_survives_a_raising_label_count` asserts status 200 and `{}`).
- Together with the fail-open read, a failed write now gives the real counts instead of a 500.
- The loop runs after the session closes at `api.py:2340`, so the first-read write does not happen inside an open session.
- `delivered[].labels` keeps the same shape: `{verified, partial, failed}` as integers, or `{}`.
- Resolved.

**Finding (2): status check inside the write** (`mission.py:~210`)
- `_write` loads the goal again inside its own write session and skips it if the goal is missing or in `workflow.ACTIVE`.
- `test_store_skipped_when_the_goal_resumes_during_the_count` resumes the goal while `report()` runs and checks that nothing is stored.
- A very small window remains between the check and the commit, because SQLite starts the write transaction lazily. It is harmless: reading an active goal raises `ValueError`, and the next status change clears the row.
- Resolved.

**Finding (3): clearing on a move into ACTIVE** (`workflow.py:109`)
- `set_status` clears the counts on every status change, which includes every move into ACTIVE (`test_reopened_goal_clears_the_cache`).
- When a reopened goal finishes again, `_update_goal` clears the counts once more and `_notify_goal` stores new ones.
- Two paths do not store immediately: approval or rejection by a person (`loop.py:1976`, `1993`) and eval goals, which return before `_notify_goal` stores. Both fill the cache on the next read, which `test_final_move_clears_the_cache_and_refills_on_read` covers.
- Resolved.

**Finding (4): the project test and `loop._chat_outcome`** (`loop.py:1247-1287`)
- `run_goal` does things in this order:
  1. `_run` sets the final status, then calls `_notify_goal` (notify, store counts, RCA).
  2. The `finally` block runs `settle_proposals`, `management.sync` and `retire_goal_clones`.
  3. `_chat_outcome` posts the outcome message last.
- The only change to `loop.py` in this diff is the three-line store call, so `_chat_outcome` still posts after the final status, as it did before this goal.
- The test's `wait()` polls only the goal's status, so its `after={id}` check could already miss the later system message. The store call makes that gap a little longer, which is why the race showed up now.
- `post_for_goal` writes `Message.goal_id` (`projects.py:447-454`), and the message begins with `Goal #{gid}:`, which is exactly what `wait_outcome` (`tests/test_projects.py:39`) waits for. The fix waits for a real event rather than hiding a defect.
- Resolved.

**Second-connection rule:** no new write happens inside an open `session()`.
- `label_counts` closes its session before `_count` and `_write`.
- `_log_not_stored` runs outside any session.
- `set_status` writes through the caller's `s`.

## Checklist
- **Contract:** met.
- **Interfaces:** the JSON shape is unchanged. `KeyError`/`ValueError` behave as in `report()`.
- **Error handling:** fail-open in the overview, RCA and store paths.
- **Edge cases:** older goals, zero tasks, resume during the count, and moves between final statuses are covered.
- **Tests:** 18 tests in `tests/test_label_cache.py` plus the `wait_outcome` change. I read them but did not run them.
- **Dependencies:** none added.
- **Security:** no new inputs.
- **Performance:** the first overview load builds the report once for each of up to 20 older finished goals, then never again.
- **Docs:** the CLAUDE.md line is updated. The `db.py` migration is unchanged from iteration 1; `_add_missing_columns` fills existing rows with `{}`, and `test_add_missing_columns_adds_label_counts` covers it.

FINDING: tests/test_label_cache.py:1-205, tests/test_projects.py:39 | No pytest run in this QA session (BLOCKER: environment), so none of the passes above have been seen by QA; this is not a code defect | Have the gate's own run of `uv run pytest -q tests/test_label_cache.py tests/test_mission_report.py tests/test_simple_mode.py tests/test_rca.py tests/test_projects.py` and `uv run pytest -q` confirm the developer's 61 + 12 and 1006 passed.

BLOCKER: environment. pytest needs approval in this QA session.

NEXT: The gate runs the five test files and the full suite. If they pass, merge.
MESSAGE fact to developer: all four iteration-1 findings are resolved in `src/agent_company/mission.py`, `src/agent_company/api.py:2368` and `tests/test_projects.py`, but QA could not run pytest, so your recorded summary lines are the only execution evidence.

OUTCOME: APPROVED_WITH_COMMENTS
VERDICT: PASS

## Run the full test suite after the change (qa, done)

I still couldn't run the suite, so there's no summary line from me and I'm marking this as failed. `uv run pytest -q` was refused again with "This command requires approval", as in iteration 1. The tree matches commit d685d88 with nothing uncommitted. The only full-suite results are the developer's (1006 passed) and the previous gate's (1 failed, 996 passed, 4 skipped); I saw neither run myself.

## Logic verification
I only read the code; none of this has been run.

- **`mission.label_counts` (`mission.py:182-201`)**
  - Normal case: if counts are already stored, they come back from the goal row without calling `report()`. Otherwise `_count` builds them with `report()` and `_write` stores them.
  - Edge case: if `_write` fails (for example "database is locked"), the error is logged and the counts are still returned. This fixes the cause of the earlier finding (a write failure on a read call), not just the symptom.
  - Error case: an unknown goal raises `KeyError`; an active goal raises `ValueError`.
- **`mission._write`**: it reads the goal again in its own session and skips it if the goal is gone or back in `workflow.ACTIVE`. This closes the race where a goal is resumed while its counts are being computed. The guard is checked when writing, and SQLite allows only one writer, so it can't miss a resume.
- **`store_label_counts`**: still never raises; the error log is shared with `label_counts` through `_log_not_stored`.
- **`api.py:2368` (Simple overview)**: catching any `Exception` means one goal's counts can't turn the overview into a 500. It falls back to `labels={}`, so the JSON shape is unchanged.
- **The `tests/test_projects.py` failure from the previous gate (`test_projects.py:223`, `assert ... == []`, "Left contains one more item: {'id': 10, 'author_type': 'system', ...}")**: this is related to this goal.
  - The loop posts the "Goal #N: …" outcome message in `_chat_outcome` (`loop.py:1268`) after `_notify_goal` has run.
  - This goal added `mission.store_label_counts` to `_notify_goal` (`loop.py:1875`), which builds the whole `report()`. That lengthens the gap between the goal reaching its final status (when `wait()` returns) and the outcome post.
  - The test read the last message id inside that gap, and the late system message then appeared after it.
  - The new `wait_outcome` waits for the message `post_for_goal` writes: it carries `goal_id` (`projects.py:453`) and starts with `Goal #{gid}:` (`loop.py:1284`). That matches what the loop actually posts, so it fixes the test's race. It is not a product bug; no message is lost, only delayed.
  - I could not check whether the test also fails on base b06b418, because that needs a pytest run.

## Checklist
| Item | Result |
|---|---|
| Contract satisfied | No: the required summary line and the base comparison are missing. |
| Interfaces compatible | Yes. |
| Error handling | Fails open on both the read path and the store path. |
| Edge cases | Resume during counting, unknown goal and active goal are covered. |
| Tests sufficient | Five new tests plus the `wait_outcome` sync. None were run by me. |
| Dependencies | None added. |
| Security | No new input surface. |
| Performance | The outcome post now waits for one `report()` per finished goal. |
| Docs | `CLAUDE.md` updated. |

FINDING: tests/ (whole suite) | This QA session has no run of its own of `uv run pytest -q`: no summary line, no failure list and no comparison with base b06b418, so the "1006 passed" can't be confirmed | Allow `uv run pytest -q` (and `git worktree add`) for the QA role, or use the gate's integration-stage run as the evidence
FINDING: src/agent_company/loop.py:1875 | `store_label_counts` builds the full `report()` before `_chat_outcome` posts the result. Users of the command window see the outcome message later, and this delay caused the `test_projects.py:223` race | Optional: move the store after `_chat_outcome` (the counts fill on first read anyway), or keep it and rely on `wait_outcome`

BLOCKER: Every pytest command in this QA session needs approval and is refused, so the full-suite run can't be done here.
NEXT: Re-run qa_full_suite with `uv run pytest -q` allowed, and quote its summary line. If `test_projects.py` fails again, re-run it alone on a `git worktree add /tmp/goal51-base b06b418` checkout, then remove that worktree.
MESSAGE request to general-manager: qa_full_suite has been refused pytest in both iterations ("This command requires approval"). Grant `uv run pytest -q` to the QA role, or treat the gate's integration-stage run as the full-suite evidence.

```json
{"insights": [{"title": "QA sessions for goal 51 cannot run pytest", "description": "Both QA iterations had every pytest command refused for approval, so run-the-suite tasks cannot produce execution evidence and fail regardless of code quality.", "evidence": ["qa_full_suite iteration 1 and 2: 'This command requires approval' for uv run pytest -q"], "expected_benefit": "Granting the QA role the test command removes repeated failed iterations", "confidence": 0.8}]}
```

OUTCOME: ESCALATED
VERDICT: FAIL
