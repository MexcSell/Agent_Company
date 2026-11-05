# SPEC-15 — Company dashboard and infrastructure

Source: only the summary in the ASEAO master specification
(`/home/luca/update/I am trying to download documents related to this.rtf`, "SPEC-15"); no full
SPEC-15 document exists in `/home/luca/update/`. Purpose: *a central interface through which
humans can manage and monitor the entire AI company.*

The `.rtf` recommends Next.js, shadcn/ui, LangGraph, PostgreSQL, Neo4j, Qdrant, Redis, NATS,
OpenTelemetry/Grafana as *starting technologies, not mandatory dependencies; each component should
have a replaceable interface.* This repository keeps FastAPI + plain JS + SQLite (CLAUDE.md);
those technologies are `skip` unless a requirement here cannot be met without them.

## Screens

| `.rtf` screen | Existing view | Gap |
| --- | --- | --- |
| Company overview | Dashboard | — |
| Organization chart | Agents (hierarchy) | room/position editing → SPEC-01 S01-R-01 |
| Agent management | Agents | — (versions and rollback on the agent card, health flags; SPEC-02) |
| Project management | Projects | — (Plan tab; topics in Orders & reports) |
| Task management | Goals | — (decisions, workflow history, blackboard, gate by stage, trace on the goal page) |
| Research center | Research | request fields, claims review → ASEAO06 (monitoring rules done) |
| Knowledge center | Knowledge | — (items with search, review queue, contradictions side by side, new versions; SPEC-11) |
| Agent relationships | — | network view (P2) → SPEC-12 S12-R-07 |
| Learning center | Evolution, Performance | gaps, learning plans, experiments → SPEC-13 |
| Innovation lab | — | gated → ASEAO10 |
| Security center | Security, Audit log, System | — (stop level, active stops, incidents, refused commands per agent, security alerts; emergency stop in the top bar, lifting in System) |
| Analytics | Performance | operational metrics → guides G-R-02 |
| System administration | Models, Settings, Users, System, Files, Tools & MCP | — |

## Specific requirements

### S15-R-01 Emergency stop control — SPEC-14 S14-R-01
Visible to administrators in the top bar on every view, with the current level shown to all users.
**Status: done:** the top-bar control is on every view for administrators and turns red while any
stop is active; every user sees the widest active level (`E1 stop` … `E5 stop`) next to their name, without its
scope or reason (`stop_level` in `/api/overview`, `governance.stop_level`).

### S15-R-02 Knowledge view — SPEC-11
List/search knowledge items with type, evidence class, status, source links; review queue
(approve/reject/supersede); contradiction pairs side by side.
**Status: done:** Knowledge view (`static/knowledge.js`, admin-only): search by text or technology, filter by
status and kind; source links open the goal; approve, reject, write a new version (supersede), delete; open
contradictions side by side with *older holds / both hold / newer holds* and a required rationale
(`/api/knowledge`, `/api/knowledge/contradictions`, SPEC-11 S11-R-04, S11-R-06).

### S15-R-03 Security view — SPEC-14 S14-R-03/R-04
Incidents (open first, by severity), security alerts, per-agent refused-command counts.
**Status: done:** Security view (`static/security.js`, `/api/security` → `governance.security_summary`): stop
level and active stops, incidents open first and worst first with status and containment buttons (moved from
the Audit view), refused commands per agent with the latest ones, security alerts.

### S15-R-04 Goal detail — SPEC-04, ASEAO09
Workflow state history, stage-wise gate table, decision records with approve/reject, trace.
**Status: done:** workflow history, the gate by stage, decisions with approve/reject, the blackboard and
pause/resume are on the goal page; its trace button shows the execution trace: totals per agent and model and
one time line of workflow changes, tasks, runs, gate results, decisions and operations.

### S15-R-05 Project detail — ASEAO05
Milestones, risk/blocker register, insights, upcoming action items, weighted progress, topics.
**Status: done:** Projects → Plan (milestones, risks and blockers, insights, action items, progress) and the
topic tabs of Orders & reports (ASEAO05 S05-R-01..R-05, S05-R-07).

All views keep the current conventions: one file per area in `static/`, live updates through
`/api/stream`, administrative actions audited.
