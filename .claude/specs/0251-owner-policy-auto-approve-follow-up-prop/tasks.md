# Tasks (iteration 1)



## 1. Specify and implement auto_approve_proposals and auto_merge (owner policy) with tests and docs
- key: `impl` · role: developer · writes: yes

FIRST write .claude/specs/0251-owner-policy-auto-approve-follow-up-prop/spec.md, a short behaviour spec that restates points A-F below. Then implement it. Read every file before you change it.

A. Settings. Add `auto_approve_proposals` and `auto_merge` to prefs.SCHEMA as bool with clear descriptions, e.g. 'Owner policy: pending follow-up proposals are approved automatically within the chain guards; project, rule and restricted proposals still wait for a person'. Add both to config.Settings as `bool = False`, read from env through _bool('AUTO_APPROVE_PROPOSALS'/'AUTO_MERGE', False) like task_critic (config.py:100/218). Add both to policies.GOVERNANCE_KEYS so each change records a policy version (S01-R-05). The generic Settings page renders bool keys from prefs.describe() as on/off selects, and PUT /api/settings/{key} is already admin-only and calls _audit, so do not change the UI.

B. New module src/agent_company/ownerpolicy.py with:
  `approve_pending(goal_id: int | None = None, ids: list[int] | None = None) -> dict` (returns approved/rejected/held lists). For each PENDING proposal, in this order:
  (1) kind in projects.CASE_KINDS or kind == 'rule' → hold, reason 'a new product or project' / 'a project rule'.
  (2) projects.restricted(title, description) → hold, reason 'it touches authority or safety controls'.
  (3) Duplicate: compare normalised titles (' '.join(t.split()).casefold()) within the same project_id. A match with an open goal (status in workflow.ACTIVE or 'awaiting_approval'; check what workflow.ACTIVE contains) or with another pending proposal of LOWER id → projects.decide(pid, False, f'auto-approve (owner policy): duplicate of goal #<id>' or '... duplicate of proposal #<id>', by_human=False). That is a reject.
  (4) Otherwise call projects.decide(pid, True, 'auto-approve (owner policy)', by_human=False, hold=True).
  Any PermissionError or ValueError from decide → hold with str(e). It must never raise out of approve_pending.
  For each outcome call governance.audit('policy:auto-approve', 'proposal_auto_approved' | 'proposal_auto_rejected' | 'proposal_auto_held', f'proposal:{pid}', after={...reason, spawned_goal_id}). For held ones, post in the project's command window (projects.post_for_goal or projects.post with kind='proposal', proposal_id where supported): 'Proposal stays pending for a person (<reason>): <title>'. Send one notify(..., level='warning', source='proposals', admin_only=True) when any are held.
  `on_awaiting_approval(goal_id: int) -> dict | None`. If settings().auto_merge is off, or the goal's origin is 'a2a' or 'eval', or its status is not 'awaiting_approval', return None. Otherwise try loop.approve(goal_id, actor='auto-merge (owner policy)'). On success: governance.audit('policy:auto-merge', 'goal_auto_merged', f'goal:{goal_id}', after={'merge_commit': sha}) and post_for_goal(goal_id, 'Merged automatically by the owner policy (auto_merge).', kind='result'). On ANY Exception: the goal stays awaiting_approval. Send governance.audit('policy:auto-merge', 'goal_auto_merge_refused', ..., after={'reason': str(e)}), notify('Automatic merge of goal #N refused', str(e), level='warning', source='goal', goal_id=..., project_id=...) naming the reason, and post_for_goal with the reason. Never raise.
  Import loop and projects lazily inside the functions to avoid import cycles.

C. projects.decide gets a keyword `hold: bool = False`. Move the chain-guard checks (lines ~1143-1146: max_proposal_goals via _spawned_from/_root, max_goal_depth via _depth) into a helper `chain_block(s, origin) -> str | None` that decide uses, keeping today's messages. With hold=True and approve=True: a chain_block reason leaves the proposal 'pending' (no status or decided_by change, no rejection post) and returns proposal_dict plus 'held': reason. When loop.create_goal raises ValueError (e.g. the parent budget, S04-R-09), reset status to 'pending' and decided_by to None instead of 'rejected', and return 'held': str(e). With hold=False the behaviour stays byte-for-byte as today; the existing tests must still pass.

D. Hooks.
  (1) projects.settle_proposals: right after `pending` is computed and found non-empty, if settings().auto_approve_proposals then call ownerpolicy.approve_pending(goal_id, ids=[x[0] for x in pending]) and RETURN. No GM triage and no plain 'wait for a decision' announcement, because held ones were already posted and notified. Research's settle_proposals(goal_id, only=...) therefore goes through the same path.
  (2) Proposals created outside a goal's end: after rca.py stores its follow-up proposals (around line 217, after the session commits) and after management.py's insight→proposal (around line 751, after the session), call ownerpolicy.approve_pending(ids=[...]) when the setting is on. Only follow_up kinds get approved; the checks above handle the rest.
  (3) loop.run_goal at the gate-pass branch (loop.py ~1643-1647): after `_update_goal(...status...)` and `_notify_goal(goal_id, status)`, if status == 'awaiting_approval', call `res = ownerpolicy.on_awaiting_approval(goal_id)`; if it merged, return {'status': 'approved', ...} instead of 'awaiting_approval'. The call must not be able to fail the goal: it is wrapped and never raises. approve() already pushes under the AUTO_PUSH rule (_push), so add no extra push.

