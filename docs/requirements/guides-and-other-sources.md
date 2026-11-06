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
`2.pdf` (framework logos and README screenshots only, no text);
`111.pdf` (a ChatGPT conversation "Research Agent Learning Materials", 34 pages: the research agent's sources,
pipeline, benchmarks and metrics, then how an agent website calls a model API with web search, which model each
role uses and how tasks are allocated) and `Research_Agent_Continuous_Learning_Guide.pdf` (the PDF export of
its first ten pages), both added 2026-11-05 and analysed 2026-11-06.

## The original brief (`1.txt`) — coverage

| Brief item | Where |
| --- | --- |
| Specialised rooms (GM, labs, collection, analysis, introduction, admin, agent management, study, outing, classroom, product, sales, comprehensive analysis, security, network security, guard) | rooms registered in `org.py`; only the core pipeline and research rooms are enabled (CLAUDE.md) |
| A room that retrieves specialised external data in rotation | done (`research.rotation_tick`); shifts and handovers → SPEC-12 S12-R-05, ASEAO06 S06-R-02 |
| Agents cooperate in a circular loop until the goal is met, bringing in external material | done (`loop.py`, research step); durability → SPEC-04 |
| A specialised agent is created per task and linked to similar agents to use their experience | done (`registry._recruit`, `_link_similar`); quality → SPEC-03, SPEC-12 S12-R-02 |
| After completion, agents learn and connect through experience | done (`loop._learn_from`, `link_team`); defects S12-D-01; retrieval → SPEC-11 |
| Come up with new ideas and put them into practice | proposals → goals; documented case → ASEAO10 S10-R-01 |

## The research agent guide (`111.pdf`, `Research_Agent_Continuous_Learning_Guide.pdf`) — coverage

The guide assumes Node.js, PostgreSQL, pgvector and Redis; here it is adapted to SQLite, FastAPI and FTS5 as
everywhere else (README → Stack).

| Guide item | Where |
| --- | --- |
| Source registry (arXiv, GitHub, Hugging Face, official docs, news) | done (`Source`, `research.add_source`, Research page) |
| Collection by RSS, APIs, GitHub releases, scheduled jobs | feeds: G-R-10; schedules: source intervals hourly..monthly (S06-R-02); arXiv/OpenAlex/Semantic Scholar API: `mcp_servers/papers.py` (G-R-05) |
| Register title, URL, dates, type, version, licence | URL, title, kind (`classify_source`), collection time, content hash and versions (S06-R-02); publication date and licence: G-R-10 |
| Filter: duplicates, relevance, source quality, freshness | one document per URL and content hash; duplicate research requests (`find_overlap`); kinds of source and preferences (S06-R-01); a change must mention the source's subject to be an event |
| Document reader: extract text from PDFs | open: G-R-12 |
| Treat collected material as untrusted, not as instructions | done (`injection_flags`; flagged pages never reach prompts; report prompts say so) |
| Three libraries: research, experiments, approved agent knowledge | `documents`; `evals/` + `experiments`; knowledge items with screening (`learning.py`); only an approved report becomes knowledge (S06-R-04) |
| Distinguish verified facts, recommendations, unverified and conflicting claims | done (S06-R-03: every claim checked against the stored source, `[n:contradicts]`, dropped claims; independent review S06-R-04) |
| Improvement levels: knowledge, skills, workflow, model | retrieval (S11-R-03), playbooks and agent definitions (`evolution.py`, `agentdefs.py`), workflow templates (S04-R-11, S04-R-13), model routes as experiments (SPEC-13); fine-tuning not taken (no training infrastructure) |
| A fixed, versioned benchmark per agent with reference tasks | coding: `evals/*.json` with practice/final/adversarial/room suites; research agent: open, G-R-13 |
| Metrics: success, accuracy, citation correctness, completeness, latency, cost per success, tool failures, regression rate | eval dimensions S09-R-05 (completion, tool use, efficiency incl. cost per passed run, reliability), claim checks per report (S06-R-03); regression rate: G-R-11 |
| "Did it improve on a fixed set without damaging existing capabilities?" | G-R-11 |
| Daily collection, weekly benchmark review | daily: source intervals and the rotation; weekly benchmark: open, G-R-14 |
| Research → experiment → QA → integrate → compare versions → decide | research proposals (S06-R-07), hypotheses and experiments (S07-R-03, SPEC-13), an administrator applies (E0-E6 policy) |
| Human approval for model, prompt, tool and deployment changes; baselines and rollback | done (routes and templates applied by an administrator, self-evolution approval and rollback, playbook versions) |
| Sandboxed benchmark code | done (S14-R-07 L3) |
| Research API: create a request, read a report, search history | done (`POST/GET /api/research/requests`, `/api/research/search`) |
| Model per role: strong model for planning/review, efficient for routine work | done (Model Router role routes, fallback, `gm set-model`) |
| Task allocation: dependencies, parallel independent tasks, one owner per file, experienced agent for complex tasks, a failing test becomes a bug, security review, approval for destructive or external actions | plan DAG and handovers (SPEC-04), file leases G-R-08, staffing by skill coverage and experience (`registry._rank`), defects S09-R-03, `risk_level` high → security review (G-R-01), merge approval and `governance.check_command` |
| Research by a provider's model with web search (OpenAI Responses API) | not taken now: the research room reads the sources it collected and stored, so every claim can be checked against a stored passage (S06-R-03); a hosted web-search tool returns text that cannot be checked that way, and it is billed per call. A decision for the owner |
| Expose the research library to Claude Code over MCP | not taken: a task's package already carries the matching approved research (S11-R-03), and the database MCP server refuses the platform's own data directory by design |

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
  shown in the trace and the goal view. Once the goal merges, the `NEXT:` lines of its final iteration's tasks
  become project action items `Follow up: …` once (`management._follow_ups`, source `next:<task>:<n>`, priority 3,
  the project GM responsible; goals approved in the last 30 days; an open item with the same text is not repeated);
  `sync` never closes them and a closed one is not made again. Also
  `decision_topic` for S04-R-03.

