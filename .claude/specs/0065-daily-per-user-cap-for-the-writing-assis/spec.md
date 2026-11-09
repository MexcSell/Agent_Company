# Specification: Daily per-user cap for the writing assistant

## Locate the writing-assist call path and specify a per-user daily token cap (architect, done)

# Design: daily per-user token cap for the writing assistant (goal #65, S04-R-09)

## 1. Does `/api/writing/assist` exist? No.

`/api/writing/assist` does **not** exist on the base branch (`agents/goal-65` at 1d08fb2).

- I searched the whole repository (src/, tests/, static/, docs/) for `writing/assist`, `/api/writing`, `writing_assist` and `writing assist`, case-insensitive. The only hits are this goal's own files: `.claude/specs/0065-…/requirements.md:3`, `tasks.md:8,15,22`, and `docs/adr/0079-missing-writing-assist-endpoint.md:10,15,31`.
- `grep -n assist src/agent_company/api.py` returns nothing, so no route name contains "assist".
- No `writing…assist` string appears in `static/*.js`, `tests/*.py` or `docs/requirements/*.md`.
- Every other "writing" hit in `src/` means "writing role/task" (for example `prefs.py:64`, `loop.py:719`), not an assistant.
- `Run` (db.py:763-789) has no user column, so model use cannot be counted per user today.

Signed-in users can still trigger model calls through two existing paths. I list them as findings; the cap does not attach to them in this goal:

| Path | Who can call it | Model call | Recorded? |
|---|---|---|---|
| `POST /api/projects/{id}/command` (api.py:3226), `/summary` → `projects.summarize` (projects.py:700, 802-803) | any signed-in user (`Depends(current_user)`, api.py:3227) | `executors.for_choice(...).run(req)` at projects.py:726 | **No**: `summarize` never calls `usage.record` (the only `usage.record` in projects.py is at :1222, in proposal triage) |
| `POST /api/research/requests` (api.py:3041) → `research.run_report` (api.py:3070) → `router.run` (research.py:1391, research_agent.py:174) | users with `allowed("run", "research_request")` | router.py:575 | Yes, router.py:576, but with no user |

Decision #79 (docs/adr/0079) is still open: build the endpoint, cap something else, or close the goal. So this goal delivers **the helpers, the setting and the tests only, with no new endpoint** (§8).

## 2. Data: `Run.user_id`

- **db.py**, in `class Run`, next to `goal_id` (db.py:767):
  `user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)  # the signed-in user whose request made the run (S04-R-09); NULL for agent work`
- Use a plain Integer, **not a ForeignKey**. `_add_missing_columns` (db.py:1500) compiles only the column type, so a foreign key would exist on new databases but not on migrated ones.
- Migration: `_add_missing_columns` already adds the column (`ALTER TABLE "runs" ADD COLUMN "user_id" INTEGER`) and its index `ix_runs_user_id` through the `table.indexes` loop. No migration code is needed. Existing rows get NULL.

## 3. `usage.record(..., user_id=None)`

- Add the keyword-only parameter `user_id: int | None = None` to `usage.record` (usage.py:29) and pass `user_id=user_id` into `Run(...)`.
- Every existing caller keeps working unchanged and writes NULL.

## 4. `usage.user_tokens_since(user_id, since, purpose=None) -> int`

```python
def user_tokens_since(user_id: int, since: datetime, purpose: str | None = None) -> int:
    q = select(func.coalesce(func.sum(Run.input_tokens + Run.output_tokens), 0)).where(
        Run.user_id == user_id, Run.started_at >= since)
    if purpose is not None:
        q = q.where(Run.purpose == purpose)
    with session() as s:
        return int(s.scalar(q) or 0)
```

- It sums **all** rows, whatever `ok` is, so failed runs count (§7).
- Rows with `user_id IS NULL` (goal-loop and agent runs) never match, which keeps agent work out of a user's count.
- Callers pass `since=usage.day_start()`: UTC midnight, the same boundary `over_limit` uses for model limits (usage.py:13-15, :80).

