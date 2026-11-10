# G5 Control Hub — redesign of the website's functions and interface

Requirements for the redesign the owner ordered on 2026-11-10 ("completely redesign the functions and interface of
the website based on the images and documents in this path. Leave the screen color and structure as is."). The
"path" is the eight ChatGPT conversations the order links; no new folder exists on the host after the AICO
delivery (`/home/luca/update/update_3..5` and `_webdesign` are consumed by [AICO-UI.md](AICO-UI.md)).

Seven of the eight conversations are about **AXoR** (the owner's other platform, "Self-Evolving Agent Ecosystem,
G5"); their concepts are applied here to Agent Company. Mining HQ, Bittensor and the AXoR name are AXoR's and are
**not** taken over (`COMPANY.name` stays the configurable company name).

**Sources** (read through `chatgpt.com/backend-api/share/<id>`; large exports were read in windows, the rest only as
summaries — items marked *(summary)* are unverified)

| Link | Title | What it gives |
| --- | --- | --- |
| 6af191fa | AI Agent Swarm Explained | **Primary.** The 18-part implementation specification: three planes, hybrid swarm (hierarchical, collaborative, parallel, competitive, adaptive), mission contract and task fields, autonomy/approval matrix (16 activities), ten mandatory safety mechanisms, resource and cost intelligence, **Part 13 Unified Control Hub** (Command Center, Missions, Swarm Control, Organization, Intelligence, Evolution Lab, Governance, Observability), §9 nine operational metrics, Part 16 acceptance criteria ("the complete mission can be inspected in the Control Hub"), "the visualization must represent actual runtime state, not simulated activity" |
| 6aee4e5a | Improve AXoR Design | Control Hub as landing page (missions, success rates, availability, budget, approvals, failed tasks and recovery, system health; every item links to its workspace), Mission Center Basic/Advanced mode, workforce marketplace steps, agent workspace, interface-section table; *(summary)* Command Center image: six KPI cards (Active Missions, Pending Approvals, Agent Utilization, Budget Burn, Success Rate, Critical Alerts), Mission Overview, Approval Queue, Live Activity Feed, Organization Map / Team Health, Resource & Budget Monitor, Alerts & Risks, Quick Actions; sidebar with collapsible sections |
| 6af21ead | Self Evolving Ecosystem | Six primary views; **live ecosystem topology** (which agents work, on which mission, which tools, which sandboxes, which resources, what they learn, which capabilities evolve, which actions wait for approval); three autonomy levels with Automatic / Policy controlled / Human approval badges; "every evolution should have" version history, evaluation results, audit, resource limits, rollback, security checks, human approval; swarm controller duties; structured task contract |
| 6af21f11 | Self Evolving Agent Ecosystem | Seven intelligence layers; capability gap table (Available/Weak/Missing → Reuse/Improve/Develop/Acquire); eight measurement dimensions; evolution states Observe → Diagnose → Propose → Experiment → Validate → Release; *(summary)* Evolution Center questions (which agents improve, what capabilities improved, new skills, knowledge acquired, experiments passed/failed, improvements awaiting approval, versions deployed, whether the ecosystem performs better) |
| 6af21f34 | Add Discord Channel AXoR | Knowledge Hub → External Sources → Discord: connect bot, select server and channels, sync on/off and frequency, imported messages, sync history, test, disconnect; health, last sync, count, errors; per-message fields; agent access per source; explicit status when the bot has no access, never a pretended success; Control Hub shows sync failures |
| 6aedbc6f | Agent Marketplace Proposal | Discover → Evaluate → Import → Configure → Integrate → Optimize; lifecycle Discovered … Active; scorecard per role dimension (exists as REQ-002 `marketplace.py`) |
| 6af21e63 | Agent Models Compared | Agent / team / swarm / company distinction; no interface requirement |
| 6af28fc1 | Autonomous AI Website Design | The GM-first website already delivered as AICO-UI (pages 03–14); used only as a cross-check |

The technology tables in the links (Temporal, NATS, PostgreSQL, React, Kubernetes …) are AXoR recommendations, not
requirements here — the same stance as AICO-UI's UI-D-01.

---

## 0. Decisions

- **G5-D-01 Colours and screen structure unchanged.** The owner's one explicit constraint. The Lumen Desk tokens
  (`style.css`), the grouped sidebar that collapses to the rail and becomes a bottom bar on phones, the top bar with
  Notifications / Audit / Settings / Users, page heads, cards and drawers stay. The links' palettes (graphite/teal,
  `#111827`) are not used. "Redesign" = functions and information architecture inside that shell.
- **G5-D-02 Control Hub information architecture.** The sidebar groups become the Control Hub's sections (link
  6af191fa Part 13), keeping every view id (deep links, saved layouts):
  - **Command**: Command Center (`dashboard`), General Manager
  - **Missions**: Projects, Missions (`goals`), Workflows, Deliverables
  - **Swarm & Organization**: Swarm Control (`swarm`, new), Teams & Agents, Company map
  - **Intelligence**: Research, Knowledge, Evaluation & QA, Models
  - **Evolution Lab**: Evolution Lab (`evolution-lab`, new), Agent Evolution, Organization Evolution, Change History
  - **Governance**: Governance (`governance`, new), Security
  - **Observability**: Observability (`observability`, new), System, Machines, Workspace

  A layout saved with the earlier group keys (company/intelligence/evolution/system) is read like a legacy layout:
  its hidden pages stay hidden, its order is replaced by the new default (as UI-D-05 did for top/bottom).
