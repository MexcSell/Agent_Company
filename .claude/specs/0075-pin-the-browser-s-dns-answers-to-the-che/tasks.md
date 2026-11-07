# Tasks (iteration 2)



## 1. Harden chromium_pin_args host handling and close the QA findings of iteration 1
- key: `harden_pin` · role: developer · writes: yes

Read src/agent_company/netpin.py (chromium_pin_args, chromium_resolver_rules, _rule_token, _literal, about lines 80-126), tests/test_netpin.py (tests from test_chromium_pin_* at line 247 onwards) and the FINDING lines in .claude/specs/0075-pin-the-browser-s-dns-answers-to-the-che/review.md. Keep the change small. Do not touch the httpx pinning, marketplace or research code. Make these changes:
1. After the IDNA step in chromium_pin_args, check the host again with _rule_token and require it to be pure ASCII, all BEFORE resolve_public, so that a fullwidth comma (U+FF0C) or another NFKC-mapped separator never reaches getaddrinfo.
2. Refuse hosts that Chromium's WHATWG parser would read as an IPv4 literal but _literal does not accept in canonical form: a host containing '%' or '\\', a host whose last label is all digits or starts with '0x' (case-insensitive), and a trailing-dot host whose remainder is numeric. Refuse them with ValueError before any lookup. Canonical literals (1.2.3.4, [2606:2800::1]) still work as before.
3. For a literal-address host, emit `MAP <ip> <ip>` (IPv6 in brackets) before `MAP * ~NOTFOUND`, so the URL's own page loads whether or not Chromium applies MAP * to literals. Update the existing literal-address test to match.
4. Add '--disable-quic' and '--force-webrtc-ip-handling-policy=disable_non_proxied_udp' to the returned args, keeping the existing four.
5. docs/threat-model.md, the 'Headless browser → web' row (line 30) only: say that IP-literal subresources and redirects are outside resolver rules and need request interception (CDP Fetch) or a network namespace in the future executor. Say that Chromium keeps only the last --disable-features / --host-resolver-rules, so the executor must merge feature lists and assert each switch occurs once. Add the new test names to the tests column.
6. tests/test_netpin.py: add tests with socket.getaddrinfo monkeypatched to record calls and return a public address: (a) '%31%32%37.0.0.1', '0x7f.1', '2130706433', '127.0.0.1.' are refused and getaddrinfo is never called; (b) a host with U+FF0C is refused with getaddrinfo never called (asked == []); (c) a literal host gives 'MAP <ip> <ip>' before the catch-all; (d) the args contain --disable-quic and the WebRTC policy switch. Tests stay offline.
Run `uv run pytest -q tests/test_netpin.py tests/test_marketplace.py` and report the exact pass count. Do not run the whole suite more than once.

Acceptance: {"type": "command", "run": "uv run pytest -q tests/test_netpin.py tests/test_marketplace.py", "stage": "security"}; {"type": "command", "run": "grep -q -- '--disable-quic' src/agent_company/netpin.py"}; {"type": "command", "run": "grep -q 'CDP Fetch' docs/threat-model.md"}

## 2. QA review of the hardened browser DNS pin (this iteration's diff only)
- key: `qa_review` · role: qa · writes: no · after: harden_pin

Review ONLY the diff of this iteration in src/agent_company/netpin.py, tests/test_netpin.py and docs/threat-model.md (`git diff HEAD~1` or the files' chromium_* parts). Iteration 1's work was already reviewed and passed: do not re-review it and do not reread unrelated modules. Stay focused, because the last review failed only on cost. Verify the data flow of three cases from the code: (normal) https://example.com → one lookup → `MAP example.com <ip>,MAP * ~NOTFOUND` plus the six switches; (edge) http://1.2.3.4/ → no lookup → `MAP 1.2.3.4 1.2.3.4` before the catch-all; (error) a host with '%', a numeric last label, '0x' or U+FF0C → ValueError before getaddrinfo. Check that the threat-model row matches the code and names the tests that exist. If commands are refused, say so in one FINDING and rely on the gate's rerun of the security-stage command. List FINDING lines (file:line | problem | fix), then an OUTCOME line, and end with 'VERDICT: PASS' or 'VERDICT: FAIL'.

Acceptance: {"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}; {"type": "contains", "value": "OUTCOME"}; {"type": "min_length", "value": 400}
