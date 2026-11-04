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

### S14-R-06 Tool permission contracts — §8.2, §8.3
- Each executor/tool declares capabilities, allowed roles, max seconds, `requires_approval`.
  Executor runs check: agent permission → autonomy level → emergency state → approval → run
  with limits → audit. Today this is split across `loop.py:301` and executors; it becomes one
  gateway function so every path is covered.
- Agents without `network` permission get no web tools; agents without `shell` get no Bash.
- Acceptance: an agent without `shell` never receives `Bash` in `--allowedTools` (SEC-011).

### S14-R-07 Isolation levels and limits — §7.3, §7.4
- Record the isolation level of each run: L0 text-only (HTTP models), L1 read-only tools,
  L2 worktree write (Claude Code / Codex `workspace-write`). L3 (container/gVisor) is `open`,
  needed before agents run untrusted code from outside sources.
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
