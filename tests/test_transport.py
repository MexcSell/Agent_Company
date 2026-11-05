"""Connection modes (transport.py): sealed cookies, the scheme gate, certificates, nginx site."""

import ipaddress
import secrets

import pytest
from cryptography import x509
from fastapi.testclient import TestClient

from agent_company import config, prefs, transport
from agent_company.api import create_app


@pytest.fixture
def app():
    with TestClient(create_app(start_background=False), client=("127.0.0.1", 50000)) as c:
        r = c.post("/api/auth/register", json={"name": "admin", "device_secret": secrets.token_urlsafe(32)})
        assert r.json()["user"]["is_admin"]
        yield c


def test_cookie_is_sealed_and_bound():
    v = transport.seal("tok", "Firefox", "203.0.113.5")
    assert "tok" not in v and v.startswith("v1.")
    assert transport.unseal(v, "Firefox", "203.0.113.5") == "tok"
    assert transport.unseal(v, "Firefox", "198.51.100.9") is None  # copied to another computer
    assert transport.unseal(v, "Chrome", "203.0.113.5") is None  # another browser
    assert transport.unseal(v[:-2] + ("A" if v[-2] != "A" else "B") + v[-1], "Firefox", "203.0.113.5") is None
    assert transport.unseal("raw-token", "Firefox", "203.0.113.5") is None
    https = transport.seal("tok", "Firefox")  # issued over HTTPS: not tied to the IP
    assert transport.unseal(https, "Firefox", "198.51.100.9") == "tok"


def test_cipher_is_cached_per_data_dir(tmp_path, monkeypatch):
    v = transport.seal("tok", "Firefox")
    secret = transport.encrypt_text("ghp_x", b"git-token")
    assert transport._cipher() is transport._cipher()  # the key file is read once
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "other"))
    config.reset_settings()
    assert transport.unseal(v, "Firefox", None) is None  # another data dir: another key
    assert transport.decrypt_text(secret, b"git-token") is None
    assert transport.unseal(transport.seal("tok", "Firefox"), "Firefox", None) == "tok"


def test_session_cookie_over_http_is_ip_bound(app):
    assert app.get("/api/auth/me").status_code == 200
    cookie = app.cookies.get("ac_session")
    assert cookie.startswith("v1.")
    elsewhere = TestClient(app.app, client=("203.0.113.7", 1))
    elsewhere.cookies.set("ac_session", cookie)
    assert elsewhere.get("/api/auth/me").status_code == 401


def test_scheme_gate(app):
    plain = {"x-forwarded-proto": "http"}
    assert config.settings().transport == "https"
    r = app.get("/api/auth/me", headers=plain)
    assert r.status_code == 421 and r.json()["code"] == "wrong_scheme"
    page = app.get("/", headers=plain)
    assert page.status_code == 200 and 'var T = "https"' in page.text and "Download certificate" in page.text
    assert app.get("/api/health", headers=plain).status_code == 200
    ca = app.get("/api/tls/ca.crt", headers=plain)
    assert ca.status_code == 200 and b"BEGIN CERTIFICATE" in ca.content
    assert app.get("/api/auth/me", headers={"x-forwarded-proto": "https"}).status_code == 200
    assert app.get("/api/auth/me").status_code == 200  # direct (no proxy): never redirected

    assert app.put("/api/settings/transport", json={"value": "http"}).status_code == 200  # MANAGE_NGINX off
    assert app.get("/api/auth/me", headers=plain).status_code == 200
    assert app.get("/api/tls/ca.crt").status_code == 200
    assert app.get("/api/auth/me", headers={"x-forwarded-proto": "https"}).status_code == 421
    assert app.put("/api/settings/transport", json={"value": "ftp"}).status_code == 400
    assert app.delete("/api/settings/transport").status_code == 200
    assert config.settings().transport == "https"


def test_tls_info_is_admin_only(app):
    info = app.get("/api/tls").json()
    assert info["mode"] == "https" and len(info["ca_fingerprint"].split(":")) == 32
    assert TestClient(app.app).get("/api/tls").status_code == 401


def test_certificates(monkeypatch):
    monkeypatch.setenv("TLS_HOSTS", "dash.example.test,198.51.100.20")
    assert transport.ensure_certs() is True
    assert transport.ensure_certs() is False  # still valid: kept
    p = transport._paths()
    leaf = x509.load_pem_x509_certificate(p["crt"].read_bytes())
    ca = x509.load_pem_x509_certificate(p["ca_crt"].read_bytes())
    assert leaf.issuer == ca.subject
    san = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert ipaddress.ip_address("198.51.100.20") in san.get_values_for_type(x509.IPAddress)
    assert ipaddress.ip_address("127.0.0.1") in san.get_values_for_type(x509.IPAddress)
    assert {"localhost", "dash.example.test"} <= set(san.get_values_for_type(x509.DNSName))
    assert (leaf.not_valid_after_utc - leaf.not_valid_before_utc).days <= 825
    assert oct(p["key"].stat().st_mode & 0o777) == "0o600"
    monkeypatch.setenv("TLS_HOSTS", "new.example.test")  # a new name: the leaf is reissued, the CA kept
    assert transport.ensure_certs() is True
    assert x509.load_pem_x509_certificate(p["ca_crt"].read_bytes()) == ca


def test_nginx_site_rendering():
    https = transport.nginx_conf("https")
    assert "listen 4900 ssl http2;" in https and "error_page 497 = @plain_http;" in https
    assert "X-Forwarded-Proto https;" in https and "X-Forwarded-Proto http;" in https
    assert "X-Forwarded-For $remote_addr;" in https and "ssl_session_cache" in https
    assert "ssl_session_tickets off;" in https
    ciphers = next(line for line in https.splitlines() if "ssl_ciphers" in line)
    assert "-SHA;" not in ciphers and "CBC" not in ciphers and all("ECDHE" in c for c in ciphers.split()[1].split(":"))
    assert f"server 127.0.0.1:{config.settings().port};" in https
    assert 'max-age=604800' in https and "alias" not in https
    http = transport.nginx_conf("http")
    assert "ssl" not in http and "listen 4900;" in http and "X-Forwarded-Proto https" not in http
    with pytest.raises(ValueError):
        transport.nginx_conf("ftp")


def test_apply_writes_nothing_unless_managed(tmp_path, monkeypatch):
    monkeypatch.setenv("NGINX_DIR", str(tmp_path / "nginx"))
    config.reset_settings()
    prefs.load()
    assert transport.apply("https") is False
    assert not (tmp_path / "nginx").exists()
    monkeypatch.setenv("MANAGE_NGINX", "1")  # managed, but PORT == PUBLIC_PORT: refused before writing
    monkeypatch.setenv("PORT", "4900")
    monkeypatch.setenv("PUBLIC_PORT", "4900")
    config.reset_settings()
    with pytest.raises(ValueError, match="proxy to itself"):
        transport.apply("https")
    assert not (tmp_path / "nginx").exists()
