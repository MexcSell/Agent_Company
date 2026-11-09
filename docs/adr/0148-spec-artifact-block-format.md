# ADR 0148: spec-artifact-block-format

Status: Accepted
Date: 2026-11-09
Decision record: #148 · goal #401 (Let the loop place task-produced spec artifacts)
Category: technical

## Context

How does a task's answer carry one or more spec documents for the loop to place in `.claude/specs/<goal>/`?

## Options

- Whole answer only (today): one document per answer, a writing task's file gets its report
- Explicit marker lines `=== FILE: <path> ===` … `=== END FILE ===`, whole answer when there are none
- Markdown code fences with a path header

## Criteria

- unambiguous parsing
- backward compatibility with today's whole-answer behaviour
- no way to write outside the goal's own spec folder or loop files

## Decision

Explicit marker lines, placed only inside the goal's own spec folder, with the whole answer as the fallback when the answer has no blocks.

## Reasons

Documents often contain fenced code, so fences used as delimiters would nest and break parsing. Whole-line markers are unambiguous. The fallback keeps every existing save_answer test and plan valid, and the existing refusal rules (loop files, other folders, .git, escapes, run-changed files) apply unchanged.

Evidence:

- src/agent_company/specs.py:141-175 (save_answer writes the whole answer, file_exists only) (FACT)
- src/agent_company/loop.py:1012-1029 (the task prompt has no note about the spec folder) (FACT)
- src/agent_company/executors.py:737 (Claude Code runs with acceptEdits; no deny rule for .claude/ in this repository, so the refusal is the CLI's own) (INFERENCE)
- K54, K329: plans whose spec-folder conditions could never pass (FACT)

## Consequences

Assumptions:

- Agents follow a format the prompt states explicitly

Limitations:

- An agent that ignores the format still gets today's whole-answer behaviour; how each CLI refuses writes is confirmed by the spec task, not here

Confidence: 0.75 (The mechanism builds on tested code; whether each CLI refuses .claude/ writes is still to be confirmed by the spec task)
