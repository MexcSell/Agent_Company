# Specification: REQ-003 + REQ-004: Simple mode mission form and the mission report

## Specify Simple mode (per-user mode, mission form, Simple dashboard) and the mission report (architect, done)

# Technical specification: Simple mode (REQ-003) and the mission report (REQ-004)

Everything below was checked against the code in `agents/goal-21`. I changed no files; this text is the spec.

## 0. Facts from the code that the spec relies on
- **Current-user endpoint:** `GET /api/auth/me` (api.py:867) returns `auth.user_dict(u)` (auth.py:76).
- **Where users are created:** `auth.register` (auth.py:99). A new row is created only at auth.py:139 (`u = User(...)`). The `by_device` branch and the "existing name" branch re-bind a user that already exists.
- **Goal statuses:** `workflow.ACTIVE = ("queued","planning","running","recovering","paused")` and `workflow.FINAL = ("approved","rejected","cancelled")`. `failed` and `awaiting_approval` are in neither list.
- **Recorded evidence:**
  - Each task's acceptance results are in `Task.gate["acceptance"] = [{condition, passed, detail}]` (loop.py:1013).
  - The gate's full result list for an iteration is stored in `Event(kind="gate").data["checks"] = [{check, passed, detail, stage, findings?, command?, ...}]` (loop.py:1418). Entries are named `task <key>` (stage `contract`), `review <key>` (stage `review`), `probation review`, and one entry per command for the command stages.
  - The `review <key>` entry is the gate's recorded verdict (quality.py:242).
- **Projects:** a project has one repository `Project.path` plus `Project.github_url`. `projects.command` (projects.py:809) is the model for creating a goal in a project. `create_goal` already raises `ValueError` for an E3 stop and for `projects.refusal` (project paused, closed or over budget).

## 1. Per-user mode
- **Column:** `db.User.ui_mode: Mapped[str] = mapped_column(String(16), default="")`. The values are `''`, `'simple'` and `'advanced'`; `''` means Advanced. `_add_missing_columns` adds the column with `''`, so every existing user stays in Advanced.
- **Registration:** in `auth.register`, only the new-`User(...)` branch (auth.py:139) passes `ui_mode="simple"`. This includes the first administrator. The re-bind branches never change it.
- **Exposure:** `auth.user_dict` adds `"ui_mode": u.ui_mode or "advanced"`. `GET /api/auth/me` therefore returns it with no other change.
- **New function:** `auth.set_ui_mode(user_id: int, mode: str) -> dict`. It raises `ValueError` unless `mode in ("simple","advanced")` and returns `user_dict`.
- **New endpoint:** `PUT /api/auth/me/ui_mode`, body `UiModeBody{mode: str = Field(pattern="^(simple|advanced)$")}`.
  - It sits beside the existing `/api/auth/me` and takes the place of the `/api/me/ui_mode` named in the task.
  - It uses `Depends(current_user)` and only ever changes the calling user. No `user_id` is accepted.
  - It calls `_audit(user, "ui_mode", user["id"], before={"ui_mode": old}, after={"ui_mode": mode})` and returns `{"ui_mode": mode}`.
  - A bad value gets a 422 from pydantic.

## 2. Goal deadline
- **Column:** `db.Goal.deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)`, migrated by `_add_missing_columns`.
- **`loop.create_goal(..., deadline: datetime | None = None)`:** a naive value is treated as UTC and stored as given. `create_goal` does not check that it lies in the future; the API does.
- **`_goal_dict`:** adds `"deadline": iso | None` and `"overdue": bool`, computed the same way as projects.py:57: `deadline < now()` while the goal is in `ACTIVE`.
- **Decision:** the deadline is only reported. The loop does not pause, escalate or cancel when it passes.

## 3. Mission form: `POST /api/simple/missions`
**Dependency:** `allowed("run", "goal_create")`.

**Body `MissionBody`:**
```json
{"project_id": 3, "goal": "Add CSV export to the report page",
 "budget_usd": 5.0, "deadline": "2026-11-20T17:00:00Z",
 "notes": "We tried pandas before; keep it stdlib.",
 "resources": {"repositories": ["/srv/repos/app"], "files": ["a1b2c3"], "links": ["https://example.org/spec"]},
 "constraints": "No new dependencies."}
```

