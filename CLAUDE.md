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
  command policy); `prefs.py` (DB settings); `gitsync.py` (push with each user's own GitHub token or SSH key, `data/git-keys/`); `files.py`; `supervisor.py` + `system.py`;
  `containers.py` (Docker and LXD VMs on the host: install, start, clean up; admin-only);
  `machines.py` (Machines view, from AXoR in /home/thomas/axor-3.0.zip: Linux/Windows KVM VMs as Docker
  containers with a noVNC console relayed over a WebSocket; admin-only; cleanup never prunes them);
  `mcp.py` (Tools & MCP catalog, from AXoR's mcp_hub: stdio/HTTP servers, encrypted secrets, tool allow-list,
  per-agent grants, call log; bundled servers in `mcp_servers/`); `catalog.py` (skills, plugins, rules, commands,
  hooks; exports a project's `.claude/`); `agentdefs.py` (each agent's versioned definition file in `data/agents/`,
  its own git repo: the loop runs agents from it, learning and the study rotation rewrite it); `gm.py` (the General
  Manager as Claude Code: its instructions and the `python -m agent_company gm …` toolbox); `specs.py`
  (`.claude/specs/<slug>/` artifacts of every goal, the GM's `.claude/` configure step); `hooks.py` (TaskCreated,
  TaskCompleted, TeammateIdle → `data/logs/team-hooks.jsonl`, fail-open);
  `auth.py` (device sign-in, permissions); `transport.py` (HTTPS/HTTP via nginx, own CA, sealed cookie); `terminal.py` (Workspace PTY shells) + `workspace.py`
  (Workspace Claude Code panel: a tab per conversation, titled by Haiku; live host sessions, session history with resume/stop/delete); `api.py`; `static/` (dashboard, plain JS, one file per area; `agents.js` (org chart, Tools & MCP, Skills & plugins, agent card),
  `models.js`; rail order shared by all users via `/api/ui/nav_layout` (Settings → Navigation); buttons with a known
  label become icons (`iconize` in app.js); `index.html` is served with content-hashed static URLs;
  `workspace.js` + `viewer.js` + `console.js` + `claude.js` are the Workspace home page, sharing
  `window.WSP`; `vendor/` holds xterm.js, pdf.js, mammoth and noVNC); `crew.py` (classic crew); `__main__.py` (CLI)
- `evals/` – benchmark suites (JSON)
- `tests/` – pytest; every test gets its own temp DATA_DIR and runs offline
- `data/` – SQLite DB, workspaces, worktrees, uploads (git-ignored)
- `docs/feature-gap.md` – features taken from AgentForge, /home/thomas/bit and /home/luca/update
- `docs/requirements/` – implementable requirements derived from every document in /home/luca/update
  (one file per spec, IDs `Sxx-FR/R/D/G-nn`, status per item, suggested order in its README;
  diagrams in `images/`, sources of non-retained images in `images/SOURCES.md`)

## Conventions

- Python 3.12+, dependencies managed with `uv` (system pip is externally managed).
- Models come from the Model Router (catalog + role routes in the DB); `MODEL` in `.env` is the
  fallback. Never hard-code keys. The offline simulator only stands in when no real model is usable.
- The General Manager is Claude Code by default (route `general_manager`; `enable_claude_code` is on in the DB
  settings). It can be switched on its agent card: another model, a `claude_code` model with a base URL, or
  another agent (`model_pref = "agent:<id>"`). A CLI GM's `.claude/` edits happen in the goal's worktree.
- Only the core pipeline (GM, Architect, Developer, QA) and the research rooms are enabled by
  default; other roles are registered but disabled until the pipeline is reliable.
- Agents must not get access to production servers. Executors that change files run only inside
  `data/worktrees/` (`worktrees.ensure_sandboxed`); commands pass `governance.check_command`;
  merging needs approval; pushing only on `/sync` or AUTO_PUSH.
- Exception, by decision of the owner: the Workspace terminal and Claude Code panel are the
  administrator's own tools, not goal-loop agents. They run host-wide as the server user (no
  command filter, Claude Code with every tool allowed in the opened folder), are admin-only, and
  every session/prompt is audited. Do not route them through the sandbox, and do not loosen
  `ClaudeCodeExecutor`/`CLAUDE_BASH` because of them. Workspace WebSockets must check the Origin.
  The same holds for `containers.py` (Docker, LXD VMs) and `machines.py` (Machines): admin API
  and CLI only, audited; agents and executors never call them.
- DB writes inside an open `session()` must use that session (`log_event(..., s=s)`,
  `notify(..., s=s)`), never a second connection: SQLite allows one writer.
- New tables/columns: add them to `db.py`; `_add_missing_columns` migrates existing databases.
- Administrative API actions call `_audit(...)`.

## Commands

- `uv sync` – install dependencies
- `uv run pytest -q` – run the tests
- `uv run python -m agent_company supervise` – API + dashboard on http://127.0.0.1:4900 with safe restarts
- `uv run python -m agent_company serve` – the same without the supervisor
- `deploy/agent-company.service` – systemd unit (installed, enabled): starts `supervise` at boot.
  The app listens on 127.0.0.1:4910; nginx serves port 4900 to other computers over HTTPS (own CA
  in `data/tls/`, default) or HTTP, switched on Settings -> Connection (`transport.py` writes the
  nginx site, MANAGE_NGINX=1). The first (admin) account can only be registered from localhost.
  `systemctl restart|status agent-company`, log in `data/logs/agent-company.log`
- `uv run python -m agent_company goal "<goal>" --check "<cmd>" [--allow 'src/*'] [--protect-tests]`
- `uv run python -m agent_company eval --suite smoke --label <name> [--repeats 3]` / `eval --compare a b`
- `uv run python -m agent_company host status|install-docker|docker-start|cleanup [--yes]|install-lxd|launch <name> [--container]|stop|delete <name>`
- `uv run python -m agent_company gm status|agents|create-agent|add|mcp|configure|goal|study` – the GM's toolbox
- `uv run python -m agent_company "<request>"` – classic four-agent crew
