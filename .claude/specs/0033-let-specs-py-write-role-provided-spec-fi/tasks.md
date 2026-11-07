# Tasks (iteration 2)



## 1. Commit read-only answers, keep saved answers from write_outputs, unquoted changed paths
- key: `fix_answer` · role: developer · writes: yes

Fix the three code findings from iteration 1's review. Read src/agent_company/specs.py (save_answer, write_outputs), src/agent_company/loop.py (_checkpoint around line 871 and the save_answer call around lines 995-1010), src/agent_company/scheduler.py (write_lock, run_dag.run_one) and the existing tests/test_spec_answer.py first. Keep the change small.

1. Non-ASCII paths. In loop._checkpoint, get `changed` with `worktrees.git(wt, '-c', 'core.quotepath=off', 'diff', '--name-only', before, head)`, or with `-z` split on NUL. First check how worktrees.git passes its arguments, so the `-c` lands before the subcommand. When the checkpoint fails, make it return `changed=None` instead of [] (update the type hint, and keep the caller's `contracts.limits(contract, changed or res.files, ...)` working). In specs.save_answer, accept `changed: list[str] | None`. For a writing task with `changed is None` (the run's changes are unknown), skip every path in the goal's own spec folder. Never overwrite what the run may have written.

2. Commit a read-only task's answer under the worktree write lock. In loop.py, when `saved` is non-empty and `not contract.writes`, take `scheduler.write_lock(goal.worktree)` (the read-only worker does not hold it; the writing worker already does, so never take it again there because threading.Lock is not reentrant). Commit only the saved paths: `git add -- <paths>` and then `git commit -q -m 'Goal N iteration I: <key> answer' -- <paths>` with the goal author's identity. Then update Goal.checkpoint['commit'] to the new HEAD, the same way _checkpoint does. A helper in loop.py is fine, e.g. a `paths` parameter on _checkpoint. A later writing task's `changed` must then no longer contain the answer file. A commit error is logged and must not fail the task.

3. write_outputs must not overwrite saved answers. When save_answer saves files, record the list on the task, e.g. `Task.gate['saved_answer'] = saved`, in the loop's existing session pattern. Write it inside an open session() through that session only. In specs.write_outputs, collect the saved_answer paths of this iteration's tasks and skip spec.md or design.md when that relative path is among them. The other loop-written files are unchanged.

Regression tests in tests/test_spec_answer.py (offline, temp DATA_DIR, real git worktrees as the existing tests use). Each test must fail on the current code:
(a) a writing run that creates `<own folder>/über.md` keeps its own content after save_answer, using a real git checkpoint so `changed` comes from git;
(b) a writing task with changed=None does not write into its own spec folder;
(c) a read-only task's saved answer is in its own commit, the goal checkpoint points at it, and a following writing task's _checkpoint `changed` does not list the file;
(d) a non-'spec'-keyed task whose acceptance names `<own>/spec.md` keeps its saved content after write_outputs runs with an architect 'spec' task in the same iteration.
Run `uv run pytest -q tests/test_spec_answer.py tests/test_spec_paths.py` and `uv run pytest -q tests -k 'loop or spec or checkpoint or resume'`, and paste both summary lines into your answer. List the files you changed. Do not edit anything under .claude/specs/ by hand.

Acceptance: {"type": "command", "run": "uv run pytest -q tests/test_spec_answer.py tests/test_spec_paths.py", "requirement_keys": ["S04-R-05"], "stage": "unit"}; {"type": "command", "run": "grep -q 'quotepath=off\\|\"-z\"' src/agent_company/loop.py"}; {"type": "command", "run": "grep -q 'saved_answer' src/agent_company/specs.py"}; {"type": "command", "run": "grep -q 'write_lock' src/agent_company/loop.py"}

## 2. Review the save_answer fixes against the four findings
- key: `qa_review` · role: qa · writes: no · after: fix_answer

Review the diff of fix_answer (`git diff HEAD~3 -- src/agent_company/specs.py src/agent_company/loop.py tests/test_spec_answer.py`, or the commits since 2d0414b) against iteration 1's four findings. 1. Changed paths come from git with core.quotepath=off or -z, and a failed checkpoint (changed=None) stops a writing task from writing into its own spec folder. 2. A read-only task's answer is committed alone under scheduler.write_lock, there is no double acquire on the writing path (deadlock), and Goal.checkpoint is updated so _reconcile keeps the file. 3. write_outputs skips the files recorded in Task.gate['saved_answer']. 4. The tests (a)-(d) exist and would fail without the fix. Trace the data flow of a normal case (read-only architect answer to spec.md), an edge case (non-ASCII file written by a writing run) and an error case (the checkpoint fails). The platform runs `uv run pytest -q tests/test_spec_answer.py tests/test_spec_paths.py` as this task's acceptance condition and the gate runs it again. If you cannot run pytest yourself, do not FAIL for that alone: base the verdict on that recorded result and the code, and say which one you used. List each problem as a `FINDING: file:line | problem | fix` line. End with `OUTCOME: ...` and `VERDICT: PASS` or `VERDICT: FAIL`.

Acceptance: {"type": "command", "run": "uv run pytest -q tests/test_spec_answer.py tests/test_spec_paths.py", "requirement_keys": ["S04-R-05"], "stage": "unit"}; {"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}; {"type": "contains", "value": "OUTCOME"}; {"type": "min_length", "value": 400}
