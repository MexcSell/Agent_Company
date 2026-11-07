# Review (iteration 1)

## Review the node --check allow-list and rerun the JS syntax check yourself (qa, failed)

# QA review: goal 86, the `node --check` check list (iteration 1, `impl` at 10408d6)

I could not run `node --check` or the tests in this session. Every form was refused, and the refusal texts are below. The verdict rests on reading the code. The gate has to rerun the tests.

## Evidence from this session
| Command | Result (exact text) |
|---|---|
| `node --check src/agent_company/static/*.js; echo "exit=$?"` | `This Bash command contains multiple operations. The following parts require approval: node --check src/agent_company/static/*.js, echo "exit=$?"` |
| `for f in src/agent_company/static/*.js; do node --check "$f" …; done` | `A variable in this command can't be checked before it runs` |
| `node --check src/agent_company/static/app.js` | `This command requires approval` |
| `python3 -I /home/luca/Agent_Company/src/agent_company/edit_check.py src/agent_company/static/app.js` (the server's copy) | `This command requires approval` |
| `python3 -I …/worktrees/goal-86/src/agent_company/edit_check.py src/agent_company/static/app.js` (the worktree's copy) | `This command requires approval` |
| `uv run pytest -q tests/test_node_check.py tests/test_gateway.py` and `pytest -q tests/test_node_check.py tests/test_gateway.py` | `This command requires approval` |

Only the auto-approved read-only commands (`git diff`, `git status`, `ls`) ran.

This is the problem the goal names, and it is expected before the merge. The running server loads `executors.py` from the main checkout (K619). That file has `CLAUDE_BASH` at line 592, no `CLAUDE_CHECK`, and still `shell = shell_ok and writes`. So a read-only QA run gets no Bash entries at all, not even pytest. The developer reported "46 passed", but that is their claim, not a result I reproduced.

## Logic verification

**`gate()` (`executors.py:352-375`)**
- **Normal:** a QA run with `("read","shell")` at G3 (the default), with a worktree. Loop task requests set `workdir=Path(goal.worktree)` at `loop.py:1002`.
  - `shell_ok` is true (line 352) and `writes` is false, so `shell` is false and `checks` is true (line 362).
  - `used` is `["read","shell"]` and isolation is L1.
  - `ClaudeCodeExecutor.tools()` returns Read/Grep/Glob plus `CLAUDE_CHECK`. There is no Edit/Write, and no `--permission-mode`/`--settings`, because those depend on `req.writes`.
  - The system prompt gets `CHECK_HINT` with the absolute path.
- **Edge:**
  - Without `shell`, `shell_ok` is false, so `checks` is false and nothing is added to `dropped`, because line 363 requires `"shell" in perms`.
  - At G1, `checks` is false and `dropped` gets `checks: autonomy G1`.
  - With `permissions=None` the run is `["read"]` and gets no checks.
  - The configure step is excluded.
- **Error:** if `CHECK_OK` is false (a path that can't be written in a rule), neither list gets the check entry and `dropped` gets `check: path not usable in a rule`.
- **Root cause:** the change fixes it. The cause was `shell = shell_ok and writes`. The fix adds a separate `checks` grant and leaves `shell` unchanged, so writing runs keep the same `dropped` texts.
- **`Grant` field order:** `checks` was inserted between `shell` and `mcp`. I searched for positional `Grant(` calls in `src/` and `tests/` and found none, so no call site shifts.

**`edit_check.cli()` / `main()`**
- **Normal:** `good.js` resolves under `root` and is a JS file, so `check()` runs `["node","--check",abspath]` with `NODE_OPTIONS`/`NODE_PATH` removed. It prints `OK good.js` and exits 0.
- **Edge:** a path outside the root, `../o.js`, `notes.md` or a directory is refused with exit 1. With no `node`, the exit is 3, and a later failure cannot lower it, because `max(3,1)` is 3.
- **Error:**
  - `bad.js`: `check()` returns node's stderr and the exit is 1.
  - `-r`, `--require=`, `--import=`, `--loader`, `-e`: the first branch refuses any argument that starts with `-`.
  - The other arguments in `-r ./evil.js good.js` are only parsed by `node --check`, never run, and the sentinel test asserts that. `-e 1`: `1` is not a file.
  - Node always gets an absolute path, so no argument can turn into a flag.
- **The no-argument call** still goes to the stdin hook unchanged.

**Executor wiring:** `ClaudeCodeExecutor.tools`/`_run` and `CopilotExecutor.tools`/`_run` match design §3.

## Checklist
- **Contract:** met. Developers and QA get the check only through the wrapper, and there is no raw `node` rule.
- **Interfaces:** `Grant` gains a keyword field with a default, so existing callers are compatible.
- **Error handling:** a missing `node` fails with exit 3 instead of passing.
- **Unchanged lists:**
  - `CLAUDE_BASH` and `COPILOT_SHELL` keep their eight original entries byte-identical. They only gain the wrapper through `append`, guarded by `CHECK_OK`.
  - `tools_for` is unchanged.
- **Tests:**
  - Design tests 1–4 and 6–10 are covered in `tests/test_node_check.py`.
  - Test 5 (the Copilot command line) is only partly covered (see FINDING).
  - The declared departures are acceptable: all tests in one file, and the extra `dropped` reasons.
- **Dependencies:** none added.
- **Security:**
  - The wrapper path is the server's own file, started with `-I`.
  - pytest at L1 is documented as a known gap.
  - One unlisted write path exists: `git diff --output` (see FINDING).
- **Performance:** no concerns.
- **Docs:**
  - `docs/threat-model.md` is updated: the new row after the edit-check row, the "Tool misuse" row, and the Known gap. The four tests it names exist.
  - `CLAUDE.md` is not updated. The developer declared it as outside their paths.

FINDING: (the server's) `src/agent_company/executors.py:592` and `:358` | In this QA session, `node --check src/agent_company/static/*.js`, the wrapper call and pytest are all refused (texts above), because the server still loads the main checkout's `gate()` (K619). So the goal's evidence cannot come from this iteration's in-session runs. | Merge and restart, then have one QA run execute `python3 -I /home/luca/Agent_Company/src/agent_company/edit_check.py src/agent_company/static/*.js` and report the real output. Until then, rely on the gate's sandboxed rerun.
FINDING: src/agent_company/executors.py:609 (`CLAUDE_CHECK` "Bash(git diff:*)"), :749 (`COPILOT_CHECK` "shell(git diff)") | `git diff --output=<path>` writes a file anywhere the server user can, so a run described as "no Edit/Write, L1" can still write. In practice pytest's conftest already allows that, so this adds little real risk, but the threat-model row understates it. | Name `git diff --output` beside pytest in the threat-model row and the Known gap, or add a deny rule `Bash(git diff --output:*)`.
FINDING: tests/test_node_check.py:77 | Design §5 test 5 is missing: no test builds a real Copilot command line for a read-only run. It would check `--allow-tool shell(<CHECK_CMD>)` and `--deny-tool shell(git push)`, and that the prompt hint is present. Also, whether Copilot's `shell(...)` rule matches a value with several words (`python3 -I /abs/path`) is unverified. | Add the `test_router.py`-style Copilot test once the path is allowed.
FINDING: src/agent_company/edit_check.py:47-49 | `cli()` documents "2 no files" and has `return 2`, but `main()` only calls `cli` when there are arguments, so that code never runs. A bare wrapper call exits 0 through the stdin hook, as the developer noted. | Correct the docstring, or have `main()` treat an interactive or empty stdin as exit 2.
FINDING: src/agent_company/executors.py:362 | `checks` also applies to the GM's `plan` run (`loop.py:588`: it has a worktree and does not write), which design §3 did not mention. This is harmless (pytest and the wrapper only), but undocumented. | Mention it in the threat-model row, or exclude `purpose == "plan"` if that is not wanted.
FINDING: CLAUDE.md (`executors.py` entry) | The design asks for a clause about `Grant.checks` and the `edit_check.py` wrapper, and it is missing. | The GM plans that one-line edit.

NEXT: The gate reruns `uv run pytest -q tests/test_node_check.py tests/test_gateway.py` and `node --check src/agent_company/static/*.js` in the L3 sandbox.
NEXT: After merge and restart, a QA run tries the wrapper with the literal `*.js` glob. If that is refused, it passes the file names one by one. It records the output.
MESSAGE request to general_manager: After the merge and restart, plan one QA task that runs the `edit_check.py` wrapper from the server's checkout on `src/agent_company/static/*.js`, plus the CLAUDE.md clause and the Copilot command-line test (design §5 test 5).

OUTCOME: APPROVED_WITH_COMMENTS
VERDICT: PASS

PROPOSAL: Deny rules for writing flags of allowed read-only commands :: `git diff --output` and similar flags let "read-only" check lists write files, and prefix allow-rules cannot exclude them.
