I'm writing the spec in my answer below rather than to a file. The goal loop's `specs.write_outputs` (`src/agent_company/specs.py:109-124`) writes an architect task's output with key `spec` into `.claude/specs/0031-pin-resolved-addresses-for-all-outbound/spec.md`, and the task said not to change files.

# Spec: `netpin` — one pinned-resolution helper for httpx and git fetches

## 0. Problem (evidence)
- **FACT** `research.crawl` (`research.py:299-330`) checks each host with `_is_public_host` (`socket.getaddrinfo`, `research.py:281`) and then calls `c.get(url)`. Its default `httpx.Client` resolves the name again inside httpcore's `SyncBackend.connect_tcp` → `socket.create_connection((host, port))` (`httpcore/_backends/sync.py:188-212`). A DNS answer that changes between the two lookups reaches 127.0.0.1 or 169.254.169.254: this is a time-of-check/time-of-use (TOCTOU) gap, open to DNS rebinding.
- **FACT** `_robots_ok` (`research.py:428`) and every redirect hop use the same client. `research_agent._fetch_url` → `_collect` → `research.collect` → `crawl` share the path. `_search_papers` (`research_agent.py:64-76`) builds its own `httpx.Client` with no check at all.
- **FACT** The marketplace already pins its clones (`marketplace.py:139-211`, `http.curloptResolve`, threat-model line 81), but it has its own copy of the check (`_is_public_host`) and of the entry builder (`_resolve_entry`).

## 1. New module `src/agent_company/netpin.py`

```python
class NotPublic(ValueError): ...

MAX_ADDRESSES = 3   # checked addresses tried per connect, in resolver order

def resolve_public(host: str, port: int, *, check: bool = True) -> list[str]
def curl_resolve_entry(host: str, port: int, ip: str) -> str
class Pins            # per-client map (host, port) -> checked addresses
class PinnedBackend(httpcore.SyncBackend)
class PinnedTransport(httpx.HTTPTransport)
def pinned_client(*, pins: Pins | None = None, allow_private: bool = False,
                  backend: type[PinnedBackend] = PinnedBackend, **httpx_kwargs) -> httpx.Client
```

### 1.1 `resolve_public(host, port, *, check=True) -> list[str]`
1. Normalise: `host = host.strip().strip("[]")`. An empty host raises `NotPublic("no host")`.
2. **IP literal:** if `ipaddress.ip_address(host.split("%")[0])` parses, the address list is that one address. No `getaddrinfo` call.
3. **Name:** otherwise call `socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)` exactly once.
   - Look it up as the attribute `socket.getaddrinfo` at call time. Never use `from socket import getaddrinfo`, because the tests monkeypatch `socket.getaddrinfo`.
   - Pass `host` and `port` positionally. `tests/test_marketplace.py::test_checked_address_is_pinned_for_the_clone` asserts `asked == [("example.com", 443)]`.
   - From each result take `info[4][0]`, strip the IPv6 scope id (`split("%")[0]`), parse it with `ipaddress.ip_address`, and de-duplicate while keeping the resolver order.
   - `OSError` (including `socket.gaierror`), `UnicodeError` and `ValueError` raise `NotPublic(f"{host} cannot be resolved")`. An empty result raises the same error.
4. **Check (when `check=True`):** each address is first unwrapped: `a = a.ipv4_mapped or a` for IPv6, so `::ffff:127.0.0.1` is judged as 127.0.0.1. This does not depend on the Python version.
   - An address is accepted only if `a.is_global and not a.is_multicast`.
   - If any address fails, raise `NotPublic(f"{host} is not a public host ({bad})")`. Mixed answers (one public, one private) are refused as a whole.
   - This is stricter than research's old list: `is_global` also refuses 100.64/10 (CGNAT) and 192.0.0/24.
5. Return the addresses as strings, without brackets or scope ids, in resolver order.

With `check=False`, steps 1-3 and 5 still run. This is the `allow_private` path: it resolves once and is unchecked.

