# Agent Company

An AI software-development company built from specialized agents (CrewAI models, Claude Code, Codex).
See ReadMe.mdx for roles, architecture, services and stack; docs/feature-gap.md for where each
feature came from.

## Layout

- `src/agent_company/` – one module per service:
  `registry.py` + `org.py` (agents, rooms; clones capped by `max_clones` per original; a project's goals are planned, configured and analysed by its own GM, `Project.manager_id` via `staff(prefer=…)`; configuration versions with rollback, health: degraded/inactive; a degraded
  agent's model is probed again, `recover_degraded`, S02-FR-18),
  `kpis.py` (SPEC-01 FR-08 department KPIs: formula from `METRICS`, target, interval, values with evidence in
  `kpi_values`, measured by the `KpiThread`; Performance → Department KPIs),
  `organization.py` (SPEC-01: room editing with parent, lead and switch, no cycles; a disabled room's agents are not
  staffed; position history `room:<key>:lead`/`project:<id>:gm`; organisation export JSON/CSV),
  `policies.py` (SPEC-01: delegated `approve_goals` per project with expiry, checked per call; versions of the
  governance settings with restore; a project's own rules may only be stricter, `effective` at the use sites),
  `recruitment.py` (SPEC-03: capability gaps from verified proficiency, recruitment records with job descriptions,
  approval policy `recruit_requires_approval` that pauses the goal, probation with reviewed tasks; with `qualify_recruits` a
  new writing recruit is first a `candidate` that runs an eval suite, then approved or rejected), `scheduler.py`, `router.py` (models, routes, fallback,
  provider catalogs, classification clearance per model; usage limits a run reports (`LIMIT_RE`: "hit your session
  limit · resets 9:50pm") mark the model `limited_until` its reset and the route's ordered `fallback` models take the
  work (`gm set-fallback`, Models → Routes); **Assign models** on Routes (`auto_routes`, `POST /api/routes/auto`)
  gives every role a model, retry model and fallbacks from the models usable now by `ROLE_TIERS` (lead: strongest;
  review: another family than the lead's; bulk/light: free or weakest; a limited model only as a last fallback); with every real model limited the simulator never stands in, staffing
  raises `registry.ModelLimitReached` and the goal resumes at the earliest reset; `run` sends a request through the
  chain for agents that call a model directly (the research room); a `claude_code` model's own sign-in `account`:
  `data/claude-accounts/<account>/.credentials.json`, its CLAUDE_CONFIG_DIR; a model's own API key encrypted in
  `api_key_enc` (only `key_hint` leaves the server); `model_type` API|CLI|Local; Test stores the provider's usage in
  `status`; every run's `Run.trace` (stamped by `Executor.run`: assigned `requested` model, the model run, model id,
  provider, base URL, `route` = why, `served` = the model the provider reported) and `trace_issues` (a model other
  than the assigned one without a configured fallback, the simulator standing in, a provider answering as another
  model; docs/verification/); `model_info`/`/api/models/info` is what the Models page shows per model, as the Claude Code panel shows a
  session (status, model, CLI version, account, usage windows: Claude plan via `workspace.claude_limits`, Codex plan
  windows from its session files, OpenRouter key limits, own daily/weekly token limits; `limitCards` in app.js); `copilot` kind (GitHub Copilot CLI, `enable_copilot`), `cli_status`/`install_cli`; `cli_login`: Sign in to GitHub on the Add model dialog runs `copilot login --device-code` in a pseudo-terminal, the page shows the code and polls `login_state`; without a keychain the CLI's "store the token as plain text?" question becomes state `consent` the person answers (`answer_login`), never the server; signed_in only from the CLI's own config (`copilot_config`: JSON after `//` lines) after exit 0; for Codex **Sign in with another ChatGPT account** runs `codex login --device-auth` with CODEX_HOME = `data/codex-accounts/<account>/` (the account entered, else `next_account` codex-2, …; never the host's `~/.codex`; a new folder without a sign-in is removed), the model is added with that `account` and its runs, Test, usage windows and Workspace panel use that CODEX_HOME (`executors.codex_env`); a CLI model keeps whose sign-in it was registered with in `ModelEntry.signin` (`cli_signin`, never a token); G-R-18 `ModelEntry.terms` (billing, context, tool calling, data
  terms, licence page) validated by `check_terms`, `terms_due` after 90 days without a person's check), `contracts.py` (task contracts with scope, risk, budget, deadline; output lines),
  `executors.py` (tracked subprocesses; a writing Claude Code run gets the `edit_check.py` PostToolUse hook (EL-R-05:
  each edited .py/.js file is parsed, a syntax error goes back to the agent; a read-only run with `shell` at G2+ and
  a worktree, also the GM's plan run, gets a `Grant.checks` list (CLAUDE_CHECK/COPILOT_CHECK): pytest, git status/diff
  (`git diff --output` denied), ls and the syntax-check wrapper `python3 -I edit_check.py <files>` (node --check per
  .js file, compile() per .py file); a raw `node` command stays refused); Codex runs without its own web tools (`CODEX_NO_WEB`: web search, apps, browser, computer use); `terminate` for stops; `gate`, the tool gateway every run passes: permissions and
  `Agent.autonomy` G0-G4 decide writes, Bash and MCP tools, the isolation level L0-L2 and every tool call (Claude Code
  stream-json, Codex `--json`) are recorded on the `Run`; FW-R-02 `StuckWatch` reads the stream while it runs and ends a run
  that repeats the same call `stuck_repeat_limit` times or fails the same way `stuck_error_limit` times), `dependencies.py` (S08-R-05: dependency changes per goal from git objects, `pip-audit`/`npm audit` at the gate's
  security stage with network in the sandbox, `dependency_audit` off|warn|block, merge record, CycloneDX SBOM of each merged commit in `data/sbom/`), `sandbox.py` (S14-R-07 L3: quality, acceptance and
  milestone commands and code benchmarks run agent-written code under bubblewrap: no network, the worktree writable
  with its `.git` read-only, `data/`/`.env` hidden, allow-listed env, systemd-scope limits; setting `sandbox`
  auto|required|off, a project only stricter, A2A goals required; a changed lock file is installed first with
  network into `data/sandbox/home`; a bwrap/systemd-run start-up failure (`INFRA_RE`, also in required mode)
  becomes `Outcome.infra`: the task is skipped with `INFRA_SKIP`, an `infrastructure` incident opens and the goal
  pauses until a person resumes it), `worktrees.py`,
  `quality.py` + `checks.py` (gate: contracts, review verdicts with logic verification, secrets, syntax, scope,
  change boundaries, baseline) + `teststrategy.py` (S09-R-02 risk-based test strategy per goal: mandatory stages
  for high-risk goals, staged command conditions the gate runs again), `usage.py`; `loop.py` (circular goal loop, budgets that pause, best-iteration
  rollback, handovers, checkpoint commits per writing task + worktree reconcile on resume, retry policy,
  re-plan, conflict review, escalation, behaviour rules, a writing task with a `BLOCKER:` line and no changed file
  is skipped (`BLOCKER_SKIP`) and the goal pauses for a person before any review (`paused_for="blocker"`, an eval
  goal fails instead; resume requeues the same iteration), EL-R-06 with setting `task_critic` a writing task's diff is
  reviewed in its worker by a critic of another model family (`_critic`, `avoid_provider` + `other_cli`) before its
  dependants start, a FAIL returns it to the same agent at most `CRITIC_RETRIES` (2) times, runs in `Task.critic`, idempotent merge/push, trace; goals of one project topic
  run in order, `lane_ahead`; G-R-08 overlapping `allowed_paths` of one repository wait, `paths_ahead`; G-R-09 mission digest `digest` in the result message, with a `report` link once finished; `run_goal` takes a goal with one conditional UPDATE (`_claim`), so a goal handed out twice runs once; each task records its sender `planned_by` and `created_at`) + `mission.py`
  (REQ-004 mission report of a finished goal: objective, plan, tasks done/failed/retried, checks and reviews, cost,
  time vs `Goal.deadline`, limitations, lessons, RCA recommendations; each task verified|partial|failed only from
  `Task.gate["acceptance"]` and gate review verdicts, never task output; `GET /api/goals/{id}/report[?format=md]`;
  `label_counts` cached per finished goal in `Goal.label_counts` (stored when the goal finishes, filled on first read
  for older goals, never for a goal active at write time, cleared by any status change; a failed store still returns
  the counts), read by the Simple overview and RCA;
  REQ-003 Simple mode: `User.ui_mode` simple|advanced, `/api/simple/missions` and `/api/simple/overview`); `workflow.py` (goal
  state machine, `goal_transitions`; task lifecycle stages `Task.stage` beside the execution status, S05-R-08; `TASK_TRANSITIONS`: forward along the lifecycle, waiting, reopened, never out of `completed`/`cancelled`, an illegal move refused and logged, raised with AGENT_COMPANY_STRICT_STAGES=1); `strategies.py` (S04-R-11 workflow templates per goal kind
  bugfix|feature|refactor|research|docs, versioned: roles, writes, checks, iteration limit, execution mode, review
  policy; the order or the planner picks, a plan must fit it) + `teams.py` (a goal's team: joined/added/released
  history, released agents not staffed for that goal) + `orchestration.py` (S04-R-13 self-evaluation per template
  version, recommended templates as `strategy` experiments under the §20.3 policy, admin rollout, canary rollback); `blackboard.py` (versioned shared goal context, agent messages; one to no existing role or of no known kind is logged, not stored);
  `network.py` (SPEC-12 relationship kinds with evidence: observed links from finished work,
  complementary specialists and a mentor candidate for new agents, administrator corrections, fair staffing
  inputs in `registry._rank`; `graph`/`overview` for the network view) + `mentoring.py` (a mentor per learning plan: reviews practice results, never certifies);
  `requirements.py` (ASEAO08 S08-R-01: project requirements with versions, links from
  tasks, checks and merges, `traceability`, conflicts as decisions, `import_docs` of `docs/requirements/`;
  S08-FR-03 story/use-case templates in `details`, `actors`);
  `decisions.py` (decision records; human ones block the merge; `adr.py` writes technical/critical ones as
  `docs/adr/NNNN-title.md` ADRs in the goal's branch (S08-R-02), superseding earlier ones; S07-R-01 reports: criteria, limitations, evidence
  classes, independent reviews that send a disputed decision to a person); `escalation.py` (S04-R-07 levels 1-5 and their
  recipients: agent, goal GM, room manager, executive GM, a person; delivered as blackboard requests);
  `budgets.py` (S04-R-09 goal budgets: cost, tokens, agents at once; child goals capped by the parent's remainder;
  daily room budgets over a room's subtree are `Room.daily_budget_usd`, checked with the agent quota in `registry.over_quota`); `ownerpolicy.py` (owner policy, both settings off by default: `auto_approve_proposals`
  approves pending follow-up proposals through `projects.decide(by_human=False, hold=True)`, while project, rule,
  restricted and guard- or budget-limited proposals stay pending and duplicates are rejected; `auto_merge` calls
  `loop.approve` at awaiting_approval; audited as `policy:auto-approve`/`policy:auto-merge`); `projects.py` (projects with a GitHub URL, filled from the repository's remote (`repo_remote`), stack, skills, rooms, budget, deadline, priority and a lifecycle `set_status`; command window
  with topics (conversation lanes, `/summary`, `DECISION:` lines → decision records), proposals (S10-R-01: a
  `PROPOSAL[product]` becomes a project only with its documented case, decided by a person), uploads); `management.py` (milestones, risks and blockers, insights, ranked action items, progress and goal times;
  blockers and generated items are reconciled from goal state by `sync`; a new project GM gets a handover package); `kgraph.py` (SPEC-11 knowledge graph: `kg_edges` written as side effects, the paged neighbourhood API);
  `schedules.py` (FW-R-04: a project's goals ordered `hourly`/`daily HH:MM`/`weekly <day> HH:MM` UTC by the
  `ScheduleThread`, origin `schedule`; skipped while the last one is open or the project is not active; Plan → Schedules);
  `metrics.py` (FW-R-03: `GET /metrics` in the Prometheus format for an admin session or the hashed scrape token,
  Settings → Metrics for Prometheus);
  `repomap.py` (FW-R-01, after aider: the base branch's definitions ranked by use in other files, within
  `repo_map_chars`, in the planner's prompt and each task's package via `learning.repo_map_context`);
  `learning.py` (knowledge items: provenance, experience reports, screening, scoped ranked retrieval over an FTS5
  index with `retrieval_w_*` weights guarded by `evals/retrieval.json` recall@5; SE-R-21 ranking procedure per query
  class (`query_class`, `retrieval_strategies` setting) learned on `evals/retrieval-classes.json` by `learn_strategies`; a task's package adds the project's
  standards from the base branch and matching research documents; contradictions and versions as `kg_edges`,
  resolved by people; REQ-005 `import_knowledge`: an admin imports a text/Markdown file, a folder inside `data_dir`,
  a chat export or experience reports as `outcome="imported"` items under the root GM, screened and `draft` until a
  person approves them, never in a playbook or definition file)
  + `evolution.py` (playbook candidates are experiments); `experiments.py` (SPEC-13: baseline vs candidate on one eval
  suite, review by the E0-E6 policy, canary, rollback; routes only applied by an administrator; G-R-11 a candidate that breaks a task the
  baseline solved is never better (`regressions`); S10-R-02 the platform
  commit in `dataset` and a `budget_usd` that stops route/template experiments; `retrieval` target: ranking weights on
  the retrieval evals + `benchmarks/retrieval.py`, applied and rolled back by an administrator; EL-R-07 archive: each
  experiment's `parent_id` (same target and scope, `lineage`, `seed`) and append-only `eval_log`, `pick_parent`
  best|diverse from the finished ones, `compete`: 2-3 route/strategy candidates on one suite against one shared
  baseline, only the `select_best` winner (no regressions) goes to review, the others end rejected); `hypotheses.py`
  (ASEAO07 S07-R-03: statements under test with evidence; an experiment's comparison concludes its hypothesis; an agent
  never marks one supported without a recorded test result; a concluded one becomes a knowledge item); `training.py`
  (capability gaps, learning plans: practice + hidden final eval suites run by the trainee in scratch repos,
  certification only from the harness; learning metrics) + `exercises.py` (S13-R-02 exercises written by the
  instructor on an administrator's request, kept only when each check fails on the seed and passes with the reference
  solution in the sandbox; `data/evals/`, a generated final set needs approval); `selfevolve.py` (verified self-evolution of the platform's own code: improvement contracts, protected
  control plane, failure layers, a fixed benchmark from `benchmarks/` run on the base and candidate commits, approval,
  deploy via the safe restart, monitoring, rollback; docs/requirements/SELF-EVOLUTION.md); `performance.py` (defects with priority and a lifecycle
  the loop drives, S09-R-03; certification, metrics, estimate accuracy, G-R-02 operational metrics `operations` (G-R-16 p95 times, `human_override_rate`, `tool_call_failure_rate`; G-R-17 `failure_layers`); G-R-04 `enablement_gate` and `room_gate`: a gated role is enabled only after its room's own suite ran as a room trial, `Goal.trial.room`, whose disabled agents `registry.staff` takes for those eval goals only); `rca.py` (S07-R-02 root-cause analysis of failed goals and repeated failing checks:
  evidence pack, causes that must cite it and name their layer from `selfevolve.LAYERS` (G-R-17), actions as proposals, follow-up;
  EL-R-04 `propose_rules`: a cause (layer, check) in `rule_proposal_min_goals` failed goals becomes one pending `kind="rule"`
  proposal citing them, drafted by the instructor (template fallback), never duplicated; a person's approval makes it a
  catalog rule, `projects._to_rule`); `evaluation.py` (eval harness;
  EL-R-08 `mutation_test` measures per-check mutation detection in disposable sandboxed worktrees; S09-R-05 dimensions with sample sizes per run and summary, no combined score; `adversarial` suites run like final ones; G-R-14 scheduled benchmark review `scheduled_review`/`BenchmarkThread`, settings `benchmark_schedule|suite|budget_usd`, off until a budget is set; G-R-13 a `research` suite, `evals/research.json`, runs through `research_eval.py`: the research room writes a report per task as `run_report` does, graded without a model judge (`check_claims` citation share, required citations and facts, forbidden claims, sections), one EvalRun per run; its reference documents stay in memory, never stored, and an eval report is never reviewed or transferred, ADR 0003); `research_agent.py` (the research lead's tool loop: its model plans JSON tool calls, validated against `TOOLS`
  (search_knowledge, search_papers, fetch_url) and run by the platform through `research.collect`, results fed back
  for `research_tool_rounds` rounds; the documents join the report's material, the steps are `tool_steps`);
  `netpin.py` (shared public-address resolution and pinned connects for httpx and git),
  `research.py` (crawl, knowledge, RSS/Atom feeds and each document's publication date and licence (G-R-10), PDFs parsed by `pdftext.py` in the L3 sandbox (G-R-12), sources with monitoring rules: versions on change, intelligence events; shifts on the
  rotation with handover packages, `research_shifts`; research requests with priority, deadline, budget, recurrence and
  duplicate detection, run by `ReportThread`; S06-R-03/R-04 claims checked against stored documents and an independent review (another agent, another model family when usable, else a person): only approved reports become `report://` documents, blackboard entries, `research` knowledge items and `research` proposals); `governance.py` (audit, notifications,
  command policy, emergency stops E0-E5, incidents, behaviour-rule alerts); `prefs.py` (DB settings); `gitsync.py` (push with each user's own GitHub token or SSH key, `data/git-keys/`); `files.py`; `supervisor.py` + `system.py`;
  `containers.py` (Docker and LXD VMs on the host: install, start, clean up; admin-only);
  `machines.py` (Machines view, from AXoR in /home/thomas/axor-3.0.zip: Linux/Windows KVM VMs as Docker
  containers with a noVNC console relayed over a WebSocket; admin-only; cleanup never prunes them);
  `a2a.py` (G-R-06: the A2A 1.0 agent card and JSON-RPC `SendMessage`/`GetTask`/`ListTasks`/`CancelTask` for
  issued bearer clients (`a2a_clients`, `a2a_tasks`); tasks become `origin="a2a"` goals that always wait for approval);
  `marketplace.py` (REQ-002 agent marketplace: a repository URL or uploaded package fetched as untrusted data into
  `data/marketplace/<id>/src` (checked URL, hardened shallow clone pinned to the checked address (`http.curloptResolve`,
  git >= 2.37) and size-bounded while it runs, archive members checked first, tar streamed), converted to a
  manifest as text, screened, a permission ceiling no package or instruction widens, a `source="marketplace"`
  recruitment candidate qualified in the L3 sandbox (`unevaluated` without a suite: approved only with
  `accept_unevaluated`), approved only by an administrator, `## Provenance` in its
  definition; `/api/marketplace` admin-only and audited, CLI `marketplace`, Agents → Marketplace tab);
  `mcp.py` (Tools & MCP catalog, from AXoR's mcp_hub: stdio/HTTP servers, encrypted secrets, tool allow-list,
  per-agent grants, call log; G-R-15 a tool whose definition `digest` changed loses its allow and is marked `changed`,
  `McpThread` re-tests every `mcp_revalidate_hours`; bundled servers in `mcp_servers/`); `catalog.py` (skills, plugins, rules, commands,
  hooks; exports a project's `.claude/`; FW-R-05 `import_skills` of a `<name>/SKILL.md` registry, screened, disabled until chosen); `agentdefs.py` (each agent's versioned definition file in `data/agents/`,
  its own git repo: the loop runs agents from it, learning and the study rotation rewrite it; G-R-19 a study rewrite is a
  pending `definition_candidates` row an administrator applies or discards after seeing the diff, `apply_candidate`;
  a study visit and a preview write nothing); `gm.py` (the General
  Manager as Claude Code: its instructions and the `python -m agent_company gm …` toolbox); `specs.py`
  (`.claude/specs/<slug>/` artifacts of every goal, the GM's `.claude/` configure step; K54 `check_plan` rejects
  acceptance paths outside the goal's spec folder and conditions on loop-written files, the GM re-plans); `hooks.py` (TaskCreated,
  TaskCompleted, TeammateIdle → `data/logs/team-hooks.jsonl`, fail-open);
  `auth.py` (device sign-in, permissions); `transport.py` (HTTPS/HTTP via nginx, own CA, sealed cookie); `terminal.py` (Workspace PTY shells) + `workspace.py`
  (Workspace Claude Code panel: a tab per conversation of this browser session (new ones on the far right; earlier ones
  only in the sessions window, opened at their end), titled by Haiku; a model menu (the CLI's aliases + the Models page's `claude_code`/`codex`
  models, `router_model`: their id, sign-in and base URL; the transcript follows a conversation to another sign-in and
  back; `/status`, `/tasks`, `/agents` follow a chosen model's own sign-in: `model_home`) and effort, per prompt, also in a project's GM panel (default: the GM's model); each turn's prompt is a one-line
  bar pinned to the top of the log (trimming the event window never drops it), tool calls and thinking fold into one
  "N steps" line, what the agent says and the result always show; other live host sessions only in the sessions window (a tab while one is
  opened), session history with resume/stop/delete); the Workspace explorer opens more file windows ("+", stacked or
  side by side) that share one code path, clipboard and drag and drop, and keeps its lists and open files while the server is
  unreachable, without a notice; the terminal keeps its screen, reconnects quietly from the last byte it has (`?after=`),
  and a shell lost to a restart is replaced in place on the next input; `api.py`; `static/` (dashboard, plain JS, one file per area; `agents.js` (org chart, Agent network, Tools & MCP, Skills & plugins, agent card; agent card Runs tab: paged runs, run detail with task output and tool calls (REQ-006)), `network.js` (the
  Agents page's Agent network tab: overview counts, paged relationship graph from `network.graph`), `companymap.js` (the
  Company map: the engine line of rooms from /api/rooms, each room's active agents with their model and route
  fallbacks as badges, live goals with their lifecycle step; read-only),
  `models.js`; `knowledge.js` (Knowledge: items, review queue, contradictions, graph explorer); `security.js` (Security: stops, incidents,
  refused commands, alerts); rail order shared by all users via `/api/ui/nav_layout` (Settings → Navigation); Notifications,
  Audit log, Settings and Users are top-bar buttons (`TOPBAR` in app.js), never on the rail; the tab icon (`setFavicon`) shows
  the stop status, running goals, unread notifications, waiting approvals and audit records not yet seen (`audit_last`
  in `/api/overview`); buttons with a known label become icons (`iconize` in app.js); no File Manager page (the
  Workspace explorer is it; `files.js` keeps the folder picker and preview); `index.html` is served with content-hashed static URLs;
  `workspace.js` + `viewer.js` + `console.js` + `claude.js` are the Workspace home page, sharing
  `window.WSP`; `vendor/` holds xterm.js, pdf.js, mammoth and noVNC); `crew.py` (classic crew); `__main__.py` (CLI)
- `evals/` – benchmark suites (JSON; `kind` benchmark|practice|final|adversarial|room|research, `skills`; `room-<key>.json` is a gated room's suite (G-R-04); a final suite's checks never reach agents;
  `research.json` is the research room's source-graded benchmark: reference documents under `eval-research://` and tasks with `expect`, G-R-13);
  `retrieval.json` is the knowledge-retrieval eval (items + cases, no tasks), `retrieval-classes.json` the same with
  cases labelled by query class (SE-R-21)
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
  `data/worktrees/` (`worktrees.ensure_sandboxed`); commands pass `governance.check_command` and run through
  `contracts.run_command` (the L3 sandbox, never `subprocess` with the server's environment);
  merging needs approval (exception, the owner's order of 2026-11-08: with the owner-policy setting `auto_merge` on,
  `loop.approve` runs as 'auto-merge (owner policy)' when a goal reaches awaiting_approval, except a2a/eval goals,
  and every merge-time block still applies); pushing only on `/sync` or AUTO_PUSH.
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
- `uv run python -m agent_company goal "<goal>" --check "<cmd>" [--allow 'src/*'] [--protect-tests] [--permit dependencies|config|skip_tests] [--kind feature|bugfix|refactor|research|docs]`
- `uv run python -m agent_company improve list|weaknesses|benchmark [name]|create|start|deploy|rollback|cancel <id>` – verified self-evolution
- `uv run python -m agent_company eval --suite smoke --label <name> [--repeats 3] [--model <name> --role <role>]` / `eval --compare a b` / `eval --review` (the scheduled benchmark review now)
- `uv run python -m agent_company learn gaps|raise|plans|plan <agent> <skill>|start <id>|cancel <id>|experiments|metrics|retrieval|generate <skill> [practice|final] [count]|suites|approve <suite>|import <path in data/> [auto|text|markdown|chat|reports|folder]`
- `uv run python -m agent_company requirements list|trace|import [--project <id>]` – requirements and traceability
- `uv run python -m agent_company strategies evaluate [kind]|recommend <kind>` – self-evaluation of the workflow templates
- `uv run python -m agent_company sbom [repo] [--rev HEAD] [--out file]` – CycloneDX SBOM of a commit
- `uv run python -m agent_company stop [--level E0..E5 --scope X] [--lift|--status]` – emergency stop; writes the DB
  directly, works while the server is stuck
- `uv run python -m agent_company host status|install-docker|docker-start|cleanup [--yes]|install-lxd|launch <name> [--container]|stop|delete <name>`
- `uv run python -m agent_company gm status|agents|create-agent|add|mcp|configure|goal|team|study|schedule|schedules` – the GM's toolbox
- `uv run python -m agent_company "<request>"` – classic four-agent crew
