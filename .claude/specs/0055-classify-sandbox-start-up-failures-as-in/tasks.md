# Tasks (iteration 2)



## 1. Classify the required-and-unavailable sandbox start-up failure as infrastructure
- key: `required_infra` · role: developer · writes: yes

Cause (FACT, from review.md finding 1): in src/agent_company/sandbox.py `run()`, when mode == 'required' and `available()` returns (False, why), the result is `Outcome(126, "", "", f"not run: the sandbox is required and unavailable ({why})")` with no `infra`. `available()` probes bwrap with the toolchain paths bound. A missing path makes `why` read like 'bwrap failed: bwrap: Can't find source path …'. contracts.run_command then takes its `if not res.isolation` branch and returns False. It sets `info['infra']` to the empty `res.infra`, so loop._make_worker fails the task instead of skipping it, and every iteration repeats the failure.
Fix the cause with the smallest change:
1. In sandbox.run's required-and-unavailable branch, take the bwrap text after any 'bwrap failed: ' prefix (read `available()` to see the exact format of `why`) and pass it through `startup_failure`, or match it against INFRA_RE. When it matches, set `infra=` to that start-up line on the returned Outcome. Any other unavailability reason (no bwrap binary, the sandbox turned off) keeps infra="".
2. Check that contracts.run_command's `if not res.isolation` path still fills `info['infra']`: the `info.update(... infra=res.infra)` call runs before the return. Change it only if it does not.
3. Add regression tests to tests/test_sandbox_infra.py, each with 'required' in its name. (a) In required mode, with `available` monkeypatched to return (False, "bwrap failed: bwrap: Can't find source path /opt/x: No such file or directory"), `run_command` returns False with info['infra'] set, and `check()` carries 'infra'. (b) The same with a non-start-up reason (e.g. 'bwrap not installed') leaves infra empty. (c) A goal with sandbox 'required' whose writing task hits case (a) ends with its task skipped with INFRA_SKIP, an incident in category 'infrastructure', and the goal paused. Reuse the existing fixtures and helpers in that file. Test (a) must fail without the fix.
4. Extend the sandbox.py entry in CLAUDE.md with one clause: a bwrap/systemd-run start-up failure (`INFRA_RE`, also in required mode) becomes `Outcome.infra`; the task is skipped with `INFRA_SKIP`, an `infrastructure` incident opens, and the goal pauses until a person resumes it.
5. If the threat-model row added in iteration 1 lists test names, add the new test there in docs/threat-model.md.
Change nothing else: no quality.py, management.py or performance.py.

Acceptance: {"type": "command", "run": "uv run pytest -q tests/test_sandbox_infra.py -k required", "requirement_keys": ["S14-R-03"], "stage": "unit"}; {"type": "command", "run": "uv run pytest -q tests/test_sandbox_infra.py", "requirement_keys": ["S14-R-03", "S07-R-02"], "stage": "unit"}; {"type": "command", "run": "uv run pytest -q -x", "stage": "integration"}; {"type": "command", "run": "grep -q 'INFRA_SKIP' CLAUDE.md", "stage": "static"}

## 2. Review the sandbox infrastructure classification, including required mode
- key: `qa_review` · role: qa · writes: no · after: required_infra

Review the goal branch's change against main: git diff main...HEAD for src/agent_company/sandbox.py, contracts.py, loop.py, governance.py, tests/test_sandbox_infra.py, CLAUDE.md and docs/threat-model.md. Use the spec in .claude/specs/0055-classify-sandbox-start-up-failures-as-in/spec.md. Iteration 1's review (review.md in that folder) already approved the core path, so do not review it again from scratch. Focus on: (1) the new required-and-unavailable classification. Trace the data flow for the normal case (bwrap starts, the command fails, no infra), the edge case (required mode, and `available()` reports "bwrap: Can't find source path", so the task is skipped, an incident opens and the goal pauses), and the error case (required mode, bwrap missing for another reason, so no infra and the normal failure). (2) Confirm that the change cannot turn a failure into a pass. (3) Confirm that the CLAUDE.md clause is present. Run `uv run pytest -q tests/test_sandbox_infra.py` once. If the permission system refuses it, say so and cite the gate's acceptance run on required_infra. Keep the review short and do not re-read unrelated modules: this task has a cost budget. List FINDING lines (file:line | problem | suggestion), then OUTCOME and 'VERDICT: PASS' or 'VERDICT: FAIL'.

Acceptance: {"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}; {"type": "contains", "value": "OUTCOME"}; {"type": "min_length", "value": 400}
