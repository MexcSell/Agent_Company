# Review (iteration 1)

## QA review of Simple mode and the mission report against REQ-003 and REQ-004 (qa, done)

I'm approving this with comments (OUTCOME: APPROVED_WITH_COMMENTS, VERDICT: PASS). Requirements (a) to (e) all hold when I trace them through the code, and I found no security or correctness defect serious enough to block the merge. I reviewed by reading only: I ran neither `node --check` nor pytest, so the frontend developer's note that `simple.js` and `app.js` were never syntax-checked still stands. I read them closely and saw no syntax error.

## Logic verification

**(a) Starting a mission — `mission_create`, api.py:2195**
- **Normal case:** the endpoint uses the same "run" permission as the existing goal-create endpoint. It checks the project with `_visible_project_ids`, takes the project's own repository and quality commands, and calls `loop.create_goal(project_id, budget_usd, deadline, urls, attachments)`.
  - The deadline is stored on `Goal.deadline` (db.py), and `budgets.set_budget` is called at api.py:2239.
  - The audit record `mission_create` names the new goal, and the goal is queued the same way as in `goal_create`.
  - `test_mission_creates_goal_with_budget_deadline_and_sections` checks the queued status, the budget limit of 5.0, the deadline, the notes and constraints sections, and the audit target.
- **Edge cases:**
  - A deadline without a timezone is treated as UTC. The form sends `new Date(v).toISOString()`, so the user's local time arrives correctly.
  - A paused project is refused by `create_goal`, which raises `ValueError` and returns 400 (tested).
- **Error cases:**
  - An unknown or invisible project gives 404.
  - A deadline in the past gives 400.
  - A non-admin naming a repository other than the project's own (compared after `resolve()`) gives 403, with a `mission_create_denied` warning in the audit log and no goal created.
  - Another user's upload, a missing upload, or a `javascript:`/`file:`/`ftp:` link gives 400.
  - A budget of 0 or less, or above 10,000, gives 422.
  - All of these are tested.
- **Root cause:** this is a real code path to `budgets` and the queue, not a front-end-only change.

**(b) Simple and Advanced modes — auth.py:76–100 and app.js:196/207/252**
- **New users:** the only place a `User` is created is auth.py:160, and it sets `ui_mode="simple"`. Re-binding an existing account keeps its mode (`test_rebind_keeps_ui_mode`).
- **Existing users:** a value of `''` or NULL is outside `UI_MODES`, so `ui_mode_of` returns `advanced`.
- **Advanced is unchanged:** the app.js diff only adds code.
  - `boot()` runs the previous line unchanged for Advanced users.
  - `navGroups` hides only the `simple` view.
  - The toggle is inserted after `TOPBAR` is rendered; `show()` then calls `renderRail` and `renderTopViews`, so the toggle's label and target are always current.
  - `show()` calls the view with `quiet` undefined, so `wireMissionForm` runs again after a cached snapshot is restored. Form handlers are not lost.

**(c) Error handling:** covered under (a). For an unfinished goal, the report raises `ValueError`, which becomes 409. For an unknown or hidden goal it raises `KeyError`, which becomes 404 through the exception handler at api.py:854.

**(d) Mission report — `mission.report`, mission.py:74–180**
- **Where labels come from:** only from `Task.gate["acceptance"][*].passed` and from gate event rows named `task <key>` (failures only) and `review <key>`.
  - The review verdict is the one `quality.py:241-250` recorded when the gate ran. `Task.output` is never read (`test_label_ignores_agent_output_text`).
- **Label rules:**
  - Done with no evidence gives `partial`. A passing `task build` row alone does not count as evidence (tested).
  - A review FAIL gives `failed`, and so does a failed acceptance check or a failed or cancelled status.
- **Retried tasks:** a task key that appears in more than one iteration, or a task with more than one run, is counted (both tested).
- **Report link in the digest:** `digest` adds `report` only when the status is not in `workflow.ACTIVE`. The old keys are unchanged, `digest_line` only appends to the end, and `test_digest` stays green.
- **Older gate events:** events written before this change have no `iteration` field; `_gates` falls back to the `iteration N:` message from loop.py:1421, the only gate event that carries `checks`.
- **Sections:** objective, plan, tasks, verification, cost, time, limitations, lessons and recommendations are always present in both the JSON and the Markdown.

