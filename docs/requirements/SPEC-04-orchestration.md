# SPEC-04 — Multi-agent orchestration and collaborative execution

Source: `/home/luca/update/ASEAO SPEC04.docx` (FR table §5, sections §9–§15).
Master criterion (ASEAO master specification, `.rtf`): *a single objective can be decomposed into
multiple dependent and independent tasks, executed by different agents, and assembled into one
verified deliverable.*

Mapping to this codebase: objective → `Goal`; task contract → `contracts.TaskContract`;
orchestrator → `loop.py`; scheduler → `scheduler.run_dag`; team → agents staffed by
`registry.staff`; budgets → `goal_budget_usd` / `goal_time_budget`.

## Functional requirements (spec IDs kept)

| ID | Requirement | Pri | Status |
| --- | --- | --- | --- |
| S04-FR-01 | Receive and register human objectives | P0 | done (`loop.create_goal`) |
| S04-FR-02 | Interpret objectives and constraints | P0 | done (`loop._plan`) |
| S04-FR-03 | Generate orchestration plans | P0 | done (`loop._plan`) |
| S04-FR-04 | Decompose objectives into subtasks | P0 | done (`contracts.validate_plan`) |
| S04-FR-05 | Define task dependencies | P0 | done (`Task.depends_on`, `contracts.topo_order`) |
| S04-FR-06 | Discover suitable agents | P0 | done (`registry.staff`) |
| S04-FR-07 | Dynamically form agent teams | P0 | done (`registry.staff`, `learning.link_team`) |
| S04-FR-08 | Assign and delegate tasks | P0 | done |
| S04-FR-09 | Support hierarchical supervisors | P0 | done (S04-R-07): escalations reach the goal GM, room manager, executive GM or a person (`escalation.py`) |
| S04-FR-10 | Support agent-to-agent communication | P0 | done (S04-R-10, `blackboard.extract_messages`/`inbox`) |
| S04-FR-11 | Support parallel execution | P0 | done (`scheduler.run_dag`) |
| S04-FR-12 | Support sequential execution | P0 | done |
| S04-FR-13 | Maintain shared task context | P0 | done (S04-R-01, `blackboard.py`) |
| S04-FR-14 | Track task and team progress | P0 | done (`Task.status`, `/api/stream`) |
| S04-FR-15 | Aggregate agent results | P0 | done (`loop._summary`) |
| S04-FR-16 | Resolve conflicting outputs | P1 | done (S04-R-03, `loop._review_conflicts`) |
| S04-FR-17 | Verify task completion | P0 | done (`contracts.check`, `quality.evaluate`) |
| S04-FR-18 | Retry failed tasks | P0 | done (S04-R-06): retries by failure type, capped by the goal deadline (`loop._execute`) |
| S04-FR-19 | Handle timeouts and cancellations | P0 | done (`loop.cancel`, command timeouts, `goal_time_budget`) |
| S04-FR-20 | Recover interrupted workflows | P1 | done: a restart resumes the planned iteration without re-running finished tasks (S04-R-05) |
| S04-FR-21 | Enforce execution budgets | P0 | done (S04-R-09): goal cost, tokens, time and agents at once (pause or stop), child goals capped by the parent, per-task cost, turns and deadline (G-R-01) |
| S04-FR-22 | Support human approval checkpoints | P0 | done (merge approval) |
| S04-FR-23 | Record orchestration decisions | P0 | done (S04-R-02 `decisions.py`, S04-R-04 `goal_transitions`) |
| S04-FR-24 | Support strategy selection | P1 | done (S04-R-11, `strategies.py`): the order or the planner picks the kind; versions per kind |
| S04-FR-25 | Support dynamic team resizing | P1 | done (S04-R-11, `teams.py`): add, release, take back; history kept |
| S04-FR-26 | Support workflow templates | P1 | done (S04-R-11, `strategies.py`) |
| S04-FR-27 | Self-evaluation of orchestration | P2 | done (S04-R-13, `orchestration.evaluate`) |
| S04-FR-28 | Recommend improved orchestration strategies | P2 | done (S04-R-13: `orchestration.recommend`, strategy experiments) |
| S04-FR-29 | Support multi-project concurrency | P1 | done (`GoalQueue` workers, `Goal.project_id`) |
| S04-FR-30 | Support execution replay and audit | P1 | done: `GET /api/goals/{id}/trace` with every run's tool calls (S04-R-12); a replay that re-runs a goal is not taken (runs are not deterministic) |

