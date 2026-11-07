# Tasks (iteration 3)



## 1. Store label counts after the outcome message is posted; prove the order with a test
- key: `store_after_outcome` · role: developer · writes: yes

Read src/agent_company/loop.py first: run_goal (around 1239-1270), _chat_outcome (1272) and _notify_goal (1858-1876). Also read mission.label_counts/store_label_counts (mission.py:182-237) and tests/test_label_cache.py.
1. Remove `mission.store_label_counts(goal_id)` from _notify_goal. Keep rca.after_goal where it is: rca reads mission.label_counts, which computes and stores the counts on the first read, so RCA still works.
2. In run_goal, after `_chat_outcome(goal_id, out)`, store the counts only when the goal is finished, i.e. its current status is not in workflow.ACTIVE. Example: `if _goal(goal_id).status not in workflow.ACTIVE: mission.store_label_counts(goal_id)`. Wrap the status check in try/except and log_event the error, so the cache never changes a goal's outcome or return value. store_label_counts already fails open.
3. In tests/test_label_cache.py, add tests that: (a) the project outcome message (projects.post_for_goal / _chat_outcome) is recorded before store_label_counts runs. Monkeypatch both, record the call order and run a goal on the offline simulator as the existing tests do. (b) A goal that stays active (paused or awaiting a resume) is not stored. (c) A store_label_counts that raises does not change run_goal's returned status.
4. Check tests/test_projects.py around line 223. If the test only passed through timing, make it wait for the outcome deterministically (e.g. its existing wait_outcome helper). Do not weaken what it asserts.
5. Update CLAUDE.md's mission.py line only if it is now wrong (it says 'stored when the goal finishes').
Run `uv run pytest -q tests/test_label_cache.py tests/test_projects.py tests/test_mission.py` and then `uv run pytest -q`. Quote the final summary line of each run in your output. If a test fails, report its name and whether it also fails on the base commit (`git stash` is forbidden; use `git worktree add` to a temp dir under data/worktrees/ if you need to compare, or say you could not compare).

Acceptance: {"type": "command", "run": "uv run pytest -q tests/test_label_cache.py tests/test_projects.py", "requirement_keys": ["S07-R-02"], "stage": "unit"}; {"type": "command", "run": "uv run pytest -q", "requirement_keys": ["S07-R-02"], "stage": "integration"}; {"type": "command", "run": "! grep -n 'store_label_counts' src/agent_company/loop.py | grep -q '_notify_goal' && python3 -c \"import re,sys;s=open('src/agent_company/loop.py').read();i=s.index('def run_goal');j=s.index('def _chat_outcome');b=s[i:j];sys.exit(0 if b.index('store_label_counts')>b.index('_chat_outcome(goal_id, out)') else 1)\"", "stage": "static"}; {"type": "regex", "pattern": "passed"}

## 2. Review the label-count cache against the goal and the iteration-2 findings
- key: `review_cache` · role: qa · writes: no · after: store_after_outcome

This is a read-only review. Do not try to run pytest yourself. The quality gate runs `uv run pytest -q` as the integration stage of task store_after_outcome; cite that run (its summary line from the gate result or the developer's output) as the full-suite evidence, and say explicitly which run you are citing. Review src/agent_company/mission.py (label_counts, _write, store_label_counts), loop.py (run_goal ordering, _notify_goal), workflow.py:107 (cache cleared on any status change), api.py:2367 and rca.py:97, and tests/test_label_cache.py. Trace the data flow for three cases. Normal: a goal finishes, the outcome is posted, then the counts are stored, and the overview and RCA read the stored counts without calling report(). Edge: a goal is resumed or reopened, the counts are cleared, and a write after the resume is not stored because _write checks ACTIVE. Error: the DB is locked during the store or the first read, the counts are still returned, an event is logged, and the GET never fails. Also check that nothing stores counts for an active goal, and that the overview's fail-open read from K495 holds. List each issue as `FINDING: <file:line> | <problem> | <fix>`, then an OUTCOME paragraph, and end with `VERDICT: PASS` or `VERDICT: FAIL`.

Acceptance: {"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}; {"type": "contains", "value": "OUTCOME"}; {"type": "min_length", "value": 600}; {"type": "file_exists", "path": ".claude/specs/0051-cache-label-counts-per-finished-goal/qa-review-3.md"}
