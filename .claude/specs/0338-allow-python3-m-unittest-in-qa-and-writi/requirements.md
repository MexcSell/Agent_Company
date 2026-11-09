# Requirements: Allow `python3 -m unittest` in QA and writing runs' command checks

Tasks and reviews are told to run stdlib unittest acceptance commands, but the run's permissions refuse them; this has now blocked the developer and QA in goal 318 and also caused K391 in goal 122.

(Raised by an agent during benchmark goal #318, which ran in a scratch repository; the change belongs to the Agent Company platform.)

## Quality commands
- `uv run pytest -q`
