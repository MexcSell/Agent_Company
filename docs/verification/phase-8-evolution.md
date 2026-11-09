# Verification phase 8: continuous evolution

Date: 2026-11-09. Question: is an agent's improvement shown by a measured comparison with its previous version on the
same tests, without breaking what worked, with the old version kept and a rollback? Statuses as the plan defines
them: PASS, FAIL, BLOCKED, PARTIAL, NOT TESTED.

## What evolves here

| Plan's term | This platform |
|-------------|---------------|
| `developer-agent-v1` | The agent's playbook at its current version (`Agent.playbook`, `PlaybookVersion` rows), beside its definition file (`agentdefs`, a git repository per agent) and model route |
| The agent improves itself | `evolution._diagnose`: after a low success rate, the agent's recent failures become a candidate playbook (warnings first); the study rotation drafts definition changes (G-R-19) |
| `developer-agent-v2-candidate` | `Agent.experiment` and an `Experiment` row (`target: playbook`): stored beside v1, never written over it |
| Fixed evaluation dataset | The eval suite in the experiment's `dataset` (`smoke`: 3 tasks, 1 repeat on this server), run by `evaluation.run_suite` as eval goals |
| Independent evaluation | The same gate as any goal, with checks the agent does not write; the comparison and G-R-11 regression rule are the platform's |
| Approval and deployment | Smoke suite → live canary for `EVAL_WINDOW` (3) tasks → promoted as a new version, or rolled back |

Other evolution paths verified elsewhere: routes and workflow templates (SPEC-13 experiments, applied by an
administrator), definition candidates (G-R-19, applied by an administrator), the platform's own code (`selfevolve`,
fixed benchmarks, approval, deploy, monitoring, rollback; `tests/test_selfevolve.py`).

## Automated tests

`tests/test_phase8_evolution.py` (5 cases) uses the developer agent and a stub suite runner that records which suite,
side and repeats ran. The existing tests are listed where they cover a step.

| Step | Pass condition | Evidence | Result |
|------|----------------|----------|--------|
| 8.1 Baseline recorded | Per side: tasks run and solved (correct/failed), pass rate, compliance, time, **tokens**, cost, cost per pass, **tool failures**. Tokens, tool failures and compliance were computed by the suite but dropped from playbook experiments (V27) | `test_the_candidate_is_kept_apart_from_v1_and_both_run_the_same_suite` (fails without the fix) | FAIL → PASS (V27) |
| 8.2 The agent improves | Recent failures produce a candidate playbook | same test; `tests/test_evolution.py::test_low_success_starts_an_experiment_then_promotes_or_rolls_back` | PASS |
| 8.3 Candidate saved, v1 not overwritten | During the trial the agent keeps v1's text and version; v1's `PlaybookVersion` row stays; only eval goals see the candidate (`test_a_candidate_reaches_users_goals_only_after_the_smoke_suite`) | same test | PASS |
| 8.4 Same dataset for v1 and v2 | Both sides run the same suite with the same repeats | same test | PASS |
| 8.5 Regression | A candidate that breaks a task v1 solved is rejected whatever its pass rate (G-R-11); v1 stays | `test_a_candidate_that_breaks_what_v1_solved_is_rejected_and_v1_stays`, `test_the_regression_guard_compares_the_tasks_not_the_rates` | PASS |
| 8.5 Held-out set | **Playbook candidates run only the smoke suite.** Hidden (`final`) suites, whose checks never reach an agent, exist for learning plans and certification, not for playbook experiments (V28) | — | PARTIAL (V28) |
| 8.6 Approve or reject | A passing canary promotes the candidate as a new version; a failing one rolls it back automatically; v1 stays in the versions and can be restored | `test_the_live_canary_promotes_or_rolls_back_and_v1_can_be_restored` (2) | PASS |
| 8.6 Rollback after deployment | Within the canary: automatic. After promotion: a person restores any version (`evolution.restore`); a later drop in success starts a new diagnosis, not an automatic rollback | `tests/test_evolution.py::test_playbook_versions_are_curated_and_restorable` | PARTIAL |

