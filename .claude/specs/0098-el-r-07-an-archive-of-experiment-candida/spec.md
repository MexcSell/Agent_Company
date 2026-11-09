# Spec 0098: EL-R-07, an archive of experiment candidates with lineage

Implements **EL-R-07** (docs/requirements/EVOLUTION-LOOPS.md §EL-R-07 and the table rows "An archive of designs with their evaluation logs; the next candidate sampled from it" and "Several competing candidates per improvement"). It keeps **G-R-11**: a candidate with a regression is never proposed.

## 0. Scope

The new surface has five parts: `parent_id` on `record`/`propose`, `lineage()`, `pick_parent()`, `select_best()` and `compete()`, plus the data model and test file they need.

**Changes:**
- `src/agent_company/db.py`
- `src/agent_company/experiments.py`
- `src/agent_company/orchestration.py`, one pass-through keyword in `record()`
- `tests/test_experiment_archive.py`
- Docs: the EVOLUTION-LOOPS.md EL-R-07 status and table rows, the `experiments.py` line in CLAUDE.md, and the docstring of experiments.py.

**Unchanged:**
- `evolution.py` and how playbook candidates become experiments.
- `run_sides`, `run_route`, `run_retrieval` and `orchestration.run` for single experiments.
- `apply`, `reject`, `observe`, every canary and rollout path, and the API, CLI and UI.

## 1. Data model (`db.py`, class `Experiment`, about line 248)

Add two columns after `results`:

```
parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)  # EL-R-07: the experiment this candidate started from
eval_log: Mapped[list[Any]] = mapped_column(default=list)  # EL-R-07: append-only evaluation log, see experiments._log
```

- **`parent_id`** has no ForeignKey, the same as `agent_id`.
- **Migration of `parent_id`:** `_add_missing_columns` runs `ALTER TABLE "experiments" ADD COLUMN "parent_id" INTEGER`. Its `table.indexes` loop then creates `ix_experiments_parent_id`, because the column is in `added`. No code change is needed in `_add_missing_columns`.
- **Migration of `eval_log`:** the column is JSON through `type_annotation_map`. The first pass adds it without a DDL default, because the default is computed. The second pass (db.py:1531-1538) then fills existing rows with `[]`. That pass skips nullable columns, which is why `eval_log` is declared non-nullable (see Open question 1). Readers still use `e.eval_log or []`.
- **Competition grouping uses no new column.** `compete()` writes `dataset["competition"] = cid` and `dataset["competitors"] = [ids sorted]`. `cid` is the lowest id among the competitors. The dataset is already the record that makes an experiment reproducible.
- **Status comment:** extend the comment with `# a competition's losers end rejected (EL-R-07)`. No new status: every value still fits String(16).

## 2. Recording, the eval log and lineage (`experiments.py`)

### 2.1 Constants

```
MAX_COMPETITORS = 3
EVAL_LOG_MAX = 200          # entries kept per experiment: the first ("proposed") + the newest 199
ARCHIVED = {"review", "canary", "rolled_out", "rolled_back", "rejected"}
```

### 2.2 `_log(e: Experiment, event: str, **fields: Any) -> None`

Appends `{"at": now().isoformat(), "event": event, **fields_without_None_values}`.

- **Reassign the list, never append in place.** Write `e.eval_log = [*(e.eval_log or []), entry]`. JSON columns are not mutation-tracked, so an in-place append is lost.
- **Keep the list bounded.** When it holds more than `EVAL_LOG_MAX` entries, keep `log[:1] + log[-(EVAL_LOG_MAX-1):]`.
- **Allowed fields:** `side`, `label`, `runs`, `pass_rate`, `solved_tasks`, `cost_per_passed`, `better`, `regressions`, `needed`, `status`, `parent_id`, `competition`, `winner`, `note`. A `note` is cut to 300 characters.

### 2.3 `record(...)`

The new signature only adds `parent_id` at the end:

```
def record(s, target, *, agent_id=None, role="", baseline_version="", candidate=None, hypothesis="",
           suite="smoke", repeats=1, status="proposed", origin="evolution", created_by="system",
           budget_usd=0.0, parent_id: int | None = None) -> Experiment
```

