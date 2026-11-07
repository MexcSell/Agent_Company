# Review (iteration 1)

## Review the infrastructure classification and goal pause against the spec (qa, failed)

I'm passing this change, with three comments that don't block it. The code does what the spec says for the normal, edge and error cases. Pytest was refused by the permission system in this session, so I couldn't run the tests myself (details under Tests).

## Logic verification

**Normal case: the bwrap `Can't find source path` failure**
- **Detection** (`sandbox.py:316-317`): `_execute` returns `(1, "bwrap: Can't find source path /x: …", False)`. Every line is a start-up line, there are at most 5, and the line matches `INFRA_RE`, so `startup_failure` returns it and it lands in `Outcome.infra`.
- **Prepare step** (`sandbox.py:278-282`): on a start-up failure, `_prepare` returns `(notes, line)` and `run` returns `Outcome(1, "", "L3", notes, infra=line)` at `sandbox.py:312-313`. The command is not started. The digest is not stored, since it is only stored when the exit code is 0.
- **`run_command`** (`contracts.py:241-247`): it records `info["infra"]`. Because the infra branch comes right after the "did not run" branch, the check can never pass. `check()` copies `infra` into each result row (`contracts.py:180-181`), which the loop needs to see it.
- **Worker** (`loop.py:1031-1038`): it finds the first row with `infra`. The task becomes `skipped` / `blocked` with output `INFRA_SKIP + line`, and the function returns `False`.
  - `run_dag` (`scheduler.py:127`) only changes its own in-memory state; it does not overwrite the database status.
  - Tasks that depend on it become `skipped: a dependency failed`, so the developer task is skipped, not failed.
  - Retries happen only at the model level (`loop.py:338+`), so the acceptance check is not retried.
- **After the task graph** (`loop.py:1442-1444`): this check runs after `_halted` and before the quota check. `_skipped_with` returns the line, and `_out_for_infrastructure` then:
  - opens one incident (`infrastructure`, SEV-3, rule `sandbox_startup`);
  - pauses the goal with reason `infrastructure: …`;
  - sets `paused_for` and `paused_why` in the checkpoint;
  - sends an admin notification.
  - It returns before `specs.write_outputs`, `fix_submitted` and `quality.evaluate`. No history entry is added and `evaluated` is not set, so the iteration is not judged.
