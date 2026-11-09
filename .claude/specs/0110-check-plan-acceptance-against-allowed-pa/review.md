# Review (iteration 1)

## Review plan acceptance scope check (qa, done)

I'm approving this with comments: all three cases trace correctly and the tests pass, but I found three low-severity gaps for follow-up.

## Logic verification

I traced each changed function: `specs._out_of_scope`, `specs.check_plan`, and the two `check_plan` calls in `loop._plan`.

**Normal case: a `file_exists` inside the allowed paths.** Example: `file_exists src/x.py` with `allowed_paths=["src/*"]`.
- `check_plan` strips the worktree prefix and any leading `./`. The path does not match `SPEC_DIR`, so it is not rewritten.
- `allowed` is `["src/*"]`, which is not empty, so `_out_of_scope` runs.
- In `_out_of_scope`, the config and ADR exemptions don't apply. `checks.in_scope("src/x.py", "src/*")` is True, so it returns False.
- Nothing is collected in `outside`, so nothing is raised or dropped.
- At `loop.py:593` the returned list is empty, and the plan reaches `strategies.violations`, `_in_rooms` and `_risk_based` unchanged.
- Covered by `test_allowed_file_passes`, which checks the path is unchanged, and `test_directory_pattern_passes` for the patterns `src/` and `src`.

**Edge cases:**
- **A file outside the allowed paths that already exists in the worktree.** `(Path(worktree)/path).exists()` is True, so the path is not out of scope. That is correct, because `contracts._check_one` would pass it without any change. Covered by `test_existing_file_passes`.
- **A `.claude/specs` answer path.** A foreign spec folder is first rewritten to this goal's folder, and `path = new` is set. The loop-file check still raises for loop-written files. The `.claude/` prefix in `CONFIG_PATHS` then exempts the path. Covered by `test_scope_gate_exemptions_pass`, `test_foreign_spec_folder_rewritten_then_exempt` and `test_loop_file_still_rejected` (strict and drop mode).
- **`.mcp.json` and `docs/adr/...`.** Both are exempt by prefix (`specs.py:60`).
- **A goal without allowed paths.** `allowed` is empty, so `_out_of_scope` is never called. This holds whether `allowed_paths` is `[]` or the attribute is missing (`getattr(..., None)`). Covered by `test_no_allowed_paths_passes`.

**Error case: a new file outside the allowed paths.** Example: `docs/x.md` with `allowed_paths=["src/*"]`.
- `_out_of_scope` returns True and the condition is collected.
- After the loop, `outside` is non-empty and `drop_out_of_scope` is False, so `check_plan` raises one `ValueError`. It names every `task <key>: <path>` and the allowed paths, and suggests a contains, regex or command condition instead.
- At `loop.py:593` that `ValueError` is caught by `except (ValueError, KeyError, TypeError)`.
- On attempt 0 with `res.ok`, the message (cut to 1500 characters) is appended to the prompt and the planner is asked again. This happens only once, because the loop is `range(2)`.
- On the second failure the code builds the fallback plan, now at `loop.py:618`. There `check_plan(goal, plan, drop_out_of_scope=True)` removes the condition, adds `dropped file_exists ...` to the report and logs `Standard plan corrected: ...`. It does not raise.
- In practice this matters for the docs fallback task's `WORKLOG.md` (`strategies.py:356`). Covered by `test_disallowed_new_file_rejected`, `test_all_violations_named_in_one_error`, `test_drop_mode_*` and the end-to-end `test_fallback_plan_drops_worklog_outside_scope`.

**Root cause or symptom?** This fixes the root cause. A plan that can never pass is now rejected when the planner writes it, instead of failing at the gate. The planner gets a correctable message.

**Same rule as the gate.** The matcher is `checks.in_scope`, the function `checks.scope_violations` uses at `checks.py:161`. Both read the goal's `allowed_paths`; the gate takes them at `quality.py:211`. So the planner and the gate agree on what is in scope.

**Exemptions compared with `scope_violations`.** `.claude/` and `.mcp.json` match the gate exactly. The ADR exemption is wider:
- The gate exempts only `adr.paths(goal_id)`, the ADR files the platform wrote for this goal.
- The planner exempts all of `docs/adr/`, as the task asked. ADR ids are not known at planning time.
- The result: a task's own `docs/adr/...` file passes the planner but would still fail the gate. The planner is more lenient, never stricter.

