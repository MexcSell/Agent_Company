# Tasks (iteration 1)



## 1. Run _link_contradictions for every draft→approved review, with regression tests
- key: `fix_review_contradictions` · role: developer · writes: yes

Bug: in src/agent_company/learning.py, review() (around line 788) calls `_link_contradictions(s, i)` only when `was == "draft" and i.outcome == "imported"`. A draft that is not an import, such as a lesson from learn() or a new version from supersede() that screen() held back as draft, joins the approved set with no contradiction check. Steps: (1) Reproduce. Write a test in tests/test_knowledge.py: create an approved item, create a second item in status 'draft' with outcome 'success' or 'failure' whose text contradicts it (copy the contradiction pattern from the existing contradiction tests in tests/test_knowledge.py; _contradicts decides what counts). Call learning.review(id, True, 'user:admin') and assert that a KgEdge with rel 'CONTRADICTS' and src_id == str(id) now exists. Confirm the test fails before the fix. (2) Fix the cause: change the condition so that any draft→approved transition calls _link_contradictions. Keep the existing exclusion that learn() applies to eval goals (see how `is_eval` is computed near line 694); if an eval draft can reach review(), exclude it the same way, else leave a short comment. Do not run the linking when the item was already approved, so re-approving creates no duplicate edges. Leave the import path and judge_contradictions(edges) behaving as they do now. (3) Regression tests: (a) a non-import draft approved → CONTRADICTS edge plus the 'contradicts' notification/event; (b) an imported draft approved → still linked (existing behaviour); (c) re-approving an approved item → no new edge; (d) rejecting a draft → no edge. Do not change other modules. Tests must run offline (the judge falls back without a model; check how the existing tests handle judge_contradictions).

Acceptance: {"type": "command", "run": "uv run pytest -q tests/test_knowledge.py", "requirement_keys": ["S11-R-05"], "stage": "unit"}; {"type": "command", "run": "uv run pytest -q tests/test_knowledge.py tests/test_hypotheses.py tests/test_research_review.py tests/test_requirements.py", "stage": "integration"}

## 2. QA review of the draft-approval contradiction fix
- key: `qa_review` · role: qa · writes: no · after: fix_review_contradictions

Review the diff of fix_review_contradictions in src/agent_company/learning.py and tests/test_knowledge.py. Verify the logic first by tracing review() for three cases. Normal: a non-import draft that contradicts an approved item is approved, a CONTRADICTS edge is created and judge_contradictions gets its id. Edge: an item that is already approved is approved again and no duplicate edge appears; an imported draft still links; an eval-goal item stays excluded as in learn(). Error: a quarantined or superseded item still raises ValueError, and rejecting creates no edge. Check that the tests fail without the fix: read the old condition and confirm the new tests would not pass against it. Check that no other module changed. List each problem as a 'FINDING: file:line | issue | fix' line, then write an OUTCOME line, and end with 'VERDICT: PASS' or 'VERDICT: FAIL'.

Acceptance: {"type": "regex", "pattern": "VERDICT:\\s*(PASS|FAIL)"}; {"type": "contains", "value": "OUTCOME"}; {"type": "min_length", "value": 300}
