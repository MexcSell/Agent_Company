# SPEC-13 — Autonomous learning, training and capability evolution

Source: `/home/luca/update/SPEC13.docx`. Master criterion (`.rtf`): *the system can demonstrate
measurable improvement against a fixed evaluation baseline, and can roll back a change that
reduces quality.* The `.rtf` defines four learning levels: L1 retrieve experiences, L2 improve
skills/instructions, L3 optimise workflows/collaboration, L4 fine-tune or distil models — and the
rule *an agent must not be allowed to declare its own approach successful without independent
evaluation*.

## Already met

| Item | Where |
| --- | --- |
| L1 experience retrieval | `learning.experience_context` (improved by SPEC-11 S11-R-03) |
| L2 versioned playbooks, candidate trial, promote/rollback | `evolution.py` |
| Certification L0–L5 with expiry (§9) | `performance.certification` |
| Fixed benchmark suites and comparison (§10, §11) | `evaluation.py`, `evals/smoke.json`, `eval --compare` |

## Defects in the current implementation

- **S13-D-01** `evolution.after_task` promotes a candidate when `rate >= baseline` after
  `EVAL_WINDOW` (= 3) live tasks — a tie, or 2/3 against a 60 % baseline, promotes. §10.4: never deploy because one result improved; consider sample size
  and uncertainty. Fix: promote only if the candidate is strictly better by at least
  `min_improvement` (setting, default 10 points) **and** the benchmark suite (`eval`) does not
  regress; ties keep the current version.
  **Fixed:** the `min_improvement` rule and "ties keep" are in (`evolution.after_task`); the
  benchmark-suite check is S13-D-02.
- **S13-D-02** Candidate playbooks are tried directly on users' goals. §10.2 wants a sandbox/eval
  run first. Fix: run the candidate on the smoke suite before the live trial (S13-R-05).
  **Fixed** (`candidate_smoke`, on by default): a new candidate starts in a `smoke` stage, in which
  users' goals keep the current playbook and their outcomes do not count. A background run
  (`evolution.smoke_trial`, one at a time, restarted by `cycle()` after a restart) runs the smoke suite
  with the current playbook, then with the candidate; only eval goals see the candidate, and only on
  its side. Passing at least as many runs moves it to the live trial (where `min_improvement` decides);
  fewer rolls it back. Eval goals no longer steer evolution: their lessons start no trials and do not
  count in `success_rate`. The full S13-R-05 (experiments table, canary) followed, see below.

## Specific requirements

### S13-R-01 Capability gap detection — §5 — **done** (`training.gaps`, `raise_gaps` from `evolution.cycle`)
- Per agent and skill, readiness = verified knowledge + practical performance (gate pass rate on
  tasks tagged with that skill) + independent evaluation (eval results) + recency.
- Gap categories (§5.2): missing capability, insufficient proficiency, outdated (no success in
  90 days), poor practical performance, low reliability.
- `GET /api/learning/gaps` lists gaps; the scheduler or `evolution.cycle` raises them.
- Acceptance: LE-001; an agent failing 3 of its last 4 `sql` tasks has a `poor_performance` gap.
- Done: gaps are recorded once a week as `gap` evolution events; a gap whose skill a `final` suite covers gets a
  proposed (not started) plan. Missing capability = at least 2 of the role's tasks asked for a skill the agent
  does not list; low reliability = under 80 % of its last 20 model runs succeeded.

### S13-R-02 Learning plans and exercises — §6, §8 — **done** (`training.create_plan`, `run_plan`)
- Table `learning_plans`: agent, target capability, current/target level, objectives, resources
  (knowledge IDs), exercises, evaluation suite, status (`planned|running|passed|failed`).
- Exercises are eval-suite goals (`evals/*.json`) run in worktrees of a scratch repository —
  never against a user project (LE-005, LE-006, LE-025).
- Practice exercises and final evaluation sets are separate files; agents never see the final
  set's expected checks in their prompts (§8.3, LE-021).
- Acceptance: LE-002, LE-004, LE-007.
- Done: suites carry `kind` (`practice`/`final`) and `skills`; a plan refuses a practice suite as its final set
  and vice versa. A final suite's goals have `trial.hide_checks`: one iteration, no quality commands in
  `.claude/specs/*/requirements.md`, no findings in lessons. `Goal.trial.trainee_id` makes the trainee do its
  role's work (a clone when it is busy; the clone's eval evidence shows in `gaps` only after it merges back).
  Exercises are hand-written (`evals/python-practice.json`,
  `python-final.json`); generating them is open.

