# Review (iteration 2)

## Review the integration test and the cache-free run paths (qa, done)

The cache-free fix (commit 89062bd) holds on every path I traced, and the new integration test is mostly sound. **Verdict: PASS (approved with comments).** I ran nothing and only read the code. Two gaps remain in `tests/test_cache_free_runs.py`: the L3 test has no control run of its own, and the error and timeout paths of `_spawn` are never tested.

## Logic verification

**Normal case: a gate command at L2.**
- `gate("off")` (test_cache_free_runs.py:55-62) calls `contracts.run_command(..., mode="off")`. That calls `sandbox.run(["/bin/sh","-c",cmd], ..., prepare=True)` at contracts.py:235 without an env.
- In `sandbox.run`, `mode == "off"` means `ok=False` (sandbox.py:297), so it runs on the host with `env_for()` (sandbox.py:301).
- `env_for()` sets `PYTHONDONTWRITEBYTECODE=1` (sandbox.py:130) and adds `-p no:cacheprovider` (sandbox.py:131). Both reach the pytest child through `/bin/sh`.
- The test's `_usable` fixture removes both variables from `os.environ` first (lines 23-24). So the values the child sees can only come from the fix, not from the outer gate run.
- The test asserts on a directory it creates: `_worktree()` makes a workspace and worktree under the per-test temporary `DATA_DIR` (conftest.py:18 → `worktrees.new_workspace`/`create`). It never looks at the goal worktree root, where old git-ignored caches sit.
- It checks for both folder names recursively (`rglob`, lines 33-34), checks `isolation == "L2"` (line 68), and runs a control first in a separate `control` worktree (line 66).

**Edge cases.**
- **Operator value already set:** `test_an_agent_cli_run_leaves_no_caches[addopts]` passes `PYTEST_ADDOPTS="-x"`. `no_cache_env` turns it into `-x -p no:cacheprovider` and adds nothing when it is already there (sandbox.py:139-143). The exact string is asserted in `tests/test_executor_env.py:47-50`. The integration test only shows the result: no caches.
- **Operator value on gate commands:** at L2 and L3 the operator's value is dropped by `ENV_KEEP`, which is unchanged and was not widened (sandbox.py:47-48). That was already so before this goal.
- **L3 skipped:**
  - The `real` marker states its reason: "bubblewrap is not installed".
  - When bwrap is installed but cannot start, `_usable` skips with `bubblewrap cannot start a sandbox here: <probe reason>` (line 22). Neither skip is silent.
  - The catch: the gate itself runs at L3, where a nested bwrap cannot create namespaces. So in the gate the L3 test is always skipped, and the L3 path is only exercised on the host. The developer reports that it passed there.

**Error case: the child fails or times out inside `_spawn`.**
- **Non-zero exit:** `_spawn` returns a `CompletedProcess`, and the test's `assert p.returncode == 0, p.stdout + p.stderr` (line 86) shows the child's output, so the failure is clear.
- **Timeout:** `_spawn` kills the process group, drains it and re-raises `TimeoutExpired` (executors.py:506-509), which pytest reports by name. `_untrack` runs in `finally` (executors.py:510-511), so at the code level the process is always untracked.
- **The test does not exercise this:**
  - It passes `req=None` (line 85). `_track` then reads `None.agent_id`, and the `AttributeError` is swallowed by its broad except (executors.py:377-380). So nothing is ever tracked, and untracking is never shown.
  - No test covers a failing or timed-out child.

**Root cause.** The fix removes the cause: no bytecode is written and pytest's cache plugin is switched off where the runs start. It does not rely on `.gitignore` hiding the folders. The developer made no `src/` change in this iteration; the only `src/` change is still 89062bd. In CLAUDE.md, one sentence now notes that `--lf/--ff/--sw/--nf/--cache-clear` are unavailable in agent and gate runs and names the new test. That is accurate: with the cache plugin off, `--lf` is an unrecognized argument.

