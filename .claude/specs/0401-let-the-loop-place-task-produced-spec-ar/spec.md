I have the sources I need. Here is the specification. Nothing was changed in the worktree; the loop saves this answer as spec.md.

# Spec: the loop places spec artifacts that tasks produce (goal 401)

## 1. Where a session is refused writes under `.claude/`, by run kind

A run's write rights come from `executors.gate` (executors.py:345-391). A write survives only with the `write` permission, autonomy G2 or higher and a worktree (360-370). The only exception is the GM's configure step (357-359, 386-387).

| Run kind | Read-only run (`grant.writes` False) | Writing run |
|---|---|---|
| claude_code | **Refused by this repo.** `--allowedTools` holds Edit/Write only when `grant.writes` (executors.py:723-724, 733), so no tool can write any path. | Edit/Write are allowed with `--permission-mode acceptEdits` (737). executors.py adds no deny rule for `.claude/`: the only `.claude` hits are gate comments (357, 374, 387). **INFERENCE:** the Claude Code CLI protects `.claude/` and asks before editing there even under acceptEdits, and nobody answers in `-p` mode, so the edit is refused. **Counter-case:** the GM configure step (loop.py:830) edits `.claude/` through the same executor, so this protection may not cover every path. To confirm it, look in `Run.tool_calls` for Edit/Write calls on `.claude/specs/` paths that were refused. |
| codex | **Refused by this repo.** `--sandbox read-only` (executors.py:804). | `--sandbox workspace-write` (804). **INFERENCE:** Codex's workspace-write mode protects `.git`, not `.claude/`, so the run can probably write the spec folder. |
| copilot | **Refused by this repo.** The `write` tool is allowed only with `grant.writes` (851-854), and a tool that is not allowed is refused (docstring 837-838). | `--allow-tool write` (852-854). **INFERENCE:** Copilot does not protect `.claude/`, so writes there probably succeed. |

What this establishes:
- Every read-only session is refused writes there.
- A writing Claude Code session probably is refused (INFERENCE).
- Writing Codex and Copilot sessions probably are not.

That last point is why a file a writing run changed itself is never overwritten (§2).

## 2. Placing files named by `command` conditions

`save_answer` gets a second kind of target, beside today's `file_exists` targets:

- Add `SPEC_PATH = re.compile(r"\.claude/specs/[^\s'\"`;|&<>()]+")`. `SPEC_DIR` captures only the folder name, so it is not enough. For each `command` condition, take every `SPEC_PATH.finditer(cond.run)` match, pass it through `_normalise(goal, m.group(0))`, and keep it only if it ends in `ANSWER_FILES`.
- Skip a path containing `*`, `?`, `[` or `$`. It is a glob or a variable, not a file name.
- Unlike `file_exists`, a command-named path is placed **only** inside the goal's own folder (`rel.startswith(own)` after resolving). The `elif` branch at specs.py:167 does not apply to these paths.
- The existing rules all still apply after `resolve()`:
  - containment in the worktree (159);
  - no `.git` part (162);
  - no `LOOP_FILES` name (165);
  - never another goal's folder: `.claude/specs/own/../x/a.md` resolves out of `own/` and is skipped;
  - dedupe through `rel in saved` (162).
- A writing task never writes a path in `changed`, and `changed is None` means nothing is written (165). A read-only task replaces a stale file.
- Content: the block for that path if one exists (§4); otherwise the answer with all block regions removed. With no blocks that is the whole answer. If the result is empty, nothing is written.
- Order is already correct: `save_answer` (loop.py:1093) runs before `contracts.check` (1102), so the file is on disk when the command runs.

## 3. Prompt note: `specs.task_note(goal, contract) -> str`

- Return "" unless one of the contract's acceptance conditions names a path in the goal's own folder: a `file_exists` path, or a `SPEC_PATH` match in a `command` run, normalised and starting with `relative_folder(goal) + "/"`.
- `loop.py` changes in one place: `head` gets `+ specs.task_note(goal, contract)` right after `_contract_note(contract)` (loop.py:1016).
- The run kind is chosen later, in `_execute`, so the note can only depend on `contract.writes`, and it says only what §1 establishes:
  - **Read-only** (`not contract.writes`): "This session has no write tool for `<own>/`; the loop writes those files from your answer."
  - **Writing:** "Claude Code sessions are refused edits under `.claude/`, so do not spend turns trying; the loop writes files in `<own>/` from your answer. If your tool did write such a file, the loop keeps yours."
- Both variants end with: "To give a file its own content, put it in a block: a line `=== FILE: <worktree-relative path> ===`, the content, then a line `=== END FILE ===`. Without blocks, your whole answer becomes each named document. `<LOOP_FILES>` are never written."

## 4. Multi-document blocks

**Format** (this one only): an opening marker line `=== FILE: <worktree-relative path> ===`, the content lines, then a closing marker line `=== END FILE ===`.
- A marker is a whole line after `strip()`. The open marker regex is `^=== FILE: (.+?) ===$` on the stripped line.
- Markdown fences are not markers, and a marker inside a fence still counts.
- Block content is exactly the lines between the markers.
- Example lines in prose must be quoted inline (as here), never as a bare marker line.

