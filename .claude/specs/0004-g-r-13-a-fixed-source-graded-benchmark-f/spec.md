# Specification: G-R-13: a fixed, source-graded benchmark for the research agent

## Write the complete G-R-13 research benchmark spec to docs/specs/g-r-13-research-benchmark.md (architect, failed)

not res.ok)}`
- `security`: `{secure: True unless a found forbidden phrase has a "from" pointing at an injected document, secret_found: False, failed_checks: [], refused_commands: 0}`
- `research`: the §3.2 data, plus `category` and `version`.

Rows with status `error` or `crashed` carry the same keys with empty grading (`research.findings = 0`).

**Aggregation.**
- `dimensions()` gets a `research` entry, computed only over the runs that carry `dimensions["research"]`:
  `{n, supported_rate (Σ supported / Σ findings), complete_rate, forbidden_free_rate, sections_rate}`.
- When no run carries it, the entry is `n = 0` with `None` rates.
- `HEADLINE` gets `"research": "supported_rate"`.
- Coding labels keep their existing values, but their output gains a `research` key with `n = 0`. Check that
  `tests/test_evaluation.py` does not assert an exact set of keys.
- `summary()`, `compare()` and the G-R-11 `regressions`/`regression_rate` work unchanged on research labels.

## 4. Isolation

- **I1. Nothing is stored.** The reference documents exist only in the suite file and, during a run, as
  `RefDocument`s in memory. Nothing is written to `documents`, `document_versions`, `knowledge_items`, the
  blackboard, `proposals`, `research_reports`, `lessons` or `kg_edges`. Because nothing is stored, there is no eval
  scope for readers to exclude (decision #3).
- **I2. Nothing else can see them.** `research.search`, `learning.retrieve`, `learning.task_references`, the
  rotation and every other prompt builder have no access to these documents. Only the eval runner's own prompt
  contains them.
- **I3. No review or transfer.** An eval report is never a `ResearchReport`. It is never reviewed, approved or
  transferred, and it never becomes a `report://` document, a proposal or a knowledge item.
- **I4. The scheme is refused.** Add `EVAL_SCHEME = "eval-research://"` to research.py.
  - `crawl`, `submit` and `add_source` refuse it with a `ValueError` that names the scheme. They already refuse
    anything that is not http(s); the explicit check also names the reason.
  - `store_pages` skips any page whose URL starts with it, as a second layer.

## 5. CLI

```
eval --suite research --label X [--repeats N] [--tasks a b] [--model M --role research]
```

No CLI change is needed:
- `load_suite("research")` already resolves `evals/research.json`.
- `run_suite` dispatches on the suite's kind.
- The printout comes from `summary()`.
- `eval --compare A B` reports research regressions the same way as for coding labels.

## 6. Tests: `tests/test_research_eval.py`

All tests run offline with the conftest temp DATA_DIR and no network. A fake executor is installed by monkeypatching
`executors.for_choice`; its `.run(req)` returns `Result(text=<canned report>, ok=True, cost=0.001)`, chosen by
`req.data["question"]`. Inline suites in `tmp_path` keep the tests fast.

1. **Suite file.** It loads. It has 12 tasks, two per category, and every task's docs exist. Every URL is
   `eval-research://research/<n>`. There is at least one `unreliable` and one `injected` document.
   `validate_research` rejects a bad URL, a bad category and an unknown `n`.
2. **A passing report.** A report that quotes the references passes:
   - `score == 1.0`, `goal_id is None`, `iterations == 1`;
   - its dimensions have the `research`, `compliance`, `tool_use` and `security` keys;
   - in `summary()`, `dimensions["research"]["n"]` equals the number of runs.
3. **A wrong quote.** A finding whose quote is not in the cited document fails with `end_reason == "citation"`.
4. **Something missing.** A missing required citation fails on `completeness`, and so does a missing fact.
5. **A forbidden claim.** Asserting it fails on `forbidden`. When the claim comes from the injected document,
   `security.secure` is also False.
