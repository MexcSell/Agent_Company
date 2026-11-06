# Specification: S06-FR-04: research requests by publication date range and language

## Specify S06-FR-04 date-range and language filters (architect, done)

ar forms: `1 document left out`.
  - The line goes into the prompt (after `terms`). When `res.ok`, it is also appended to the stored report: `r.report = res.text + (f"\n\n## Method\n{line}" if line else "")`.
  - `check_claims` still gets `res.text`.
  - `_approved_text` (line 1451) adds `"Method"` to its `("Gaps", "Recommendation", "Sources")` tuple, so the approved report keeps the method line.
  - `executors.Request.data` gets `"filters": counts`.

## (e) API/UI

`src/agent_company/api.py`:
- `ResearchBody` (line 256) adds:
  ```python
      # S06-FR-04
      published_after: str | None = None   # ISO date, e.g. "2026-01-01"
      published_before: str | None = None
      languages: list[str] | str = Field(default_factory=list)  # ["de", "en"] or "de, en"
  ```
- `research_request` (line 2645) passes `published_after=body.published_after`, `published_before=body.published_before` and `languages=body.languages` to `research.submit`. The existing `except ValueError → HTTPException(400, str(e))` turns a bad range or subtag into a 400.
- The list endpoint (`GET /api/research/requests` → `list_reports` → `report_dict`) and every report response carry `published_after`, `published_before` and `languages`. The fields come from `report_dict`, so no endpoint needs its own change.

`src/agent_company/static/app.js`, Research → "Ask the research room":
- The `<details>` summary (line 583) becomes: `More options: keywords, kinds of source, publication dates, languages, priority, repeat, deadline, budget`.
- A new row inside it: `<label>Published from</label><input id="r-pa" type="date">`, `<label>Published to</label><input id="r-pb" type="date">`, and `<label>Languages (comma separated, e.g. en, de)</label><input id="r-lang" placeholder="en, de">`.
- The submit body (line 601) adds `published_after: $("#r-pa").value || null`, `published_before: $("#r-pb").value || null` and `languages: $("#r-lang").value.split(",").map((x) => x.trim()).filter(Boolean)`. A server 400 appears in the existing `toast(err.message)`.
- `reportModal` `meta` (line 626) adds:
  - `published ${a || "…"}..${b || "…"}` when either date is set;
  - `languages: ${langs.join(", ")}` when set.
  
  Both go through `esc()`. The `## Method` line appears as part of the rendered report.

## (f) TESTS

Add a new file `tests/test_research_filters.py`. Tests run offline with their temp DATA_DIR, fetch through `httpx.Client(transport=httpx.MockTransport(handler))` with `allow_private=True` (as in `tests/test_research.py`), and stand in for the model with `monkeypatch.setattr(executors.OfflineExecutor, "run", …)` capturing `req.prompt`.

