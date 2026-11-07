# Review (iteration 1)

## Review the label-count cache against the goal (qa, done)

I couldn't run the required pytest command, so this review is ESCALATED. Both ways of running it (`uv run pytest -q tests/test_label_cache.py tests/test_mission_report.py tests/test_simple_mode.py tests/test_rca.py`, and the same tests via `.venv/bin/python -m pytest`) were stopped with "This command requires approval". I have no summary line of my own to quote. The developer reported "64 passed in 27.89s", but that is their claim, not something I saw. Everything below comes from reading the code and `git diff main...HEAD`.

## Logic verification

**`mission.label_counts` / `_store` / `store_label_counts`** (`src/agent_company/mission.py:182-218`)
- **Normal case:** a goal finishes and `loop._update_goal` saves the new status in its own session. `workflow.set_status` (`workflow.py:109`) clears `label_counts` in that same session. After the session closes, `_notify_goal` (`loop.py:1875`) calls `store_label_counts`, then `rca.after_goal`. Inside `_store`, `report()` opens and closes its own session, then a new session writes `{verified, partial, failed}`. On the overview's next refresh, `label_counts` finds a non-empty dict and returns it without calling `report()`.
- **Edge cases:**
  - An older goal with `label_counts={}`: the first read finds nothing stored and calls `_store` once; later reads use the stored value. `test_old_goal_filled_on_first_read_then_served_from_the_row` covers this.
  - A goal with zero tasks: it stores `{0,0,0}`, which is a non-empty dict and so counts as cached.
  - A goal that resumes (failed → recovering): `set_status` sets `{}`, and `label_counts` then raises `ValueError` because the goal is active.
  - The developer clears the cache on every status change, which is wider than spec step 3. I think that is correct: `_tasks_follow` changes task states on final moves, and the cache fills again on the next read.
  - A person approving or rejecting a goal (`loop.py:1976` and `:1993`) clears the cache without storing new counts, so it fills again on the next read.
- **Error cases:**
  - Unknown goal → `KeyError`; active goal → `ValueError`. The overview catches both and returns `labels={}`.
  - If `report()` or the write fails, `store_label_counts` catches it and logs an event, and that logging is guarded too. It never raises, and by then the goal's status is already committed, so the outcome is unaffected.
- **Root cause or symptom?** This fixes the cause: the 15-second overview no longer rebuilds `report()` for finished goals that already have stored counts.

**`api.simple_overview`** (`api.py:2367`)
- The session ends at line 2340, before the loop over `heads`, so a first-read write inside `label_counts` does not run under an open session.
- **Shape:** `{verified:int, partial:int, failed:int}`, or `{}` when no counts are available. This is the same as before.
- **New error path:** see the first FINDING below.

**`rca.evidence`** (`rca.py:92-99`)
- Counts are collected before `with session()`, so there is no write under an open session.
- **Callers:** `analyse` ← `after_goal`; both call it outside any session.
- **Error case:** any `Exception` from `label_counts` is skipped and the goal line simply has no label suffix, so the evidence pack still builds. `test_rca_evidence_names_the_labels` checks this for an active goal.

**`db.Goal.label_counts`** (`db.py:508`)
- The default is a callable, so `_add_missing_columns` adds the column without a DDL default. Its second pass then fills NULL rows with `{}`, because the column is not nullable.
- `test_add_missing_columns_adds_label_counts` checks that this is idempotent and that existing goals end up with `"{}"`.

**Second-connection rule:** none of the new writes happen inside an open `session()`.
- `_store` reads first, then writes in a separate session.
- `store_label_counts` calls `log_event` outside any session.
- `set_status` writes through the caller's `s`.

## Checklist
- **Contract:** met. Counts are stored before `rca.after_goal` and read by the overview and RCA.
- **Interfaces:** the JSON shape is unchanged.
- **Error handling:** fail-open where the spec asks for it. One gap in the overview (FINDING 1).
- **Edge cases:** covered.
- **Tests:** they cover cases a–g plus migration, zero tasks and finished-to-finished moves, but I could not run them.
- **Dependencies:** none added.
- **Security:** no new inputs.
- **Performance:** the overview's first load builds the report once for each of up to 20 older finished goals, then stops.
- **Docs:** CLAUDE.md and ADR 0064 are updated.

FINDING: src/agent_company/api.py:2368 | The GET `/api/simple/overview` now writes on the first read of an older goal. A write failure, for example SQLite "database is locked" while the loop thread writes, is not a `KeyError`/`ValueError`, so the whole overview returns a 500 instead of `labels={}` | Catch `Exception` there, or have `label_counts` return the computed counts when the store fails, as `store_label_counts` already does.
FINDING: src/agent_company/loop.py:1875 | `store_label_counts` does not check the goal's status. If a person resumes a failed goal between `_update_goal` and the store, counts are written to an active goal. This is harmless, because reads of an active goal raise `ValueError` and the next status change clears the row, but the row is briefly inconsistent | Skip the write in `_store` when `g.status in workflow.ACTIVE` (low priority).
FINDING: tests/test_label_cache.py:1-144 | This QA session has no independent run of the four required test files, because the environment refused the command, so nothing confirms the code works at runtime | Run `uv run pytest -q tests/test_label_cache.py tests/test_mission_report.py tests/test_simple_mode.py tests/test_rca.py` in `data/worktrees/goal-51` with the command allowed, and record the summary line.

