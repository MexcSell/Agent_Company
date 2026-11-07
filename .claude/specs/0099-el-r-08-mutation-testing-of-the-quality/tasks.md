# Tasks (iteration 2)



## 1. Fix mutation_test review findings: run_command, line splitting, worktree cleanup, tests, threat model
- key: `fix_mutation` · role: developer · writes: yes

Read src/agent_company/evaluation.py (mutation_test, _mutate, _run_checks, the site finder near lines 450-610), contracts.run_command (contracts.py:269), tests/test_mutation.py and .claude/specs/0099-el-r-08-mutation-testing-of-the-quality/design.md §0.2 and §5.2 first. Then fix each item below.
1. _run_checks (evaluation.py:512-522): replace `sandbox.run(shlex.split(check), ...)` with `info = {}; ok, out = contracts.run_command(check, root, timeout, mode=mode, info=info)`. Fill each result entry from info: isolation, exit_code, timed_out, infra, output_tail (and duration if present). A refused command (`info.get('isolation') == ''`, or run_command reports a refusal) is handled like infra: it is excluded from the caught/total counts and reported with a reason. A failing baseline still returns the existing error result. Keep the empty-check validation, but do not split commands into argv. Remove `import shlex` if nothing uses it anymore.
2. Line splitting: in _mutate (line ~456) and in the site finder (line ~489), stop using str.splitlines. Split only on \n, \r\n and \r, as ast/tokenize count lines. Example: `re.split(r'(?<=\n)', text)` with keepends, or an equivalent helper used in both places; '\r'-only files must still work. Line numbers from ast must index the same lines.
3. Worktree cleanup (line ~553-606): set `created = True` only after `git worktree add` succeeds. In `finally`, call worktrees.remove(repo, path) only when created. Always keep the shutil.rmtree(path, ignore_errors=True) and `git worktree prune`. The original exception must reach the caller unchanged.
4. Tests in tests/test_mutation.py (offline, own temp DATA_DIR as the other tests do; use sys.executable for the python path):
   a. a chained check `f"{sys.executable} -m pytest -q -p no:cacheprovider && true"`: the baseline passes and the removed assertion is caught;
   b. a refused check (a command governance.check_command refuses, e.g. one containing `rm -rf /` or `curl ... | sh`; check which command the policy really refuses): the result has no caught count for it, and it is reported as refused/infra or as an error, not as caught;
   c. `mutation_test(repo, 'nope', checks=[CHECK])`: `'error' in result`, and no `mutation-*` directory exists under the worktrees dir;
   d. a seeded file with a form feed (\x0c) in a comment before an assert: the assert after it is removed on the correct line and caught;
   e. git worktree add raising (monkeypatch) propagates the original error, and worktrees.remove is not called.
   Keep the existing test_removed_assertion_is_caught (the acceptance test) passing.
5. docs/threat-model.md: add a row in the existing table format: mutation testing (evaluation.mutation_test) runs agent-written code under mutation → controls: governance.check_command via contracts.run_command, the L3 sandbox (sandbox mode), a scratch worktree under worktrees_dir that is always removed → tests: tests/test_mutation.py.
6. If design.md says something different from these changes, update only the affected lines. Do not touch requirements.md, tasks.md, review.md or decisions.md.
Run `uv run pytest -q tests/test_mutation.py tests/test_evaluation.py` serially and read the summary line (there is no xdist). Run `python3 -I /home/luca/Agent_Company/src/agent_company/edit_check.py src/agent_company/evaluation.py tests/test_mutation.py`. Remove any .pytest_cache you create. Report the commands and their output as evidence.

Acceptance: {"type": "command", "run": "uv run pytest -q tests/test_mutation.py", "requirement_keys": ["EL-R-08"], "stage": "unit"}; {"type": "command", "run": "uv run pytest -q tests/test_evaluation.py", "stage": "integration"}; {"type": "command", "run": "grep -q 'run_command(' src/agent_company/evaluation.py", "requirement_keys": ["EL-R-08"], "stage": "static"}; {"type": "command", "run": "grep -q 'mutation' docs/threat-model.md", "stage": "security"}; {"type": "command", "run": "python3 -I /home/luca/Agent_Company/src/agent_company/edit_check.py src/agent_company/evaluation.py tests/test_mutation.py", "stage": "static"}

## 2. Review mutation testing against the EL-R-08 spec and the iteration-1 findings
- key: `qa_review` · role: qa · writes: no · after: fix_mutation

Review the diff of src/agent_company/evaluation.py, tests/test_mutation.py and docs/threat-model.md against EL-R-08 and .claude/specs/0099-el-r-08-mutation-testing-of-the-quality/design.md. Confirm that each of the five iteration-1 findings is fixed: (1) checks run through contracts.run_command (check_command plus audit, shell syntax works, info fields fill the result, a refused command is treated like infra and never counted as caught); (2) line splitting matches ast/tokenize (\n, \r\n, \r only; form feed safe) in both _mutate and the site finder; (3) worktrees.remove is called only after the worktree was created, and the original error propagates; (4) there are tests for a chained check, a refused check, an unresolvable base/head with no leftover mutation-* directory, and a form-feed file; (5) the threat-model row exists. Trace the data flow of a normal case (assert removed → check fails → caught), an edge case (boundary flip survives → reported as a survivor; a form feed before the site) and an error case (failing baseline, refused command, bad revision). Run `uv run pytest -q tests/test_mutation.py tests/test_evaluation.py` and quote the summary line. Also check that the scratch worktree is always under worktrees_dir and that no data/ or .env path is exposed. List FINDING lines with file:line, then an OUTCOME line, then end with exactly 'VERDICT: PASS' or 'VERDICT: FAIL'.

Acceptance: {"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}; {"type": "contains", "value": "OUTCOME"}; {"type": "min_length", "value": 400}
