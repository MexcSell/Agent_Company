# Review (iteration 1)

## Review the per-user daily cap against the spec (qa, done)

# QA review: per-user daily cap (goal #65, task `qa_review`)

The code matches design.md §2–§7, but nothing enforces the cap yet: `/api/writing/assist` does not exist and nothing calls `user_cap`. I could not run any tests in this session, so the only proof that the tests pass is the gate's own run of the impl task's commands.

**Test evidence.** Three attempts were refused with "This command requires approval":
- `uv run pytest -q tests/test_user_cap.py tests/test_budgets.py`
- `uv run pytest -q tests/test_user_cap.py`
- `.venv/bin/python -m pytest -q tests/test_user_cap.py tests/test_budgets.py`

I have no pass/fail summary line of my own. The only proof the tests pass is the gate's run of the impl task's commands (tasks.md:17: `uv run pytest -q tests/test_user_cap.py` and `tests/test_budgets.py`). The developer reports "8 passed" and "9 passed", but I could not check those numbers. Everything below comes from reading the code.

## Logic verification

**`Run.user_id`** (db.py:771): a nullable, indexed plain Integer with no foreign key, as §2 asks.
- **Normal:** a new database gets it from `create_all`.
- **Edge (existing database):** `init` calls `_add_missing_columns` at db.py:1496. db.py:1514-1522 runs `ALTER TABLE "runs" ADD COLUMN "user_id" INTEGER` with no default, so old rows hold NULL. The loop at db.py:1525-1527 then creates `ix_runs_user_id`, because the new column is in `added`.
- **Error:** none. The column is nullable and has no foreign key that could fail on old rows.
- `test_a_runs_table_without_user_id_is_migrated` checks the column, the index and the NULL value.

**`usage.record(..., user_id=None)`** (usage.py:33-50): the new parameter is keyword-only with default None, so every existing caller still writes NULL.
- **Normal:** a call with `user_id=7, purpose="writing"` stores a `Run` row with `user_id=7`. `started_at` defaults to `db.now`, the time the row is written.
- **Error:** with `ok=False, error="boom"`, the row is still written with its tokens. `router.is_limit_error("boom")` is false, so no model gets marked as limited.

**`user_tokens_since`** (usage.py:84-91): it sums input plus output tokens filtered by `Run.user_id == user_id` and `started_at >= since`, and by `purpose` when one is given. It never filters on `ok`.
- **Normal:** 40 tokens recorded means it returns 40.
- **Error:** a failed run with 30 + 20 tokens counts 50, and a failed run with 0/0 adds 0, as §7 asks.
- **Isolation:** `user_id = :u` never matches NULL rows, by SQL NULL semantics, so agent runs are left out. One user's rows never count for another.
- `test_users_are_isolated_and_agent_runs_never_count` checks both.

**`user_cap` and `CapReached`** (usage.py:94-120):
- **Cap 0 means unlimited:** usage.py:109-112 reads the setting and, when it is 0, yields at once. No lock is taken and nothing is read from the database.
  - I checked that the setting reaches this code: `prefs.set_value` calls `load()` (prefs.py:255), which calls `config.set_overrides` (prefs.py:213), which sets `_settings = None` (config.py:260). The next `settings()` call therefore sees the new value.
- **Normal:** cap 100, 40 used, estimate 10: 40 + 10 < 100, so the request passes. The caller's `record` inside the block stores the row, and the next call reads it.
- **Concurrency edge:** both threads look up the same lock in `_user_locks`, guarded by `_user_locks_guard`.
  - With `estimate=60`, the first thread sees 0 + 60 < 100, passes, and records 60 while still holding the lock. The second thread then sees 60 + 60 ≥ 100 and gets `CapReached`.
  - With `estimate=0`, the second thread sees 60 < 100 and also passes, which ends at 120 of 100. The spec's §9.4 test (design.md:131) could not pass as written, so I accept the developer's change to `estimate=60` (tests/test_user_cap.py:69). See finding 1.
- **UTC midnight edge:** `day_start()` uses the `now` imported into `usage`, while `Run.started_at` uses `db.now`. The test patches only `usage.now` by +1 day, so the row from "yesterday" falls before the new `d0`. The count becomes 0 and the request passes.
  - `reset_at` is `(d0 + 1 day).isoformat()`, with a `+00:00` offset because `db.now` is UTC-aware. This is the same value as `summary()["resets"]["daily_at"]`, which §6 allows.
  - Minor: `started_at` is the time the row is written, not when the call started. A call that crosses midnight counts toward the new day. That is harmless.
- **Error (exception inside the guard):** the `raise` happens inside `with lock`, so the lock is released as the exception propagates. `CapReached` is raised inside the lock as well, and the lock is released then too. The test proves the release from a second thread with `join(5)`. That is stronger than §9.5's same-thread check, and a same-thread check would deadlock anyway if the lock were not released.
- **429 and data leaks:** there is no 429 response, because there is no endpoint.
  - `CapReached`'s message and fields (usage.py:96-97) hold only `used`, `cap` and `reset_at`. There is no model name, route, key hint or provider error.
  - The spec's response sketch (§8) carries the same three values.

**Settings** (prefs.py:26 and 120, config.py:48 and 184): the key is in `SCHEMA`, `LIMITS` (0 to 1e9), `Settings` and `load_settings()`. `prefs.describe()` keeps working.

