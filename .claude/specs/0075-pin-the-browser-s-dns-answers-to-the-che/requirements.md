# Requirements: Pin the browser's DNS answers to the checked address

closes the DNS-rebinding gap between check_url's own lookup and the browser's (e.g. Chromium's --host-resolver-rules built from check_url's result).

## Quality commands
- `uv run pytest -q`
