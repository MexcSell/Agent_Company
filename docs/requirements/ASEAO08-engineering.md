# ASEAO SPEC-08 — Software engineering and product development

Source: `/home/luca/update/ASEAO08.docx`; review guidance from
`/home/luca/update/extensions/analysis_results.md` (an agent-improvement report on a PR-review
agent). Master criterion (`.rtf`): *an assigned team can develop a feature independently,
integrate it without overwriting other work, pass automated tests and produce a deployable
artifact.* The `.rtf` engineering rule: every development task includes requirements, I/O
specifications, API contracts, relevant source, instructions, unit and integration tests,
documentation and acceptance criteria.

Already met: contract-driven tasks (`contracts.py`), isolated worktrees and branches per goal
(`worktrees.py`), one writer per worktree (`scheduler`), protected tests and allowed paths
(`checks.scope_violations`), secret scan, syntax checks, baseline checks, approval before merge,
push only on `/sync`. The 13 engineering teams (§3) are **gated** (CLAUDE.md).

## Functional requirements (spec IDs kept)

| ID | Requirement | Status |
| --- | --- | --- |
| S08-FR-01 | Register product ideas | done as proposals; product register gated (ASEAO10) |
| S08-FR-02 | Convert ideas into requirements | open → S08-R-01 |
| S08-FR-03 | User stories and use cases | open → S08-R-01 |
| S08-FR-04 | Define acceptance criteria | done (contract `acceptance`) |
| S08-FR-05 | Functional and non-functional requirements | open → S08-R-01 |
| S08-FR-06 | Dependencies and constraints | done (`depends_on`, `allowed_paths`) |
| S08-FR-07 | Requirement versions | open → S08-R-01 |
| S08-FR-08 | Link requirements to architecture, tasks, code, tests | open → S08-R-01 |
| S08-FR-09 | Detect conflicting requirements | open → S08-R-01 |
| S08-FR-10 | Human review and approval | done (merge approval) |

## Specific requirements

### S08-R-01 Requirements and traceability — §5, §5.3 (AT-002, AT-003, AT-031)
- Table `requirements`: `project_id`, key (`REQ-001`), text, kind (`functional|non_functional`),
  acceptance criteria, version, status (`draft|approved|implemented|verified|retired`), source
  (user message, document, proposal).
- Goals and task contracts carry `requirement_keys`; the gate records which checks verified which
  requirement; merges record the commit. A traceability query answers "which requirements are
  implemented, tested and merged" per project.
- The planner flags requirements that contradict each other (same subject, opposite statements)
  as a decision record (SPEC-04 S04-R-02).
- The files in `docs/requirements/` are the first content for the platform project itself.

### S08-R-02 Architecture Decision Records — §7.2 (AT-005)
- The Architect writes ADRs for significant technology choices to `docs/adr/NNNN-title.md` in the
  project worktree (context, options, decision, reasons, consequences, status), and a matching
  decision record is stored (S04-R-02) with the file path.
- ADRs are versioned by git; a superseding ADR references the old one.
- Acceptance: a goal whose plan includes a technology choice produces one ADR file and one
  decision record.

### S08-R-03 Review checklist with logic verification — §13.2, §13.3, `analysis_results.md`
- The QA review prompt and output contract change from a bare `VERDICT: PASS|FAIL` to:
  1. **Logic verification first**: trace the data flow of each changed function for the normal
     case, an edge case and an error case; state whether the change actually fixes the root cause
     of the task (not a band-aid). Code that looks simple but is not shown to work is not passed.
  2. Checklist (§13.2): contract satisfied, interfaces compatible, error handling, edge cases,
     tests sufficient, dependencies justified, security, performance, docs updated.
  3. Each finding names file and line, the concrete risk (what breaks, for whom), and a concrete
     fix (patch or exact replacement text) — not a theoretical concern.
  4. One finding per distinct issue; repeated occurrences are listed under one finding.
  5. Outcome (§13.3): `APPROVED | APPROVED_WITH_COMMENTS | REVISION_REQUIRED | REJECTED |
     ESCALATED`, still ending with `VERDICT: PASS|FAIL` for the gate
     (PASS = the first two).
- The reviewer never reviews its own change (exists: different agent, different provider).
- Acceptance: a fake review missing the logic-verification section is treated as FAIL by the
  gate; duplicate findings are merged.

### S08-R-04 Change boundaries — §12.2 (AT-014, AT-018)
- Extend `checks.py`: detect disabled/skipped tests added in the diff (`@pytest.mark.skip`,
  `it.skip`, `xfail` without reason), removed assertions in test files, new dependencies in
  `pyproject.toml`/`package.json`/lock files (needs a decision record or task permission
  `dependencies`), and edits to protected configuration (`.env*`, CI files) unless allowed.
- Acceptance: a diff adding `@pytest.mark.skip` to an existing test fails the gate.

### S08-R-05 Dependency control — §15 (AT-021, AT-022)
- On merge, record new/changed dependencies; when `pip-audit`/`npm audit` are installed, run them
  as gate checks for changed manifests. SBOM generation: `open` (P2).

### S08-R-06 Release candidates and rollback — §20 (AT-024..027) — gated
- Agents never deploy (CLAUDE.md). Release candidates = approved merges with tag; rollback =
  `git revert` of a merge commit by an administrator action. P2.
