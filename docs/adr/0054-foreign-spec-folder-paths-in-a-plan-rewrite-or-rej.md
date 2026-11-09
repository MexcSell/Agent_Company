# ADR 0054: Foreign spec-folder paths in a plan: rewrite or reject

Status: Accepted
Date: 2026-11-07
Decision record: #54 · goal #32 (In plan validation (`loop.py`/`contracts.py`), reject any acceptance path under `.claude/specs/` that is outside `specs.)
Category: technical

## Context

Should plan validation keep rewriting `.claude/specs/<other>/` paths to the goal's folder, which goal #27 added, or reject the plan and send the error back to the GM?

## Options

- Keep the silent rewrite
- Reject and return the error to the GM for one re-plan, then fall back to the standard plan

## Criteria

- The plan's conditions can pass
- The GM learns the right folder
- Iterations and QA are not wasted

## Decision

Reject and return the error to the GM

## Reasons

The goal asks for rejection explicitly. A rewrite hides the mistake from the GM, and it can redirect a command to a file whose content the task never produces. K54 and K329 show conditions that could never pass and used up all three iterations while QA was skipped. Re-planning costs one planner call before staffing, which is cheaper than a failed iteration.

Evidence:

- K54 (FACT)
- K329 (FACT)
- src/agent_company/specs.py:55-86 (current rewrite) (FACT)
- src/agent_company/loop.py:582-613 (one retry with the error in the prompt) (FACT)
- One re-plan costs less than a failed iteration (INFERENCE)

## Consequences

Assumptions:

- The GM corrects the path when the error names the right folder

Limitations:

- If the GM repeats the mistake, the standard plan is used, which may lose the goal's specific work for that iteration

Confidence: 0.80 (The goal states the behaviour it wants. The gain is inferred from three failed goals.)
