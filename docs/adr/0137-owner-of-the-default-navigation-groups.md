# ADR 0137: Owner of the default navigation groups

Status: Accepted
Date: 2026-11-09
Decision record: #137 · goal #391 (UI redesign P0-1 (third run): AICO design system and application shell)
Category: technical

## Context

Python (migrating saved layouts) and JS (drawing the sidebar, placing pages registered later) both need the default group table. Which one owns it?

## Options

- prefs.py owns DEFAULT_NAV_GROUPS and VIEW_GROUP; GET always returns the normalised groups form; app.js keeps a mirror used only when the API fails, and a test checks the mirror matches prefs.py
- app.js owns the table and the server stores the value unchecked

## Criteria

- one source of truth
- every client migrates an old saved layout the same way
- migration testable offline with pytest
- a stale Settings tab does not break

## Decision

prefs.py owns DEFAULT_NAV_GROUPS; app.js NAV_DEFAULT is an offline mirror, kept equal by tests/test_nav_layout.py::test_app_js_mirror_equals_prefs

## Reasons

This repeats ADR 0128 from #337, which never merged. If the server does the migration, every browser sees the same order and the migration can be tested with pytest. The ADR is re-issued here so adr.py numbers it on this branch; the 0128 file from #337 is not copied.

Evidence:

- git diff main...agents/goal-337 -- docs/adr/0128-owner-of-the-default-navigation-groups.md (FACT)
- git diff main...agents/goal-337 -- src/agent_company/prefs.py (DEFAULT_NAV_GROUPS, normalise_nav) (FACT)
- git diff main...agents/goal-337 -- tests/test_nav_layout.py (test_app_js_mirror_equals_prefs) (FACT)

## Consequences

Assumptions:

- the iteration-2 prefs.py logic merges with main's prefs.py changes without changing behaviour

Limitations:

- the JS mirror can drift; only the mirror test catches it

Confidence: 0.80 (The same choice passed QA and security review in #337 iteration 2)