- **G5-D-03 Mission = goal.** "Mission" is the visible name of a goal (page title Missions); the mission contract is
  the goal's stored fields (objective, deadline, budget, tokens, agents at once, checks, allowed paths, permits,
  sandbox, approval policy, risk from its test strategy) — no second entity, no renamed API.
- **G5-D-04 Swarm = a goal's team in action.** The swarm of a mission is its team (`teams.py`) with the current
  iteration's tasks; swarm teams are rooms; the topology is read from tasks, runs (tool calls, isolation level),
  live processes and the approval queue. Nothing is simulated (Part 16).
- **G5-D-05 Autonomy levels are presented, not re-modelled.** L1 Automatic / L2 Policy-controlled / L3 Human approval
  are computed per activity from the live settings (`require_plan_approval`, `auto_merge`, `auto_approve_proposals`,
  `recruit_requires_approval`, `sandbox`, `dependency_audit`, `task_critic`, `qualify_recruits`), the agents'
  G0–G4 autonomy and the hard rules (merging, new tool permissions, security boundaries always a person).
- **G5-D-06 Read-only overview endpoints, no new state except the Discord connector.** Command Center, Swarm
  Control, Evolution Lab, Governance and Observability are new GET endpoints composing existing tables (`command.py`,
  `swarm.py`, `hub.py`); actions link to the pages that already perform them (and their permission checks and audit).
  The only new table is `discord_connectors`; the goal API gains optional fields and the goal response a `contract`
  key, no existing response shape changes.
- **G5-D-07 Discord is a research source kind.** Knowledge → External sources lists every source; Discord is added
  as a connector with its own encrypted bot token, pinned connections to discord.com, and an explicit access state.

## 1. Command Center (UI-G5-CC)

- **UI-G5-CC-01** six KPI cards: Active missions, Pending approvals, Agent utilisation, Budget burn (period spend
  against the daily budgets), Success rate (accepted ÷ finished missions in the period, with n), Critical alerts.
  `done` — `command.py` `center`, `dashboard.js`.
