# Requirements: S06-FR-04: research requests by publication date range and language

Implement the rest of ASEAO06 S06-FR-04 in docs/requirements/ASEAO06-research.md: research requests with a publication date range and languages. It was not taken because the knowledge store had no publication date; G-R-10 has since added Document.published_at (feeds, page meta tags), and pages declare their language.

Build:
1. Document.language (db.py, migrated by _add_missing_columns): the primary language subtag, lower case ("en", "de"), from the HTTP Content-Language header, <html lang>, <meta http-equiv="content-language">, a feed's <language> or xml:lang; empty when unknown. Never guessed from the text. Set by the crawler and the feed reader where they already read published_at and license.
2. ResearchReport: published_after and published_before (dates, optional) and languages (list of primary subtags, empty = any). research.submit validates them (after <= before; a subtag is 2-3 letters) and research.find_overlap treats a request with different filters as a different request.
3. run_report: material outside the date range or not in one of the languages is left out before the model sees it; a document with an unknown date or language is kept but labelled as such in the material, and the report's method line states how many documents the filters left out and how many were kept undated or of unknown language. Explicit URLs of the request are filtered the same way.
4. API: ResearchBody and the request list carry the new fields; Research -> Ask the research room -> More options gets "Published from / to" (date inputs) and "Languages" (comma-separated). The research request shows them.
5. Tests (offline, temp DATA_DIR, fake fetch): the language is read from the header, the html tag and a feed; a date range and a language filter leave out the right documents and count them; undated documents are kept and labelled; invalid ranges and subtags are refused; two requests differing only in their filters are not duplicates.
6. Update S06-FR-04's status in docs/requirements/ASEAO06-research.md, the README's "Deliberately not taken" list and docs/feature-gap.md.

Keep to the repository's conventions (CLAUDE.md): one session per DB write, new columns in db.py, no network in tests.

## Quality commands
- `uv run pytest -q -p no:cacheprovider`
