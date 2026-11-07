"""Pinned resolution for outbound fetches: the address that passed the check is the one connected.

A host is resolved once, every address must be global, and the connect goes only to those addresses, so a DNS
answer that changes between the check and the fetch (DNS rebinding) cannot reach 127.0.0.1 or an internal service.
For httpx the pin is an httpcore network backend; the URL, the Host header and TLS SNI/certificate checks keep the
name. For git it is the `http.curloptResolve` entry (`curl_resolve_entry`). For a Chromium browser it is
`--host-resolver-rules` (`chromium_pin_args`): the checked host maps to its checked address and every other name the
page or a redirect asks for resolves to nothing, with DNS-over-HTTPS and proxies off.
"""

from __future__ import annotations

import ipaddress
import socket
import threading
from typing import Any
from urllib.parse import urlsplit

import httpcore
import httpx


class NotPublic(ValueError):
    """A host that is not public, or cannot be resolved."""


MAX_ADDRESSES = 3  # checked addresses tried per connect, in resolver order

_BYPASS = ("transport", "mounts", "proxy", "trust_env", "follow_redirects", "app")


def _literal(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host.split("%")[0])
    except ValueError:
        return None


def resolve_public(host: str, port: int, *, check: bool = True) -> list[str]:
    """The host's addresses in resolver order, resolved once; with check, each one must be global."""
    host = (host or "").strip().strip("[]")
    if not host:
        raise NotPublic("no host")
    lit = _literal(host)
    if lit is not None:
        addrs = [lit]
    else:
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)  # looked up at call time: tests patch it
            addrs = []
            for info in infos:
                a = ipaddress.ip_address(str(info[4][0]).split("%")[0])
                if a not in addrs:
                    addrs.append(a)
        except (OSError, UnicodeError, ValueError):
            raise NotPublic(f"{host} cannot be resolved") from None
        if not addrs:
            raise NotPublic(f"{host} cannot be resolved")
    if check:
        bad = [str(a) for a in addrs if not _global(a)]
        if bad:
            raise NotPublic(f"{host} is not a public host ({', '.join(bad)})")
    return [str(a) for a in addrs]


def _global(a: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped is not None:
        a = a.ipv4_mapped  # ::ffff:127.0.0.1 is judged as 127.0.0.1
    return a.is_global and not a.is_multicast


def curl_resolve_entry(host: str, port: int, ip: str) -> str:
    """git's `http.curloptResolve` entry `host:port:ip` that pins the connect to the checked address."""
    addr = ipaddress.ip_address(ip)
    if not host or any(ch.isspace() or ch in ",:" for ch in host):
        raise ValueError(f"host not usable in a resolve entry: {host!r}")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError(f"bad port: {port!r}")
    return f"{host}:{port}:[{addr}]" if addr.version == 6 else f"{host}:{port}:{addr}"


def _rule_token(value: str, what: str) -> str:
    """A host or address that cannot end or extend a resolver rule (',', space, control character, '~', '*')."""
    if not value or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 or ch in ",~*" for ch in value):
        raise ValueError(f"{what} not usable in a resolver rule: {value!r}")
    return value


def chromium_resolver_rules(pins: list[tuple[str, str]]) -> str:
    """Chromium's `--host-resolver-rules` value: each checked host maps to its checked address, every other name
    fails to resolve (`MAP * ~NOTFOUND`). A literal-address host needs no entry; the catch-all always stays."""
    rules = []
    for host, ip in pins:
        host = _rule_token(host, "host").strip("[]").lower()
        addr = _literal(_rule_token(ip, "address").strip("[]"))
        if addr is None:
            raise ValueError(f"not a literal address: {ip!r}")
        if _literal(host) is not None:
            continue  # Chromium never resolves an address literal
        rules.append(f"MAP {host} [{addr}]" if addr.version == 6 else f"MAP {host} {addr}")
    return ",".join([*rules, "MAP * ~NOTFOUND"])


def chromium_pin_args(url: str, *, allow_private: bool = False) -> list[str]:
    """Chromium flags that pin the URL's host to the address checked once here (no second lookup by the browser);
    raises NotPublic for a non-public host (unless allow_private) and ValueError for an unusable URL."""
    try:
        parts = urlsplit((url or "").strip())
        port = parts.port
    except ValueError:
        raise ValueError(f"bad URL: {url!r}") from None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"only http(s) URLs are pinned, not {scheme or url!r}")
    if "@" in parts.netloc:
        raise ValueError("a pinned URL must not carry a user name or password")
    host = _rule_token(parts.hostname or "", "host")
    if _literal(host) is None:
        try:
            host = host.encode("idna").decode("ascii")  # the name Chromium looks up
        except UnicodeError:
            raise ValueError(f"host not usable in a resolver rule: {host!r}") from None
    ip = resolve_public(host, port or (443 if scheme == "https" else 80), check=not allow_private)[0]
    return [f"--host-resolver-rules={chromium_resolver_rules([(host, ip)])}",
            "--disable-features=DnsOverHttps,AsyncDns", "--no-proxy-server", "--proxy-server=direct://"]


