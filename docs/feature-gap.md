# Feature gap: what the reference projects have that Agent Company lacked

Sources: `/home/luca/AgentForge` (Node control room), `/home/thomas/bit` (SWE agent,
eval harness, local model), `/home/luca/update/` (ASEAO/SPEC documents, guides).
Status: `done` = implemented in this repository, `skip` = deliberately not taken (reason given),
`open` = worth doing later (none are left open).

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
