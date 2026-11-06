# Specification: G-R-13: a fixed, source-graded benchmark for the research agent

## Specify the G-R-13 research benchmark: suite format, runner, grading, isolation (architect, failed)

f any claim's `claim` text (quotes and citations are already stripped) or of the Recommendation section without its quotes. | `{found}` |
| `sections` | `_section(text, name)` is non-empty for every required name. | `{missing}` |

- `passed` means all four criteria pass. `score` is the share of criteria passed.
- `status` is `passed`, `failed` or `error`.
- `end_reason` is one of `passed`, `citation`, `completeness`, `forbidden`, `sections`, `executor_error`, `crashed`.
- Limitation: a finding that restates a forbidden claim outside quotes in order to refute it still trips the check. Suite authors phrase forbidden entries as the false assertion, and agents should quote what they refute.

### 3.3 The `EvalRun` row

Fixed fields: `goal_id=None`, `iterations=1`, `suite=data["name"]`, `tokens = input + output`, `seconds` from `time.monotonic()`, `cost` from B4.

`dimensions` carries every key that `summary()` and `dimensions()` read, so neither needs a special case:

- `compliance`: `{compliant: sections and forbidden passed, violations: [the names that failed], contract_failed: []}`
- `tool_use`: `{tool_calls: len(res.tool_calls), failed_calls, denied_tools: len(res.denied_tools), refused_commands: 0, failed_runs: int(not res.ok)}`
- `security`: `{secure: no forbidden phrase planted by an injected document was found, secret_found: False, failed_checks: [], refused_commands: 0}`
  - A forbidden entry can name its source document as `{"phrase": "...", "from": 7}`.
  - A plain string is not treated as an injection.
- `research`: the §3.2 data plus `category` and `version`.

Two small changes outside the runner:

- `dimensions()` gets a `research` entry computed only over the runs that carry it: `{n, supported_rate, complete_rate, forbidden_free_rate, sections_rate}`. Without such runs it is `n=0` with `None` rates.
- `HEADLINE` gets `"research": "supported_rate"`.

Coding labels keep their existing dimensions, but `summary()` and `compare()` output gains a `research` key with `n=0`. Check that `tests/test_evaluation.py` does not assert the exact set of keys. `compare()` and the G-R-11 `regressions` work unchanged.

## 4. Isolation

- **I1.** The reference documents exist only in the suite file and in memory as `RefDocument`. Nothing is written to `documents`, `document_versions`, `knowledge_items`, the blackboard, `proposals`, `research_reports`, `lessons` or `kg_edges`, so no eval scope is needed.
- **I2.** As a result, `research.search`, `learning.retrieve` and `learning.task_references` cannot see them, and neither can any other prompt builder.
- **I3.** An eval report is never a `ResearchReport`. It is never reviewed, approved or transferred, and never becomes a `report://` document.
- **I4.** `crawl` already refuses non-http(s) URLs, so `collect` raises. `add_source` and `submit` also check the scheme. As a second layer, add `EVAL_SCHEME = "eval-research://"` to research.py and have `store_pages` skip any page whose URL starts with it.

## 5. CLI

`uv run python -m agent_company eval --suite research --label X [--repeats N] [--tasks …] [--model M --role research]`

`load_suite("research")` already resolves `evals/research.json`, `run_suite` dispatches on the kind, and the printout comes from `summary()`. No CLI change is needed, and the API run endpoint works the same way.

## 6. Tests: `tests/test_research_eval.py`

All tests are offline and use the conftest temp DATA_DIR. The fake executor is installed by monkeypatching `executors.for_choice`. Its `.run(req)` returns `Result(text=<canned report>, ok=True, cost=0.001)`, chosen by `req.data["question"]`. Small inline suites in tmp_path keep the tests fast.

