# Tasks (iteration 2)



## 1. Make label-count reads fail-open, skip storing for active goals, fix the project-message test race
- key: `fix_label_cache` · role: developer · writes: yes

Work in this worktree. Read each file before you change it.

1. `src/agent_company/mission.py`: `label_counts(goal_id)` must never fail because the store failed. Split `_store` into computing the counts (`report(goal_id)["tasks"]["labels"]` → `{k: int(...) for k in LABELS}`) and writing them. In `label_counts`, wrap only the write in `try/except Exception`. On failure, `log_event("error", ...)` (itself guarded by try/except, as in `store_label_counts`) and return the computed counts. Missing goal → `KeyError`, active goal → `ValueError` stay as they are.
2. `_store` / the write: inside the write session, re-read the goal (`s.get(Goal, goal_id)`) and write nothing when `g is None` or `g.status in workflow.ACTIVE`, so a goal resumed between `_update_goal` and the store never gets counts. Return the computed counts either way.
3. `src/agent_company/api.py` around line 2366 (`/api/simple/overview` delivered cards): change `except (KeyError, ValueError)` to `except Exception:  # noqa: BLE001 - one goal's counts never break the overview` → `labels = {}`.
4. `rca.evidence` already catches `Exception`. Leave it alone.
5. `tests/test_projects.py::test_project_api_flow` failed in the full suite. `wait(gid)` returns once the status is final, but `loop._chat_outcome` posts the goal's outcome message ('Goal #N: ...', kind result/error) after that, so it can arrive after the test read `msgs[-1]`. Fix the test, not production timing: add a helper in tests/test_projects.py that, after `wait(...)`, polls (timeout 20 s, 0.05 s sleep) until a ProjectMessage / project message for that goal whose content starts with `f"Goal #{gid}:"` exists. Use it in `test_project_api_flow` (and in any other test in that file with the same `wait` → read-last-message pattern). Check how `projects.post_for_goal` stores messages (model and columns) before you query.
6. `tests/test_label_cache.py`: add tests for (a) `label_counts` on a finished goal without stored counts, where the write raises (monkeypatch the write helper, or make `session` raise on the second use): it returns the correct counts and does not raise; (b) `/api/simple/overview` (TestClient, as tests/test_simple_mode.py does) returns 200 with `labels == {}` for a delivered goal when `mission.label_counts` is monkeypatched to raise `RuntimeError("database is locked")`; (c) the store is skipped when the goal's status is in ACTIVE at write time: set the goal to `running` and call `_store`/`store_label_counts`, and `Goal.label_counts` stays `{}`.
7. Update the one-line `label_counts` note in CLAUDE.md only if the behaviour description changes (e.g. 'a failed store still returns the counts'). Keep the edit minimal.

Run the commands in the acceptance conditions and paste their summary lines in your answer.

Acceptance: {"type": "command", "run": "uv run pytest -q tests/test_label_cache.py tests/test_mission_report.py tests/test_simple_mode.py tests/test_rca.py", "requirement_keys": ["S07-R-02"], "stage": "unit"}; {"type": "command", "run": "uv run pytest -q tests/test_projects.py", "stage": "integration"}; {"type": "command", "run": "grep -n 'except Exception' src/agent_company/api.py | grep -q 'one goal'", "stage": "static"}; {"type": "command", "run": "grep -n 'workflow.ACTIVE' src/agent_company/mission.py | wc -l | awk '$1>=2{exit 0} {exit 1}'", "stage": "static"}

## 2. Review the label-count cache against the goal and the iteration-1 findings
- key: `review_label_cache` · role: qa · writes: no · after: fix_label_cache

Review the branch diff against b06b418 (`git diff b06b418 -- src tests CLAUDE.md`). Check every iteration-1 finding: (1) `/api/simple/overview` cannot return a 500 when a label-count store or read fails, and `mission.label_counts` returns the computed counts when the write fails; (2) `_store` checks the status inside the write session and skips active goals; (3) `workflow` clears `Goal.label_counts` on any move into ACTIVE, and a reopened then finished goal gets fresh counts from `loop`'s `store_label_counts`; (4) test_project_api_flow now waits for the outcome message, and the test change does not hide a real defect: confirm by reading `loop._chat_outcome` that the message is posted after the final status, as before this goal. Trace the data flow for the normal case (goal finishes → stored → overview/RCA read the stored dict, no `report()` call), an edge case (older goal without counts → computed once, then stored) and an error case (write raises → counts returned, overview 200). Run `uv run pytest -q tests/test_label_cache.py tests/test_mission_report.py tests/test_simple_mode.py tests/test_rca.py tests/test_projects.py` and quote its summary line. If the environment refuses the command, write 'BLOCKER: environment' and do not count that as a code defect. List FINDING lines (file:line | defect | fix), then OUTCOME and 'VERDICT: PASS' or 'VERDICT: FAIL'.

Acceptance: {"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}; {"type": "contains", "value": "OUTCOME"}; {"type": "min_length", "value": 400}

## 3. Run the full test suite after the change
- key: `full_suite` · role: qa · writes: no · after: fix_label_cache

Run `uv run pytest -q` in this worktree and quote the final summary line. For every failure, give its first assertion line. Say whether the test touches code this goal changed (mission.label_counts, store_label_counts, the overview, rca.evidence, tests/test_projects.py), and whether it also fails on base b06b418 (`git stash` is forbidden; use `git worktree add /tmp/<unique> b06b418` only if you need it, then remove it). End with 'VERDICT: PASS' when everything passes, otherwise 'VERDICT: FAIL'.

Acceptance: {"type": "command", "run": "uv run pytest -q", "stage": "integration"}; {"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}
