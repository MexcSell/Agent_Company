# ADR 0087: decoupling-mechanism

Status: Superseded by 0088-decoupling-mechanism
Date: 2026-11-07
Decision record: #87 · goal #85 (Make `threat_model` depend only on the existence of `browser.py` and the test names it cites, not on the DNS-rebinding t)
Category: technical

## Context

How should a task be decoupled from a sibling whose failure should not skip it?

## Options

- Order-only `after` keys plus worktree `requires` preconditions in the contract
- Only re-plan goal #25 without depends_on on the DNS task (no platform change)
- Make every failed dependency soft for all roles

## Criteria

- prevents the K237 chain in future plans
- keeps old plans' behaviour
- a non-review task never runs on missing input

## Decision

Order-only `after` keys plus worktree `requires` preconditions in the contract

## Reasons

Re-planning alone fixes one plan, and the next planner can rebuild the chain. Making every dependency soft would run developers on missing input. Preconditions name the real inputs (a file and the test names it cites), so it is the state of the worktree that decides, not a sibling's outcome.

Evidence:

- src/agent_company/scheduler.py:108 (FACT)
- K237 (FACT)
- K623 (FACT)
- K538 (FACT)

## Consequences

Assumptions:

- Planners will use `after`/`requires` once PLAN_INSTRUCTIONS documents them

Limitations:

- Does not fix the budget sizing that caused the original task failure (K237's other cause)

Confidence: 0.75 (The scheduler change is small and contained; whether planners adopt the new fields is an INFERENCE)
