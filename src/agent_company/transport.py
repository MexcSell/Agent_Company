"""How browsers reach the dashboard: HTTPS (default) or plain HTTP, switched on the Settings page.

nginx sits in front of the app (MANAGE_NGINX=1) on PUBLIC_PORT and talks to uvicorn on
127.0.0.1:PORT. It is configured for speed: HTTP/2, a TLS session cache (no tickets, whose
key nginx never rotates), an ECDSA certificate, gzip, keep-alive to the app, and /static
served from disk. TLS 1.2 is limited to forward-secret AEAD ciphers.

- HTTPS: certificates come from the company's own CA (`data/tls/`). Each computer installs
  the CA once (Settings -> Connection); browsers then trust the site and keep their cache
  (Chrome turns its disk cache off on untrusted certificates, which is what makes HTTPS slow).
  A plain-HTTP request on the same port is handed to the app (nginx `error_page 497`), which
  answers with a hand-off page that moves the browser, and its device secret, to HTTPS.
- HTTP: nothing is encrypted on the wire. The session cookie is still sealed (AES-GCM) and
  bound to the browser and its IP address, so a copied cookie does not work elsewhere.

The session cookie is sealed in both modes; IP binding applies to cookies issued over HTTP.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from agent_company.config import settings

MODES = ("https", "http")
CA_NAME = "Agent Company CA"
LEAF_DAYS = 825  # Apple rejects longer server certificates, even from a user-installed CA
RENEW_DAYS = 30
SITE_NAME = "agent-company"
COOKIE_PREFIX = "v1."

# --- session cookie sealing -----------------------------------------------------------


# AES-256-GCM stays: with AES-NI/VAES it costs ~0.6 us per cookie (ChaCha20 would be slower here).
# What made sealing slow was reading secret.key from disk on every request, so the cipher is kept
# in memory, per data directory (every test has its own).
_aead: dict[str, AESGCM] = {}


def _cookie_key() -> bytes:
    path = settings().data_dir / "secret.key"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:  # another process created it first
            return path.read_bytes()
        with os.fdopen(fd, "wb") as f:
            f.write(AESGCM.generate_key(bit_length=256))
    return path.read_bytes()


def _cipher() -> AESGCM:
    where = str(settings().data_dir)  # a str key: hashing a Path costs more than the decryption
    aead = _aead.get(where)
    if aead is None:
        aead = _aead[where] = AESGCM(_cookie_key())
    return aead


def _ua_hash(user_agent: str | None) -> str:
    return hashlib.sha256((user_agent or "").encode()).hexdigest()[:16]


def encrypt_text(text: str, purpose: bytes) -> str:
    """Encrypt a stored secret (e.g. a user's GitHub token). `purpose` keeps kinds apart."""
    nonce = os.urandom(12)
    ct = _cipher().encrypt(nonce, text.encode(), purpose)
    return base64.urlsafe_b64encode(nonce + ct).decode().rstrip("=")


def decrypt_text(blob: str | None, purpose: bytes) -> str | None:
    if not blob:
        return None
    try:
        raw = base64.urlsafe_b64decode(blob + "=" * (-len(blob) % 4))
        return _cipher().decrypt(raw[:12], raw[12:], purpose).decode()
    except Exception:  # noqa: BLE001 - wrong key or tampered: treat as absent
        return None


def seal(token: str, user_agent: str | None, ip: str | None = None) -> str:
    """Encrypt a session token for the cookie, bound to the browser (and the IP, if given)."""
    payload: dict[str, Any] = {"t": token, "u": _ua_hash(user_agent)}
    if ip:
        payload["i"] = ip
    nonce = os.urandom(12)
    ct = _cipher().encrypt(nonce, json.dumps(payload, separators=(",", ":")).encode(), b"ac-cookie")
    return COOKIE_PREFIX + base64.urlsafe_b64encode(nonce + ct).decode().rstrip("=")


def unseal(value: str | None, user_agent: str | None, ip: str | None) -> str | None:
    """The session token in a sealed cookie, or None if it is forged, tampered or used elsewhere."""
    if not value or not value.startswith(COOKIE_PREFIX):
        return None
    try:
        raw = value[len(COOKIE_PREFIX):]
        blob = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        payload = json.loads(_cipher().decrypt(blob[:12], blob[12:], b"ac-cookie"))
    except Exception:  # noqa: BLE001 - any decoding problem means "no valid session"
        return None
    if payload.get("u") != _ua_hash(user_agent):
        return None
    if "i" in payload and payload["i"] != ip:
        return None
    return payload.get("t")


# --- certificates ------------------------------------------------------------------------


def tls_dir() -> Path:
    d = settings().data_dir / "tls"
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    return d


def _paths() -> dict[str, Path]:
    d = tls_dir()
    return {"ca_key": d / "ca.key", "ca_crt": d / "ca.crt", "key": d / "server.key", "crt": d / "server.crt"}


def _write_private(path: Path, data: bytes) -> None:
    path.unlink(missing_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


def _pem_key(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


def _detect_ips() -> set[str]:
    """This machine's outward-facing addresses (no packets are sent: UDP connect only)."""
    found = set()
    for family, probe in ((socket.AF_INET, "8.8.8.8"), (socket.AF_INET6, "2001:4860:4860::8888")):
        try:
            with socket.socket(family, socket.SOCK_DGRAM) as s:
                s.connect((probe, 53))
                found.add(s.getsockname()[0])
        except OSError:
            pass
    return found


MAX_DOMAINS = 20
_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


def parse_domains(value: str | list[str]) -> list[str]:
    """Validated domain names for the certificate and nginx `server_name` (REQ-009).

    Separated by commas, whitespace or newlines (or a list); lowercased, a trailing dot dropped, duplicates
    removed in input order. Strict letter-digit-hyphen names only: they end up in the nginx config, so ';',
    '{', newlines, wildcards, ports, schemes and IP literals are refused (ValueError naming the entry).
    """
    if isinstance(value, str):
        parts = re.split(r"[\s,]+", value)
    elif isinstance(value, list) and all(isinstance(x, str) for x in value):
        parts = [p for x in value for p in re.split(r"[\s,]+", x)]
    else:
        raise ValueError("domain names must be a string or a list of strings")
    out: list[str] = []
    for raw in parts:
        name = raw.strip().lower().removesuffix(".")
        if not name:
            continue
        try:
            ipaddress.ip_address(name)
        except ValueError:
            pass
        else:
            raise ValueError(f"{raw!r} is an IP address, not a domain name (IP access needs no entry)")
        if not re.fullmatch(r"[a-z0-9.-]+", name):
            raise ValueError(f"{raw!r} is not a domain name: only letters, digits, '-' and '.' are allowed")
        if len(name) > 253:
            raise ValueError(f"{raw!r} is longer than 253 characters")
        for label in name.split("."):
            if not label:
                raise ValueError(f"{raw!r} has an empty label")
            if len(label) > 63:
                raise ValueError(f"{raw!r} has a label longer than 63 characters")
            if not _LABEL.match(label):
                raise ValueError(f"{raw!r} has a label that starts or ends with '-'")
        if name not in out:
            out.append(name)
    if len(out) > MAX_DOMAINS:
        raise ValueError(f"at most {MAX_DOMAINS} domain names")
    return out


def hosts() -> list[str]:
    """Names and addresses the server certificate covers: localhost, this machine, TLS_HOSTS, tls_domains."""
    names = {"localhost", "127.0.0.1", "::1", socket.gethostname(), *_detect_ips()}
    names |= {h.strip() for h in os.environ.get("TLS_HOSTS", "").split(",") if h.strip()}
    names |= set(settings().tls_domains)
    return sorted(n for n in names if n)


def _san(names: list[str]) -> list[x509.GeneralName]:
    out: list[x509.GeneralName] = []
    for n in names:
        try:
            out.append(x509.IPAddress(ipaddress.ip_address(n)))
        except ValueError:
            out.append(x509.DNSName(n))
    return out


def _ensure_ca() -> tuple[ec.EllipticCurvePrivateKey, x509.Certificate]:
    p = _paths()
    if p["ca_key"].exists() and p["ca_crt"].exists():
        key = serialization.load_pem_private_key(p["ca_key"].read_bytes(), None)
        return key, x509.load_pem_x509_certificate(p["ca_crt"].read_bytes())  # type: ignore[return-value]
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, CA_NAME),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Agent Company")])
    start = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=5)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(start)
            .not_valid_after(start + dt.timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                         content_commitment=False, key_encipherment=False,
                                         data_encipherment=False, key_agreement=False,
                                         encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .sign(key, hashes.SHA256()))
    _write_private(p["ca_key"], _pem_key(key))
    p["ca_crt"].write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return key, cert


def _leaf_ok(ca: x509.Certificate, names: list[str]) -> bool:
    p = _paths()
    if not (p["key"].exists() and p["crt"].exists()):
        return False
    cert = x509.load_pem_x509_certificate(p["crt"].read_bytes())
    if cert.issuer != ca.subject:
        return False
    if cert.not_valid_after_utc - dt.datetime.now(dt.timezone.utc) < dt.timedelta(days=RENEW_DAYS):
        return False
    have = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    return set(_san(names)) == set(have)  # a removed name renews the leaf too


def ensure_certs() -> bool:
    """Create the CA and the server certificate if needed. True if the server certificate changed."""
    ca_key, ca = _ensure_ca()
    names = hosts()
    if _leaf_ok(ca, names):
        return False
    key = ec.generate_private_key(ec.SECP256R1())
    start = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=5)
    cert = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Agent Company")]))
            .issuer_name(ca.subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(start)
            .not_valid_after(start + dt.timedelta(days=LEAF_DAYS))
            .add_extension(x509.SubjectAlternativeName(_san(names)), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=False, crl_sign=False,
                                         content_commitment=False, key_encipherment=False,
                                         data_encipherment=False, key_agreement=False,
                                         encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                           critical=False)
            .sign(ca_key, hashes.SHA256()))
    p = _paths()
    _write_private(p["key"], _pem_key(key))
    # Leaf only: browsers have the CA installed, and a shorter chain keeps the handshake small.
    p["crt"].write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return True