### 1.2 `curl_resolve_entry(host, port, ip) -> str` (replaces `marketplace._resolve_entry`)
- Parse `ip` with `ipaddress.ip_address` (a `ValueError` propagates).
- Refuse a `host` that contains whitespace, `,` or `:` with `ValueError`.
- `port` is an int in 1..65535.
- Return `f"{host}:{port}:[{ip}]"` for IPv6 and `f"{host}:{port}:{ip}"` for IPv4. The output is identical to `_resolve_entry` today.
- An IP-literal URL host never reaches DNS, so refusing a `:` in the host costs nothing. `check_url` keeps working for IPv6-literal URLs because the host there is the literal and the entry is not needed. However, `clone_command` requires a pin for every https URL, so `check_url` must pass the bare literal. The developer checks `https://[2606:2800::1]/x.git` in a test and either refuses it with `ImportRefused` or keeps it working. **HYPOTHESIS:** curl does not need a resolve entry for a literal host. Refusing IPv6-literal repository URLs is acceptable.

### 1.3 `Pins`
- `__init__(allow_private=False)`: `self._map: dict[tuple[str, int], list[str]] = {}` plus a `threading.Lock`.
- `key(host, port)` → `(host.strip("[]").lower(), int(port))`.
- `check(host, port) -> list[str]`: if the key is already present, return the stored list without resolving again. Otherwise call `resolve_public(host, port, check=not self.allow_private)`, store the result and return it. `NotPublic` propagates and nothing is stored. The caller caches refusals itself.
- Every client gets its own `Pins`. There is never a module-global map, so the lifetime is one crawl or one request and nothing is pinned across processes or days.

### 1.4 `PinnedBackend(httpcore.SyncBackend)`
- `__init__(pins: Pins)`.
- `connect_tcp(host, port, timeout=None, local_address=None, socket_options=None)`:
  1. `addrs = self.pins.check(host, port)`. A host that was never checked is resolved and checked here, inside connect, and the connect goes only to that result, never after a second lookup.
  2. `NotPublic` is re-raised as `httpcore.ConnectError(str(e)) from e`, so httpx maps it to `httpx.ConnectError` (an `httpx.HTTPError`). A raw `ValueError` would escape `c.get()`, and `crawl` only catches `httpx.HTTPError`, so one bad redirect would end the whole crawl.
  3. **Decision: try the remaining checked addresses.** For each `ip` in `addrs[:MAX_ADDRESSES]`, in resolver order, call `return self.connect_ip(ip, port, timeout, local_address, socket_options)`. On `httpcore.ConnectError` or `httpcore.ConnectTimeout`, remember the error and try the next address. After the last one, raise the last error.
     - Reason: every address in the list passed the check, so the fallback costs no security. It does what `socket.create_connection` does today (an IPv6-first answer on an IPv4-only host still works).
     - The cap of 3 bounds the worst case to 3 × the connect timeout.
- `connect_ip(ip, port, timeout, local_address, socket_options) -> httpcore.NetworkStream`: `return super().connect_tcp(ip, port, timeout, local_address, socket_options)`.
  - `socket.create_connection` with a numeric address does no DNS lookup.
  - This is the single test seam. Tests subclass the backend or monkeypatch this method to record `ip` and return an `httpcore.MockStream`.
- `connect_unix_socket(...)` raises `httpcore.ConnectError("unix sockets are not used by pinned clients")`.
- The hostname is never changed for anything except the socket address:
  - **FACT:** httpcore passes the origin host to `connect_tcp` (`httpcore/_sync/connection.py:116-124`).
  - **FACT:** after that it calls `stream.start_tls(server_hostname=sni_hostname or origin.host)` (`connection.py:149-156`). SNI and the certificate's hostname check therefore use the original name.
  - The URL and the `Host` header come from the `httpx.Request` and are not touched.

