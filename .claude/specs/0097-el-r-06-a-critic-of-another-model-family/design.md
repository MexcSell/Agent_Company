# Design: a critic of another model family for each writing task (EL-R-06, EL-R-03)

The scheduler holds a task's dependants back until that task's worker returns (`scheduler.py:146-156`). Its `state` is kept in memory and set from `fut.result()`. A writing task's worker also holds the worktree write lock for the whole of `work()` (`scheduler.py:125-127`). So if the critic loop runs inside `work()`, dependants cannot start before the critic passes or the retries run out, and the retries run under the lock.

## 1. The setting `task_critic`
- `prefs.py:15` SCHEMA: `"task_critic": (bool, "After each writing task, a critic of another model family reviews its diff before dependent tasks start; a FAIL returns the task to its author, at most twice (EL-R-06; costs model calls)")`.
- `config.py:97`: add the field `task_critic: bool = False`. `config.py:213`: add `task_critic=_bool("TASK_CRITIC", False)`. DB overrides reach it through `set_overrides` (`config.py:255`).
- `static/admin.js:164`: list it next to `qualify_recruits` so it appears in the settings page.
- Do not add it to `policies.GOVERNANCE_KEYS` (`policies.py:35`). It is a quality option, not a governance limit.
- `loop.py` reads `settings().task_critic` once per worker call.

## 2. Where it hooks in (`loop.py`, `_make_worker.work`)
- Wrap the run part, lines 984-1014 (prompt → `_execute` → `_checkpoint`), in `for attempt in range(1, CRITIC_RETRIES + 2):`. Add the constant `CRITIC_RETRIES = 2` near `RESUME_WINDOW_MINUTES` (loop.py:1099).
- After each attempt's `_checkpoint`, call `_critic(...)` (section 3) only when all of these hold: `settings().task_critic`, `contract.writes`, `writes`, `res.ok`, a non-empty `changed`, and `choice.kind != "offline"`.
  - An empty diff goes to the existing BLOCKER path (loop.py:1057).
  - A failed run goes on to acceptance and QA as before.
- Everything from loop.py:1015 onward (the WorktreeError incident, `save_answer`, acceptance, `set_status`) runs once, on the last attempt's `res`/`choice`.
- Two details:
  - **`changed`:** `_checkpoint` returns only the files changed since the previous checkpoint (loop.py:878-879). The acceptance step (1043, 1045) must get the union of all attempts. If any attempt returns `None`, the result is `None`. Also take the union of `res.files`.
  - **Diff base:** read `base = (_goal(goal.id).checkpoint or {}).get("commit") or git rev-parse HEAD` once, before attempt 1. The critic reviews the task's whole diff so far, `git diff base..commit`, not only the last attempt.
- Before each retry, call `_budget_note(goal, started)`. If it raises `BudgetExhausted`, stop retrying and keep the current result. The normal checks then decide.

## 3. The critic run: `_critic(goal, contract, task_id, author, author_choice, base, commit, attempt) -> dict`
- **Author's provider:** `author_provider = choice.provider` from the task's own `_execute` (`router.py:98`; claude_code → anthropic, codex → openai).
- **Staffing:** `critic = registry.staff("qa", ["review"], goal.id, writes=False, task_id=task_id)`, released in a `finally` with `registry.release(critic.id, goal.id)`. No critic is recorded as `{"verdict": "skipped", "reason": ...}` and never blocks. That covers:
  - any exception from staffing (`QuotaExceeded`, `RecruitmentPending`, `RuntimeError`);
  - `critic.id == author.id` (as in `research.py:1579-1588`).
- **Request:** `executors.Request(purpose="critic", role="qa", system=_system_for(critic) + " You did not write this change.", prompt=..., workdir=Path(goal.worktree), writes=False)`.
  - A workdir is needed, or CLI kinds are dropped at loop.py:318-319.
  - The prompt holds:
    - the goal title;
    - the contract's title, description and acceptance JSON (as at 983);
    - `git diff --stat base commit`;
    - `git diff base commit`, cut to `CRITIC_DIFF_CHARS = 40_000` with a note `[diff truncated, N more characters]`;
    - the instructions: "List each defect as `FINDING: <file:line> | <risk> | <fix>`. FAIL only for defects that break the task's contract or its acceptance. End with exactly one line `VERDICT: PASS` or `VERDICT: FAIL`."
- **Call:** `_execute(critic, req, "critic", task_id=task_id, goal_id=goal.id, avoid_provider=author_provider, other_cli=True, deadline=_deadline(started))`.
  - Add an `other_cli: bool = False` keyword to `_execute` (loop.py:280) and pass it on at 308.
  - The hard-coded `False` there exists because a code review on another CLI cannot run the tests. A diff critic does not need tests, so `True` lets Codex review Claude's work and the other way round (EL-R-03).
  - The QA review at 1007-1009 keeps the default (`False`).
