# Agent Company

An AI software-development company built from specialized agents (CrewAI models).
See ReadMe.mdx for roles, architecture, services and stack.

## Layout

- `src/agent_company/` – one module per CDAF service:
  `registry.py` + `org.py` (Agent Registry, rooms), `scheduler.py` (Task Scheduler),
  `router.py` (Model Router), `contracts.py` (Contract Engine), `executors.py` (Execution
  Adapter), `worktrees.py` (Git Worktree Manager), `quality.py` (Quality Gate),
  `usage.py` (Cost and Usage Monitor); plus `loop.py` (circular goal loop),
  `learning.py`, `research.py`, `auth.py`, `api.py`, `static/` (dashboard), `crew.py`
  (classic four-agent crew), `__main__.py` (CLI)
- `tests/` – pytest; every test gets its own temp DATA_DIR and runs offline
- `data/` – SQLite DB, workspaces and worktrees (git-ignored)
- `output/` – classic crew step outputs (git-ignored)

## Conventions

- Python 3.12+, dependencies managed with `uv` (system pip is externally managed).
- Models come from the Model Router (catalog + role routes in the DB); `MODEL` in `.env`
  is the fallback. Never hard-code keys.
- Only the core pipeline (GM, Architect, Developer, QA) and the research rooms are enabled
  by default; other roles are registered but disabled until the pipeline is reliable.
- Agents must not get access to production servers. Executors that change files run only
  inside `data/worktrees/` (`worktrees.ensure_sandboxed`); merging needs approval.
- DB writes inside an open `session()` must use that session, not `log_event` (SQLite lock).

## Commands

- `uv sync` – install dependencies
- `uv run pytest -q` – run the tests
- `uv run python -m agent_company serve` – API + dashboard on http://127.0.0.1:4900
- `uv run python -m agent_company goal "<goal>" --check "<cmd>"` – run one goal
- `uv run python -m agent_company "<request>"` – classic four-agent crew