### 1.5 `PinnedTransport(httpx.HTTPTransport)` and `pinned_client`
- `PinnedTransport.__init__(pins, backend_cls, verify=True, cert=None, http1=True, http2=False, limits=httpx.Limits(...defaults))`:
  1. Call `super().__init__(verify=verify, cert=cert, trust_env=False, http1=http1, http2=http2, limits=limits, proxy=None, retries=0)`.
  2. **Guard:** `if not isinstance(getattr(self, "_pool", None), httpcore.ConnectionPool): raise RuntimeError("httpx changed its transport internals; netpin cannot pin")`.
  3. Replace the pool: `self._pool = httpcore.ConnectionPool(ssl_context=httpx.create_ssl_context(verify=verify, cert=cert, trust_env=False), max_connections=limits.max_connections, max_keepalive_connections=limits.max_keepalive_connections, keepalive_expiry=limits.keepalive_expiry, http1=http1, http2=http2, retries=0, network_backend=backend_cls(pins))`.
  4. Keep `retries=0`, so httpcore never calls `_connect` again on its own.
- `pinned_client(*, pins=None, allow_private=False, backend=PinnedBackend, **httpx_kwargs)`:
  - `pins = pins or Pins(allow_private=allow_private)`. A `pins` passed in keeps its own `allow_private`.
  - Refuse the keys `transport`, `mounts`, `proxy`, `trust_env`, `follow_redirects` and `app` in `httpx_kwargs` with `TypeError`, because any of them would bypass the pin.
  - Pop `verify`, `cert`, `http1`, `http2` and `limits` from the kwargs and give them to `PinnedTransport`.
  - Return `httpx.Client(transport=PinnedTransport(...), trust_env=False, follow_redirects=False, **rest)`. Timeout, headers and the rest pass through.
  - With `trust_env=False`:
    - `HTTP(S)_PROXY`, `ALL_PROXY`, `NO_PROXY` and `.netrc` are ignored.
    - `SSL_CERT_FILE` and `SSL_CERT_DIR` are ignored too. **FACT:** `httpx/_config.py:33-39` uses certifi in that case. A caller that needs another CA passes `verify=ssl_context`.

## 2. Call sites

### 2.1 `research.crawl`
- Delete `research._is_public_host`. Import `from agent_company import netpin`.
- Build the client and the pin map:
  ```python
  own = client is None
  pins = netpin.Pins(allow_private=allow_private)
  c = client or netpin.pinned_client(pins=pins, timeout=15, headers={"User-Agent": USER_AGENT})
  ```
  `pinned_client` always sets `follow_redirects=False`. Redirects stay followed by hand, as today.
- The per-host check keeps its cache of verdicts, now keyed by `(host, port)`:
  ```python
  u = httpx.URL(url)
  key = (u.raw_host.decode("ascii"), u.port or (443 if u.scheme == "https" else 80))
  if not allow_private:
      if key not in public:
          try:
              pins.check(*key)
              public[key] = True
          except netpin.NotPublic:
              public[key] = False
      if not public[key]:
          pages.append({"url": url, "error": "private or unresolvable address"})  # string unchanged
          continue
  ```
  - With crawl's own client, `pins` is the transport's map. The pre-check seeds it, and the connect uses exactly the checked addresses: one DNS lookup per host and port.
  - **INFERENCE:** `httpx.URL.raw_host` is the IDNA-encoded, lower-case host without IPv6 brackets. httpcore's origin host is built from it, so both sides use the same key. `Pins.key` also strips brackets and lower-cases the host. A missed key only causes a second, still checked, lookup at connect time, never an unchecked one.
