# Tasks (iteration 1)



## 1. Reject foreign spec folders and loop-file conditions in plan validation; GM re-plans; regression tests
- key: `reject_spec_paths` · role: developer · writes: yes

Read src/agent_company/specs.py (lines 29-86) and src/agent_company/loop.py `_plan` (lines 546-640) before changing anything. Read tests/test_spec_paths.py as well.

1. specs.py, `check_plan(goal, plan) -> list[str]`: stop rewriting paths and validate them instead.
   - Normalise each file_exists path as the code does now: strip a leading `./` and the absolute worktree prefix. Return `<key>: <old> -> <new>` only for these harmless normalisations.
   - Collect every problem across the plan, then raise ONE `ValueError` that lists them all, one per line.
   - Problem (a): a file_exists path, or any `.claude/specs/<X>` occurrence in a command's `run`, where `<X>` is not `slug(goal.title, goal.id)`. Message: `task <key>: <path> is outside this goal's spec folder <relative_folder>/; put spec documents there or in the source tree`.
   - Make the spec-dir match also catch a folder with no trailing slash, e.g. `ls .claude/specs/foo` or `.claude/specs/foo'`. A glob such as `.claude/specs/*/x` counts as foreign.
   - Problem (b): a condition on a loop-written file (`LOOP_FILES`) under ANY `.claude/specs/<X>/` folder, own or foreign. This covers file_exists paths and command text. Keep the existing message `... is written by the loop itself, not by a task` and add `(the gate writes decisions.md)`.
   - A name such as `decisions-notes.md` stays allowed.
   - Update the docstring and the module comment to say the function rejects and no longer rewrites.

2. loop.py `_plan`:
   - Move the `specs.check_plan` call so it runs on the final plan, after `strategies.with_review` and `_risk_based`, still inside the same `try`. A ValueError then reaches the GM through the existing retry: `prompt += 'Your previous answer was not a valid plan: ...'`, then the standard plan.
   - Keep logging the normalisation list, with the wording 'Spec paths normalised: ...'.
   - Make sure the retry message contains the full error text, which includes the right folder. It must not be cut below the length the errors need; 1500 chars is fine.
   - Fallback branch: wrap `specs.check_plan(goal, plan)` like `_in_rooms`, so a ValueError raises `RuntimeError(f"no valid standard plan: {e}")`.
   - No task may be created or staffed from a rejected plan.

3. tests/test_spec_paths.py: replace the tests that assert a rewrite (`test_foreign_folder_rewritten`, `test_dot_slash_and_absolute_prefix_normalised`, `test_command_rewritten`, `test_planner_is_told_the_folder_and_its_plan_is_corrected`) with rejection tests. Keep the existing passing cases (own folder, non-spec paths, planner note). Add:
   - `test_decisions_condition_rejected`: file_exists `<OWN>/decisions.md`, a foreign `.claude/specs/req-001-x/decisions.md`, and a command `grep 'Iteration 1: PASS' .claude/specs/<OWN slug>/decisions.md`. Each raises ValueError.
   - `test_mismatched_slug_rejected`: for the GOAL with id 27, `.claude/specs/0019-req-001-merge-approval-bound-to-the-revi/spec.md` and `.claude/specs/req-001-reviewed-commit/spec.md` are rejected, in both file_exists and command form. For a goal with id=19 and title 'REQ-001: merge approval bound to the reviewed commit', check what `specs.slug` produces; the 0019 path must be accepted when it equals that slug.
   - `test_all_problems_reported`: two bad tasks give one error that names both keys.
   - `test_planner_gets_spec_path_error_and_replans` (loop level, monkeypatching `executors.OfflineExecutor._plan` as the old test did): the first answer has a decisions.md condition; the second answer uses the own folder. Assert that the second prompt contains 'written by the loop itself' and the goal's `relative_folder`. Assert that the stored Task contract uses the second plan's path, and that no 'using the standard plan' event exists.
   - `test_repeated_bad_plan_falls_back`: both answers name a foreign folder. Assert that the standard plan is used and that no Task row has a `.claude/specs/` path outside the own folder.

4. CLAUDE.md, `specs.py` entry: add a few words, e.g. '`check_plan` rejects acceptance paths outside the goal's spec folder and conditions on loop-written files; the GM re-plans'.

Run `uv run pytest -q tests/test_spec_paths.py tests/test_loop.py tests/test_contracts.py tests/test_strategies.py` and fix anything that fails.

Acceptance: {"type": "command", "run": "grep -n 'def test_decisions_condition_rejected' tests/test_spec_paths.py && grep -n 'def test_mismatched_slug_rejected' tests/test_spec_paths.py && grep -n 'def test_planner_gets_spec_path_error_and_replans' tests/test_spec_paths.py"}; {"type": "command", "run": "uv run pytest -q tests/test_spec_paths.py", "requirement_keys": ["S07-R-02", "G-R-01"], "stage": "unit"}; {"type": "command", "run": "uv run pytest -q tests/test_loop.py tests/test_contracts.py tests/test_strategies.py", "stage": "integration"}

## 2. QA review of the spec-path rejection and the GM re-plan
- key: `qa_review` · role: qa · writes: no · after: reject_spec_paths

Review the diff of `reject_spec_paths` in src/agent_company/specs.py, src/agent_company/loop.py and tests/test_spec_paths.py. Verify the data flow of three cases.

(1) Normal: a plan whose acceptance paths are only in the goal's spec folder or the source tree passes, and its tasks are staffed.

(2) Edge: `./`- and absolute-prefixed paths, a folder with no trailing slash in a command, a glob `.claude/specs/*/`, and `decisions-notes.md`, which must stay allowed. Check that the check runs after `with_review` and `_risk_based`.

(3) Error: a decisions.md condition or a goal #19-style mismatched slug (`req-001-reviewed-commit`). The ValueError must reach the GM's second prompt with the right folder. No task may be created before a valid plan exists. A plan that is bad twice must fall back to the standard plan, and a bad fallback must raise a clear RuntimeError.

Also confirm the tests fail without the fix, i.e. they would have caught K54. Confirm no other caller relied on the old rewrite behaviour (`grep -rn check_plan src tests`).

List FINDING lines as `file:line | problem | fix`. End with an OUTCOME line and `VERDICT: PASS` or `VERDICT: FAIL`.

Acceptance: {"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}; {"type": "contains", "value": "OUTCOME"}; {"type": "min_length", "value": 400}