### G-R-02 Operational metrics — Designing guide §10, Subscription guide §7
Performance view adds: acceptance-test pass rate, escaped defects (defects reopened after
merge), review rejection rate, median cycle time per goal, blocked time, retry rate, cost per
accepted goal, rollback rate, security findings, human approval latency. Optimise for verified
outcomes, not activity.
**Status: done** (`performance.operations`, `report()["operations"]`, Performance → Operational metrics;
`tests/test_performance.py`). Over real work (benchmark goals left out), each rate with its sample size `n` and
`None` without samples, nothing combined into one score: the quality gate's pass rate per evaluation and the share of
goals passing in iteration 1 (the gate is the platform's acceptance test: commands, stages, reviews); review
rejection rate (QA/security reviews answering `VERDICT: FAIL`); escaped defects (closed at a merge, then reopened —
only a later check run, e.g. a retest, can reopen them); median cycle time (order → merged, rejected, cancelled or
failed) and blocked time (paused) from `goal_transitions`; human approval latency (`awaiting_approval` → a person's
approve or reject); retry rate and cost per accepted (merged) goal, shared with the enablement gate
(`_retries_and_cost`); rollback rate of deployed improvement contracts, with best-iteration resets counted beside it;
security findings (failed secret scans, security stages and dependency audits, refused commands, incidents).

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
`override_gate` (audited, high severity).
The per-room eval suites are done too (`performance.room_gate`, `GET /api/performance/enablement?room=<key>`,
`eval --gate <room>`; `tests/test_room_gate.py`). Enabling (or resuming, `POST /api/agents/{id}/lifecycle`) an agent of a
gated role needs its room's gate: the pipeline gate above; the room's suite `evals/room-<key>.json` (`kind: room`)
passed ≥ 80 % over ≥ 3 repeats on real models with the room's gated roles doing a task in every run; a smoke label
run with the room in the pipeline (`eval --suite smoke --room <key>`) that passes no less often than the pipeline's
label ("`eval --compare` before/after must not regress"); and "one at a time": no other gated room's agent enabled
or changed since the pipeline's label. A suite runs as a room trial (`Goal.trial.room`): that room's disabled
agents are planned for and staffed on those eval goals only, their clones stay disabled, and the pipeline's own
gate never counts a room trial's label. Suites so far: `room-security` (password storage, token comparison and path
traversal in sensitive files, so each plan gets a security review; `[security]` checks fail insecure work) and
`room-development` (Frontend: an accessible form; DevOps: a Dockerfile). The other gated rooms show "no eval suite
for room X" until theirs is written.

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
- **Status: done, adapted** (`loop.paths_ahead`, `_note_lease`, `GoalQueue._loop`; `checks.overlapping`, `in_scope`;
  `tests/test_leases.py`). A goal's declared paths are its `allowed_paths`. The queue holds a goal while an earlier goal
  on the same repository (before either has one: the same project) is queued or being worked on and declared paths
  that can name the same file — the scope check's rule (`fnmatch` either way, or a folder prefix). The goal's events
  and its command window say once which goal it waits for and on which paths; the watcher offers it again. A goal
  that declares no paths may change anything and leases nothing (making it wait would run every goal of a repository
  one by one); goals waiting for approval, paused or ended hold no lease, so a person's queue never blocks work. The
  merge half is met by G-R-03's behaviour instead of a new `revision_required` state: a conflicting merge is rolled
  back, the repository is left unchanged, an automatic merge leaves the goal `awaiting_approval` and `approve`
  answers 400 until the branch is resolved. Paths a workflow template gives a goal count as declared
  (`strategies.py`: a `docs` goal gets `docs/*`, `*.md`, …), so `docs` goals of one project run one at a time.

