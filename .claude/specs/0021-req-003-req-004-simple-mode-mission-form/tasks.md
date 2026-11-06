# Tasks (iteration 1)



## 1. Specify Simple mode (per-user mode, mission form, Simple dashboard) and the mission report
- key: `spec` · role: architect · writes: no

Read: `src/agent_company/loop.py` (`create_goal` at ~37, `_chat_outcome`/`digest`/`digest_line` at ~1250-1305); `budgets.py` (`set_budget`, `summary`, `limits`); `db.py` (`Goal`, `Task`, `Run`, `User`, `_add_missing_columns`); `api.py` (`GoalBody` ~199, `POST /api/goals` ~2089, `GET /api/goals/{id}` ~2114, `/api/ui` ~3285, `_audit`, `_visible_project_ids`, the `/api/overview` handler); `auth.py` (where a user is registered); `management.py` (blockers, progress); `quality.py`/`checks.py` (how check results and reviewer verdicts are recorded per task, e.g. `Task.gate`); `workflow.py` (`ACTIVE`/`FINAL`); `decisions.py` (approvals waiting for a person); `performance.py` (open defects); `learning.py`/`rca.py` (knowledge items and RCA actions tied to a goal); `static/app.js` (how the dashboard/overview view is built and routed); `static/projects.js` (goal view). Write ONE technical spec with these sections:
1. **Per-user mode**: column `User.ui_mode` (String(16), default ''; 'simple'|'advanced'; '' = advanced). Name the registration function that sets 'simple'. `GET /api/me` (or the existing current-user endpoint, whichever exists) returns it. `PUT /api/me/ui_mode` {mode} sets it for the calling user only and calls `_audit`.
2. **Goal deadline**: add `Goal.deadline` (DateTime tz, nullable) and a `deadline` parameter to `loop.create_goal`. State whether the loop only reports it or also pauses/escalates when it is passed. Recommendation: only report it in this goal; do not change loop control.
3. **Mission form**: `POST /api/simple/missions` with body {project_id (required, must be visible to the user), goal (title/description), budget_usd (>0, ≤10000), deadline (ISO, must be in the future), notes (experience/notes), resources {repositories: [...], files: [upload ids or paths of the project's uploads], links: [urls]}, constraints (text)}. Use permission `allowed("run","goal_create")`. A non-admin's repositories must belong to the chosen project's repos, or be refused with 403, matching the rule in `goal_create`. The goal is created through `loop.create_goal(project_id=..., urls=links, ...)`; notes and constraints go into the description under clear headings; the budget is set through `budgets.set_budget(gid, actor, budget_usd=...)`. The goal is queued exactly as `goal_create` queues it. `_audit` is called. Spell out the validation errors (400) and the visibility error (404).
4. **Simple dashboard**: `GET /api/simple/overview`, scoped to the projects the user can see. It returns `running` (active goals with title, project, status, progress 0-1 from tasks done/total of the current iteration or `management` progress, cost used vs budget from `budgets.summary`, elapsed vs deadline, blockers), `approvals` (goals in awaiting_approval plus decisions that wait for a person), `blocked`, and `delivered` (finished goals with their report summary and a link). Give exact JSON shapes.
5. **Mission report** in a new module `src/agent_company/mission.py`: `report(goal_id) -> dict`, built only from DB records, with keys objective, plan (tasks per iteration: key, title, role, depends_on), tasks {done, failed, retried (keys that ran in more than one iteration or were retried), items: [{key, title, iteration, status, label: verified|partial|failed, evidence: [...]}]}, verification {checks: [...], reviews: [...]}, cost {usd, tokens, budget_usd}, time {created, finished, duration, active, deadline, met}, limitations (open defects, failed/unrun checks, open blockers), lessons (knowledge items/experience reports of the goal), recommendations (RCA actions, open defects, partial tasks to finish). Labelling rule: verified = done AND every recorded check passed AND every recorded review verdict is PASS AND at least one check or review exists; failed = status failed/cancelled OR a check failed OR a review is FAIL; partial = otherwise. Never read the agent's output text to decide a label. `report()` raises ValueError for a goal that is not finished. Finished means status in approved|awaiting_approval|failed|rejected|cancelled; confirm the exact set from workflow.py. Also `report_markdown(d) -> str`.
6. **Digest**: `digest()` gains `report: "/api/goals/{id}/report"` when the goal is finished. `digest_line` appends ` · report: /api/goals/{id}/report`. Existing keys stay unchanged so `tests/test_digest.py` still passes.
7. **API**: `GET /api/goals/{id}/report` (same visibility check as `GET /api/goals/{id}`; 409 when the goal is not finished; `?format=md` returns markdown).
8. **UI**: a new `static/simple.js` with the form, the dashboard and a report view. `app.js` shows Simple when `ui_mode=='simple'` and has a Simple/Advanced toggle in the top bar (`TOPBAR`). Advanced renders exactly today's code path. The goal view shows a 'Mission report' section for finished goals. List each file to change.
9. **Tests**: name the test functions for `tests/test_mission_report.py` and `tests/test_simple_mode.py` (normal, edge and error cases: label rules including 'done but no evidence → partial', a review FAIL → failed, a retried key; mission form validation, 403 repo, 404 project, budget applied, deadline stored; ui_mode default for a new vs an existing user; overview shape; report 409 for a running goal).
Keep it concrete: signatures, field names, JSON examples. No code beyond short signatures.

Acceptance: {"type": "min_length", "value": 2500}; {"type": "contains", "value": "mission.py"}; {"type": "contains", "value": "ui_mode"}; {"type": "contains", "value": "/api/simple/missions"}; {"type": "regex", "pattern": "(?i)partial"}

## 2. Backend: mission.py report, digest link, User.ui_mode, Goal.deadline, /api/simple/* and report endpoints, tests
- key: `backend` · role: developer · writes: yes · after: spec

Implement the spec from task 'spec' (in the blackboard/handover) for the backend only.
- `db.py`: add `User.ui_mode` (String(16), default '') and `Goal.deadline` (DateTime tz, nullable); `_add_missing_columns` migrates existing databases.
- `auth.py`: a newly registered user gets ui_mode 'simple'; an existing user's '' is treated as 'advanced'.
- `loop.create_goal`: a `deadline` parameter.
- New `src/agent_company/mission.py`: `report(goal_id)` and `report_markdown(d)`. Labels come only from recorded check results and review verdicts, never from task output text. 'done' with no recorded evidence is 'partial'.
- `loop.digest`: add a `report` link for finished goals. `digest_line`: append the report link. Existing keys and test_digest stay green.
- `api.py`: `GET/PUT` ui_mode for the calling user (audited); `POST /api/simple/missions` (validation, project visibility, a non-admin cannot name a repository outside the project, budget through `budgets.set_budget`, queued like `goal_create`, audited); `GET /api/simple/overview`; `GET /api/goals/{id}/report` (409 while running, `?format=md`).
- DB writes inside an open session use that session.
- Tests: `tests/test_mission_report.py` and `tests/test_simple_mode.py` as listed in the spec, offline with their own DATA_DIR like the existing tests. Build goals/tasks/runs directly in the DB; do not run the loop.
- Update the CLAUDE.md layout line for `mission.py`, and mark REQ-003/REQ-004 progress in the docs if a requirements file lists them.
Run `uv run pytest -q tests/test_mission_report.py tests/test_simple_mode.py tests/test_digest.py tests/test_budgets.py tests/test_api.py` before you hand back.

Acceptance: {"type": "file_exists", "path": "src/agent_company/mission.py"}; {"type": "file_exists", "path": "tests/test_mission_report.py"}; {"type": "file_exists", "path": "tests/test_simple_mode.py"}; {"type": "command", "run": "uv run pytest -q tests/test_mission_report.py tests/test_simple_mode.py tests/test_digest.py", "requirement_keys": ["REQ-003", "REQ-004"], "stage": "unit"}; {"type": "command", "run": "uv run pytest -q tests/test_api.py tests/test_budgets.py", "requirement_keys": ["REQ-003"], "stage": "integration"}

## 3. Frontend: static/simple.js (form, dashboard, report) and the Simple/Advanced switch in app.js
- key: `frontend` · role: developer · writes: yes · after: spec, backend

Implement the spec's UI section in plain JS, following the style of the existing static files.
- New `static/simple.js`: the mission form (project select from the projects the user can see, goal title/description, budget USD, deadline datetime, experience/notes, resources: repositories/files/links, constraints) posts to `/api/simple/missions`. The Simple dashboard reads `/api/simple/overview` and shows running goals with a progress bar, cost vs budget, time vs deadline, blockers, waiting approvals (linking to the existing approval UI) and delivered results with their mission report (`/api/goals/{id}/report`).
- `index.html`: load simple.js with the same content-hashing as the other static files.
- `app.js`: read the user's ui_mode; in 'simple' render the Simple view as the home view, else today's dashboard through the unchanged code path. Add a Simple/Advanced toggle to `TOPBAR` that calls `PUT /api/me/ui_mode`. Remove nothing.
- Goal view (`projects.js` or wherever a goal is shown): for finished goals, a 'Mission report' section with every report section and a verified/partial/failed badge per task.
- Escape all goal and task text when rendering, the same way the existing files do.
Check with `node --check` on each changed JS file.

Acceptance: {"type": "file_exists", "path": "src/agent_company/static/simple.js"}; {"type": "command", "run": "node --check src/agent_company/static/simple.js && node --check src/agent_company/static/app.js", "requirement_keys": ["REQ-003"], "stage": "static"}; {"type": "command", "run": "grep -q 'simple.js' src/agent_company/static/index.html && grep -q '/api/simple/missions' src/agent_company/static/simple.js && grep -q '/report' src/agent_company/static/simple.js", "requirement_keys": ["REQ-003", "REQ-004"], "stage": "static"}

## 4. QA review of Simple mode and the mission report against REQ-003 and REQ-004
- key: `qa` · role: qa · writes: no · after: backend, frontend

Review the changes of tasks 'backend' and 'frontend' against the spec and the acceptance criteria of REQ-003 and REQ-004. Read the code; do not depend on running anything. For each requirement, trace the data flow for a normal case, an edge case and an error case:
(a) A mission is started from the form with budget and deadline → goal created in the project, `budgets.set_budget` applied, deadline stored, queued, audited.
(b) Edge: a new user gets Simple and an existing user keeps Advanced; Advanced renders today's dashboard unchanged and nothing is removed.
(c) Error: an invisible project, a past deadline, a non-admin naming a foreign repository.
(d) Report: every section is present for approved, failed and cancelled goals. Labels come only from recorded checks and review verdicts, never from task output. Done with no evidence → partial. A review FAIL → failed. Retried tasks are counted. 409 for a running goal. The digest line links the report and old digest keys are unchanged.
(e) Security: visibility checks on `/api/goals/{id}/report` and `/api/simple/overview`; `PUT ui_mode` only changes the caller; rendered text is escaped; `_audit` is called on the admin-relevant actions.
Also check that the tests really cover these cases. List each problem as `FINDING: <file:line> | <severity> – <issue>`. End with `OUTCOME: ...` and `VERDICT: PASS` or `VERDICT: FAIL`.

Acceptance: {"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}; {"type": "contains", "value": "OUTCOME"}; {"type": "min_length", "value": 400}

## 5. Security review of 'Backend: mission.py report, digest link, User.ui_mode, Goal.deadline, /api/simple/* and report endpoints, tests'
- key: `backend-security` · role: security · writes: no · after: backend

Review the change made by task 'backend' for security: secrets, injection, unsafe file and network access, authentication and permission changes. Verify the logic first.

Acceptance: {"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}
