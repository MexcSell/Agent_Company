# Tasks (iteration 1)



## 1. Specify the agent marketplace: manifest, untrusted fetch, permission ceiling, candidate flow, data model, API/CLI/UI
- key: `spec` · role: architect · writes: no

Read src/agent_company/recruitment.py (open_request ~220, approve ~381, reject ~419, qualification_suite ~442, start_qualification ~456, qualify ~473, _finish ~504, _activate ~548, _retire_candidate ~560), agentdefs.py (render, sync, write_text, _commit; the definition file is a git repo, so rollback is through its versions), catalog.py (the `source` field), evaluation.py, sandbox.py, db.py, gm.py and the Agents page in static/agents.js. Then write a technical specification as your answer. It must cover:
1) MANIFEST: an agent package manifest (`agent-package.json`, schema version 1) with role, name, instructions, skills, tools, models, dependencies, permissions (requested), version, author, source (URL or upload name plus commit sha or sha256), licence (SPDX id or 'unknown'). Give validation rules and limits (field lengths, list sizes, a role must be an existing registry role, unknown keys are kept but ignored).
2) CONVERSION: build a manifest from (a) a native agent-package.json, (b) a Claude Code subagent .md with frontmatter, (c) a CrewAI agents.yaml (role/goal/backstory), (d) a skill pack (SKILL.md). Conversion only parses text with yaml.safe_load, json and markdown. It never imports, executes or installs anything from the package.
3) UNTRUSTED FETCH: each import gets its own new empty directory data/marketplace/<import_id>/src. For a repository URL, allow only https:// (plus file:// when a test setting enables it) and use `git clone --depth 1 --no-recurse-submodules -c core.hooksPath=/dev/null -c protocol.file.allow=...` with a timeout and a size cap. For an uploaded .zip or .tar.gz: reject absolute paths, '..' (zip-slip), symlinks, device files, too many files and too many bytes. Nothing from the package runs on the host. Python reading the package runs with no cwd inside it.
4) PERMISSION CEILING: the candidate's permissions and autonomy are the intersection of the administrator-granted set and a fixed import default (lowest autonomy, no MCP, no Bash beyond the role default, writes only when the role writes). Requested permissions are only displayed. Instructions text is stored as data in the definition's instructions section and never parsed for permissions, tools or models. A model outside the router catalog is ignored.
5) CANDIDATE FLOW: an import creates a Recruitment with source='marketplace' and a disabled candidate agent, then runs the role's eval suite through the existing qualify path (L3 sandbox via contracts.run_command/sandbox) plus a security screen: secret patterns, prompt-injection phrases that claim permissions, and requested permissions above the ceiling. Results (per-dimension, S09-R-05) are stored on the import record and the recruitment. Only admin approve enables the agent. Reject retires the candidate and deletes data/marketplace/<id>/src.
6) PROVENANCE: the agent definition file gets a `## Provenance` section (source, sha, author, licence, manifest version, eval summary, import id), committed via agentdefs, so rollback is a revert to an earlier agentdefs version.
7) DATA MODEL in db.py: a table `agent_imports` (id, kind url|upload, source, source_sha, manifest JSON, licence, author, version, status fetched|converted|qualifying|qualified|failed|approved|rejected, screen JSON, eval JSON, recruitment_id, agent_id, created_by, created_at, error), plus the columns needed on recruitments or agents, migrated by _add_missing_columns.
8) API: endpoints under /api/marketplace (import url, upload, list, get, approve with grant, reject). All are admin-only and call _audit.
9) CLI: `python -m agent_company marketplace import <url|path> | list | show <id> | approve <id> | reject <id>`.
10) UI: a 'Marketplace' tab on the Agents page in static/agents.js.
11) THREATS: new rows for docs/threat-model.md (malicious repo or archive, zip-slip, git hooks and submodules, prompt injection widening permissions, licence, SSRF via URL).
12) TESTS: name the tests and the local fixture package, tests/fixtures/marketplace/sample-agent/, that run offline.
Keep the spec concrete, with function signatures in a new module src/agent_company/marketplace.py.

Acceptance: {"type": "contains", "value": "agent_imports"}; {"type": "contains", "value": "marketplace.py"}; {"type": "regex", "pattern": "(?i)zip-?slip"}; {"type": "regex", "pattern": "(?i)permission ceiling|intersection"}; {"type": "regex", "pattern": "(?i)provenance"}; {"type": "min_length", "value": 3000}

## 2. marketplace.py: untrusted fetch, conversion to manifest, permission ceiling, candidate qualification, provenance; db tables; offline tests
- key: `backend` · role: developer · writes: yes · after: spec

