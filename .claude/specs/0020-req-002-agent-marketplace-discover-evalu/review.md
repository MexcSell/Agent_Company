# Review (iteration 2)

## QA and security review of the iteration-2 marketplace fixes against REQ-002 (qa, failed)

## Iteration-2 marketplace fixes: QA and security review against REQ-002 (commits d6e5f17 + 88621ae)

**Verdict: FAIL.** All ten iteration-1 findings are fixed and tested; the `agents.js` syntax check is still not done. One medium gap from my previous security review is still open: a marketplace candidate can be enabled through the general recruitment approve, which skips every marketplace check. That breaks edge case 2 ("approved only with `accept_unevaluated`").

I could not run the tests. `pytest` and `node --check` both needed approval, so the 771 passed figure is the developer's report.

## Logic verification

**Case 1, normal: upload the fixture package → approve → rollback.** This works.
1. `fetch_upload` checks every zip member, then unpacks into a new `data/marketplace/<id>/src`.
2. `convert` and `validate_manifest` read text only, and `screen` runs.
3. `create_candidate` (marketplace.py:652):
   - The eval suite comes from the role only (`[role, *ROLES[role].skills]`, :664).
   - Skills start at level 0 (:642) and `model_pref=None` (:691).
   - Permissions and autonomy are set to the ceiling.
4. `qualify` puts `trial.sandbox="required"` on the eval goals. `mode_for` (sandbox.py:223) returns `required` for them, and every place that runs a goal's commands asks it: loop.py:1009, quality.py:165/214 and performance.py:226.
5. `on_qualified` stores the result on the import.
6. `approve` (:772) needs `qualified` with eval `passed`. The ceiling is intersected with the grant, and the model must be in the router catalog.
7. Provenance (source, sha, licence, eval with sample sizes, requested skills and models "not granted/applied") is committed through `agentdefs`.
8. `restore` needs `admin`, a 7–40 hex id, and a commit that appears in this file's `git log`.
9. Package code never runs: `MARKER` is asserted absent (test_marketplace.py:121).

**Case 2, edge: an unevaluated role and `sandbox=off`.** Only partly works.
- Inside `marketplace.approve`, an `unevaluated` import needs `accept_unevaluated` (:789), which is recorded in the eval, Provenance and the audit.
- `sandbox=off` for the company or the project still gives `required` for the candidate's trial, and other goals are not affected (test :551-566).
- **Gap:** the record's `Recruitment.status` is `pending`. `POST /api/recruitments/{id}/approve` (api.py:2252) leads to `recruitment.approve` (recruitment.py:395). An `actor="user:…"` passes the check there, and `_activate` (:403-404) enables the agent.
  - `accept_unevaluated`, `accept_licence` and `acknowledge` are all skipped.
  - The import stays `unevaluated`/`qualified`, and Provenance never records the decision.
  - admin.js:141 shows a plain Approve button for every pending record, including these.
  - This is unchanged since my last review. Permissions don't widen on this path (`pending_permissions=[]`), but the safeguards the requirement adds are skipped.

**Case 3, error: hostile URL or tar bomb.** This works.
- A private or mixed address: `_is_public_host` requires every resolved address to be global, otherwise `ImportRefused`, before any import record exists.
- The API records `marketplace_import_refused` as a warning.
- DNS rebinding:
  - git connects only to the checked address (`http.curloptResolve=<host>:<port>:<ip>`, :203).
  - With git older than 2.37, https imports are refused (fail-closed).
  - Redirects are off.
- Clone size:
  - The clone uses `--no-checkout --filter=blob:limit=2 MB`, with `RLIMIT_FSIZE` limiting every file it writes, the pack included (:217).
  - `_check_objects` (:232) checks with `rev-list --missing=print` and `ls-tree -l` before checkout.
  - After the clone, git calls drop `GIT_ALLOW_PROTOCOL`, so `protocol.allow=never` blocks any lazy fetch.
