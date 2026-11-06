# Specification: REQ-009: own domain name for the dashboard

## Specify own domain names for the dashboard (REQ-009) (architect, done)

I couldn't create `.claude/specs/req-009-domain-names/spec.md` because writing to `.claude/` was refused, and the task says to answer in text. The full spec is below so the loop can save it to that path. Until then, the `file_exists` acceptance check fails.

# REQ-009: Own domain names for the dashboard (spec)

An administrator enters domain names (e.g. `agent-company.internal`) on Settings -> Connection. They go into the own-CA server certificate and into nginx `server_name`. Access by IP address and by `localhost` keeps working.

## 1. Setting `tls_domains`
- `config.Settings.tls_domains: str = ""`. `load_settings()` reads `os.environ.get("TLS_DOMAINS", "")`.
- `prefs.SCHEMA["tls_domains"] = (str, "Domain names of the dashboard (comma or space separated); added to the HTTPS certificate and the nginx site")`. It gets no CHOICES entry.
- The stored value is normalised. `prefs._coerce` handles `tls_domains` by returning `",".join(transport.parse_domains(value))`. The result is lowercase, has no trailing dot, is de-duplicated, sorted and comma-joined. `""` means no names.

## 2. Validation: `transport.parse_domains(text: str) -> list[str]`
- Split with `re.split(r"[,\s]+", text)` and drop empty parts. Lowercase each name and remove one trailing `.`.
- Check each name. A failure raises `ValueError(f"tls_domains: {name!r} is not a valid domain name ({reason})")`.
  - At most 253 chars.
  - Not an IP address: reject if `ipaddress.ip_address(name)` succeeds. The last label must not be all digits.
  - None of `* : / @` and no whitespace, so no wildcard, port, scheme or path.
  - Every label matches `^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$`: 1-63 chars of `[a-z0-9-]`, not starting or ending with `-`. An empty label (`a..b`) fails.
- At most 20 names after de-duplication, otherwise `ValueError("tls_domains: at most 20 names")`. Return `sorted(set(...))`.
- Valid: `agent-company.internal`, `dash.lan`, `Dash.LAN.` (→ `dash.lan`), and `""` (→ `[]`).
- Rejected: `http://x`, `*.x`, `a..b`, `-a.b`, `1.2.3.4`, `x:443`, and a 64-char label.

## 3. Certificate (`hosts`, `_leaf_ok`, `ensure_certs`)
- New `domains() -> list[str]` returns `parse_domains(settings().tls_domains)`. If the value is invalid, it logs a warning and returns `[]`. Only a bad `.env` value can be invalid, so a bad name never stops the server from starting.
- `hosts()` covers localhost, 127.0.0.1, ::1, the hostname, the detected IPs, `TLS_HOSTS` (unchanged) and `*domains()`, sorted. `_san` already makes `DNSName` entries for non-IP names.
- `_leaf_ok` is unchanged. It checks that the wanted SANs are a subset of the certificate's, so adding a name makes `ensure_certs()` reissue the leaf. The CA is kept.
- **Removing a name does not trigger renewal.** The old leaf keeps the extra name until its normal renewal. This is harmless and intended.

## 4. nginx (`nginx_conf`)
- Render `    server_name {" ".join(["_", *domains()])};`.
  - No names: `server_name _;` (unchanged).
  - One name: `server_name _ agent-company.internal;`.
- `_` stays and the block stays the only catch-all server on the port, so IP and `localhost` access keep working. No `default_server` and no redirect.

## 5. Saving (`prefs.set_value` / `prefs.clear`)
`set_value("tls_domains", v)`:
1. `_coerce` validates and normalises the value. A `ValueError` becomes HTTP 400 through the existing handler at `api.py:858`, and nothing is stored.
2. Remember the previous DB row value, or that there was none.
3. Write the row and call `load()`, so `settings().tls_domains` holds the new names.
4. Call `transport.ensure_certs()`, then `transport.apply(settings().transport, force_reload=True)`.
5. If step 4 raises `ValueError`, restore the previous row (or delete it), call `load()` and re-raise. The setting is unchanged, as with `transport`.

Storing before applying is the reverse of the `transport` hook (`prefs.py:228`). It is needed because `hosts()` and `nginx_conf()` read `settings()`.

`clear("tls_domains")` deletes the row, calls `load()` and runs step 4. On failure it restores the row and re-raises.

`apply()` does nothing unless MANAGE_NGINX is on, and nothing under pytest. `ensure_certs()` writes only to `data/tls/`. The API is unchanged: the existing `PUT/DELETE /api/settings/tls_domains` (`api.py:3198`) is admin-only and audited (`setting_set`/`setting_clear`).