def ca_path() -> Path:
    _ensure_ca()
    return _paths()["ca_crt"]


def info() -> dict[str, Any]:
    """What the Settings page shows about the connection."""
    ca = x509.load_pem_x509_certificate(ca_path().read_bytes())
    fp = ca.fingerprint(hashes.SHA256()).hex().upper()
    out: dict[str, Any] = {"mode": settings().transport, "managed": settings().manage_nginx,
                           "public_port": settings().public_port, "ca_name": CA_NAME,
                           "ca_fingerprint": ":".join(fp[i:i + 2] for i in range(0, len(fp), 2)),
                           "ca_valid_until": ca.not_valid_after_utc.date().isoformat(),
                           "domains": settings().tls_domains, "hosts": [], "cert_valid_until": None}
    crt = _paths()["crt"]
    if crt.exists():
        leaf = x509.load_pem_x509_certificate(crt.read_bytes())
        out["cert_valid_until"] = leaf.not_valid_after_utc.date().isoformat()
        san = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        out["hosts"] = [str(v) for v in san.get_values_for_type(x509.IPAddress)] + san.get_values_for_type(x509.DNSName)
    return out


# --- nginx -----------------------------------------------------------------------------------


def _proxy(proto: str) -> str:
    return f"""        proxy_pass http://agent_company_app;
        proxy_http_version 1.1;
        proxy_set_header Host $http_host;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto {proto};
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $agent_company_connection;
        proxy_read_timeout 1d;
        proxy_send_timeout 1d;
        proxy_buffering off;
        proxy_request_buffering off;"""


