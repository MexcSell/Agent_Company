# Tasks (iteration 1)



## 1. Eval seed repos get a .gitignore for Python caches, with a regression test
- key: `seed_gitignore` · role: developer · writes: yes

Work in THIS repository, the Agent Company platform. Do not create or edit an eval scratch repository under data/workspaces. Read `_seed_repo` in src/agent_company/evaluation.py (around line 121) first.

1. Add a module constant near `_seed_repo`, `SEED_IGNORES = ["__pycache__/", "*.pyc", ".pytest_cache/"]`, with a one-line comment: bytecode and test caches from runs in a seed repo must never show up as untracked changes. Keep the list narrow. Do NOT add `.env`, `*.log`, `node_modules/` or anything from `gitsync.SOURCE_IGNORES`, because ignoring `.env` would hide the protected-config and adversarial traps from the gate.
2. In `_seed_repo`, after the task's files are written and before `git add -A`, ensure `repo/.gitignore` contains every SEED_IGNORES line:
   - If the task did not seed a `.gitignore`, write one with exactly those lines, newline-terminated.
   - If it did, keep its text unchanged and append only the missing lines, adding a newline first if the file does not end with one.
   The existing `git add -A` + commit then puts .gitignore into the 'Eval seed' commit, so HEAD stays clean.
3. Do not change the function's signature, its path-escape check or anything else in evaluation.py.
4. Create tests/test_eval_seed.py. Use the repo's existing pytest fixtures, since each test already gets its own temp DATA_DIR (see tests/conftest.py), and stay offline. Tests:
   a. Normal case: `evaluation._seed_repo('t', {'name': 'x', 'files': {'stats.py': 'def f():\n    return 1\n'}}, 0)`. Assert that `.gitignore` is tracked (`git ls-files` lists it) and contains the three lines. Create `__pycache__/stats.cpython-312.pyc`, `pkg/__pycache__/m.pyc`, `a.pyc` and `.pytest_cache/v/cache/lastfailed` in it. Assert that `git status --porcelain --untracked-files=all` prints nothing.
   b. Edge case: a task that seeds its own `.gitignore` with `dist/` and no trailing newline. Assert that the first line is still `dist/` and that all three SEED_IGNORES entries are present exactly once. Calling the merge twice must not duplicate them: factor the merge into a small helper and test it directly, or just check the counts.
   c. Narrowness: after seeding, create `new_module.py` and `.env`. Assert that both still appear as `??` in `git status --porcelain`, so real changes and protected config stay visible.
   d. Run the real generated-file path: `python -c 'import stats'` with `cwd=repo` via subprocess and `sys.executable` (sys.dont_write_bytecode off; pass env `PYTHONDONTWRITEBYTECODE` removed) so a genuine `__pycache__` appears. Assert that the status is still clean.
5. Run `python3 -I /home/luca/Agent_Company/src/agent_company/edit_check.py src/agent_company/evaluation.py tests/test_eval_seed.py`, `uv run pytest -q tests/test_eval_seed.py` and `uv run pytest -q tests/test_evaluation.py`. Read each summary line (no `-n auto`). Paste the commands and their summary lines into your final answer as evidence.

Acceptance: {"type": "command", "run": "uv run pytest -q tests/test_eval_seed.py", "requirement_keys": ["S09-R-05"], "stage": "unit"}; {"type": "command", "run": "uv run pytest -q tests/test_evaluation.py", "stage": "integration"}; {"type": "command", "run": "grep -q '__pycache__/' src/agent_company/evaluation.py", "stage": "static"}; {"type": "file_exists", "path": "tests/test_eval_seed.py"}

## 2. Review the eval seed .gitignore fix against the change-boundary goal
- key: `qa_review` · role: qa · writes: no · after: seed_gitignore

Review the diff of src/agent_company/evaluation.py and tests/test_eval_seed.py against the base branch. You cannot run commands. Execution evidence comes from the gate: its acceptance commands (`uv run pytest -q tests/test_eval_seed.py`, `uv run pytest -q tests/test_evaluation.py`) are re-run by the quality gate itself. Also use the developer's pasted summary lines. Do not fail only because you could not run them yourself (K283/K284). Fail if the evidence is missing or contradicts the code. Verify the logic by reading the code:
(1) Normal case data flow: task files are written, then .gitignore is written, then `git add -A`, then the 'Eval seed' commit. Confirm that .gitignore lands in the seed commit, that the worktree is clean afterwards, and that `__pycache__/`, `*.pyc` and `.pytest_cache/` are ignored at any depth.
(2) Edge case: a task that seeds its own .gitignore (with and without a trailing newline). Its content is kept, the missing entries are appended once and nothing is duplicated.
(3) Error/safety case: the list must NOT ignore `.env`, `*.log` or source files. Otherwise checks.PROTECTED_CONFIG and the adversarial suite (evals/adversarial*.json) would lose visibility. Confirm that the path-escape ValueError in `_seed_repo` is unchanged, and that a seed `.gitignore` key still goes through that check.
(4) Confirm the change sits in the platform's seed creation (evaluation._seed_repo) and not in an eval scratch repo, and that no file outside the two in-scope paths changed.
(5) Check that the tests really exercise the behaviour: they assert on `git status` output, not just on file contents, and they run offline.
List each problem as `FINDING: <file>:<line> | <problem> | <fix>`. End with `OUTCOME: <one line>` and then `VERDICT: PASS` or `VERDICT: FAIL`.

Acceptance: {"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}; {"type": "contains", "value": "OUTCOME:"}; {"type": "min_length", "value": 400}
