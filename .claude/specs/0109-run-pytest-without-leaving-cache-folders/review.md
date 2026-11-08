# Review (iteration 1)

## Review the cache-free run environment (qa, done)

I read the three files and traced every path the task names. **Verdict: PASS (approved with comments).** I ran no commands, as instructed; the tests that run real subprocesses are left to the gate.

## Logic verification

**`sandbox._no_cacheprovider` / `no_cache_env` (sandbox.py:136-152)**
- **Normal case:** `no_cache_env(None)` copies `os.environ` (line 149), sets `PYTHONDONTWRITEBYTECODE=1` and turns `""` into `"-p no:cacheprovider"`.
- **Operator value kept:** `{"PYTEST_ADDOPTS": "-x"}` becomes `"-x -p no:cacheprovider"`. The value is appended, not replaced.
- **Applied twice:** the check `"no:cacheprovider" in addopts` (line 141) returns the value unchanged, so the option is never added twice. It also catches an operator's own `-pno:cacheprovider`.
- **Input dict:** it is copied and never changed (line 149).

**`Executor._spawn` (executors.py:498-499)** now passes `env=no_cache_env(env)`.
- **Claude Code when `claude_env` returns None** (executors.py:603 → 662): the child gets a copy of `os.environ` plus both settings. Before, Popen's `env=None` inherited the same environment, so nothing new is exposed. The Bash tool's `pytest`/`uv run` children inherit it as well.
- **Claude Code with `claude_env` set:** its dict, which already starts from `os.environ`, gets both settings added.
- **Codex** (executors.py:702) and **Copilot** (executors.py:744) pass no env, so they take the same path as the None case.
- **Contracts unchanged:** `claude_env` (597-613) and `CLAUDE_BASH` (593-594) look as before.

**`sandbox.env_for` (sandbox.py:120-133)** adds line 131 right after the `PYTHONDONTWRITEBYTECODE` line and before `env.update(extra or {})`, so `extra` still overrides it. The test at test_executor_env.py:58 checks this. The secret filtering (lines 122-124: `ENV_KEEP`, `SECRETISH`, admin-named `sandbox_env`) is untouched.

**Gate commands** (`contracts.run_command` → `sandbox.run(...)` at contracts.py:235, with no env passed):
- **L3:** line 303 calls `env_for({UV_OFFLINE…})`. bubblewrap clears the environment and sets each key, so both settings reach the command.
- **L2 fallback** (sandbox off, or auto with bubblewrap missing): line 301 calls `env_for()`, so both settings reach the command.
- **Sandbox required but unavailable:** nothing runs, so there is nothing to cache.

**Root cause:** fixed, not hidden. Python stops writing bytecode and pytest's cache plugin is switched off where the runs start. No ignore rule masks the folders.

**Other callers, outside the task:** the self-evolution benchmark (selfevolve.py:270-276) builds its own env without `PYTEST_ADDOPTS`. Its tree is mounted read-only and it runs a plain script, not pytest, so it cannot leave cache folders.

## Checklist
- **Contract satisfied:** yes, for agent CLI runs and for gate commands at L2 and L3.
- **Interfaces compatible:** `_spawn`'s signature is unchanged. `sandbox` only imports `config`, so the new import in executors.py:25 creates no cycle.
- **Error handling:** nothing new can raise.
- **Edge cases:** an operator's value, a second application and the `extra` override are all handled.
- **Tests sufficient:** mostly.
  - `caches()` searches recursively (`rglob("*")`) for both `__pycache__` and `.pytest_cache`.
  - `test_fault_without_helper` shows that both folders do appear without the settings, so the no-cache test proves something.
  - The helper module the test imports makes sure a `__pycache__` would actually be written.
  - `_spawn` is tested through a fake Popen, with and without an env.
  - Gap: `sandbox.run` on the L2 path is only covered indirectly, through `env_for()`.
- **Dependencies:** none added.
- **Security:** nothing is weakened. Agent CLIs get the same environment they inherited before, plus two harmless variables.
- **Performance:** negligible.
- **Docs:** CLAUDE.md's `executors.py` entry now names `sandbox.no_cache_env`.
- **pyproject.toml / .gitignore:** no change shows up in these files. Without git I could not confirm they are byte-identical, so that part rests on the developer's report.

FINDING: src/agent_company/sandbox.py:47 | `PYTEST_ADDOPTS` is not on `ENV_KEEP` (it starts with `PY` but not `PYTHON`), so an operator's server-side pytest options are silently dropped from gate commands at L2 and L3 unless named in `sandbox_env`. This was already so before the change, and the cache option is still applied. | Add `"PYTEST_ADDOPTS"` to `ENV_KEEP` (it contains no secret-like word), or document that it has to be named in `sandbox_env`.
FINDING: tests/test_executor_env.py:53 | The L2 fallback is only tested indirectly through `env_for()`. A later change to `sandbox.run` line 301 (for example `env or {}`) would go unnoticed. | Add a test that calls `sandbox.run([sys.executable, "-m", "pytest", "-q", "test_x.py"], tmp_path, 120, mode="off")` and asserts `caches(tmp_path) == []` and `isolation == "L2"`.

NEXT: Merge after the gate's unit stage (`tests/test_executor_env.py`, `tests/test_sandbox.py`) passes. The two findings can be a small follow-up.
MESSAGE recommendation to developer: optionally add `PYTEST_ADDOPTS` to `sandbox.ENV_KEEP` and an L2 `sandbox.run(mode="off")` no-cache test in `tests/test_executor_env.py`.

OUTCOME: APPROVED_WITH_COMMENTS
VERDICT: PASS

## Security review of 'Stop agent and gate pytest runs from writing __pycache__ and .pytest_cache' (qa, done)

## Security review: fix_env (commit 89062bd)