## Specific requirements

Status (2026-11-05): S04-R-01 done · S04-R-02 done · S04-R-03 done · S04-R-04 done · S04-R-05 done ·
S04-R-06 done · S04-R-07 done · S04-R-08 done · S04-R-09 done · S04-R-10 done · S04-R-11 done ·
S04-R-12 done · S04-R-13 done. Details under each item.

### S04-R-01 Blackboard (shared, versioned goal context) — §10
- New table `blackboard` in `db.py`: `goal_id`, `section`
  (`objective|planning|research|engineering|qa|reports`), `key`, `version`, `author_agent_id`,
  `task_id`, `content`, `sources` (list), `review_status` (`draft|reviewed|rejected`), `created_at`.
  Unique `(goal_id, section, key, version)`.
- Writes use optimistic concurrency: a write names the version it is based on; if a newer version
  exists the write is rejected (no silent overwrite). Old versions are never deleted.
- The planner writes `objective` (goal, constraints, acceptance criteria) and `planning`
  (breakdown, dependencies); research writes findings with source URLs; Developer writes
  technical decisions; QA writes test results and defects.
- Context loading (§10.2): each task prompt gets its contract, the dependencies' outputs, and only
  the sections its role needs (Developer: objective, planning, engineering, relevant decisions;
  QA: objective, engineering, qa). Never the full conversation, never another goal's entries.
- Acceptance: two writes based on the same version → the second is rejected; a Developer prompt
  contains the goal's `engineering` entries and no entries of another goal.
- **Status: done** (`blackboard.py`, `tests/test_blackboard.py`).

### S04-R-02 Decision records — §12.2, also SPEC-01 FR-10, ASEAO07 §13.1, ASEAO08 §7.2
- New table `decisions`: `goal_id`, `project_id`, `topic`, `alternatives` (list),
  `recommendation`, `reasoning`, `evidence` (blackboard/task/source refs), `assumptions`,
  `confidence` (0–1, labelled an estimate), `category`
  (`operational|technical|organizational|financial|critical`), `requires_human_approval`,
  `status` (`draft→submitted→under_review→approved|rejected`, plus `escalated|expired|cancelled`),
  `approver`, `decided_at`.
- The Architect's plan may emit decisions; a goal with an open `requires_human_approval`
  decision cannot be merged.
- API: `GET /api/decisions?goal_id=`, `POST /api/decisions/{id}/decide` (calls `_audit`).
- Acceptance: a decision marked `requires_human_approval` blocks `loop.approve` until it is approved.
- **Status: done** (`decisions.py`, goal page approve/reject; also forces `awaiting_approval` when
  REQUIRE_APPROVAL=0).

### S04-R-03 Conflict resolution — §12.1
- The orchestrator must never resolve conflicting outputs by taking the last one received.
- Contracts may name a `decision_topic`; when two tasks of one iteration give different
  recommendations on the same topic, the orchestrator schedules a review task (reviewer from a
  different provider, reusing `avoid_provider`). The review compares both against the acceptance
  criteria and produces a decision record (S04-R-02). High-impact (`critical`/`financial`)
  conflicts escalate (S04-R-07).
- Acceptance: two task outputs with different recommendations on one topic → one review task and
  one decision record citing both task IDs.