- Tar bomb: `_tar_scan` (:346) makes one streaming pass through a reader that stops at a byte cap, and also applies `data_filter`. The test confirms it stops after at most 4 `next()` calls and never calls `getmembers()`. The import is then `failed`, its folder is deleted, and the API audits the refusal.

**Widening by the package.**
- Permissions: the ceiling is the role template minus network, mcp and admin.
- Autonomy: clamped to G2 or G1.
- Skills: start at 0.
- Model: not applied.
- None of these can be widened from the manifest or the instructions.
- Through the generic approve (case 2), the agent keeps its candidate permissions, which are within the ceiling, so permissions are not widened there either.

**Rejection.** `reject` needs `admin`. It retires the candidate (`enabled=False`, `lifecycle=retired`) and deletes the package directory (test :513).

## Each iteration-1 finding

| # | Finding | Fix | Test |
|---|---|---|---|
| 1 | `sandbox=off` bypass | sandbox.py:223; recruitment.py:498-506 | `test_sandbox_off_setting_cannot_weaken_the_candidate_eval`; test_sandbox.py `test_a_trial_that_requires_the_sandbox_overrides_sandbox_off` |
| 2 | package chooses the eval suite | marketplace.py:664 | `test_suite_comes_from_the_role_not_the_package_skills` |
| 3 | unevaluated import approvable | :789 (bypassable, see below) | `test_unevaluated_import_needs_explicit_acceptance`, `test_qualified_needs_a_passed_eval` |
| 4 | self-granted skill levels | :642 | `test_claimed_skills_start_at_the_lowest_level` |
| 5 | package-chosen `model_pref` | :691 | `test_requested_models_are_not_applied_admin_picks_a_catalog_model` |
| 6 | DNS rebinding | :153, :203 | `test_checked_address_is_pinned_for_the_clone`, `test_private_or_mixed_address_refused`, `test_old_git_refuses_https_imports` |
| 7 | clone unbounded while running | :203, :217, :232 | `test_clone_runs_with_a_file_size_limit`, `test_clone_with_a_pack_over_the_file_size_limit_fails`, `test_oversized_file_in_repository_refused_and_removed`, `test_git_calls_after_the_clone_never_lazy_fetch`, `test_repository_over_the_total_size_refused` |
| 8 | admin checks on reject/restore, restore id and `git log` check | :817; agentdefs.py:195-207 | `test_reject_and_restore_need_an_administrator` |
| 9 | refused approve/reject not audited | api.py:2344, :2359 | `test_api_refused_approve_and_reject_are_audited`; test_api.py |
| 10 | gzip bomb | :346 | `test_tar_bomb_refused_while_streaming`, `test_tar_decompressed_bytes_are_bounded` |
| 11 | `agents.js` syntax | not checked (`node --check` refused again) | none |

The tests I read assert behaviour: statuses, which suite was chosen, the `next()` count, that no `src` folder is left, the exact `curloptResolve` string, that `MARKER` is absent. They don't only call functions.

## Checklist

- **Contract:** criteria (a), (d) and (e) are met. Criteria (b) and (c) are met through the marketplace path but can be bypassed (case 2).
- **Interfaces:** `check_url` now returns a tuple. Its callers in the code I read were updated (`fetch_url`, tests).
- **Error handling:** fails closed in every case I traced.
- **Tests:** strong. Missing: a marketplace record approved through `/api/recruitments/{id}/approve`.
- **Dependencies:** none new; only the standard-library `resource`, `bz2`, `gzip` and `lzma`.
- **Security:** see the findings.
- **Performance:** a tar is decompressed twice (scan, then extract), bounded by `MAX_BYTES`. Acceptable.
- **Docs:** the threat model and CLAUDE.md were updated.

## Findings

