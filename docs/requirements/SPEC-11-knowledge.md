# SPEC-11 — Organizational knowledge, long-term memory and knowledge graph

Source: `/home/luca/update/SPEC11.docx`. Master criterion (`.rtf`): *a newly recruited agent can
retrieve verified experiences from relevant historical projects and cite the source of its
inherited knowledge.* The `.rtf` also requires, on every new task, a search over: similar previous
projects, agents who did similar tasks, technologies used, successful implementations, failed
approaches, relevant research and company standards — delivered as a curated package.

Today: `Lesson` (agent, goal, text, outcome) is written by `loop._learn_from` and read by
`learning.experience_context`, which picks the **latest** lessons of the agent and of related
agents. Retrieval is not task-relevant, not project-scoped, unreviewed, without provenance and
without secret screening. Research documents are searched by keyword (`research.search`).

Stack decision: stay on SQLite. Neo4j, Qdrant and Redis are `skip` (single host, one writer); the
graph is an edge table and search is SQLite FTS5. Revisit only if retrieval evaluations
(S11-R-08) show the need.

## Specific requirements

Status (2026-11-05): S11-R-01 done · S11-R-02 done · S11-R-03 done · S11-R-04 done · S11-R-05 done ·
S11-R-06 done · S11-R-07 done · S11-R-08 done.

### S11-R-01 Knowledge items with type, evidence class and provenance — §3.1, §6.1
- Extend `lessons` (via `_add_missing_columns`) or add `knowledge` with: `type`
  (`episodic|semantic|procedural|architectural|research|operational|capability|organizational|negative`),
  `evidence_class` (`FACT|MEASURED|INFERENCE|HYPOTHESIS|ASSUMPTION|PREDICTION|UNKNOWN`),
  `project_id`, `goal_id`, `task_id`, `agent_id`, `sources` (run IDs, commits, URLs, report IDs),
  `technologies` (list), `status` (`draft|approved|rejected|superseded|expired`), `version`,
  `supersedes_id`, `review_due_at`, `classification` (SPEC-14 S14-R-05), `created_at`.
- Lessons from failures are stored as `negative` with environment and conditions; one failure is
  never generalised into a rule (§3.1.4).
- Acceptance: KT-001, KT-002 — every new item names the goal/run it came from.
- **Status: done** on `lessons` (`kind`, `evidence_class`, `project_id`, `task_id`, `sources` with goal,
  task and commit, `technologies`, `status`, `version`, `supersedes_id`, `review_due_at`,
  `classification`).

### S11-R-02 Structured experience reports — §9.1
- After a task, the lesson step produces JSON: objective, actions, outcome, evidence refs,
  failures, successful approach, reusable lesson, limitations. Observable actions only, no
  hidden reasoning (§4.1). Stored on the knowledge item.
- Acceptance: KT-011.
- **Status: done** (`Lesson.report`; `loop._experience_facts`, `loop._lesson`, `learning.clean_report`,
  `REPORT_KEYS`; Knowledge view → "Experience report"; `tests/test_knowledge.py`, `tests/test_training.py`).
  The §9.1 fields: `objective`, `context_refs`, `actions`, `outcome`, `evidence_refs`, `failures`,
  `successful_approach`, `reusable_lesson`, `limitations`. The harness fills the observable ones from what it
  recorded (the contract's objective; the project and the K-numbers retrieved for the agent in this goal; each
  run's model, executor, duration and error, the changed files, the commit, how many acceptance conditions held;
  failed conditions and findings; goal, task, run and commit refs). The teacher (the instructor, else the agent)
  sees those as data and answers a fenced JSON with only `reusable_lesson`, `successful_approach` and
  `limitations`; a plain-text answer still gives the lesson. Other keys (reasoning) are dropped, fields are
  capped. The report is screened like the lesson (a credential in it quarantines the item), stored empty for
  secret work, and under a final evaluation names no condition or finding (LE-021). Retrieved items carry their
  limitations into the prompt (S11-R-03).

