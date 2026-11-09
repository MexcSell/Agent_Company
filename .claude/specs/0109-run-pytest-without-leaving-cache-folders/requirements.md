# Requirements: Run pytest without leaving cache folders in the worktree

Running pytest left `__pycache__/` folders in the worktree, which the scope check or a "no other files changed" rule could flag; `-B -p no:cacheprovider` or a default `.gitignore` would prevent this.

(Proposal #55, raised during benchmark goal #56; the change belongs to the Agent Company platform. Re-created after the first attempt ran in the eval scratch repository.)

## Quality commands
- `uv run pytest -q`
