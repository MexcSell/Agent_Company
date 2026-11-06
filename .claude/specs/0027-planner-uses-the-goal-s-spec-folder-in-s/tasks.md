# Tasks (iteration 2)



## 1. Add relative_folder, planner_note and check_plan to specs.py with regression tests
- key: `spec_helpers` · role: developer · writes: yes

Cause (FACT): the planner prompt never names this goal's spec folder. Plans therefore invent folders such as `.claude/specs/req-001-reviewed-commit/` (K54) or point at files the loop writes itself, and a read-only task can never fill those. Iteration 1 of this goal failed because no file was changed at all. You MUST edit and save real files on disk. Before you hand back, run `git status --short` and `git diff --stat` and paste their output into your answer.

Read src/agent_company/specs.py first (`slug`, `folder`, `write_plan`, `write_outputs`, `write_decision`). Also read loop.py around `_save_answer`/`ANSWER_FILES` (around line 800) so you know which .md/.txt paths the loop fills from an answer.

In src/agent_company/specs.py add the following:
1. `LOOP_FILES = ("requirements.md", "tasks.md", "review.md", "sa-review.md", "decisions.md")`. The loop writes these itself, so a task acceptance condition on them is impossible or meaningless. spec.md and design.md stay allowed.
2. `relative_folder(goal) -> str`: returns `f".claude/specs/{slug(goal.title, goal.id)}"`. It must reuse `slug()` so it always matches `folder(goal)`.
3. `planner_note(goal) -> str`: a short paragraph ending with a newline. It says that this goal's spec folder is `<relative_folder>/`; that any spec or design document a task produces goes there (a read-only task's .md/.txt file_exists path in it is filled with that task's answer); that no other `.claude/specs/` folder may be used; and that the LOOP_FILES are written by the loop and must not appear in acceptance conditions.
4. `check_plan(goal, plan) -> list[str]` (plan: list of contracts.TaskContract; import lazily or by type only, to avoid an import cycle).
   - For every acceptance condition of every contract, a `file_exists` path is normalised: strip a leading `./`, and strip the absolute worktree prefix `str(goal.worktree)+'/'` when the worktree is set.
   - If the path is under `.claude/specs/<X>/` with X different from the goal's slug, replace X with the goal's slug and keep the rest of the path.
   - In a `command` condition's `run` string, apply the same substitution with a regex on `.claude/specs/<X>/`.
   - Mutate the conditions in place and return one line per rewrite, of the form `<task key>: <old> -> <new>`.
   - After rewriting, raise `ValueError(f"task {key}: {path} is written by the loop itself, not by a task")` when a file_exists path, or a path inside a command, names a LOOP_FILES file in the goal's spec folder.
   - Leave paths outside `.claude/specs/` unchanged. Never create, move or delete a folder.

Then create tests/test_spec_paths.py. It runs offline and uses `types.SimpleNamespace(id=27, title='Planner uses the goal\'s spec folder in spec-task acceptance paths', worktree='/tmp/wt')` as the goal, plus real `contracts.TaskContract` objects (read src/agent_company/contracts.py for the constructor). The tests:
- test_foreign_folder_rewritten: a read-only task whose path is `.claude/specs/req-027-foo/notes.md` becomes `.claude/specs/0027-planner-uses-the-goal-s-spec-folder-in-s/notes.md`, with one report line. This test must fail without the fix.
- test_correct_path_unchanged: no report.
- test_non_spec_path_unchanged: `tests/x.py`.
- test_dot_slash_and_absolute_prefix_normalised.
- test_command_rewritten: `grep x .claude/specs/other/spec.md`.
- test_loop_file_rejected: `decisions.md` in the goal's folder raises ValueError, and so does a foreign folder's `review.md` after rewriting.
- test_planner_note_names_folder.
- test_relative_folder_matches_folder: `str(specs.folder(goal)).endswith(specs.relative_folder(goal))`.

Run `uv run pytest -q tests/test_spec_paths.py` and make it pass before handing back. Keep the change small and in the file's style.

