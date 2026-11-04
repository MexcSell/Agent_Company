# Feature gap: what the reference projects have that Agent Company lacked

Sources: `/home/luca/AgentForge` (Node control room), `/home/thomas/bit` (SWE agent,
eval harness, local model), `/home/luca/update/` (ASEAO/SPEC documents, guides).
Status: `done` = implemented in this repository, `skip` = deliberately not taken (reason given),
`open` = worth doing later; IDs point to `docs/requirements/`.

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
| Topics: separate conversation lanes inside a project (AgentForge website spec) | open → S05-R-07, G-R-07 |
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
| Cost and time budgets per goal, with a wrap-up warning at 60% | done (`goal_budget_usd`, `goal_time_budget`) |
| Reviewer from a different model family than the driver | done (`avoid_provider` for QA/security tasks) |
| Eval harness: run benchmark goals N times, pass rate / cost / time / end reason, compare labels | done (`evaluation.py`, `evals/smoke.json`, `eval` CLI, Performance view) |
| Local OpenAI-compatible model server (llama-server, LM Studio, vLLM) via base URL | done (model `base_url`, `api_key_env`) |

## From /home/luca/update (ASEAO specs, SPEC11-14, guides)

| Feature | Spec | Status |
| --- | --- | --- |
| Tamper-evident (hash-chained) audit log | SPEC01 §10.5, SPEC14 | done (`governance.audit`, `verify_audit`) |
| Dangerous-operation policy for commands; approval for destructive actions | SPEC14 §8.4 | done (`governance.check_command`, used by every quality command) |
| Secret scanning of changes in the quality gate | SPEC14, subscription guide §7 | done (`checks.scan_secrets`) |
| Prompt-injection flags on collected material; flagged pages kept out of prompts; material wrapped as untrusted data | SPEC14 §9, SPEC11 §10.4 | done (`research.injection_flags`) |
| Knowledge provenance and source kinds (no single trust score, per ASEAO06 §9.2) | ASEAO06 §9, SPEC11 §6 | done (`research.classify_source`) |
| Research requests producing a cited research report | ASEAO06 §5-6 | done (`research.request_report`, `run_report`) |
| Team compatibility: prefer agents that worked well together | SPEC12 §7 | done (`registry._skill_score` team utility) |
| Task handover package when work moves to another agent | SPEC12 §10 | done (`loop._handover`, `handovers` table) |
| Capability certification from evaluations (L0-L5, expiry) | SPEC13 §9 | done (`performance.certification`) |
| Defect records from failed checks, closed when fixed, reopened on regression | ASEAO09 §11 | done (`performance.track_defects`) |
| Performance intelligence: agent, room and company metrics | ASEAO07 §15 | done (`performance.report`, Performance view) |
| Agent lifecycle states (active, paused, retired) | SPEC02 §6 | done (`Agent.lifecycle`) |
| Capability-gap calculation, agent template catalog, duplicate-agent prevention | SPEC03 §5, §8, §11.2 | open → S03-R-01..R-03 |
| Blackboard: shared context per goal, with versioning | SPEC04 §10 | open → S04-R-01 |
| Conflict resolution between agents' results, with decision records | SPEC04 §12 | open → S04-R-02, S04-R-03 |
| Durable workflow state: resume interrupted goals after a restart | SPEC04 §13 | open → S04-R-04, S04-R-05 |
| Retry policy and escalation levels for failed tasks | SPEC04 §14 | open → S04-R-06, S04-R-07 |
| Milestones, blockers and risks, insights, upcoming action items per project | ASEAO05 §7-10 | open → S05-R-01..R-04 |
| Continuous monitoring of registered sources (beyond the rotation) | ASEAO06 §13 | open → S06-R-02 |
| Research-to-innovation pipeline: findings become proposals | ASEAO06 §15 | open → S06-R-06 |
| Hypotheses, root-cause analysis, predictions, decision center | ASEAO07 §9-13 | open → S07-R-01..R-04 |
| Architecture Decision Records | ASEAO08 §7.2 | open → S08-R-02 |
| Requirements traceability (requirement → task → test) | ASEAO08 §5.3, ASEAO09 §5.1 | open → S08-R-01, S09-R-06 |
| Code-review checklist with a logic-verification step (trace data flow; no approval for code that only looks clean) and de-duplicated comments | ASEAO08 §13, `extensions/analysis_results.md` | open → S08-R-03 |
| Quality gates as named stages; test-strategy selection by test category | ASEAO09 §6-7, §13 | open → S09-R-01, S09-R-02 |
| Innovation experiments, portfolio and technology radar | ASEAO10 §10-14, §21 | open → S10-R-02, S10-G-01..G-06 |
| Knowledge graph of agents, projects, technologies and lessons | SPEC11 §7, 1.txt (Neo4j) | open → S11-R-07 |
| Memory hierarchy and hybrid knowledge retrieval | SPEC11 §4, §8 | open → S11-R-01, S11-R-03 |
| Contradiction handling between stored lessons | SPEC11 §6.3 | open → S11-R-04 |
| Memory-poisoning protection for learned lessons | SPEC11 §10.4 | open → S11-R-05 |
| Agent mentoring and knowledge transfer | SPEC12 §9 | open → S12-R-04 |
| Collaboration recommendation with bias and reputation-manipulation protection | SPEC12 §11, §18 | open → S12-R-03, S12-R-06 |
| Autonomous learning planner and training-exercise generator | SPEC13 §6, §8 | open → S13-R-01, S13-R-02 |
| A/B testing of playbook or model changes | SPEC13 §10.4 | open → S13-R-05 |
| Agent behaviour monitoring and anomaly detection | SPEC14 §10 | open → S14-R-03 |
| Security incident records and response | SPEC14 §11 | open → S14-R-04 |
| Emergency shutdown with levels (agent, project, company); today only `/stop` per project | SPEC14 §12 | open → S14-R-01 |
| Autonomy levels and action classification | SPEC14 §13 | open → S14-R-02 |
| Information classification and data-handling rules | SPEC14 §5 | open → S14-R-05 |
| MCP tool servers for agents | 1.txt, CDAF guide §11 | open → G-R-05 |
| A2A protocol for agent-to-agent messages | 1.txt | open → G-R-06 |

## Requested directly (Workspace home page, VS Code + Claude Code style)

| Feature | Status |
| --- | --- |
| Workspace as the administrators' home page: explorer, viewer over terminal, Claude Code panel, resizable panes | done (`static/workspace.js`) |
| Explorer: tree, open any folder, OS drag-in of files and folders, move/copy by drag, cut/copy/paste, rename, duplicate, delete, copy path, download (zip for folders), compress, extract, icons per extension | done (`files.py`, `static/workspace.js`) |
| Viewer: coloured code with line numbers and editing, Markdown preview, CSV tables, JSON, images, SVG, PDF (pdf.js), DOCX (mammoth), audio/video | done (`static/viewer.js`) |
| Terminal: real PTY shells over WebSockets (ssh works), coloured prompt and typed commands, sessions survive reloads | done (`terminal.py`, `static/console.js`) |
| Claude Code panel: streamed thinking, tool calls and results, answers with copy buttons, image attachments, resumed sessions | done (`workspace.py`, `static/claude.js`) |

Browser libraries are vendored in `static/vendor/` (xterm.js 5.5.0 + fit addon 0.10.0, pdf.js 3.11.174,
mammoth 1.8.0) so the dashboard needs no CDN; pdf.js runs with `isEvalSupported: false`.

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
