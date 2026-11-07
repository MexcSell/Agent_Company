# Design: Persist the iteration on gate events

## Write the design of gate-event iteration persistence as it is implemented (architect, done)

I've read the branch diff and the call sites. Running the tests needed approval that I don't have, so I'll say that in the document. Here is the design document.

# Design: persist the iteration on gate events

Covers the code already on `agents/goal-45` (`git diff main...HEAD -- src tests`: db.py, loop.py, mission.py, tests/test_gate_iteration.py). It proposes no new work.

## Column
- `Event.iteration` is an `Integer`, `nullable=True`, `index=True` (src/agent_company/db.py:797). SQLAlchemy names the index `ix_events_iteration`, which tests/test_gate_iteration.py:89-115 checks.
- It holds the goal iteration a gate event belongs to. `0` is the baseline, which runs before any change of the first iteration (src/agent_company/loop.py:1350-1352). Rows written before the column existed hold NULL (comment at db.py:796).

## Writers
- `log_event` sets `iteration=_as_int(data.get("iteration"))` from its `**data` kwargs. The value also stays in `data` (db.py:1585-1595).
- `_as_int` turns `7` and `"7"` into an int. Anything else gives `None`, and a `bool` is never an iteration (db.py:804-811).
- The three gate writers:
  - Baseline ("already failing before any change"): `iteration=0` (loop.py:1351-1352).
  - Per-iteration quality gate: `iteration=iteration`, plus `passed`, `checks` and `commit` (loop.py:1443-1445). This kwarg was already there before this branch. The new column only stores it.
  - `_keep_best`: `iteration=last["iteration"]` and `kept_iteration=best["iteration"]` (loop.py:1826). Its message stays the same because `performance.report` counts these events by "Kept iteration %" (comment at loop.py:1825).

## Readers
- `event_iteration(e)` is the single reader (db.py:814-822). loop.py imports it at loop.py:33 and mission.py at mission.py:21.
- **Mission report:** `mission._gates` keys each iteration's checks by `event_iteration(e)` (mission.py:35-46). `report` calls it at mission.py:87, and the endpoint is `GET /api/goals/{id}/report` (api.py:2240-2249).
- **Trace:** `loop.trace` adds `"iteration": event_iteration(e)` to each entry in `gates` (loop.py:2078). The endpoint is `GET /api/goals/{id}/trace` (api.py:2855-2862).
- Both readers only take gate events whose `data` has `checks` (mission.py:40-41, loop.py:2082). The baseline and `_keep_best` events carry no `checks`, so neither reader shows them. Their column value is stored but nothing reads it yet.

## Legacy rows
- `event_iteration` checks three places in order and returns the first value it finds (db.py:816-822):
  1. the column;
  2. `_as_int(data["iteration"])`;
  3. the regex `iteration (\d+):` on the message (`_ITER`, db.py:801; it was moved from mission.py);
  4. otherwise `None`.
- Old rows are not backfilled (test at tests/test_gate_iteration.py:104).
- `mission._gates` skips events with `None`, because the report needs an iteration (mission.py:43-44).
- The trace keeps such entries and shows `"iteration": None` (loop.py:2078-2082).
- `_keep_best`'s message "Kept iteration N (score …" has no `N:`, so the regex does not match it. That is harmless because the column is set (INFERENCE from db.py:801 and loop.py:1823).

## Migration
- `init` runs `create_all` and then `_add_missing_columns` (db.py:1490-1491).
- `_add_missing_columns` adds each missing column with `ALTER TABLE … ADD COLUMN` and records its name in `added` (db.py:1508-1518).
- It then creates every table index that covers a column it just added, with `idx.create(conn, checkfirst=True)` (db.py:1519-1522).
- So a migrated database and a fresh `create_all` both end up with `ix_events_iteration`. A second run adds no column and so creates no index, and `checkfirst` would skip an existing index anyway. Running it twice is harmless (tests/test_gate_iteration.py:100).

## Tests (tests/test_gate_iteration.py)
- `test_log_event_stores_iteration_column_and_data` (:34): `iteration=2` lands in the column and in `data`, and `event_iteration` returns 2.
- `test_log_event_without_iteration_leaves_column_null` (:42): an event logged without an iteration has a NULL column and no `data` key.
- `test_column_wins_over_data_and_message` (:49): the column (7) beats `data` (8), which beats the message (9).
- `test_old_row_falls_back_to_message_in_report_and_trace` (:57): a NULL row gets iteration 3 from its message in `_gates`, `mission.report` (task labelled failed) and `loop.trace`.
- `test_no_iteration_anywhere_is_none_and_skipped` (:72): with no iteration anywhere the result is `None`, `_gates` returns `{}`, and `report` raises no error.
- `test_trace_has_iteration_for_each_gate` (:81): the trace's `gates` list shows `[1, 2]`.
- `test_add_missing_columns_adds_iteration` (:89): on an old `events` table, migrating twice adds the column and `ix_events_iteration`, with no backfill, and the data and message fallbacks still work.
- `test_fresh_database_has_the_same_index_name` (:112): a fresh database has `ix_events_iteration`.
- These tests were not run for this document (the command needed approval). Their pass/fail state is unverified here.

## Requirements
- **S04-R-12** (execution trace with "gate results", docs/requirements/SPEC-04-orchestration.md:254-256): each trace gate now carries a structured iteration (loop.py:2078).
- **Related, touched only through the consumers:**
  - S09-R-05 and REQ-004: the mission report's verified/partial/failed labels per iteration (mission.py:35-46).
  - S08-R-01: `requirements.on_gate(goal_id, iteration, report)` sits beside the gate event (loop.py:1436). It is unchanged, and it does not read `Event.iteration`.

FINDING: The trace includes gate entries whose iteration is `None` (loop.py:2078-2082), while the mission report skips them (mission.py:43-44). This is consistent with "skipped where required", but consumers of the trace must handle `None`.
FINDING: Neither reader shows the baseline (`iteration=0`) or `_keep_best` gate events, because those have no `checks` (loop.py:2082, mission.py:40).
NEXT: QA runs `uv run pytest -q tests/test_gate_iteration.py` and records the summary line, since it was not run here.
MESSAGE request to qa: Run `uv run pytest -q tests/test_gate_iteration.py`, record the result, and review the branch against .claude/specs/0045-persist-the-iteration-on-gate-events/design.md.
