# Tasks (iteration 1)



## 1. Write the specification
- key: `spec` · role: architect · writes: no

Write a short technical specification for: REQ-006: each agent's run history on its card

Acceptance: {"type": "min_length", "value": 40}

## 2. Implement the specification
- key: `build` · role: developer · writes: yes · after: spec

Implement: REQ-006: each agent's run history on its card. Record what you did and how to verify it in WORKLOG.md.

Acceptance: {"type": "file_exists", "path": "WORKLOG.md"}

## 3. Review the implementation
- key: `review` · role: qa · writes: no · after: build

Review the change against the specification: verify the logic first (trace the data flow of the normal, an edge and an error case), then the checklist. End with an OUTCOME line and VERDICT: PASS or VERDICT: FAIL.

Acceptance: {"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}
