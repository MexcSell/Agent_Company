# ADR 0083: browser-dns-pin-scope

Status: Accepted
Date: 2026-11-07
Decision record: #83 · goal #75 (Pin the browser's DNS answers to the checked address)
Category: technical

## Context

How should this goal pin the browser's DNS when the repository has no browser tool yet?

## Options

- Deliver a tested netpin helper that builds the Chromium resolver arguments; the future browser executor must use it
- Build the browser tool and the pin together in this goal
- Run the browser behind a local pinning proxy instead of resolver rules

## Criteria

- cost per task against measured budgets
- testability offline
- closeness of the pin to the check

## Decision

Deliver a tested netpin helper that builds the Chromium resolver arguments; the future browser executor must use it

## Reasons

Goal #25 failed three times because one task combined the browser tool and the DNS redesign and went over its budget (K236, K237). A small helper next to resolve_public and curl_resolve_entry follows the pattern already used for git (http.curloptResolve). It can be tested offline by patching getaddrinfo.

Evidence:

- src/agent_company/netpin.py (resolve_public, curl_resolve_entry) (FACT)
- src/agent_company/marketplace.py:137 check_url (FACT)
- no browser/Chromium launcher in src/agent_company (grep) (FACT)
- K236, K237 (MEASURED)
- Chromium evaluates --host-resolver-rules in order, first match wins; '~NOTFOUND' fails a lookup (INFERENCE)

## Consequences

Assumptions:

- A later browser tool will launch Chromium with the args this helper returns

Limitations:

- The research notes found no official Chromium source for the rule syntax; QA must judge it from the tests and known behaviour
- It does not protect a browser that is launched without these args

Confidence: 0.70 (The pattern matches the existing git pin. Chromium behaviour is not verified against a live browser here.)
