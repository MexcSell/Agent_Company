# Requirements: Owner policy: auto-approve follow-up proposals and auto-merge goals that pass the gate

The owner instructed on 2026-11-08: approve everything, now and in the future. Build two DB settings (prefs.py, both default off, admin-only, audited, toggles on Settings): 
1) auto_approve_proposals: when a pending follow_up proposal is created or settled (projects.settle_proposals), approve it with projects.decide(..., decided_by='auto-approve (owner policy)'). Never auto-approve kind project/rule or a proposal projects.restricted() matches (S14-R-02/SEC-007: an agent must not get its own authority widened); those stay pending for a person. A proposal the chain guards (max_proposal_goals, max_goal_depth) or a parent budget would refuse stays PENDING (not rejected) under auto-approve. An exact-title duplicate of an open goal or pending proposal is rejected as 'duplicate of #id'.
2) auto_merge: when a goal reaches awaiting_approval, call loop.approve(goal_id, actor='auto-merge (owner policy)'). If it raises (decisions waiting for a person, open critical defect, selfevolve.merge_block, merge conflict) the goal stays awaiting_approval and a notification names the reason. Push follows the existing AUTO_PUSH rule.
Each automatic action writes governance.audit with actor 'policy:auto-approve' / 'policy:auto-merge' and posts in the project's command window. Update CLAUDE.md (the 'merging needs approval' convention gets the exception) and docs/threat-model.md. Tests: both settings off keep today's behaviour; on: follow_up approved, restricted/rule/project left pending, guard-limited left pending, duplicate rejected, merge done, merge blocked by a pending decision leaves the goal waiting.

## Quality commands
- `uv run pytest -q`
