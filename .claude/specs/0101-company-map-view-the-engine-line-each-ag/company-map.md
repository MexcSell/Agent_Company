I built the Company map view and its tests, but I couldn't write the spec file. The tool refused writes into `.claude/` as a sensitive file, and I didn't get around that through the shell. So the `file_exists` acceptance check for the spec will fail; the other five conditions should pass. The full spec text is below so a person can put it in place.

**What I changed:**
- **`src/agent_company/static/companymap.js`** (new): `viewCompanyMap()` loads `/api/rooms`, `/api/agents`, `/api/models` and `/api/goals` together.
  - **Room tree:** built from the data. Rooms carry their parent as `parent`, not `parent_key`; the code reads either. A room with an unknown parent counts as a top-level room.
  - **Columns:** the engine rooms are nested in the main column. Every other room under General Management goes in a side column, and agents whose room doesn't exist go in an "Unassigned" box. Disabled rooms are dimmed with a `disabled` pill.
  - **Model badges:** the agent's own model, else its role's route, else the default model, followed by the route's fallbacks.
  - **Live goals:** up to 20. Each goal's tasks are fetched separately, so one failed fetch doesn't break the list; that goal just shows "tasks unavailable".
  - **Lifecycle step:** `cmLifecycleStep(goal, tasks)` follows COMPANY-DESIGN §3. Two cases the task didn't cover: a task still at stage `created` or `blocked` counts as execute, and an unknown goal status counts as plan.
  - **Safety:** every value goes through `esc()`, a failed endpoint shows an error card instead of throwing, and the view sends no writes.
  - **Registration:** `registerView({ id: "companymap", title: "Company map", icon: "network" }, viewCompanyMap)`.
- **`static/index.html`:** the `companymap.js` script tag, after `app.js` and before `start()`.
- **`static/app.js`:** `NAV_DEFAULT.top` now has `"companymap"` directly after `"agents"`. Nothing else in the file changed.
- **`tests/test_company_map.py`** (new): checks the script tag position, the nav order, the registration and use of `esc(`, and that the view sends no writes. When node is installed it also runs `cmLifecycleStep` on eight cases: the seven required ones plus a paused goal with a task in review, which should be critique.
- **`docs/COMPANY-DESIGN.md` §4:** the Company map row now says "done" instead of "queued as a goal". I left `CLAUDE.md` alone.

**Test results:**
- `edit_check.py` printed `OK` for `static/companymap.js` and `static/app.js`. It couldn't find `tests/test_company_map.py` because the shell's working directory had moved, so it never checked that file. pytest did import it, which compiles it.
- `uv run pytest -q tests/test_static_js.py tests/test_company_map.py`: **25 passed in 5.63s**, nothing skipped. Node is on this host, so `node --check` ran on `companymap.js` and the lifecycle test ran.

I didn't touch any `.py` file under `src/` or the Research page `tool_steps` task.

### Spec text for `.claude/specs/0101-company-map-view-the-engine-line-each-ag/company-map.md`
```
# Company map view (COMPANY-DESIGN §4)
Read-only view static/companymap.js, registerView({ id: "companymap" … }), NAV_DEFAULT.top directly after agents. No backend code, no new endpoint, no write.

## Data sources
- GET /api/rooms (registry.list_rooms → organization.room_dict): key, name, parent (parent_key also accepted), enabled
- GET /api/agents (registry.agent_dict): id, name, role, room, enabled, status, model_pref
- GET /api/models: routes {role: model}, fallbacks {role: [model…]}, default_model
- GET /api/goals (_goal_dict): id, title, status, iteration
- GET /api/goals/{id}: tasks[] with iteration, role, status, stage (workflow.TASK_STAGES)
The four lists load with Promise.all, each catching its own error; task details only for live goals (max 20), each caught.

## Layout
- Tree from data: children = rooms whose parent is the key; a parent that is empty or unknown makes a root.
- Main column: root and nested engine line organization_engine → evolution_engine → orchestrator → bridge → development; each room's non-engine children (Agent Management, Study Room…) as a row of smaller boxes above the next engine box. The key list only decides which root child goes into the main column; missing keys are absent.
- Side column: every other root child (research, analysis, security, business…) with its subtree.
- enabled=false → dimmed + "disabled" pill.
- In a room: enabled agents with agent.room === room.key (name, role, model badges); enabled agents with an unknown room → "Unassigned" box.
- Live goals (queued|planning|running|recovering|paused|awaiting_approval): title opening goalModal, status pill, extra "paused" pill, the six steps in a row with the current one highlighted.

## Model badge rule
1. model_pref "agent:<id>" → "via agent #<id>"; 2. else model_pref; 3. else routes[role] "(route)", else default_model "(default)"; 4. then fallbacks[role] in order as small "→ name" badges.

## Lifecycle mapping: cmLifecycleStep(goal, tasks), pure; caller passes only tasks with task.iteration === goal.iteration
queued → discover; planning → plan; awaiting_approval → learn.
running/recovering/paused: no tasks → plan; a qa/review-role task in stage in_progress|assigned, or any task in stage review → critique; any task in stage in_progress|assigned|planned|reopened or status running|queued → execute; all stages approved|completed|failed|skipped|cancelled → validate; otherwise (created, blocked) → execute. Any other status → plan.

## Empty and failed endpoints
A failed endpoint → esc()'d "Could not load <path>: <error>" card; the rest still renders. "No rooms." / "No live goals." / "No other rooms."; a goal whose detail failed takes its step from status alone and shows "tasks unavailable". Every value, attributes included, passes through esc().

## Tests
tests/test_company_map.py (script tag order, nav order, registerView + esc(, cmLifecycleStep through node vm) and tests/test_static_js.py (node --check).
```

BLOCKER: the harness refused the Write to `.claude/specs/0101-company-map-view-the-engine-line-each-ag/company-map.md` as a sensitive file; the full spec text is in this report and the rest of the task is done.
MESSAGE handoff to general-manager: please put the spec text from this report at `.claude/specs/0101-company-map-view-the-engine-line-each-ag/company-map.md` (your run can write to `.claude/`); `companymap.js`, the wiring and the tests are done and pass.
FINDING: `/api/rooms` returns the parent as `parent`, not `parent_key` as the task text says; `companymap.js` accepts both.
NEXT: the QA review checks `companymap.js` against COMPANY-DESIGN §1–§4 once the spec file is in place.