### G-R-09 Mission digest — AgentForge website spec, step 5
- After a goal, the GM posts one digest in the command window: tasks passed/total, duration,
  tokens, cost, merge state. (Verify the existing summary includes tokens; add if missing.)
- **Status: done** (`loop.digest`, `digest_line`, posted by `_chat_outcome`; `GET /api/goals/{id}` → `digest`, the goal
  page's "Digest" line; `tests/test_digest.py`). The result message's second line reads "Digest: 3/3 task(s) passed in
  iteration 2 · 4m 10s (1m 5s of agent work) · 12,340 tokens · $0.0210 · waiting for approval to merge": the last
  iteration's tasks done of total, the time since the order and the time in agent runs, tokens and cost of every run
  of the goal (planning, research and reviews included), and the merge state (merged, waiting for approval to merge,
  not merged).

### G-R-10 Feeds and provenance of collected material — research guide §3, §11
- A source can be an RSS 2.0, RSS 1.0 or Atom feed (arXiv listings, GitHub `releases.atom`, documentation and
  blog feeds): each entry with a link becomes a document with the entry's summary or content as its text; the
  feed itself is not stored, so a refetch adds a version only for an entry that changed. A feed with a DOCTYPE is
  refused (no entity expansion).
- Every document records its publication date and licence when the material states them: the feed entry or
  channel (`published`/`updated`/`pubDate`/`dc:date`, `rights`/`copyright`), or the page's citation, article and
  Dublin Core meta tags and `<link rel="license">`.
- **Status: done** (`research.parse_feed`, `is_feed`, `parse_date`, `_provenance`; `Document.published_at`,
  `Document.license`; shown on the Research page, in `/api/research/documents`, search results and a report's
  sources; the report writer sees the date; `tests/test_research.py`). Acceptance: Atom and RSS entries with UTC
  dates and the feed's licence, an unchanged refetch adds no version, a DOCTYPE is refused, HTML meta tags give
  both.

### G-R-11 No regression on what the baseline solved — research guide §7 ("without damaging existing capabilities")
- A candidate (route, workflow template, playbook) that does not solve, in every repeat, a task the baseline
  solved in every repeat is not better, whatever its pass rate or its savings.
- **Status: done** (`experiments.regressions`, `side_metrics` with `tasks_run`/`solved_tasks`, `compare` with
  `regressions` and `regression_rate`; `orchestration.compare` counts a regression as a quality guard;
  `evolution.smoke_trial` rolls such a playbook back; `eval --compare` lists them; `tests/test_regressions.py`).
  A task the candidate did not run is no evidence; sides recorded before the per-task record compare as before.

### G-R-12 Text from PDF documents — research guide §8 ("Document reader", PyMuPDF)
- The crawler stores only HTML and text; a linked paper (application/pdf) is skipped.
- **Status: open, needs a decision**: it needs a PDF library (none is installed) and parsing untrusted binaries,
  which belongs in the L3 sandbox (S14-R-07) rather than in the server process. Acceptance: a PDF from a source
  becomes a document with its text, extracted in the sandbox with a size and time limit.

### G-R-13 A fixed benchmark for the research agent — research guide §6
- Reference research tasks (find, verify a source, summarise, compare, generate tests, recommend), each with
  approved reference sources and a correctness method that is source-based (the S06-R-03 claim check), not only
  another model's opinion; run by the eval harness with its own suite kind.
- **Status: open**: the eval harness runs coding goals in scratch repositories; research tasks need a runner that
  grades a report against stored reference documents. Larger than one change: a goal for the goal loop.

### G-R-14 A scheduled benchmark review — research guide §8 (daily collection, weekly benchmark review)
- A setting that runs a chosen suite on a schedule and compares it with the last run (G-R-11), reporting
  regressions as a notification.
- **Status: open, needs a decision**: every run uses real models and costs money; the owner sets whether, which
  suite and the budget (S10-R-02 budgets apply).

## Not requirements
- Download guide, CrewAI setup, VS Code extension lists, Hugging Face/Ollama/LM Studio links:
  setup references; local model servers are already supported (`ModelEntry.base_url`).
- The cloned framework repositories next to these documents are code, not specifications; see
  `docs/feature-gap.md` → "Not yet examined".
