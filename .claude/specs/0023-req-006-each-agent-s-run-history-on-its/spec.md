# Specification: REQ-006: each agent's run history on its card

## Write the specification (architect, done)

# REQ-006: each agent's run history on its card (technical specification)

Spec slug: `.claude/specs/req-006-agent-runs/`. I was told not to change files, so this text is the spec. If the gate wants a file, the developer can save it as `spec.md` in that folder.

## 1. Current state (checked)
- **The card.** `static/agents.js:140` `agentModal` has five tabs: Profile, Definition, Playbook & history, Relationships and Versions (`:150`). The only run information is the Stats table (`:195`). It shows `a.usage.runs` as a count, plus total tokens and cost. **No list of runs exists.**
- **The data.** `registry.agent_detail` (`registry.py:1086`) loads every `Run` of the agent just to add up `usage`.
- **What a `Run` stores** (`db.py:715`): `task_id`, `agent_id`, `goal_id`, `model_name`, `kind` (the executor), `purpose`, `input_tokens`/`output_tokens`, `cost`, `duration`, `ok`, `error`, `isolation`, `tools`, `permissions`, `dropped`, `denied_tools`, `tool_calls` (`{tool, input, ok}`, at most 200) and `started_at`.
  - `goal_id` is a plain `Integer`, not a foreign key, so the query has to join `Goal` explicitly.
- **Messages.** No transcript is stored per run. The only "recorded messages" are the task's `Task.output` (plus `Task.title`, `Task.key`, `Task.session_id`) and the run's `error` and `tool_calls`. This spec does not add a new store.
- **A row format to reuse.** `loop.trace` (`loop.py:2035`) already builds a run dict for a goal.
- **Permissions to reuse.** `/api/agents/{id}` only needs `current_user`. `goal_trace` (`api.py:2539`) also hides goals in projects the user can't see, using `_visible_project_ids`. An unknown id raises `KeyError`, which the existing handler (`api.py:844`) turns into a 404.

## 2. Backend: `src/agent_company/registry.py`
Both functions go next to `agent_detail`.

```python
RUNS_PAGE_MAX = 200

def agent_runs(agent_id: int, offset: int = 0, limit: int = 50,
               visible: set[int] | None = None) -> dict[str, Any]:
    """One page of the agent's runs, newest first (REQ-006). `visible`: project ids
    the caller may see (None = everything, i.e. an administrator)."""

def agent_run(agent_id: int, run_id: int, visible: set[int] | None = None) -> dict[str, Any]:
    """One run with what was recorded: its task's output, error and tool calls."""
```

- **Agent check.** If the agent is missing, raise `KeyError(agent_id)`.
- **Clamping.** `offset = max(0, offset)` and `limit = min(max(1, limit), RUNS_PAGE_MAX)`.
- **Query.** `select(Run).where(Run.agent_id == agent_id)`, outer-joined to `Goal` on `Goal.id == Run.goal_id`.
  - If `visible` is not None, add `or_(Run.goal_id.is_(None), Goal.project_id.is_(None), Goal.project_id.in_(visible))`. A run whose goal was deleted (no `Goal` row) counts as having no project.
  - Order by `Run.id.desc()`, so the newest run comes first and the order is stable.
  - `total` is the count under the same filter.
- **Page format.** `{"total", "offset", "limit", "runs": [row…]}`, where each row is:
  `{"id", "goal_id", "goal_title", "task_id", "task_key", "task_title", "model", "executor" (kind), "purpose", "input_tokens", "output_tokens", "tokens", "cost", "duration" (rounded to 2 places), "outcome": "ok"|"failed", "error" (first 200 characters), "started_at" (ISO), "tool_call_count"}`.
  - Get goal and task titles with one `IN` lookup each per page, not one query per row.
  - Do not include `tool_calls` in the list.
- **`agent_run`.**
  - Raise `KeyError(run_id)` if the run is missing, if `run.agent_id != agent_id`, or if its goal is outside `visible`.
  - Return the row fields plus: the full `error`; `isolation`, `tools`, `permissions`, `withheld` (taken from `dropped`), `denied_tools` and `tool_calls`; and a `task` field of `{"id", "key", "title", "status", "output", "session_id"}`, or `null`.
- **`agent_detail`.** Change the `usage` sum to SQL aggregates (`func.count` / `func.sum`). The result stays the same; it just stops loading every row.

## 3. API: `src/agent_company/api.py`
These go next to `/api/agents/{agent_id}/relationships`. They are read-only, so there is no `_audit`.

```python
@app.get("/api/agents/{agent_id}/runs")
def agent_runs(agent_id: int, offset: int = 0, limit: int = 50,
               user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    with session() as s:
        seen = _visible_project_ids(s, user)
    return registry.agent_runs(agent_id, offset, limit, seen)

@app.get("/api/agents/{agent_id}/runs/{run_id}")
def agent_run(agent_id: int, run_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    ...  # the same, registry.agent_run
```