## 6. `/api/tls`
`info()` adds `"domains": domains()`. The existing `hosts` field (the leaf's SANs) shows the names after saving.

## 7. UI (`static/admin.js` `connectionCard`)
- New row **Domain names**: `<input id="tls-domains">` with the value `tls.domains.join(", ")` and the placeholder `agent-company.internal`.
- **Save** sends `PUT /api/settings/tls_domains {value}`.
- **Reset to .env** sends `DELETE`. It shows only when the `tls_domains` row is overridden; `viewSettings` passes that row in.
- After either action, re-render from `/api/tls` so the Server certificate line shows the new hosts. Errors go to `toast(e.message)`.
- Add a muted hint: clients must resolve the name to this server (DNS or hosts file) and trust the CA.
- `viewSettings` removes `tls_domains` from the generic settings table, as it does for `transport` (`admin.js:74`).

## 8. ReadMe.mdx
Add an "Own domain name" subsection under HTTPS/connection, documented and not automated:
- Set the names on Settings -> Connection or with `TLS_DOMAINS` in `.env`.
- Each client must resolve the name to the server, in one of two ways:
  - an A/AAAA record on the LAN DNS server or router;
  - a hosts-file line such as `192.0.2.10  agent-company.internal`:
    - Linux/macOS: `/etc/hosts` (edit with sudo).
    - Windows: `C:\Windows\System32\drivers\etc\hosts` (edit as Administrator).
- Each client must install and trust the Agent Company CA, as already described.
- Avoid `.local`, which is used by mDNS. Prefer `.internal` or `.lan`.

## 9. Tests (`tests/test_transport.py`)
All tests use a tmp DATA_DIR with MANAGE_NGINX off, and none calls systemctl or nginx.
- **`parse_domains`:** every valid case, including normalisation, de-duplication and sorting. Every invalid case from §2 raises a `ValueError` naming the input. 21 names are rejected.
- **Certificate names:** set `TLS_DOMAINS=agent-company.internal` and call `config.reset_settings()`. The leaf's DNS SANs then include `agent-company.internal` and `localhost`, and its IP SANs still include `127.0.0.1`.
- **Renewal:**
  - `ensure_certs()` returns True, then False.
  - After adding `dash.lan` it returns True again, and the CA is the same.
  - After removing a name it returns False.
- **nginx text:**
  - With the name set, `nginx_conf("https")` and `nginx_conf("http")` both contain `server_name _ agent-company.internal;`.
  - With no names, they contain `server_name _;`.
- **prefs and API:**
  - `prefs.set_value("tls_domains", "*.bad")` raises `ValueError`, and the setting keeps its previous value.
  - `set_value("tls_domains", "Dash.LAN, dash.lan agent-company.internal")` stores `agent-company.internal,dash.lan`.
  - `PUT /api/settings/tls_domains` with a bad name returns 400.
  - `/api/tls` includes `domains`.

## Files each task may change
- **backend:** `src/agent_company/config.py`, `src/agent_company/prefs.py`, `src/agent_company/transport.py`, `tests/test_transport.py`
- **ui_docs:** `src/agent_company/static/admin.js`, `ReadMe.mdx`, `docs/threat-model.md` (one line: domain names in the certificate and the nginx site, validated)
- **qa_review:** no files

---

BLOCKER: writing `.claude/specs/req-009-domain-names/spec.md` was refused, so the `file_exists` acceptance check fails until the loop saves this text there.
FINDING: The `transport` hook in `prefs.set_value` applies the mode before it stores the setting. `tls_domains` can't follow that order, because `hosts()` and `nginx_conf()` read `settings()`. §5 stores first and rolls back on failure.
FINDING: A bad `TLS_DOMAINS` value in `.env` would break startup if `hosts()` raised, so `domains()` logs a warning and ignores it.
NEXT: backend task implements §1–6 and §9; ui_docs task implements §7–8; QA checks §3's note on removed names and the rollback in §5.
MESSAGE handoff to developer: the spec text is above (meant for `.claude/specs/req-009-domain-names/spec.md`); `parse_domains` normalises, and `_coerce` stores its comma-joined result.
MESSAGE request to general-manager: please save this text as `.claude/specs/req-009-domain-names/spec.md` (the architect role can't write under `.claude/`).

```json
{"insights": [{"title": "Spec tasks need a path the architect may write", "description": "Spec tasks require .claude/specs/<slug>/spec.md, but the architect is told not to change files and writing under .claude/ is refused. The file_exists check then fails, as in the S06-FR-04 playbook entry.", "evidence": ["Write to .claude/specs/req-009-domain-names/spec.md refused as a sensitive file", "Playbook AVOID: .claude/specs/s06-fr-04-filters/spec.md does not exist"], "expected_benefit": "Spec tasks stop failing the gate for a file that was never saved", "confidence": 0.7}]}
```

PROPOSAL: Save architect text answers as the spec file :: the loop (or `specs.py`) should save the architect's text to the expected `.claude/specs/<slug>/spec.md`, or allow that path, so `file_exists` checks stop failing.
