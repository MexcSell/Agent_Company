# Review (iteration 3)

## QA review of the G-R-19 definition candidates against the spec (qa, done)

## Logic verification

Normal: `study()` collects lessons and documents, then `_propose()` stores a pending candidate. With an existing definition, it leaves the file, history, version and prompt unchanged. `candidates()` filters records; `candidate()` renders proposed sections over the current file and produces a unified diff. `apply_candidate()` checks pending status, calls `_write()` once, increments the version and marks the candidate applied. This addresses the root cause by deferring the rewrite until approval.

Edge: `_propose()` supersedes older pending candidates. `candidate()` compares base and current versions and supplies `stale_note`. However, missing-definition paths still write before approval.

Error: apply/discard reject non-pending candidates with `ValueError`; their API handlers translate this to HTTP 409. All four candidate routes require `manage_agents`. Apply/discard call `_audit`; candidate reads do not.

UI/CLI: the Definition tab loads pending candidates, escapes diff content, assigns added/removed line classes and offers Apply/Discard to administrators. Study reports a candidate ready for review. `gm study` prints the candidate and diff. Independently executed `node --check src/agent_company/static/agents.js`: exit 0.

Checklist: schema and migration coverage are present; interfaces and ordinary error handling match the contract; notification and event logging reuse the open session. No new dependency is apparent. Documentation covers all requested locations. Supplied backend evidence reports 45 targeted tests passing; I inspected the candidate tests but did not rerun them. Their missing-file and GET-audit coverage is insufficient.

FINDING: src/agent_company/api.py:3216-3222 | Both candidate GET endpoints omit `_audit`, violating the explicit requirement that each new endpoint audits access | Capture the authorized user, audit successful list/detail reads, and test both audit records.

FINDING: src/agent_company/agentdefs.py:249, src/agent_company/agentdefs.py:315-316 | With a missing definition file, study calls `system_prompt()` → `sync()`, and candidate preview explicitly calls `sync()`; both can create a file, increment its version and commit before administrator approval | Use side-effect-free rendering for missing-file study and preview paths; add tests asserting no file, version or history changes before apply.

NEXT: Fix both gaps and add the missing regression tests; retain the passing JavaScript syntax check.

OUTCOME: REVISION_REQUIRED
VERDICT: FAIL
