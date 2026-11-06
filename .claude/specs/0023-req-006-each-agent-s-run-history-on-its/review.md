# Review (iteration 1)

## Review the implementation (qa, done)

## Logic verification

The diff contains nothing to trace. `git diff --name-only main...HEAD` lists only two plan files:
- `.claude/specs/0023-req-006-each-agent-s-run-history-on-its/requirements.md`
- `.claude/specs/0023-req-006-each-agent-s-run-history-on-its/tasks.md`

The working tree is clean, and HEAD is `6255e17 Goal 23 iteration 1: plan`. This matches the build step's own report: the sandbox could not reach the worktree (`bwrap: … Permission denied`), so no files changed and no tests ran.

What I checked in the code:
- **Normal case (open an agent card, see its runs newest first):** nothing to trace. `static/agents.js:195` still shows only the Stats table, with `a.usage.runs` as a count. There is no runs list, no paging and no run detail.
- **Edge case (agent with no runs, or a page past the end):** no endpoint exists. A search of `api.py` for an agent-runs route found none. A search of `src` and `tests` for `agents/.*/runs` or `agent_runs` also found nothing.
- **Error case (a user without the agents permission, or an unknown agent id):** no route exists, so the permission and 404 behaviour can't be checked.

Nothing has been fixed, and no symptom is hidden either: the root cause, the missing feature, is still there.

## Checklist

- **Contract satisfied:** No. None of the four parts exists: the API, the card's runs list, the run detail view and the tests.
- **Interfaces compatible:** Nothing to check; `api.py` and `agents.js` are unchanged.
- **Error handling:** Not present.
- **Edge cases:** Not present (empty list, last page, a run whose `goal_id` points to a deleted goal, the 200-entry cap on `tool_calls`).
- **Tests sufficient:** No tests were added. No tests or acceptance commands ran in any session, which is the AVOID pattern from K135 and K147.
- **Dependencies:** None.
- **Security:** Nothing to check yet. The future endpoint must use the existing agent permissions. It also must not return run `permissions`, tool inputs with secrets, or another project's data to users who may not see them.
- **Performance:** Not addressed. `registry.agent_detail` (`registry.py:1086`) already loads every `Run`. The new list must page in SQL with `ORDER BY started_at DESC, id DESC LIMIT/OFFSET`.
- **Docs updated:** No. The CLAUDE.md line for `agents.js` and the threat model, if the endpoint exposes tool inputs, still need updating.

## Findings

FINDING: src/agent_company/api.py:1 | There is no endpoint for an agent's runs, so neither the card nor any client can read run history. REQ-006's API part is missing for every user | Add `GET /api/agents/{id}/runs?limit=&offset=` (newest first, `Run` joined explicitly to `Goal` and `Task` because `goal_id` is not a foreign key, returning total plus items: goal, task, started_at, model_name, tokens, cost, ok/error). Add `GET /api/agents/{id}/runs/{run_id}` for `tool_calls`, `error` and `Task.output`. Both must use the same permission dependency as the existing agent-detail route.
FINDING: src/agent_company/static/agents.js:150 | The agent card still has only its five tabs. At :195 runs appear only as a count, so administrators can't see or open an agent's runs | Add a "Runs" tab to `agentModal` with a paged table (newest first, prev/next driven by `total`). Clicking a row should open the run's recorded tool calls, error and task output, with every field escaped through `esc()`.
FINDING: tests/ | No tests for paging or permissions, and no command ran in any session, so the behaviour is unproven | Add pytest cases for: newest-first order across two pages; an offset past the end returning an empty list with the correct total; limit clamping; 404 for an unknown agent or for a run belonging to another agent; 401/403 for a user without the permission. Then run `uv run pytest -q tests/<new file>` and `uv run pytest -q` serially, without xdist, and report the summary lines.
FINDING: .claude/specs/0023-req-006-each-agent-s-run-history-on-its/tasks.md:1 | The build step never ran because the sandbox couldn't read the worktree (`bwrap: Can't find source path … Permission denied`). The loop moved on to review anyway | Fix the worktree's permissions or bwrap bind for the build executor, and don't schedule the review when the build reports BLOCKER with no diff.

BLOCKER: The build executor's sandbox can't access `/home/luca/Agent_Company/data/worktrees/goal-23` (bwrap permission denied), so no implementation exists to review.

NEXT: Restore the build sandbox's access to the worktree. Then implement the API, the card's Runs tab and the tests as described in the findings.
NEXT: Run the new tests and the full suite in the developer session, and give the review the real pytest output.

MESSAGE request to developer: The goal-23 branch contains only the plan files. Implement `api.py` (paged agent runs and run detail), the Runs tab in `static/agents.js`, and tests in `tests/`, and attach the pytest summary lines.
MESSAGE fact to general-manager: The build task for goal 23 failed because of a sandbox permission error, not a code problem. The review found an empty diff.

```json
{"insights": [{"title": "Review scheduled after a blocked build with an empty diff", "description": "The build step reported a BLOCKER (bwrap could not access the worktree) and changed nothing, yet the loop still scheduled the review. Checking `git diff --name-only main...HEAD` first shows within seconds that there is nothing to review.", "evidence": ["build output: bwrap: Can't find source path .../goal-23: Permission denied", "git diff main...HEAD: only .claude/specs/0023-*/requirements.md and tasks.md"], "expected_benefit": "Saves review runs and gives a clearer failure layer (infrastructure, not code)", "confidence": 0.7}]}
```

PROPOSAL: Skip the review when the build reports BLOCKER and no files changed :: the build failed for infrastructure reasons, so reviewing an empty diff costs a run and hides the real failure layer.

OUTCOME: REVISION_REQUIRED
VERDICT: FAIL
