# Tasks (iteration 1)



## 1. Fail a writing task with no command condition and an empty diff
- key: `build_diff_rule` · role: developer · writes: yes

Fault: a writing task can pass when its acceptance has no `command` condition and checks only files that already exist (file_exists, contains, regex, min_length), even if its run changed nothing. A blocked developer that writes no BLOCKER line then passes the build step.

1. In src/agent_company/contracts.py, add `def requires_change(contract: TaskContract, changed: list[str] | None) -> list[dict[str, Any]]` next to `limits`. It returns [] in three cases: `contract.writes` is false, `changed is None` (the change is unknown), or the acceptance contains any condition with `type == "command"`. Otherwise it returns exactly one row: `{"condition": {"type": "diff"}, "passed": bool(changed), "detail": "" if changed else "a writing task without a command condition changed no file in this goal's diff; existing files do not count"}`. Give it a short docstring like the ones nearby, and say what this guards against.
2. In src/agent_company/loop.py, around line 1030 (after `acceptance += contracts.limits(...)`), add: `acceptance += contracts.requires_change(contract, None if changed is None else sorted(set(changed) | set(saved)))`. Use `changed`, not `changed or res.files`. Answers that the loop saved for this task (`saved`) count as changes. Leave the BLOCKER_SKIP branch unchanged: it runs before `ok` is computed, so a blocked empty build is still skipped and the goal pauses. The new row only affects the `ok` of tasks that are not skipped.
3. Tests:
(a) in tests/test_contracts.py, cover requires_change for these cases: a writing task with no command and an empty diff fails; a writing task with no command and a changed file passes; a writing task with a command and an empty diff returns []; a read-only task returns []; changed None returns [].
(b) in tests/test_loop.py or tests/test_blocker_pause.py, following the existing loop tests that use the offline simulator or a fake executor, add a regression test. A writing task whose acceptance is only file_exists on a file already committed in the worktree, run with an empty diff and no BLOCKER line, ends `failed`, and its gate has a `diff` row with passed False. Also check that the existing BLOCKER-with-empty-diff test still skips.
Read the files before you change them. Do not change any other file.

Acceptance: {"type": "command", "run": "uv run pytest -q tests/test_contracts.py tests/test_blocker_pause.py tests/test_loop.py", "requirement_keys": ["S09-R-01"], "stage": "unit"}; {"type": "command", "run": "grep -n 'def requires_change' src/agent_company/contracts.py && grep -n 'requires_change' src/agent_company/loop.py", "stage": "static"}

## 2. Review the empty-diff build rule
- key: `qa_review` · role: qa · writes: no · after: build_diff_rule

Review the goal's diff against main in src/agent_company/contracts.py (requires_change), in src/agent_company/loop.py (where it is called in run_task, near the BLOCKER_SKIP branch) and in the new tests. Trace the data flow of these cases:
(1) Normal: a writing task with no command condition that changed a file passes the diff row.
(2) Edge: `changed` is None because the checkpoint failed, so there is no diff row. A writing task with an empty diff and a command condition gets no diff row either. A saved answer counts as a change.
(3) Error: a writing task with an empty diff, only file_exists on old files and no BLOCKER fails. With a BLOCKER line it is still skipped (BLOCKER_SKIP) and the goal pauses.
Also check that read-only tasks are unaffected and that `changed or res.files` was not used for the new rule. Run `uv run pytest -q tests/test_contracts.py tests/test_blocker_pause.py tests/test_loop.py` if you can. If the environment blocks the run, report that as a blocker, not as a defect. List FINDING lines, then an OUTCOME line, then end with VERDICT: PASS or VERDICT: FAIL.

Acceptance: {"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}; {"type": "contains", "value": "OUTCOME"}
