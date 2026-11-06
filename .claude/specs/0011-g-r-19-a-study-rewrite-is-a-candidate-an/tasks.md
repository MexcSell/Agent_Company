# Tasks (iteration 3)



## 1. Verify the committed G-R-19 backend and tests; fix only what fails
- key: `backend_verify` · role: developer · writes: yes

The definition-candidate backend is already committed on this branch (src/agent_company/db.py definition_candidates, agentdefs.py candidates/candidate/apply_candidate/discard_candidate and study(), api.py endpoints, gm.py study, tests/test_definition_candidates.py, and the adjusted tests in tests/test_company_config.py and tests/test_knowledge.py). Do NOT rewrite it. Steps: 1) Run `uv run pytest -q tests/test_definition_candidates.py tests/test_company_config.py tests/test_knowledge.py`. 2) Read agentdefs.py's study/candidate code once and confirm these points. study() never calls learn_into and never commits. A newer candidate supersedes the older pending one. candidate(id) returns a unified diff against the CURRENT file and notes when base_version is older than the current version. apply_candidate makes one commit naming the candidate id and the actor, and bumps definition_version. Applying or discarding a non-pending candidate raises ValueError. The notification to admins is created through the open session (notify(..., s=s)). The API checks allowed('manage_agents') and calls _audit on apply and discard. POST /api/learning/study returns candidate_id. `gm study` prints the candidate. _add_missing_columns covers the table. 3) Fix only the defects you find, with minimal edits, and add a test for each fix. Do not touch static/ or docs/. Report each point as OK or FIXED, with evidence (test output lines).

Acceptance: {"type": "command", "run": "uv run pytest -q tests/test_definition_candidates.py tests/test_company_config.py tests/test_knowledge.py", "requirement_keys": ["G-R-19"], "stage": "unit"}; {"type": "command", "run": "grep -q 'def apply_candidate' src/agent_company/agentdefs.py && grep -q 'def discard_candidate' src/agent_company/agentdefs.py && grep -q 'definition_candidates' src/agent_company/db.py", "stage": "static"}

## 2. Agent card: confirm the candidate box shows a coloured diff with admin-only Apply/Discard
- key: `ui_verify` · role: developer · writes: yes · after: backend_verify

static/agents.js already has a definition-candidate box (around lines 139-170) and the 'Study now' toast that names the candidate. Do NOT rewrite it. Only these checks, with small edits where one fails. a) Diff lines that start with '+' or '-' (but not '+++' or '---') get CSS classes that colour them (added green, removed red), and the classes exist in static/style.css. b) Apply and Discard render only for administrators, using the same admin check the rest of agents.js uses. c) The box sits in the Definition tab and shows the base_version note when the API returns one. d) The Study toast does not say 'Rewrote'. Run `node --check src/agent_company/static/agents.js`. Edit only agents.js and style.css. Keep your answer short: a list of a-d, each OK or FIXED.

Acceptance: {"type": "command", "run": "node --check src/agent_company/static/agents.js", "stage": "static"}; {"type": "command", "run": "grep -q 'definition-candidates' src/agent_company/static/agents.js && ! grep -q 'Rewrote' src/agent_company/static/agents.js", "requirement_keys": ["G-R-19"], "stage": "static"}

## 3. Docs: confirm that G-R-19 is marked done everywhere it is required
- key: `docs_verify` · role: developer · writes: yes · after: backend_verify

The docs lines are mostly written already. Check each of these and edit only what is missing or wrong. 1) docs/requirements/guides-and-other-sources.md, section G-R-19: the status says done (option a) and names what was built (definition_candidates, candidates/candidate/apply_candidate/discard_candidate, the API routes, the notification, the agent card, gm study) and the test file tests/test_definition_candidates.py; option (b) is noted as out of scope. 2) The G-R-19 row of the table in that same file. 3) docs/requirements/README.md. 4) docs/feature-gap.md. 5) The CLAUDE.md agentdefs description mentions G-R-19 definition_candidates and apply_candidate. 6) The study_interval setting text in src/agent_company/prefs.py says study makes a candidate for review. Do not change code. Answer as a checklist 1-6, each OK or FIXED.

Acceptance: {"type": "command", "run": "grep -q 'definition_candidates' CLAUDE.md && grep -q 'G-R-19' docs/feature-gap.md && grep -q 'G-R-19' docs/requirements/README.md && grep -q 'test_definition_candidates' docs/requirements/guides-and-other-sources.md", "requirement_keys": ["G-R-19"], "stage": "static"}; {"type": "command", "run": "uv run python -c \"import agent_company.prefs\"", "stage": "static"}

## 4. QA review of the G-R-19 definition candidates against the spec
- key: `qa_review` · role: qa · writes: no · after: backend_verify, ui_verify, docs_verify

Read-only review of the goal's 8 points against the code on this branch (agentdefs.py, api.py, db.py, gm.py, static/agents.js, tests/test_definition_candidates.py, docs). Trace the data flow of three cases. Normal: study → pending candidate → the file, version and system_prompt are unchanged → candidate(id) diff → apply → one commit, version +1, status applied. Edge: a second study supersedes the first pending candidate, and a candidate whose base_version is older than the current version gets the note in its diff. Error: applying or discarding a non-pending candidate raises ValueError (HTTP 4xx at the API), and a user without manage_agents is refused. Also check that the notification uses the open session and that apply/discard are audited. Do not read files beyond those listed. List the problems as FINDING lines, then an OUTCOME line, and end with 'VERDICT: PASS' or 'VERDICT: FAIL'.

Acceptance: {"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}; {"type": "contains", "value": "OUTCOME"}; {"type": "min_length", "value": 300}