The gate has no `WORKLOG.md` exemption (I checked `quality.py`, `checks.py` and `loop.py`), so dropping `WORKLOG.md` matches the gate's view.

**Existing behaviour.** `git diff main...HEAD -- tests/test_spec_paths.py src/agent_company/checks.py` is empty, so neither file changed. The spec-path rewrite still happens before the scope check, and its report entries come first (`test_drop_mode_keeps_rewrite_report`).

**Test run:** `uv run pytest -q tests/test_plan_allowed_paths.py tests/test_spec_paths.py` printed **`23 passed in 5.78s`**.

## Checklist

- **Contract satisfied:** yes. Strict at plan time, dropped in the fallback plan, and the matcher is the gate's own rule.
- **Interfaces compatible:** yes. `drop_out_of_scope` is keyword-only with default False, so existing callers behave the same.
- **Error handling:** the `ValueError` uses the existing retry path. The fallback never raises on scope, so a model cannot produce an unrecoverable plan this way. The loop-file `ValueError` still raises in drop mode, as before.
- **Edge cases:** handled, apart from the absolute and `..` paths and the empty acceptance list in the findings below.
- **Tests sufficient:** mostly. 13 unit cases plus an end-to-end loop run. No test drives the loop's first call through "model plan fails on scope → retry → fallback". By reading, the generic `except` covers it.
- **Dependencies:** none added. `adr` and `checks` are imported inside the function, consistent with `revert_outside_config`.
- **Security:** no new paths or executors. The worktree existence check only reads.
- **Performance:** one `exists()` per out-of-scope `file_exists`, which is negligible.
- **Docs updated:** the `specs.py` description in `CLAUDE.md` has the new clause.

## Findings

FINDING: src/agent_company/specs.py:64 | `_out_of_scope` treats any existing file as passable, but `Path(worktree) / "/etc/hosts"` resolves to `/etc/hosts`, and `../x` points outside the worktree. Both exist, so the planner accepts them, yet `contracts._check_one` requires `workdir.resolve() in target.parents` and those conditions can never pass. This is the class the goal is meant to catch; found by reading the code, not by running it. | Resolve the target and count existence only when the worktree is one of its parents; otherwise treat the path as out of scope.

FINDING: src/agent_company/specs.py:109 | In drop mode a task can lose every acceptance condition: the docs fallback `write` task whose only condition was `file_exists WORKLOG.md`. `contracts.check` loops over an empty list, finds nothing failed, and the writing task passes acceptance with no evidence. | When a drop empties `c.acceptance`, put in the `TaskContract` default `Condition(type="min_length", value=20)` or a `contains` condition on the task output.

FINDING: src/agent_company/specs.py:60 | The ADR exemption covers all of `docs/adr/`, while the gate (`checks.py:159`) exempts only `adr.paths(goal_id)`. A task's own `docs/adr/0009-x.md` outside the allowed paths passes the planner, then fails the scope gate. This is lenient, not a regression, and the task asked for it. | Document the difference in the `_out_of_scope` docstring, or limit the exemption to files written by the platform's decision step.

FINDING: tests/test_plan_allowed_paths.py:118 | No test runs a model plan that fails only on scope through `loop._plan`, that is, a strict raise at `loop.py:593`, the correction text in the prompt, and one retry. The end-to-end test reaches the fallback through `"not json"`, not through the scope error. | Add a loop test that patches `OfflineExecutor._plan` to return a valid JSON plan with `file_exists docs/x.md` under `allowed_paths=["src/*"]`, then assert the `"was invalid (file_exists acceptance outside"` event and the `Standard plan corrected` event.

NEXT: Fix the absolute and `..` path check in `_out_of_scope` and replace an emptied acceptance list with a default condition. Both are small follow-ups.

MESSAGE recommendation to general_manager: If the goal's test strategy requires an integration stage, attach `uv run pytest -q tests/test_plan_allowed_paths.py::test_fallback_plan_drops_worklog_outside_scope` as a `{"type": "command", "stage": "integration"}` condition, because it runs the whole loop.

PROPOSAL: Keep the planner's scope exemptions with the gate's :: `specs._out_of_scope` repeats the gate's exemptions by hand. A shared `checks.scope_exempt(path, exempt)` would keep the two from drifting apart again.

OUTCOME: APPROVED_WITH_COMMENTS
VERDICT: PASS
