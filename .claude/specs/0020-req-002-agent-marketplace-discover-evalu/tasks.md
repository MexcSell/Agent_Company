# Tasks (iteration 3)



## 1. Marketplace candidates are decided only through marketplace.approve/reject: guard the generic recruitment approve/reject, hide its buttons, remove the dead sandbox check, tests
- key: `route_decisions` · role: developer · writes: yes

Fix exactly these findings and nothing else. Do not refactor the rest of the marketplace.

1. recruitment.py `approve(recruitment_id, actor, grant=True, *, via_marketplace: bool = False)`: when `r.source == "marketplace"` and `via_marketplace` is not set, raise `PermissionError(f"recruitment #{r.id} is a marketplace import: decide it on Agents → Marketplace")` before any state change. Do this for every actor, including administrators. Replace the existing actor-set check at line 395 with this guard; the marketplace.approve admin check already covers internal actors.
2. recruitment.py `reject(..., *, via_marketplace: bool = False)`: apply the same guard to marketplace records, before any state change.
3. marketplace.py: pass `via_marketplace=True` only in the two existing calls (lines ~808 and ~835). Grep the repo (src/, including __main__.py and gm.py) for any other caller of recruitment.approve/reject, and make sure none passes the flag for a marketplace record.
4. api.py `recruitment_approve`/`recruitment_reject`: catch PermissionError, record `_audit(user, 'recruitment_approve_denied' / 'recruitment_reject_denied', recruitment_id, severity='warning', after={'reason': 'marketplace import'})`, then raise HTTPException(403, str(e)). Alternatively, if they reach the global handler at api.py:843, make sure the refusal is still audited. Nothing may be changed when it is refused.
5. static/admin.js (Evolution → Recruitment list, ~line 133-148): for `r.source === 'marketplace'`, render a short label/link 'decide on Agents → Marketplace' instead of the Approve/Reject buttons.
6. recruitment.py lines 505-506: delete the unreachable `sandbox.mode_for(Goal(trial=trial)) == 'off'` check. If `policies.effective('sandbox', None) == 'off'`, add a `log_event` saying the setting is overridden to `required` for the marketplace candidate. Remove the `Goal` import only if it is now unused.
7. Tests in tests/test_marketplace.py (offline, using the existing local fixture package and helpers):
   (a) as admin, POST /api/recruitments/{id}/approve on the marketplace candidate's recruitment returns 403. The agent's lifecycle, the import status and the recruitment status are unchanged, and an audit row 'recruitment_approve_denied' exists.
   (b) the same for /reject, and data/marketplace/<id>/ still exists afterwards.
   (c) calling recruitment.approve directly with actor 'user:admin' raises PermissionError.
   (d) marketplace.approve (with the accept flags the existing tests use) still approves, and marketplace.reject still rejects and removes the directory.
   (e) a non-marketplace recruitment still approves through the generic endpoint (no regression).
8. docs/threat-model.md: add one line to the marketplace control list saying that decisions go only through marketplace.approve/reject, with the test names.
9. Run `uv run pytest -q tests/test_marketplace.py tests/test_recruitment.py` and `node --check src/agent_company/static/admin.js`, and report their output. Keep the diff small.

Acceptance: {"type": "command", "run": "uv run pytest -q tests/test_marketplace.py tests/test_recruitment.py", "requirement_keys": ["REQ-002"], "stage": "unit"}; {"type": "command", "run": "node --check src/agent_company/static/admin.js", "stage": "static"}; {"type": "command", "run": "grep -n 'via_marketplace' src/agent_company/recruitment.py src/agent_company/marketplace.py", "requirement_keys": ["REQ-002"], "stage": "static"}; {"type": "command", "run": "! grep -n 'mode_for(Goal(trial=trial)) == \"off\"' src/agent_company/recruitment.py", "stage": "static"}; {"type": "command", "run": "grep -n \"marketplace\" src/agent_company/static/admin.js", "stage": "static"}

## 2. QA and security review of the iteration-3 decision-routing fix (narrow scope)
- key: `review` · role: qa · writes: no · after: route_decisions

Review ONLY the diff of the route_decisions task (`git diff HEAD~1 -- src tests docs` or the task's commit). Do not re-review the whole marketplace; the previous review ran over budget because it did. Read only recruitment.approve/reject/qualify, the marketplace.approve/reject call sites, the two API endpoints with their audit calls, the admin.js recruitment list, and the new tests. Check the data flow of three cases. Normal: marketplace.approve with its accept flags activates the candidate and writes Provenance. Edge: a non-marketplace recruitment still approves through the generic endpoint. Error: an admin's generic approve/reject on a marketplace record → 403, audited, no state change, directory kept. Also confirm: no other caller passes via_marketplace=True; the refusal happens before any write in the session; the dead sandbox check is gone and the sandbox stays 'required' for marketplace candidates. Run `uv run pytest -q tests/test_marketplace.py tests/test_recruitment.py` once. List each problem as a 'FINDING: file:line | severity | issue | fix' line, then an OUTCOME line, then 'VERDICT: PASS' or 'VERDICT: FAIL'. Fail only for a real defect inside this scope.

Acceptance: {"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}; {"type": "contains", "value": "OUTCOME"}; {"type": "min_length", "value": 300}
