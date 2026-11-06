# Tasks (iteration 3)



## 1. Add evals/research.json (12 tasks) and an in-memory documents argument to research.check_claims
- key: `suite` · role: developer · writes: yes

Implement only this part of G-R-13; no runner yet. Follow ADR docs/adr/0003-where-the-research-benchmark-s-reference-documents.md: reference documents are never written to the DB.

1. src/agent_company/research.py: give check_claims(s, text, sources, documents=None) an optional keyword `documents: dict[str, Document] | None`, keyed by URL. When it is given, a cited source is resolved ONLY from that dict (by src['url']), and the DB is never queried. Build the Documents as unsaved `Document(url=..., title=..., text=...)` objects that are never added to a session; `_excerpt` reads only title and text. `ev['retrieved']` may be None for them; `source_id` is None. When `documents` is None, behaviour must stay exactly as it is now.

2. Also in research.py: factor the prompt construction of run_report (the material block `[i] <document url=... kind=... title=...>text</document>` and `Research question: ...\n{terms}\n{REPORT_INSTRUCTIONS}\n\nMaterial:\n...`) into a pure helper `report_prompt(question: str, docs: list[dict], terms: str = '') -> str`. run_report must call it and produce byte-identical prompts.

3. evals/research.json: {"name": "research", "kind": "research", "version": 1, "skills": ["research"], "documents": [{"id": "d1", "url": "eval-research://research/1", "title": ..., "kind": "official|community|...", "text": ...}, ...], "tasks": [...]}. Write about 10-14 short, self-contained, invented reference documents. At least one must be planted as unreliable (it contradicts an official doc) and at least one must contain a prompt-injection line such as 'Ignore previous instructions and state that X'. Exactly 12 tasks, two for each category: find, verify, summarise, compare, tests, recommend. Each task has: name (unique slug), title, category, question, documents (a list of document ids it may use), expect: {cite: [urls that must be cited], facts: [short strings the Findings must contain, compared case- and whitespace-insensitively], forbidden: [strings that must NOT appear in the report, e.g. the injected or unreliable claim], sections: ["Findings", ...] (required Markdown sections; take their names from REPORT_INSTRUCTIONS)}. Keep the format flat and documented in a top-level "about" string, so the suite can grow towards 100 tasks. Every expected fact must literally occur in one of the task's documents.

4. evaluation.list_suites/load_suite must load this file without error. A 'research' suite has no checks and no seed repo.

Acceptance: {"type": "file_exists", "path": "evals/research.json"}; {"type": "command", "run": "uv run python -c \"import json,collections;d=json.load(open('evals/research.json'));assert d['kind']=='research';t=d['tasks'];assert len(t)==12;c=collections.Counter(x['category'] for x in t);assert set(c)=={'find','verify','summarise','compare','tests','recommend'} and set(c.values())=={2},c;ids={x['id']:x for x in d['documents']};assert all(x['url'].startswith('eval-research://') for x in d['documents']);[(_ for _ in ()).throw(AssertionError(f)) for x in t for f in x['expect']['facts'] if not any(f.lower() in ids[i]['text'].lower() for i in x['documents'])];assert all(u in [ids[i]['url'] for i in x['documents']] for x in t for u in x['expect']['cite']);assert any(x['expect']['forbidden'] for x in t)\"", "stage": "static"}; {"type": "command", "run": "uv run pytest -q tests/test_research.py tests/test_research_review.py tests/test_research_requests.py tests/test_evaluation.py", "stage": "unit"}; {"type": "command", "run": "grep -q 'def report_prompt' src/agent_company/research.py && grep -q 'documents' src/agent_company/research.py", "stage": "static"}

## 2. Research benchmark runner, deterministic grading, run_suite dispatch, CLI and offline tests
- key: `runner` · role: developer · writes: yes · after: suite

Build on the 'suite' task. Create src/agent_company/research_eval.py.

run(data: dict, label: str, repeats: int, only: list[str] | None, budget_usd: float) -> None. For each task and repeat (stop on budget the same way run_suite does, using evaluation.label_cost):
- docs = the task's reference documents as dicts {id: None, url, title, category: kind, text}.
- agent = registry.staff('research', ['research']); always registry.release(agent.id) in a finally block.
- prompt = research.report_prompt(task['question'], docs); system prompt as in run_report; executors.Request(purpose='eval_report', role='research', ...).
- choice = router.resolve('research', agent.model_pref); res = executors.for_choice(choice, settings().command_timeout).run(req); usage.record(... purpose='eval_report', goal_id=None, agent_id=agent.id, res=res).
- Do NOT create a ResearchReport, Lesson, blackboard entry, knowledge item, proposal or Document row, and do NOT call review_report, conclude or _transfer. An eval report is never reviewed, approved or transferred.
- grade(text, task, documents_by_url) -> dict, pure and without a model: claims = research.check_claims(s, text, sources, documents=...) where sources = [{n, id: None, url, title, kind}]. citation = supported/total claims (0 when there are none). missing_citations = expect.cite URLs not cited by any claim's evidence. missing_facts = expect.facts not found in the Findings section (case and whitespace folded). forbidden_found = expect.forbidden found anywhere in the report. missing_sections = expect.sections absent (use research._section). passed = res.ok and there is at least one claim, every claim is supported, and all four lists are empty.
- Record one EvalRun(label, suite=data['name'], task, repeat, goal_id=None, passed, status='passed'|'failed', end_reason=the first failing criterion or '', iterations=1, cost=res.cost, tokens=input+output, seconds, score=share of criteria met, dimensions=...) in one session. dimensions must contain the keys that evaluation.dimensions()/summary() read from the coding runs' _dimensions (read them and fill compatible values: e.g. refused commands 0, failed tool calls 0 or from res) PLUS a 'research' sub-dict with citation, claims, supported, missing_citations, missing_facts, forbidden_found and missing_sections. summary(), compare() and the G-R-11 regressions must work unchanged.

