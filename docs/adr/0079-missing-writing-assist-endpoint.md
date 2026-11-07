# ADR 0079: missing-writing-assist-endpoint

Status: Proposed (waits for a person)
Date: 2026-11-07
Decision record: #79 · goal #65 (Daily per-user cap for the writing assistant)
Category: technical

## Context

The goal names `/api/writing/assist`, but no such route exists on the base branch. What should this goal deliver?

## Options

- Build the reusable per-user daily token cap (Run.user_id, usage.user_cap, setting) with tests now, and attach it once a writing-assist endpoint exists
- Also build a `/api/writing/assist` endpoint in this goal (a new feature with its own scope and threat-model entry)
- Close the goal: the abuse path does not exist on this deployment

## Criteria

- solves a real exposure
- scope and cost
- no speculative endpoint without a spec
- reuse for other user-triggered model calls

## Decision

Build the reusable cap with tests now, and attach it to the endpoint if the architect finds it under another name. Do not create a new endpoint inside this goal.

## Reasons

A search of src/, tests/ and docs/ for 'writing/assist', '/api/writing' and 'writing assist' returned nothing (FACT). Runs carry no user id (FACT, db.py:763), so per-user accounting is missing groundwork either way. Building a new user-facing model endpoint would widen the attack surface and needs its own spec and threat-model update (INFERENCE from CLAUDE.md's rule on new network paths).

Evidence:

- src/agent_company/api.py (no 'writing' match) (FACT)
- src/agent_company/db.py:763 Run has no user_id (FACT)
- src/agent_company/usage.py:79 over_limit is per model only (FACT)
- Research notes for this goal: no supplied evidence on quota mechanics (FACT)

## Consequences

Assumptions:

- The endpoint may exist in another deployment or branch that this worktree does not contain

Limitations:

- Without the endpoint, the cap protects nothing until it is attached; the history search over all branches could not be run in this session

Confidence: 0.70 (The absence on this branch is certain; what the requester meant is not)
