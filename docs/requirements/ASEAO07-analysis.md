# ASEAO SPEC-07 — Analysis and decision intelligence

Source: `/home/luca/update/ASEAO07.docx`. Master criterion (`.rtf`): *every important technical
decision must have a recorded rationale, alternatives, supporting evidence, limitations and
responsible approver. The result is a decision report, not simply a majority vote.*

Status: no analysis room or analysis requests exist; performance metrics exist (§15, done in
`performance.report`). The analysis department itself (Chief Analysis Officer, statistical,
strategic teams) is **gated**: CLAUDE.md keeps non-core roles disabled until the pipeline is
reliable. The requirements below are the parts the core pipeline needs now.

## Functional requirements (spec IDs kept)

| ID | Requirement | Status |
| --- | --- | --- |
| S07-FR-01 | Create analytical requests | gated |
| S07-FR-02 | Each request defines an analytical question | gated |
| S07-FR-03 | Reference multiple projects and research reports | gated |
| S07-FR-04 | Generate an analytical plan | gated |
| S07-FR-05 | Select analytical methods | gated |
| S07-FR-06 | Assign specialized analysts | gated |
| S07-FR-07 | Parallel analysis | gated (scheduler supports it) |
| S07-FR-08 | Track evidence and assumptions | done (S07-R-01: evidence classes on decisions, assumptions apart) |
| S07-FR-09 | Independent review | done (S07-R-01: `decisions.review`, never by the author) |
| S07-FR-10 | Structured recommendations | done (S04-R-02 decision records with the S07-R-01 report fields) |
| S07-FR-11 | Budgets and deadlines | gated |
| S07-FR-12 | Reproducible results | partial (S07-R-03): hypotheses record their test method, expected and actual outcome and the test's source; experiments keep suite, repeats and sample sizes |

## Specific requirements

### S07-R-01 Decision reports with alternatives — §13, master criterion
- Implemented by SPEC-04 S04-R-02 (`decisions` table) with the §13.1 fields: question, options,
  recommended option, criteria, evidence refs, confidence with an explanation (§14), approval
  required, status. A recommendation is not authorisation to implement.
- Facts, hypotheses and assumptions are labelled separately (AT-007) using the SPEC-11 evidence
  classes.
- **Status: done** (`decisions.py`, columns `question`, `criteria`, `limitations`, `confidence_note`, `reviews` on
  `decisions`; `tests/test_decision_reports.py`). Each evidence entry may carry a SPEC-11 class
  (`{"ref": …, "class": "MEASURED"}`); entries are stored as given (kgraph reads them), unknown classes are dropped,
  unlabelled ones count as `UNKNOWN` (a `hypothesis` reference as `HYPOTHESIS`, an eval or experiment as
  `MEASURED`); rows carry `evidence_labelled`. `decisions.report` groups the evidence by class with the assumptions
  as `ASSUMPTION`, and gives a Markdown report and `missing`: the empty parts (rationale, alternatives, criteria,
  evidence, limitations, confidence explanation) plus, for an important decision (a person approves it, financial
  or critical, or escalation level 3-5), the responsible approver until a person decided and an independent review.
  Missing parts are shown, not enforced: decisions from topic summaries and older records stay valid.
  `authorised` is true only for an approved decision. Independent review (S07-FR-09): `decisions.review` by anyone
  but the author (`PermissionError`), once per reviewer, `agree|concerns|disagree` (the last two need a note); a
  dispute of an automatically approved or open decision makes it `under_review` with `requires_human_approval`, so
  it blocks the merge and notifies the administrators; a person's approval stands and the dispute is only recorded.
  The planner's JSON and the conflict review (`CRITERIA:`, `LIMITATIONS:`, `CONFIDENCE_NOTE:` lines, the tasks'
  recommendations as `INFERENCE`) fill the fields; the self-evolution deploy decision names its alternative,
  criteria and limitations. API: `GET /api/decisions/{id}` (the report), `POST /api/decisions/{id}/review` (run
  permission, audited); GM toolbox `gm decision <id>`, `gm decision-review <id> <verdict> --note`; the goal page
  shows criteria, labelled evidence, limitations, reviews and what is missing, with review buttons.

### S07-R-02 Root-cause analysis of failing goals — §10 (AT-012)
- When a goal fails (all iterations used) or the same check fails in 3 consecutive goals of a
  project, the GM (or the analysis room when enabled) runs an RCA task with: task history,
  repeated blockers, assignments, dependency structure, estimated vs actual time, failed reviews,
  executor/infrastructure errors (from the trace, SPEC-04 S04-R-12).
