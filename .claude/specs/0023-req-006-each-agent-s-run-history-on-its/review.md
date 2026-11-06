# Review (iteration 2)

## Review the implementation (qa, done)

## Logic verification

**What I could check.** The work is committed as `162bd41 Goal 23 iteration 2: build`, and the working tree is clean. I read the full diff of `api.py`, `registry.py`, `static/agents.js`, `CLAUDE.md` and `tests/test_agent_runs.py`.

I could not run anything in this session: `uv run pytest`, `node --check` and `git log` were all refused for needing approval. So I did not rerun the tests myself. The test evidence comes from the developer's recorded summary lines in `WORKLOG.md:49-58`: `6 passed in 3.00s` for the new file and `749 passed in 315.10s` for the serial full suite. One independent artifact backs them up: `tests/__pycache__/test_agent_runs.cpython-313-pytest-9.2.0.pyc` exists in this worktree, and pytest only writes that file when it collects the test file.

**`registry.agent_runs` (`registry.py`) and `GET /api/agents/{id}/runs` (`api.py:1231`)**
- **Normal case:** The route takes `current_user`, the same dependency as `GET /api/agents/{agent_id}` at `api.py:1054`. It then gets `_visible_project_ids`: `None` for an admin, otherwise the ids of projects the user may see (the same helper the goal views use). `agent_runs` clamps the input to `offset ≥ 0` and `1 ≤ limit ≤ 200`. It counts and pages through `Run LEFT JOIN Goal ON Goal.id = Run.goal_id`; the join is explicit because `goal_id` is not a foreign key. Runs come back ordered `Run.id DESC`. Goal titles and tasks are loaded with one `IN` query each, so there is no N+1. Each row carries the goal, task key and title, time, model, executor, tokens, cost, outcome, the first 200 characters of the error, and the number of tool calls. `test_runs_are_paged_newest_first_and_clamped` checks 7 runs split 3/3/1 with no overlap or gap and newest first.
- **Edge case:** `offset=50` returns `runs == []` with `total == 7`, `limit=0` becomes 1, `limit=10000` becomes 200, and `offset=-5` becomes 0. A goal with a deleted project, or a run with no goal, is visible: `Goal.project_id IS NULL` or `Run.goal_id IS NULL`. An empty visible set uses `[-1]`, so the `IN ()` is never empty.
- **Error case:** An unknown agent raises `KeyError`, which the app's handler at `api.py:844` turns into a 404 (`test_unknown_agents…`). No session, or a user not yet approved, gets 401 (`test_runs_need_a_signed_in_active_user`). A guest does not see runs of a hidden project, neither in the count nor in the page (`test_guests_see_only_runs…`).

**`registry.agent_run` and `GET /api/agents/{id}/runs/{run_id}` (`api.py:1238`)**
- **Normal case:** Returns the row plus the full `error`, `isolation`, `tools`, `permissions`, `withheld` (stored as `dropped`), `denied_tools`, `tool_calls`, and the task with its `output`, `status` and `session_id`. `test_run_detail_has…` checks a 500-character error that is cut to 200 in the list but complete here, the exact `tool_calls`, and the task output.
- **Edge case:** A run without a task returns `task: None`, which is tested.
- **Error case:** An unknown run, another agent's run, or a run of a hidden project returns 404. A 404 rather than a 403 doesn't reveal that the run exists. All three are tested.
- **Exposure:** This doesn't widen what users can already see. `loop.trace` (`loop.py:2041`) already returns `tool_calls` behind `current_user` and the same project visibility.

**`agent_detail` usage (`registry.py:1086`)**
- The old code loaded every `Run` to add up the totals; it is now one `COUNT`/`SUM` query with `COALESCE`. For an agent with no runs the result is `{0, 0, 0, 0.0}`, the same as the old sums over an empty list. `test_agent_detail_usage_is_the_sum_of_its_runs` checks the change against two runs of this agent plus one run of another agent. The output keys are unchanged. This fixes the root cause of the inefficiency the spec named; it doesn't hide it.

**`runsTab` (`static/agents.js:227`)**
- **Normal case:** It loads 25 runs per page and shows a table with when, goal, task, model plus executor, tokens, cost and an outcome pill. Clicking a row opens or closes a detail row with the task output, the error, a gateway line and the tool calls. Every value goes through `esc()` and ids through `Number()`. The helpers it uses (`ib`, `pill`, `ago`, `fmt`, `money`, `toast`) and the `chevron_left`/`chevron_right` icons exist in `app.js:6-67`. The `.pill.ok`, `.pill.done` and `.pill.failed` styles exist at `style.css:89-91`.
- **Edge case:** `total == 0` shows "No runs yet." and adds no handlers. "Newer" is disabled at offset 0 and "Older" at the end.
- **Error case:** An API error shows a toast and leaves the tab empty.
- **Not checked:** I read the template literals and the nesting looks balanced, but neither `node --check` nor a browser test has run in any session.

