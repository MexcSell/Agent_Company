# Review (iteration 3)

## Review the skip-free cache tests and the cache fix end to end (qa, done)

The cache fix works and the new test file now runs with no skips. **Verdict: PASS (approved with comments).** All four points check out, but I could not confirm that `find` lists nothing new: my own outer pytest run updated one file in `tests/__pycache__`.

## Test run
`uv run pytest -q -rs tests/test_cache_free_runs.py tests/test_executor_env.py tests/test_sandbox.py`, run once without `-n`: **25 passed in 7.42s**, with no skip reasons listed. `git status --short` is empty afterwards.

## Cache folders in the worktree
`find . -name __pycache__ -o -name .pytest_cache | grep -v '^./.venv'` lists `./tests/__pycache__`, `./src/agent_company/__pycache__`, `./src/agent_company/mcp_servers/__pycache__` and `./.pytest_cache`.
- **From earlier runs:** the `src/` folders (19:06 and 19:20) and `.pytest_cache` (created 19:11) predate my run. All four are git-ignored, which is why `git status` stays clean.
- **From my run (19:47):** `tests/__pycache__/test_cache_free_runs.cpython-313-pytest-9.2.0.pyc` was rewritten. My shell does not have the fix's two variables (the live server's `_spawn` doesn't add them until this branch is merged and the service restarted), so that came from the outer pytest command.
- **Not mine:** `.pytest_cache/v/cache/nodeids` was written at 19:47:58, after my run finished, by some other pytest process.
- **Not from the code under test:** every pytest run the tests start happens in temporary directories, never in the goal worktree.

## Logic verification

**(1) No skips, no lost assertions.**
- `test_cache_free_runs.py` has no `pytest.skip(`, `mark.skip`, `skipif`, `skipTest(`, `xfail` or `importorskip`.
- Compared with commit 995e23d, the `real` marker and the `pytest.skip` in the fixture are gone.
- The three assertions of the old L3 test (`gate("required")` passes and prints "1 passed", `isolation == "L3"`, no caches) are all still there, inside the sandbox-usable branch.
- No other assertion was removed. A `control()` run was added before the L3 gate.

**(2) Data flow.**
- **Normal case (L3 on this host):** `test_a_sandboxed_gate_command_leaves_no_caches` reads `sandbox.available()` inside the test body.
  - If the sandbox is usable, it runs `control(_worktree("control"))`, which asserts both folders appear without the fix.
  - Then `gate("required")` → `run_command` → `sandbox.run`. The command runs under bwrap with `env_for({UV_OFFLINE…})` (sandbox.py:303), passed in as `--setenv` pairs after `--clearenv`.
  - It then asserts `isolation == "L3"` and no caches under the fresh worktree.
  - `tests/test_sandbox.py`'s own bwrap tests also skip when `available()` is false, and none skipped in the same session. So the L3 branch is the one that ran here.
- **Edge case (sandbox unusable):** `refused()` asserts that the command is refused, the detail says "the sandbox is required and unavailable", `info["isolation"] == ""`, and no caches exist.
  - `test_a_required_sandbox_that_cannot_start_writes_nothing` forces `sandbox.available` to return false, so this branch runs on every host.
  - It is not a vacuous pass: a sandbox that wrongly fell back to the host would give `isolation == "L2"` and fail the test.
- **Control case:** `control()` (lines 45-49) is unchanged and backs the L2 test, both `_spawn` cases and now the L3 branch.

**(3) Command-line test.**
- `test_the_sandbox_command_line_carries_the_fix` reads the `--setenv` pairs from `sandbox.build(PYTEST, wt, writable=[wt], env=sandbox.env_for(), limits=False)`. It asserts `PYTHONDONTWRITEBYTECODE == "1"`, that `PYTEST_ADDOPTS` contains `-p no:cacheprovider`, and that the pytest command ends the argv.
- With `sandbox_env=["PYTEST_ADDOPTS"]` and `PYTEST_ADDOPTS=-x`, it asserts `-x -p no:cacheprovider`, so an operator's value is kept, not replaced.
- `build()` never starts bwrap, and nothing runs at import or collection time. `prefs.set_value` writes to the per-test temporary `DATA_DIR` (conftest.py:18).