## Checklist
- **Contract satisfied:** yes for L2 and `_spawn`; L3 only outside the gate.
- **Interfaces:** unchanged.
- **Error handling:** correct in the code, not tested.
- **Edge cases:** covered, apart from the operator's value on gate commands, which is the existing `ENV_KEEP` behaviour.
- **Tests sufficient:**
  - The L2 test and both `_spawn` cases are each backed by a control run that shows both folders appear without the fix (lines 48-52, 66, 81).
  - The L3 test (lines 72-76) has no control.
  - The developer says that switching the fix off makes all four tests fail. I could not confirm that, because I ran nothing.
- **Dependencies:** none.
- **Security:** `ENV_KEEP` and `SECRETISH` are unchanged, so nothing new leaks into the sandbox.
- **Performance:** a few short subprocess runs per test.
- **Docs:** CLAUDE.md is updated.
- **Unchanged files:**
  - `tests/test_executor_env.py` is still 93 lines and matches what I read before.
  - `pyproject.toml` has no cache or addopts setting.
  - `.gitignore` lists `__pycache__/` and `.pytest_cache/`; those entries look like they predate this goal.
  - I could not diff `tests/test_sandbox.py`, `pyproject.toml` or `.gitignore` byte for byte without git, so that part rests on the developer's report.

FINDING: tests/test_cache_free_runs.py:73 | The L3 no-cache assertion (line 76) has no control run in its own test. Run alone, or after a change to the control helper, it cannot show it would ever fail. And in the gate it is always skipped because a nested bwrap cannot start, so no gate run ever checks L3. | Call `control(_worktree("control"))` at the start of `test_a_sandboxed_gate_command_leaves_no_caches`, and list the L3 test as a host-only check in the goal's notes.
FINDING: tests/test_cache_free_runs.py:85 | `_spawn` gets `req=None`, so `_track` fails silently and the track/untrack path is never run. No test covers a failing or timed-out child, so a regression in `_spawn`'s `finally`/timeout handling, such as a leftover `LiveProcess` row or a process group left alive, would pass. | Pass a minimal `executors.Request` with goal and agent ids. Add one case with a failing `test_x.py` that asserts `returncode != 0` and still no caches, and one with `timeout=1` on a sleeping test that asserts `TimeoutExpired`. In both, assert there is no `LiveProcess` row afterwards.

NEXT: Let the loop's checkpoint commit take `tests/test_cache_free_runs.py` and the CLAUDE.md line. Then run the integration stage, `uv run pytest -q tests/test_cache_free_runs.py tests/test_executor_env.py`, with `stage: "integration"` so the gate's mandatory integration check actually runs (K794).
MESSAGE recommendation to developer: in `tests/test_cache_free_runs.py`, add a control run to the L3 test and a non-zero/timeout `_spawn` case with a real `Request` that asserts no `LiveProcess` row is left.

OUTCOME: APPROVED_WITH_COMMENTS
VERDICT: PASS

## Security review of 'Integration test: gate commands and agent CLI runs leave no cache folders in a fresh worktree' (qa, done)

## Security review: integration_test (commit 403ce61)

## Logic verification
The commit adds `tests/test_cache_free_runs.py` and one sentence in CLAUDE.md. `git diff 89062bd HEAD -- src/` is empty, so the fix under test is unchanged.

**`_usable` fixture** (line 19)
- Normal case: it removes `PYTHONDONTWRITEBYTECODE` and `PYTEST_ADDOPTS` from the test process's environment. The outer gate or agent run sets both, so without this only the outer environment would keep the caches away. With it, only the fix under test can.
- Edge case: only the `@real` test carries `skipif`. That test is skipped when bwrap is installed but cannot create namespaces, which is the case inside the gate's own L3 run.
- Error case: `monkeypatch.delenv(..., raising=False)` does not fail when a variable is missing.

**`control(root)`** (line 48)
- Normal case: the same pytest run with both variables stripped must create both `__pycache__` and `.pytest_cache`.
- This proves the later "no caches" assertions can actually fail. If the control created no caches, the test would fail rather than pass by accident. The developer also reports that all 4 tests failed when the fix was switched off in the test process.

