# Requirements: Proposal goals descended from a benchmark goal must target the platform, not the eval scratch repo

projects.decide() sends a proposal to the platform project only when its origin goal itself has origin 'eval'. A proposal raised by a goal that was itself spawned from an eval goal's proposal (any depth) inherits origin.repo_path, an eval seed repo under data/workspaces/eval-*, with project_id None and the seed's fizzbuzz/stats checks. On 2026-11-08 21 such goals (e.g. #178, #180, #185) planned platform changes inside a scratch repo and could not succeed; the GM repointed the queued ones by hand. Fix: treat a proposal as platform work when any ancestor (projects._root and the chain between) has origin 'eval', or when origin.repo_path lies under the eval workspaces folder; then spawn exactly as the existing eval branch does (platform project, its quality_commands, its path, note in the description). Keep the chain guards. Tests: depth-1 and depth-2 descendants of an eval goal both land on the platform project; an ordinary project chain keeps its repo.

## Quality commands
- `uv run pytest -q`
