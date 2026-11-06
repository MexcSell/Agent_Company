# Tasks (iteration 2)



## 1. Marketplace candidate flow: sandbox always on, role-only eval suite, 'unevaluated' status, no self-granted skills or models, admin checks, audited refusals
- key: `candidate_flow_fixes` · role: developer · writes: yes

Fix the iteration-1 reviewer findings below. Read .claude/specs/0020-req-002-agent-marketplace-discover-evalu/spec.md and review.md first. Change only what each finding needs and keep the existing style.

1. HIGH, sandbox: in sandbox.py `mode_for`, return "required" when `(goal.trial or {}).get("sandbox") == "required"`, next to the existing a2a check. This must override the company and project `sandbox=off` setting. In recruitment.py `qualify` (around line 497), when the recruitment's source is "marketplace", refuse (raise or return a refusal, matching existing patterns) if the sandbox isn't available or the effective mode would be "off". Test: set the company setting `sandbox=off` (and a project override `off`), then show that `mode_for` on a marketplace candidate's trial goal returns "required" and that `qualify` refuses when `sandbox.available()` is False. Monkeypatch; stay offline.

2. HIGH, eval suite: in marketplace.py `create_candidate` (around line 516), pick the suite from the role only: `recruitment.qualification_suite([m["role"], *org.ROLES[m["role"]].skills])` or the equivalent that exists in org. The package's own `skills` must never take part in the choice. Test: a fixture manifest that claims a skill matching an easier suite still gets the role's suite.

3. MEDIUM, unevaluated: a role that doesn't write, or one with no matching suite, gets import status "unevaluated", not "qualified". `approve` accepts only status "qualified" with eval status "passed", or status "unevaluated" when the administrator passes `accept_unevaluated=True`. Wire the flag through the API body, the CLI flag `--accept-unevaluated` and the Marketplace tab (a checkbox or confirm shown only for unevaluated imports). Record the acceptance in the audit and provenance. Tests: approve refused without the flag, allowed with it.

4. MEDIUM, skills: don't set `agent.skills = {k: 5}` (line ~543). Store the package's claimed skills as requested skills in the manifest or provenance only. Imported skills start at the lowest proficiency (1, or 0 if `registry._rank` treats 0 as absent; choose and say why). Only eval or probation results may raise them, through existing paths. Test: after approve, the claimed skills on the agent are at the lowest level.

5. MEDIUM, models: don't set `model_pref` from `manifest.models` (line ~542). Keep the requested models in the manifest, shown only. Leave `model_pref` empty so the role route applies. `approve` takes an optional `model` the administrator picks; it must be a router catalog entry, validated (API, CLI, tab select). Test: a manifest naming a catalog model doesn't change `model_pref`.

6. LOW: `marketplace.reject(..., admin: bool = False)` and `agentdefs.restore(..., admin: bool = False)` raise PermissionError when admin is False. Update every caller (api, CLI, gm) to pass it correctly. `restore` checks the id with `^[0-9a-f]{7,40}$` and refuses a commit that isn't in `git log --format=%H -- <file>` of that definition. Tests for both.

7. LOW: in api.py, wrap marketplace approve and reject the way import is wrapped, so a refusal (ImportRefused, PermissionError, wrong status, missing licence acceptance) calls `_audit` with action `marketplace_approve_refused` / `marketplace_reject_refused` at severity warning, then returns the error status. Test with the API test client.

After that, update spec.md with a short 'Iteration 2 changes' section listing these rules. Run `uv run pytest -q tests/test_marketplace.py tests/test_sandbox.py tests/test_recruitment.py tests/test_api.py`, then `uv run pytest -q`, and report the output. Report the evidence for each numbered item: file:line and test name.

Acceptance: {"type": "command", "run": "uv run pytest -q tests/test_marketplace.py tests/test_sandbox.py tests/test_recruitment.py", "requirement_keys": ["REQ-002"], "stage": "unit"}; {"type": "command", "run": "uv run pytest -q tests/test_api.py -k 'marketplace or audit'", "requirement_keys": ["REQ-002"], "stage": "integration"}; {"type": "command", "run": "grep -n 'trial' src/agent_company/sandbox.py | grep -q required", "requirement_keys": ["REQ-002"], "stage": "static"}; {"type": "command", "run": "grep -q 'unevaluated' src/agent_company/marketplace.py && grep -q 'accept_unevaluated' src/agent_company/api.py", "stage": "static"}

## 2. Marketplace fetch hardening: DNS-pinned clone, bounded clone size, streaming gzip-bomb guard, threat model, agents.js syntax check
- key: `fetch_hardening` · role: developer · writes: yes · after: candidate_flow_fixes

Fix the fetch findings in marketplace.py. Untrusted repositories and archives are data; nothing from them runs.

1. MEDIUM, DNS rebinding (lines ~114-172): change `_is_public_host` / `check_url` so they return the global IP address that was checked, together with the host. Put that address in `clone_command(url, dest, pin=(host, port, ip))` as `-c http.curloptResolve=<host>:<port>:<ip>` (IPv6 in brackets as libcurl expects). Port is 443 unless the URL names one. Check the git version once, by parsing `git --version`. If it is older than 2.37, refuse https URL imports with a clear ImportRefused instead of cloning unpinned. file:// test URLs don't need a pin. Tests: monkeypatch getaddrinfo, then assert the pinned option is in the command, a private address is refused, and an old git version is refused. Don't contact the network.