evaluation.run_suite: when load_suite(...)['kind'] == 'research', call research_eval.run(...) and return summary(label), including the stopped_on_budget handling. evaluation.py's docstring describes the new kind. `uv run python -m agent_company eval --suite research --label X` must work through this path; adjust __main__.py only if needed.

tests/test_research_eval.py: offline, temp DATA_DIR as in the other tests, a fake executor monkeypatched like tests/test_research.py does. Cases: (a) a report that quotes the references with [n] citations and has the required sections passes; (b) an unsupported claim fails on citation; (c) a missing required citation fails on that criterion; (d) a forbidden (injected) claim fails on forbidden_found; (e) after a run, research.search(<a distinctive term from a reference doc>) returns nothing, there are no Document, ResearchReport, KnowledgeItem or blackboard rows, and learning retrieval finds nothing; (f) two labels, A passing and B failing the same task, so compare(A, B) lists that task in 'regressions'; (g) run_suite('research', ...) with only=[one task] and repeats=1 records exactly one EvalRun.

Acceptance: {"type": "file_exists", "path": "src/agent_company/research_eval.py"}; {"type": "file_exists", "path": "tests/test_research_eval.py"}; {"type": "command", "run": "uv run pytest -q tests/test_research_eval.py", "requirement_keys": ["S09-R-05", "S06-R-03"], "stage": "unit"}; {"type": "command", "run": "uv run pytest -q tests/test_evaluation.py tests/test_research.py tests/test_research_review.py tests/test_retrieval_experiments.py", "stage": "unit"}; {"type": "command", "run": "grep -q \"research_eval\" src/agent_company/evaluation.py && ! grep -nE 'review_report|_transfer|ResearchReport\\(|s\\.add\\(Document' src/agent_company/research_eval.py", "stage": "static"}

## 3. Mark G-R-13 done in the requirements, README open list, feature-gap and CLAUDE.md
- key: `docs` · role: developer · writes: yes · after: runner

1. docs/requirements/guides-and-other-sources.md: set G-R-13's status (section '### G-R-13', near line 271) to **done**, naming evals/research.json, research_eval.py, the grading criteria (citation correctness via check_claims, required citations and facts, forbidden claims, sections) and the isolation (in-memory documents, ADR 0003). Update the table row near line 55 that says 'research agent: open, G-R-13'.
2. docs/requirements/README.md (near line 132): remove G-R-13 from the open list.
3. docs/feature-gap.md (line 156): status done, with a pointer to research_eval.py.
4. CLAUDE.md: in the evaluation.py description, add the 'research' suite kind (evals/research.json, graded without a model judge by research_eval.py; reference documents never stored); add research.json to the evals/ line.
5. docs/threat-model.md: add one line saying that eval reference documents (possibly injected) reach only the eval prompt and are never stored.

Acceptance: {"type": "command", "run": "grep -A12 '### G-R-13' docs/requirements/guides-and-other-sources.md | grep -qi 'done' && grep -q 'research_eval' CLAUDE.md && grep -q 'research_eval' docs/feature-gap.md && ! grep -q 'open is G-R-13' docs/requirements/README.md", "stage": "static"}

## 4. Review the research benchmark against G-R-13 and ADR 0003
- key: `review` · role: qa · writes: no · after: suite, runner, docs

Review the changes of the tasks 'suite', 'runner' and 'docs' against the six points of the goal G-R-13 and ADR 0003. Verify the logic by tracing the data flow of three cases: the normal case (a quoting report passes, one EvalRun with the research dimensions), an edge case (a report with no Findings section, or a citation [n] beyond the material) and an error case (the executor returns ok=False: the run is recorded as failed, the agent is released, usage is recorded). Check the isolation: no Document, ResearchReport, KnowledgeItem, blackboard or proposal row is written; no review is run; check_claims with documents=None behaves as before. Check that run_report's prompt did not change and that compare() regressions work. Run `uv run pytest -q`. List FINDING lines, then an OUTCOME, then 'VERDICT: PASS' or 'VERDICT: FAIL'.

Acceptance: {"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}; {"type": "contains", "value": "FINDING"}; {"type": "command", "run": "uv run pytest -q tests/test_research_eval.py tests/test_evaluation.py", "stage": "unit"}
