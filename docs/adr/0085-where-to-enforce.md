# ADR 0085: where_to_enforce

Status: Accepted
Date: 2026-11-07
Decision record: #85 · goal #77 (Build acceptance that cannot pass on old files)
Category: technical

## Context

Should the build rule be enforced at plan validation (reject plans whose writing tasks lack a command) or at run time (fail an empty diff without a command)?

## Options

- run time in loop.run_task via contracts.requires_change
- plan time in specs.check_plan
- both

## Criteria

- matches the goal wording 'change files in this goal's diff OR include a test command'
- does not reject valid plans
- small change

## Decision

run time in loop.run_task via contracts.requires_change

## Reasons

The goal lets a build pass either by changing files or by having a test command. Whether files changed is only known after the run, so a plan-time check would have to reject every writing task without a command, which is stricter than the goal. Running the check after the BLOCKER_SKIP branch keeps the pause-for-a-person behaviour for blocked builds.

Evidence:

- src/agent_company/loop.py:1027-1049 (FACT)
- src/agent_company/contracts.py:259 limits (FACT)
- K624 (FACT)

## Consequences

Assumptions:

- the loop's saved answers for a writing task are legitimate new content of this run

Limitations:

- a task with a command condition whose command only tests old files can still pass; the command is the planner's responsibility

Confidence: 0.75 (The rule is small and local. Counting saved answers as changes is a judgement call.)