### S11-R-03 Task-relevant, project-scoped retrieval — §8, §14
- Replace recency-only selection in `learning.experience_context` with ranked retrieval over
  approved knowledge: FTS5 keyword relevance on task title/contract + technology overlap +
  verification status + recency − contradiction penalty (§8.3). Weights are settings, tuned by
  S11-R-08.
- Scope filter is applied **before** ranking: same project, or items with no project, or
  `public/internal` items of other projects (SPEC-14 S14-R-05).
- Each returned item carries: id, excerpt, source reference, status, last reviewed date,
  limitations, contradiction warning. Prompt wraps them as data, not instructions (KT-017).
- The package for a task includes: similar past tasks (same files/technologies), agents who did
  them (from `relationships`), failed approaches, relevant research documents, project
  standards (CLAUDE.md/README of the repo).
- Acceptance: KT-007, KT-009, KT-010, KT-018 — a goal of project B never sees a confidential
  lesson from project A; a newly cloned agent receives lessons of its template's history with
  their source goal IDs.
- **Status: done** (`learning.retrieve`, `learning.task_references`, `tests/test_knowledge.py`). Scope filter before
  ranking; the candidate pool is the agent's own and its related agents' latest items plus the best 200 matches of
  an FTS5 index over item text and technologies (`lessons_fts`, external content kept current by triggers, created
  and filled in `db.engine`). Rank = relevance (mean of bm25 normalised to the best hit and the share of the task's
  terms the item covers) + technology overlap (the task's skills and the project's stack) + verification (evidence
  class, reviewed by a person) + who learned it + recency − the contradiction penalty (S11-R-04). The weights are
  the `retrieval_w_*` settings (S11-R-08 guards them). Each item in the prompt carries its K-number, source goal,
  outcome, author, a *reviewed* mark, limitations and the contradiction warning, wrapped as data. A task's package
  adds the project's standards (the first of `CLAUDE.md`, `AGENTS.md`, `CONTRIBUTING.md`, `README.md`, read from
  the goal's base branch in the project's checkout, so an agent's edit in the worktree never reaches other prompts; a file that tries to
  instruct agents is left out) and up to three matching research documents (unflagged; research reports of another
  project above internal are left out). Similar past tasks, the agents who did them and failed approaches arrive
  as lessons (negative items for failures). Without SQLite FTS5 the pool falls back to the latest 300 items.

### S11-R-04 Contradiction handling — §6.3
- When a new item states the opposite of an approved one on the same subject (same technology +
  overlapping terms, judged by the lesson step), both are kept, linked with `CONTRADICTS`, and a
  review is queued (the instructor or a reviewer of a different provider). The resolution and its
  rationale are recorded; nothing is silently overwritten.
