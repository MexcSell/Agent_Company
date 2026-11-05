# SPEC-12 — Agent relationships, collaboration network and rotation

Source: `/home/luca/update/SPEC12.docx`. Master criterion (`.rtf`): *an agent can be replaced
during a task without losing project state, research findings or outstanding actions.* The `.rtf`
also asks that a new agent automatically gets relationships to similar agents, complementary
specialists, mentors and past collaborators, and that departments keep a shift schedule
(research: acquisition → verification → analysis → knowledge organisation → review).

## Already met

| Item | Where |
| --- | --- |
| Team compatibility in staffing (§7) | `registry._skill_score`, `_team_weights` |
| Similar-agent links on creation | `registry._link_similar` |
| Handover package (§10, AR-010) | `loop._handover`, `handovers` table |
| Research rotation | `research.rotation_roster`, `rotation_tick` |

## Defects in the current implementation

- **S12-D-01** `learning.link_team` adds `collaborated` weight to every pair on a goal regardless
  of outcome. §18.1: count verified outcomes, not participation. Fix: weight +0.1 only when the
  goal passed the gate, −0.05 (floor 0) when it failed for reasons attributed to the pair's
  tasks; repeated links from the same goal are not counted twice (AR-015).
  **Fixed** (`learning.link_team`, `relationships.evidence`; `tests/test_safety.py`).
- **S12-D-02** `_skill_score` gives an agent without history quality 0.5 and no exploration
  bonus, and a single failure lowers quality permanently. §11.3/§18.2: new agents need
  opportunities. Fix in S12-R-03. **Fixed** (S12-R-03).

## Specific requirements

Status (2026-11-05): S12-R-01 done · S12-R-02 done · S12-R-03 done · S12-R-04 done · S12-R-05 done ·
S12-R-06 done · S12-R-07 open.

### S12-R-01 Relationship kinds with evidence — §2.2
- Extend `relationships.kind` beyond `similar|collaborated` to: `reviewed_by`, `mentored_by`,
  `depends_on`, `handover_to`, `complementary_skill`, `research_partner`, `testing_partner`,
  `supervises`, `knowledge_contributor`.
- Add `evidence` (list of goal/task/evaluation IDs), `updated_at`, `status` (`active|expired`).
  Inferred kinds (`similar`, `complementary_skill`) are kept distinct from observed ones.
- Written as side effects: QA task on a Developer's output → `reviewed_by`; handover →
  `handover_to`; task dependency → `depends_on`.
- Acceptance: AR-001, AR-002, AR-021, AR-022.
- **Status: done** (`network.py`, `registry.KINDS`): `relationships.status` (`active|expired`); directed kinds
  keep their order (`a reviewed_by b`), symmetric ones are stored once per pair; every link lists its evidence
  (`task:<id>`, `goal:<id>`, `candidate:<skill>:L<n>`, `admin:<name>: <reason>`) and each piece counts once.
  Origin is derived: `inferred` (similar, complementary_skill, a mentor candidate), `observed`, `corrected`.
  Side effects: `network.observe` after every iteration's lesson step writes `reviewed_by` (a QA/security task
  depending on a writer's task), `depends_on` (other dependencies), and, when the goal passed, `research_partner`
  and `knowledge_contributor` (an agent's task retrieved another agent's knowledge); `loop._handover` writes
  `handover_to`. `supervises` stays the hierarchy (`superior_id`); `testing_partner` is set by administrators.
  Only experience-sharing kinds (`registry.SHARING`) that are not expired feed knowledge ranking.

### S12-R-02 Relationships on recruitment — `.rtf` SPEC-12
- When `registry._recruit` / `create_agent` / `clone_agent` creates an agent: link `similar`
  (exists), `complementary_skill` to agents whose skills cover the task's other required skills,
  and a `mentored_by` candidate (most certified agent of the same role, SPEC-13 certification).
- Acceptance: a recruited agent has at least one `similar` and, when a certified agent of its role
  exists, one `mentored_by` link.
- **Status: done** (`network.link_on_creation` from `_recruit`, `create_agent`, `_clone`):
  `complementary_skill` to the (up to 3) specialists of other roles that its peers (same role, else its similar
  agents) have `collaborated` links with; `mentored_by` to the active agent of the same role with the highest
  SPEC-13 certification, on the new agent's skills when one is certified (never its own original or a sibling).

### S12-R-03 Fair team recommendation — §11.2, §11.3, §18.2
- Score = capability coverage + relevant experience + quality evidence + availability
  − workload − cost − risk, after filtering by permissions, `lifecycle=active`, and project
  access (AR-006, AR-007).
- Quality for agents with fewer than 3 finished tasks is "insufficient evidence", not 0.5 or a
  penalty; with probability `explore_rate` (setting, default 0.1) and only for non-critical
  tasks, the best-scoring new agent is chosen (AR-017).
