# AICO-UI — redesign of the website's interface and functions

Requirements for the redesign the owner ordered on 2026-11-09 ("completely redesign the functions and interface of
the website based on the images and documents" in `/home/luca/update/update_3`, `update_4`, `update_5`), written
against the code of 2026-11-09 (`static/`, `api.py`, [WEBSITE.md](WEBSITE.md) = the site as it is today).

**Sources**

| Folder | Document | What it gives |
| --- | --- | --- |
| update_3 | `readme.docx` (Complete UI/UX Design Proposal) | Control-centre direction, keep rail and regroup, page compositions (Dashboard, Projects 3-panel, Agents tabs + agent card, Company map, Models, Evolution workflow, Research/Knowledge, Workspace 4 windows), interaction rules, P0–P2 priorities, engineering constraints |
| update_3 | `a-1/a-2/a-2-1/a-3.png` | Dashboard mockup: six KPI cards with trend, Active projects, Company lifecycle stepper, Pending approvals, Recent activity (Live), Model usage donut |
| update_3 | `other-3-1/2/3.png`, `other-1/2.png` | GM-first home: GM card with quick actions, project progress, company activity, team performance, pending approvals with Review/Reject; org/workflow/evolution illustrations |
| update_3 | `Screenshot_1/2.jpg` | Settings → Appearance (theme light/dark/system, **accent colours**, 21 screen formats) and Service/Technology cards — the screen formats exist already (`layouts.js`); the Node/React/PostgreSQL stack card is another product's, **not** a directive |
| update_4 | `UIUX.docx` (Autonomous AI Company — Website UI/UX Design) | GM-centred operating model, route list, pages 01–14, workflow/QA/evolution processes, homepage order |
| update_4 | `functional.docx` (AICO-FRS-001) | FR-001…FR-016, business rules BR-001…014, data model, API groups, NFR-001…012, acceptance tests AT-001…020 |
| update_4 | `autonomous_ai_company_website_uiux_design.pdf` (21 pages) | Routes, project status model, task record fields, workflow node types and statuses, research report sections, knowledge lifecycle, evaluation record fields, release rule, org-change proposal fields, notification card, deliverables + completion report, settings/autonomy levels, definition of complete, design tokens |
| update_4 | `1-x … 13-x.jpg`, `func-*.jpg` | Inspiration per page number of UIUX.docx (third-party dashboards) and the docx's own flow diagrams |
| update_5 | `1-1, 2-1, 3-3, Screenshot_1/2.jpg` | **AICO** identity (Clarity, Control, Transparency, Evolution), grouped sidebar (COMPANY / INTELLIGENCE / EVOLUTION / Settings), Company Overview, GM conversation with "Awaiting plan approval · Approve plan · Request revision", final palette, typography, spacing, three experience levels |
| update_5 | `3-1-x … 13-1.jpg` | Inspiration per page (org chart, agent cards, workflow canvas, knowledge graph, QA analytics, compare experiments, org map, approval inbox) |

Third-party screenshots are **style references**, not requirements; their names, numbers and people are illustrative
(FR-002.4, NFR-010: the site never shows placeholder figures as live data).

**Conventions.** IDs `UI-<AREA>-nn`. Status: `done` (met today, file named), `partial`, `open`. Every requirement keeps
the rules of [WEBSITE.md §20](WEBSITE.md) and CLAUDE.md: server-side permission checks, `_audit` on every administrative
call, quiet live updates, no UI state in a second store, Workspace stays the administrator's own tool.

---

## 0. Decisions

- **UI-D-01 Frontend redesign, additive backend.** The redesign keeps every API contract, the auth and permission
  model, the goal lifecycle and approval logic, Workspace sessions, and the audit (update_3 §8 "engineering
  constraints"). New functions add endpoints and tables (`db.py` + `_add_missing_columns`); none changes an existing
  response shape. Stack stays FastAPI + SQLite + plain JS (no React/Node build step).
- **UI-D-02 Information architecture: grouped, labelled sidebar** (update_4 §2, update_5 2-1, PDF p3) instead of the
  icon-only rail. Groups and default order:
  - **Company**: Dashboard, General Manager, Projects, Goals, Teams & Agents, Company map, Workflows, Deliverables
  - **Intelligence**: Research, Knowledge, Evaluation & QA, Models
  - **Evolution**: Agent Evolution, Organization Evolution, Change History
  - **System**: Workspace, Machines, Security, System

  The sidebar collapses to the icon rail (button and below 1100 px) and becomes a bottom bar on phones. Notifications,
  Audit log, Settings and Users stay top-bar buttons (CLAUDE.md rule kept); Notifications remains also a full page
  reached from its button and from every notification link.
- **UI-D-03 Palette: update_5's tokens** (the newest folder; update_3's and update_4's differ). Dark (default):
  app background `#0B1220`, sidebar `#111C2D`, panel `#142033`, elevated panel `#1E293B`, border `#24324A`, text
  `#F9FAFB`, secondary text `#9CA3AF`, primary `#2563EB`, success `#16A34A`, warning `#D97706`, error `#DC2626`,
  info `#0EA5E9`. Light theme uses the same semantic tokens with light values; both share one layout.
  Accent is selectable (update_3 Screenshot_1: indigo, violet, blue, green, amber, rose, grey), per browser.
- **UI-D-04 Three experience levels** (update_5 Screenshot_2) map onto the existing modes: Level 1 *Owner Command
  Center* (company status, GM communication, approvals, deliverables) = Simple mode and the top of the Dashboard;
  Levels 2 *Management & Orchestration* and 3 *Engineering & Intelligence* = Advanced mode. No third mode.
- **UI-D-05 Saved navigation migrates.** `/api/ui/nav_layout` gains a `groups` form (`{groups: [{key, title,
  items}], hidden}`); a saved `top/bottom` layout is read as the default groups with its own hidden list, so no
  administrator keeps a broken order. Settings → Navigation edits groups (rename, reorder, move items).
- **UI-D-06 Project lifecycle display.** The design's Draft → Planning → Ready → Active → Blocked → QA/Review →
  Release ready → Completed → Archived (+ Failed, Cancelled) is a **phase** derived from the project's status and its
  goals (no new stored states): Planning = only goals in planning; Blocked = an open blocker; QA/Review = a goal
  awaiting approval; Release ready = all milestones met; the stored statuses (active, paused, completed, archived,
  cancelled) keep their meaning and transitions (`projects.set_status`).
- **UI-D-07 "Teams" are rooms and goal teams.** A permanent department/team = a room (`organization.py`, parent,
  lead, enabled, budget); a project-specific team = a goal's team (`teams.py`). No third team model.
- **UI-D-08 Evidence before claims** (update_3 §5, PDF p13, BR-010): every progress, completion and acceptance shown
  comes from the gate (`Task.gate`, checks, review verdicts, `mission.py` labels), never from an agent's own text.

---

## 1. Design system and application shell (P0)

- **UI-SHELL-01 Tokens.** One token set in `style.css` (`:root`, `[data-theme]`) per UI-D-03: colours, radius
  12 px cards / 8 px controls, 4 px spacing unit (8–16 px inside cards, 12–16 px between cards, 20–24 px page
  padding), 1 px borders, body 14 px, page title 22–26 px, section title 16–18 px, metadata 12 px, Inter/system UI.
  `workspace.css` reads the same tokens. *Acceptance:* no hard-coded colour remains in the page CSS outside the token
  blocks (a test greps `style.css`/`workspace.css` for hex colours outside `:root`/theme blocks, with an allow-list).
- **UI-SHELL-02 Components.** Shared classes used by every page: page header (title, subtitle, primary action on the
  right), KPI card (icon, value, label, optional trend and link), card with header + "View all", status badge
  (operational/success, pending/warning, failed/error, info, neutral), progress bar, stepper, timeline/activity
  list, table, empty state, tabs, drawer (right-side panel for details without leaving the page), toast, confirm
  dialog. *Acceptance:* a style-guide page (Settings → Appearance → "Components") renders each once.
- **UI-SHELL-03 Sidebar** per UI-D-02: brand block ("Agent Company · Autonomous Engineering Organization", status
  pill), group titles, labelled items with icon and badge, collapse button, active item highlighted, company status
  card at the bottom (operating / degraded / paused / maintenance from `/api/overview` + stop state), user block
  (name, role). Admin-only items are hidden for others (WEB-ROLE-03 unchanged).
- **UI-SHELL-04 Top bar**: breadcrumbs (page / project / goal or agent), **global search** (Ctrl+K: projects, goals,
  tasks, agents, knowledge, reports, files; UI-SRCH-01), system status pill, Simple/Advanced switch, emergency stop,
  notifications bell with count, audit, settings, users, screen format, theme, user menu with My Git settings.
- **UI-SHELL-05 Keep everything WEB-SHELL-04…16 does** (badges, favicon, live updates with quiet redraws,
  last-picture page switching, new-version reload, one modal at a time, icon buttons, screen formats, modes). Screen
  formats keep working with the sidebar (labels on/off = expanded/collapsed; rail bottom = bottom bar).
- **UI-SHELL-06 Responsive**: desktop (sidebar expanded), laptop (collapsed by default under 1280 px), tablet
  (collapsible, panels stack under 900 px), phone (bottom bar with Dashboard, GM, Projects, Notifications, More;
  approvals and notifications readable and actionable). *Acceptance:* headless screenshots at 1440, 1024, 768 and
  390 px show no horizontal scroll on Dashboard, GM, Projects, Notifications.
- **UI-SHELL-07 Routes.** Each page has a hash route (`#/dashboard`, `#/general-manager`, `#/projects/:id`,
  `#/agents/:id`, `#/goals/:id`, `#/teams`, `#/workflows`, `#/evaluation`, `#/evolution`, `#/organization`,
  `#/changes`, `#/deliverables`, `#/notifications`, `#/settings`); back/forward and links work; `ac.view` stays the
  fallback. *Acceptance:* opening `/#/projects/1` shows project 1.

## 2. Dashboard — Company Control Hub (P0)

- **UI-DASH-01 Header**: "Company Overview", date, company status (operating / degraded / paused / maintenance) and
  a period menu (24 h, 7 d, 30 d).
- **UI-DASH-02 KPI row** (each opens its filtered list): Running goals, Awaiting approval, Active projects, Busy
  agents / registered, Tasks running · blocked, Spend in period (with budget when set). Trend arrows only where the
  history exists (`/api/overview` + `usage`); no invented trend.
- **UI-DASH-03 General Manager card** (update_3 other-3-x, update_5 2-1): GM name, model, status, "n decisions need
  your attention", composer shortcut and quick actions *Create a project*, *Check project status*, *View reports*,
  *Manage teams* → the GM page.
- **UI-DASH-04 Active projects**: name, phase (UI-D-06), progress bar, team count, next milestone; opens the project.
- **UI-DASH-05 Company lifecycle**: the Discover → Plan → Execute → Critique → Validate → Learn stepper of the live
  goals (the company map's derivation), "n of 6 stages" per selected goal.
- **UI-DASH-06 Pending approvals** (one queue, UI-APPR-01): goals awaiting merge, decisions for a person, definition
  candidates, recruitment, experiments, proposals, org-change proposals — each with **Review** (opens the item) and,
  where the server allows it from the list, **Approve**/**Reject**.
- **UI-DASH-07 Recent activity (Live)**: newest events with icon by kind, link to the affected object, "n min ago".
- **UI-DASH-08 Model usage**: donut of token share per model in the period + the limit windows (WEB-DASH-03 data).
- **UI-DASH-09 Team performance**: per room accepted / failed tasks in the period (`performance.py`), as bars.
- **UI-DASH-10 Recent completion reports and evolution events**: the last finished goals with their mission report
  link; the last definition/experiment/org changes.
- Status: partial (WEB-DASH-01…03 exist: six figures, activity, usage table).

## 3. General Manager page (P0) — the primary control surface

- **UI-GM-01 Layout**: left = conversations (projects and their topics, newest first, search); centre = the
  conversation with the GM; right = context panel (current project, its team, open decisions, latest report,
  pending approvals of that project).
- **UI-GM-02 Composer**: text, attachments (PDF, images, documents; existing uploads), classification, Send; the
  `DECISION:`, `/status`, `/standup`, `/summary`, `/stop`, `/sync`, `/help` commands keep working
  (`POST /api/projects/{id}/command`). A message to "New project…" asks the GM to analyse and propose a project
  (a `PROPOSAL[product]` / documented case, S10-R-01) instead of starting code (FR-003.5, BR-003).
- **UI-GM-03 Structured GM replies** shown as cards, not raw text: *Requirement analysis* (objectives, functional and
  non-functional requirements, constraints, assumptions, acceptance criteria, open questions), *Plan* (tasks with
  role, dependencies, estimate), *Team* (members, added/released), *Status*, *Completion report*. Built from the
  stored plan, requirements, decisions, team and mission report of the goal the message belongs to.
- **UI-GM-04 Plan approval**: when a project's approval policy requires it (UI-PROJ-06), a goal stops after planning
  as **Awaiting plan approval** with **Approve plan** / **Request revision** (a note that goes back to the planner).
  *Open*: new goal state `awaiting_plan` in `workflow.goal_transitions` (planning → awaiting_plan → running |
  planning), off unless the project's policy asks for it; audited; AT-003/FR-003.4: the GM's clarification questions
  appear as a card with an answer box and the goal waits (`paused_for="clarification"`).
- **UI-GM-05 Traceability** (FR-003.7): each instruction gets its id (the message id) and links to the goal(s),
  requirements, tasks, merge and deliverable it produced.
- **UI-GM-06** For admins, the GM's Claude Code panel (WEB-PROJ-09) is a tab of this page ("Console"), unchanged.
- Status: partial (command window, topics, uploads, GM panel exist in Projects; the page, cards, plan approval and
  clarification are open).

## 4. Projects and the project workspace (P0)

- **UI-PROJ-01 Portfolio**: cards or table (toggle) with name, phase, priority, progress, manager, teams, agents,
  tasks done/total, next milestone, deadline, last activity; search (name, id, requirement, repository); filters
  (planning, active, blocked, review, completed, archived, failed); sort (priority, last activity, deadline,
  created, progress); **New project**.
- **UI-PROJ-02 Project workspace tabs**: Overview, Requirements, Teams, Tasks, Workflow, Files, Evaluation, Reports,
  plus the existing **Orders & reports** (command window) and **Plan** content distributed: milestones, risks,
  blockers, insights, action items, schedules → Overview / Reports.
  - *Overview*: phase, progress, milestone timeline, team cards, current tasks and blockers, risks and decisions,
    requirement changes, latest activity.
  - *Requirements*: original brief and documents, functional / non-functional / stories / use cases, acceptance
    criteria, change history (versions), traceability (WEB-PROJ-19).
  - *Teams*: rooms taking part, goal teams with leads and members, capacity.
  - *Tasks*: board by stage (queued, ready, running, waiting, blocked, failed, succeeded, cancelled) and table with
    the task record fields (UI-TASK-01).
  - *Workflow*: the project's goals' task graphs (UI-WF-01).
  - *Files*: repository browser (existing preview), spec artifacts (`.claude/specs/<slug>/`), reports, SBOMs.
  - *Evaluation*: gate results per goal, defects, acceptance status (UI-QA-02).
  - *Reports*: mission reports, decisions/ADRs, progress summaries, completion report.
- **UI-PROJ-03 Header** keeps WEB-PROJ-05/06 (pills, edit, catalog, sync, workspace, status menu).
- **UI-PROJ-04 Right panel**: assigned agents with model, status and availability (WEB-PROJ-22).
- **UI-PROJ-05 Registration form** adds to WEB-PROJ-03: Requirements (text), Documents (attachments), Target
  outcome, Related project, Team strategy (reuse / form new / GM recommends), Approval policy (UI-PROJ-06). Stored
  on the project (new columns: `target_outcome`, `related_project_id`, `team_strategy`, `approval_policy` JSON).
- **UI-PROJ-06 Approval policy** per project: which steps need the owner — plan, merge, new agents (existing
  `recruit_requires_approval`), dependency changes, org changes. Merge approval keeps the owner-policy `auto_merge`
  rule; a project may only be stricter (`policies.effective`).
- Status: partial (3-panel page, form, plan tab exist; tabs, portfolio filters/sort, new fields open).

## 5. Teams & Agents (P1)

- **UI-TEAM-01 Teams view** (tab of Teams & Agents): departments (rooms) as cards: name, purpose, lead, members,
  current tasks, performance (accepted/failed, cost), status (enabled/disabled), daily budget, history (positions);
  project teams (active goals' teams) with lifecycle *project-specific*. Recommended initial departments (PDF p8)
  are documented as the target of org evolution, not created by the redesign.
- **UI-TEAM-02 Org chart** (existing Organisation tab) drawn as a hierarchy with connectors (update_5 5-1), cards
  with role, status dot, model, tools; a click opens the agent drawer, never another page (update_3 §C).
- **UI-AG-01 Agent grid** (update_5 6-1): filter by room, status, model, skill; card with role, model, status
  (active, idle, working, blocked, failed, paused, retired — FR-006.2), runs, success rate, last activity.
- **UI-AG-02 Agent drawer tabs** (update_4 p8 / PDF p9) mapped onto the existing card: Overview (Profile), Capabilities
  (skills with evidence, tools, plugins, MCP grants), Agent code (Definition + diff), Configuration (model, autonomy,
  permissions, quotas), Tasks (Runs), Evaluation (certification, success, cost, latency), Evolution (candidates,
  versions, restore), Memory (the agent's knowledge items and lessons), Logs (runs' tool calls and errors),
  Relationships.
- **UI-AG-03** Tools & MCP, Skills & plugins, Marketplace, Agent network stay as tabs (WEB-AG-12…15).
- Status: partial.

## 6. Workflows (P1)

- **UI-WF-01 Execution graph** per goal: tasks as nodes (role, agent, status colour, duration, retries), edges from
  `Task.depends_on`, parallel branches side by side, gate and approval as nodes; status legend per PDF p10 (Queued,
  Ready, Running, Waiting, Blocked, Failed, Succeeded, Cancelled) mapped from `Task.status`/`Task.stage`.
- **UI-WF-02 Workflows page**: all running goals' graphs (one per row, collapsible) with filters (project, status,
  blocked only); a blocked task shows its blocker and responsible agent.
- **UI-WF-03 Node detail** drawer: inputs, outputs, acceptance checks, tool calls, logs, handoffs (blackboard
  entries), with **Retry** and **Retry with another agent** for a failed task (FR-007.7; open: endpoint
  `POST /api/tasks/{id}/retry` with optional `agent_id`, admin or `run` on the project, audited).
- **UI-WF-04 Agent communication**: the goal's blackboard messages as a thread beside the graph (FR-008.5).
- **UI-WF-05 Templates**: the workflow templates (`strategies.py`) with versions (read-only list here; changes stay
  in Evolution).
- Status: open (data exists: `depends_on`, `stage`, runs, blackboard).

## 7. Deliverables (P1)

- **UI-DEL-01 Release record.** When a goal is merged, one record is stored: project, goal, title, merge commit, branch,
  tag (if any), remote URL, SBOM path, mission report (Markdown, stored at that moment, checksum), acceptance summary
  (labels), who approved, when. New table `deliverables`. *Acceptance:* `loop.approve` of a test goal stores one row
  and a second approve stores none; the completion notification is sent only after the row is written (PDF p17
  "do not report completion if storage fails").
- **UI-DEL-02 Deliverables page**: per project the release records (Ready / Superseded), each with Final report,
  Source code (commit link), QA report (gate results), Build artifacts (SBOM, changed files), Download (Markdown).
- **UI-DEL-03 Completion report** sections (PDF p17): overview, requirements and fulfilment, features, architecture
  (ADRs), team summary, development, evaluation, limitations, deployment notes, artifacts, next steps — built from
  `mission.py` plus traceability, team history and ADRs.
- Status: open (mission report and SBOM exist; the stored record and page are new).

## 8. Evaluation & QA (P1)

- **UI-QA-01 Overview**: test executions, passed / failed / blocked / pending (FR-011.12) from gate results over the
  period, flaky checks, defects by priority, acceptance status per goal; charts pass rate per day and passed vs failed.
- **UI-QA-02 Latest evaluation** per goal: functional (acceptance checks), integration (quality commands), security
  (secrets/dependency audit stage), final acceptance (review verdicts) with the evidence links.
- **UI-QA-03 Defects** (WEB-PERF defects) with lifecycle actions; **Return to development** for a goal awaiting approval
  (= reject with reason, which reopens it, AT-014).
- **UI-QA-04 Eval runs and benchmarks** (existing `eval` results, suites, dimension summaries, no combined score).
- The existing Performance page's content moves here (rooms, KPIs, operational metrics stay as tabs).
- Status: partial.

## 9. Agent Evolution (P1)

- **UI-EVO-01 Candidate review** (update_3 §C/F, update_4 p11): per agent current version vs proposed version,
  reason, diff, evaluation results (baseline vs candidate, sample sizes, regressions), approval state, **Apply**,
  **Discard**, **Restore previous version**. Sources: `definition_candidates`, `agentdefs` versions, experiments.
- **UI-EVO-02 Workflow stepper**: Baseline → Candidate → Experiment → Regression/safety → Human approval →
  Deploy/rollback, the current step highlighted for each candidate.
- **UI-EVO-03 Metrics** per agent version (UIUX p11): task success, evaluation score, failure rate, regressions,
  cost, time, tool-call failure rate — only those measured; "not measured" otherwise.
- **UI-EVO-04** Existing sections (learning rotation, trials, recruitment, experiments, strategies, learning plans,
  code evolution) stay as tabs.
- Status: partial (all data exists; the comparison view is new).

## 10. Organization Evolution (P1)

- **UI-ORG-01 Organization versions.** Every structural change (room created/edited/enabled/disabled, lead change,
  agent moved to another room, role created/retired) stores a snapshot version (rooms with parent, lead, enabled,
  members) with reason, actor and time. New table `org_versions` (`version`, `snapshot` JSON, `reason`, `actor`,
  `at`). *Acceptance:* `organization.update_room` writes one version; two changes write two.
- **UI-ORG-02 Change proposals** (PDF p15): current structure, proposed structure, reason, expected impact, evidence,
  migration plan, approval, outcome review. Stored as a proposal of kind `org` (existing proposals list) whose
  approval by a person applies it (`organization` calls) and records a version; never applied by an agent alone.
- **UI-ORG-03 Compare** two versions side by side with the differences highlighted (added, removed, moved).
- **UI-ORG-04 Restore** a previous version (admin, confirm, audited): rooms' parent, lead and enabled state are set back;
  agents are not deleted.
- Status: open (room history exists as positions only).

## 11. Change History (P2)

- **UI-HIST-01** One timeline of changes to the company: agent definition versions, configuration versions, playbook
  versions, org versions, route changes, governance setting versions, merges, self-evolution deploys/rollbacks —
  filter by kind, agent, project, period; each entry links to its diff or record and its restore action where one
  exists. Read from existing tables via a new `GET /api/changes` (admin; paged).

## 12. Research & Knowledge (P2)

- **UI-RES-01** Research page in three areas: requests and ongoing research, sources and investigation steps
  (`tool_steps`), reports awaiting review; a report shows objective, scope, sources, findings, comparison,
  recommendation, risks, implementation impact, confidence, limitations, and its claims with citations.
- **UI-KN-01** Knowledge page: search, filters by scope (company, project, team/room, agent, research, evaluation),
  review queue, contradictions side by side, graph explorer, version history (existing knowledge.js content restyled).

## 13. Notifications and approvals (P0 for the queue, P1 for the page)

- **UI-APPR-01 Approval queue** `GET /api/approvals`: one list of what waits for a person — goals awaiting approval,
  decisions sent to a person, definition candidates, recruitment requests, experiments in review, pending proposals
  (incl. `org`), draft requirements — each with kind, title, origin (project/agent/team), age, link. The Dashboard,
  the GM context panel and the Notifications page use it.
- **UI-NOTE-01 Notifications page** (update_5 13-1): All / Unread / Action needed; category (completion, approval,
  blocker, failure, quality, evolution, security), severity, source, affected object link, required action, read
  state; the right pane shows the selected item with its actions. Duplicates are grouped ("×n").
- Status: partial (notifications exist; categories, grouping and the approvals endpoint are open).

## 14. Settings (P2)

- **UI-SET-01** Settings in sections: Appearance (theme light/dark/system, accent, screen format, components preview),
  Company (name, timezone), Navigation (groups, UI-D-05), Governance (approval policies, autonomy levels G0–G4 shown with
  the PDF p18 names: Observe, Prepare, Execute with limits, Approval required, Restricted), Models & routing (link),
  Tools & integrations (link), Limits & budgets, Knowledge & retention, Notifications, Connection, Metrics.

## 15. Global search (P1)

- **UI-SRCH-01** `GET /api/search?q=` (signed in; results filtered by the user's project permissions): projects, goals,
  tasks, agents, knowledge items (FTS index), research reports, files by name in the project's repository; grouped
  results, keyboard navigation, Enter opens.

---

## Delivery phases (goals for project 1)

Each phase is one goal with `uv run pytest -q` as its check; phases touch disjoint files where possible and run in
this order (G-R-08 serialises overlapping paths). Visual review against the mockups is done by the GM with the
headless browser (screenshots at 1440/1024/768/390 px).

| Phase | Requirements | Main files |
| --- | --- | --- |
| P0-1 Design system + shell | UI-D-02/03/05, UI-SHELL-01…07 | `static/style.css`, `static/workspace.css` (tokens only), `static/app.js` (sidebar, top bar, routes), `static/index.html`, `api.py` (nav_layout groups), tests |
| P0-2 Dashboard + approvals queue | UI-DASH-01…10, UI-APPR-01 | `static/dashboard.js` (new, moved out of app.js), `api.py` (`/api/approvals`), tests |
| P0-3 General Manager page | UI-GM-01…06 | `static/gm.js` (new), `api.py`, `workflow.py`/`loop.py` (awaiting_plan, clarification), tests |
| P0-4 Projects workspace | UI-PROJ-01…06 | `static/projects.js`, `projects.py`, `db.py`, tests |
| P1-1 Teams & Agents | UI-TEAM-01/02, UI-AG-01…03 | `static/agents.js`, tests |
| P1-2 Workflows | UI-WF-01…05 | `static/workflows.js` (new), `api.py` (task retry), tests |
| P1-3 Deliverables | UI-DEL-01…03 | `deliverables.py` (new), `loop.py` (record at merge), `db.py`, `static/deliverables.js`, tests |
| P1-4 Evaluation & QA | UI-QA-01…04 | `static/performance.js` → `static/evaluation.js`, `api.py`, tests |
| P1-5 Agent & Organization Evolution | UI-EVO-01…04, UI-ORG-01…04 | `static/evolution.js` (moved out of app.js), `organization.py`, `db.py`, tests |
| P1-6 Notifications + search | UI-NOTE-01, UI-SRCH-01 | `static/notifications.js`, `api.py`, tests |
| P2 Change history, Research, Knowledge, Settings, responsive pass | UI-HIST-01, UI-RES-01, UI-KN-01, UI-SET-01, UI-SHELL-06 | `static/*.js`, `api.py`, tests |

**Done when** (update_4 FRS §13 / PDF p19 adapted): the owner can run the company from the GM page; every page in
UI-D-02 exists and is reachable by route; approvals are one queue; completion is shown only from gate evidence and a
stored release record; agent and organization changes are versioned, comparable and restorable; WEBSITE.md describes
the new site; the full test suite passes.