- Acceptance: KT-006.
- **Status: done** (`learning._link_contradictions`, `contradictions`, `resolve`). Without a model call the
  lesson step judges "the opposite" deterministically: a technology in common, at least half of the terms shared
  (Jaccard, negation words left out) and a negation (*not, never, avoid, without, don't* …) in exactly one of the
  two; lessons of the same goal are progress, not contradiction. A pair gets a `CONTRADICTS` edge in `kg_edges`
  and an administrator notification; both items stay approved but lose 1.0 in ranking and are marked *disputed
  by K…* in prompts. A person resolves it in the Knowledge view (*older holds*, *both hold*, *newer holds*, with a
  required rationale); the losing item is rejected with the reason, the edge keeps the resolution. A model judge
  (the instructor) is open.

### S11-R-05 Memory-poisoning protection — §10.4
- Before storing: secret scan (reuse `checks.scan_secrets` patterns) → quarantine on hit (KT-016);
  prompt-injection scan (reuse `research.injection_flags`) → `rejected` with reason.
- Items that would change procedures or playbooks (`procedural`, `organizational`) start as
  `draft` and need review before retrieval; `episodic` items from passing gates may auto-approve.
- No agent approves its own item. Rejection and rollback are possible per version.
- Retrieval events are logged (item IDs per task) (KT-021).
- **Status: done** (`learning.screen`, `learning.review`, `/api/knowledge`). There is no `draft` kind
  yet that agents create (the lesson step only writes episodic/negative items, auto-approved after
  screening).

### S11-R-06 Lifecycle: versioning, expiry, deletion — §10.3
- New versions supersede old ones (KT-013); items past `review_due_at` drop out of retrieval
  until reviewed (KT-014); deletion removes the item, its FTS row and graph edges (KT-015).
- **Status: done:** review dates (`learning.expire_due`, run by the evolution cycle, rebuilds playbooks);
  `learning.supersede` writes version n+1 with `supersedes_id`, a `SUPERSEDES` edge and the old item's sources
  plus `knowledge:<old id>`, the old item becomes `superseded` and leaves retrieval; `learning.delete` removes the
  item and every edge naming it. Both rebuild the agent's playbook. There is no FTS row yet (S11-R-03).

### S11-R-07 Knowledge graph (edge table) — §7
- Table `kg_edges`: `src_type`, `src_id`, `rel`, `dst_type`, `dst_id`, `weight`, `evidence`,
  `created_at`. Entity types reuse existing tables: agent, project, goal, task, knowledge, source,
  decision, incident, technology (string), skill.
- Relations: `CREATED, USED, DEPENDS_ON, SUPPORTS, CONTRADICTS, DERIVED_FROM, APPLIES_TO,
  SUPERSEDES, VERIFIED_BY, EXPERIENCED_IN, ASSIGNED_TO, COLLABORATED_WITH`.
- Edges are written as side effects (lesson stored → `CREATED`, `APPLIES_TO` technology; task →
  `ASSIGNED_TO`; decision → `SUPPORTS` evidence). `relationships` stays the agent-agent store.
- API `GET /api/knowledge/graph?entity=type:id&depth=2` (paginated, KT-019); a graph view in the
  dashboard is `open` (P2).
- **Status: done** (`kgraph.py`): side effects in the caller's session: `agent CREATED knowledge`,
  `knowledge APPLIES_TO technology`, `knowledge DERIVED_FROM goal|task` (once the item is approved, at storage
  or on review), `knowledge VERIFIED_BY user` (a person approved it), `agent EXPERIENCED_IN skill` (weight =
  passed tasks, each task once), `goal USED knowledge` (retrieval), `task ASSIGNED_TO agent` (staffing),
  `x SUPPORTS decision` (evidence entries naming an entity: `K12`, `goal:3`, `{"type": "review", "id": 7}`),
  plus the existing `CONTRADICTS`/`SUPERSEDES`. `COLLABORATED_WITH` is read from `relationships` (not copied).
  `GET /api/knowledge/graph?entity=type:id&depth=2&offset=&limit=&rel=` (administrators) walks breadth-first
  (depth ≤ 3, nearest first) and pages the edges with the nodes they touch (KT-019). The Knowledge view has a
  graph explorer (from an item, or any `type:id`; click a node to centre on it). `DEPENDS_ON` between
  entities is not written yet (task dependencies are agent links in SPEC-12); research documents and incidents
  are not linked yet. Known limitation: `kg_edges` has no unique index (`_add_missing_columns` cannot add one),
  so two tasks of one goal running in parallel may both write the same `goal USED knowledge` edge; harmless
  (it shows twice), a unique index is a later migration if it matters.

### S11-R-08 Retrieval evaluation — §8.3, KT-023
- A small eval set (`evals/retrieval.json`: task → expected knowledge IDs) and a metric
  (recall@5) so ranking weights are changed only when the metric improves.
- **Status: done** (`learning.evaluate_retrieval`, `prefs._check_retrieval`). The eval set holds its own 25 items
  (text, technologies, evidence class, reviewer, author, one contradiction pair) and 10 tasks with expected items;
  it is ranked in memory with the same FTS5 tokenizer and scoring as `retrieve` and writes nothing. Setting a
  `retrieval_w_*` weight is refused when recall@5 would drop (adapted from "improves": an equal score is allowed);
  an accepted change is logged with recall before and after; resetting a weight (back to its `.env` value) is not
  checked. Default weights: recall@5 = 1.0.

### Not taken
- Embedding/vector search: `open` (P2) — add only if S11-R-08 shows keyword+FTS is insufficient.
- Legal holds, backup expiry policies: `skip` (single-user deployment).
