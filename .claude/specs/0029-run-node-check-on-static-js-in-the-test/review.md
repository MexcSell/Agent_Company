# Review (iteration 1)

## QA review of the static JS syntax test (qa, done)

I couldn't run any commands in this session: every shell call, even a single `node --check`, needed an approval that wasn't given. So this verdict comes from reading the code and checking the files directly, plus the run output the developer recorded. I did not run anything myself.

## Logic verification

**Gate today (`src/agent_company/checks.py:100-130`).** `syntax_errors` only looks at files the goal changed. For `.js/.mjs/.cjs` it calls `_tool(["node","--check",rel])`. If node isn't on PATH, `_tool` returns `None` (line 101-102), and `None` counts as "no problem". So a dashboard syntax error passes whenever node is missing, or when the file wasn't touched by that goal. The new test closes the second gap: every top-level script is checked on every full test run, not just changed ones.

**Normal case: files are collected and `vendor/` is left out.**
- `STATIC = Path(agent_company.__file__).parent / "static"` (line 10).
- `SCRIPTS = sorted(STATIC.glob("*.js"))` (line 11) is not recursive, so `vendor/` stays out.
- I listed `static/**/*.js` separately and found exactly 17 top-level files. That matches the developer's count of 20 tests (1 + 17 + 2).
- Line 23 also asserts that every collected file sits directly in `static/`.
- The editable install points at this worktree: `.venv/.../_editable_impl_agent_company.pth` contains `/home/luca/Agent_Company/data/worktrees/goal-29/src`. So the test checks this branch's files, not another copy.

**Edge case: no dependence on the working directory, and an empty list fails.**
- The path comes from the package, so the directory pytest starts in doesn't matter.
- If the glob finds nothing, the parametrized test gets an empty parameter set and pytest skips it. But `test_scripts_found` (line 22) fails in that case, so the run as a whole fails.

**Error case: a broken file fails the test.**
- The per-file test asserts `returncode == 0` and puts the file name and the last 800 characters of stderr in the message (line 29).
- The negative test writes `function (\n` to a temp file and asserts a non-zero exit and `SyntaxError` in stderr (lines 32-37).
- The positive control (lines 40-44) shows the helper doesn't fail on everything.

This fixes the root cause the goal names: syntax errors in dashboard scripts are now caught by the full test suite, whether or not the task had permission to run node.

**Node missing.** A module-level `skipif` with the exact reason from the contract (lines 13-14), so the skip shows in the report. Inside the L3 sandbox, `/usr/local/bin` is on PATH (`src/agent_company/sandbox.py:126-127`), which is where the developer found node. So the gate's run shouldn't skip.

## Checklist
- **Contract:** all four points are met.
- **Interfaces:** nothing changed outside the test. The only file in the commit is `tests/test_static_js.py` (44 lines, commit dccf81f).
- **Error handling:** the failure message shows which file broke and why.
- **Timeouts:** there is one subprocess call, at line 18, and it has `timeout=60`.
- **Security:** no network, no database, no writes to `src/`. The only writes go to `tmp_path`.
- **Performance:** about 20 short node processes; the developer recorded 3.98s.
- **Dependencies:** none added.
- **Docs:** none needed.
- **Recorded output:** the developer reported `20 passed` for the test file, `874 passed` for the full suite, and `static-ok` for the static check. This agrees with what I checked: 17 files, the path resolves to this worktree, and the code reads as correct.

FINDING: tests/test_static_js.py:13 | The module-level skip also skips `test_scripts_found`. On a host without node the whole file shows as skipped, pytest exits 0, and the merge isn't blocked. This is what the contract asks for, but the goal's gap stays open on such hosts. | Fix: let an environment variable (e.g. `REQUIRE_NODE=1`) turn the skip into a failure in gate and CI runs, and keep `test_scripts_found` outside the skip.
FINDING: src/agent_company/checks.py:101-102 | Outside this task's scope: `_tool` still returns `None` when node is missing, so `syntax_errors` passes changed `.js` files without checking them and records nothing. | Fix: have `syntax_errors` add a warning such as "node not installed: x.js not checked" instead of passing silently.
FINDING: (process) | This session couldn't run the gate commands because shell calls needed approval. The verdict rests on reading the code plus the recorded run output. | Fix: re-run `uv run pytest -q tests/test_static_js.py -rs` in a session that can run commands, to confirm 20 passed and 0 skipped.

NEXT: The GM decides whether a missing node should fail the gate (the `REQUIRE_NODE` proposal).
MESSAGE recommendation to general_manager: tests/test_static_js.py meets its contract. The remaining gap is that a host without node only skips the check (tests/test_static_js.py:13, checks.py:101), and that needs a policy decision.

PROPOSAL: Report unchecked syntax in the gate :: `checks.syntax_errors` should warn when a file's checker (node, ruby, php, gofmt) is missing, instead of passing it silently.

OUTCOME: APPROVED_WITH_COMMENTS
VERDICT: PASS
