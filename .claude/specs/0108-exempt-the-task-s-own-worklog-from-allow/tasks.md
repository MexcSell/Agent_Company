# Tasks (iteration 2)



## 1. Exempt worklogs from every iteration up to the gate's, restore the lost assertion, add edge-case tests
- key: `fix_worklog_exemption` · role: developer · writes: yes

The branch already has the exemption from iteration 1. Read `git diff f5af0a5 -- src tests` first. Only change what is listed here.

CAUSE (FACT, src/agent_company/quality.py:205-206 and 222-227): `worklogs` comes from `tasks`, which holds only `Task.iteration == iteration`. `checks.scope_violations` diffs `base...HEAD`, and that includes the commits of every earlier iteration. A WORKLOG.md written by iteration 1's build task is therefore reported as 'outside the allowed paths' when the gate runs for iteration 2.

1. src/agent_company/quality.py: inside `evaluate`'s session, build `worklogs` with its own query, `s.scalars(select(Task).where(Task.goal_id == goal_id, Task.iteration <= iteration))`. Keep the rest of the existing rule as it is: only tasks with `t.writes`; only `file_exists` acceptance conditions whose path, after removeprefix('./'), has the basename exactly `WORKLOG.md`; the exempt entry is that exact path, with no globbing and no basename matching against changed files. Do not change `tasks` itself, because rows, staged commands and everything else must stay per iteration. Update the comment so it says earlier iterations count too. The literal text `Task.iteration <= iteration` must appear in quality.py.

2. src/agent_company/loop.py:761-762: change the hint to `(and a WORKLOG.md a writing task's acceptance asks for)`. Change nothing else in loop.py, which has failed checks in earlier goals.

3. tests/test_execution.py::test_gate_includes_static_checks: keep the existing asserts and add one more, `assert "WORKLOG.md" not in names["scope"]["detail"]`. The function must have 3 `assert` statements again, as on main. Do not remove or weaken an assertion anywhere.

4. tests/test_worklog_scope.py: generalise the `gate_scope` helper so it takes a list of tasks (iteration, writes, acceptance path) and the gate iteration (the Goal's iteration matches it). It may also return the whole check list. Add these tests with EXACTLY these names:
   - `test_gate_exempts_a_worklog_from_an_earlier_iteration`: the iteration-1 writing task asks for WORKLOG.md; the iteration-2 writing task asks only for stats.py; the gate runs `quality.evaluate(gid, 2)`. The scope row passes. This test must fail without fix 1 (verify by temporarily reverting it).
   - `test_gate_exempts_a_nested_worklog_only_on_an_exact_match`: acceptance `docs/WORKLOG.md` exempts a changed `docs/WORKLOG.md`. In a second case, acceptance `WORKLOG.md` does NOT exempt a changed `docs/WORKLOG.md`, and the detail names `docs/WORKLOG.md: outside the allowed paths`. Use two tmp sub-directories or two separate repos.
   - `test_gate_does_not_exempt_notes_md`: a writing task with acceptance file_exists `NOTES.md` and a changed NOTES.md. Scope fails, naming NOTES.md.
   - `test_exempt_worklog_still_fails_the_secret_scan`: an exempt WORKLOG.md that contains an AWS key. The scope row passes and the 'secret scan' row fails. IMPORTANT: build the key at runtime, e.g. `"AKIA" + "Q" * 16`. A literal matching `\b(AKIA|ASIA)[0-9A-Z]{16}\b` or `-----BEGIN ... PRIVATE KEY` in the test file would make this goal's own secret scan fail.
   Keep all existing tests and their assertions.

5. Run `uv run pytest -q tests/test_worklog_scope.py tests/test_execution.py tests/test_loop.py` serially, without -n (pytest-xdist is not installed). Read the summary line. Do not write a WORKLOG.md or any other file outside the in_scope paths.

Acceptance: {"type": "command", "run": "uv run pytest -q tests/test_worklog_scope.py tests/test_execution.py", "stage": "unit"}; {"type": "command", "run": "uv run pytest -q tests/test_worklog_scope.py::test_gate_exempts_a_worklog_from_an_earlier_iteration tests/test_worklog_scope.py::test_gate_exempts_a_nested_worklog_only_on_an_exact_match tests/test_worklog_scope.py::test_gate_does_not_exempt_notes_md tests/test_worklog_scope.py::test_exempt_worklog_still_fails_the_secret_scan", "stage": "unit"}; {"type": "command", "run": "uv run pytest -q tests/test_loop.py", "stage": "integration"}; {"type": "command", "run": "grep -q \"Task.iteration <= iteration\" src/agent_company/quality.py"}; {"type": "command", "run": "grep -q \"a WORKLOG.md a writing task's acceptance asks for\" src/agent_company/loop.py"}

## 2. Review the worklog exemption: cross-iteration logic, bypass risk, tests and assertion counts
- key: `qa_review` · role: qa · writes: no · after: fix_worklog_exemption

Review `git diff f5af0a5 -- src tests` on this branch against the four findings of iteration 1.
Verify the logic by tracing the data flow of three cases through quality.evaluate → checks.run_static → checks.scope_violations:
(a) normal: the build task's WORKLOG.md in the same iteration;
(b) edge: WORKLOG.md written in iteration 1 with the gate at iteration 2, and docs/WORKLOG.md against acceptance WORKLOG.md (no basename match);
(c) error and bypass: NOTES.md, any other file_exists path (e.g. src/other.py), a non-writing task's WORKLOG.md, a path like `../WORKLOG.md` or `x/../WORKLOG.md`, and a credential inside an exempt WORKLOG.md (the secret scan must still fail).
Also check that `tasks` is still per iteration everywhere else in evaluate; that tests/test_execution.py::test_gate_includes_static_checks has the same number of `assert` statements as on main (3); that no assertion was removed in any test file; that the tests contain no literal credential the secret scan would flag; and that the loop.py hint reads "(and a WORKLOG.md a writing task's acceptance asks for)".
List each problem as a `FINDING: <file>:<line> | <problem> | <fix>` line. End with an OUTCOME paragraph and then exactly `VERDICT: PASS` or `VERDICT: FAIL`.

Acceptance: {"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}; {"type": "contains", "value": "OUTCOME"}; {"type": "min_length", "value": 400}
