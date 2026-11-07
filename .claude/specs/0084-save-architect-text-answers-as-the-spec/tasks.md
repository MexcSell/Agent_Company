# Tasks (iteration 2)



## 1. Run the spec-answer regression tests on the committed fix and repair only what fails
- key: `verify_fix` · role: developer · writes: yes

The fix for this goal is already committed on this branch: 97cf040 changed src/agent_company/loop.py, src/agent_company/specs.py and tests/test_spec_answer.py. Read the 'Changes' list (items 1-7) in .claude/specs/0084-save-architect-text-answers-as-the-spec/spec.md, then `git show 97cf040`. Steps:
1. Run `uv run pytest -q tests/test_spec_answer.py tests/test_spec_paths.py` and paste its summary line.
2. If a test fails, find the cause. Fix the code when the code is wrong, or the test when it contradicts the spec. Keep the change minimal, then re-run.
3. Check that each of Changes 1-5 is present. Check that the five spec tests exist: test_failed_run_answer_not_saved, test_spec_v2_answer_is_the_spec_not_design, test_read_only_architect_with_writing_contract_saves, test_trailing_slash_saved, test_answer_on_disk_before_acceptance_check. Check that test_writing_task_with_unknown_changes_keeps_out_of_its_folder is unchanged in meaning. Add anything that is missing.
4. Run `uv run pytest -q tests/test_spec_answer.py tests/test_spec_paths.py tests/test_loop.py` if tests/test_loop.py exists, otherwise any tests/test_*loop*.py, and paste the summary line.
Do NOT run the whole suite (`uv run pytest -q` with no paths). The last run timed out at 600 s, and the gate runs the suite itself. Do not refactor and do not touch files outside the in_scope paths. If everything passes and nothing is missing, change no file and say so with the pasted summary lines. End with a line `RESULT: <n> passed, <m> failed`.

Acceptance: {"type": "command", "run": "uv run pytest -q tests/test_spec_answer.py tests/test_spec_paths.py", "requirement_keys": ["S08-R-01"], "stage": "unit"}; {"type": "regex", "pattern": "RESULT: \\d+ passed, 0 failed"}

## 2. Review the spec-answer fix against the spec
- key: `qa_review` · role: qa · writes: no · after: verify_fix

Review only `git diff 212acd9..HEAD -- src/agent_company/loop.py src/agent_company/specs.py tests/test_spec_answer.py` against Changes 1-7 and §2-§3 of .claude/specs/0084-save-architect-text-answers-as-the-spec/spec.md. Read the code: run no test suite, because the gate does that, and the earlier QA ran over budget (K336). Verify the data flow of the following cases:
- normal case: a read-only architect task whose file_exists names <own>/spec.md gets its answer saved before contracts.check;
- edge case: a trailing-slash path, a `spec_v2` key, and a contract-writes architect with `changed` None;
- error case: res.ok false, so nothing is saved and the task still fails.
Confirm the invariants in §3: no LOOP_FILES, no other goal's folder, no .git, nothing outside the worktree or reached through a symlink. Confirm the lock choice stays on contract.writes. Check that each regression test would fail without its change. List FINDING lines with file:line, then an OUTCOME line, and end with "VERDICT: PASS" or "VERDICT: FAIL".

Acceptance: {"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}; {"type": "contains", "value": "OUTCOME"}; {"type": "min_length", "value": 400}
