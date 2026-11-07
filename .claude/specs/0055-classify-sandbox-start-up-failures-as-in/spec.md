# Spec: treat sandbox start-up failures as infrastructure and pause the goal

Slug: `0055-classify-sandbox-start-up-failures-as-in` · Kind: bugfix · Requirements: S14-R-03, S07-R-02

## 1. Problem

When bubblewrap cannot start, for example with `bwrap: Can't find source path /x: No such file or directory`, the command the agent wrote never runs. Today `sandbox.run` still returns bwrap's exit code 1 as if the command had failed:

- `contracts.run_command` reports a failed acceptance check.
- The loop marks the spec and developer tasks failed.
- `quality.evaluate` judges the iteration, the next iteration re-plans, and the same thing happens again until `max_iterations` runs out.

This is a fault in the host, not in the work. It should open an incident and pause the goal until a person fixes the host and resumes it.

## 2. Detection (`src/agent_company/sandbox.py`)

### 2.1 Pattern and rule

Add these next to `SECRETISH` (sandbox.py:49):

```python
INFRA_RE = re.compile(
    r"^(bwrap: (Can't find source path|No permissions to create new namespace|setting up uid map"
    r"|Creating new namespace failed|execvp)"
    r"|Failed to (start transient scope unit|create bus connection|connect to bus))")
STARTUP_LINE = re.compile(r"^(bwrap: |Failed to (start transient scope unit|create bus connection|connect to bus))")


def startup_failure(rc: int, output: str, timed_out: bool) -> str:
    """The matched start-up line when bwrap (or the systemd-run scope around it) could not start the command, else "".
    Only output made solely of start-up lines counts: a command that started and printed anything is its own failure."""
    if rc == 0 or timed_out:
        return ""
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    if not lines or len(lines) > 5 or not all(STARTUP_LINE.match(ln) for ln in lines):
        return ""
    return next((ln[:300] for ln in lines if INFRA_RE.match(ln)), "")
```

Why command output cannot be misread as a start-up failure:

- `_execute` (sandbox.py:229) merges stderr into stdout.
- bwrap and systemd-run fail before they `exec` the child, so a real start-up failure produces only their own lines. bwrap's die path exits 1; the rule accepts any non-zero exit.
- `startup_failure` returns a line only when **every** non-blank line (at most 5) is a start-up line and at least one matches `INFRA_RE`.
- If the command started and printed anything, such as `collected 3 items` followed later by `bwrap: Can't find source path`, the first line is not a start-up line. It stays a normal failure.
- A timeout (`rc == -9`, `timed_out`) and `rc == 0` are never infrastructure.
- systemd-run's scope error has no `systemd-run:` prefix. It reads `Failed to start transient scope unit: …`, so the pattern matches that text directly.

Residual risk, accepted: a command whose only output is such a line before it exits non-zero, for example one that runs a nested failing bwrap, is classified as infrastructure. This fails safe. The task is never counted as passed; the goal pauses and a person is asked to look.

### 2.2 `Outcome` (sandbox.py:59-64)

Add `infra: str = ""` as the **last** field. It holds the matched start-up line and is empty otherwise. It must be last because `Outcome` is built positionally, for example `Outcome(126, "", "", note)` at sandbox.py:280.

### 2.3 `run` (sandbox.py:268-292)

- Only the L3 path is classified. On the L2 host path (sandbox.py:281-283) and the "required and unavailable" path, `infra` stays `""`. On the host, a `bwrap:` line can only come from the command itself.
- L3 path: `Outcome(rc, out, "L3", notes, timed_out, infra=startup_failure(rc, out, timed_out))`.

### 2.4 `_prepare` (sandbox.py:244-265)

- The return type changes from `str` to `tuple[str, str]`, meaning `(notes, infra)`.
- For each lock-file step, compute `startup_failure(rc, out, timed_out)`.
- On the first match, add the note `prepare `{command}`: sandbox did not start: {line}` and return `(notes, line)` at once, without trying more lock files.
- The digest is not stored on failure. That is already the case, because it is only stored when `rc == 0`.
- In `run`, if prepare returns `infra`, return `Outcome(1, "", "L3", "; ".join(notes), infra=infra)` and do **not** start the command. The same bwrap would fail again.

