# Review (iteration 1)

## Review the per-task critic against EL-R-06 (qa, done)

I found no blocking defects; the verdict is PASS (approved with comments). I ran `uv run pytest -q tests/test_task_critic.py tests/test_loop.py tests/test_model_limits.py` myself: **20 passed in 8.73s**.

## Logic verification

**How the dependant is held back.** The critic loop runs inside `work()` in `_make_worker` (`loop.py:1014-1048`), before `set_status(... "done")` at `loop.py:1101`. The scheduler starts a task's dependants only after that task's worker returns, and holds the write lock for the whole of `work()`. So the dependant waits for the whole critic loop, and the retries run under the lock. This is the actual mechanism, not a workaround.

**Normal case (setting on, critic FAILs then PASSes)**
1. `critic_on` is true because the setting is on and the task writes.
2. `_critic_base` takes the last checkpoint commit (or HEAD) under the write lock, before the author runs.
3. Attempt 1: the author runs and `_checkpoint` commits; `changed` is set to `["feature.py"]`.
4. `_critic(...)` staffs a QA agent and diffs base against the new commit. It calls `_execute(..., avoid_provider=author_choice.provider, other_cli=True)`.
   - In `router.resolve_chain` (`router.py:527-540`), another family moves to the front of the chain: first a model of another family already in the chain, otherwise one from the catalog, now including CLIs (Codex).
5. The verdict is FAIL. Its points come from `contracts.findings`, are built into `fb`, and go into both `req.prompt` (via `compose`) and `req.data["feedback"]`. `resume` is set to the author's session.
6. The same `agent` object runs attempt 2. The critic now reviews base against the new commit, which covers both attempts. The verdict is PASS, so the loop ends.
7. `set_status(..., critic=critics)` stores both entries, then the worker returns and the dependant starts.

The test asserts the order `implement, critic, implement, critic, review`, the same agent id, the point in both feedback and prompt, `avoid_provider == "anthropic"`, `other_cli is True` and two stored entries. **Correct.**

**Edge case (critic always FAILs)**
- `range(1, CRITIC_RETRIES + 2)` gives attempts 1, 2 and 3.
- At attempt 3, `attempt > CRITIC_RETRIES` appends the last points to `output` and breaks.
- Result: exactly 3 writing runs and 3 critic runs, and the loop is bounded. The test asserts the 3×(implement, critic) order, three FAILs, and the points in `t.output`.
- I traced a retry where the agent changes nothing. `_checkpoint` then returns HEAD with `step=[]`, the union keeps the earlier files, and the critic reviews the same diff again. Still bounded. **Correct.**

**Error case (no other family, or the critic errors)**
- **No QA agent can be staffed** (also `ModelLimitReached`): `_critic` catches it and records `skipped` with a reason. Not tested.
- **The critic is the author:** recorded as `skipped`.
- **The critic's model is the author's family or offline:** recorded as `skipped`, reason "no usable model of another family". Tested.
- **The critic run fails (`res.ok` False) or gives no verdict:** recorded as `error` with the reason. Not tested.
- **A `WorktreeError` on the diff:** the reason is recorded and the verdict stays `skipped`.
- Only `FAIL` sends the task back, so none of these block it. `registry.release` runs in `finally`.

**Is the root cause fixed?** Yes. The critic gates dependants by running inside the worker, which is the real dependency mechanism. Nothing is bolted on after the task is marked done.

**Setting off:** `critic_on` is False, so the loop runs exactly once and breaks at the first check. `changed`/`files` match the old `changed`/`res.files`, and `critics=[]` is stored. The test confirms the order `implement, review` and `critic == []`.

The run-id lookup (`loop.py:1200`) opens a read-only `session()` with no nested writer. `log_event` and `set_status` run outside it, so no second DB connection is opened inside a session.

## Checklist
- **Contract:** met.
  - Setting `task_critic`, default off: `prefs.py` SCHEMA, `config.py:98` default False, plus the `TASK_CRITIC` environment variable.
  - The critic runs before the dependant and avoids the author's provider.
  - FAIL goes back to the same agent at most twice.
  - Runs are recorded on `Task.critic`.
- **Migration:** `Task.critic` is `list[Any]`, which maps to JSON (`db.py:41`). `_add_missing_columns` adds the column, and the computed-default backfill (`db.py:1528-1538`) sets existing rows to `[]`. So the comment's claim that old rows read as NULL (`db.py:760`) is outdated but harmless.
- **Interfaces:** `_execute` gains `other_cli=False`, so existing callers behave as before (the QA review stays on API models).
- **Settings page:** the developer's admin.js finding does not apply. The settings page renders every key from `/api/settings` (`admin.js:77`), which is built from `prefs.SCHEMA`, so `task_critic` already shows there.
- **Security:** the critic runs read-only (`writes=False`), and the diff it sees is capped at 40,000 characters.
- **Performance:** at most 3 extra runs per writing task, and only when the setting is on.
- **Docs:** EVOLUTION-LOOPS.md and CLAUDE.md are updated.
- **Tests:** they cover FAIL→PASS, three FAILs, the setting off, and a critic of the author's family. The critic-error and no-critic paths are not tested.

FINDING: src/agent_company/loop.py:1042 | When the goal's budget runs out after a critic FAIL, the loop breaks without appending the critic's points to `output`, unlike the attempt-3 path at :1037. The QA review then cannot see an open FAIL; it is visible only in `Task.critic` and in events. | Append the same "Critic (attempt N, model): VERDICT: FAIL" text before `break` in the `BudgetExhausted` branch.
FINDING: tests/test_task_critic.py:105 | The error case from the brief is untested: a critic run with `res.ok=False` or no verdict (verdict `error`), and a `registry.staff` failure (verdict `skipped`). A regression that turned an error into a FAIL, and so an extra retry, would not be caught. | Add a test where the fake critic returns `Result(text="", ok=False, error="boom")` and assert one critic entry with verdict `error`, no second implement run, and the review still runs.
FINDING: src/agent_company/loop.py:1189 | The critic's runs are recorded under the author's `task_id`, so `usage.task_spend(task_id)` (used by `contracts.limits` at :1077) counts critic cost against the author's task budget. With a tight budget, a task can fail its limits because of the critic. | Either document that critic cost belongs to the task, or leave the critic runs out of the author's budget check (for example by filtering `purpose != "critic"` in the spend that `contracts.limits` uses).
FINDING: src/agent_company/db.py:760 | The comment says rows from before the column stay NULL, but the `_add_missing_columns` backfill sets them to `[]`, which misleads readers. | Reword it to "existing rows are backfilled with [] by _add_missing_columns".

OUTCOME: APPROVED_WITH_COMMENTS

MESSAGE recommendation to developer: in a follow-up, append the critic's last FAIL points to the output in the BudgetExhausted branch (loop.py:1042) and add a critic-error test to tests/test_task_critic.py; the admin.js item can be dropped, because the settings page lists `prefs.SCHEMA` keys automatically.
NEXT: Add the critic-error test and the budget-break points fix. Decide whether critic cost counts toward the author task's budget.

VERDICT: PASS
