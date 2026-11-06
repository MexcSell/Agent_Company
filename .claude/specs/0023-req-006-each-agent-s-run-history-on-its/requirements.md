# Requirements: REQ-006: each agent's run history on its card

First check what static/agents.js already shows. The agent card lists the agent's runs (Run rows in db.py: goal, task, time, model, tokens, cost, outcome), newest first, paged, and opens a run's recorded messages and tool calls. API in api.py with the existing permissions. Tests for the API paging and permissions.

## Quality commands
- `uv run pytest -q`