`_prepare` has only one caller, sandbox.py:286.

## 3. `src/agent_company/contracts.py`

**`run_command` (contracts.py:213-246):**
- Add `infra=res.infra` to the `info.update(...)` call.
- After the `not res.isolation` branch, when `res.infra` is set, return:
  `False, f"`{cmd}` infrastructure: the sandbox did not start{level}: {res.infra}{note}"`
- It stays `False`. An infrastructure failure never passes a check.

**`check` (contracts.py:170-181):** the loop only sees the rows that `check()` builds, so the row must carry the field:

```python
| ({"isolation": info["isolation"]} if info.get("isolation") else {})
| ({"infra": info["infra"]} if info.get("infra") else {})
```

## 4. `src/agent_company/governance.py`

Add `"infrastructure"` to `INCIDENT_CATEGORIES` (governance.py:352).

- `Incident.category` is `String(32)` (db.py:1423), so the value fits. No migration and no new table are needed.
- `security.js` reads the categories from the API, so it needs no change.

`open_incident` (governance.py:366) already merges into the open incident with the same rule and goal (`new: False`, evidence appended, capped at 50). It sends the "Security incident SEV-3" notification only for a **new** incident. That title is cosmetic and accepted.

## 5. `src/agent_company/loop.py`

### 5.1 Constant

Add this next to `QUOTA_SKIP` and `RECRUIT_SKIP` (loop.py:1648-1652):

```python
INFRA_SKIP = "skipped (infrastructure): "
```

### 5.2 Worker (`_make_worker.work`, right after `acceptance = contracts.check(...)` at loop.py:1027-1030)

```python
acceptance += contracts.limits(...)          # unchanged
infra = next((c["infra"] for c in acceptance if c.get("infra")), "")
if infra:  # the sandbox did not start: not this task's failure, no attempt (S14-R-03)
    set_status(task_id, "skipped", stage="blocked", output=f"{INFRA_SKIP}{infra}", model_name=choice.name,
               gate={"acceptance": acceptance, "files": changed or res.files, "commit": commit,
                     "saved_answer": saved}, session_id=res.session_id)
    log_event("task", f"'{contract.title}' skipped: the sandbox did not start ({infra})", goal_id=goal.id,
              agent_id=agent.id)
    return False
```

- Only the line is stored in `output`, so `_skipped_with` returns exactly that line.
- The agent's work is kept in the checkpoint commit and in the `save_answer` file. The answer text itself is not kept, because the task runs again after resume.
- No blackboard entry, proposals, insights or project post are written for a skipped task.
- `return False` makes `run_dag` mark the task's dependents `skipped: a dependency failed`, as quota skips do today.
- The task is not retried in the DAG and its status is not `failed`, so it adds nothing to failure streaks or escalation.

### 5.3 Post-DAG check (loop.py:1431-1445)

Add this right after `if halted: return {"status": halted}` and **before** the quota check:

```python
infra = _skipped_with(goal_id, iteration, INFRA_SKIP)
if infra:  # the sandbox did not start: the iteration is incomplete, do not judge it
    return _out_for_infrastructure(goal_id, infra, report)
```

It goes before quota on purpose. A quota pause resumes by itself (`resume_after_quota`) and would only run into the same broken sandbox. The infrastructure pause needs a person first.

### 5.4 New `_out_for_infrastructure(goal_id, why, report)`

Place it next to `_out_of_quota` (loop.py:1719):

