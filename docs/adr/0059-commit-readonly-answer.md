# ADR 0059: commit-readonly-answer

Status: Accepted
Date: 2026-11-07
Decision record: #59 · goal #33 (Let specs.py write role-provided spec files to the task's acceptance path)
Category: technical

## Context

How is a read-only task's saved answer committed so that it survives _reconcile and is not attributed to a later writing task?

## Options

- Commit only the saved paths under scheduler.write_lock right after saving
- Leave it for the gate's commit_all
- Exclude answer files from writing tasks' checkpoints

## Criteria

- survives a restart (S04-R-05)
- correct attribution in `changed`/out_of_scope
- no deadlock or race with parallel writing tasks
- smallest change

## Decision

Commit only the saved paths under scheduler.write_lock right after saving

## Reasons

run_dag runs read-only workers without the lock and writing workers with it (scheduler.py:92-97). Taking the lock in the read-only path serialises the commit against writing checkpoints. Committing only the saved paths keeps a concurrent writer's files out of it. Leaving it for the gate's commit_all is the iteration-1 behaviour the reviewer failed.

Evidence:

- src/agent_company/scheduler.py:87-97 (FACT)
- src/agent_company/loop.py:1000-1009 (FACT)
- review of iteration 1 (commit 838e7e2 swept spec.md into impl) (FACT)

## Consequences

Assumptions:

- threading.Lock from write_lock is not held by read-only workers

Limitations:

- Does not cover answers saved by tasks that finish after the gate starts

Confidence: 0.80 (Based on reading the scheduler code; not yet tested under real concurrency.)
