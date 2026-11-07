# Tasks (iteration 1)



## 1. Company map view: spec, companymap.js, registration, nav entry and tests
- key: `companymap` · role: developer · writes: yes

Read docs/COMPANY-DESIGN.md §1-§4, src/agent_company/static/app.js (esc, api, pill, registerView, NAV_DEFAULT at ~line 222, icon()), one existing view file such as static/network.js or static/models.js for the idiom, and index.html. Read the response shapes of the endpoints you use before you rely on them: GET /api/rooms -> registry.list_rooms / organization.room_dict (key, parent_key, name, lead, enabled …); GET /api/agents -> registry.agent_dict (role, room, enabled, status, model_pref …); GET /api/models -> {models, routes: {role: model_name}, fallbacks: {role: [model names]}, default_model}; GET /api/goals -> _goal_dict list (status, iteration …); GET /api/goals/{id} -> adds tasks[] with iteration, role, status and stage (workflow.TASK_STAGES). Add NO backend code and NO new endpoint.

Step 1, spec: write .claude/specs/0101-company-map-view-the-engine-line-each-ag/company-map.md (about 40-80 lines). Cover the data sources, the layout, the model-badge rule and the lifecycle mapping below, and how an empty or failed endpoint is shown.

Step 2, implement src/agent_company/static/companymap.js in plain JS like the other view files (no modules, no framework):
- `async function viewCompanyMap()` loads /api/rooms, /api/agents, /api/models and /api/goals with Promise.all, then renders into $("#view").
- Room tree: build the children of each room from parent_key. Render nested boxes (div.card or a small cm-room class with inline styles, matching existing CSS variables). The engine line general_management → organization_engine → evolution_engine → orchestrator → bridge → development is drawn as the nested main column, each room with its other child rooms (e.g. Agent Management, Study Room) inside it. Every other child of the root (research, analysis, security, business …) goes in a side column beside the line. Rooms whose keys do not exist are simply absent: the tree comes from data, never from a hard-coded list. The key list may only decide which column a root child goes into. A room with enabled=false is shown dimmed and marked 'disabled'.
- Inside each room: its enabled agents (agent.room === room.key), name and role. Model badge: if model_pref starts with 'agent:' show 'via agent #<id>'. Else if model_pref is set show it. Else routes[agent.role], else default_model, marked '(route)' or '(default)'. Then the route's fallbacks[agent.role] as smaller badges in order ('→ name'). Agents whose room matches no room go in an 'Unassigned' box.
- Live goals: goals whose status is in queued, planning, running, recovering, paused or awaiting_approval. Fetch /api/goals/{id} only for those (cap at 20) to read the tasks of the current iteration (task.iteration === goal.iteration). Show title (link or data-goal opening goalModal if app.js has it), status pill and lifecycle step.
- Lifecycle mapping as a pure function `cmLifecycleStep(goal, tasks)` returning one of discover|plan|execute|critique|validate|learn (COMPANY-DESIGN §3):
  queued -> discover;
  planning -> plan;
  awaiting_approval -> learn;
  running/recovering/paused, using the current iteration's tasks:
    no tasks -> plan;
    any task with role qa (or a review task, role containing 'review') whose stage is in_progress/assigned, or any task in stage 'review' -> critique;
    any other task in stage in_progress/assigned/planned/reopened, or with status running/queued -> execute;
    all tasks closed (approved/completed/failed/skipped/cancelled) -> validate (the quality gate).
  A paused goal additionally shows a 'paused' pill.
  Show the six steps as a row with the current one highlighted.
- Every interpolated value goes through esc(), including attribute values. A failing endpoint shows an esc()'d message in a card instead of throwing. Read-only: no buttons that POST.
- At the end: `registerView({ id: "companymap", title: "Company map", icon: "<an icon name icon() already knows, e.g. agents or network>" }, viewCompanyMap);`

Step 3, wiring: add `<script defer src="/static/companymap.js"></script>` to src/agent_company/static/index.html after app.js and before the start() line. In app.js, set NAV_DEFAULT.top to ["dashboard", "projects", "agents", "companymap", "models", …] (directly after 'agents'; change nothing else there).

