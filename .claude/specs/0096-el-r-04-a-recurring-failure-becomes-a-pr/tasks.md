# Tasks (iteration 1)



## 1. Specify recurring-failure rule proposals (EL-R-04)
- key: `spec` · role: architect · writes: no

Write a short technical spec for EL-R-04 (docs/requirements/EVOLUTION-LOOPS.md). Read src/agent_company/rca.py (analyse, after_goal, evidence, the report dict stored on Lesson.report with 'rca', 'goals', 'watch', 'confirmed' entries carrying 'layer'), src/agent_company/projects.py (Proposal handling: decide, settle_proposals, list_proposals, proposal_dict, CASE_KINDS, restricted), src/agent_company/db.py (Proposal: kind String(16), case JSON, tag), src/agent_company/catalog.py (add, export_claude writes enabled 'rule' items to .claude/rules/<name>.md) and gm.py configure (line ~264). Answer these points concretely:
1. Cause key. A cause is (layer, check). The layer comes from a CONFIRMED cause's 'layer' (selfevolve.LAYERS only; unclassified causes never count). The check comes from the analysis report's 'watch' list. Task-key checks ('task …') are excluded, as rca._streak already does. Count DISTINCT failed goals per cause per project, using the 'goals' of each rca Lesson with status approved (screened). Secret goals never count (classification secret).
2. Threshold. A new setting in config.py/prefs (e.g. rule_proposal_min_goals, default 3).
3. Trigger. A function, e.g. rca.propose_rules(project_id) -> list[int], called at the end of rca.analyse after the item is stored, outside the open session or with that same session (one SQLite writer, see CLAUDE.md). It never raises into the goal's ending.
4. Drafting. The instructor role drafts the line: staff/resolve role 'instructor' through executors/router the way exercises.py does. The output must be ONE line, trimmed and screened with learning.screen. If no instructor agent exists or its run fails or returns nothing usable, use a deterministic template line naming the layer and check. Tests run offline, so the fallback must be deterministic.
5. Proposal. Proposal(kind='rule', tag='rule', status pending, project_id, goal_id = the latest goal of the cause, title 'Rule: …', description with the rule line, the cause and the cited goals '#a, #b, #c' and the RCA K-ids, case={'cause': {'layer','check'}, 'goals': [...], 'rule': line, 'name': slug}).
6. Deduplication. No new proposal when any Proposal of kind 'rule' in the same project has the same cause in its case, whatever its status, so a rejected rule is not proposed again. Say whether the goals of a later 4th failure are added to the existing pending proposal's case or left out; recommend leaving it unchanged, so the result is deterministic.
7. Decision. projects.decide for kind 'rule': approval spawns no goal. It calls catalog.add('rule', name, description, content=line plus evidence, enabled) and records the catalog id in the proposal's case/decided_by. Rejection only marks it rejected. Restricted text still needs a person. Check that settle_proposals, list_proposals and the dashboard do not mistake 'rule' for a case kind or a goal-spawning kind.
8. Tests to write in tests/test_rule_proposals.py: (a) three failed goals with one cause give exactly one pending rule proposal citing all three goal ids; (b) a fourth goal with the same cause gives no second proposal; (c) two goals give none; (d) a different layer or check is a separate cause; (e) approval creates an enabled catalog rule that export_claude writes to .claude/rules/<name>.md. Create the RCA Lesson rows directly or stub the model run, the way tests/test_rca.py does.
List the exact files and functions to change. Keep it small.

Acceptance: {"type": "file_exists", "path": ".claude/specs/0096-el-r-04-a-recurring-failure-becomes-a-pr/spec.md"}; {"type": "contains", "value": "propose_rules"}; {"type": "contains", "value": "test_rule_proposals"}; {"type": "min_length", "value": 1500}

## 2. Implement rule proposals from recurring RCA causes, with tests
- key: `implement` · role: developer · writes: yes · after: spec

Implement the spec in .claude/specs/0096-el-r-04-a-recurring-failure-becomes-a-pr/spec.md (read it first).
- src/agent_company/rca.py: propose_rules(project_id), called from analyse after the RCA item is stored. It groups approved RCA Lessons by (layer, check) over distinct failed goals and drafts the line through the instructor with a deterministic fallback. It creates one pending Proposal(kind='rule', tag='rule', case={cause, goals, rule, name}) citing the goals and never duplicates a cause that already has a rule proposal of any status.
- src/agent_company/config.py: add the threshold setting, default 3.
- src/agent_company/projects.py: decide approves kind 'rule' by adding an enabled catalog 'rule' item (catalog.add), spawns no goal, and keeps settle_proposals and list_proposals working.
- tests/test_rule_proposals.py: covers 3 goals → exactly one proposal citing #a, #b, #c; a 4th → still one; 2 → none; a different cause → a separate proposal; approval → catalog rule exported by catalog.export_claude to .claude/rules/<name>.md.
Tests must run offline with their own DATA_DIR (see tests/conftest.py and tests/test_rca.py). DB writes inside an open session() must use that session. Update the EL-R-04 status line in docs/requirements/EVOLUTION-LOOPS.md (the table row and the '**Status:**' line) to done, naming the functions. Add one line about it to the rca.py entry in CLAUDE.md.

Acceptance: {"type": "file_exists", "path": "tests/test_rule_proposals.py"}; {"type": "command", "run": "uv run pytest -q tests/test_rule_proposals.py", "requirement_keys": ["EL-R-04"], "stage": "unit"}; {"type": "command", "run": "uv run pytest -q tests/test_rca.py tests/test_projects.py tests/test_eval_proposals.py", "stage": "integration"}

## 3. Review EL-R-04 against the spec
- key: `qa_review` · role: qa · writes: no · after: implement

Review the change against .claude/specs/0096-el-r-04-a-recurring-failure-becomes-a-pr/spec.md and EL-R-04. Run `uv run pytest -q tests/test_rule_proposals.py tests/test_rca.py tests/test_projects.py tests/test_eval_proposals.py`. Trace the data flow in three cases. Normal case: three failed goals with one cause give one pending rule proposal citing them. Edge cases: a fourth matching goal gives no duplicate; a previously rejected rule for the cause is not proposed again; unclassified layers and 'task …' checks do not count; the same goal analysed twice counts once; secret goals are excluded. Error case: no instructor agent, or the instructor run fails, falls back to the deterministic line and the goal's ending never raises. Also check that approval creates an enabled catalog rule exported to .claude/rules/, spawns no goal, and that restricted text still needs a person. Check that writes inside an open session use it. List FINDING lines, then an OUTCOME and 'VERDICT: PASS' or 'VERDICT: FAIL'.

Acceptance: {"type": "regex", "pattern": "VERDICT: (PASS|FAIL)"}; {"type": "contains", "value": "OUTCOME"}