### S13-R-03 Independent evaluation and certification — §9 — **done** (`training.certified`, in `performance.certification`)
- Certification changes only from eval runs scored by the harness (commands), never from the
  agent's or its mentor's own report (LE-022).
- Failing an evaluation recommends retraining (LE-009); passing updates `skills` evidence (LE-011).

### S13-R-04 Self-improvement levels — §12 — **done** (`experiments.AUTONOMY`, `check_target`, `may_deploy`)
Enforced as policy:

| Level | What | Control |
| --- | --- | --- |
| E0–E2 | observe, identify gaps, propose | automatic |
| E3 | test in sandbox (eval suite) | automatic within budget |
| E4 | recommend deployment | needs review (administrator or automatic rule S13-D-01) |
| E5 | deploy playbook version | automatic only for playbooks; routes/models need an administrator |
| E6 | change governance, security, permissions, budgets, approvals | never autonomous |

Restricted targets (§12.2) are rejected by the proposal path (SPEC-14 S14-R-02).

### S13-R-05 Experiments with baseline and A/B comparison — §10 — **done** (`experiments.py`: `playbook`, `route`, `strategy`, `retrieval`)
- Table `experiments`: target (`playbook|route|workflow_template|retrieval_weights`), baseline
  version, candidate version, hypothesis, metrics (success rate, rework rate, time, cost per
  verified task), dataset (eval suite + repeats), results, status.
- Lifecycle: propose → baseline run → candidate run on the same suite and repeats → compare with
  sample sizes shown → review (S13-R-04) → canary (live trial window) → full rollout or rollback.
- Rollback restores the previous version automatically when live success drops below baseline
  during the canary (LE-018).
- Acceptance: LE-013..LE-018, LE-023.
- Done: evolution candidates are experiment records (smoke trial = baseline and candidate runs, live trial = canary,
  ended early once it cannot reach `min_improvement`); users propose playbook or route experiments. A route
  candidate is only recommended; an administrator applies it, then a canary of 3 live outcomes of the role
  restores the previous route when it cannot reach the live baseline (the suite's baseline pass rate when the
  role has no live history). A playbook experiment is refused while the agent has no live outcomes or too high a
  success rate to beat by `min_improvement`. The workflow-template target is `strategy` (SPEC-04 S04-R-13: template
  versions, a canary of live goals). The ranking-weight target is `retrieval` (administrators only): the candidate
  (`name=value` pairs or JSON over `learning.WEIGHTS`) and the current weights each run `evals/retrieval.json`,
  `evals/retrieval-classes.json` and the fixed `benchmarks/retrieval.py` (weights passed as `RETRIEVAL_W_*`, the
  procedure per query class in force on both sides, its own DATA_DIR in the L3 sandbox). Better = MRR over all eval
  cases up by `min_improvement` points, recall@5 not lower on either eval and the benchmark's guard metrics
  (`hit_at_3`, `mrr`) not lower; a benchmark that did not run holds it back. It has no canary (live work carries
  no relevance labels): an administrator applies it (`prefs.set_weights`, recall@5 guarded for the change as a
  whole), it is `rolled_out` with the previous values, and `rollback` restores them (an override that was not
  there is cleared) unless the weights were changed again since. Weights changed after the proposal stop the run
  and block the apply. `tests/test_retrieval_experiments.py`.

### S13-R-06 Model evaluation and routing — §11 — **done** (`eval --model <name> --role <role>`, route experiments)
- New catalog models can be benchmarked (`eval --label model:<name>`); a route change proposed
  from results is applied only by an administrator (LE-019, LE-020).

### S13-R-07 Learning metrics — `.rtf` SPEC-13 — **done** (`training.metrics`, `/api/learning/metrics`)
- Performance view adds: first-pass success rate, rework frequency (iterations per goal),
  knowledge reuse rate (retrieved items later cited in passing tasks), improvement over the
  fixed benchmark per playbook version.

### Not taken
- L4 fine-tuning/distillation: `skip` — no training infrastructure; models come from providers
  and local servers.
