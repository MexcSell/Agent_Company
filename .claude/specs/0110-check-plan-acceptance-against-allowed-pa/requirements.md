# Requirements: Check plan acceptance against allowed paths

When the planner writes a plan, it should reject any `file_exists` acceptance whose path is outside the goal's allowed paths, because that combination can never pass.

(Proposal #57, raised during benchmark goal #57; the change belongs to the Agent Company platform. Re-created after the first attempt ran in the eval scratch repository.)

## Quality commands
- `uv run pytest -q`