- **No other family:** if `choice.provider in (author_provider, "offline")`, as at `research.py:1607`, record `verdict: "skipped", reason: "no usable model of another family"` and ignore the text. Never FAIL in this case.
- **Verdict:** apply the regex from `quality.py:68`, `re.findall(r"VERDICT:\s*\**\s*(PASS|FAIL)", text, re.I)`, and take the last match. Keep it as `_CRITIC_VERDICT` in loop.py.
  - Do not use `review_verdict`: it requires a logic-verification section.
  - A run that is not `ok`, or text with no verdict, gives `verdict: "error"` (inconclusive, no retry).
  - `points = contracts.findings(res.text)` (`contracts.py:346`). If there are none, use the last 1500 characters of the text.

## 4. Retry
- On `FAIL` with `attempt <= CRITIC_RETRIES`, rerun the **same `agent` object** (no new staffing).
- **Feedback:** `fb = f"A critic of another model family ({critic_choice.name}) failed your change (attempt {attempt}):\n" + "\n".join(points) + ("\n\n" + feedback if feedback else "")`. It goes into the existing slot "Findings from the last failed attempt:" (loop.py:990) and into `req.data["feedback"]`.
- **Session:** set `req.data["resume"] = res.session_id` so a Claude Code author continues its session.
- **Limits:** at most 3 author runs and 3 critic runs per task. A critic still runs after the 2nd retry so its outcome is recorded.
- **After a 3rd FAIL:** the task keeps attempt 3's result. Acceptance and the gate/QA decide as before (loop.py:1040-1067); the critic does not fail the task. The last critic points are added to `output` as `\n\nCritic (attempt 3, <model>): VERDICT: FAIL\n<points>` so the dependent QA review sees them in its `dep_text`.
- `PASS`, `skipped` or `error` end the loop.

## 5. Recording
- **DB column:** `db.py:756` Task gets `critic: Mapped[list[Any]] = mapped_column(default=list)`, with entries `{attempt, model, provider, verdict, points, reason, run_id}`.
  - `_add_missing_columns` (db.py:1516) emits no DEFAULT for a callable default, so existing rows read `NULL`. Readers use `t.critic or []`.
  - `usage.record` returns nothing. So `run_id` is looked up afterwards: `select(Run.id).where(Run.task_id == task_id, Run.purpose == "critic").order_by(Run.id.desc())`.
  - The list is written with the final `set_status(..., critic=entries)` (`scheduler.py:43` takes `**fields`). The infra and blocker branches pass it too.
- **Event:** `log_event("critic", f"{critic.name} ({choice.name}) on '{contract.title}' attempt {n}: {verdict}", goal_id=, agent_id=critic.id, task_id=task_id, provider=..., points=points[:10])` for each critic run.
- **Cost:** passing `task_id` puts the critic's Runs on the task (purpose `critic`). Their cost then counts in `usage.task_spend(task_id)` at loop.py:1043. This is intended: the critic is part of the task's cost.

## 6. Test plan: `tests/test_task_critic.py`
**Fixture:**
- Built like `tests/test_blocker_pause.py:16-43`: plan `implement` (developer, writes) → `review` (qa, `depends_on=["implement"]`), with `quality.evaluate` and `enqueue` monkeypatched.
- `config.set_overrides({"task_critic": True})` (or `prefs.set_value`).
- Fake `_execute`:
  - `purpose == "task"` records `(title, agent.id, req.data["feedback"])`, writes a file, and returns `Choice(name="claude", kind="claude_code", ...)`.
  - `purpose == "critic"` records `("critic", kw["avoid_provider"])` and returns the next scripted verdict with `Choice(name="gpt", kind="llm", model_id="gpt-5", ...)`.
  - Any other purpose goes to the real `_execute`.

**Tests:**
1. **FAIL then PASS:**
   - The call order is exactly `implement, critic, implement, critic, review`.
   - Both `implement` calls have the same `agent.id`.
   - The 2nd `implement` feedback contains the critic's FINDING text.
   - Every critic call got `avoid_provider == "anthropic"`.
   - `Task.critic` has 2 entries, verdicts `FAIL`, `PASS`, with provider ≠ `anthropic`.
   - `implement` is `done`.
2. **Always FAIL:** exactly 3 implement and 3 critic calls, 3 entries, and `review` still runs after them.
3. **Setting off:** no `critic` call, and `Task.critic == []`.
4. **Critic Choice of the author's family:** the entry is `skipped`, there is no retry, and the dependant runs.

Check: `uv run pytest -q tests/test_task_critic.py tests/test_blocker_pause.py`.

FINDING: loop.py:306-308 | `_execute` always passes `other_cli=False`, so no critic inside the goal loop could be the Codex CLI, which EL-R-03 asks for | add the `other_cli` keyword (section 3); the QA review keeps the default.
FINDING: loop.py:878-879 | `_checkpoint` returns only the files since the previous checkpoint, so a retried task would report only its last attempt's changes to acceptance and limits | take the union of `changed` across attempts (section 2).
NEXT: developer implements sections 1-5 in prefs.py, config.py, admin.js, db.py and loop.py, plus the tests in section 6.
NEXT: QA checks the retry count (3+3), the `avoid_provider` value, and that dependants start only after the critic loop.
MESSAGE recommendation to developer: keep the critic loop inside `work()` under the write lock already held (no new lock), and run acceptance once, on the final attempt.
