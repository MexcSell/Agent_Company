# Review (iteration 2)

## Review the dependency change against the spec and K237 (qa, done)

## Logic verification

I could not run `uv run pytest -q tests/test_task_dependencies.py`: this session needs approval for the command and doesn't have it. So the test results below come from reading the code. The developer reported 31 tests passing across the three test files; I could not confirm that number myself.

**`scheduler.run_dag`** (scheduler.py:84-157). Each task's tuple now carries the `after` keys from the stored contract and a flag for whether its plan role is a review role (`REVIEW_ROLES`). Two new helpers decide what happens to a waiting task. `skip_reason` (scheduler.py:108-115) decides when to skip it and `is_ready` (scheduler.py:117-121) decides when it can start. The rules match spec §2.2 exactly.

- **(a) Normal case, the K237 plan.**
  - `dns_fix` depends on `build`. Once `build` is done, `dns_fix` runs and fails.
  - `threat_model` has no `depends_on`, so nothing can skip it. It waits only for its `after` key (`dns_fix`) to finish with any outcome, then runs. The skip reason is empty and the task becomes ready.
  - `qa_review` (role qa) depends on `build`, `dns_fix` and `threat_model`. It counts as a partial review. Once all three have finished and at least one is done, it is ready.
  - In `_make_worker`, the review gets only the outputs of dependencies that are done. A "Not delivered: dns_fix (failed)" line is added in front (loop.py:977-982).
  - Test `test_k237_threat_model_and_review_run_after_dns_fix_fails` checks the order and the final states.
  - This fixes the root cause (an ordering need wrongly treated as a success requirement), not just the symptom.
- **(b) Edge cases.**
  - **`browser.py` missing:** `loop._skip_unmet` (loop.py:1699-1708) runs before `resolve_role` and before any agent is staffed. `contracts.unmet_requires` returns "file_exists …browser.py: missing". The task row is set to `skipped` with `REQUIRES_SKIP` and the worker returns False.
    - Inside `run_dag` that task then counts as `failed`, which still counts as finished. `qa_review` therefore still has `build` done and runs.
    - Test `test_k237_unmet_requires_skips_threat_model_review_still_runs` checks the stored states and that no agent started on `threat_model`.
  - **A dependency still running:** its state is `running`, which is not a finished state. The review is neither skipped nor ready, so it waits.
    - Test `test_review_waits_for_every_dependency` checks this: `a` fails fast, and `qa` starts only after `b` has ended.
- **(c) Error cases.**
  - **Every dependency of a review failed:** the review is skipped with "skipped: every dependency failed or was skipped" and the worker is never called (test T5).
  - **A non-review task:** its old rule and exact text are kept. That matters because `selfevolve.py:108` and the tests in `test_selfevolve`/`test_task_stages` depend on the text (test T7).
  - **A partial review cannot make the iteration pass:** `quality.evaluate` is unchanged. The failed `dns_fix` row still has `ok=False`, so the iteration fails. Test `test_k237_partial_review_never_passes_the_iteration` asserts that the report fails, the `dns_fix` row fails, and the review row passes.
- **No deadlock:** `validate_plan` checks the union of `depends_on` and `after` for cycles. Keys of tasks outside this iteration are filtered out of both lists. Every running task ends as done or failed.

