# Tasks (iteration 1)



## 1. qualification_suite picks only practice/benchmark suites; regression test for a research-skilled recruit
- key: `fix` · role: developer · writes: yes

Reproduce first. In tests/test_recruitment.py, write a test that monkeypatches evaluation.list_suites (or puts a suite file in evaluation.generated_dir()) so that it returns a suite with kind 'research' and skills ['research'] next to the real ones. Check that recruitment.qualification_suite(['research']) currently returns that research suite. Then fix src/agent_company/recruitment.py qualification_suite: build the candidate list from suites whose kind is in {'practice', 'benchmark'} only, instead of 'kind != "final"'. This also skips research, room, adversarial, retrieval and final suites. Put the allowed kinds in a module constant (e.g. QUALIFY_KINDS) and update the docstring so it names the kinds that are skipped and why: run_suite refuses or misuses them, and final/adversarial ones stay for certification. Keep the fallback to QUALIFY_SUITE and the '' result when neither exists. Regression tests: (1) qualification_suite(['research']) returns 'smoke' (the fallback), not the research suite, and also when a room suite names the skill. (2) The existing test_qualification_suites_name_a_skill_or_fall_back_to_smoke still passes. (3) Without monkeypatching run_suite, when a research suite is listed, staffing a writing recruit with skills ['research'] and qualify_recruits on (use the qualify_on helper in the test file) records qualification.suite == 'smoke' and no 'error' with 'ValueError'. Keep the offline simulator, as the other tests in that file do. Do not change evaluation.py or the eval suite files.

Acceptance: {"type": "command", "run": "uv run pytest -q tests/test_recruitment.py", "requirement_keys": ["S03-R-04"], "stage": "unit"}; {"type": "command", "run": "grep -q 'practice' src/agent_company/recruitment.py && ! grep -q 'x\\[\"kind\"\\] != \"final\"' src/agent_company/recruitment.py"}

## 2. QA review of the qualification_suite fix
- key: `review` · role: qa · writes: no · after: fix

Review the diff of src/agent_company/recruitment.py and tests/test_recruitment.py. Follow the data flow of qualification_suite -> recruitment record 'suite' -> qualify -> evaluation.run_suite for three cases. Normal: skills ['python'] -> python-practice. Edge: skills ['research'] while a research and a room suite are listed -> the QUALIFY_SUITE fallback. Error: no usable suite -> '' and no qualification. Confirm that no suite kind run_suite refuses (room with an ungated room, retrieval with no tasks) or that certifies (final, adversarial) can still be chosen. Confirm that the tests fail without the fix and that evaluation.py and evals/ are unchanged. Run `uv run pytest -q tests/test_recruitment.py`. List FINDING lines (file:line | problem), then an OUTCOME line, then 'VERDICT: PASS' or 'VERDICT: FAIL'.

Acceptance: {"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}; {"type": "contains", "value": "OUTCOME"}
