"""Goal 338: stdlib `python -m unittest` acceptance commands are allowed in writing and review runs, as the pytest
forms are (goals 318 and 122 were refused them). Only the exact `-m unittest` prefixes, never python in general."""

import pytest

from agent_company import checks, executors

FORMS = ["python3 -m unittest", "python3 -B -m unittest", "python -m unittest"]
BARE_BASH = ["Bash(python3:*)", "Bash(python:*)", "Bash(python3 -c:*)", "Bash(python -c:*)", "Bash(python3 -B:*)"]
BARE_SHELL = ["shell(python3)", "shell(python)", "shell(python3 -c)", "shell(python -c)", "shell(python3 -B)"]
# goal 412: writing runs may run `-c` (test_python_c_forms.py); bare python stays forbidden in every list
C_BASH = {"Bash(python3 -c:*)", "Bash(python -c:*)"}
C_SHELL = {"shell(python3 -c)", "shell(python -c)"}


@pytest.mark.parametrize("name", ["CLAUDE_BASH", "CLAUDE_CHECK"])
def test_claude_lists_allow_unittest(name):
    tools = getattr(executors, name)
    for form in FORMS:
        assert f"Bash({form}:*)" in tools, (name, form)
    bare = set(BARE_BASH) - C_BASH if name == "CLAUDE_BASH" else set(BARE_BASH)
    assert not bare & set(tools)


@pytest.mark.parametrize("name", ["COPILOT_SHELL", "COPILOT_CHECK"])
def test_copilot_lists_allow_unittest(name):
    tools = getattr(executors, name)
    for form in FORMS:
        assert f"shell({form})" in tools, (name, form)
    bare = set(BARE_SHELL) - C_SHELL if name == "COPILOT_SHELL" else set(BARE_SHELL)
    assert not bare & set(tools)


def test_read_only_grants_get_the_unittest_forms_and_no_writes():
    grant = executors.Grant(checks=True)
    claude = executors.ClaudeCodeExecutor.tools(None, grant)
    copilot = executors.CopilotExecutor.tools(None, grant)
    for form in FORMS:
        assert f"Bash({form}:*)" in claude and f"shell({form})" in copilot
    assert not {"Edit", "Write"} & set(claude) and "write" not in copilot
    assert "Bash(uv run:*)" not in claude and "shell(uv run)" not in copilot
    assert not set(BARE_BASH) & set(claude) and not set(BARE_SHELL) & set(copilot)


def test_the_check_entry_and_deny_lists_are_unchanged():
    if executors.CHECK_OK:
        assert executors.CLAUDE_CHECK[0] == f"Bash({executors.CHECK_CMD}:*)"
        assert executors.CLAUDE_BASH[-1] == f"Bash({executors.CHECK_CMD}:*)"
        assert executors.COPILOT_CHECK[0] == f"shell({executors.CHECK_CMD})"
        assert executors.COPILOT_SHELL[-1] == f"shell({executors.CHECK_CMD})"
    assert executors.CLAUDE_CHECK_DENY == ["Bash(git diff --output:*)"]
    assert executors.COPILOT_CHECK_DENY == ["shell(git diff --output)"]


def test_unittest_commands_are_extracted_as_checks():
    text = "run `python3 -m unittest discover -s tests` and `python3 -B -m unittest test_stats`"
    assert checks.extract_check_commands(text) == ["python3 -m unittest discover -s tests",
                                                   "python3 -B -m unittest test_stats"]


@pytest.mark.parametrize("cmd", ["python -m unittest", "python3 -m unittest -v", "python3 -B -m unittest test_x",
                                 "uv run python3 -m unittest discover", "uv run python -m unittest"])
def test_unittest_forms_count_as_test_commands(cmd):
    assert checks.CHECK_COMMAND.search(cmd), cmd


@pytest.mark.parametrize("cmd", ["python3 script.py", "python -c 'import os'", "python3 -m http.server",
                                 "python3 -m unittestx", "python3 -B script.py", "uv run python3 script.py"])
def test_plain_python_is_not_a_test_command(cmd):
    assert not checks.CHECK_COMMAND.search(cmd), cmd


def test_pytest_forms_still_count():
    for cmd in ("pytest -q", "python3 -m pytest", "uv run pytest -q", "python -m pytest tests"):
        assert checks.CHECK_COMMAND.search(cmd), cmd
