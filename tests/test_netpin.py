"""Pinned resolution (netpin): the connect goes to the checked address, never to a second DNS answer. Offline: DNS
is a patched socket.getaddrinfo and every socket is a fake stream returned from PinnedBackend.connect_ip."""

import json
import socket

import httpcore
import httpx
import pytest

from agent_company import netpin, research, research_agent

PUBLIC = "93.184.216.34"
OTHER = "93.184.216.35"
V6 = "2606:2800:220:1:248:1893:25c8:1946"


def resolving(monkeypatch, answers):
    """socket.getaddrinfo answers answers(host, call_no) -> [ip, ...]; returns the list of (host, port) asked."""
    asked = []

    def fake(host, port, *a, **kw):
        asked.append((host, port))
        out = []
        for ip in answers(host, len(asked)):
            fam = socket.AF_INET6 if ":" in ip else socket.AF_INET
            sa = (ip, port or 0, 0, 0) if fam == socket.AF_INET6 else (ip, port or 0)
            out.append((fam, socket.SOCK_STREAM, 6, "", sa))
        if not out:
            raise socket.gaierror("no such host")
        return out

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    return asked


def http_response(body: bytes, status: str = "200 OK", ctype: str = "text/html", extra: str = "") -> bytes:
    return (f"HTTP/1.1 {status}\r\nContent-Type: {ctype}\r\nContent-Length: {len(body)}\r\n{extra}"
            f"Connection: close\r\n\r\n").encode() + body


PAGE = http_response(b"<html><title>T</title>hello</html>")


class FakeStream(httpcore.NetworkStream):
    """Answers the request it was written with responder(request_bytes)."""

    def __init__(self, log, responder):
        self.log, self.responder, self.sent, self.out = log, responder, b"", None

    def write(self, buffer, timeout=None):
        self.sent += buffer
        self.log["written"].append(buffer)

    def read(self, max_bytes, timeout=None):
        if self.out is None:
            self.out = self.responder(self.sent)
        chunk, self.out = self.out[:max_bytes], self.out[max_bytes:]
        return chunk

    def close(self):
        pass

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        self.log["sni"].append(server_hostname)
        return self

    def get_extra_info(self, info):
        return None


@pytest.fixture
def net(monkeypatch):
    """Records each connect's (ip, port), the TLS server names and the bytes written; no real socket."""
    log = {"connects": [], "sni": [], "written": [], "fail": set(), "responder": lambda req: PAGE}

    def connect_ip(self, ip, port, timeout, local_address, socket_options):
        log["connects"].append((ip, port))
        if ip in log["fail"]:
            raise httpcore.ConnectError(f"refused by {ip}")
        return FakeStream(log, lambda req: log["responder"](req))

    monkeypatch.setattr(netpin.PinnedBackend, "connect_ip", connect_ip)
    return log


def test_dns_change_after_the_check_does_not_reach_the_new_address(monkeypatch, net):
    asked = resolving(monkeypatch, lambda host, n: [PUBLIC] if n == 1 else ["127.0.0.1"])
    pages = research.crawl("https://docs.example/a", depth=0, allow_private=False)
    assert [p.get("error") for p in pages] == [None] and "hello" in pages[0]["text"]
    assert len(net["connects"]) == 2  # robots.txt and the page
    assert all(c == (PUBLIC, 443) for c in net["connects"])
    assert asked == [("docs.example", 443)]
    assert set(net["sni"]) == {"docs.example"}
    assert all(b"Host: docs.example\r\n" in w for w in net["written"] if w.startswith(b"GET"))


def test_a_public_host_is_fetched_through_the_pinned_address(monkeypatch, net):
    resolving(monkeypatch, lambda host, n: [PUBLIC])
    with netpin.pinned_client(timeout=5) as c:
        r = c.get("http://docs.example/x")
    assert r.status_code == 200 and "hello" in r.text
    assert net["connects"] == [(PUBLIC, 80)] and net["sni"] == []


def test_unchecked_host_is_checked_inside_connect(monkeypatch, net):
    resolving(monkeypatch, lambda host, n: ["10.0.0.5"])
    with netpin.pinned_client() as c, pytest.raises(httpx.ConnectError, match="not a public host"):
        c.get("http://other.example/")
    assert net["connects"] == []


def test_mixed_public_and_private_addresses_are_refused(monkeypatch, net):
    resolving(monkeypatch, lambda host, n: [PUBLIC, "10.0.0.1"])
    with pytest.raises(netpin.NotPublic, match="10.0.0.1"):
        netpin.resolve_public("mixed.example", 443)
    pages = research.crawl("https://mixed.example/", depth=0, allow_private=False)
    assert pages == [{"url": "https://mixed.example/", "error": "private or unresolvable address"}]
    assert net["connects"] == []


def test_unresolvable_and_special_ranges_are_refused(monkeypatch):
    resolving(monkeypatch, lambda host, n: {"cgnat.example": ["100.64.0.1"], "meta.example": ["169.254.169.254"]}
              .get(host, []))
    for host in ("cgnat.example", "meta.example", "nothing.example", ""):
        with pytest.raises(netpin.NotPublic):
            netpin.resolve_public(host, 443)


