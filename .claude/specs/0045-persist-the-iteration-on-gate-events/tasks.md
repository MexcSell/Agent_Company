# Tasks (iteration 2)



## 1. Index events.iteration on migrated databases, wrap the trace gates line, document the behaviour
- key: `fix_index_wrap` · role: developer · writes: yes

Iteration 1 is already on this branch (see `git show 51d93a8`): Event.iteration (db.py ~line 797, nullable, index=True) is written on every gate event in loop.py, and mission.py plus the trace read it through event_iteration(e). Do not rewrite that work. Fix the review findings only:
1. src/agent_company/db.py `_add_missing_columns`: when it adds a column through ALTER TABLE, it must also create that column's index if the column has `index=True`. The fix should work for any column, not only this one. After the ALTER, run `CREATE INDEX IF NOT EXISTS "ix_<table>_<column>" ON "<table>" ("<column>")`, using SQLAlchemy's default index naming so a fresh `create_all` and a migrated database end up with the same index name, `ix_events_iteration`. Alternatively, go through each table's `table.indexes` and call `idx.create(conn, checkfirst=True)` for indexes whose columns were just added. Keep the code short, and keep the comment density of the surrounding code.
2. src/agent_company/loop.py, the trace `gates` comprehension (~line 2078): wrap it after `"iteration": event_iteration(e),` so no line is longer than the lines around it (~120 chars).
3. tests/test_gate_iteration.py: add a test. Build an SQLite database whose `events` table has no `iteration` column, using raw SQL CREATE TABLE in a temp DATA_DIR, the way other migration tests in tests/ do; look for existing `_add_missing_columns` tests first. Then run the migration and assert that the column exists and that `PRAGMA index_list(events)` includes `ix_events_iteration`. Also check that older rows have iteration NULL and that event_iteration falls back to the message/data for them.
4. Write .claude/specs/0045-persist-the-iteration-on-gate-events/design.md, about 15 lines. Cover: the column and its meaning, who writes it, the readers (mission report, trace), the fallback for rows that predate the column, and the migration plus index.
5. Run `uv run pytest -q tests/test_gate_iteration.py tests/test_mission_report.py tests/test_loop.py` yourself and paste the summary line into your answer. Also run any test file that covers db migrations (grep tests/ for `_add_missing_columns`).

Acceptance: {"type": "command", "run": "uv run pytest -q tests/test_gate_iteration.py tests/test_mission_report.py tests/test_loop.py", "requirement_keys": ["S04-R-12"], "stage": "unit"}; {"type": "command", "run": "grep -q 'ix_events_iteration' tests/test_gate_iteration.py", "stage": "static"}; {"type": "file_exists", "path": ".claude/specs/0045-persist-the-iteration-on-gate-events/design.md"}

## 2. Review gate-event iteration persistence and the iteration-2 fixes against the goal
- key: `qa_review` · role: qa · writes: no · after: fix_index_wrap

Review the whole branch diff against main (`git diff main...HEAD -- src tests`). Focus on db.py (Event.iteration, `_add_missing_columns` index creation), loop.py (every place that logs kind="gate" passes iteration; the trace `gates` wrapping), mission.py (reads the column, falls back for NULL), and tests/test_gate_iteration.py. Trace the data flow for three cases: (a) normal: a gate in iteration 2 is stored with iteration=2 and shows as 2 in /api/goals/{id}/trace and in the mission report; (b) edge: a legacy row with iteration NULL is still attributed through the fallback, and a migrated DB has index ix_events_iteration; (c) error: the migration runs twice without failing. Grep for every `log_event(` with a gate kind and confirm none of them misses the iteration. The pytest command is a command acceptance of this task and the previous one, so the loop's gate runs it independently. You do not need to run Python yourself. If you cannot run it, write that down, but do not fail the review only for that reason; base the verdict on the code and the gate result. Output FINDING lines (file:line | problem | fix), an OUTCOME line, and end with "VERDICT: PASS" or "VERDICT: FAIL".

Acceptance: {"type": "command", "run": "uv run pytest -q tests/test_gate_iteration.py tests/test_mission_report.py tests/test_loop.py", "requirement_keys": ["S04-R-12"], "stage": "unit"}; {"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}; {"type": "contains", "value": "OUTCOME"}
