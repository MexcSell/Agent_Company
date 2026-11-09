# Requirements: EL-R-04: a recurring failure becomes a proposed project rule

See docs/requirements/EVOLUTION-LOOPS.md EL-R-04. When rca.py finds the same cause (failure layer + failing check) in 3 or more failed goals, the instructor drafts a one-line rule for .claude/rules/ citing those goals; it becomes an ordinary pending proposal (projects.py) and, once approved, a catalog 'rule' item that catalog/gm configure exports. No duplicate proposal for a cause that already has one. Acceptance: a pytest where three failed goals with one cause yield exactly one rule proposal citing them and a fourth does not duplicate it.

## Quality commands
- `uv run pytest -q`
