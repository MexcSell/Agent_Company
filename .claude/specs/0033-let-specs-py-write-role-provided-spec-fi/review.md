# Review (iteration 1)

## Review save_answer's logic against the spec (qa, done)

# Review of `save_answer` against spec 0033

I could not run the tests: all three forms of pytest I tried were refused at the permission prompt (`uv run pytest -q tests/test_spec_answer.py tests/test_spec_paths.py`, `.venv/bin/python -m pytest …` and `uv run python -m pytest …`). So nothing below comes from a test run of mine. The developer's "47 passed / 974 passed" is a claim I have not checked. All the conclusions come from reading the code at `838e7e2` against the spec.

## Logic verification

### `specs.save_answer` (`src/agent_company/specs.py:109-147`)

**Normal case: a read-only architect task with acceptance `file_exists <own>/x.md`**
- The run's `res.ok` is true, so `loop.py:1000` calls `save_answer(goal, contract, res.text, writes=False, changed=[])`. A read-only task gives `changed=[]` because `loop.py:991` returns `("", [])` for it.
- Inside `save_answer`:
  - The text is not empty and the path ends in `.md`, so the condition is used.
  - The `./` and absolute-prefix forms are stripped (`:126-129`).
  - `target = (root/path).resolve()`; `root in target.parents` and `.git` is not in the parts.
  - `rel.startswith(own)` is true, `x.md` is not a loop file and `writes` is false, so `_write` creates the parent folders and writes `text.rstrip()+"\n"`. The function returns `[rel]`.
- Next, `loop.py:1006` logs the save and `contracts.check` runs. Its `file_exists` check (`contracts.py:204-207`) resolves the same path under the worktree and passes.
- **Commit:** a read-only task makes no commit. The file stays uncommitted until the next writing task's checkpoint or the gate's `commit_all` (`quality.py:264`).
  - This goal shows it: `git log --stat -- .claude/specs` puts the architect's `spec.md` (115 lines) inside commit `838e7e2 "Goal 33 iteration 1: impl"`. So it is also in the `impl` task's `changed` list.
  - The developer's report says the file is "still untracked", which is wrong; it is committed.
  - The spec (§3 and its FINDING) records this as existing behaviour and out of scope, so I list it as a comment only.
- **Root cause:** this is fixed for read-only tasks. The loop now writes where the agent cannot.

**Edge case: iteration 2, read-only task**
- The file from iteration 1 is in HEAD.
- In the own-folder branch, `target.exists()` is never checked, so the file is overwritten with the new answer. This fixes K336.
- The overwrite is uncommitted and gets picked up later, as above.

**Edge case: iteration 2, writing task whose run did not create the file**
- `_checkpoint` (`loop.py:871-890`) compares the previous `Goal.checkpoint.commit` with the new HEAD. The stale file is not in that list, so `writes and rel in changed` is false and the file is overwritten.
- Then `loop.py:1005` makes the second checkpoint `"<key> answer"` while the write lock is still held. That commit becomes `gate["commit"]`.
- `changed` and `gate["files"]` stay the run's own list (`loop.py:1015,1018`), so `contracts.limits` does not count the loop's write.
- If the content is identical, `commit_all` returns None and `_checkpoint` returns the unchanged HEAD. Nothing breaks.
- If the run did change the file, `rel in changed` is true and the run's file is kept.

**Error cases: nothing is written**
- **`<own>/tasks.md` and the other loop files:** refused by `target.name in LOOP_FILES` (`:137`) for both read-only and writing tasks.
- **`.claude/specs/other/spec.md`:** refused by `rel.split("/")[:2] == [".claude","specs"]` (`:139`).
  - `rel` is the resolved path, so `<own>/../other/spec.md` and a symlinked folder are also refused.
  - `.claude/specs/x.md`, sitting directly in `specs/`, is refused too.
- **`<own>/../../../../x.md`, `../outside.md`, `/etc/x.md`:** refused by `root not in target.parents` (`:131`).
- **`<own>/../../../x.md`:** resolves to `x.md` at the worktree root, not outside. The "elsewhere" rule then applies: written once by a read-only task, never by a writing one. That is correct, and the developer pointed it out.
- **`.git/x.md`:** refused (`:134`).
- **A folder at the target path:** raises `IsADirectoryError`, an `OSError`, which is logged as an `error` event and skipped. This differs from rule 5's "skip if `is_dir()`" but meets error case 16. Acceptable.

### Call site in `loop.py` (`src/agent_company/loop.py:1000-1009`)
- The call now runs for writing tasks too. The `try/except` keeps a failure fail-open, and the second checkpoint commits the saved file on the goal's branch. That holds for writing tasks.
- `loop.ANSWER_FILES` is kept as an alias of `specs.ANSWER_FILES` (`:813`).

### Interaction with `write_outputs` (`specs.py:165-186`)
- For an architect task keyed `spec` or `specification`, `spec.md` is rewritten after all tasks as a header plus `t.output`. `t.output` is the same `res.text`, so the content survives, but only its last 50,000 characters (`loop.py:1017`).
- For `spec_v2`, `spec.md` is left alone.
- **Gap:** if the task that names `<own>/spec.md` or `<own>/design.md` is not the architect task the frame is built from (for example an analyst, or a non-`spec` architect key), `write_outputs` replaces its saved answer. For `design.md` this happens whenever the goal has any design task. See the findings.

