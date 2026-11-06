# Decisions: G-R-19: a study rewrite is a candidate an administrator applies after seeing the diff

## Iteration 1: FAIL
- backend (developer): failed
- ui_docs (developer): skipped
- review (qa): skipped

Findings:
- task backend: the run cost $1.2718 (task budget $1.2000)
- task ui_docs: skipped: a dependency failed
- task review: skipped: a dependency failed

Next: fix the findings (loop back to Build).

## Iteration 2: FAIL
- backend_verify (developer): done
- ui_docs (developer): failed
- qa_review (qa): skipped

Findings:
- task ui_docs: the run cost $2.2444 (task budget $2.0000)
- task qa_review: skipped: a dependency failed

Next: fix the findings (loop back to Build).

## Iteration 3: FAIL
- backend_verify (developer): done
- ui_verify (developer): done
- docs_verify (developer): done
- qa_review (qa): done

Findings:
- review qa_review: reviewer: FAIL

Next: fix the findings (loop back to Build).
