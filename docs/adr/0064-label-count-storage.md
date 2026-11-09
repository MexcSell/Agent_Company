# ADR 0064: label-count storage

Status: Accepted
Date: 2026-11-07
Decision record: #64 · goal #51 (Cache label counts per finished goal)
Category: technical

## Context

Where should a finished goal's label counts be stored, and when should they be computed?

## Options

- JSON column Goal.label_counts filled at finish plus a fill-once fallback on first read
- in-process TTL cache in api.py
- separate goal_labels table

## Criteria

- survives a restart
- older goals without counts
- no full report per 15 s poll
- SQLite one-writer rule
- simplicity

## Decision

JSON column Goal.label_counts, filled when the goal finishes and, if missing, once on first read; cleared when the goal moves back into an ACTIVE status

## Reasons

The counts are one small dict per goal and are needed after a restart. An in-memory cache is lost on restart and has to be rebuilt per process. A separate table adds a join for the same data. Filling the counts on first read covers older goals without a backfill migration.

Evidence:

- src/agent_company/api.py:2367 (FACT)
- src/agent_company/static/simple.js:21 (FACT)
- src/agent_company/workflow.py:99 (FACT)
- K494 (INFERENCE)

## Consequences

Assumptions:

- A finished goal's gate records and review verdicts no longer change unless the goal moves back into an ACTIVE status

Limitations:

- A late change to a finished goal's Task.gate made outside set_status would leave stale counts until store_label_counts runs again

Confidence: 0.80 (The data flow was traced in the code; nothing has been measured yet)
