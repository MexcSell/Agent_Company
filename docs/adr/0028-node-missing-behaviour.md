# ADR 0028: node-missing-behaviour

Status: Accepted
Date: 2026-11-06
Decision record: #28 · goal #29 (run `node --check` on static/*.js in the test suite)
Category: technical

## Context

When node is not installed, should the static JS test skip or fail?

## Options

- skip with a visible reason
- fail
- fall back to a Python JS parser

## Criteria

- the test suite keeps working on machines without node
- a syntax error cannot pass silently
- no new dependency

## Decision

skip with a visible reason

## Reasons

The host that runs the gate has node (FACT: /usr/local/bin/node v24), so the check runs there. A failure would break the suite on developer machines without node, and a parser package adds a dependency.

Evidence:

- src/agent_company/checks.py:100-104 (_tool returns None when the tool is missing) (FACT)
- node --version on the host: v24.22.2 (MEASURED)

## Consequences

Assumptions:

- the L3 sandbox's allow-listed PATH includes /usr/local/bin

Limitations:

- if the sandbox PATH leaves out /usr/local/bin, the test skips at the gate; the gate output's skip reason shows this

Confidence: 0.75 (The sandbox PATH was not checked directly. The gate's recorded pytest output will show whether the test ran or skipped.)