## 5. Setting `writing_daily_tokens_per_user`

- **prefs.py**: add `SCHEMA["writing_daily_tokens_per_user"] = (int, "Model tokens one user may spend per UTC day through the writing assistant (0 = no limit)")` and `LIMITS["writing_daily_tokens_per_user"] = (0, 1_000_000_000)`, the same as `goal_token_budget`.
- **config.py**: add the field `writing_daily_tokens_per_user: int = 0` to `Settings` (next to config.py:47), and `writing_daily_tokens_per_user=_int("WRITING_DAILY_TOKENS_PER_USER", 0)` in `settings()` (next to :182).
  - This is required: `prefs.describe()` reads `getattr(cur, k)` for every SCHEMA key, so a missing field breaks the whole Settings page.
- Admins change it in Settings. `admin.js` renders `describe()` generically, so no JS change is needed.
- It is not a governance key (`policies.GOVERNANCE_KEYS`); the change stays small.
- Default 0 means no cap.

## 6. `usage.user_cap(user_id, purpose="writing", estimate=0)`: the guard

`executors.Request` has **no `max_tokens`** (grep of executors.py and router.py). Do not add one. The caller passes its request's max_tokens as `estimate`; with no estimate it is 0.

```python
class CapReached(Exception):
    def __init__(self, used: int, cap: int, reset_at: str) -> None:
        super().__init__(f"daily writing cap reached: {used} of {cap} tokens used; resets at {reset_at}")
        self.used, self.cap, self.reset_at = used, cap, reset_at

_user_locks: dict[int, threading.Lock] = {}
_user_locks_guard = threading.Lock()

@contextmanager
def user_cap(user_id: int, purpose: str = "writing", estimate: int = 0) -> Iterator[None]:
    cap = int(settings().writing_daily_tokens_per_user or 0)
    if cap <= 0:              # 0 = unlimited: no lock, no DB read
        yield
        return
    with _user_locks_guard:
        lock = _user_locks.setdefault(user_id, threading.Lock())
    with lock:                # released on exit, also when the body raises
        used = user_tokens_since(user_id, day_start(), purpose)
        if used + max(0, estimate) >= cap:
            raise CapReached(used, cap, summary()["resets"]["daily_at"])
        yield                 # the caller runs the model AND calls usage.record(..., user_id=user_id) here
```

- **Refusal rule:** refuse when `used + estimate >= cap`.
  - With estimate 0, a user exactly at the cap is refused.
  - A user one token under the cap is allowed.
- **Holding the lock:** the caller must call `usage.record(..., user_id=…, purpose=purpose)` inside the `with` block. The lock is held until that row is committed, so a second request from the same user reads the updated sum. Two parallel requests cannot both pass.
- **Scope of the lock:** it covers one process only. The app runs as a single process (`supervise` → one uvicorn worker). A multi-process deployment would need a DB-level reservation; that is out of scope.
- `reset_at` may also be computed as `(day_start() + timedelta(days=1)).isoformat()`. `summary()` gives the same value but reads all models, so either is acceptable. Use the cheaper one if `summary()` turns out to be costly in tests.

## 7. Failed or interrupted calls

- A failed call that reports tokens: the caller records them with `ok=False, error=...`, and they **count** because `user_tokens_since` ignores `ok`.
- A call that reports no tokens (timeout, exception before any output) is recorded with 0/0 or not at all, and counts **0**.
- An exception raised inside the guard still releases the lock (the `with lock` exits). Nothing is counted unless the caller recorded a row first.

## 8. API answer when the cap is reached; where the cap attaches

**When the endpoint exists** (not the case today), the route wraps its model call:

