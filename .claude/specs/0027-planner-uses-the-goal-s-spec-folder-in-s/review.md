# Review (iteration 2)

## Review the spec-folder path fix (qa, done)

## Logic verification

**Did all three files change?** Yes. `git log --stat` shows `2b95865 spec_helpers` (specs.py +48, tests/test_spec_paths.py +73) and `58058d3 loop_wiring` (loop.py +4/−1, tests/test_spec_paths.py +29). A plain `git diff main --stat` lists 24 files, including deletions of `mission.py`, `simple.js` and two ADRs. Those come from `main` moving on after this branch was cut: it has the goal-21 merge `fd3e4ab`, which this branch doesn't. The branch's own changes are `git diff main...HEAD --name-status`:
```
A .claude/specs/0027-planner-uses-the-goal-s-spec-folder-in-s/{decisions,requirements,review,tasks}.md   (written by the loop)
M src/agent_company/loop.py
M src/agent_company/specs.py
A tests/test_spec_paths.py
```
Nothing outside the scope changed. The working tree is clean. Nothing was deleted or moved.

**How I got the evidence:** I did not run pytest. Every way I tried to call it (`uv run pytest …`, `.venv/bin/python -m pytest …`) stopped at a permission prompt that was never approved. The test results here are the developer's, from the handoff: `9 passed` for test_spec_paths.py and `39 passed` with test_loop/test_contracts/test_strategies. The gate's unit check runs `uv run pytest -q tests/test_spec_paths.py` again itself. Everything else below comes from reading the code.

### `specs.check_plan` (specs.py:55-86) wired at loop.py:584 and :609

**Normal case (a foreign folder):** The planner returns a read-only `spec` task with `file_exists .claude/specs/req-001-elsewhere/spec.md`.
1. `contracts.validate_plan` builds it (loop.py:583).
2. `check_plan` sees that `SPEC_DIR.match` hits and rewrites the path with `sub(count=1)` to `.claude/specs/0027-…/spec.md`. It changes `cond.path` in place, returns the rewrite, and loop.py:585 logs "Spec paths corrected: …".
3. The changed `plan` goes back to `run_goal`. There, `_persist` (loop.py:1374) stores the corrected contract, then `_spec_step(specs.write_plan, …)` (loop.py:1375) writes tasks.md with the corrected path. Both happen before any task runs.
4. When the task runs, `_save_answer` (loop.py:804-818) gets a `file_exists` path that ends in `.md`, lies inside the worktree and doesn't exist yet. It writes the answer there, so the condition holds and the file lands in the goal's own folder.

This removes the root cause from K54 and doesn't just hide it: the path is fixed in the contract itself, not in the check.

**Edge cases:**
- **Correct path:** `sub` returns the same string, nothing is reported (test_correct_path_unchanged).
- **Non-spec path** (`tests/x.py`, `WORKLOG.md`): `SPEC_DIR.match` misses and the code reaches `continue`, so it is left alone.
- **`./` prefix:** stripped (loop at :69-70).
- **Absolute path inside this worktree:** the worktree prefix is stripped (:67-68), and the stored path becomes relative, which `_save_answer` resolves against the root.
- **Command strings:** every occurrence is rewritten with `sub`, which isn't anchored, so `grep x .claude/specs/other/spec.md` gets the goal's folder.
- **Lookalike file names:** `decisions-notes.md` is accepted because of the `(?![\w.-])` lookahead and `fullmatch`.
- **Deeper paths:** `.claude/specs/a/b/x.md` becomes `<own>/b/x.md`.
- **Absolute path into another worktree:** left as it is, and `_save_answer` then skips it because it is outside the root. This is a rare edge.

**Error case (a loop file):** A condition on `.claude/specs/<X>/decisions.md`, `review.md` or another `LOOP_FILES` entry is first rewritten to the goal's folder, then `loop_file.fullmatch` raises `ValueError("task k: … is written by the loop itself, not by a task")`. That happens inside the `try` (:580). The `except (ValueError, …)` at :598 handles it:
- On attempt 0 with `res.ok`, the message is appended to the prompt and the plan goes back to the planner once.
- On attempt 1, the standard plan is used instead: `validate_plan(fallback_plan)`, then `check_plan` at :609, which is a no-op because the only `file_exists` in the fallback is `WORKLOG.md` (strategies.py:356, executors.py:752).

