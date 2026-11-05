# ASEAO SPEC-10 — Innovation, research-to-product and experimental laboratory

Source: `/home/luca/update/ASEAO10.docx`. Master criterion (`.rtf`): *a proposed product cannot
become a full development project until its requirements, feasibility and intended value have
been documented.* `1.txt`: *come up with new ideas and put them into practice; new technology lab,
new technology introduction room, product introduction room.*

Status: the innovation department (CINO, strategy, creative, experimental lab, evaluation,
technology transfer — §3) is **gated** (CLAUDE.md). Rooms for the new technology lab and
introduction rooms exist in `org.py` but are disabled. Agent proposals that become goals are the
existing, minimal innovation path (`projects.extract_proposals`).

## Specific requirements (to implement now, inside the core pipeline)

### S10-R-01 Proposals need a documented case before becoming projects — master criterion
- A proposal of kind `product` or `new_project` must contain: concept, target user, expected
  value, feasibility notes, requirements draft, estimated cost. Missing fields → it stays a
  proposal (not convertible). Ordinary follow-up proposals inside a project are unaffected.
- **Status: done** (`Proposal.kind` `follow_up|product|new_project`, `Proposal.case`, `spawned_project_id`;
  `projects.CASE_FIELDS`, `case_missing`, `set_case`, `decide` → `_to_project`; `POST /api/proposals/{id}/case`
  (admin, audited); Projects → Proposals waiting → Documented case; `tests/test_innovation.py`). An agent writes
  `PROPOSAL[product]: <title> :: <why>` (or `[new_project]`) followed by `- Concept:`, `- Target user:`,
  `- Expected value:`, `- Feasibility:`, `- Requirements:`, `- Estimated cost:` lines (the task prompt says so). Approving
  one with a field missing answers 400 naming the missing fields, and it stays `pending`; an administrator completes the
  case (or changes the kind) on the proposal. Only a person approves one — the GM's triage (`proposal_policy =
  superiors`) always leaves it to a person — and a complete case becomes a **project**, not a goal: the case is its
  description, the requirements draft its first `draft` requirement (S08-R-01, source `proposal:<id>`). The goal-chain
  guards (`max_proposal_goals`, depth) limit spawned goals and do not apply. Follow-up proposals are unchanged.

### S10-R-02 Experiments are SPEC-13 experiments — §10–§12
- Innovation experiments use the `experiments` table from SPEC-13 S13-R-05 (baseline, candidate,
  hypothesis, metrics, dataset, source commit, configuration) so they are reproducible (AT-016,
  AT-017) and stop on budget (AT-025, AT-026).
- Prototypes run only in worktrees/scratch repositories; never in production (AT-024).

### S10-R-03 What may evolve autonomously — §16.2, §16.3
Allowed (subject to SPEC-13 S13-R-04): team composition, scheduling, retrieval methods, model
routing proposals, prompt templates/playbooks, workflow templates, benchmark selection, knowledge
organisation. Never autonomous: authority, security policies, authentication, access control,
approval requirements, audit retention, spending limits, production access, emergency stop
(enforced by SPEC-14 S14-R-02).

## Gated (enable with the innovation rooms)

| ID | Item | Spec |
| --- | --- | --- |
| S10-G-01 | Opportunity register fed by research events and internal data (defects, slow tasks) | §6, AT-001, AT-002 |
| S10-G-02 | Multi-perspective idea generation with prior-art search | §7, §8, AT-003..005 |
| S10-G-03 | Proposal review by an independent evaluator | §9, AT-007 |
| S10-G-04 | Evaluation report: value, feasibility, risk, cost; decisions continue/pivot/stop | §13 |
| S10-G-05 | Portfolio view and technology radar (adopt/trial/assess/hold) | §14, §21.1 |
| S10-G-06 | Technology transfer package to engineering (prototype, docs, tests, ADRs) | §15, AT-022 |
