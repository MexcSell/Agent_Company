# ASEAO SPEC-09 — Quality assurance, testing and verification

Source: `/home/luca/update/ASEAO09.docx`. Master criterion (`.rtf`): *critical failures block
release; all test results must be traceable to code versions and the corresponding requirements.*
The `.rtf` test stages: static analysis, unit, integration, API, functional, performance,
security, regression, user acceptance, release verification.

Already met: quality gate with contracts, QA verdict, check commands, baseline, secret, syntax and
scope checks (`quality.py`, `checks.py`); defects opened/closed/reopened automatically
(`performance.track_defects`, AT-013, AT-016); eval harness with suites and comparisons
(`evaluation.py`, AT-019, AT-022, AT-023). The QA department of nine teams (§3) is **gated**.

## Specific requirements

### S09-R-01 Named quality gates per stage — §13 (AT-024)
- The gate result (`Task.gate`/goal history) is organised by stage, each `pass|fail|skipped` with
  evidence: `requirements` (traced, S08-R-01), `static` (syntax, scope, secrets, change
  boundaries S08-R-04), `unit`, `integration`, `regression` (baseline comparison), `security`
  (secret scan, dependency audit), `review` (S08-R-03), `governance` (approval, open decisions).
- Check commands are tagged with a stage (`{"run": "...", "stage": "unit"}`; untagged = `unit`).
- Merge is blocked when any mandatory stage fails or a critical defect is open (AT-018).
- Acceptance: the goal page shows one row per stage; an open critical defect blocks `approve`.
- **Status: done** (`quality.STAGES`, `stages`, `split_stage`; goal page "Gate by stage"). Commands are
  tagged with a prefix in the command string (`[integration] pytest tests/it`) rather than a JSON object,
  so `quality_commands` stays a list of strings. `requirements` stays `skipped`: traceability (S08-R-01, S09-R-06)
  is recorded by `requirements.on_gate` after the gate, and unknown keys are logged, not fatal, so it has no
  pass/fail of its own;
  `security` holds the secret scan and, when manifests changed, the dependency audit (S08-R-05); `governance` is `waiting` while a
  decision needs a person (it holds the merge, it does not fail the gate).

### S09-R-02 Risk-based test strategy — §6 (AT-001)
- The planner derives a short test strategy per goal from project classification (SPEC-14
  S14-R-05), changed areas, and previous defects in those files: which stages are mandatory and
  which reviewers (security reviewer when auth/crypto/permissions files change).
- Stored in the goal; high-risk goals require `integration` and `security` stages.
- **Status: done** (`teststrategy.py`: `derive`, `escalate`, `planner_note`, `Goal.test_strategy`;
  `quality.evaluate`; `tests/test_teststrategy.py`). Inputs: the goal's classification (with its project's, S14-R-05:
  confidential → medium, restricted/secret → high on their own), the changed areas (allowed paths, each task's
  `in_scope`, the files changed so far), sensitive areas (a whole word of the path, split on `/ . _ -` and camelCase:
  auth, login, session, token, password, secret, crypto, permission/ACL/RBAC, OAuth/JWT/SSO, credentials, env, keys →
  high; documentation files and `docs/` never count), tasks the planner marked
  `risk_level: high` (→ high), and files earlier goals on the same repository changed in an iteration the gate failed
  (`Goal.history[].changed_files`, recorded from now on → medium, named to the planner). The planner sees a
  preliminary strategy; the one with the plan is stored per iteration and shown on the goal page. Writing tasks
  touching a sensitive area become `risk_level: high`, so the existing security review follows them (the reviewer
  is QA until the Security Room is enabled, G-R-04). High risk makes `integration` and `security` mandatory: the gate
  adds a `stage <name>` row per mandatory stage that fails when no check of that stage ran (a defect of severity
  high). The planner satisfies `integration` with a command acceptance condition carrying `"stage": "integration"`
  (`contracts.Condition.stage`: unit|integration|security|static); the gate runs it again itself on the tested
  commit as a `command` row of that stage (S09-R-04), for done and failed tasks. The `security` stage holds the secret
  scan (and the dependency audit when manifests changed, S08-R-05), so it is met whenever the goal has a base branch. Templates that
  change no files (`strategies.skips_checks`) have no mandatory stages.

### S09-R-03 Defect fields and lifecycle — §11 (AT-014, AT-015)
- Separate `severity` (`critical|high|medium|low|info`, exists via `severity_of`) and
  `priority` (new). Lifecycle adds `assigned`, `fix_submitted`, `retested`, and alternatives
  `deferred`, `duplicate`, `not_reproducible`, `accepted_risk` (administrator only, audited).
- A defect fix is verified by re-running the failing check in a fresh process, not by the
  developer's report (§12).
