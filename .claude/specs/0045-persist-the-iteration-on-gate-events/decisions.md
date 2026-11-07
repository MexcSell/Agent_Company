# Decisions: Persist the iteration on gate events

## Iteration 1: FAIL
- impl (developer): done
- qa (qa): done

Findings:
- review qa: reviewer: FAIL

Next: fix the findings (loop back to Build).

## Iteration 2: FAIL
- fix_index_wrap (developer): failed
- qa_review (qa): skipped

Findings:
- task fix_index_wrap: .claude/specs/0045-persist-the-iteration-on-gate-events/design.md does not exist
- task qa_review: skipped: a dependency failed

Next: fix the findings (loop back to Build).