BLOCKER: environment. Both forms of the required pytest command were refused in this QA session ("This command requires approval"), so I have no execution evidence (K293).

The code itself looks approvable with the two comments above. The FAIL is because of the environment block, not the code.

NEXT: Re-run the four test files with pytest allowed. If they pass, approve with FINDING 1 as a follow-up.
MESSAGE request to developer: In `src/agent_company/api.py:2368`, widen the overview's `except` (or make `mission.label_counts` fail-open on its store) so that a locked-database write on the first read cannot turn `/api/simple/overview` into a 500.

OUTCOME: ESCALATED
VERDICT: FAIL

## Run the full test suite after the change (qa, failed)

I could not run the suite, so I can't give you a pass. Every pytest command I tried was refused with "This command requires approval", so I have no summary line and no list of failures. The gate's own `uv run pytest -q` will be the first real run.

## Logic verification

Commands refused in this session:
- `uv run pytest -q` (in the background, and with `-p no:cacheprovider`)
- `uv run pytest -q tests/test_label_cache.py`
- `.venv/bin/python -m pytest -q tests/test_label_cache.py`

So nothing below has been run. I only read the diff against main, which touches 6 source files plus `tests/test_label_cache.py`.

- **`mission.label_counts` (`mission.py:182`):**
  - Normal case: a finished goal with stored counts returns them from the goal row without calling `report()`. Old goals with nothing stored go through `_store`, which calls `report()` and then saves in a new session after `report()` has closed its own.
  - Edge case: a goal with zero tasks is stored as `{0,0,0}`, so it is not rebuilt on every read.
  - Error cases: an unknown goal raises `KeyError`; an active goal (`workflow.ACTIVE`, `workflow.py:39`) raises `ValueError`. These are the same exceptions the API's `except (KeyError, ValueError)` already caught.
  - This fixes the cause (the report was rebuilt on every poll), not just the symptom.
- **`mission.store_label_counts`:** it catches every exception and logs an event, so a broken report can't change a goal's outcome. It runs in `loop._notify_goal` (`loop.py:1875`) after `_update_goal`. I checked all 5 call sites (`loop.py:1452, 1471, 1479, 1488, 1854`), and none of them has a session open. Eval goals return early and fill on their first read.
- **`workflow.set_status` (`workflow.py:107`):** it clears the stored counts on every status change. This is the only place a goal's status is assigned in `src/` (grep confirms). So a resumed goal (moving into `ACTIVE`) and a move between finished statuses (which also updates the goal's tasks via `_tasks_follow`) can't serve stale counts. This is wider than step 3 of the spec, but it is the safer choice and has a test.
- **`api.py:2367` (Simple overview):** `report(gid)["tasks"]["labels"]` is replaced by `label_counts(gid)`. The JSON shape stays the same, and the `{}` fallback is kept.
- **`rca.evidence` (`rca.py:92`):** counts are read before the session opens, so a first read can write without a second writer. Any error is skipped, and an active goal just gets a goal line without counts.

## Checklist

| Item | Result |
|---|---|
| Contract satisfied | No. The task is to report the full-suite summary line, and it could not be produced. |
| Interfaces compatible | Yes (static check): API shape unchanged, and RCA only adds a suffix to the goal line. |
| Error handling | Yes: the store never raises, and RCA and the overview fall back. |
| Edge cases | Zero tasks, eval goals, reopened goals and finished-to-finished moves are covered in code and tests. |
| Tests sufficient | They cover cases a–g, the migration, never raising on a store error, and refilling after a final move. None were run here. |
| Dependencies | None added. |
| Security | No new input surface. |
| Performance | The overview runs `report()` only once per finished goal, then reads the stored counts. |
| Docs | `CLAUDE.md` and ADR 0064 were updated. |

Whether any test fails, and whether such a failure also happens on main, is still unknown.

FINDING: tests/ (whole suite) | This QA session has no evidence from running `uv run pytest -q`, so it can't show that nothing failed because of Goal.label_counts / mission.label_counts / workflow.set_status / simple_overview / rca.evidence | Allow `uv run pytest -q` for this QA run, or use the gate's integration-stage run of the same command, and record its summary line

BLOCKER: Every pytest command in this session needed approval and was refused, so the requested full-suite run couldn't be done.
NEXT: Re-run this task with `uv run pytest -q` allowed. If anything fails, check whether it also fails on main by re-running that one test against `git show main:<file>`.
MESSAGE request to general-manager: qa_full_suite couldn't run pytest (refused, needs approval). Allow `uv run pytest -q` for the QA role, or take the summary line from the gate's integration-stage run.

OUTCOME: ESCALATED
VERDICT: FAIL
