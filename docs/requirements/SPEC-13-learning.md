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
  count in `success_rate`. The full S13-R-05 (experiments table, canary) is still open.

## Specific requirements

### S13-R-01 Capability gap detection — §5
- Per agent and skill, readiness = verified knowledge + practical performance (gate pass rate on
  tasks tagged with that skill) + independent evaluation (eval results) + recency.
- Gap categories (§5.2): missing capability, insufficient proficiency, outdated (no success in
  90 days), poor practical performance, low reliability.
- `GET /api/learning/gaps` lists gaps; the scheduler or `evolution.cycle` raises them.
- Acceptance: LE-001; an agent failing 3 of its last 4 `sql` tasks has a `poor_performance` gap.

### S13-R-02 Learning plans and exercises — §6, §8
- Table `learning_plans`: agent, target capability, current/target level, objectives, resources
  (knowledge IDs), exercises, evaluation suite, status (`planned|running|passed|failed`).
- Exercises are eval-suite goals (`evals/*.json`) run in worktrees of a scratch repository —
  never against a user project (LE-005, LE-006, LE-025).
- Practice exercises and final evaluation sets are separate files; agents never see the final
  set's expected checks in their prompts (§8.3, LE-021).
- Acceptance: LE-002, LE-004, LE-007.

### S13-R-03 Independent evaluation and certification — §9
- Certification changes only from eval runs scored by the harness (commands), never from the
  agent's or its mentor's own report (LE-022).
- Failing an evaluation recommends retraining (LE-009); passing updates `skills` evidence (LE-011).

### S13-R-04 Self-improvement levels — §12
Enforced as policy:

| Level | What | Control |
| --- | --- | --- |
| E0–E2 | observe, identify gaps, propose | automatic |
| E3 | test in sandbox (eval suite) | automatic within budget |
| E4 | recommend deployment | needs review (administrator or automatic rule S13-D-01) |
| E5 | deploy playbook version | automatic only for playbooks; routes/models need an administrator |
| E6 | change governance, security, permissions, budgets, approvals | never autonomous |

Restricted targets (§12.2) are rejected by the proposal path (SPEC-14 S14-R-02).

### S13-R-05 Experiments with baseline and A/B comparison — §10
- Table `experiments`: target (`playbook|route|workflow_template|retrieval_weights`), baseline
  version, candidate version, hypothesis, metrics (success rate, rework rate, time, cost per
  verified task), dataset (eval suite + repeats), results, status.
- Lifecycle: propose → baseline run → candidate run on the same suite and repeats → compare with
  sample sizes shown → review (S13-R-04) → canary (live trial window) → full rollout or rollback.
- Rollback restores the previous version automatically when live success drops below baseline
  during the canary (LE-018).
- Acceptance: LE-013..LE-018, LE-023.

### S13-R-06 Model evaluation and routing — §11
- New catalog models can be benchmarked (`eval --label model:<name>`); a route change proposed
  from results is applied only by an administrator (LE-019, LE-020).

### S13-R-07 Learning metrics — `.rtf` SPEC-13
- Performance view adds: first-pass success rate, rework frequency (iterations per goal),
  knowledge reuse rate (retrieved items later cited in passing tasks), improvement over the
  fixed benchmark per playbook version.

### Not taken
- L4 fine-tuning/distillation: `skip` — no training infrastructure; models come from providers
  and local servers.