**(4) Coverage of the earlier-failing files.**
- `executors._spawn` → `no_cache_env`: two real-subprocess `_spawn` cases with controls, plus the fake-Popen test in `test_executor_env.py`.
- `sandbox.env_for` and `no_cache_env`: `test_executor_env.py`, the command-line test, and the L2 and L3 gate runs.
- CLAUDE.md: the sentence matches the code. It names `sandbox.no_cache_env` and `PYTEST_ADDOPTS`, the unavailable `--lf`/`--ff` options, and the L2, L3 and `_spawn` coverage.

**Root cause.** The fix removes the cause rather than hiding the folders. `PYTHONDONTWRITEBYTECODE=1` and `-p no:cacheprovider` are set where agent CLI runs and gate commands start, with no `.gitignore` masking.
- The branch's own source change (`git diff main...HEAD`) is unchanged since 89062bd: one import and `env=no_cache_env(env)` in executors.py, and `_no_cacheprovider` plus `no_cache_env` in sandbox.py.
- `ENV_KEEP` and `SECRETISH` are untouched.
- `pyproject.toml`, `.gitignore` and `tests/test_sandbox.py` are not in the branch diff.

## Checklist
- **Contract and interfaces:** contract met; interfaces unchanged.
- **Error handling:** the refusal path is tested.
- **Edge cases:** an operator's `PYTEST_ADDOPTS`, an unusable sandbox, and a value already present.
- **Tests:** sufficient, apart from the comments below.
- **Dependencies:** none added.
- **Security:** no wider environment.
- **Performance:** about 7s for the three files.
- **Docs:** updated.

**Merging with main.** Main has moved on since the merge base f5af0a5.
- Its changes to `sandbox.py`, `executors.py` and `contracts.py` are in other hunks than the branch's.
- `run_command` on main still returns the refusal note before its new `infra` branch, so `refused()` keeps passing after a merge.
- CLAUDE.md is the exception: both sides rewrote the same `executors.py` sentence. `git merge-tree` needed approval, so I could not test-merge.

## Findings
FINDING: CLAUDE.md:33 | Main rewrote the same `executors.py` sentence (the `Grant.checks`/CLAUDE_CHECK clause), and other lines nearby, since the merge base. The merge will almost certainly hit a text conflict here, and a careless resolution would drop main's text or the new cache clause. | Resolve by keeping main's sentence and appending the `PYTHONDONTWRITEBYTECODE … tests/test_cache_free_runs.py` clause after it.
FINDING: tests/test_cache_free_runs.py:101 | `test_a_sandboxed_gate_command_leaves_no_caches` takes the L3 or the refusal branch without saying which. Inside the gate's own L3 run it always takes the refusal branch, so a pass there does not show the L3 path left no caches. | Record the branch in the test output (for example `print("L3 ran" if usable else "refused")`, shown with `-rP`), or add an assertion that the `required` gate check itself reports `isolation == "L3"`.
FINDING: tests/test_cache_free_runs.py:122 | This is still open from iteration 2. The `_spawn` tests pass `req=None`, so `_track`/`_untrack` are never exercised, and no failing or timed-out child is tested. | Add a case with a real `executors.Request` and a failing `test_x.py`, and one with `timeout=1` on a sleeping test. Assert the expected return code or `TimeoutExpired`, no caches, and no leftover `LiveProcess` row.
FINDING: tests/__pycache__ (worktree) | The reviewer's own `uv run pytest` rewrote `test_cache_free_runs.cpython-313-pytest-9.2.0.pyc`, because this session's shell lacks the fix until the branch is merged and the service restarted. The folders are git-ignored and `git status` is clean, so this is no defect of the change. | After the merge and restart, re-run the `find` check from an agent run to confirm nothing new is written.

NEXT: Merge after the gate's integration stage passes, resolving CLAUDE.md:33 as described above.
MESSAGE recommendation to developer: in `tests/test_cache_free_runs.py`, report which L3 branch ran, and add `_spawn` failure and timeout cases that use a real `Request`.

OUTCOME: APPROVED_WITH_COMMENTS
VERDICT: PASS
