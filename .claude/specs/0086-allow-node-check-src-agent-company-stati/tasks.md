# Tasks (iteration 2)



## 1. Close the iteration-1 review findings on the node --check wrapper (CLAUDE.md, Copilot test, git diff --output)
- key: `finish` · role: developer · writes: yes

The implementation from iteration 1 is already on this branch. QA passed it (see .claude/specs/0086-allow-node-check-src-agent-company-stati/review.md and design.md). Do NOT redesign it. Keep the wrapper approach (CHECK_CMD = `python3 -I <server edit_check.py>`) and add no raw `node` allow rule. Fix only these review findings:
1. CLAUDE.md, in the `executors.py` entry: add one clause. Read-only runs with `shell` get a `Grant.checks` list (CLAUDE_CHECK/COPILOT_CHECK): pytest, git status/diff, ls, and the syntax-check wrapper `python3 -I edit_check.py <files>` (node --check per .js file, compile() per .py file). A raw `node` command stays refused.
2. tests/test_node_check.py: add design §5 test 5. Build a real Copilot command line for a read-only QA run with a worktree, offline, the way tests/test_router.py builds one. Assert that `--allow-tool` contains `shell(<CHECK_CMD>)`, that `shell(git push)` is denied, that CHECK_HINT is in the prompt, and that there is no `shell(node` allow.
3. `git diff --output=<path>` writes files. Add a deny rule for it to the read-only check lists of Claude Code and Copilot (e.g. `Bash(git diff --output:*)` in the disallowed tools, or the matching Copilot deny). Add a test for it. If a deny rule cannot be expressed, name it in the docs/threat-model.md row and Known gap instead.
4. src/agent_company/edit_check.py: the `cli()` docstring says '2 no files', which cannot happen. Make the docstring match the behaviour (main() sends a call without arguments to the stdin hook).
5. docs/threat-model.md: note that `checks` also applies to the GM's read-only plan run.
Run `uv run pytest -q tests/test_node_check.py tests/test_gateway.py tests/test_threats.py` and `python3 -I src/agent_company/edit_check.py src/agent_company/static/*.js`. Paste their real output. Keep the diff small.

Acceptance: {"type": "command", "run": "uv run pytest -q tests/test_node_check.py tests/test_gateway.py tests/test_threats.py", "requirement_keys": ["EL-R-05"], "stage": "unit"}; {"type": "command", "run": "python3 -I src/agent_company/edit_check.py src/agent_company/static/*.js", "requirement_keys": ["EL-R-05"], "stage": "static"}; {"type": "command", "run": "grep -q 'Grant.checks' CLAUDE.md", "stage": "static"}

## 2. Review the finished node --check wrapper and record the JS syntax check evidence
- key: `review` · role: qa · writes: no · after: finish

Review the diff of the `finish` task against design.md and the findings in review.md. Iteration 1's review already verified the core logic; do not repeat it in full. Limit cost: the last QA run failed only because it exceeded its budget. Try each command at most ONCE and do not retry variants of a refused command:
(a) `python3 -I src/agent_company/edit_check.py src/agent_company/static/*.js`
(b) `uv run pytest -q tests/test_node_check.py tests/test_gateway.py`
If a command is refused, quote the refusal and move on. The server still runs the main checkout's executors.py until the merge and restart (K619), so the gate's sandboxed commands are the executable evidence. Verify:
1. The new Copilot command-line test asserts the wrapper allow, the git push deny and that no raw node is allowed.
2. The `git diff --output` handling, as a deny rule plus test or as documentation.
3. The CLAUDE.md clause is accurate.
4. CLAUDE_BASH/COPILOT_SHELL still have no raw `node` entry, and the wrapper refuses arguments that start with '-'.
Give the logic for the normal case (a read-only QA run gets the wrapper), an edge case (CHECK_OK false, so no entry) and an error case (`-r evil.js`, which is refused). List FINDING lines. End with OUTCOME and 'VERDICT: PASS' or 'VERDICT: FAIL'.

Acceptance: {"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}; {"type": "contains", "value": "OUTCOME"}; {"type": "command", "run": "for f in src/agent_company/static/*.js; do node --check \"$f\" || exit 1; done", "requirement_keys": ["EL-R-05"], "stage": "static"}
