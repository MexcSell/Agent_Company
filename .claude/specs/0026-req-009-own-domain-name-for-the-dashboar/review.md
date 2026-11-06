# Review (iteration 3)

## QA review of REQ-009 against the spec (qa, done)

## Logic verification

I couldn't run anything myself: the permission layer refused both `uv run pytest …` and `node --check …` with "This command requires approval". The test evidence below therefore comes from the verify_fix session, which ran `uv run pytest -q tests/test_transport.py tests/test_threats.py tests/test_api.py -x` and got `29 passed in 12.45s`. I confirmed the new tests exist: `tests/test_transport.py` has 14 test functions, 6 of them new (lines 116-220). No test in `tests/` syntax-checks the static JS.

**`transport.parse_domains`** (`transport.py:171-210`)
- **Normal case:** `"agent-company.internal"` is split on `[\s,]+`, lowercased, and has any trailing dot removed. The IP check fails, so the name is kept. It matches `[a-z0-9.-]+`, is at most 253 characters, and each label is at most 63 characters and matches `_LABEL`. The result is `["agent-company.internal"]`.
- **Edge cases:**
  - `"a.lan\nB.lan.  a.lan,,"` gives `["a.lan","b.lan"]`. Mixed case and the trailing dot are normalised, and duplicates are removed after normalisation, in input order. Empty parts are skipped.
  - A 63-character label is accepted and a 64-character label is refused.
- **Error case:** `"a.lan\nserver {"` splits into `a.lan`, `server`, `{`. `{` fails `[a-z0-9.-]+` and raises `ValueError("'{' is not a domain name…")`. `*.x`, `x:443`, `http://x`, `1.2.3.4`, `::1`, `x;y` and `a..b` are refused too (`tests/test_transport.py:136-140`).
- **Root cause:** this fixes it. Validation is an allow-list, which closes the nginx-injection path at the input. `nginx_conf` runs it again (`transport.py:347`) as defence in depth.

**`config._domains` / `load_settings`** (`config.py:145-154,188`)
- **Normal case:** `TLS_DOMAINS="Good.lan, other.lan"` gives `["good.lan","other.lan"]`.
- **Error case:** an invalid value is ignored with a warning, so startup isn't blocked (`test_invalid_env_domains_are_ignored`).

**`prefs._coerce` / `set_value` / `_apply_domains` / `clear`** (`prefs.py:137-140,240-279,358-365`)
- **Normal case:**
  1. PUT `/api/settings/tls_domains` calls `set_value`.
  2. `_coerce` → `parse_domains` returns the list.
  3. `_stored` keeps the previous row, the new row is written, and `load()` → `set_overrides` updates the running settings.
  4. `_apply_domains` → `transport.apply(mode, force_reload=True)`.
  5. Inside `apply`: `ensure_certs()` → `hosts()` now includes the domain (`transport.py:217`). `_leaf_ok` compares SAN sets for equality (`:265`), sees a difference and reissues the leaf. Then `nginx_conf`, `nginx -t` and the reload run.
  6. The API audits `setting_set` (`api.py:3202`).
- **Edge case (clearing):**
  1. The empty field makes admin.js send DELETE, so `clear` deletes the row.
  2. `load()` makes the setting fall back to `.env` `TLS_DOMAINS` (`[]` by default).
  3. A forced apply follows. The leaf's SAN set no longer matches, so it is reissued, and `server_name _;` is written.
  4. This is asserted at `tests/test_transport.py:184-187`.
- **Error case:** `_coerce` raises before `_stored` or any session write. The API's ValueError handler returns 400, nothing is stored and `apply` is never called. This is asserted by `test_bad_domains_store_nothing` (`applied == []`, not overridden) and `test_domains_api` (400 for `*.lan`).
- **nginx refuses a valid value:** the previous row is restored and the error is raised again (`test_failed_apply_restores_domains`). This covers both set and clear.