## This server's evolution history (live)

20 experiments since 2026-11-06, 19 of them playbook candidates of the architect, developers and QA (the other one
is a workflow template proposal):

| Outcome | Count | Experiments |
|---------|------:|-------------|
| Rolled back: broke a task the current version solved (G-R-11) | 3 | #4 (developer, fix-average), #5 (QA, fizzbuzz and slugify), #14 (QA, fix-average) |
| Rolled back: live canary below the needed rate | 9 | #1, #2, #3, #6, #7, #8, #9, #12, #15 |
| Promoted as a new version | 5 | #10 QA → v9 (2/3 live), #13 developer #29 → v7 (3/3), #16 architect → v2 (**1/3**), #17 architect → v3 (2/3), #18 developer → v13 (3/3) |
| On its canary | 1 | #19 architect |
| Interrupted | 1 | #20 QA: its smoke trial ran in a verification script's process, which ended (V25). The server's sweep restarts it |

What the history shows against the plan:

- **The mechanism works**: no candidate reached users' goals before its suite; three that broke a solved task
  were rejected (each also had a lower pass rate, so the G-R-11 rule was not yet the deciding one live; offline it
  rejects a candidate with a higher rate); nine whose live results stayed below the bar were
  rolled back; every promotion kept the earlier versions.
- **The evidence is small**: every experiment ran the 3-task smoke suite once per side (the plan: 20 assignments
  and a held-out set), and every live decision rested on 2–3 tasks. #16 was promoted on one success out of three
  (33% against a needed 27%).

## Findings

- **V27 (fixed):** a playbook experiment kept only pass rate, iterations, time and cost per side. The suite's
  S09-R-05 dimensions (compliance, tokens, tool calls and failures, refusals, security) were computed and dropped,
  while route and template experiments kept them. `evolution.smoke_trial` now stores them too.
- **V28 (open, owner):** playbook promotions rest on little evidence. The fixed set is the 3-task `smoke` suite with
  1 repeat, there is no held-out set, and the live canary is 3 tasks (`EVAL_WINDOW`). The platform has what a
  stricter policy needs: larger suites and repeats in the experiment's `dataset`, hidden `final` suites, and
  `min_improvement`. Whether to spend the model time (a smoke trial costs about $3–10 per side on this server) is
  the owner's decision. Not changed.

## Phase 8 acceptance

| Criterion | Result |
|-----------|--------|
| 8.1 Baseline with the plan's metrics | PASS after V27 |
| 8.2–8.4 Candidate beside v1, same dataset | PASS |
| 8.5 Regression on the same tasks | PASS |
| 8.5 Held-out dataset | PARTIAL (V28) |
| 8.6 Approve or reject; keep v1; rollback | PASS within the canary; after promotion, rollback is a person's (PARTIAL) |
| Improvement measured, not claimed | PASS: every promotion and rollback on this server cites suite and live results; none rests on the agent's word |

## The plan's execution order (section 10), over phases 1–8

| # | Verification | Result | Where |
|---|--------------|--------|-------|
| 1–4 | Register models, test the connection, routing | PASS (V7–V10 open or by design) | phase-1, phase-2 |
| 5–6 | Each agent alone, tools and permissions | PASS; security PARTIAL (V13) | phase-3 |
| 7–8 | Two-agent communication, multi-agent coordination | PASS after V17–V20 | phase-4 |
| 9–10 | Register a project, assign agents | PASS after V21; developer/QA assignment PARTIAL (V22) | phase-5 |
| 11–12 | Create and execute a task | PASS after V23, V24 | phase-6 |
| 13–15 | QA, correction cycle, evaluation | PASS; review model family PARTIAL (V26) | phase-7 |
| 16–17 | Agent evolution, regression | PASS after V27; evidence size PARTIAL (V28) | phase-8 |
