# Review (iteration 2)

## Review the finished node --check wrapper and record the JS syntax check evidence (qa, done)

# QA review: goal 86 iteration 2, the `finish` diff (`10408d6..86d576f`)

**Verdict: PASS (approved with comments).** I could not run either command, so the gate's sandboxed rerun is the executable evidence (K619). The verdict rests on reading the diff of the five changed files. I tried each command once, as asked.

## Evidence from this session
| Command | Result (exact text) |
|---|---|
| (a) `python3 -I src/agent_company/edit_check.py src/agent_company/static/*.js` | `This command requires approval` |
| (b) `uv run pytest -q tests/test_node_check.py tests/test_gateway.py` | `This command requires approval` |

The refusals are expected before the merge. The server still loads the main checkout's `executors.py`, which has no `CLAUDE_CHECK`, so this read-only QA session gets no Bash list (K619).

The executable evidence is:
- the gate's sandboxed acceptance loop, `for f in src/agent_company/static/*.js; do node --check "$f" || exit 1; done`;
- its rerun of the pytest command.

The developer reported `37 passed` and an `OK` for each of the 17 static JS files. I did not reproduce either result.

## Logic verification
Iteration 1 already traced the core logic of `gate()` and `cli()`. This covers only the iteration-2 changes and the three cases asked for.

- **Normal: a read-only QA run gets the wrapper.**
  - A QA run with `["read","shell"]` at G2/G3 and a worktree gets `shell_ok` True and `writes` False, so `checks` is True and `shell` is False (`executors.py:362`).
  - Claude Code's tools are Read/Grep/Glob plus `CLAUDE_CHECK`, with the wrapper `Bash(python3 -I <server>/edit_check.py:*)` first.
  - New: because `grant.checks and not grant.shell`, the command also gets `--disallowedTools Bash(git diff --output:*)` (`executors.py:665-666`).
  - Copilot gets `--allow-tool shell(<CHECK_CMD>)`, `--deny-tool shell(git push)` and `--deny-tool shell(git diff --output)` (`executors.py:779-780`).
  - The `CHECK_HINT` line is in the prompt.
  - A writing developer run gets `CLAUDE_BASH` with the wrapper and no deny list. `test_git_diff_output_is_denied_to_read_only_runs` asserts both cases.
- **Edge: `CHECK_OK` false, so no entry.**
  - The `if CHECK_OK:` blocks skip the wrapper entry in all four lists.
  - In `gate()`, `checks = … and CHECK_OK` is False, so a read-only run gets no Bash list at all.
  - `dropped` gets `check: path not usable in a rule`.
  - `with_check_hint` also requires `CHECK_OK`, so no hint points at a path that cannot be used. The behaviour is correct, but no test covers it (see FINDING).
- **Error: `-r evil.js` is refused.**
  - `cli()` checks `arg.startswith("-")` first and prints `-r: option not allowed`, with exit 1.
  - `evil.js` is then only parsed by `["node","--check",<abspath>]`, never run.
  - `test_flags_that_run_code_are_refused` asserts the non-zero exit and that no `sentinel` file is created.
  - Removing the `if not args: return 2` branch changes no behaviour: `main()` calls `cli()` only when `len(sys.argv) > 1`.
- **Root cause:** fixed, not hidden. Read-only runs now get a check grant (`Grant.checks`) instead of nothing, and `node` is reachable only through a wrapper that builds node's command line itself.

## Checks against the task
1. **Copilot test:** done. `test_copilot_read_only_run_gets_only_the_check_shell` builds the real Copilot command line. It asserts:
   - `--allow-tool` includes `shell(<CHECK_CMD>)`, and not `write`, `shell(uv run)` or any `shell(node…)`;
   - `shell(git push)` and `shell(git diff --output)` are denied;
   - `CHECK_HINT` is in the prompt.
2. **`git diff --output`:** done, as a deny rule plus a test, applied to read-only check runs only. The limit of a prefix rule (`git diff HEAD --output=x`) is honestly written up under Known gaps in `docs/threat-model.md`.
3. **CLAUDE.md clause:** accurate. It covers:
   - the conditions: `shell` at G2+, a worktree, and the GM's plan run;
   - `CLAUDE_CHECK`/`COPILOT_CHECK`, pytest, git status/diff, `ls` and the wrapper;
   - `git diff --output` denied and raw `node` refused.

   This matches `gate()` and the lists.
4. **Lists and wrapper:** `CLAUDE_BASH` and `COPILOT_SHELL` were not touched in iteration 2. They have no raw `node` entry, and `test_no_rule_runs_node_directly` asserts it. The wrapper refuses every argument starting with `-`.

## Checklist
- **Contract:** met.
- **Interfaces:** compatible. The deny flags are only added to read-only check runs.
- **Error handling:** unchanged and correct.
- **Tests:** sufficient, apart from the `CHECK_OK` false gap below.
- **Dependencies:** none added.
- **Security:** improved by the `git diff --output` deny; the remaining gap is documented.
- **Performance:** no concerns.
- **Docs:** CLAUDE.md and `docs/threat-model.md` are updated.

FINDING: src/agent_company/executors.py:362-365 | No test covers `CHECK_OK` False, so a later change could add a wrapper entry or a hint pointing at a path that cannot be used in a rule. | Monkeypatch `CHECK_OK=False` in a test and assert `not grant.checks` and the `check: path not usable in a rule` text in `dropped`.
FINDING: src/agent_company/executors.py:614 | The `git diff --output` deny only matches as a prefix, so `git diff HEAD --output=x` still writes a file. This is documented under Known gaps; pytest's conftest already gives the same ability. | Leave it as documented for now; the PreToolUse guard below would close it.
FINDING: src/agent_company/executors.py:364 | The `dropped` text says `check:` where the other reasons say `checks:`. This is cosmetic. | Change it to `checks: path not usable in a rule`.

NEXT: After the merge and the restart, one QA run executes `python3 -I /home/luca/Agent_Company/src/agent_company/edit_check.py src/agent_company/static/*.js` in its session and records the real output. That run is the goal's own proof that QA can run the check.

OUTCOME: APPROVED_WITH_COMMENTS
VERDICT: PASS

PROPOSAL: A PreToolUse Bash guard for read-only agent runs :: Allow and deny rules only match the start of a command, so flags placed later (`git diff HEAD --output=x`) can only be blocked by parsing each command.
