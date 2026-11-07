# ADR 0094: Codex wiring scope

Status: Accepted
Date: 2026-11-07
Decision record: #94 · goal #100 (EL-R-09: a bridge MCP server for the CLI agents (Codex, Claude Code))
Category: technical

## Context

Should iteration 1 also hand the bridge to Codex runs?

## Options

- Claude Code only now; Codex in a follow-up goal
- Wire Codex MCP config now as well

## Criteria

- gateway change size
- risk of a 600 s run timeout / budget overrun (K623, K234)
- acceptance of EL-R-09

## Decision

Claude Code only in this goal. Keep the server CLI-agnostic and leave Codex wiring as a documented open item.

## Reasons

Today the tool gateway gives MCP to claude_code only (executors.py:367, grant.mcp). Giving Codex MCP would widen the gateway for a second executor, which is a separate security change. EL-R-09's acceptance needs only a granted call reaching the blackboard and refusals.

Evidence:

- src/agent_company/executors.py:366-369 (mcp only when kind == claude_code) (FACT)
- K234, K623 (overrun and timeout on oversized developer tasks) (MEASURED)

## Consequences

Assumptions:

- (none recorded)

Limitations:

- The goal title names Codex. Codex agents cannot use the bridge until the follow-up.

Confidence: 0.70 (This is a scoping choice. A person may prefer Codex now.)
