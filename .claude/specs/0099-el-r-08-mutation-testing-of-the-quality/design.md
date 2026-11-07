# EL-R-08: mutation testing of the quality gate

**Requirement:** docs/requirements/EVOLUTION-LOOPS.md EL-R-08. **Module:** `src/agent_company/evaluation.py`. **Tests:** `tests/test_mutation.py`.

## 0. Two changes from the task text

1. **The seeded repository in the task cannot pass its own acceptance test.** Its change puts `assert is_adult(18)` and `assert not is_adult(17)` in a test file. Removing one of them still leaves pytest passing on the other. A removed *test* assertion is never caught by pytest, so the test would fail. Two fixes:
   - Mutation sites come only from non-test `.py` files (§4). Mutating tests only shows whether they repeat each other. It says nothing about how strong the gate is, which is what EL-R-08 measures.
   - The seeded change adds a production assertion that a test depends on (§8). Removing that assertion makes pytest fail, so the mutation is caught.
2. **Checks run through `contracts.run_command`, not `shlex.split` plus `sandbox.run`.** `run_command` is the gate's own path. It calls `ensure_sandboxed` and `governance.check_command`, then `sandbox.run(["/bin/sh", "-c", cmd], cwd, timeout, mode=mode, prepare=True)`, and it fills `info` with `isolation`, `exit_code`, `timed_out` and `infra`. A check string therefore behaves exactly as it does in the gate (`&&` and pipes included), and every check still goes only through `sandbox.run`.

## 1. Public API

```python
MUTATION_KINDS = ("remove_assert", "flip_boundary")
TEST_FILE = ("tests/*", "*/tests/*", "test_*.py", "*/test_*.py", "*_test.py", "conftest.py", "*/conftest.py")

def mutation_test(repo: str | Path, base: str, head: str = "HEAD", checks: list[str] | None = None,
                  kinds: tuple[str, ...] = MUTATION_KINDS, max_mutations: int = 20, timeout: int = 120,
                  mode: str | None = None) -> dict[str, Any]:
```

- `checks` are the gate's command strings, the same strings `quality` passes to `run_command`. If a caller has staged conditions, it strips the stage first with `quality.split_stage(cmd)[1]`.
- An empty `checks` list raises `ValueError`. An unknown kind raises `ValueError`.
- `mode=None` means `sandbox.mode_for()`: with no goal, this is the company setting, made stricter by policy.

Private helpers (names recommended):

- `_sites(root, files, kinds) -> list[dict]`
- `_mutate(text, site) -> str | None`
- `_run_checks(worktree, checks, timeout, mode) -> list[dict]`
- `_share_table(...)`

No API endpoint and no UI. A CLI is optional and out of scope for this iteration.

## 2. Scratch worktree

1. Resolve the head commit in the agent's repository: `sha = worktrees.git(repo, "rev-parse", "--verify", f"{head}^{{commit}}")`. Resolve `base` the same way. If either does not resolve, return an error result (§6) and do not create a worktree.
2. Create the worktree:
   - `path = settings().worktrees_dir / f"mutation-{uuid4().hex[:12]}"`
   - `path.parent.mkdir(parents=True, exist_ok=True)`
   - `worktrees.git(repo, "worktree", "add", "--detach", "-q", str(path), sha)`
   - `worktrees.ensure_sandboxed(path)`
3. Do everything else inside `try:`. In `finally:`:
   - `worktrees.remove(repo, path)`
   - then `shutil.rmtree(path, ignore_errors=True)` if `path` still exists
   - then `worktrees.git(repo, "worktree", "prune", check=False)`

**Safety rule:** the agent's repository is only used for `rev-parse`, `diff` and `worktree add/remove/prune`. Its branch, index, working tree and HEAD are never changed. All edits happen in `path`.

## 3. Precondition: the baseline passes

- Before any mutation, run every check once on the unmutated worktree (`_run_checks`). Each entry is `{"check", "ok", "exit_code", "timed_out", "infra", "level", "tail"}`, where `tail` is the last 300 characters of the output.
- If any check is not `ok`, return the error result and run no mutations. "Not ok" means a non-zero exit, a timeout, `infra` set, or a refusal by the command policy (`isolation == ""`).
- Error message: `"baseline: <check> failed"`. Use `"... did not start (infra)"` when `infra` is set.

## 4. Mutation sites

**Files**

