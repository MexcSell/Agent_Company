# Requirements: EL-R-06: a critic of another model family per writing task

See docs/requirements/EVOLUTION-LOOPS.md EL-R-06. New DB setting task_critic (bool, default off). When on, after a writing task finishes, a critic run reviews that task's diff with router.resolve_chain(avoid_provider=<author's provider>) (Codex reviews Claude's work and the other way round, EL-R-03) before dependent tasks start; a VERDICT: FAIL returns the task to the same agent with the critic's points, at most twice. Keep it in loop.py next to the existing review handling; record the critic runs on the task. Acceptance: a pytest with a fake executor where the critic fails the first attempt and passes the second, and the dependent task starts only after the pass.

## Quality commands
- `uv run pytest -q`
