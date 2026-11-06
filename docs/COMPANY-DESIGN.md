# Company design: structure, models, loops and screens (2026-11-07)

The owner's instruction of 2026-11-06 asked for "the face-to-face design again". The phrase is ambiguous: it most
likely means the **screen design** (画面設計) and possibly the **design of the company itself**. Both readings start
from the same document; this is it. The structure part is applied (rooms below); the screen part is planned and the
one new screen it needs is queued as a goal, so nothing the owner uses changes without review.

Sources: the owner's step 1 and step 2 (2026-11-06), `/home/luca/update_2/1/a_1..a_9.jpg`, the shared conversations
"Codex components and Bridge" (four layers: Organization, Evolution Engine, Bridge/Orchestrator, Coding Engine) and
"AXoR Redesign Proposal" (one Control Hub; Organization → Departments → Teams → Agents), and
[requirements/EVOLUTION-LOOPS.md](requirements/EVOLUTION-LOOPS.md).

## 1. Structure: the engines above the code agents

The owner: "Agents belonging to the orchestrator, bridge, Organization Engine, and Evolution engine are placed to the
top of code agent." Applied on 2026-11-07 as rooms (Agents → Rooms; `organization.py`, recorded in the position
history):

```
Company General Manager (general_management)
└─ Organization Engine (organization_engine)      lead: Agent Manager
   ├─ Agent Management, Administrative Management
   └─ Evolution Engine (evolution_engine)          lead: Classroom Instructor
      ├─ Study Room, New Technology Classroom
      └─ Orchestrator (orchestrator)               lead: Company General Manager
         └─ Bridge (bridge)                        Model Router, tool gateway, MCP
            └─ Development Flow (development)      the code agents
               Software Architect, Backend Developer, QA Engineer,
               Auxiliary Developer (Codex), Frontend, DevOps
```

The research, analysis, security and business rooms stay beside this line under the General Manager: they serve the
whole company, not only the code agents.

| Engine | What it does | Code |
| --- | --- | --- |
| Organization Engine | rooms, positions, the agent registry, recruitment, staffing and teams | `organization.py`, `registry.py`, `recruitment.py`, `teams.py`, `network.py` |
| Evolution Engine | evaluates the work and improves it: evals, experiments (baseline vs candidate, rollback), learning plans, playbooks, RCA, verified self-evolution | `evaluation.py`, `experiments.py`, `training.py`, `evolution.py`, `rca.py`, `selfevolve.py` |
| Orchestrator | goal → task contracts → staffing → the circular goal loop until the gate passes | `loop.py`, `workflow.py`, `strategies.py`, `blackboard.py` |
| Bridge | agents ↔ models and tools: Model Router with fallbacks at usage limits (EL-R-01), tool gateway, MCP; the planned bridge MCP server (EL-R-09) | `router.py`, `executors.py`, `mcp.py` |

## 2. Models per agent

| Agent | Model (Models page) | On a usage limit | On a retry after a failed gate |
| --- | --- | --- | --- |
| Company General Manager | claude_37 (Claude Code, own sign-in) | Claude Code (host sign-in), then Codex CLI | Claude Code |
| Competent developers (Architect, Backend Developer, QA) | Claude Code | Codex CLI | Claude Code |
| Auxiliary Developer (Codex) | Codex CLI (its own choice on its card) | — | Claude Code: a task the assistant failed is retried by the competent developer |
| Research Lead | Codex CLI (route `research`) | claude_37, then Claude Code | Claude Code |
| Reviewers of another agent's work | another family when one is usable (EL-R-03): Claude reviews Codex and the other way round in the research room; code reviews in the goal loop use only API models of another family until QA receives the gate's test output (goal 93) | | |
| GitHub Copilot | registered kind `copilot`; not installed on this host | | |

The research lead calls its model through the router, gets a JSON plan back and carries it out with its own tools
(EL-R-02); Codex writes the research, Claude reviews it.

## 3. Loops

The lifecycle each goal runs through, as the "loops within loops" diagram draws it, mapped to what exists:

| Step | Where | Inner loop |
| --- | --- | --- |
| Discover | research notes, knowledge retrieval, repository map in the plan prompt | research tool loop (EL-R-02) |
| Plan | GM planner: task contracts, workflow template, test strategy | plan validation, re-plan |
| Execute | agents in the goal's worktree | edit → syntax hook → fix (EL-R-05); stuck watch |
| Critique | review tasks of another family | per-task critic (EL-R-06, queued) |
| Validate | the quality gate: contracts, checks, sandboxed commands, reviews | iterate until it passes, best iteration kept |
| Learn | lessons, playbooks, RCA, experiments | rule proposals from recurring failures (EL-R-04, queued), candidate archive (EL-R-07, queued) |

## 4. Screens

The dashboard keeps its rail (Settings → Navigation) and the Simple mode merged on 2026-11-06 (REQ-003). How each
screen serves the structure above:

| Screen | Serves | Change |
| --- | --- | --- |
| Dashboard / Simple | the owner's control hub: running missions, approvals, alerts | none |
| Projects | goals per project, approvals, command window | none |
| Agents (org chart, network, Tools & MCP, marketplace) | Organization Engine | the org chart follows the new room tree |
| Models | Bridge: models, routes, **fallbacks** (new column, 2026-11-06), usage limits | done |
| Evolution, Performance, Knowledge | Evolution Engine | none |
| Research | the research room; a report now carries its `tool_steps` | show the steps on the report (part of the goal below) |
| Workspace, Machines, Security, System | administrator's tools | none |
| **Company map** (new) | one picture of §1–§3: the engine line with each room's agents, each agent's model and fallbacks as badges, the live goals with the lifecycle step they are in | queued as a goal |

The Company map is a read-only view built from existing endpoints (`/api/rooms`, `/api/agents`, `/api/models`
routes and fallbacks, running goals); no new data. It replaces nothing.