**`contracts.validate`, `validate_plan` and `unmet_requires`.**
- `requires` accepts only `file_exists` and `contains`. Each condition needs a path; an empty path is rejected.
- A `requires` path cannot escape the worktree:
  - Paths that start with `/` or `\`, that are absolute, that have a drive, or that contain a `..` part (after turning `\` into `/`) are rejected.
  - When the check runs, `unmet_requires` resolves symlinks and refuses any target that is not inside the worktree root.
  - Test `test_requires_path_stays_inside_worktree` covers a symlink, `/etc/passwd`, `../x`, `a/../../b`, `C:/x` and `\x`.
- `unmet_requires` never raises: `OSError` and `RuntimeError` count as an unmet condition.
- `validate_plan` rejects an unknown `after` key and a key that appears in both `depends_on` and `after`. A cycle through `after` is rejected with "cycle" (T9).
- Old plans validate unchanged: both new fields default to empty lists (T10).
- `PLAN_INSTRUCTIONS` documents `after`, `requires` and the partial-review rule (loop.py:530-534). T10 asserts that both field names appear.

## Checklist
- **Contract satisfied:** yes. Spec §1-§5 are implemented as written.
- **Interfaces compatible:** yes. The old skip text and the default fields are kept, and no database migration is needed.
- **Error handling:** fine. Paths that cannot be resolved count as unmet, and the scheduler's crash guard is unchanged.
- **Edge cases:** one gap (finding 3).
- **Tests:** they cover spec T1-T10, plus the unmet-precondition test.
- **Dependencies:** none added.
- **Security:** reads are contained inside the worktree and no command ever runs.
- **Performance:** each check is O(dependencies) per scheduling pass, which is negligible.
- **Docs:** `threat-model.md` has a new row, which goes beyond spec §6 but is justified by CLAUDE.md. `workflow.py` is not updated (finding 1).

FINDING: src/agent_company/workflow.py:24 | Spec §6 requires this docstring to say "skipped (a dependency failed, or a precondition was not met)". It still lists only "a dependency failed", so anyone reading the stage documentation misses `REQUIRES_SKIP` | Change the docstring as spec §6 says.
FINDING: src/agent_company/specs.py:161 | tasks.md still prints `depends_on` under the label "after:". Now that a real `after` field exists, a plan reader sees hard dependencies described as order-only, and the actual `after`/`requires` fields are not shown at all | Rename the label to "depends on:" and add "after (any outcome): …" and "requires: …". Spec §6 marks this as optional, but the label is now actively misleading.
FINDING: src/agent_company/loop.py:980, src/agent_company/scheduler.py:89 | The scheduler decides whether a task is a review from its plan role (`t.role`). The worker decides whether to add the "Not delivered" line from the role returned by `registry.resolve_role`. If that function maps `qa` to a role outside `REVIEW_ROLES`, the task is scheduled as a partial review but the agent is not told that some work was not delivered | In the worker, test `contract.role in REVIEW_ROLES`, the same rule the scheduler uses.
FINDING: src/agent_company/scheduler.py:156 | When a worker returns False, `run_dag`'s returned map records the task as `failed`, even when its stored row says `skipped` (precondition, budget, quota or blocker skip). The developer reported this too. Today it does no harm because the skip rules treat failed and skipped alike, but a caller reading that map sees the wrong reason | Re-read the stored status after a False result, or document that the map is internal.

OUTCOME: APPROVED_WITH_COMMENTS. Cases (a), (b) and (c) all trace correctly, and so do the checks for old plans, `after` cycles and `PLAN_INSTRUCTIONS`. The findings are documentation and consistency issues only. The tests were reviewed by reading them, not by running them.
VERDICT: PASS

NEXT: Run `uv run pytest -q tests/test_task_dependencies.py tests/test_contracts.py tests/test_task_stages.py tests/test_selfevolve.py`, then `uv run pytest -q`, and record both summary lines. The second command is spec §7's full-suite run, which the developer did not do.
NEXT: Fix the `workflow.py:24` docstring and the `specs.py:161` label.
MESSAGE recommendation to developer: Update `workflow.py:24` as spec §6 says, fix the label in `specs.py:161`, and have the worker test `contract.role` against `REVIEW_ROLES` (loop.py:980) so it matches the scheduler.

PROPOSAL: Repeat the skip pass in run_dag until nothing changes :: The skip pass at scheduler.py:134-140 goes through the tasks once in id order. If a plan lists a dependent before its dependency, that task can stay queued with nothing running, and the loop breaks. This predates this change.
