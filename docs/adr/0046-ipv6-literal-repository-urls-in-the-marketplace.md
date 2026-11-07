# ADR 0046: IPv6-literal repository URLs in the marketplace

Status: Accepted
Date: 2026-11-07
Decision record: #46 · goal #31 (Pin resolved addresses for all outbound fetches)
Category: technical

## Context

Should an https URL whose host is an IPv6 (or IPv4) literal be refused, or allowed without a DNS pin?

## Options

- Allow literals after the public-address check; add no curloptResolve entry because nothing is resolved
- Refuse IPv6-literal URLs with ImportRefused
- Build a bracketed curloptResolve entry for the literal

## Criteria

- closes the TOCTOU
- no unhandled exception
- smallest change
- usability for administrators

## Decision

Allow literals after the public check and add no resolve entry (the developer may choose the bracketed entry instead if that is simpler). Either way, every refusal is an ImportRefused.

## Reasons

With a literal address, curl never asks DNS, so a check-then-fetch race cannot happen. Only the public check is needed. Refusing would also be safe but needlessly limits administrators.

Evidence:

- src/agent_company/marketplace.py:158-166 (FACT)
- .claude/specs/0031-pin-resolved-addresses-for-all-outbound/review.md:74 (FACT)
- curl does not resolve a literal IP host, so CURLOPT_RESOLVE is not consulted for it (INFERENCE)

## Consequences

Assumptions:

- git passes the bracketed URL host to curl unchanged

Limitations:

- Not tested against a real git/curl clone here; the tests mock resolution and check the built command

Confidence: 0.80 (The cause is visible in the code. The curl behaviour for literals is well known but not measured here.)
