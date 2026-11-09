# Tasks (iteration 2)



## 1. Verify and finish the `after`/`requires`/partial-review change from iteration 1
- key: `finish_deps` · role: developer · writes: yes

The implementation is already committed on this branch (commit ac4f7f0; spec at .claude/specs/0085-make-threat-model-depend-only-on-the-exi/spec.md, ADR docs/adr/0087-decoupling-mechanism.md). Do NOT rewrite or redesign it. Keep the run short: the last run timed out at 600 s.
1. Run `uv run pytest -q tests/test_task_dependencies.py tests/test_contracts.py tests/test_loop.py`. Fix only what fails, in the smallest way.
2. Confirm a K237 regression test exists that would fail under the old rule. If the scheduler had skipped every dependent of a failed task, `threat_model` (after: dns_fix, requires: browser.py + test_blocks_rebinding in tests/test_browser.py) and `qa_review` would be skipped. test_k237_threat_model_and_review_run_after_dns_fix_fails must assert both are 'done' while dns_fix is 'failed'. Also add a case where `requires` is unmet (browser.py missing) and assert that threat_model ends 'skipped' with an output starting with loop.REQUIRES_SKIP. If such a test exists already, leave it.
3. In docs/threat-model.md add one short entry for the new `requires` path read: a relative path only, no '..', symlinks resolved inside the worktree, contains/file_exists only, never a command. Cite test_requires_path_stays_inside_worktree.
4. Run `uv run pytest -q` on those three files again and paste the pass line.
End with a single 'BLOCKER:' line only if you cannot make the tests pass.

Acceptance: {"type": "command", "run": "uv run pytest -q tests/test_task_dependencies.py", "requirement_keys": ["S07-R-02", "G-R-01"], "stage": "unit"}; {"type": "command", "run": "uv run pytest -q tests/test_contracts.py tests/test_loop.py", "requirement_keys": ["G-R-01", "S05-R-08"], "stage": "unit"}; {"type": "command", "run": "grep -q 'REQUIRES_SKIP' tests/test_task_dependencies.py && grep -q 'requires' docs/threat-model.md"}

## 2. Review the dependency change against the spec and K237
- key: `qa_review` · role: qa · writes: no · after: finish_deps

Review `git diff main -- src/agent_company/contracts.py src/agent_company/loop.py src/agent_company/scheduler.py tests/test_task_dependencies.py docs/threat-model.md` against .claude/specs/0085-make-threat-model-depend-only-on-the-exi/spec.md and K237. Trace the data flow of three cases. (a) Normal: in the K237 plan dns_fix fails; threat_model (after dns_fix, requires browser.py + the cited test name) still runs once dns_fix has finished; qa_review runs after every dependency has finished and reviews what was delivered. (b) Edge: browser.py is missing, so threat_model is skipped with REQUIRES_SKIP and qa_review still runs on build; one dependency is still running, so qa_review waits. (c) Error: every dependency of a review failed, so the review is skipped; a non-review dependent keeps 'skipped: a dependency failed'; a partial review that passes never makes the iteration pass (quality.evaluate still fails on the failed task); `requires` paths cannot escape the worktree. Also check that old plans without after/requires validate unchanged, that cycles through `after` are rejected, and that loop.PLAN_INSTRUCTIONS documents both fields. Run `uv run pytest -q tests/test_task_dependencies.py` if your tools allow it; otherwise read the tests. List each problem as a 'FINDING:' line with file:line, then an 'OUTCOME:' line, then 'VERDICT: PASS' or 'VERDICT: FAIL'.

Acceptance: {"type": "contains", "value": "VERDICT:"}; {"type": "regex", "pattern": "OUTCOME:"}; {"type": "min_length", "value": 400}
