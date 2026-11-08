I reviewed the goal text and tasks.md A–F. `spec.md` contains the developer's report rather than a spec (see the first finding). The implementation meets the spec. There are only minor findings, so the verdict is PASS. I didn't create `qa-findings.md` because this task is read-only. The loop stores this answer there, the same way it stored `spec.md`.

**Test result:** `uv run pytest -q tests/test_owner_policy.py` → `12 passed in 4.84s`

## Logic verification

**`ownerpolicy.approve_pending` / `_decide_one` (with the `settle_proposals` hook)**
- **Normal case:** a goal ends, and `run_goal` calls `settle_proposals` (`loop.py:1404`). It collects the pending proposals in a session, closes it, and with the setting on calls `approve_pending(goal_id, ids)` and returns (`projects.py:1241-1245`).
  - `_decide_one` skips the case/rule/restricted holds, then finds no duplicate.
  - It calls `projects.decide(pid, True, "auto-approve (owner policy)", by_human=False, hold=True)`. This sets `approved` and `decided_by` and spawns a goal with origin `proposal`.
  - `decide` posts "Proposal approved by auto-approve (owner policy)… is now goal #N" in the command window (`projects.py:1214`).
  - The policy writes the audit `policy:auto-approve` / `proposal_auto_approved`.
  - `test_follow_up_is_approved_and_audited` confirms this.
- **Edge case:** kind `product`/`project` (CASE_KINDS), `rule`, or a title that `restricted()` matches is held before `decide` runs. Even if it got there, `by_human=False` raises `PermissionError` (`projects.py:1132,1135,1146`), which is also caught as held. Held proposals are audited as `proposal_auto_held`, posted with "Proposal stays pending for a person (…)", and one admin-only notification goes out.
- **Error case:** any exception in `_decide_one` becomes "held" with the error text. Audit and post failures only log. `approve_pending` never raises, so it can't break a goal's ending.
- **Root cause:** this addresses the goal itself (proposals waiting for a click), not a symptom.

**`projects.decide(hold=…)` and `chain_block`**
- **Normal case:** `chain_block(s, origin)` returns `None`, and `decide` continues exactly as before.
- **Edge case, chain guard:** with `hold=True`, a chain-guard reason returns `proposal_dict + held` before any field changes. Leaving the `with session()` block commits nothing, so the proposal stays `pending`, `""`, `None`. `test_a_chain_guard_…` confirms this, and also that a person's later `decide` still rejects it.
- **Edge case, parent budget:** with `hold=True`, a parent-budget `ValueError` from `create_goal` resets status to `pending` and `decided_by` to `""` in a new, non-nested session and returns `held` (`projects.py:1197-1207`). `test_a_parent_budget_…` confirms this.
- **`hold=False` is unchanged:**
  - `chain_block(s, None)` returns `None`, the same as the old `origin is not None and …` guards.
  - The messages are the same: "limit of N proposal goals reached" and "goal chain depth N reached".
  - The `limit` read only moved into the helper.
  - The budget path still rejects with `"… (e)"`.
- **Error case:** a `create_goal` exception that isn't a `ValueError` behaves as it did before (the proposal stays `approved` with no goal). The policy then mislabels it as "held", which is low impact.

**`ownerpolicy.on_awaiting_approval` (with the `loop._run_steps` hook)**
- **Normal case:** the gate passes and the goal is set to `awaiting_approval`.
  - `_notify_goal` runs first; for improvement goals this runs `selfevolve.on_goal_finished`, so `merge_block` sees the verdict. The order is correct.
  - The hook (`loop.py:1647-1654`) then calls `on_awaiting_approval`. It reads the goal in a session that closes before calling `loop.approve(goal_id, actor="auto-merge (owner policy)")`.
  - `approve` merges, records the transition with the policy actor and calls `_push`. `_push` → `gitsync.auto_push` is the only push, so the AUTO_PUSH rule still governs it.
  - The policy audits `policy:auto-merge` / `goal_auto_merged` and posts in the command window. `run_goal` returns `approved`.
  - `test_auto_merge_merges_a_goal_that_passed_the_gate` confirms this.
- **Edge case:** origin `a2a`/`eval`, the setting off, or a status other than `awaiting_approval` returns `None` and nothing happens. Eval goals also return earlier, at `loop.py:1628`.
- **Error case:** a decision waits for a person, so `approve` raises `ValueError("decision(s) waiting for a person: #… Database")`.
  - The policy writes the audit `goal_auto_merge_refused` with the reason, sends the notification "Automatic merge of goal #N refused" whose body names the reason, and posts in the command window.
  - It returns `merged: False`, so the status stays `awaiting_approval`. The outer `try` means the hook can't mark the goal failed.
  - `test_a_decision_for_a_person_keeps_the_goal_waiting` drives this through `run_goal` and confirms it.

**Other hooks:** the `rca.analyse` and `management.advance_insight` hooks run after their sessions have closed. Both modules import `settings`.

## Checklist
- **Contract satisfied: yes.** All of A–F are implemented.
  - The duplicate reason reads `duplicate of goal #N` / `duplicate of proposal #N`, as tasks.md specified.
  - A held proposal's `decided_by` is `""` rather than None, because the column is not nullable. That is justified.
