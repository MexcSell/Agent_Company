# Verification phase 7: quality and evaluation

Date: 2026-11-09. Question: can the platform tell good work from bad, and does it measure quality itself instead of
trusting what the developer reports? Statuses as the plan defines them: PASS, FAIL, BLOCKED, PARTIAL, NOT TESTED.

## Who evaluates here

| Plan's role | This platform |
|-------------|---------------|
| Developer: produces the implementation | The developer task's run and its changed files |
| QA: runs and checks the tests | The QA review task: a different agent, given the work, the dependencies' outputs and `REVIEW_CHECKLIST`; its `VERDICT` counts only with a logic-verification section (normal, edge and error case, `quality.review_verdict`) |
| Evaluator: measures against acceptance criteria | The quality gate (`quality.evaluate`): each task's acceptance conditions, review verdicts, static checks (syntax, scope, boundaries, secret scan), dependency audit, the goal's quality commands and staged checks, all run by the platform in the L3 sandbox |
| PM: requests corrections or submits for approval | A failed gate sends its feedback to the GM's next plan and the developer's next task; a passing one goes to approval (or the owner policy's merge) |

## 7.1 / 7.2 Correct, buggy and incomplete work

### Live (this server's QA Engineer)

`docs/verification/phase7_live.py`: one delivery of the same task per goal: `calc.py` with add, subtract, multiply
and divide (true quotient, `ValueError('division by zero')`), and unit tests. Each delivery's own tests pass, and each
"developer" reports success. QA was not told which was which. The goal's quality command is the platform's own
check (`divide(7, 2) == 3.5`, `multiply(4, 2.5) == 10`, the `ValueError`), never shown to an agent. Goals #334–#336,
runs 2130–2132 (QA on Claude Code). Evidence: `data/verification/phase7/<A|B|C>.json`.

| Task | Delivered | Own tests | QA decision | What QA found | Gate | Expected | Result |
|------|-----------|-----------|-------------|---------------|------|----------|--------|
| A | Correct | 3 passed | **PASS** (APPROVED_WITH_COMMENTS) | Two test-quality notes (the error message is not checked; no float-zero or negative-quotient case), not blocking | passed | Pass | PASS |
| B | `a // b` (floor division) | 3 passed | **FAIL** (REVISION_REQUIRED) | "calc.py:16 \| `divide` uses floor division `a // b`, so `divide(7, 2)` returns 3 instead of the specified 3.5", and why the tests missed it: they only use exact divisions | failed: review + the platform's check | Fail | PASS |
| C | No `multiply`, no zero check | 2 passed | **FAIL** (REVISION_REQUIRED) | "`multiply(a, b)` is missing", "`divide(x, 0)` raises ZeroDivisionError instead of `ValueError('division by zero')`", and the tests that skip both | failed: review + the platform's check | Fail / Incomplete | PASS |

QA took 26–40 s per review. Each goal was closed after judging (A cancelled, B and C failed) and never merged.

### Offline

`tests/test_phase7_quality.py` (9 cases): PM, developer and QA each on their own stub provider, through the real loop.

| Check | Pass condition | Evidence | Result |
|-------|----------------|----------|--------|
| A passes, B and C fail | A correct factorial passes. One whose own tests were rewritten to expect the bug, and one without input validation, fail on QA's FAIL | `test_qa_and_the_gate_pass_a_and_fail_b_and_c` (3) | PASS |
| A rubber-stamp review | QA approves the buggy delivery; the goal's own command, run by the gate, still fails it | `test_a_rubber_stamp_review_does_not_pass_bad_work` | PASS |
| A verdict without evidence | "Looks good to me. VERDICT: PASS" without the logic check is not a pass | `test_a_bare_verdict_without_the_logic_check_is_not_a_pass` | PASS |

Before the live run, the script ran against a throwaway database where the offline simulator stood in for QA and
approved all three. The gate still failed B and C on the platform's own check.

## 7.3 Evaluation criteria

| Plan's dimension (weight) | Where it is measured |
|---------------------------|----------------------|
| Functional correctness (30%) | Command checks run by the gate: the task's acceptance commands, the goal's quality commands, staged unit/integration checks |
| Requirement compliance (20%) | Acceptance conditions of each task contract; requirement traceability from tasks and checks (S08-R-01) |
| Test coverage and results (15%) | The unit stage and the test strategy's mandatory stages (S09-R-02); coverage percentage is not measured |
| Code quality (10%) | Static checks (syntax, scope, change boundaries, baseline) and QA's findings |
| Security (10%) | Secret scan, dependency audit (S08-R-05), a security review for high-risk tasks |
| Reliability (10%) | The eval harness's reliability across repeats (S09-R-05) |
| Documentation (5%) | Only where a task's acceptance asks for it |

| Rule | What happens | Evidence | Result |
|------|--------------|----------|--------|
| A critical security failure blocks approval regardless of the score | A hard-coded API key, with every task condition and QA's review passing: the secret scan fails the gate and the goal | `test_a_critical_security_failure_blocks_approval_whatever_else_passed` | PASS |
| A failed mandatory acceptance test blocks approval regardless of the score | The developer's tests fail when the gate runs them: the task fails, QA's review of nothing is skipped ("every dependency failed"), the iteration fails | `test_a_failed_mandatory_acceptance_test_blocks_approval` | PASS |
| A weighted total score | **Not implemented, by decision:** S09-R-05 (`docs/requirements/ASEAO09-quality.md`) keeps separate dimensions with sample sizes and "no single opaque score". Every failed check blocks, so there is no score for a failure to hide behind. The plan calls its weights "initial suggestions" | — | PARTIAL (by design) |

## 7.4 Evaluator independence

| Check | Evidence | Result |
|-------|----------|--------|
| The developer never decides its own work is correct | The review task is staffed by a QA agent, never the developer; the developer's "3 passed" sits beside the gate's own run, never instead of it (`test_the_developer_never_reviews_its_own_work`; phases 3 and 4) | PASS |
| Separate responsibilities | Developer, QA, gate and PM as in the table above; the PM never grades | PASS |
| A reviewer from another model family | `loop._developer_provider` asks the router for a reviewer of another family **when the QA's chain has one**. On this server QA's only model is Claude Code (agent preference), the developer's route is Claude Code too: in phase 6 both ran on Anthropic (runs 2125, 2126). Models → Routes → Assign models gives review "another family than the lead's" (V26) | PARTIAL (V26) |

## 7.5 The correction cycle

`test_qa_finds_a_bug_the_pm_plans_the_fix_the_developer_fixes_qa_and_the_gate_confirm`:

| Step | What the records show | Result |
|------|-----------------------|--------|
| 1. QA reports the bug | Iteration 1: `VERDICT: FAIL` with a FINDING naming the line and input | PASS |
| 2. The PM creates a correction task | The GM's second plan holds the finding | PASS |
| 3. The developer fixes it | The developer's second task holds the finding; the defect is assigned to it (S09-R-03) | PASS |
| 4. QA runs the tests again | Iteration 2: `VERDICT: PASS` | PASS |
| 5. The evaluator checks the new result | The gate's own rerun moves the defect detected → assigned → fix_submitted → retested; a developer's report never does | PASS |
| 6. Project status updates | The goal ends approved (or waiting for approval); `Goal.history` is [failed, passed] | PASS |
| The original failure is kept | Iteration 1's tasks, outputs and gate result stay | PASS |

Live, phase 4 ran the same cycle with a broken factorial (`test_a_failed_result_goes_back_to_the_pm_who_delegates_the_correction`).

## Findings

- **V26 (open, owner):** an independent review on another model family depends on the routes. QA's own model is
  Claude Code and the developer's route is Claude Code, so on this server developer and QA run on the same family
  (phase 6, runs 2125/2126; phase 7's QA on Claude Code). They are separate agents and sessions, and the gate's
  checks do not depend on either. Models → Routes → **Assign models** (or clearing QA's `model_pref` and giving the
  `qa` route a model of another family) would make reviews cross-family. Not changed: model assignment is the
  owner's.

## Phase 7 acceptance

| Criterion | Result |
|-----------|--------|
| 7.1/7.2 QA passes correct work, fails buggy and incomplete work | PASS (live 3/3, offline 3/3) |
| 7.3 Mandatory failures block regardless of score | PASS; weighted score PARTIAL by design (S09-R-05) |
| 7.4 Evaluator independence | PASS for roles; PARTIAL for model family (V26) |
| 7.5 Correction cycle with history | PASS |

## Phase 8 starts from here

Phase 8 (continuous evolution): a fixed evaluation set for one agent, v1 against a v2 candidate on the same set,
regression and held-out sets, approval or rejection, rollback.
