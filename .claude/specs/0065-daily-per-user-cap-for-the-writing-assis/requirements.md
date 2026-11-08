# Requirements: Daily per-user cap for the writing assistant

any signed-in user can spend model budget by calling `/api/writing/assist` again and again; a per-user daily token cap would bound it.

## Quality commands
- `uv run pytest -q`