FINDING: src/agent_company/recruitment.py:395, src/agent_company/recruitment.py:403, src/agent_company/api.py:2252, src/agent_company/static/admin.js:141 | **medium** – an administrator approving a marketplace record through the general recruitment approve (API or the Evolution → Recruitment button) enables an unevaluated, unlicensed or flagged import without `accept_unevaluated`, `accept_licence` or `acknowledge`. The import record is never marked `approved` and Provenance doesn't record the decision | in `recruitment.approve`, raise `PermissionError("decide it on Agents → Marketplace")` when `r.source == "marketplace"` unless a keyword `via_marketplace=True` is passed (only `marketplace.approve` passes it); do the same in `recruitment.reject`; hide the generic buttons in admin.js for `source === "marketplace"`; add an API test expecting 403
FINDING: src/agent_company/static/agents.js:351 | **low** – the Marketplace tab's JavaScript has still not been syntax-checked or opened in a browser (`node --check` refused for every role) | run `node --check src/agent_company/static/agents.js` with approval, or add a node syntax check to the test suite
FINDING: src/agent_company/marketplace.py:217 | **low** – `preexec_fn` in the multithreaded API server can deadlock the child between fork and exec (Python's documentation warns against it with threads) | run the clone as `["prlimit", f"--fsize={CLONE_FSIZE}", "git", …]` or inside the systemd scope `sandbox.py` already uses

OUTCOME: REVISION_REQUIRED. Iteration 2 fixes all ten iteration-1 findings at their root, each with a test that checks behaviour. The sandbox is forced on for candidate evals, the suite comes from the role, package skills and models aren't applied, the clone is pinned to the checked address and bounded while it runs, tar files are scanned in one bounded stream, and refusals are audited. One medium security gap is left: the older recruitment approve and reject path still reaches marketplace candidates and skips the unevaluated, licence and screen checks. That leaves edge case 2 and criteria (b)/(c) open. The fix is a guard in `recruitment.approve`/`reject` plus a test.

VERDICT: FAIL

NEXT: add the `source == "marketplace"` guard in recruitment.py `approve`/`reject` with an API test, hide the generic buttons in admin.js, and run `node --check` on `agents.js`.
MESSAGE request to developer: guard `recruitment.approve`/`reject` (src/agent_company/recruitment.py:382) against `source == "marketplace"` unless called from `marketplace.approve`/`reject`, add a test that `POST /api/recruitments/{id}/approve` on a marketplace record returns 403, and hide the generic buttons in src/agent_company/static/admin.js:141.
MESSAGE fact to security: DNS pinning, the bounded clone and the streamed tar scan are in marketplace.py (:153, :203, :217, :232, :346) with tests; the open item is the generic recruitment approve bypass.

PROPOSAL: node syntax check of static/*.js in pytest :: no role is allowed to run `node --check`, so dashboard syntax errors pass every gate.

## Security review of 'Marketplace candidate flow: sandbox always on, role-only eval suite, 'unevaluated' status, no self-granted skills or models, admin checks, audited refusals' (qa, done)

## Security review of `candidate_flow_fixes` (commit d6e5f17)

**Verdict: FAIL.** Six of the seven fixes are correct. Fix 3 can be bypassed: the existing approve button in Evolution → Recruitment enables a marketplace candidate without going through the marketplace checks. The fix is small.

I could not run the tests. The 759 passed / 77 passed counts are the developer's report.

## Logic verification

**1. `sandbox.mode_for` (sandbox.py:223) and `recruitment.qualify` (:497-506): fixed at the root.**
- Normal case: `qualify` builds `trial={"trainee_id", "qualify", "sandbox": "required"}`. `evaluation.run_suite(trial=…)` (evaluation.py:193) passes it to `loop` as `Goal.trial = dict(trial)` (loop.py:127).
- Every place that runs a goal's commands asks `sandbox.mode_for(goal)`: loop.py:1009, quality.py:165/214 (and so :283/:304), and performance.py:226. Each now gets `required`, ahead of the company or project setting.
- Edge case: `sandbox=off` for the company or the project still gives `required`.
- Error case: with bubblewrap missing, `available()` is false and `_finish(…, error)` retires the candidate.
- The extra `mode_for(Goal(trial=trial)) == "off"` check can never be true, because the trial already forces `required`. It is dead code but harmless.

**2. The suite comes from the role (marketplace.py:523-524): fixed.**
- Normal case: `qualification_suite([role, *ROLES[role].skills])`.
- Edge case: a package claiming `markdown` cannot reach an easier docs suite.
- Error case: no suite for the role gives the `unevaluated` path below.

**3. `unevaluated` status (:557, :645-649): fixed inside `marketplace.approve`, but bypassable.**
- Inside `marketplace.approve`:
  - Normal case: only `qualified` with `eval.status == "passed"` is accepted, or `unevaluated` with `accept_unevaluated`, which is recorded in the eval and in Provenance.
  - Edge case: a `qualified` import whose eval isn't `passed` is refused.
- The bypass: the record still has `status="pending"` (`_ask_or_approve`), so `POST /api/recruitments/{id}/approve` reaches `recruitment.approve`. There, `actor="user:<admin>"` passes the check at :395 and `_activate` enables the agent. None of the marketplace checks run: `accept_unevaluated`, `accept_licence` and `acknowledge` are skipped. The import record stays `unevaluated`/`qualified`, nothing marks it `approved`, and Provenance is not rewritten.
- The Evolution → Recruitment list (admin.js:133-141) shows every pending record with a plain **Approve** button, so this path is one click away.
- Permissions don't widen on this path: marketplace records have `pending_permissions=[]`, so `grant=True` adds nothing.

**4. Skills start at 0 (:501, :551): fixed.** `registry._rank` scores level 0 the same as a missing skill. The claimed skills are kept only in `requested_skills` and in Provenance.

**5. Models (:550, :504): fixed.** `model_pref=None`, so the role route applies. The administrator's `model` must be in the router catalog, otherwise `ValueError` gives 400; that raise happens before any field changes and is audited as refused.

**6. Admin checks: fixed.**
- `reject` and `restore` refuse without `admin`.
- `restore` uses the `{7,40}` regex and accepts only commits in the file's own `git log`.
- The commit id is checked against a hex regex and the file name comes from `path_for`, so neither can carry an option or a path.

**7. Refused approve and reject are audited: fixed.** api.py:2344 and :2359 record them at severity warning before re-raising.

## Checklist

- **Contract:** items 1, 2 and 4-7 are met; item 3 has the bypass above.
- **Interfaces:** new keyword-only arguments with safe defaults; the API body gains two fields.
- **Error handling:** good.
- **Tests:** a named test for each item (by the developer's report). None covers approve through `/api/recruitments/{id}/approve` for a marketplace record.
- **Dependencies:** none.
- **Security:**
  - Within this task's scope, the problem is the bypass.
  - Outside this task's scope, still open and owned by `fetch_hardening`: DNS rebinding, the clone's size while it runs, the tar gzip bomb, and `node --check` of `agents.js`.
- **Performance:** fine.
- **Docs:** `spec.md` §12 is stale ("`model_pref` from the catalog only"); the developer's BLOCKER covers it.

## Findings

FINDING: src/agent_company/recruitment.py:395, src/agent_company/api.py:2252, src/agent_company/static/admin.js:141 | **medium** – an administrator can approve a marketplace candidate through the generic recruitment approve (API or the Evolution → Recruitment "Approve" button). `_activate` then enables an unevaluated, unlicensed or flagged import without `accept_unevaluated`, `accept_licence` or `acknowledge`. The import record never becomes `approved`, and Provenance does not record the decision | in `recruitment.approve`, raise `PermissionError("approve it on Agents → Marketplace")` when `r.source == "marketplace"` unless a new keyword `via_marketplace=True` is passed (only `marketplace.approve` passes it); hide the button in admin.js for `source === "marketplace"`; add a test that `POST /api/recruitments/{id}/approve` on a marketplace record returns 403
FINDING: src/agent_company/api.py:2261, src/agent_company/recruitment.py:419 | **low** – rejecting through the generic recruitment reject retires the candidate but leaves the import `unevaluated`/`qualified` and keeps `data/marketplace/<id>/src` | refuse marketplace records there as well (same guard), so every decision goes through `marketplace.reject`
FINDING: src/agent_company/recruitment.py:505 | **low** – `mode_for(Goal(trial=trial)) == "off"` can never be true, because the trial already forces `required`; it reads as a second safeguard but isn't one | drop it, or check `policies.effective("sandbox", None)` and log that the setting was overridden

OUTCOME: REVISION_REQUIRED. The sandbox fix, the role-only suite, skills at level 0, the administrator's choice of model, the admin checks on reject and restore, and audited refusals are correct and fix the root causes. The new `unevaluated`, licence and screen safeguards live only in `marketplace.approve`, and the older recruitment approve endpoint and its admin-UI button skip them. A one-line guard in `recruitment.approve` plus a test closes this.

VERDICT: FAIL

NEXT: add the `source == "marketplace"` guard to `recruitment.approve` and `recruitment.reject` with an API test, hide the buttons in admin.js, then re-run `tests/test_marketplace.py tests/test_recruitment.py tests/test_api.py`.
NEXT: `fetch_hardening` still owns DNS pinning, the clone's size bound, the tar bomb and `node --check src/agent_company/static/agents.js`.
MESSAGE request to developer: guard `recruitment.approve`/`reject` (src/agent_company/recruitment.py:382) against `source == "marketplace"` unless called from `marketplace.approve`/`reject`, and hide the generic buttons in src/agent_company/static/admin.js:141.

```json
{"insights": [{"title": "New safeguards must cover every older path to the same state change", "description": "Checks added to marketplace.approve (unevaluated, licence, screen acknowledgement) were skipped because the generic recruitment.approve endpoint and its admin button can still activate the same record. When a new flow adds conditions to an existing state change, guard the shared lower-level function, not only the new entry point.", "evidence": ["src/agent_company/recruitment.py:395-404", "src/agent_company/api.py:2252", "src/agent_company/static/admin.js:133-141"], "expected_benefit": "Prevents approval bypasses in future features built on recruitment", "confidence": 0.75}]}
```

## Security review of 'Marketplace fetch hardening: DNS-pinned clone, bounded clone size, streaming gzip-bomb guard, threat model, agents.js syntax check' (qa, done)

## Logic verification

**`_is_public_host` / `check_url` / `clone_command` (DNS pinning)**
- **Normal case:** `https://example.com/x.git` resolves to a set of addresses. Every one must be `is_global`; the first is returned and becomes `(host, port, ip)`. That pin goes into `clone_command` as `-c http.curloptResolve=example.com:443:<ip>`, and IPv6 addresses are bracketed. Because `_git_env` passes on no `*_proxy` variables, curl connects directly to the pinned address. This fixes the root cause (git resolving the name a second time). It does not just hide it.
- **Edge case:** a host with mixed addresses (one public, one private) returns `None` and is refused before any import record is written (`test_private_or_mixed_address_refused`). An invalid port such as `:99999` raises `ValueError`, which becomes `ImportRefused`.
- **Error case:** git older than 2.37, or a git whose version can't be read (`(0,)`), refuses https imports before any subprocess runs (`test_old_git_refuses_https_imports`). `clone_command` refuses an https URL that has no pin.
- **Gap (see FINDING 1):** the pin only works if curl's lookup key equals the pinned `parts.hostname` string. If they differ, curl silently resolves through DNS again, so the pin fails open.

**`clone_command` + `_limit_fsize` + `_check_objects` + `_git` (clone size)**
- **Normal case:** the clone runs with `--no-checkout --filter=blob:limit=MAX_FILE_BYTES` under `RLIMIT_FSIZE`. Then:
  - `rev-list --missing=print` finds no `?` lines, so no blob was filtered out.
  - `ls-tree -r -l -z` adds up blob sizes and checks each file and the total against `MAX_FILE_BYTES`, `MAX_BYTES` and `MAX_FILES`.
  - Checkout runs only after both checks.
- **Edge case:** a server that ignores the filter still has the blob sizes read by `ls-tree` before checkout (the parametrized test covers both cases). Submodule `commit` entries are skipped and never fetched.
- **Error case:** a pack over `CLONE_FSIZE` gets SIGXFSZ, git exits non-zero, the import is marked `failed` and its directory is removed (`test_clone_with_a_pack_over_the_file_size_limit_fails`).
- **Lazy fetch:** later calls drop `GIT_ALLOW_PROTOCOL` while keeping `protocol.allow=never`, so even a git that ignores `GIT_NO_LAZY_FETCH` cannot fetch the missing blob. The test shows this directly (`cat-file` fails with "not allowed").
- Total disk use is bounded to roughly pack + idx, each under `CLONE_FSIZE`, plus the 60 s timeout. Dumb-HTTP multi-pack fetches are not possible because `--depth` is unsupported on dumb transports. Root cause fixed.

**`_Bounded` / `_decompressed` / `_tar_scan` / `_tar_members` (decompression bomb)**
- **Normal case:** the archive is read in one pass in stream mode (`r|`) through a reader capped at `limit + header room`. For each member it checks the path, the file type and `tarfile.data_filter`, then adds up `m.size`. It stops at the first member that crosses a limit. `_tar_members` then re-reads the same, already-validated bytes in random-access mode with `next()` and never calls `getmembers()`.
- **Edge case:** a huge pax header is not declared in `m.size` but is cut off by `_Bounded` (`test_tar_decompressed_bytes_are_bounded`). The scan and the `r:*` path recognise the same compressions (gz, bz2, xz); legacy `.lzma` data is fed to the scan raw, which rejects it as an invalid header.
- **Error case:** corrupt compressed data (FINDING 2) raises `zlib.error` or `lzma.LZMAError`. Neither is an `OSError`, so neither is caught at `marketplace.py:417`. Root cause of the bomb: fixed.

**agents.js:** the only change is icon names and two inline buttons. The `git`, `x`, `check` and `upload` icons all exist (`app.js:23`, `:26`, `:28`). The template literals are balanced on reading. `node --check` needed approval in this session as well, so the syntax check is still not run.

## Checklist
- **Contract:** all four parts are met: DNS pin, bounded clone, streaming bomb guard, threat-model rows. The `node --check` item is still not run (blocked by permissions).
- **Interfaces:** `check_url` now returns a tuple, and the only caller (`fetch_url`) is updated. `_is_public_host` now returns `str | None` instead of `bool`; I found no other callers. `clone_command(url, dest, pin=None)` stays compatible for `file:` URLs.
- **Error handling:** good on the clone path (`_git` raises `ImportRefused`, and timeouts are handled). The gap in the upload path is FINDING 2.
- **Tests:** 15+ new tests that are offline and specific (the bomb test asserts at most 4 `next()` calls and no `getmembers`). There is no test that curl actually honours the pin (offline limitation) and none for host-form canonicalisation.
- **Dependencies:** standard library only (`resource`, `bz2`, `lzma`). Justified.
- **Security:**
  - No secrets were added, and the URL is passed after `--`.
  - The env is allow-listed and carries no proxy variables, so the pin can't be bypassed through a proxy.
  - `-c` settings are not persisted into the clone's config.
  - `core.symlinks=false` is set at checkout.
- **Performance:** one extra decompression pass per tar upload, bounded. Acceptable.
- **Docs:**
  - `docs/threat-model.md` has new rows for DNS rebinding, clone size, decompression bomb and self-grading, each mapped to existing tests.
  - The module docstring is updated.

## Findings
FINDING: src/agent_company/marketplace.py:174 (and :204 `_resolve_entry`) | The pin fails open. The `http.curloptResolve` key is the raw `parts.hostname`, so any host form that curl rewrites before its lookup misses the entry, and curl falls back to DNS (rebinding/SSRF again). Two forms an attacker who controls the domain can choose: a trailing dot (`https://rebind.example./`; whether curl strips the dot differs by version) and a non-ASCII IDN host (Python keeps the Unicode form, while curl with libidn2 looks up the punycode form). I reasoned this from how curl handles host names and did not run it. | Canonicalise before resolving and pinning: refuse a trailing dot and non-ASCII hosts, or convert with `host.encode("idna").decode()`, and require `^[a-z0-9.-]+$` (plus IP literals). Add a test for `https://x.example./` and `https://bücher.example/`.

FINDING: src/agent_company/marketplace.py:417 (raised from :338 `_decompressed`/`_Bounded`) | In a corrupt `.tar.gz` or `.tar.xz` upload, `GzipFile` raises `zlib.error` and `LZMAFile` raises `lzma.LZMAError` from inside `_tar_scan`. Neither is in the except tuple, so `_fail` is never called: the import record stays in its in-progress status, the empty `data/marketplace/<id>/src` directory stays behind, and the admin API returns a 500. The admin sees a crash instead of "unreadable archive". | Add `zlib.error` and `lzma.LZMAError` to the except at :417, or catch them in `_tar_scan` and re-raise as `ImportRefused`. Add a test that sends a truncated or bit-flipped `.tar.gz`.

FINDING: src/agent_company/marketplace.py:255 (`subprocess.run(..., preexec_fn=_limit_fsize)`) | `preexec_fn` is documented as unsafe in a multithreaded process: the server runs `KpiThread`, `McpThread`, `ReportThread` and others, and a fork while another thread holds the allocator or GIL state can deadlock an API worker. That risk is low here because `setrlimit` is a single syscall and `resource` is imported at module level. | Prefer `["prlimit", f"--fsize={CLONE_FSIZE}", "--", "git", …]` (util-linux) or the existing systemd-scope wrapper used by `sandbox.py`. Then drop `preexec_fn`.

FINDING: src/agent_company/static/agents.js:353 | `node --check` and opening the Marketplace tab in a browser were still not done (the call needed approval). The diff looks valid on reading and its icons exist, but nobody has run it. | Run `node --check src/agent_company/static/agents.js` and open Agents → Marketplace once (headless playwright on the scratch server).

None of these undo the root-cause fixes. FINDING 1 is a hardening gap in the new pin, with a medium-to-low impact: it needs an administrator to import the attacker's URL in an unusual host form. The others are low.

NEXT: Canonicalise or refuse trailing-dot and IDN hosts before pinning, and add tests for both.
NEXT: Catch `zlib.error` and `lzma.LZMAError` in `fetch_upload`, and add a test with a corrupt `.tar.gz`.
NEXT: Run `node --check` on agents.js and open the Marketplace tab once.

MESSAGE recommendation to developer: in `marketplace.check_url`, canonicalise `parts.hostname` (refuse a trailing dot and non-ASCII, or IDNA-encode it) so the `http.curloptResolve` key always matches curl's lookup; add `zlib.error` and `lzma.LZMAError` to the except at `marketplace.py:417`.

```json
{"insights": [{"title": "curl CURLOPT_RESOLVE pins fail open on a host-string mismatch", "description": "A pin set with http.curloptResolve only applies when curl's lookup key exactly matches the pinned host; otherwise curl resolves through DNS without any error. A DNS-rebinding defence built on it must canonicalise the host (IDNA, trailing dot, case) or refuse unusual forms.", "evidence": ["src/agent_company/marketplace.py:174", "src/agent_company/marketplace.py:204"], "expected_benefit": "Prevents SSRF bypasses of the clone pin", "confidence": 0.6}]}
```

OUTCOME: APPROVED_WITH_COMMENTS
VERDICT: PASS
