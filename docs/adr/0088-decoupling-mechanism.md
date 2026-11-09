# ADR 0088: decoupling-mechanism

Status: Accepted
Date: 2026-11-07
Decision record: #88 · goal #85 (Make `threat_model` depend only on the existence of `browser.py` and the test names it cites, not on the DNS-rebinding t)
Category: technical
Supersedes: [0087-decoupling-mechanism](0087-decoupling-mechanism.md)

## Context

Keep decision #87 (order-only `after` plus worktree `requires` preconditions, with reviews running on partial work) or change it?

## Options

- Keep #87 as implemented in ac4f7f0
- Soft depends_on flag per edge
- Drop the dependencies and order by priority only

## Criteria

- Stops one failed task from skipping threat_model and the review (K237)
- Old plans stay valid
- Precondition reads cannot escape the worktree
- Size of the change

## Decision

Keep #87 unchanged; iteration 2 only verifies it and fills test/doc gaps.

## Reasons

The implementation and 11 tests are already committed (FACT: git diff main --stat shows contracts.py +59, scheduler.py +43, loop.py +26, tests 194 lines). The iteration-1 failure was a 600 s run timeout, not a design fault (FACT: the gate finding). Redesigning now would spend the last iterations again, as happened in K237 and K623 (INFERENCE).

Evidence:

- commit ac4f7f0 (FACT)
- tests/test_task_dependencies.py (FACT)
- K237 (FACT)
- K623 (INFERENCE)

## Consequences

Assumptions:

- The committed tests pass or need only small fixes. I could not run pytest during planning because the tool approval was refused.

Limitations:

- The live loop runs from main, so `after` and the partial-review rule only take effect for goals after the merge; this goal's own QA still uses a hard depends_on.

Confidence: 0.75 (The design is sound and tested on paper, but I have not seen the test results.)