def nginx_conf(mode: str) -> str:
    """The nginx site for `mode`. Plain string so it can be tested without nginx."""
    if mode not in MODES:
        raise ValueError(f"transport must be one of {list(MODES)}")
    s = settings()
    p = _paths()
    port = s.public_port
    # Validated again here: the names are written into the config text (defence in depth).
    # `_` and default_server keep access by IP address or any other Host header working.
    server_name = " ".join([*parse_domains(s.tls_domains), "_"])
    if mode == "https":
        listen = f"""    listen {port} ssl http2 default_server;
    listen [::]:{port} ssl http2 default_server;
    ssl_certificate {p['crt']};
    ssl_certificate_key {p['key']};
    ssl_protocols TLSv1.2 TLSv1.3;
    # TLS 1.2: forward-secret AEAD suites only (no CBC, no SHA-1); TLS 1.3 suites are all AEAD.
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305;
    ssl_ecdh_curve X25519:prime256v1:secp384r1;
    ssl_prefer_server_ciphers off;
    # Resumption from the server-side cache; no tickets, whose key nginx never rotates (forward secrecy).
    ssl_session_cache shared:agent_company_tls:10m;
    ssl_session_timeout 1d;
    ssl_session_tickets off;
    # Plain HTTP sent to this port: the app answers with a page that moves the browser to HTTPS.
    error_page 497 = @plain_http;"""
        plain = f"""
    location @plain_http {{
{_proxy("http")}
    }}
"""
    else:
        listen = f"""    listen {port} default_server;
    listen [::]:{port} default_server;"""
        plain = ""
    return f"""# Agent Company dashboard ({mode}). Written by the app (Settings -> Connection); edits are overwritten.
map $http_upgrade $agent_company_connection {{
    default upgrade;
    ''      '';
}}

upstream agent_company_app {{
    server 127.0.0.1:{s.port};
    keepalive 16;
}}

server {{
{listen}
    server_name {server_name};
    client_max_body_size 100m;
    keepalive_timeout 75s;

    gzip on;
    gzip_vary on;
    gzip_proxied any;
    gzip_comp_level 5;
    gzip_min_length 1024;
    gzip_types text/css application/javascript text/javascript application/json image/svg+xml text/plain application/xml;

    # Static files: the app sets Cache-Control (a versioned URL, ?v=<content hash>, is cached for a
    # year; anything else revalidates with a cheap 304). Proxied, not read from disk: nginx's worker
    # user usually cannot enter the owner's home.
    location /static/ {{
{_proxy(mode)}
    }}
    # Third-party libraries (xterm.js, pdf.js, mammoth) change rarely: cache them for a week.
    location /static/vendor/ {{
{_proxy(mode)}
        add_header Cache-Control "public, max-age=604800" always;
    }}

    location / {{
{_proxy(mode)}
    }}
{plain}}}
"""


