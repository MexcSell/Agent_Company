# Tasks (iteration 1)



## 1. Allow `python3 -c` and `python3 -m doctest` in writing runs' command lists (not in read-only check lists)
- key: `allow_python_c` · role: developer · writes: yes

Reproduce the fault, then fix it. Root cause (FACT): src/agent_company/executors.py:660-667. CLAUDE_BASH and CLAUDE_CHECK are built only from PYTEST_FORMS + UNITTEST_FORMS, and COPILOT_SHELL/COPILOT_CHECK (:826-829) the same way. So a writing developer run is refused `python3 -c "…"` and `python3 -m doctest file.py`, which plans use as acceptance commands.
1. Write the failing test first: tests/test_python_c_forms.py. Define FORMS_C = ["python -c", "python3 -c", "python3 -B -c"] and FORMS_DOCTEST = ["python -m doctest", "python3 -m doctest", "python3 -B -m doctest"].
   - For each form, assert `Bash(<form>:*)` is in executors.CLAUDE_BASH and `shell(<form>)` is in executors.COPILOT_SHELL.
   - Assert that none of these forms are in CLAUDE_CHECK or COPILOT_CHECK.
   - Assert that `ClaudeCodeExecutor.tools(None, Grant(writes=True, shell=True))` and `CopilotExecutor.tools(None, Grant(writes=True, shell=True))` include them, and that `tools(None, Grant(checks=True))` does not.
   - Assert that bare python (`Bash(python3:*)`, `Bash(python:*)`, `Bash(python3 -B:*)`, `shell(python3)`, `shell(python)`, `shell(python3 -B)`) is in none of the four lists.
   - Assert that no list has a `Bash(node` or `shell(node` entry.
2. Fix: in executors.py, next to UNITTEST_FORMS, add `PYTHON_C_FORMS = ["python -c", "python3 -c", "python3 -B -c"]` and `DOCTEST_FORMS = ["python -m doctest", "python3 -m doctest", "python3 -B -m doctest"]`. Add a one-line comment in the existing style: writing runs only, since `uv run:*` already runs any Python there (goal 411); never in a run without writes. Add them to CLAUDE_BASH and COPILOT_SHELL only, and add them in the list literals so the `if CHECK_OK:` appends still put CHECK_CMD last. Don't change CLAUDE_CHECK, COPILOT_CHECK, CLAUDE_CHECK_DENY or COPILOT_CHECK_DENY.
3. Two existing tests now contradict the goal. Change them only as follows:
   - tests/test_unittest_forms.py:9-26: the `-c` entries in BARE_BASH/BARE_SHELL may no longer be asserted absent from CLAUDE_BASH/COPILOT_SHELL. Keep asserting that they are absent from CLAUDE_CHECK/COPILOT_CHECK and from the Grant(checks=True) tools. Keep bare python forbidden everywhere.
   - tests/test_phase4_communication.py:426-435: keep the `python3 -B -c` refusal for CLAUDE_CHECK only, keep `Bash(python3:*)` refused in both lists, and update the docstring's last sentence to match.
   Don't weaken any other assertion.
4. docs/threat-model.md:102-109 (Known gaps, agent CLI runs): extend the unittest sentence. Writing runs also get `python -c` and `python -m doctest` (`executors.PYTHON_C_FORMS`, `DOCTEST_FORMS`). This adds no new capability, because `uv run:*` already runs any Python there. Read-only check runs never get them (`test_python_c_forms.py`).
5. Run `python3 -I /home/luca/Agent_Company/src/agent_company/edit_check.py src/agent_company/executors.py tests/test_python_c_forms.py tests/test_unittest_forms.py tests/test_phase4_communication.py`, then the acceptance commands. Report the files changed, the commands and their summary lines.

Acceptance: {"type": "file_exists", "path": "tests/test_python_c_forms.py"}; {"type": "command", "run": "uv run pytest -q tests/test_python_c_forms.py tests/test_unittest_forms.py", "stage": "unit"}; {"type": "command", "run": "uv run pytest -q tests/test_phase4_communication.py tests/test_node_check.py tests/test_phase3_agents.py tests/test_edit_check.py", "stage": "unit"}

## 2. Review the python -c / doctest allowance: logic, scope, no widened read-only rights
- key: `qa_review` · role: qa · writes: no · after: allow_python_c

Review the diff of task allow_python_c (`git diff` against the base branch) against its description. Trace the data flow for three cases.
(a) Normal: a writing developer run (Grant writes+shell) gets `Bash(python3 -c:*)` and `Bash(python3 -m doctest:*)` from ClaudeCodeExecutor.tools, and the matching `shell(...)` entries from CopilotExecutor.tools.
(b) Edge: `python3 -B -c "…"` and `python -m doctest x.py` match a rule. CHECK_CMD is still the last entry of CLAUDE_BASH/COPILOT_SHELL and the first of the check lists. Bare `python3 script.py` and a raw `node` command are still refused.
(c) Error/negative: a read-only run (Grant checks=True, e.g. a QA review) does NOT get `-c` or doctest. CLAUDE_CHECK_DENY/COPILOT_CHECK_DENY are unchanged. checks.CHECK_COMMAND is unchanged, so `python -c` is still not extracted as a test command.
Confirm these three points:
- The two edited existing tests were only narrowed as described, not weakened elsewhere.
- The threat-model text matches the code.
- The new test would fail without the fix (reason from the code).
Run `uv run pytest -q tests/test_python_c_forms.py tests/test_unittest_forms.py tests/test_phase4_communication.py tests/test_node_check.py`. If uv exits 2 on a read-only cache (K915), run `python3 -B -m pytest -q` with the same files instead. If the environment blocks both, write `BLOCKER: environment` and judge from the code; don't count that as a defect. List FINDING lines, then an OUTCOME line, and end with `VERDICT: PASS` or `VERDICT: FAIL`.

Acceptance: {"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}; {"type": "contains", "value": "OUTCOME"}; {"type": "min_length", "value": 300}
