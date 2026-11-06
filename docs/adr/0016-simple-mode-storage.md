# ADR 0016: simple_mode_storage

Status: Accepted
Date: 2026-11-06
Decision record: #16 · goal #21 (REQ-003 + REQ-004: Simple mode mission form and the mission report)
Category: technical

## Context

Where is the per-user Simple/Advanced mode stored, and what do existing users get?

## Options

- New column User.ui_mode ('' = advanced; registration sets 'simple')
- Shared prefs.set_ui key (admin-only, shared by every user)
- Browser localStorage only

## Criteria

- per user
- survives across devices
- existing users keep today's dashboard
- no admin needed to switch

## Decision

New column User.ui_mode, added by _add_missing_columns. A new registration writes 'simple'. Existing rows stay '' and are treated as 'advanced'. The user switches it with PUT /api/me/ui_mode.

## Reasons

/api/ui/{key} needs an admin and is shared by every user (api.py:3285-3292), so it cannot store a per-user choice. localStorage does not follow the user to another device. With '' meaning advanced, existing users keep today's dashboard, as REQ-003 requires.

Evidence:

- src/agent_company/api.py:3285 (FACT)
- src/agent_company/db.py:1032 (User has no UI field) (FACT)

## Consequences

Assumptions:

- 'default for a new user' means accounts registered after this change

Limitations:

- Existing users have to switch to Simple mode themselves

Confidence: 0.80 (Follows directly from the current code)