- **UI-G5-CC-02** nine operational metrics (link 6af191fa §9): mission success rate, task completion rate,
  validation score (gate pass rate), cost per successful mission, agent utilisation, rework rate (review rejection
  + retries), recovery rate (failed tasks later done), improvement gain (latest finished experiment's delta),
  approval wait time — each with its sample size, "–" without samples. `done`.
- **UI-G5-CC-03** Alerts & risks: open incidents, degraded agents, stops, limited models, open blockers, failed
  missions, sync failures of external sources. `done`.
- **UI-G5-CC-04** Resource & budget monitor: host load, memory, disk, spend per day against room budgets, tokens.
  `done`.
- **UI-G5-CC-05** Quick actions: New mission, New project, Ask the GM, Swarm Control, Approvals. `done`.
- Kept from AICO: GM card, active projects, approval queue, lifecycle, live activity, model usage, team
  performance, recent completions.

## 2. Swarm Control (UI-G5-SW)

- **UI-G5-SW-01** topology Mission → Team (room) → Agent → Task of every live mission, as a layered graph; each
  node shows status; edges are task dependencies within a mission. `done` — `swarm.py` `topology`, `swarm.js`.
- **UI-G5-SW-02** per agent node: model, the tools it called in its latest run (with failures), its isolation level
  (L0–L2/L3), cost and tokens in this mission; per task: stage, retries, critic verdicts. `done`.
- **UI-G5-SW-03** approvals and policy events of the mission on the mission node; failed and recovered tasks
  marked. `done`.
- **UI-G5-SW-04** selecting a node opens its detail (state, history, latest output, tool calls) in the drawer with
  links to its controls (goal page: stop, retry task; agent card). `done`.
- **UI-G5-SW-05** coordination pattern per mission from its workflow template (execution mode) and team size.
  `done`.

## 3. Evolution Lab (UI-G5-EV)

- **UI-G5-EV-01** the eight questions: agents improving (XP/level and certification changes), capabilities
  improved (certified learning plans), new skills (catalog skills added), knowledge acquired (approved items),
  experiments passed/failed, improvements awaiting approval (definition candidates, experiments in review,
  improvement contracts, org proposals), versions deployed (definition versions, applied experiments, deployed
  contracts), is the ecosystem performing better (mission success, task completion and cost per successful mission,
  this period vs the one before, judged only with 3+ samples on each side). `done` — `hub.evolution_lab`, `hub.js`.
- **UI-G5-EV-02** capability table: each required skill of active projects → Available / Weak / Missing →
  Reuse / Improve / Develop / Acquire, from verified proficiency (`recruitment.capability_gap`). `done`.
- **UI-G5-EV-03** the evolution lifecycle Observe → Diagnose → Propose → Experiment → Validate → Release with the
  count of items at each step. `done`.

## 4. Governance (UI-G5-GV)

- **UI-G5-GV-01** autonomy matrix: the activities of link 6af191fa §8 with Autonomous?/Approval computed from the
  live settings (G5-D-05) and a badge per level. `done` — `hub.autonomy_matrix`, `hub.js`.
- **UI-G5-GV-02** agents per autonomy level G0–G4 and per room; agents with network or shell permission. `done`.
- **UI-G5-GV-03** approval queue with action, target, evidence link, expected cost and age. `done` (from
  `approvals.queue`).
- **UI-G5-GV-04** budgets: daily room budgets and today's spend, missions near their budget, per-agent quotas.
  `done`.
- **UI-G5-GV-05** the ten mandatory safety mechanisms, each with the control that implements it and its live state.
  `done`.

## 5. Observability (UI-G5-OB)

- **UI-G5-OB-01** sandboxes: live processes with their goal and isolation, worktrees in use, sandbox setting and
  bubblewrap availability. `done` — `hub.observability`, `hub.js` (administrators).
- **UI-G5-OB-02** tools: tool calls in the period, failure rate, the most used tools, MCP servers and their state.
  `done`.
- **UI-G5-OB-03** resources: CPU load, memory, disk; model and token consumption per model in the period. `done`.
- **UI-G5-OB-04** network: research fetches and external-source syncs in the period, failures. `done`.

## 6. Missions (UI-G5-MI)

- **UI-G5-MI-01** Mission form, Basic mode (default): objective, deadline, budget, expected deliverable; the GM
  plans, staffs and validates. Advanced mode: kind, checks, allowed paths, permits, iterations, tokens, agents at
  once, cost estimate, URLs. `done` — `app.js` mission form (mode per browser).
- **UI-G5-MI-02** mission contract card on the mission page: objective, deliverables, deadline, budget (spent /
  limit), constraints, success criteria (checks + acceptance), required capabilities (roles), risk, approval
  requirements, status. `done` — `hub.mission_contract`, the goal window.

## 7. External sources (UI-G5-KN)

- **UI-G5-KN-01** Knowledge → External sources: web sources (existing) and connectors. `done`.
- **UI-G5-KN-02** Discord connector: bot token stored encrypted, test connection, discover the servers and channels
  the bot can see, select channels (forum threads included), sync on/off and interval, last sync, imported message
  count, errors, sync history; imported messages become research documents (server, channel, thread, message and
  author ids, timestamp, attachments, link, parent) and are searchable; an explicit state when the bot lacks access
  (`no_access`, `missing_intent`, `invalid_token`), never a pretended success; sync failures appear in the Command
  Center's alerts; unverified community content is marked as such; who may read it is the source's `agent_access`
  (none by default, research, all). `done` — `discord.py`, `sources.js`. Realtime (Gateway) sync is not built:
  syncs are incremental on an interval (5 min at the shortest). Slack and Telegram connectors are not built.

## Status

Delivered 2026-11-10 in one branch (`g5-hub`), one commit per phase (P1 navigation + Command Center, P2 Swarm
Control, P3 Evolution Lab + Governance + Observability, P4 Missions, P5 External sources), tests in
`tests/test_g5_*.py`, screenshots checked on a scratch server in the dark theme at 1440 px.

Not built (no source asks for them as requirements, or they are AXoR's): Mining HQ, the AXoR name, Temporal/NATS/
PostgreSQL/React, Kubernetes, a Discord Gateway listener, Slack/Telegram connectors, Generation 6/7 federation.
