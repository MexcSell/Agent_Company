# Specification: Allow `node --check src/agent_company/static/*.js` for agent sessions

## Specify a read-only node --check allow-list for developer and QA sessions (architect, done)

# Design: a read-only `node --check` allow-list for developer and QA sessions (goal 86, EL-R-05)

Save this text at `.claude/specs/0086-allow-node-check-src-agent-company-stati/design.md`. I changed no repository files.

Labels: **FACT** means I read it in the code at commit a7dc53c. **INFERENCE** follows from those facts. **HYPOTHESIS** is not verified: I asked to run a local probe of the `node` flags and it was not approved, so every statement about what Node does with extra flags is unverified.

## 1. Why the check cannot run today

1. **FACT**: Claude Code's Bash allow-list `CLAUDE_BASH` (`src/agent_company/executors.py:592-593`) has `pytest`, `python -m pytest`, `uv run`, `npm test`, `npm run`, `git status`, `git diff` and `ls`. It has no `node` entry. Claude Code gets only the tools passed in `--allowedTools` (`executors.py:636`), and refused calls are recorded as `denied_tools` (`executors.py:676-677`).
2. **FACT**: Copilot's list `COPILOT_SHELL` (`executors.py:719-720`) has the same entries and no `node`. Copilot refuses any tool that is not allowed in prompt mode (`executors.py:724-725`).
3. **FACT**: `gate()` (`executors.py:329-368`) sets `shell = shell_ok and writes` (`executors.py:357`). A run that does not write never gets commands, even with the `shell` permission at G2 or higher. QA's default permissions are `("read", "shell")` (`src/agent_company/org.py:92`). A QA review is not a writing run, so its grant has `shell=False`. `ClaudeCodeExecutor.tools` (`executors.py:624-627`) then returns only `Read, Grep, Glob`, and `CopilotExecutor.tools` (`executors.py:728-729`) returns `[]`.
4. **INFERENCE**: A QA review on Claude Code or Copilot cannot run any command, so not `node --check` either. A developer run gets Bash, but no rule matches `node --check src/agent_company/static/*.js`. That explains why the JS syntax check was unverifiable in goal 26.
5. **FACT**: `edit_check.py` already runs `["node", "--check", path]` (`edit_check.py:28-30`), but only as a PostToolUse hook on writing runs (`executors.py:615-618`, `:637-638`). It reads a hook event from stdin (`edit_check.py:37-39`) and has no command-line file mode. Today `python3 -I edit_check.py a.js` blocks on stdin, and with empty stdin it exits 0. **INFERENCE**: as it stands it cannot serve as a check command.
6. **FACT**: `governance.check_command` (`governance.py:156-170`) is called for check, acceptance and schedule commands (`contracts.py:283`, `loop.py:73-75`, `projects.py:287`, `schedules.py:81`, `exercises.py:93`, `management.py:109`). It is not called for a Bash call inside a Claude Code or Copilot session. **INFERENCE**: a refusal pattern there would not stop anything an agent types into its session.

## 2. Permission entries to add

New constants in `executors.py`, next to `EDIT_CHECK_SETTINGS` (`:614`):

```python
EDIT_CHECK = Path(__file__).with_name("edit_check.py")  # the server's copy, never the worktree's
CHECK_CMD = f"python3 -I {EDIT_CHECK}"
# the syntax check only through the platform's wrapper: a raw `node` prefix would also allow -r/--import (§4)
CLAUDE_CHECK = [f"Bash({CHECK_CMD}:*)", "Bash(pytest:*)", "Bash(python -m pytest:*)", "Bash(uv run pytest:*)",
                "Bash(git status:*)", "Bash(git diff:*)", "Bash(ls:*)"]
COPILOT_CHECK = [f"shell({CHECK_CMD})", "shell(pytest)", "shell(python -m pytest)", "shell(uv run pytest)",
                 "shell(git status)", "shell(git diff)", "shell(ls)"]
```

