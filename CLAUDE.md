# Agent Company

An AI software-development company built from specialized agents (CrewAI models, Claude Code, Codex).
See ReadMe.mdx for roles, architecture, services and stack; docs/feature-gap.md for where each
feature came from.

## Layout

- `src/agent_company/` – one module per service:
  `registry.py` + `org.py` (agents, rooms; configuration versions with rollback, health: degraded/inactive), `scheduler.py`, `router.py` (models, routes, fallback,
  provider catalogs, classification clearance per model), `contracts.py` (task contracts with scope, risk, budget, deadline; output lines),
  `executors.py` (tracked subprocesses: `terminate` for stops; `gate`, the tool gateway every run passes: permissions and
  `Agent.autonomy` G0-G4 decide writes, Bash and MCP tools, the isolation level L0-L2 is recorded on the `Run`), `worktrees.py`,
  `quality.py` + `checks.py` (gate: contracts, review verdicts with logic verification, secrets, syntax, scope,
  change boundaries, baseline), `usage.py`; `loop.py` (circular goal loop, budgets that pause, best-iteration
  rollback, handovers, checkpoint commits per writing task + worktree reconcile on resume, retry policy,
  re-plan, conflict review, escalation, behaviour rules, idempotent merge/push, trace; goals of one project topic
  run in order, `lane_ahead`); `workflow.py` (goal
  state machine, `goal_transitions`); `blackboard.py` (versioned shared goal context, agent messages);
  `network.py` (SPEC-12 relationship kinds with evidence: observed links from finished work,
  complementary specialists and a mentor candidate for new agents, administrator corrections, fair staffing
  inputs in `registry._rank`; `graph`/`overview` for the network view) + `mentoring.py` (a mentor per learning plan: reviews practice results, never certifies);
  `requirements.py` (ASEAO08 S08-R-01: project requirements with versions, links from
  tasks, checks and merges, `traceability`, conflicts as decisions, `import_docs` of `docs/requirements/`);
  `decisions.py` (decision records; human ones block the merge; S07-R-01 reports: criteria, limitations, evidence
  classes, independent reviews that send a disputed decision to a person); `escalation.py` (S04-R-07 levels 1-5 and their
  recipients: agent, goal GM, room manager, executive GM, a person; delivered as blackboard requests);
  `budgets.py` (S04-R-09 goal budgets: cost, tokens, agents at once; child goals capped by the parent's remainder); `projects.py` (projects with stack, skills, rooms, budget, deadline, priority and a lifecycle `set_status`; command window
  with topics (conversation lanes, `/summary`, `DECISION:` lines → decision records), proposals, uploads); `management.py` (milestones, risks and blockers, insights, ranked action items, progress;
  blockers and generated items are reconciled from goal state by `sync`); `kgraph.py` (SPEC-11 knowledge graph: `kg_edges` written as side effects, the paged neighbourhood API);
  `learning.py` (knowledge items: provenance, experience reports, screening, scoped ranked retrieval;
  contradictions and versions as `kg_edges`, resolved by people)
  + `evolution.py` (playbook candidates are experiments); `experiments.py` (SPEC-13: baseline vs candidate on one eval
  suite, review by the E0-E6 policy, canary, rollback; routes only applied by an administrator); `hypotheses.py`
  (ASEAO07 S07-R-03: statements under test with evidence; an experiment's comparison concludes its hypothesis; an agent
  never marks one supported without a recorded test result); `training.py`
  (capability gaps, learning plans: practice + hidden final eval suites run by the trainee in scratch repos,
  certification only from the harness; learning metrics); `selfevolve.py` (verified self-evolution of the platform's own code: improvement contracts, protected
  control plane, failure layers, a fixed benchmark from `benchmarks/` run on the base and candidate commits, approval,
  deploy via the safe restart, monitoring, rollback; docs/requirements/SELF-EVOLUTION.md); `performance.py` (defects, certification,
  metrics); `evaluation.py` (eval harness); `research.py` (crawl, knowledge, sources with monitoring rules: versions on change, intelligence events; shifts on the
  rotation with handover packages, `research_shifts`; research requests with priority, deadline, budget, recurrence and
  duplicate detection, run by `ReportThread`); `governance.py` (audit, notifications,
  command policy, emergency stops E0-E5, incidents, behaviour-rule alerts); `prefs.py` (DB settings); `gitsync.py` (push with each user's own GitHub token or SSH key, `data/git-keys/`); `files.py`; `supervisor.py` + `system.py`;
  `containers.py` (Docker and LXD VMs on the host: install, start, clean up; admin-only);
  `machines.py` (Machines view, from AXoR in /home/thomas/axor-3.0.zip: Linux/Windows KVM VMs as Docker
  containers with a noVNC console relayed over a WebSocket; admin-only; cleanup never prunes them);
  `a2a.py` (G-R-06: the A2A 1.0 agent card and JSON-RPC `SendMessage`/`GetTask`/`ListTasks`/`CancelTask` for
  issued bearer clients (`a2a_clients`, `a2a_tasks`); tasks become `origin="a2a"` goals that always wait for approval);
  `mcp.py` (Tools & MCP catalog, from AXoR's mcp_hub: stdio/HTTP servers, encrypted secrets, tool allow-list,
  per-agent grants, call log; bundled servers in `mcp_servers/`); `catalog.py` (skills, plugins, rules, commands,
  hooks; exports a project's `.claude/`); `agentdefs.py` (each agent's versioned definition file in `data/agents/`,
  its own git repo: the loop runs agents from it, learning and the study rotation rewrite it); `gm.py` (the General
  Manager as Claude Code: its instructions and the `python -m agent_company gm …` toolbox); `specs.py`
  (`.claude/specs/<slug>/` artifacts of every goal, the GM's `.claude/` configure step); `hooks.py` (TaskCreated,
  TaskCompleted, TeammateIdle → `data/logs/team-hooks.jsonl`, fail-open);
  `auth.py` (device sign-in, permissions); `transport.py` (HTTPS/HTTP via nginx, own CA, sealed cookie); `terminal.py` (Workspace PTY shells) + `workspace.py`
  (Workspace Claude Code panel: a tab per conversation (new ones on the far left), titled by Haiku; other live host sessions only in the sessions window (a tab while one is opened), session history with resume/stop/delete); `api.py`; `static/` (dashboard, plain JS, one file per area; `agents.js` (org chart, Tools & MCP, Skills & plugins, agent card), `network.js` (Agent network: overview counts, paged
  relationship graph from `network.graph`),
  `models.js`; `knowledge.js` (Knowledge: items, review queue, contradictions, graph explorer); `security.js` (Security: stops, incidents,
  refused commands, alerts); rail order shared by all users via `/api/ui/nav_layout` (Settings → Navigation); buttons with a known
  label become icons (`iconize` in app.js); `index.html` is served with content-hashed static URLs;
  `workspace.js` + `viewer.js` + `console.js` + `claude.js` are the Workspace home page, sharing
  `window.WSP`; `vendor/` holds xterm.js, pdf.js, mammoth and noVNC); `crew.py` (classic crew); `__main__.py` (CLI)
- `evals/` – benchmark suites (JSON; `kind` benchmark|practice|final, `skills`; a final suite's checks never reach agents)
- `benchmarks/` – fixed code benchmarks for improvement contracts (`<name>.json` manifest with metric weights and
  guards + `<name>.py` printing JSON metrics for the `agent_company` first on PYTHONPATH); protected
- `tests/` – pytest; every test gets its own temp DATA_DIR and runs offline
- `data/` – SQLite DB, workspaces, worktrees, uploads (git-ignored)
- `docs/threat-model.md` – threats → controls → tests; update it with any new executor, provider, integration or
  network path
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
- `uv run python -m agent_company goal "<goal>" --check "<cmd>" [--allow 'src/*'] [--protect-tests] [--permit dependencies|config|skip_tests]`
- `uv run python -m agent_company improve list|weaknesses|benchmark [name]|create|start|deploy|rollback|cancel <id>` – verified self-evolution
- `uv run python -m agent_company eval --suite smoke --label <name> [--repeats 3] [--model <name> --role <role>]` / `eval --compare a b`
- `uv run python -m agent_company learn gaps|raise|plans|plan <agent> <skill>|start <id>|cancel <id>|experiments|metrics`
- `uv run python -m agent_company requirements list|trace|import [--project <id>]` – requirements and traceability
- `uv run python -m agent_company stop [--level E0..E5 --scope X] [--lift|--status]` – emergency stop; writes the DB
  directly, works while the server is stuck
- `uv run python -m agent_company host status|install-docker|docker-start|cleanup [--yes]|install-lxd|launch <name> [--container]|stop|delete <name>`
- `uv run python -m agent_company gm status|agents|create-agent|add|mcp|configure|goal|study` – the GM's toolbox
- `uv run python -m agent_company "<request>"` – classic four-agent crew
