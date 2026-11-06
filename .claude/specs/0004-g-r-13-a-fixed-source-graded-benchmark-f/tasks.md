# Tasks (iteration 2)



## 1. Write the complete G-R-13 research benchmark spec to docs/specs/g-r-13-research-benchmark.md
- key: `spec` · role: architect · writes: yes

Write the FULL specification to docs/specs/g-r-13-research-benchmark.md with your file tool. Do NOT write under .claude/specs/: your tool refuses that path, which is why iteration 1 failed (K6). Start from the surviving part of iteration 1's spec in .claude/specs/0004-g-r-13-a-fixed-source-graded-benchmark-f/spec.md (sections 3.2 end to 7 and the Decision). Rebuild the missing beginning from src/agent_company/research.py (REPORT_INSTRUCTIONS ~l.1069, run_report ~l.1294, check_claims ~l.1457, _section, search ~l.615, crawl/submit/store_pages) and src/agent_company/evaluation.py (load_suite, run_suite ~l.157, summary, compare, dimensions, HEADLINE, the regressions guard). Required sections:
1. Suite format for evals/research.json: kind 'research', version, documents [{n, url 'eval-research://research/<n>', title, text, reliability: normal|unreliable|injected}], and tasks [{name, title, category find|verify|summarise|compare|tests|recommend, question, docs [n...], expect {cite [n...], facts [str], forbidden [str | {phrase, from}], sections [str]}}]. Also a validate_research() with its errors. Plan for 12 tasks, 2 per category, so the file can grow to 100.
2. Runner (run_research_suite): staffing of the research room / route 'research' via router and executors.for_choice, the prompt built from the same REPORT_INSTRUCTIONS with the reference documents numbered [n], usage.record for cost, budget stop, repeats, trial/trainee_id, and freeing the agent on error.
3. Grading without a model judge: check_claims gets an optional documents= argument (in-memory RefDocument). The criteria are citation (supported share), completeness (cite+facts), forbidden and sections, plus pass rule, score and end_reason. The EvalRun row and the dimensions keys (compliance, tool_use, security, research) are filled so summary(), dimensions(), compare() and regressions work unchanged, and HEADLINE gets research.
4. Isolation I1-I4: nothing is written to documents, knowledge, blackboard, proposals, research_reports, lessons or kg_edges. Add EVAL_SCHEME to research.py, refuse it in crawl/submit/add_source, and skip it in store_pages. An eval report is never reviewed, approved or transferred.
5. CLI: eval --suite research --label X.
6. The test list (keep iteration 1's 12 tests).
7. The docs to update.
Keep decision #3 (in-memory documents). Do not change any code in this task.

Acceptance: {"type": "file_exists", "path": "docs/specs/g-r-13-research-benchmark.md"}; {"type": "command", "run": "grep -q 'check_claims' docs/specs/g-r-13-research-benchmark.md && grep -q 'eval-research://' docs/specs/g-r-13-research-benchmark.md && grep -qi 'forbidden' docs/specs/g-r-13-research-benchmark.md && grep -q 'REPORT_INSTRUCTIONS' docs/specs/g-r-13-research-benchmark.md && grep -qi 'isolation' docs/specs/g-r-13-research-benchmark.md && grep -q 'run_suite' docs/specs/g-r-13-research-benchmark.md", "stage": "static"}; {"type": "command", "run": "test $(wc -c < docs/specs/g-r-13-research-benchmark.md) -gt 4000"}

## 2. Implement the research suite, runner, grading, isolation, CLI, tests and docs
- key: `impl` · role: developer · writes: yes · after: spec

Implement docs/specs/g-r-13-research-benchmark.md exactly.
(a) evals/research.json: kind 'research', reference documents at eval-research://research/<n>, including at least one unreliable and one injected document, and 12 tasks, 2 per category (find, verify, summarise, compare, tests, recommend), each with question, docs and expect {cite, facts, forbidden, sections}.
(b) research.py: EVAL_SCHEME. Give check_claims an optional documents= argument so it grades against in-memory documents; its behaviour without it is unchanged. Refuse the scheme in crawl/submit/add_source and skip it in store_pages.
(c) evaluation.py: validate_research, and a research runner dispatched by run_suite when kind == 'research'. It uses the same REPORT_INSTRUCTIONS, executor via executors.for_choice and router route 'research', and records usage. Grading uses no model judge. Write one EvalRun per task × repeat (passed, cost, tokens, seconds, and dimensions research/compliance/tool_use/security), add the research entry to dimensions() and HEADLINE, and keep summary()/compare()/regressions working unchanged. Write nothing to documents, knowledge_items, the blackboard, proposals, research_reports, lessons or kg_edges, and never review, approve or transfer an eval report.
(d) 'uv run python -m agent_company eval --suite research --label X' must work.
(e) tests/test_research_eval.py, offline, using the conftest temp DATA_DIR and a fake executor monkeypatched on executors.for_choice. It covers: a passing report; failure on an unsupported claim, a missing required citation and a forbidden claim, each on that criterion; a missing section; an executor error; reference documents invisible to research.search and to learning retrieval/task_references, with row counts unchanged; compare() reporting a regression between two labels.
(f) Docs: set G-R-13 to done in docs/requirements/guides-and-other-sources.md (section + §6 table row), remove it from the open list in docs/requirements/README.md, update docs/feature-gap.md (line ~156), CLAUDE.md's evaluation.py description and docs/threat-model.md.
Follow CLAUDE.md: use one session per DB write (log_event(..., s=s)), put new columns in db.py, no network in tests. Run 'uv run pytest -q' and report the output.

Acceptance: {"type": "file_exists", "path": "evals/research.json"}; {"type": "file_exists", "path": "tests/test_research_eval.py"}; {"type": "command", "run": "uv run python -c \"import json,collections;d=json.load(open('evals/research.json'));assert d['kind']=='research';c=collections.Counter(t['category'] for t in d['tasks']);assert len(d['tasks'])>=12 and set(c)=={'find','verify','summarise','compare','tests','recommend'} and min(c.values())>=2;assert all(x['url'].startswith('eval-research://') for x in d['documents'])\"", "requirement_keys": ["G-R-13"], "stage": "static"}; {"type": "command", "run": "uv run pytest -q tests/test_research_eval.py", "requirement_keys": ["G-R-13", "S09-R-05"], "stage": "unit"}; {"type": "command", "run": "uv run pytest -q tests/test_evaluation.py tests/test_research.py", "requirement_keys": ["S13-R-05", "S06-R-03"], "stage": "integration"}; {"type": "command", "run": "grep -q 'G-R-13' docs/requirements/guides-and-other-sources.md && grep -q 'research' CLAUDE.md && ! grep -q 'open (G-R-13' docs/feature-gap.md", "stage": "static"}

## 3. Review the research benchmark against the spec and G-R-13
- key: `review` · role: qa · writes: no · after: impl

Review the change against docs/specs/g-r-13-research-benchmark.md and G-R-13 in docs/requirements/guides-and-other-sources.md, reusing the approach of K5.
Verify the logic by tracing the data flow of three cases:
- normal: a report that quotes the references passes, and its EvalRun dimensions appear in summary();
- edge: a stray citation [n] outside the task's documents, and a forbidden phrase planted by an injected document (security.secure False);
- error: an executor failure gives an error run and frees the agent.
Check isolation: grep every writer and reader of documents, knowledge_items, the blackboard, proposals and research_reports, and confirm that no eval-research:// content can reach them or any prompt outside the runner, and that eval reports are never reviewed, approved or transferred.
Check that check_claims without documents= behaves as before, that compare()/regressions work for research labels, that 'eval --suite research' dispatches, that the tests are offline, and that the docs (requirements file, README open list, feature-gap, CLAUDE.md, threat model) were updated.
Run 'uv run pytest -q'. Write a FINDING line for each problem, then an OUTCOME, and end with 'VERDICT: PASS' or 'VERDICT: FAIL'. Do not change files.

Acceptance: {"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}; {"type": "contains", "value": "OUTCOME"}; {"type": "command", "run": "uv run pytest -q", "stage": "integration"}