- **Root cause:** the change fixes the cause (a host fault was read as the agent's failure), not just the symptom. The task is never counted as passed, and the goal stops until a person acts.

**Edge case: no false positives**
- Output like `collected 3 items\nbwrap: Can't find source path …` is not all start-up lines, so it gives `""` and stays a normal failure.
- These also give `""`:
  - an exit code of 0;
  - a timeout;
  - empty output;
  - a `bwrap:` line that is not a start-up phrase;
  - more than 5 lines.
- On the host fallback (L2), nothing is classified, which `test_the_host_path_is_never_classified` covers.
- A known gap the spec accepts: a command whose only output is a forged start-up line before it exits non-zero gets classified as infrastructure. That fails safe: the check never passes, and a person is asked to look.

**Error cases**
- **Benchmark goal:** an `origin == "eval"` goal opens the incident, then ends `failed` with `Stopped: infrastructure: …`, as `_out_of_quota` does.
- **Repeat occurrence:** `open_incident` (`governance.py:386-390`) finds the open incident with the same rule and goal and adds the new evidence to it, capped at 50.
- **Resume:** `resume()` (`loop.py:2080-2096`) checks the budget, clears `paused_for` and `paused_why`, and queues the goal. `run_goal` picks up the same iteration, and `_requeue_unfinished` (`loop.py:1817`) puts the skipped tasks back in the queue. `resume_after_quota` only matches `paused_for == "quota"`, so this pause waits for a person.

## Checklist
- **Contract:** sections §2–§5 and §7 of the spec are implemented exactly. The `CLAUDE.md` clause from §6 is missing (finding 3).
- **Interfaces:**
  - `Outcome.infra` is the last field, so the positional `Outcome(126, "", "", note)` at `sandbox.py:304` and `Outcome(rc, out, "L2", …, timed_out)` at `sandbox.py:306` still work.
  - `_prepare` has one caller, which was updated.
  - `"infrastructure"` fits the `String(32)` category column.
- **Session use:**
  - `open_incident` creates its notification inside its own session (`notify(..., s=s)`).
  - `_out_for_infrastructure` opens and closes its sessions one after another (`_update_goal`, then the checkpoint `with session()`, then `log_event` and `notify` outside it).
  - The worker's infra branch runs with no session open.
- **Error handling:** consistent with the existing quota and recruitment pauses.
- **Fallback consistency:** the `required`-and-unavailable path is not classified (finding 1).
- **Tests:** `tests/test_sandbox_infra.py` covers the classification table, a timeout, `run_command` and `check`, a normal failure, the host path, a failed prepare step, a paused goal, the edge case, a benchmark goal, a repeat occurrence and a resume after the host is fixed. The test names listed in the threat-model row all exist.
- **Dependencies:** none added.
- **Security:** the change can never turn a failure into a pass. Text an agent prints can at worst cause a pause.
- **Performance:** each resume re-runs the agent's model call before the sandbox is reached again; spec §5.4 accepts this.
- **Docs:** the `docs/threat-model.md` row was added; the `CLAUDE.md` clause is missing.

## Tests
- I was not able to run `uv run pytest -q tests/test_sandbox_infra.py`. All three attempts were refused by the permission system ("This command requires approval").
- **Independent evidence:** the `implement` task's acceptance conditions in `tasks.md:17` include `uv run pytest -q tests/test_sandbox_infra.py` and `uv run pytest -q -x`. This review only started because `implement` finished as `done`. `run_dag` (`scheduler.py:116-117`) only starts a task once every task it depends on is `done`, and the worker marks a task `done` only when all its acceptance checks pass (`loop.py:1039`). So the gate ran both commands and they passed. I did not see their output myself.

BLOCKER: In this QA session, running `uv run pytest -q tests/test_sandbox_infra.py` was refused by the permission system, so there is no test run of my own; the evidence is the gate's acceptance run on the `implement` task.

FINDING: src/agent_company/sandbox.py:303-304, contracts.py:245 | In `required` mode with the sandbox unavailable, the result is `Outcome(126, "", "", why)` with no `infra` set. `available()` probes bwrap once per process with the toolchain paths bound (`sandbox.py:204`). If that probe hits a missing path, `why` reads `bwrap failed: bwrap: Can't find source path …`, and every task of an A2A or `sandbox=required` goal fails with "not run: the sandbox is required and unavailable", iteration after iteration. That is the same repeated failure this goal is about, and it stays unclassified (the spec excludes it in §8, and it predates this change). | Set `infra=why` on that `Outcome` when the bwrap text after the `bwrap failed: ` prefix matches `INFRA_RE`, and let `run_command`'s "did not run" branch fill `info["infra"]`, so the same skip, incident and pause apply.
FINDING: src/agent_company/quality.py:170, quality.py:283, quality.py:304, management.py:234, performance.py:228 | The quality gate, milestone checks and performance checks receive `info["infra"]` but ignore it. A goal whose tasks have no command acceptance but which has `quality_commands` still fails and re-plans each iteration when bwrap cannot start (the spec excludes this in §8). | Send these paths through `_out_for_infrastructure` as well (the architect's proposal).
FINDING: CLAUDE.md (sandbox.py entry) | The clause required by spec §6 (`INFRA_RE` → `Outcome.infra` → `INFRA_SKIP`, the incident and the pause) was not added, so later agents won't learn about the new skip prefix from the project instructions. | Add the one-line clause in a follow-up task whose allowed paths include `CLAUDE.md`.

NEXT: Run a follow-up task that classifies the `required`-and-unavailable path (finding 1) and the gate's own commands (finding 2) as infrastructure.
NEXT: Add the §6 clause to `CLAUDE.md`.

MESSAGE fact to general_manager: The change passes review with three comments; the `required`-and-unavailable path (`sandbox.py:303-304`) can still repeat the same failure for A2A goals and is worth a follow-up task.

PROPOSAL: Classify the sandbox-unavailable path and the gate's commands as infrastructure :: the `required`-and-unavailable path (rc 126) and the quality gate's commands can still fail a goal over and over for a host fault.

OUTCOME: APPROVED_WITH_COMMENTS
VERDICT: PASS