- Command: `git diff --name-only --diff-filter=AM <base_sha> <sha> -- '*.py'`, run in the scratch worktree (it shares the object store).
- Drop files that match `TEST_FILE` (`fnmatch` on the posix path).
- Sort the result.
- Read each file as UTF-8. Skip a file that does not parse with `ast.parse` and list it in `skipped`.

**`remove_assert`**

- A site is an `ast.Assert` node that fills whole lines:
  - Before `col_offset` on its first line there is only whitespace.
  - After `end_col_offset` on its last line there is only whitespace or a `#` comment.
  - Note that ast columns are UTF-8 byte offsets: compare against `line.encode()[:col]`.
  - So `if x: assert y` and `a = 1; assert a` are not sites.
- The mutation keeps every line number:
  - the first line becomes `<same indentation>pass`
  - the remaining lines of a multi-line assert become empty lines
- The site's `line` is `lineno` and its `col` is the indentation width.

**`flip_boundary`**

- `ast.Compare` has no operator positions, so two passes are combined:
  - `ast.walk` collects the `(lineno, col_offset)–(end_lineno, end_col_offset)` span of each `Compare` node whose `ops` include `Lt`, `LtE`, `Gt` or `GtE`.
  - `tokenize.generate_tokens` yields `OP` tokens whose string is `<`, `<=`, `>` or `>=`, with their character positions.
- A token is a site if it lies inside at least one such span. Each token is one site, even inside nested compares.
- The mutation replaces the token's text at its line and column: `<`↔`<=`, `>`↔`>=`. The spans only filter tokens; the edit itself uses the token's character column.

**Validation and order**

- Each mutated text must parse with `ast.parse` and differ from the original. Otherwise the site is listed in `skipped` and not run.
- Sort key: `(path, line, col, kinds.index(kind))`.
- `sites` is the total number of sites found. Only the first `max_mutations` are run, and `truncated = len(all_sites) > max_mutations`.

## 5. Running the mutations

For each selected site, in order:

1. Write the mutated text to `path / site.path`.
2. Run `_run_checks`. Each check runs as `contracts.run_command(check, path, timeout, mode=mode, info=info)`.
   - `sandbox.env_for` already sets `PYTHONDONTWRITEBYTECODE=1`, so a stale `.pyc` from an earlier mutation of the same size and timestamp cannot hide a mutation.
   - After the first run, the prepare step for a lock file is cached per worktree, and only `.py` files change.
3. Score each check from `info`:
   - **Caught:** `infra` is empty and (`exit_code != 0` or `timed_out`).
   - **`infra`:** `info["infra"]` is set. The check goes into the mutation's `infra` list and is excluded from that check's numerator and denominator.
   - **`refused`:** the command policy refused the check (`isolation == ""`). This is treated the same way as `infra`. It cannot happen after a passing baseline unless the policy changes mid-run.
   - **Survived:** anything else.
4. Restore the file with `worktrees.git(path, "checkout", "--", site.path)` in a `finally` around steps 1–3, so an exception never carries one mutation over into the next.

A timeout counts as caught, because the gate fails on a timeout too. It is also flagged as `timed_out` on the mutation, so slow checks stand out.

## 6. Result

```python
{
  "repo": str, "base": base_sha, "head": sha,
  "baseline": [{"check", "ok", "exit_code", "timed_out", "infra", "level", "tail"}, ...],
  "sites": int, "truncated": bool,
  "skipped": [{"path", "line", "kind", "reason"}],
  "mutations": [{"kind", "path", "line", "col", "caught_by": [check, ...], "survived": bool,
                 "timed_out": [check, ...], "infra": [check, ...], "level": "L3" | "L2"}],
  "per_check": {check: {"mutations": n, "caught": k, "share": _rate(k, n)}},
  "overall": {"mutations": N, "caught": K, "share": _rate(K, N), "infra": I},
  "level": "L3" | "L2" | "",
  "error": str,  # only present on an error
}
```

- `survived` is true when no check caught the mutation and at least one check really ran.
- `per_check[...]["mutations"]` counts only the mutations where that check really ran (no infra).
- In `overall`, `N` counts the mutations where at least one check ran, and `K` counts those caught by at least one check.
- `share` uses the existing `_rate`, so it is `None` when the count is 0.
- `level` is `"L3"` only if every run was L3, `"L2"` if any run was on the host, and `""` if nothing ran.
- The error result has the same keys, with `mutations: []`, `per_check: {}`, `overall` counts all 0 and share `None`, and `error` set.

## 7. Edge cases

