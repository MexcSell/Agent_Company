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
