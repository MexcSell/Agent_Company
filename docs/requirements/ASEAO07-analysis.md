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
| S07-FR-08 | Track evidence and assumptions | open → S07-R-01 |
| S07-FR-09 | Independent review | open → S07-R-01 |
| S07-FR-10 | Structured recommendations | open → SPEC-04 S04-R-02 (decision records) |
| S07-FR-11 | Budgets and deadlines | gated |
| S07-FR-12 | Reproducible results | open → S07-R-03 |

## Specific requirements

### S07-R-01 Decision reports with alternatives — §13, master criterion
- Implemented by SPEC-04 S04-R-02 (`decisions` table) with the §13.1 fields: question, options,
  recommended option, criteria, evidence refs, confidence with an explanation (§14), approval
  required, status. A recommendation is not authorisation to implement.
- Facts, hypotheses and assumptions are labelled separately (AT-007) using the SPEC-11 evidence
  classes.

### S07-R-02 Root-cause analysis of failing goals — §10 (AT-012)
- When a goal fails (all iterations used) or the same check fails in 3 consecutive goals of a
  project, the GM (or the analysis room when enabled) runs an RCA task with: task history,
  repeated blockers, assignments, dependency structure, estimated vs actual time, failed reviews,
  executor/infrastructure errors (from the trace, SPEC-04 S04-R-12).
- Output: confirmed causes vs plausible contributing factors (never correlation as cause),
  corrective actions as proposals, and a follow-up check whether the action worked.
- Stored as a knowledge item of type `operational` (SPEC-11).

### S07-R-03 Hypotheses — §9 (AT-008)
- `hypotheses` (or knowledge items of class `HYPOTHESIS`): statement, reason, supporting and
  contradicting evidence, test method, expected/actual outcome, status
  (`proposed→investigating→testing→supported|rejected|inconclusive→archived`).
- Used by SPEC-13 experiments (each experiment states its hypothesis). An agent cannot mark a
  hypothesis supported without a recorded test result.

### S07-R-04 Predictions with calibration — §11 (AT-014..016) — P2
- Planner time/cost estimates are predictions; record them with the actual outcome
  (SPEC-05 S05-R-05) and show estimate accuracy per role in the Performance view.

### S07-R-05 Opportunity detection — §12 (AT-017) — gated
- Belongs to the innovation pipeline (ASEAO10); enable with the analysis/innovation rooms.
