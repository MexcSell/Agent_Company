# Tasks (iteration 1)



## 1. Pin mcp.slugify's behaviour on non-str and falsy input in tests/test_mcp_slugify.py
- key: `pin_tests` · role: developer · writes: yes

Create tests/test_mcp_slugify.py using pytest, like the other tests/test_mcp_*.py files, and import it with `from agent_company.mcp import slugify`. Do NOT change src/agent_company/mcp.py or any eval file. First run slugify on each input below and confirm the expected result. If anything differs, pin what you observe and report the difference in your answer; do not 'fix' the code. Expected (from reading mcp.py:291): (1) str: slugify('Hello, World!') == 'hello_world'; the result is cut to at most 16 characters with no leading or trailing '_'; slugify('!!!') == 'server'. (2) Falsy input falls back on purpose: slugify(None) == 'server', slugify('') == 'server', and the same for 0 and False. Add a one-line comment saying this fallback is intentional (save_server relies on it). (3) Truthy non-str input must fail loudly, never return a slug: `with pytest.raises(AttributeError): slugify(123)`, the same for ['a'], and `with pytest.raises(TypeError): slugify(b'x')`. Add a one-line comment saying a guard that quietly returns '' or 'server' for these would be a behaviour change and must update this test on purpose. Keep the file short (about 30-50 lines). Run the acceptance commands and report their output.

Acceptance: {"type": "file_exists", "path": "tests/test_mcp_slugify.py"}; {"type": "command", "run": "uv run pytest -q tests/test_mcp_slugify.py", "stage": "unit"}; {"type": "command", "run": "python3 -c \"import pathlib,sys; t=pathlib.Path('tests/test_mcp_slugify.py').read_text(); sys.exit(0 if 'raises(AttributeError)' in t and 'raises(TypeError)' in t and 'None' in t else 1)\"", "stage": "static"}; {"type": "command", "run": "uv run pytest -q tests/test_mcp_drift.py tests/test_mcp_everyone.py tests/test_mcp_bridge.py", "stage": "unit"}

## 2. Review the slugify pinning tests against mcp.py
- key: `qa_review` · role: qa · writes: no · after: pin_tests

Review tests/test_mcp_slugify.py against src/agent_company/mcp.py:290-291 and its caller save_server (mcp.py:354). Trace the data flow for one normal case ('Hello, World!'), one edge case (None and '' -> 'server'; truncation to 16 characters with no trailing '_') and one error case (123 -> AttributeError, b'x' -> TypeError). Check that: each assertion matches what the code really does; the AttributeError/TypeError tests would fail if someone added a guard that quietly returns '' or 'server' for non-str input; the None -> 'server' test would fail if that fallback were removed; and no file outside tests/test_mcp_slugify.py changed (git diff --stat). Run `uv run pytest -q tests/test_mcp_slugify.py`. List each problem as a FINDING line with file:line, then give an OUTCOME and end with 'VERDICT: PASS' or 'VERDICT: FAIL'.

Acceptance: {"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}; {"type": "contains", "value": "OUTCOME"}