**Does `/api/writing/assist` exist? No.** A case-insensitive grep for `writing/assist|/api/writing|writing_assist` finds only the spec, the ADR and the setting name. Nothing in api.py calls `user_cap`, so the cap is attached to nothing.

**Root cause:** this change lays the groundwork (per-user accounting, the guard and the setting). It does not yet remove the problem the goal names, because spec §8 and decision #79 deliberately leave the endpoint out. That fits this iteration's contract, but the goal's exposure stays open until a person decides #79.

## Checklist

| Check | Result |
|---|---|
| Contract satisfied | Yes for §2–§7 and §8 ("do not create the endpoint"). The §9.4 deviation is justified. |
| Interfaces compatible | Yes. `user_id` is keyword-only with default None, and the new functions are additions only. |
| Error handling | The lock is released on exceptions, failed runs are counted, and a 0-token failure counts 0. |
| Edge cases | Covered: at the cap, one token under it, estimate reaching the cap, UTC rollover, cap 0, NULL-user rows. Not covered: estimate ≥ cap (finding 3). |
| Tests sufficient | 8 tests, one per spec case plus the migration. There is no API test because there is no endpoint. Not run by me (see above). |
| Dependencies | None added; stdlib `threading` and `contextlib` only. |
| Security | No secret or model data in `CapReached`. The lock design becomes a DoS lever once attached (finding 2). |
| Performance | One indexed SUM per guarded call. `_user_locks` grows by one lock per user, which is negligible. |
| Docs | Docstrings are present. CLAUDE.md does not mention the new helper (finding 4). No threat-model change is needed, since there is no new network path. |

## Findings

FINDING: src/agent_company/usage.py:106-108 | The docstring promises "two requests of one user cannot both pass", but that only holds when `estimate` is at least the request's real token use. With the default `estimate=0` (also the spec's §6 default), a second parallel request passes and the user can go one full request past the cap. design.md:131 (test 4 with estimate 0) cannot pass for the same reason | Reword the docstring to "callers must pass the request's max_tokens as estimate", update design.md §6 and §9.4 to match, and require a non-zero estimate when the endpoint is attached.

FINDING: src/agent_company/usage.py:115-120 | The per-user lock is held across the whole model call (the `yield` is inside `with lock`). Once a sync FastAPI route uses it, each waiting request of the same user occupies a threadpool worker for the full model latency. One user firing many parallel calls can use up the pool and stall every other user's requests | When the guard is attached, use `lock.acquire(timeout=…)` or a non-blocking acquire, and answer 429 "request already running" right away.

FINDING: src/agent_company/usage.py:118 | When `estimate ≥ cap` (for example a cap of 1,000 and an endpoint `max_tokens` of 4,096), a user with 0 tokens used is refused on every call and never gets in | Clamp the estimate to the remaining budget's meaning, or state in the prefs.py:26 description and spec §8 that the cap must be above the endpoint's `max_tokens`, and add a test.

FINDING: CLAUDE.md (usage.py entry) | The layout lists only `usage.py` by name, so the next developer attaching the cap will not find `user_cap`, its estimate requirement or the single-process lock scope | Add one clause about `user_tokens_since`/`user_cap`, `Run.user_id` and the `writing_daily_tokens_per_user` setting to the `usage.py` entry.

FINDING: tests/test_user_cap.py (whole file) | This QA session has no test run of its own, because all three pytest invocations were refused. The evidence that the tests pass is the gate's own run of the impl acceptance commands, not this review | The gate should confirm `uv run pytest -q tests/test_user_cap.py` and `tests/test_budgets.py` from its own output before merging.

NEXT: A person decides #79 (docs/adr/0079-missing-writing-assist-endpoint.md): build `/api/writing/assist` with `usage.user_cap` (non-zero estimate, non-blocking lock, 429 with only used/cap/reset_at, `_audit`), cap `/summary` instead, or close the goal.
NEXT: The developer rewords the `user_cap` docstring and design.md §6/§9.4 about the estimate (finding 1), and adds the CLAUDE.md note (finding 4).

MESSAGE recommendation to developer: when attaching `usage.user_cap` to an endpoint, acquire the per-user lock non-blocking (or with a short timeout) and pass the request's max_tokens as `estimate`; see findings on usage.py:115 and :106.

```json
{"insights": [{"title": "A per-user lock held across a model call is only safe with a non-zero estimate and a non-blocking acquire", "description": "A check-then-record guard serialises a user's calls. With estimate 0 it still allows one request of overspend. With a blocking acquire in a sync FastAPI route, waiting requests tie up threadpool workers.", "evidence": ["src/agent_company/usage.py:115-120", "tests/test_user_cap.py:69", ".claude/specs/0065-daily-per-user-cap-for-the-writing-assis/design.md:131"], "expected_benefit": "Future quota guards get specified with an estimate requirement and a non-blocking lock from the start", "confidence": 0.7}]}
```

PROPOSAL: Non-blocking per-user guard before any endpoint uses `user_cap` :: the current blocking lock would let one user stall the API's worker pool once the cap is attached.

OUTCOME: APPROVED_WITH_COMMENTS. The diff matches §2–§7 and is backward compatible. The four code and doc findings are non-blocking because no endpoint exists yet, and the test evidence depends on the gate's run, not this session.

VERDICT: PASS
