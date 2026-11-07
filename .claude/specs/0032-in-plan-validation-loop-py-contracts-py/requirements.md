# Requirements: In plan validation (`loop.py`/`contracts.py`), reject any acceptance path under `.claude/specs/` that is outside `specs.folder(goal)`, and any condition on the loop-owned `decisions.md`. Return the error to the GM so it re-plans before staffing.

The plan as written can never pass, so it fails at planning time instead of using up iterations and QA.

From root-cause analysis K54 of goal #19. Follow-up: Re-run goal #19 (or a copy of it). The plan must pass validation with every acceptance path inside `.claude/specs/0019-req-001-merge-approval-bound-to-the-revi/` or the source tree. The developer task's conditions must pass in iteration 1, `qa_review` must run rather than be skipped, and the gate must write `decisions.md` with "Iteration 1: PASS". A test in `tests/` must show that a plan with a `decisions.md` condition, or a mismatched spec slug, is rejected.

## Quality commands
- `uv run pytest -q`