- **Status: done** (`loop._review_conflicts`, `_decide_conflicts`; `tests/test_orchestration.py`). Tasks
  end with a `RECOMMENDATION:` line when their contract has a `decision_topic`; recommendations that
  differ after normalising case and punctuation get one QA review (`conflict-<topic>`, depending on the
  tasks, so their outputs are in its prompt; `avoid_provider` as for other reviews). Its `DECISION`,
  `CONFIDENCE` and `CATEGORY` lines become a decision record whose evidence cites every task;
  `financial` and `critical` ones need a person and block the merge. The review runs after the
  iteration's other tasks; if the work followed the recommendation it rejects, it fails the gate
  (`VERDICT: FAIL`), so the next iteration corrects it.

### S04-R-04 Goal workflow state machine — §13.1

![Workflow states](images/spec04-workflow-states.png)

Transitions (from the diagram): `Created→Planning`; `Planning→Ready`;
`Planning→AwaitingApproval`; `AwaitingApproval→Ready`; `AwaitingApproval→Cancelled`;
`Ready→Running`; `Running→Paused`; `Paused→Running`; `Running→AwaitingReview`;
`AwaitingReview→Running` (revision required); `AwaitingReview→Completed` (verified);
`Running→Failed`; `Failed→Recovering`; `Recovering→Running`; `Running→Cancelled`;
`Completed` and `Cancelled` are final.

- Map onto `Goal.status`: `queued`=Created/Ready, add `planning`, `paused`, `recovering`;
  `awaiting_approval`=AwaitingReview; `approved`=Completed. Keep existing values readable.
- New table `goal_transitions` (`goal_id`, `from`, `to`, `reason`, `actor`, `at`); every status
  change goes through one function that rejects transitions not listed above.
- Acceptance: an illegal transition (e.g. `approved→running`) raises; each change writes one row.
- **Status: done** (`workflow.py`; `planning`, `recovering`, `paused` added; pause/resume for
  administrators, effective at the next step boundary).

### S04-R-05 Durable recovery — §13.2
- On restart, a goal that was `running` resumes from its last checkpoint: tasks already `done` in
  the current iteration are not re-run, `running` tasks go back to `queued`, the worktree is
  reconciled with the last recorded commit (`Goal.history`).
- Non-idempotent operations (merge, push, notifications to external services) record an
  idempotency key `goal:iteration:operation` before running and are skipped if the key is already
  marked complete.
- Resumption re-checks budget and permissions first; history is preserved.
- Acceptance: stop the process after task A of iteration 2 finished → restart → A is not
  executed again and the goal finishes; a merge interrupted after completion is not repeated.
- **Status: done.** `Goal.checkpoint`, resume without re-running finished tasks, idempotency keys for
  merge and push (`operations`), status written only after the gate result. Worktree reconcile: the plan
  step and every finished writing task are committed on the goal branch (`loop._plan_commit`,
  `_checkpoint`; the scheduler's write lock makes a commit hold that task's changes only) and the commit
  is the checkpoint; after a restart the worktree is reset to it (`loop._reconcile`,
  `worktrees.reset_to`), so a task cut off mid-write reruns without its partial edits. Generated files,
  attachments and git-ignored files stay. Resumption re-checks the budget first.

### S04-R-06 Failure-specific retry policy — §14, §14.1

| Failure | Action |
| --- | --- |
| Network error, provider 429/5xx, timeout | retry the task with backoff `d_n = min(d_max, d_0·2^n) + jitter`; then next model in `router.resolve_chain` |
| Invalid task contract | return to planner (re-plan) |
| Executor crash | restart the task once on a fresh session |
| Missing capability | search workforce / recruit (`registry._recruit`) |
| Command blocked by `governance.check_command` | stop the task, notify, no retry |
| Conflicting outputs | review (S04-R-03) |
| Budget exhausted | pause the goal (`paused`), notify, wait for an administrator |
| Same check failing in N consecutive iterations | escalate (S04-R-07) |
| Secret found / scope violation | stop the goal, notify, open an incident (SPEC-14 S14-R-04) |

