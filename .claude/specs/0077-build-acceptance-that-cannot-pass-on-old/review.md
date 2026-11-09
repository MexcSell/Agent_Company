# Review (iteration 1)

## Review the empty-diff build rule (qa, done)

I could not run the tests myself, so this approval comes from reading and tracing the code. The developer reported 27 passed on the three test files and 1074 passed on the full suite; I have not reproduced either number. The gate reruns the acceptance command.

## Logic verification

**`contracts.requires_change`** (`src/agent_company/contracts.py:274-282`)
- **Normal case:** a writing task with no `command` condition that changed `["src/a.py"]` gets one row, `{"condition": {"type": "diff"}, "passed": True, "detail": ""}`. `tests/test_contracts.py` checks exactly this row.
- **Edge cases:**
  - `changed=None` returns `[]`.
  - A `command` condition anywhere in the acceptance returns `[]`.
  - A read-only task returns `[]` because `contract.writes` is False.
- **Error case:** a writing task with an empty diff and only `file_exists`/`contains` conditions gets one row with `passed=False`. Its detail says "existing files do not count".

**Call site** (`src/agent_company/loop.py:1032-1033`)
- `_checkpoint` (`loop.py:872-900`) returns the files changed between the previous checkpoint commit and the new HEAD. It returns `None` when the checkpoint fails. A read-only task gets `("", [])`, but `requires_change` returns `[]` for it because `writes` is False. Read-only tasks are unaffected.
- The new rule receives `None if changed is None else sorted(set(changed) | set(saved))`:
  - A failed checkpoint passes `None`, so there is no diff row.
  - With `changed == []` and a saved answer, the union is not empty, so the row passes. A saved answer counts as a change.
  - `res.files` is not used. `changed or res.files` still appears only in the existing `limits` call (`:1030`) and in the `gate["files"]` values (`:1037, :1047, :1054`). That code was there before and the new rule does not use it.
- **Order:** the diff row is added at `:1032`. The BLOCKER_SKIP branch (`:1043-1051`) checks `changed == []`, sets the task to `skipped`, and returns False before `ok` is computed at `:1052`.
  - So a blocked build with an empty diff is still skipped and still pauses the goal. The failing diff row is only recorded in its gate.
  - Without a BLOCKER line, the row's `passed=False` makes `ok` False, so the task fails.
- **Downstream:** the new `{"type": "diff"}` row has the same form as the existing `budget` and `out_of_scope` rows. `loop.py:1626` reads it with `.get("type")`, and `mission.py:108-112` handles rows of any type. No code turns these rows back into a `Condition`, so adding a type outside `ConditionType` breaks nothing.

**Root cause or symptom?** The root cause is fixed. Before, a writing task with no `command` condition was judged only by checks on files, and those checks pass on files that already exist. Now the task must also produce a change.

**Tests**
- `test_requires_change_fails_a_writing_task_without_a_command_on_an_empty_diff` covers all 5 cases of the function.
- `test_an_empty_build_on_old_files_without_a_blocker_line_fails` makes `old.txt` an old file correctly. It commits the file and moves `checkpoint["commit"]` to HEAD before the task runs, so `_checkpoint` sees `before == head` and returns `changed == []`. It then checks:
  - the task status is `failed`;
  - `gate["files"] == []`;
  - the `file_exists` row passed and the `diff` row failed;
  - the goal did not pause.
- **The reversed assertion the developer asked about** (`tests/test_blocker_pause.py:93-101`): this fixture's `implement` task has the default acceptance (`min_length 20`), no command, writes nothing and has no BLOCKER line. That is exactly the case this goal targets, so `failed` is the correct new expectation. Loosening the `ran` check to `ran[0] == "implement"` is acceptable but weaker than before.
- The existing BLOCKER tests (`:52-90`) are unchanged:
  - The blocked build is still skipped, the goal pauses, the review does not run and the gate does not evaluate.
  - After resuming, the build writes `feature.py`, so its diff row passes and the task ends `done`.
- `tests/test_loop.py`, `tests/test_contracts.py` and `tests/test_blocker_pause.py` all exist. The acceptance command will not hit "file not found" (exit 4).

## Checklist
- **Contract satisfied:** yes. The function signature, the three empty-return cases, the row text, the use of `changed` plus `saved` instead of `res.files`, and the unchanged BLOCKER_SKIP branch all match `tasks.md`.
- **Interfaces compatible:** yes. One new function was added; nothing existing changed its signature.
- **Error handling:** yes. A failed checkpoint (`None`) is not treated as an empty diff.
- **Edge cases:** covered, except later iterations (finding 1).
- **Tests sufficient:** yes, as a unit test plus a loop regression test. There is no loop test for "empty diff + saved answer passes"; it is only checked by tracing the code above.
- **Dependencies:** none added.
- **Security:** no new input surface. The rule only makes acceptance stricter.
- **Performance:** one extra pass over the acceptance list per task, which is negligible.
- **Docs:** ADR 0085 was added. One sentence in it is inaccurate (finding 2).

FINDING: src/agent_company/loop.py:1032 (with loop.py:874, `_checkpoint`) | `changed` covers only this task's changes since the last checkpoint commit, not the whole goal's diff against main. Worktrees are not reset between iterations. So in iteration 2 or later, a writing task with no command condition fails when its work was already committed in an earlier iteration and nothing new needs changing, for example when that iteration's review failed only because tests were not run. This is stricter than the goal's wording, "change files in this goal's diff". | Accept this as intended and record it as a limitation in ADR 0085, or pass a non-empty value when the goal's diff against the base branch is not empty. The second option needs care so that a blocked developer cannot pass on earlier iterations' files.

FINDING: docs/adr/0085-where-to-enforce.md:31 | The ADR says the check runs "after the BLOCKER_SKIP branch". In fact the row is added at loop.py:1032, before that branch (`:1043`), and is stored in a skipped task's gate. Behaviour is correct only because the skip returns before `ok`. Anyone who later moves the `ok` line would be misled. | Reword it to "the row is added before the BLOCKER_SKIP branch but only affects `ok`, which is computed after the skip returns".

BLOCKER: I could not run `uv run pytest -q tests/test_contracts.py tests/test_blocker_pause.py tests/test_loop.py`. The command was not approved in this session (two tries, both "requires approval"). The evidence for passing tests is the developer's reported run (27 passed on the three files, 1074 on the full suite) and the gate's own rerun of the acceptance command.

MESSAGE fact to developer: the reversed assertion in tests/test_blocker_pause.py:93-101 is correct: that fixture is the goal's exact target case (default `min_length` acceptance, no command, no file written, no BLOCKER line), so `failed` is the expected result.

NEXT: the gate reruns `uv run pytest -q tests/test_contracts.py tests/test_blocker_pause.py tests/test_loop.py` and `uv run pytest -q`, because QA could not run them here.
NEXT: record the iteration-2 limitation in ADR 0085, or decide to measure against the goal's diff instead.

PROPOSAL: Diff rule against the goal's base for iteration 2+ :: a rebuild that correctly needs no further change currently fails each iteration until max_iterations, unless the planner adds a command condition.

OUTCOME: APPROVED_WITH_COMMENTS
VERDICT: PASS