- `allow_private=True`: no pre-check. The backend resolves each host once without the check (`Pins(allow_private=True)`) and connects only to that result.
- **Redirects and new hosts:** each hop is a new queue entry, so it gets the pre-check above. A host the pre-check missed (or the robots.txt base) is checked inside `connect_tcp`. A refused host becomes `httpx.ConnectError` and that page records the error.
- **robots.txt:** `_robots_ok` is unchanged. It uses `c`, so its fetch is pinned.
- **A client passed in by the caller** (tests pass `httpx.Client(transport=httpx.MockTransport(...))`, and `collect` and `run_report` forward `client=`): **behaviour stays exactly as it is today.**
  - The pre-check runs through `pins.check` (so the private-address refusal and its error string are the same).
  - The fetch goes through the caller's transport, which `netpin` does not control.
  - A caller-supplied client that does real network I/O is **unpinned**: it resolves the name again and the TOCTOU gap stays open for it. Say this in `crawl`'s docstring. The only production caller is `crawl(client=None)` via `collect`/`run_report`/`_fetch_url`, which get the pinned default.

### 2.2 `research_agent._search_papers`
- Replace `httpx.Client(timeout=20, headers=...)` with `netpin.pinned_client(timeout=20, headers={"User-Agent": research.USER_AGENT})`.
- `api.openalex.org` is checked inside connect. A refusal raises `httpx.ConnectError`, which the tool loop reports like any HTTP error today.
- The `ctx["client"]` path is unchanged and unpinned, as in 2.1.
- `_fetch_url` needs no change: it goes through `collect` → `crawl`.

### 2.3 `marketplace`
- Delete `_is_public_host` and `_resolve_entry`.
- In `check_url`:
  ```python
  try:
      addrs = netpin.resolve_public(host, port)
  except netpin.NotPublic:
      raise ImportRefused(f"{host or url} is not a public host") from None
  ```
  Then the git version check as today, and `return url, (host, port, addrs[0])`.
- `clone_command`: `allow += ["-c", f"http.curloptResolve={netpin.curl_resolve_entry(*pin)}"]`.
- The rest stays: `http.followRedirects=false`, refusing an https URL without a pin, and `PIN_GIT`.

## 3. Tests

New file `tests/test_netpin.py`, all offline.

**Helper.** `resolving(monkeypatch, answers)` replaces `socket.getaddrinfo`:
- `answers` is a function `(host, call_no) -> list[str]`.
- It records `(host, port)` and returns `AF_INET`/`AF_INET6` tuples like the marketplace helper.

**Fake backend.** Monkeypatch `netpin.PinnedBackend.connect_ip` to:
- record `(ip, port)`;
- return a `MockStream` subclass whose `start_tls` records `server_hostname` and whose `write` records the bytes;
- answer `HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: N\r\nConnection: close\r\n\r\n<html><title>T</title>hello</html>`.

Using `Connection: close` gives one connection per request. **FACT:** `httpcore.MockStream.start_tls` returns `self` (`httpcore/_backends/mock.py:43-49`), so https URLs work offline.

1. `test_dns_change_after_the_check_does_not_reach_the_new_address`
   - getaddrinfo returns `93.184.216.34` on the first call and `127.0.0.1` afterwards.
   - Run `research.crawl("https://docs.example/a", depth=0, allow_private=False)` with no client.
   - Every recorded connect ip is `93.184.216.34`, on both the robots.txt and the page connects.
   - getaddrinfo was called exactly once.
   - `server_hostname == "docs.example"`.
   - The written request contains `Host: docs.example`.
   - The page has text and no error.
2. `test_unchecked_host_is_checked_inside_connect`
   - `pinned_client().get("http://other.example/")` with getaddrinfo returning `10.0.0.5` raises `httpx.ConnectError` matching "not a public host".
   - `connect_ip` was never called.
3. `test_mixed_public_and_private_addresses_are_refused`
   - `resolve_public` with `["93.184.216.34", "10.0.0.1"]` raises `NotPublic`.
   - `crawl` reports `"private or unresolvable address"`.
   - Nothing is connected.
4. `test_ipv6`. `resolve_public` cases:
   - A global IPv6 answer is returned bare.
   - `"fe80::1%eth0"` raises `NotPublic`; the scope id is stripped and nothing crashes.
   - `"::ffff:127.0.0.1"` raises `NotPublic`.
   - The literals `"[2606:2800:220:1:248:1893:25c8:1946]"` and `"127.0.0.1"` make no getaddrinfo call; the first is accepted, the second raises `NotPublic`.
   - `connect_ip` receives the bare IPv6.
   - `curl_resolve_entry("example.com", 8443, "2606:...")` returns `"example.com:8443:[2606:...]"`.
