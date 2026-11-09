# Review (iteration 1)

## Review the eval-ancestry routing fix in projects.decide() (qa, done)

## Logic verification

The fix is approved with comments: it addresses the root cause and the required tests pass. Running the required command gave this summary line: `22 passed in 11.22s`.

**Root cause.** The old branch in `decide()` checked `origin.origin == "eval"`, which looks at one level only. A goal spawned from an eval goal's proposal has origin `"proposal"`, so its own proposals took the ordinary branch and copied the scratch repo, its checks and its parent. The fix replaces that check with `_eval_source(s, origin)` (`projects.py:1074-1089`). It looks for an eval ancestor anywhere in the chain, and also catches a repo path inside the eval workspaces folder. It fixes the cause rather than hiding a symptom. The spawn settings at 1194-1200 are unchanged: platform repo, platform `quality_commands`, platform `project_id`, `parent: None`. Only the description text changed.

**`_eval_source`**
- **Normal, depth 1** (eval goal R → proposal goal A raises a proposal):
  - The walk checks A (origin `proposal`), then its parent R (origin `eval`) and returns R.
  - Since R is not A, the description reads "goal #A, which descends from benchmark goal #R".
  - The spawn gets the platform path, checks and project id, with no parent.
- **Normal, depth 2** (R → A → B raises the proposal): the walk goes B → A → R and returns R. The description names both #B and #R.
- **Eval goal itself:** the walk returns the origin at once and the description reads "benchmark goal #origin". The original test's check `f"#{eval_goal}" in g.description` still holds.
- **Edge, repo under `workspaces_dir/eval-*` with no eval ancestor:**
  - The walk finds nothing.
  - `Path(repo).resolve().relative_to(workspaces_dir.resolve())` gives `eval-smoke-…`, whose first part starts with `eval-`, so it returns the origin.
  - The description names the scratch path. The new path-only test covers this case.
- **Edge, empty `repo_path`:** skipped by the guard at 1082, returns None, ordinary branch.
- **Edge, `tmp_path/eval-x` outside the workspaces folder:** `workspaces_dir` is `tmp_path/data/workspaces`, so `relative_to` raises `ValueError`, which is caught, and the result is None. The goal keeps its repo, which is what the original test's `plain` goal asserts, and it still passes.
- **Edge, the workspaces folder itself:** `relative.parts` is empty, so the result is None.
- **Edge, parent cycle:** the `seen` set stops the walk. Then the path check runs and the result is None (or the origin, if its path is under `eval-*`).
- **Edge, missing parent:** `s.get` returns None and the loop ends.
- **Error, chain guards:** `chain_block` (1174) still runs before the platform branch (1182).
  - `_root`, `_depth` and `_spawned_from` walk the real chain up to the eval root, so the limit and depth guards still refuse at the boundary.
  - With `hold=True`, the early return at 1176 leaves the proposal pending.
  - If `loop.create_goal` raises `ValueError`, the hold/reject path at 1219-1231 is unchanged.
- **Error, `rule`/`project` kinds:** `kind == "rule"` is still handled first (1180), and the new branch requires `kind != "project"`. Neither kind is affected.

**Would the new tests fail without the fix?** I could not run the old code (see the BLOCKER line), so this comes from tracing it. In the depth-1, depth-2 and path-only tests the proposing goal has origin `"proposal"`, so the old check is false and the ordinary branch runs: `repo=scratch`, `parent=origin`. That fails `goal.repo_path == plat_path` and `parent_goal_id is None`. The ordinary-chain test and the original test pass either way. That gives 3 failures, which matches the developer's report of "3 failed, 2 passed".

**Scope:** `git diff --name-only ea05c1e..HEAD` (the fix commit only) lists exactly `src/agent_company/projects.py` and `tests/test_eval_proposals.py`. The two `.claude/specs/0313-…` files come from the planner's commit `ea05c1e` and are the goal's spec artifacts. The original test is unchanged.

## Checklist
- **Contract satisfied:** yes.
  - Eval goals at any depth go to the platform project.
  - The eval-workspace path is recognised.
  - The spawn matches the existing eval branch.
  - The chain guards are kept and still run first.
- **Interfaces compatible:** yes. `decide()`'s signature and return value are unchanged, and `_eval_source` is a new private helper.
- **Error handling:** `ValueError` and `OSError` from `resolve()`/`relative_to()` are caught; cycles and missing parents end the walk.
- **Edge cases:** traced above. Cycle, missing parent and empty repo path are checked by hand only, not by tests.
- **Tests:** 4 new tests cover depth 1, depth 2, path only and an ordinary chain. There are gaps; see the findings.
- **Dependencies:** none added.
- **Security:** the description now contains a local scratch path, which is not secret. No new write paths.
- **Performance:** at most one query per ancestor; chains are bounded by `max_goal_depth`.
- **Docs:** the comment was updated. CLAUDE.md does not describe this branch, so no change is needed there.

FINDING: tests/test_eval_proposals.py:42 | The depth-2 test only passes because the helper raises `max_goal_depth` to 4. With the default of 3, `chain_block` (projects.py:1134) refuses a depth-2 eval descendant before the platform branch is reached. That is correct ("keep the chain guards"), but no test checks it, so "any depth" really means "any depth the guard allows" | Add a test that a depth-2 eval descendant at the default depth is refused (and stays pending with `hold=True`) and creates no platform goal.
FINDING: tests/test_eval_proposals.py:37 | No test covers a parent cycle or a dangling `parent_goal_id` in `_eval_source`; both are only traced by hand | Add one parametrised case for each: a cycle with no eval ancestor keeps its repo, and a missing parent ends the walk without an error.
FINDING: src/agent_company/projects.py:1085 | The path-only fallback depends on the eval harness naming its workspaces `eval-*` directly under `workspaces_dir`. If that naming changes, the fallback silently stops matching (the ancestry walk still works) | Name the prefix once as a shared constant used by the eval harness and `_eval_source`, or note the coupling in a comment.

BLOCKER: environment — the sandbox refused to copy the tree to /tmp with the pre-fix `projects.py` (`git archive … | tar -x` plus `git show fd12918:…`), so whether the new tests fail without the fix is traced by hand above, not executed.

NEXT: Optionally add the guard-boundary and cycle/missing-parent tests named in the findings; they don't block the merge.

MESSAGE recommendation to developer: Add a test in tests/test_eval_proposals.py that a depth-2 eval descendant at the default max_goal_depth is held or rejected by chain_block and does not create a platform goal.

OUTCOME: APPROVED_WITH_COMMENTS

VERDICT: PASS