Implement the backend from the spec task's answer.
(1) Add the agent_imports table, and any recruitment or agent columns, to src/agent_company/db.py so that _add_missing_columns migrates them.
(2) Create src/agent_company/marketplace.py with: fetch_url(url, actor), fetch_upload(path_or_bytes, filename, actor), convert(src_dir) -> manifest dict, validate_manifest, permission_ceiling(role, requested, granted), screen(manifest, src_dir), create_candidate(import_id), approve(import_id, actor, grant), reject(import_id, actor, reason) and list/get.
Requirements for the fetch: own empty directory data/marketplace/<id>/src; scheme allow-list; git clone --depth 1 with core.hooksPath=/dev/null, no submodules, timeout and size cap; archive extraction rejects absolute, '..', symlink and special members and enforces count and byte limits. Nothing from the package is executed or installed on the host.
Requirements for the candidate: reuse recruitment.py, so the candidate is a disabled agent with a Recruitment record and qualify() runs its role's eval suite through the existing L3-sandboxed eval path. Store the eval and screen results on agent_imports and the recruitment. approve() goes through recruitment.approve and enables the agent only with an administrator actor. reject() goes through recruitment.reject/_retire_candidate and removes the fetched directory.
Requirement for permissions: the candidate's permissions and autonomy never exceed the ceiling, whatever the manifest or instructions request.
Requirement for provenance: write a '## Provenance' section (source, sha, author, licence, version, eval summary) into the agent's definition via agentdefs so it is committed as a new version.
(3) Tests in tests/test_marketplace.py. They run offline and use a local fixture package tests/fixtures/marketplace/sample-agent/ (agent-package.json + instructions whose text tries to claim Bash/MCP/admin permissions) and a local git repo built from it in tmp (file:// URL enabled by a test setting). Cover: url import → manifest + disabled candidate, and no file from the package was executed (a fixture script that would write a marker file never runs); upload of a .zip with a '../' member and with a symlink is refused; injected permission requests are not granted; the qualification result is stored (stub the eval run the way tests/test_recruitment.py does); approve enables the agent and the provenance section is in the definition; reject retires it and deletes the src directory; agentdefs history shows the provenance version.
Do not touch api.py, __main__.py or static/ (the next task owns them).

Acceptance: {"type": "file_exists", "path": "src/agent_company/marketplace.py"}; {"type": "file_exists", "path": "tests/test_marketplace.py"}; {"type": "command", "run": "uv run pytest -q tests/test_marketplace.py", "requirement_keys": ["REQ-002"], "stage": "unit"}; {"type": "command", "run": "uv run pytest -q tests/test_recruitment.py", "requirement_keys": ["S03-R-04"], "stage": "unit"}; {"type": "command", "run": "grep -q 'agent_imports' src/agent_company/db.py", "stage": "static"}

## 3. Admin API with _audit, CLI `marketplace`, Agents-page Marketplace tab, threat model and docs
- key: `surface` · role: developer · writes: yes · after: backend

Expose src/agent_company/marketplace.py.
(1) api.py: admin-only endpoints under /api/marketplace: POST import (url), POST upload (multipart, saved to its own new directory first), GET list, GET {id}, POST {id}/approve (with a granted-permissions body that is clamped by the ceiling), POST {id}/reject. Every mutating call uses _audit(...). Non-admins get 403.
(2) __main__.py: `marketplace import <url|path> | list | show <id> | approve <id> [--grant ...] | reject <id> [--reason]`.
(3) static/agents.js: a 'Marketplace' tab on the Agents page. It lists imports with status, licence, source and sha, a manifest view, the requested vs granted permissions, screen findings and eval results, plus import (URL or file) and Approve/Reject buttons. Follow the existing plain-JS style and `iconize`.
(4) docs/threat-model.md: add the marketplace integration as threats → controls → tests (malicious repo or archive, zip-slip, git hooks and submodules, SSRF via URL, prompt injection widening permissions, licence), each pointing at tests/test_marketplace.py.
(5) CLAUDE.md layout: one line for marketplace.py. Mark REQ-002's status in docs/requirements if it is listed there.
(6) Add API tests to tests/test_marketplace.py: admin import/approve/reject write audit records, and a non-admin gets 403.

Acceptance: {"type": "command", "run": "uv run pytest -q tests/test_marketplace.py", "requirement_keys": ["REQ-002"], "stage": "integration"}; {"type": "command", "run": "grep -q '/api/marketplace' src/agent_company/api.py && grep -A40 '/api/marketplace' src/agent_company/api.py | grep -q '_audit('", "stage": "static"}; {"type": "command", "run": "uv run python -m agent_company marketplace --help", "stage": "static"}; {"type": "command", "run": "grep -qi 'marketplace' docs/threat-model.md && grep -qi 'marketplace' src/agent_company/static/agents.js", "stage": "static"}

## 4. QA review of the agent marketplace against REQ-002
- key: `qa` · role: qa · writes: no · after: surface

Review src/agent_company/marketplace.py, the db.py changes, the recruitment and agentdefs integration, the api.py endpoints, the CLI, the static/agents.js tab, docs/threat-model.md and tests/test_marketplace.py against the five REQ-002 acceptance criteria:
(a) url or upload import → manifest + candidate, nothing runs on the host;
(b) the role's eval suite runs in the L3 sandbox and the results are on the record;
(c) only an administrator's approval enables the agent, and rejection removes it;
(d) provenance, licence and eval results are on the agent definition and rollback works via agentdefs versions;
(e) instructions cannot widen permissions.
Trace the data flow of a normal case (url import → approve), an edge case (zip with a '../' member or a symlink; manifest requesting Bash/MCP/high autonomy) and an error case (unreachable URL or invalid manifest → failed status, no agent left enabled). Check: git hook and submodule handling, the scheme allow-list (no ssh:// or http:// to internal hosts unless the spec allows it), that the archive is never extracted into a shared directory, _audit on every admin action, non-admin 403, and the DB session discipline (no second connection inside session()). Write one FINDING line per issue with file:line and severity. End with an OUTCOME paragraph and either 'VERDICT: PASS' or 'VERDICT: FAIL'.

Acceptance: {"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}; {"type": "contains", "value": "OUTCOME"}; {"type": "min_length", "value": 800}

## 5. Security review of 'marketplace.py: untrusted fetch, conversion to manifest, permission ceiling, candidate qualification, provenance; db tables; offline tests'
- key: `backend-security` · role: security · writes: no · after: backend

Review the change made by task 'backend' for security: secrets, injection, unsafe file and network access, authentication and permission changes. Verify the logic first.

Acceptance: {"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}
