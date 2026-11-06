# Requirements: REQ-005: knowledge import by a person

Knowledge items are Lesson rows in learning.py (screen ~219, FTS5 index, review queue). Add an admin import: a text/Markdown file, a folder (earlier workspace), a chat export (e.g. the Markdown files in data/research/chatgpt-shares/ as a format example; do not commit them) and experience reports. Each becomes knowledge items with source and date, screened, pending review, and not retrieved for tasks until approved. Imported text is data, never executed. API in api.py next to /api/knowledge/* with _audit, CLI under learn, button in static/knowledge.js. Tests: import creates pending items; retrieve() ignores them until approved.

## Quality commands
- `uv run pytest -q`