**(e) Security**
- **Visibility:** `/report` and `/simple/overview` use the same visibility rule as `GET /api/goals` and `GET /api/goals/{id}`, including goals with no project. That is not a new exposure, and eval goals are excluded.
- **`PUT ui_mode`:** it uses `user["id"]` from the session only and is audited as `ui_mode_set`.
- **Escaping:** every value from the server goes through `esc`, through `pill` (which escapes), through `Number()`, or into `textContent`/`toast`.
- **Audit:** `_audit` is called for `mission_create`, `mission_create_denied` and `ui_mode_set`. A missing run permission is audited as `goal_create_denied` by `allowed()`.

## Checklist
- **Contract:** met.
- **Interfaces:** compatible; only additive keys.
- **Error handling:** good.
- **Edge cases and tests:** see the findings below.
- **Dependencies:** none added.
- **Security:** OK.
- **Performance:** see the findings below.
- **Docs:** `CLAUDE.md` and ADRs 0016/0017 are updated.
- **Threat model:** no change needed; there is no new executor or network path.

## Findings

FINDING: src/agent_company/mission.py:115 | Real acceptance records are `{"condition": {"type": …}, "passed", "detail"}` (contracts.py:179), so `c.get("type") or c.get("check")` is always None and every piece of acceptance evidence in a real report is named just "acceptance". The labels are still right, but the report cannot say which condition passed. The test fixture `OK = [{"type": "command", …}]` (tests/test_mission_report.py:43) does not match the real shape, which hides this. | Read `(c.get("condition") or {}).get("type")` first and change the fixture to the shape `contracts.check_acceptance` produces.

FINDING: src/agent_company/api.py:2259, api.py:2281, api.py:2304 | `/api/simple/overview` calls `budgets.summary` (its own session) for up to 300 goals and the full `mission.report` (several queries plus `rca.list_analyses`) for up to 20 delivered goals on every call. It is the default landing page for new users and runs every 15 s plus on live-update refreshes, so it slows the dashboard and holds SQLite locks for every user with it open. | Build cards only for the goals that are shown (running, approvals, blocked, the first 20 delivered), compute budgets in one grouped query, and store or memoise the label counts per finished goal.

FINDING: src/agent_company/api.py:2259 | The newest 300 goals are taken before the visibility filter, so on a busy install a non-admin's running goals can be missing from their own Simple dashboard. | Filter by `project_id IN seen OR project_id IS NULL` in the SQL, then apply the limit.

FINDING: src/agent_company/api.py:2296-2306 | A goal waiting for approval is listed both under "Waiting for approval" and under "Delivered". "Delivered" also contains failed, rejected and cancelled goals, which is misleading for a Simple-mode user, and `test_simple_overview_lists_awaiting_approval_and_delivered_summary` (tests/test_simple_mode.py:202) asserts the double listing. | Leave `awaiting_approval` out of "delivered", or rename the group to "Finished" and show the status prominently.

