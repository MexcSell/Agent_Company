# SPEC-14 — Security, AI safety, identity, access control and agent governance

Sources: `/home/luca/update/SPEC14.docx` (authoritative) and
`ASEAO_SPEC-14_Enterprise_Security_Governance.pdf` (same text typeset; adds §19 Threat model,
used below). Master criterion (`.rtf`): *the company can reconstruct the execution history of any
important task and identify which agent performed each action.*

Mapping: identities → `User` (humans), `Agent` (agents); permissions → `Agent.permissions`
(`read|write|shell|network`) and `auth.DEFAULT_PERMISSIONS` (users); tool gateway →
`executors.py` (`--allowedTools`, Codex sandbox) + `governance.check_command`; sandbox →
`worktrees.ensure_sandboxed`; audit → `governance.audit` (hash chain).

## Already met

| Item | Where |
| --- | --- |
| Tamper-evident audit (SEC-023, SEC-025) | `governance.audit`, `verify_audit` |
| Dangerous-command policy (§8.4) | `governance.check_command` |
| Secret scanning of changes | `checks.scan_secrets` |
| Untrusted content treated as data (§9, SEC-015) | `research.injection_flags` |
| Agents never touch production; file changes only in `data/worktrees/` | `worktrees.ensure_sandboxed` |
| Independent approval before merge (SEC-024 analogue) | `loop.approve` |

## Status of the specific requirements (2026-11-05)

