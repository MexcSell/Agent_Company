# Review (iteration 1)

## Review the unittest allowance: logic, scope and no widened rights (qa, done)

## Logic verification

I compared against the branch point (`git merge-base main HEAD` = `0695a5c`). A plain `git diff main` also shows unrelated deletions in sandbox.py, specs.py and loop.py, but those come from main moving ahead after the branch was cut, not from this task. The task's own change touches only `executors.py`, `checks.py`, `docs/threat-model.md`, the new `tests/test_unittest_forms.py` and the loop's spec files.

**`executors.py`: `UNITTEST_FORMS` and the four lists (lines 659–665, 824–831)**
- **(a) Normal case: works.**
  - **Writing run (`Grant(writes, shell)`):** `ClaudeCodeExecutor.tools` (line 718) returns `CLAUDE_BASH`, which now includes `Bash(python -m unittest:*)`, `Bash(python3 -m unittest:*)` and `Bash(python3 -B -m unittest:*)`. `CopilotExecutor.tools` (line 839) returns `COPILOT_SHELL`, which has the matching `shell(...)` entries.
  - **Review run (`Grant(checks=True)`, no shell):** gets `CLAUDE_CHECK` / `COPILOT_CHECK`, which have the same three entries.
  - The syntax check stays in place: it is still added with `.append` / `.insert(0, …)` at lines 666–668 and 828–830, after the lists are built.
  - **Root cause:** before the fix the lists only had `PYTEST_FORMS`, so the CLI refused `python3 -m unittest -v test_text_utils` (K391, goal 122) and the goal-318 commands. The change fixes that cause; it doesn't hide a symptom.
- **(b) Edge cases: correct.**
  - `python3 -B -m unittest discover -s tests` matches the prefix `python3 -B -m unittest`. `python -m unittest test_x` matches `python -m unittest`.
  - No entry is a bare `python` or `python3` prefix, so `python3 -c 'import os'` and `python3 script.py` match nothing and are still refused.
  - There is no `node` entry. `CHECK_CMD` is still `python3 -I <abs>/edit_check.py`, so a raw `node` command is still refused.
  - `CLAUDE_CHECK_DENY` and `COPILOT_CHECK_DENY` are unchanged and still applied: `--disallowedTools` at line 733, `--deny-tool` at line 854. A test pins both lists.
- **(c) Error case:** a bare-Python attempt in a review run hits no allow rule, so the CLI refuses it, the same as before.

**`checks.py`: `CHECK_COMMAND` (lines 45–51)**
- **Normal case:**
  - `python3 -m unittest discover -s tests` matches the new branch `^(uv run |poetry run )?python3? (-B )?-m unittest\b`.
  - `extract_check_commands` (line 58) therefore returns it.
  - `create_goal` (loop.py:71–76) passes it through `governance.check_command`. That function is unchanged, and none of the `DANGEROUS` patterns match `python… -m unittest`.
  - The gate then runs it in the L3 sandbox.
- **Edge cases:**
  - `python3 -m unittestx` doesn't match, because `\b` fails before `x`.
  - `python3 -c …`, `python3 script.py`, `python3 -m http.server` and `python3 -I -m unittest` match no branch: each needs `-m ` and then one of the named tools.
  - The first line of the go/npm/cargo branch is unchanged.
- **Error case:** non-test commands are still ignored by `extract_check_commands`. Quality commands are still checked with `check_command` at loop.py:74–76, and `governance.py` and `loop.py` have no changes in this task's diff.

**Rights:** no permission rule was widened beyond the three unittest prefixes. `uv run:*` and `npm run:*` are still missing from the check lists. Edit and Write still need `grant.writes`.

**unittest gives the agent nothing pytest doesn't already give it:**
- pytest already imports repository code and modules given by path, and `-p` loads any module.
- `unittest discover -s <path>` is no broader than `pytest <path>`.
- unittest has no file-writing options, while pytest has `--junitxml`.
- A `unittest.py` in the worktree would shadow the stdlib through `sys.path[0]`, but that is again repository code, the same as a `conftest.py`.

So the threat-model claim ("add no new capability … no rule allows `python`/`python3` in general") matches the code.