```python
try:
    with usage.user_cap(user["id"], "writing", estimate=body.max_tokens or 0):
        res = ...  # model call
        usage.record(..., user_id=user["id"], purpose="writing")
except usage.CapReached as e:
    _audit(user, "writing_cap_reached", user["id"], after={"used": e.used, "cap": e.cap}, severity="warning")
    return JSONResponse({"error": "daily writing cap reached", "used": e.used, "cap": e.cap,
                         "reset_at": e.reset_at}, status_code=429, headers={"Retry-After": str(seconds_to_reset)})
```

- **HTTP 429.** The body holds the used tokens and `reset_at` from `usage.summary()['resets']['daily_at']`. It never contains the model name, route, key hint or provider error text.
- **Admins are bound too.** The cap protects the shared model budget, and an admin who needs more can raise the setting, which is audited. No exemption is given.

**What the developer does now, since the endpoint does not exist:**
- Implement §2-§7 and the tests in §9.
- Do **not** create `/api/writing/assist`.
- Do not attach the cap to `/summary` or research requests; decision #79 chooses what to cap.
- State this in the answer.

## 9. Tests: `tests/test_user_cap.py`

The tests run offline with their own DATA_DIR, as in `tests/test_budgets.py`. Each test sets the cap with `prefs.set_value("writing_daily_tokens_per_user", N)` and writes runs with `usage.record(model_name="m", kind="llm", input_tokens=…, output_tokens=…, user_id=…, purpose="writing")`.

1. **Under the cap:** cap 100, 40 tokens used. `user_cap(u, estimate=10)` lets the request through; `user_tokens_since(u, day_start(), "writing") == 40`.
2. **At the cap:** cap 100, 100 used. `user_cap(u)` raises `CapReached` with `used == 100`. Also, 95 used plus estimate 5 is refused.
3. **New UTC day resets the count.** `day_start()` reads `now` from usage's own globals (usage.py:10,14), while `Run.started_at` uses `db.now`. So record 100 tokens, then `monkeypatch.setattr(usage, "now", lambda: real + timedelta(days=1))`. The count drops to 0 and `user_cap` passes.
4. **Two concurrent calls, exactly one allowed.** Cap 100, 0 used, two threads started together by a `threading.Barrier(2)`. Inside the guard each records 60 tokens. Assert exactly one `CapReached` and that 60 tokens are stored.
5. **A failed call's tokens count:** record `ok=False, input_tokens=30, output_tokens=20`; the count is 50. A failed call with 0 tokens adds 0. An exception raised inside `user_cap` releases the lock: a second `user_cap` in the same thread does not block (use a timeout).
6. **Setting 0 means no cap:** with 10**9 tokens used, `user_cap` passes.
7. **Users are isolated:** user A's runs and NULL-user runs do not count for user B, and B passes while A is refused.
8. **Migration:** a `runs` table created without `user_id` gets the column and `ix_runs_user_id` from `_add_missing_columns`.

Commands: `uv run pytest -q tests/test_user_cap.py` and `uv run pytest -q tests/test_budgets.py`.

FINDING: src/agent_company/projects.py:726 | `/summary` (any signed-in user, api.py:3226-3227) runs a model and never calls `usage.record`, so its tokens appear in no count, cap or budget.
FINDING: src/agent_company/router.py:576 | research-request runs are recorded without the requesting user, so a future per-user cap cannot count them until `user_id` is passed through.
NEXT: The developer implements §2-§7 and the tests in §9 without creating an endpoint.
NEXT: A person decides #79 (docs/adr/0079): build `/api/writing/assist`, cap `/summary` or research requests instead, or close.
MESSAGE recommendation to developer: add `writing_daily_tokens_per_user` to `config.Settings` and `settings()` as well as to `prefs.SCHEMA`/`LIMITS`, or `prefs.describe()` breaks the Settings page; keep `Run.user_id` a plain indexed Integer without a ForeignKey.
PROPOSAL: Meter `projects.summarize` model runs :: `/summary` is an unrecorded model call any signed-in user can repeat, which is the same exposure this goal describes.
