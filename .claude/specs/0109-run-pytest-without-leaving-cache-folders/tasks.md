# Tasks (iteration 3)



## 1. Replace the runtime pytest.skip in tests/test_cache_free_runs.py with checks that always assert
- key: `drop_runtime_skip` · role: developer · writes: yes

Cause of the gate failure (FACT, checks.py SKIP_MARKER): tests/test_cache_free_runs.py:22 adds `pytest.skip(f"bubblewrap cannot start a sandbox here: ...")` in the autouse fixture `_usable`. The boundary check rejects any added `pytest.skip(`, `@pytest.mark.skip`, `skipTest(` or a reasonless xfail in a test file. Change ONLY tests/test_cache_free_runs.py:
1. Remove the `pytest.skip(...)` call and the `request.node.get_closest_marker("skipif")` branch from `_usable`. Keep the monkeypatch.delenv loop over FIX.
2. Remove the module-level `real = pytest.mark.skipif(...)` marker and its uses. Do NOT replace it with a skipif that calls sandbox.available() at import or collection time: sandbox.build() reads settings() and sandbox.home(), which would use the real DATA_DIR before conftest's `env` fixture sets the temporary one, and `_probe` would be cached globally.
3. Add an unconditional test, e.g. `test_the_sandbox_command_line_carries_the_fix(tmp_path)`. It builds `sandbox.build(PYTEST, wt, writable=[wt], env=sandbox.env_for(), limits=False)` and asserts that the argv contains `--setenv PYTHONDONTWRITEBYTECODE 1` and a `--setenv PYTEST_ADDOPTS <v>` whose value contains `-p no:cacheprovider`. Also assert that an existing PYTEST_ADDOPTS (e.g. `-x`) is kept when it is in the environment. Check sandbox.env_for's signature first and read the variable the way env_for does. build() does not run bwrap, so this test runs everywhere.
4. Rewrite `test_a_sandboxed_gate_command_leaves_no_caches`. Inside the test body, after the fixtures have run, read `usable, why = sandbox.available()`. If usable: run the real L3 gate as it does today (`info['isolation'] == 'L3'`, `caches(wt) == []`). If not usable (no bwrap, or nested inside the gate's own L3 sandbox): assert `contracts.run_command(..., mode='required')` returns ok False with a detail that says the sandbox is required and unavailable, and that `caches(wt) == []` (nothing ran, so nothing was written). Either branch asserts. No skip of any kind.
5. In `gate()`, use `shlex.join(PYTEST)` instead of `" ".join(PYTEST)` (earlier review FINDING; the interpreter path may contain spaces).
6. Keep the control runs (`control()`), which prove that the assertions fail without the fix. Do not remove any existing assertion (the boundary check counts them per file).
Run `uv run pytest -q -rs tests/test_cache_free_runs.py tests/test_executor_env.py tests/test_sandbox.py` serially, without -n. Report the summary line and the skip reasons (only test_sandbox.py's existing markers may skip). Also run `python3 -I /home/luca/Agent_Company/src/agent_company/edit_check.py tests/test_cache_free_runs.py`. Do not touch src/, CLAUDE.md, tests/test_executor_env.py or tests/test_sandbox.py.

Acceptance: {"type": "command", "run": "! grep -nE 'pytest\\.skip\\(|@pytest\\.mark\\.skip\\b|skipTest\\(|mark\\.xfail' tests/test_cache_free_runs.py"}; {"type": "command", "run": "grep -q 'no:cacheprovider' tests/test_cache_free_runs.py && grep -q 'shlex.join' tests/test_cache_free_runs.py"}; {"type": "command", "run": "uv run pytest -q tests/test_cache_free_runs.py tests/test_executor_env.py tests/test_sandbox.py", "stage": "integration"}

## 2. Review the skip-free cache tests and the cache fix end to end
- key: `qa_review` · role: qa · writes: no · after: drop_runtime_skip

Review the goal branch's whole change against main (`git diff main -- src/agent_company/sandbox.py src/agent_company/executors.py CLAUDE.md tests/test_cache_free_runs.py tests/test_executor_env.py`), focusing on tests/test_cache_free_runs.py as changed by drop_runtime_skip.
Verify:
(1) The file adds no pytest.skip(, @pytest.mark.skip, skipTest( or xfail, and no assertion was removed relative to the previous iteration (commit 995e23d).
(2) Logic and data flow. Normal case: on the host, the L3 branch really runs bwrap and asserts no __pycache__/.pytest_cache. Edge case: with the sandbox unusable, the test asserts the mode='required' refusal and the sandbox command line instead of passing vacuously. Error/control case: control() still shows both cache folders appear without the fix.
(3) The unconditional command-line test checks sandbox.build + env_for argv for PYTHONDONTWRITEBYTECODE=1 and -p no:cacheprovider, keeps an existing PYTEST_ADDOPTS, and does not probe bwrap at import or collection time against the real DATA_DIR.
(4) The earlier-failing files CLAUDE.md, src/agent_company/executors.py and src/agent_company/sandbox.py are covered by tests: executors._spawn via no_cache_env, sandbox.env_for/no_cache_env, and the CLAUDE.md sentence matching the code.
Run `uv run pytest -q -rs tests/test_cache_free_runs.py tests/test_executor_env.py tests/test_sandbox.py` serially and quote the summary line and any skip reasons. Then run `find . -name __pycache__ -o -name .pytest_cache | grep -v '^./.venv'` and confirm it lists nothing new under tests/ or src/. List FINDING lines (file:line | problem | fix), then OUTCOME and VERDICT: PASS or VERDICT: FAIL.

Acceptance: {"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}; {"type": "contains", "value": "FINDING"}; {"type": "min_length", "value": 400}
