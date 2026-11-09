# Requirements: EL-R-08: mutation testing of the quality gate

See docs/requirements/EVOLUTION-LOOPS.md EL-R-08. evaluation.py: apply known mutations (remove an assert, flip a comparison boundary) to a passing change in a scratch worktree, run the gate's quality command in the L3 sandbox, report per check the share of mutations caught. Acceptance: pytest where a removed assertion in a tiny seeded repo is caught by its pytest command.

## Quality commands
- `uv run pytest -q`
