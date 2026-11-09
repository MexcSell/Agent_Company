# Review (iteration 2)

## Review the sandbox infrastructure classification, including required mode (qa, done)

## Logic verification

I only reviewed iteration 2's change, `9513e56..HEAD`. Iteration 1's core path was already approved.

**`sandbox.run`, required-and-unavailable branch (`src/agent_company/sandbox.py:302-305`)**
- **Normal case (bwrap starts and the command fails):** `available()` returns `(True, "")`, so this branch is skipped. The command runs at L3 and `Outcome.infra` comes from `startup_failure` on the real output. The command's own output is not a start-up line, so `infra` stays empty. `run_command` (`contracts.py:244-250`) reaches `return res.returncode == 0, …`, which gives an ordinary failed check. Iteration 2 doesn't change this path.
- **Edge case (required mode, probe stderr is "bwrap: Can't find source path …"):**
  - `available()` (`sandbox.py:206`) returns `(False, "bwrap failed: bwrap: Can't find source path …")`.
  - `why.removeprefix("bwrap failed: ")` leaves the bare stderr line. `startup_failure(126, line, False)`: rc is not 0, the output is not a timeout, there is one line, and it matches `STARTUP_LINE` and `INFRA_RE`. It returns the line, which becomes `Outcome(126, "", "", note, infra=line)`.
  - In `run_command`, `info.update(..., infra=res.infra)` at `contracts.py:238-241` runs before the `if not res.isolation: return False` at :244, so `info["infra"]` is set. `check()` then copies it into the row (`contracts.py:181`).
  - From there the loop takes the path already approved in iteration 1: skip with `INFRA_SKIP`, one `infrastructure` incident, pause.
  - This fixes the root cause in required mode: the probe failure was the same host fault and used to bypass the classification. It isn't just hiding a symptom.
- **Error case (required mode, bwrap missing for another reason):**
  - "bubblewrap (bwrap) is not installed" doesn't start with `bwrap:` once the prefix is removed. `INFRA_RE` doesn't match, so `infra` is `""`.
  - An OSError gives "bwrap failed: [Errno 13] …", which leaves "[Errno 13] …" after the prefix is removed. That also doesn't match.
  - "sandbox off" never reaches this branch through `run`, but the test covers it anyway.
  - In all of these, `run_command` returns `False, "… not run: the sandbox is required and unavailable (…)"`, which is the earlier normal failure.

**Can it turn a failure into a pass?** No.
- In the required branch, `res.isolation` is `""`, so `run_command` returns `False` at `contracts.py:244` whatever `infra` holds.
- `infra` only adds a field. It never sets `passed`, and `test_required_and_unavailable_start_up_failure_is_infra` checks `rows[0]["passed"] is False`.
- The loop turns infra into a skip and a pause. That isn't a pass: the goal cannot finish or merge while it is paused.

**CLAUDE.md clause:** present in the `sandbox.py` entry: "a bwrap/systemd-run start-up failure (`INFRA_RE`, also in required mode) becomes `Outcome.infra`: the task is skipped with `INFRA_SKIP`, an `infrastructure` incident opens and the goal pauses until a person resumes it".

## Tests
I tried `uv run pytest -q tests/test_sandbox_infra.py` twice. The permission system refused it both times ("This command requires approval"), so I did not run the tests myself.
- The evidence comes from the developer's report on the `required_infra` task: 29 passed in the file, 6 passed with `-k required`, and a full suite of `1038 passed in 422.80s`.
- The report also says tests (a) and (c) fail when the fix is removed (`2 failed, 4 passed`).

I read the three new tests. They cover the three cases above, end to end:
- (a) checks `run_command` and `check()`.
- (b) has four parametrized non-infra reasons.
- (c) runs a `required` goal and checks the status `paused`, `paused_for == "infrastructure"`, `INFRA_SKIP + LINE`, that no task failed, and exactly one incident.

## Checklist
- **Contract satisfied:** yes.
- **Interfaces compatible:** yes. The `Outcome` field `infra` already exists and is passed by keyword.
- **Error handling:** the non-infra reasons keep the old message.
- **Edge cases:** covered. stderr is truncated to 200 characters, but `INFRA_RE` matches at the start of the line, so a long line is still classified.
- **Tests sufficient:** yes. I could not run them myself; see above.
- **Dependencies:** none added.
- **Security:** no widening. Required mode still never runs the command on the host.
- **Performance:** no change.
- **Docs updated:** CLAUDE.md and the `docs/threat-model.md` row.

FINDING: src/agent_company/sandbox.py:196-209, 88 | `available()` stores the probe result for the life of the server process, and nothing in `src/` calls `sandbox.reset()`. If a person fixes the host (for example creates the missing source path) and resumes a required-mode goal without restarting the server, it gets the cached failure and pauses again straight away. This was already the case before this change, so it is not a regression. | Call `sandbox.reset()` when an infra-paused goal is resumed, or when the `infrastructure` incident is resolved, so the probe runs again. Add a test that resumes after `available` starts returning True.

FINDING: tests/test_sandbox_infra.py:1 | I could not run the requested pytest command here because the permission system refused it. The test results come only from the developer's report and the gate's `required_infra` acceptance run. | The gate should run `uv run pytest -q tests/test_sandbox_infra.py` itself, or the command should be allowed for the QA role.

NEXT: Merge after the gate's own run of `tests/test_sandbox_infra.py` confirms 29 passed. Track the probe reset on resume as a follow-up.
MESSAGE recommendation to developer: in a follow-up, call `sandbox.reset()` on resume of a goal paused for `infrastructure` (`src/agent_company/sandbox.py:88`); otherwise a required-mode goal pauses again until the server restarts.

PROPOSAL: Re-probe the sandbox on infra resume :: without it the "pause until a person resumes it" promise needs a server restart in required mode

OUTCOME: APPROVED_WITH_COMMENTS
VERDICT: PASS
