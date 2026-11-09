# WEBSITE — functional requirements of the dashboard (rebuild specification)

What the Agent Company website does, page by page, written from the code of 2026-11-08 (`src/agent_company/static/`,
`api.py`, `auth.py`, `prefs.py`) so that it can be rebuilt elsewhere. Every requirement names what the user sees,
what they can do, who may do it and which API call carries it. The Workspace page has its own, more detailed
specification in [WORKSPACE.md](WORKSPACE.md); §15 here summarises it and lists what changed since.

**Conventions**

- IDs are `WEB-<AREA>-nn`. "Admin" means a user with `is_admin`; "user" means any signed-in, approved user.
- Every administrative action is written to the audit log on the server (`_audit`); every permission is checked by the
  server, the page only hides what a user may not do.
- "Confirm" means a browser confirmation dialog; "prompt" means a browser text prompt; "toast" means a short message at
  the bottom right.
- API paths are relative to the site root; `{id}` is a path parameter.

---

## 1. Users, roles and permissions

- **WEB-ROLE-01** Two kinds of user: administrators (may do everything) and other users with five permissions:
  - `projects`: `"own"` (projects they created, default), `"all"` regular projects, or a list of project ids (plus own);
  - `run` (default on): give orders, start goals, ask the research room;
  - `create_projects` (default off): create projects (in a new workspace; choosing folders and commands stays admin-only);
  - `manage_agents` (default off): create, clone, pause, retire and edit agents, and evolution actions;
  - `manage_models` (default off): add, edit, test and delete models, and change routes.
- **WEB-ROLE-02** A delegation gives one user one right for one project until a date: today the right is
  `approve_goals` (approve and merge that project's goals). It is checked on every call and expires on its own.
- **WEB-ROLE-03** Admin-only pages: Workspace, Machines, Security, System, Knowledge, Settings, Audit log, Users. They
  are not shown on the rail for other users.

## 2. Sign-in (no passwords)

- **WEB-AUTH-01** The site signs a browser in by a *device secret*: 32 random bytes made in the browser, kept in
  `localStorage` (`ac.device`), sent with `POST /api/auth/device`. There is no password.
- **WEB-AUTH-02** Registration screen ("Agent Company"): one field **Name** (3–32 characters, letters, digits, `.`,
  `_`, `-`, starting with a letter or digit) and **Register**. It sends `POST /api/auth/register` with the name, the
  secret and device information (browser and OS label, user agent, screen size, language, time zone).
- **WEB-AUTH-03** The first account becomes the administrator and can only register from the server itself
  (localhost). Later registrations wait for an administrator: the screen shows "Waiting for approval" with
  **Check again**, and retries by itself every 10 seconds; once approved, the browser is signed in automatically.
- **WEB-AUTH-04** A rejected or disabled account sees the reason ("Registration was rejected.", "This account is
  disabled.") and no form. An unknown device secret is dropped and the registration screen shows again. When the
  server cannot be reached, the waiting screen says so and keeps retrying.
- **WEB-AUTH-05** A session lasts 30 days. `GET /api/auth/me` returns the user, permissions, delegations and UI mode.

## 3. Application shell (every page)

- **WEB-SHELL-01 Sidebar.** Grouped, labelled navigation: Company (Dashboard, Projects, Goals, Agents,
  Company map), Intelligence (Research, Knowledge, Performance, Models), Evolution, and System (Workspace,
  Machines, Security, System). Registered future pages take their default group positions; unknown ids go to
  their group's end. The brand reads “Agent Company · Autonomous Engineering Organization”. Active state,
  icons and badges remain. A bottom status card reports operating, degraded, paused or stopped from
  `/api/overview`; the user block shows name and role. Admin-only views remain hidden for other users.
  Collapse is remembered per browser, defaults below 1280px, and becomes a bottom bar below 700px with More.
  Screen formats retain labels, bottom bar and right-side mirror support.
- **WEB-SHELL-02 Navigation order.** Admins edit group titles, reorder groups and drag items within or between
  groups in sidebar Edit mode or Settings → Navigation. `GET/PUT /api/ui/nav_layout` uses
  `{groups:[{key,title,items:[ids]}],hidden:[ids]}`; an old `{top,bottom,hidden}` setting is read as the default
  groups with each hidden id removed from its group, plus the hidden list. Admin writes remain audited and shared
  by all users.
- **WEB-SHELL-03 Top bar.** Breadcrumbs show the page then project, goal or agent via `window.setCrumbs`.
  The search entry “Search projects, goals, agents… (Ctrl+K)” supports Ctrl/Command+K, arrow keys and Enter;
  this phase searches visible pages and open projects client-side using the existing `/api/projects` endpoint
  (60-second cache). Server-wide search is P1-6. User name, Simple/Advanced, stop pill, Notifications, Audit,
  Settings, Users, emergency stop, My Git, Screen format and theme remain in the top bar.
  Hash routes `#/<view>`, `#/projects/<id>`, `#/goals/<id>` and `#/agents/<id>` support direct links and
  back/forward; `ac.view` remains the fallback without a hash. `show()` writes the hash with `pushState`, so it
  triggers no second render. Closing a goal or agent window replaces the hash with the page's hash.
  WEB-SHELL-04…16 behaviour is kept, including quiet redraws and last-picture switching (§3 WEB-SHELL-17 for the
  tokens and components).
- **WEB-SHELL-04 Badges.** Every few seconds (and on every live event) `GET /api/overview` gives the counts:
  Users = registrations waiting; Notifications = unread; Audit log = audit records newer than the last one this
  browser showed (counted from the first visit; opening the Audit log resets it); Projects = goals awaiting
  approval + goals running.
- **WEB-SHELL-05 Emergency stop button (admins).** One click from every page: a prompt "Emergency stop (E5): why stop
  all autonomous work now?", then `POST /api/emergency` (all goals cancelled, all agent processes ended; the toast
  gives both counts). While a stop is on, the button is red and leads to System.
- **WEB-SHELL-06 Stop level for everyone.** While any stop (E1–E5) is active, every user sees a pill "E<n> stop" in
  the top bar with its scope in the tooltip.
- **WEB-SHELL-07 Browser tab icon.** The favicon is drawn live: brand tile, red while a stop is on, grey while the
  server is unreachable; the number of running goals in the centre; dots for unread notifications (top right),
  goals waiting for approval (bottom left) and unseen audit records (bottom right). The tab title spells out the same.
- **WEB-SHELL-08 Live updates.** The page subscribes to `GET /api/stream` (server-sent events); on any event the
  current page is redrawn quietly within 0.7 s. If the stream is down, the page polls every 5 seconds and retries the
  stream every 10 seconds. A quiet redraw is skipped while a dialog is open, a form has focus, or the tab is hidden.
  Workspace and Machines are never redrawn by live events (they update themselves).
- **WEB-SHELL-09 Page switching.** The last picture of a page is shown at once when the user returns, inert, while the
  fresh data loads (a top loading bar and a pending rail icon). A load that never answers unlocks the page after
  15 seconds. A slow page that finishes after the user moved on never overwrites the newer page.
- **WEB-SHELL-10 New version.** When `/api/overview` reports a new build, the next page switch reloads the site, unless
  a Workspace editor tab has unsaved changes.
- **WEB-SHELL-11 Dialogs.** One modal at a time; closes with ×, a click outside it, or Escape.
- **WEB-SHELL-12 Buttons as icons.** Buttons with a known label are drawn as icons with the label as tooltip and
  accessible name, already when the page appears (no change of shape afterwards).
- **WEB-SHELL-13 Theme.** Dark by default, with light/dark/system choices kept per browser; system follows OS changes.
- **WEB-SHELL-14 Screen formats.** A picker of 21+ presets kept per browser: Standard, Compact, Comfortable, Focus
  (command window only), Zen (centred), Wide chat, Agent ops, Mirror (rail on the right), Command centre, Stacked,
  Presentation, Large text, Dense data, Ultrawide (labelled rail), Laptop 13", Tablet and Mobile (rail at the
  bottom), Cinema (centred 1440 px), Floating cards, Labelled rail, Square. Each sets scale, density, corner radius,
  rail position and labels, maximum width, and the Projects page's panel order, sizes, hidden panels and
  orientation. Panel widths dragged by hand override the preset; "Widths reset" restores them.