**Would the new test fail without the fix?** Yes. On `0695a5c` the lists have no unittest entries, and the old regex `^(uv run |poetry run |python3? -m )?(pytest|…)` can't match `unittest`. That makes these 11 cases fail:
- `test_claude_lists_allow_unittest`: 2 cases
- `test_copilot_lists_allow_unittest`: 2 cases
- `test_read_only_grants…`: 1
- `test_unittest_commands_are_extracted_as_checks`: 1
- `test_unittest_forms_count_as_test_commands`: 5

The other 8 cases are guards against regressions and pass on both versions. This matches the developer's reported "11 failed, 8 passed".

**Tests run:** `uv run pytest -q tests/test_unittest_forms.py tests/test_node_check.py tests/test_phase4_communication.py` gave **50 passed in 47.13s**.

BLOCKER: environment. The run's permissions refused an ad-hoc `uv run python -` probe of the old and new regex, and an `awk` line-length check. I judged those parts from the code above; this isn't counted as a defect.

## Checklist
- **Contract satisfied:** yes. Writing and review runs on Claude Code and Copilot both allow the three unittest forms, and the gate extracts them as checks.
- **Interfaces compatible:** yes. The list names and order are unchanged apart from the additions, and the `CHECK_CMD` positions are pinned by a test.
- **Error handling:** unchanged, and none is needed.
- **Edge cases:** covered, but the regex and the allow-lists don't accept the same set of forms (first finding below).
- **Tests sufficient:** yes. They cover the four lists, the read-only grant, extraction, positive and negative regex cases, and the deny lists.
- **Dependencies:** none added.
- **Security:** no wider rights, and no bare-Python rule.
- **Performance:** not affected.
- **Docs:** the threat model is updated and accurate. CLAUDE.md still names only pytest (the developer already proposed fixing this).

## Findings
FINDING: src/agent_company/checks.py:46-48, src/agent_company/executors.py:664,826 | The regex and the allow-lists don't accept the same forms. The regex now extracts `uv run python3 -m unittest`, `poetry run python -m unittest`, `python -B -m unittest`, `uv run python3 -m pytest` and `python -B -m pytest` as acceptance checks. `CLAUDE_CHECK` and `COPILOT_CHECK` don't allow these, since they only have `uv run pytest` and no `python -B` forms. A QA review run told to re-run one of these commands would still be refused, which is the goal-318 failure again for those forms. The gate itself runs them in L3, so this doesn't block. | Either limit both regex branches to the forms in `PYTEST_FORMS`/`UNITTEST_FORMS` (plus `uv run pytest`), or add `python -B -m unittest` / `uv run python -m unittest` style entries to the check lists. Either way, add a test that every form the regex accepts is in `CLAUDE_CHECK`.

FINDING: src/agent_company/checks.py:46 | The first regex branch was widened beyond the task: `(uv run |poetry run )?(python3? (-B )?-m )?` now also extracts ruff, mypy, flake8 and tox through `python3 -B -m` and `uv run python3 -m`. It only affects which commands are extracted as checks; the commands still pass `check_command` and run in L3, so no run gets wider rights. The developer disclosed it. | Keep it, and record the reason in the commit message or the spec. Or revert it to the old branch: the unittest fix doesn't depend on it.

FINDING: CLAUDE.md (executors.py summary, "`Grant.checks` list … pytest") | The project summary still lists pytest as the only test runner, so it no longer describes the check list. | Mention `UNITTEST_FORMS` there; CLAUDE.md was outside this task's allowed paths.

OUTCOME: APPROVED_WITH_COMMENTS

NEXT: Make the forms in `CHECK_COMMAND` and the forms in the check lists the same, with a test that the regex accepts nothing the check lists don't allow.

MESSAGE recommendation to developer: Make `checks.CHECK_COMMAND` and `executors.CLAUDE_CHECK`/`COPILOT_CHECK` accept the same command forms (`uv run python3 -m unittest`, `python -B -m …`), so a QA run can re-run every acceptance command the gate extracts.

PROPOSAL: Test that every extracted check form can run in review runs :: commands the gate extracts but the review list refuses keep producing blocked QA runs like goals 122 and 318.

VERDICT: PASS
