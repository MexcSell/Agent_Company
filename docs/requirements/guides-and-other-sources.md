# Guides, AgentForge website spec, protocols and the original brief

Sources in `/home/luca/update/`:
`1.txt` (original brief and reference URLs);
`Designing_Autonomous_AI_Software_Development_Company.pdf`;
`Design Autonomous AI Company.pdf` (illustrated overview; same organisation as the ASEAO master
spec, no additional requirements);
`AI_Agent_Subscription_and_Free_Specialized_Agents_Guide.pdf`;
`AI_Agent_Company_Download_Guide.pdf`;
`Claude_Code_VSCode_Extensions_CDAF_Guide.pdf`;
`AgentForge-Website-Specification.pdf`;
`agentic-ai-frameworks.pdf` (AWS Prescriptive Guidance on frameworks and protocols);
`2.pdf` (framework logos and README screenshots only, no text).

## The original brief (`1.txt`) — coverage

| Brief item | Where |
| --- | --- |
| Specialised rooms (GM, labs, collection, analysis, introduction, admin, agent management, study, outing, classroom, product, sales, comprehensive analysis, security, network security, guard) | rooms registered in `org.py`; only the core pipeline and research rooms are enabled (CLAUDE.md) |
| A room that retrieves specialised external data in rotation | done (`research.rotation_tick`); shifts and handovers → SPEC-12 S12-R-05, ASEAO06 S06-R-02 |
| Agents cooperate in a circular loop until the goal is met, bringing in external material | done (`loop.py`, research step); durability → SPEC-04 |
| A specialised agent is created per task and linked to similar agents to use their experience | done (`registry._recruit`, `_link_similar`); quality → SPEC-03, SPEC-12 S12-R-02 |
| After completion, agents learn and connect through experience | done (`loop._learn_from`, `link_team`); defects S12-D-01; retrieval → SPEC-11 |
| Come up with new ideas and put them into practice | proposals → goals; documented case → ASEAO10 S10-R-01 |

## Specific requirements

### G-R-01 Task contract minimum fields — Designing guide §3
Add to `contracts.TaskContract` (all optional, defaulted, so existing plans stay valid):
`in_scope`, `out_of_scope` (paths/areas; `out_of_scope` paths become scope violations),
`risk_level` (`low|medium|high`; high adds a security reviewer, ASEAO09 S09-R-02),
`test_plan` (text), `budget` (cost/turns), `deadline`, `requirement_keys` (ASEAO08 S08-R-01).
Task outputs gain `findings` and `next_actions` (parsed like proposals) that feed action items
(ASEAO05 S05-R-04).
- **Status: done** (`contracts.TaskContract`, `contracts.limits`, `loop._contract_note`,
  `loop._with_security_reviews`). Changing an `out_of_scope` path fails the task and counts as a scope
  violation for the repeated-scope rule; a high-risk task gets a `security` review after it (QA while
  the Security Room is disabled); `budget.cost_usd` fails a run that costs more, `budget.turns` becomes
  Claude Code's `--max-turns`, `deadline` caps the run's timeout; `test_plan`, `in_scope` and
  `requirement_keys` go into the prompt. `FINDING:` and `NEXT:` lines are stored on the task's gate and
  shown in the trace and the goal view; turning them into action items is ASEAO05 S05-R-04 (open). Also
  `decision_topic` for S04-R-03.

### G-R-02 Operational metrics — Designing guide §10, Subscription guide §7
Performance view adds: acceptance-test pass rate, escaped defects (defects reopened after
merge), review rejection rate, median cycle time per goal, blocked time, retry rate, cost per
accepted goal, rollback rate, security findings, human approval latency. Optimise for verified
outcomes, not activity.

### G-R-03 Failure-mode tests — Designing guide §8
`tests/` covers: executor timeout, provider rate limit (429), worker crash mid-goal (restart
resume, SPEC-04 S04-R-05), malformed planner JSON, failing dependency task, merge rollback.
**Status: done** (`tests/test_failure_modes.py`, `test_safety.py`, `test_loop.py`). Writing them found a
defect, now fixed: a conflicting merge left the person's repository mid-merge; `worktrees.merge` now
aborts and leaves it unchanged, `approve` answers 400, and an automatic merge that is refused leaves the
goal awaiting approval instead of failing.

### G-R-04 Measured gate for enabling more roles — Subscription guide §7, `.rtf` §6
"Pipeline reliable" (CLAUDE.md) is defined as: smoke suite pass rate ≥ 80 % over 3 repeats with
the configured models, no open critical defects, retry rate and cost per accepted goal reported.
Only then are gated rooms (analysis, innovation, extra engineering/QA teams) enabled, one at a
time, each with its own eval suite. `eval --compare` before/after must not regress.
**Status: done** (`performance.enablement_gate`, `GET /api/performance/enablement`): enabling an agent of a
gated role through `PATCH /api/agents/{id}` answers 409 with the reasons until the latest smoke label
passed ≥ 80 % over ≥ 3 repeats per task on real models (simulator runs never count) and no critical
defect is open; retry rate and cost per accepted goal are reported. An administrator may override with
`override_gate` (audited, high severity). The per-room eval suites are open.

### G-R-05 MCP servers for Claude Code agents — CDAF guide §11, §13; `1.txt`; AWS guide
- Administrators register MCP servers (name, command/URL, allowed roles, required env var names —
  never values). For a run, the executor writes a `.mcp.json` into the worktree with only the
  servers allowed for the agent's role and passes `--mcp-config`; tools from MCP servers are
  added to `--allowedTools` explicitly.
