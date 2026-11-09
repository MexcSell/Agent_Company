# Review (iteration 1)

## QA review of the spec-path rejection and the GM re-plan (qa, done)

I couldn't run any tests: every Python command (`uv run`, `.venv/bin/python`, `python3`) needed approval and was refused. I traced the diff by hand and used the developer's pytest cache files in the worktree as indirect evidence. The code does what the task asks, so I'm approving with comments.

## Logic verification

**`specs.check_plan`** (`src/agent_company/specs.py:59-98`)
- **Root cause:** fixed, not hidden. The old version quietly repointed `.claude/specs/<X>/` at the goal's own folder with `SPEC_DIR.sub` and returned a report. Now it collects every problem, raises one `ValueError`, and changes nothing until the plan is clean (`fixes` are applied only after the `if problems: raise`, lines 94-97). A goal #19-style plan can no longer be patched into something that still can't pass.
- **Normal case:** a condition on `{OWN}/spec.md` plus a command `grep … {OWN}/spec.md && ls {OWN}` passes.
  - `SPEC_DIR` captures the folder name exactly equal to `own`.
  - `LOOP_FILE` has no match, so the problem list stays empty and `[]` is returned.
  - `slug()` (lines 38-40) only produces `[0-9]{4}-[a-z0-9-]+`, which fits inside `SPEC_DIR`'s allowed characters, so a goal's own folder can never be rejected by mistake.
  - Source-tree paths contain no `.claude/specs/`, so they are never checked.
- **Edge cases:**
  - **`./` and absolute prefixes:** for `file_exists`, these are stripped before `scan`. The change is applied and reported only when the path is a spec path and the plan has no problems (`test_dot_slash_and_absolute_prefix_normalised`).
  - **Folder with no trailing slash:** `ls .claude/specs/foo` is caught because `SPEC_DIR` no longer needs the `/`, and the stop characters now include `;|&<>()`.
  - **Prefix trap:** a folder name that only starts with the goal's own name is still caught, because the match is greedy and the whole name is compared for equality.
  - **Glob:** `.claude/specs/*/spec.md` captures `*`, which is not the goal's name, so it is rejected.
  - **`decisions-notes.md`:** stays allowed. `decisions.md` does not match there, and `(?![\w.-])` also lets `decisions.md.bak` through.
  - **`sa-review.md` vs `review.md`:** cannot be confused, because `LOOP_FILE` requires a `/` right before the file name.
- **Error cases:**
  - `grep 'Iteration 1: PASS' {OWN}/decisions.md` matches `LOOP_FILE`, so it is rejected with "…written by the loop itself… (the gate writes decisions.md)".
  - `.claude/specs/req-001-reviewed-commit/spec.md` on goal 19 captures `req-001-reviewed-commit`. That is not `0019-req-001-merge-approval-bound-to-the-revi`, so it is rejected, and the message names `rel/`, the correct folder.
  - A foreign folder that also holds a loop file gets both messages.

**`loop._plan`** (`src/agent_company/loop.py:589-631`)
- **Order:** the check now runs after `with_review` (line 601), `_in_rooms` and `_risk_based` (line 603), at line 604, inside the same `try`. Any spec path those steps added is checked too. Today they only add `regex` conditions (`strategies.py:313`, `loop.py:742`).
- **Normal case:** the check returns `[]`, the loop breaks, and the plan goes on to `_persist` at line 1388.
- **Error case:**
  - The `ValueError` is caught at line 607. On attempt 0 the error is added to the second prompt, up to 1500 characters (line 611). Every message line contains `rel/`, so even a cut-off message names the correct folder.
  - No tasks exist yet: `_persist` runs only after `_plan` returns (line 1383 → 1388).
  - A second bad plan falls back to `strategies.fallback_plan`, which contains no `.claude/specs/` paths (`executors.py:752-766`, `strategies.py:333-355`).
  - A bad fallback raises `RuntimeError("no valid standard plan: …")` (line 626). The outer handler at line 1477 marks the goal failed with that text, the same path as the existing "no valid plan within the project's rooms" error.
- **Side effect of a rejected attempt:** `teststrategy.store` (inside `_risk_based`) has already been written for it. The next attempt or the fallback overwrites it (`teststrategy.py:153-157`), so this is harmless.

**Would the tests have caught K54?** Yes, judging from the old code in the diff:
- Old `check_plan` rewrote foreign folders and raised nothing, so every `pytest.raises` / `rejected(...)` call in `test_foreign_folder_rejected_not_rewritten`, `test_foreign_folder_in_command_rejected[*]`, `test_mismatched_slug_rejected` and `test_dot_slash…` fails on it.
- The old loop-file message lacked "(the gate writes decisions.md)", so every `LOOP_MSG` assertion fails.
- With old `_plan`, `test_repeated_bad_plan_falls_back` would see the rewritten plan accepted, so its assertion that the standard plan was used fails.

**Other callers:** `grep -rn check_plan src tests` finds only `loop.py:604` and `loop.py:624`, plus the tests. Searching for the old log text (`grep "Spec paths"`) finds only the new log line at `loop.py:605`, so nothing reads the old "Spec paths corrected" message.