- Settings (`prefs.py`): `retry_max_attempts` (3), `retry_base_seconds` (2),
  `retry_max_seconds` (60). Every retry has a cap and respects the goal deadline.
- Acceptance: a fake executor raising a transient error twice then succeeding is retried with
  growing delays; a blocked command is not retried.
- **Status: done.** Transient errors (backoff with jitter, then the next model; a retry that would
  wait past the goal's time budget is not made), executor crash on a resumed session (once, fresh),
  blocked command (no retry), secret/scope (goal stopped, incident). Invalid contract: the planner gets
  the validation error and one more try before the standard plan. Missing capability: staffing searches
  the workforce, then recruits (`registry.staff`, `_covering`, `_recruit`). Conflicting outputs: S04-R-03.
  Budget exhausted: the goal pauses and administrators are notified (`budget_exhausted_action`, S04-R-09).
  The same check failing in `escalate_after` (2) consecutive iterations is an `escalation` event (level 2)
  and a note to the planner to change approach.

### S04-R-07 Escalation levels — §14.2
- Levels: 1 assigned agent (retry), 2 goal GM (re-plan), 3 room manager, 4 executive GM of the
  protected platform project, 5 human (notification with approval).
- Escalation depth is chosen by decision category (S04-R-02) and impact, not only by attempt
  count. Each escalation is an `events` row with `level`.
- Acceptance: a `critical` decision escalates directly to level 5.
- **Status: done** (`escalation.py`, `tests/test_escalation.py`). `escalation.recipient` resolves each level:
  1 the agent, 2 the goal project's manager (else the company GM), 3 the head of the agent's room (the enabled
  agent there that reports outside it; a head goes to its superior), 4 the manager of the protected platform
  project, 5 nobody (an admin notification). `escalation.escalate` writes the `escalation` event with `level`,
  `recipient`, `recipient_agent_id` and leaves levels 2–4 as a `request` on the goal's blackboard, read in the
  recipient's next prompt (the planner now reads the GM's inbox). Decisions: `level_for(category, protected,
  human)`; technical and organizational decisions on the protected project go to 4; recorded decisions of level
  3–4 are escalated to that agent. A check that keeps failing climbs one level per `escalate_after` iterations
  (2, 3 to the room manager of the writing agent, 4, 5 to a person). Limitation: the inbox delivers by role, so a
  level-2 or level-4 request addressed to a GM is read by whichever GM agent plans the goal; `recipient_agent_id`
  is recorded on the message and the event, not enforced.

### S04-R-08 Communication-loop prevention — §9.4
- Maximum proposal/goal chain depth (follow `Goal.parent_goal_id`; setting `max_goal_depth`,
  default 3), extending the existing proposal loop guard.
- Per-task message budget for agent messages (S04-R-10), duplicate-message detection,
  escalation after repeated unanswered requests.
- Acceptance: a proposal from a goal at depth 3 is recorded but not turned into a goal.
- **Status: done.** `max_goal_depth`, per-task message budget (3), duplicate messages dropped; a `request`
  repeated `escalate_after` times with no message from its recipient back to its sender escalates once to the
  goal's GM (level 2, `blackboard._asked_again`).

### S04-R-09 Budget dimensions and allocation — §15, §15.1

![Budget allocation](images/spec04-budget-allocation.png)

*(Company budget → research / engineering / QA / security budgets → their agents.)*

- Add to goal budgets: model tokens, tool calls / executor turns, number of active agents,
  wall-clock (exists), cost (exists).
- A child goal (from a proposal) cannot exceed its parent's remaining budget; budget increases
  need an administrator and are audited; unused budget returns to the parent.