**`hosts` / `_san` / `_leaf_ok` / `ensure_certs`**
- Domains become `DNSName` entries. localhost, 127.0.0.1, ::1, the hostname and the detected IPs stay in the list, so IP access keeps working (the test asserts `127.0.0.1` is in the leaf's IP SANs).
- Changing from `==` to the old `<=` check means a removed name now renews the leaf too. This is tested: the second list drops `agent-company.internal` from the leaf (`:179-181`).
- `ensure_certs()` returns False when nothing changed, so there is no needless reissue.

**`nginx_conf`** (`transport.py:342-386`)
- `server_name agent-company.internal _;` appears in both https and http modes, with `default_server` on both listen lines. Requests by IP address, or with any other Host header, still match.
- With no names it gives `server_name _;` (`:104`, `:187`).

**`admin.js`** (`:26-29`, `:78`, `:107-114`)
- The value is shown through `esc(...)` in the `value="…"` attribute, and `esc` escapes `& < > " '` (`app.js:5`).
- Save sends `PUT {value}` when the field has text and `DELETE` when it is empty. `api()` serialises the body as JSON (`app.js:126`). Errors show as a toast.
- `tls_domains` is filtered out of the generic settings table, so it isn't edited in two places.
- I read the lines by hand: template literals, braces and the try/catch are balanced, and every helper used (`$`, `api`, `toast`, `esc`, `viewSettings`) exists. **`node --check` has not run in any session**, so this is a manual check only.

**Documentation and threat model**
- **ReadMe** (`ReadMe.mdx:680-710`):
  - Covers the `/etc/hosts` and `C:\Windows\System32\drivers\etc\hosts` hosts-file entries and the LAN DNS A record.
  - Says plainly: "The server does not do this for you: DNS setup is not automated."
  - Covers installing the CA, and that access by IP and by localhost keeps working.
- **Threat model** (`docs/threat-model.md:49`) has a row for the nginx-config and certificate injection threat, its control (`parse_domains` allow-list, re-run in `nginx_conf`, restore on refusal, audit) and three tests that exist.

## Checklist

| Item | Result |
|---|---|
| Contract (a)-(h) | (a) ✓, (b) ✓, (c) ✓, (d) ✓, (e) ✓, (f) ✓ (CA trust is described only briefly; the per-OS steps are on the Connection card), (g) ✓ (two layers: the `applied` fixture replaces `transport.apply` at `tests/test_transport.py:118-125`, and `apply` returns early under `PYTEST_CURRENT_TEST` at `transport.py:438`; certs go to the per-test temp `DATA_DIR`), (h) ✓ |
| Interfaces | `apply(mode, force_reload=False)` is unchanged; `info()` adds `domains`. Compatible. |
| Error handling | ValueError gives 400 with nothing stored. An nginx refusal restores the old value. A non-ValueError from `apply` (e.g. OSError) leaves the new value stored (minor, see finding 4). |
| Edge cases | Covered as traced above. Two gaps: all-numeric names like `1.2.3` pass, and clearing reverts to the `.env` names instead of none (findings 3 and 5). |
| Tests | Sufficient for the cert names, the nginx text, validation, restore, the API and the env value. No JS syntax test. |
| Dependencies | None added (`re` only). |
| Security | Allow-list plus re-validation, the admin-only audited endpoint, and escaped output. |
| Performance | Negligible. The leaf is reissued only when the SAN set changes. |
| Docs | ReadMe and threat model are updated. The ADR and spec are stale (findings 1 and 2). |

## Findings

FINDING: docs/adr/0025-where-the-domain-names-are-stored.md:45 | The Limitations line says "A removed name stays in the certificate until the next renewal", and the evidence line at :37 says "not a subset". Both are stale: `_leaf_ok` now uses `==` (`transport.py:265`), and a removed name renews the leaf at once, which the tests check (`tests/test_transport.py:179-187`). Future readers are misled. | Update the ADR's Limitations and evidence to say that a removed name renews the certificate immediately.

FINDING: .claude/specs/req-009-domain-names/spec.md:10,19 | The spec says `SCHEMA` type `str`, and a sorted, comma-joined stored value. The code stores a `list` in input order (`prefs.py:32`, `transport.py:205-206`). The acceptance criteria don't require sorting, but the spec and code disagree. | Change the spec to the list-in-input-order form, or sort in `parse_domains`.

FINDING: src/agent_company/static/admin.js:29,108 | The hint says "Leave the field empty to remove all names", but DELETE only removes the DB override. When `.env` sets `TLS_DOMAINS`, the `.env` names come back (`prefs.clear` → `load()`), so the admin doesn't get what the hint promises. | Reword the hint to "…returns to the .env value (TLS_DOMAINS), usually none", or send `PUT {value: ""}` to store an empty list.

FINDING: src/agent_company/prefs.py:268 | `_apply_domains` restores the previous value only on `ValueError`. An `OSError` or `PermissionError` while writing the nginx site leaves the new names stored while nginx still has the old site, until the next restart. | Catch `Exception` for the restore, then raise again.

FINDING: src/agent_company/transport.py:194-203 | All-numeric names such as `1.2.3` or `10.1` pass, because `ipaddress` refuses them and every label matches `_LABEL`. Browsers read them as legacy IPv4 forms, so the SAN entry never matches. This is harmless for injection. | Refuse a name whose last label is all digits (RFC 3696 §2).

FINDING: src/agent_company/static/admin.js:26-29,107-114 | `node --check` has never run in any session; it was refused for the developer in both iterations and for QA now. No test in `tests/` syntax-checks static JS, so the parse is verified only by hand. | Let the gate's static stage run `node --check src/agent_company/static/admin.js`, or allow-list it for agent sessions.

None of these findings blocks the spec: every acceptance criterion (a)-(h) is met in the code and covered by tests that passed in the verify_fix session.

BLOCKER: The permission layer refused `node --check src/agent_company/static/admin.js` and `uv run pytest` in this QA session.
NEXT: The gate runs `node --check src/agent_company/static/admin.js`. On failure, fix only the reported line in `admin.js:26-29` or `:107-114`.
NEXT: In a narrow docs-only follow-up, fix the stale ADR 0025 Limitations line and the spec §1/§2 normalisation text.
MESSAGE done to developer: I read `admin.js:26-29,107-114` by hand and found no syntax problem, but `node --check` was refused in my session too, so only the gate can confirm it.

PROPOSAL: Allow `node --check src/agent_company/static/*.js` for agent sessions :: the JS syntax check has been unverifiable by developer and QA across three iterations of goal 26.

```json
{"insights": [{"title": "QA sessions are also refused pytest and node", "description": "In goal 26 iteration 3 the QA session was refused both `uv run pytest` and `node --check`, so QA has to rely on the developer's recorded test run plus a manual reading of the code.", "evidence": ["QA iteration 3: 'This command requires approval' for pytest and node --check", "verify_fix iteration 3: node --check refused"], "expected_benefit": "Granting read-only test commands to QA would let reviews cite their own runs instead of other sessions' runs.", "confidence": 0.7}]}
```

OUTCOME: APPROVED_WITH_COMMENTS

VERDICT: PASS
