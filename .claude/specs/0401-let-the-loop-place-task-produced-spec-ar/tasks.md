# Tasks (iteration 1)



## 1. Specify how the loop places task-produced spec artifacts
- key: `spec` · role: architect · writes: no

Write a short technical specification (your answer becomes .claude/specs/0401-let-the-loop-place-task-produced-spec-ar/spec.md). Read src/agent_company/specs.py (save_answer, check_plan, SPEC_DIR, LOOP_FILES, _normalise, write_outputs), src/agent_company/loop.py lines ~985-1135 (task prompt `head`/`tail`, _contract_note, the save_answer call, contracts.check, requires_change, the BLOCKER skip), src/agent_company/executors.py (gate, ClaudeCodeExecutor allowedTools/--permission-mode acceptEdits, the Codex and Copilot command lines) and tests/test_spec_answer.py.

The spec must cover these sections:
1. Refusal: where a session is refused writes under `.claude/`, per run kind (claude_code, codex, copilot), each with file:line evidence or labelled INFERENCE when it comes from the CLI's own built-in behaviour rather than this repository. The prompt note in (3) must state only what this section establishes.
2. Placement from command conditions: save_answer also targets every document path (specs.ANSWER_FILES suffix) inside the goal's OWN spec folder that a `command` condition's `run` names (found with SPEC_DIR / a path regex, then _normalise). The existing rules still apply: never a LOOP_FILES name, never another goal's folder, never `..` or a path outside the worktree, never `.git`. A writing task never overwrites a path its run changed, and `changed` None means unknown, so nothing is written. A read-only task may replace a stale file.
3. Prompt note: a function `specs.task_note(goal, contract) -> str` that loop.py adds to the task prompt `head` when the contract's acceptance names a path in the goal's own spec folder. The note says the session cannot write that folder, the loop writes those files from the answer, and it gives the block format from (4). It returns "" otherwise.
4. Multi-document blocks, with this exact format and no other: a line `=== FILE: <worktree-relative path> ===`, the content, then a line `=== END FILE ===` (marker lines are matched whole, whitespace trimmed; markdown fences are not markers). A block whose path resolves inside the goal's own spec folder, is not a loop file and has a document suffix (ANSWER_FILES, plus .json/.yaml/.yml for blocks only) is placed, even when no acceptance condition names it, under the same writing/read-only rules. Any other block is ignored and logged. If a block exists for a path, the block's content is written there, not the whole answer. With no blocks, today's whole-answer behaviour stays exactly as it is.
5. Data flow: a normal case, an edge case (two blocks, one targeting decisions.md) and an error case (an unwritable target, a block naming another goal's folder), showing what `saved` holds and how it reaches _checkpoint, write_outputs `named`/`kept`, requires_change and the BLOCKER check, so that loop.py needs no change besides the prompt note.
6. A test list for tests/test_spec_answer.py.
Keep it under ~150 lines. Do not change files; answer in text.

Acceptance: {"type": "file_exists", "path": ".claude/specs/0401-let-the-loop-place-task-produced-spec-ar/spec.md"}; {"type": "contains", "value": "=== FILE:"}; {"type": "contains", "value": "task_note"}; {"type": "min_length", "value": 1500}

## 2. Place spec artifacts from command conditions and FILE blocks; tell the agent
- key: `place_artifacts` · role: developer · writes: yes · after: spec

Implement the specification from task 'spec' (its output is in your inputs).

1. src/agent_company/specs.py:
- Add `documents(text) -> dict[str, str]`. It parses `=== FILE: <path> ===` ... `=== END FILE ===` blocks. Marker lines are matched whole after trimming whitespace, and an unterminated block is ignored.
- Extend `save_answer(goal, contract, text, *, writes, changed)`:
  (a) Besides file_exists .md/.txt paths, it also targets document paths in the goal's OWN spec folder that are named in a `command` condition's `run`.
  (b) For each target, the content is the block for that path when the answer has one, else the whole answer. With no block, the behaviour is exactly today's.
  (c) Blocks for other paths inside the own spec folder (ANSWER_FILES suffix, or .json/.yaml/.yml) are placed too, under the same rules. Any other block is ignored, with a log_event.
- Keep every existing refusal: LOOP_FILES in any spelling, another goal's folder, `..`, symlink escapes, `.git`, a path a writing run changed, `changed` None.
- Add `task_note(goal, contract) -> str` as the spec describes. It returns "" when the acceptance names no own-spec-folder path.
- Update the module docstring's save_answer sentence.

2. src/agent_company/loop.py: add `specs.task_note(goal, contract)` to the task prompt `head` next to `_contract_note(contract)`. Nothing else in loop.py should need to change, because `saved` already feeds _checkpoint, write_outputs, requires_change and the BLOCKER check. If something does need to change, say why.

3. tests/test_spec_answer.py: add tests using the existing `goal` fixture and the monkeypatch loop-test pattern:
- a command condition `grep -q X .claude/specs/<own>/design.md` gets the file and passes the acceptance check in a loop run;
- a command naming `<own>/decisions.md` or another goal's folder writes nothing;
- two FILE blocks fill two paths with their own contents, not the whole answer;
- a block for decisions.md, `../x.md` or another folder is ignored;
- a writing task whose run changed the file keeps it, even with a block for it;
- with no blocks, the whole answer is written as before;
- task_note is non-empty only when an own-spec path is named, and it appears in the prompt the executor receives.

Optionally adjust the one CLAUDE.md sentence about save_answer. Run `uv run pytest -q tests/test_spec_answer.py tests/test_spec_paths.py tests/test_plan_allowed_paths.py` after each change, and run the syntax check on the edited .py files.

Acceptance: {"type": "command", "run": "uv run pytest -q tests/test_spec_answer.py tests/test_spec_paths.py tests/test_plan_allowed_paths.py", "stage": "unit"}; {"type": "command", "run": "grep -q 'def task_note' src/agent_company/specs.py && grep -q 'def documents' src/agent_company/specs.py && grep -q 'specs.task_note' src/agent_company/loop.py"}; {"type": "command", "run": "grep -q '=== END FILE ===' tests/test_spec_answer.py"}

## 3. Review the spec-artifact placement
- key: `qa_review` · role: qa · writes: no · after: place_artifacts

Review the developer's diff to src/agent_company/specs.py, src/agent_company/loop.py and tests/test_spec_answer.py against the spec from task 'spec'. Run `uv run pytest -q tests/test_spec_answer.py tests/test_spec_paths.py tests/test_plan_allowed_paths.py`. Verify the logic by tracing the data flow:
- normal case: a read-only architect whose `command` acceptance greps <own>/design.md gets the file before contracts.check runs;
- edge case: two FILE blocks, one of them for decisions.md, so only the other is placed; a writing task whose run changed the file keeps the run's version;
- error case: a block or command path into another goal's folder, `..`, a symlink or .git writes nothing; an unwritable target is logged and the task is not crashed.
Also confirm:
- an answer without blocks behaves exactly as before (existing tests unchanged and passing);
- task_note appears in the prompt only when an own-spec path is named, and it claims nothing the spec did not establish;
- `saved` still reaches _checkpoint, write_outputs (`kept`), requires_change and the BLOCKER check.
List each issue as a `FINDING:` line with file:line, end with `OUTCOME:` and then `VERDICT: PASS` or `VERDICT: FAIL`.

Acceptance: {"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}; {"type": "contains", "value": "OUTCOME"}
