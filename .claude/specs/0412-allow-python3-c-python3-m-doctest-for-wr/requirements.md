# Requirements: Allow `python3 -c` / `python3 -m doctest` for writing developer runs

the acceptance commands use them, and developers currently can't run them to check their own work.

(Raised by an agent during benchmark goal #411, which ran in a scratch repository; the change belongs to the Agent Company platform.)

## Quality commands
- `uv run pytest -q`
