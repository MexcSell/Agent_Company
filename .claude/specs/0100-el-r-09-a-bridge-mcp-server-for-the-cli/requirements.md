# Requirements: EL-R-09: a bridge MCP server for the CLI agents (Codex, Claude Code)

See docs/requirements/EVOLUTION-LOOPS.md EL-R-09 (the 'Codex components and Bridge' conversation). A bundled stdio MCP server in src/agent_company/mcp_servers/ offering get_task, post_progress, save_insight and request_review, scoped to the calling run's own goal and task (the run passes a per-run token; no other goal is reachable), writing only to the blackboard and the proposals queue (never approvals, budgets, permissions or merges). Not granted to anyone by default (everyone=False); an administrator grants it per agent in Tools & MCP; calls are logged like other MCP calls. Add the threat-model row. Acceptance: pytest that a granted agent's post_progress reaches its goal's blackboard and a call for another goal or without a grant is refused.

## Quality commands
- `uv run pytest -q`
