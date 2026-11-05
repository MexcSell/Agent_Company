# Feature gap: what the reference projects have that Agent Company lacked

Sources: `/home/luca/AgentForge` (Node control room), `/home/thomas/bit` (SWE agent,
eval harness, local model), `/home/luca/update/` (ASEAO/SPEC documents, guides).
Status: `done` = implemented in this repository, `skip` = deliberately not taken (reason given),
`partial` = part met (the rest named in the linked requirement), `open` = worth doing later; IDs point to
`docs/requirements/`. Statuses checked against the code on 2026-11-05.

## From AgentForge

| Feature | Status |
| --- | --- |
| Projects with repo path and GitHub URL; protected platform project; multi-select delete, edit | done (`projects.py`, Projects view) |
| Command window: conversation with the project's GM; slash commands (/standup, /status, /stop, /sync, /help); copy button; identical messages collapsed; running-agents rail | done (`projects.command`, `static/projects.js`) |
| Image/file attachments in the command input (attach or paste) | done (`/api/uploads`, copied into the worktree's `.attachments/`) |
| Agent proposals, approved by the user or by superiors, become new goals (with loop guard) | done (`projects.extract_proposals`, `decide`, `settle_proposals`) |
| Notifications (badge count, read state, admin notices) and audit log | done (`governance.py`, Notifications and Audit views) |
| Evolution: versioned playbooks, candidate experiment over an evaluation window, promote or roll back; restore any version | done (`evolution.py`, Evolution view) |
| Clone merge and retirement after a mission; consolidate a role | done (`evolution.merge_clone`, `retire_goal_clones`, `consolidate`) |
| Git sync: push approved work (source-only .gitignore, files over 50 MB refused); per-user Git identity (commit author) and branch; AUTO_PUSH | done (`gitsync.py`, `/sync`, Users page → Git) |
| File manager: panes, Ctrl/Shift multi-select, drag to move, copy/cut/paste, rename, delete, upload, viewer with syntax colours and Markdown preview, editor; folder picker for project creation | done (`files.py`, `static/files.js`) |
| System metrics and status; safe self-restart supervisor (syntax check, candidate boot, health check, wait for idle, swap) | done (`system.py`, `supervisor.py`, `supervise` CLI, System view) |
| Settings stored in the database, editable by administrators | done (`prefs.py`, Settings view) |
| Model test button | done (`POST /api/models/{id}/test`, Models page) |
| OpenAI-compatible provider catalogs (Groq, Mistral, Cohere, SambaNova) | done (`router.provider_catalog`, Models page → Model catalogs) |
| Claude Code session resume per agent (same goal, within 60 minutes) | done (`--resume`, `Task.session_id`) |
| Stand-up meeting | done (`/standup`) |
| Manual agent create/clone/retire (delete keeps history: agents are retired, not erased) | done (`registry.create_agent`, `clone_agent`, `set_lifecycle`) |
| Dynamic favicon with status | done (`static/app.js`) |
| Live events (push instead of polling) | done (`/api/stream` Server-Sent Events; polling remains as fallback) |
| Per-user permissions (projects, run, create projects, manage agents, manage models) | done (`auth.DEFAULT_PERMISSIONS`, Users page → Permissions) |
| Terminal (node-pty, full host shell) | skip: gives agents' users a root shell; the file manager and quality commands cover the need |
| Bittensor watcher | skip: domain-specific, burns TAO; already lives in AgentForge |
| Project preview proxy | skip: it runs arbitrary dev servers on the host and needs WebSocket proxying and URL rewriting to be useful; a half version would be worse than none, and AgentForge already has it |
| Topics: separate conversation lanes inside a project (AgentForge website spec) | done (`projects.topics`, `loop.lane_ahead`; Projects → Orders & reports tabs) → S05-R-07, G-R-07 |
| 21 screen-format presets (scale, density, corners, rail left/right/bottom, labels, width, floating cards, Projects panel order/sizes/hidden/orientation) | done (`static/layouts.js`, top bar → Screen format) |
| Resizable areas: drag the gutters between the Projects panels; widths remembered per format | done (`static/layouts.js`) |

## From /home/thomas/bit

| Feature | Status |
| --- | --- |
| Baseline checks: run the checks on the untouched code first, so pre-existing failures are not blamed on agents | done (`quality.baseline`, `ALLOW_PREEXISTING_FAILURES`) |
| Snapshot / keep the best attempt: roll back to the best iteration when a later one is worse | done (`loop._keep_best`, goal history) |
| Scope rules: changes limited to allowed paths; existing tests protected | done (`checks.scope_violations`, `--allow`, `--protect-tests`) |
| Syntax checks of changed files (Python, JSON, TOML, shell; JS/Ruby/PHP/Go when installed) | done (`checks.syntax_errors`) |
| Extract check commands named in the task statement | done (`checks.extract_check_commands`, administrators' goals only) |
| Model fallback chain on API errors; escalation to a stronger model after failures | done (`router.resolve_chain`, retry model per route) |
| Cost and time budgets per goal, with a wrap-up warning at 60% | done (`goal_budget_usd`, `goal_time_budget`; per goal also tokens and agents at once, child goals capped by the parent, `budgets.py`, S04-R-09) |
| Reviewer from a different model family than the driver | done (`avoid_provider` for QA/security tasks) |
| Eval harness: run benchmark goals N times, pass rate / cost / time / end reason, compare labels | done (`evaluation.py`, `evals/smoke.json`, `eval` CLI, Performance view) |
| Local OpenAI-compatible model server (llama-server, LM Studio, vLLM) via base URL | done (model `base_url`, `api_key_env`) |
| Docker on the host: detect, install (apt `docker.io`), start/stop/restart the daemon | done (`containers.py`, System view → Docker, `host` CLI) |
| `scripts/docker-cleanup.sh`: dry-run report, then prune stopped containers, dangling (or all unused) images, unused networks, build cache, optionally anonymous volumes, older than an age | done (`containers.cleanup`, System view → Clean up…, `host cleanup [--yes]`) |
| Virtual machines: install/initialise LXD, launch Ubuntu (or other) VMs or system containers on KVM, start/stop/delete | done (`containers.install_lxd`, `launch`, `instance_action`; System view → Virtual machines) |
| Background image builds (`phase-6/tools/build_bg.sh`: nice/ionice, pinned cores, one at a time) | skip: Agent Company builds no images; host jobs already run one at a time |
| Harbor task Dockerfiles (`eval/make_tb_tasks.py`) | skip: eval-specific; our eval harness runs goals in worktrees |

From AXoR 3.0 (`/home/thomas/axor-3.0.zip`, the workbench installed on `/home/thomas/bit`; `bit` itself has no
Machines code):

| Feature | Status |
| --- | --- |
| Machines: Linux (qemux/qemu: Ubuntu, Debian, Fedora, Rocky, Arch, … or any ISO/disk URL) and Windows (dockurr/windows: 10, 11, Server, unattended setup) VMs on KVM; list with status and usage, New/Edit form, start/stop/restart/delete, Overview, Logs, in-browser console (noVNC over a WebSocket relay to QEMU's VNC) | done (`machines.py`, `static/machines.js`, Machines view). AXoR runs each machine as a Kubernetes Deployment with a KVM device plugin; here each is one Docker container with `/dev/kvm` and a named-volume disk, settings in the `machines` table. Its network policy (no access to the host or private ranges) is not reproduced |
| Admin sudo step-up before changes and the console | skip: Agent Company has no sudo session; every Machines endpoint is admin-only and audited |

## From /home/luca/update (ASEAO specs, SPEC11-14, guides)

| Feature | Spec | Status |
| --- | --- | --- |
| Tamper-evident (hash-chained) audit log | SPEC01 §10.5, SPEC14 | done (`governance.audit`, `verify_audit`) |
| Room editing (hierarchy without cycles, lead, switch), position history, organisation export (JSON/CSV) | SPEC01 FR-02, FR-03, FR-13 | done (`organization.py`, Agents → Rooms, S01-R-01, R-02, R-04) |
| Delegation of approval rights; versioned organisational policies, stricter project rules | SPEC01 FR-09, FR-14 | done (`policies.py`, Users → Delegate, Settings → Policy versions, S01-R-03, S01-R-05) |
| Dangerous-operation policy for commands; approval for destructive actions | SPEC14 §8.4 | done (`governance.check_command`, used by every quality command) |
| Secret scanning of changes in the quality gate | SPEC14, subscription guide §7 | done (`checks.scan_secrets`) |
| Prompt-injection flags on collected material; flagged pages kept out of prompts; material wrapped as untrusted data | SPEC14 §9, SPEC11 §10.4 | done (`research.injection_flags`) |
| Knowledge provenance and source kinds (no single trust score, per ASEAO06 §9.2) | ASEAO06 §9, SPEC11 §6 | done (`research.classify_source`) |
| Research requests producing a cited research report | ASEAO06 §5-6 | done (`research.request_report`, `run_report`) |
| Team compatibility: prefer agents that worked well together | SPEC12 §7 | done (`registry._rank` team utility; weight only from verified outcomes, once per goal, S12-D-01) |
| Task handover package when work moves to another agent | SPEC12 §10 | done (`loop._handover`, `handovers` table) |
| Capability certification from evaluations (L0-L5, expiry) | SPEC13 §9 | done (`performance.certification`) |
| Defect records from failed checks, closed when fixed, reopened on regression | ASEAO09 §11 | done (`performance.track_defects`) |
| Defect priority and lifecycle (assigned, fix submitted, retested; duplicate, not reproducible, deferred, accepted risk), fixes verified by a fresh run of the check | ASEAO09 §11-12 | done (`performance`, S09-R-03) |
| Performance intelligence: agent, room and company metrics | ASEAO07 §15 | done (`performance.report`, Performance view) |
| Agent lifecycle states (active, paused, retired) | SPEC02 §6 | done (`Agent.lifecycle`) |
| Duplicate-agent prevention: role synonyms, reuse of an agent covering the skills, near-duplicate list | SPEC03 §6, §11.2 | done (S03-R-02) |
| Capability-gap calculation, recruitment records and job descriptions | SPEC03 §5, §7, §8 | done (`recruitment.py`, S03-R-01, S03-R-03) |
| Recruitment approval policy, probation and recruitment effectiveness | SPEC03 §9-10, §16.3 | done (S03-R-05, S03-R-04 with the `qualify_recruits` run) |
| Blackboard: shared context per goal, with versioning; agent-to-agent messages on it | SPEC04 §9-10 | done (`blackboard.py`, S04-R-01, S04-R-10) |
| Decision records (approval blocks the merge) | SPEC04 §12.2 | done (`decisions.py`, S04-R-02) |
| Conflict resolution between agents' results | SPEC04 §12.1 | done (`loop._review_conflicts`, S04-R-03) |
| Goal workflow states with a transition log; pause/resume | SPEC04 §13.1 | done (`workflow.py`, S04-R-04) |
| Durable recovery: resume interrupted goals after a restart; merge/push never repeated | SPEC04 §13.2 | done (`Goal.checkpoint`, `operations`, checkpoint commits and worktree reconcile, S04-R-05) |
| Retry policy and escalation levels for failed tasks | SPEC04 §14 | done (retry by failure type, budget pause, re-plan, repeated-check escalation, S04-R-06; levels 1-5 routed to agent, goal GM, room manager, executive GM, a person, `escalation.py`, S04-R-07) |
| Task contract fields: scope, risk, test plan, budget, deadline, requirement keys; findings and next actions | Designing guide §3 | done (`contracts.TaskContract`, G-R-01) |
| Workflow templates per goal kind (bugfix, feature, refactor, research, docs) with versions; strategy picked by the order or the planner; team resizing with history | SPEC04 FR-24..26, §8.4, §20.4 | done (`strategies.py`, `teams.py`, S04-R-11) |
| Execution trace per goal | SPEC04 FR-30, SPEC14 | done (`/api/goals/{id}/trace` with each run's tool calls) → S04-R-12 |
| Goal-chain depth limit for proposals | SPEC04 §9.4 | done (`max_goal_depth`, message budget, duplicates dropped, unanswered requests escalated, S04-R-08) |
| Project registration fields (rooms, skills, stack, budget, deadline, priority) and lifecycle (pause, resume, archive, cancel, complete) | ASEAO05 FR-05..09 | done (`projects.set_status`, project budget caps its goals, priority queue, S05-R-06) |
| Milestones, blockers and risks, insights, upcoming action items per project | ASEAO05 §7-10 | done (`management.py`, Projects → Plan, S05-R-01..R-04; time per goal with the planner's estimates and weighted progress, S05-R-05) |
| Replacing a project's GM hands over open goals, blockers and action items; one agent needed by two projects' goals | ASEAO05 §4.3, §13 | done (`management.gm_handover`, `registry._other_projects`, S05-R-09) |
| Task status chain created → planned → assigned → in progress → review → approved → completed, with blocked, failed, cancelled, reopened, skipped; blockers from task output | `.rtf` SPEC-05 | done, adapted (`Task.stage` beside the execution status, `BLOCKER:` lines become project blockers, S05-R-08) |
| Continuous monitoring of registered sources (beyond the rotation) | ASEAO06 §13 | done (per-source intervals, page versions, intelligence events; S06-R-02) |
| Research requests with keywords, source preferences, priority, budget, deadline, recurrence, project/goal link and duplicate detection | ASEAO06 FR-03..08 | done (`research.submit`, `ReportThread`, S06-R-01) |
| Research-to-innovation pipeline: findings become proposals | ASEAO06 §15 | open → S06-R-06 |
| Hypotheses, root-cause analysis, predictions, decision center | ASEAO07 §9-13 | hypotheses done (`hypotheses.py`, tested by experiments, S07-R-03); decision reports with labelled evidence, limitations and independent review done (`decisions.report`, S07-R-01); estimate accuracy per role (`performance.calibration`, S07-R-04); root-cause analysis of failed goals and repeated failing checks with evidence-backed causes, actions as proposals and a follow-up (`rca.py`, S07-R-02) |
| Architecture Decision Records | ASEAO08 §7.2 | done, adapted (`adr.py`: the plan's technical and critical decisions become `docs/adr/NNNN-title.md` in the goal's branch, superseding earlier ones, S08-R-02) |
| Requirements traceability (requirement → task → test) | ASEAO08 §5.3, ASEAO09 §5.1 | done (`requirements.py`: versions, conflicts as decisions, links from tasks, checks and merges, S08-R-01; check commands and acceptance commands naming keys, untested requirements, S09-R-06) |
| Code-review checklist with a logic-verification step (trace data flow; no approval for code that only looks clean) and de-duplicated comments | ASEAO08 §13, `extensions/analysis_results.md` | done (`quality.REVIEW_CHECKLIST`, `review_verdict`, `merge_findings`, S08-R-03) |
| Change boundaries: no skipped tests, removed assertions, new dependencies or CI/.env edits without a permit | ASEAO08 §12.2 | done (`checks.boundary_violations`, S08-R-04) |
| Quality gates as named stages; an open critical defect blocks the merge | ASEAO09 §13 | done (`quality.stages`, S09-R-01) |
| Risk-based test strategy per goal: mandatory integration/security stages for high-risk goals, security review of sensitive areas, files with earlier failures | ASEAO09 §6 | done (`teststrategy.py`, S09-R-02) |
| Test-strategy selection by test category and risk | ASEAO09 §6-7 | done (`teststrategy.py`, S09-R-02) |
| Independent verification: the gate re-runs every command and records commit, exit code, duration, output tail and toolchain versions | ASEAO09 §12 | done (`quality.evaluate`, `quality.environment`, goal history `verification`, S09-R-04) |
| Agent evaluation by separate dimensions (completion, contract compliance, tool use, reliability, security behaviour, efficiency) with sample sizes; adversarial suite (prompt injection, a test tempting to be disabled, a failing tool) | ASEAO09 §8 | done (`evaluation.dimensions`, `evals/adversarial.json`, S09-R-05) |
| Innovation experiments, portfolio and technology radar | ASEAO10 §10-14, §21 | open → S10-R-02, S10-G-01..G-06 |
| Knowledge graph of agents, projects, technologies and lessons | SPEC11 §7, 1.txt (Neo4j) | done (`kgraph.py`: edges as side effects, paged graph API, Knowledge → graph explorer; S11-R-07) |
| Knowledge items with provenance; task-relevant, project-scoped retrieval | SPEC11 §3, §8 | done (`learning.retrieve`: FTS5, weights as settings guarded by the retrieval eval, standards and research documents in a task's package) → S11-R-01, S11-R-03, S11-R-08 |
| Contradiction handling between stored lessons | SPEC11 §6.3 | done (`CONTRADICTS` edges, people resolve with a rationale, S11-R-04) |
| Knowledge versions (supersede) and deletion with their links | SPEC11 §10.3 | done (`learning.supersede`, `delete`, S11-R-06) |
| Structured experience reports after every task | SPEC11 §9.1 | done (`Lesson.report`, `loop._experience_facts`, `learning.clean_report`, S11-R-02) |
| Dashboard: Knowledge view (search, review queue, contradictions side by side), Security view (stops, incidents, refused commands per agent, alerts), goal trace, stop level for every user | SPEC-15 | done (`static/knowledge.js`, `static/security.js`, S15-R-01..R-04) |
| Memory-poisoning protection for learned lessons | SPEC11 §10.4 | done (`learning.screen`, people review, S11-R-05) |
| Agent mentoring and knowledge transfer | SPEC12 §9 | done (`mentoring.py`: a mentor per learning plan reviews practice, never certifies, S12-R-04) |
| Collaboration recommendation with bias and reputation-manipulation protection | SPEC12 §11, §18 | done (`registry._rank`: windowed quality, no penalty without evidence, exploration, recorded decisions, S12-R-03; administrator corrections, S12-R-06) |
| Relationship kinds with evidence; links of a new agent | SPEC12 §2.2, `.rtf` | done (`network.py`, S12-R-01, S12-R-02) |
| Agent network view: overview counts, graph with server-side filters, pages and bounded depth | SPEC12 §16, §17 | done (`network.graph`, `static/network.js`, S12-R-07) |
| Shift rotation (acquisition → verification → analysis → organisation → review) with handovers; agent workload | `.rtf` SPEC-12, ASEAO06 §14 | done (`research_shifts`, S12-R-05, S06-R-05) |
| Capability gaps, learning plans with practice and hidden final evaluations, certification by the harness only | SPEC13 §5, §6, §8, §9 | done (`training.py`, `evals/python-*.json`, S13-R-01..R-03; exercises are hand-written suites, generated ones open) |
| A/B testing of playbook or model changes: experiments with baseline, candidate, sample sizes, review, canary and rollback; self-improvement levels E0-E6 | SPEC13 §10, §11, §12 | done (`experiments.py`, S13-R-04..R-06; workflow-template and retrieval-weight targets open) |
| Learning metrics: first-pass success, rework, knowledge reuse, benchmark per playbook version | SPEC13 `.rtf` | done (`training.metrics`, S13-R-07) |
| Agent behaviour monitoring with deterministic rules | SPEC14 §10 | done (six rules, cost-vs-estimate included, S14-R-03) |
| Security incident records and response | SPEC14 §11 | done (`incidents`, Security view, S14-R-04) |
| Emergency shutdown with levels E0-E5 (executor, agent, goal, project, room, company), CLI and top-bar control | SPEC14 §12 | done (`governance.stop`, `python -m agent_company stop`, S14-R-01) |
| Autonomy levels and action classification | SPEC14 §13 | done (`Agent.autonomy` G0–G4, enforced by the tool gateway; only administrators change permissions or autonomy, S14-R-02) |
| Threat model (STRIDE + AI threats) mapped to controls and tests; security simulations | SPEC14 PDF §19 | done (`docs/threat-model.md`, `tests/test_threats.py`, S14-R-09) |
| Tool permission contracts: one gateway for every executor run; isolation level, tools and permissions recorded per run | SPEC14 §7.3, §8.2, §8.3 | done (`executors.gate`, `Run.isolation`, `Run.tool_calls`, S14-R-06, S14-R-08; L3 sandbox for checks and benchmarks, `sandbox.py`, S14-R-07) |
| Information classification and data-handling rules | SPEC14 §5 | partial (projects and lessons; uploads, documents, model rule open) → S14-R-05 |
| MCP tool servers for agents | 1.txt, CDAF guide §11 | done (`mcp.py`, Tools & MCP, G-R-05) |
| A2A protocol for agent-to-agent messages | 1.txt | done (`a2a.py`: A2A 1.0 agent card, JSON-RPC tasks as goals for issued clients, G-R-06) |

## From docs/advanced_self_improving_agent_guide.pdf (verified self-evolution)

| Feature | Guide | Status |
| --- | --- | --- |
| Improvement contracts for the platform's own code: one capability, allowed paths, research proposal, fixed benchmark and acceptance | §5, §18, §19 | done (`selfevolve.py`, Evolution → Code evolution, SE-R-04, SE-R-05) |
| Protected control plane: security, evaluation, deployment, data and host tools are never a contract target; a candidate touching them is rejected | §6, §12 | done (SE-R-01) |
| Failure layer diagnosis (model, prompt, code, orchestration, tools, data, environment) and weaknesses by layer and component | §3, §4 | done (SE-R-02, SE-R-03) |
| Baseline vs candidate on the same fixed benchmark, run by the platform in detached worktrees; weighted score with guarded metrics | §7, §11 | done (SE-R-06, SE-R-09, SE-R-10) |
| Several candidates per contract; the best verified one deploys | §14 | done (SE-R-12) |
| Approval, deployment through the safe restart, automatic revert of a refused restart, benchmark of the deployed commit, one-click rollback | §12, §15, §16 | done (SE-R-13..R-15) |
| Agent Score per agent with role weights | §7 | done (Performance view, SE-R-11) |
| Fixed research-retrieval benchmark by query class | §9, §18 | done (`benchmarks/retrieval.py`, SE-R-17) |
| Retrieval strategy learned per query class | §9 v4 | open → SE-R-21 |
| Candidates in containers with network and resource limits | §12 | done (benchmarks in the L3 sandbox, `sandbox.py`, SE-R-22) |
| Fine-tuning, training models | §13 | skip (no training infrastructure) |

## Requested directly (Workspace home page, VS Code + Claude Code style)

| Feature | Status |
| --- | --- |
| Workspace as the administrators' home page: explorer, viewer over terminal, Claude Code panel, resizable panes | done (`static/workspace.js`) |
| Explorer: tree, open any folder, OS drag-in of files and folders, move/copy by drag, cut/copy/paste, rename, duplicate, delete, copy path, download (zip for folders), compress, extract, icons per extension | done (`files.py`, `static/workspace.js`) |
| Viewer: coloured code with line numbers and editing, Markdown preview, CSV tables, JSON, images, SVG, PDF (pdf.js), DOCX (mammoth), audio/video | done (`static/viewer.js`) |
| Terminal: real PTY shells over WebSockets (ssh works), coloured prompt and typed commands, sessions survive reloads | done (`terminal.py`, `static/console.js`) |
| Claude Code panel: streamed thinking, tool calls and results, answers with copy buttons, image attachments, resumed sessions | done (`workspace.py`, `static/claude.js`) |
| Claude Code sessions window: running sessions on the host (open, stop) and the history of every session with a transcript (search, open read-only, resume in the panel, delete) | done (`workspace.py`, `static/claude.js`) |
| Claude Code panel tabs: one per conversation (+ opens another and keeps the rest, each with its own draft), named by a short Haiku title of the first prompt (`--name`), other live sessions as tabs named by their transcript title (an ended one continues in place), a prompt that outlived a server restart followed live in its own tab, never twice | done (`workspace.py`, `static/claude.js`) |

Browser libraries are vendored in `static/vendor/` (xterm.js 5.5.0 + fit addon 0.10.0, pdf.js 3.11.174,
mammoth 1.8.0, noVNC 1.7.0 with its licences, unchanged from AXoR) so the dashboard needs no CDN; pdf.js runs with `isEvalSupported: false`.

Not yet examined (no features taken, no decision recorded):

- The cloned framework repositories in `/home/luca/update/`: OpenHands, SWE-agent, mini-swe-agent,
  aider, MetaGPT, autogen, langgraph, crewAI, dify, Flowise, agent-framework, openinterpreter,
  extensions. Candidates: aider's repo map, SWE-agent's agent-computer interface, LangGraph
  checkpointing (see the durable workflow row), OpenHands context condensing.
- `agentic-ai-frameworks.pdf` and `I am trying to download documents related to this.rtf`.

Not usable:

- `978-3-032-22940-3 (1).pdf` and `2.pdf`: no extractable text (scanned or damaged).
- `AXoR-Design-System-Requirements.pdf`: design system for a different product (AXoR, 46 views);
  not applicable here.
