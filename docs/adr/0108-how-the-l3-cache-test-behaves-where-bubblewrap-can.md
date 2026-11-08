# ADR 0108: How the L3 cache test behaves where bubblewrap cannot start

Status: Accepted
Date: 2026-11-08
Decision record: #108 · goal #109 (Run pytest without leaving cache folders in the worktree)
Category: technical

## Context

The gate rejects runtime pytest.skip. How should the L3 test cover environments, including the gate's own nested L3 run, where bwrap cannot start?

## Options

- A skipif marker whose condition calls sandbox.available() at import time
- Branch inside the test body: real L3 run when the sandbox is usable, otherwise assert the required-mode refusal; plus an unconditional test of the sandbox command line
- Ask for the goal permit skip_tests

## Criteria

- passes the boundary check without a permit
- every test asserts something in every environment
- no test touches the real data/ directory

## Decision

Branch inside the test body plus an unconditional command-line test

## Reasons

FACT: checks.SKIP_MARKER flags pytest.skip( but not skipif. FACT: sandbox.available() -> build() reads settings() and home(), which depend on DATA_DIR, and conftest sets DATA_DIR only in a function fixture. A probe at import time would use the real data/ directory and cache _probe. INFERENCE: The command-line test catches a regression in env_for even where bwrap cannot run, which the previous review noted as a gap. A skip_tests permit would hide the limitation instead of covering it.

Evidence:

- src/agent_company/checks.py:168-170 SKIP_MARKER (FACT)
- src/agent_company/sandbox.py:155-211 build/available (FACT)
- .claude/specs/0109-run-pytest-without-leaving-cache-folders/review.md FINDING on nested L3 (FACT)

## Consequences

Assumptions:

- sandbox.build with limits=False neither runs a process nor needs bwrap

Limitations:

- Inside the gate's nested L3 run, the real bwrap pytest run is still not executed; it is executed on the host and in the QA run.

Confidence: 0.80 (Based on the code that was read; the gate's nested environment has not been run in this plan.)
