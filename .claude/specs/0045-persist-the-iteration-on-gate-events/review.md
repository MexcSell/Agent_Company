# Review (iteration 3)

## Review gate-event iteration persistence against the goal and the design (qa, done)

## Logic verification

Python was refused again in this session. I ran `uv run pytest -q tests/test_gate_iteration.py tests/test_mission_report.py tests/test_loop.py` and got "This command requires approval". Everything below comes from reading the code. The gate runs the pytest acceptance command itself; the developer reported `35 passed` for it, and I have not confirmed that myself.

**The branch diff (`main...HEAD -- src tests`) touches `db.py`, `loop.py`, `mission.py` and `tests/test_gate_iteration.py`.**

**(a) Normal case: a gate event in iteration 2**
- **Write (loop.py:1443-1445):** the call is `log_event("gate", "iteration 2: quality gate …", goal_id=…, iteration=2, passed, checks, commit)`.
- **Storage (db.py:1594-1595):** `log_event` builds `Event(..., data=data, iteration=_as_int(data.get("iteration")))`. `_as_int(2)` returns 2, so `events.iteration` is 2, and `data["iteration"]` is still 2.
- **Trace (loop.py:2078-2082):** `GET /api/goals/{id}/trace` returns `gates[].iteration` from `event_iteration(e)`. The column is set, so it returns 2.
- **Mission report (mission.py:38-45):** `_gates` keys the checks by `event_iteration(e)`, which is 2. So the report places them in iteration 2.
- **Root cause:** fixed. The iteration is now stored as a structured column, and message parsing remains only as a fallback for legacy rows.

**(b) Edge cases**
- **Legacy row, column NULL, iteration in `data`:** `event_iteration` (db.py:816-824) returns `_as_int(data["iteration"])`.
- **Legacy row, column NULL, iteration only in the message:** the `_ITER` regex `iteration (\d+):` matches messages like "iteration 4: quality gate", so the result is 4. This regex used to live in mission.py, so the behaviour is unchanged.
- **No iteration anywhere:** `event_iteration` returns `None`. `_gates` then hits `continue` (mission.py:43-44) and nothing raises. The trace keeps the entry with `"iteration": None`, which the design documents at design.md:33.
- **Migrated database:**
  - `_add_missing_columns` (db.py:1508-1522) runs `ALTER TABLE "events" ADD COLUMN "iteration" INTEGER` with no DDL default, because the column is nullable with no default.
  - It records `added = {"iteration"}`.
  - It then creates every index in `table.indexes` that covers an added column, which is `ix_events_iteration` (SQLAlchemy's `ix_<table>_<column>` naming).
  - The second loop skips the column because it is nullable, so there is no backfill.
  - A fresh `create_all` produces the same index name. Both cases are tested at tests/test_gate_iteration.py:102 and :112-115.

**(c) Error case: running `_add_missing_columns` twice**
- `insp = inspect(eng)` is created on each call, so the second run sees `iteration` in `have`.
- As a result `added` is empty, no `ALTER` runs and no index is created. Even if an index were attempted, `checkfirst=True` would skip it because it already exists.
- `Index.create(bind, checkfirst=...)` exists in the locked SQLAlchemy version, 2.1.7 (uv.lock:2483).
- tests/test_gate_iteration.py:98-99 calls the function twice.

**Every gate writer passes an iteration.** I grepped `src/` for `log_event("gate"` and for `kind="gate"`:
- loop.py:1351-1352, the baseline: `iteration=0`.
- loop.py:1443-1445, the per-iteration gate: `iteration=iteration`.
- loop.py:1826, `_keep_best`: `iteration=last["iteration"]` and `kept_iteration=best["iteration"]`. `last` and `best` are `g.history` entries that this function already indexes, so this adds no new KeyError risk.

No other code writes gate events.

**Other consumers are unaffected:**
- The `_keep_best` message is byte-identical, so `performance.py:422`, which matches `Event.message.like("Kept iteration %")`, still counts these events.
- `data["iteration"]` is still written.

**The design document matches the code.** I checked these claims in design.md against the code:
- the column definition and `index=True` (db.py:797);
- `_as_int` and `event_iteration`, including the order of the fallbacks;
- the three writers;
- `init` at db.py:1490-1491;
- index creation at db.py:1519-1522;
- the trace at loop.py:2078;
- the tests listed at :34-:112.

All of them are accurate. The one problem is that design.md:1 begins with a chat preamble ("I've read the branch diff…") before the `# Design` heading.

**Files flagged by the test strategy (db.py, loop.py):** I checked both explicitly, as traced above. The diff in them contains only the changes described here.

## Checklist
- **Contract satisfied:** yes. The column exists and is migrated with its index, all three writers set it, both readers use `event_iteration`, and legacy rows still resolve.
- **Interfaces compatible:** yes. `log_event`'s signature is unchanged, trace entries only gain a key, and the message formats are unchanged.
- **Error handling:** `_as_int` catches `TypeError` and `ValueError` and rejects bools. A `None` iteration is skipped without an exception.
- **Edge cases:** covered: NULL column, data fallback, message fallback, no iteration anywhere, a repeated migration and a fresh database. One small gap: `_as_int(2.5)` silently returns 2, but no caller passes a float.
- **Tests sufficient:** yes, 9 cases in tests/test_gate_iteration.py covering all three requested flows. I could not run them myself.
- **Dependencies:** none added.
- **Security:** nothing changes. The DDL uses table and column names from the model metadata, not from user input.
- **Performance:** migrated and fresh databases now get the same index.
- **Docs updated:** design.md is present and accurate.

FINDING: .claude/specs/0045-persist-the-iteration-on-gate-events/design.md:1 | The design document opens with a chat preamble ("I've read the branch diff… Here is the design document.") before its `# Design` heading, so readers of the spec folder see agent chatter; this is cosmetic and does not justify FAIL | Delete line 1 so the file starts at `# Design: persist the iteration on gate events`
FINDING: src/agent_company/db.py:806-811 | `_as_int` truncates a float such as `2.5` to 2 without an error; no current caller passes a float, so nothing breaks today, and this is a comment rather than a defect | Optionally accept only `int`, or a `str` that passes `isdigit()`

I could not run Python in this session: the pytest command needed approval. The gate runs that acceptance command independently. Per this task, that is not grounds for FAIL.

NEXT: The gate runs `uv run pytest -q tests/test_gate_iteration.py tests/test_mission_report.py tests/test_loop.py`, and its summary line confirms the approval.
MESSAGE fact to developer: My review found no defects against S04-R-12. The only fix I suggest is removing the chat preamble at design.md:1.

OUTCOME: APPROVED_WITH_COMMENTS

VERDICT: PASS
