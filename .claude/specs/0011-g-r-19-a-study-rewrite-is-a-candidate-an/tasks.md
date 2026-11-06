# Tasks (iteration 1)



## 1. Definition candidates: table, agentdefs API, REST endpoints, notification, gm study, tests
- key: `backend` · role: developer · writes: yes

Implement G-R-19 option (a). The study rotation must no longer write an agent's definition directly. Today agentdefs.study() (src/agent_company/agentdefs.py:191-240) calls learn_into() for 'Working method' and, when there are documents, for 'Knowledge notes'. Each call commits at once.

1. db.py: add the model DefinitionCandidate, table `definition_candidates`, with these columns: id; agent_id (FK agents); sections JSON, e.g. {"Working method": str, "Knowledge notes": str}, holding only the sections the study produced; base_version int, the agent's definition_version at creation; model str; reason str; status str, default 'pending', one of pending|applied|discarded|superseded; created_at; decided_by str nullable; decided_at nullable; note str nullable. The table must be created on existing databases (create_all plus _add_missing_columns for columns).

2. agentdefs.py:
- Factor the write half of learn_into into a helper `_write_sections(agent_id, {section: text}, message)`. It replaces several sections, renders once with render(), bumps definition_version once and makes one _commit. learn_into keeps its behaviour and uses this helper.
- study(): where it now calls learn_into, build the sections dict and create a pending DefinitionCandidate instead. Use base_version = current definition_version. Use the existing reason text ('studied N lesson(s) and M document(s) with <model>'). In the same session, mark any older pending candidate of this agent 'superseded' (decided_by='system', decided_at=now). Notify administrators about the new candidate with governance's notify, passing s=s (CLAUDE.md: one SQLite writer, never a second connection), and call log_event(..., s=s). Return {studied, agent_id, changed: True, candidate_id, model, lines}. The no-bullets branch stays as it is. The definition file, its git history, definition_version and system_prompt() must stay unchanged.
- candidates(agent_id=None, status=None) -> list[dict], newest first.
- candidate(cid) -> dict with every field, plus: agent_name; current_version; `stale` = base_version < current_version, with a human note when stale; and `diff`, a difflib.unified_diff of the current definition file against the text the candidate would produce (render(a, sections(current) updated by candidate.sections)), always taken against the current file. Raise KeyError if the candidate is missing.
- apply_candidate(cid, actor): raise ValueError unless the status is pending. Write the sections through _write_sections with one commit message naming the candidate id and the actor, e.g. '<name> v<N>: definition candidate #<id> applied by <actor>'. Then set status 'applied', decided_by and decided_at. Return the dict.
- discard_candidate(cid, actor, note=''): raise ValueError unless pending. Set status 'discarded', decided_by, decided_at and note.
- Update the module docstring: study makes a candidate that a person applies after seeing the diff (G-R-19).

3. api.py: GET /api/agents/{id}/definition/candidates (optional ?status=); GET /api/definition-candidates/{cid} (includes the diff); POST /api/definition-candidates/{cid}/apply; POST /api/definition-candidates/{cid}/discard (JSON body {note}). Every one of these checks allowed('manage_agents') the way the existing agent-management endpoints do, and apply and discard call _audit(...). A missing candidate returns 404. ValueError returns 409, or 400 if that matches the neighbouring code better. POST /api/learning/study passes candidate_id through.

4. gm.py / `python -m agent_company gm study`: print the candidate's id, agent, model and status, plus its diff (or the no-change detail).

5. prefs.py: in the study_interval setting text, say the visit produces a definition candidate that an administrator applies.

6. Tests: create tests/test_definition_candidates.py. It must run offline and use the per-test DATA_DIR fixtures the other tests use. Cover these cases:
(a) study creates a pending candidate; file text, definition_version, git log length and system_prompt() are unchanged.
(b) candidate(id)['diff'] contains '+' lines of the new working method.
(c) apply_candidate changes the file, raises definition_version by exactly 1 and adds one git commit whose message names the candidate and the actor.
(d) discard_candidate sets status discarded and leaves the file unchanged.
(e) a second study supersedes the first pending candidate.
(f) applying twice, and applying after discard, raises ValueError.
(g) via the FastAPI TestClient: a user without manage_agents gets 403 on list, detail, apply and discard; an admin's apply and discard each write an audit record.
(h) an administrator notification exists after study.
Also adjust tests/test_company_config.py (~line 67) and tests/test_knowledge.py (~line 425): where they expected study to rewrite directly, they must apply the returned candidate before asserting on the file. The confidential-lessons assertion in test_knowledge must still hold.

Run `uv run pytest -q` and report the result.

