# Requirements: Default `everyone` to False for every catalog MCP server

the db default of True grants each newly added server to every agent, which goes against least privilege.

## Quality commands
- `uv run pytest -q`