- **Interfaces compatible: yes.** `decide` only gains a keyword, `hold=False`.
- **Error handling: yes.** The policy functions never raise, and failures are logged.
- **Edge cases:** whitespace and case are normalised in duplicate checks. A restricted duplicate is held rather than rejected, which is the safe choice.
- **Settings: correct.**
  - Both default to off and are in `prefs.SCHEMA`, `config.Settings` (env `_bool(…, False)`) and `policies.GOVERNANCE_KEYS`, so each change records a policy version.
  - The only setters are `PUT`/`DELETE /api/settings/{key}`, which are admin-only (`admin_user`) and call `_audit`.
  - The GM toolbox and CLI have no setter.
- **Audit actors:** exactly `policy:auto-approve` and `policy:auto-merge`.
- **Sessions:** no write inside an open session uses a second connection. Every hook and policy call runs after its `with session()` block has closed.
- **Security:**
  - `by_human=False` keeps S14-R-02 in force.
  - a2a goals are excluded (ADR 0107). Improvement goals are included, but `merge_block` still applies.
  - The reasoning for treating the owner's standing approval as the merge approval is in ADR 0106.
- **Performance:** one extra query per proposal; negligible.
- **Docs:** the CLAUDE.md Conventions exception and Layout entry, and the threat-model owner-policy row and merge row, are both present.
- **Tests:** they cover cases (a)–(i). Gaps are listed below.

## Findings
FINDING: .claude/specs/0251-owner-policy-auto-approve-follow-up-prop/spec.md:1 | The file holds the developer's final report (commit 0b00e23 "impl answer"), not the behaviour spec. It even says "the required spec file is missing", which misleads anyone who reads the spec folder later | Replace it with the A–F behaviour spec from tasks.md, which is also in /tmp/goal251-spec.md

FINDING: src/agent_company/rca.py:243 | `item.report = dict(report)` is stored inside the session with every action set to `"result": "pending"`. The owner-policy outcomes (lines 413-420) only update the returned dict, so the stored analysis item disagrees with what `analyse()` returns and with each proposal's real status | Write the updated `actions` back to the item after `approve_pending`, in a short session, or note that the stored report shows the state before any automatic decisions

FINDING: src/agent_company/loop.py:1644 | With auto_merge on, `_notify_goal(…, "awaiting_approval")` sends "passed the gate and waits for approval" seconds before the policy merges the goal, and nothing later says it was merged. On a merge conflict with `require_approval` off, a second "refused" notification repeats the first (the developer reported this too). Administrators see stale or duplicate alerts | After a successful policy merge, send the "approved" notification, or have `_notify_goal` skip the waiting text when the policy will act. Skip the policy's retry when `note` already records a refused automatic merge

FINDING: src/agent_company/ownerpolicy.py:252 | When `create_goal` raises something other than a `ValueError` (a pre-existing gap in `decide`), the proposal stays `approved` with no goal, but the policy audits it as `proposal_auto_held` / "pending for a person" | In `_decide_one`'s caller, re-read the proposal status before labelling the outcome "held"

FINDING: tests/test_owner_policy.py:189 | Nothing tests an eval-origin goal in `on_awaiting_approval`, the `rca.analyse` and `management.advance_insight` hooks, or that toggling a setting records a policy version. A regression there would go unnoticed | Add one test per hook, an eval-origin test, and a `policies` version assertion after `prefs.set_value("auto_merge", True)`

OUTCOME: APPROVED_WITH_COMMENTS — All three traces behave as specified.
- A settled follow_up proposal is approved as 'auto-approve (owner policy)', spawns its goal, and is audited and posted.
- A chain-guard or parent-budget refusal leaves the proposal pending, while `decide(hold=False)` behaves exactly as before.
- With a decision waiting for a person, `loop.approve` raises; the goal stays awaiting_approval with an audit, a notification and a post that name the reason, and `run_goal` does not fail it.
- Restricted, rule, project and product proposals are never auto-approved, and exact-title duplicates are rejected.
- a2a and eval goals are never auto-merged.
- Both settings default to off, are versioned governance keys, and change only through the admin-only, audited settings API. The audit actors are exact, there are no nested-session writes, and push still goes only through AUTO_PUSH. CLAUDE.md and the threat model are updated.
- `tests/test_owner_policy.py`: 12 passed in 4.84s.
- The findings are non-blocking: the spec.md artifact holds the impl report instead of the spec, the stored RCA report is stale, there are redundant notifications, one outcome is mislabelled on a pre-existing edge, and some test gaps remain.

VERDICT: PASS

NEXT: Replace spec.md with the A–F behaviour spec (the GM can place /tmp/goal251-spec.md).
NEXT: Fix the stale `_notify_goal` text after a policy merge, and the stale stored RCA report.

MESSAGE request to general_manager: `.claude/specs/0251-owner-policy-auto-approve-follow-up-prop/spec.md` holds the impl report, not the spec; place /tmp/goal251-spec.md there or regenerate it from tasks.md A–F.
