# Review (iteration 3)

## Review save_answer, the answer commit and write_outputs against the four findings (qa, done)

I couldn't run pytest myself: the permission prompt refused it twice, both times as a bare `uv run pytest -q tests/test_spec_answer.py tests/test_spec_paths.py tests/test_loop.py`. The test evidence is therefore verify_fix's run, `61 passed in 17.81s` at HEAD `5f938ee` with a clean worktree. My own `git log` and `git status` show the same HEAD and a clean tree, so the code I reviewed is the code that was tested. The loop also runs those three files as this task's acceptance command.

## Logic verification

**`specs.save_answer` (specs.py:109-148)**
- **Normal case.** The task is a read-only architect task whose `file_exists` check names `.claude/specs/0033-let-specs-py-write-role-provided-spec-fi/spec.md`.
  - The path ends in `.md` and is not under `.git`.
  - It starts with `own` (the goal's own folder name plus `/`, so another goal's folder can't match it by prefix).
  - `spec.md` is not one of the files the loop writes itself (`LOOP_FILES`), and `writes=False`, so line 138 doesn't skip it.
  - `_write` writes the file, even over an old copy from an earlier iteration. That fixes the real cause of the failing checks (K336, and the old "skip if it exists" at main's `loop.py:825`), not just the symptom.
  - The function returns `[rel]`.
- **Edge case.** A writing task's run made `über.md` at its acceptance path.
  - `_checkpoint` lists changed paths with `-c core.quotepath=off` (loop.py:892), so `changed` holds the plain name.
  - `rel in changed` is true, so line 138 skips the file and the run's own file is kept.
  - `test_writing_run_keeps_its_own_non_ascii_file` checks this, including `git show` of the run's own text.
- **Error cases.**
  - **Target is a directory or can't be written:** `_write` raises `OSError`, lines 144-146 log it and skip the path, `saved` stays `[]`, nothing is committed, and the `file_exists` check reports the missing file (`test_unwritable_target_logged`).
  - **Other exceptions** (for example a symlink loop in `resolve()`): the broad `except` at loop.py:1023 logs them, and the task carries on to its acceptance check without crashing.
  - **Paths that must never be written:**
    - A path outside the worktree, including through a symlink, fails `root not in target.parents`, because the check runs on the resolved path.
    - `.git` is caught by the `parts` check.
    - Another goal's `.claude/specs/<X>` hits the `elif` at line 140.
    - The loop's own files are caught by the `LOOP_FILES` check at line 138.
    - When the checkpoint failed (`changed=None`), a writing task writes nothing, either in its own folder or anywhere else.
  - All of these paths are covered in tests/test_spec_answer.py.

**`loop._checkpoint(paths=…)` (loop.py:872-900)**
- **Normal case.** It runs `ensure_sandboxed`, then `git add -- paths`, then `git commit -- paths`. That commit contains only the saved file, and the goal's checkpoint moves to it. `test_read_only_answer_committed_on_its_own` checks that the commit's `diff-tree` is exactly `[path]` and that the next writing task's commit sits on top of it.
- **Error case.** On a `WorktreeError` the function now returns `("", None)`. The caller's `[0] or commit` keeps the previous commit, and the error is logged.
  - The file is still on disk, so `file_exists` passes here.
  - The gate's later `commit_all` picks the file up. That is by design, not a defect.
- **Caller.** `_checkpoint` has only the one caller at loop.py:1001/1020, and that caller handles `None` through `changed or res.files`.

**Lock: never taken twice**
- `run_dag` takes the lock only when `tasks[tid][3]` is true (scheduler.py:82, 95-97). That value is `Task.writes`, which `_persist` sets to `c.writes` (loop.py:1894).
- loop.py:1016 uses a `nullcontext` exactly when `contract.writes` is true. Both sides test the same thing, so the non-reentrant lock is never entered twice.
- The permission-gated local `writes` at loop.py:951 is not used on either side, so an agent without the `write` permission doesn't break this pairing.
- `work()` is reached only through `run_dag`. All three calls (loop.py:1403/1407/1412) pass `goal.worktree`, so they all use the same lock.
- If the lock were taken twice, `test_writing_task_answer_committed_by_the_loop` would hang. It passed.

**`write_outputs` (specs.py:166-190)**
- It collects every path in this iteration's `gate["saved_answer"]` lists.
- If `<own>/spec.md` or `<own>/design.md` is among them, it keeps the saved answer and doesn't overwrite it.
- `test_saved_spec_md_kept_by_write_outputs` checks that the committed `spec.md` is the review answer and contains no `# Specification:` heading.

**Would the new tests have failed on main?** I checked this by reading the code; I did not run it.
- On main, `_save_answer` ran only for read-only tasks, skipped files that already existed, and made no separate commit.
- `_checkpoint` had no `core.quotepath=off`.
- The gate dict had no `saved_answer` key.
- So these four tests cannot pass on main: `test_read_only_answer_committed_on_its_own`, `test_writing_run_keeps_its_own_non_ascii_file`, `test_saved_spec_md_kept_by_write_outputs` and `test_writing_task_answer_committed_by_the_loop`.

**I found no defect in the four cases the task asked me to trace.**

## Checklist
- **Contract satisfied:** yes. Findings (a) read-only commit, (b) `write_outputs` keeps the answer, (c) unquoted paths and (d) forbidden paths are all addressed.
- **Interfaces compatible:** yes. `_checkpoint` now returns `list | None`, and its only caller handles `None`. `ANSWER_FILES` is still exported from `loop.py`.
- **Error handling:** fail-open and logged, with no crash.
- **Edge cases:** see the findings below.
- **Tests sufficient:** yes, with one gap: no test makes the answer checkpoint fail.
- **Dependencies:** none added.
- **Security:** nothing is written outside the worktree, into `.git`, into the loop's own files or into another goal's folder. `ensure_sandboxed` runs before `git add`.
- **Performance:** see the first finding.
- **Docs:** the module docstring and the function docstrings are updated.

FINDING: src/agent_company/loop.py:1016 | Every successful read-only task now waits for the worktree's write lock, even when its acceptance names no document, and it holds a global slot (MAX_PARALLEL defaults to 3) while a writing task runs in parallel; this delays other tasks, not correctness | Take the lock only if `contract.acceptance` has a `file_exists` path ending in `ANSWER_FILES`

FINDING: src/agent_company/specs.py:183,185 | `write_outputs` trusts `saved_answer` without checking that the file still exists. If the answer checkpoint fails and the goal then restarts, `_reconcile` removes the uncommitted spec.md, and it is then never written again for that iteration | Use `… not in saved_answer or not (d / "spec.md").exists()`, and the same for design.md

FINDING: src/agent_company/loop.py:888-892 | Git C-quotes path names containing `"`, `\`, a tab or a newline even with core.quotepath=off. Separately, `git add`/`commit -- paths` treat `*`, `?` and `[` as glob patterns, so a saved name containing them could also commit other changed files that match it | Use `diff --name-only -z`, and pass `:(literal)` pathspecs or set `GIT_LITERAL_PATHSPECS=1`

FINDING: tests/test_spec_answer.py:196-216 | No test covers a failing `_checkpoint(paths=…)`, the path that returns None and keeps the previous commit | Add a test where the spy raises `WorktreeError` for the "answer" key; it should check that `gate["commit"]` is the earlier commit and that an error event is logged

None of these blocks the fix: (a)–(d) behave as required and the targeted tests pass.

MESSAGE fact to developer: I checked by reading the code, not by running it, that the four new loop tests cannot pass on main; my pytest runs were refused, so the evidence is verify_fix's 61 passed at 5f938ee.

NEXT: Take the write lock at loop.py:1016 only when the contract names an answer document.

PROPOSAL: Decode git output as UTF-8 in worktrees.git :: `subprocess.run(text=True)` at worktrees.py:42 uses the locale's encoding, so under a non-UTF-8 LANG `rel in changed` would miss a non-ASCII file and save_answer would overwrite the run's own file.

OUTCOME: APPROVED_WITH_COMMENTS
VERDICT: PASS