class Pins:
    """One client's checked addresses per (host, port); never shared between clients."""

    def __init__(self, allow_private: bool = False) -> None:
        self.allow_private = allow_private
        self._map: dict[tuple[str, int], list[str]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def key(host: str, port: int) -> tuple[str, int]:
        return host.strip("[]").lower(), int(port)

    def check(self, host: str, port: int) -> list[str]:
        """The stored addresses, or resolve (and check unless allow_private) once and store them."""
        k = self.key(host, port)
        with self._lock:
            if k in self._map:
                return self._map[k]
            addrs = resolve_public(k[0], k[1], check=not self.allow_private)
            self._map[k] = addrs
            return addrs


class PinnedBackend(httpcore.SyncBackend):
    """Connects only to the addresses checked for the origin host; the host name stays for TLS."""

    def __init__(self, pins: Pins) -> None:
        self.pins = pins

    def connect_tcp(self, host: str, port: int, timeout: float | None = None, local_address: str | None = None,
                    socket_options: Any = None) -> httpcore.NetworkStream:
        try:
            addrs = self.pins.check(host, port)
        except NotPublic as e:
            raise httpcore.ConnectError(str(e)) from e
        last: Exception | None = None
        for ip in addrs[:MAX_ADDRESSES]:
            try:
                return self.connect_ip(ip, port, timeout, local_address, socket_options)
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as e:
                last = e
        raise last or httpcore.ConnectError(f"{host} has no address")

    def connect_ip(self, ip: str, port: int, timeout: float | None, local_address: str | None,
                   socket_options: Any) -> httpcore.NetworkStream:
        """The one place a socket is opened; a numeric address does no DNS lookup."""
        return super().connect_tcp(ip, port, timeout, local_address, socket_options)

    def connect_unix_socket(self, *args: Any, **kwargs: Any) -> httpcore.NetworkStream:
        raise httpcore.ConnectError("unix sockets are not used by pinned clients")


class PinnedTransport(httpx.HTTPTransport):
    def __init__(self, pins: Pins, backend_cls: type[PinnedBackend] = PinnedBackend, verify: Any = True,
                 cert: Any = None, http1: bool = True, http2: bool = False,
                 limits: httpx.Limits = httpx.Limits(max_connections=100, max_keepalive_connections=20,
                                                     keepalive_expiry=5.0)) -> None:
        super().__init__(verify=verify, cert=cert, trust_env=False, http1=http1, http2=http2, limits=limits,
                         proxy=None, retries=0)
        if not isinstance(getattr(self, "_pool", None), httpcore.ConnectionPool):
            raise RuntimeError("httpx changed its transport internals; netpin cannot pin")
        self._pool.close()
        self._pool = httpcore.ConnectionPool(
            ssl_context=httpx.create_ssl_context(verify=verify, cert=cert, trust_env=False),
            max_connections=limits.max_connections, max_keepalive_connections=limits.max_keepalive_connections,
            keepalive_expiry=limits.keepalive_expiry, http1=http1, http2=http2, retries=0,
            network_backend=backend_cls(pins))


def pinned_client(*, pins: Pins | None = None, allow_private: bool = False,
                  backend: type[PinnedBackend] = PinnedBackend, **httpx_kwargs: Any) -> httpx.Client:
    """An httpx client whose connects go only to checked addresses: no proxy, no automatic redirects.

    Pinned clients use trust_env=False, so proxy variables and SSL_CERT_FILE/SSL_CERT_DIR are ignored and the
    default certifi bundle is used."""
    for k in _BYPASS:
        if k in httpx_kwargs:
            raise TypeError(f"pinned_client does not take {k}=: it would bypass the pinned address")
    pins = pins or Pins(allow_private=allow_private)
    tkw = {k: httpx_kwargs.pop(k) for k in ("verify", "cert", "http1", "http2", "limits") if k in httpx_kwargs}
    return httpx.Client(transport=PinnedTransport(pins, backend, **tkw), trust_env=False, follow_redirects=False,
                        **httpx_kwargs)