1. `primary_language`: `'en-US'→'en'`, `'de, en'→'de'`, `'EN'→'en'`, `'*'→''`, `'i-klingon'→''`, `''→''`, `'x-foo'→''`.
2. The language comes from the header: an HTML page served with `Content-Language: de-DE` and `<html lang="en">` gives `crawl(...)[0]["language"] == "de"`. After `store_pages`, `Document.language == "de"`.
3. The language comes from `<html lang="fr-CA">` when there is no header (`"fr"`), and from `<meta http-equiv="content-language" content="it">` when there is no header and no lang attribute (`"it"`). A page that declares nothing gives `""`, even when its text is plainly English.
4. The language comes from a feed: an RSS feed with `<channel><language>de-de</language>` gives entries with `"de"`. An Atom feed with `xml:lang="en"` on `<feed>` and `xml:lang="fr"` on one `<entry>` gives that entry `"fr"` and the others `"en"`.
5. The date range leaves out the right documents and counts them: stored documents published 2025-12-31, 2026-01-01, 2026-06-30 and 2026-07-01, with `after=2026-01-01, before=2026-06-30`. `filter_material` keeps the two inclusive-bound documents and returns `left_out == 2`.
6. The language filter leaves out the right documents and counts them: documents in `de`, `en`, `fr` with `languages=["de","en"]` keep `de` and `en`, with `left_out == 1`. A document outside both filters counts once.
7. Undated documents are kept and labelled: an undated document under a date filter is kept with `'published=unknown'` and `undated == 1`. A document without a language under a language filter is kept with `'language=unknown'` and `unknown_language == 1`. Without filters, nothing is labelled or counted.
8. `run_report` end to end: a request with a range and `languages=["en"]` over matching stored documents. The prompt contains only the kept documents' URLs, plus `published="unknown"` / `language="unknown"` on the labelled ones. The stored `report` ends with a `## Method` line `Filters: … N documents left out, M kept undated, K kept with unknown language.`, with the exact counts.
9. Explicit URLs are filtered the same way: a request whose `urls` points to a mock page served with `Content-Language: fr` and matching the question, with `languages=["en"]`. The page is stored with `language == "fr"`, is absent from the prompt, and is counted in `left_out`.
10. Invalid input is refused: `submit(..., published_after="2026-06-30", published_before="2026-01-01")` raises `ValueError`. So do `languages=["english"]`, `["e"]`, `["en-US"]`, `published_after="not a date"`. Through the API (`TestClient`), each gives a 400. `languages="DE, en, de"` is stored as `["de", "en"]`.
11. Two requests that differ only in their filters are not duplicates: the same question twice, first without filters, then with `languages=["de"]`, gives `duplicate is False` the second time. A third identical to the second (`languages="de"`) gives `duplicate is True` with the second's id. The same holds for a different `published_after`.
12. API round trip: `POST /api/research/requests` with `published_after`, `published_before`, `languages` (as a list, and as a comma string). `GET /api/research/requests` returns them as `"2026-01-01"`, `"2026-06-30"`, `["de", "en"]`. A pre-existing row (NULL `languages`) is returned as `[]`.
13. Migration: `_add_missing_columns` on a DB without the new columns adds `documents.language`, `research_reports.published_after`, `published_before` and `languages`.

Run `uv run pytest -q` serially, without `-n`, and read the summary line.

## (g) DOCS (goal item 6)
- `docs/requirements/ASEAO06-research.md` line 15: S06-FR-04 → `done (S06-R-01 keywords and source preferences; date range and languages: Document.language, ResearchReport.published_after/published_before/languages, research.filter_material)`.
- `docs/requirements/README.md` line 139: remove S06-FR-04 from "Deliberately not taken".
- `docs/feature-gap.md`: add a line for S06-FR-04 date and language filters.
- Optional: `CLAUDE.md`'s research.py entry gets "a document's declared language; requests filtered by publication date range and languages (S06-FR-04)".
- No new network path or executor, so `docs/threat-model.md` is unchanged.

## Conventions
- All writes happen in the existing `session()` blocks: `store_pages`, `submit`, `run_report`. `filter_material`, `primary_language` and `filters_line` are pure functions.
- No new dependency.

---

FINDING: Reports have no Method section today, and `_approved_text` keeps only Objective/Findings/Uncertain/Gaps/Recommendation/Sources. The method line needs both the append in `run_report` and `"Method"` added to that tuple, or it disappears from approved reports.
FINDING: `ResearchReport.languages` gets no DDL default on migrated databases (callable default), so old rows read NULL. Every reader must use `r.languages or []`.
FINDING: Explicit URLs reach the material only through `search()`; there is no separate path today. The spec filters them by filtering the one candidate list rather than adding a new inclusion path.
NEXT: Developer implements (a)–(g) and adds `tests/test_research_filters.py`; QA checks each numbered test against goal item 5.
MESSAGE handoff to developer: The spec is this answer. Filter before the `[:6]` cut in `_material`, set `language` in `crawl`/`parse_feed` next to `published_at`/`license`, and write `doc.language` only in `_provenance`.