**Field rules:**
- `project_id: int`
- `goal: str` (3..4000)
- `budget_usd: float` (gt=0, le=10000)
- `deadline: datetime`
- `notes: str = ""` (≤ 8000)
- `constraints: str = ""` (≤ 4000)
- `resources: {repositories: list[str] ≤ 5, files: list[str] ≤ 20, links: list[str] ≤ 20}`

**Order of checks:**
1. **Project visibility.** `p = s.get(Project, project_id)`. Answer **404** `"unknown project"` when `p` is None, or when `_visible_project_ids(s, user)` is not None and `p.id` is not in it.
2. **Deadline.** It must be in the future, otherwise **400** `"deadline must be in the future"`. A naive value counts as UTC.
3. **Repositories.** For a non-admin, every entry must equal `p.path` or `p.github_url`. Otherwise call `_audit(user, "goal_create_denied", p.id, after={"needs": "project repository", "repositories": [...]}, severity="warning")` and answer **403** `"repositories must be the project's own"`. Administrators may name any repository, but the goal still runs on `p.path`.
4. **Files.** Each id must be an `Upload`, and for a non-admin it must also have `Upload.user_id == user["id"]`. Otherwise answer **400** `"unknown upload <id>"`. The message is the same in both cases so it does not reveal that an upload exists.
5. **Links.** Each must match `^https?://` and be ≤ 2000 characters, otherwise **400** `"invalid link <url>"`.

**Creating the goal:**
- Title: `goal.splitlines()[0][:200]`, prefixed with `"Mission: "` when it is shorter than 3 characters.
- Description: the full `goal`, then the sections `\n\n## Experience and notes\n{notes}`, `\n\n## Constraints\n{constraints}` and `\n\n## Resources\n- repository: …\n- file: <name>\n- link: …`. Empty sections are left out.
- Call `loop.create_goal(title, description, repo_path=p.path, urls=links, quality_commands=list(p.quality_commands), created_by=user["id"], project_id=p.id, attachments=projects.uploads(files), extract_checks=user["is_admin"], deadline=deadline)`.
- A `ValueError` (E3 stop, `refusal`, a refused command) becomes **400** with its text.

**Budget:** `budgets.set_budget(gid, f"user:{user['name']}", budget_usd=body.budget_usd)`. This runs before the goal is queued. If it raises `ValueError`, the goal is cancelled with `workflow.transition(gid,"cancelled","budget refused")` and the answer is **400**.

**Queue:** the same block as api.py:2105-2109 (`request.app.state.queue.submit(gid)`, otherwise a `_run_quietly` thread).

**Audit:** `_audit(user, "mission_create", gid, after={"project_id", "budget_usd", "deadline", "repositories", "files", "links"})`.

**Response:**
```json
{"id": 42, "budget": {"own": {...}, "limits": {...}, "used": {...}, "remaining": {...}}, "deadline": "2026-11-20T17:00:00+00:00"}
```
`budget` is the result of `budgets.summary`.

## 4. Simple dashboard: `GET /api/simple/overview?limit=20`
**Dependency:** `current_user`. `limit` is between 1 and 50.

**Scope:**
- Goals with `origin != "eval"`.
- Administrators see all of them.
- Other users see goals with `project_id ∈ _visible_project_ids`, plus goals without a project that they created (`created_by == user.id`).

**Response:**
```json
{"running": [{"id": 42, "title": "…", "project": {"id": 3, "name": "App"}, "status": "running",
   "iteration": 1, "progress": 0.45,
   "cost": {"used_usd": 1.2, "budget_usd": 5.0},
   "time": {"created": "…", "elapsed_s": 3600, "deadline": "…", "remaining_s": 86400, "overdue": false},
   "blockers": [{"id": 7, "title": "Goal #42 paused: budget used up", "severity": "high"}],
   "paused_for": ""}],
 "approvals": [{"kind": "merge", "goal_id": 41, "title": "…", "project": {...}, "since": "…", "can_decide": true},
               {"kind": "decision", "decision_id": 9, "goal_id": 42, "topic": "…", "can_decide": false}],
 "blocked": [{"goal_id": 42, "title": "…", "reason": "budget|cost_overrun|recruitment|blocker", "blockers": [...]}],
 "delivered": [{"id": 40, "title": "…", "project": {...}, "status": "approved", "finished": "…",
   "summary": {"verified": 3, "partial": 1, "failed": 0, "cost_usd": 2.1, "deadline_met": true},
   "report": "/api/goals/40/report"}]}
```

