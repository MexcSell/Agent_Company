# SPEC-02 — Agent registry and runtime

Source: `/home/luca/update/ASEAO SPEC02.docx`. Master criterion (`.rtf`): *the system can register,
start, assign, monitor, suspend and retire agents without losing their historical project
records.*

Mapping: registry → `registry.py` + `agents` table; runtime instance → one executor run (`Run`)
per task; sessions → `Task.session_id` (Claude Code resume).

![Agent lifecycle](images/spec02-agent-lifecycle.png)

Spec states (§6.1): Registered (identity created) → Configured (profile validated) → Provisioning
→ Starting → Running ↔ Idle; Running → Degraded → Restarting → Running; Running/Idle → Paused;
Running → Stopping → Stopped → Starting; Registered, Paused, Stopped, Provisioning → Retired (final).
§6.1 also requires the agent lifecycle and the runtime instance state to be stored separately.

Here: `Agent.lifecycle` (`active|paused|retired`) is the agent state; `Agent.status`
(`idle|busy|disabled`) and `Run` rows are the runtime state. Agents are invoked per task, so
Provisioning/Starting/Stopping collapse into a run's start and end.

## Functional requirements (spec IDs kept)

| ID | Requirement | Pri | Status |
| --- | --- | --- | --- |
| S02-FR-01 | Register an agent with a unique identity | P0 | done (`registry.create_agent`, unique name) |
| S02-FR-02 | Retrieve and search agents | P0 | done (`list_agents`, Agents view) |
| S02-FR-03 | Update profiles | P0 | done (`update_agent`) |
| S02-FR-04 | Version all agent configurations | P0 | partial: playbook only → S02-R-01 |
| S02-FR-05 | Assign an organizational position | P0 | done (`room_key`, `superior_id`) |
| S02-FR-06 | Register skills | P0 | done (`Agent.skills`) |
| S02-FR-07 | Associate an approved model | P0 | done (`model_pref`, routes) |
| S02-FR-08 | Tool permission policies | P0 | done (`Agent.permissions`); gateway → S14-R-06 |
| S02-FR-09 | Create and launch runtime instances | P0 | done (executor run per task) |
| S02-FR-10 | Stop and restart runtimes | P0 | partial: per goal; per agent → S14-R-01 (E1) |
| S02-FR-11 | Monitor runtime health | P0 | open → S02-R-02 |
| S02-FR-12 | Manage sessions | P0 | done (`--resume`, `Task.session_id`) |
| S02-FR-13 | Track resource consumption | P1 | done (`usage.py`, `Run`) |
| S02-FR-14 | Temporary agents | P1 | done (clones retired after the goal) |
| S02-FR-15 | Clone from templates | P1 | done (`clone_agent`, role specs) |
| S02-FR-16 | Controlled model replacement | P1 | done (routes, model test) |
| S02-FR-17 | Runtime migration | P2 | skip (single host) |
| S02-FR-18 | Automatic runtime recovery | P1 | partial → SPEC-04 S04-R-05 |
| S02-FR-19 | Retire without deleting history | P0 | done (`set_lifecycle`) |
| S02-FR-20 | Complete lifecycle history | P0 | partial → S02-R-01 |
| S02-FR-21 | Capability discovery | P1 | partial → SPEC-03 S03-R-02 |
| S02-FR-22 | Configuration rollback | P1 | partial: playbook `restore` → S02-R-01 |
| S02-FR-23 | Detect inactive and unhealthy agents | P1 | open → S02-R-02 |
| S02-FR-24 | Quotas and concurrency limits | P1 | partial: global `max_parallel`; per agent → S02-R-03 |

## Specific requirements

### S02-R-01 Versioned agent configuration and lifecycle history — FR-04, FR-20, FR-22
- Table `agent_versions`: `agent_id`, `version`, snapshot of (skills, permissions, model_pref,
  superior_id, room_key, enabled, autonomy, playbook_version), `changed_by`, `reason`, `at`.
  Written by `update_agent`, `set_lifecycle`, `create_agent`, evolution promotions.
- `POST /api/agents/{id}/rollback?version=` restores a snapshot (audited).
- Acceptance: three updates → three versions; rollback restores the first snapshot exactly.

### S02-R-02 Health and inactivity — FR-11, FR-23
- An agent is `degraded` when its last 5 runs include ≥3 executor errors (not gate failures) or
  its model's test fails; degraded agents are skipped by `staff` and an administrator is notified.
- Agents idle more than `inactive_days` (setting, 30) are listed for retirement review; clones
  idle after their goal ends are already retired.
- Acceptance: three executor errors in a row mark the agent degraded and staffing picks another.

### S02-R-04 Spec acceptance tests (§15) still to cover
Tests to add to `tests/` (the rest are met by existing behaviour or tests):
AT-004 update → new version (S02-R-01); AT-008 assigning a model not in the catalog → rejected
(`update_agent` must validate `model_pref` against `models`); AT-013 missed heartbeat / repeated
executor errors → degraded (S02-R-02); AT-014 unauthorized launch → denied and audited;
AT-016 rollback (S02-R-01); AT-019 quota exceeded → queued (S02-R-03); AT-023 concurrent profile
updates → second update based on a stale version is rejected, not lost.

### S02-R-03 Per-agent concurrency and quotas — FR-24
- `Agent.max_concurrent` (default 1, as today via `busy`) and an optional daily cost quota per
  agent; exceeding it makes the agent unavailable until the next day.