- Suggested first servers: Context7 (current docs) for developer/research, Playwright for QA UI
  checks. GitHub MCP is not given to agents (pushing stays on `/sync`, CLAUDE.md).
- Agents cannot add or change MCP servers (SPEC-14 S14-R-02). Changes are audited.
- **Status: done** (`mcp.py`, Tools & MCP): administrators register servers (secrets encrypted, names
  only shown), grants are per agent (or every agent) rather than per role, a run gets
  `--mcp-config … --strict-mcp-config` with only its granted, tested servers and their allowed tools
  added to `--allowedTools`; the GM's toolbox only lists servers.

### G-R-06 A2A interoperability — `1.txt`, `.rtf` §15 infrastructure, AWS guide — P2
- Publish an A2A agent card describing the company's skills and accept A2A tasks as goals for
  authenticated callers only. Not before G-R-04 is met.
- **Status: done** (`a2a.py`, tables `a2a_clients`, `a2a_tasks`; `tests/test_a2a.py`), against A2A 1.0
  (a2a-protocol.org, JSON-RPC binding). Off by default (`a2a_enabled`); while off, the card and the endpoint answer
  404. `GET /.well-known/agent-card.json` lists the enabled roles as skills, one JSONRPC interface
  (`/api/a2a`, protocol 1.0), a bearer security scheme, no streaming, push notifications or extended card.
  "Not before G-R-04 is met" is read as the measured condition: issuing a client (`POST /api/a2a/clients`,
  admin) answers 409 with the gate's reasons unless `performance.enablement_gate()` is met or the administrator
  overrides (audited, high severity). Only the token's SHA-256 is stored; revoking is immediate. Calls need the
  `A2A-Version: 1.0` header (else -32009) and a valid token (else HTTP 401 with `WWW-Authenticate: Bearer` and
  JSON-RPC code -32040, a code chosen here: the specification leaves it open). `SendMessage` (text parts only, else
  -32005) creates a goal of the client's project on behalf of the issuing administrator with `origin="a2a"`,
  `extract_checks=False` and no repository, quality commands or permits from the caller; it is audited
  (`a2a_task`) and refused during an E5 stop or an E3 stop of its project. An A2A goal always waits for a person's
  approval, whatever `require_approval` says (`loop`: held like improvement goals). States: queued → SUBMITTED;
  planning, running, recovering, paused, awaiting approval → WORKING (paused and awaiting approval explain
  themselves in `status.message`); approved → COMPLETED with the goal's result as an artifact; failed → FAILED;
  rejected → REJECTED; cancelled → CANCELED. `GetTask` (with `historyLength`), `ListTasks` (newest first, pages
  with `pageToken`, filters `contextId` and `status`) and `CancelTask` (idempotent; work waiting for approval is withdrawn; a finished task is -32002)
  see only the caller's tasks; another client's task is -32001. A client cannot be issued for a project
  classified above its clearance, and a result whose goal is later classified above it is withheld. Push
  methods answer -32003, streaming and the extended card -32004.
  **Deviations:** `SendMessage` answers at once with the submitted task (as if `returnImmediately` were true):
  goals take minutes to hours. A message to an existing task is refused (-32004, "send a new message"): there is
  no input channel into a running goal. Security → A2A clients issues and revokes clients.

### G-R-07 Topics run in order — AgentForge website spec (orchestration)
- Goals in the same project topic (ASEAO05 S05-R-07) run in sequence; different topics run
  concurrently.
- **Status: done** (`loop.lane_ahead`, `GoalQueue._loop`; `tests/test_topics.py`): a goal waits while an
  earlier goal of its project topic is queued, planning, running or recovering; the queue's watcher offers it
  again every 5 s. Paused goals and goals waiting for approval do not hold the topic. Goals without a project
  (evals, the CLI) and goals run directly (`run_goal`) are not held.

### G-R-08 File leases between concurrent goals — AgentForge website spec, step 3
- Goals of the same project run in separate worktrees, so conflicts surface only at merge. Each
  goal declares the paths it expects to change (`allowed_paths` or planner output); a second goal
  declaring overlapping paths waits or is warned. Merge conflicts set the goal to
  `revision_required` instead of failing.

### G-R-09 Mission digest — AgentForge website spec, step 5
- After a goal, the GM posts one digest in the command window: tasks passed/total, duration,
  tokens, cost, merge state. (Verify the existing summary includes tokens; add if missing.)
- **Status: done** (`loop.digest`, `digest_line`, posted by `_chat_outcome`; `GET /api/goals/{id}` → `digest`, the goal
  page's "Digest" line; `tests/test_digest.py`). The result message's second line reads "Digest: 3/3 task(s) passed in
  iteration 2 · 4m 10s (1m 5s of agent work) · 12,340 tokens · $0.0210 · waiting for approval to merge": the last
  iteration's tasks done of total, the time since the order and the time in agent runs, tokens and cost of every run
  of the goal (planning, research and reviews included), and the merge state (merged, waiting for approval to merge,
  not merged).

## Not requirements
- Download guide, CrewAI setup, VS Code extension lists, Hugging Face/Ollama/LM Studio links:
  setup references; local model servers are already supported (`ModelEntry.base_url`).
- The cloned framework repositories next to these documents are code, not specifications; see
  `docs/feature-gap.md` → "Not yet examined".
