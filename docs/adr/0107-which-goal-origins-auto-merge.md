# ADR 0107: which goal origins auto-merge

Status: Accepted
Date: 2026-11-08
Decision record: #107 · goal #251 (Owner policy: auto-approve follow-up proposals and auto-merge goals that pass the gate)
Category: technical

## Context

Should auto_merge also merge goals from origin a2a, origin improvement and origin eval?

## Options

- every origin
- exclude a2a and eval
- exclude a2a, eval and improvement

## Criteria

- threat-model promise that A2A work always waits for a person
- the self-evolution verification already enforced in approve()

## Decision

Exclude a2a, because the threat model and a2a.py promise an outside caller's work always waits for a person. Exclude eval, which never merges. Include improvement: approve() refuses an unverified candidate through selfevolve.merge_block, and the goal then stays waiting with a notification.

## Reasons

This is the stricter choice for the only untrusted-origin path. It keeps an existing documented security promise without overriding the owner's general instruction.

Evidence:

- docs/threat-model.md:28 (A2A tasks always wait for approval) (FACT)
- src/agent_company/loop.py:1628-1634 (FACT)

## Consequences

Assumptions:

- (none recorded)

Limitations:

- If the owner also wants A2A work merged automatically, that is a later and separate change.

Confidence: 0.75 (Based on the documented A2A promise. The owner may decide otherwise.)
