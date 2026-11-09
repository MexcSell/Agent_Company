# Requirements: Pin resolved addresses for all outbound fetches

research crawling and marketplace clones share the TOCTOU of resolving the address for the check and again for the fetch; one helper (`curloptResolve`/pinned connect) closes it everywhere.

## Quality commands
- `uv run pytest -q`
