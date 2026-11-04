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
| Agent management | Agents | versions, rollback, health → SPEC-02 |
| Project management | Projects | milestones, risks, insights, action items, topics → ASEAO05 |
| Task management | Goals | stage-wise gate, decisions, trace → ASEAO09 S09-R-01, SPEC-04 S04-R-02/R-12 |
| Research center | Research | request fields, monitoring rules, claims review → ASEAO06 |
| Knowledge center | — | new view: knowledge items, review queue, contradictions → SPEC-11 |
| Agent relationships | — | network view (P2) → SPEC-12 S12-R-07 |
| Learning center | Evolution, Performance | gaps, learning plans, experiments → SPEC-13 |
| Innovation lab | — | gated → ASEAO10 |
| Security center | Audit log, Notifications | incidents, emergency stop, alerts → SPEC-14 |
| Analytics | Performance | operational metrics → guides G-R-02 |
| System administration | Models, Settings, Users, System, Files | MCP servers → guides G-R-05 |

## Specific requirements

### S15-R-01 Emergency stop control — SPEC-14 S14-R-01
Visible to administrators in the top bar on every view, with the current level shown to all users.

### S15-R-02 Knowledge view — SPEC-11
List/search knowledge items with type, evidence class, status, source links; review queue
(approve/reject/supersede); contradiction pairs side by side.

### S15-R-03 Security view — SPEC-14 S14-R-03/R-04
Incidents (open first, by severity), security alerts, per-agent refused-command counts.

### S15-R-04 Goal detail — SPEC-04, ASEAO09
Workflow state history, stage-wise gate table, decision records with approve/reject, trace.

### S15-R-05 Project detail — ASEAO05
Milestones, risk/blocker register, insights, upcoming action items, weighted progress, topics.

All views keep the current conventions: one file per area in `static/`, live updates through
`/api/stream`, administrative actions audited.
