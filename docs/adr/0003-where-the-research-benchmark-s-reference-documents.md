# ADR 0003: Where the research benchmark's reference documents live during grading

Status: Accepted
Date: 2026-11-06
Decision record: #3 · goal #4 (G-R-13: a fixed, source-graded benchmark for the research agent)
Category: technical

## Context

How can research.check_claims grade an eval report against reference documents without putting them in the live documents table?

## Options

- In-memory Document objects passed to check_claims through a new optional `documents` argument, never added to a session
- Rows in `documents` with an eval scope column that every reader (search, retrieval, rotation, kgraph) excludes
- A separate eval_documents table plus a copy of the check logic

## Criteria

- isolation from search, knowledge and prompts
- risk of a forgotten reader leaking eval data
- reuse of the S06-R-03 check unchanged
- migration cost

## Decision

In-memory Document objects passed to check_claims through an optional `documents` argument

## Reasons

Nothing is written, so no current or future reader of `documents` can leak the references. The goal forbids live rows unless every reader excludes them, and that is easy to break later. The grading uses the same _excerpt/_support code that live reports use.

Evidence:

- src/agent_company/research.py check_claims (s.get(Document, id) or lookup by url) (FACT)
- goal item 3: no live documents without an eval scope every reader excludes (FACT)
- A scope column needs a filter in every reader of documents (INFERENCE)

## Consequences

Assumptions:

- _excerpt only reads title, text, fetched_at and created_at from the Document

Limitations:

- Benchmark material is not exercised through research.search ranking. The benchmark measures report writing and grounding, not retrieval.

Confidence: 0.80 (Based on reading check_claims and _excerpt. Not yet confirmed by tests.)
