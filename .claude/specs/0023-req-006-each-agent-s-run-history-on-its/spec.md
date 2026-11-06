# Specification: REQ-006: each agent's run history on its card

## Write the specification (architect, done)

The spec below is unchanged from iteration 1 except for the changes listed first. Iteration 1 failed because the build task never ran: its sandbox couldn't read this worktree (`bwrap: Can't find source path …/goal-23: Permission denied`). The spec was not the problem. I checked again and none of the work exists yet: `api.py` and `registry.py` have no `/runs` route, and `agents.js` still has five tabs.

**What changed from iteration 1:**
- **Spec folder.** The real one is `.claude/specs/0023-req-006-each-agent-s-run-history-on-its/`. The `req-006-agent-runs` slug in iteration 1 was wrong.
- **`WORKLOG.md` is in scope.** The build task's acceptance in `tasks.md:17` is `file_exists WORKLOG.md`. Iteration 1 left it out of scope, which would have failed the build gate.
- **The reviewer's test list is now included:** an offset past the end, and the 401/403 cases. There is no 403 case, because the existing permission is just `current_user`. An unapproved user gets 401 (`auth.py:177`).
- **Test evidence is required.** The developer must run the tests and paste the pytest summary lines into `WORKLOG.md` (K133, K145, K164).

# REQ-006: each agent's run history on its card (spec, iteration 2)

## 1. Current state (checked again at `da9cb3d`)
- **The card.** `static/agents.js:150` has five tabs: profile, definition, history, links and config. The only run information is a count, `a.usage.runs`, in the Stats table (`:195`). No list of runs exists and no run can be opened.
- **The API.** `api.py` has no `/api/agents/{id}/runs`. `/api/agents/{id}` (`:1053`) only needs `current_user`.
- **Run fields** (`db.py:715`): `task_id`, `agent_id`, `goal_id`, `model_name`, `kind`, `purpose`, `input_tokens`, `output_tokens`, `cost`, `duration`, `ok`, `error`, `isolation`, `tools`, `permissions`, `dropped`, `denied_tools`, `tool_calls` (`{tool, input, ok}`, at most 200) and `started_at`.
  - `goal_id` is a plain `Integer`, not a foreign key. Join `Goal` and `Task` explicitly.
- **Messages.** No transcript is stored per run. A run's "recorded messages" are its task's `Task.output`, plus the run's `error` and `tool_calls`. This spec adds no new store.
- **Patterns to reuse:**
  - the run dict in `loop.trace` (`loop.py:2035`);
  - the visibility check in `goal_trace` (`api.py:2539`, `_visible_project_ids` at `:3413`);
  - `KeyError` → 404 (`api.py:844`);
  - `offset`/`limit` paging (`network_graph`, `api.py:1217`).

## 2. Backend: `src/agent_company/registry.py` (next to `agent_detail`)
```python
RUNS_PAGE_MAX = 200

def agent_runs(agent_id: int, offset: int = 0, limit: int = 50,
               visible: set[int] | None = None) -> dict[str, Any]:
    """One page of the agent's runs, newest first (REQ-006). `visible`: the project ids the caller may see
    (None = all, an administrator)."""

def agent_run(agent_id: int, run_id: int, visible: set[int] | None = None) -> dict[str, Any]:
    """One run with what was recorded: its task's output, error, gateway grants and tool calls."""