Acceptance: {"type": "file_exists", "path": "tests/test_definition_candidates.py"}; {"type": "command", "run": "uv run pytest -q tests/test_definition_candidates.py", "requirement_keys": ["G-R-19"], "stage": "unit"}; {"type": "command", "run": "uv run pytest -q tests/test_company_config.py tests/test_knowledge.py", "requirement_keys": ["G-R-19"], "stage": "unit"}; {"type": "command", "run": "uv run pytest -q", "stage": "integration"}; {"type": "command", "run": "grep -q 'definition_candidates' src/agent_company/db.py && grep -q 'def apply_candidate' src/agent_company/agentdefs.py && grep -q 'def discard_candidate' src/agent_company/agentdefs.py && grep -q '/api/definition-candidates/' src/agent_company/api.py", "stage": "static"}

## 2. Agent card shows a pending candidate with its diff and Apply/Discard; docs mark G-R-19 done
- key: `ui_docs` · role: developer · writes: yes · after: backend

This task uses the endpoints built by the backend task: GET /api/agents/{id}/definition/candidates, GET /api/definition-candidates/{cid} (field `diff`, plus `stale`), and POST .../apply and .../discard.

1. static/agents.js: in the agent card's Definition tab, fetch the candidates with status=pending. If one exists, show a box above the definition with its model, reason, created_at and a stale note when present. Render the diff in a <pre> with each line in its own span. Colour added lines ('+' but not '+++') green and removed lines ('-' but not '---') red, using existing CSS variables/classes where possible, and escape every line with esc(). For admins only (use the same admin check the card already uses for the Study button), add Apply and Discard buttons. Discard asks for an optional note. Both POST, toast the result and reload with agentModal(id, 'definition'). The 'Study now' handler (agents.js:162) must say e.g. `Definition candidate #${r.candidate_id} is ready for review` instead of 'Rewrote N line(s)'. Change the similar toast in static/admin.js:119 the same way.

2. Docs:
- docs/requirements/guides-and-other-sources.md: set G-R-19 to done. Say option (a) was decided by the owner on 2026-11-06, and list what was built: the table, the agentdefs functions, the endpoints, the notification, the UI, gm study, and the tests in tests/test_definition_candidates.py. Note that option (b) is not built. Update G-R-19's row in that file's table.
- docs/requirements/README.md: update G-R-19's status.
- docs/feature-gap.md: update the G-R-19 line.
- CLAUDE.md: in the agentdefs.py description, change 'the loop runs agents from it, learning and the study rotation rewrite it' so it says the study rotation makes a definition candidate that an administrator applies or discards after seeing the diff (G-R-19, `definition_candidates`).

Check the JS with `node --check src/agent_company/static/agents.js` and the same for admin.js.

Acceptance: {"type": "command", "run": "node --check src/agent_company/static/agents.js && node --check src/agent_company/static/admin.js", "stage": "static"}; {"type": "command", "run": "grep -q 'definition-candidates' src/agent_company/static/agents.js && ! grep -q 'Rewrote' src/agent_company/static/agents.js", "requirement_keys": ["G-R-19"], "stage": "static"}; {"type": "command", "run": "grep -n 'G-R-19' docs/requirements/guides-and-other-sources.md | grep -qi 'done' && grep -n 'G-R-19' docs/requirements/README.md | grep -qi 'done' && grep -q 'G-R-19' docs/feature-gap.md && grep -q 'definition_candidates' CLAUDE.md", "stage": "static"}

## 3. QA review of G-R-19 definition candidates against the spec
- key: `review` · role: qa · writes: no · after: backend, ui_docs

Review the branch against the goal spec (points 1-8 of G-R-19 option (a)). Verify the logic by tracing the data flow of three cases:
- Normal: study → pending candidate; file, version and system_prompt unchanged; diff against the current file; apply → one commit naming the candidate and actor, version +1, status applied, audit record.
- Edge: a second study supersedes the first pending one; a stale candidate (base_version older than the current version after a manual edit) still diffs against the current file and is flagged; a study with no documents produces only 'Working method'.
- Error: applying or discarding a non-pending candidate raises ValueError, which maps to a 4xx; a missing id gives 404; a user without manage_agents gets 403.
Also check: the notification and log_event use the open session (s=s), with no second SQLite writer; _add_missing_columns or create_all covers old DBs; JS escapes the diff lines (no XSS from model text); the docs and CLAUDE.md were updated; existing tests were adjusted, not weakened. Run `uv run pytest -q` and report the result. List each problem as a 'FINDING:' line with file:line, then an 'OUTCOME:' line, and end with 'VERDICT: PASS' or 'VERDICT: FAIL'.

Acceptance: {"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}; {"type": "contains", "value": "OUTCOME"}; {"type": "min_length", "value": 400}
