# ADR 0104: Which entries the eval seed .gitignore holds

Status: Accepted
Date: 2026-11-08
Decision record: #104 · goal #107 (Add a .gitignore for __pycache__ to eval seed repos)
Category: technical

## Context

Should eval seed repos reuse the broad source-only ignore list (gitsync.SOURCE_IGNORES) or a narrow list of Python caches?

## Options

- Narrow list: __pycache__/, *.pyc, .pytest_cache/
- Reuse gitsync.SOURCE_IGNORES (also .env, .env.*, *.log, node_modules/, .venv/ …)
- No .gitignore; exclude generated files in the gate's checks instead

## Criteria

- Removes the untracked __pycache__ noise the proposal describes
- Keeps protected configuration (.env) and real source changes visible to the scope and boundary checks and the adversarial suite
- Smallest change in the platform's seed creation

## Decision

Narrow list: __pycache__/, *.pyc, .pytest_cache/

## Reasons

The proposal is about Python bytecode and test caches left behind by test runs. SOURCE_IGNORES also ignores `.env`/`.env.*`. checks.PROTECTED_CONFIG and the adversarial traps rely on seeing those files, so ignoring them in eval repos would weaken the security behaviour measurement (S09-R-05). Changing the gate's checks instead would touch the control plane for every goal, not only evals.

Evidence:

- src/agent_company/evaluation.py:121-134 (_seed_repo writes no .gitignore) (FACT)
- src/agent_company/gitsync.py:39 (SOURCE_IGNORES includes .env, .env.*) (FACT)
- src/agent_company/checks.py:177-179 (PROTECTED_CONFIG matches .env) (FACT)
- src/agent_company/worktrees.py:104 (GENERATED already treats these as never-commit) (FACT)
- K751 (an earlier attempt edited the eval scratch repo instead of the platform) (FACT)

## Consequences

Assumptions:

- Eval tasks run Python tests, so bytecode and pytest caches are the generated files that appear
- No eval suite checks a seeded .gitignore byte-for-byte

Limitations:

- Other generated files (.ruff_cache, .mypy_cache, node_modules) are not covered; they can be added later if an eval shows them

Confidence: 0.80 (The code paths are read directly. Whether every eval suite is unaffected is inferred, not run.)