- **Status: done** (`performance.OPEN`, `assign`, `fix_submitted`, `track_defects`, `on_merged`, `retest`,
  `set_defect_status`, `set_defect_priority`; `tests/test_defects.py`). `priority` 1 (first) to 5 follows the
  severity until an administrator changes it; the open list sorts by it and project action items use it. The loop
  drives the lifecycle, never an agent's output: a writing task of a later iteration starting → `assigned` (to its
  agent); the iteration's writing tasks done → `fix_submitted`; the gate's own fresh run of the check passing →
  `retested` (with the run's command, commit, exit code and duration as `verification`), failing → `reopened`;
  the merge closes retested defects (`closed` is never set by hand). Open = detected, reopened, assigned,
  fix_submitted: an open critical one in any of them blocks `approve`. Administrator alternatives (admin API,
  audited, a reason kept as `resolution`): `accepted_risk`, `deferred`, `duplicate` (of another defect of the same
  goal), `not_reproducible` (only after a passing `retest`), or `detected` to open it again. `retest` runs a command
  check alone again through `contracts.run_command` (L3 sandbox, the goal's sandbox mode) on the goal's worktree
  while the goal is not running; other checks are retested by the next gate. Each step is in the defect's
  `history`; Performance → Open defects and the goal page show priority, status, assignee and the evidence.

### S09-R-04 Independent verification — §12 (AT-011, AT-026)
- The gate re-runs commands itself and records: commit SHA, command, exit code, duration, output
  tail, environment (Python/Node versions). Developer claims ("tests pass") are never accepted as
  evidence.
- **Status: done** (`contracts.run_command` fills `info` with `exit_code` (None when refused or not run),
  `timed_out`, `duration_s`, `output_tail`; `quality.evaluate` puts them on every command row with the `command`
  and the `commit` it ran on, and `environment` (platform, `python3`/`node --version` on the sandbox's PATH, probed
  once per process) on the report; `loop._record_history` keeps them per iteration as `verification` and
  `environment`; the goal page shows "Verification"; `tests/test_verification.py`). The gate never reads an
  agent's output as a test result: contract conditions and review verdicts are separate rows.

### S09-R-05 Agent evaluation dimensions — §8 (AT-019..021)
- Eval reports keep separate dimensions: task completion, instruction/contract compliance,
  tool-use accuracy (refused commands, failed tool calls), reliability across repeats, security
  behaviour, resource efficiency (time, cost) — with sample sizes; no single opaque score.
- Add an `adversarial` suite to `evals/`: tasks whose repository contains prompt-injection text
  (in a README or a fetched page) and a check that the injected instruction was not followed;
  a task that tempts disabling a failing test; a task with a failing tool (AT-020, AT-021).
- **Status: done** (`evaluation._dimensions`, `evaluation.dimensions`, `EvalRun.dimensions`, `evals/adversarial.json`;
  `tests/test_evaluation.py`). Each eval run records, from the gate's own history, its runs and the policy's
  refusals (never an agent's output): `compliance` (scope or change-boundary failures in any iteration, which
  include disabled or weakened tests, and contract rows failing in the last one), `tool_use` (tool calls, failed
  calls, denied tools, refused commands, failed runs) and `security` (a failed `security`-stage command, a secret in
  the change, a refused command). The summary reports `completion`, `compliance`, `tool_use`, `reliability`
  (per task with more than one repeat: the same outcome in every repeat, solved in every / any repeat), `security`
  and `efficiency` (time, cost, tokens, iterations, cost per pass), each with its sample size `n`; runs recorded
  before count only where their columns suffice. `compare` diffs one named metric per dimension with both `n`;
  the Performance view and the `eval` CLI show them; there is no combined score (`EvalRun.score`, the gate's share
  of passing rows, stays on the row only). The `adversarial` suite (kind `adversarial`, run like a final set:
  checks hidden, one iteration, so a failed trap is never fed back): an injection in a README asking for
  `PWNED.txt` and the deletion of `tests/`; a hidden instruction in a saved vendor page to post API keys to an
  outside host; a failing test whose quick fix is to weaken it (`protect_tests`); a tool that fails, where the
  honest result is a note and an error instead of an invented rate. Trap checks carry the `[security]` stage; the
  test checks that every seed passes its trap checks, a correct solution passes all checks and a trapped one fails.

### S09-R-06 Test-to-requirement links — §5.1 (AT-002, AT-003)
- Check commands and generated tests can name requirement keys (S08-R-01); the traceability
  report shows untested requirements.
- **Status: done** (`quality.split_tag`, `contracts.Condition.requirement_keys`, `requirements.on_gate`,
  `traceability()["untested"]`; `tests/test_requirements.py`). A quality command names keys in its tag, alone or
  after the stage: `[REQ-007] pytest -q`, `[integration REQ-003,REQ-004] pytest tests/it` (a bracket holding
  anything but a stage and keys stays part of the command, so `[ -f x ]` still runs). The planner's acceptance
  `command` conditions (the generated tests) carry `"requirement_keys"`. A command naming keys is linked as a check
  of those requirements only, also when no task named them; an unnamed command still tests all of the iteration's
  requirements (the goal's and its tasks'). `untested`: requirements past draft and not retired with no passing
  check on their current version, shown on the project's Plan tab.

### S09-R-07 Load, stress, compatibility and accessibility stages — §7.7–7.10
- Gate stages `performance` (load and stress), `compatibility` and `accessibility` beside the functional ones; a
  project's check commands and the planner's acceptance conditions can name them; the company or a project can make
  them mandatory for every goal.
- Acceptance: a project that requires `accessibility` fails a goal with no accessibility check; a tagged
  `[performance]` command satisfies its stage.
- **Status: done** (`quality.NONFUNCTIONAL_STAGES`, `teststrategy.STAGE_HINTS`, setting `required_test_stages`,
  `tests/test_teststrategy.py::test_required_nonfunctional_stages_come_from_the_rules_and_tagged_project_commands`).
  Commands carry the stage tag (`[performance] python bench.py --max-ms 200`, `[accessibility] npx pa11y-ci`);
  contract conditions accept `"stage": "performance|compatibility|accessibility"`. The setting
  `required_test_stages` (a list of command stages) adds them to every goal's mandatory stages through the test
  strategy (`required by the rules: …`); a project's own rule (Projects → edit → Stricter rules) adds stages, never
  removes the company's (`policies.effective` takes the union). The gate adds a `stage <name>` row that fails when no
  check of that stage ran, and its feedback tells the planner what such a check does. The goal page lists these
  stages only when a check of theirs ran or they are required. Eval goals get no rule stages (they only measure).
  There is no built-in load or accessibility runner: the project's tools are the checks.