5. `test_redirect_to_a_new_host_gets_its_own_check`
   - Run `crawl(..., same_host=False)`. A's page answers `302 Location: https://b.example/x`.
   - getaddrinfo maps A → public and B → `127.0.0.1`.
   - B's entry carries the error "private or unresolvable address". No connect goes to 127.0.0.1. getaddrinfo was asked for B once.
   - Second case: B resolves to another public IP and `connect_ip` gets B's IP.
6. `test_proxy_environment_is_ignored`
   - Set `HTTPS_PROXY`, `HTTP_PROXY` and `ALL_PROXY` to `http://127.0.0.1:9`.
   - `pinned_client().get("https://docs.example/")` connects only to the checked ip on port 443, never to 127.0.0.1:9.
7. `test_remaining_checked_addresses_are_tried_in_order`
   - With `[A, B]`, if A raises `httpcore.ConnectError`, B is used.
   - If both fail, `httpx.ConnectError` is raised.
   - getaddrinfo is called once in both cases. At most `MAX_ADDRESSES` are tried.
8. `test_allow_private_resolves_once_and_connects_there`
   - `crawl(..., allow_private=True)` with an answer of `127.0.0.1` first and `10.9.9.9` afterwards connects to 127.0.0.1 only.
9. `test_pinned_client_refuses_bypassing_kwargs`: `transport=`, `proxy=`, `mounts=`, `trust_env=` and `follow_redirects=` each raise `TypeError`.
10. `test_search_papers_is_pinned`
    - `_search_papers({"query": "x"}, {})` with getaddrinfo answering public and then 127.0.0.1.
    - `connect_ip` receives the public ip and returns OpenAlex-shaped JSON.

**Existing tests**
- `tests/test_research.py:59` patches `research._is_public_host`, which no longer exists. Replace that line with the `resolving` helper: `docs.example` → `93.184.216.34`, `metadata.internal` → `169.254.169.254`. The assertions stay as they are, because that test passes a `MockTransport` client, which is the "caller client unchanged" path.
- `tests/test_marketplace.py` stays green without edits, in particular `test_url_checks`, `test_checked_address_is_pinned_for_the_clone`, `test_private_or_mixed_address_refused` and `test_old_git_refuses_https_imports`.
- Run `uv run pytest -q tests/test_netpin.py tests/test_research.py tests/test_research_agent.py tests/test_marketplace.py`, then the full suite, serially.

## 4. Threat model (`docs/threat-model.md`)
- **New row** after "Marketplace: DNS rebinding":
  `| **Crawler / research agent: DNS rebinding** | a research URL, a link, a redirect target or the OpenAlex host resolves to a public address for the check and to 127.0.0.1, 169.254.169.254 or an internal service when the fetch resolves it again (SSRF on the server) | netpin.resolve_public: every address global (IPv4-mapped unwrapped, scope ids stripped), mixed answers refused; netpin.pinned_client: an httpcore network backend connects only to the addresses checked for that host (a host not yet checked is checked inside connect), the URL, Host header and TLS SNI/certificate keep the name, trust_env=False (no proxy), no automatic redirects; each redirect hop and robots.txt go through the same check; a caller-supplied httpx client is not pinned (tests only) | test_netpin.py::test_dns_change_after_the_check_does_not_reach_the_new_address, test_unchecked_host_is_checked_inside_connect, test_mixed_public_and_private_addresses_are_refused, test_ipv6, test_redirect_to_a_new_host_gets_its_own_check, test_proxy_environment_is_ignored |`
- **Line 29 (Web → knowledge):** replace `research._is_public_host (no private addresses, also after redirects)` with `netpin.resolve_public + pinned connect (no private addresses, also after redirects; the checked address is the one connected)`.
- **Line 81:** say that the pin entry is `netpin.curl_resolve_entry` and the check is `netpin.resolve_public`, shared with the crawler. The tests named there are unchanged.