The changes made before the raise are thrown away with the rejected plan. This is exactly the K54 case where `decisions.md` failed three times in a row; it can no longer reach tasks.md.

### `specs.relative_folder` / `planner_note` (specs.py:43-52, loop.py:563)
- `relative_folder` returns `.claude/specs/{slug(goal.title, goal.id)}`. That is the same `slug()` call `folder()` makes at :40, and `test_relative_folder_matches_folder` checks both agree.
- `planner_note` puts `` `<folder>/` `` and every `LOOP_FILES` entry into the planner prompt at loop.py:563, before `PLAN_INSTRUCTIONS`.
- None of the three helpers creates, renames or deletes a path; they only build and rewrite strings. No folder is created, moved or deleted.

## Checklist
- **Contract satisfied:** Yes. Foreign folders are rewritten, loop files are rejected, and the prompt names the folder.
- **Interfaces compatible:** Yes. These are new functions, and `_plan` keeps its signature. The helpers are always called as `specs.planner_note`, so they don't clash with the `planner_note` in other modules.
- **Error handling:** The `ValueError` uses the existing retry-then-fallback route (see the error case above).
- **Edge cases:** Covered as listed above.
- **Tests:**
  - Seven unit tests cover the pure function.
  - The end-to-end test `test_planner_is_told_the_folder_and_its_plan_is_corrected` runs offline: it patches `OfflineExecutor._plan` and needs no network.
  - The tests would fail without the fix. Without `check_plan` you get `AttributeError`. Without the loop wiring, both the prompt assertion and the stored-contract assertion fail. The old `_save_answer` would just have written the stray `req-001-elsewhere/spec.md`, which is the K54 symptom.
  - Gap: the retry-then-fallback route is only tested by reading the code (see the findings).
- **Dependencies:** None.
- **Security:** Only paths inside the worktree are rewritten, and `_save_answer`'s root and `.git` guards are unchanged.
- **Performance:** Trivial.
- **Docs:** CLAUDE.md's `specs.py` entry doesn't mention `check_plan`/`planner_note` (minor).

FINDING: tests/test_spec_paths.py:56 | The error path (a loop-file condition, then the planner once more, then the standard plan) is only covered by a unit test that checks the raise. Nothing end-to-end proves that `_plan` asks the planner again and then falls back, so a later refactor of the `except` at loop.py:598 could break this unnoticed. | Add an end-to-end test like the existing one, where the patched `_plan` always returns `<own>/decisions.md`. Assert two planner prompts, the second containing "written by the loop itself", and a stored `spec` contract equal to the standard plan.
FINDING: src/agent_company/loop.py:584 | `check_plan` runs before `strategies.with_review` (:594) and `_risk_based` (:596). Today both add only `regex` conditions (strategies.py:313, loop.py:730), so nothing is missed yet. A future generated task with a `.claude/specs/` path would skip the check. | Move the call after `_risk_based`, still inside the `try`, or add a comment explaining the order.
FINDING: src/agent_company/loop.py:609 | The fallback `check_plan` call isn't guarded. If a fallback template ever gets a loop-file condition, `_plan` raises instead of degrading. It can't happen with today's `fallback_plan`, which only has WORKLOG.md. | Leave as is, or wrap it like `_in_rooms` at :610-613 so it raises a RuntimeError with a clear message.
FINDING: CLAUDE.md:1 | The `specs.py` entry in the layout section doesn't mention the K54 plan check or the planner note. | Add a few words, e.g. "`check_plan`: plans use only this goal's spec folder, never loop-written files".

OUTCOME: APPROVED_WITH_COMMENTS. The fix removes the K54 root cause. None of the four findings blocks it. I approved by reading the code; I didn't run the tests myself.

NEXT: Let the gate's `uv run pytest -q tests/test_spec_paths.py` stand as the run evidence, then add the end-to-end test for the error path.
MESSAGE recommendation to developer: In tests/test_spec_paths.py, add an end-to-end test for the loop-file case (the planner once more, then the standard plan), and consider calling `specs.check_plan` after `_risk_based` in loop.py `_plan`.
PROPOSAL: Stale spec.md satisfies file_exists :: From iteration 2 on, `write_outputs` (specs.py:124) has already written spec.md, and `_save_answer` skips files that exist, so a read-only task's `file_exists <own>/spec.md` passes on last iteration's content.

VERDICT: PASS
