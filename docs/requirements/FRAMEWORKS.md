# Functions from the cloned agent frameworks (`/home/luca/update/*`)

Source: the thirteen repositories cloned into `/home/luca/update/` (read on 2026-11-06; nothing in them was run,
no code copied). Excluded on the owner's instruction: the folder `1`, `1.txt`, `other_claude.txt`.
IDs `FW-R-nn`; status words as in [README.md](README.md) (`done (symbol)`, `partial`, `open`, `skip (reason)`).
Every `R` ends with an acceptance condition that is a pytest test (offline, temp `DATA_DIR`).

## 1. What each repository provides

| Repository | Licence · last commit | What it is | Its distinctive functions |
| --- | --- | --- | --- |
| aider | Apache-2.0 · 2026-05-22 | Terminal pair programmer | **Repo map** (`aider/repomap.py`: tree-sitter tags of every file, a graph of which file references which definition, PageRank, the top-ranked definitions printed in a token budget); auto-commits with generated messages; lint and test after every edit with a fix loop; watch mode (`AI!` comments in the editor); images and URLs in the chat; voice |
| SWE-agent | MIT · 2026-07-16 (superseded by mini-swe-agent, reference only) | Research coding agent, one YAML config | **Agent-computer interface** (`tools/`: windowed file viewer, edits that are rejected when the linter fails, `filemap` = a file's skeleton with long bodies elided); **review on submit** (`review_on_submit_m`: the first `submit` shows the agent its own diff and the problem statement again before it is accepted); **history processors** (`sweagent/agent/history_processors.py`: keep the last N observations, elide older ones, cache control); trajectories and an inspector; batch mode for benchmarks |
| mini-swe-agent | MIT · 2026-09-03 | 100-line bash-only agent | Linear history (the trajectory *is* the prompt); each action an independent `subprocess.run` (trivially sandboxed); **limits per run**: `step_limit`, `cost_limit`, `wall_time_limit_seconds`, consecutive format errors (`agents/default.py`); trajectory saved as JSON |
| OpenHands (Agent Canvas) | MIT · 2026-11-04 | Self-hosted control centre for coding agents | Drives its own agent or **ACP agents** (Claude Code, Codex, Gemini CLI, OpenCode) over the Agent Client Protocol; **context condensers** (summarise old events when a conversation grows, `condensation-event.ts`, per-profile condenser settings); **automations**: prompts run on a **cron schedule or on an event** (GitHub, Slack, Linear webhooks); several agent backends (local, Docker, VM, cloud) switched from one UI |
| extensions (OpenHands) | MIT · 2026-11-03 | Public registry for OpenHands | **Skills** as `skills/<name>/SKILL.md` with front matter (name, description, `triggers:`) and progressive disclosure (76 skills: github, code-review, docker, kubernetes, …); plugins with hooks; an **automation catalog** (`automations/catalog/*/manifest.json`: GitHub PR reviewer, issue triage, stale-CI closer, …, each with `cron` or `event` triggers and a form); an integrations (MCP) catalog; `analysis_results.md` (already used: S08-R-03) |
| MetaGPT | MIT · 2026-01-21 | "Software company" of LLM roles | `Code = SOP(Team)`: product manager → architect → project manager → engineer with typed documents (PRD, competitive analysis, system design, API spec, task list); roles subscribe to message types (publish/subscribe pool); incremental development on an existing repo; AFlow (searching agent workflows) and SPO (prompt optimisation) |
| AutoGen | MIT (code) · 2026-10-28, **archived** (successor: Microsoft Agent Framework) | Multi-agent conversations | Team patterns (round robin, selector, swarm hand-off, Magentic-One); **termination conditions** (max messages, text mention, token usage, timeout, hand-off, external, function call; combinable with and/or); **Magentic-One progress ledger**: after each turn the orchestrator answers "is the request satisfied? are we in a loop? is progress being made?" and re-plans after `max_stalls` stalls |
| Microsoft Agent Framework | MIT · 2026-11-03 | Production agent SDK (.NET, Python) | Graph workflows (sequential, concurrent, hand-off, group chat, Magentic) with **checkpointing** and human-in-the-loop requests; agent/function/chat **middleware**; **OpenTelemetry** GenAI tracing; **declarative agents** in YAML; agent skills; DevUI; A2A, MCP and AG-UI hosting |
| LangGraph | MIT · 2026-11-03 | Low-level graph runtime for long-running agents | **Durable execution** (a checkpoint after every super-step, resume after a failure); **interrupts** (`interrupt_before/after`, `interrupt()` waits for a person, who may edit the state); **time travel** (`get_state_history`, resume or fork from any checkpoint); short- and long-term memory store; streaming modes |
| crewAI | MIT · 2026-11-03 | Role-based crews and event-driven Flows | Crews (sequential/hierarchical) and Flows (`@start`, `@listen`, `@router`); **task guardrails** (a function or an LLM checks a task's output; on failure the agent retries with the reason, `guardrail_max_retries`; a hallucination guardrail compares with a reference); memory (short, long, entity); knowledge sources; `crewai test` (N runs with scores); planning. Agent Company's classic crew already uses it |
| Dify | Apache-2.0 with conditions · 2026-11-03 | LLM app platform | Visual workflow canvas; RAG pipeline (ingestion of PDF/PPT/…, chunking, hybrid search, rerank); prompt IDE comparing models side by side; LLMOps logs with **annotations** (a person's corrected answer is replayed for similar questions); every app as an API (backend-as-a-service); plugin marketplace; agents with a sandbox |
| Flowise | Apache-2.0 (enterprise parts separate) · 2026-08-13 | Visual LLM flow builder | Chatflows and Agentflows on a canvas; human-input node; evaluations (datasets, evaluators); **metrics**: a Prometheus endpoint and OpenTelemetry export with Grafana dashboards (`metrics/`, `packages/server/src/metrics/Prometheus.ts`); queue mode with workers |
| Open Interpreter | Apache-2.0 (a fork of OpenAI Codex) · 2026-11-01 | Terminal coding agent | **Harness emulation** (`/harness`: claude-code, kimi-code, qwen-code, swe-agent, minimal, … prompt and tool layouts for cheaper models); ACP server mode (`interpreter acp`); speaks the Codex exec protocol, so the Codex SDK can drive it |

## 2. Functions compared with Agent Company

| Function (source) | Agent Company today | Decision |
| --- | --- | --- |
| Repository map in the agent's context (aider) | the task package has standards, knowledge and research documents, no map of the code | **FW-R-01** |
| Loop / stall detection inside a run (OpenHands stuck detector, Magentic-One ledger, mini-swe-agent limits) | across iterations: repeated failing check escalates (S04-R-06), budgets, behaviour rules; inside one executor run nothing stops an agent that repeats the same call | **FW-R-02** |
| Metrics for Prometheus / OpenTelemetry (Flowise, Agent Framework) | numbers on the Performance page and `/api/...`; nothing a monitoring system can scrape | **FW-R-03** |
| Scheduled automations (OpenHands automations, extensions catalog `cron` triggers) | research requests recur (S06-R-01); goals only from a person, a proposal or A2A | **FW-R-04** |
| Skill registry with `SKILL.md` folders (extensions, Agent Framework skills) | `catalog.py` skills written into `.claude/skills/`; no import of a skills folder | **FW-R-05** |
| Event triggers: a GitHub webhook starts a goal (OpenHands automations) | none | open (FW-R-06): a new inbound network path; needs the owner's decision and a threat-model entry |
| Fork a goal from an earlier iteration (LangGraph time travel) | best-iteration rollback (`loop._keep_best`), checkpoints per iteration | open (FW-R-07) |
| Context condensing for long conversations (OpenHands condensers, SWE-agent history processors) | Claude Code and Codex compact their own sessions (`/compact`); goal feedback is passed per iteration | partial (FW-R-08) |
| Task output guardrails with retry (crewAI) | the quality gate, review verdicts and acceptance conditions check every task; a read-only task's answer is checked against its acceptance (`842847d`) | done, adapted (`quality.py`, `checks.py`, `contracts.py`) |
| Termination conditions (AutoGen) | cost, token, time and agent budgets, iteration limit, emergency stops | done (`budgets.py`, `loop.py`, `governance.stop`) |
| Durable execution, checkpoints (LangGraph, Agent Framework) | checkpoint commits per writing task, resume after restart, idempotent merge/push | done (S04-R-05) |
| Human in the loop / interrupts (LangGraph, Agent Framework) | approval before merge, decisions that block, pause/resume, escalation to a person | done (`decisions.py`, `workflow.py`, `escalation.py`) |
| SOP roles with typed documents (MetaGPT) | specs artifacts per goal (requirements, spec, design, tasks, review), workflow templates per goal kind | done (`specs.py`, `strategies.py`) |
| Team patterns: sequential, concurrent, hand-off, group chat (AutoGen, Agent Framework) | DAG of task contracts with dependencies and parallelism, handovers, conflict review | done (`scheduler.py`, `loop._handover`, S04-R-03) |
| Middleware around every agent call (Agent Framework) | one tool gateway every run passes (`executors.gate`) | done (S14-R-06) |
| Declarative agents in YAML (Agent Framework) | each agent's versioned definition file in `data/agents/` | done (`agentdefs.py`) |
| Review of the own diff before submitting (SWE-agent `review_on_submit_m`) | independent QA review with a logic-verification checklist | done, stronger (S08-R-03) |
| Lint on edit (SWE-agent), lint and test after each change (aider) | syntax checks of changed files and the quality commands at the gate | done (`checks.syntax_errors`, `quality.py`) |
| Auto-commit with messages (aider) | checkpoint commit per writing task | done (S04-R-05) |
| Prompt optimisation, workflow search (MetaGPT SPO/AFlow), prompt IDE comparing models (Dify) | experiments baseline vs candidate on eval suites, workflow templates as experiments, `eval --compare` | done (`experiments.py`, `orchestration.py`) |
| Evaluations with datasets (Flowise, `crewai test`) | eval harness with suites and dimensions | done (`evaluation.py`) |
| Memory: short-, long-term, entity (crewAI, LangGraph) | blackboard per goal, knowledge items, knowledge graph | done (`blackboard.py`, `learning.py`, `kgraph.py`) |
| Trajectories and an inspector (SWE-agent, mini-swe-agent) | trace per goal with every run's tool calls | done (S04-R-12) |
| ACP agents (OpenHands, Open Interpreter) | executors drive the Claude Code, Codex and Copilot CLIs directly | skip for now: the three CLIs in use need no ACP; an ACP executor becomes worth it when Gemini CLI or OpenCode are wanted (open as FW-R-09) |
| Harness emulation (Open Interpreter) | one prompt layout per executor | skip: a research topic for cheap models, measurable later with experiments |
| Visual workflow canvas (Dify, Flowise) | workflow templates as versioned code | skip: templates are validated plans, not free graphs; a canvas would be a second source of truth |
| RAG pipeline with vector search and rerank (Dify) | FTS5 retrieval with learned weights per query class | skip: no vector store in the stack (README: Stack); retrieval is guarded by its eval |
| Annotation replies (Dify) | knowledge items from reviewed experience | skip: a chat-app feature; corrections already become knowledge items |
| Voice, IDE watch mode, copy/paste to web chat (aider) | the Workspace is the IDE | skip: interactive single-user features |

## 3. Requirements

### FW-R-01 Repository map in a task's package — done (`repomap.py`, `learning.repo_map_context`, setting `repo_map_chars`)

Source: aider `repomap.py`. A writing or reviewing agent gets a compact map of the repository: per file, its
top-level definitions (classes, functions, methods with their signature line), ranked by how often other files
refer to their names, cut to a character budget (setting `repo_map_chars`, default 4000; 0 = off). As in aider's graph, a function counts
as used where another file calls it, a class or type wherever its name appears, each file by the square root of its
uses, shared among the files that define the name; names and files the task mentions are boosted. Read from the
goal's base branch with `git ls-files` / `git show` (like the project standards), never from an agent's worktree;
Python is parsed with `ast`, JavaScript/TypeScript/Go/Rust/Java/Ruby/PHP by line patterns; files over 200 KB,
vendored, generated and minified files are skipped. The map is data, not instructions, and is cached per base commit.

Acceptance: in a temp repository with `a.py` defining `helper()` used by `b.py` and `c.py`, the map lists
`helper` before a definition nobody uses, stays within the budget, and a task package of that goal contains it;
with `repo_map_chars` = 0 the package has none.

### FW-R-02 Stop a run that repeats itself — open (planned: `executors.StuckWatch`)

Source: OpenHands stuck detection, AutoGen Magentic-One `max_stalls`, mini-swe-agent limits. While an executor
run streams its tool calls (Claude Code stream-json, Codex `--json`), the same call (tool name + input) repeated
`stuck_repeat_limit` times in a row (setting, default 5; 0 = off), or the same call failing with the same error
`stuck_error_limit` times (default 3), ends the run: the process is terminated, the run's error says
`stuck: <tool> repeated N times`, and the task fails with that reason so the retry policy re-tries it with the
reason in its feedback. The event is logged and counted in the operational metrics' tool-call failures.

Acceptance: a fake executor stream with five identical Bash calls ends with a `stuck:` error and the process is
terminated; four identical calls followed by a different one does not; three identical failing calls with the
same error do.

### FW-R-03 Metrics endpoint for Prometheus — open (planned: `metrics.py`, `GET /metrics`)

Source: Flowise `metrics/` (Prometheus, OpenTelemetry, Grafana). `GET /metrics` answers the Prometheus text format
(version 0.0.4): goals by status, tasks by status, runs and tool calls by executor, tokens and cost today and in
total, gate pass rate, open incidents by severity, the emergency-stop level, agents by health. Access: an
administrator's session, or `Authorization: Bearer <metrics_token>` (setting, stored hashed; empty = token access
off). No labels carry goal titles, prompts or paths. Audit: changing the token is audited.

Acceptance: without a session or token the endpoint answers 401; with the token it answers 200 with
`agent_company_goals{status="queued"}` and the other families, each with `# HELP` and `# TYPE`; values match
rows created in the test.

### FW-R-04 Scheduled goals — open (planned: `schedules.py`, Projects → Schedules, `gm schedule`)

Source: OpenHands automations and the extensions automation catalog (`cron` triggers). A project has schedules:
title, details, check commands, kind, an interval (`hourly`, `daily HH:MM`, `weekly <day> HH:MM`, UTC), enabled,
next run, last goal. The `ScheduleThread` creates the goal when it is due (origin `schedule`, the creator's rights;
approval as for any goal of that project). A schedule whose previous goal is still open skips that run and says so.
Administrators create, pause and delete schedules (audited); a paused or archived project runs none.

Acceptance: a daily schedule due in the past creates one goal with origin `schedule` and moves its next run by a
day; a second tick while that goal is queued creates none; a disabled schedule or a paused project creates none;
the interval parser rejects `every 5 seconds`.

### FW-R-05 Import skills from a `SKILL.md` registry — open (planned: `catalog.import_skills`, `gm import-skills`)

Source: OpenHands extensions `skills/<name>/SKILL.md`, Agent Framework skills. An administrator imports a folder
of skills (`<dir>/<name>/SKILL.md` with front matter `name`, `description`, optional `triggers`) into the catalog
as `skill` items with `source` = the folder, replacing earlier imports of the same name and source. Text that
tries to instruct agents beyond the skill (the `research.injection_flags` screen) is refused and listed. The folder
must be inside an allowed root (`data/`, `/home/luca/update`) and is only read.

Acceptance: a temp folder with two skills and one whose body says "ignore all previous instructions" imports two,
refuses one with its reason; importing again updates instead of duplicating; `export_claude` writes the two into
`.claude/skills/<name>/SKILL.md`.

### FW-R-06 Goals from GitHub events — open (needs the owner's decision)

Source: OpenHands automations (`event` triggers). A signed GitHub webhook (HMAC with a per-project secret) for
an issue labelled `agent` creates a goal that waits for approval. It opens an inbound network path, so it needs
the owner's decision and an entry in `docs/threat-model.md` before it is built.

### FW-R-07 Fork a goal from an earlier iteration — open

Source: LangGraph time travel. "Continue from iteration N": a new goal whose branch starts at that iteration's
checkpoint commit, with the original goal's plan and that iteration's feedback.

### FW-R-08 Condense long feedback — partial

Source: OpenHands condensers, SWE-agent `LastNObservations`. Claude Code and Codex compact their own sessions;
the loop's own prompts (planner, CrewAI executors) could keep the last N iterations' feedback whole and summarise
older ones. Open until a goal is measured to hit a context limit.

### FW-R-09 ACP executor — open

Source: Agent Client Protocol in OpenHands and Open Interpreter. An executor speaking ACP (JSON-RPC over stdio)
would add Gemini CLI, OpenCode or Open Interpreter as model kinds. Worth it once one of them is wanted.

## 4. Order

1. FW-R-01 repo map (better plans and edits on larger repositories).
2. FW-R-02 stuck runs (saves tokens on the rate-limited Claude plans).
3. FW-R-03 metrics endpoint.
4. FW-R-04 scheduled goals.
5. FW-R-05 skill import.
6. Then, on the owner's decision: FW-R-06, FW-R-07, FW-R-09.
