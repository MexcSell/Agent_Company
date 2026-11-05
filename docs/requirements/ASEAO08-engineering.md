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
| S08-FR-02 | Convert ideas into requirements | done (S08-R-01; the GM writes drafts, a person approves) |
| S08-FR-03 | User stories and use cases | done (S08-R-01 kinds `story` and `use_case` with templates: a story is "As a <actor>, I want <what>, so that <benefit>", a use case has labelled actors, preconditions, numbered main flow, alternatives and postconditions; parsed into `Requirement.details` or given as `details`, refused without actor and want / actors and main flow, versioned with the text, shown to the planner; `requirements.actors` and the traceability's `actors` list who they serve; `tests/test_requirement_templates.py`) |
| S08-FR-04 | Define acceptance criteria | done (contract `acceptance`) |
| S08-FR-05 | Functional and non-functional requirements | done (S08-R-01 `kind`) |
| S08-FR-06 | Dependencies and constraints | done (`depends_on`, `allowed_paths`) |
| S08-FR-07 | Requirement versions | done (S08-R-01 `version`, `history`) |
| S08-FR-08 | Link requirements to architecture, tasks, code, tests | done for tasks, checks, merge commits and decisions (S08-R-01); ADRs open → S08-R-02 |
| S08-FR-09 | Detect conflicting requirements | done (S08-R-01: planner instruction plus a negation/word-overlap backstop → decision) |
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
- **Status: done** (`requirements.py`, tables `requirements` and `requirement_links`, `Goal.requirement_keys`;
  `tests/test_requirements.py`). Keys are `REQ-001…` per project or a document's own ID; kinds
  `functional|non_functional|story|use_case`. A change of text, kind or acceptance is a new version (the earlier one in
  `history`) and sends an approved, implemented or verified requirement back to `draft`. Only a person approves
  (`PermissionError` for agents); `verified` needs a passing check on the current version. Goals take
  `requirement_keys` (unknown keys are refused; the project's keys named in the title or description are added); the
  planner's prompt lists the goal's requirements in full and at most eight related approved ones (never the whole
  list) and asks for `requirement_keys` on tasks and a decision for contradicting requirements. After each gate
  `on_gate` links every task to its requirements (passed or not) and every quality command to all of the
  iteration's requirements (unknown keys in a plan are logged, not fatal); the merge (`on_merged`, idempotent)
  records the commit, then approved becomes implemented, or verified when a check passed on the current version;
  a draft is linked but not moved. `traceability(project_id)` gives per requirement (current version) implemented,
  tested, merged, failing checks, goals and the counts. Conflict backstop: exactly one of two requirements negated
  and the same explicit subject or at least 70% of their words in common → one decision per pair for a person
  (no goal, so it notifies and does not hold a merge). `import_docs` reads `<project>/docs/requirements/*.md`
  (the protected platform project falls back to this repository's): table rows and `### ID` sections; done →
  implemented, partial/open → approved, gated or no status → draft, skip → retired; unchanged texts are left
  alone, changed ones get a new version with the document's status; no conflict check on import. API:
  `GET/POST /api/projects/{id}/requirements`, `GET /api/projects/{id}/traceability`,
  `POST /api/projects/{id}/requirements/import` (admin), `GET/PATCH /api/requirements/{id}`,
  `POST /api/requirements/{id}/status` (admin), all mutations audited; GM toolbox `gm requirement`,
  `gm requirements [--trace]`, `gm goal --requirement`; CLI `requirements list|trace|import [--project]`; the
  project's Plan tab lists requirements with implemented/tested/merged and conflicts.

### S08-R-02 Architecture Decision Records — §7.2 (AT-005)
- The Architect writes ADRs for significant technology choices to `docs/adr/NNNN-title.md` in the
  project worktree (context, options, decision, reasons, consequences, status), and a matching
  decision record is stored (S04-R-02) with the file path.
- ADRs are versioned by git; a superseding ADR references the old one.
- Acceptance: a goal whose plan includes a technology choice produces one ADR file and one
  decision record.
- **Status: done, adapted** (`adr.py`, column `decisions.adr_path`; `tests/test_adr.py`). The loop writes the ADR
  from the decision record instead of an Architect task (deterministic, and the record is the single source): every
  `technical` or `critical` decision of the plan (unless its workflow template changes no files, like `research`)
  becomes `docs/adr/NNNN-<topic>.md` in the goal's worktree before
  the plan checkpoint, so it is committed and merged with the branch. NNNN is the decision's id (unique when several
  goals work on one repository at once; the ADR and its record name each other). Sections: context (the question),
  options, criteria, decision, reasons with labelled evidence, consequences (assumptions, limitations, confidence);
  status Accepted, Proposed (waits for a person), Rejected or Superseded. A later decision on the same topic of the
  same project (of the same goal outside a project) names the earlier ADR (`Supersedes:`) and sets the earlier
  file's status to `Superseded by …`; a re-plan repeating the same recommendation reuses the existing ADR. When a
  person decides, or a review disputes an automatic approval, the status line follows (committed at once, only that
  file, when the goal waits for approval; with the next checkpoint while it works). The gate's scope check accepts
  exactly these files (`adr.paths`); a failure to write one is logged and never stops the goal. The goal page
  shows each decision's ADR path.

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
- **Status: done** (`quality.REVIEW_CHECKLIST`, `review_verdict`, `merge_findings`; the checklist is
  added to every review task's prompt).

### S08-R-04 Change boundaries — §12.2 (AT-014, AT-018)
- Extend `checks.py`: detect disabled/skipped tests added in the diff (`@pytest.mark.skip`,
  `it.skip`, `xfail` without reason), removed assertions in test files, new dependencies in
  `pyproject.toml`/`package.json`/lock files (needs a decision record or task permission
  `dependencies`), and edits to protected configuration (`.env*`, CI files) unless allowed.
- Acceptance: a diff adding `@pytest.mark.skip` to an existing test fails the gate.
- **Status: done** (`checks.boundary_violations`, gate check `boundaries`; goal permits
  `dependencies`, `config`, `skip_tests`, administrators only, `--permit` on the CLI).

### S08-R-05 Dependency control — §15 (AT-021, AT-022)
- On merge, record new/changed dependencies; when `pip-audit`/`npm audit` are installed, run them
  as gate checks for changed manifests. SBOM generation (P2).
- **Status: done** (`dependencies.py`: `changes`, `audit`, `check`, `on_merged`; setting
  `dependency_audit` off|warn|block, a governance key; `tests/test_dependencies.py`). `changes` itemises every
  manifest and lock file the change touched (requirements files, `pyproject.toml` with optional dependencies,
  dependency groups and Poetry tables, `package.json`, `package-lock.json`, `uv.lock`, `poetry.lock`, `Cargo.toml`/
  `.lock`, `go.mod`; other manifests count as changed without names) between the merge base and the tested commit,
  both sides read from git objects; a manifest that is a symlink is recorded as such and never followed. The gate
  (after the change boundaries, which still need `--permit dependencies`) runs `pip-audit -r <file> --no-deps
  --disable-pip` on a changed pinned requirements file and on `uv export --frozen` of a `uv.lock` (the export
  offline, never building the project), and `npm audit --package-lock-only` where a `package-lock.json` is; fixed
  argument lists, the worktree read-only, in the L3 sandbox with network as the goal's sandbox mode allows. A known
  vulnerability in a dependency the change added or changed fails a `dependency audit <tool> <dir>` row of the
  `security` stage (a defect of severity high, so `warn` can unblock a goal whose advisory has no fix); one in an
  untouched dependency is listed in the row and passes. An audit that cannot run (tool missing, unpinned
  requirements, no lock file, no report, sandbox required and unavailable) adds a note, never a passing row. The
  goal history keeps `dependencies` (changes and notes) per iteration, the goal page shows them, and the merge
  logs the last iteration's changes as a `dependencies` event with the commit. An audit row is retested by the
  next gate, not by the defect `retest` (S09-R-03), which re-runs command checks only.
  SBOM (`dependencies.sbom`, `store_sbom`): a CycloneDX 1.5 JSON document of one commit, read from git objects only
  (no checkout, install or build). Every manifest and lock file in the tree is itemised except under
  `node_modules/`, `.venv/`, `vendor/` and symlinked ones; lock files give exact versions (the project's own
  editable/virtual/workspace packages and linked npm packages left out), a manifest's entry has a version only when
  it pins one (else its specifier as a property) and gives way to a lock file in the same directory naming the same
  package; package URLs for pypi, npm, cargo and golang; formats not itemised are listed as `unparsed`; the serial
  number is derived from repository and commit. The merge writes `data/sbom/<commit>.cdx.json` and an `sbom` event;
  `GET /api/goals/{id}/sbom` downloads it (goal page, merged goals; built again if the file is gone),
  `GET /api/projects/{id}/sbom?rev=` builds one for any branch, tag or commit of a project the user can see, and
  `python -m agent_company sbom [repo] [--rev] [--out]` on the command line.

### S08-R-06 Release candidates and rollback — §20 (AT-024..027) — gated
- Agents never deploy (CLAUDE.md). Release candidates = approved merges with tag; rollback =
  `git revert` of a merge commit by an administrator action. P2.