**Parent check.** This runs before the row is added. `t = check_target(target)`.

| Condition | Result |
|---|---|
| `parent_id` given and `s.get(Experiment, parent_id)` is None | `ValueError(f"parent experiment #{parent_id} does not exist")` |
| `p.target != t` | `ValueError(f"parent experiment #{parent_id} is a {p.target} experiment, not {t}")` |
| Scopes differ | `ValueError(f"parent experiment #{parent_id} is for {p_scope}, not {scope}")` |

- **Scope** is `_scope(target, agent_id, role)`: `("playbook", agent_id)` for a playbook and `(target, role)` for every other target. A route uses its role, a strategy its goal kind, and retrieval is always `"knowledge"`.
- **Any status of the parent is accepted**, including rejected and failed. A parent always has a lower id than its child, so lineage cannot contain a cycle.
- **After `s.flush()`:** set `e.parent_id = parent_id` and call `_log(e, "proposed", status=e.status, parent_id=parent_id, note=hypothesis)`.

### 2.4 `update(s, exp_id, status=None, decision=None, **results)`

The signature stays the same. The new behaviour, in this order:

1. Remember `prev = e.status`.
2. Apply the results, status and decision as today.
3. Append to the eval log:
   - **A side result:** for each `side in ("baseline", "candidate")` where `isinstance(results.get(side), dict)`, log `_log(e, side, side=side, label=..., runs=..., pass_rate=..., solved_tasks=..., cost_per_passed=...)`. The values come from that dict.
   - **A comparison:** if `isinstance(results.get("comparison"), dict)`, log `_log(e, "comparison", better=cmp.get("better"), regressions=cmp.get("regressions"), needed=cmp.get("needed"), note=decision or "")`.
   - **A competition result:** if `isinstance(results.get("competition"), dict)`, log `_log(e, "competition", competition=..., winner=..., note=...)`.
   - **A deployment:** if `"deployed" in results`, log `_log(e, "deployed", note=decision or "")`.
   - **A status change:** if `status and status != prev`, log `_log(e, "status", status=status, note=decision or "")`. This entry covers review, canary, applied (canary or rolled_out), rejected, failed, rolled_back and rolled_out.
4. **An update that carries only `canary=` adds nothing** when the status is unchanged. `observe()` calls `update` on every live outcome, and the canary dict is already its own log.

### 2.5 `lineage`

```
def lineage(s: Session, exp_id: int) -> list[int]
```

- **Returns** the ancestor ids, root first, ending with `exp_id` itself. A root experiment returns `[exp_id]`.
- **Unknown id:** `KeyError(exp_id)`.
- **Walk:** it follows `parent_id` one `s.get` at a time. It stops when a parent is missing (a deleted row), when an id repeats, or after 100 steps.

### 2.6 `to_dict` and `get`

- `to_dict` adds `"parent_id": e.parent_id`, `"eval_log": e.eval_log or []` and `"competition": (e.dataset or {}).get("competition")`.
- `get()` adds `"lineage": lineage(s, exp_id)`.
- `list_experiments` does not compute lineage, to avoid one extra query per row.

### 2.7 `propose(...)`

`propose` gains a keyword `parent_id: int | None = None`.

- **With an empty `candidate` and a `parent_id`**, the candidate text starts from the parent's payload, `seed(parent_id)`, before the check that requires a non-empty candidate.
- **The parent check itself** happens in `record()`. That includes the strategy branch: `orchestration.record` gains `parent_id: int | None = None` and passes it to `experiments.record`.
- **After success**, log `log_event("evolution", f"experiment #{id} proposed: {target} {role or agent_id} → {candidate[:60]}" + (f" (from #{parent_id})" if parent_id else ""))`.

```
def seed(parent_id: int) -> str
```

Returns the parent's candidate as the text `propose` accepts. Unknown id: `KeyError`.

| Parent target | Text returned |
|---|---|
| playbook | `candidate["playbook"]` |
| route | `candidate["model"]` |
| strategy | `json.dumps(candidate["changes"])` |
| retrieval | `_weights_text(candidate["changes"])` |

