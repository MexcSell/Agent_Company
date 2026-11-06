# ADR 0001: language-source

Status: Accepted
Date: 2026-11-06
Decision record: #1 · goal #3 (S06-FR-04: research requests by publication date range and language)
Category: technical

## Context

How is a document's language determined?

## Options

- Declared metadata only (HTTP Content-Language, html lang, meta http-equiv, feed language/xml:lang)
- Statistical language detection from the text
- Declared metadata with detection as a fallback

## Criteria

- the goal's explicit requirement
- no new dependency
- explainable provenance
- offline tests

## Decision

Declared metadata only; empty when unknown, and unknown documents are kept but labelled

## Reasons

The goal says 'Never guessed from the text'. Declared tags have a clear provenance and need no new dependency. Keeping unknown-language documents labelled avoids silently dropping material.

Evidence:

- goal item 1 (FACT)
- src/agent_company/research.py:206,365,476 (where published_at/license are already read) (FACT)

## Consequences

Assumptions:

- Most crawled pages and feeds declare a language

Limitations:

- Pages with wrong or missing lang attributes are filtered as declared or kept as unknown

Confidence: 0.85 (Required by the goal; coverage of declared tags is not measured)
