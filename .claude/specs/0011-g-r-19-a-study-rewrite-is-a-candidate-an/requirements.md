# Requirements: G-R-19: a study rewrite is a candidate an administrator applies after seeing the diff

Owner's decision (2026-11-06): option (a) of G-R-19 in docs/requirements/guides-and-other-sources.md. The study rotation (agentdefs.study, StudyThread, POST /api/learning/study, `gm study`) must no longer write an agent's definition directly. Today study() calls learn_into() for "Working method" and "Knowledge notes" and commits at once.

Build:
1. db.py: a table `definition_candidates` (id, agent_id, sections JSON {"Working method": ..., "Knowledge notes": ...}, base_version = the agent's definition_version when made, model, reason, status pending|applied|discarded|superseded, created_at, decided_by, decided_at). _add_missing_columns covers old databases.
2. agentdefs.py: study() stores a pending candidate instead of calling learn_into; a newer candidate for the same agent supersedes the older pending one. The definition file, its git history, definition_version and system_prompt() stay unchanged until a person applies it. New functions: candidates(agent_id=None, status=None), candidate(id) with a unified diff of the current definition file against the file the candidate would produce (always against the current file, and note when base_version is older than the current version), apply_candidate(id, actor) (writes the sections through learn_into-style rendering, one commit naming the candidate and the actor, status applied), discard_candidate(id, actor, note). Applying or discarding a non-pending candidate raises ValueError. Update the module docstring.
3. api.py: GET /api/agents/{id}/definition/candidates, GET /api/definition-candidates/{cid} (with diff), POST /api/definition-candidates/{cid}/apply and /discard: allowed("manage_agents"), each calls _audit. POST /api/learning/study returns the candidate id.
4. A notification to administrators when a candidate is created (governance notify, inside the same session rules as CLAUDE.md says).
5. static/agents.js: the agent card's Definition tab lists a pending candidate with its diff (added/removed lines coloured) and Apply / Discard buttons for admins; the "Study now" toast says a candidate is ready for review instead of "Rewrote N lines".
6. gm study prints the candidate.
7. Tests (new tests/test_definition_candidates.py, offline): study makes a candidate and leaves file, version and system_prompt unchanged; the diff shows the new working method; apply changes file + version + adds a git commit; discard; a second study supersedes the first pending one; applying twice raises; the API refuses a user without manage_agents and audits apply/discard. Adjust existing tests that expected study to rewrite directly (tests/test_company_config.py, tests/test_knowledge.py) so they apply the candidate.
8. Docs: G-R-19 status done (with what was built and the tests) in docs/requirements/guides-and-other-sources.md, its row in the table there, docs/requirements/README.md, docs/feature-gap.md line for G-R-19, CLAUDE.md agentdefs description and the study_interval setting text in prefs.py. Option (b) (smoke suite before applying) is out of scope.

## Quality commands
- `uv run pytest -q -p no:cacheprovider`
