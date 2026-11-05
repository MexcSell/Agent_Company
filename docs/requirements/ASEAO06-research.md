# ASEAO SPEC-06 — Research and external intelligence

Source: `/home/luca/update/ASEAO06.docx`. Master criterion (`.rtf`): *the research department can
collect, verify, analyze, store and hand over research without losing source attribution or
investigation history.* `1.txt`: *a room that analyzes and retrieves specialized external data
and carries out its duties in a rotational manner.*

## Functional requirements (spec IDs kept)

| ID | Requirement | Status |
| --- | --- | --- |
| S06-FR-01 | Humans and agents create research requests | done (`research.request_report`; agents via goal research step) |
| S06-FR-02 | Clear research objective | done (`ResearchReport.question`) |
| S06-FR-03 | One-time and recurring research | done: one-time and recurring reports (`recurring`, `research.ReportThread`, S06-R-01); recurring monitoring of sources (S06-R-02) |
| S06-FR-04 | Subject areas, keywords, date ranges, languages, source preferences | partial (S06-R-01): keywords and source preferences; date ranges and languages not taken (the knowledge store has no publication date or language to filter on) |
| S06-FR-05 | Budget and deadline | done (S06-R-01) |
| S06-FR-06 | Detect duplicate or overlapping requests | done (S06-R-01, `research.find_overlap`) |
| S06-FR-07 | Priority levels | done (S06-R-01) |
| S06-FR-08 | Link to projects, tasks, objectives | done (S06-R-01: `project_id`, `goal_id`; the report lands on the goal's blackboard) |

## Already met
Provenance and source kinds (`research.classify_source`), injection flags kept out of prompts
(`research.injection_flags`), cited reports (`run_report`), rotation of research agents over
registered sources (`rotation_tick`), robots.txt and public-host checks (`_robots_ok`,
`_is_public_host`).

## Specific requirements

### S06-R-01 Richer research requests — FR-04..08
- Add to `ResearchReport`: `project_id`, `goal_id`, `keywords`, `source_preferences`
  (`official|academic|open_source|any`), `priority`, `budget_usd`, `deadline`, `recurring`
  (cron-like interval or empty).
- Before creating, search open/recent requests by normalised question + keywords; on overlap
  return the existing request instead (AT-007 analogue for requests).
- **Status: done** (`ResearchReport` columns, `research.submit`, `find_overlap`, `due_reports`, `run_due_reports`,
  `ReportThread`; `POST/GET /api/research/requests`; Research → Ask the research room → More options;
  `tests/test_research_requests.py`). Overlap: an open, recurring, or less than `RECENT_DAYS` (7) old request with
  the same normalised question or at least `OVERLAP` (0.6, Jaccard) of the question's and keywords' terms; failed
  and expired ones do not count. A duplicate returns `{"id": <existing>, "duplicate": true}` and is not run.
  Source preferences put the preferred kinds (`classify_source`) first in the material and tell the model.
  Priority and deadline order the queue of `ReportThread` (every 60 s, one request, skipped under an E4 stop of the
  research room, E3 of its project, or E5); a request past its deadline is `expired`, not run. Budget: the model
  cost over all runs; a recurring request stops when it is used. Recurring: `hourly|daily|weekly|monthly`; the
  latest report replaces the request's `report://` document. A request linked to a goal writes its report to that
  goal's `research` blackboard section. Two runners never run one request at once (a conditional claim); requests
  cut off by a restart are queued again. Not taken: date ranges and languages (no such data on documents).

### S06-R-02 Change detection and monitoring rules — §13 (AT-016..018)
- `Document`: add `content_hash`, `version`, `fetched_at`; refetch stores a new version only when
  the hash changes (AT-008) and keeps the previous one.
- `Source`: add `interval` (`hourly|daily|weekly|monthly|manual`), `subject`, `notify_room`;
  rotation picks sources whose interval has elapsed (instead of one global interval) and honours
  `Retry-After`/rate limits (§13.2).
- A changed page with a relevant subject creates an intelligence event: notification to the
  room's agents and an `events` row with a diff summary.
- Acceptance: unchanged page → no new version; changed page → new version + one event.
- **Status: done** (`Document.content_hash`, `version`, `fetched_at`, table `document_versions`;
  `Source.interval`, `subject`, `notify_room`, `not_before`; `research.store_pages`, `content_hash`, `diff_summary`,
  `document_versions`, `update_source`, `_due`, `relevant`, `_intelligence`; `PATCH /api/research/sources/{id}`,
  `POST /api/research/sources/{id}/run`, `GET /api/research/documents/{id}/versions`; Research → Rotation sources;
  `tests/test_research.py`). The hash ignores whitespace. The last 5 earlier versions of a page are kept. A first
  fetch is new knowledge, not an event. "Relevant subject": the changed page mentions one of the subject's words
  (any change when the source has no subject). An event is an `events` row (`intelligence`, with +/- line counts
  and up to 3 added lines), a note for every active agent of the source's room (a research lesson; the latest 3
  of the last 14 days are in the agent's experience context, marked as web data; added lines of a page flagged
  as possible prompt injection are left out), and one administrators' notification per source run. A source with no interval keeps the global rotation
  interval; `manual` sources run only from their Run button. 429/503 stop the crawl and hold the source for
  `Retry-After` (at most a week; 1 hour when the header is missing or unreadable).

### S06-R-03 Claim-level evidence and contradictions — §9.3, §10.4 (AT-009..012, AT-015)
- Reports store claims as JSON: claim, evidence list (`source_id`, location, excerpt,
  relationship `supports|contradicts|partial|context|unrelated`), verification status,
  reviewer.
- A claim is `verified` only when an excerpt from a stored document supports it and a second
  agent (different provider) agreed; otherwise `unverified`. Unsupported claims are dropped from
  the approved report (AT-015).
- Disagreeing sources: both kept, dates and conditions compared, unresolved uncertainty stated;
  never pick the most frequent answer.

### S06-R-04 Independent review of reports — §11 (AT-014)
- Report status `draft→in_review→approved|rejected`; review by an agent other than the author
  and of a different provider. Only approved reports feed knowledge (SPEC-11) and goals.

### S06-R-05 Rotation handover — §14
- Each rotation turn writes a handover: active requests, completed work, unverified claims,
  outstanding sources, decisions, pending reports, deadlines, known problems (SPEC-12 S12-R-05).
- **Status: done** with SPEC-12 S12-R-05 (`research_shifts.handover`): active and pending requests (queued or running
  reports), completed work (visited sources with retrieval dates, findings), unverified pages, outstanding sources
  (never visited, due, held by Retry-After), known problems, open questions, confidence/limitations and next
  actions. Decisions and deadlines are not part of the package: research requests have neither yet (S06-R-01).

### S06-R-06 Research → innovation → goal pipeline — §15 (AT-020)
- An approved finding can become a proposal tagged `research` (existing proposal flow) with the
  report linked; the chain is research → verification → evaluation → proposal → (sandbox
  experiment, SPEC-13 S13-R-05) → approval → goal. Research never changes code directly.

### S06-R-07 Knowledge transfer — §16 (AT-019)
- Approved report claims become `research` knowledge items with sources and review date
  (SPEC-11 S11-R-01).

### S06-R-08 Recovery — AT-021
- A report left `running` at restart is re-queued (same rule as goals, SPEC-04 S04-R-05).