**How each part is filled:**
- **`running`:** goals in `ACTIVE`.
  - `progress` comes from `mission.progress(s, g)`, which applies management.progress's rule to one goal: contract weights over the current iteration's tasks that are `done`/`skipped`, times 0.9; 0.0 when there are no tasks.
  - `cost` comes from `budgets.summary`: `used.cost_usd` and `limits.cost_usd`.
  - `blockers` are `Risk` rows with `kind="blocker"`, `status="open"` and `goal_id=g.id`. Reading them does not call `management.sync`.
- **`approvals`:**
  - Goals in `awaiting_approval`.
  - Decisions with `requires_human_approval` and `status` not in `("approved","rejected","cancelled","expired")` whose goal is in scope.
  - `can_decide = policies.allows(user, "approve_goals", project_id)`.
- **`blocked`:** goals that are `paused` (reason from `checkpoint.paused_for`) or that have open blocker rows.
- **`delivered`:** goals that are finished but not `awaiting_approval`, newest first, up to `limit`. `summary = mission.summary(gid)`, a light version that never builds the full report.

## 5. Mission report: `src/agent_company/mission.py`
**Constants and functions:**
- `FINISHED = ("awaiting_approval","approved","failed","rejected","cancelled")`, which is every status not in `workflow.ACTIVE`. The module asserts that relation.
- `report(goal_id: int) -> dict`. It raises `KeyError` for an unknown goal and `ValueError("goal N is not finished")` when the status is not in `FINISHED`. It is built on request and never stored, so the report of an `awaiting_approval` goal changes once the goal is merged.
- `summary(goal_id) -> dict` and `progress(s, g) -> float`.
- `report_markdown(d: dict) -> str`.

**`report()` returns:**
```json
{"goal_id": 40, "status": "approved", "objective": "<Goal.title>\n\n<Goal.description>",
 "plan": [{"iteration": 1, "tasks": [{"key": "spec", "title": "…", "role": "architect", "depends_on": []}]}],
 "tasks": {"done": 3, "failed": 1, "retried": ["backend"],
   "items": [{"key": "backend", "title": "…", "iteration": 2, "status": "done", "label": "verified",
              "evidence": [{"type": "acceptance", "detail": "…", "passed": true},
                           {"type": "review", "check": "review qa", "passed": true, "detail": "APPROVED"}]}]},
 "verification": {"checks": [{"iteration": 2, "check": "pytest -q", "stage": "tests", "passed": true, "detail": "…"}],
                  "reviews": [{"iteration": 2, "check": "review qa", "passed": true, "detail": "APPROVED", "findings": []}]},
 "cost": {"usd": 2.1, "tokens": 51234, "budget_usd": 5.0},
 "time": {"created": "…", "finished": "…", "duration": 5400.0, "active": 1300.5, "deadline": "…", "met": true},
 "limitations": [{"kind": "defect|check|blocker", "text": "…", "ref": "defect:12"}],
 "lessons": [{"id": 88, "text": "…", "kind": "episodic", "outcome": "success"}],
 "recommendations": [{"kind": "rca_action|defect|partial_task", "text": "…", "ref": "proposal:5"}]}
```

**Where each key comes from (database records only):**
- **`plan`:** `Task` rows grouped by `iteration`.
- **`tasks.items`:** the newest Task row per key, i.e. the one with the highest iteration.
- **`done`/`failed`:** counts of those items with status `done` or `failed`.
- **`retried`:** a key is retried when it occurs in two or more iterations, or when any of its Task rows has more than one `Run` with `purpose="task"`.
- **Gate records:**
  - Read the gate records with `Event(kind="gate", goal_id)` where `"checks" in data`.
  - The iteration comes from `data["iteration"]`. The developer adds `iteration=iteration` to the `log_event` call at loop.py:1418. For older events it falls back to the regex `^iteration (\d+):` on `message`.
  - The last event of each iteration is the one that counts.
- **`verification.checks`:** gate entries whose stage is not `contract` and not `review`, from the last iteration. **`verification.reviews`:** the `review <key>` entries of every iteration.
- **`cost` and `time`:**
  - `cost.usd`/`budget_usd` come from `budgets.summary` (`used.cost_usd`, `limits.cost_usd`), so the report and the overview show the same numbers.
  - `tokens`, `duration` and `active` come from `loop.digest`.
  - `finished` is the `at` of the last `workflow.history` transition into the current status, falling back to `updated_at`.
  - `met` is `finished <= deadline`, or `null` when there is no deadline.
