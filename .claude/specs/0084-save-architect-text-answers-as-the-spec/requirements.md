# Requirements: Save architect text answers as the spec file

the loop (or `specs.py`) should save the architect's text to the expected `.claude/specs/<slug>/spec.md`, or allow that path, so `file_exists` checks stop failing.

## Quality commands
- `uv run pytest -q`
