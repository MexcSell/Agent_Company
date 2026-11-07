# Tasks (iteration 1)



## 1. Specify mutation testing of the quality gate (EL-R-08) in evaluation.py
- key: `spec` · role: architect · writes: no

Write a short technical specification for EL-R-08 (docs/requirements/EVOLUTION-LOOPS.md). Read src/agent_company/evaluation.py, sandbox.py (run(argv, cwd, timeout, *, mode, writable, env) -> Outcome with rc/out/level/note/timed_out/infra), worktrees.py (ensure_sandboxed, remove), contracts.run_command and quality.py so the design uses the existing pieces. Specify:
(1) The public API in evaluation.py, for example `mutation_test(repo, base, head='HEAD', checks=[...], kinds=('remove_assert','flip_boundary'), max_mutations=20, timeout=120, mode=None) -> dict`.
(2) The scratch worktree: a detached `git worktree add` of `head` under settings().data_dir/worktrees/mutation-<uuid>. It must pass worktrees.ensure_sandboxed and be removed in a finally block. The repository the agent changed is never touched.
(3) The precondition: every check passes on the unmutated head. If one does not, return an error result and run no mutations.
(4) Mutation sites, taken only from .py files the change touched (git diff --name-only base..head). Find them with ast and keep them deterministic, sorted by path and line. remove_assert replaces an `assert` statement with `pass` at the same indentation. flip_boundary swaps `<`↔`<=` and `>`↔`>=` in one ast.Compare, edited by column offset.
(5) Each mutation is applied, all checks run through sandbox.run with the effective mode (sandbox.mode_for or settings), and the file is restored with `git checkout -- <file>` before the next one. A check that exits non-zero or times out catches the mutation. An Outcome with infra set is reported as `infra` and is not counted as caught.
(6) The result shape: {'baseline': [...], 'mutations': [{'kind','path','line','caught_by':[check...],'level'}], 'per_check': {check: {'mutations': n, 'caught': k, 'share': k/n or None}}, 'overall': {...}, 'level': 'L3'|'L2'}.
(7) Edge cases: no sites, max_mutations reached, timeouts.
(8) The test plan for tests/test_mutation.py. The test seeds a tiny git repository in tmp: a base commit, plus a change that adds `def is_adult(age): return age >= 18` and a test file with `assert is_adult(18)` and `assert not is_adult(17)`. The check is `<sys.executable> -m pytest -q -p no:cacheprovider`. The test asserts that the remove_assert mutation is caught by that check (share > 0) and that the scratch worktree is gone afterwards. The test also covers the error case where the head fails its check and no mutations are run. It must pass whether or not bwrap is available (mode 'auto' falls back to L2) and must run offline.
Do not specify any API or UI. A CLI entry point is optional. Give a recommendation for each choice, not a survey.

Acceptance: {"type": "file_exists", "path": ".claude/specs/0099-el-r-08-mutation-testing-of-the-quality/design.md"}; {"type": "contains", "value": "remove_assert"}; {"type": "contains", "value": "flip_boundary"}; {"type": "contains", "value": "per_check"}; {"type": "min_length", "value": 1500}

## 2. Implement evaluation.mutation_test with its offline pytest
- key: `implement` · role: developer · writes: yes · after: spec

Implement the specification in .claude/specs/0099-el-r-08-mutation-testing-of-the-quality/design.md. Read evaluation.py, sandbox.py and worktrees.py before changing anything.
- In src/agent_company/evaluation.py, add the mutation-testing function and its helpers: find sites with ast, apply mutations with text edits by line and column, make the scratch worktree under data_dir/worktrees and always remove it in a finally block, run checks through sandbox.run, restore with git checkout between mutations, and aggregate the per_check shares.
- Run every check command through sandbox.run, never through a bare subprocess with the server's environment. Use shlex.split for string checks. Use git only for the worktree and diff plumbing.
- Add tests/test_mutation.py, which must run offline with the temp DATA_DIR the test fixtures provide. It must include: (a) the acceptance test, in which a tiny seeded git repository whose change adds is_adult() plus asserts on 18 and 17 has its removed assertion caught by its pytest command (`sys.executable -m pytest -q -p no:cacheprovider`), with per_check share > 0 and the worktree removed afterwards; (b) a flip_boundary mutation on `>=` that is caught; (c) the error case, a head that fails its check, which returns an error with no mutations run; (d) a change with no .py sites, which returns zero mutations and share None. The tests must pass whether or not bwrap is usable (pass mode='auto').
- Add one line about EL-R-08 to the evaluation.py entry in CLAUDE.md, and mark EL-R-08's status in docs/requirements/EVOLUTION-LOOPS.md as implemented.
- Do not add or edit files under .claude/specs/ and do not leave a .pytest_cache behind (K474, K323).
- Run the syntax check `python3 -I /home/luca/Agent_Company/src/agent_company/edit_check.py src/agent_company/evaluation.py tests/test_mutation.py`, then run both pytest files below, and report their summary lines.

Acceptance: {"type": "file_exists", "path": "tests/test_mutation.py"}; {"type": "command", "run": "uv run pytest -q tests/test_mutation.py", "requirement_keys": ["EL-R-08"], "stage": "unit"}; {"type": "command", "run": "uv run pytest -q tests/test_evaluation.py", "stage": "integration"}

## 3. Review mutation testing against the EL-R-08 spec
- key: `qa_review` · role: qa · writes: no · after: implement

Review the implementation in src/agent_company/evaluation.py and tests/test_mutation.py against .claude/specs/0099-el-r-08-mutation-testing-of-the-quality/design.md and EL-R-08. Run `uv run pytest -q tests/test_mutation.py tests/test_evaluation.py` and quote the summary line as evidence. Trace the data flow of three cases: the normal case (an assertion is removed, the check fails, the mutation counts as caught in per_check), an edge case (no mutation sites, or max_mutations reached), and an error case (the head fails its check before any mutation, or the sandbox infra fails). Also check these points:
- check commands go only through sandbox.run;
- the scratch worktree lives under data_dir/worktrees and is removed even when an exception is raised;
- the original repository and its branch are unchanged;
- the file is restored between mutations, so the mutations do not accumulate;
- an infra outcome is not counted as caught;
- the shares are computed correctly;
- the tests run offline and do not depend on bwrap.
List each problem as a FINDING line with file:line, end with an OUTCOME line, and then give "VERDICT: PASS" or "VERDICT: FAIL".

Acceptance: {"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}; {"type": "contains", "value": "OUTCOME"}