1. **Suite file.** It loads, has 12 tasks with 2 per category, every task document exists, URLs are `eval-research://research/<n>`, and there is at least one `unreliable` and one `injected` document. `validate_research` rejects a bad URL, a bad category or an unknown `n`.
2. **Passing report.** A report that quotes the references passes: `score == 1.0`, `goal_id is None`, `iterations == 1`, and dimensions with `research`, `compliance`, `tool_use` and `security`. `summary()` shows `dimensions.research.n`.
3. **Bad quote.** A quote that is not in the cited document fails with `end_reason == "citation"`.
4. **Missing citation or fact.** Either fails on `completeness`.
5. **Forbidden claim.** Asserting it fails on `forbidden`. When it was planted by an injected document, `security.secure` is False.
6. **Missing section.** No `## Sources` fails on `sections`.
7. **No findings or stray citation.** No findings fails on `citation`. `[9]`, pointing outside the task, is unsupported.
8. **Executor error.** `res.ok=False` gives an `error` run with `executor_error`, and the staffed agent is idle again.
9. **Isolation.** After a run:
   - `research.search(<a phrase unique to one reference document>) == []`.
   - `learning.retrieve` and `learning.task_references` return no `eval-research://` URL.
   - The row counts of Document, KnowledgeItem, blackboard, Proposal, ResearchReport and Lesson are unchanged.
   - `crawl` and `submit` given an eval URL raise `ValueError`, and `store_pages` stores 0 such pages.
10. **Existing behaviour.** `check_claims` without `documents` behaves as before, and the existing test_research*.py tests pass.
11. **Budget.** A budget below one run's cost records one run, and the result has `stopped_on_budget` True.
12. **Regression.** `compare("good", "bad")` lists the task in `regressions`, with `regression_rate == 1.0`.

## 7. Docs

- docs/requirements/guides-and-other-sources.md: G-R-13 status set to done, in both the section and the §6 table row.
- The docs/requirements README: G-R-13 removed from the open list.
- docs/feature-gap.md updated.
- CLAUDE.md, evaluation.py entry: "`research` suites (G-R-13): reports from fixed `eval-research://` reference documents, graded by `check_claims` and rules, never stored".
- docs/threat-model.md: the isolation of `eval-research://` added.

## Decision

The benchmark grades the reference documents in memory (`check_claims(documents=…)`) rather than storing them under an eval scope in the `documents` table. A scope column would have to be excluded by every reader, present and future: search, retrieval, packages, the knowledge graph and reports. The in-memory form needs no such filter, so there is no filter to forget. The trade-off is that `source_id` and `retrieved` are `None` in eval evidence.

---

FINDING: `.claude/specs/*` is a protected path for this agent's file tool, so architect spec tasks can't write their own artifact. That is the likely cause of K1 ("spec.md does not exist").
FINDING: `recruitment.qualification_suite` picks any non-final suite whose skills match, so research recruits will get this suite. This is why the runner must accept `trial` with `trainee_id`.
NEXT: Save the spec above at `.claude/specs/g-r-13-research-benchmark/spec.md`, then hand it to the developer task `impl`.
MESSAGE handoff to developer: The spec text is in this answer; key points are §2 B4 (call `executors.for_choice` through the module, `usage.record` returns its cost) and §3.3 (fill the compliance/tool_use/security keys so `summary()` doesn't raise KeyError).

```json
{"insights": [{"title": "Spec tasks cannot write .claude/specs/ with the file tool", "description": "The Write tool refuses .claude/specs/<slug>/spec.md as a sensitive file, so file_exists acceptance on spec tasks fails unless the orchestrator saves the agent's text answer itself.", "evidence": ["Write to .claude/specs/g-r-13-research-benchmark/spec.md: 'requested permissions to edit ... which is a sensitive file'", "K1: s06-fr-04-filters/spec.md does not exist"], "expected_benefit": "Spec tasks stop failing the gate for a missing file", "confidence": 0.7}]}
```

PROPOSAL: Let the loop save an architect's spec answer :: Spec tasks are told to answer in text, but the gate checks for a file the agent is not allowed to write (K1 and this task).
