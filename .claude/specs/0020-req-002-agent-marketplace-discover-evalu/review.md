# Review (iteration 1)

## QA review of the agent marketplace against REQ-002 (qa, done)

## Marketplace QA review against REQ-002

**Verdict: FAIL.** Criterion (b) is not met, and none of the issues I raised in the earlier security review of the backend have been fixed: `git diff 5dec38f HEAD` shows no change to `marketplace.py`, `recruitment.py`, `sandbox.py` or `agentdefs.py`. The `surface` commit (34a3934) added only the API, CLI, Marketplace tab, threat-model rows and tests.

I could not run `pytest` or `node --check` because both needed approval, so the "25 pass" figure is the developer's claim. I did not open the tab in a browser either; I read its code instead.

## Logic verification

**Normal case: URL import, then approve.**
1. `POST /api/marketplace/import` needs an admin (`admin_user`), so others get 403. It calls `fetch_url`, which runs `check_url`: https only, a public host, no user name or password. `file://` is allowed only when the module constant `ALLOW_FILE_URLS` is set, which only tests do. ssh://, http:// and `ext::` are refused, both by the scheme check and by `protocol.allow=never` with only https allowed.
2. The repository is cloned shallow into a new `data/marketplace/<id>/src`, with hooks off (`core.hooksPath=/dev/null`, `--template=`), no submodules, no symlinks and LFS filters emptied. `.git` is removed, then `_check_tree` checks the size.
3. `convert` reads text only, `validate_manifest` checks it, `screen` looks for secrets, injected instructions, code and a missing licence, and `permission_ceiling` sets what the agent may hold. The import is then `converted`.
4. `create_candidate` creates a `Recruitment` with `source="marketplace"` and an agent with `lifecycle="candidate"`, at the ceiling's permissions and autonomy. It writes `## Imported instructions` and `## Provenance` and starts `qualify`. `_finish` passes the result to `on_qualified`, which stores the eval on the import.
5. Approve: `marketplace.approve(admin=True)` re-computes the ceiling intersected with `grant` and clamps autonomy, then calls `recruitment.approve`, which refuses `policy`/`system` as actors. Provenance is written again, the API records `_audit`, and the agent is enabled.
6. Neither the API nor the CLI can grant outside the ceiling: the ceiling is the role template's permissions minus network, mcp and admin, and `clamp_autonomy` takes the lower of the request and the ceiling. This answers the developer's question.

**Edge case: archive with `../` or a symlink.** `_member_path` refuses absolute paths, `..` and backslashes. `_zip_members` and `_tar_members` refuse links and special files before anything is written, and files are created with `open("xb")`. The import is marked `failed`, its folder is deleted, the API returns 400 and records a `*_refused` audit entry. Nothing is extracted into a shared directory: Starlette's upload spool holds only the compressed bytes.

**Edge case: manifest requesting Bash, MCP or high autonomy.** The requested permissions only fill `not_granted`; `mcp` and `network` are always dropped and autonomy is capped at G2 or G1. The screen sets `acknowledge`. Permissions cannot be widened, but self-declared skills and models are still applied (findings 4 and 5).

**Error case: unreachable URL.** A host that doesn't resolve is refused in `check_url`, before any record exists. A clone failure or timeout calls `_fail`: status `failed` and the folder deleted. No agent is created, because the agent is only created in `create_candidate`, which needs `converted`.

**Error case: invalid manifest.** `_after_fetch` catches `ImportRefused`, then `_fail` runs, with no agent. Correct.

**Rejection.** `reject` either retires the candidate (`lifecycle="retired"`, `enabled=False`, recruitment.py:582) or calls `recruitment.reject`, then deletes `data/marketplace/<id>`. Correct.

**Root cause:** the fetch, unpacking, ceiling and approve paths fix the root causes. The "eval only in L3" guarantee only covers the symptom (bubblewrap missing), not the setting that turns the sandbox off (finding 1).

## Checklist