- Failures older than the last 20 tasks decay; one failure is not permanent incompetence.
- Each staffing decision records its inputs and score in the task (`contract.staffing`) (AR-018).
- **Status: done** (`registry._rank`, `staff`): score = skill coverage + experience (level) + collaboration with
  the goal's team + quality − workload (0.2 per goal it already works on), after filtering by role, `enabled`,
  `lifecycle=active`, free slot, health and quota. Quality is the success rate over the agent's last 20
  verified task outcomes (older failures drop out); with fewer than 3 the agent gets the pool's mean quality.
  With probability `explore_rate` (DB setting, default 0.1) a non-critical task (`risk_level` not `high`) of a
  goal that is not an evaluation goes to the best idle agent with fewer than 3 outcomes. `contract.staffing`
  holds the action, the chosen agent, whether it explored and the top candidates with each score part.
  Project access is not a filter: agents are not scoped to projects in this deployment.

### S12-R-04 Mentoring — §9
- A mentoring record: mentor, learner, topic (skill), learning objectives, gap assessment,
  materials (knowledge IDs), exercises, review feedback, final evaluation, status.
- Triggered when SPEC-13 detects a gap (S13-R-01) and a `mentored_by` candidate exists. The mentor
  reviews exercise results; certification uses the independent evaluation (SPEC-13 S13-R-03), never
  the mentor's own judgement.
- Acceptance: AR-013, AR-014.
- **Status: done** (`mentoring.py`, table `mentorings`): `training.create_plan` opens a mentoring when the learner
  has a `mentored_by` link to an active agent, else when `network.mentor_for` finds a certified agent of its role
  (which then gets the link); it holds objectives, gap assessment (gap, current and target level) and materials
  (the plan's knowledge items plus the mentor's own on the skill). After the practice suite the mentor reviews
  the harness's practice summary in a run of its own (purpose `mentor`; fail-open: a plain note when the run
  fails) and its text is stored as feedback; it never sees the final set. The plan's verdict closes the record
  (`completed`/`failed`/`cancelled`) and adds `mentoring:<id>` evidence to the link, which makes it observed.
  Certification is untouched by anything in the record (AR-014). People add feedback on the agent card's
  Relationships tab (`POST /api/mentoring/{id}/feedback`, audited); `GET /api/mentoring?agent_id=`.
  Mentoring starts with a learning plan (S13-R-02, which `raise_gaps` proposes for detected gaps).

### S12-R-05 Shift rotation and workload — `.rtf` SPEC-12, ASEAO06 §14
- Rotational rooms get shifts (`acquisition`, `verification`, `analysis`, `organisation`,
  `review`); `rotation_tick` assigns the next shift's agent and records a handover with objective,
  sources and retrieval dates, findings, confidence/limitations, open questions, next actions.
- Track per agent: open tasks, hours active in the last 24 h, last handover status.
- Acceptance: replacing the agent of a running research shift keeps findings and open questions
  (master criterion).
- **Status: done** (`research.py`, table `research_shifts`; `tests/test_research.py`): the rotation is worked in
  shifts that cycle `acquisition → verification → analysis → organisation → review`; the agent on duty makes the
  shift's source visits. A shift's `state` holds its objective, the visited sources with retrieval dates and
  durations, findings (new and changed pages), unverified pages (flagged as possible prompt injection), known
  problems (failed visits) and the open questions they raise; a clean visit of the same source resolves its
  problems and questions. A shift ends after `shift_seconds` (DB setting; default 0 = every rotation turn), checked on
  every rotation tick even when no source is due; the next agent on the roster then starts the next phase with the
  unresolved state and a handover package (objective, sources, findings, confidence and limitations, open
  questions, unverified pages, known problems, active research requests, outstanding sources, next actions), a
  `handover_to` link with `shift:<id>` evidence and a note among its lessons. When the agent on duty leaves the
  roster (paused, disabled, retired: the roster now also requires `lifecycle=active`) or an administrator replaces it
  (`POST /api/research/shifts/replace`, audited; Research → Shift → Replace agent), the next agent continues the
  same shift with its whole state. Workload per agent (`registry.workload`, agent card → Profile): open tasks, hours
  active in the last 24 h (model runs plus research visits), last handover given or received (task or shift).
  Only one rotation track exists (the external-data room and the research leads share a roster).

### S12-R-06 Reputation-manipulation protection — §18.1
- Collaboration scores come only from gate results and QA verdicts, never from message counts.
- Duplicate/artificial records (same pair, same goal, repeated) are ignored (AR-015, AR-016).
- Administrators can correct a relationship (audited).
- **Status: done.** Collaboration weight comes only from gate results and each goal counts once
  (S12-D-01); administrators correct a link's weight or expire it (or add one) with a reason on the agent card's
  Relationships tab (`POST /api/agents/{id}/relationships`, audited `relationship_correct`).

### S12-R-07 Network view — §16 (P2)
- Agent network view with paging (AR-009, AR-020): `open`.