### Behaviour outside the spec folder
Compared with the old `_save_answer`, the rules are the same: read-only only, new files only, inside the worktree, never `.git`. Writing tasks used to skip the call entirely; now `writes` makes them skip at `:139`, with the same result. There are three deliberate changes:
- Any `.claude/specs/<X>/` path is now refused, which backs up `check_plan`.
- The path is normalised first.
- An `OSError` is logged instead of propagating. Before, it could have broken the worker.

**Does it fix the root cause?** Yes, for both causes in spec §1: a read-only task can't write the file, and a stale file from iteration 1 used to stay in place. The new rules are applied by the loop, not hidden behind the acceptance check, and the check still requires the file to exist.

## Checklist
- **Contract satisfied:** I traced spec rules 1-9 and the call site in §3, and they match. Two documented deviations (case 16 and case 17's HEAD assertion) are justified.
- **Interfaces compatible:** the `ANSWER_FILES` alias stays, and `_save_answer` has no other callers in `src/` (checked with grep).
- **Error handling:** an `OSError` is logged per path, and the call site is wrapped in a `try/except`.
- **Edge cases:** I found three minor ones, listed in the findings.
- **Tests sufficient:** cases 1-16 have unit tests and case 17 has the loop test (`tests/test_spec_answer.py:149-169`). There is no loop-level test for a read-only task or for iteration 2. **None of the tests have been run independently.**
- **Dependencies:** no new ones.
- **Security:** checks are made on the resolved path; the escape, `.git` and other-goal cases are covered, and a symlink pointing outside the worktree is tested (`:100-105`).
- **Performance:** trivial.
- **Docs:** the module docstring is updated (`specs.py:16-17`). CLAUDE.md does not mention `save_answer`, which is minor.

## Findings

FINDING: tests/test_spec_answer.py:1-169 | Neither the requested `uv run pytest -q tests/test_spec_answer.py tests/test_spec_paths.py` nor any other pytest command ran in this QA session: all three forms were refused at the permission prompt. The only evidence the tests pass is the developer's own report, so the change is not shown to work. | Allow `uv run pytest` for QA, or have the gate run that command and attach its summary line, then re-review.
FINDING: src/agent_company/loop.py:1000-1009, .claude/specs/0033-let-specs-py-write-role-provided-spec-fi/spec.md | A read-only task's saved answer is never committed by its own task. In this goal the architect's `spec.md` went into commit `838e7e2 "…: impl"` and the `impl` task's `changed`, which can trip that task's `out_of_scope` limits, and `_reconcile` drops the file after a restart. The developer's report also wrongly calls the file untracked. | Out of scope per spec §3. Follow up by committing read-only answers under the worktree write lock.
FINDING: src/agent_company/specs.py:180-182 | When a non-architect task, or an architect task not keyed `spec`/`specification`, names `<own>/spec.md` or `<own>/design.md`, `write_outputs` later replaces the saved answer with the architect section(s), so that task's content is lost from the branch after the gate. | Have `write_outputs` skip a file that `save_answer` wrote this iteration, or have it append that answer as its own section.
FINDING: src/agent_company/specs.py:137 | `rel in changed` compares against `git diff --name-only` output. With the default `core.quotepath`, git quotes non-ASCII names (`"\303\274ber.md"`), so a writing run's own non-ASCII file under the spec folder never matches and is overwritten by the answer. The same happens to every writing run's file when the first `_checkpoint` fails and returns `[]`. | Get `changed` with `git -c core.quotepath=off diff --name-only` (or `-z`) in `_checkpoint`, and skip own-folder writes when the checkpoint failed for a writing task.

## Outcome
Tracing the logic found no defect that blocks the change for the three requested cases, and non-spec-folder behaviour is unchanged. But the required test command could not be run in this session, so the change is not shown to work by any evidence I produced. Under the review rule "not shown to work must not be approved", I am escalating rather than approving.

BLOCKER: the session's permission settings refuse `uv run pytest` (and the `.venv` and `python -m` forms), so QA cannot run `tests/test_spec_answer.py` or `tests/test_spec_paths.py`.
NEXT: Allow `uv run pytest -q tests/test_spec_answer.py tests/test_spec_paths.py` for QA and re-run this review. If it passes, the expected outcome is APPROVED_WITH_COMMENTS (findings 2-4).
NEXT: Add a loop-level test for iteration 2 with a read-only task that overwrites a stale `<own>/spec.md`.
MESSAGE request to general_manager: QA's pytest run of `tests/test_spec_answer.py` and `tests/test_spec_paths.py` was refused by the permission prompt; please grant it or attach the gate's run output so this review can be completed.
MESSAGE fact to developer: `.claude/specs/0033-…/spec.md` is not untracked; it was committed inside `838e7e2` (the `impl` checkpoint), see `git log --stat -- .claude/specs`.
PROPOSAL: Commit read-only answers under the write lock :: Today they go into an unrelated writing task's commit and `changed` list (seen in 838e7e2), which blurs which task changed what and can fail that task's scope limits.

OUTCOME: ESCALATED
VERDICT: FAIL
