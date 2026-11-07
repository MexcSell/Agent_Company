# Requirements: Build acceptance that cannot pass on old files

Require the build to change files in this goal's diff or to include a test command, so a blocked developer cannot pass the build step.

## Quality commands
- `uv run pytest -q`