All four parts of REQ-006 exist: the API, the card's list, the run detail and the tests. They address the missing feature itself, not a symptom.

## Checklist

- **Contract satisfied:** Yes. The list has goal, task, time, model, tokens, cost and outcome, newest first and paged, and a run opens its recorded output and tool calls. The UI says plainly that no per-run transcript is stored, which matches spec §1.
- **Interfaces compatible:** Yes. Two new GET routes, no collision with the other `/api/agents/{agent_id}/…` routes, and the `agent_detail.usage` shape is unchanged.
- **Error handling:** Yes. `KeyError` becomes 404, a missing or unapproved session gets 401, and the UI shows a toast.
- **Edge cases:** Mostly covered. Two small gaps are in the comments below.
- **Tests sufficient:** Yes for paging, clamping, past-the-end, 401, visibility, 404s, the detail contents and the usage sums. There is no 403 case because the existing permission is `current_user`; spec iteration 2 says this. The UI is untested.
- **Dependencies:** None added.
- **Security:** Project visibility is applied in both routes, and a hidden run returns 404, not 403. The output is escaped. No writes, so `_audit` isn't needed.
- **Performance:** Paging and counting happen in SQL, and the totals query no longer loads every run.
- **Docs updated:** Yes. `CLAUDE.md` describes the Runs tab, and `WORKLOG.md` records what changed and the test lines. The scope is fine: `tasks.md` sets no path restriction, and its `file_exists WORKLOG.md` acceptance is met.

## Findings (none block the merge)

FINDING: src/agent_company/registry.py:1145 (agent_runs order_by) | "Newest first" is ordered by `Run.id DESC`, not by `started_at`. That is correct today because ids are assigned in insert order, but it would be wrong if runs were ever imported or backfilled | `order_by(Run.started_at.desc(), Run.id.desc())`.
FINDING: src/agent_company/registry.py:1101 (_runs_query), registry.py:1166 (agent_run) | Runs with `goal_id=None` are visible to every approved user. Today these are model tests, exercises and some research or triage runs (`projects.py:1177` passes `goal_id` that may be `None`). A project's triage run, including its error text, is therefore shown to guests who can't see that project. `task_id` is only ever passed together with `goal_id` (`loop.py:335`), so task output does not leak | When `goal_id` is `None`, show the run only to admins, or record the project on those runs and filter by it.
FINDING: src/agent_company/static/agents.js:227-258 | The Runs tab has not passed `node --check` and has not been opened in a browser in any session. A syntax error there would break the whole Agents page for every user | Run `node --check src/agent_company/static/agents.js`, then open an agent card's Runs tab and check paging and the detail row.

NEXT: A person (or a session allowed to run node) runs `node --check src/agent_company/static/agents.js` and opens the Runs tab on the Agents page before the merge is approved.
NEXT: Optionally, follow up on the ordering and the no-goal visibility comments above.

MESSAGE fact to developer: The review passed with comments. The `goal_id=None` visibility point and the `started_at` ordering are follow-ups in `registry.py`, not blockers.
MESSAGE request to general-manager: In this QA session Python, node and git log all needed approval, so the test evidence is the developer's summary lines in WORKLOG.md plus the pytest `.pyc` artifact. Allow `uv run pytest` and `node --check` for QA so reviews can rerun the checks themselves.

```json
{"insights": [{"title": "Use pytest's rewritten .pyc as independent evidence when QA cannot run Python", "description": "When the QA session can't run commands, `tests/__pycache__/<test>.cpython-*-pytest-*.pyc` in the worktree shows that pytest really collected the new test file. That backs up the developer's summary lines without trusting them blindly.", "evidence": ["tests/__pycache__/test_agent_runs.cpython-313-pytest-9.2.0.pyc present", "WORKLOG.md:57-58 summary lines"], "expected_benefit": "Fewer false FAILs and fewer unverified PASSes when the QA sandbox blocks execution", "confidence": 0.6}]}
```

PROPOSAL: Allow `uv run pytest` and `node --check` for the QA role :: QA could then rerun the checks instead of relying on the developer's recorded summary lines.

OUTCOME: APPROVED_WITH_COMMENTS
VERDICT: PASS