E. Docs. In CLAUDE.md, extend the Conventions line 'merging needs approval; pushing only on `/sync` or AUTO_PUSH' with the exception: with the owner-policy setting `auto_merge` on, loop.approve runs automatically as 'auto-merge (owner policy)' when a goal reaches awaiting_approval, except a2a/eval goals, and every merge-time block still applies. Add one short mention of `ownerpolicy.py` (auto_approve_proposals, auto_merge) in the Layout list near projects.py/loop.py. In docs/threat-model.md, add a row for 'Owner policy: automatic proposal approval and merge'. It names the threat (an agent's proposal widening its own authority; a merge without a person's click) and the controls: off by default, admin-only, versioned, audited as policy:auto-approve/policy:auto-merge, by_human=False so restricted, rule and project proposals stay pending, chain guards and budget keep proposals pending, approve() keeps decisions, critical defects, merge_block and conflicts as blocks, A2A excluded. It names the tests in tests/test_owner_policy.py. Also update the merge row (line ~44) to mention auto_merge.

F. Tests: new tests/test_owner_policy.py. Copy fixtures and setup from tests/test_projects.py, tests/test_rule_proposals.py, tests/test_policies.py and tests/test_loop.py (each test has its own temp DATA_DIR and runs offline; set settings with prefs.set_value). Required tests:
  (a) both settings off: settle_proposals leaves a follow_up proposal pending (policy 'user'), and a goal at awaiting_approval stays there;
  (b) auto_approve on: a follow_up proposal is approved, decided_by 'auto-approve (owner policy)', spawned_goal_id set, and there is an audit row with actor 'policy:auto-approve';
  (c) restricted (e.g. title with 'permission'), kind 'rule' and kind 'project' proposals stay pending;
  (d) max_proposal_goals=0 (or depth reached) → the proposal stays PENDING, not rejected;
  (e) a parent budget that makes create_goal raise ValueError → stays pending;
  (f) an exact-title duplicate of an open goal → rejected with 'duplicate of goal #' in decided_by;
  (g) auto_merge on: ownerpolicy.on_awaiting_approval merges a goal at awaiting_approval → status 'approved', audit actor 'policy:auto-merge';
  (h) auto_merge on with a decision waiting for a person (decisions.blocking non-empty) → the goal stays awaiting_approval, a notification names the reason, audit 'goal_auto_merge_refused';
  (i) an a2a-origin goal is not auto-merged.
Run `python3 -I /home/luca/Agent_Company/src/agent_company/edit_check.py <changed .py files>`, then the test commands below, serially (no -n).

Acceptance: {"type": "file_exists", "path": ".claude/specs/0251-owner-policy-auto-approve-follow-up-prop/spec.md"}; {"type": "file_exists", "path": "src/agent_company/ownerpolicy.py"}; {"type": "file_exists", "path": "tests/test_owner_policy.py"}; {"type": "command", "run": "uv run pytest -q tests/test_owner_policy.py", "requirement_keys": ["S14-R-02", "S04-R-09"], "stage": "unit"}; {"type": "command", "run": "uv run pytest -q tests/test_projects.py tests/test_rule_proposals.py tests/test_eval_proposals.py tests/test_policies.py", "requirement_keys": ["S01-R-05", "S14-R-02"], "stage": "integration"}; {"type": "command", "run": "uv run pytest -q tests/test_loop.py", "stage": "integration"}; {"type": "command", "run": "grep -q auto_merge CLAUDE.md"}; {"type": "command", "run": "grep -q policy:auto-approve docs/threat-model.md"}; {"type": "command", "run": "grep -q auto_approve_proposals src/agent_company/prefs.py"}

## 2. QA review of the owner-policy auto-approve and auto-merge against the spec
- key: `qa` · role: qa · writes: no · after: impl

Review the diff of task 'impl' against .claude/specs/0251-owner-policy-auto-approve-follow-up-prop/spec.md and the goal text. Trace the data flow for three cases. (1) Normal: a follow_up proposal is settled with auto_approve_proposals on → approved by 'auto-approve (owner policy)', goal spawned, audit plus command-window post. (2) Edge: a chain-guard or parent-budget refusal → the proposal is still 'pending' (not rejected), and decide() with hold=False behaves exactly as before. (3) Error: auto_merge on while a decision waits for a person → loop.approve raises, the goal stays awaiting_approval, a notification and audit name the reason, and run_goal does NOT mark the goal failed.
Also verify:
- restricted, rule, project and product proposals are never auto-approved (by_human=False is used);
- an exact-title duplicate is rejected as 'duplicate of ...';
- a2a and eval goals are never auto-merged;
- both settings default to off, are in prefs.SCHEMA, config.Settings and policies.GOVERNANCE_KEYS, and change only through the admin-only, audited settings API;
- audit actors are exactly 'policy:auto-approve' and 'policy:auto-merge';
- no DB write uses a second connection inside an open session (see CLAUDE.md);
- push still follows AUTO_PUSH only;
- CLAUDE.md and docs/threat-model.md were updated.
Run `uv run pytest -q tests/test_owner_policy.py` and report the summary line. List each problem as 'FINDING: <file>:<line> | <problem>'. End with 'OUTCOME: <one paragraph>' and 'VERDICT: PASS' or 'VERDICT: FAIL'.

Acceptance: {"type": "file_exists", "path": ".claude/specs/0251-owner-policy-auto-approve-follow-up-prop/qa-findings.md"}; {"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}; {"type": "contains", "value": "OUTCOME"}