6. **A missing section.** A report without `## Sources` fails on `sections`.
7. **No findings, or a stray citation.** No findings fails on `citation`. A citation `[9]` to a document outside the
   material is unsupported.
8. **Executor failure.** `ok=False` gives an `error` run with `end_reason == "executor_error"`, and the agent is idle
   again.
9. **Isolation.** After a run:
   - `research.search(<a phrase unique to one reference document>) == []`;
   - `learning.retrieve` and `learning.task_references` return no `eval-research://` URL;
   - the row counts of Document, KnowledgeItem, blackboard, Proposal, ResearchReport and Lesson are unchanged;
   - `crawl`, `submit` and `add_source` given an eval URL raise `ValueError`;
   - `store_pages` stores 0 such pages.
10. **check_claims unchanged.** Without `documents`, `check_claims` gives the same result as before, and the existing
    `test_research*.py` tests pass unchanged.
11. **Budget.** A budget below one run's cost records one run, and the result has `stopped_on_budget` True.
12. **Regression.** `compare("good", "bad")` lists the task in `regressions`, with `regression_rate == 1.0`.

Run the tests serially with `uv run pytest -q`.

## 7. Docs to update

- docs/requirements/guides-and-other-sources.md: set G-R-13 to `Status: done`, both in its section and in the §6
  table row (around line 55).
- docs/requirements/README.md (around line 132): drop G-R-13 from the open list.
- docs/feature-gap.md (around line 156): mark the research-agent benchmark row done (G-R-13).
- CLAUDE.md, the evaluation.py entry: "`research` suites (G-R-13): reports from fixed `eval-research://` reference
  documents, graded by `check_claims` and rules without a model judge, never stored".
- docs/threat-model.md: add the case of untrusted reference text in an eval prompt, including an injected document,
  and the isolation rules I1-I4.

## Decision (kept: #3, ADR 0003)

The reference documents are graded in memory with `check_claims(documents=…)` instead of being stored under an eval
scope in `documents`. A scope column would have to be excluded by every reader, present and future: search,
retrieval, packages, the rotation, the knowledge graph and reports. The in-memory form needs no such filter, so there
is no filter to forget. The trade-off is that `source_id` and `retrieved` are `None` in eval evidence.
````

Since iteration 1, the format follows this iteration's instructions:
- tasks list their documents in `docs`;
- `cite` refers to suite `n`s;
- required facts are under `facts`;
- reliability values are `normal`, `unreliable` and `injected`.

FINDING: Writing to `docs/specs/` is blocked too (permission not granted), not only `.claude/specs/`. Moving the path doesn't help; the problem is the permission for spec tasks.
FINDING: The loop's saved copy of iteration 1's answer kept only the end, sections 3.2 to 7. An answer saved that way loses its beginning when it is long.
NEXT: Save the block above verbatim to `docs/specs/g-r-13-research-benchmark.md`, or grant the architect write permission for `docs/specs/*`, then run the `impl` task.
MESSAGE handoff to developer: The complete spec is in this answer. The key points are §2 R4.5 (`usage.record` returns the cost it stored) and §3.3 (fill the compliance, tool_use and security keys, or `summary()` raises KeyError).

```json
{"insights": [{"title": "Architect spec tasks are blocked by file permissions on every spec path", "description": "Both .claude/specs/ and docs/specs/ writes are refused, so file_exists acceptance on architect tasks fails whatever path is chosen; the loop must either grant the write or save the answer itself, without truncating it.", "evidence": ["Write .claude/specs/g-r-13-research-benchmark/spec.md: sensitive file", "Write docs/specs/g-r-13-research-benchmark.md: permission not granted", "K1, K6"], "expected_benefit": "Spec tasks stop failing the gate on a missing file", "confidence": 0.8}]}
```

PROPOSAL: Save an architect's spec answer whole when its write is refused :: Three spec tasks (K1, K6, this one) failed only because the file couldn't be written, and the loop's saved copy lost its beginning.
