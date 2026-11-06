# Tasks (iteration 1)



## 1. Name the goal's spec folder to the planner and correct spec-folder paths in plans
- key: `fix_spec_paths` · role: developer · writes: yes

Read src/agent_company/specs.py (`slug`, `folder`, `write_plan`, `write_outputs`, `write_decision`) and src/agent_company/loop.py (`_plan`, around lines 537-635, `PLAN_INSTRUCTIONS`, and `_save_answer`) before you change anything.

Reproduce first. Write a test in which a plan for goal id 27 with a read-only task whose condition is `{"type":"file_exists","path":".claude/specs/req-027-foo/notes.md"}` keeps that foreign path. That is the fault.

Then implement the following in src/agent_company/specs.py:
1. `relative_folder(goal) -> str`: returns `.claude/specs/<slug(goal.title, goal.id)>` (POSIX, no worktree prefix).
2. `LOOP_FILES = ("requirements.md", "tasks.md", "review.md", "sa-review.md", "decisions.md")`. The loop writes these itself (before the tasks or after the gate), so a task condition on them is meaningless or impossible. spec.md and design.md stay allowed, because `_save_answer` fills them from an architect's answer.
3. `planner_note(goal) -> str`: a short paragraph stating that this goal's spec folder is `<relative_folder>/`, that any spec or design document a task must produce goes there (a read-only task's .md/.txt `file_exists` path in it is filled with its answer), that no other `.claude/specs/` folder may be used, and that the LOOP_FILES are written by the loop and must not appear in acceptance conditions.
4. `check_plan(goal, plan: list[TaskContract]) -> list[str]`: handles every acceptance condition of every contract. For `file_exists` paths, normalise a leading `./` or the absolute worktree prefix. If the path lies under `.claude/specs/<X>/` with X different from the goal's slug, rewrite it to the goal's folder with the rest of the path kept. Do the same substitution for `.claude/specs/<X>/` inside `command` conditions' `run` strings. If a `file_exists` path (after rewriting) or a command names a LOOP_FILES file in the goal's spec folder, raise `ValueError` naming the task key, the path and why. Return one human-readable line per rewrite. Paths outside `.claude/specs/` are left unchanged. Do not create, move or delete any folder.

In loop.py `_plan`:
- Add `specs.planner_note(goal)` to the prompt, next to `strategies.planner_note(goal)`.
- Call `specs.check_plan(goal, plan)` right after `contracts.validate_plan(tasks)`, inside the existing `try`, so a ValueError returns the plan to the planner once (S04-R-06 path). Log the rewrites with `log_event("plan", ..., goal_id=goal.id, agent_id=gm.id)` when there are any.
- Also run `check_plan` on the fallback standard plan. It should not raise there; if it does, let it surface.

Tests go in a new tests/test_spec_paths.py, offline, using the conftest temp DATA_DIR. Build a Goal-like object or a real goal with a worktree path. Cover:
- the normal case: a foreign folder path is rewritten to `.claude/specs/0027-<slug>/notes.md` and reported;
- the edge cases: an already-correct path is unchanged with no report, a path outside `.claude/specs` is unchanged, a `./`-prefixed path is normalised, and a command string with a foreign folder is rewritten;
- the error case: `decisions.md` in the goal's folder (or reached by rewriting a foreign one) raises ValueError;
- `planner_note` contains the relative folder.

If an existing test in tests/test_loop.py drives `_plan` with the offline executor, add one assertion there or in the new file that the planner prompt contains the folder. Keep the change small and in the style of the surrounding code. Run the acceptance commands before handing back.

Acceptance: {"type": "file_exists", "path": "tests/test_spec_paths.py"}; {"type": "command", "run": "grep -n 'def check_plan' src/agent_company/specs.py && grep -n 'def planner_note' src/agent_company/specs.py && grep -n 'specs.check_plan' src/agent_company/loop.py && grep -n 'specs.planner_note' src/agent_company/loop.py"}; {"type": "command", "run": "uv run pytest -q tests/test_spec_paths.py", "stage": "unit"}; {"type": "command", "run": "uv run pytest -q tests/test_loop.py tests/test_strategies.py tests/test_contracts.py", "stage": "integration"}

## 2. Review the spec-folder path fix
- key: `qa_review` · role: qa · writes: no · after: fix_spec_paths

Review the change in src/agent_company/specs.py, src/agent_company/loop.py and tests/test_spec_paths.py against the goal. A read-only spec task must never again fail on a `.claude/specs/<invented>/` file or on a file the loop writes itself (K54).

Verify the data flow of three cases:
- normal: a plan with a foreign spec folder path is rewritten to `specs.relative_folder(goal)` before `write_plan` and before the tasks run, and `_save_answer` then fills that path for a read-only .md task;
- edge: correct paths, non-spec paths, `./` prefixes, and command strings;
- error: a condition on decisions.md, review.md or another LOOP_FILES entry raises ValueError inside `_plan`'s try block, goes back to the planner once, and falls back to the standard plan as before.

Also check that:
- the prompt actually contains the folder;
- the slug used matches `specs.folder(goal)` exactly;
- nothing outside the in-scope files changed;
- no folder is created, moved or deleted;
- the tests run offline.

Run or read `uv run pytest -q tests/test_spec_paths.py tests/test_loop.py`. List each problem as a `FINDING: file:line | issue` line, then write an `OUTCOME:` line and end with `VERDICT: PASS` or `VERDICT: FAIL`.

Acceptance: {"type": "regex", "pattern": "VERDICT:\\s*\\**\\s*(PASS|FAIL)"}; {"type": "regex", "pattern": "(?m)^\\W*OUTCOME:"}; {"type": "min_length", "value": 300}