A person or the loop edits this text to make the next candidate. Proposing it unchanged is allowed; the existing checks still apply. For example, a retrieval parent whose weights are already in force raises "changes no ranking weight".

## 3. Choosing a parent (archive sampling)

### 3.1 `archive`

```
def archive(s: Session, target: str, *, role: str = "", agent_id: int | None = None) -> list[dict[str, Any]]
```

**The archive** holds the experiments of that target and scope (`agent_id` for a playbook, `role` otherwise) that meet all three conditions:
- the status is in `ARCHIVED`
- `results["candidate"]` is a dict with a non-None `pass_rate`
- `results["comparison"]` is a dict

Each entry is `{"id", "candidate": results["candidate"], "comparison": results["comparison"]}`, ordered by id. Failed experiments and those still running are never in it.

### 3.2 Pure helpers

These run without a database.

```
def _cost(c: dict) -> float            # cost_per_passed, None -> float("inf")
def _rank_key(entry) -> tuple          # (-pass_rate, _cost(candidate), id)
def jaccard_distance(a: Iterable[str], b: Iterable[str]) -> float   # 1 - |A∩B|/|A∪B|; both empty -> 0.0
def best_of(entries) -> dict | None    # entries with not comparison.get("regressions"); min by _rank_key
def most_diverse(entries, best) -> dict | None
```

`most_diverse` considers the entries other than `best` that have `"solved_tasks"` in their candidate. It returns the one with the largest `jaccard_distance(e.solved_tasks, best.solved_tasks)`. Ties go to the higher pass_rate, then the lower id. A `best` without `solved_tasks` counts as the empty set.

### 3.3 `pick_parent`

```
def pick_parent(target: str, *, role: str = "", agent_id: int | None = None, mode: str = "best") -> int | None
```

- **Validation:** `check_target(target)`. A mode other than `"best"` or `"diverse"` raises `ValueError`.
- **`best`:** the id of `best_of(archive)`. This is the highest candidate pass rate whose comparison has no regressions (G-R-11). Ties go to the lower `cost_per_passed`, then to the lower id. The candidate does not have to have been `better` than its baseline.
- **`diverse`:** the id of `most_diverse(archive, best)`. With no best (every entry has regressions), the reference is the entry ranked first by `_rank_key` over all entries. When nothing other than the reference qualifies, it returns the reference's id (Open question 3).
- **Empty archive:** `None` for both modes. The caller then proposes without a parent.
- **No writes and no log.** It only reads.

## 4. Competition

### 4.1 Pure selection

```
def select_best(entries: list[dict[str, Any]]) -> int | None
```

- **Entries** are `{"id", "candidate": side metrics, "comparison": cmp}`.
- **Eligible:** `cmp.get("better") is True and not cmp.get("regressions")`. The second test is redundant with `compare()`, but it guards against the `orchestration.compare` efficiency path.
- **Winner:** the eligible entry with the minimum `_rank_key`: highest pass_rate, then lowest cost_per_passed (None last), then lowest id.
- **No eligible entry:** `None`.

### 4.2 `compete`

```
def compete(exp_ids: list[int], *, actor: str = "system") -> dict[str, Any]
```

**Checks.** These run synchronously in one session. Any failure raises before anything is written.

| Check | Error |
|---|---|
| An emergency stop is active | `PermissionError("an emergency stop is active (E5)")` |
| After deduplication, fewer than 2 or more than `MAX_COMPETITORS` ids | `ValueError(f"2 to {MAX_COMPETITORS} candidates compete on one suite")` |
| A duplicate id | `ValueError` |
| An unknown id | `KeyError` |
| A status other than `proposed` | `ValueError(f"experiment #{id} is {status}")` |
| A target other than `route` or `strategy` | `ValueError(f"{target} candidates cannot compete yet")`; playbook and retrieval are excluded (Open question 2) |
| Targets, scopes (`role`), `dataset["suite"]`, `dataset["repeats"]` or the baseline differ | `ValueError(...)` naming the field. The baseline is `baseline_version` for a route and `candidate["base_version"]` for a strategy |
| An id already has `dataset["competition"]` | `ValueError` |