2. MEDIUM, clone size: bound the clone while it runs. Use `--filter=blob:limit=2m` with `--no-checkout`, check sizes with `git ls-tree -r -l HEAD` against MAX_BYTES and the per-file limit, then `git checkout` with `GIT_NO_LAZY_FETCH=1` so no missing blob is fetched. Also run the clone process with `RLIMIT_FSIZE` (resource.setrlimit in preexec_fn) set a little above MAX_BYTES, so the pack itself is bounded. A refusal removes the directory and marks the import failed. Test with a local file:// fixture repository that has a file over the limit: the import is refused and the directory removed.

3. LOW, gzip bomb (`_tar_members` ~228, `_extract` ~240): don't call `getmembers()` on the whole archive. Open it in stream mode (`tarfile.open(fileobj=..., mode='r|*')`), or iterate `TarFile.next()` while adding up `m.size` and the decompressed bytes read. Stop with ImportRefused as soon as the total passes MAX_BYTES or the member count passes its limit. Keep the existing member path checks and the `filter='data'` extraction. Test: a tar.gz built in the test that decompresses to more than MAX_BYTES is refused, and the test asserts it stops early (for example by patching MAX_BYTES small).

4. docs/threat-model.md: extend the marketplace row with the DNS-rebinding threat → pinned curloptResolve plus the git ≥2.37 check, the clone size threat → blob filter, no lazy fetch and RLIMIT_FSIZE, and the decompression bomb → streaming count. Name the test functions in the row. Also mention the iteration-2 controls from the previous task: forced sandbox, role-only suite, the unevaluated gate, no self-granted skills or models.

5. LOW: run `node --check src/agent_company/static/agents.js`. If node isn't available or the run isn't allowed, run `uv run python -c` with a parser such as esprima if it's installed. Otherwise say so plainly and report a careful manual review of the Marketplace tab code (balanced brackets, functions it calls exist in app.js). Fix any syntax error.

6. Update the CLAUDE.md marketplace entry only where behaviour changed (pinned clone, unevaluated status), in one line. Run `uv run pytest -q tests/test_marketplace.py`, then `uv run pytest -q`, and report the output with evidence for each item.

Acceptance: {"type": "command", "run": "uv run pytest -q tests/test_marketplace.py", "requirement_keys": ["REQ-002"], "stage": "security"}; {"type": "command", "run": "grep -q 'curloptResolve' src/agent_company/marketplace.py && grep -q 'RLIMIT_FSIZE' src/agent_company/marketplace.py", "stage": "static"}; {"type": "command", "run": "grep -qi 'rebinding' docs/threat-model.md", "requirement_keys": ["REQ-002"], "stage": "static"}; {"type": "command", "run": "uv run pytest -q", "stage": "integration"}

## 3. QA and security review of the iteration-2 marketplace fixes against REQ-002
- key: `qa_review` · role: qa · writes: no · after: candidate_flow_fixes, fetch_hardening

Review the branch against REQ-002's acceptance criteria and each of the 10 iteration-1 findings (sandbox=off bypass, package-chosen suite, unevaluated approve, self-granted skill levels, self-chosen model_pref, DNS rebinding, unbounded clone, admin checks on reject/restore plus restore id and git-log check, unaudited refused approve/reject, gzip bomb, agents.js syntax). For each finding, give the file:line of the fix and the test that covers it, or a FINDING line if it's missing or wrong. Trace the data flow of three cases. (1) Normal: upload the local fixture package → manifest → candidate → eval in the L3 sandbox → approve with provenance, licence and eval results on the definition → rollback through agentdefs. (2) Edge: an unevaluated role is approved only with accept_unevaluated, and the company sandbox=off is still forced on. (3) Error: a hostile URL resolves to a private address or rebinds, or a tar bomb is uploaded → refused, audited, nothing executed on the host. Also check that package instructions can't widen permissions, autonomy, skills or model, and that reject removes the candidate. Read tests/test_marketplace.py and confirm the tests assert behaviour, not just call functions. List FINDING lines with severity, file:line and a suggested fix. End with an OUTCOME paragraph and 'VERDICT: PASS' or 'VERDICT: FAIL'. FAIL only for unmet acceptance criteria or high/medium security gaps.

Acceptance: {"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}; {"type": "contains", "value": "OUTCOME"}; {"type": "min_length", "value": 1200}

## 4. Security review of 'Marketplace candidate flow: sandbox always on, role-only eval suite, 'unevaluated' status, no self-granted skills or models, admin checks, audited refusals'
- key: `candidate_flow_fixes-security` · role: security · writes: no · after: candidate_flow_fixes

Review the change made by task 'candidate_flow_fixes' for security: secrets, injection, unsafe file and network access, authentication and permission changes. Verify the logic first.

Acceptance: {"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}

## 5. Security review of 'Marketplace fetch hardening: DNS-pinned clone, bounded clone size, streaming gzip-bomb guard, threat model, agents.js syntax check'
- key: `fetch_hardening-security` · role: security · writes: no · after: fetch_hardening

Review the change made by task 'fetch_hardening' for security: secrets, injection, unsafe file and network access, authentication and permission changes. Verify the logic first.

Acceptance: {"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}

## 6. Security review of 'QA and security review of the iteration-2 marketplace fixes against REQ-002'
- key: `qa_review-security` · role: security · writes: no · after: qa_review

Review the change made by task 'qa_review' for security: secrets, injection, unsafe file and network access, authentication and permission changes. Verify the logic first.

Acceptance: {"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}
