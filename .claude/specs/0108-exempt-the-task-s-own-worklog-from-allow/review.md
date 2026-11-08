# Review (iteration 1)

## Review the worklog exemption: logic, bypass risk and tests (qa, done)

I found two defects that need a fix, so this is **VERDICT: FAIL**. The exemption works for a goal's first iteration but breaks from the second iteration on. The edit to an existing test would also trip this goal's own boundary check. I ran `uv run pytest -q tests/test_worklog_scope.py tests/test_execution.py`: **32 passed in 11.96s**.

## Logic verification

**`quality.evaluate`, lines 222-227 and 275.** It takes the current iteration's tasks (205-206). It keeps the `file_exists` paths of tasks that write, removes a leading `./`, and keeps a path only if its file name is `WORKLOG.md`. Those paths are added to `adr.paths(goal_id)` and passed to `run_static`, which passes them to `scope_violations` as an exact-match set.

- **Normal case:** a goal limited to `stats.py`, with the build task requiring `WORKLOG.md`. `worklogs` is `["WORKLOG.md"]`, the changed `WORKLOG.md` is skipped, and the scope check passes. Test (d) shows this.
- **Edge cases:**
  - `./WORKLOG.md` becomes `WORKLOG.md` and is exempt (tested).
  - `docs/WORKLOG.md` is exempt only if the acceptance names exactly `docs/WORKLOG.md`. If it says `WORKLOG.md` but the file is written to `docs/`, it is not exempt, so a mistake leads to a failure rather than a pass. Neither case is tested.
  - A task with `writes=False` exempts nothing (tested).
  - **A task from an earlier iteration exempts nothing, and that is a bug.** `tasks` holds only `Task.iteration == iteration`. But the scope check diffs `base...HEAD` across all iterations, and the worktree is not reset between iterations (`loop.py` only resets to a checkpoint after a restart). Say iteration 1's build task writes `WORKLOG.md` and the goal goes to iteration 2. If the re-plan has a writing "fix" task without a `WORKLOG.md` condition (as this goal's own plan has), `WORKLOG.md` is reported as outside the allowed paths again. So for goals that need more than one iteration, the fix hides the problem in iteration 1 instead of removing its cause.
- **Error and bypass cases:** a `file_exists` on `src/other.py` or `NOTES.md` is dropped by the file-name filter, so it does not widen the allowed paths. A disallowed file still fails: test (e) at the gate level, test (b) at the unit level. A path such as `src/../WORKLOG.md` or `.//WORKLOG.md` does not match git's normalized path, so it is not exempt.

**`checks.run_static` / `scope_violations`.** Only the docstring changed. `scan_secrets(worktree, base)`, `syntax_errors(changed)` and `boundary_violations` all work on the whole change and never read `exempt`. So exempt files do still go through the secret, syntax and boundary checks, as the docstring says. "Exact match" is also accurate (set membership).

**`loop._board_plan`.** It only adds a sentence to the prompt. The wording "your task's acceptance" is a bit narrow: the code exempts a `WORKLOG.md` asked for by *any* writing task in the *same iteration*.

## Checklist

- **Contract:** met for iteration 1 only (see the first finding).
- **Interfaces:** compatible. The `run_static` and `scope_violations` signatures are unchanged.
- **Error handling:** an odd or unknown path is not exempt, so it fails rather than slipping through.
- **Edge cases:** an earlier iteration's `WORKLOG.md` is not covered.
- **Tests:** (d)–(f) and the `./` case go through `quality.evaluate`, not only `scope_violations`, which is good. Missing: a subdirectory case, an earlier-iteration case, a `NOTES.md` case, and a check that an exempt `WORKLOG.md` with a credential still fails the secret scan.
- **Dependencies:** none added.
- **Security:** the exemption covers only files named `WORKLOG.md`, which are Markdown and never executed. Deleting an existing tracked `WORKLOG.md` (git status D) would also be exempt; the risk is low.
- **Performance:** negligible.
- **Docs:** the docstring is accurate. The `_board_plan` line is slightly too broad.

FINDING: src/agent_company/quality.py:222-227 (task query at 205-206) | `worklogs` is built only from the current iteration's tasks, but the scope check diffs `base...HEAD` across all iterations. Example: iteration 1's build task writes `WORKLOG.md`, the goal re-plans, and iteration 2's writing fix task has no `WORKLOG.md` condition. The scope check then fails with "WORKLOG.md: outside the allowed paths" again, so multi-iteration goals keep the original bug | Build `worklogs` with its own query over `Task.goal_id == goal_id, Task.iteration <= iteration`, and add a `quality.evaluate` test where the `WORKLOG.md` task is in iteration 1 and the gate runs for iteration 2.

FINDING: tests/test_execution.py:83-84 | The edit removes two `assert` lines and adds one, so the file loses one assertion overall. Unless this goal has the `skip_tests` permit, its own gate's boundary check (`checks.boundary_violations`, counted with `ASSERTION` per file) will report "tests/test_execution.py: 1 assertion(s) removed from tests". This file is also outside the paths the plan listed | Keep the assertion count, for example by adding `assert "WORKLOG.md" not in names["scope"]["detail"]` or asserting the status the run now ends with.

FINDING: tests/test_worklog_scope.py:62-85 | The tests miss edge cases the task lists: a `docs/WORKLOG.md` that is exempt only on an exact match, an earlier-iteration task, `NOTES.md`, and an exempt `WORKLOG.md` containing a credential that must still fail the "secret scan" row. A regression in any of these would go unnoticed | Add one `gate_scope`-style test for each.

FINDING: src/agent_company/loop.py:761-762 | "(and a WORKLOG.md your task's acceptance asks for)" suggests the exemption is per task and lasts across iterations. The code exempts any writing task's `WORKLOG.md`, and only in the current iteration | After fixing the first finding, word it as "(and a WORKLOG.md a writing task's acceptance asks for)".

OUTCOME: REVISION_REQUIRED

NEXT: Gather `worklogs` from every iteration up to the current one and add the iteration-2 test.
NEXT: Restore the assertion count in `tests/test_execution.py:test_gate_includes_static_checks`.

MESSAGE request to developer: In `src/agent_company/quality.py:222`, collect `worklogs` from tasks of all iterations up to the current one, and keep the assertion count in `tests/test_execution.py` from dropping, so the boundary check does not flag it.

VERDICT: FAIL
