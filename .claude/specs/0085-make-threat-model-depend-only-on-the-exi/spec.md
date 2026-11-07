I have what I need; writing the spec now.

# Spec 0085: order-only dependencies (`after`), worktree preconditions (`requires`), partial reviews

Goal: K237 (goal #25). Requirements: S07-R-02, G-R-01, S05-R-08. File: `.claude/specs/0085-make-threat-model-depend-only-on-the-exi/spec.md`.

## 0. Current behaviour

- **FACT** `scheduler.run_dag` (scheduler.py:104-110): a queued task with any dependency in `{failed, skipped}` is set to `skipped` with `"skipped: a dependency failed"`. A task is ready only when every dependency is `done` (scheduler.py:116-118).
- **FACT** `run_dag` takes dependencies only from the `Task.depends_on` column (scheduler.py:82). `deps_of` drops keys that are not in this call's `task_ids` (scheduler.py:91).
- **FACT** `loop._persist` (loop.py:1984-1986) stores `contract=c.model_dump(mode="json")`, `depends_on`, `role=c.role`, `priority` and `writes`.
- **FACT** `quality.evaluate` gives every task row the result `ok = status == "done" and not failed` (quality.py:236). `passed = all(...)` (quality.py:325). So any failed or skipped task already fails the iteration.
- **INFERENCE** In K237, `threat_model` had `depends_on: [dns_fix]` and `qa_review` depended on both. When `dns_fix` failed, both were skipped by the rule above in every iteration.

## 1. Contract fields (`src/agent_company/contracts.py`)

### 1.1 `TaskContract`
Add these two fields after `depends_on` (contracts.py:68). Both are defaulted, so older plans stay valid (G-R-01):
```
after: list[str] = Field(default_factory=list)       # order only: wait until these finish, any outcome
requires: list[Condition] = Field(default_factory=list)  # worktree preconditions checked before the run
```

### 1.2 `validate()` (contracts.py:100)
After `model_validate`, check each `cond` in `c.requires`. Each failure raises `ContractError` with the task key in the message.
- `cond.type` must be in `{"file_exists", "contains"}`. Any other type gives `"{key}: requires supports only file_exists and contains, not {type}"`.
  - **FACT** `Condition.type` already rejects unknown literals (contracts.py:28,32). So tests must use valid but unsupported types: `command` and `regex`.
- `cond.path` must be a non-empty string.
- The path is rejected if it is absolute: it starts with `/` or `\`, or `PurePosixPath(p).is_absolute()`, or it has a drive such as `C:`.
- The path is rejected if any `PurePosixPath(p.replace("\\", "/")).parts` element equals `".."`.
- `contains` needs `value` to be a non-empty `str`.

### 1.3 `validate_plan()` (contracts.py:115)
- Unknown `after` keys give `"{key} runs after unknown task(s) [...]"`. This sits beside the `depends_on` check.
- A key listed in both `depends_on` and `after` gives `"{key}: {k} is in both depends_on and after"`. **Decision:** reject it rather than guess which rule wins.
- The cycle check becomes `topo_order({c.key: [*c.depends_on, *c.after] for c in contracts})`. A cycle that mixes the two lists gives the existing `"task dependencies contain a cycle"`.

### 1.4 New pure function `unmet_requires(contract, workdir: Path) -> list[str]`
It returns one human-readable line per unmet condition. An empty list means everything is met. It never raises.
- **Containment:** `root = workdir.resolve()`, `target = (root / cond.path).resolve()`. If `root not in target.parents`, the condition is unmet (`"{path} is outside the worktree"`). This catches symlinks that escape the worktree.
  - **FACT** This is the same test `_check_one` uses for `file_exists` (contracts.py:206-207).
- `file_exists`: met when `target.exists()`. Otherwise `"file_exists {path}: missing"`.
- `contains`: met when `target.is_file()` and `str(cond.value) in target.read_text(errors="replace")`.
  - The match is case-sensitive and checks the file, not the task output. **Decision:** test names are identifiers. Acceptance `contains` stays case-insensitive and keeps checking output (contracts.py:188-190).
  - An `OSError` counts as unmet.
  - Unmet lines: `"contains {value!r} in {path}: not found"`, or `"... : file missing"`.

Update the module docstring (contracts.py:1-15) with one line each for `after` and `requires`.

## 2. Scheduler (`src/agent_company/scheduler.py`, `run_dag`)

### 2.1 Inputs
- The tuple becomes `(key, depends_on, priority, writes, after, is_review)`.
- `after = list((t.contract or {}).get("after") or [])`.
  - **Decision:** read it from the stored contract. There is no new `Task.after` column, so there is no db.py migration.
- `is_review = t.role in REVIEW_ROLES`. This uses the plan role written by `_persist`, not `resolve_role`'s output.
- Import `REVIEW_ROLES` lazily inside `run_dag` (`from agent_company.quality import REVIEW_ROLES`).
  - **FACT** scheduler.py already imports `workflow` and `hooks` lazily (lines 41, 59). quality.py imports `contracts`, `sandbox` and `strategies` at module level (quality.py:25-28).
  - **INFERENCE** A lazy import avoids any new import cycle.
- Add `afters_of(tid)`, which filters to `by_key` like `deps_of`.

### 2.2 Rules per queued task
`FIN = {"done", "failed", "skipped"}`, `D = deps_of(tid)`, `A = afters_of(tid)`.

| Task | Skipped when | Ready when |
|---|---|---|
| Not a review, or a review with an empty `D` | any `d` in D has state `failed` or `skipped`. Output stays `"skipped: a dependency failed"`, the exact string, so `tests/test_selfevolve.py:183` and `tests/test_task_stages.py:59` keep their meaning | every `d` in D is `done` and every `a` in A is in FIN |
| Review (`is_review`) with a non-empty `D` | every `d` in D is in FIN and none is `done`. Output: `"skipped: every dependency failed or was skipped"` | every `d` in D is in FIN, at least one is `done`, and every `a` in A is in FIN |

- `after` never causes a skip.
- A review whose dependencies have not all finished waits, even if one has already failed.
- The cancel branch (scheduler.py:111-115) is unchanged.
- **INFERENCE** No deadlock is possible. `validate_plan` makes depends_on ∪ after acyclic, and every running task ends as done or failed, so each queued task eventually becomes ready or skipped.

Update the module docstring (scheduler.py:3-8) to describe both rules: "after: order only; a review runs on partial work".

## 3. Worker (`src/agent_company/loop.py`, `_make_worker.work`, ~line 924)

### 3.1 `requires`
Insert directly after the `with session()` block (loop.py:925-931), before `registry.resolve_role` and `registry.staff`. That way no agent is staffed, no quota is used, and `teams.joined` is not called.
```
unmet = contracts.unmet_requires(contract, Path(goal.worktree)) if contract.requires else []
if unmet: set_status(task_id, "skipped", output=REQUIRES_SKIP + "; ".join(unmet)); return False
```
- Add `REQUIRES_SKIP = "skipped (precondition): "` beside the other prefixes (loop.py:1676-1682).
- It uses the default stage (`STATUS_STAGE["skipped"]`), not `stage="blocked"`.
  - **INFERENCE** The other `*_SKIP` prefixes use `blocked` because they pause the goal. This one does not.
- `REQUIRES_SKIP` is not added to the `_skipped_with` pause chain (loop.py:1456-1467). The gate judges the task like any skipped one: `quality.py:236` sets `ok=False`, so the iteration fails.
- `REQUIRES_SKIP` must start with `"skipped"`, so stage/workflow code that keys on status is unaffected.

### 3.2 Package for a review of partial work
- The `deps` query (loop.py:930-931) also loads `d.status`: `{d.key: (d.status, d.output)}`.
- `dep_text` includes outputs only from `done` dependencies.
- When `role in REVIEW_ROLES` and some dependencies are not `done`, add one line before `dep_text`:
  `"Not delivered (failed or skipped): dns_fix (failed), x (skipped). Review only what was delivered; the gate already fails this iteration for them."`
- The outputs of `after` keys are not added to the prompt. **Decision:** `after` sets order only.

## 4. The gate (no change)

- **FACT** `quality.evaluate` already fails the iteration for any task that is not `done` (quality.py:236-240, 325). A passing review only adds a `review <key>` row that passes. It removes no failed row.
- So a review of partial work never turns a failed iteration into a pass. No change is made to quality.py. The test in §7 (T2) pins this down.

## 5. `PLAN_INSTRUCTIONS` (loop.py:524, Optional task fields)

Add:
```
"after": ["key"] (order only: start once those tasks finished, whatever their outcome), "requires": [{"type": "file_exists", "path": "..."} |
{"type": "contains", "value": "...", "path": "..."}] (checked in the worktree before the run; unmet → the task is skipped).
```

## 6. Other files

- `src/agent_company/specs.py`, tasks.md rendering (`write_plan`, ~line 161):
  - **FACT** It currently prints `depends_on` with the label `after:`.
  - Recommended but optional: change that label to `depends on:` and add ` · after (any outcome): …` and ` · requires: …`. Before changing the label, grep tests for `" · after: "`.
- `src/agent_company/workflow.py:24`: change the docstring to "skipped (a dependency failed, or a precondition was not met)".
- `specs.check_plan`: no change. It scans only `acceptance` (specs.py:92-101). `requires` paths are validated in `contracts.validate`.
- `mission.py`: no change. Its REVIEW_ROLES copy is unaffected.
- `docs/threat-model.md`: no change. **INFERENCE** There is no new executor or network path. `unmet_requires` reads only inside the worktree, with a containment check.

## 7. Test plan (`tests/test_task_dependencies.py`, new, offline)

**Test setup:**
- Create the goal with `gid = loop.create_goal("K237")`.
- Create the tasks with `ids = loop._persist(gid, 1, [contracts.validate({...}), ...])`, as in `tests/test_task_stages.py:35`.
- Use a stub worker that records the call order. It calls `scheduler.set_status(tid, "done"|"failed")` and returns True or False from a `{key: outcome}` table.
- Call `scheduler.run_dag(ids, worker, "wt-test")`.
- Do not drive `_make_worker` (it needs an agent). Test `requires` through `contracts.unmet_requires` on a `tmp_path` worktree.

| # | Test | Asserts |
|---|---|---|
| T1 | `test_k237_threat_model_and_review_run_after_dns_fix_fails`: `build` done; `dns_fix` (depends_on build) fails; `threat_model` (role architect, `after=[dns_fix]`, `requires=[file_exists src/agent_company/mcp_servers/browser.py, contains "test_blocks_rebinding" in tests/test_browser.py]`); `qa_review` (role qa, depends_on `[build, dns_fix, threat_model]`) | `unmet_requires(threat_model, tmp)` is `[]` once those two files are written in tmp. The worker called `threat_model` after `dns_fix` finished and `qa_review` last. Final states: build done, dns_fix failed, threat_model done, qa_review done |
| T2 | same scenario, then `quality.evaluate(gid, 1)` (no commands: set `goal.quality_commands=["true"]` or patch settings) | `report["passed"] is False`, and the `task dns_fix` row fails, while the review row passes |
| T3 | `test_unmet_precondition_skips_with_requires_skip` | `unmet_requires` returns one line naming `browser.py` when the file is missing, and one naming the value when the test name is absent. `loop.REQUIRES_SKIP == "skipped (precondition): "` |
| T4 | `test_requires_path_stays_inside_worktree` | A symlink in tmp pointing outside the worktree → unmet. `validate` rejects `/etc/passwd`, `../x` and `a/../../b` with ContractError |
| T5 | `test_review_whose_dependencies_all_failed_is_skipped` | qa depends on `[a, b]`, both fail → qa skipped with output `"skipped: every dependency failed or was skipped"`, and the worker is never called for qa |
| T6 | `test_review_waits_for_every_dependency` | a fails fast and b succeeds later (worker uses a `threading.Event`) → qa starts only after b finished |
| T7 | `test_non_review_task_keeps_old_skip` | developer task depends on a failed task → skipped with `"skipped: a dependency failed"`, never called |
| T8 | `test_after_is_order_only` | `x` with `after=[a]`, where `a` fails → x runs, and runs after a |
| T9 | `test_validate_plan_rejects_bad_after_and_requires` | ContractError for each of: unknown `after` key; cycle `a.depends_on=[b]` + `b.after=[a]`; `requires` type `command`; `requires` type `regex`; a key in both lists; `contains` without a path |
| T10 | `test_old_plans_stay_valid` | a contract without the new fields validates, with `after == []` and `requires == []` |

**Commands for the developer and QA (both must run them and paste the summary lines):**
```
uv run pytest -q tests/test_task_dependencies.py tests/test_contracts.py tests/test_task_stages.py tests/test_selfevolve.py
uv run pytest -q
```
Run them serially and read the summary line. There is no xdist.

## 8. Files and functions to change

| File | Function / place |
|---|---|
| `src/agent_company/contracts.py` | `TaskContract` (+`after`, `requires`), `validate`, `validate_plan`, new `unmet_requires`, module docstring |
| `src/agent_company/scheduler.py` | `run_dag` (tuple, `afters_of`, skip/ready rules, lazy `REVIEW_ROLES` import), module docstring |
| `src/agent_company/loop.py` | `REQUIRES_SKIP` (~1682), `_make_worker.work` (precondition check, dep status, reviewer line), `PLAN_INSTRUCTIONS` |
| `src/agent_company/workflow.py` | docstring line 24 |
| `src/agent_company/specs.py` | optional: tasks.md label (~161) |
| `tests/test_task_dependencies.py` | new, T1-T10 |

No changes to db.py, quality.py, specs.check_plan or mission.py.

FINDING: specs.py:161 prints `depends_on` under the label "after:". Once `after` exists, tasks.md would misdescribe hard dependencies as order-only.
FINDING: `selfevolve.diagnose_layer` (selfevolve.py:108) matches "skipped: a dependency" as orchestration. The new texts "every dependency failed…" and "precondition" fall through to another layer, so RCA will place them differently. This is acceptable, but QA should know.
NEXT: The developer implements §1-§5 and T1-T10, then runs both §7 commands and pastes the summary lines.
NEXT: QA checks §2.2 (the two predicates) and §4 (T2) against the code. To stay within budget, it runs only the §7 commands.
MESSAGE handoff to developer: The spec is in `.claude/specs/0085-make-threat-model-depend-only-on-the-exi/spec.md`. Test `requires` through `contracts.unmet_requires` and the ordering through `scheduler.run_dag` with a stub worker; do not drive `_make_worker`.
MESSAGE recommendation to qa: Verify §2.2 and §4 by reading scheduler.py and quality.py:236 and running the two §7 commands only; earlier QA tasks on these specs went over budget.
