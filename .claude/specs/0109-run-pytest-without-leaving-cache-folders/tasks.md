# Tasks (iteration 1)



## 1. Stop agent and gate pytest runs from writing __pycache__ and .pytest_cache
- key: `fix_env` · role: developer · writes: yes

Platform repository (src/agent_company). Read executors.py (Executor._spawn ~line 490, claude_env ~596, the _spawn calls at ~661, ~701, ~743) and sandbox.py (env_for ~118) before changing them.
1. Reproduce the fault first. In a tmp dir, write a trivial test_x.py. Run `sys.executable -m pytest -q test_x.py` with the current server-like env, and note in your answer that __pycache__/ and .pytest_cache/ appear.
2. In executors.py, add a small helper, e.g. `def no_cache_env(env: dict[str, str] | None) -> dict[str, str]`. It starts from a copy of `env` if given, else `os.environ`, and sets `PYTHONDONTWRITEBYTECODE=1`. It appends `-p no:cacheprovider` to any existing `PYTEST_ADDOPTS`, separated by a space, and never overwrites an operator's value. It does not append the option twice if it is already present. `_spawn` must always pass `env=no_cache_env(env)` to Popen, so Claude Code (also when claude_env returns None), Codex and Copilot all get it. Do not change claude_env's return contract or CLAUDE_BASH.
3. In sandbox.env_for, add the same PYTEST_ADDOPTS handling after the existing PYTHONDONTWRITEBYTECODE line. Reuse one function: put it in sandbox.py and import it in executors, or the other way round, without creating an import cycle. Keep `extra` able to override it.
4. Add a NEW test file tests/test_executor_env.py. Do not edit existing test files. It must cover: (a) the helper with an empty dict and with None (falls back to os.environ via monkeypatch); (b) a pre-set PYTEST_ADDOPTS='-x' becomes '-x -p no:cacheprovider', and calling the helper twice does not duplicate it; (c) sandbox.env_for() contains both settings; (d) a real check: in tmp_path, write test_x.py with `def test_ok(): assert True` and a module it imports. Run `subprocess.run([sys.executable, '-m', 'pytest', '-q', 'test_x.py'], cwd=tmp_path, env=<helper output>)`. Assert rc 0 and that no `__pycache__` or `.pytest_cache` exists anywhere under tmp_path (rglob). (e) _spawn passes the env: monkeypatch subprocess.Popen in executors with a fake that records the `env` kwarg (or test the helper through the code path _spawn uses) and assert PYTHONDONTWRITEBYTECODE=='1'.
5. Do NOT edit pyproject.toml, .gitignore or CI files: change boundaries flag manifests and protected config.
6. Grep tests/ for `config.cache`, `cache.get`, `--lf`, `--ff` and `cacheprovider`. Report whether any test relies on the cacheprovider. Then run the whole suite once serially: `uv run pytest -q` (no -n; there is no xdist). Report its summary line.
7. Run `python3 -I /home/luca/Agent_Company/src/agent_company/edit_check.py src/agent_company/executors.py src/agent_company/sandbox.py tests/test_executor_env.py`.
Optionally add a short clause to CLAUDE.md's executors.py description: agent and gate runs get PYTHONDONTWRITEBYTECODE and `-p no:cacheprovider`.

Acceptance: {"type": "file_exists", "path": "tests/test_executor_env.py"}; {"type": "command", "run": "uv run pytest -q tests/test_executor_env.py tests/test_sandbox.py", "stage": "unit"}; {"type": "command", "run": "grep -q 'no:cacheprovider' src/agent_company/sandbox.py && grep -q 'no:cacheprovider\\|no_cache_env' src/agent_company/executors.py", "stage": "static"}

## 2. Review the cache-free run environment
- key: `qa_review` · role: qa · writes: no · after: fix_env

Review the delivered change by reading the code only. Do not run commands: your tools are read-only, and the gate already runs the tests. Read src/agent_company/executors.py (_spawn, its three callers, claude_env), src/agent_company/sandbox.py (env_for, run) and tests/test_executor_env.py. Trace the data flow for: (normal) a Claude Code run where claude_env returns None, and a Codex run that passes no env: does the child process get PYTHONDONTWRITEBYTECODE=1 and '-p no:cacheprovider'? (edge) the operator already set PYTEST_ADDOPTS, or the helper is applied twice: is the value appended, not clobbered or duplicated? Does `extra` in env_for still override it? (error) a gate command on the L2 fallback (sandbox off or unavailable): does it still get the env? Also check that no secret-filtering in env_for was weakened, that claude_env's contract and CLAUDE_BASH are unchanged, that pyproject.toml/.gitignore were not edited, and that the real-subprocess test checks recursively for both __pycache__ and .pytest_cache. Write one line per issue as 'FINDING: <file>:<line> | <problem> | <fix>'. End with an OUTCOME line and 'VERDICT: PASS' or 'VERDICT: FAIL'.

Acceptance: {"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}; {"type": "contains", "value": "OUTCOME"}; {"type": "min_length", "value": 400}

## 3. Security review of 'Stop agent and gate pytest runs from writing __pycache__ and .pytest_cache'
- key: `fix_env-security` · role: security · writes: no · after: fix_env

Review the change made by task 'fix_env' for security: secrets, injection, unsafe file and network access, authentication and permission changes. Verify the logic first.

Acceptance: {"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}