**Parsing:** `_blocks(text) -> tuple[dict[str, str], str]` returns the normalised path → content map and the remainder (the text outside blocks).
- If one path has two blocks, the first wins; the second is logged.
- An open marker with no `END FILE` marker is ignored and logged; its lines stay in the remainder.
- An `END FILE` line with no open block is plain text.

**Placing a block:** a block is placed, even when no condition names its path, if all of these hold:
- its path resolves inside the goal's own folder (same `resolve()`, `.git` and containment checks as §2);
- its name is not in `LOOP_FILES`;
- its suffix is in `ANSWER_FILES` or `.json`, `.yaml`, `.yml` (`BLOCK_FILES`; the extra suffixes apply to blocks only).

The writing and read-only rules of §2 apply. Any other block is ignored and logged with `log_event("task", f"block {path} of task {key} ignored: <why>", goal_id=…)`, where `<why>` is one of: loop file, outside the goal's spec folder, not a document, changed by the run, empty.

**Content precedence:** a named target (`file_exists` or command) that has a block gets the block's content, not the whole answer. A named target without a block gets the remainder. **With no blocks at all**, every path takes today's code path unchanged: `file_exists` targets get the whole answer.

## 5. Data flow

`saved` stays a list of worktree-relative paths that were written. The flow after it needs no change:
- `_checkpoint(…, saved)` commits them (loop.py:1095);
- `gate["saved_answer"]` (1113, 1123, 1131) → `write_outputs`: `named` (specs.py:203) and `kept` (202);
- `requires_change(changed ∪ saved)` (1107);
- the BLOCKER skip, which already requires `not saved` (1119).

**Normal case.** A read-only architect task `spec` with condition `command: grep -q Interface .claude/specs/0401-…/design.md` and an answer with no blocks.
- `saved == [".claude/specs/0401-…/design.md"]` holds the whole answer and is committed as "spec answer".
- `contracts.check` runs grep against it.
- `write_outputs`: the architect task matches the `spec` key, so it is the spec. `design.md` is in `kept`, so it is not overwritten at 218 (and `design` is empty anyway).

**Edge case.** A writing developer task with `changed == ["src/x.py"]` and two blocks: one for `.claude/specs/0401-…/design.md`, one for `.claude/specs/0401-…/decisions.md`.
- The decisions.md block hits `LOOP_FILES`: it is ignored and logged.
- `saved == [".claude/specs/0401-…/design.md"]`; the file holds the block content, not the work report.
- `requires_change` sees `["src/x.py", design.md]` and passes.
- The BLOCKER check is not reached, because `changed` is not empty and `saved` is not empty.
- `kept` contains design.md, so `write_outputs` keeps it.

**Error case.** A writing task with `changed == []` whose answer has one block for `.claude/specs/0399-other/spec.md` and condition `command: test -s .claude/specs/0401-…/notes.md`. notes.md is not writable (its parent is a file, an `OSError`).
- The foreign block is ignored and logged.
- `_write` raises; it is logged and skipped (specs.py:171-173).
- `saved == []`, so no answer checkpoint is made and `commit` is unchanged.
- `contracts.check` fails the `test -s` condition.
- `requires_change` is skipped because a `command` condition exists.
- If the output has a `BLOCKER:` line, the existing skip applies: `writes`, `changed == []` and `not saved`.

## 6. Tests for tests/test_spec_answer.py

1. A command condition naming an own-folder `.md` is written with the whole answer when there are no blocks, by a read-only task and by a writing task.
2. Command paths that are a `LOOP_FILES` name, another goal's folder, `..` out of the folder, `.git`, a glob, `$VAR` or a non-document are never written.
3. A command path that is absolute or starts with `./` is normalised and written once, even when a `file_exists` condition names the same path (dedupe).
4. A writing task does not overwrite a command path in `changed`, and with `changed=None` nothing is written. A read-only task replaces a stale file.
5. Two blocks write two files with their own contents. A named path without a block gets the remainder.
6. An unnamed `.json`/`.yaml` block in the own folder is placed. An unnamed `.py` block is ignored and logged.
7. Blocks for decisions.md, another goal's folder or `../x.md` are ignored and logged; the other blocks are still placed.
8. Marker parsing: indented markers count; a fenced marker counts; an unterminated block is ignored and logged; for duplicate paths the first wins; an `END` without an open block is plain text.
9. With no blocks, the existing `file_exists` tests stay unchanged (regression).
10. `task_note`: "" without an own-folder path; contains `=== FILE:` and the folder for `file_exists` and command conditions; the read-only and writing wordings differ; it never names another goal's folder.
11. Loop (`run_with`): `head` contains the note; a design.md placed from a block is in `gate["saved_answer"]` and `write_outputs` keeps it; a command condition passes against the placed file.
12. An unwritable target is logged, and `saved` leaves it out.

FINDING: Whether Claude Code refuses `.claude/specs/` edits under acceptEdits is unverified here (INFERENCE), and the GM configure step (loop.py:830) suggests the protection may be narrower than assumed.
NEXT: Developer implements §2–§4 in specs.py, plus the one-line `head` change in loop.py:1016, with tests 1–12.
NEXT: QA confirms the §1 inference from the `Run.tool_calls` of a writing Claude Code run on a `.claude/specs/` path.
MESSAGE recommendation to developer: Keep loop.py's only change to the `task_note` line in `head`; `saved` already flows to `_checkpoint`, `write_outputs`, `requires_change` and the BLOCKER skip (spec §5).