## 5. Limits of the httpcore/httpx internals used
- **FACT:** The installed versions are httpx 0.28.1 (`httpx/__version__.py`) and httpcore 1.0.9 (`uv.lock:872-873`). The lock file already pins exact versions, and `pyproject` allows `httpx>=0.27`.
- **FACT:** `NetworkBackend`, `NetworkStream`, `SyncBackend`, `MockStream` and `ConnectionPool(network_backend=...)` are public httpcore API (`httpcore/__init__.py`, `_sync/connection_pool.py:60`). The only private use is `httpx.HTTPTransport._pool` (`httpx/_transports/default.py:156`). `httpx.HTTPTransport` has no `network_backend` parameter.
- **INFERENCE:** if a future httpx renames `_pool`, the assignment would create a dead attribute and the client would connect unpinned without any error. The `isinstance` guard in 1.5 turns that into a `RuntimeError`, and test 1 fails, because `connect_ip` would never be called.
- **Recommendation:** do not change `pyproject` constraints in this goal (a dependency change needs `--permit dependencies`). Rely on the lock pin plus the guard and tests. Any `uv lock --upgrade` must run `tests/test_netpin.py`.
- **INFERENCE:** a pooled keep-alive connection keeps the address it was opened to. A new connection to a host already in `Pins` reuses the checked list and does not resolve again for the client's lifetime, which is one crawl.
- **HYPOTHESIS:** `http2=True` behaves the same because the pool hands the same backend to HTTP/2 connections. It is not used, and `pinned_client` defaults to `http2=False`.
- **Not covered:** async clients. No async outbound fetch exists in these call sites.

## 6. Out of scope
`router.py:881/985/1056/1093` build plain `httpx.Client`s for model providers and administrator-configured base URLs. MCP HTTP servers also connect unpinned. Both come from administrators, not from crawled or untrusted URLs, and this goal does not change them.

---

FINDING: `_search_papers` (`research_agent.py:74`) fetches without any public-address check today, not even an unpinned one. `pinned_client` adds the check as well as the pin.
FINDING: research's current check (private/loopback/link-local/reserved/multicast) accepts CGNAT 100.64/10 and IPv4-mapped loopback. Moving to `is_global` with the IPv4-mapped address unwrapped closes both.
NEXT: Developer task `netpin_research`: write `netpin.py`, change `crawl` and `_search_papers`, update `tests/test_research.py:59` and write the tests 1-10 in §3.
NEXT: Developer task `marketplace_docs`: switch `marketplace.check_url`/`clone_command` to `netpin` and write the threat-model rows in §4.
MESSAGE handoff to developer: the spec for netpin.py and its call sites is in `.claude/specs/0031-pin-resolved-addresses-for-all-outbound/spec.md`. Use the `connect_ip` seam for tests and never `from socket import getaddrinfo`.
MESSAGE request to qa: check that `crawl` with its own client makes exactly one `getaddrinfo` call per host and port, and that the `_pool` guard raises rather than silently unpinning.
PROPOSAL: Pin the model-router and MCP HTTP clients too :: `router.py` and the MCP HTTP transports resolve administrator-given hosts again for every request; `netpin.pinned_client(allow_private=True)` would close that time-of-check/time-of-use gap at low cost.

```json
{"insights": [{"title": "httpcore network backend is the pin point for httpx", "description": "httpx.HTTPTransport takes no network_backend; rebuilding its httpcore.ConnectionPool with network_backend=<SyncBackend subclass> pins the socket address. httpcore keeps the origin host for SNI and certificate checks (connection.py:149-152), so only the address changes.", "evidence": ["httpcore/_sync/connection.py:116-156", "httpcore/_sync/connection_pool.py:60", "httpx/_transports/default.py:156"], "expected_benefit": "One reusable way to stop DNS rebinding for any httpx client without breaking TLS", "confidence": 0.8}]}
```