- These routes don't clash with `/api/agents/health`: their paths have more segments, and `agent_id` is typed `int`.
- **Permissions:**
  - Not signed in → 401 (from `current_user`).
  - A signed-in user sees only the runs of goals in projects they can see, or with no project. An administrator sees everything.
  - Unknown agent, a run that belongs to another agent, or a hidden run → 404.

## 4. UI: `src/agent_company/static/agents.js`
- **New tab.** Add `["runs", "Runs"]` to `tabs` (after "Playbook & history"). Then add a `tab === "runs"` branch that calls `runsTab(box, a)` and returns, the same way `relationshipsTab` works.
- **The table.** `runsTab` fetches `/api/agents/${a.id}/runs?offset=${o}&limit=25` and draws a table with the columns: When (`ago(started_at)`), Goal (`#id title`), Task, Model, Tokens (`fmt.format`), Cost (`money`) and Outcome (`pill(outcome)`, with the error as its title).
  - Under the table: "Showing x–y of total", with Newer/Older `ib` buttons that change `offset` and are disabled at either end.
  - With no runs: `<span class="muted">No runs yet.</span>`.
  - Escape all text with `esc`.
- **Opening a run.** Clicking a row (`data-run`) fetches `/api/agents/${a.id}/runs/${id}` and shows it in a `<details>`/panel under the row:
  - the task's output in a `<pre>`;
  - the error, if there is one;
  - the gateway line: isolation, tools, permissions, withheld, denied;
  - the tool calls as a list: `pill(ok ? "done" : "failed")`, the tool name, and the shortened input.
  - Add a muted note: "No transcript is stored per run; this is the task's output and the recorded tool calls."
- **Errors.** `toast(e.message)`.

## 5. Tests: `tests/test_agent_runs.py`
These run offline, each with its own DATA_DIR. Use the client pattern from `test_governance.py:137`:
- the admin registers from `("127.0.0.1", 50000)`;
- a guest registers through `TestClient(app.app)`, then `/api/users/{uid}/approve`, then `/api/auth/device`.

Seed runs with `usage.record(model_name=..., kind="sim", agent_id=..., goal_id=..., task_id=..., res=SimpleNamespace(tool_calls=[...]))`.

1. **Not signed in.** A fresh client without a session gets 401 from both endpoints.
2. **Paging.** Create 7 runs.
   - `limit=3` returns `total == 7` and the three highest ids, in descending order.
   - `offset=3` gives the next three, and `offset=6` gives one.
   - The pages don't overlap, and together they cover all 7.
3. **Clamping.** `limit=0` gives a page of 1 row. `limit=10_000` gives `limit == 200`. A negative offset counts as 0.
4. **Visibility.** Create a run on a goal in a project the guest can't see, and runs with no goal or with a project-less goal.
   - The guest's `total` leaves out the hidden run, and its detail returns 404 for the guest.
   - The admin sees all of them.
5. **Ownership.** Asking for another agent's run under this agent's path returns 404. An unknown agent returns 404.
6. **Detail.** The detail returns the recorded `tool_calls`, the full `error`, and `task.output` when the run has a task.
7. **Usage totals.** `agent_detail(...)["usage"]` has the same totals as before the aggregate rewrite.

Run the tests serially: `uv run pytest -q tests/test_agent_runs.py`, then the full suite. There is no xdist. Read the summary line.

## 6. Scope (allowed paths)
`src/agent_company/registry.py`, `src/agent_company/api.py`, `src/agent_company/static/agents.js`, `tests/test_agent_runs.py`, and `CLAUDE.md` (one clause after `agents.js`: "agent card Runs tab: paged runs, run detail with task output and tool calls, REQ-006").
- No database change.
- No new executor or network path, so `docs/threat-model.md` stays unchanged.
- No files outside this list (K123): no WORKLOG unless the task contract asks for one (K138).

## 7. Acceptance
- The card's Runs tab lists the agent's runs, newest first, with goal, task, time, model, tokens, cost and outcome, and pages through them.
- Opening a run shows its task output and its recorded tool calls.
- Both endpoints respect sign-in and project visibility.
- The tests above pass, and the full suite has no new failures.

FINDING: `Run` has no per-run transcript. "Recorded messages" can only mean `Task.output` plus `Run.error` and `Run.tool_calls`, so the spec states this in the UI instead of inventing a store.
FINDING: `registry.agent_detail` loads every `Run` row of an agent just to add up usage (`registry.py:1086`). Section 2 replaces this with SQL aggregates.
NEXT: The developer implements sections 2–5 within the scope in section 6 and reports the pytest summary line.
NEXT: QA checks the visibility filter with a guest who has `projects: "own"`, plus the 404s for runs of other agents.
MESSAGE handoff to developer: The spec is in this answer (slug `.claude/specs/req-006-agent-runs/`). Reuse the `goal_trace` visibility pattern (`api.py:2539`) and the `loop.trace` run dict (`loop.py:2035`). Join `Goal` explicitly, because `Run.goal_id` is not a foreign key.
PROPOSAL: Store a short redacted transcript per Run :: The card could then show a run's actual prompt and response, not just the task output and tool calls.