## Logic verification

**`sandbox._no_cacheprovider(addopts)`** (sandbox.py:139)
- Normal case: `""` becomes `"-p no:cacheprovider"`, and `"-x"` becomes `"-x -p no:cacheprovider"`.
- Edge case: a value that already contains `no:cacheprovider`, including `-pno:cacheprovider`, comes back unchanged, so the option is never added twice. `tests/test_executor_env.py:47` covers this.
- Error case: there is no failure path. The input is always a string, because `env.get(..., "")` supplies `""` when the variable is missing.

**`sandbox.no_cache_env(env)`** (sandbox.py:146)
- Normal case: it returns a copy of `env` with `PYTHONDONTWRITEBYTECODE=1` and the option added to `PYTEST_ADDOPTS`.
- Edge case: with `env=None` it copies `os.environ`. That is exactly what `Popen(env=None)` inherited before, so the child process gets the same variables plus these two.
- Input handling: the dict passed in is not changed (test at line 41).

**`sandbox.env_for`** (sandbox.py:131)
- The allow-list filter is unchanged. Only a constant option is added to `PYTEST_ADDOPTS`, and `extra` is applied afterwards, so it can still override it (test at line 58).
- Every gate, acceptance and milestone command takes this path. `contracts.py:235` calls `sandbox.run` without `env`, so it falls back to `env_for()`. That holds for L3, and also for L2 when the sandbox is off or unavailable (sandbox.py:301 and 303).

**`Executor._spawn`** (executors.py:499)
- It covers Claude Code (`claude_env`, which returns either a dict or None), Codex (line 702) and Copilot (line 744).
- When `claude_env` returns a dict, `CLAUDE_CONFIG_DIR`, `ANTHROPIC_BASE_URL` and `ANTHROPIC_API_KEY` pass through unchanged. Nothing new is copied in.

**Root cause or symptom?** The root cause is fixed. Python stops writing bytecode, and pytest's cache plugin is switched off, at the point where every agent process and every gate command starts. Child processes, such as the Bash tool inside the CLIs and `uv run pytest`, inherit both settings.

**Evidence I ran:**
- `uv run pytest -q tests/test_executor_env.py tests/test_sandbox.py` gave **19 passed in 4.97s**.
- The test file contains a real reproduction of the fault: without the two settings both cache folders appear (`test_fault_without_helper`). It also contains a real pytest run with the helper that leaves none (`test_real_pytest_run_leaves_no_caches`).

## Checklist
- **Contract satisfied:** yes. Gate commands go through `env_for`, and agent CLI runs go through `_spawn`. `CLAUDE_BASH` and `claude_env` are unchanged, as the project rules require.
- **Interfaces compatible:** `_spawn`'s signature is unchanged and `env_for`'s return type is unchanged. The one incompatibility is the cache flags (first finding below).
- **Error handling:** nothing can fail.
- **Edge cases:** an existing operator value is kept, the option is never duplicated, `extra` can still override it, and `env=None` is handled.
- **Tests sufficient:** yes. They cover the helper, `env_for`, a real subprocess, and `_spawn` with a fake Popen, with and without an env.
- **Dependencies:** none added. There is no import cycle, because `sandbox` only imports `config`.
- **Security:**
  - **Secrets:** no new exposure. `env_for` still drops names that look like credentials and keeps only allow-listed ones. `no_cache_env(None)` passes on the same environment the agent CLIs already inherited.
  - **Injection:** none. The added value is a fixed string. pytest splits `PYTEST_ADDOPTS` into arguments itself and no shell evaluates it. An existing value can only come from the server's environment or the admin's `sandbox_env` setting, never from an agent.
  - **Files and network:** no change to sandbox binds, `_secrets`, network or worktree confinement.
  - **Authentication and permissions:** untouched.
- **Performance:** negligible. Imports are no longer cached, which `env_for` already did for gate commands.
- **Docs updated:** yes, the `executors.py` entry in CLAUDE.md. The threat model needs no change, since there is no new executor, provider or network path.

## Findings
FINDING: src/agent_company/sandbox.py:131, src/agent_company/executors.py:499 | With the cache plugin off, any agent or check command that uses `pytest --lf/--ff/--sw/--nf/--cache-clear` now stops with "unrecognized arguments" (exit 4). I reproduced this with `uv run pytest -q -p no:cacheprovider --lf …`. Nothing in the repository uses these flags today, but an agent or a goal's `--check` could. | Mention this in the CLAUDE.md sentence and in the gate's failure hint, or add a test that pins the behaviour so it is a deliberate choice.

FINDING: data/worktrees/goal-109 (`.pytest_cache/`, `tests/__pycache__/`, `src/agent_company/__pycache__/`) | These folders already exist in this worktree. They were written between 19:11 and 19:20, before the commit at 19:24, by runs under the old code. They are git-ignored (`!!`), so the scope check does not flag them. But they show that the fix only applies once this branch runs on the server. | Clean the worktree before the goal's final "no cache folders" check, or run that check from a fresh checkout.

FINDING: fix_env output (text) | The developer wrote "Nothing is committed yet", but the change is committed as 89062bd ("Goal 109 iteration 1: fix_env"). Reviewers could look at the wrong state. | The handover should name the commit.

NEXT: Before merging, run the goal's acceptance check from a fresh worktree on the new code, and confirm there is no `__pycache__/` or `.pytest_cache/` under it afterwards.

MESSAGE fact to developer: The security review found no secrets, injection or permission problems in the fix_env commit 89062bd (`sandbox.py:131`/`146`, `executors.py:499`). The only behaviour change to watch is that pytest's cache flags (`--lf` and similar) now stop with an error.

OUTCOME: APPROVED_WITH_COMMENTS
VERDICT: PASS
