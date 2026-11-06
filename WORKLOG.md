# WORKLOG – REQ-006: each agent's run history on its card (goal 23, iteration 2)

## Before
`static/agents.js` `agentModal` had five tabs (Profile, Definition, Playbook & history, Relationships, Versions).
The only run information was the Stats table's count, tokens and cost. No endpoint returned an agent's runs.

## What changed
- `src/agent_company/registry.py`
  - `agent_runs(agent_id, offset, limit, visible)` returns one page of the agent's `Run` rows, newest first
    (`Run.id desc`): `{total, offset, limit, runs}`. It clamps `offset >= 0` and `1 <= limit <= RUNS_PAGE_MAX (200)`.
    `Goal` is outer-joined explicitly because `Run.goal_id` is not a foreign key. With `visible` set, only runs with
    no goal, with a project-less (or deleted) goal, or with a goal in a visible project count, both in the page and
    in `total`. Goal titles and tasks come from one `IN` lookup each per page. A row has goal, task, time, model,
    executor, tokens, cost, duration, outcome ok|failed, the first 200 characters of the error and
    `tool_call_count`. Rows do not include `tool_calls`.
  - `agent_run(agent_id, run_id, visible)` returns the row plus the full error, the gateway fields (isolation, tools,
    permissions, `withheld` from `dropped`, `denied_tools`), `tool_calls` and the `task` (id, key, title, status,
    output, session_id) or null. It raises `KeyError` (404) for an unknown agent, a missing run, another agent's run,
    or a run of a hidden project.
  - `agent_detail`: `usage` now comes from SQL aggregates and no longer loads every run. The keys and rounding are
    unchanged.
- `src/agent_company/api.py`: `GET /api/agents/{id}/runs?offset=&limit=` and `GET /api/agents/{id}/runs/{run_id}`.
  They use `current_user` (401 without an active session), like `/api/agents/{id}`, plus `_visible_project_ids`, as
  `goal_trace` does. They are read-only, so there is no audit record.
- `src/agent_company/static/agents.js`: a new **Runs** tab (`runsTab`) shows a table with When, Goal, Task, Model,
  Tokens, Cost and Outcome (the error as the pill's title), 25 rows per page. Newer/Older buttons, disabled at either
  end, sit around "x–y of total". When there are no runs it shows "No runs yet.". Clicking a row toggles a detail
  row with a note that no transcript is stored, the task's output in a `<pre>`, the error, the gateway line and the
  tool calls (pill done/failed, tool, input). Every value goes through `esc()`, and errors are shown with `toast`.
- `tests/test_agent_runs.py`: 6 tests, described below.
- `CLAUDE.md`: one clause on the agents.js Runs tab.

There is no DB change and no new executor or network path, so `docs/threat-model.md` is unchanged.

## How to verify
1. `uv run pytest -q tests/test_agent_runs.py` covers:
   - 401 without a session and for an unapproved guest, 200 once approved;
   - paging of 7 runs at limit 3 (ids descending, no overlap, all 7 covered), offset 50 giving an empty page with
     total 7, and clamping (limit 0 → 1, 10000 → 200, offset -5 → 0);
   - the guest not seeing the run of the admin's project (total and detail 404) while the admin sees all three;
   - 404 for an unknown agent and for another agent's run;
   - the detail with full error, tool_calls and task.output;
   - `agent_detail` usage equal to the seeded sums.
2. `uv run pytest -q` runs the full suite serially (there is no xdist).
3. In the dashboard, go to Agents, open an agent card and choose **Runs**. Page through with Newer/Older, and click
   a row to see its output and tool calls.

## Results
- `uv run pytest -q tests/test_agent_runs.py`: `6 passed in 3.06s`
- `uv run pytest -q`: `749 passed in 315.37s (0:05:15)`

Not run: `node --check` on agents.js, because running node needs approval in this session. The browser UI was not
tried either.

## Re-verification (2026-11-06, iteration 2, Auxiliary Developer (Codex) #3)
I found the changes above in the worktree, not yet committed, and checked them again here:
- `uv run pytest -q -p no:cacheprovider tests/test_agent_runs.py` → `6 passed in 3.00s`
- `uv run pytest -q -p no:cacheprovider` (serial) → `749 passed in 315.10s (0:05:15)`
- Read against the spec: both routes use `Depends(current_user)` and `_visible_project_ids`, the same check as
  `goal_trace` (`api.py:2554`). The `KeyError` the registry raises becomes 404 through the global handler
  (`api.py:844`). The JS helpers `runsTab` uses (`esc`, `fmt`, `money`, `ago`, `ib`, `pill`, and the
  `chevron_left`/`chevron_right` icons) exist in `app.js`, with the signatures it calls them with.
- Still not run: `node --check` (needs approval) and a browser test of the Runs tab.
