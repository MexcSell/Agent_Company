# Requirements: Exempt the task's own worklog from allowed-paths checks

Goals restricted to one file (like this one, and K116 on goal #46) clash with the build task's required `WORKLOG.md`, which makes the developer's task fail.

(Proposal #36, raised during benchmark goal #47; the change belongs to the Agent Company platform. Re-created after the first attempt ran in the eval scratch repository.)

## Quality commands
- `uv run pytest -q`