- Exhaustion pauses or stops according to a setting (`budget_exhausted_action`: `pause|stop`).
- Acceptance: a child goal requesting more than the parent's remainder is capped.
- **Status: done** (`budgets.py`, `tests/test_budgets.py`). Goal columns `budget_usd`, `budget_tokens`,
  `max_active_agents` (0 = the settings `goal_budget_usd`, `goal_token_budget`, `goal_agent_limit`). `_budget_note`
  pauses or stops on cost and tokens (`budget_exhausted_action`, default `pause`; an iteration cut short is not
  judged, its unrun tasks run after the resume, and resuming refuses while the goal is still over budget); a goal's
  cost and tokens count what its child goals use or hold. Active agents: `run_dag(max_agents=)` caps how many of a
  goal's tasks (one agent each) run at once. Child goals: `budgets.for_child` caps a proposal goal to the parent's
  remainder (an event records the cap); with nothing left the proposal is recorded as rejected and no goal is made.
  A running child holds its budget; once it ends only its use counts, so the unused part returns to the parent.
  Increases: `POST /api/goals/{id}/budget` (administrators, audited; a child stays within its parent), the settings
  button in the goal view. Per-task cost, turns and deadline stay in the contract (G-R-01). The time budget counts
  per run of the loop, so a resumed goal gets a fresh time window. Room budgets of the diagram (company →
  research/engineering/QA/security): `Room.daily_budget_usd` (Agents → Rooms → edit; 0 = none) covers what the
  agents of the room and of every room below it (`parent_key`) spend per UTC day (`registry.room_spent_today`).
  `registry.over_quota` checks the agent's own quota, then each room on the way to the top
  (`room_over_budget`); a used-up room budget works like a used-up agent quota (S02-R-03): its agents are not
  staffed or cloned, a goal that needs them pauses (`paused_for: quota`) and goes back to the queue after 00:00 UTC,
  or when an administrator raises the budget and resumes it. Project budgets (S05-R-06) cap all of a project's
  goals beside it.

### S04-R-10 Agent-to-agent messages — §9.2, §9.3
- Message contract: `sender_agent_id`, `recipient_agent_id` or `channel`, `goal_id`, `task_id`,
  `kind` (`fact|assumption|recommendation|request|handoff|done`), `body`, artifact references.
  Stored as blackboard entries of section `messages`; recipients see unread messages in their
  next prompt.
- Large content is referenced by artifact or file path, not inlined.
- Acceptance: a `request` from Developer to Architect appears in the Architect's next prompt and
  is linked to the task.
- **Status: done** (`MESSAGE <kind> to <role>:` lines; `blackboard.extract_messages`, `inbox`).

### S04-R-11 Workflow templates, strategy selection, team resizing — FR-24/25/26
- Templates per goal kind (`bugfix`, `feature`, `refactor`, `research`, `docs`) define roles,
  default checks and iteration limits; the planner picks one and records the choice.
- Team resizing: the GM may add an agent of a missing role mid-goal (recruit) or release one.
- Acceptance: a goal created with `kind=research` runs without Developer tasks.
- **Status: done** (`strategies.py`, `teams.py`, `tests/test_strategies.py`). Templates are versioned rows
  (`strategies`, §20.4: roles, `writes`, `recruit`, checks `inherit|none`, iteration limit, execution
  `parallel|sequential`, review `independent_qa|none`, protected tests, default scope, planner instructions); an
  administrator saves or restores a version (`POST /api/strategies/{kind}`, audited) and every goal keeps a snapshot
  of the version it ran under (`Goal.kind`, `Goal.strategy`, in the trace). The order names the kind
  (`create_goal(kind=)`, API, `goal --kind`, `gm goal --kind`) and its defaults fill what the order left empty;
  otherwise the planner names one in its plan and it can only tighten the goal (a lower iteration limit, protected
  tests, a docs scope); a plan naming none gets an inferred label only. A plan that does not fit goes back to the
  planner once, then the template's own plan is used. A research template runs no quality commands (no file
  changes). Team resizing (§8.4): staffing records each agent's first task (`joined`); the GM's plan
  (`"team": {"add", "release"}`), `gm team` or an administrator (`POST /api/goals/{id}/team`, audited) adds a role,
  releases an agent (it gets no further task of the goal) or takes one back; rows are never rewritten. Templates
  are experiment targets since S04-R-13 (FR-27/28).

