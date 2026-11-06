# Decisions: REQ-002: agent marketplace — discover, evaluate, import external agents

## Iteration 1: FAIL
- spec (architect): done
- backend (developer): done
- surface (developer): done
- qa (qa): done
- backend-security (qa): done

Findings:
- review qa: reviewer: FAIL
- review backend-security: reviewer: FAIL

Next: fix the findings (loop back to Build).

## Iteration 2: FAIL
- candidate_flow_fixes (developer): done
- fetch_hardening (developer): done
- qa_review (qa): failed
- candidate_flow_fixes-security (qa): done
- fetch_hardening-security (qa): done
- qa_review-security (security): skipped

Findings:
- task qa_review: the run cost $2.6121 (task budget $1.5000)
- review candidate_flow_fixes-security: reviewer: FAIL
- task qa_review-security: skipped: a dependency failed

Next: fix the findings (loop back to Build).

## Iteration 3: PASS
- route_decisions (developer): done
- review (qa): done

Next: approval and merge.
