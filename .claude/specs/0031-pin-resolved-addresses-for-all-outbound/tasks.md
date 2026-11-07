# Tasks (iteration 2)



## 1. Fix the iteration-1 review findings: IPv6-literal marketplace URLs, port in the client-path check, CA-bundle note
- key: `fix_review_findings` · role: developer · writes: yes

Read .claude/specs/0031-pin-resolved-addresses-for-all-outbound/review.md lines 73-83 and spec.md first, then src/agent_company/netpin.py, src/agent_company/marketplace.py:140-200 and :240-260, and src/agent_company/research.py:275-345. Keep the change as small as the fix allows.

1) Cause: marketplace.check_url always builds a curl resolve entry. For an IPv6-literal host such as https://[2606:2800::1]/x.git, netpin.curl_resolve_entry raises a ValueError that escapes fetch_url's _fail handling, so the import record stays 'fetched'. Fix: a literal IP host (netpin._literal(host) is not None) still goes through the public check (netpin.resolve_public raises NotPublic -> ImportRefused for private, loopback or link-local literals). It needs no DNS pin, because curl does not resolve a literal. Make check_url return a pin that clone_command accepts for a literal without adding http.curloptResolve, for example pin=(host, port, ip) where curl_resolve_entry is skipped when host is a literal. Alternatively, if curl_resolve_entry is easier to fix generally, make it format an IPv6 address as curl expects ('host:port:[addr]') and never raise for valid input. Whichever you choose, any exception that can still come out of check_url/clone_command for a bad URL must be an ImportRefused, so fetch_url records the failure.

Add to tests/test_marketplace.py: (a) https://[2606:2800::1]/x.git passes check_url, and clone_command contains no http.curloptResolve entry (or a correct bracketed one, if you chose that route). (b) https://[::1]/x.git and https://[fe80::1]/x.git raise ImportRefused. (c) A fetch_url of a URL that check_url refuses leaves the import record failed, not 'fetched'. Use the existing `resolving` monkeypatch helper. Tests stay offline.

2) research.py (around line 337): pass the real port on the caller-supplied-client path (`_is_public_host(*key)`, or the equivalent for that signature). A test is not required for this cosmetic fix.

3) In the crawl/pinned_client docstring (netpin.pinned_client) and in the outbound-fetch row of docs/threat-model.md, add one sentence: pinned clients use trust_env=False, so proxy variables and SSL_CERT_FILE/SSL_CERT_DIR are ignored and the default certifi bundle is used. Do not add a new setting.

Run: uv run pytest -q tests/test_netpin.py tests/test_marketplace.py tests/test_research.py tests/test_research_agent.py, then uv run pytest -q. Report the pass counts verbatim. Do not touch src/agent_company/loop.py, api.py or the static files.

Acceptance: {"type": "command", "run": "uv run pytest -q tests/test_netpin.py tests/test_marketplace.py tests/test_research.py tests/test_research_agent.py", "requirement_keys": ["S06-R-02"], "stage": "unit"}; {"type": "command", "run": "grep -n '2606:2800::1' tests/test_marketplace.py", "stage": "static"}; {"type": "command", "run": "grep -n -i 'trust_env' docs/threat-model.md", "stage": "static"}; {"type": "command", "run": "uv run pytest -q", "stage": "integration"}

## 2. QA/security re-review of the three fixed findings and the pinned fetch paths
- key: `qa_rereview` · role: qa · writes: no · after: fix_review_findings

Keep this review tight: the last QA run went over its budget [K374]. Do NOT re-review all of iteration 1, which review.md lines 60-71 already accepted (contract, interfaces, grep for second lookups, proxy variables, allow_private). Read only: the diff of this iteration's fix task (git diff HEAD~N against the previous iteration commit 39ab324, limited to marketplace.py, netpin.py, research.py, tests/test_marketplace.py, docs/threat-model.md) and review.md lines 73-83.

Verify the data flow of three cases. Normal: a public IPv6 literal goes through check_url -> clone_command with no broken or second resolution. Edge: a hostname with several A/AAAA records is still pinned to the checked address; spot-check netpin.curl_resolve_entry and the clone command. Error: a refused or loopback IPv6 literal goes through fetch_url -> _fail and the record ends up failed. Confirm that research.py passes the port on the client path and that the trust_env/CA-bundle note is present. Run `uv run pytest -q tests/test_netpin.py tests/test_marketplace.py tests/test_research.py` once. If running a command needs approval, do not retry: say so once and rely on the gate's recorded command results.

List each issue as 'FINDING: file:line | problem | fix'. Then give OUTCOME and end with 'VERDICT: PASS' or 'VERDICT: FAIL'. FAIL only for a real defect: a reachable second DNS lookup, an unhandled exception, or a missing regression test. The papers MCP server and router clients are out of scope (already a PROPOSAL).

Acceptance: {"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}; {"type": "contains", "value": "OUTCOME"}; {"type": "min_length", "value": 400}
