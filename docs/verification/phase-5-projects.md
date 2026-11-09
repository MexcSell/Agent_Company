# Verification phase 5: project registration

Date: 2026-11-09. Question: can a user register a project, is it stored, reopened, updated and kept apart from
another project, and are agents associated with each project correctly? Statuses as the plan defines them: PASS,
FAIL, BLOCKED, PARTIAL, NOT TESTED.

## The plan's fields on this platform

| Plan's field | TEST-PROJECT-001 value | This platform |
|--------------|------------------------|---------------|
| Project name | TEST-PROJECT-001 | `Project.name` (unique, any case) |
| Description | Simple calculator application | `Project.description` |
| Project type | Software development | No such field: every project is a software project in a git repository. The nearest is `stack` (`["python"]`) |
| Status | Registered | `Project.status` starts `active`; then paused, archived, cancelled, completed (`PROJECT_TRANSITIONS`) |
| Assigned manager | Project Manager Agent | `Project.manager_id`, the project's General Manager, with its position history `project:<id>:gm`; a new one gets a handover package |
| Assigned developer, QA | Developer Agent, QA Agent | No staffing assignment: workers are picked per task from the project's participating rooms (`Project.rooms`). A topic's agent list (`Topic.agent_ids`, the general topic for the whole project) records who is meant, and staffing ignores it (V22) |

## Automated tests

`tests/test_phase5_projects.py` (6 cases, offline) uses the real API (`create_app`, device sign-in) with an
administrator and a member who may create projects. Project goals run through the real loop on phase 4's stub
providers. Run: `uv run pytest -q tests/test_phase5_projects.py`.

### 5.1 / 5.2 Registration and storage

| Check | Pass condition | Evidence | Result |
|-------|----------------|----------|--------|
| Project ID generated, information saved | The member registers TEST-PROJECT-001 through `POST /api/projects`; an integer id comes back. Description, stack, rooms, status `active`, creator and creation time are stored | `test_a_project_is_registered_stored_and_survives_a_new_session` | PASS |
| Persists after refreshing | A new app instance and a new sign-in (`GET /api/projects/{id}`, `/topics`, `/api/projects`) return the same values from the database | same | PASS |
| Assigned manager | `manager_id` = the General Manager, and the position history names it as holder | same | PASS |
| **The assigned manager does the managing** | TEST-PROJECT-002 is given its own General Manager. **Its goals were planned by the company GM** (agent 1, not 22): `manager_id` was recorded but not used for staffing. Now the planner, the `.claude/` configure step and the root-cause analysis of a project's goal take the project's own manager (a clone of it while it is busy) | `test_the_projects_own_manager_plans_its_goals` (fails without the fix) | FAIL → PASS (V21) |
| Assigned developer and QA | Stored on the general topic and returned; they decide nothing (V22). The project's rooms decide which agents may be staffed. Every core developer and QA agent is in the `development` room, so two projects cannot get different developers today | `test_a_project_is_registered…`, `test_project_assignments_are_independent` | PARTIAL (V22) |
| Status correct, reopened, updated | Description changed with `PATCH` and stored. Statuses go paused → active → archived → active → completed. A completed project cannot be reopened, only archived, and takes no new goals (the order is kept, no goal starts) | `test_a_project_is_updated_paused_reopened_archived_and_reopened` | PASS |

### 5.3 Isolation

| Check | Pass condition | Evidence | Result |
|-------|----------------|----------|--------|
| 001 cannot access 002's tasks | 001's member gets 404 for 002's project, messages, topics, goal and command window, and does not see 002's goal in `/api/goals` or 002 in `/api/projects` | `test_one_projects_user_cannot_reach_the_others_project_goals_or_messages` | PASS |
| 002 cannot modify 001's files | Each project has its own repository (`data/workspaces/project-<id>`). A goal of one project whose developer writes `../../…/project-<other>/calculator.py` is refused ("refusing to write outside the worktree"): the run fails and the other project's file is unchanged | `test_one_projects_goal_cannot_write_the_other_projects_files` | PASS |
| Conversation history separate | Each project's messages and topics carry its own `project_id`; the same command in both stays in each one's window | first isolation test | PASS |
| Agent assignments independent | Changing 002's manager and topic agents leaves 001's unchanged. When both projects need the same agent at once, staffing gives the second a free agent or a clone and logs a `staffing_conflict` (S05-R-09) | `test_project_assignments_are_independent`, `tests/test_project_handover.py` | PASS |
| One goal's context never reaches another's | Blackboard sections are per goal (`tests/test_blackboard.py::test_a_task_sees_its_own_goals_sections_and_never_another_goals`); each goal has its own worktree and branch | as named | PASS |

### Not run live

Phase 5 ran only offline, on the real code and the real API. Registering TEST-PROJECT-001/002 on this server would
put them on the owner's Projects page. The live server's database stores exactly what the tests stored. Phase 6
registers a live project (TEST-PROJECT-003), because that phase needs one.

## Findings

- **V21 (fixed):** a project's General Manager (`Project.manager_id`) was a record without effect. The command
  window, escalations and handovers used it, but its goals were planned by whichever GM ranked best. With one GM
  agent this never showed; with a second GM assigned to a project, it did. `registry.staff(prefer=…)` now gives the
  work to the preferred agent when it is in the pool, healthy and within its quota (a clone of it while it is busy).
  `loop._plan`, `loop._configure` and `rca` pass the project's manager.
- **V22 (open, owner):** the platform assigns workers per task within the project's rooms. A topic's agent list
  (the plan's "assigned developer/QA") is shown, but staffing ignores it. Every core agent is in `development`, so
  rooms cannot give two projects different developers either. Making the list constrain staffing is a behaviour
  change (a busy listed agent, an agent listed in two projects) that needs the owner's decision. Not changed.
- **V20 (phase 4, fixed here):** `executors.PYTEST_FORMS` puts `python3 -m pytest` and `python3 -B -m pytest`, the
  forms the GM writes in acceptance commands, on the developer's and QA's command lists; `python3 -B -c` stays
  refused.

## Phase 5 acceptance

| Criterion | Result |
|-----------|--------|
| Created | PASS |
| Saved (survives a new session) | PASS |
| Reopened | PASS (paused and archived reopen; completed and cancelled are final by design) |
| Updated | PASS |
| Isolated from other projects | PASS |
| Agents associated correctly | Manager: PASS after V21. Developer/QA: PARTIAL (V22) |

## Phase 6 starts from here

Phase 6 (project task execution): TEST-PROJECT-003, the simple calculator, live, with the PM, developer and QA, the
task records and their state machine.