- Add `f"Bash({CHECK_CMD}:*)"` to `CLAUDE_BASH` and `f"shell({CHECK_CMD})"` to `COPILOT_SHELL`. Writing runs then get the check as well.
- Add no `Bash(node --check:*)` or `shell(node --check)` entry; §4 explains why. **FACT**: `EDIT_CHECK` is the path of the running server's own module (`Path(__file__)`). The goal's worktree copy, `data/worktrees/goal-N/src/agent_company/edit_check.py`, is a different file. **INFERENCE**: an agent can edit the worktree copy, but that does not change the script its rule allows. `-I` keeps the worktree's modules and `PYTHON*` variables out.
- If `str(EDIT_CHECK)` does not fully match `[A-Za-z0-9_./-]+`, leave both check entries out and add `check: path not usable in a rule` to `dropped`. **INFERENCE**: rule syntax has no quoting.
- The test entries in the check list are narrowed to the pytest forms. A check-only run gets no `uv run:*`, `npm run:*` or `npm test:*`. **INFERENCE**: `uv run python -c …` and npm scripts run any code, including file writes, which would undo "no writes".
- **Codex**: `CodexExecutor` runs `--sandbox read-only` when `req.writes` is false (`executors.py:697`). The comment at `executors.py:355-356` says Codex's read-only sandbox "may still run read-only commands" (**FACT**, a code comment). **INFERENCE**: `node --check file.js` reads the file and writes nothing, so Codex already allows it in read-only runs, inside Codex's own sandbox. Codex needs no change. **HYPOTHESIS** (memory note, not checked here): on this host the Codex bubblewrap sandbox needs `u:root:x` on `/home/luca`, and read-only Codex could not run `uv`.

## 3. A separate check-only grant for read-only runs

- **New field** on `Grant` (`executors.py:47-56`): `checks: bool = False`, documented as "the run may run the syntax check and the test commands, without writes".
- **Rule in `gate()`**, after `shell` (`:357`):
  ```python
  checks = shell_ok and req.workdir is not None and not configure
  if not checks and not writes and kind in CLI_KINDS and "shell" in perms and n < 2:
      dropped.append(f"checks: autonomy G{n}")
  ```
  The existing autonomy and permission rules still apply. `shell_ok` already requires a CLI kind, the `shell` permission and G2 or higher (`:351`). A request without permissions is read-only `["read"]` (`:332`) and gets no checks. The configure step gets no checks.
- `shell` keeps its meaning: commands for a writing run. Nothing in §1 FACT 3 changes for writing runs, including the text of the `dropped` entries.
- **Recorded permissions**: `used` gets `"shell"` when `shell or checks`. Isolation stays **L1** for a check-only run (**INFERENCE**: it has no writes), but the threat model must say that L1 now runs test commands on the host (§6).
- **`ClaudeCodeExecutor.tools`**:
  ```python
  ["Read", "Grep", "Glob"] + (["Edit", "Write"] if grant.writes else [])
  + (list(CLAUDE_BASH) if grant.shell else list(CLAUDE_CHECK) if grant.checks else [])
  ```
  A read-only run still gets no `--permission-mode acceptEdits` and no `--settings` hook, because both depend on `req.writes` (`:637`).
- **`CopilotExecutor.tools`**: `(["write"] if grant.writes else []) + (COPILOT_SHELL if grant.shell else COPILOT_CHECK if grant.checks else [])`. `--deny-tool shell(git push)` stays.
- **Hint to the agent**: when `grant.checks` or `grant.shell`, append one line to the system prompt (Claude Code's `--append-system-prompt`, Copilot's prompt prefix): `Syntax check: run \`{CHECK_CMD} <files>\` (it runs node --check for each .js file); a raw node command is refused.` **INFERENCE**: without this line the agent cannot know the absolute path.
- **`tools_for` is unchanged** (`executors.py:318-326`). It writes the `tools:` line of subagent definition files. A subagent's `Bash` is not limited by prefix, so a read-only subagent must not get it (**INFERENCE**).

### `edit_check.py` command-line mode (the wrapper)

