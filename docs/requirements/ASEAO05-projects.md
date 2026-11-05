# ASEAO SPEC-05 — Project and task management

Source: `/home/luca/update/ASEAO05.docx`. Master criterion (`.rtf`): *a manager can see exactly
who is doing what, what has been completed, what is blocked, how much time has been used, and
what happens next.* The `.rtf` task status chain: `CREATED → PLANNED → ASSIGNED → IN_PROGRESS →
REVIEW → APPROVED → COMPLETED`, plus `BLOCKED, FAILED, CANCELLED, REOPENED`.

Mapping: project → `Project`; project manager agent → `Project.manager_id` (GM); task → `Task`
(per goal iteration); conversation → `Message`; files → `files.py`.

## Functional requirements (spec IDs kept)

| ID | Requirement | Status |
| --- | --- | --- |
| S05-FR-01 | Authorized users can register projects | done (`projects.py`, permission `create_projects`) |
| S05-FR-02 | Unique project ID | done |
| S05-FR-03 | Each project has an owner | done (`Project.created_by`) |
| S05-FR-04 | Assigned PM agent or explicit pending state | done (`Project.manager_id`) |
| S05-FR-05 | Select participating departments | done (`Project.rooms`, S05-R-06) |
| S05-FR-06 | Required skills and capabilities | done (`Project.required_skills`, S05-R-06) |
| S05-FR-07 | Environment, repository, stack, working directory | done (path, GitHub URL, `Project.stack`, S05-R-06) |
| S05-FR-08 | Budgets, deadlines, priorities, security classification | done (S05-R-06, S14-R-05) |
| S05-FR-09 | Pause, resume, archive, cancel, complete | done (`projects.set_status`, S05-R-06) |

## Specific requirements

Status (2026-11-05): S05-R-01 done · S05-R-02 done · S05-R-03 done · S05-R-04 done · S05-R-05 done ·
S05-R-06 done · S05-R-07 done · S05-R-08 done (adapted) · S05-R-09 done · S05-R-10 done (SPEC-04).

### S05-R-01 Milestones — §7 (AT-012)
- Table `milestones`: `project_id`, name, description, target date, deliverables, required goal
  IDs, acceptance criteria (check commands or manual), owner agent, status, completed_at.
- A milestone cannot be completed while a mandatory criterion is unmet (commands are re-run).
- Projects view shows milestones with progress.
- **Status: done** (`management.py`: `create_milestone`, `check_milestone`, `complete_milestone`,
  `set_criterion`; Projects → Plan; `tests/test_management.py`). Command criteria run in a throwaway
  detached worktree of the project's current HEAD under `data/worktrees/` (the sandbox), through the command
  policy; manual criteria are ticked by an administrator. Required goals must be `approved`.

### S05-R-02 Risks and blockers — §8 (AT-014)
- Table `risks`: `project_id`, `goal_id`, kind (`risk|blocker`), category (technical, security,
  budget, schedule, staffing, external dependency, quality, infrastructure, requirements),
  severity, probability (risks only), impact, mitigation, owner agent, status.
- Blockers are created automatically when a goal is paused for budget, waits for approval longer
  than a setting, or a dependency goal failed; they close when the cause is resolved.
- The GM's `/status` and stand-up list open blockers first.
- **Status: done** (`management.sync`, `create_risk`, `update_risk`; `projects.status_text`, `standup`).
  Automatic blockers carry their `cause` (`goal:<id>:budget`, `goal:<id>:approval`,
  `milestone:<id>:goal:<id>`) and are reconciled after every goal and whenever the plan or `/status` is
  read; the wait is `approval_blocker_hours` (Settings, default 24, 0 = never). "A dependency goal failed"
  is read as a goal a milestone requires (goals have no other dependencies).

### S05-R-03 Insights — §9 (AT-015, AT-016)
- Agents can report an insight in their output (JSON block `insights`), parsed like proposals.
- Table `insights`: project, goal, discovering agent, title, description, evidence, sources,
  expected benefit, estimated cost, confidence, related tasks, status
  (`recorded→analyzed→reviewed→accepted|rejected→implemented→evaluated`), decision, result.
- Accepted insights become knowledge items (SPEC-11) and may become proposals; originals keep
  their project and source references.
