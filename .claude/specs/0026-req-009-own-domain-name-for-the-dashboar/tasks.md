# Tasks (iteration 3)



## 1. Run the REQ-009 checks on the branch and fix only what fails
- key: `verify_fix` · role: developer · writes: yes

The REQ-009 work is already on branch agents/goal-26. Do NOT rewrite it, restructure it or restyle it. Steps: (1) Run `uv run pytest -q tests/test_transport.py tests/test_threats.py tests/test_api.py -x`. (2) Run `node --check src/agent_company/static/admin.js`. (3) If both pass, change nothing except, if it is missing, one test in tests/test_transport.py: it should assert that `nginx_conf('https')` and `nginx_conf('http')` contain `server_name agent-company.internal _;` after `prefs.set_value('tls_domains', 'agent-company.internal')`, and contain `server_name _;` when no domain is set. Use the existing `applied` fixture or monkeypatch so the live nginx is never written (MANAGE_NGINX off; never call `apply` against /etc/nginx). (4) If something fails, make the smallest fix in the file that fails and report the failing output before and after. Stop as soon as the checks pass. Report the exact command output as evidence.

Acceptance: {"type": "command", "run": "uv run pytest -q tests/test_transport.py", "requirement_keys": ["REQ-009"], "stage": "unit"}; {"type": "command", "run": "uv run pytest -q tests/test_threats.py tests/test_api.py", "stage": "integration"}; {"type": "command", "run": "node --check src/agent_company/static/admin.js", "stage": "static"}; {"type": "command", "run": "grep -q 'C:\\\\Windows\\\\System32\\\\drivers\\\\etc\\\\hosts' ReadMe.mdx && grep -q '/etc/hosts' ReadMe.mdx", "requirement_keys": ["REQ-009"], "stage": "static"}

## 2. QA review of REQ-009 against the spec
- key: `qa_review` · role: qa · writes: no · after: verify_fix

Read-only review of the REQ-009 implementation on agents/goal-26 against the acceptance criteria: (a) a domain set in Settings → Connection appears in the certificate's subject alternative names (hosts() → _san → ensure_certs; _leaf_ok renews the certificate when a name is added or removed); (b) it appears in nginx_conf server_name for both https and http modes; (c) IP access keeps working (the `_` catch-all stays in server_name, and the IP addresses stay in the SAN list); (d) validation refuses ';', '{', newlines, wildcards, ports, schemes, IP literals and over-long labels, and a refused value stores nothing and does not reload nginx; (e) admin.js saves with PUT and clears with DELETE on /api/settings/tls_domains, and escapes the value it displays; (f) the ReadMe documents hosts-file and DNS setup on clients and trusting the CA, and does not claim this is automated; (g) tests never touch the live nginx; (h) docs/threat-model.md lists the nginx-config injection threat, its control and its test. Trace the data flow for a normal case ('agent-company.internal'), an edge case (mixed case with a trailing dot, duplicates, clearing the list) and an error case ('a.lan\nserver {'). Write one FINDING line per gap with file:line. End with an OUTCOME line and 'VERDICT: PASS' or 'VERDICT: FAIL'.

Acceptance: {"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}; {"type": "contains", "value": "OUTCOME"}; {"type": "min_length", "value": 400}
