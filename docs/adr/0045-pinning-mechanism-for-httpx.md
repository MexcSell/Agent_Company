# ADR 0045: pinning mechanism for httpx

Status: Accepted
Date: 2026-11-06
Decision record: #45 · goal #31 (Pin resolved addresses for all outbound fetches)
Category: technical

## Context

How should research fetches connect to the address that was checked?

## Options

- Custom httpcore network backend whose connect_tcp maps host to the checked IP (URL, Host and SNI unchanged)
- Rewrite the URL to the IP and pass Host header + extensions={'sni_hostname': host}
- Resolve-and-check inside a socket-level hook only, with no pinning

## Criteria

- closes the TOCTOU
- TLS verification on the original name
- keeps existing MockTransport tests
- code size
- dependence on private APIs

## Decision

Custom httpcore network backend behind a `pinned_client` factory

## Reasons

The URL and the TLS server_hostname stay the original name, so certificate checks and existing URL-based tests and mocks are unaffected. The check and the connect use the same address, so DNS cannot change in between. URL rewriting touches every redirect and robots URL and is easy to get wrong.

Evidence:

- src/agent_company/research.py:281-334 (check by getaddrinfo, then c.get(url) resolves again) (FACT)
- src/agent_company/marketplace.py:198-212 (git already pinned with http.curloptResolve) (FACT)
- httpcore ConnectionPool accepts a network_backend; httpx.HTTPTransport wraps it (INFERENCE)

## Consequences

Assumptions:

- The httpx/httpcore versions in uv.lock keep the network_backend interface

Limitations:

- It relies on httpx's internal `_pool` or on building the httpcore pool directly, so an httpx upgrade may need an adjustment. A caller-supplied client is not pinned.

Confidence: 0.75 (The spec task confirms the httpcore interface against the installed versions before implementation.)
