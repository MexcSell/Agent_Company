# Review (iteration 1)

## Review mutation testing against the EL-R-08 spec (qa, done)

**Evidence:** I ran `uv run pytest -q tests/test_mutation.py tests/test_evaluation.py` and got `11 passed in 8.11s`. I read the diff `b34a460..HEAD` in full: `src/agent_company/evaluation.py` (+171 lines), `tests/test_mutation.py`, `CLAUDE.md`, `docs/requirements/EVOLUTION-LOOPS.md` and the spec files.

## Logic verification

**`mutation_test`, normal case (an assertion is removed and the check catches it).**
- `rev-parse` turns `base` and `head` into SHAs (`evaluation.py:544-545`).
- A detached worktree is created at `settings().worktrees_dir/mutation-<hex12>` (`:550-553`) and passes `ensure_sandboxed`.
- The baseline `_run_checks` passes, so `ok=True`.
- `git diff --name-only -z --diff-filter=AM base head -- *.py` lists `adult.py` and `test_adult.py`. `TEST_FILE` drops `test_adult.py`.
- `_sites` returns three sites, in this order:
  - `remove_assert` at `adult.py:2`
  - `flip_boundary` at `adult.py:2`
  - `flip_boundary` at `adult.py:3`
- For site 1, `_mutate` replaces line 2 with `    pass` and `ast.parse` accepts the result. The file is written and the checks run through `sandbox.run`.
- `test_negative` now fails because `is_adult(-1)` no longer raises. pytest exits with 1, so `ok=False`, `caught=[CHECK]`, `row["caught"] += 1` and `share = _rate(1, 1)`.
- The `finally` at `:583` restores the file with `git checkout -- adult.py` before the next site.
- The test's `per_check share > 0` assertion and the survivor test (`>= 0` flip, share strictly between 0 and 1) show this works.

This fixes the root cause. The seed puts a production `assert` behind a `pytest.raises` test, so removing it has an effect pytest can see (design §0.1). Assertions in test files are never mutated, so a passing test cannot hide behind a duplicated test assertion.

**Edge cases.**
- **No sites:** with only `notes.txt` changed, the `*.py` diff is empty. Then `sites=0`, `mutations=[]` and `per_check[CHECK]={"mutations":0,"caught":0,"share":None}`, because `_rate` returns `None` when the count is 0 (`test_no_python_sites`).
- **Cap reached:** `sites[:max_mutations]` runs only the first sorted site, `truncated = 3 > 1` and `sites` keeps the full count (`test_cap`).

**Error cases.**
- **The head fails its own check:** the baseline is not `ok`, so `error="baseline: <check> failed"` and the function returns from inside the `try`. The `finally` still removes the worktree. `per_check={}` and `mutations=[]`, as design §6 requires (`test_failing_baseline`).
- **Sandbox start-up failure (infra) during a mutation:** `_run_checks` sets `infra` and `ok=False`. That check goes into neither `caught` nor `ran` (`:585-586`), so the mutation is excluded from the numerator and the denominator. `overall.infra` goes up by one and `survived=False`.
- **Infra on the baseline:** this gives `error "... did not start (infra)"`.
- A mocked `Outcome` sequence (`test_timeout_counts_as_caught_and_infra_is_excluded`) gives `{"mutations":2,"caught":1,"share":0.5}`, which is correct: the timeout counts as caught and the infra run is excluded.
- **An exception during a check run:** the file is restored, then the nested `finally` runs `worktrees.remove`, `rmtree` and `worktree prune` (`test_exception_removes_worktree`).

## Checklist

- **Checks only go through `sandbox.run`:** yes (`:515`). The new code has no `subprocess` call; `git` is used only for worktree and diff plumbing. However, the design (§0.2, §5.2) requires the checks to go through `contracts.run_command`. They do not; see FINDING 1.
- **Scratch worktree under data_dir/worktrees, removed on exception:** yes. `_clean()` checks this in every test, including the exception test.
- **Original repository and branch unchanged:** yes. The `measured` fixture asserts that HEAD and `status --porcelain` are the same before and after.
- **File restored between mutations:** yes, through the `finally` that runs `git checkout --`. The surviving `>= 0` flip at line 2 shows the earlier `remove_assert` on the same line did not carry over.
- **Infra not counted as caught:** yes.
- **Shares computed correctly:** yes. They use `_rate` per check and overall, and `overall` counts a mutation only if at least one check ran.
- **Tests offline and independent of bwrap:** yes. All tests use `mode="auto"`, which runs on the host (L2) when bwrap is missing, and the seed has no lock file, so nothing is installed from the network. Two tests replace `sandbox.run` with a mock.
- **Interfaces:** additive only; the existing functions are unchanged and `test_evaluation.py` still passes.
- **Dependencies:** standard library only.
- **Docs:** `CLAUDE.md` and EVOLUTION-LOOPS (status "implemented") are updated. `docs/threat-model.md` is not updated, although this adds a new path for running commands.
- **Performance:** each mutation runs every check once, and runs are capped at `max_mutations=20`. Fine.