**Then:**
- `cid = min(ids)`.
- For each member:
  - set `e.dataset = {**e.dataset, "competition": cid, "competitors": sorted(ids)}`
  - call `update(s, id, status="baseline")`
  - call `_log(e, "competition", competition=cid, note=f"competes with {others} on '{suite}', {repeats} repeat(s)")`
- Log `log_event("evolution", f"competition #{cid}: {target} {role}, candidates {', '.join('#'+str(i) for i in ids)} on '{suite}'", s=s)`.
- Under `_running_lock`: if any id is already in `_running`, return `get_competition(cid)` without starting anything. Otherwise add all ids.
- Call `_spawn(lambda: run_competition(sorted(ids)))` and return `get_competition(cid)`.

### 4.3 `get_competition`

```
def get_competition(cid: int) -> dict[str, Any]
```

Returns `{"id": cid, "members": [get(i) for i in competitors], "winner": the member in review whose dataset competition == cid, or None}`.

### 4.4 `run_competition`

```
def run_competition(ids: list[int]) -> dict[str, Any]
```

The whole body is in try/except/finally, the same pattern as `run_route`.

**Trials:**
- **Route:** the baseline trial is `{"experiment_id": cid, "side": "baseline"} | ({"models": {role: base}} if base else {})`. Each candidate's trial is `{"experiment_id": id, "side": "candidate", "models": {role: e.candidate["model"]}}`.
- **Strategy:** `{"experiment_id": ..., "side": ..., "strategy": orchestration.side_trial(e, side)}`. The baseline uses the `cid` row.

**Budget (S10-R-02):**
- The total is `sum(float(dataset["budget_usd"] or 0))` over the members. A total of 0 means no limit.
- `spent` is counted across every run. The baseline is charged once.
- Before each run: if `budget > 0 and budget - spent <= 0`, raise `OverBudget`.
- The run gets `budget_usd=budget - spent` when the budget is above 0.
- A summary with `stopped_on_budget` raises `OverBudget`, using the message shapes of `run_sides`.
- On `OverBudget`, every member not in `FINAL` gets `_stopped(id, f"competition #{cid} {why}")` and ends `failed`.

**Order:**
1. **The shared baseline.** Run `evaluation.run_suite(suite, f"comp{cid}-baseline-{ms}", repeats=repeats, trial=baseline_trial, ...)` once. `b = side_metrics(summary) | {"dimensions": summary.get("dimensions") or {}}`. For each member, `update(s, id, baseline=b)`; the log entry is written by `update`.
2. **Each candidate,** in id order: `update(s, id, status="candidate")`, then `run_suite(suite, f"exp{id}-candidate-{ms}", ...)`, then `update(s, id, candidate=c)`.
3. **Compare.** `cmp = compare(b, c)` for a route and `orchestration.compare(b, c)` for a strategy. Store each with `update(s, id, comparison=cmp)`. That concludes each member's hypothesis.
4. **Select.** `winner = select_best([...])`. All the writes below happen in one session.

**Winner:**
- `update(s, winner, status="review", decision=..., competition={"id": cid, "members": ids, "winner": winner, "rank": 1})`
- The decision is the existing verdict of `run_route` or `orchestration.run` for a better candidate, plus `f" (best of {n} in competition #{cid})"`.
- The winner then follows the unchanged `apply` and `reject` paths.

**Each loser:**
- Set `e.reviewed_by = "system:competition"`.
- Call `update(s, id, status="rejected", decision=f"competition #{cid}: #{winner} won ({wrate:.0%}); this candidate {reason}", competition={"id": cid, "members": ids, "winner": winner, "why": reason})`.
- **The reason** is one of:
  - `f"breaks {', '.join(regs)} (G-R-11)"` when it has regressions
  - `f"is not better than the baseline: {c:.0%} vs {b:.0%}, needs {needed:.0%}"` when `better` is False
  - `f"ranked below #{winner}: {c:.0%}, ${cost} per passed run"` when it was eligible but lost

**No eligible candidate:**
- Every member gets `status="rejected"` and `decision=f"competition #{cid}: no candidate beat the baseline without regressions; this candidate {reason}"`.
- `competition={"id": cid, "members": ids, "winner": None, "why": reason}`.