### S04-R-12 Execution trace and replay — FR-30, also SPEC-14 observability
- `GET /api/goals/{id}/trace`: ordered transitions, tasks, runs (agent, model, tool calls,
  duration, cost, errors), decisions, gate results, merges.
- Acceptance: for a finished goal the trace names the agent and model of every run.
- **Status: done** (`loop.trace`, `/api/goals/{id}/trace`, `tests/test_gateway.py`). Every run carries its tool
  calls (`Run.tool_calls`: tool, a short input — the command, file or pattern — with credential-like text redacted,
  and whether it failed; at most 200 per run, then a count). Claude Code runs with `--output-format stream-json
  --verbose` (`executors.parse_claude_stream`: `tool_use` blocks, a `tool_result` with `is_error` marks the call
  failed, the final `result` event as before); Codex with `exec --json` (`executors.parse_codex_stream`: commands
  with their exit code, file changes, MCP calls, web searches, usage from `turn.completed`; a Codex without events
  is read as plain text). HTTP models and the simulator make no tool calls. An output over the 5 M character cap
  keeps its head and tail, so the result event survives. The trace view lists the calls under each run.

### S04-R-13 Self-evaluation of orchestration and recommended strategies — FR-27, FR-28, §20, AT-031..037
- Evaluate every workflow template version (S04-R-11) from the live goals that ran under its rules (the order or
  the planner chose the kind; an inferred kind is only a label; eval and improvement goals are left out): acceptance,
  first-iteration passes, goals that used every iteration, iterations, time to the gate's answer, cost per accepted
  goal, failed runs (retries), conflict reviews, plans replaced by the template's own, and the bottlenecks (most
  frequent failed checks, roles with the most failed runs). Each figure with its sample size; no combined score.
- Recommend: deterministic rules on the current version's evidence (at least 5 decided goals) propose a candidate
  template, recorded as a SPEC-13 experiment with target `strategy`, its base version, changes, reasons and
  evidence. The system never changes a template on its own.
- §20.3 policy: a candidate may not turn on file changes or recruitment, drop the checks, the independent review or
  protected tests, widen the template's paths or open it to any role; such a candidate is recorded `rejected` with
  the violations. A person who wants that saves a version directly (`POST /api/strategies/{kind}`, admin, audited).
- Experiment: both sides on the same suite and repeats as eval goals in scratch repositories (checks in the L3
  sandbox); each side's template travels in the goal's trial, never through the template in force. Better = pass
  rate up by `min_improvement` points, or §20.5 cheaper/faster by that much with pass rate, compliance and security
  not lower. It then waits for an administrator.
- Rollout: an administrator applies it as the kind's new version; a canary watches the final word of the next goals
  under it and restores the previous version when the window can no longer reach its baseline.
- Acceptance: AT-031 report with sample sizes; AT-032 recorded candidate; AT-033 isolated evaluation of both sides;
  AT-034 policy violation rejected; AT-035 approval requested; AT-036 new version recorded; AT-037 rollback.