| Case | Behaviour |
|---|---|
| No `.py` files changed, only test files changed, or no eligible sites | Not an error. `sites: 0`, `mutations: []`, `per_check` has each check with `{"mutations": 0, "caught": 0, "share": None}`, and the baseline is still reported. |
| More than `max_mutations` sites | The first `max_mutations` in the sort order run, `truncated: True`, and `sites` holds the full count. |
| A check times out | `run_command` already kills the process group. The mutation counts as caught and is listed in `timed_out`. On the baseline, a timeout is an error. |
| Sandbox start-up failure (`infra`) on the baseline | Error result. During the mutations, the check is excluded per mutation. |
| `mode="required"` without bwrap | `run_command` returns a refusal with `infra` set, so the result is a baseline error. |
| A file deleted by the change | Excluded by `--diff-filter=AM`. |
| Unparsable file or mutation | Listed in `skipped`, never run. |
| Exception anywhere | The `finally` blocks restore the file and remove the worktree. The exception then propagates. |

## 8. Test plan: `tests/test_mutation.py`

Constraints: offline, using the test fixtures' temporary DATA_DIR. The check is `f"{sys.executable} -m pytest -q -p no:cacheprovider"`, run with `mode="auto"`, so it passes with bwrap (L3) and without it (L2).

**Seed helper `_seed(tmp_path)`**

1. `git init -b main`, commit `README.md` as the base, and record its SHA.
2. Second commit:
   - `adult.py`:
     ```python
     def is_adult(age):
         assert age >= 0
         return age >= 18
     ```
   - `test_adult.py`:
     ```python
     import pytest
     from adult import is_adult

     def test_adult():
         assert is_adult(18)
         assert not is_adult(17)

     def test_negative():
         with pytest.raises(AssertionError):
             is_adult(-1)
     ```
   - Set `user.name` and `user.email` with `git -c`, or use `worktrees.GIT_ENV`.

**Tests**

- **(a) Acceptance.** Run `mutation_test(repo, base, checks=[CHECK], mode="auto")`. Assert:
  - `"error" not in r` and `r["baseline"][0]["ok"]`.
  - The `remove_assert` mutation at `adult.py:2` has `CHECK in caught_by`.
  - `r["per_check"][CHECK]["share"] > 0`.
  - `test_adult.py` produced no sites.
  - `r["level"] in ("L3", "L2")`.
  - The scratch worktree is gone afterwards: no `mutation-*` entry in `settings().worktrees_dir`, and none in `git worktree list --porcelain` of the repository.
  - The repository's HEAD and `git status --porcelain` are unchanged.
- **(b) `flip_boundary`.** The mutation on `>= 18` (line 3) is caught. The one on `>= 0` (line 2) survives, because 0 is not tested. So the share is below 1, which shows a survivor is reported.
- **(c) Error.** A second commit makes `is_adult` return `age > 18`, so the baseline fails. Assert `r["error"]`, `r["mutations"] == []`, `r["per_check"] == {}`, and that the worktree is removed.
- **(d) No sites.** A change that only adds `notes.txt`. Assert `sites == 0`, `mutations == []`, and `per_check[CHECK]["share"] is None`.
- **(e) Cap.** With `max_mutations=1`, assert `truncated is True` and `len(mutations) == 1`, and that the one run is the first site in the sort order (`adult.py:2`, `remove_assert`).

---
FINDING: The task text's seed repository (asserts only in a test file) cannot satisfy "a removed assertion is caught by its pytest command". Removing one test assert leaves pytest passing. This spec moves the assert into `adult.py` and excludes test files from the mutation sites.
FINDING: The developer task says to use `shlex.split` for checks. This spec runs them through `contracts.run_command` (`/bin/sh -c`, the gate's own path), so commands behave as they do in the gate.
MESSAGE recommendation to developer: Use the seed in design.md §8 (the `assert age >= 0` in adult.py plus a `pytest.raises` test), not the one in tasks.md. With asserts only in the test file, test (a) fails by construction.
MESSAGE recommendation to qa: "Checks only go through `sandbox.run`" is met via `contracts.run_command`, which calls `sandbox.run(["/bin/sh","-c",cmd], ...)`. Check that no `subprocess` call runs a check command directly.
NEXT: Implement `evaluation.mutation_test` and `tests/test_mutation.py` as specified, then run `uv run pytest -q tests/test_mutation.py tests/test_evaluation.py`.
PROPOSAL: Run mutation testing at the gate's security/quality stage for high-risk goals :: a gate whose checks catch no mutations of a change is a weak gate, and the per-check share could feed `teststrategy` and RCA.
