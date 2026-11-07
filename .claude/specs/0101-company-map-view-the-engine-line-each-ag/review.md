# Review (iteration 1)

## QA review of the Company map and the tool_steps detail against COMPANY-DESIGN §1-§4 (qa, done)

**Logic verification**

I traced the four endpoint shapes against the backend:
- `organization.room_dict` (`organization.py:34`) returns `parent`, not `parent_key`. The code reads `r.parent ?? r.parent_key`.
- `registry.agent_dict` has `room`, `role`, `enabled` and `model_pref`.
- `/api/models` (`api.py:2944`) returns `routes` as role → name, `fallbacks` as role → [names] in order (`router.list_fallbacks`), and `default_model`.
- `/api/goals/{id}` (`api.py:2244`) gives each task `iteration`, `role`, `status` and `stage`, where `stage` is one of `workflow.TASK_STAGES`.

*Normal case (`viewCompanyMap`, `cmRoom`, `cmModels`).*
- A room is a child when its parent key exists and differs from its own key; otherwise it is a root. With the §1 tree, the root is `general_management`. Its non-engine children (research, analysis, security, business) move into the side column, and its engine child stays in `kids`.
- `cmRoom` puts the first engine child last as `next`, which nests organization_engine → evolution_engine → orchestrator → bridge → development. Other children, such as Agent Management and Study Room, appear as a row of smaller boxes above the next engine box. This matches the spec's layout section.
- Only enabled agents are kept, grouped by `room`.
- Badge order: an `agent:<id>` preference shows "via agent #id", then any other `model_pref`, then `routes[role]` "(route)", then `default_model` "(default)". After that come the `fallbacks[role]` entries in order as small "→ name" badges.
- The task text puts "model_pref" before "agent:<id>". The code checks `agent:` first, which is the only correct order, because `agent:<id>` is itself a `model_pref` value.
- Live goals use the six live statuses, capped at 20. Their tasks are filtered to `t.iteration === g.iteration` before calling `cmLifecycleStep`.

*Edge cases, traced by hand.*
- A room with no agents shows "no active agents".
- An agent whose room is missing, null or empty falls into the dashed "Unassigned" box. Disabled agents are dropped.
- `enabled === false` dims the room to `opacity:.55` and adds `pill("disabled")`.
- With no live goals the card says "No live goals."
- A running goal whose tasks all belong to an earlier iteration gets an empty list after the filter, so it maps to plan.
- `awaiting_approval` maps to learn.
- A paused goal shows its status pill, an extra "paused" pill, and its step from its tasks.
- If a goal's detail request fails, `.catch(() => null)` shows "tasks unavailable" and the step comes from the status alone.

*Error case.* Each list request is wrapped as `{d}|{e}`, so `Promise.all` cannot reject. A failed endpoint becomes an escaped `cmFail` card ("Could not load /api/rooms: …") and the rest falls back:
- no rooms: "No rooms."
- no models: `m = {}`, so the badge reads "? (default)"
- no goals: an empty list

I found no path that throws or leaves a blank page.

*`cmLifecycleStep`.* It matches the spec line for line:
- queued → discover, planning → plan, awaiting_approval → learn.
- For running, recovering and paused:
  - no tasks → plan
  - any task in stage `review`, or a qa/review-role task in `in_progress` or `assigned` → critique
  - any task in `in_progress`, `assigned`, `planned` or `reopened`, or with status `running` or `queued` → execute
  - all tasks closed (approved, completed, failed, skipped, cancelled) → validate
  - anything else (created, blocked) → execute
- Any other status → plan.

This is consistent with §3: Critique is the reviews, Validate is the gate once the work is closed, and Learn is the approval or lessons step after the gate passes.

*`reportToolSteps`.* Its input shape matches `research_agent.py:176-188`: `{round, model, results[{action, <arg>, documents, titles, errors}], stopped | sufficient, reason, refused}`.
- An empty or missing list renders nothing.
- A round that stopped renders as a muted row.
- Error strings are escaped and shortened.

The change does what the task asks: it reads existing data and adds no workaround.

