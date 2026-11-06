# ADR 0017: task_label_source

Status: Accepted
Date: 2026-11-06
Decision record: #17 · goal #21 (REQ-003 + REQ-004: Simple mode mission form and the mission report)
Category: technical

## Context

Where does a task's verified/partial/failed label come from?

## Options

- Recorded gate/check results and reviewer verdicts on Task.gate and quality records
- The agent's output text

## Criteria

- REQ-004: never from an agent's own claim
- reproducible

## Decision

Label from recorded results only. verified = the task is done, every recorded acceptance/quality check passed, and every reviewer verdict recorded for it is PASS. failed = the task failed or was cancelled, or a recorded check failed, or a review verdict is FAIL. partial = everything else, for example done but with no recorded check or review, or with a check that was skipped or did not run. The label lists the evidence it was based on.

## Reasons

This is what REQ-004 asks for. Treating 'done with no evidence' as partial keeps a task from being called verified when nothing was checked.

Evidence:

- REQ-004 acceptance (FACT)

## Consequences

Assumptions:

- Task.gate and the quality/review records hold per-task check and verdict results; the architect confirms the exact fields

Limitations:

- The exact field names are not verified yet

Confidence: 0.75 (The rule is fixed by the requirement; the field mapping is still open)
