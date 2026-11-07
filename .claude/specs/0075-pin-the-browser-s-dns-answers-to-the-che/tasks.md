# Tasks (iteration 1)



## 1. Build Chromium --host-resolver-rules from the checked address, with regression tests
- key: `pin_args` · role: developer · writes: yes

Read src/agent_company/netpin.py, src/agent_company/marketplace.py (check_url, clone_command) and tests/test_netpin.py first. Add to netpin.py only:
1. `chromium_resolver_rules(pins: list[tuple[str, str]]) -> str`. pins are (host, checked_ip) pairs. It returns 'MAP <host> <ip>' for each pin, IPv6 addresses in [brackets], hosts lower-cased, followed by 'MAP * ~NOTFOUND', joined with ','. A host or IP that holds ',', a space, a control character or '~', or an IP that is not a literal address (use _literal), raises ValueError. A literal-IP host needs no MAP entry, but the catch-all stays.
2. `chromium_pin_args(url: str, *, allow_private: bool = False) -> list[str]`. Only http/https URLs are accepted. It refuses userinfo and resolves the host once with resolve_public(host, port, check=not allow_private), taking the first address in resolver order. It returns ['--host-resolver-rules=<rules>', '--disable-features=DnsOverHttps,AsyncDns', '--no-proxy-server', '--proxy-server=direct://']. A non-public host raises NotPublic.
Extend the module docstring by one sentence about the browser pin. Add tests to tests/test_netpin.py. Patch socket.getaddrinfo the way the existing tests do and stay offline. The tests cover: (a) a normal IPv4 host maps to its checked address and ends with the catch-all; (b) DNS rebinding: getaddrinfo first returns a public address, later 127.0.0.1, and the args built once still name only the first address; (c) IPv6 bracketed; (d) a private or loopback answer raises NotPublic; (e) an injection attempt (host with ',' or ' ') raises ValueError; (f) a literal-IP URL. Also add one row to the data-flow table in docs/threat-model.md: browser fetches pinned via --host-resolver-rules, citing the test names. Do not create a browser executor or MCP server, and do not change marketplace.py.

Acceptance: {"type": "command", "run": "uv run pytest -q tests/test_netpin.py tests/test_marketplace.py", "stage": "security"}; {"type": "command", "run": "uv run python -c \"from agent_company import netpin; r = netpin.chromium_resolver_rules([('example.org', '93.184.216.34'), ('v6.example', '2001:db8::1')]); assert r == 'MAP example.org 93.184.216.34,MAP v6.example [2001:db8::1],MAP * ~NOTFOUND', r\"", "stage": "unit"}; {"type": "command", "run": "grep -q 'host-resolver-rules' docs/threat-model.md", "stage": "static"}

## 2. QA review of the browser DNS pin
- key: `qa_review` · role: qa · writes: no · after: pin_args

Review the diff of pin_args against this goal. Check the data flow in three cases. Normal: a URL is resolved once and every arg names the checked IP plus the catch-all. Edge: IPv6, a literal-IP URL, a redirect or subresource to another host (it must hit ~NOTFOUND), and a mixed-case host. Error: a private or rebinding answer, an injection in the host, a non-http scheme, userinfo. Confirm the tests really simulate DNS changing after the check, that no network access occurs, and that the rule order is correct (the first matching rule wins, so the catch-all must be last). Name the remaining limits: Chromium's resolver rules do not cover proxies and DoH, which is why those flags exist; WebRTC/QUIC; no browser is wired to it yet. List FINDING lines, then an OUTCOME, then 'VERDICT: PASS' or 'VERDICT: FAIL'.

Acceptance: {"type": "regex", "pattern": "FINDING"}; {"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}; {"type": "min_length", "value": 400}
