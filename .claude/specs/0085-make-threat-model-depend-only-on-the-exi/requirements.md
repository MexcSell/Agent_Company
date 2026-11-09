# Requirements: Make `threat_model` depend only on the existence of `browser.py` and the test names it cites, not on the DNS-rebinding task, and let QA run after whichever tasks succeeded.

This removes the chain where one failed task skips the threat-model change and the review in every iteration.

From root-cause analysis K237 of goal #25. Follow-up: These must pass:

## Quality commands
- `uv run pytest -q`