- **WEB-SHELL-15 Simple / Advanced.** Each user switches their own mode (`PUT /api/auth/me/ui_mode`). Simple mode
  opens the Mission page (§5); Advanced opens the Dashboard. The Mission page is on the rail only in Simple mode.
- **WEB-SHELL-16 My Git settings** (every user; admins for anyone from Users): name, email and branch used for
  commits (`PUT /api/users/{id}/git`); GitHub access by a personal access token (**Save token**, **Remove**, shown
  only as a hint) or by an SSH key the server creates (**Create SSH key**, **Copy** the public key, **Delete key**);
  **Test GitHub access** (`POST /api/users/{id}/git/test`). Pushes use the pushing user's own token or key.
- **WEB-SHELL-17 Design system.** `style.css` holds the update_5 semantic tokens: dark by default, with a light set
  under the same names. Spacing is on a 4px unit, cards have a 12px radius and controls 8px, the font is
  Inter/system-ui and body text is 14px. The old token names (`--bg`, `--panel`, `--accent`, …) are aliases of the new
  ones, so existing pages keep working. `workspace.css` uses the same tokens and has no palette of its own. Seven
  accent choices are kept per browser (`ac.accent`). The shared components are page head, KPI, card with "View all",
  status badge, progress, stepper, timeline, drawer, empty state and tabs. Settings → Appearance → Components shows
  each of them once.

## 4. Dashboard

- **WEB-DASH-01** Header "Company Overview" with the date, the company status (Operating, Paused, Degraded, Stopped,
  from `/api/overview`) and a period menu (Last 24 hours, 7 days, 30 days; kept per browser, `ac.dashPeriod`).
- **WEB-DASH-02** Six KPI tiles, each a link: Running goals (queued · paused), Awaiting approval (the approvals
  queue's total), Active projects (blocked), Busy agents / registered, Tasks running · blocked (tasks done against
  the period before), Spend in the period (against the period before). A trend is shown only when the period
  before was counted.
- **WEB-DASH-03** General Manager card: the GM's name, model, state, how many items wait for a person, a message box
  that sends to a chosen project's command window and opens it, and *Create a project*, *Check project status*,
  *View reports*, *Manage teams*.
- **WEB-DASH-04** Active projects: name, phase (Draft, Planning, Active, Blocked, QA / Review, Release ready, Paused;
  derived, `dashboard.phase`), progress (`management.progress`), live goals, agents, next milestone (overdue badge).
