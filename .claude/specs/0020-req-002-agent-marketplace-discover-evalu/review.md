# Review (iteration 3)

## QA and security review of the iteration-3 decision-routing fix (narrow scope) (qa, done)

I traced all three cases and found no defect, so the verdict is PASS. One gap in the evidence: the required test run (`uv run pytest -q tests/test_marketplace.py tests/test_recruitment.py`) was refused permission in this session, so the "74 passed" result is the developer's, not mine.

## Logic verification

I reviewed only commit `208bee6` (route_decisions). That is 6 files, +77/−12.

**`recruitment.approve` (`recruitment.py:382-392`)**
- **Normal case:** `marketplace.approve` first runs its own checks: acknowledgement, licence, unevaluated status, the permission ceiling and `clamp_autonomy`. It then calls `recruitment.approve(rec_id, actor, grant=False, via_marketplace=True)` at `marketplace.py:808`. The guard lets it through, the candidate is activated, and `marketplace.approve` then sets the import to `approved` and writes Provenance.
- **Edge case:** a recruitment that is not from the marketplace has a different `r.source`, so the guard does not apply and the existing path is unchanged. The new API test approves such a plain record through `/api/recruitments/{id}/approve`.
- **Error case:** a generic call by an administrator (`user:admin`) on a marketplace record raises `PermissionError` at line 391.
  - This happens right after `s.get` and before any status change, so nothing is written in that session.
  - It replaces the old check, which only refused internal actors. That fixes the root cause, not just the symptom: every caller except the marketplace path is now refused.
- **Auto-approve path:** `_finish` does not call `approve()`. It uses `_ask_or_approve` and `_activate` directly, and `_ask_or_approve` (`recruitment.py:276`) always holds a marketplace record for an administrator. So the new guard does not break qualification.

**`recruitment.reject` (`recruitment.py:422-433`)**
- **Normal case:** `marketplace.reject` retires the candidate in its own session (`:832`), calls `reject(..., via_marketplace=True)` at `:835`, then updates the import and deletes the package directory with `rmtree`.
- **Edge case:** a failed qualification uses `_retire_candidate` directly (`:551`), not `reject()`, so it is unaffected.
- **Error case:** a generic call raises `PermissionError` before any write (`:432`).

**API endpoints (`api.py:2253-2277`)**
- `_recruit_admin` runs first, so a non-administrator is still refused with 403 and audited.
- `_recruit_call` turns only `KeyError` and `ValueError` into HTTP errors, so a `PermissionError` reaches the new `except`. That handler records `recruitment_*_denied` as a warning and returns 403. The success audit is never reached.
- An unknown id still returns 404, because `KeyError` is raised before the guard.

**`qualify` (`recruitment.py:501-510`)**
- The dead check `mode_for(Goal(trial=trial)) == "off"` is gone, and `trial["sandbox"] = "required"` stays.
- The new `log_event` runs outside the `with session()` block, so it does not open a second writer (no SQLite conflict).
- `policies.effective("sandbox", None)` matches its signature (`policies.py:307`).
- `test_sandbox_off_setting_cannot_weaken_the_candidate_eval` still asserts that the trial is `required` even when the setting is `off`.

**Who passes `via_marketplace=True`:** a grep of `src/` finds only `marketplace.py:808` and `:835`. The CLI calls `marketplace.approve`/`reject`, never `recruitment.*`. There are no unqualified internal calls to `approve(` or `reject(` in `recruitment.py`.

**`admin.js:139`:** for a marketplace record, an administrator sees a label instead of the Approve, Reject, Cancel and Grant buttons.

## Checklist
- **Contract:** met.
- **Interfaces:** compatible. The new parameter is keyword-only and defaults to `False`.
- **Error handling:** correct (403, audited, no write).
- **Edge cases:** covered (non-marketplace records, unknown id).
- **Tests:** the two new tests cover cases (a) to (e). The new override log is not tested.
- **Dependencies:** none added.
- **Security:** the bypass is closed for every actor.
- **Performance:** no effect.
- **Docs:** a new threat-model row was added. One older row is now stale (see the findings).
- **`node --check admin.js`:** still not run by anyone. The changed ternary reads as balanced.

## Findings

FINDING: docs/threat-model.md:72 | low | The "candidate that grades or grants itself" row still says "a marketplace candidate is refused when the effective mode is `off`". That check was never effective and has now been removed; the code overrides the setting to `required` and logs it. Readers of the threat model get a control that does not exist. | Change it to "the trial's `sandbox=required` overrides an `off` setting (logged); a candidate is refused when the sandbox is missing".

FINDING: src/agent_company/recruitment.py:508 | low | No test asserts the new "overridden to 'required'" log event, so it could disappear without anyone noticing. | In `test_sandbox_off_setting_cannot_weaken_the_candidate_eval`, assert that an `Event` containing "overridden to 'required'" exists.

FINDING: src/agent_company/static/admin.js:139 | low (cosmetic) | Records that are already approved or rejected still show "decide on Agents → Marketplace" after the decision. | Show the label only when `r.status` is `pending` or `qualifying`.

OUTCOME: APPROVED_WITH_COMMENTS

VERDICT: PASS

NEXT: Run `uv run pytest -q tests/test_marketplace.py tests/test_recruitment.py` and `node --check src/agent_company/static/admin.js` in a session where those commands are allowed.

MESSAGE recommendation to developer: Fix the stale sentence in `docs/threat-model.md:72` and add the override-log assertion in `tests/test_marketplace.py`. Both are low-severity and do not block.