- Output: confirmed causes vs plausible contributing factors (never correlation as cause),
  corrective actions as proposals, and a follow-up check whether the action worked.
- Stored as a knowledge item of type `operational` (SPEC-11).
- **Status: done** (`rca.py`, from `loop._notify_goal`; `GET /api/goals/{id}` → `rca`, the goal page's "Root-cause
  analysis"; `tests/test_rca.py`). Triggers: a goal failed with all its iterations used, or the same check (not a
  per-plan `task …` check) failed in the last `STREAK` (3) finished goals of a project; the streak is analysed once
  (again only after three more failing goals), and when both hold the streak analysis covers the goal. Benchmark
  and improvement goals are left out. The platform collects the evidence: per goal its status and iterations, the
  defects with their iterations and detail, the last iteration's tasks (role, agent, status/stage, dependencies,
  estimated against actual minutes, failed conditions), failed runs, error and escalation events and the goal's
  open blockers, each with a reference. The General Manager answers with `CONFIRMED:`, `CONTRIBUTING:`, `ACTION:`
  and `FOLLOW_UP:` lines; a confirmed cause that cites no reference of the pack is kept as contributing with the
  reason (correlation is not cause). The analysis is a knowledge item of kind `operational`, class `INFERENCE`,
  screened like every lesson; up to three actions become pending proposals (only when the item passed the
  screen). Follow-up: when a goal spawned from one of them ends, the action is marked `worked` (it passed and none
  of the watched checks failed) or `did not work` with the checks still failing. The analysis is one model run billed
  to the failed goal (and its project; a goal's own exhausted budget does not stop it, it is the last spend on the
  goal); none is made once the project's budget is used up. Not taken: the analysis room (gated); the General
  Manager analyses until it is enabled.

### S07-R-03 Hypotheses — §9 (AT-008)
- `hypotheses` (or knowledge items of class `HYPOTHESIS`): statement, reason, supporting and
  contradicting evidence, test method, expected/actual outcome, status
  (`proposed→investigating→testing→supported|rejected|inconclusive→archived`).
- Used by SPEC-13 experiments (each experiment states its hypothesis). An agent cannot mark a
  hypothesis supported without a recorded test result.
- **Status: done** (table `hypotheses`, `hypotheses.py`, `tests/test_hypotheses.py`). Transitions exactly as above,
  plus archiving a proposed or investigating hypothesis (dropped) and inconclusive → investigating (tested again).
  Evidence for and against is a list of `{type, id, note, by, at}`. `record_result` stores the actual outcome and the
  test's source; `set_status(..., "supported")` raises `PermissionError` for an agent (`agent:*`, `system`) without
  a recorded result, and asks a person for a note instead. `experiments.record` turns a stated hypothesis into one
  in `testing` (test method: the suite and repeats; expected: beating the baseline by `min_improvement`), and the
  comparison written by `experiments.update` concludes it (better → supported, not better → rejected, a side
  with no runs → inconclusive; evolution smoke trials use `passed`). API: `GET/POST /api/hypotheses`,
  `POST /api/hypotheses/{id}/evidence|result|status` (result and status for administrators; all audited); the
  GM's toolbox `gm hypothesis|hypotheses|hypothesis-status` acts as an agent; Knowledge → Hypotheses. Not done:
  hypotheses as knowledge items of class `HYPOTHESIS` (they are their own table; knowledge items keep the class
  for lessons).

### S07-R-04 Predictions with calibration — §11 (AT-014..016) — P2
- Planner time/cost estimates are predictions; record them with the actual outcome
  (SPEC-05 S05-R-05) and show estimate accuracy per role in the Performance view.
- **Status: done** (`performance.calibration`, `report()["calibration"]`, Performance → Estimate accuracy;
  `tests/test_performance.py`). The prediction is the task contract's `estimated_minutes` (S05-R-05), the outcome
  the summed duration of the task's runs; per role over finished tasks of non-benchmark goals: samples, estimated
  and actual totals, the median actual/estimate ratio, the mean absolute percentage error and the share within
  ±50%. Goal cost: `estimated_cost_usd` (the order's or the planner's) against the model cost of finished goals.
  A label (calibrated, underestimates above ×1.5, overestimates below ×0.67) only from `CALIBRATION_MIN` (5)
  samples; fewer are shown as "too few samples". Not taken: AT-015/016 forecasts of other kinds (there is no other
  prediction source yet).

### S07-R-05 Opportunity detection — §12 (AT-017) — gated
- Belongs to the innovation pipeline (ASEAO10); enable with the analysis/innovation rooms.