**`gate(mode)` and the L2 and L3 tests** (lines 55–76)
- Normal case: a fresh `_worktree()` under the temporary per-test DATA_DIR (conftest.py:18) runs through the real `contracts.run_command`. That covers `ensure_sandboxed`, `check_command`, `sandbox.run` and `env_for`.
- Each test asserts that the command passed, the isolation level (`L2` or `L3`), "1 passed", and that no cache folders exist anywhere under the worktree.
- Edge case: the control uses its own worktree name, `control`, because `worktrees.create` with the same name returns the existing worktree. The control's caches therefore cannot hide the result of the real run.
- Error case: if the command is refused or fails, `assert ok, detail` shows the reason.

**`test_an_agent_cli_run_leaves_no_caches`** (line 79)
- It drives the real `Executor._spawn` (executors.py:491) and a real Popen with two inputs:
  - `env=None`, which is `claude_env`'s default and the Codex and Copilot path;
  - an explicit environment without both variables plus `PYTEST_ADDOPTS=-x`, which checks that an operator's options are kept.
- `req=None` only reaches `_track` and `_untrack`, so it is harmless.

**Root cause or symptom?** These tests check the root-cause fix through the real gate and executor paths at the folder level. They do not hide anything, for example with `.gitignore`.

**Evidence I ran:**
- `uv run pytest -q -rs tests/test_cache_free_runs.py tests/test_executor_env.py` gave **13 passed in 4.16s**, with nothing skipped. The L3 test therefore ran under bwrap on the host.
- The developer reports 19 passed and 4 skipped (nested bwrap) inside the L3 sandbox. I did not reproduce that run.

## Checklist
- **Contract satisfied:** yes. This is a command-runnable integration test of the changed parts together (`contracts.run_command`, `sandbox.env_for`, `executors._spawn`), which K795 says the iteration-1 attempt was missing.
- **Interfaces compatible:** no `src/` changes.
- **Error handling:** assertions carry stdout and stderr, and every subprocess has a 120-second timeout.
- **Edge cases:** L2, L3, `env=None`, and an existing `PYTEST_ADDOPTS`.
- **Tests sufficient:** yes, with control runs. One limit: inside the gate's L3 run the L3 case is skipped (see findings).
- **Dependencies:** none added.
- **Security:**
  - **Files:** everything the tests write is under pytest's `tmp_path` or the temporary DATA_DIR, never the goal worktree or `data/`.
  - **Network:** none.
  - **Secrets:** the `_spawn` and control runs give a child pytest a copy of the test process's environment. The child only runs the seeded two-line test, and only its output reaches the assertion message, never the environment.
  - **Injection:** the shell command is a fixed string built from `sys.executable`.
  - **Authentication and permissions:** none touched.
- **Performance:** a few seconds per run.
- **Docs updated:** yes. The CLAUDE.md sentence names the unavailable cache flags and the new test file.

## Findings
FINDING: tests/test_cache_free_runs.py:59 | `" ".join(PYTEST)` builds the `/bin/sh -c` command without quoting, so an interpreter path containing a space or shell metacharacter would make the L2 and L3 tests fail for reasons unrelated to the fix. There is no injection risk, because the path comes from the test host, not from an agent. | Use `shlex.join(PYTEST)`.

FINDING: tests/test_cache_free_runs.py:72 | The gate runs its integration command inside L3, where a nested bwrap cannot create namespaces, so the L3 case is always skipped there. A regression in `env_for` would still be caught by `tests/test_executor_env.py::test_env_for_has_both` and by the L2 test, which uses the same `env_for`, but nothing in the gate would run L3 itself. | Accept this as it is and note it in the task output, or also run the file once on the host when merging.

NEXT: Run the goal's integration-stage command, `uv run pytest -q tests/test_cache_free_runs.py tests/test_executor_env.py tests/test_sandbox.py`, at the gate, then do the merge review.

MESSAGE fact to developer: The security review of 403ce61 (`tests/test_cache_free_runs.py`) found no secrets, injection, file, network or permission problems. The only suggestion is `shlex.join` at line 59.

OUTCOME: APPROVED_WITH_COMMENTS
VERDICT: PASS
