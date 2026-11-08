# ADR 0106: auto-merge vs S14-R-02 'Merge: independent approval'

Status: Accepted
Date: 2026-11-08
Decision record: #106 · goal #251 (Owner policy: auto-approve follow-up proposals and auto-merge goals that pass the gate)
Category: technical

## Context

S14-R-02's action table requires independent approval for a merge. Can an owner-enabled setting stand in for that approval?

## Options

- auto_merge setting (owner's standing approval; the gate and the QA reviewer remain the independent check)
- keep a person's click for every merge

## Criteria

- the owner's explicit instruction of 2026-11-08
- the independent checks that remain in place
- whether it can be reversed

## Decision

Add auto_merge, off by default and admin-only, as the owner's recorded standing approval. It goes through loop.approve, so every merge-time block still applies: decisions waiting for a person, open critical defects, selfevolve.merge_block, cancelled or archived projects, and merge conflicts. CLAUDE.md and the threat model record it as the one exception to 'merging needs approval'.

## Reasons

The goal text is the person's decision (the owner ordered it). The gate and the review verdicts stay independent of the authoring agent. Turning the setting off restores today's behaviour, and every change to it is versioned in policy_versions.

Evidence:

- src/agent_company/loop.py:2174-2214 (approve checks decisions, critical defects, merge_block, project status) (FACT)
- goal #251 description (owner instruction 2026-11-08) (FACT)
- src/agent_company/policies.py:34 GOVERNANCE_KEYS (versioned with author) (FACT)

## Consequences

Assumptions:

- The owner wants the setting to apply to every project of this installation

Limitations:

- A project cannot yet opt out on its own. A per-project stricter rule is out of scope for this goal.

Confidence: 0.80 (The goal states the owner's will directly. The rest follows from the existing approve() checks.)
