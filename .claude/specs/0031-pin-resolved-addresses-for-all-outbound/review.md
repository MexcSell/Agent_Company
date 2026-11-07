# Review (iteration 2)

## QA/security re-review of the three fixed findings and the pinned fetch paths (qa, done)

I'm approving the fixes with comments. Two of the three earlier findings are fixed in code with regression tests. The third, research.py's port, wasn't changed, but it is harmless. I couldn't run the tests myself.

BLOCKER: environment. `uv run pytest -q tests/test_netpin.py tests/test_marketplace.py tests/test_research.py` returned "This command requires approval". I did not retry, so the test results below are the developer's recorded runs, not mine: 91 passed on the four files and 926 passed on the full suite.

## Logic verification

I read the fix diff against `39ab324` for the five files and `review.md` lines 73-83.

**`marketplace.check_url` (lines 140-169)**
- **Normal, a public IPv6 literal:** `https://[2606:2800::1]/x.git` parses, the host becomes `2606:2800::1`, and `netpin.resolve_public` treats it as a literal. So it makes no DNS call, passes the public-address check, and returns the pin `("2606:2800::1", 443, "2606:2800::1")`. The test asserts `asked == []`, so it makes no DNS lookup.
- **Edge, a malformed literal:** `https://[2606:2800::1/x.git` makes `urlsplit` raise `ValueError`, which is now turned into `ImportRefused`. The test covers it.
- **Error, loopback or link-local literals:** `[::1]`, `[fe80::1]` and `[::ffff:127.0.0.1]` are refused with `ImportRefused` inside `check_url`. This happens before `_new_import`, so no import record is created. The task expected the record to end up `failed`; creating no record at all is the stronger outcome. `test_a_refused_pin_leaves_the_import_failed` asserts there is exactly one record, the one from the clone-time refusal.

**`marketplace.clone_command` (lines 186-197)**
- **Normal, a literal host:** `netpin._literal(pin[0])` recognises it, so no `http.curloptResolve` entry is added. curl connects to the literal without resolving it, so there is no second lookup, and the broken entry from the first review can't be built any more.
- **Edge, a host name with several A/AAAA records:**
  - `check_url` refuses the host unless every address is public, then pins the first one.
  - `curl_resolve_entry` builds `host:port:ip`, with brackets for IPv6.
  - git uses only that address, so it does not resolve the name again.
  - Covered by `test_checked_address_is_pinned_for_the_clone` (an AAAA answer gives `example.com:8443:[2606:…]`) and `test_marketplace_pin_comes_from_netpin` (two A records; the first is pinned).
- **Error, the pin can't be built:**
  - A `ValueError` from `curl_resolve_entry` now becomes `ImportRefused`.
  - `fetch_url`'s `except ImportRefused` (around line 254) then runs `_fail`.
  - `test_a_refused_pin_leaves_the_import_failed` asserts the record is `failed` with "cannot be pinned", its directory is gone, and no git command ran.
  - The raw `ValueError` that left the record at `fetched` can no longer escape.

**Root cause:** this closes the defect from the first review: a literal host no longer gets a resolve entry, and every refusal goes through `ImportRefused` and `_fail`. It doesn't just hide the symptom.

**`netpin.pinned_client` docstring and `docs/threat-model.md`:** the note is in both places. Pinned clients use `trust_env=False`, so proxy variables and `SSL_CERT_FILE`/`SSL_CERT_DIR` are ignored and the default certifi bundle is used.

**`research.py:336`:** unchanged. It still calls `_is_public_host(key[0])` without the port. This is the caller-supplied-client path, which only tests use, and the answer doesn't depend on the port. It isn't a second lookup on a pinned path and nothing else can go wrong with it.

## Checklist
- **Contract:** met for the IPv6 literal and CA-bundle findings; the port item is deferred, with a reason given.
- **Interfaces:** the signatures are unchanged. An IPv4 literal (`https://93.184.216.34/…`) now also gets no resolve entry. That's correct, because no DNS lookup happens.
- **Error handling:** every refusal on the marketplace URL path is now `ImportRefused`.
- **Edge cases:** malformed, loopback, link-local and IPv4-mapped literals are all covered.
- **Tests:** two new regression tests cover the earlier finding, `test_ipv6_literal_url_is_checked_and_cloned_without_a_resolve_entry` and `test_a_refused_pin_leaves_the_import_failed`.
- **Dependencies:** none added.
- **Security:** none of the changed paths looks an address up a second time.
- **Performance:** unchanged.
- **Docs:** updated, including the threat-model row and its test names.

## Findings
FINDING: src/agent_company/research.py:336 | The caller-supplied-client check still ignores `key[1]` and uses port 443. It's cosmetic (the verdict doesn't depend on the port, and only tests use this path), so it doesn't block. | Pass `*key` and change the lambda at `tests/test_research.py:59` to `lambda h, port=443: ...` in a task that may edit both files.
FINDING: src/agent_company/marketplace.py:193 | `clone_command` calls `netpin._literal`, a private helper of another module. It's a style issue and nothing breaks. | Rename it to a public `netpin.is_literal` or `literal_address`.
FINDING: docs/threat-model.md:82 | The "Research crawl" row now states `trust_env=False` twice: once in the existing text and again in the new CA-bundle parenthesis. It's only wording. | Merge the two into one clause.

NEXT: In a later task that may edit both `src/agent_company/research.py` and `tests/test_research.py`, pass the port to the caller-client check.
MESSAGE done to general-manager: the IPv6-literal marketplace fix and the CA-bundle note pass re-review; the research.py port item is cosmetic and can go in a later task that may edit both research.py and tests/test_research.py.

OUTCOME: APPROVED_WITH_COMMENTS
VERDICT: PASS