- **Contract:**
  - (a) met.
  - (b) **not met** (findings 1–3).
  - (c) met for the API and CLI, but `reject` and `restore` have no admin check of their own (finding 8).
  - (d) met; rollback through `agentdefs.restore` works, but it is laxer than the spec (finding 8).
  - (e) met for permissions and autonomy; skills and model are still taken from the package (findings 4 and 5).
- **Interfaces:** compatible.
- **Error handling:** good. API errors map to 404, 403 and 400.
- **DB session discipline:** correct. `log_event`, `notify` and `record_version` get `s=s`. `agentdefs` and `recruitment.approve` run after the session closes, and `on_qualified` uses the caller's session.
- **`_audit`:** present on import, upload, approve, reject and the refused import/upload, and the CLI audits as `cli`. A refused *approve* (400/403) is not audited (finding 9).
- **UI:** every interpolated value goes through `esc()`. The checkboxes are limited to `ceiling.permissions` and the autonomy menu stops at the ceiling. The upload uses the same raw `fetch` pattern as `projects.js:410`.
- **Tests:** good coverage. The missing cases are `sandbox=off`, a suite chosen by the package's skills, an unevaluated role being approved, and DNS rebinding.
- **Docs:** six threat-model rows that name their tests. The SSRF row claims redirect protection but doesn't cover rebinding (finding 6).

## Findings

