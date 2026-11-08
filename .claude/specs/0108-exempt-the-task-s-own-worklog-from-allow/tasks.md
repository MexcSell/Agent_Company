# Tasks (iteration 1)



## 1. Exempt a writing task's own WORKLOG.md from the gate's allowed-paths check, with regression tests
- key: `fix` · role: developer · writes: yes

Read each file before you change it. Allowed paths are checked in only one place: quality.evaluate -> checks.run_static -> checks.scope_violations.

1. src/agent_company/quality.py, function evaluate (around lines 201-272). Inside the open session that loads `tasks` (lines 205-222), build `worklogs`: a sorted list of every `c["path"]` taken from `(t.contract or {}).get("acceptance", [])` where `t.writes` is true, `c.get("type") == "file_exists"`, and `PurePosixPath(c["path"]).name == "WORKLOG.md"`. Strip a leading './' before you compare. Use the same comprehension style as `staged` at lines 218-221. At line 269, pass `exempt=adr.paths(goal_id) + worklogs` instead of `exempt=adr.paths(goal_id)`.
2. src/agent_company/checks.py:155, the docstring of scope_violations. It says `exempt` holds files the platform wrote. Change it to say that `exempt` holds the goal's ADR files (adr.paths) plus the WORKLOG.md a writing task's own acceptance asks for, and that a path is exempt only on an exact match. Do not change the function's logic. Exempt files still go through the secret, syntax and boundary checks, which run separately in run_static.
3. src/agent_company/loop.py:761, _board_plan. When goal.allowed_paths is set, add to the 'Allowed paths: ...' line that a WORKLOG.md your task's acceptance asks for is also allowed. Keep it to one short clause.
4. Write the new file tests/test_worklog_scope.py. It runs offline, and every test gets its own DATA_DIR from conftest. Copy the repo_with/wt_with_change helpers from tests/test_execution.py:11-28. Cover these cases:
  (a) checks.scope_violations with allowed=['stats.py'], changes to stats.py and WORKLOG.md, exempt=['WORKLOG.md'] -> [];
  (b) the same, plus a change to other.py -> exactly one violation, for other.py;
  (c) the same changes without exempt -> WORKLOG.md is reported as outside the allowed paths;
  (d) through quality.evaluate: a goal with allowed_paths=['stats.py'] on a scratch repo. Look at how tests/test_knowledge.py:22 and :367 create and run goals with loop.create_goal(..., repo_path=..., quality_commands=['test -f WORKLOG.md']) on the offline simulator. The simulator's build task requires file_exists WORKLOG.md (executors.py:757-759). Run the goal and assert that the gate's 'scope' check passed. If running the whole goal is impractical, create the Goal and a done writing Task whose contract acceptance is [{'type':'file_exists','path':'WORKLOG.md'}], commit the WORKLOG.md and stats.py changes in its worktree, call quality.evaluate(goal_id, iteration), and assert that the 'scope' row passed;
  (e) the same as (d), but the task's file_exists names 'src/other.py' and the change touches it -> the 'scope' row fails. Other files are not exempt.
  (f) the same as (d), but the WORKLOG.md condition belongs to a task with writes=False -> the 'scope' row fails.
5. Run `python3 -I /home/luca/Agent_Company/src/agent_company/edit_check.py <changed .py files>`. Then run, one after the other (there is no xdist): `uv run pytest -q tests/test_worklog_scope.py tests/test_execution.py`, then `uv run pytest -q`. In your answer, report the summary lines and any failure that was there before your change. Do not write a WORKLOG.md for this goal.

Acceptance: {"type": "file_exists", "path": "tests/test_worklog_scope.py"}; {"type": "command", "run": "uv run pytest -q tests/test_worklog_scope.py tests/test_execution.py", "stage": "unit"}; {"type": "command", "run": "grep -q 'WORKLOG.md' src/agent_company/quality.py"}

## 2. Review the worklog exemption: logic, bypass risk and tests
- key: `qa_review` · role: qa · writes: no · after: fix

Review the diff on this branch against main (`git diff main...HEAD -- src tests`). Check the data flow in src/agent_company/quality.py evaluate -> checks.run_static -> checks.scope_violations for three cases. Normal: a goal limited to one file, whose writing task's file_exists WORKLOG.md makes the 'scope' check pass. Edge: a WORKLOG.md in a subdirectory or written as './WORKLOG.md', a task with writes=False, and a task from an earlier iteration. Error or bypass: a file_exists condition on any other path, such as src/other.py or NOTES.md, must not widen allowed_paths, and a disallowed file must still fail. Confirm that exempt files still go through the secret, syntax and boundary checks. Confirm that the `exempt` docstring in checks.py and the 'Allowed paths' line in loop._board_plan match what the code does, and that tests/test_worklog_scope.py tests the wiring through quality.evaluate and not only scope_violations. Run `uv run pytest -q tests/test_worklog_scope.py tests/test_execution.py` yourself. Write one FINDING line per defect, with file:line, what is wrong and the scenario. Then write an OUTCOME line and end with 'VERDICT: PASS' or 'VERDICT: FAIL'.

Acceptance: {"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}; {"type": "contains", "value": "OUTCOME"}; {"type": "min_length", "value": 400}