- **Status: done** (`orchestration.py`, `tests/test_strategy_evolution.py`). Report: `orchestration.evaluate`
  (latest 20 decided goals per version; `GET /api/strategies/evaluation`, `python -m agent_company strategies
  evaluate [kind]`, Evolution → Orchestration strategies). Rules: one more iteration (≤ 10) when ≥ 30 % of the goals
  used every iteration without passing; `sequential` when ≥ 30 % had a conflict review; `independent_qa` when a
  template without review had < 80 % accepted. `recommend` runs after every decided live goal of the kind
  (`workflow.set_status` → `orchestration.after_outcome`, errors logged, never in the way of the transition), and on
  request (`POST /api/strategies/{kind}/recommend`, admin, audited; `strategies recommend <kind>`); it records nothing
  while an experiment of the kind is open or when the same change to the same version was rejected or rolled back.
  A person proposes one with `POST /api/experiments` (`target: strategy`, `role`: the kind, `candidate`: JSON of the
  fields to change; administrators only). The experiment's hypothesis is concluded by its comparison (S07-R-03).
  Eval goals take `trial["strategy"]` (kind, version, body) as their template, including its iteration limit (the
  suite's own limit is not passed for strategy sides). Applying needs an administrator (`experiments.apply`, API
  admin-only, audited `high`) and a candidate measured against the version still in force. The canary (3 goals)
  counts approved / rejected / failed of goals under the deployed version, one outcome per goal (its last word);
  awaiting approval does not count, the person's rejection being the quality signal. Its baseline is the previous
  version's live acceptance rate, or the suite's baseline without live goals. If a newer version was saved meanwhile
  nothing is restored, the experiment is only marked rolled back.

## Spec acceptance tests (§23) → requirements

| AT | Test → expected | Covered by |
| --- | --- | --- |
| AT-001..004 | goal → run, invalid goal → errors, plan version, child tasks | done |
| AT-005 | cyclic dependencies → rejected | done (`contracts.topo_order`) |
| AT-006, AT-007 | qualified candidates; unqualified agent → rejected | done (SPEC-03 S03-R-02: an agent covering < 70 % of the skills is not staffed; a match is reused, cloned or recruited) |
| AT-008..011 | group recorded; unauthorized member / delegation beyond authority → denied | S14-R-02, S14-R-06 |
| AT-012, AT-013 | parallel and dependent execution | done (`scheduler.run_dag`) |
| AT-014, AT-015 | message delivery recorded; unauthorized context denied | done (S04-R-10, S04-R-01) |
| AT-016..018 | result stored; failed review → revision; passed → verified | done (gate, iterations); states S04-R-04 |
| AT-019, AT-028 | budget exceeded / exhausted → policy action, pause or stop | done (`budget_exhausted_action`, S04-R-06) |
| AT-020 | cancel → pending work cancelled safely | done (`loop.cancel`) |
| AT-021, AT-027 | worker crash / orchestrator restart → resume from durable state | done (S04-R-05) |
| AT-022 | database unavailable → no false completion | done (status written only after the gate result is stored) |
| AT-023 | duplicate event → no duplicate execution | done for merge and push (idempotency keys) |
| AT-024 | provider timeout → retry or fallback | done (S04-R-06) |
| AT-025 | agent unavailable → replacement or escalation | S04-R-07, SPEC-02 S02-R-02 |
| AT-026 | message delivery fails → retry, dead letter | S04-R-10 (messages are DB rows; no broker) |
| AT-029 | approval revoked → protected action blocked | done (decision status checked at merge time) |
| AT-030 | human emergency suspension | done (SPEC-14 S14-R-01) |
| AT-031..037 | orchestration self-evolution | done: S04-R-13 (`tests/test_strategy_evolution.py`) |
| §23.4 load tests | 10k agents, 1k events, large graphs | skip for now: establish a measured baseline first (single host) |

## Diagrams kept

![Agent execution loop](images/spec04-agent-execution-loop.png)

*Loop: receive task contract → load authorized context → understand objective → plan next action →
check permission and budget → authorized? (no → request approval or stop) → execute action →
evaluate result → task complete? (no → update context → plan next action; yes → submit deliverable →
independent verification → passed? no → return for revision; yes → mark complete).*

![Dependency DAG](images/spec04-dependency-dag.png)

*Requirements → Architecture → Database design / Frontend design / Backend design → implementations
→ Integration → QA → Release approval.*

![Improvement loop](images/spec04-improvement-loop.png)

*Execute workflows → collect evidence → analyse performance → identify bottlenecks → improvement
proposals → evaluate strategy → sandbox experiments → meets acceptance criteria? → approval →
version new strategy → controlled rollout → monitor → back to execution. Implemented through
SPEC-13 (S13-R-05).*
