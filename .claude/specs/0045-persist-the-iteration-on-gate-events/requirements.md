# Requirements: Persist the iteration on gate events

`Event(kind="gate")` has no iteration field today, so reports and traces have to parse the message text to find it.

## Quality commands
- `uv run pytest -q`
