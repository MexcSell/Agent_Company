# Review (iteration 1)

## Review the eval seed .gitignore fix against the change-boundary goal (qa, done)

## Logic verification

**`evaluation._seed_repo` (src/agent_company/evaluation.py:121-145)**

- **Normal case.** The repo is created, then `git init -b main`. Each task file goes through the path-escape check (lines 130-135, the same as base lines 133-138 in the main checkout) and is written. Next, `.gitignore` is read, or `""` if there is none. `missing` gets every `SEED_IGNORES` entry that isn't already an exact line, and the file is written as the existing text plus the missing lines, ending in `\n`. Then `git add -A` and the `Eval seed` commit run (lines 143-144). Because `.gitignore` is written before `add -A`, it goes into the seed commit. `test_seed_ignores_generated_files` checks this with `ls-files`.
  - The patterns `__pycache__/`, `*.pyc` and `.pytest_cache/` contain no slash except a trailing one, so git matches them at any depth. The test covers the root `__pycache__/x.pyc`, `pkg/__pycache__/m.pyc`, a bare `a.pyc` and a nested `.pytest_cache/v/cache/lastfailed`, and checks that `git status --porcelain --untracked-files=all` is empty.
  - `test_seed_ignores_real_import_bytecode` produces real bytecode with `import stats`, with `PYTHONDONTWRITEBYTECODE` and `PYTHONPYCACHEPREFIX` removed, checks that the `.pyc` file exists, then checks that `git status` is clean.
- **Edge case: a task seeds its own `.gitignore`.**
  - With `"dist/"` and no trailing newline: line 140 adds `\n`, then the three entries are appended. The test checks `startswith(existing)`, that the first line is still `dist/`, that each entry appears exactly once, that the file ends in `\n`, and that `git status` is clean.
  - With all entries already present: `missing` is empty and the file is not rewritten. The test checks the same things.
  - The seeded `.gitignore` key still goes through the escape check, because it is written by the same loop.
- **Error and safety case.** `ValueError("seed file escapes the repository")` is unchanged. The list ignores only bytecode and the pytest cache.
  - `.env`, `*.log` and source files are not ignored, so the `.env` rule in `checks.PROTECTED_CONFIG` (checks.py:177-179) still sees them.
  - `test_seed_keeps_source_and_protected_config_visible` checks that `?? .env` and `?? new_module.py` both appear in `git status`.
  - No suite in `evals/` seeds a `.gitignore` or depends on `.pyc` or pytest-cache files (grep found nothing), so the adversarial suites lose nothing.
- **Root cause or symptom?** It fixes the root cause. The seed repo had no ignore rules, so git reported every test run's cache files as untracked. A committed `.gitignore` removes them for every tool that respects ignore rules: any agent or CLI that runs `git add -A` or `git status`, and `ls-files --exclude-standard` in `worktrees.reset_to`.
  - The platform's own checkpoint commits (worktrees.py:111-112) already skip the `GENERATED` names, so this fix mainly covers whatever else stages or lists files in the scratch repo. That is defence in depth that matches the goal, not a cover-up.
- **Placement.** The change is in the platform's seed creation, `evaluation._seed_repo`, which `run_suite` calls at line 235. It is not in an eval scratch repo, which fixes what went wrong in the first attempt.

## Checklist

- **Contract met:** yes. Seed repos get a committed `.gitignore` for `__pycache__`, and test runs can't leave cache files behind as untracked.
- **Interfaces compatible:** yes. The signature is unchanged, and `SEED_IGNORES` is a new module constant.
- **Error handling:** the escape check is unchanged.
- **Edge cases:** an existing file, a missing trailing newline and entries already present are all handled. A CRLF line or a variant without the slash (`__pycache__`) would get the canonical entry appended. That is harmless and not a literal duplicate.
- **Tests sufficient:** 5 cases (one is parametrized). They check `git status --porcelain --untracked-files=all` and `ls-files`, not just file contents. They run offline: local git and the per-test DATA_DIR.
- **Dependencies:** none added.
- **Security:** protected config and source files stay visible.
- **Performance:** negligible.
- **Docs:** the constant has a comment, and no user-facing docs are affected.
- **Execution evidence:** the developer reports `edit_check` OK on both files, `tests/test_eval_seed.py` with 5 passed and `tests/test_evaluation.py` with 4 passed. The 5 matches the 4 test functions plus 2 parameters. The gate runs the acceptance commands again itself (K283/K284). Nothing in that evidence contradicts the code.
- **Scope:**
  - I was told not to run commands, so I did not run `git diff`.
  - The new function body is the base `_seed_repo` (main checkout lines 128-141) plus the ignore block and the constant.
  - `tests/test_eval_seed.py` is new on this branch; it does not exist in the main checkout.
  - The branch's commits are `plan` and `seed_gitignore`. The gate's scope check settles whether any other file changed.

## Findings

FINDING: src/agent_company/evaluation.py:138 | A task-seeded `.gitignore` with CRLF line endings would not match the entries, and the appended lines would mix line endings. This is cosmetic: ignoring still works and no eval suite seeds such a file | Optional: compare against `line.strip()` of `text.splitlines()`.

FINDING: tests/test_eval_seed.py:16 | No test drives a full `run_suite` to show that the change-boundary/scope gate passes after a test run in the seed repo. Only `git status` is checked. That is enough for this contract, but not end to end | Optional follow-up: an offline `run_suite` smoke check with a check command that runs pytest in the seed repo.

NEXT: Let the gate re-run `uv run pytest -q tests/test_eval_seed.py` and `uv run pytest -q tests/test_evaluation.py`, and confirm the scope check lists only these two paths.

MESSAGE fact to developer: the fix in src/agent_company/evaluation.py `_seed_repo` and tests/test_eval_seed.py is approved; the two findings are optional.

OUTCOME: APPROVED_WITH_COMMENTS. `_seed_repo` commits a narrow `.gitignore` (`__pycache__/`, `*.pyc`, `.pytest_cache/`) that keeps existing content without duplicates. The escape check, `.env` and source files are unaffected. Tests check `git status` offline.

VERDICT: PASS