Step 4, tests: create tests/test_company_map.py (offline):
(a) index.html contains the companymap.js script tag, positioned after app.js;
(b) app.js NAV_DEFAULT top lists 'companymap' directly after 'agents' (parse with a regex);
(c) companymap.js contains registerView({ id: "companymap" and uses esc(;
(d) skipped when shutil.which('node') is None: a node script run through subprocess loads companymap.js with stubs (registerView, esc, api, $) via vm. It then asserts cmLifecycleStep for queued -> discover, planning -> plan, awaiting_approval -> learn, running with a developer task in_progress -> execute, running with a qa task in_progress -> critique, running with all tasks completed -> validate, running with no tasks -> plan.
Run `python3 -I src/agent_company/edit_check.py src/agent_company/static/companymap.js src/agent_company/static/app.js tests/test_company_map.py`, then `uv run pytest -q tests/test_static_js.py tests/test_company_map.py` and read the summary line. Optionally add one line for companymap.js to the static/ list in CLAUDE.md and change 'queued as a goal' for the Company map in docs/COMPANY-DESIGN.md §4 to 'done'.

Acceptance: {"type": "file_exists", "path": "src/agent_company/static/companymap.js"}; {"type": "file_exists", "path": "tests/test_company_map.py"}; {"type": "file_exists", "path": ".claude/specs/0101-company-map-view-the-engine-line-each-ag/company-map.md"}; {"type": "command", "run": "uv run pytest -q tests/test_static_js.py tests/test_company_map.py", "stage": "unit"}; {"type": "command", "run": "grep -q 'registerView({ id: \"companymap\"' src/agent_company/static/companymap.js", "stage": "static"}; {"type": "command", "run": "grep -q '/static/companymap.js' src/agent_company/static/index.html", "stage": "static"}

## 2. Research page: show a report's tool_steps on the report detail
- key: `tool_steps` · role: developer · writes: yes

research.report_dict already returns `tool_steps` (a list, set in research.py ~line 1402 from research_agent's tool loop). Read src/agent_company/research_agent.py to learn the shape of one step (tool name, arguments, result summary/count, round …) and do not guess it. Then find where the Research page opens a report's detail: viewResearch in src/agent_company/static/app.js ~line 607, the `data-report` links and their handler/modal. In that detail, add a 'Tool steps (N)' section, collapsed in a <details> when the list is long. Each step is one row: its round/index, the tool name, its arguments shown compactly (JSON.stringify, truncated to about 200 chars) and its outcome. Every value goes through esc(). Show nothing (or 'No tool steps') when the list is empty or missing; older reports have none. Do not change any backend file. Add to tests/test_company_map.py (it exists after the companymap task; create it if it does not) a test that app.js references `tool_steps`. Run `python3 -I src/agent_company/edit_check.py src/agent_company/static/app.js tests/test_company_map.py` and `uv run pytest -q tests/test_static_js.py tests/test_company_map.py`.

Acceptance: {"type": "command", "run": "grep -q 'tool_steps' src/agent_company/static/app.js", "stage": "static"}; {"type": "command", "run": "uv run pytest -q tests/test_static_js.py tests/test_company_map.py", "stage": "unit"}

## 3. QA review of the Company map and the tool_steps detail against COMPANY-DESIGN §1-§4
- key: `qa_review` · role: qa · writes: no · after: companymap, tool_steps

Review the diff of this goal's branch against main (git diff main...HEAD). Read the spec .claude/specs/0101-company-map-view-the-engine-line-each-ag/company-map.md and docs/COMPANY-DESIGN.md §1-§4. Verify:
(1) Data flow, normal case: rooms nested by parent_key with the engine line in the main column and the side rooms beside it. Each enabled agent appears in its room with a model badge following the rule: model_pref, then 'agent:<id>', then routes[role], then default_model. Fallbacks come from /api/models fallbacks[role], in order.
(2) Edge cases: a room with no agents; an agent whose room does not exist ('Unassigned'); a disabled room; no live goals; a running goal with no tasks in its current iteration (plan); a goal at awaiting_approval (learn); a paused goal.
(3) Error case: one endpoint failing renders a message, not a blank page or an uncaught exception.
(4) Every interpolated string, including attributes, goes through esc(), in companymap.js and the new tool_steps section.
(5) No backend file changed and no POST/PUT/DELETE call in companymap.js (read-only).
(6) index.html loads companymap.js after app.js; NAV_DEFAULT.top has 'companymap' directly after 'agents' and is otherwise unchanged.
(7) The cmLifecycleStep mapping matches the spec and §3, and tests/test_company_map.py checks it.
Run `uv run pytest -q tests/test_static_js.py tests/test_company_map.py` and `python3 -I src/agent_company/edit_check.py src/agent_company/static/companymap.js src/agent_company/static/app.js`, and quote their summary lines. List each problem as `FINDING: <file>:<line> | <problem>`, then an OUTCOME paragraph and finally `VERDICT: PASS` or `VERDICT: FAIL`. FAIL only for a spec violation, missing escaping, a backend change or a failing test.

Acceptance: {"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}; {"type": "contains", "value": "OUTCOME"}; {"type": "min_length", "value": 400}
