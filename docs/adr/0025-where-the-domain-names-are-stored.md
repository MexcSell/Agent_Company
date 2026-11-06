# ADR 0025: Where the domain names are stored

Status: Accepted
Date: 2026-11-06
Decision record: #25 · goal #26 (REQ-009: own domain name for the dashboard)
Category: technical

## Context

Store the dashboard's domain names as a DB setting (`tls_domains`, Settings -> Connection) or only in .env (TLS_HOSTS)?

## Options

- DB setting tls_domains with TLS_DOMAINS .env fallback
- Extend the existing TLS_HOSTS env only

## Criteria

- REQ-009 asks for Settings -> Connection
- validation before nginx/cert use
- audit trail
- IP access unaffected

## Decision

DB setting tls_domains via prefs (audited by the existing settings endpoints), validated by transport.parse_domains; TLS_HOSTS stays as is for compatibility.

## Reasons

REQ-009 requires the names to be editable in Settings -> Connection. prefs already re-applies nginx for `transport`, and PUT /api/settings is audited. Keeping `_` in server_name preserves IP access.

Evidence:

- src/agent_company/prefs.py:228 transport hook calls transport.apply (FACT)
- src/agent_company/transport.py:166 hosts() reads TLS_HOSTS env only (FACT)
- src/agent_company/transport.py:335 server_name _ (FACT)
- src/agent_company/transport.py:217 _leaf_ok renews when wanted SANs are not a subset (FACT)

## Consequences

Assumptions:

- Only one nginx site listens on public_port, so `_` remains the catch-all for IP access

Limitations:

- A removed name stays in the certificate until the next renewal; this is harmless but not immediate

Confidence: 0.85 (Follows existing patterns in prefs/transport closely.)