- **WEB-DASH-05** Pending approvals from `GET /api/approvals`: goals awaiting merge, decisions for a person,
  follow-up proposals, definition candidates, recruitments, experiments in review, draft requirements per project
  and account requests; **Review** opens the item, **Approve**/**Reject** only for decisions and follow-up proposals.
- **WEB-DASH-06** Company lifecycle: the Discover → Plan → Execute → Critique → Validate → Learn stepper of a chosen
  live goal (the Company map's derivation) and how many live goals are at each step.
- **WEB-DASH-07** Recent activity (Live): the eight newest events but ranking runs, coloured by outcome, linked to
  their goal. Model usage: a donut of each model's token share in the period with runs and tokens. Team
  performance: per room the share of finished tasks accepted, as bars. Recent completions: goals merged or failed
  in the period with their mission report.
  Data: `/api/overview`, `/api/dashboard?period=…`, `/api/events`, `/api/approvals`. A live redraw replaces a card
  only when it changed, so the message box keeps its text.

## 4a. General Manager

- **WEB-GM-01** Route `#/general-manager/<project id>`. Left: Conversations — every project the user may see with its
  live goals, the selected one with its topics; search over project and topic names; *New project*.
- **WEB-GM-02** Centre: the project's command window, the same one the Projects page shows (orders and reports,
  topics, search, attachments, classification, `DECISION:` lines and slash commands; Plan tab; for administrators the
  GM's Claude Code/Codex console). The file pane is left out.
- **WEB-GM-03** Right: the project (phase, progress, GM model, next milestone); each goal whose plan waits for
  approval with **Approve plan** and **Request revision** (asks what to change); the focused goal (a click on one of
  its messages focuses it; by default a plan waiting for approval, else the newest goal of the topic) as cards:
  Goal (status, iteration, tasks passed, cost, merge state), Requirement analysis (objective, linked requirements,
  allowed paths, acceptance criteria of the plan), Plan (this iteration's tasks with stage, role, dependencies,
  model), Team (joined, added, released), Completion report (verified / partial / failed labels, cost, time,
  limitations, Markdown link) once finished, Decisions for you (approve/reject); then the project's other items in
  the approvals queue and its agents.
- **WEB-GM-04** Setting `require_plan_approval` (Settings, or a project's own stricter rule): a goal stops after
  planning until a person approves; benchmark, improvement, A2A and room-trial goals never wait.

## 5. Mission (Simple mode)

- **WEB-SIMPLE-01 New mission** form: Project, Goal ("What should be achieved"), Budget (USD), Deadline, Files
  (attachments); **Start mission** sends `POST /api/simple/missions`.
- **WEB-SIMPLE-02** Below, a board of the user's missions (`/api/simple/overview`) with their state and resources,
  each with **Open**, and for finished ones **Mission report** and **Open goal**.
- **WEB-SIMPLE-03 Mission report** (`GET /api/goals/{id}/report`): Objective, Plan, Tasks (each *verified*,
  *partial* or *failed*, decided only from the gate's acceptance checks and review verdicts, never from what an
  agent wrote), Verification, Cost, Time (against the deadline), Remaining limitations, Lessons, Recommendations.
  The same report is available as Markdown (`?format=md`).

## 6. Projects

- **WEB-PROJ-00** `#/projects` is the portfolio: cards or table (kept per browser), search over name, id, description
  and repository, filter by phase (Open, All, Draft, Planning, Active, Blocked, QA / Review, Release ready, Paused,
  Completed, Archived, Cancelled), sort (priority, last activity, deadline, created, progress), *New project*. A card
  shows phase, progress, manager, agents working, tasks done/total, priority, next milestone, deadline and last
  activity (`GET /api/projects-portfolio`). `#/projects/<id>` opens the workspace below; its list's title links back.
- **WEB-PROJ-00a** Workspace tabs: Overview (status with outcome, team strategy, budget, deadline; milestones;
  current tasks; blockers and risks; team; goals), Orders & reports, Requirements (traceability per kind with
  acceptance and links), Tasks (board by Queued, Ready, Waiting, Running, Blocked, Failed, Succeeded, Cancelled, or a
  table; `GET /api/projects/{id}/tasks`), Plan, Evaluation (each goal's latest gate: stages, failed checks; defects;
  Reject for a goal awaiting approval), Reports (mission reports, decisions and ADRs), and the GM console for
  administrators.
- **WEB-PROJ-00b** New project also asks for Requirements (the brief, a draft requirement), Documents (posted to the
  command window), Target outcome, Team strategy and Related project; Edit has the last three and, under the
  project's stricter rules, "Plans wait for a person".

### 6.1 Project list
- **WEB-PROJ-01** A left list of the projects the user may see; the first one, protected, is the platform itself.
  **New project** (admins and `create_projects`), checkboxes and **Delete the selected projects** (confirm: running
  goals are cancelled, repositories on disk are kept; `POST /api/projects/delete`).
- **WEB-PROJ-02** The page has three resizable panels (list, main area, agents) arranged by the screen format.

### 6.2 Project form (create and edit)
- **WEB-PROJ-03** Fields: Name; Work folder (a git repository; by default a new folder next to Agent Company;
  **Browse…** opens a folder picker, admins only); GitHub URL (filled from the repository's remote when known;
  approved work is pushed there); Description; Quality commands (one per line, e.g. `uv run pytest -q`); Stack and
  Required skills (comma-separated); Participating rooms (none checked = every room; the planner then uses only these
  rooms' roles); General Manager (a change is kept in the position history, with a **Reason for a change**);
  Budget; Deadline; Priority (queue order); Goal budget, Goal tokens, Agents at once (each shown with the company
  value); Recruitment approval; Sandbox for agent code (a project may only be stricter); Required test stages
  (performance, compatibility, accessibility); Classification (what agents may carry to other projects).
- **WEB-PROJ-04** "Stricter rules for this project": the project's own governance values, which may only be
  stricter than the company's (`PUT /api/projects/{id}/policy`). Defaults come from `/api/projects-defaults`.

### 6.3 Project header
- **WEB-PROJ-05** Name with pills: *protected*, status (with its reason), priority, deadline (red and "overdue" when
  passed), budget used / budget (red when used up).
- **WEB-PROJ-06** Buttons: **Edit the project** (admins); **Write the company catalog into this project's .claude/**
  (agents, rules, skills, commands, hooks, `.mcp.json`; confirm, existing files of the same names are replaced;
  admins); **Sync to GitHub** (disabled until a GitHub URL is set; sends `/sync`); **Open the folder in the
  Workspace** (admins); **Show files / Hide files**; **Stop the project's running goals**; a status menu for the
  creator or admins: Pause, Resume, Complete, Archive, Cancel, Restore (`POST /api/projects/{id}/status`).
- **WEB-PROJ-07** Mode tabs: **General Manager** (admins, when the GM is a CLI agent), **Orders & reports**, **Plan**;
  at the right "GM: <model>", or "orders go to the agent loop" when the GM is an API model.
- **WEB-PROJ-08 Proposals waiting.** Pending proposals from agents (title, agent, source goal). Follow-up work:
  **Approve** / **Reject**. A product or new project needs its *documented case* first (Concept, Target user,
  Expected value, Feasibility, Estimated cost, with the kind Product / New project / Follow-up): **Documented case**
  opens the form, "case incomplete: n of 6 missing" disables **Approve as a project**.

### 6.4 General Manager panel (admins)
- **WEB-PROJ-09** A Claude Code (or Codex) conversation panel running in the project's folder, the same component as
  the Workspace panel (§15), kept per project; its model defaults to the GM's model.

### 6.5 Orders & reports (command window)
- **WEB-PROJ-10 Topics.** Conversation lanes; "general" always exists. **Open a topic**; per topic **Topic settings**:
  description, assigned agents (they report in its `/standup`), archive. Goals of one topic run in order.
- **WEB-PROJ-11 Messages.** Each shows author, a kind pill (result, error, …), the goal link `#id` (opens the goal
  window), the topic when searching, the classification, "×n" for identical messages collapsed, time, and **Copy**.
  The list stays at the bottom while new messages arrive unless the user scrolled up. **Search all topics** filters.
- **WEB-PROJ-12 Composer.** Text, **Attach images or files**, a **Classification** menu (only models cleared for it
  receive the order; never lower than the project's) and **Send (Enter)** (`POST /api/projects/{id}/command`).
  A plain-words order starts a goal for the General Manager in the current topic. Commands:
  - `DECISION: <subject> :: <choice> [:: <why>]` records a decision (no goal);
  - `/standup` every agent of the project (or of the topic) reports its status;
  - `/status` open blockers, the goals and their state, what happens next;
  - `/summary` the GM summarises the topic and records the decisions it finds;
  - `/stop` cancels the project's running and queued goals;
  - `/sync` pushes approved work to the GitHub remote;
  - `/help` lists the commands.
- **WEB-PROJ-13** Users without `run` read the project but cannot give orders.

### 6.6 Plan tab
- **WEB-PROJ-14** Progress (percent), open blockers and risks, milestones, the next action items in the GM's order,
  insights, requirements with traceability, capability gap, schedules, and GM handovers (`/api/projects/{id}/plan`,
  `/traceability`, `/capability_gap`, `/schedules`). Everyone who sees the project reads; admins edit.
- **WEB-PROJ-15 Milestones.** Name, description, target date, deliverables, *command criteria* (run on the
  project's current HEAD) and *manual criteria* (a person ticks them), goals that must be approved. Each criterion
  shows met / unmet / unchecked. Actions: **Run the command criteria now**, **Complete** (re-runs the commands),
  **Cancel the milestone** (confirm).
- **WEB-PROJ-16 Risks and blockers.** Kind (risk / blocker), title, category, severity, probability (0–100 %), impact,
  mitigation; **Mark mitigated** / **Resolved**. Blockers derived from goal state (for example a goal waiting for
  approval longer than `approval_blocker_hours`) are added and closed automatically.
- **WEB-PROJ-17 Action items.** Title, priority (1–5), due date, "waits for action items" (ids), expected output;
  **Done** / **Drop**.
- **WEB-PROJ-18 Insights.** Move through recorded → analyzed → reviewed → accepted → implemented → evaluated, or
  **Reject**; accepting one makes it a knowledge item and asks whether to propose it as follow-up work.
- **WEB-PROJ-19 Requirements.** Kinds functional, non-functional, story, use case; key (REQ-001 … by default),
  subject (finds contradicting requirements), acceptance criteria; **Approve**, **Reject**, **Retire**,
  **Add requirement**, **Import docs/requirements of the project**. Traceability shows the tasks, checks and merges
  that cover each requirement.
- **WEB-PROJ-20 Schedules.** Goals ordered every hour, every day at HH:MM or every week on a day at HH:MM (UTC), with
  goal text, details and quality commands (empty = the project's); skipped while the last one is still open or the
  project is not active. **Delete the schedule** (confirm; goals it created stay).
- **WEB-PROJ-21 Capability gap.** Skills the project's open work needs against what the agents have proven.

### 6.7 Side panels
- **WEB-PROJ-22 Agents** (right): the agents working for the project and the organisation around them.
- **WEB-PROJ-23 Files** (toggle): the project folder's listing with **Up one folder**; opening a file previews it.

## 7. Goal window and trace

A goal opens in a dialog from any `#id` link (`GET /api/goals/{id}`); there is no goals page.

- **WEB-GOAL-01 Header.** `#id title`, status pill, and actions by state and right:
  **Approve and merge** / **Reject** (awaiting approval; admins or a delegate with `approve_goals` for the project);
  **Pause** (tasks already started finish) and **Resume** from the checkpoint (admins); **Cancel**; **Change the
  goal's budget** (prompts for cost in USD, tokens, agents at once; 0 = the company setting; admins);
  **Execution trace**; **Software bill of materials** (CycloneDX JSON download, approved goals with a branch).
- **WEB-GOAL-02 Facts line.** Iteration n/max, branch, worktree, cost estimate; time (estimated, active, waiting,
  blocked); the mission digest of a finished goal (tasks, time since the order, tokens, cost, merge state); budget
  used per dimension (and "shared from goal #n"); why a paused goal is paused (budget used up, or cost more than
  twice its estimate: resuming accepts the cost so far); the result text.
- **WEB-GOAL-03 Sections**, each shown only when it has content:
  Before any change (each check passed / already failing before the agents started); Test strategy (risk,
  mandatory stages, reasons); Gate by stage (pass/fail per stage of the last iteration); Verification (the commands
  the gate ran itself, with environment; an agent's report never counts as evidence); Dependencies (manifest,
  dependency, change, before, after, audit notes); Iterations (pass/fail and score per iteration); Workflow template
  and version; Scope (allowed paths, protected tests).
- **WEB-GOAL-04 Iterations and tasks.** Per iteration the tasks as expandable rows: stage, status, role, agent,
  model, then the acceptance checks (✓/✗ with detail), files changed, recommendation, blockers, findings, next
  actions, output and the run's tool calls (failed ones marked). Expanded rows stay expanded across redraws.
- **WEB-GOAL-05 Defects.** Status, priority P1–P5, severity, check, verification; actions (§12 WEB-PERF-03).
- **WEB-GOAL-06 Root-cause analysis.** Trigger, goals covered, confirmed causes with their layer and evidence,
  contributing causes, actions as proposals with their result, follow-up.
- **WEB-GOAL-07 Decisions.** Status, topic, category, question, recommendation ("not yet authorised" until
  approved), alternatives, evidence with its class (FACT, MEASURED, INFERENCE, HYPOTHESIS, PREDICTION),
  assumptions, limitations, ADR path, "Report incomplete" when fields are missing, approver and note. Admins
  **Approve** / **Reject** (note or reason prompt). Anyone may give an **Independent review**: agree, concerns,
  disagree (a disagreement sends the decision to a person).
- **WEB-GOAL-08 Blackboard** (collapsed): the shared context entries agents read and write.
- **WEB-GOAL-09 Team.** Current members (joined, added, requested = waiting for recruitment, released) with
  **Release** per agent and **Add a role** (prompt) for admins on active goals; a history of changes.
- **WEB-GOAL-10 Workflow** (state transitions with reason), **Mission report** (finished goals, §5), **Activity**
  (the goal's events).
- **WEB-GOAL-11 Trace** (`/api/goals/{id}/trace`): one time line of workflow steps, runs, gate results, decisions and
  operations, and totals per agent and model (runs, failed, tokens, seconds, cost); **Back to the goal**.

## 8. Agents

- **WEB-AG-00** The page is "Teams & Agents". Tabs: **Teams** (departments = rooms: description, lead, members with
  their state, tasks and completion share, cost, daily budget, parent room; project teams = the agents on each live
  goal), **Agents** (cards with state working/idle/blocked/failed/paused/disabled/retired, room, model, tasks, success,
  last activity, top skills; search and filters by room, status, model, skill), **Org chart**, Agent network, Tools &
  MCP, Skills & plugins, Marketplace. The agent card's tabs: Overview, Agent code, Tasks & logs, Evaluation, Evolution,
  Memory, Relationships, Versions.

Tabs: Organisation, Agent network, Tools & MCP, Skills & plugins, Marketplace (admins). The tab is kept per browser.

### 8.1 Organisation
- **WEB-AG-01** The org chart: rooms with their agents (role, title, status, model, lifecycle); disabled rooms marked
  ("its agents are not staffed until the room is enabled again"); candidates marked (a recruit in its qualification
  run is never staffed for real work); a switch to show retired agents.
- **WEB-AG-02 Needs attention.** A button with a count opens the agents that are *degraded* (not staffed until reset
  or given another model) and *inactive* (no work for `inactive_days`, review for retirement).
- **WEB-AG-03 Add an agent** (`manage_agents`): Role (existing or a new specialist role), Title, Room, Reports to,
  Skills (`name=level`, comma-separated), Mission.
- **WEB-AG-04 Rooms** dialog: table of rooms (room, within, lead, agents) with **Edit** and **New room**: key, name,
  division, parent room (no cycles), lead, reason (kept in the position history), description, daily budget (covers
  the room and its sub-rooms; spent today shown; when used up its agents take no work until 00:00 UTC), enabled
  switch. **Position history**: position, from, to, reason, by, when. Organisation export: JSON, or CSV of the agents
  or of the rooms (`/api/org/export?format=json|csv&table=agents|rooms`).

### 8.2 Agent card (dialog)
- **WEB-AG-05** Tabs: Profile, Definition, Playbook & history, Runs, Relationships, Versions.
- **WEB-AG-06 Profile.** Mission, skills, permissions, certification, stats, settings: Model, Goals at once, Daily
  cost quota, Permissions and autonomy G0–G4 (admins only), Plugins and skills it uses. **Save**, **Clone**,
  **Merge into original** (clones), **Retire**, lifecycle Pause/Resume, **Reset its health**. Enabling an agent of a
  gated role is refused with the reasons until its room's measured gate is met; an admin may override (audited).
- **WEB-AG-07 Definition.** The agent's versioned definition file in an editor, **Save the definition**; **Study now**
  proposes a new working method from new knowledge and lessons as a *pending candidate*: its diff with **Apply** (one
  commit) or **Discard** (optional reason).
- **WEB-AG-08 Playbook & history.** Playbook versions with restore, a trial playbook under test, handovers, recent
  lessons, related agents, agents reporting to it.
- **WEB-AG-09 Runs.** Pages of 25 runs (when, goal, task, model, tokens, cost, outcome), **Newer** / **Older**; a run
  opens with its task, error and tool calls.
- **WEB-AG-10 Relationships.** Relationship, origin, weight, evidence; mentoring with **Add feedback** (advice, never a
  grade); **Correct this relationship**: other agent, kind, weight 0–1, status active/expired, reason (audited).
- **WEB-AG-11 Versions.** Configuration versions with **Restore** (confirm). Only an admin restores a version with
  other permissions or autonomy.

### 8.3 Agent network
- **WEB-AG-12** Overview counts and a paged relationship graph filtered by agent, steps from it, room, kind, origin
  and skill or technology; table step, from, kind, to, origin, weight, evidence; **Previous / Next page**.

### 8.4 Tools & MCP
- **WEB-AG-13** MCP servers: **Add a server** (name; transport *Local command (stdio)* with command, arguments,
  environment, or *Remote (Streamable HTTP)* with URL and headers; secrets stored encrypted, an empty value keeps the
  stored one); **Test** (connect and list the tools); per tool an allow tick ("changed" when its description or schema
  changed since it was allowed: it loses its allow until ticked again); **Save who may use it** (per-agent grants);
  **Delete** (confirm). A catalog of bundled servers to add with one click. Recent calls.

### 8.5 Skills & plugins
- **WEB-AG-14** Catalog of skills, plugins, rules, commands and hooks: kind, name, description, content (SKILL.md
  body, rule text, command prompt); **Add**, **Edit**, **Delete** (confirm).

### 8.6 Marketplace (admins)
- **WEB-AG-15** Import an agent from a repository URL or an uploaded `.zip`/`.tar.gz`; list with name, role, status,
  licence, source, SHA. Detail: permissions requested vs the ceiling (never above it), screening result, eval result,
  model (role route or a catalog model), autonomy; **Approve** (enable with the ticked permissions) or **Reject**
  (retire the candidate and delete the package; confirm, reason).

## 8a. Workflows

- **WEB-WF-01** `#/workflows`, tab Execution: one card per live goal (or live and the last 10 finished), foldable,
  filtered by project and "blocked only". The graph: the current iteration's tasks in columns by dependency, each
  node with title, role, agent, state (Queued, Ready, Waiting, Running, Blocked, Failed, Succeeded, Cancelled),
  duration and retries; then Quality gate and Approval & merge. A paused goal or a blocked task shows its reason.
  Agent messages of the goal beside the graph. A node opens a drawer: input, allowed paths, dependencies, acceptance
  checks with results, output, changed files, findings, blockers, handoffs, stage log.
- **WEB-WF-02** Tab Templates: each goal kind's workflow template in force (roles, writes, iterations, mode, review,
  checks, versions, goals run under it); read-only.

## 8b. Deliverables

- **WEB-DEL-01** `#/deliverables`: per project the release records, newest first, Ready (the project's newest) or
  Superseded: title (opens the goal), merge commit (a GitHub commit link when the remote is GitHub), branches, QA
  labels (verified / partial / failed from the gate and reviews), changed files, SBOM, who approved, the report's
  SHA-256; **Final report** (the stored completion report: overview, requirements and fulfilment, ADRs, team,
  deployment notes, artifacts, then the mission report), **Download .md**, **SBOM**, **Changed files**.

## 9. Company map (read-only)

- **WEB-MAP-01** The room tree as nested boxes: the engine line (General Management → Organization Engine →
  Evolution Engine → Orchestrator → Bridge → Development) in the main column, the other rooms beside it.
- **WEB-MAP-02** In each room its active agents with the model they use and the route's fallbacks as badges.
- **WEB-MAP-03** Live goals with their lifecycle step: discover, plan, execute, critique, validate, learn
  (derived from the goal's status and its current tasks); a goal opens its window.

## 10. Models

- **WEB-MOD-01 Catalog** (left list, scrolls by itself): one card per model with type (API, CLI · Claude Code /
  Codex / Copilot, Local, Offline), name (rename in place, Enter saves, Escape cancels), **Active** switch, **Test**,
  **Terms, name and limits**, **Delete** (confirm: routes using it move to another model).
- **WEB-MOD-02 Card content.** Status (available / unavailable with the reason / inactive), model id, CLI and its
  version, server URL, account (method, email, plan or organisation, own sign-in), the sign-in it was registered
  with (a CLI model: method, login or email, date), last test (when, OK or the error),
  usage: the provider's usage windows as rings (5-hour session, weekly, per model family; percent used, reset time),
  a note, and this server's tokens today and this week, price per 1M tokens. Terms line: billing, context window,
  tool calling, training on prompts, commercial use, terms link, and "verify terms" when no person checked them in
  90 days. "Receives up to": the highest classification it may get (confirm before *secret*).
- **WEB-MOD-03 Colours.** An available model's card has a green left border, green name and green status badge; an
  unavailable or switched-off one is red. Set again when the usage info arrives, after a test and after the switch.
- **WEB-MOD-04 Test** sends a short prompt (`POST /api/models/{id}/test`); the toast gives the time, the reply and
  the tokens, or the error. Only that card is redrawn; the page and its scroll stay. The Active switch behaves the
  same. Any action that redraws the whole page (delete, clearance, add) keeps both lists' scroll positions.
- **WEB-MOD-05 Add a model**: type **API** (OpenRouter with a searchable list sortable by performance or price, a
  free-only filter, score and context per model; or another provider by LiteLLM id; API key), **CLI** (Codex, GitHub
  Copilot, Claude Code with optional own sign-in account and a session to fork; shows whether the CLI is installed
  and enabled, with **Install** and **Enable**; for GitHub Copilot **Sign in to GitHub**: the server runs the CLI's device sign-in, the dialog shows the one-time code with **Copy**, **Open GitHub**, **Cancel** and the time left, polls every 2 s; when the server has no system keychain it asks whether the CLI may keep the token as plain text in `~/.copilot/config.json` (**Store the token** / **Cancel sign-in**); it then reports "Signed in to GitHub as <login>" or the reason it failed; the status line then reads "signed in as <login>"; for OpenAI Codex an optional **Codex account** folder and **Sign in with another ChatGPT account**: the same device sign-in on auth.openai.com into `data/codex-accounts/<account>/` (the account entered, else the next free `codex-N`), so a model can use another ChatGPT account than the host's; once signed in the account field holds that folder and the status line reads that account's "signed in as <email>"), **Local** (OpenAI-compatible URL, **Ask the server which models it
  serves**, served model).
- **WEB-MOD-06 Terms dialog**: name, billing (API per token, subscription, free tier, trial credits, runs here),
  context window, tool calling, provider may retain or train on prompts, commercial use, terms or pricing page.
- **WEB-MOD-07 Routes** (right list): per role its Model, Retry model (used after a failed quality gate; "same
  model"), and Fallbacks (comma-separated model names, in order, taking the work when the model hits a usage limit or
  fails). Each change saves at once (`PUT /api/routes/{role}`, "Route saved"). Read-only without `manage_models`.

## 11. Evolution

- **WEB-EVO-01** "How agents evolve" explanation; **Diagnose all agents now**; **Merge idle clones**; **Study now**
  (the next agent of the learning rotation writes a definition candidate).
- **WEB-EVO-02** Lists: learning rotation, near-duplicate agents, running trials, evolution events.
- **WEB-EVO-03 Recruitment.** Recruitment requests with job description; **Approve** / **Reject** / **Cancel**.
- **WEB-EVO-04 Experiments.** Change, suite, baseline, candidate, canary, status, platform commit; **New experiment**
  (prompts by target: candidate model, goal kind and template fields, ranking weights, agent and candidate playbook;
  a hypothesis; a budget in USD that stops it); **Run both sides**, **Apply the route**, **Roll back**, **Reject**.
- **WEB-EVO-05 Orchestration strategies.** Per workflow template: goals, accepted, out of iterations, iterations,
  minutes, cost per accepted, failed runs, conflicts, bottlenecks; **Propose** a better template.
- **WEB-EVO-06 Capability gaps, learning plans** (**Plan**, **Start**, **Cancel**), **Exercise suites**
  (**Generate exercises** for a skill, practice or final; **Approve** a generated final set before it certifies).
- **WEB-EVO-07 Code evolution** (admins): weaknesses by layer, the protected control plane, improvement contracts
  (title, objective, component, layer, kind, benchmark, allowed paths; or from a research proposal), old vs new
  benchmark metrics (baseline, candidate, change, weight); **Approve and deploy** (merge and safe restart; confirm),
  **Roll back** (reason; a revert commit), **Cancel**.

## 11a. Agent Evolution and Organization Evolution

- **WEB-EVO-01** `#/evolution` (Agent Evolution), tab **Candidates**: definition rewrites waiting for review (agent,
  versions, model, reason, sections) with **Review diff** (drawer: the diff, Apply, Discard, a link to the agent's
  versions), experiments with the stepper Baseline → Candidate → Experiment → Regression / safety → Human approval →
  Deploy / rollback, baseline vs candidate (pass rate with n, cost per pass, seconds, tool-call accuracy),
  regressions and the decision; decided rewrites. Tab **Learning & experiments**: the content of §11 below.
- **WEB-ORG-01** `#/organization` (Organization Evolution): the versions (reason, who, when, rooms, agents), A/B to
  compare two (rooms and agents added, removed, changed field by field), **Restore** for administrators (rooms'
  parent, lead, switch and budget; no agent deleted; the result is a new version).

## 12. Performance

- **WEB-PERF-00** The sidebar's **Evaluation & QA** (`#/evaluation`) holds this page as its tab *Performance &
  benchmarks*; `#/performance` still opens it alone (off the sidebar). Its other tabs: **Overview** (test executions,
  passed, failed, blocked tasks, goals pending a gate, gate pass rate; passed vs failed per day; pass share by stage;
  flaky checks; open defects by priority) and **Latest evaluation** (each goal's latest gate by Functional,
  Integration, Security, Final acceptance, Static, with the failing checks), over 24 h / 7 d / 30 d.

- **WEB-PERF-01** Open defects (priority, severity, status, check, goal, iterations); rooms (agents, tasks,
  completion, cost); agents (done, rework, reliable, goals passed, Weighted Agent Score 0–100); certification (skill
  family, level, evidence); recruitment effectiveness (recruit, role, probation, passed, failed).
- **WEB-PERF-02 Eval harness.** Suites and labels; **Run** a suite under a label (admins;
  "Eval started; results appear as runs finish"); a label's summary per task (passed, compliant, secure,
  iterations, seconds, end reasons) and per dimension with its sample size; **Compare** two labels.
- **WEB-PERF-03 Defect actions.** Status (with a reason; "duplicate of #"), priority 1–5, **Retest**.
- **WEB-PERF-04** Estimate accuracy per role (samples, estimated → actual, ratio, calibrated / under- / over-estimates).
- **WEB-PERF-05 Operational metrics** with sample sizes: p95 cycle time, paused time, cost of merged goals,
  best-iteration resets, human override rate, tool-call failure rate, confirmed RCA causes by layer, security findings.
- **WEB-PERF-06 Department KPIs.** Room, KPI, latest value, target, history, interval; **Measure now**, **Change the
  target**, **Remove (disable)** (history kept), **Add a KPI** (room key and target prompts).

## 13. Research

- **WEB-RES-01 Collect a website**: URL, link depth (0 = this page only), max pages; **Collect**.
- **WEB-RES-02 Rotation sources**: URL, subject words; **Add**; per source **Run this source now** and **Monitoring
  rules** (how often to check, subject words that make a change an event, which room's agents are told, link depth,
  max pages); **Run next**. Shift cards: the agent on duty, findings, open questions, known problems, **Replace
  agent** (reason prompt; the next agent on the roster continues); shift history.
- **WEB-RES-03 Ask the research room**: question, sources to read first, keywords, prefer sources, priority, repeat
  (once or recurring), published from / to, languages, deadline, budget in USD; **Ask**. Requests with their state.
- **WEB-RES-04 Report review.** A finished report shows its claims with citations and the tool steps; **Approve** or
  **Reject** (reason). Only approved reports become knowledge and proposals.
- **WEB-RES-05 Search knowledge** and the list of collected pages.

## 14. Knowledge (admins)

- **WEB-KNOW-01** Search items by text or technology; items with status and source; **Approve**, **Reject** (reason),
  **Write a new version** (supersedes it, with why), **Show its graph**, **Delete** (confirm).
- **WEB-KNOW-02 Review queue**: drafts, quarantined and rejected items.
- **WEB-KNOW-03 Contradictions**: two items side by side with **Older holds**, **Both hold**, **Newer holds**.
- **WEB-KNOW-04 Graph explorer**: an entity (`agent:3`, `goal:12`, `knowledge:5`, `technology:python`,
  `skill:testing`), depth and relation; paged table step, from, relation, to, weight.
- **WEB-KNOW-05 Import knowledge**: a file (.md, .txt, .json, at most 2 MB) or a server path inside the data folder,
  format (auto, text, Markdown with a piece per heading, chat export, experience reports, folder), classification,
  project, date. Imported items are drafts until a person approves them.
- **WEB-KNOW-06 Ranking per query class**: class, cases, procedure in use; **Learn** (learns it on the labelled eval).
- **WEB-KNOW-07 Hypotheses**: statement, **Evidence** (supports or contradicts), **Result** (held or not), **Add**.

## 15. Workspace (admins) — summary; full detail in [WORKSPACE.md](WORKSPACE.md)

- **WEB-WSP-01** Four windows: explorer (left), editor/viewer (centre), terminal (below), Claude Code panel (right),
  with resizable gutters. Each window can be minimised to a strip or maximised to fill the whole Workspace.
- **WEB-WSP-02 Explorer.** Open any folder on the host; tree; new file and folder, upload files and folders by drag
  and drop, move or copy by drag, cut/copy/paste, rename, duplicate, delete, copy path, download (zip for folders),
  compress, extract; filter by name; **+** opens more file windows, stacked or side by side. While the server is
  unreachable it keeps its lists and open files without any notice.
- **WEB-WSP-03 Editor and viewer.** Code with syntax colours, find, edit and **Save** (Ctrl+S; unsaved tabs ask before
  closing); Markdown, PDF, Word, images, CSV and JSON previews.
- **WEB-WSP-04 Terminal.** Real shells on the host in tabs: **New terminal**, **Reconnect**, kill (confirm), hide
  (Ctrl+`). It keeps its screen, reconnects quietly from the last byte it has, and a shell lost to a server restart is
  replaced in place on the next input.
- **WEB-WSP-05 Claude Code panel.** A tab per conversation of this browser (new ones on the right); a model menu (the
  CLI's aliases and the Models page's Claude Code and Codex models) and effort per prompt; attachments by paste, drop
  or button; Enter sends, Shift+Enter new line, ↑/↓ prompt history; **Compact** (`/compact`); `/status`, `/tasks`,
  `/agents` (usage windows, background tasks, todo list, subagents); stop a running prompt; each turn's prompt is a
  one-line bar pinned to the top of the log, tool calls and thinking fold into one "N steps" line, what the agent says
  and the result always show; tabs titled automatically.
- **WEB-WSP-06 Sessions window** (clock button, `/history`, `/resume`): Running (every live Claude Code session on the
  host: **Open**, **Stop**) and History (every session with a transcript; search title, folder, prompt, id): **Open**
  shows it in a tab at its end (the latest prompt and answer), **Continue** resumes it in this panel with `--resume`, **Delete** (confirm).
- **WEB-WSP-07** The Workspace runs host-wide as the server user with no command filter, by decision of the owner;
  every session and prompt is audited; its WebSockets check the Origin.

## 16. Machines (admins)

- **WEB-MAC-01** Linux and Windows virtual machines run as Docker containers: list with container, state, restarts,
  address, started.
- **WEB-MAC-02 New machine**: name, operating system, image address (an ISO URL), Windows account, language,
  password, CPU cores, memory (GB), disk (GB).
- **WEB-MAC-03** Actions: start, stop, restart, reset, delete (confirm); logs with a tail length.
- **WEB-MAC-04 Console** in the browser (noVNC over a WebSocket relayed by the server): **Send Ctrl+Alt+Del**,
  **Full screen**, screen settings (image quality, compression, refresh interval, scaling: fit to the page, own size
  with scroll, or resize the machine's screen; background) applied at the next Connect. Leaving the page closes it.

## 17. Security (admins)

- **WEB-SEC-01 Active stops** with **Open System to lift**.
- **WEB-SEC-02 Security incidents**: when, severity, category, title, goal, status; **Open an incident** (title, goal);
  containment buttons **Pause agent** / **Cancel goal** (confirm); closing asks for the root cause and remediation.
- **WEB-SEC-03 Refused commands by agent** (agent, refused, goals, last) and the latest refused commands.
- **WEB-SEC-04 Security alerts** from behaviour rules, and the state of the L3 sandbox for agent code.
- **WEB-SEC-05 A2A clients**: name, project, clearance, issued, last used; **Issue token** (shown once; refused with the
  reasons until the pipeline is measured reliable, unless overridden) and **Revoke** (confirm; stops at once).

## 18. System (admins)

- **WEB-SYS-01** Host, service and repository facts; **Restart safely**: the supervisor syntax-checks the code, boots
  it as a candidate on the next port and health-checks it, waits until no goal is running, then replaces the old
  server and health-checks again; a candidate that fails is refused and the old server keeps running.
- **WEB-SYS-02 Emergency stop** (E5: reason prompt) and **Lift the emergency stop** (confirm). **Narrower stops**:
  level E0–E4 and a scope (executor, agent id, goal id, project id or room key); **Stop**; each active stop with
  **Lift** (confirm).
- **WEB-SYS-03 Docker and virtual machines**: install and start Docker, LXD VMs and containers (**Launch** with a name,
  VM or container; **Start**, **Stop**, **Restart**, **Delete**), jobs, and **Clean up…** (shows stopped containers and
  images, **Check again**, **Remove** with confirm; machines of the Machines page are never pruned).

## 19. Notifications, Audit log, Settings, Users

- **WEB-NOTE-01 Notifications** (every user): list with kind and time; **Mark all read**; **Delete**; admins post a
  notice to every user (text, kind info / warning / success / error).
- **WEB-AUD-01 Audit log** (admins): when, actor, action, target, change (before → after), hash (records are chained);
  a link to Security incidents.
- **WEB-SET-01 Settings** (admins): every setting below with its description, **Save** and **Reset to the .env value**;
  booleans as on/off, lists one per line.
- **WEB-SET-02** Settings cards: **Connection** (HTTPS recommended or HTTP, domain names for the certificate,
  **Switch** with confirm: every computer moves to the new address), **Navigation** (§3), **Policy versions**
  (version, change, by, when, **Restore** with confirm), **Metrics for Prometheus** (create a scrape token, shown
  once; replace; revoke).
- **WEB-SET-03 The settings** (`prefs.SCHEMA`): max_iterations, max_parallel, require_approval, command_timeout,
  rotation_interval, proposal_policy, max_proposal_goals, rule_proposal_min_goals, goal_budget_usd, goal_time_budget,
  goal_token_budget, writing_daily_tokens_per_user, goal_agent_limit, allow_preexisting_failures, auto_push,
  quality_commands, executor, transport, tls_domains, enable_claude_code, enable_codex, enable_copilot,
  study_interval, retry_max_attempts, retry_base_seconds, retry_max_seconds, min_improvement, max_goal_depth,
  budget_exhausted_action, cost_overrun_factor, escalate_after, repo_map_chars, stuck_repeat_limit,
  stuck_error_limit, candidate_smoke, benchmark_schedule, benchmark_suite, benchmark_budget_usd,
  approval_blocker_hours, inactive_days, explore_rate, recruit_requires_approval, qualify_recruits, task_critic,
  auto_approve_proposals, auto_merge, improvement_min_score, improvement_max_regression, improvement_auto_rollback,
  a2a_enabled, shift_seconds, blocked_commands, retrieval_w_relevance, retrieval_w_technology, retrieval_w_verified,
  retrieval_w_author, retrieval_w_recency, retrieval_w_contradiction, retrieval_strategies, sandbox,
  sandbox_memory_mb, sandbox_tasks, sandbox_cpus, sandbox_paths, sandbox_env, dependency_audit,
  research_tool_rounds, health_recovery_minutes, mcp_revalidate_hours, required_test_stages,
  contradiction_judge. Each one's meaning is its description in `prefs.py`.
- **WEB-USR-01 Users** (admins): name (admin pill), status, computer (browser and OS, screen, time zone, language),
  registered. Actions by state: **Approve**, **Reject**, **Disable**, **Enable**, **Reset computer** (confirm; the user
  registers the browser again), **Make admin**, **Permissions** (§1: projects and capabilities), **Delegate** (project,
  right, until, note; list with **Revoke**), **Git** (§3 WEB-SHELL-16), **Delete** (confirm). An admin cannot act on
  themselves here.

## 19a. Notifications page and search

- **WEB-NOTE-01** `#/notifications`: tabs All, Unread, Action needed (the approvals queue first, then notifications
  that need a person); a category filter (Approval, Blocker, Failure, Completion, Quality, Evolution, Security,
  Notice); the same notification in a row shows once with "×n"; selecting one marks it (and its group) read and shows
  its details, goal and project links, *Open goal*, *Review the plan/merge* when it waits, *Ask the GM*, and Delete
  for administrators. *Mark all read*; administrators post a notice to every user.
- **WEB-SRCH-01** The top bar's search (Ctrl+K) lists pages and projects at once and then, from 2 characters, the
  server's goals, tasks, agents, research reports, knowledge (administrators) and files; Enter opens the selected one.

## 19b. Change History (admins)

- **WEB-HIST-01** `#/changes`: one timeline of agent configuration, playbook and definition changes, organisation
  versions, governance settings, model routes, merges and self-evolution deploys/rollbacks, newest first, 50 per page;
  filters kind, agent, project, period (24 h, 7 d, 30 d, a year); each entry links to its page and names where it is
  restored.

## 20. Rules that hold everywhere

- **WEB-ALL-01** The server enforces every right; a refused call answers 401 (signs the browser out to the device
  check), 403, or 409 with the reason, shown as a toast.
- **WEB-ALL-02** Destructive actions ask first (confirm) and say what is kept (repositories, history, goals created).
- **WEB-ALL-03** Merging a goal needs a person (or a delegate) unless the owner policy `auto_merge` is on; A2A and
  eval goals always wait for a person.
- **WEB-ALL-04** Agents never reach the Workspace, Machines or Docker functions; those are the administrators' tools.
- **WEB-ALL-05** Text from agents, web pages and files is shown as text (escaped), never as page markup.
- **WEB-ALL-06** Times of events are shown relative ("just now", "n min ago", "n h ago", "n d ago", "never");
  budget resets and schedules are in UTC.
