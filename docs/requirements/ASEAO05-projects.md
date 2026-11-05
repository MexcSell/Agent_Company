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
| S05-FR-05 | Select participating departments | open → S05-R-06 |
| S05-FR-06 | Required skills and capabilities | open → S05-R-06 |
| S05-FR-07 | Environment, repository, stack, working directory | partial: path and GitHub URL; stack open → S05-R-06 |
| S05-FR-08 | Budgets, deadlines, priorities, security classification | open → S05-R-06 |
| S05-FR-09 | Pause, resume, archive, cancel, complete | partial: `/stop` only → S05-R-06 |

## Specific requirements

Status (2026-11-05): S05-R-01 done · S05-R-02 done · S05-R-03 done · S05-R-04 done · S05-R-05 partial ·
S05-R-06 open · S05-R-07 done · S05-R-08, R-09 open · S05-R-10 done (SPEC-04).

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
- **Status: partial** (`management.goal_time`: active = Σ run durations, waiting = queued + awaiting
  approval, blocked = paused, from `goal_transitions`; `management.progress` with contract `weight`;
  overdue milestones and action items notify once, on the next sync). Open: the planner's time estimate
  per task (contracts have no estimate field yet) and showing goal times on the goal page.

### S05-R-06 Project registration fields and lifecycle — FR-05..09
- Add to `Project`: `stack` (list), `required_skills` (list), `rooms` (list of room keys),
  `budget_usd`, `deadline`, `priority`, `classification` (SPEC-14 S14-R-05), `status`
  (`active|paused|archived|cancelled|completed`).
- Paused/archived projects accept no new goals; cancel cancels running goals (exists as `/stop`).
- Project budget caps the sum of its goals' budgets (SPEC-04 S04-R-09).

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

### S05-R-09 PM replacement and shared-agent conflicts — §4.3, §13 (AT-024, AT-025)
- Replacing `Project.manager_id` writes a handover (SPEC-12) with open goals, blockers, action items.
- When the same agent is needed by goals of two projects, `registry.staff` prefers a free agent of
  the same role or clones; the conflict is logged.

### S05-R-10 Restart and concurrency — AT-026, AT-027, AT-030
- Covered by SPEC-04 S04-R-05; project updates use the open session (CLAUDE.md rule).
