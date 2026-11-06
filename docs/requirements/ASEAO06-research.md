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
| S06-FR-04 | Subject areas, keywords, date ranges, languages, source preferences | done: keywords and source preferences (S06-R-01); publication date range and languages: `Document.language` (declared only: Content-Language header, `<html lang>`, `<meta http-equiv>`, a feed's `<language>`/`xml:lang`; `research.primary_language`), `ResearchReport.published_after`/`published_before`/`languages` validated by `research.submit` and compared by `find_overlap`, `research.filter_material` on the stored material and the request's own URLs before the model sees it (undated or undeclared documents kept and labelled), the counts in the report's `## Method` line (`filters_line`) |
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
  cut off by a restart are queued again. Date ranges and languages followed later (S06-FR-04, see the table):
  a request's `published_after`/`published_before` (inclusive UTC days) and `languages` leave documents out of the
  material; one without a date or a declared language is kept and labelled `published=unknown`/`language=unknown`,
  and the report's `## Method` line counts both. A request with other filters is not a duplicate.

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
- **Status: done** (`ResearchReport.claims`, `research.check_claims`, `_excerpt`, `conclude`, `_approved_text`;
  `tests/test_research_review.py`). Claims are the report's numbered findings; the author cites `[n]` (supports) or
  `[n:contradicts|partial|context|unrelated]` and quotes the passage. The check is deterministic: a quote must occur in
  the stored document (case and whitespace aside; location = offset in the collapsed text), else at least
  `SUPPORT_SHARE` (0.6) of the claim's terms; each evidence entry keeps `source_id`, URL, excerpt, location,
  relationship and the document's retrieval date. Status per claim after the review: `verified` (backed, and a
  reviewer of another model family or a person agreed), `supported` (backed, and a reviewer of the same family agreed;
  kept, but marked "checked by the same model family" and stored as INFERENCE, not FACT), `contested` (one source
  backs it, another backed source contradicts it: both are kept under "Uncertain" with their retrieval dates, not
  resolved and not stored as knowledge), `unverified` (the reviewer did not agree) and `unsupported` (no excerpt backs
  it). The approved report replaces the findings with the claims that held; `unverified` and `unsupported` ones are
  dropped from it (AT-015) and stay visible only in the draft. Adapted: "a different provider" is a different model
  family *when one is usable*; otherwise the claim is `supported`, not `verified`, so a single-provider install still
  learns from reviewed reports without calling them independently verified.

### S06-R-04 Independent review of reports — §11 (AT-014)
- Report status `draft→in_review→approved|rejected`; review by an agent other than the author
  and of a different provider. Only approved reports feed knowledge (SPEC-11) and goals.
- **Status: done** (`ResearchReport.review`, `reviewed_by`, `reviewer_agent_id`, `review_provider`, `review_note`;
  `research.review_report`, `decide_report`; `POST /api/research/requests/{id}/review` (admin, audited); Research →
  report window: claims with their evidence, Approve/Reject). The review runs right after the author's run: another
  research agent (`registry.staff` while the author is busy, so never the author) on a model of another provider
  (`router.resolve(avoid_provider=)`) answers `C<n>: AGREE|DISAGREE` per claim and `VERDICT: APPROVE|REJECT`. An
  approved report with no claim left is rejected. When nobody else can review, the model is not cleared for the
  report's classification, or the reviewer gives no verdict, the report stays `in_review` and administrators are
  notified; a person's approval counts as independent (`verified`), a rejection needs a reason. `review` is a column
  beside the run `status` (queued|running|done|failed|expired), which the queue keeps using; `""` marks reports
  written before reviews existed. Only an approved report becomes the `report://` document (search, task packages),
  reaches its goal's blackboard, and feeds knowledge; a later rejected run of a recurring request leaves the last
  approved document in place.

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
- **Status: done** (`Proposal.tag`, `Proposal.report_id`, `research._proposals`, `projects.settle_proposals(only=)`).
  The author may end its recommendation with `PROPOSAL: <title> :: <why>` lines (at most 2 per report); on approval
  each becomes a pending proposal tagged `research` with the report and the claims it rests on in its description,
  on the report's goal. They take the normal path: the goal's end settles them (a person, or the GM under
  `proposal_policy=superiors`; restricted ones always need a person), or at once when the goal has already ended.
  An approved proposal becomes a goal like any other (approval, gate, merge). Not taken: a report without a goal
  raises no proposal (`proposals.goal_id` is required, and a goal needs a repository), and the sandbox experiment
  step is left to the person deciding (SPEC-13 experiments exist but are not created automatically).

### S06-R-07 Knowledge transfer — §16 (AT-019)
- Approved report claims become `research` knowledge items with sources and review date
  (SPEC-11 S11-R-01).
- **Status: done** (`research._transfer`). Each `verified` or `supported` claim of an approved report is a knowledge
  item: kind `research`, evidence class FACT (verified) or INFERENCE (supported), sources `report:<id>` and the
  `url:` of every backing document, the request's keywords as technologies, the report's project and goal, the
  classification of its goal/project, `review_due_at` after `KNOWLEDGE_REVIEW_DAYS` (90). They are screened like
  lessons (`learning.screen`), linked in the knowledge graph and checked for contradictions. Nothing is stored for a
  secret project or goal; a claim already stored (a recurring report) is not stored again.

### S06-R-08 Recovery — AT-021
- A report left `running` at restart is re-queued (same rule as goals, SPEC-04 S04-R-05).
- **Status: done** (`api.reset_after_restart`, `research.resume_reviews`). A report left `running` is queued again; a
  written report whose review a restart cut off (`draft` or `in_review` without a note) is reviewed again when
  `ReportThread` starts; reports already waiting for a person stay waiting. A rejected report never answers a new
  request on the same question (`find_overlap`).
