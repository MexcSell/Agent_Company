# Tasks (iteration 1)



## 1. Default `everyone` to False for new MCP servers (DB, API, UI, catalog) with regression tests
- key: `default_false` · role: developer · writes: yes

Reproduce first: in a test, mcp.save_server({...}) without 'everyone' and mcp.add_from_catalog(...) currently give a server with everyone=True, so mcp.claude_config(agent_id) offers it to an agent that has no grant. Fix the cause:
1. src/agent_company/db.py:1270 McpServer.everyone: default=False. Also add server_default=false() (sqlalchemy.false) only if that matches how other Boolean columns in db.py do it; otherwise just default=False. Update the trailing comment.
2. src/agent_company/api.py:457 McpServerBody.everyone: bool = False.
3. src/agent_company/mcp.py: save_server already copies data['everyone'] only when it is given, so a new row takes the column default. Do not change add_from_catalog to grant anyone. Update the module docstring line 15 to say that a new server is open to no agent until an administrator grants agents or ticks 'every agent'.
4. src/agent_company/static/agents.js:374 the new-server form checkbox: checked only when s?.everyone === true (unchecked for a new server, kept as stored when editing).
5. Existing rows: do NOT migrate or change them (decision 'existing_rows'). _add_missing_columns is not involved, because the column already exists.
6. New test file tests/test_mcp_everyone.py (offline, no network: monkeypatch mcp.Session or mcp.test with a fake, as tests/test_mcp_drift.py does with FakeSession), with these tests: (a) save_server without 'everyone' gives everyone False, and claude_config for an agent without a grant does not include it, even when the server is enabled with status ok; (b) add_from_catalog for a catalog key with no required fields (choose one from mcp.CATALOG whose fields are all optional, or pass the values it needs; stub mcp.test) gives everyone False; (c) set_grants(sid, [agent]) offers it to that agent only; (d) save_server with everyone=True or set_grants(..., everyone=True) still opens it to all; (e) McpServerBody().everyone is False; (f) editing an existing everyone=True server through save_server without the key keeps True (existing rows unchanged).
7. Check that tests/test_company_config.py, tests/test_mcp_drift.py and any test that depended on the old default still pass. If one relied on everyone=True implicitly, make it pass everyone=True or grant explicitly. Do not weaken its assertions.
8. docs/threat-model.md 'Elevation of privilege' / 'Data exfiltration' row: add that a new MCP server is granted to no agent by default, and cite tests/test_mcp_everyone.py.
Keep the change minimal and in the style of the surrounding code.

Acceptance: {"type": "file_exists", "path": "tests/test_mcp_everyone.py"}; {"type": "command", "run": "uv run pytest -q tests/test_mcp_everyone.py tests/test_mcp_drift.py tests/test_company_config.py", "requirement_keys": ["G-R-05"], "stage": "unit"}; {"type": "command", "run": "grep -nE 'everyone: Mapped\\[bool\\] = mapped_column\\(Boolean,[^)]*default=False' src/agent_company/db.py", "stage": "static"}; {"type": "command", "run": "grep -nE '^\\s+everyone: bool = False' src/agent_company/api.py", "stage": "static"}; {"type": "command", "run": "node --check src/agent_company/static/agents.js", "stage": "static"}

## 2. Review the least-privilege default for MCP servers
- key: `qa_review` · role: qa · writes: no · after: default_false

Review the diff of default_false against the goal. Verify the logic by following the data flow in three cases. Normal: a catalog server is added through POST /api/mcp/catalog/... → add_from_catalog → save_server → McpServer row; check that claude_config/the .mcp.json written for an ungranted agent leaves it out, and that it appears once set_grants grants the agent. Edge: an existing row with everyone=True is edited without the key and must stay True; an API body that omits everyone gets False; the UI checkbox is unchecked for a new server and keeps the stored value when editing. Error: check that no other creation path still produces everyone=True implicitly. Grep for McpServer(, save_server(, add_from_catalog( and the gm toolbox/CLI. Also check mcp.call's grant check at mcp.py:553. Run uv run pytest -q tests/test_mcp_everyone.py tests/test_mcp_drift.py tests/test_company_config.py tests/test_gateway.py and report the result. List each problem as a 'FINDING:' line, then an 'OUTCOME:' line, and end with 'VERDICT: PASS' or 'VERDICT: FAIL'.

Acceptance: {"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}; {"type": "contains", "value": "OUTCOME"}; {"type": "min_length", "value": 400}