**Checklist**
- **Contract.**
  - Spec items 1–7 are met. The spec file is now committed at `.claude/specs/0101-…/company-map.md`.
  - `index.html` loads `companymap.js` after `app.js` and before `start()`.
  - `NAV_DEFAULT.top` is unchanged except that `"companymap"` now sits directly after `"agents"`. A saved rail order still picks up the new view in its default place (`navGroups`, app.js:231-238).
  - The `network` icon exists (app.js:58).
- **Escaping.**
  - In `companymap.js`, every interpolated value goes through `esc()` or `pill()`, which escapes too (app.js:67). That covers `name`, `key`, `role`, the model, fallback and pref values, `g.id` in `data-goal`, `g.title`, the steps and the error text. The only unescaped pieces are literal class and style strings.
  - In `reportToolSteps`, `round`, `model`, `stopped`, `reason`, `action`, the JSON arguments, `titles`, `errors` and `refused` are all escaped. `docs` and `rows.length` are numbers.
  - The new test confirms in node that `<b>` comes out as `&lt;b&gt;`.
- **Read-only, no backend change.**
  - `git diff main...HEAD --stat` touches only `static/`, `tests/`, `docs/COMPANY-DESIGN.md` and `.claude/specs/`. No `.py` file under `src/` changed.
  - `companymap.js` only calls `api(path)` with no options, which is a GET, plus a click handler that opens `goalModal`.
- **Tests.**
  - `uv run pytest -q tests/test_static_js.py tests/test_company_map.py`: **27 passed in 5.35s**. That is two more than the developer's 25, because the tool_steps commit added two tests.
  - `python3 -I …/edit_check.py static/companymap.js static/app.js`: **`OK static/companymap.js`** and **`OK static/app.js`**.
  - The lifecycle mapping runs through node in 8 cases.
- **Security.** The page writes only escaped HTML through `innerHTML`, and every goal id in a URL is passed through `encodeURIComponent`.
- **Performance.** See the finding on full goal-detail requests below.
- **Docs.** COMPANY-DESIGN §4 is updated. CLAUDE.md does not list the new static file.

FINDING: src/agent_company/static/companymap.js:76 | Each time the view opens it fetches the full `/api/goals/{id}` for up to 20 live goals. Each response carries events, the blackboard, up to 8000 characters of output per task, RCA and the digest, which is slow on a busy company. | Accept for now, since adding a lighter endpoint would need a backend change. Note it as a follow-up, or lower the cap.
FINDING: tests/test_company_map.py:33 | The read-only guard only checks for `"method:"`. A direct `fetch(`, `XMLHttpRequest`, or an `api(p, opts)` call built elsewhere would pass. | Also assert that `fetch(` and `XMLHttpRequest` are absent, and that every `api(` call takes a single argument.
FINDING: tests/test_company_map.py:37-47 | The lifecycle cases miss `recovering`, an unknown status (should be plan), a `created` or `blocked` stage (should be execute), and a qa task in `assigned` (should be critique). The caller's iteration filter is not tested at all. | Add these four cases, and one goal whose only tasks belong to an earlier iteration, which should be plan.
FINDING: src/agent_company/static/companymap.js:79 | A paused goal shows two identical "paused" pills: the status pill and the extra one. It does what the spec says literally, but it is cosmetic noise. | Keep it, or change the extra pill to something like "paused for a person".
FINDING: CLAUDE.md:1 | The layout section of CLAUDE.md lists each static view file, but `companymap.js` is not among them. | Add one line to the `static/` list next to `network.js`.

The tool_steps task is marked failed or skipped by the harness, but its code is in commit `20d1a6e`. I reviewed it for escaping and found no problem, and its two node-rendered tests pass.

OUTCOME: APPROVED_WITH_COMMENTS. Everything delivered meets the spec's layout, model badge rule, live-goal list and lifecycle mapping, as well as COMPANY-DESIGN §1–§4. Every interpolated string is escaped, no backend file changed, the view sends no writes, and the wiring tests pass. The five findings are follow-ups (load, test coverage, one cosmetic pill, docs). None violates the spec.

NEXT: add the missing lifecycle and read-only test cases to `tests/test_company_map.py`.
NEXT: add `companymap.js` to the CLAUDE.md layout list.
MESSAGE recommendation to general-manager: the Company map is approved with comments; the follow-ups are listed as FINDING lines in this review (tests/test_company_map.py, CLAUDE.md).

VERDICT: PASS