def test_ipv6(monkeypatch, net):
    asked = resolving(monkeypatch, lambda host, n: {"v6.example": [V6], "ll.example": ["fe80::1%eth0"],
                                                     "mapped.example": ["::ffff:127.0.0.1"]}[host])
    assert netpin.resolve_public("v6.example", 443) == [V6]
    with pytest.raises(netpin.NotPublic, match="fe80::1"):
        netpin.resolve_public("ll.example", 443)
    with pytest.raises(netpin.NotPublic):
        netpin.resolve_public("mapped.example", 443)
    n = len(asked)
    assert netpin.resolve_public(f"[{V6}]", 443) == [V6]
    with pytest.raises(netpin.NotPublic):
        netpin.resolve_public("127.0.0.1", 443)
    with pytest.raises(netpin.NotPublic):
        netpin.resolve_public("fe80::1%eth0", 443)  # a literal with a scope id: stripped, refused, no crash
    assert len(asked) == n  # literals never reach DNS
    with netpin.pinned_client() as c:
        assert c.get("https://v6.example/").status_code == 200
        assert c.get(f"https://[{V6}]/").status_code == 200
    assert net["connects"] == [(V6, 443), (V6, 443)]
    assert net["sni"][0] == "v6.example"
    assert netpin.curl_resolve_entry("example.com", 8443, V6) == f"example.com:8443:[{V6}]"
    assert netpin.curl_resolve_entry("example.com", 443, PUBLIC) == f"example.com:443:{PUBLIC}"
    for bad in (("exa mple.com", 443, PUBLIC), ("a,b", 443, PUBLIC), ("a:b", 443, PUBLIC),
                ("example.com", 0, PUBLIC), ("example.com", 443, "not-an-ip")):
        with pytest.raises(ValueError):
            netpin.curl_resolve_entry(*bad)


@pytest.mark.parametrize("b_ip,reached", [("127.0.0.1", False), (OTHER, True)])
def test_redirect_to_a_new_host_gets_its_own_check(monkeypatch, net, b_ip, reached):
    asked = resolving(monkeypatch, lambda host, n: {"a.example": [PUBLIC], "b.example": [b_ip]}[host])

    def responder(req):
        if req.startswith(b"GET /start") and b"Host: a.example" in req:
            return http_response(b"", "302 Found", extra="Location: https://b.example/x\r\n")
        return PAGE

    net["responder"] = responder
    pages = research.crawl("https://a.example/start", depth=0, same_host=False, allow_private=False)
    by_url = {p["url"]: p for p in pages}
    assert asked.count(("b.example", 443)) == 1
    assert ("127.0.0.1", 443) not in net["connects"]
    if reached:
        assert "hello" in by_url["https://b.example/x"]["text"]
        assert (OTHER, 443) in net["connects"]
    else:
        assert by_url["https://b.example/x"]["error"] == "private or unresolvable address"
        assert set(net["connects"]) == {(PUBLIC, 443)}


def test_proxy_environment_is_ignored(monkeypatch, net):
    for var in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy"):
        monkeypatch.setenv(var, "http://127.0.0.1:9")
    resolving(monkeypatch, lambda host, n: [PUBLIC] if host == "docs.example" else ["127.0.0.1"])
    with netpin.pinned_client() as c:
        assert c.get("https://docs.example/").status_code == 200
    assert net["connects"] == [(PUBLIC, 443)]
    assert net["sni"] == ["docs.example"]


def test_remaining_checked_addresses_are_tried_in_order(monkeypatch, net):
    asked = resolving(monkeypatch, lambda host, n: [PUBLIC, OTHER, "93.184.216.36", "93.184.216.37"])
    net["fail"] = {PUBLIC}
    with netpin.pinned_client() as c:
        assert c.get("http://multi.example/").status_code == 200
    assert net["connects"] == [(PUBLIC, 80), (OTHER, 80)]
    net["connects"].clear()
    net["fail"] = {PUBLIC, OTHER, "93.184.216.36", "93.184.216.37"}
    with netpin.pinned_client() as c, pytest.raises(httpx.ConnectError):
        c.get("http://multi.example/")
    assert len(net["connects"]) == netpin.MAX_ADDRESSES
    assert len(asked) == 2  # once per client


def test_allow_private_resolves_once_and_connects_there(monkeypatch, net):
    asked = resolving(monkeypatch, lambda host, n: ["127.0.0.1"] if n == 1 else ["10.9.9.9"])
    pages = research.crawl("http://intranet.example/", depth=0, allow_private=True)
    assert "hello" in pages[0]["text"]
    assert set(net["connects"]) == {("127.0.0.1", 80)} and len(net["connects"]) == 2
    assert len(asked) == 1


def test_pinned_client_refuses_bypassing_kwargs():
    for kw in ({"transport": httpx.HTTPTransport()}, {"proxy": "http://127.0.0.1:9"}, {"mounts": {}},
               {"trust_env": True}, {"follow_redirects": True}):
        with pytest.raises(TypeError):
            netpin.pinned_client(**kw)


def test_transport_guard_refuses_unknown_httpx_internals(monkeypatch):
    orig = httpx.HTTPTransport.__init__

    def renamed(self, *a, **kw):
        orig(self, *a, **kw)
        self._renamed_pool = self.__dict__.pop("_pool")

    monkeypatch.setattr(httpx.HTTPTransport, "__init__", renamed)
    with pytest.raises(RuntimeError, match="cannot pin"):
        netpin.pinned_client()


def test_search_papers_is_pinned(monkeypatch, net):
    asked = resolving(monkeypatch, lambda host, n: [PUBLIC] if n == 1 else ["127.0.0.1"])
    body = json.dumps({"results": [{"id": "https://openalex.org/W1", "title": "Reflexion",
                                    "publication_date": "2023-03-20", "abstract_inverted_index": {"Agents": [0]},
                                    "primary_location": {"landing_page_url": "https://arxiv.org/abs/2303.11366"}}]})
    net["responder"] = lambda req: http_response(body.encode(), ctype="application/json")
    out = research_agent._search_papers({"query": "x"}, {})
    assert len(out["documents"]) == 1
    assert net["connects"] == [(PUBLIC, 443)] and net["sni"] == ["api.openalex.org"]
    assert asked == [("api.openalex.org", 443)]
