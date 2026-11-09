# Requirements: Add a .gitignore for __pycache__ to eval seed repos

Test runs then can't leave untracked out-of-scope files behind that fail the change-boundary check.

(Proposal #5, raised during benchmark goal #7; the change belongs to the Agent Company platform. Re-created after the first attempt ran in the eval scratch repository.)

## Quality commands
- `uv run pytest -q`