- **Status: done** (`management.extract_insights`, called beside `extract_proposals`; `advance_insight`).
  Only fenced ```json blocks holding `{"insights": [...]}` are read, at most three per task. Steps go
  forward only; `rejected` before acceptance; `implemented`/`evaluated` only after it. An accepted insight
  is written as a knowledge item directly (kind `semantic`, evidence `INFERENCE`, source `insight:<id>`),
  screened like lessons and withheld for secret projects, without XP or evolution; `as_proposal` adds a
  pending proposal on the insight's goal.

### S05-R-04 Upcoming action items — §10 (AT-017)
- Table `action_items`: project, title, description, responsible agent, priority, due date,
  dependencies, expected output, status, related goal/milestone.
- Generated from: open proposals, unresolved defects, open blockers, failed milestone criteria;
  the GM ranks them by dependencies, priority, due date and risk. Shown in the Projects view and
  in `/status`.
- **Status: done** (`management.ranked_items`; generated items carry `source` = `proposal:`, `defect:`,
  `risk:`, `criterion:<milestone>:<n>` and close as `done` when the source is settled; defects count
  only on goals that are not failed, cancelled or rejected). The ranking is
  deterministic (no model call): items waiting for open dependencies last, then in progress, priority, due
  date, risk (severity of the blocker it resolves or the goal's worst open blocker).

### S05-R-05 Time and weighted progress — §6 (AT-010, AT-011, AT-013)
- Per goal: estimated time (planner estimate per task), actual active time (sum of run
  durations), waiting time (queued + awaiting approval), blocked time.
- Project progress = Σ(weight × completion) / Σ weights over the project's goals and milestones;
  task weight from the contract (default 1).
- Overdue detection for milestones and action items with notifications.
- **Status: done** (`management.goal_time`: active = Σ run durations, waiting = queued + awaiting
  approval, blocked = paused, from `goal_transitions`, estimated = Σ the contract field `estimated_minutes` of the
  latest iteration's tasks, which the planner is asked for (0 = none given); `management.progress` with contract
  `weight`; overdue milestones and action items notify once, on the next sync). `GET /api/goals/{id}` returns
  `time` and the goal view shows it under the iteration line; `tests/test_management.py`.

### S05-R-06 Project registration fields and lifecycle — FR-05..09
- Add to `Project`: `stack` (list), `required_skills` (list), `rooms` (list of room keys),
  `budget_usd`, `deadline`, `priority`, `classification` (SPEC-14 S14-R-05), `status`
  (`active|paused|archived|cancelled|completed`).
- Paused/archived projects accept no new goals; cancel cancels running goals (exists as `/stop`).
- Project budget caps the sum of its goals' budgets (SPEC-04 S04-R-09).
- **Status: done** (`Project.stack`, `required_skills`, `rooms`, `budget_usd`, `deadline`, `priority`, `status`,
  `status_reason`; `projects.update` validates them (known rooms, slug skills, ISO deadline), `set_status`,
  `PROJECT_TRANSITIONS`, `refusal`, `project_spend`; `POST /api/projects/{id}/status` for an administrator or the
  owner, audited; Projects → Edit and the status menu; `tests/test_project_lifecycle.py`). Transitions: active →
  paused|completed|archived|cancelled, paused → active|archived|cancelled, archived → active, completed → archived;
  cancelled is final; the platform project stays active. Pausing pauses its open goals (marked
  `paused_by_project`; running ones stop at the next step) and resuming resumes only those; a goal of a paused
  project cannot be resumed on its own. Cancelling cancels the open goals; completing and archiving need none.
  Only an active project within its budget takes new goals (`loop.create_goal`, orders get an error reply; eval goals
  are exempt). The budget is the cost of all its goals' runs: at the cap a running goal is paused like at its own
  budget (`loop._budget_note`), and raising the budget lets it resume. The planner is told the stack, required
  skills, rooms, deadline, priority and budget (`loop._project_context`) and only offered the roles of the
  participating rooms; a plan naming a role of another room goes back once and otherwise fails the goal, as does a
  standard plan outside them (`loop._in_rooms`; a role nobody has yet passes and is recruited as usual). The goal
  queue is a priority queue: critical, high, normal (also goals without a project), low, then first come first
  served (`loop.PRIORITY_RANK`; no aging: a steady stream of critical or high goals keeps low ones waiting). A goal
  of a cancelled or archived project waiting for approval cannot be merged (`loop.approve`); completed projects
  still merge pending work. The deadline is shown and marked overdue in `/status` and the header; it does not
  stop work.

### S05-R-07 Topics (conversation lanes) — §12 (AT-021..023), AgentForge website spec
- `Message.topic` (default `general`); the command window shows topic tabs; agents assigned to a
  topic; search within a project's messages; a summary of long topics on request (`/summary`).
- Decisions found in topics are stored as decision records (SPEC-04 S04-R-02), not left in chat.
- Acceptance: an order sent in a topic is answered and reported (plan, results) only in that topic; a
  `DECISION:` line and a decision a summary finds are decision records of the project, recorded once.
- **Status: done** (`Message.topic`, `Goal.topic`, table `topics`; `projects.topics`, `create_topic`,
  `update_topic`, `summarize`, `record_decision_lines`; `GET/POST /api/projects/{id}/topics`,
  `PATCH …/topics/{name}`, `GET …/messages?topic=&q=`; Projects → Orders & reports: a tab per topic, search
  over every topic, topic settings; `tests/test_topics.py`). `general` always exists. Anyone who may give
  orders opens topics; administrators assign agents (a topic's `/standup` reports them), edit and archive
  (archived topics take no new messages). A goal keeps its order's topic, as do goals spawned from its
  proposals. Decisions: `DECISION: <subject> :: <choice> [:: <why>]` lines a person writes (a message of
  only such lines starts no goal), and the ones the GM's model lists in a fenced ```json
  `{"decisions": [...]}` block of a `/summary`; agents' own decisions still come from plans and conflict
  reviews. `/summary` uses the GM's model on the newest 12 000 characters of the topic, with an extractive
  draft (goals, decisions, latest results) that the offline simulator returns as is; it is kept on the topic.