**Notification and log:**
- Call `notify(f"Competition #{cid}: {target} {role}", <winner verdict or 'no candidate proposed'>, level="info", source="evolution", admin_only=True, s=s)`.
- Then `log_event("evolution", f"competition #{cid}: #{winner} proposed, {', '.join('#'+str(i) for i in losers)} rejected", s=s)`, or `f"competition #{cid}: no eligible candidate, all rejected"`.
- Return `get_competition(cid)`.

**Errors:**
- Any other exception: every member not in `FINAL` gets `update(s, id, status="failed", decision=f"{type(e).__name__}: {e}")`.
- Then `log_event("error", f"competition #{cid} failed: ...")`, and the function returns `{}`.
- `finally`: discard every id from `_running`.

### 4.5 Status transitions per member

`proposed → baseline` (in `compete`) `→ candidate` (when its own run starts) `→ review` (winner only) `| rejected` (losers, or everyone when nothing is eligible) `| failed` (budget or exception).

- Losers are set to `rejected` by `run_competition` directly, not through `reject()`, which accepts only proposed or review.
- `rejected` is in `FINAL`.

### 4.6 Audit

- There is no new API endpoint in this iteration, so there is no new `_audit` call.
- Every state change goes through `log_event` and `eval_log`.
- An endpoint added later (see the proposals below) must call `_audit("experiments.compete", ...)`.

## 5. Tests: `tests/test_experiment_archive.py`

The tests run offline with a temp DATA_DIR, following tests/test_training.py:227-247.

**Setup:**
- `router.add_model` for Old, A, B and C, plus `router.set_route("developer", "Old")`.
- `monkeypatch.setattr(experiments, "_spawn", lambda fn: fn())`.

**A new fake suite.** Don't reuse `fake_suite` from test_training.py: it returns no `by_task`, so `regressions()` returns None and the regression case cannot be built. Instead, define:

```
SOLVED = {"Old": "ab", "A": "acde", "B": "abc", "C": "ab"}   # tasks a..e
def run_suite(suite, label, repeats=3, only=None, trial=None, budget_usd=0.0):
    model = (trial.get("models") or {}).get("developer", "Old"); calls.append((trial["side"], trial["experiment_id"], model, suite, repeats))
    solved = SOLVED[model]; return {"label": label, "runs": 5, "passed": len(solved), "pass_rate": len(solved)/5,
        "total_cost": 0.1, "cost_per_passed": 0.1/len(solved), "by_task": [{"task": t, "solved": t in solved} for t in "abcde"]}
```

With `min_improvement` at its default of 10 points (config.py:66), the baseline is 0.4 and a candidate needs 0.5:
- **A:** 0.8, breaks b.
- **B:** 0.6, no regressions.
- **C:** 0.4, not better.

**Tests:**

- **(a) `test_lineage_and_eval_log_are_stored`**
  - `root = propose("route", "A", role="developer")`, then `child = propose("route", "B", role="developer", parent_id=root["id"])`.
  - `get(child)` has `parent_id == root id` and `lineage == [root, child]`, and `experiments.lineage(s, child) == [root, child]` holds in a new session.
  - `eval_log[0]["event"] == "proposed"` with `parent_id`.
  - After `update(s, child, status="review", baseline=..., candidate=..., comparison=...)`, the events are `["proposed", "baseline", "candidate", "comparison", "status"]`.
  - A further `update(s, child, canary={...})` adds nothing.
  - `propose("route", "", role="developer", parent_id=root)` takes `"A"` from `seed`.
  - **Raises `ValueError`:**
    - parent_id 9999
    - a parent made with `experiments.record(s, "strategy", role="bugfix")`
    - a route parent for role `"qa"`

- **(b) `test_the_best_of_three_without_regressions_is_the_one_proposed`**
  - Propose A, B and C for developer with the same suite and repeats, then `compete([a, b, c])`.
  - The baseline ran exactly once: one call with side `"baseline"`. Each candidate ran once, with the same suite and repeats in every call.
  - B ends in `review`, and its decision contains `competition #`.
  - A and C end in `rejected`. Their decisions name `#B`. A's last `competition` log entry, or its decision, contains `"breaks b"`, and its comparison shows `regressions == ["b"]`. C's says "not better".
  - All three share an identical `results["baseline"]` and `dataset["competition"] == min id`.
  - `get_competition(cid)["winner"]["id"] == B`.