**Execution evidence:** this is the developer's run, not my own.
- `.pytest_cache/v/cache/nodeids` lists the new test ids: all six parametrized command cases, `test_decisions_condition_rejected`, `test_mismatched_slug_rejected`, `test_planner_gets_spec_path_error_and_replans` and `test_repeated_bad_plan_falls_back`.
- `lastfailed` is `{}`, written at 02:15:05. That is after the last edits to `specs.py` (02:14:49) and the test file (02:14:39), and before the commit at 02:15:46.
- `specs.cpython-313.pyc` (02:15:01) was compiled from the fixed source.
- The file has 18 tests, matching the developer's "18 passed".

## Checklist
- **Contract satisfied:** yes for the code and the test. The goal #19 re-run is still open as follow-up work after the merge.
- **Interfaces compatible:** yes. The signature and return type are unchanged, and only `loop.py` calls the function.
- **Error handling:** good. All problems are listed in one `ValueError`, and the fallback failure is a clear `RuntimeError`.
- **Edge cases:** mostly covered; see the findings for the gaps.
- **Tests sufficient:** good, with one weak spot (finding 3).
- **Dependencies:** none added.
- **Security:** no new attack surface. The check uses regexes only and runs no subprocess.
- **Performance:** trivial.
- **Docs updated:** yes, in the module docstring and the `specs.py` entry in `CLAUDE.md`.

## Findings
FINDING: src/agent_company/specs.py:35 | `LOOP_FILE` only catches a loop file written with a `.claude/specs/<X>/` prefix. `cd .claude/specs/<own> && grep -q PASS decisions.md` or `grep -rq 'Iteration 1: PASS' .claude/specs/<own>/` still get through, so a plan can still depend on what the gate writes | In command conditions, also reject a bare `decisions.md` name when the command mentions `.claude/specs/`, and reject a recursive grep over the goal's spec folder; add tests for both

FINDING: src/agent_company/specs.py:34 | `SPEC_DIR` treats `:` `,` `}` `]` `=` as part of the folder name, so `{.claude/specs/<own>,src}` or `….claude/specs/<own>:` wrongly rejects the goal's own folder. Rare, and the GM gets a clear error and re-plans | Add `,:}\]=` to the excluded characters of both `SPEC_DIR` and `LOOP_FILE`

FINDING: tests/test_spec_paths.py:150-157 | `test_repeated_bad_plan_falls_back` cannot fail on its last check: the standard plan has no spec paths, so the assertion is always true. The `RuntimeError("no valid standard plan")` branch (`loop.py:623-626`) has no test, and with today's `fallback_plan` it can't be reached | Monkeypatch `strategies.fallback_plan` to return a `decisions.md` condition and assert that `run_goal` leaves the goal `failed` with "no valid standard plan" in its result

FINDING: src/agent_company/specs.py:80 | Behaviour change: `file_exists` paths are now searched anywhere in the path (`finditer`) instead of only at the start (the old `SPEC_DIR.match`), so a fixture such as `tests/fixtures/.claude/specs/x/a.md` is now rejected | Probably intended; if so, add a one-line comment or a test that documents it

OUTCOME: APPROVED_WITH_COMMENTS. The fix addresses the K54 root cause and checks the final plan after `with_review` and `_risk_based`. The error, with the correct folder, reaches the GM's second prompt before any task exists; a second bad plan falls back and a bad fallback raises a clear `RuntimeError`. None of the findings blocks the merge.

BLOCKER: The QA session was refused every Python command (`uv run pytest`, `.venv/bin/python -m pytest`, `python3 -c`), so the tests were not run independently. The test evidence above comes from the developer's pytest cache files, not from a run in this session.
NEXT: Re-run goal #19 (or a copy) after the merge. Check that the plan passes with every path inside `.claude/specs/0019-req-001-merge-approval-bound-to-the-revi/` or the source tree, that `qa_review` runs, and that `decisions.md` contains "Iteration 1: PASS".
MESSAGE request to general_manager: Allow QA review tasks to run `uv run pytest -q <files>` in the goal worktree, so reviewers can produce their own test evidence instead of relying on cache files.
PROPOSAL: Close the remaining loop-file gaps in check_plan :: bare `decisions.md` after `cd` and recursive greps over the spec folder still let a plan depend on what the gate writes (K54 variant).

```json
{"insights": [{"title": "Use .pytest_cache as fallback execution evidence when QA cannot run Python", "description": "When the QA session is refused Python, .pytest_cache/v/cache/nodeids (which test ids were collected) and lastfailed (whether any failed), together with source/.pyc/cache timestamps compared with the commit time, show whether the developer's run used the fixed files.", "evidence": ["goal-32: lastfailed {} at 02:15:05, after the last specs.py/test edits (02:14:49/02:14:39) and before the commit at 02:15:46", "nodeids lists all new test_spec_paths ids"], "expected_benefit": "QA reports can cite real execution artifacts instead of repeating the developer's claims", "confidence": 0.6}]}
```

VERDICT: PASS
