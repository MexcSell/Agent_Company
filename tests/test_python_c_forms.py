"""Goal 412: writing runs may run `python -c` and `python -m doctest`, which plans use as acceptance commands (goal
411 was refused them). They add nothing there, since `uv run:*` already runs any Python. Read-only check runs never
get them, and no list allows python in general or a raw `node`."""

import pytest

from agent_company import executors

FORMS_C = ["python -c", "python3 -c", "python3 -B -c"]
FORMS_DOCTEST = ["python -m doctest", "python3 -m doctest", "python3 -B -m doctest"]
FORMS = FORMS_C + FORMS_DOCTEST
BARE_BASH = ["Bash(python3:*)", "Bash(python:*)", "Bash(python3 -B:*)"]
BARE_SHELL = ["shell(python3)", "shell(python)", "shell(python3 -B)"]
LISTS = ["CLAUDE_BASH", "CLAUDE_CHECK", "COPILOT_SHELL", "COPILOT_CHECK"]


@pytest.mark.parametrize("form", FORMS)
def test_writing_lists_allow_the_form(form):
    assert f"Bash({form}:*)" in executors.CLAUDE_BASH
    assert f"shell({form})" in executors.COPILOT_SHELL


@pytest.mark.parametrize("form", FORMS)
def test_check_lists_refuse_the_form(form):
    assert f"Bash({form}:*)" not in executors.CLAUDE_CHECK
    assert f"shell({form})" not in executors.COPILOT_CHECK
    assert not any(t.startswith(f"Bash({form}") for t in executors.CLAUDE_CHECK)
    assert not any(t.startswith(f"shell({form}") for t in executors.COPILOT_CHECK)


def test_writing_grants_get_the_forms():
    grant = executors.Grant(writes=True, shell=True)
    claude = executors.ClaudeCodeExecutor.tools(None, grant)
    copilot = executors.CopilotExecutor.tools(None, grant)
    for form in FORMS:
        assert f"Bash({form}:*)" in claude and f"shell({form})" in copilot, form


def test_read_only_grants_do_not_get_the_forms():
    grant = executors.Grant(checks=True)
    claude = executors.ClaudeCodeExecutor.tools(None, grant)
    copilot = executors.CopilotExecutor.tools(None, grant)
    for form in FORMS:
        assert f"Bash({form}:*)" not in claude and f"shell({form})" not in copilot, form


@pytest.mark.parametrize("name", LISTS)
def test_no_list_allows_bare_python(name):
    tools = getattr(executors, name)
    assert not (set(BARE_BASH) | set(BARE_SHELL)) & set(tools), name


@pytest.mark.parametrize("name", LISTS)
def test_no_list_allows_raw_node(name):
    tools = getattr(executors, name)
    assert not any(t.startswith(("Bash(node", "shell(node")) for t in tools), name
