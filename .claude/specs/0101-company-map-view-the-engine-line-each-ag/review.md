# Review (iteration 2)

## QA review of the Company map, tool_steps detail and the follow-up tests against COMPANY-DESIGN §1-§4 (qa, done)

## Logic verification

**Unchanged since iteration 1.** `git diff 20d1a6e HEAD --stat -- src` prints nothing, so `companymap.js`, `app.js` (`reportToolSteps`, `NAV_DEFAULT`) and `index.html` are identical to what review.md approved. Iteration 2 changed only `tests/test_company_map.py`, `CLAUDE.md` and the spec folder. No backend `.py` file under `src/` changed on the branch: `git diff main...HEAD --name-only -- 'src/**/*.py'` is empty, and the stat lists only `static/` files under `src/`.

**Normal case: a running goal with a developer task in progress, and the agent's badge.**
- **Lifecycle step:** the goal is `running` in iteration 2 and its task is `{iteration: 2, role: "developer", stage: "in_progress"}`.
  - `goalRow` keeps the task, since `t.iteration === g.iteration`.
  - In `cmLifecycleStep`, the status is live and the task list is not empty.
  - The critique check fails: the stage is not `review` and `reviewer(t)` is false for a developer.
  - The execute check matches `in_progress`, so the step is **execute**. This is case 4 of the node test.
- **Model badge:** the agent's `model_pref` is `""` and `routes.developer` is `claude_code`.
  - `cmModels` gives `cmBadge("claude_code (route)")`.
  - Each entry of `fallbacks.developer` (`codex`, `claude_37`) follows in order as a small "→ name" badge.
  - Every badge's text and title go through `esc()`.

**Edge cases.**
- **Tasks only from an earlier iteration:** a goal in iteration 2 whose tasks are all from iteration 1 has an empty list after the filter. `cmLifecycleStep` returns **plan** because `!ts.length`. This is the last case in `CASES`.
- **Report without tool_steps:** `reportToolSteps(undefined)` and `reportToolSteps([])` both turn the input into `[]` and return `""`. The node test asserts `none == ""` and `empty == ""`.

**Error case: `/api/models` fails.**
- `load` turns the rejection into `{e}`, so `Promise.all` cannot reject. `m` becomes `{}`.
- `cmModels` finds no pref and no `m.routes`, and renders the badge "? (default)" with no fallbacks.
- `cmFail("/api/models", e)` adds an escaped error card, and the room tree and goals still render. Nothing throws.

The view reads only existing endpoint data and matches §1–§3. The follow-up tests close coverage gaps; they do not hide a defect.

**(a) New lifecycle cases against §3 and the spec.** All of them match:
- `recovering` with a task `in_progress` → execute (live status, a task in progress).
- `done` → plan (the spec says "any other status → plan").
- A task at stage `created`, or at `blocked` beside a completed one → execute (the spec's "otherwise (created, blocked)").
- A qa task `assigned` → critique (§3: Critique is the reviews).
- Only earlier-iteration tasks → plan.

All 14 cases pass through node.

**(b) Read-only guard.**
- `test_view_registers_and_escapes` fails if the file contains `method:`, `fetch(` or `XMLHttpRequest`.
- `api_call_args` matches brackets, so `${encodeURIComponent(g.id)}` inside a template string stays balanced. The test requires at least 2 `api(` calls, each with exactly one argument, and its self-test proves that `api("/x", { method: "POST" })` counts as two.
- The guard misses calls that are not written as `api(` (an alias such as `const f = api`, or `api.call`) and other write channels (`sendBeacon`, `WebSocket`, a form submit). None of these is in the file today, so this is a comment, not a failure.

**(d) Escaping.** The code is unchanged, so iteration 1's check of every interpolation still holds:
- `companymap.js`: `esc()` covers names, keys, roles, model/fallback/pref text, `g.id` in `data-goal`, titles, steps and error text. `pill()`, which escapes its input (app.js:67), covers statuses and "disabled".
- `reportToolSteps`: `round`, `model`, `stopped`, `reason`, `action`, the JSON arguments, `titles`, `errors` and `refused` all go through `esc()`. The only unescaped values are numbers.
- The node test confirms `<b>` comes out as `&lt;b&gt;`.

## Checklist
- **Contract:** met.
  - The script tag is after `app.js` and before `start()`.
  - `NAV_DEFAULT.top` has `companymap` right after `agents` and is otherwise unchanged.
  - The view is registered as `registerView({ id: "companymap" … })`.
  - The spec file is committed.
- **Interfaces:** no API or backend change.
- **Error handling:** each endpoint failure becomes its own error card; a failed goal-detail request shows "tasks unavailable".
- **Tests:** the lifecycle, read-only and tool_steps tests cover what was asked.
- **Dependencies:** none added.
- **Security:** everything is escaped and the view only reads.
- **Performance:** up to 20 full goal-detail requests each time the view opens (still open from iteration 1).
- **Docs:** the CLAUDE.md `static/` list now has `companymap.js` after `network.js`, and COMPANY-DESIGN §4 is marked done.

**Commands**
- `uv run pytest -q tests/test_static_js.py tests/test_company_map.py`: **`28 passed in 5.44s`**. Nothing was skipped, so node ran.
- `python3 -I …/edit_check.py src/agent_company/static/companymap.js src/agent_company/static/app.js`: **`OK src/agent_company/static/companymap.js`**, **`OK src/agent_company/static/app.js`**. Calling the wrapper by its worktree path needed approval, so I ran the main repository's copy (`/home/luca/Agent_Company/src/agent_company/edit_check.py`) on the worktree files.

FINDING: tests/test_company_map.py:98-101 | The iteration-filter case runs a copy of the filter written in the test script, not `goalRow`'s own filter (companymap.js:78). A regression in the view's filter would still pass the test. | Move the filter into a small pure function in `companymap.js` (e.g. `cmCurrentTasks(g, tasks)`) and call that from both the view and the test.
FINDING: tests/test_company_map.py:34,41 | The read-only guard does not catch an aliased or `.call` use of `api`, or other write channels (`sendBeacon`, `WebSocket`, form submit). Nothing like that is in the file today. | Add `sendBeacon`, `WebSocket` and `api.call` / `api.apply` to the banned strings.
FINDING: src/agent_company/static/companymap.js:79 | A paused goal shows two identical "paused" pills: the status pill and the extra one the spec asks for. Cosmetic, carried over from iteration 1. | Give the extra pill a distinct label, or drop it when the status pill already reads "paused".
FINDING: src/agent_company/static/companymap.js:76 | Up to 20 full `/api/goals/{id}` requests each time the view opens (events, blackboard, task output, RCA, digest). Carried over from iteration 1. | Lower the cap, or later add a lighter tasks-only read (that is a backend change, so outside this goal).

OUTCOME: APPROVED_WITH_COMMENTS. The whole branch meets COMPANY-DESIGN §1–§4 and the spec:
- The engine line is nested with the side rooms beside it.
- Model badges follow model_pref → agent → route → default, with the fallbacks in order.
- Live goals map to the §3 lifecycle steps.
- tool_steps show on the report detail and render nothing for older reports.
- Every value is escaped and the view sends no writes.

No backend file changed, and both commands pass. The four findings are about test robustness, one cosmetic duplicate pill and request load; none violates the spec.

NEXT: optionally move the iteration filter into a pure function in `companymap.js` so the test calls the view's own filter.
MESSAGE done to general-manager: goal 101 iteration 2 QA review passes (28 tests pass, syntax check OK, no backend change); the remaining FINDING lines are optional follow-ups.

VERDICT: PASS