| ID | Status |
| --- | --- |
| S14-R-01 | done (`governance.stop`/`lift`/`set_emergency`, `executors.terminate`, `live_processes`, `python -m agent_company stop`, `/api/stops`, top-bar stop; `tests/test_safety.py`) |
| S14-R-02 | done: `Agent.autonomy` G0–G4 (`executors.AUTONOMY`; G5 reserved to humans and refused), default by role (`org.RoleSpec.default_autonomy`: G3 for the roles on by default, G2 for gated roles and recruited specialists; clones keep their original's; agents of an existing database get G3, so their tools do not change), part of the versioned configuration; enforced by the tool gateway (G0 runs nothing, G1 no file changes or commands, MCP tools from G3); a G0 agent is never staffed, and when every agent of a role is at G0 staffing refuses rather than clone or recruit around it; refusals do not count against an agent's health; an administrator's model test still runs during a stop; only an administrator changes an agent's permissions or autonomy (API 403 otherwise, also for a rollback that would change them; audited `high`); agents can never approve a proposal touching permissions, autonomy, budgets, routes, settings or safety controls (`projects.restricted`, `decide(by_human=False)`); `tests/test_gateway.py` |
| S14-R-03 | done: rules for refused commands (pause + one alert), credentials in a change (stop + SEV-2), writes outside the worktree (SEV-2), repeated scope violations (stop + alert), failure rate > 70 % over 10 tasks (flag) (`governance.on_command_refused`, `loop._behaviour_rules`, `evolution._flag_unreliable`); cost > 2× estimate pauses the goal (`Goal.estimated_cost_usd`, set by the order or the planner's `estimated_cost_usd`; `loop.CostOverrun`; resuming accepts the cost so far as the new estimate) |
| S14-R-04 | done (`incidents` table, `governance.open_incident`/`update_incident`, containment actions, closing needs a root cause and becomes a lesson, Incidents list in the Security view) |
| S14-R-05 | done: projects, orders (goals), uploads and lessons are classified; models receive only what they are cleared for; secret work stores no lesson text; other projects never retrieve confidential+ lessons, and playbooks take only public/internal items. Research sources: skip (see S14-R-05) |
| S14-R-06 | done: `executors.gate` in `Executor.run`, so every run is covered (the loop, research reports, topic summaries, proposal triage, model tests): `TOOL_CONTRACTS` per executor (capabilities, roles, max seconds, `requires_approval`), then permission → autonomy → emergency state (`governance.stopped`, also E0/E1/E5 for runs outside the loop) → approval → limits; `Edit`/`Write` need `write`, Bash (`CLAUDE_BASH`) needs `shell` and comes only with a writing run as before, Codex writes only with `shell` (its `workspace-write` runs commands), granted MCP tools need G3. A request without an agent is read-only at G1. The CLI General Manager's configure step is the one write without `write` permission (recorded as `write:.claude`; edits outside `.claude/` are put back), with no Bash. What is withheld is a `security` event per run (`loop._execute`). `network` adds no web tools to Claude Code (none are allowed today; `CLAUDE_BASH` is not widened) |
| S14-R-07 | done: every run records its isolation level (`Run.isolation`: L0 text only — HTTP models and the simulator without writes; L1 read-only CLI tools; L2 writes in the worktree), has a timeout capped by its tool contract and runs in its own process group (killed on timeout or stop); output beyond 5 M characters is cut after the run rather than ending it. L3 (`sandbox.py`): the commands that execute agent-written code (quality and baseline commands, task acceptance commands, milestone checks, code-improvement benchmarks) run under bubblewrap with no network, own PID/IPC/UTS namespaces, no capabilities, system directories and toolchains read-only, only the work directory writable (its `.git` and git directories read-only), `data/`, `.env`, `~/.claude`, `~/.codex`, `~/.ssh` hidden, HOME of its own, an allow-listed environment (also on the host) and memory/task/CPU limits through a systemd scope; each check row records `isolation`. Setting `sandbox` (governance key) `auto`/`required`/`off`, a project may only tighten it, A2A goals always `required`; a changed lock file is installed with network in the same filesystem sandbox first. The agent CLIs themselves stay L2 (they need the network and their logins; threat model, known gaps); `tests/test_sandbox.py` |
| S14-R-08 | done: every run records agent, model, executor, purpose, goal/task, duration, cost, error, isolation, the tools it was allowed, the permissions it used, what was withheld, the tool calls Claude Code refused (`permission_denials`) and every tool call the run made (`Run.tool_calls`, from Claude Code's stream-json and Codex's `--json` events; inputs short and redacted; S04-R-12); shown in `GET /api/goals/{id}/trace` and the trace view |
| S14-R-09 | done: `docs/threat-model.md` (trust boundaries; STRIDE + prompt injection, memory poisoning, tool misuse, data exfiltration, supply chain, model compromise, each with its control and test; known gaps; when to review it); simulations in `tests/test_threats.py` (a compromised model cannot write outside the worktree or widen authority through a proposal — `projects.RESTRICTED` now also names shell, Bash, sandbox, tools, MCP, network/write access, grants, autonomy levels, escalation and deployment; one project's work never reaches another project's prompts) |

## Specific requirements

### S14-R-01 Emergency stop with levels — §12 (SEC-019..022)
Levels (spec E0–E6, adapted):

| Level | Scope | Effect here |
| --- | --- | --- |
| E0 | one executor/tool (`claude-code`, `codex`, HTTP models, web research) | executor refuses new runs |
| E1 | one agent | agent `lifecycle=paused`, its running tasks cancelled |
| E2 | one goal | `loop.cancel` (exists) |
| E3 | one project | all goals of the project cancelled (exists as `/stop`), new goals refused |
| E4 | one room | all agents of the room paused |
| E5 | all autonomous execution | goal queue stops taking work, running executor processes are terminated, research rotation and evolution cycle stop |

- State lives in the DB (`settings` key `emergency`: level, scope, actor, reason, at), so it
  survives a restart: after a restart under E5 nothing resumes until it is lifted.
- The stop path must not depend on any model or agent: a plain API call and a CLI command
  (`python -m agent_company stop [--level E5]`) that write the DB directly; it must work when the
  goal workers are stuck.
- Running executor subprocesses are tracked (PID per `Run`) so E1/E5 can terminate them.
- Lifting a stop requires an administrator; triggering and lifting are audited and notify all
  administrators.
- Dashboard: a visible stop control in the top bar for administrators.
- Acceptance: with E5 active, `create_goal` is accepted but not run, `GoalQueue` starts nothing,
  a running fake executor is killed within 5 s, and after restart the state is still E5.

### S14-R-02 Autonomy levels and action classification — §13
- Autonomy levels as explicit policy, per agent (`Agent.autonomy`, default by role):
  G0 observe, G1 recommend (no file writes), G2 low-risk operations, G3 approved workflows,
  G4 bounded autonomy; G5 is reserved to humans.
- Action table enforced in code (not by prompt):

| Action | Default |
| --- | --- |
| Read docs, search public sources | autonomous |
| Modify code in a worktree, create a branch | requires `write` permission |
| Merge | independent approval (exists) |
| Push | only `/sync` or AUTO_PUSH (exists) |
| Change permissions, routes, budgets, security settings | human administrator only |
| Disable the emergency stop or audit | prohibited to agents |

- Agents may propose policy changes (proposals) but can never approve a change that raises their
  own authority (SEC-007): proposals touching `permissions`, `autonomy`, budgets or settings
  always need a human.
- Acceptance: an agent-originated proposal to add `shell` to itself cannot be approved by any agent.

### S14-R-03 Behaviour monitoring with deterministic rules — §10
Per agent and per goal, record: tool calls, denied commands (`check_command` refusals), failures,
duration, cost, network fetches, permission changes, approval requests. Rules (first release,
no ML):

| Rule | Response |
| --- | --- |
| Command refused by `check_command` ≥ 3 times in one goal | pause the agent (E1), security alert |
| Secret detected in a change | stop the goal, incident SEV-2 |
| Scope violation (`checks.scope_violations`) repeated in 2 iterations | stop the goal, alert |
| Attempt to touch audit, settings, or another project's path | deny, incident SEV-2 |
| Failure rate of an agent over its last 10 tasks > 70 % | flag for review (evolution/certification) |
| Cost of one goal > 2× its budget estimate | pause the goal |

- Alerts are notifications of kind `security` to administrators plus an `events` row.
- Acceptance: three refused commands in one goal pause the agent and create one alert (SEC-017).

### S14-R-04 Security incidents — §11
- New table `incidents`: `category` (§11.2 list: unauthorized access, credential compromise,
  prompt injection, data leakage, malicious code, privilege escalation, agent compromise,
  supply chain, audit manipulation, unauthorized deployment), `severity` (`SEV-1..4`),
  `status` (`open→contained→investigating→remediated→closed`), `goal_id`, `agent_id`,
  `project_id`, `evidence` (event/run/audit refs), `root_cause`, `actions`, timestamps.
- Created automatically by S14-R-03 rules and manually by administrators (SEC-018).
- Containment actions available on an incident: pause agent, cancel goal, stop project,
  E5 (via S14-R-01). Evidence references are frozen when the incident is opened.
- Closing needs a root cause and remediation note; closed incidents feed lessons (SPEC-11).
- Dashboard: Incidents list in the Audit area. Acceptance: SEC-018, SEC-019, SEC-026.

### S14-R-05 Information classification — §5
- Classification levels: `public|internal|confidential|restricted|secret` on projects, uploads,
  documents, sources and lessons (default `internal`).
- Rules: `secret` content is never sent to a model and never stored in lessons; `restricted` and
  `confidential` content only goes to models whose catalog entry is marked as allowed for that
  level (local models by default); cross-project retrieval never returns another project's
  `confidential`+ content.
- Acceptance: a lesson from project A marked `confidential` is not in the context of a goal of
  project B (SEC-008, SEC-028 analogue).
- **Status: done** (`ModelEntry.clearance`, `Goal.classification`, `Upload.classification`;
  `router.clearance`, `cleared`, `NotCleared`, `resolve_chain(classification=)`; `learning.goal_level`,
  `highest`; `loop._execute`, `_plan`, `_configure`; `projects.command(classification=)`, `summarize`,
  `settle_proposals`; Models → "Receives up to", Projects → Orders → Classification; `tests/test_classification.py`,
  `tests/test_knowledge.py`). A model's clearance is set on the model, or by default `restricted` for local models
  (an OpenAI-compatible server on localhost, Ollama, vLLM) and `internal` for hosted ones; the offline simulator
  sends nothing and counts as `secret`. "Secret is never sent to a model" is read as: only to a model an
  administrator explicitly cleared for `secret` (none by default). A goal's level is the highest of its order's,
  its attachments' and its project's (the project's current one, so raising a project raises its running goals).
  When the level rules out every real model that would have run, the run fails with "no model is cleared for …"
  (no retry, not counted against the agent's health) and planning fails the goal; the simulator never stands in.
  After planning, every planned role is checked the same way (`loop._cleared_for`, with the role's original
  agent), so a goal no worker may run fails before any task. Topic summaries use the topic's level (its project,
  raised by classified orders and their goals) and fall back to their draft; proposals go to a person. Documents are the knowledge items, already
  classified. Research sources: skip, they are public web pages (private hosts need `ALLOW_PRIVATE_CRAWL`).

### S14-R-06 Tool permission contracts — §8.2, §8.3
- Each executor/tool declares capabilities, allowed roles, max seconds, `requires_approval`.
  Executor runs check: agent permission → autonomy level → emergency state → approval → run
  with limits → audit. Today this is split across `loop.py:301` and executors; it becomes one
  gateway function so every path is covered.
- Agents without `network` permission get no web tools; agents without `shell` get no Bash.
- Acceptance: an agent without `shell` never receives `Bash` in `--allowedTools` (SEC-011).

### S14-R-07 Isolation levels and limits — §7.3, §7.4
- Record the isolation level of each run: L0 text-only (HTTP models), L1 read-only tools,
  L2 worktree write (Claude Code / Codex `workspace-write`). L3 (container/gVisor), needed before agents run
  untrusted code from outside sources: done for the commands that execute agent-written code, with bubblewrap
  (`sandbox.py`; namespaces as containers use them, no daemon or image to maintain, the host's toolchains
  read-only). Docker would need an image per project toolchain; gVisor is not installed.
  `containers.py` can install and start Docker and LXD VMs, and `machines.py` runs KVM VMs in
  Docker (administrator's tools), which L3 can build on, but no agent run uses them yet: that
  alone is not isolation.
- Every executor run has a timeout and output-size limit; exceeding it terminates the run
  (SEC-013).

### S14-R-08 Execution trace (observability) — master spec SPEC-14
Every run must record: agent, model, executor, tools called, initiating task/goal, duration,
errors, outputs (truncated), permissions used. Delivered by S04-R-12.

### S14-R-09 Threat model — PDF §19
Keep `docs/threat-model.md` with the STRIDE + AI table (spoofing, tampering, repudiation,
information disclosure, denial of service, elevation of privilege, prompt injection, memory
poisoning, tool misuse, data exfiltration, supply chain, model compromise), each mapped to the
control in this repository; review it when a new executor, provider, integration or network path
is added. Security tests (§19.1): prompt-injection and memory-poisoning simulations and
cross-project isolation tests live in `tests/`.

### Skipped
- Workload identity tokens, ABAC engine, HSM/vault, field-level encryption, gVisor/Firecracker:
  `skip` for now — single-host deployment, agents have no credentials of their own (keys stay in
  the server environment). Revisit before multi-host or multi-tenant use.
