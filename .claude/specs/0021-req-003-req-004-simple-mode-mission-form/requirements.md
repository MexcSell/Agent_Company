# Requirements: REQ-003 + REQ-004: Simple mode mission form and the mission report

REQ-003: a Simple mode (default for a new user, switchable per user) with one form: project, goal, budget, deadline, experience/notes, resources (repositories, files, links), constraints, which starts a goal (goal budget via budgets.py). Its dashboard shows running goals with progress, cost vs budget, time vs deadline, blockers, waiting approvals and delivered results; Advanced = today's dashboard unchanged. Files: static/app.js (+ a new static file if needed), api.py. REQ-004 (same goal): after a goal finishes (merged, failed, stopped), one mission report: objective, plan, tasks done/failed/retried, verification evidence (checks, reviews), cost, time, remaining limitations, lessons, recommendations; each task labelled verified, partial or failed from recorded check and review results, never from an agent's claim. Extend loop.digest (~1272) / the result message; show it on the goal and in Simple mode. Tests for the report and the API.

## Quality commands
- `uv run pytest -q`
