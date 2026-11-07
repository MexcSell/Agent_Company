# Review (iteration 2)

## QA review of the hardened browser DNS pin (this iteration's diff only) (qa, done)

## Logic verification

I reviewed only this iteration's diff (`git diff HEAD~1`, commit `5922588`) in `netpin.py`, `test_netpin.py` and `docs/threat-model.md`. The one test run I tried was refused (see the last FINDING), so the traces below come from reading the code.

**`chromium_pin_args` (`netpin.py:107-136`)**
- **Normal case, `https://example.com`:**
  - `urlsplit` gives the host `example.com`, and `_browser_host` passes it (no `%` or `\`, and `_rule_token` passes).
  - `_literal` returns None, so the IDNA step runs and leaves the name unchanged. `_browser_host` passes it again, it is ASCII, and the last label `com` is neither all digits nor `0x…`.
  - `resolve_public` calls `getaddrinfo` once. The args are `--host-resolver-rules=MAP example.com <ip>,MAP * ~NOTFOUND` followed by the six switches: `--disable-features=DnsOverHttps,AsyncDns`, `--no-proxy-server`, `--proxy-server=direct://`, `--disable-quic` and `--force-webrtc-ip-handling-policy=disable_non_proxied_udp`.
  - Correct. The catch-all stays last, so the first matching rule is the host's own.
- **Edge case, `http://1.2.3.4/`:**
  - `_browser_host` passes and `_literal` returns an address, so the IDNA and numeric-label block is skipped.
  - `resolve_public` takes the literal without calling `getaddrinfo` and still checks that it is global.
  - In `chromium_resolver_rules`, the host and the address are equal, so the host becomes the target and the rules are `MAP 1.2.3.4 1.2.3.4,MAP * ~NOTFOUND`.
  - Correct, and it works whether or not Chromium applies `MAP *` to literals. IPv6 gives `MAP [v6] [v6]`.
  - A literal host paired with a different address now raises `ValueError` instead of being skipped. `chromium_pin_args` can never trigger this. It is stricter than before, which is fine.
- **Error cases** (all raise `ValueError` and none reach `getaddrinfo`):
  - **`%`:** `%31%32%37.0.0.1` and `1.2.3.4%25eth0` are refused by the first `_browser_host` call, before `_literal` and before IDNA.
  - **Numeric last label:** `2130706433` is not a literal (`ipaddress` refuses an integer written as a string), and its last label is all digits, so it is refused. `127.0.0.1.` has its trailing dot removed, leaves the label `1`, and is refused. Fullwidth digits (`１２７.0.0.1`) are rejected by `ipaddress` (it accepts only ASCII digits), become `127.0.0.1` after IDNA, and are then refused for the numeric label.
  - **`0x`:** `0x7f.1`, `0X7F.0.0.1` and `a.example.0x1` are caught by the `.lower().startswith("0x")` check.
  - **U+FF0C (fullwidth comma):** `urlsplit`'s check of the normalised netloc only rejects `/?#@:`, so the host gets through parsing. The first `_rule_token` call passes it, IDNA's NFKC mapping turns it into `,`, and the second `_browser_host` call refuses it. The fullwidth tilde `～` becomes `~` and is refused the same way.
- **Root cause:** the change closes the gaps I raised in iteration 1 at their source. Hosts that Python and Chromium parse differently are refused before any lookup instead of relying on glibc's behaviour, and the post-IDNA host is checked again. The DNS-rebinding fix itself is unchanged and stays correct for a browser launched with these args.

## Checklist
- **Contract:** met. All six requested changes are present, and only the three allowed files changed.
- **Interfaces:** the signatures are unchanged. Two outputs did change, but no code uses the helper yet (`chromium_*` has no callers outside netpin):
  - a literal URL now produces `MAP ip ip` instead of only the catch-all;
  - the args list has 6 entries instead of 4.
- **Error handling:** every refusal is a `ValueError` and not `NotPublic`, which the tests assert.
- **Edge cases:** covered. An IPv6 zone ID (`[fe80::1%25eth0]`) is now refused because of the `%`. That fails closed and is acceptable.
- **Tests:** offline with the patched `getaddrinfo`, and every error case checks `asked == []`. The new tests (a parametrised IPv4 test with 10 hosts, the IDNA recheck, and the QUIC/WebRTC test) exist under the names the threat-model row cites.
- **Dependencies:** none added.
- **Security:** improved. Every mismatch between Python's and Chromium's parsing now fails closed.
- **Performance:** trivial.
- **Docs:** the threat-model row matches the code:
  - `MAP <ip> <ip>`, the IDNA recheck, the IPv4-looking hosts, QUIC and WebRTC are described as implemented;
  - the IP-literal subresource limit (CDP Fetch or a network namespace) is stated;
  - the warning that a launcher must merge switches rather than append them is stated;
  - all 9 cited tests exist.

FINDING: tests/test_netpin.py:244-352 | No test run of my own: `uv run pytest -q tests/test_netpin.py tests/test_marketplace.py` was refused ("requires approval") in this session. Results rest on the developer's report (86 passed) and the gate's rerun of the security-stage command | Rely on the gate's rerun of that command as the evidence.

FINDING: src/agent_company/netpin.py:98-104 | Not verified against a real Chromium: whether the rule pattern `[v6]` matches how Chromium represents an IPv6 literal host (with or without brackets). If it does not match, an IPv6-literal URL falls to `~NOTFOUND` and fails to load. That is safe, but the page won't open | Check against a real Chromium when the browser executor is built, and emit the unbracketed pattern too if needed.

FINDING: src/agent_company/netpin.py:135-136 | It is assumed, not shown, that `--force-webrtc-ip-handling-policy=disable_non_proxied_udp` stops all direct WebRTC traffic when `--no-proxy-server` is set. Non-proxied TCP candidates are not addressed | In the future executor, also test that WebRTC is blocked, or turn WebRTC off entirely.

FINDING: docs/threat-model.md:30 | The pin is still not wired to a browser, so the goal's rebinding gap stays open in practice. The row says so, which is correct | Track this as the browser-executor follow-up.

NEXT: Build the browser executor with merged `--disable-features`, a check that each switch occurs once in the final launch arguments, CDP Fetch interception for IP-literal requests, and a live Chromium test that is skipped when Chromium is not installed.

OUTCOME: APPROVED_WITH_COMMENTS

VERDICT: PASS
