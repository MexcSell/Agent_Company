# Review (iteration 1)

## QA/security review of the pinned outbound fetches (qa, failed)

I didn't approve this. Most of the change does what the spec asks, but one case the spec required is broken and untested. I also couldn't run the tests: every pytest attempt was blocked by the permission layer, so this review is from reading the code.

## Logic verification

**Root cause.** Before this change the crawler looked up a host's address once for the safety check and then again when connecting. Now `resolve_public` looks the host up once and stores the result in `Pins._map` (`netpin.py:97-98`). The connect gets an IP address, not a name, through `PinnedBackend.connect_ip` (`netpin.py:122-125`), so there is no second lookup. This fixes the cause rather than hiding the symptom.

**`netpin.resolve_public` (`netpin.py:36-60`)**
- **Normal:** a public name triggers one `socket.getaddrinfo(host, port, SOCK_STREAM)` call. Duplicate addresses are dropped, every address must be global and not multicast, and the list comes back in resolver order.
- **Edge:** an IP literal never reaches DNS (`_literal`, line 41). An IPv6 scope id is stripped. `::ffff:127.0.0.1` is judged as 127.0.0.1 (line 64). Shared-carrier addresses (100.64/10) are now refused because the check uses `is_global`.
- **Error:** `OSError`, `UnicodeError`, `ValueError` or an empty answer raise `NotPublic("cannot be resolved")`. If one address is public and another private, the whole host is refused (lines 57-59).

**`Pins.check` (`netpin.py:91-99`)**
- **Normal:** the first call resolves and stores the result; later calls return the stored list. That gives one lookup per host and port, which the architect asked me to check.
- **Edge:** host names are lower-cased and brackets removed, so the crawl's key and httpcore's host match.
- **Error:** `NotPublic` propagates and nothing is stored.

**`PinnedBackend.connect_tcp` (`netpin.py:108-120`)**
- **Normal:** it takes the checked addresses for the host and opens the socket to the first IP. httpcore still uses the original host name for TLS (SNI and the certificate check), and the URL and `Host` header come from the request unchanged.
- **Edge:** if a connect fails, it tries the next checked address, up to `MAX_ADDRESSES = 3`. Every address in the list passed the check.
- **Error:** a refused host becomes `httpcore.ConnectError`, which httpx raises as `httpx.ConnectError`. It does not try another address after a refusal or look the name up again.

**`PinnedTransport.__init__` (`netpin.py:132-145`)**
- **Normal:** proxies are switched off (`proxy=None`, `trust_env=False`) and the connection pool is rebuilt around the pinned backend, also with `trust_env=False`.
- **Edge:** if a future httpx renames its internal `_pool`, the `isinstance` check at lines 138-139 raises `RuntimeError` instead of silently connecting unpinned. `test_netpin.py:219` covers this; I couldn't run it.

**`pinned_client` (`netpin.py:148-157`)**
- **Normal:** builds the client with `trust_env=False` and `follow_redirects=False`.
- **Error:** the arguments that would bypass the pin (`transport`, `mounts`, `proxy`, `trust_env`, `follow_redirects`, `app`) raise `TypeError` (lines 151-153).

**`research.crawl` (`research.py:313-345`)**
- **Normal:** for a public https host, the pre-check `pins.check(*key)` seeds the pin map. The robots.txt fetch (`_robots_ok`, line 449) and the page fetch (`c.get`, line 350) use the same host and port, so both connect to the checked IP with no new lookup. `test_netpin.py:87-95` asserts one lookup, both connects to the public IP, SNI `docs.example` and `Host: docs.example`.
- **Edge cases:**
  - DNS answers a public address, then 127.0.0.1: the second answer is never asked for (`test_netpin.py:87`).
  - Redirect to a new host: it goes back on the queue and gets its own check (`:159`).
  - IPv6-only host: `:130`.
  - Several A records: `:190`.
- **Error cases:**
  - Private or mixed answer: the page records "private or unresolvable address" and nothing connects (`:113`).
  - Unresolvable host: `:122`.
  - Connect failure on the pinned IP: up to 3 tries, then `httpx.ConnectError`, which the crawl records as that page's error (`:190-200`).

**`research_agent._search_papers` (`research_agent.py:74-76`)**
- Before, this fetch had no address check at all; now it uses `pinned_client`. `test_netpin.py:231` asserts one lookup and that only the public IP is reached.