FINDING: src/agent_company/mission.py:74 | A goal-level gate check that fails (for example the project's `pytest`) does not affect any task's label, so a task can be "verified" in an iteration whose gate failed. It does appear under limitations. | Either document this in ADR 0017 or, for the last iteration, label otherwise-verified tasks "partial" when the gate failed.

FINDING: src/agent_company/mission.py:71 | A review FAIL with nothing to attribute to (no contract inputs, no dependencies, no writing tasks in the iteration) is silently attached to no task. Separately, every reviewer task without its own acceptance checks is "partial" and adds a "verify task qa …" recommendation. | When the target list is empty, apply the verdict to all non-reviewer tasks of the iteration, and leave reviewer tasks out of the partial recommendations.

FINDING: tests/test_mission_report.py:207 | The every-section test only covers the default (approved) goal. Nothing checks that a goal with status `failed` or `cancelled` yields every JSON and Markdown section, or that `/simple/overview` hides a delivered goal of an invisible project. | Parametrize `test_report_markdown_has_all_sections` over approved, failed and cancelled, and add a hidden delivered goal to `test_simple_overview_shape_and_scope`.

FINDING: src/agent_company/static/app.js:196 | An Advanced-mode user whose stored last view is `simple` (they switched mode in another browser) is reopened on the Simple view, which is not on their rail. | Accept `current === "simple"` only when `me.ui_mode === "simple"`.

FINDING: src/agent_company/static/simple.js:1, app.js:196 | `node --check` has not run on either file in this goal; I only read them. | Let the gate's `static` stage, or the next session that can run `node --check`, confirm the syntax.

NEXT: The backend developer fixes the evidence name in mission.py:115 and its test fixture, and makes the overview query cheaper (the first two findings).
NEXT: Run `node --check` on `static/simple.js` and `static/app.js`, then open the dashboard headless as a new user (Simple) and as an existing user (Advanced).

MESSAGE recommendation to developer: in src/agent_company/mission.py:115, read the acceptance condition type from `c["condition"]["type"]` (the shape is in contracts.py:179) and change the `OK` fixture in tests/test_mission_report.py:43 to match.

OUTCOME: APPROVED_WITH_COMMENTS
VERDICT: PASS

PROPOSAL: Cache label counts per finished goal :: Simple overview and RCA could read stored label counts instead of rebuilding the full report every 15 s.

```json
{"insights": [{"title": "Test fixtures must mirror the recorded data shape", "description": "mission.py reads acceptance records in a shape the loop never writes; the tests passed only because the fixture used the same wrong shape.", "evidence": ["contracts.py:179 writes {condition, passed, detail}", "tests/test_mission_report.py:43 uses {type, passed, detail}", "mission.py:115 reads c.get('type')"], "expected_benefit": "Fewer silent gaps between the report and real data", "confidence": 0.8}]}
```

## Security review of 'Backend: mission.py report, digest link, User.ui_mode, Goal.deadline, /api/simple/* and report endpoints, tests' (qa, done)

I couldn't run the tests myself: both attempts to run `pytest` needed an approval I don't have, so the developer's "54 passed" is unconfirmed. My verdict rests on reading the code and comparing it with the existing goal-creation paths.

## Logic verification

**`POST /api/simple/missions` (`mission_create`, api.py:2194)**
- **Normal case:** a project the user can see (`_visible_project_ids`) gives `p.path` and `p.quality_commands`. The text sections are joined, then `loop.create_goal(... project_id, budget_usd, deadline)` and `budgets.set_budget` run, the call is audited as `mission_create`, and the goal is queued.
  - This matches the project command window (`projects.py:809`): non-admins already start goals with the project's own repository and checks, and `extract_checks=user["is_admin"]` is the same.
  - The permission check is stricter: `allowed("run","goal_create")` instead of `can(user,"run")`.
- **Edge case:** a non-admin names the project's own repository (also with `~`, or `..` that resolve to it). After `resolve()` it compares equal and is allowed. If the project has no `path`, every named repository is "foreign" and gets a 403. That is correct; otherwise a non-admin could add any git repository on the host, which `goal_create` (api.py:2128) forbids.
- **Error cases:**
  - A hidden project → 404. A paused, closed or over-budget project → `create_goal` raises (`refusal`, loop.py:115) → 400.
  - A refused quality command → ValueError → 400. A past deadline → 400. A budget ≤0 or >10,000 → 422 (pydantic).
  - Uploads: `own.get(f,-1) != user["id"]` rejects other users' uploads and ownerless ones (stricter than the command window, which checks no ownership).
  - Links must match `^https?://`, so `file:`, `javascript:` and similar schemes are refused.
- **Root cause or symptom:** this adds a new endpoint and opens no new privilege. Every input that `goal_create` restricts to admins (repository, quality commands, permits) stays restricted.

**`GET /api/goals/{id}/report` (api.py:2177)**
- **Normal case:** the same visibility rule as `GET /api/goals/{id}`, then `mission.report`.
- **Edge case:** `format=md` returns `text/plain`-style markdown, not HTML, so agent text is not rendered as markup.
- **Error cases:** unknown or hidden goal → `KeyError` → 404 (no difference that would leak a goal's existence); an active goal → `ValueError` → 409.
- The report exposes less than `GET /api/goals/{id}` already does (full events, task output), so nothing new leaks.

**`mission.report` / `label` (mission.py)**
- **Normal case:**
  - Acceptance evidence comes from `Task.gate["acceptance"]` (only entries that have `passed`).
  - Gate rows `review <key>` and `task <key>` match the names that quality.py writes (lines 238, 245, 250).
  - Reviews are attributed in the spec's order: contract inputs, then `depends_on`, then the iteration's writing tasks.
  - `Task.output` is never read, so the label cannot come from an agent's claim (REQ-004 is met).
- **Edge case:** a task that is `done` with no evidence → `partial`, and it is listed under limitations and recommendations. `_gates` keeps only the last gate of each iteration and skips gate events without `checks` (such as the "already failing before any change" event).
- **Error cases:** an unknown goal → KeyError; an active goal → ValueError.
- **Gap:** a failing goal-wide check (for example the project's test command) does not mark any task `failed`. It only appears under limitations. See the findings.

**`auth.set_ui_mode` and `PUT /api/auth/me/ui_mode`**
- **Normal case:** the endpoint passes `user["id"]`, so a caller can only change their own mode, and the change is audited.
- **Edge case:** an account from before the setting existed has `''`, which counts as `advanced`. Only a newly created `User` row gets `simple` (auth.py:161); re-binding an existing account leaves it alone.
- **Error case:** any other value → `AuthError(400)`, which the existing handler at api.py:846 turns into a response. The pydantic field caps the value at 16 characters.

**`loop.digest` / `digest_line` and the `iteration` field on the gate event**
- These add a `report` link only when the status is not in `workflow.ACTIVE`, and record which iteration a gate belongs to. Read-only, with no effect on security.

## Checklist
- **Contract:** met for the backend part of REQ-003 and REQ-004.
- **Interfaces:** new fields only (`ui_mode`, `deadline`); `create_goal` takes a new keyword with a default, so existing callers are unaffected.
- **Error handling:** 400, 403, 404, 409 and 422 as listed above, with the 403 refusal audited.
- **Edge cases:** mostly covered; the gaps are in the findings.
- **Tests:** the 20 Simple-mode tests cover the security paths: the 404 for a hidden project, the audited 403 for a foreign repository, another user's upload, a bad link, a paused project, the run permission, the overview's scope and the report's 404 and 409. I could not run them myself.
- **Dependencies:** none added.
- **Secrets:** none in the code. Text in the report is cut to 300 or 500 characters and already appears in the existing goal endpoint.
- **Performance:** see the overview finding.
- **Docs:** the `CLAUDE.md` entry and ADRs 0016 and 0017 are present.

FINDING: src/agent_company/api.py:216-219 | each item of `repositories` and `files` has no length limit (only the list length is capped), so a client can send megabyte-long strings that are resolved as paths, stored in the goal description and written into the `mission_create_denied` audit record | declare items as `Annotated[str, Field(max_length=1024)]` for repositories and `max_length=32` for upload ids (the size of `Upload.id`)

FINDING: src/agent_company/api.py:2224-2236 | the command window raises an attached upload's classification to the project's level (`projects.py:753-757`); `mission_create` does not, so an upload attached to a confidential project through Simple mode keeps its lower level (the goal itself is still raised by `create_goal`) | apply the same `highest(u.classification, p.classification)` update to the caller's uploads before `create_goal`

FINDING: src/agent_company/mission.py:136-141 | a failing goal-wide gate check (for example the project's test command) does not change any task's label, so a failed goal can show every task as `verified`; the Simple dashboard's `labels` will mislead users | have the frontend show the goal's status and the limitations next to the labels, or count an iteration's failing non-task checks as evidence against that iteration's writing tasks (either way, document the choice in ADR 0017)

FINDING: src/agent_company/api.py:2253-2309 | `/api/simple/overview` calls `budgets.summary` for each of up to 300 goals and the full `mission.report` (including `rca.list_analyses`) for up to 20 finished goals on every request; if the dashboard polls this endpoint, the SQLite load will be significant | load the budget totals in one query, or work out only the labels for finished goals instead of whole reports, and limit `running`/`delivered` before building the cards

FINDING: src/agent_company/api.py:2241 | links are only checked for the http(s) scheme; internal addresses (`http://127.0.0.1:4910/…`, `169.254.169.254`) are accepted | not a regression, since `POST /api/goals` already accepts `urls` from non-admins unchecked; the security review of the loop's URL fetching should confirm it blocks private addresses

On the developer's two open questions:
- **403 when the project has no `path`:** I agree, this should stay. Allowing it would let a non-admin attach any git repository on the host.
- **Goals with no project are visible to non-admins:** this matches the existing `GET /api/goals` and `GET /api/goals/{id}`, so it adds no new exposure.

NEXT: the developer adds per-item length limits to `MissionResources` and raises the classification of attached uploads.
NEXT: the frontend shows `status` and `limitations` next to the task labels in Simple mode, and renders the Markdown report as text, never as raw HTML.

MESSAGE recommendation to developer: in `src/agent_company/api.py` `MissionResources`, add `max_length` to each item and copy the upload classification raise from `projects.py:753-757` into `mission_create`.
MESSAGE fact to developer: the mission report's Markdown and `/api/simple/overview` contain titles and details written by agents; `static/simple.js` must insert them as `textContent`, never `innerHTML`.

OUTCOME: APPROVED_WITH_COMMENTS
VERDICT: PASS

PROPOSAL: Block private addresses in goal URLs :: `urls` from non-admins (`/api/goals` and `/api/simple/missions`) are not checked for loopback, link-local or private addresses before the loop fetches them.
