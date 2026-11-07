# ADR 0093: Bridge server data path

Status: Accepted
Date: 2026-11-07
Decision record: #93 · goal #100 (EL-R-09: a bridge MCP server for the CLI agents (Codex, Claude Code))
Category: technical

## Context

Should the bundled bridge server write the database itself, or forward each call to the running API with a per-run token?

## Options

- Thin stdio shim that forwards to a loopback API endpoint (bearer per-run token); the endpoint does scope, grant check, writes and the McpCall log
- The stdio server imports agent_company and writes the SQLite DB directly (DATA_DIR passed in its env)

## Criteria

- single SQLite writer
- bundled-server isolation (bare env, no app import)
- where scope and grant are enforced
- testability offline

## Decision

Thin shim + loopback API endpoint

## Reasons

Bundled servers are stdlib-only and start with a bare environment (mcp_servers/_stdio.py docstring; mcp.py _Stdio). CLAUDE.md says SQLite allows one writer and that DB writes go through the service's session(). Claude Code calls stdio servers directly, so mcp.call() never sees those calls. Only a service-side endpoint can both enforce the grant and write the McpCall log. With this design the server process never gets the DB path, so an agent that runs the script itself still holds only its own run's token.

Evidence:

- src/agent_company/mcp_servers/_stdio.py (no import of the app, bare environment) (FACT)
- src/agent_company/executors.py:679-694 (Claude Code gets --mcp-config from a temp file; calls go straight to the server) (FACT)
- src/agent_company/mcp.py:548-573 (call() is the only path that writes McpCall today) (FACT)
- CLAUDE.md Conventions: one SQLite writer (FACT)

## Consequences

Assumptions:

- Claude Code runs on the host and can reach 127.0.0.1:4910 (the app's listen address)

Limitations:

- Needs the API to be running. In tests the endpoint is driven via TestClient or the handler directly, not through a live server
- A sandboxed CLI without loopback network could not use the bridge

Confidence: 0.80 (Follows from existing code constraints. The loopback reachability of later Codex runs is not verified.)
