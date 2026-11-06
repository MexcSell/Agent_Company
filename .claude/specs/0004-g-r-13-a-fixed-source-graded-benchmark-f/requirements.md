# Requirements: G-R-13: a fixed, source-graded benchmark for the research agent

Implement G-R-13 in docs/requirements/guides-and-other-sources.md (source: the research agent guide, /home/luca/update/1/111.pdf section 6). The eval harness (evaluation.py) only runs coding goals in scratch repositories; the research agent has no benchmark.

Build:
1. A suite kind 'research' (evals/research.json): reference documents (url under a reserved scheme such as eval-research://<suite>/<n>, title, text) and tasks, each with: name, title, category (find | verify | summarise | compare | tests | recommend), the question, the reference documents it may use, and a deterministic expectation (URLs that must be cited, quotes or facts the findings must contain, claims that must NOT be made, e.g. from a planted unreliable or injected document). Start with 12 tasks, two per category; keep the format easy to grow towards the guide's 100.
2. A runner, dispatched by evaluation.run_suite when the suite's kind is 'research': for each task and repeat, put the reference documents in front of the research room and have it write a report exactly as research.run_report does (same REPORT_INSTRUCTIONS, same executor and router route 'research', usage recorded), then grade it WITHOUT a model judge: research.check_claims against the reference documents (share of supported findings = citation correctness), required citations and facts present (completeness), forbidden claims absent, required sections present. Record one EvalRun per run (passed, cost, tokens, seconds, and the grading in dimensions) so summary(), compare() and the G-R-11 regression guard work unchanged.
3. Isolation: the reference documents must never reach research.search, knowledge items, the blackboard, proposals or any agent prompt outside these eval runs, and an eval report is never reviewed, approved or transferred. Do not write them into the live documents table without an explicit eval scope that every reader excludes.
4. 'eval --suite research --label X' works from the CLI.
5. Tests (offline, temp DATA_DIR, a fake executor): a report that quotes the references passes; one with an unsupported claim, a missing required citation or a forbidden claim fails on that criterion; the reference documents are invisible to research.search and learning retrieval; compare() reports a regression between two labels.
6. Update G-R-13's status in docs/requirements/guides-and-other-sources.md, the README's open list and docs/feature-gap.md, and CLAUDE.md's evaluation.py description.

Keep to the repository's conventions (CLAUDE.md): one session per DB write, new columns in db.py, no network in tests.

## Quality commands
- `uv run pytest -q -p no:cacheprovider`
