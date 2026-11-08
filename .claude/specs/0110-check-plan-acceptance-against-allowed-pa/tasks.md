# Tasks (iteration 1)



## 1. Reject file_exists acceptance outside the goal's allowed paths at plan time
- key: `implement` · role: developer · writes: yes

Read src/agent_company/specs.py (check_plan), src/agent_company/checks.py (in_scope, scope_violations), src/agent_company/adr.py (FOLDER) and src/agent_company/loop.py lines 580-630 before changing anything.

1. In specs.check_plan(goal, plan, *, drop_out_of_scope: bool = False), run the new rule on each file_exists condition after the existing normalisation and spec-folder rewrite. The path at that point has the worktree prefix and leading './' removed and the spec folder rewritten. Read allowed = list(getattr(goal, 'allowed_paths', None) or []) because tests pass a SimpleNamespace that has no allowed_paths. If allowed is empty, skip the check. A path is out of scope when all of these are true:
(a) it does not start with '.claude/' and is not '.mcp.json'. These are the scope gate's own exemptions in checks.scope_violations.
(b) it does not start with adr.FOLDER + '/'. The platform writes ADR files there itself.
(c) not any(checks.in_scope(path, pat) for pat in allowed). Use that same function so the planner and the gate cannot disagree.
(d) when goal.worktree is set, (Path(goal.worktree) / path) does not exist. A file that already exists passes file_exists without any change.
Import checks and adr inside the function if a module-level import would create a cycle.

2. Collect every out-of-scope condition in the plan, not just the first. When drop_out_of_scope is False, raise one ValueError whose text names each task key and path, lists the allowed paths, and tells the planner to use a path inside them or a contains/regex/command condition instead. The loop already sends this text back to the planner on the first attempt. When drop_out_of_scope is True, remove those conditions from c.acceptance, add '<key>: dropped file_exists <path> (outside the allowed paths)' to the returned report, and do not raise. Keep the existing loop-file ValueError and the rewrite report unchanged.

3. In loop.py, change the fallback-plan call (currently a bare specs.check_plan(goal, plan) near line 618) to specs.check_plan(goal, plan, drop_out_of_scope=True). If it returns a report, log it with log_event('plan', ...). The fallback docs plan's file_exists WORKLOG.md must never raise outside the try block. Leave the first call at line 593 strict.

4. Add tests/test_plan_allowed_paths.py. Follow tests/test_spec_paths.py: use SimpleNamespace goals, contracts.Condition and TaskContract, and tmp_path as the worktree. Cover these cases:
- An allowed file such as src/x.py under 'src/*' passes.
- A directory pattern 'src/' or 'src' covering src/a/b.py passes.
- A disallowed new file such as docs/x.md under 'src/*' raises ValueError, and the message names the task key and path.
- The same path already existing in tmp_path passes.
- A .claude/specs/... answer path passes.
- A docs/adr/0001-x.md path passes.
- A goal with empty allowed_paths, and a goal without the attribute, both pass.
- A path with a './' prefix or an absolute worktree prefix is normalised before the check.
- Two bad conditions in two tasks are both named in one error.
- drop_out_of_scope=True removes the condition, reports it and does not raise.
- The existing loop-file error still raises.

5. In CLAUDE.md, add one short clause to the specs.py / loop.py description saying that plan acceptance file_exists paths outside allowed_paths are rejected (dropped in the fallback plan). Run python3 -I src/agent_company/edit_check.py on the changed .py files, then run the acceptance commands. Report the pytest summary line as evidence.

Acceptance: {"type": "file_exists", "path": "tests/test_plan_allowed_paths.py"}; {"type": "command", "run": "uv run pytest -q tests/test_plan_allowed_paths.py tests/test_spec_paths.py", "stage": "unit"}; {"type": "command", "run": "grep -q 'drop_out_of_scope' src/agent_company/specs.py && grep -q 'drop_out_of_scope=True' src/agent_company/loop.py", "stage": "static"}

## 2. Review plan acceptance scope check
- key: `qa_review` · role: qa · writes: no · after: implement

Review the diff of src/agent_company/specs.py, src/agent_company/loop.py, tests/test_plan_allowed_paths.py and CLAUDE.md against the base branch. Check the logic by tracing the data flow of three cases.
- Normal case: a model plan with a file_exists inside allowed_paths reaches the plan unchanged.
- Edge case: a file_exists outside allowed_paths that already exists in the worktree, a .claude/specs answer path, a docs/adr path, and a goal without allowed_paths are all accepted.
- Error case: a new file outside allowed_paths raises ValueError in check_plan at loop.py:593. The except branch then sends the message back to the planner once, and the fallback-plan call drops the condition instead of raising.
Confirm the matcher is checks.in_scope, the scope gate's own rule, so the planner and the gate agree. Confirm the exemptions match checks.scope_violations, and that the existing tests/test_spec_paths.py behaviour is unchanged. Run 'uv run pytest -q tests/test_plan_allowed_paths.py tests/test_spec_paths.py' if your tools allow it, and quote the summary line. Otherwise say it was not run. List each problem as a FINDING line with file:line. End with an OUTCOME line and 'VERDICT: PASS' or 'VERDICT: FAIL'.

Acceptance: {"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}; {"type": "contains", "value": "OUTCOME"}