- **`limitations`:**
  - open defects (`performance.list_defects(status="open", goal_id=…)`)
  - failed checks of the last iteration
  - failed acceptance entries, and acceptance conditions that never ran, of the newest tasks
  - open blocker `Risk` rows
- **`lessons`:** `Lesson` rows with `goal_id` that are `status="approved"` and `kind != "operational"`. Experience reports are included through `report`.
- **`recommendations`:**
  - `rca.list_analyses(goal_id=…)[*].actions` (proposal id and title)
  - open defects ("fix <check>")
  - partial tasks ("finish and verify <key>")

**Labelling rule (per item, in its own iteration):**
- Checks: `Task.gate["acceptance"]`.
- Reviews: the `review <rk>` entries that apply to the task. Reviewer task `rk` applies to:
  - (a) the task ids in its `contract["inputs"]["tasks"]` when that list is present;
  - otherwise (b) its `depends_on` keys;
  - otherwise (c) every writing task (`writes=True`) of the same iteration;
  - and always to the reviewer task `rk` itself.
- **`failed`:** status is `failed`/`cancelled`, OR any check has `passed=false`, OR any review has `passed=false`.
- **`verified`:** status is `done`, AND every check passed, AND every review passed, AND there is at least one check or review.
- **`partial`:** everything else. This includes `done` with no evidence at all, `skipped`, `queued` and `running`.

`Task.output` and `quality.review_verdict` are never called. The only verdict used is the gate's recorded `review <key>` result, which is what makes the label independent of the agent's claim. The synthetic `task <key>` gate entry is never counted as evidence.

**`summary()`** returns `{verified, partial, failed, cost_usd, deadline_met, status}`.

**`report_markdown`** writes `# Mission report: goal #N`, then sections in this order: Objective, Plan, Tasks (a table: key | iteration | status | label | evidence count), Verification, Cost, Time, Limitations, Lessons, Recommendations. An empty section shows "None recorded."

## 6. Digest
- In `loop.digest`, every existing key stays. Add `"report": f"/api/goals/{goal_id}/report"` when `g.status not in workflow.ACTIVE`.
- `digest_line` appends `f" · report: {d['report']}"` when the key is present.
- `_chat_outcome` runs after the status has been set, so the posted line always carries the link.
- `tests/test_digest.py` still passes. Its asserts are `startswith("Digest: ")`, the substrings `"N/M task(s) passed"`, `"tokens"` and `"approval"`, and the `merge` value, and none of them change.

## 7. API: `GET /api/goals/{id}/report[?format=md]`
- **Visibility:** the same check as `GET /api/goals/{id}`. An unknown goal, or one outside the user's projects, gives **404**. A goal without a project is allowed, as it is there.
- **Not finished:** `mission.report` raises `ValueError`, which becomes **409** `"goal N is not finished"`.
- **Answer:** JSON with the `report()` dict. With `format=md`, a `PlainTextResponse(report_markdown(d), media_type="text/markdown")`.

## 8. UI (files to change)
- **New `static/simple.js`:**
  - Registers a view with `registerView({id: "simple", title: "Mission", icon: "dashboard"}, viewSimple)`.
  - The view has three parts. The form posts to `/api/simple/missions`; the project list comes from `/api/projects`, and files are uploaded through the existing upload endpoint and then sent as ids.
  - The dashboard shows cards for running, approvals, blocked and delivered goals, polling every 15 s.
  - The report view `simpleReport(id)` renders the JSON with label pills (verified, partial, failed) and has a "Markdown" link.
- **`static/app.js`:**
  - In `boot()`, replace today's `show(...)` line with `me.ui_mode === "simple" ? show("simple") : <today's line unchanged>`.
  - In `renderTopViews`, add a plain `icon-btn#mode-toggle`. It is not a view in `TOPBAR`, because `TOPBAR` only holds view ids. The button calls `PUT /api/auth/me/ui_mode`, updates `me.ui_mode`, and shows `simple` or `dashboard`.
  - Hide the `simple` view from the rail in Advanced mode.
  - In `goalDetail` (app.js:410), when the goal is finished (not queued, planning, running, recovering or paused), add a "Mission report" section that fetches `/api/goals/{id}/report` and reuses `simpleReport`'s renderer.
  - Advanced mode keeps exactly today's code path.