```
- **Agent check.** If the agent doesn't exist, raise `KeyError(agent_id)`.
- **Clamping.** `offset = max(0, offset)` and `limit = min(max(1, limit), RUNS_PAGE_MAX)`.
- **Query.** `select(Run).outerjoin(Goal, Goal.id == Run.goal_id).where(Run.agent_id == agent_id)`.
  - If `visible is not None`, also require `or_(Run.goal_id.is_(None), Goal.id.is_(None), Goal.project_id.is_(None), Goal.project_id.in_(visible))`.
  - Order by `Run.id.desc()`.
  - Compute `total` with `func.count()` under the same filter.
  - An offset past the end returns `runs: []` with the correct `total`.
- **Return value.** `{"total", "offset", "limit", "runs": [row]}`, where each row is:
  `{"id", "goal_id", "goal_title", "task_id", "task_key", "task_title", "model", "executor", "purpose", "input_tokens", "output_tokens", "tokens", "cost", "duration", "outcome": "ok"|"failed", "error" (first 200 chars), "started_at" (ISO), "tool_call_count"}`.
  - Fetch goal and task titles with one `IN` query each per page.
  - Do not include `tool_calls` in the list.
- **`agent_run`.**
  - Raise `KeyError(run_id)` if the run doesn't exist, if `run.agent_id != agent_id`, or if its goal's project is not in `visible`.
  - Return the row plus: the full `error`; `isolation`, `tools`, `permissions`, `withheld` (from `dropped`), `denied_tools` and `tool_calls`; and `task`, which is `{"id", "key", "title", "status", "output", "session_id"}` or `None`.
- **`agent_detail` `usage`** (`:1086`). Compute it with `func.count`/`func.sum` instead of loading every row. The output stays the same.

## 3. API: `src/agent_company/api.py` (after `/api/agents/{agent_id}/relationships`, `:1226`)
```python
@app.get("/api/agents/{agent_id}/runs")
def agent_runs(agent_id: int, offset: int = 0, limit: int = 50,
               user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    with session() as s:
        seen = _visible_project_ids(s, user)
    return registry.agent_runs(agent_id, offset, limit, seen)

@app.get("/api/agents/{agent_id}/runs/{run_id}")
def agent_run(agent_id: int, run_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    with session() as s:
        seen = _visible_project_ids(s, user)
    return registry.agent_run(agent_id, run_id, seen)
```
- **Permissions.** These use the same dependency as the agent-detail route.
  - No session, or a user who isn't active (not approved) → 401.
  - Hidden runs are left out of the list, and opening one returns 404.
  - An unknown agent, or a run that belongs to another agent → 404.
- **No audit.** Both endpoints are read-only, so there is no `_audit`.

## 4. UI: `src/agent_company/static/agents.js`
- **New tab.** Add `["runs", "Runs"]` to `tabs` after `history`. Add `if (tab === "runs") return runsTab(box, a);`, written like `relationshipsTab`.
- **The table.** `runsTab(box, a, offset = 0)` fetches `/api/agents/${a.id}/runs?offset=${offset}&limit=25` and draws a table with these columns:
  - When (`ago`)
  - Goal (`#id title`)
  - Task
  - Model
  - Tokens (`fmt.format`)
  - Cost (`money`)
  - Outcome (`pill`, with the error as its `title`)
- **Paging.** Show "x–y of total", with `ib` Newer/Older buttons that are disabled at either end. `total` drives the paging.
- **Empty list.** Show `<span class="muted">No runs yet.</span>`.
- **Opening a run.** Clicking a row (`data-run`) fetches the detail and shows it under the row:
  - the task output in a `<pre>`;
  - the error;
  - the gateway line: isolation, tools, permissions, withheld, denied;
  - the tool calls, each as `pill(ok === false ? "failed" : "done")`, the tool name and its input.
  - Add a muted note: "No transcript is stored per run; this is the task's output and the recorded tool calls."
- **Escaping.** Escape every value with `esc()`.
- **Errors.** Show them with `toast(e.message)`.

## 5. Tests: `tests/test_agent_runs.py` (offline, own DATA_DIR)
- **Setup.** Use the client pattern from `test_governance.py:137`:
  - the admin registers with `client=("127.0.0.1", 50000)`;
  - the guest uses `TestClient(app.app)`, then `/api/auth/register`, `/api/users/{uid}/approve` and `/api/auth/device`.
- **Seeding.** Create runs with `usage.record(model_name="sim", kind="sim", agent_id=..., goal_id=..., task_id=..., error=..., res=SimpleNamespace(tool_calls=[{"tool": "Read", "input": "x", "ok": True}]))`.

1. **401.** A client without a session gets 401 from both endpoints. So does a registered guest who hasn't been approved.
2. **Paging.** Create 7 runs.
   - `limit=3` returns `total == 7` and ids in descending order.
   - `offset=3` returns the next 3, and `offset=6` returns 1.
   - The pages don't overlap, and together they contain all 7.
3. **Past the end.** `offset=50` returns `runs == []` with `total == 7`.
4. **Clamping.** `limit=0` returns 1 row. `limit=10000` returns `limit == 200`. `offset=-5` is treated as 0.
5. **Visibility.** Create one run on a goal in a project the guest can't see (the admin's project), one run with no goal, and one run on a goal with no project.
   - The guest's `total` leaves out the hidden run.
   - The hidden run's detail returns 404 for the guest and 200 for the admin.
6. **404.** An unknown agent returns 404 on both endpoints. Another agent's run under this agent's path returns 404.
7. **Detail.** It returns the `tool_calls`, the full `error` and `task.output`.
8. **Usage.** `registry.agent_detail(id)["usage"]` equals the sums of the seeded runs.

Run the tests serially: first `uv run pytest -q tests/test_agent_runs.py`, then `uv run pytest -q`. The project has no xdist. Copy both summary lines into WORKLOG.md.

## 6. Scope (allowed paths)
- `src/agent_company/registry.py`
- `src/agent_company/api.py`
- `src/agent_company/static/agents.js`
- `tests/test_agent_runs.py`
- `WORKLOG.md` (required by the build acceptance, `tasks.md:17`): what changed, how to verify it, and the two pytest summary lines.
- `CLAUDE.md`: one clause on `agents.js`, "agent card Runs tab: paged runs, run detail with task output and tool calls (REQ-006)".

There is no DB change and no new executor or network path, so `docs/threat-model.md` stays unchanged.

## 7. Acceptance
- **The list.** The card's Runs tab lists the agent's runs, newest first, with goal, task, time, model, tokens, cost and outcome. It pages through them using `total`.
- **The detail.** Opening a run shows its task output, error and tool calls.
- **Permissions.** Both endpoints use `current_user` plus project visibility.
- **Tests.** The tests in §5 pass, the full suite has no new failures, and the summary lines are in WORKLOG.md.

FINDING: Iteration 1's build never ran, because the sandbox couldn't read the worktree (`bwrap: … Permission denied`), and the review was scheduled anyway. The worktree is owned by `root` with mode 755, so the sandbox's user, or the bwrap bind of a parent directory, likely can't reach it. I couldn't check the parent directories from this session.
FINDING: Iteration 1's spec left out `WORKLOG.md`, which `tasks.md:17` requires, and used the wrong spec folder name. Both are fixed above.
BLOCKER: Until the build executor's sandbox can bind `/home/luca/Agent_Company/data/worktrees/goal-23`, no developer can implement or test this, whatever the spec says.
NEXT: An administrator fixes the sandbox's access to the worktree path (the permissions of `/home/luca` and `data/worktrees`, or the bwrap source bind), then reruns the build.
NEXT: The developer implements §2–§5 within the §6 scope and records the pytest summary lines in WORKLOG.md.
MESSAGE handoff to developer: The spec is this answer; the goal's spec folder is `.claude/specs/0023-req-006-each-agent-s-run-history-on-its/`. Copy the visibility check from `goal_trace` (`api.py:2539`), join `Goal` explicitly, and write WORKLOG.md with both pytest summary lines.
MESSAGE request to general-manager: Don't schedule the review when the build reports a BLOCKER with no diff (iteration 1 reviewed an empty change).

```json
{"insights": [{"title": "Sandbox failures get reported as spec or review failures", "description": "When the build executor's bwrap can't bind the worktree, the build ends 'done' with a BLOCKER and no diff, the review still runs, and the architect's spec gets the FAIL.", "evidence": ["blackboard engineering/build v1: bwrap: Can't find source path …/goal-23: Permission denied", "K164/K165/K166 all carry the same reviewer FAIL for goal #23"], "expected_benefit": "Fewer wasted iterations, and the failure lands on the real layer (infrastructure, not the spec)", "confidence": 0.7}]}
```

PROPOSAL: Stop the loop when the build reports a BLOCKER with an empty diff :: The goal would pause for a person instead of spending a review and a whole new iteration on nothing.