```python
def _out_for_infrastructure(goal_id: int, why: str, report: dict[str, Any]) -> dict[str, Any]:
    """The L3 sandbox did not start (`sandbox.INFRA_RE`): one incident per goal (S07-R-02 evidence), the goal pauses
    until a person fixes the host and resumes it. Benchmark runs end instead."""
    from agent_company.governance import notify, open_incident

    g = _goal(goal_id)
    open_incident("infrastructure", "SEV-3", f"The sandbox could not start for goal #{goal_id}",
                  "rule:sandbox_startup", rule="sandbox_startup", goal_id=goal_id,
                  evidence=[{"type": "sandbox", "iteration": g.iteration, "line": why[:500]}])
    if g.origin == "eval":
        _update_goal(goal_id, status="failed", result=f"Stopped: infrastructure: {why}.")
        return {"status": "failed", "iterations": g.iteration, "report": report, "infrastructure": why}
    _update_goal(goal_id, status="paused", reason=f"infrastructure: {why}"[:500])
    with session() as s:
        row = s.get(Goal, goal_id)
        row.checkpoint = {**(row.checkpoint or {}), "paused_for": "infrastructure", "paused_why": why}
    log_event("goal", f"paused: the sandbox did not start: {why}", goal_id=goal_id)
    notify(f"Goal #{goal_id} paused: the sandbox could not start"[:256],
           f"{g.title}\n{why}\nFix the host (bubblewrap, user namespaces, the bound paths, systemd-run) and resume "
           "the goal.", level="warning", source="goal", goal_id=goal_id, project_id=g.project_id, admin_only=True)
    return {"status": "paused", "iterations": g.iteration, "report": report, "infrastructure": why}
```

- It never calls `quality.evaluate`, `specs.write_outputs`, `fix_submitted` or `track_defects`.
- It adds no `history` entry and never sets `checkpoint["evaluated"]`. The iteration is not counted.
- Eval-origin goals open the incident, then end as failed, matching `_out_of_quota`.
- The goal-level notification is sent every time, because `open_incident` does not notify when it merges into an existing incident.
- The model cost of the task's run is still recorded in `usage`. "No attempt" means no judged iteration and no failed task, not zero spend.

### 5.5 `resume()` (loop.py:~2035): unchanged

- Because `paused_for` is set, `resume()` checks the budget and clears `paused_for`, `paused_why` and `resume_after`.
- `run_goal` sees `checkpoint.iteration == goal.iteration` and `planned` without `evaluated`, so it resumes the same iteration.
- `_requeue_unfinished` (loop.py:1776) puts the `skipped` tasks back in the queue.
- `resume_after_quota` only matches `paused_for == "quota"`, so an infrastructure pause waits for a person.

## 6. Files and functions to change

| File:line | Change |
|---|---|
| sandbox.py:49 | add `INFRA_RE`, `STARTUP_LINE`, `startup_failure()` |
| sandbox.py:59-64 | `Outcome.infra: str = ""` (last field) |
| sandbox.py:244-265 `_prepare` | return `(notes, infra)`; stop at the first start-up failure |
| sandbox.py:268-292 `run` | set `infra` on L3; return early when prepare reports infra |
| contracts.py:170-181 `check` | copy `infra` into the result row |
| contracts.py:213-246 `run_command` | `info["infra"]`; infrastructure message |
| governance.py:352 | add `"infrastructure"` to `INCIDENT_CATEGORIES` |
| loop.py:1648-1652 | `INFRA_SKIP` |
| loop.py:~1027 (`work`) | skip with `INFRA_SKIP` |
| loop.py:~1433 (post-DAG) | `_skipped_with(..., INFRA_SKIP)` → `_out_for_infrastructure` |
| loop.py:~1719 | new `_out_for_infrastructure` |
| CLAUDE.md, the sandbox.py line | one clause: "a bwrap/systemd-run start-up failure (`INFRA_RE`) is `Outcome.infra`; the loop skips the task (`INFRA_SKIP`), opens an `infrastructure` incident and pauses the goal" |

## 7. Tests: new `tests/test_sandbox_infra.py`

### Fixtures

- `fake(out, rc=1)`: monkeypatch `sandbox._execute` with `lambda cmd, cwd, env, timeout: (rc, out, False)`.
- `sandbox.available` → `(True, "")`.
- `sandbox.limits_available` → `False`.
- `loop.enqueue` → no-op.
- `monkeypatch.setattr(quality, "evaluate", boom)`, where `boom` raises `AssertionError`.
- `monkeypatch.setattr(loop, "_plan", lambda goal, it, fb, notes: (plan, None))` with this plan:
  - `TaskContract(key="spec", role="architect", acceptance=[Condition(type="command", run="true")])`
  - `TaskContract(key="implement", role="developer", writes=True, depends_on=["spec"])`