## Findings

FINDING: src/agent_company/evaluation.py:515 | Checks run as `sandbox.run(shlex.split(check), …)` instead of `contracts.run_command(check, path, timeout, mode=mode, info=info)`, as design.md §0.2 and §5.2 require. This has two effects. First, `governance.check_command` and its audit are skipped, so `mutation_test` would run a command the gate's command policy refuses; that breaks the CLAUDE.md rule that commands pass `check_command` and run through `run_command`. Second, a gate command with shell syntax (`uv run pytest -q && uv run ruff check`, pipes, `cd x && …`) is passed as literal argv: pytest receives `&&` as an argument and the baseline fails. Such a check can never be measured, so the tool does not measure "the gate's quality command" as the gate runs it. (The developer task in tasks.md said to use shlex.split; the design overrode that.) | Call `contracts.run_command` in `_run_checks` and fill the result entry from `info` (`isolation`, `exit_code`, `timed_out`, `infra`, `output_tail`). Treat `isolation == ""` (refused) like infra. Add a test with a chained check such as `"<python> -m pytest -q -p no:cacheprovider && true"` and a test with a refused command.

FINDING: src/agent_company/evaluation.py:456, src/agent_company/evaluation.py:489 | `str.splitlines()` also splits on `\x0c`, `\x1c`, `\u2028` and similar characters, but `ast` and `tokenize` count lines only at `\n`, `\r\n` and `\r`. In a file containing a form feed, every site after it is edited on the wrong line: the result is either skipped as an invalid mutation or a different statement is mutated, so the shares come out wrong. | Split with `re.split(r"(?<=\n)", text)`, or `text.split("\n")` with `keepends` handling, in both places.

FINDING: src/agent_company/evaluation.py:553 | If `git worktree add` itself raises (for example, the disk is full or the path is locked), the `finally` calls `worktrees.remove` on a worktree that was never created. That raises a second `WorktreeError`, which replaces the original error the caller would see. | Set a flag once `worktree add` succeeds and call `worktrees.remove` only when it is set. Keep the `rmtree` and `prune`.

FINDING: tests/test_mutation.py:1 | No test covers an unresolvable `base` or `head` (`evaluation.py:546-548`, where an error result is returned without creating a worktree), and no test covers a refused or shell-syntax check. | Add `mutation_test(repo, "nope", checks=[CHECK])`, then assert `"error" in result` and that no `mutation-*` directory exists.

FINDING: docs/threat-model.md | This adds a new path for running commands (agent-written code under mutation, run through the sandbox), but the threat model is not updated, as CLAUDE.md requires for new executors. | Add a row: mutation runs → L3 sandbox, `check_command`, scratch worktree under `worktrees_dir` → tests in `tests/test_mutation.py`.

The core behaviour works and the acceptance test passes. The command path, however, does not follow the design's explicit decision and skips the command policy. That is a security control and a correctness problem for chained gate commands, so this needs a revision before approval.

NEXT: The developer replaces `shlex.split` and `sandbox.run` in `_run_checks` with `contracts.run_command(..., info=info)`, fixes the line splitting, guards the cleanup when `worktree add` fails, and adds tests for a refused check, a chained check and a bad ref.
NEXT: Update `docs/threat-model.md` for the mutation-run path.

MESSAGE request to developer: in src/agent_company/evaluation.py `_run_checks` (line 515), use `contracts.run_command` as design.md §0.2 and §5.2 specify, not shlex.split plus sandbox.run; tasks.md's shlex.split note was overridden by the design.

```json
{"insights":[{"title":"Task text and design can disagree on the command path","description":"tasks.md told the developer to use shlex.split, and design.md §0.2 overrode it with contracts.run_command. The developer followed the task text, which skipped governance.check_command and broke chained gate commands. When a design states a 'change from the task text', the review should check that exact point first.","evidence":["src/agent_company/evaluation.py:515","design.md §0.2"],"expected_benefit":"Catches mismatches between spec and task text early","confidence":0.8}]}
```

PROPOSAL: Run mutation testing at the gate for high-risk goals :: the per-check share would show whether the gate's checks can catch anything, and it could feed teststrategy and RCA.

OUTCOME: REVISION_REQUIRED

VERDICT: FAIL