### S05-R-08 Task status chain — `.rtf` SPEC-05
- Extend `Task.status` to `created|planned|assigned|in_progress|review|approved|completed` plus
  `blocked|failed|cancelled|reopened|skipped`, mapping existing values
  (`queued`→`assigned`, `running`→`in_progress`, `done`→`completed`).
- Tasks record blockers, insights and next actions from the agent's output.
- **Status: done, adapted** (`workflow.TASK_STAGES`, `set_stage`, `task_stage`; `Task.stage`, `Task.stage_log`;
  `tests/test_task_stages.py`). The stored `Task.status` values are not renamed: the scheduler, the quality gate
  and about twenty modules execute on queued|running|done|failed|skipped. The chain is a second field, `Task.stage`,
  with every change in `stage_log`: planned (or reopened when the previous iteration's task with the same key
  failed or was skipped, or a resume put a started task back) → assigned (staffed) → in_progress → review
  (acceptance conditions and contract limits) → approved (they passed) | failed → completed when the goal is
  approved. A quota, budget or recruitment skip is `blocked`, a dependency skip `skipped`, a cancelled goal's
  waiting tasks `cancelled`; a goal that pauses or fails blocks its waiting tasks, a resume plans them again;
  a cancelled or rejected goal cancels every task not yet closed. Only the latest iteration follows the goal;
  earlier iterations keep their last stage. `created` is only the default before a task is planned; rows from
  before this change read as the stage of their status (queued → planned, running → in_progress, done →
  approved). Agents name blockers as `BLOCKER:` lines (`contracts.blockers`, at most three per task, in the
  task's `gate` beside `findings` and `next_actions`); while the goal is open they are project blockers
  (`management.sync`, cause `task:<id>:blocker:<n>`), resolved when it ends or re-plans. Insights keep their
  `task_id` (S05-R-03). The goal view shows the stage (its log on hover) and the blockers; the trace has both.

### S05-R-09 PM replacement and shared-agent conflicts — §4.3, §13 (AT-024, AT-025)
- Replacing `Project.manager_id` writes a handover (SPEC-12) with open goals, blockers, action items.
- When the same agent is needed by goals of two projects, `registry.staff` prefers a free agent of
  the same role or clones; the conflict is logged.
- **Status: done** (`management.gm_handover`, called by `projects.update` inside its session after a `sync`:
  a `project_handovers` row with the open goals (not approved, rejected or cancelled; no benchmark goals), open
  blockers and open action items, never rewritten; the old GM's open blockers and action items move to the new one;
  a `handover_to` link old → new (evidence `project:<id>:gm`), an event and a message in the command window. The
  Plan tab lists the handovers, the agent card shows them beside task handovers. `registry._other_projects`: when the
  best-ranked free agent (one with `max_concurrent` > 1 and a slot left) works for a goal of another project, the
  best agent without such a goal is staffed instead, or that agent is cloned; when every agent of the role is busy
  (the default, `max_concurrent` 1) and the one to clone works for another project, the clone is the resolution.
  Either way a `staffing_conflict` event and the task's staffing record (`conflict`) name the agent, the projects
  and the resolution. Goals of one project, goals
  without a project and benchmark goals never conflict. `tests/test_project_handover.py`.)

### S05-R-10 Restart and concurrency — AT-026, AT-027, AT-030
- Covered by SPEC-04 S04-R-05; project updates use the open session (CLAUDE.md rule).
- **Status: done** through S04-R-05: a restart marks working goals `recovering` and resumes them
  (`tests/test_workflow.py::test_restart_marks_working_goals_recovering_and_they_resume`), finished tasks are not run
  again and partial edits are dropped (`tests/test_loop.py::test_restart_after_a_finished_task_does_not_run_it_again`,
  `test_a_restart_drops_the_partial_edits_of_a_task_cut_off_mid_write`), merge and push happen once
  (`loop._once`); concurrent goals of one project run in topic order (G-R-07) and wait for overlapping paths (G-R-08,
  `tests/test_leases.py`).
