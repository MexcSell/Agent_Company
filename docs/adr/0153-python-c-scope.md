# ADR 0153: python-c-scope

Status: Accepted
Date: 2026-11-09
Decision record: #153 · goal #412 (Allow `python3 -c` / `python3 -m doctest` for writing developer runs)
Category: technical

## Context

Which agent runs may run `python -c` and `python -m doctest`: writing runs only, or also read-only check runs (QA reviews)?

## Options

- writing runs only (CLAUDE_BASH, COPILOT_SHELL)
- writing and read-only check runs (all four lists)
- leave refused; planners avoid these commands

## Criteria

- does it widen what a run can already do
- does it unblock the goal's developers
- can a read-only run write files through it

## Decision

writing runs only (CLAUDE_BASH, COPILOT_SHELL)

## Reasons

Writing runs already run any Python through `Bash(uv run:*)`, so a direct `python3 -c` prefix adds nothing. Read-only runs deliberately lack `uv run:*`, `npm run:*` and `npm test:*` because those run any code and so could write files (executors.py:665). `python3 -c` would give them file writes, which they are designed not to have. The goal asks only for writing developer runs.

Evidence:

- src/agent_company/executors.py:663 (CLAUDE_BASH has Bash(uv run:*)) (FACT)
- src/agent_company/executors.py:665 (comment: a run without writes gets no uv run/npm run because they could write files) (FACT)
- tests/test_phase4_communication.py:426-435 and tests/test_unittest_forms.py:9-37 (bare python and -c refused in all lists today) (FACT)
- goal #411 (developer could not run the plan's python3 -c / doctest acceptance commands) (FACT)
- QA can still verify python -c acceptance results because the gate runs acceptance commands itself in the L3 sandbox (INFERENCE)

## Consequences

Assumptions:

- Claude Code and Copilot match these rules as command prefixes, as they do for the existing unittest/pytest rules

Limitations:

- A QA review run still can't re-run a `python3 -c` acceptance command itself; it relies on the gate's result
- Agent CLI runs stay L2 on the host (threat-model Known gaps); this doesn't change that

Confidence: 0.85 (Same shape as goal #338's unittest change, which passed its gate. The prefix-matching behaviour of the CLIs was not verified live.)
