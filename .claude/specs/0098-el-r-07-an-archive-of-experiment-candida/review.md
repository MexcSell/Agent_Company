# Review (iteration 1)

## Review the experiment archive against the spec, EL-R-07 and G-R-11 (qa, done)

## Logic verification

The tests pass. `uv run pytest -q tests/test_experiment_archive.py tests/test_regressions.py tests/test_evolution.py` gave **22 passed in 6.40s**.

I traced each changed function through the diff against `main`.

- **`record` and `check_parent`** (`experiments.py:113-157`)
  - **Normal:** `parent_id` is checked before the row is created. The row is saved with it, and `_log` writes `{"event": "proposed", "parent_id": …}`.
  - **Edge:** with `parent_id=None` the check returns at once, and the log entry has no `parent_id`. The test asserts this at `test_experiment_archive.py:51`.
  - **Error:** a missing parent raises "does not exist". A parent of another target, or the same target with another role or agent, raises before anything is written. The test checks that 4 rows exist after the refused attempts (`:79`).
  - **Cycles:** a parent always has a lower id than its child, so a lineage cannot loop. `lineage` also stops on a repeated id and after 100 steps.
- **`update` and `_log`**
  - **Normal:** the baseline, candidate, comparison and status updates each add one entry, in the order the spec gives. The test checks the sequence `[proposed, baseline, candidate, comparison, status]`.
  - **Edge:** an update with only canary data adds nothing (`:63`). The log keeps the first entry and the newest 199.
  - **Error:** an unknown id returns early, as before.
  - **Existing paths:** apply, canary and rollback are unchanged. They now also log a status entry, or a "deployed" entry when `results` carries `deployed`.
- **`lineage` and `get`**
  - **Normal:** it returns the chain from the root to the experiment.
  - **Error:** an unknown id raises `KeyError` (tested).
  - **Edge:** if a parent row is missing, the chain stops there instead of failing.
- **`propose` and `seed`**
  - **Normal:** the route, playbook and retrieval branches pass `parent_id` to `record`. The strategy branch calls `check_parent` first, then sets `parent_id` after `orchestration.record` and patches the first log entry (`:323`). `test_a_strategy_candidate_records_its_parent` covers this.
  - **Edge:** with an empty candidate, `seed` supplies the parent's own candidate (tested for a route).
  - **Error:** a missing parent becomes a `ValueError`.
- **`archive` and `pick_parent`**
  - **Normal:** the archive holds only finished, measured experiments of the same target and scope. "Best" is the highest pass rate without regressions, then the cheapest. In the test, e2 is excluded because of its regression, and e3 beats e1 because it is cheaper.
  - **Edge:** an empty archive gives `None` for both modes (tested). When every entry has a regression, "diverse" uses the top-ranked entry as its reference.
  - **Error:** any other mode raises `ValueError`.
  - **Legacy rows without per-task records:** `regressions()` returns None, and `compare` then stores `regressions: []`. So `best_of` and `select_best` treat such a row as having no regressions. `most_diverse` skips rows without `solved_tasks`. This is how `compare` already behaved before this change, not a new bug.
- **`select_best`**
  - **Normal:** only candidates that are better and have no regressions are eligible. The winner has the lowest `_rank_key`: highest pass rate, then lowest cost, then lowest id.
  - **Edge:** ties are broken by cost and then by id, and both cases are unit-tested (`:166-169`).
  - **Error:** with nothing eligible it returns `None` (`:172`). A candidate marked better but with regressions is refused (`:172`). This second guard covers `orchestration.compare`'s efficiency path.
- **`compete`**
  - **Normal:** all checks run in one session before anything is written. Then the competition id is the lowest member id, each member gets `competition` and `competitors`, and each moves to status `baseline`.
  - **Error:** 4 ids, 1 id, a duplicate, another role, other repeats, a member already finished, a playbook, or an unknown id all raise. The test then checks that no member was changed (`:138-140`).
  - **Concurrency:** `start()` also commits a non-proposed status before it adds the id to `_running`. So the early return on `_running` cannot leave members stuck.
- **`run_competition`**
  - **Normal:** the baseline runs once and its result is copied to every member. Each candidate then runs in id order, and `compare` is applied to each.
  - **Winner:** goes to `review` with the verdict plus "(best of n in competition #cid)".
  - **Losers:** end `rejected` with the reason, and `reviewed_by` is `system:competition`.
  - **Edge:** in the acceptance test, A (80%, the highest pass rate) breaks task b and is rejected with "breaks b (G-R-11)". C (40%) is "not better". B (60%) wins. The baseline ran exactly once, and the route still points at "Old", because only an administrator applies the winner.
  - **Budget:** the members' budgets are added together. Once the total is spent, the second candidate never starts and both members end `failed` (tested).
  - **Other errors:** any other exception marks every unfinished member `failed`, and `finally` clears `_running`.