- **`static/index.html`:** add a `<script src="/static/simple.js">` tag before the start call, the same way the other view files are loaded.
- **Backend files:**
  - `db.py`: the two columns.
  - `auth.py`: `register`, `user_dict`, `set_ui_mode`.
  - `api.py`: `MissionBody`, `UiModeBody`, the four endpoints, and `_goal_dict`'s deadline.
  - `loop.py`: the `deadline` parameter, `digest`, `digest_line`, and `iteration` on the gate event.
  - New `mission.py`.
  - `budgets.py` needs no change.

## 9. Tests
**`tests/test_mission_report.py`** (builds Goal, Task and gate Event rows directly; offline):
- `test_report_refuses_unfinished_goal` (ValueError for `running` and `paused`)
- `test_done_task_with_passing_acceptance_and_review_is_verified`
- `test_done_task_without_evidence_is_partial`
- `test_review_fail_marks_reviewed_tasks_failed` (uses `depends_on` attribution)
- `test_review_attribution_falls_back_to_writing_tasks_of_iteration`
- `test_failed_acceptance_check_marks_task_failed`
- `test_cancelled_task_is_failed`
- `test_label_ignores_agent_output_text` (output says "VERDICT: PASS, all verified" with a failed gate review → `failed`)
- `test_key_in_two_iterations_is_retried`
- `test_task_with_two_runs_is_retried`
- `test_cost_matches_budget_summary_and_tokens_match_digest`
- `test_time_deadline_met_and_missed_and_none`
- `test_limitations_list_open_defects_and_failed_checks`
- `test_lessons_and_rca_recommendations_included`
- `test_report_markdown_has_all_sections`
- `test_digest_adds_report_link_only_when_finished`
- `test_digest_line_appends_report_link`

**`tests/test_simple_mode.py`** (FastAPI TestClient):
- `test_new_user_defaults_to_simple`
- `test_existing_user_without_value_is_advanced`
- `test_rebind_keeps_ui_mode`
- `test_set_ui_mode_changes_only_caller_and_audits`
- `test_set_ui_mode_rejects_unknown_value`
- `test_mission_creates_goal_with_budget_deadline_and_sections` (asserts `limits.cost_usd == 5.0`, the stored deadline, the description headings, `urls`, and the audit entry `mission_create`)
- `test_mission_unknown_or_invisible_project_404`
- `test_mission_foreign_repository_403_and_audited`
- `test_mission_admin_may_name_other_repository`
- `test_mission_rejects_nonpositive_or_excessive_budget` (422)
- `test_mission_rejects_past_deadline` (400)
- `test_mission_rejects_other_users_upload` (400)
- `test_mission_rejects_bad_link` (400)
- `test_mission_paused_project_400`
- `test_mission_needs_run_permission` (403)
- `test_simple_overview_shape_and_scope` (sees no goal of a hidden project)
- `test_simple_overview_lists_awaiting_approval_and_delivered_summary`
- `test_goal_report_endpoint_409_while_running`
- `test_goal_report_endpoint_json_and_markdown`
- `test_goal_report_endpoint_404_for_hidden_project`

---

FINDING: once a new account (the first administrator included) starts in Simple mode, headless UI scripts that expect the Advanced dashboard after registration have to click the mode toggle or call `PUT /api/auth/me/ui_mode` first.

FINDING: review verdicts are recorded per iteration, not per task, so §5 sets an attribution rule (contract inputs → `depends_on` → the iteration's writing tasks). QA should hold the backend to exactly that rule.

NEXT: the backend developer implements §1–§7 and §9 (`mission.py`, `db.py`, `auth.py`, `loop.py`, `api.py`, the tests).

NEXT: the frontend developer implements §8 (`static/simple.js`, `app.js`, `index.html`).

MESSAGE handoff to developer: the spec above is complete. Watch three points: the gate event needs `iteration=iteration` at loop.py:1418, labels must read `Event(kind="gate").data["checks"]` and never `Task.output`, and the setter is `PUT /api/auth/me/ui_mode`.

MESSAGE fact to qa: "finished" means `status not in workflow.ACTIVE` (approved|awaiting_approval|failed|rejected|cancelled); "done" with no recorded check or review must be labelled `partial`.

PROPOSAL: Persist the iteration on gate events :: `Event(kind="gate")` has no iteration field today, so reports and traces have to parse the message text to find it.
