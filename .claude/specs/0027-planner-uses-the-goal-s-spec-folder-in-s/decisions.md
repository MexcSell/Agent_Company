# Decisions: Planner uses the goal's spec folder in spec-task acceptance paths

## Iteration 1: FAIL
- fix_spec_paths (developer): failed
- qa_review (qa): skipped

Findings:
- task fix_spec_paths: tests/test_spec_paths.py does not exist; `grep -n 'def check_plan' src/agent_company/specs.py && grep -n 'def planner_note' src/agent_company/specs.py && grep -n 'specs.check_plan' src/agent_company/loop.py && grep -n 'specs.planner_note' src/agent_company/loop.py` exit 1 (L3); `uv run pytest -q tes
- task qa_review: skipped: a dependency failed
- command uv run pytest -q tests/test_spec_paths.py: `uv run pytest -q tests/test_spec_paths.py` exit 4 (L3)

no tests ran in 0.00s
ERROR: file or directory not found: tests/test_spec_paths.py

Next: fix the findings (loop back to Build).
