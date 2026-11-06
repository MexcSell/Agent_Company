# ADR 0002: language-source

Status: Accepted
Date: 2026-11-06
Decision record: #2 · goal #3 (S06-FR-04: research requests by publication date range and language)
Category: technical
Supersedes: [0001-language-source](0001-language-source.md)

## Context

Where does Document.language come from?

## Options

- Declared metadata only (HTTP header, html lang, meta http-equiv, feed language / xml:lang); empty when unknown
- Declared metadata, else detect from the text with a language-ID library

## Criteria

- What the goal requires
- Whether the result can be checked
- New dependencies
- Behaviour in offline tests

## Decision

Declared metadata only; an unknown language is kept and labelled

## Reasons

The goal says 'Never guessed from the text'. Declared tags can be traced to the page or feed that carried them. Detecting from the text would add a dependency and give confident wrong answers on short or mixed pages.

Evidence:

- goal text item 1 (FACT)
- src/agent_company/research.py:64-97 (license/meta parsing already in the HTML extractor) (FACT)
- escalation decision #1 'language-source' (FACT)

## Consequences

Assumptions:

- Most pages and feeds declare a language

Limitations:

- Pages with no declared language cannot be filtered by language; they are only labelled

Confidence: 0.90 (The goal states this requirement outright)