- **(c) `test_competition_size_and_shape_are_checked`**
  - **Raises `ValueError`:**
    - four ids
    - one id
    - mixed roles
    - different repeats
    - an experiment not in `proposed`
    - a playbook experiment
  - After each, every experiment is still `proposed` and has no `dataset["competition"]`.

- **(d) `test_pick_parent_best_and_diverse`**
  - An empty archive returns `None` for both modes.
  - Seed the archive with `record` plus `update` (no evals), all in status `review`:

    | Experiment | Pass rate | Cost | Solved | Regressions |
    |---|---|---|---|---|
    | e1 | .6 | 1.0 | ab | none |
    | e2 | .8 | – | – | x |
    | e3 | .6 | 0.5 | ac | none |
    | e4 | .4 | – | de | none |

    Also add e5, `proposed` and unmeasured.
  - `best` → e3 (same pass rate as e1, cheaper; e2 is excluded for its regression).
  - `diverse` → e4 (Jaccard distance 1.0 from ac).
  - A different role returns `None`.
  - `select_best` on plain dicts:
    - a tie on pass rate goes to the cheaper entry, then to the lower id
    - `better` False is never chosen
    - all ineligible gives `None`

- **(e) `test_a_competition_stops_all_candidates_on_its_shared_budget`**
  - Budgets 0.1 + 0.1 give a total of 0.2. Each run costs 0.15.
  - Both members end `failed` with "stopped on its budget".

Run: `uv run pytest -q tests/test_experiment_archive.py tests/test_regressions.py tests/test_training.py tests/test_evolution.py`, then `uv run pytest -q`. Run serially; there is no xdist.

## Open questions (with the defaults chosen)

1. **`eval_log` is "nullable" in the request but declared non-nullable.** Default: declared non-nullable with `default=list`, so that the backfill in `_add_missing_columns` gives old rows `[]`. The SQLite column itself is added as nullable.
2. **Which targets may compete.** Default: route and strategy only. Playbook trials go through the agent's single `a.experiment` slot and a live canary. Retrieval has no pass-rate or regression comparison, and its sides are deterministic.
3. **`diverse` with only one candidate in the archive.** The task says to exclude the best. Default: return the best, so a caller asking for a parent still gets one. `None` only for an empty archive.
4. **Should the parent have to be finished?** Default: no. Any existing experiment of the same target and scope can be a parent. `pick_parent` only samples finished ones.
5. **Is a competition's winner deployed automatically?** Default: no. It goes to `review`, and the existing per-target rules decide (`DEPLOY_BY`, `apply`).

PROPOSAL: API/CLI/UI for the experiment archive :: `POST /api/experiments/compete` (admin, `_audit`), a `parent_id` field in the propose body, and a lineage view on Evolution → Experiments, so people can use EL-R-07 without Python.
PROPOSAL: Playbook competitions :: let the evolution loop run up to three playbook candidates per agent on one smoke suite, which needs more than one `a.experiment` slot.

FINDING: The existing `fake_suite` in tests/test_training.py returns no `by_task`, so no regression (G-R-11) can be built with it; the test for (b) needs its own fake that returns `by_task`.
FINDING: JSON columns are not mutation-tracked. `eval_log` must be reassigned on every append, the way `update()` already reassigns `results`.
NEXT: The developer implements sections 1–5 in db.py, experiments.py and orchestration.py (the `parent_id` keyword only), adds tests/test_experiment_archive.py and updates the EL-R-07 status in docs.
NEXT: QA checks that `select_best` never returns a candidate with regressions and that the shared baseline runs exactly once.
MESSAGE handoff to developer: The spec is .claude/specs/0098-el-r-07-an-archive-of-experiment-candida/spec.md. Keep `run_sides`, `apply`, `reject` and the canary paths unchanged, and add no API routes in this iteration.
