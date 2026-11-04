# Agent Company

An AI software-development company built from specialized agents (CrewAI models, Claude Code, Codex).
See ReadMe.mdx for roles, architecture, services and stack; docs/feature-gap.md for where each
feature came from.

## Layout

- `src/agent_company/` – one module per service:
  `registry.py` + `org.py` (agents, rooms), `scheduler.py`, `router.py` (models, routes, fallback,
  provider catalogs), `contracts.py`, `executors.py`, `worktrees.py`, `quality.py` + `checks.py`
  (gate: contracts, verdicts, secrets, syntax, scope, baseline), `usage.py`; `loop.py` (circular
  goal loop, budgets, best-iteration rollback, handovers); `projects.py` (projects, command window,
  proposals, uploads); `learning.py` + `evolution.py`; `performance.py` (defects, certification,
  metrics); `evaluation.py` (eval harness); `research.py`; `governance.py` (audit, notifications,
  command policy); `prefs.py` (DB settings); `gitsync.py`; `files.py`; `supervisor.py` + `system.py`;
  `auth.py` (device sign-in, permissions); `api.py`; `static/` (dashboard, plain JS, one file per
  area); `crew.py` (classic crew); `__main__.py` (CLI)
- `evals/` – benchmark suites (JSON)
- `tests/` – pytest; every test gets its own temp DATA_DIR and runs offline
- `data/` – SQLite DB, workspaces, worktrees, uploads (git-ignored)
- `docs/feature-gap.md` – features taken from AgentForge, /home/thomas/bit and /home/luca/update

## Conventions

- Python 3.12+, dependencies managed with `uv` (system pip is externally managed).
- Models come from the Model Router (catalog + role routes in the DB); `MODEL` in `.env` is the
  fallback. Never hard-code keys. The offline simulator only stands in when no real model is usable.
- Only the core pipeline (GM, Architect, Developer, QA) and the research rooms are enabled by
  default; other roles are registered but disabled until the pipeline is reliable.
- Agents must not get access to production servers. Executors that change files run only inside
  `data/worktrees/` (`worktrees.ensure_sandboxed`); commands pass `governance.check_command`;
  merging needs approval; pushing only on `/sync` or AUTO_PUSH.
- DB writes inside an open `session()` must use that session (`log_event(..., s=s)`,
  `notify(..., s=s)`), never a second connection: SQLite allows one writer.
- New tables/columns: add them to `db.py`; `_add_missing_columns` migrates existing databases.
- Administrative API actions call `_audit(...)`.

## Commands

- `uv sync` – install dependencies
- `uv run pytest -q` – run the tests
- `uv run python -m agent_company supervise` – API + dashboard on http://127.0.0.1:4900 with safe restarts
- `uv run python -m agent_company serve` – the same without the supervisor
- `uv run python -m agent_company goal "<goal>" --check "<cmd>" [--allow 'src/*'] [--protect-tests]`
- `uv run python -m agent_company eval --suite smoke --label <name> [--repeats 3]` / `eval --compare a b`
- `uv run python -m agent_company "<request>"` – classic four-agent crew
