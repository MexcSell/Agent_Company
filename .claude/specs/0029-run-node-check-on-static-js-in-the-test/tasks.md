# Tasks (iteration 1)



## 1. tests/test_static_js.py: node --check every static/*.js, with a negative case
- key: `static_js_test` · role: developer · writes: yes

Read src/agent_company/checks.py (syntax_errors, _tool) and a few files in tests/ first to match their style. Create tests/test_static_js.py:
1) Find the static directory from the package (Path(agent_company.__file__).parent / 'static'), not from the current working directory. Collect sorted(static.glob('*.js')), top level only: vendor/ is third-party and must not be checked. Assert the list is not empty, so a moved directory cannot make the test pass silently.
2) Write a parametrized test, one case per file with ids = file name. It runs subprocess.run(['node', '--check', str(path)], capture_output=True, text=True, timeout=60) and asserts returncode == 0, with the file name and the stderr tail in the assert message.
3) Add a negative test: write a .js file with a syntax error (e.g. 'function (') to tmp_path and assert node --check returns non-zero. This proves the check really detects errors. Add a positive control for a valid tmp file.
4) If shutil.which('node') is None, skip with the reason 'node not installed: static/*.js syntax not checked'. Use pytest.mark.skipif at module level so the skip is visible in the report, not a silent pass.
The tests must run offline and must not use the DB or network. Do not change src/, the static files, or the gate. If an existing static/*.js file fails node --check, report the error output. Do not fix the file in this task: a dashboard fix is out of scope and becomes a FINDING for the GM. Run `uv run pytest -q tests/test_static_js.py` and then `uv run pytest -q`, and paste the last lines of both outputs.

Acceptance: {"type": "file_exists", "path": "tests/test_static_js.py"}; {"type": "command", "run": "uv run pytest -q tests/test_static_js.py", "requirement_keys": ["S09-R-01", "S09-R-04"], "stage": "unit"}; {"type": "command", "run": "grep -q \"node\" tests/test_static_js.py && grep -q \"\\-\\-check\" tests/test_static_js.py", "stage": "static"}

## 2. QA review of the static JS syntax test
- key: `qa_review` · role: qa · writes: no · after: static_js_test

Review tests/test_static_js.py against the static_js_test contract. Read src/agent_company/checks.py syntax_errors and _tool to confirm what the gate does today. Check the data flow of three cases:
- Normal: every top-level src/agent_company/static/*.js file is collected, and vendor/ is excluded.
- Edge: the path does not depend on the cwd, and an empty glob fails rather than passes.
- Error: a broken .js file makes the test fail, and the tmp negative test asserts a non-zero exit.
Also check that the skip when node is missing has a visible reason, that every subprocess call has a timeout, and that nothing touches the network, DB or src/. Base your verdict on the command output recorded by the gate. A developer's claim alone is not evidence. List each issue as a 'FINDING: <file>:<line> | <problem>' line. End with an OUTCOME line and then 'VERDICT: PASS' or 'VERDICT: FAIL'.

Acceptance: {"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}; {"type": "contains", "value": "OUTCOME"}; {"type": "min_length", "value": 300}