def _site_paths() -> tuple[Path, Path]:
    avail = Path(settings().nginx_dir) / "sites-available" / SITE_NAME
    enabled = Path(settings().nginx_dir) / "sites-enabled" / SITE_NAME
    return avail, enabled


def _run(*cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60)


def apply(mode: str, force_reload: bool = False) -> bool:
    """Write the nginx site for `mode`, test it and reload nginx. Restores the old site on failure.

    A no-op (False) unless MANAGE_NGINX=1. Raises ValueError if nginx rejects the configuration.
    """
    if mode not in MODES:
        raise ValueError(f"transport must be one of {list(MODES)}")
    if not settings().manage_nginx:
        return False
    if settings().port == settings().public_port:
        raise ValueError(f"PORT and PUBLIC_PORT are both {settings().port}: nginx would proxy to itself; "
                         "let the app listen on another port (e.g. PORT=4910)")
    if "PYTEST_CURRENT_TEST" in os.environ:  # a test run must never rewrite the live site
        return False
    if shutil.which("nginx") is None:
        raise ValueError("nginx is not installed")
    force_reload = ensure_certs() or force_reload
    avail, enabled = _site_paths()
    conf = nginx_conf(mode)
    old = avail.read_text() if avail.exists() else None
    if old == conf and enabled.exists() and not force_reload and _run("systemctl", "is-active", "--quiet", "nginx").returncode == 0:
        return False
    avail.parent.mkdir(parents=True, exist_ok=True)
    avail.write_text(conf)
    if not enabled.exists():
        enabled.symlink_to(avail)
    test = _run("nginx", "-t")
    if test.returncode != 0:
        if old is None:
            avail.unlink(missing_ok=True)
            enabled.unlink(missing_ok=True)
        else:
            avail.write_text(old)
        raise ValueError("nginx rejected the configuration: " + (test.stderr or test.stdout).strip()[-800:])
    r = _run("systemctl", "reload-or-restart", "nginx")
    if r.returncode != 0:
        raise ValueError("nginx did not reload: " + (r.stderr or r.stdout).strip()[-800:])
    _wait_ready(mode)
    return True


def _speaks_tls(port: int) -> bool:
    import ssl

    ctx = ssl.create_default_context(cafile=str(_paths()["ca_crt"]))
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2) as raw, \
                ctx.wrap_socket(raw, server_hostname="localhost"):
            return True
    except (OSError, ssl.SSLError):
        return False


def _wait_ready(mode: str, timeout: float = 10.0) -> None:
    """A reload returns before nginx's new workers take over; wait so the browser's switch lands."""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _speaks_tls(settings().public_port) == (mode == "https"):
            return
        time.sleep(0.2)
    raise ValueError(f"nginx did not start serving {mode.upper()} on port {settings().public_port} in time")