The change addresses the requirement itself, not just the symptom: the parent, the evaluation log, the parent selection and the single proposal are all stored and enforced in `experiments.py`.

**DB migration** (checked by reading the code, not by running it; my ad-hoc script was refused permission):
- `_add_missing_columns` adds both columns (`db.py:1517-1526`) and rebuilds `ix_experiments_parent_id` through `table.indexes` (`db.py:1528-1530`).
- `eval_log` is not nullable and has a computed default, so it is added as NULL and then filled with `[]` (`db.py:1533-1543`). The same mechanism already fixed `projects.stack`.
- `parent_id` is nullable and stays NULL for old rows.
- The developer reports checking this by hand on a database with both columns removed.

## Checklist
- **Contract:** met. Lineage is stored and the best of three is the one proposed. One deviation from the spec is listed below.
- **Interfaces:** compatible. Only optional keywords were added, and `to_dict` and `get` gained keys.
- **Error handling:** good. Inputs are checked before any write, and budget and exception paths end in `failed`.
- **Edge cases:** covered, apart from the gaps below.
- **Tests:** they really test the claims. A regressing candidate with the highest pass rate exists in the competition test, the baseline count is asserted, and the refused-parent cases assert that no row was written.
- **Dependencies:** none added.
- **Security:** there is no new API, so no `_audit` call is needed (spec §4.6).
- **Performance:** fine. The log is capped at 200 entries and the lineage at 100 steps.
- **Docs:** `EVOLUTION-LOOPS.md` and `CLAUDE.md` are updated.

FINDING: tests/test_experiment_archive.py:92 | The end-to-end competition has only one eligible candidate (B), so choosing the best among several eligible ones is covered only by the pure `select_best` unit asserts (:166-169). A bug in `run_competition` that passes the wrong entries to `select_best` could go unnoticed. | Add a fourth model (e.g. D solving "abcd") to `SOLVED` and assert that the higher-ranked of two eligible candidates wins and the other is rejected with "ranked below".
FINDING: src/agent_company/experiments.py:550 | The strategy branch of `run_competition` runs in no test: the shared-baseline check, the `orchestration.side_trial` trials, the `orchestration.compare` dispatch and the strategy verdict text. A broken strategy competition would pass CI. | Add a strategy competition test with a fake `run_suite` keyed on the trial's strategy.
FINDING: src/agent_company/experiments.py:323 | This deviates from spec §2.7, which adds a `parent_id` keyword to `orchestration.record`; that file was outside the allowed paths. As a result, the strategy branch's "proposed" log entry is patched after creation, and no other caller of `orchestration.record` can set a parent. | In a later iteration, add the pass-through keyword to `orchestration.record` and remove the patch.
FINDING: src/agent_company/experiments.py:299 | `propose("strategy", "", parent_id=<route experiment>)` seeds the route's model name, and `parse_changes` then fails with a JSON error before `check_parent` can report the real problem. The user sees a misleading error. | Look up the parent's target (or call `check_parent`) before seeding.
FINDING: src/agent_company/experiments.py:590 | Each member's hypothesis is concluded with an empty decision, because the comparison is stored before the winner or loser decision. Hypothesis records lose the reason. This follows spec §4.4 as written. | Optionally store the comparison together with the final decision.
FINDING: src/agent_company/db.py:264 | No automated test covers the migration of an existing database; it was checked by reading the code and by the developer by hand. | Add a test that drops `parent_id` and `eval_log` from a temporary database, calls `db.engine()`, and checks the columns, the index and the `[]` fill.

NEXT: Add the four-candidate (two eligible) case and a strategy competition test to tests/test_experiment_archive.py.
NEXT: Pass `parent_id` through `orchestration.record` once that file is in scope.

MESSAGE recommendation to developer: the two test gaps (several eligible candidates end to end, and strategy competitions) are in tests/test_experiment_archive.py; the misleading seed error is at experiments.py:299.

OUTCOME: APPROVED_WITH_COMMENTS
VERDICT: PASS

PROPOSAL: Admin API and CLI for competitions :: `POST /api/experiments/compete` with `_audit` and `parent_id` in the propose body, so people can use EL-R-07 without Python.
