# Requirements: Pin non-str behaviour of slugify

An assertRaises(AttributeError) test for None would stop someone later adding a guard that quietly returns '' for bad input.

(Raised by an agent during benchmark goal #398, which ran in a scratch repository; the change belongs to the Agent Company platform.)

## Quality commands
- `uv run pytest -q`