# --- switching schemes ------------------------------------------------------------------------

EXEMPT = ("/api/health", "/api/tls/ca.crt")


def handoff_page(target: str) -> str:
    """Served on the old scheme: moves the browser, with its device secret, to `target`."""
    fp = info()["ca_fingerprint"] if target == "https" else ""
    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Agent Company</title>
<style>
:root{--bg:#f8fafc;--fg:#0f172a;--muted:#475569;--card:#fff;--line:#e2e8f0;--accent:#2563eb}
@media (prefers-color-scheme:dark){:root{--bg:#0b1220;--fg:#e2e8f0;--muted:#94a3b8;--card:#111a2e;--line:#1e293b;--accent:#60a5fa}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 system-ui,sans-serif}
main{max-width:600px;margin:8vh auto;padding:0 16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:20px 22px}
h1{font-size:20px;margin:0 0 8px}.muted{color:var(--muted)}code{word-break:break-all;font-size:12px}
a.btn,button{display:inline-block;background:var(--accent);color:#fff;border:0;border-radius:8px;padding:9px 14px;
font:inherit;text-decoration:none;cursor:pointer;margin:6px 8px 6px 0}button.ghost{background:transparent;color:var(--accent);border:1px solid var(--line)}
details{margin-top:10px}li{margin:4px 0}
</style></head><body><main><div class="card" id="m"><p class="muted">Connecting securely…</p></div></main>
<script>
(function () {
  var T = "__TARGET__", FP = "__FP__";
  var dest = T + "://" + location.host + location.pathname + location.search;
  var s = null; try { s = localStorage.getItem("ac.device"); } catch (e) {}
  // The secret stays on this origin too, so switching back to HTTP later keeps everyone signed in.
  function go() {
    location.replace(dest + (s ? "#ac-device=" + encodeURIComponent(s) : ""));
  }
  if (T !== "https") return go();
  // This computer reached HTTPS with this CA before, so it is installed: skip the probe.
  var KEY = "ac.https-ok";
  try { if (localStorage.getItem(KEY) === FP) return go(); } catch (e) {}
  // An untrusted certificate makes the probe fail at once. A slow handshake is not a failure
  // (Windows can take several seconds to verify a user-installed CA), so a late success still
  // moves the browser on, even after the install page is shown.
  var shown = false, timer = setTimeout(show, 15000);
  fetch("https://" + location.host + "/api/health", { mode: "no-cors", cache: "no-store" })
    .then(function () {
      clearTimeout(timer);
      try { localStorage.setItem(KEY, FP); } catch (e) {}
      go();
    }, show);
  function show() {
    if (shown) return; shown = true; clearTimeout(timer);
    document.getElementById("m").innerHTML =
      '<h1>This site now uses HTTPS</h1>' +
      '<p>Install the company certificate once on this computer, then restart the browser. ' +
      'Without it the browser warns on every visit and turns off its cache, which makes pages slow.</p>' +
      '<a class="btn" href="/api/tls/ca.crt" download="agent-company-ca.crt">Download certificate</a>' +
      '<button id="go" class="ghost">Continue without installing</button>' +
      '<p class="muted">Check that the fingerprint (SHA-256) matches the Settings page:<br><code>' + FP + '</code></p>' +
      '<details><summary>How to install</summary><ul>' +
      '<li><b>Windows</b>: open the file → Install Certificate → Current User → Place all certificates in → <i>Trusted Root Certification Authorities</i>.</li>' +
      '<li><b>macOS</b>: open the file (Keychain Access, System) → double-click <i>Agent Company CA</i> → Trust → <i>Always Trust</i>.</li>' +
      '<li><b>Linux, Chrome/Edge</b>: Settings → Privacy and security → Security → Manage certificates → Authorities → Import, tick <i>Trust this certificate for identifying websites</i>.</li>' +
      '<li><b>Firefox</b> (any OS): Settings → Privacy &amp; Security → Certificates → View Certificates → Authorities → Import, tick <i>identify websites</i>.</li>' +
      '</ul></details>';
    document.getElementById("go").onclick = go;
  }
})();
</script></body></html>""".replace("__TARGET__", target).replace("__FP__", fp)