**`marketplace.check_url` and `clone_command` (`marketplace.py:158-191`)**
- **Normal:** the first checked address goes into git's `http.curloptResolve` entry (`test_marketplace.py:176`).
- **Edge (broken):** an IPv6-literal repository URL fails. For `https://[2606:2800::1]/x.git`:
  1. `parts.hostname` is `2606:2800::1`, and `resolve_public` accepts it as a literal.
  2. The pin is `("2606:2800::1", 443, "2606:2800::1")`.
  3. `curl_resolve_entry` raises a plain `ValueError` because the host contains `:` (`netpin.py:72`).
  4. By then `fetch_url` has already removed the empty directory (line 238), and its `try` only catches `TimeoutExpired` and `ImportRefused` (`marketplace.py:248-253`).
  5. So `_fail` never runs and the import record stays at `status="fetched"` with no source directory.

  Spec §1.2 (line 47) asked for a test of this URL, either refusing it with `ImportRefused` or keeping it working. There is no such test (`test_marketplace.py:168` only covers a name that resolves to IPv6).

## Checklist
- **Contract:** met apart from the IPv6-literal case. One small deviation: the spec said to delete `research._is_public_host`, but it was kept as a wrapper for the caller-supplied-client path. That is harmless.
- **Interfaces:** the signatures of `crawl`, `collect`, `run_report` and `check_url` are unchanged. Every production caller passes no client, so every real fetch is pinned: `loop.py:473`, `api.py:3039/3070/3146/3155`, `__main__.py:302/433`, `research_agent.py:48`.
- **Grep:** across `research.py`, `research_agent.py`, `marketplace.py` and `netpin.py`, `httpx.Client(` appears only at `netpin.py:156` and `getaddrinfo` only at `netpin.py:46`. There is no `httpx.get` or `urlopen`. No outbound path looks the address up again after the check.
- **Proxy variables:** cannot bypass the pin. `trust_env=False` is set on the client and the TLS context, and the proxy arguments are refused. `test_netpin.py:180` covers this.
- **`allow_private`:** with it on, the host is looked up once without the check and connects only to addresses from that answer (`netpin.py:97, 115`). If the first fails it may try the next one (up to 3), but it never looks the name up again. `test_netpin.py:204` covers this.
- **Error handling:** fine everywhere except the marketplace IPv6 case above.
- **Tests:** good coverage, except the IPv6-literal marketplace URL the spec asked for.
- **Dependencies:** none added. The code relies on httpx's private `_pool` attribute, which the guard protects.
- **Security:** the TOCTOU gap is closed for the crawler, robots.txt, redirects, the paper search and marketplace clones.
- **Performance:** a dead host now costs at most 3 × 15 s per request.
- **Docs:** the two threat-model rows are updated and accurate.

## Findings
FINDING: src/agent_company/marketplace.py:160-166, src/agent_company/netpin.py:72 | An administrator importing an IPv6-literal https URL gets a raw `ValueError` from `curl_resolve_entry`; it skips `fetch_url`'s `_fail` (marketplace.py:248-253), so the import record stays `status="fetched"` with its source directory already removed. The test spec §1.2 asked for is missing. | In `check_url`, raise `ImportRefused("IPv6-literal repository URLs are not supported")` when `netpin._literal(host)` is an IPv6 address (or skip the resolve entry for literal hosts), and add the `https://[2606:2800::1]/x.git` test to `tests/test_marketplace.py`.
FINDING: src/agent_company/research.py:337 | On the caller-supplied-client path, `_is_public_host(key[0])` ignores the port in `key[1]` and checks with port 443. The verdict doesn't depend on the port, so this is cosmetic. | Pass `*key`.
FINDING: src/agent_company/netpin.py:142 | `trust_env=False` also ignores `SSL_CERT_FILE` and `SSL_CERT_DIR`, so a host that relies on a custom CA bundle will now fail TLS on crawls (the spec says this, but nothing tells operators). | Mention it in the ReadMe or the crawl docstring, or pass `verify=` from settings.

## Blocker and outcome
BLOCKER: environment. I tried to run the tests three times (`uv run pytest -q tests/test_netpin.py tests/test_marketplace.py tests/test_research.py tests/test_research_agent.py` twice and `.venv/bin/python -m pytest …` once). A fourth command, checking whether `.venv` exists, was blocked too. Each returned "This command requires approval". None of the four test files was run by me. The 14 / 47 / 61 passed counts on the blackboard come from the developer agents, not from me.

NEXT: Refuse IPv6-literal URLs with `ImportRefused` in `marketplace.check_url`, add that test, then run the four test files and the full suite in a session that is allowed to run pytest.
MESSAGE request to developer: in `marketplace.check_url` (lines 158-166), refuse IPv6-literal hosts with `ImportRefused` before the pin is built, and add the spec §1.2 test for `https://[2606:2800::1]/x.git` to `tests/test_marketplace.py`.
PROPOSAL: Pin the papers MCP server and the router's httpx clients :: `src/agent_company/mcp_servers/papers.py` (`get(url…)`) and `router.py` still look up hosts again when they fetch; `netpin.pinned_client` (with `allow_private=True` for addresses administrators configure) would close that gap too.

OUTCOME: REVISION_REQUIRED
VERDICT: FAIL