Acceptance: {"type": "file_exists", "path": "tests/test_spec_paths.py"}; {"type": "command", "run": "grep -n 'def check_plan' src/agent_company/specs.py && grep -n 'def planner_note' src/agent_company/specs.py && grep -n 'def relative_folder' src/agent_company/specs.py && grep -n 'LOOP_FILES' src/agent_company/specs.py"}; {"type": "command", "run": "uv run pytest -q tests/test_spec_paths.py", "requirement_keys": ["S09-R-02"], "stage": "unit"}

## 2. Wire specs.planner_note and specs.check_plan into loop._plan
- key: `loop_wiring` · role: developer · writes: yes · after: spec_helpers

Iteration 1 changed no file. You MUST save your edits, and your answer must include the output of `git diff --stat`.

specs.py already has `relative_folder`, `planner_note`, `check_plan` and `LOOP_FILES` (from the task spec_helpers). Read src/agent_company/loop.py `_plan` (around lines 537-640) and the fallback standard plan path.

Changes in loop.py:
1. In the prompt assembly at about line 563, next to `strategies.planner_note(goal)`, add `+ specs.planner_note(goal)`. Always call it as `specs.planner_note`, never import the bare name: other modules have functions of the same name.
2. Inside the existing `try` that calls `contracts.validate_plan(...)`, call `notes = specs.check_plan(goal, plan)` right after validation. A ValueError then follows the existing path (the plan goes back to the planner once, then the standard plan is used). When `notes` is non-empty, call `log_event("plan", "Spec paths corrected: " + "; ".join(notes), goal_id=goal.id, agent_id=gm.id)`, using the variable names that exist there and `s=s` if inside a session.
3. Run `specs.check_plan(goal, plan)` on the fallback standard plan as well, without catching errors there.

Add a test to tests/test_spec_paths.py that proves the prompt names the folder. Either drive `_plan` with the offline executor as tests/test_loop.py does (look for an existing pattern), or monkeypatch the executor call to capture the prompt and assert that `specs.relative_folder(goal)` is in it. Run `uv run pytest -q tests/test_spec_paths.py tests/test_loop.py tests/test_contracts.py` and make it pass.

Acceptance: {"type": "command", "run": "grep -n 'specs.check_plan' src/agent_company/loop.py && grep -n 'specs.planner_note' src/agent_company/loop.py"}; {"type": "command", "run": "uv run pytest -q tests/test_spec_paths.py", "requirement_keys": ["S09-R-02"], "stage": "unit"}; {"type": "command", "run": "uv run pytest -q tests/test_loop.py tests/test_contracts.py tests/test_strategies.py", "stage": "integration"}

## 3. Review the spec-folder path fix
- key: `qa_review` · role: qa · writes: no · after: loop_wiring

Review the changes to src/agent_company/specs.py, src/agent_company/loop.py and tests/test_spec_paths.py against the goal: a read-only spec task must never again fail on a `.claude/specs/<invented>/` file or on a file the loop writes itself (K54). First confirm with `git log --stat` / `git diff main --stat` that all three files really changed (iteration 1 delivered no change).

Trace the data flow of three cases:
- normal: a foreign spec-folder path is rewritten to `specs.relative_folder(goal)` before `_spec_step(specs.write_plan, ...)` writes tasks.md and before any task runs, and `_save_answer` then fills that .md path for a read-only task;
- edge: correct paths, non-spec paths, `./` and absolute-worktree prefixes, and command strings;
- error: a condition on decisions.md, review.md or another LOOP_FILES entry raises ValueError inside `_plan`'s try, goes back to the planner once, and falls back to the standard plan.

Also check that:
- the prompt contains the folder;
- relative_folder uses `slug()` exactly as `folder()` does;
- nothing outside the in-scope files changed;
- no folder is created, moved or deleted;
- the tests run offline and the foreign-folder test would fail without the fix.

Run or read `uv run pytest -q tests/test_spec_paths.py tests/test_loop.py`. List each problem as `FINDING: file:line | issue`, then write an `OUTCOME:` line and end with `VERDICT: PASS` or `VERDICT: FAIL`.

Acceptance: {"type": "regex", "pattern": "VERDICT:\\s*\\**\\s*(PASS|FAIL)"}; {"type": "regex", "pattern": "(?m)^\\W*OUTCOME:"}; {"type": "min_length", "value": 300}
