# Decisions: AICO UI redesign (docs/requirements/AICO-UI.md)

## 2026-11-09 — delivered directly, phase by phase, instead of as loop goals
Three goal-loop runs of P0-1 (#337, #367, #391) merged nothing in a day: #391 failed only at the review stage on
reviewer infrastructure (read-only reviewers could not run uv/pytest) while every code stage and the full suite
passed. The owner cancelled the queued goals (#435–#445) and gave the order again in the Workspace panel. The GM now
lands each phase itself: P0-1 is goal #391's iteration-2 branch (`gm/p0-1-iter2-774`) merged onto main and checked
with the full suite and headless screenshots; later phases are written and checked the same way, one commit per phase.

## P0-2 Dashboard + approvals queue
- One call per card group: `GET /api/dashboard?period=24h|7d|30d` (dashboard.py) for counted figures,
  `GET /api/approvals` (approvals.py) for the queue; `/api/overview` and `/api/events` unchanged (UI-D-01).
- Trends compare the same count over the period before (spend, tasks done); no trend where nothing was counted.
- A project's phase (UI-D-06) is derived in `dashboard.phase` from its stored status, goals, open blockers and
  milestones; P0-4 reuses it.
- The list decides only follow-up proposals and decisions for a person (existing endpoints); goals, definition
  candidates, recruitments, experiments, draft requirements and account requests open their page (Review).
- Ranking (`retrieval`) events stay out of Recent activity: thousands of them, nothing a person follows.
- The old usage table (WEB-DASH-03) left the Dashboard; the limit windows stay on the Models page.

## P0-3 General Manager page
- The centre is the Projects page's command window (`projectMain`), not a copy: one code path for orders, topics,
  uploads and the admin console. The page adds the conversation list and the context cards.
- Plan approval is a pause, not a new goal state: `paused_for="plan"` after the plan is stored. Approve = resume
  (the stored iteration runs, nothing is planned again); revision = checkpoint `planned: False` + `revision` note,
  so the iteration is discarded and planned again with the note in the feedback. `workflow.goal_transitions` needs
  no change and every existing pause rule (project paused, budget) still applies.
- Off by default (`require_plan_approval`), as the owner's standing order is to approve plans; a project may switch
  it on for itself (stricter only).
- `_goal_dict` gains `topic` (additive) so the context panel can follow the open topic.

## P0-4 Projects
- `#/projects` without an id is the portfolio; with an id the existing three-panel workspace. The left list's title
  links back to the portfolio, so the workspace keeps its fast project switching.
- New tabs render from `projtabs.js` into the existing tab bar (`projectMain` modes), so Orders & reports, Plan and
  the console keep their code.
- The approval policy is the project's existing stricter rules plus `require_plan_approval`, not a second JSON
  column: one place decides, `policies.effective`.
- The Evaluation tab's action is labelled Reject, because `loop.reject` ends a goal; a reopen ("return to
  development") would be a new goal transition and stays open.

## P1-1…P1-3
- Teams are rooms plus live goals' teams (UI-D-07); the Agents page keeps its view id `agents` and is titled
  Teams & Agents, so links and saved layouts keep working.
- Workflows are read-only: retrying a task inside a running DAG belongs to the loop; the drawer points to the goal.
- A release record is written after the goal is marked approved (the mission report needs a finished goal) and before
  any completion message or notification; a failure leaves the merge in place and says so.

## Follow-ups (2026-11-09, owner: "all issues are approved")
- A merge refused because the main checkout had uncommitted changes (or was on another branch) is retried, not
  merged in another worktree: a dirty checkout means someone is editing it, and merging elsewhere would leave its
  working files behind the branch head. `ownerpolicy.retry_refused` runs every minute from the ScheduleThread (no new
  thread) and only touches the goal once the checkout is clean, so a dirty tree causes no repeated refusals.
- The records after a merge (`selfevolve`, `requirements`, `dependencies`) live in `loop._on_merged`, used by
  `approve()` and the merge without approval; `_push` stays separate because the two paths push differently. A
  failing record is logged: the merge has already happened.
- V22: a topic's assigned agents are a preference, not a filter. Idle ones (or their clones) are ranked first; when
  all are busy the role's other idle agents take the work, so a topic never waits on one agent or clones it.
- V15/V26 needed no code: Models → Routes → Assign models (`router.auto_routes`) gives `developer` Claude Code and
  `qa` Codex CLI and cleared the GM's own model.
- V28 raises the evidence, never lowers it: smoke repeats 1 → 2, a held-out suite (`python-final`, hidden checks)
  after a passed smoke suite, the canary 3 → 5 tasks, and a 50 % live floor besides beating the baseline. Each is a
  setting (Settings → Governance). A trial keeps the canary length it started with (`exp["window"]`).