FINDING: src/agent_company/recruitment.py:497, src/agent_company/sandbox.py:216 | **high** – with the company or project setting `sandbox=off`, a marketplace candidate's eval commands (model-written code, steered by untrusted imported instructions) run on the host without the sandbox. `qualify` checks only `available()`, and `trial["sandbox"]="required"` is never read by `mode_for`. Criterion (b) is broken | `mode_for` returns `"required"` when `goal.trial.get("sandbox")=="required"`, and `qualify` refuses a marketplace candidate whose effective mode is `off`; add a test with `sandbox=off`
FINDING: src/agent_company/marketplace.py:516 | **high** – `qualification_suite([*m["skills"], m["role"]])` puts the package's own skills first, so the package chooses its eval suite (e.g. an easy one) instead of its role's. Criterion (b) | pick the suite from the role only (`[m["role"]]` or the role template's skills); add a test
FINDING: src/agent_company/marketplace.py:527, :547 | **medium** – a role that doesn't write, or one with no matching suite, becomes import status `"qualified"` with the eval `skipped`, and `approve` enables it with no eval run. Criterion (b) | use a separate `unevaluated` status, and make `approve` require `eval.status=="passed"` or an explicit `accept_unevaluated`
FINDING: src/agent_company/marketplace.py:543 | **medium** – `agent.skills = {k: 5 …}` gives proficiency 5 in every skill the package claims, so once approved, `registry._rank` prefers it for those skills with no verified evidence. A package widens how it is staffed. Criterion (e) | start the imported skills at the lowest level and raise them only from eval or probation results
FINDING: src/agent_company/marketplace.py:542 | **medium** – `model_pref` is taken from the untrusted manifest's `models` (any catalog entry, e.g. an expensive model or a `claude_code` model with its own sign-in), although the module says requested items are "shown, never applied" | keep the requested models in the manifest only, use the role route, and let the administrator choose at approve
FINDING: src/agent_company/marketplace.py:142, :172, docs/threat-model.md:68 | **medium** – DNS rebinding: `_is_public_host` resolves the host, then git resolves it again, so the clone can reach 127.0.0.1 or internal services (SSRF on the server). The threat-model row doesn't cover this | pin the checked address with `-c http.curloptResolve=<host>:443:<ip>` in `clone_command`; add the case to the row and a test
FINDING: src/agent_company/marketplace.py:172 | **medium** – the clone has no size bound while it runs, so a hostile repository can fill `data/` within the 60 s timeout before `_check_tree` runs | add `--filter=blob:limit=2m`, or an `RLIMIT_FSIZE` / systemd-scope disk limit on the clone process
FINDING: src/agent_company/marketplace.py:649, src/agent_company/agentdefs.py:196 | **low** – `reject` and `restore` don't check for an administrator themselves (only the callers do, unlike `approve(admin=True)`). `restore` accepts 4-hex-character ids and doesn't check the file's `git log` as spec C2 requires | add `admin: bool` → `PermissionError` to both; use the regex `{7,40}` and check the commit is in `git log --format=%H -- <file>`
FINDING: src/agent_company/api.py:2337 | **low** – a refused approve (missing licence acceptance, 403 for a non-person actor, wrong status) raises before `_audit`, so attempts are not in the audit log; the same holds for reject | wrap `_market_call` in approve and reject the way import does, with `marketplace_approve_refused` at severity warning
FINDING: src/agent_company/marketplace.py:246 | **low** – `tarfile.getmembers()` decompresses the whole stream of a 20 MB gzip bomb before the `MAX_RATIO` check runs (CPU/time DoS in an API worker) | count the decompressed bytes while iterating the members and stop at `MAX_BYTES`
FINDING: src/agent_company/static/agents.js:351 | **low** – the new tab's JavaScript was never syntax-checked or opened in a browser (`node --check` was refused for both developer and QA) | run `node --check src/agent_company/static/agents.js` and open Agents → Marketplace once

NEXT: a developer task with `sandbox.py`, `marketplace.py`, `recruitment.py` and `agentdefs.py` in its allowed paths fixes findings 1–5 and adds tests for `sandbox=off`, a suite chosen by skills, approving an unevaluated import, and starting skills unverified.
NEXT: pin the clone address and bound the clone size (findings 6 and 7), then update the SSRF row in the threat model.
MESSAGE request to developer: fix src/agent_company/sandbox.py:216 (`trial.sandbox` must be binding) and src/agent_company/marketplace.py:516/527/542/543 before the next QA pass; `tests/test_marketplace.py` needs the four missing cases.
MESSAGE fact to security: no backend security fix landed between 5dec38f and 34a3934; the DNS-rebinding gap and the `sandbox=off` host path are still open.

PROPOSAL: run `node --check` on static/*.js in the test suite :: dashboard syntax errors pass every gate today, and tasks without approval for node can't check them.

```json
{"insights": [{"title": "Review findings are not carried into the next task", "description": "The surface task left every backend security finding from the previous review unfixed, because none of the files involved (sandbox.py, marketplace.py) were in its allowed paths. Findings that block a requirement need their own fix task, or the following QA will fail again on the same points.", "evidence": ["git diff 5dec38f 34a3934 -- src/agent_company/marketplace.py src/agent_company/sandbox.py is empty", "sandbox.py:216 mode_for ignores trial"], "expected_benefit": "Fewer QA iterations that repeat the same findings", "confidence": 0.7}]}
```

OUTCOME: REVISION_REQUIRED. Criteria (a), (c) and (d) are essentially met: the fetch and unpacking are hardened, nothing is extracted into a shared directory, approval needs an administrator, the API and CLI are audited and give non-admins 403, and the database session handling is correct. Criterion (b) is not met. With `sandbox=off` the candidate's eval runs on the host, the package chooses its own eval suite, and a role with no suite can be approved with no eval. Criterion (e) holds for permissions and autonomy, but self-declared skills at level 5 and the package's chosen model are applied.

VERDICT: FAIL

## Security review of 'marketplace.py: untrusted fetch, conversion to manifest, permission ceiling, candidate qualification, provenance; db tables; offline tests' (qa, done)

I'm reviewing commit 5dec38f (`marketplace.py`, `recruitment.py`, `agentdefs.py`, `db.py`, `tests/test_marketplace.py`). I read all the code. I could not run the tests: running them needed approval, so the 49-test pass is the backend's claim, not something I checked.

## Logic verification

- **`check_url` / `clone_command` / `fetch_url`**
  - Normal case: an https URL to a public host passes the check. It is cloned shallow with hooks off (`core.hooksPath=/dev/null`), no templates, no symlinks, no fsmonitor, no LFS, no redirects, https only and no proxy variables. `.git` is then removed and `_check_tree` caps the size.
  - Edge case: a leading `-`, `user@host`, or a host resolving to an address that is not global are refused, and `--` comes before the URL.
  - Error case: on a non-zero exit or timeout, `_fail` deletes the directory.
  - Gap: the DNS lookup in `_is_public_host` and the lookup git does are separate (TOCTOU).
- **`_extract` / `_member_path`**
  - Normal case: every member is checked before anything is written. Files are created with `open("xb")`, so a duplicate name fails as an `OSError`, which is caught and refused.
  - Edge case: a zip-slip `../evil` is refused, and zip/tar symlinks and hard links are refused.
  - Error case: a bad archive becomes `ImportRefused` and the directory is removed.
  - Gap: tar `getmembers()` decompresses the whole stream before the ratio check runs.
- **`convert` / `validate_manifest` / `safe_yaml`**: text only. `_read` uses `lstat`, so symlinks are refused. `instructions_file` refuses `..` and absolute paths. YAML anchors are refused. `general_manager` and unknown roles are refused. Correct.
- **`permission_ceiling` / `clamp_autonomy` / `approve`**
  - The permissions are the role template's, minus `NEVER_ON_IMPORT`, intersected with `grant`.
  - `requested` only fills `not_granted`. Autonomy is `min(value, ceiling)`.
  - With `grant=["admin","network","write"]` the result is a subset of the ceiling, so `approve` cannot widen the ceiling (the architect's question). This holds.
- **`recruitment.qualify`, marketplace branch**: it refuses when `sandbox.available()` is false and adds `trial["sandbox"]="required"`. `sandbox.mode_for` (sandbox.py:216) never reads that key. It returns `policies.effective("sandbox", …)`. So with the company setting `sandbox=off`, bubblewrap can be installed and `qualify` proceeds, yet the eval checks run without the sandbox, on the host. The commands run are model-written code, steered by the imported instructions. The fix closes the symptom (bubblewrap missing) but not the root cause.
- **`create_candidate`**
  - The package's own `skills` are listed before the role in `qualification_suite`, so the package chooses which suite it is tested on.
  - A role that doesn't write, or that has no matching suite, gets `imp.status="qualified"` with no eval having run. It can then be approved.
- **`agentdefs.demote` / `write_section` / `restore`**: forged `## ` and `---` lines are escaped, and the kept sections survive `sync`. `restore` accepts 4–40 hex characters (the spec says 7–40). It does not check the file's `git log` and takes no permission check.
- **Tests**: `MARKER` is asserted absent (test_marketplace.py:132). Zip-slip, symlinks, limits, secrets, widening, demotion, approve, reject and restore are covered. Missing: `sandbox=off`, a suite chosen by the package's skills, a non-writing role becoming "qualified", and rebinding.

## Checklist

- **Contract**: partly met. "Runs its role's eval suite in the L3 sandbox" is not guaranteed (findings 1–3).
- **Interfaces**: compatible. `recruitment.approve` keeps its signature.
- **Error handling**: good. Every fetch failure cleans up.
- **Edge cases**: see the findings.
- **Tests**: good but missing the cases above.
- **Dependencies**: none new.
- **Security**: findings below.
- **Performance**: decompression and clone size are unbounded while running.
- **Docs**: the threat model is left to the next task.

FINDING: src/agent_company/recruitment.py:497, src/agent_company/sandbox.py:216 | with the company or project setting `sandbox=off`, a marketplace candidate's eval commands (model-written, steered by untrusted imported instructions) run on the host without the sandbox, even though `qualify` checked `available()`; `trial["sandbox"]` is never read | `mode_for` returns `"required"` when `goal.trial.get("sandbox")=="required"` (or when the trainee's recruitment has `source=="marketplace"`), and `qualify` refuses a marketplace candidate when the effective mode is `off`; add a test with `sandbox=off`
FINDING: src/agent_company/marketplace.py:516 | the package's self-declared `skills` come first in `qualification_suite([*m["skills"], m["role"]])`, so a package can pick an easier suite than its role's | use `qualification_suite([m["role"], *org.ROLES[m["role"]].skills])` (role only); show the package's skills but don't let them choose
FINDING: src/agent_company/marketplace.py:543 | `agent.skills = {k: 5 …}` gives proficiency 5 in every skill the package claims; once approved, `registry._rank` prefers the import for those skills (e.g. security review) with no verified evidence | start the imported skills at 0 or 1 and raise them only through the eval or probation results, or store them only as `requested_skills`
FINDING: src/agent_company/marketplace.py:547 | a role that doesn't write, or one with no matching suite, gets import status `"qualified"` with the eval `skipped`; `approve` accepts it, so an agent can be enabled without the eval the task requires | use a separate status such as `unevaluated`, and make `approve` require `eval.status=="passed"` or an explicit `accept_unevaluated=True`; add a test
FINDING: src/agent_company/marketplace.py:142, :172 | DNS rebinding: `_is_public_host` resolves the host, then git resolves it again, so a rebinding host can point the clone at 127.0.0.1 or internal services (SSRF on the server) | pin the checked address with `-c http.curloptResolve=<host>:<port>:<ip>` in `clone_command`, or clone through a proxy that refuses addresses that aren't global
FINDING: src/agent_company/marketplace.py:172 | the clone has no size bound while it runs: a hostile repository can fill `data/` within the 60 s timeout before `_check_tree` runs | add `--filter=blob:limit=2m`, or run the clone under an `RLIMIT_FSIZE` / disk quota / systemd-scope limit, and refuse a pack larger than the limit
FINDING: src/agent_company/marketplace.py:246 | `tarfile.getmembers()` on a 20 MB gzip bomb decompresses gigabytes before the ratio check (CPU/time DoS in the API worker) | wrap the decompression in a reader that stops after `MAX_BYTES * 2`, or check `MAX_RATIO` while iterating the members
FINDING: src/agent_company/marketplace.py:542 | `model_pref` is taken from the untrusted manifest's `models` (any catalog name, e.g. an expensive model or a `claude_code` model with its own sign-in), although the module says requested items are "shown, never applied" | store the requested models in the manifest only, leave `model_pref` as the role's route, and let the administrator set it at `approve`
FINDING: src/agent_company/marketplace.py:649, src/agent_company/agentdefs.py:196 | `reject` and `restore` take no admin check (`approve` takes `admin=True`); `restore` accepts 4-hex-character ids and doesn't check the file's `git log` as spec C2 requires | add `admin: bool` and raise `PermissionError` for both; use the regex `{7,40}` and check `git log --format=%H -- <file>` contains the commit

OUTCOME: REVISION_REQUIRED
VERDICT: FAIL

NEXT: fix findings 1–4 (`sandbox.py` needs to be in a task's allowed paths), then the clone and tar bounds; add the four missing tests.
MESSAGE recommendation to developer: in the next task, make `sandbox.mode_for` honour `trial.sandbox`, take the qualification suite from the role only, start imported skills unverified, and refuse to approve an unevaluated import (src/agent_company/marketplace.py:516,543,547; src/agent_company/sandbox.py:216).
MESSAGE request to security: add threat-model rows for DNS rebinding of the clone and for the eval of an imported agent when `sandbox=off` (docs/threat-model.md).

PROPOSAL: Pin resolved addresses for all outbound fetches :: research crawling and marketplace clones share the TOCTOU of resolving the address for the check and again for the fetch; one helper (`curloptResolve`/pinned connect) closes it everywhere.

```json
{"insights": [{"title": "A flag in trial is not a sandbox guarantee", "description": "Setting trial['sandbox']='required' has no effect: sandbox.mode_for only reads origin and policies.effective. Every 'must run in L3' requirement has to be enforced inside mode_for, not at the caller.", "evidence": ["src/agent_company/sandbox.py:216", "src/agent_company/recruitment.py:497"], "expected_benefit": "Prevents host execution when the company setting is off", "confidence": 0.8}]}
```
