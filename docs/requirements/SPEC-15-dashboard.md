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
| Task management | Goals | decisions, workflow history, blackboard done; stage-wise gate → ASEAO09 S09-R-01; trace has an API but no view |
| Research center | Research | request fields, claims review → ASEAO06 (monitoring rules done) |
| Knowledge center | — | new view: knowledge items, review queue, contradictions → SPEC-11 |
| Agent relationships | — | network view (P2) → SPEC-12 S12-R-07 |
| Learning center | Evolution, Performance | gaps, learning plans, experiments → SPEC-13 |
| Innovation lab | — | gated → ASEAO10 |
| Security center | Audit log, Notifications, System | incidents (Audit), emergency stop (top bar, System) done; alerts are notifications of source `security`; refused-command counts open |
| Analytics | Performance | operational metrics → guides G-R-02 |
| System administration | Models, Settings, Users, System, Files, Tools & MCP | — |

## Specific requirements

### S15-R-01 Emergency stop control — SPEC-14 S14-R-01
Visible to administrators in the top bar on every view, with the current level shown to all users.
**Status: partial:** the top-bar control is on every view for administrators and turns red while any
stop is active; other users do not see the level.

### S15-R-02 Knowledge view — SPEC-11
List/search knowledge items with type, evidence class, status, source links; review queue
(approve/reject/supersede); contradiction pairs side by side.

### S15-R-03 Security view — SPEC-14 S14-R-03/R-04
Incidents (open first, by severity), security alerts, per-agent refused-command counts.
**Status: partial:** an Incidents list with containment and status buttons is in the Audit view; no
separate view, no refused-command counts.

### S15-R-04 Goal detail — SPEC-04, ASEAO09
Workflow state history, stage-wise gate table, decision records with approve/reject, trace.
**Status: partial:** workflow history, the gate by stage, decisions with approve/reject, the blackboard and
pause/resume are on the goal page; a trace view is open (the trace has an API).

### S15-R-05 Project detail — ASEAO05
Milestones, risk/blocker register, insights, upcoming action items, weighted progress, topics.
**Status: done:** Projects → Plan (milestones, risks and blockers, insights, action items, progress) and the
topic tabs of Orders & reports (ASEAO05 S05-R-01..R-05, S05-R-07).

All views keep the current conventions: one file per area in `static/`, live updates through
`/api/stream`, administrative actions audited.
