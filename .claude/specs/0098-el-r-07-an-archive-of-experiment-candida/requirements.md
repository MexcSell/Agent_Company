# Requirements: EL-R-07: an archive of experiment candidates with lineage

See docs/requirements/EVOLUTION-LOOPS.md EL-R-07 (after the meta-agent design archive diagram). experiments.py: record each experiment's parent experiment id and evaluation log; a new candidate for the same target may start from the best or a diverse earlier candidate; up to three candidates may compete on one suite and only the best (no regressions, G-R-11) is proposed. Acceptance: pytest that lineage is stored and the best of three competing candidates is the one proposed.

## Quality commands
- `uv run pytest -q`
