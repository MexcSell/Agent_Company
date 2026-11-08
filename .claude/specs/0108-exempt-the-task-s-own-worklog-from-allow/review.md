# Review (iteration 2)

## Review the worklog exemption: cross-iteration logic, bypass risk, tests and assertion counts (qa, done)

## Review of iteration 2: worklog exemption

**VERDICT: PASS.** All four findings from iteration 1 are fixed, and I found no new defects. I ran `uv run pytest -q tests/test_worklog_scope.py tests/test_execution.py` myself: **36 passed in 13.40s**.

## Logic verification

In `quality.evaluate` (`src/agent_company/quality.py:222-229`), `worklogs` now comes from its own query: `Task.goal_id == goal_id, Task.iteration <= iteration`. It keeps a path only when the task writes, the condition is `file_exists`, and the path's last part is `WORKLOG.md` after a leading `./` is removed. The list goes to `run_static` together with `adr.paths(goal_id)` (line 277), and `scope_violations` skips a changed file only if its path matches exactly.

**(a) Normal case: the build task's `WORKLOG.md` in the same iteration.**
- `worklogs` is `["WORKLOG.md"]`, so the changed `WORKLOG.md` is skipped and `stats.py` is in scope. The scope check passes.
- Tested by `test_gate_exempts_the_writing_tasks_worklog`, the `./` variant, and `test_execution.py::test_gate_includes_static_checks`, which runs the whole goal on the offline simulator.

**(b) Edge cases.**
- **Earlier iteration:** the gate diffs `base...HEAD`, so iteration 1's `WORKLOG.md` is still part of the change at iteration 2. The iteration-1 task is now found by `iteration <= 2`, so the file is exempt. `test_gate_exempts_a_worklog_from_an_earlier_iteration` covers this, and the developer reports it fails if `<=` is changed back to `==`. This fixes the cause found in iteration 1.
- **`docs/WORKLOG.md`:**
  - An acceptance path of `docs/WORKLOG.md` exempts exactly that file.
  - An acceptance path of `WORKLOG.md` puts only `"WORKLOG.md"` in the set, so a changed `docs/WORKLOG.md` is still reported.
  - `test_gate_exempts_a_nested_worklog_only_on_an_exact_match` tests both.

**(c) Error and bypass cases.**
- **`NOTES.md` or `src/other.py`:** the name filter drops them, so the scope check fails and names the file. Tested by `test_gate_does_not_exempt_notes_md` and `test_gate_does_not_exempt_other_file_exists_paths`.
- **A non-writing task's `WORKLOG.md`:** dropped by `if t.writes`. Tested.
- **`../WORKLOG.md` and `x/../WORKLOG.md`:** both names end in `WORKLOG.md`, so the literal strings go into the set. But `git diff --name-status` only ever reports normalized paths inside the repository, never with `..` in them. Neither string can match a changed file, so they exempt nothing and a stray `WORKLOG.md` is still reported. I traced this from the code; there is no test for it.
- **A credential inside an exempt `WORKLOG.md`:** `scan_secrets(worktree, base)` scans every added line and never reads `exempt`. The secret scan fails and names `WORKLOG.md`. Tested by `test_exempt_worklog_still_fails_the_secret_scan`.
  - The test builds the key at runtime (`"AKIA" + "Q" * 16`), so the test file contains nothing the AWS pattern `\b(AKIA|ASIA)[0-9A-Z]{16}\b` would match.
- **Syntax and boundary checks:** `syntax_errors(changed)` and `boundary_violations` also work on the whole change and never read `exempt`.

The change fixes the cause: the scope check compares every iteration's changes against the base, and the set of exempt files now spans the same iterations. It does not just hide the symptom.

## Checklist
- **Contract satisfied:** yes.
  - `tasks` is still loaded for the gated iteration only (lines 205-206).
  - `rows`, `staged`, `probation` and `reviewers` all still use `tasks`. Only `worklogs` uses the wider query.
- **Interfaces compatible:** yes. The `run_static` and `scope_violations` signatures are unchanged.
- **Error handling:** an unusual path is not exempt, so it fails rather than slipping through.
- **Edge cases:** covered as traced above.
- **Assertion counts:**
  - `test_gate_includes_static_checks` removes 2 `assert` lines and adds 2.
  - It has 3 asserts, the same as main: line 82, which is unchanged, plus the two new ones.
  - No other test file loses an assert line; `test_worklog_scope.py` is new.
  - So the boundary check that failed iteration 1 ("1 assertion(s) removed") has nothing to report.
- **Tests sufficient:** yes. Ten tests in `test_worklog_scope.py`, seven of which go through `quality.evaluate`.
- **Dependencies:** none added.
- **Security:** only exact paths that end in `WORKLOG.md` are exempt, and the secret, syntax and boundary checks still apply to them.
- **Performance:** one extra indexed query per gate, which is negligible.
- **Docs updated:**
  - The `loop.py:761-762` hint now reads `(and a WORKLOG.md a writing task's acceptance asks for)`, as requested.
  - The `checks.py` docstring matches the code.
  - The comment in `quality.py` explains why earlier iterations count.

One optional addition, not a defect: a test asserting that an acceptance path of `x/../WORKLOG.md` exempts nothing would pin down the behaviour traced above.

OUTCOME: APPROVED_WITH_COMMENTS. The exemption now covers a `WORKLOG.md` asked for in any iteration up to the gated one and only on an exact path match. `NOTES.md`, other `file_exists` paths, non-writing tasks and paths containing `..` all still fail the scope check. The secret, syntax and boundary checks still apply to an exempt file. `test_gate_includes_static_checks` keeps its 3 asserts. The tests go through `quality.evaluate`, and 36 of 36 pass.

NEXT: Merge after approval. Until then the gate runs main's `quality.py`, so this exemption only takes effect for goals gated after the merge.

MESSAGE done to developer: The iteration-2 fixes in `src/agent_company/quality.py`, `src/agent_company/loop.py`, `tests/test_execution.py` and `tests/test_worklog_scope.py` pass review, and the targeted tests pass (36 passed).

VERDICT: PASS
