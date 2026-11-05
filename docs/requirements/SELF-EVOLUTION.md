# SELF-EVOLUTION — Verified self-evolution of the platform's own code

Source: `docs/advanced_self_improving_agent_guide.pdf` ("What Constitutes the Most Advanced AI Agent?",
7 pages, 19 sections). Its closing criterion: *the most advanced practical agent is not the one that can
rewrite itself freely; it is the one that can repeatedly engineer, verify and preserve better versions of
its own software.* IDs `SE-R-nn` (requirement written here, citing the guide's section).

## Analysis

The guide defines five conceptual levels (§1): basic, tool-enhanced, autonomous, self-improving and
continuously evolving. Before this work, Agent Company reached level 3 and the behavioural half of level 4:

| Already present (2026-11-05) | Guide | Where |
| --- | --- | --- |
| Plans, executes, observes, recovers (retries, re-plan, best-iteration rollback) | §1 level 3 | `loop.py` |
| Behavioural improvement: playbook candidates, smoke suite, live trial, promote or roll back | §8 behavioural | `evolution.py` |
| Agent definitions rewritten by the study rotation, each change a commit | §8 behavioural | `agentdefs.py` |
| Fixed benchmark suites run through the whole pipeline; label comparison | §11 (agents, not code) | `evaluation.py` |
| Worktree sandbox, scope rules, protected tests, change boundaries, approval before merge | §12 | `worktrees.py`, `checks.py` |
| Safe restart: syntax check, candidate boot on a side port, health check, swap | §12, §15 | `supervisor.py` |
| Claude Code as the coding environment, the platform controls the workflow | §16 | `executors.py`, `gm.py` |

What was missing is the guide's core, code-level self-improvement (§1 levels 4-5, §2, §3, §6, §19): a
contract that names one capability and the files that may change, a fixed benchmark run by the platform
(not by the coding agent) on the old and the new implementation, acceptance criteria fixed before the
result is known, deployment through the safe restart, monitoring of the deployed version and recovery.
`selfevolve.py` adds it on top of the existing pipeline rather than beside it: the candidate is an ordinary
goal on the protected platform project, so every existing gate (scope, boundaries, secrets, syntax, tests,
security review, emergency stops, quotas) still applies.

Agent Company's own repository already has the layout §6 asks for: separate modules per component, tests,
benchmarks (`evals/`, now `benchmarks/`) and git history. The §6 split "self_improvement/ apart from
protected infrastructure" is `selfevolve.py` itself, which is part of the protected control plane.

## Requirements

| ID | Guide | Requirement | Status |
| --- | --- | --- | --- |
| SE-R-01 | §2, §12 | Component map of the platform with who may change it: evolvable, high risk (orchestration, execution, routing: critical decision), protected control plane (security/permissions, evaluation and gate, deployment/versioning/recovery, database and migrations, host tools, `benchmarks/`, `pyproject.toml`, `.env`, `CLAUDE.md`) | done (`selfevolve.COMPONENTS`, `protected_paths`) |
| SE-R-02 | §4 | Diagnose the responsible layer of a failure (model, prompt, code, orchestration, tools, data, environment) with the action that fits it; a model or environment weakness cannot become a code contract | done (`diagnose_layer`; `create` refuses those layers) |
| SE-R-03 | §3 "analyse performance, identify weakness" | Recent failed goals, open defects, failed eval runs and error events, grouped by layer and component, most frequent first, with "write contract" where a code change can help | done (`weaknesses`, Evolution → Code evolution) |
| SE-R-04 | §5, §19 | Improvement contract: objective (one capability), component, layer, kind (§8 behavioural/algorithmic/architectural), research proposal (limitation, solution, affected files, benefits, risks, tests, targets), allowed paths, benchmark, acceptance | done (`improvement_contracts` table, `create`) |
| SE-R-05 | §11 "do not let the candidate redefine success" | The benchmark manifest and script are copied into the contract (sha256) when it is written; acceptance criteria (`improvement_min_score`, `improvement_max_regression`) are fixed then too; `benchmarks/` is protected, and a candidate touching it is rejected | done |
| SE-R-06 | §19 baseline | The benchmark runs on the base commit in a detached worktree before any change; the result is stored with the commit | done (`_baseline_and_build`) |
| SE-R-07 | §6, §16 | The candidate is a goal on the platform project: Claude Code (or the routed model) changes only the allowed paths in a worktree, existing tests are protected, the project's quality commands (`uv run pytest -q`) must pass | done (`_build`, origin `improvement`) |
| SE-R-08 | §10 "the coding agent should not be the sole judge" | A human decision record is created with the goal, and the loop never auto-merges an `improvement` goal, so it waits for approval whatever `require_approval` says (even if the decision was approved early); `loop.approve` refuses a candidate that the benchmark has not accepted | done (`merge_block`) |
| SE-R-09 | §11 | Independent evaluation: the platform runs the same benchmark on the base commit and on the candidate branch, alternating sides, `repeats` times each, median per metric; it checks that the measured package came from the tree under test | done (`measure`, `run_benchmark`) |
| SE-R-10 | §7 | Weighted score: each metric's relative change (against a floor) × its weight, negative weights for costs such as latency; the score is the weighted mean in percent. Accepted only if the score gain ≥ `improvement_min_score`, no guarded metric is worse and no metric is worse by more than `improvement_max_regression` | done (`compare`) |
| SE-R-11 | §7 | Agent Score per agent: w1 completion + w2 quality + w3 reliability + w4 efficiency − w5 latency − w6 cost, weights by role (research favours quality, coding correctness and reliability) | done (`agent_score`, Performance → Agents → Score) |
| SE-R-12 | §14 | Several candidates per contract against the same fixed benchmark; only the best verified one can be deployed | done (`start` again, `_best`) |
| SE-R-13 | §12, §16 | Deployment: an administrator approves the best verified candidate (decision approved, goal merged); the supervisor's safe restart checks and swaps the code in | done (`deploy`, `on_merged`) |
| SE-R-14 | §12 "rollback automatically if deployment fails" | A deployment the safe restart refused, or that came up unhealthy (`last_error_at` in `data/supervisor.json`), is reverted with `git revert` (a new commit) when `improvement_auto_rollback` is on (default); if the revert itself fails (uncommitted changes, conflict) the contract stops retrying and waits for a person. A deployment that changes no Python file needs no restart | done (`_monitor`) |
| SE-R-15 | §3 "monitor production" | After the restart, the fixed benchmark is re-run on the deployed commit and compared with the verified candidate; a regression notifies the administrators, who roll back with one click; otherwise the contract becomes `stable` | done (`_monitor`, `rollback`) |
| SE-R-16 | §12 "version every change" | Every step is an evolution event (contract, baseline, build, verify, deploy, monitor, stable, rollback, cancel); every API action is audited (deploy and rollback with severity high); the merge and the revert are git commits | done |
| SE-R-17 | §9, §18 first implementation | Fixed research-retrieval benchmark: `learning.retrieve` on a fixed corpus, queries per §9 class (exact name, concept, recent, document, complex); hit@3, MRR, precision@5, duplicate rate, latency; per-class results go into the candidate's instructions | done (`benchmarks/retrieval.py`, `.json`) |
| SE-R-18 | §15 | Evolution control survives restarts: a monitor thread (every 30 s) resumes baselines and verifications a restart cut off, finishes candidates whose goal ended without the hook, and monitors deployments; the emergency stop halts it | done (`advance`, `Monitor`) |
| SE-R-19 | §16 | CLI: `python -m agent_company improve list|weaknesses|benchmark [name]|create|start <id>|deploy <id>|rollback <id>|cancel <id>|advance` | done |
| SE-R-20 | §10 | Specialised team. Mapped to existing pieces instead of new agents (CLAUDE.md: only the core pipeline is enabled): Evolution Manager = `selfevolve` + the administrator; Performance Analyst = `weaknesses()`; Research Agent = research rooms; Architect, Coding, Testing = the goal's pipeline (GM, Architect, Developer, QA); Benchmark Agent = the deterministic benchmark runner; Security Agent = `_with_security_reviews`, secret scan, protected paths; Release Agent = approval, safe restart, monitor, rollback | done (mapping) |
| SE-R-21 | §9 version 4 | Learn which retrieval strategy works best per query class | open: the per-class results exist; a contract on `memory` can implement it |
| SE-R-22 | §12 | Run candidates in containers with restricted network access and resource limits | done: `run_benchmark` runs in the L3 sandbox (`sandbox.py`, bubblewrap): no network, the candidate tree and the benchmark script read-only, only the fresh `DATA_DIR` writable, `data/` and `.env` of a bound checkout hidden, memory/task/CPU limits through a systemd scope; the setting `sandbox` (`auto` falls back to the host, `required` refuses) |
| SE-R-23 | §13 | Fine-tuning and training new models | skip: no training infrastructure; models come from providers and local servers (as SPEC-13) |

## Acceptance (tests)

`tests/test_selfevolve.py`: the control plane is never a target; an accepted candidate deploys, is
re-benchmarked after deployment and rolls back to the old file; a worse candidate and its goal are rejected; a
candidate that edits the benchmark is measured with the contract's copy and rejected; the best of two
candidates is the one that deploys; a refused restart is reverted automatically; a failed goal fails its
candidate (picked up by `advance`); score arithmetic; layer diagnosis and weaknesses; Agent Score; the real
retrieval benchmark runs on this checkout; the API is admin-only and audited.

## First use

As the guide recommends (§18): one agent, one capability. Write a contract on **Memory and retrieval** with
the `retrieval` benchmark ("Improve research retrieval accuracy and reduce redundant searches"); allowed paths
default to `src/agent_company/learning.py` and new tests. Then extend to planning, coding, testing, memory and
finally the orchestrator (high risk, critical decisions).
