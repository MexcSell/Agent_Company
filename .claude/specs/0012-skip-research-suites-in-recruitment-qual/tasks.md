# Tasks (iteration 2)



## 1. QA review of the qualification_suite fix (read-only, scoped)
- key: `qa_review` · role: qa · writes: no

Review only the change in commit e3e998b: `git diff 97eac0c e3e998b -- src/agent_company/recruitment.py tests/test_recruitment.py`. Keep the review short to stay within budget. Read `qualification_suite` and `QUALIFY_KINDS` in src/agent_company/recruitment.py, and the tests from tests/test_recruitment.py:496 onwards. Do not explore other modules beyond one grep for the suite kinds that `evaluation.run_suite` refuses. Trace the data flow of three cases: (1) normal: skills ['python'] gives 'python-practice'; (2) edge: skills ['research'] with a research suite and a room suite present falls back to 'smoke'; (3) error: no practice or benchmark suite gives '', and the caller `qualify` handles '' without a ValueError (check how `qualify` uses the return value). Then confirm that the new tests would fail without the fix: with the old filter `kind != 'final'`, a research suite naming the `research` skill would be chosen. Run `uv run pytest -q tests/test_recruitment.py` once and report the result line. List any problems as `FINDING: file:line | issue` lines. End with an OUTCOME line and 'VERDICT: PASS' or 'VERDICT: FAIL'.

Acceptance: {"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}; {"type": "contains", "value": "OUTCOME"}; {"type": "contains", "value": "qualification_suite"}; {"type": "command", "run": "uv run pytest -q tests/test_recruitment.py", "requirement_keys": ["S03-R-04"], "stage": "unit"}
