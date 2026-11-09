# Requirements: Let specs.py write role-provided spec files to the task's acceptance path

acceptance checks on `.claude/specs/<slug>/` fail every iteration because agents cannot write `.claude/`.

## Quality commands
- `uv run pytest -q`
