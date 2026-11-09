# Decisions: In plan validation (`loop.py`/`contracts.py`), reject any acceptance path under `.claude/specs/` that is outside `specs.folder(goal)`, and any condition on the loop-owned `decisions.md`. Return the error to the GM so it re-plans before staffing.

## Iteration 1: PASS
- reject_spec_paths (developer): done
- qa_review (qa): done

Next: approval and merge.