- `main()`: with no arguments, keep the current hook mode on stdin, unchanged and fail-open. With arguments, call `cli(sys.argv[1:])`.
- `cli(paths)`, with `root = realpath(cwd)`, handles each argument as follows:
  - An argument starting with `-` is refused with `option not allowed: <arg>`.
  - A path outside `root`, a missing file or a non-regular file is reported as `not a file in the working directory`.
  - A suffix other than `.py`, `.js`, `.mjs` or `.cjs` is reported as `not a checked file type`.
  - A `.js` file when `shutil.which("node")` is None gets `node not found` and exit 3, so the check never passes silently.
  - Otherwise the file goes through `check(realpath)` and the wrapper prints `OK <path>` or the error.
- Exit codes: 0 when every file passed, 1 on any syntax error or refused argument, 3 when `node` is missing, 2 when there are no files.
- Node always gets the absolute realpath. **INFERENCE**: an absolute path can never be read as a flag.
- **In `check()`**: pass `env` without `NODE_OPTIONS` and `NODE_PATH`. **HYPOTHESIS**: `NODE_OPTIONS="--require x"` would load code before the check.

## 4. Flags that would run code, and the chosen mitigation

- **FACT**: a Claude Code rule `Bash(node --check:*)` is a prefix match. Anything after `node --check` matches: `-r ./x.js`, `--require`, `--import`, `--loader`, `--experimental-*`, `-e`, `-p`. **INFERENCE**: a prefix rule cannot exclude them. Claude Code has no allow-with-exceptions rule, and listing deny rules for every flag spelling (`--require=`, `-r=`, combined flags, `--experimental-loader`) cannot be complete. The same applies to Copilot's `shell(node --check)`.
- **HYPOTHESIS** (Node's documented preload semantics, not run here): `-r`, `--require`, `--import` and `--loader` load their modules during start-up, before the syntax check, so `node --check -r ./x.js ok.js` runs `x.js`. Node is believed to reject `--check` together with `-e`/`-p` ("either --check or --eval can be used, not both"). Either way, the mitigation does not depend on it.
- **Options rejected**:
  - (a) A raw prefix plus `governance.check_command` patterns does not work, because `check_command` never sees in-session Bash calls (§1 FACT 6).
  - (b) A PreToolUse hook that parses Bash commands would work, but it is a second policy engine. It is more than this goal needs.
- **Chosen: the wrapper only**, `python3 -I <server path>/edit_check.py <files>` (§2, §3). The platform builds the node command line itself (`["node", "--check", <absolute path>]`). Option arguments are refused, and `NODE_OPTIONS` and `NODE_PATH` are removed.
- **Residual risk**:
  - **INFERENCE**: a compound command (`… ; node -r x`) is stopped only if Claude Code checks each subcommand against the rules. That is the vendor's documented behaviour, but it was not tested here.
  - **INFERENCE**: pytest in a check-only run executes the test code the developer wrote, as the server user, on the host (L1 now runs code; the Known gap "Agent CLI runs stay L2" in `docs/threat-model.md` extends to it). pytest may also write `.pytest_cache`, and `uv run pytest` may create `.venv` in the worktree.
  - **HYPOTHESIS**: a bug in the V8 parser itself.
  - **FACT**: acceptance commands run separately through `contracts.run_command` in the L3 sandbox (`contracts.py:269-283`), so `node --check src/agent_company/static/*.js` as a gate `--check` command stays allowed and sandboxed. **INFERENCE**: no `DANGEROUS` pattern matches it.

## 5. Tests the developer must add

Build `executors.Request(...)` directly for read-only runs. The `req()` helper in `tests/test_gateway.py:53` hard-codes `writes=True`, so passing `writes=False` raises a duplicate-keyword `TypeError`. Assert membership with `allowed(cmd)`. Never compare the whole `cmd` list, because the `--settings` JSON makes exact comparisons brittle (the playbook's `acceptEdits`/`--settings` failures).

**`tests/test_gateway.py`**

1. `test_a_read_only_run_with_shell_gets_only_the_check_list(claude, wt)`: role `qa`, purpose `review`, `writes=False`, perms `["read","shell"]`, `G3`. Assert:
   - `f"Bash({executors.CHECK_CMD}:*)"` and `"Bash(uv run pytest:*)"` are in the tools.
   - `Edit`, `Write`, `"Bash(uv run:*)"` and `"Bash(npm run:*)"` are not in the tools, and no tool starts with `"Bash(node"`.
   - `"--permission-mode"` and `"--settings"` are not in the command.
   - The grant has `checks=True`, `shell=False`, `writes=False`; `res.isolation == "L1"`; `res.permissions == ["read", "shell"]`.
2. `test_a_read_only_run_without_shell_or_below_g2_gets_no_commands(claude, wt)`:
   - perms `["read"]` at G3 gives no `Bash(` tool.
   - perms `["read","shell"]` at `G1` gives no `Bash(` tool, and `"checks: autonomy G1"` is in `res.dropped`.
   - `permissions=None` gives no `Bash(` tool.
3. `test_writing_runs_also_get_the_syntax_check(claude, wt)`: perms `["read","write","shell"]` gives both `f"Bash({CHECK_CMD}:*)"` and `"Bash(uv run:*)"`.
4. `test_no_rule_runs_node_directly()`: no entry of `CLAUDE_BASH + CLAUDE_CHECK` starts with `"Bash(node"`, and none of `COPILOT_SHELL + COPILOT_CHECK` starts with `"shell(node"`. `executors.EDIT_CHECK` is the server's `edit_check.py` and exists.

The existing tests must pass unchanged: `test_an_agent_without_shell_never_gets_bash`, `test_a_request_without_an_agent_is_read_only`, `test_the_configure_step_writes_without_write_permission_but_gets_no_bash` and `test_definition_files_name_the_tools_the_gateway_gives`.

**`tests/test_router.py`**

5. `test_copilot_read_only_run_gets_only_the_check_shell`, in the style of `:213-243`: `writes=False`, perms `["read","shell"]`, `G2`. Assert:
   - `"--allow-tool", f"shell({CHECK_CMD})"` is in the command.
   - `"write"` is not allowed, and `"shell(uv run)"` is not in the command.
   - `"--deny-tool", "shell(git push)"` is still there.

**`tests/test_edit_check.py`** (each with `@pytest.mark.skipif(not shutil.which("node"))`; run `[sys.executable, "-I", str(executors.EDIT_CHECK), …]` with `cwd=tmp_path`)

6. `test_the_check_command_reports_js_syntax_errors`: `good.js` (`let a = 1;`) returns rc 0 with `OK` in stdout. `bad.js` (`let = = ;`) returns rc 1 with `bad.js` in the output. Both together return rc 1.
7. `test_the_check_command_refuses_options_and_outside_paths`: create `evil.js`, which writes a `sentinel` file when it runs. The argument sets `["-r", "./evil.js", "good.js"]`, `["--import=./evil.js", "good.js"]`, an outside file and `notes.md` each return rc ≠ 0, and no `sentinel` file is created.
8. `test_node_options_do_not_reach_the_check`: with `NODE_OPTIONS=f"--require {tmp_path/'evil.js'}"` in the environment, `good.js` returns rc 0 and no `sentinel` file is created.
9. `test_the_check_command_fails_without_node`: with a `PATH` that has no `node`, `good.js` returns rc 3 and `node` is in the output.
10. `test_the_dashboard_scripts_pass_the_check`: the wrapper on `sorted(glob("src/agent_company/static/*.js"))` returns rc 0. This is goal 26's check as a regression test.

Command: `uv run pytest -q tests/test_gateway.py tests/test_router.py tests/test_edit_check.py`. Run it without `-n`, because pytest has no xdist here.

## 6. Row to add to `docs/threat-model.md`

Add it after the edit-check hook row (line 46):

| Read-only CLI run → check commands | a QA review or another non-writing Claude Code/Copilot run with `shell` at G2+ gets `Grant.checks`: `python3 -I <server>/edit_check.py <files>` (syntax only: `compile()`, `node --check`), pytest, `git status/diff`, `ls`; no Edit/Write, no `uv run:*`/`npm run:*` (EL-R-05) | `gate()`: `checks` needs the `shell` permission and G2+; the wrapper is the server's own file (not the worktree's), started with `-I`; it refuses option arguments, paths outside the working directory and unknown types, removes `NODE_OPTIONS`/`NODE_PATH`, and fails when `node` is missing; no raw `node` rule exists, because a prefix rule cannot exclude `-r`/`--import`/`--loader`; pytest still runs agent-written tests at L1 on the host (Known gaps) | `test_gateway.py::test_a_read_only_run_with_shell_gets_only_the_check_list`, `test_gateway.py::test_no_rule_runs_node_directly`, `test_edit_check.py::test_the_check_command_refuses_options_and_outside_paths`, `test_edit_check.py::test_node_options_do_not_reach_the_check` |

Also extend the Known gap "Agent CLI runs stay L2" (`docs/threat-model.md:95-99`): read-only (L1) runs with `checks` run pytest on the host too. In the "Tool misuse" row (`:72`), add that `CLAUDE_BASH`/`CLAUDE_CHECK` reach `node` only through the `edit_check.py` wrapper.

## Files to change

- `src/agent_company/executors.py`: `Grant.checks`, the `gate()` rule, `EDIT_CHECK`/`CHECK_CMD`/`CLAUDE_CHECK`/`COPILOT_CHECK`, the check entry in `CLAUDE_BASH`/`COPILOT_SHELL`, both `tools()`, and the system-prompt hint.
- `src/agent_company/edit_check.py`: the command-line mode `cli()`, and `check()` without `NODE_OPTIONS`/`NODE_PATH`.
- `tests/test_gateway.py`, `tests/test_router.py`, `tests/test_edit_check.py`: the tests in §5.
- `docs/threat-model.md`: the row and edits in §6.
- `CLAUDE.md`: one clause in the `executors.py` entry about `Grant.checks` and the wrapper.

FINDING: The running server loads `executors.py` from the main checkout (K619, K620), so the new allow-list takes effect only after merge and restart. In this goal, QA's own in-session `node --check` will still be refused. QA can verify with the unit tests in §5 and the gate's acceptance command, which runs in the L3 sandbox, not through the allow-list.
FINDING: `edit_check.py` with file arguments exits 0 today without checking anything (it waits on stdin, `edit_check.py:37-39`). Any acceptance condition built on it before `cli()` exists would pass falsely.
FINDING: The Node flag behaviour in §4 is HYPOTHESIS: my local probe needed an approval that was not given. Test 7 turns it into an observed result.
NEXT: The developer implements §2, §3 and §5 and runs `uv run pytest -q tests/test_gateway.py tests/test_router.py tests/test_edit_check.py`.
NEXT: QA reviews the `gate()` logic against §3 and reruns `node --check src/agent_company/static/*.js` as a gate check command.
MESSAGE request to general_manager: Save this answer at `.claude/specs/0086-allow-node-check-src-agent-company-stati/design.md`, the acceptance path. I did not write files because the task said to answer in text.
MESSAGE recommendation to developer: In `tests/test_gateway.py` build read-only `Request`s directly (the `req()` helper fixes `writes=True`), and assert with `allowed(cmd)` membership, never the whole command list.
MESSAGE fact to qa: Your own in-session check stays refused until the change is merged and the server restarts. Verify with the §5 tests and the gate's `node --check` acceptance command, and check that a `-r` argument creates no `sentinel` file (test 7).

```json
{"insights": [{"title": "Claude Code prefix rules cannot allow a command while excluding its flags", "description": "Bash(x:*) allows anything that starts with x, so code-running flags after the prefix cannot be excluded. Allow a platform-owned wrapper at the server's absolute path that builds the argv itself, and leave out the raw command.", "evidence": ["src/agent_company/executors.py:592", "src/agent_company/edit_check.py:30", "src/agent_company/governance.py:156 is not called for in-session Bash calls"], "expected_benefit": "Narrow command grants for read-only roles without opening code execution", "confidence": 0.7}]}
```

PROPOSAL: A PreToolUse Bash guard for agent CLI runs :: `governance.check_command` never sees in-session Bash calls, so the DANGEROUS rules and company rules do not apply inside Claude Code or Copilot sessions.