- Goals are created with `quality_commands=[]`.

### Unit tests (no loop)

1. `startup_failure` table, parametrized:
   - `(1, "bwrap: Can't find source path /x: No such file or directory\n")` → that line.
   - Each other `INFRA_RE` phrase → its line.
   - `"Failed to start transient scope unit: Access denied"` → that line.
   - `(0, <bwrap line>)` → `""`.
   - Timed out → `""`.
   - `"collected 3 items\nbwrap: Can't find source path /x"` → `""`.
   - `"bwrap: some other message"` only → `""`.
2. `run_command` with the fake output: `ok` is False, `info["infra"]` is the line, the message contains `infrastructure`, and `contracts.check` rows carry `infra`.
3. Prepare: `monkeypatch.setattr(sandbox, "PREPARE", {"deps.lock": ("sh", "true", "deps")})` as in tests/test_sandbox.py:110, plus a `deps.lock` file. The fake fails every call with the bwrap line. `sandbox.run(..., prepare=True)` gives an `Outcome` with `infra` set, and `_execute` was called once (the command did not start).

### Normal case

`run_goal` gives `status == "paused"`, and:

- `checkpoint["paused_for"] == "infrastructure"` and `paused_why` is the line.
- `goal.reason` starts with `infrastructure: `.
- `not g.history`, and `quality.evaluate` was never called.
- The spec task is `skipped` with output starting with `loop.INFRA_SKIP`; no task is `failed`.
- Exactly one `Incident` with `rule == "sandbox_startup"`, `category == "infrastructure"` and `severity == "SEV-3"`, whose evidence contains the line.
- There is an admin notification titled "Goal #… paused: the sandbox could not start".

### Edge case

The fake returns `(1, "collected 3 items\nbwrap: Can't find source path /x\n", False)` and `quality.evaluate` is not patched. Then:

- The spec task is `failed` and its output does not start with `INFRA_SKIP`.
- `paused_for` is not `"infrastructure"`.
- There is no `sandbox_startup` incident.

### Error cases

- An `origin="eval"` goal gives `status == "failed"`, and its result contains `infrastructure`.
- For a normal goal: pause, then `loop.resume(gid, "user:admin")`, then `run_goal` again with the same fake. It pauses again, there is still **one** `sandbox_startup` incident, and its `evidence` list is longer than after the first run.
- After a resume with `_execute` restored to a passing fake `(0, "", False)`, the requeued spec task finishes `done` in the same iteration (`iterations == 1`).

Run with `uv run pytest -q tests/test_sandbox_infra.py tests/test_sandbox.py tests/test_verification.py tests/test_execution.py`, serially, and read the summary line.

## 8. Out of scope

- The gate's own `run_command` calls (quality.py:170/283/304, management.py:234, performance.py:228, exercises.py:123) receive `info["infra"]` but do not act on it.
- The "required and unavailable" path (`isolation == ""`) is not classified.

No new tables or settings are added.

FINDING: `contracts.check` (contracts.py:177-180) copies only `isolation` from `info`, so a field that `run_command` adds never reaches the loop unless `check` copies it too. The spec does this.
FINDING: tasks marked `skipped` are queued again by `_requeue_unfinished` on resume, so `resume()` needs no change for the new pause reason.
NEXT: the developer implements §2-§6 and the tests in §7. QA then runs the four test files and checks the edge case by hand.
MESSAGE handoff to developer: put `Outcome.infra` last (it is built positionally), and make `contracts.check` copy `infra` into its rows. Otherwise the loop's skip in §5.2 never sees it.
PROPOSAL: Infrastructure classification for gate and milestone commands :: quality.evaluate, milestone and performance checks hitting a bwrap start-up failure still fail the iteration; they should pause the goal through the same `_out_for_infrastructure` path.
PROPOSAL: Use bwrap `--json-status-fd` to prove the child started :: a `child-pid` report from bwrap would separate start-up failures from command output without any text pattern.
