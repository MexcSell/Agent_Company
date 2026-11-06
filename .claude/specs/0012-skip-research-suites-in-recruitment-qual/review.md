# Review (iteration 1)

## QA review of the qualification_suite fix (qa, failed)

## Logic verification

The diff is small: one line plus the docstring in `recruitment.py`, and one helper plus three tests in `tests/test_recruitment.py`. Branch `agents/goal-12` vs `main`, commit e3e998b. `git diff --stat main...HEAD -- src/agent_company/evaluation.py evals/` is empty, so neither of those changed.

**`qualification_suite`** (`src/agent_company/recruitment.py:442-453`). The old filter was `kind != "final"`. The new one only keeps kinds in `QUALIFY_KINDS = {"practice","benchmark"}` (`:49`).

- **Normal case, `["python"]`.** `list_suites()` returns `python-practice` (kind practice, with the python skill). The filter keeps it and the skill matches, so the result is `"python-practice"`. `staff` then stores `{"suite": "python-practice", "status": "queued"}` (`:247-249`). `qualify` reads it (`:487`) and calls `run_suite("python-practice", …, repeats=1, trial={trainee_id, qualify})` (`:493`). That is a practice kind, so it is not hidden as a final suite and has no room check (`evaluation.py:170-178`). Correct.
- **Edge case, `["research"]` with research and room suites listed.** Both are dropped by kind, so no suite names the skill. The fallback picks `smoke`: its file has no `kind`, and `list_suites` defaults that to `"benchmark"` (`evaluation.py:79`). The record gets `suite="smoke"`, and `run_suite("smoke")` takes the plain benchmark path. Before the fix, a room suite reached `run_suite`. It set `trial["room"]` and raised `ValueError` when the room was not gated (`evaluation.py:172-178`). If the room was gated, it was misused: the room's disabled agents would be staffed for a recruit's qualification. A research suite was handed to a writing candidate as its task suite. The fix removes the cause, the choice of suite, rather than catching the error. **This fixes the root cause.**
- **Error case, no usable suite.** Both `next(...)` calls return `""`. `staff` then takes the else branch (`:252`), so the record is never `qualifying` and `qualify` returns `{}` (`:480`).
- **Kinds that are refused or certify.** room, research, final and adversarial are all outside the allow-list, so none of them can be chosen. A retrieval suite cannot even reach `list_suites`: it has no `tasks`, so `load_suite` raises (`evaluation.py:56`), and `list_suites` catches that and skips it (`:83`). Generated final suites are excluded by kind whether approved or not. Generated practice suites from `exercises.py` still qualify, which is wanted.
- **Do the tests fail without the fix?** By static reading, yes. With `!= "final"`, the injected `research-suite`/`room-suite` entries come first in `list_suites` and match `research`, so all three new tests fail on their `== "smoke"` or `== ""` assertions. The developer reports reproducing exactly this. I could not re-run it myself (see BLOCKER).

## Checklist

- **Contract:** met. Only `recruitment.py` and its test file changed in code; the two `.claude/specs` docs are the goal's own artifacts.
- **Interfaces:** the signature and return values are unchanged, and the only caller is `:247`.
- **Error handling:** the `''` path is unchanged. `qualify`'s `except` stays as a backstop.
- **Edge cases:** covered as traced above. An allow-list fails safe: an unknown future kind is skipped.
- **Tests:** they cover normal, edge, error and an end-to-end `qualify` on the real harness offline. There are small gaps, listed below.
- **Dependencies, security, performance:** nothing new; the change is one set lookup.
- **Docs:** the docstring is updated. CLAUDE.md does not list suite kinds for qualification, so it needs no update.

FINDING: tests/test_recruitment.py:514 | The retrieval entry injected into the test cannot happen for real: `list_suites` never returns a retrieval suite because it has no tasks (`evaluation.py:56,83`). That assertion therefore proves nothing about real data; it is harmless but misleading. | Add a comment that it guards the filter only, or drop "retrieval" from the injected kinds.
FINDING: tests/test_recruitment.py:524-541 | The end-to-end test asserts `"ValueError" not in error`, but nothing in it would raise one with the research suite. The failure that is actually prevented is the room-suite `ValueError` at `evaluation.py:178`, and no end-to-end test covers that. | Add a room-kind file naming the skill in the same test, or a variant of it, so the original crash path is the one exercised.
FINDING: src/agent_company/recruitment.py:487 | `qualify` still falls back to `QUALIFY_SUITE` without the kind filter (`... or QUALIFY_SUITE`). It is not reachable through `staff`, but a hand-edited record would bypass `QUALIFY_KINDS`. | Optional: send that fallback through `qualification_suite([])`, or leave it with a comment.
FINDING: evals/smoke.json | The fallback only works while `smoke.json` has no `kind`, or a practice/benchmark one; this dependency is implicit. If someone adds a `kind` outside those two, qualification is silently skipped for unmatched skills. | Optional: add an assertion to the existing smoke-fallback test that `smoke`'s kind is in `QUALIFY_KINDS`.

BLOCKER: I could not run `uv run pytest -q tests/test_recruitment.py`. Every pytest call, through `uv run` or `.venv/bin/python`, needed approval that wasn't granted in this session. The pass result (27 passed) and the red-without-fix result come from the developer's report, not from my own run. The gate's acceptance check runs pytest independently.

NEXT: Let the gate's automated `uv run pytest -q tests/test_recruitment.py` confirm 27 passed before the merge.
NEXT: Optionally, cover the room-suite crash path end to end (finding 2).

MESSAGE recommendation to developer: In `tests/test_recruitment.py`, exercise the room-suite `ValueError` path (`evaluation.py:178`) end to end; the current integration test only covers a research suite, which never raised.

OUTCOME: APPROVED_WITH_COMMENTS. The kind allow-list removes the root cause. Read statically, it is correct for the normal, edge and error cases, and it keeps every refused or certifying kind out. `evaluation.py` and `evals/` are unchanged. The test run is the developer's evidence, not one I re-ran.

VERDICT: PASS
