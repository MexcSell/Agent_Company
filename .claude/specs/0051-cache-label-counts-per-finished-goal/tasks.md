# Tasks (iteration 1)



## 1. Store label counts per finished goal; read them in the Simple overview and RCA
- key: `impl_label_cache` · role: developer · writes: yes

Read src/agent_company/mission.py (label, report), the simple_overview endpoint in src/agent_company/api.py (around line 2314-2372), rca.evidence in src/agent_company/rca.py:88, workflow.set_status in src/agent_company/workflow.py:99 and the end of loop.run_goal around src/agent_company/loop.py:1873 first.

1. db.py: add `label_counts: Mapped[dict[str, Any]] = mapped_column(default=dict)` to Goal, with a short comment (`# cached mission.report labels of a finished goal; empty = not cached`). _add_missing_columns must migrate existing databases. Check that it does, and add whatever is needed for a JSON column if not.

2. mission.py: add `label_counts(goal_id: int) -> dict[str, int]`.
   - Unknown goal: KeyError. Goal in workflow.ACTIVE: ValueError, as report() does.
   - If Goal.label_counts is non-empty, return it without calling report().
   - Otherwise compute `report(goal_id)["tasks"]["labels"]` and store it in a new session() opened after report() has returned. Never write from inside another open session, because SQLite allows one writer. Then return it.
   - The stored dict always has the keys verified, partial and failed, so a goal with zero tasks stores {verified:0, partial:0, failed:0}. That is non-empty, so it is cached too.
   - Add `store_label_counts(goal_id)`, which recomputes and overwrites the counts. It is fail-open: log_event an error on an exception and never raise.

3. workflow.set_status: when `to` is in ACTIVE and the goal's cur status is not, set `g.label_counts = {}` in the same session, so a resumed or reopened goal is recomputed.

4. loop.py: when a goal finishes, call `mission.store_label_counts(goal_id)` right before `rca.after_goal(goal_id, status)` at ~line 1873, outside any open session. A failure must not change the goal's outcome.

5. api.py simple_overview: replace `mission.report(gid)["tasks"]["labels"]` with `mission.label_counts(gid)`, keeping the `except (KeyError, ValueError): labels = {}` fallback. The JSON shape of `delivered[].labels` must stay identical.

6. rca.evidence: for a goal that is not ACTIVE, append ` (task labels: verified V, partial P, failed F)` to the existing `goal:{gid}` line, using mission.label_counts. Wrap it in try/except so that missing counts never break the evidence pack. Do not call it inside the open session; collect the counts before `with session() as s:` or after it. Avoid recursion: report() calls rca.list_analyses, not evidence, so this is safe, but do not make report() call evidence.

7. tests/test_label_cache.py (offline, own DATA_DIR like the other tests; reuse the fixtures and helpers of tests/test_mission_report.py and tests/test_simple_mode.py). Cover:
   - (a) after a goal finishes, Goal.label_counts equals report()["tasks"]["labels"];
   - (b) the stored counts survive a new session/re-read from the DB;
   - (c) an older finished goal with label_counts={} is filled on the first label_counts() call, and the second call does not call report() (monkeypatch mission.report to raise and count calls);
   - (d) GET /api/simple/overview called twice for a finished goal with stored counts does not call mission.report, and its delivered labels match the report's;
   - (e) moving the goal back to an ACTIVE status clears the cache, and label_counts raises ValueError while it is active;
   - (f) an unknown goal raises KeyError;
   - (g) rca.evidence text contains `task labels: verified` for a finished goal.

8. Add a short line to CLAUDE.md's mission.py description: `label_counts` cached per finished goal in `Goal.label_counts`, read by the Simple overview and RCA.

Run `uv run pytest -q tests/test_label_cache.py tests/test_mission_report.py tests/test_simple_mode.py tests/test_rca.py tests/test_workflow*.py` before handing back.

Acceptance: {"type": "file_exists", "path": "tests/test_label_cache.py"}; {"type": "command", "run": "grep -q 'label_counts' src/agent_company/db.py && grep -q 'def label_counts' src/agent_company/mission.py && grep -q 'label_counts' src/agent_company/rca.py"}; {"type": "command", "run": "! grep -n 'mission.report(gid)' src/agent_company/api.py"}; {"type": "command", "run": "uv run pytest -q tests/test_label_cache.py", "requirement_keys": ["S07-R-02"], "stage": "unit"}; {"type": "command", "run": "uv run pytest -q tests/test_mission_report.py tests/test_simple_mode.py tests/test_rca.py", "requirement_keys": ["S07-R-02"], "stage": "integration"}

## 2. Review the label-count cache against the goal
- key: `qa_review` · role: qa · writes: no · after: impl_label_cache

Review the diff of impl_label_cache (git diff main...HEAD) independently. Trace the data flow of three cases and report them:
- normal: a goal finishes, the counts are stored before rca.after_goal, and the Simple overview reads them without calling mission.report;
- edge: an older finished goal with label_counts={} or a goal with zero tasks is filled once and then served from the cache, and a goal that moves back to an ACTIVE status (resume) has its cache cleared;
- error: an unknown goal or an active goal falls back to labels={} in the overview, and a failing store_label_counts leaves the goal's outcome and the RCA evidence pack unaffected.
Also check that no DB write opens a second connection inside an open session() (CLAUDE.md rule), that the delivered[].labels JSON shape is unchanged, and that the db.py column migrates on an existing database. Run `uv run pytest -q tests/test_label_cache.py tests/test_mission_report.py tests/test_simple_mode.py tests/test_rca.py` and quote the summary line. Report each issue as a `FINDING:` line with file:line, then an `OUTCOME:` line, and end with `VERDICT: PASS` or `VERDICT: FAIL`. If the environment blocks you (permissions, read-only cache), say `OUTCOME: ESCALATED` with `BLOCKER: environment` instead of failing the code (K293).

Acceptance: {"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}; {"type": "contains", "value": "OUTCOME"}; {"type": "min_length", "value": 400}

## 3. Run the full test suite after the change
- key: `qa_full_suite` · role: qa · writes: no · after: impl_label_cache

Run `uv run pytest -q` in the worktree and report the summary line plus any failures. For each failure, say whether it is related to Goal.label_counts / mission.label_counts / workflow.set_status / simple_overview / rca.evidence, or whether it also fails on main (check with `git stash`-free means: compare against `git show main:<file>` or re-run the single test). End with `VERDICT: PASS` when there is no failure caused by this goal, otherwise `VERDICT: FAIL`.

Acceptance: {"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}; {"type": "command", "run": "uv run pytest -q", "stage": "integration"}
