# Verification phase 4: agent communication

Date: 2026-11-09. Question: can two agents that each passed phase 3 exchange information and coordinate their work,
and does the orchestrator handle communication failures without losing tasks, running them twice, or marking work
complete when it is not? Statuses as the plan defines them: PASS, FAIL, BLOCKED, PARTIAL, NOT TESTED.

## How agents communicate here

| Plan's term | This platform |
|-------------|---------------|
| Project Manager Agent | Company General Manager (#1): turns a goal into task contracts (`loop._plan`) |
| Developer Agent | Backend Developer (#3), or another enabled developer that staffing picks |
| Task message / contract | A `Task` row holding a validated `TaskContract` (`contracts.validate_plan`): title, description, role, acceptance conditions, dependencies, budget, deadline |
| Delivery | The loop's DAG (`scheduler.run_dag`) staffs an agent for the role and composes its prompt: the contract, the dependencies' outputs, the goal's blackboard sections for the role, and its inbox |
| Code + test results back | The run's output and changed files (`Task.output`, `Task.gate.files`, a checkpoint commit), and the acceptance conditions **run by the platform** (`Task.gate.acceptance`), never the agent's own report |
| PM receives and evaluates | The quality gate evaluates the iteration (`quality.evaluate`). A failure goes back to the PM as feedback in its next planning prompt, with the developer's board entry and messages. The PM never grades the work itself (phase 7, evaluator independence) |
| Agent-to-agent messages | `MESSAGE <kind> to <role>: …` lines in an output become entries in the goal's blackboard `messages` section; the receiving role's next prompt gets them once (`blackboard.inbox`) |

## Automated tests

`tests/test_phase4_communication.py` (19 cases, offline): the GM on one local OpenAI-compatible stub ("Model PM"),
the developer on another ("Model DEV"), through the real loop (`loop.run_goal`: plan, persist, staff, work, gate).
Each stub records what it received. Run: `uv run pytest -q tests/test_phase4_communication.py`.

### 4.1 / 4.2 The communication chain

| Step | Pass condition | Evidence | Result |
|------|----------------|----------|--------|
| PM → Developer | The PM's plan reached provider PM only; the developer's prompt (provider DEV only) holds the task, the goal and the acceptance conditions | `test_the_pm_delegates_to_the_developer_and_the_result_comes_back` | PASS |
| Developer → PM: source, tests, execution result | `factorial.py` and `test_factorial.py` exist. **The platform ran the tests** in the L3 sandbox: "3 passed". The answer has an execution result section. Each part is an acceptance condition in `Task.gate` | same | PASS |
| The result reaches the PM | The developer's entry is on the goal's board (author = developer, task = the task). The runs are plan on Model PM and task on Model DEV, both traced with no `trace_issues` | same | PASS |
| PM evaluates and asks for a correction | A broken factorial with a false claim of "3 passed" fails the gate. The PM's second planning prompt holds the failure, the developer's correction task carries it, and iteration 2 passes. The failed iteration stays in `Goal.history` | `test_a_failed_result_goes_back_to_the_pm_who_delegates_the_correction` | PASS |

### 4.3 The communication data

| Field | Task (PM → Developer) | Agent message (`MESSAGE` line) | Result |
|-------|-----------------------|--------------------------------|--------|
| Sender agent ID | `Task.planned_by` (**added**, V17) | `author_agent_id`, `meta.from` | PASS after V17 |
| Receiver agent ID | `Task.agent_id`; the `assigned` stage names the agent | `meta.to` (a role) + `meta.delivered` (agent ids) | PASS |
| Task ID | `Task.id`, `Task.key`, `Task.goal_id` | `task_id` | PASS |
| Message ID | `Task.id` | the entry's `id`, key `<task>:<n>` | PASS |
| Content | `Task.contract` | `content` | PASS |
| Format | `TaskContract` schema, validated before it is stored | `meta.kind`: fact, assumption, recommendation, request, handoff, done | PASS |
| Timestamp | `Task.created_at` (**added**, V17), `started_at`, `finished_at`, each stage's `at` | `created_at` | PASS after V17 |
| Delivery status | `Task.stage`: planned → assigned (agent) → in_progress → review | `meta.delivered`: delivered once per agent | PASS |
| Response status | `Task.status` done/failed/skipped, with the acceptance results | a later message from the receiver answers a request; an unanswered repeat escalates (S04-R-08) | PASS |

Evidence: `test_every_message_names_sender_receiver_task_content_time_and_status`. The goal trace (`loop.trace`, the
Goals page's trace) now shows each task's sender and creation time beside its receiver.

### 4.4 Communication failures

Expected: no lost tasks, no uncontrolled duplicate executions, no assignment marked complete when it is not.

| # | Failure | What happens | Evidence | Result |
|---|---------|--------------|----------|--------|
| 1a | Receiver offline: its model unreachable | Retried, then the run fails. The task is `failed` with the error, never done, and every run is recorded against the task | `test_1_receiver_offline_its_model_unreachable_the_task_fails_never_completes` | PASS |
| 1b | Receiver offline: its model at its usage limit, no fallback | The task waits (`skipped`/`blocked`) and the goal pauses until the reset. Once the limit is gone, the same task (same id) runs once and the plan is not made again | `test_1_receiver_offline_its_model_at_its_limit_the_task_waits_and_runs_once_on_resume` | PASS |
| 1c | Receiver offline: every developer agent disabled | Staffing recruits a replacement (`recruit_requires_approval` = never). It has no model, and the developer route has none in the test, so the offline simulator stands in. The task fails honestly and the trace flags it (phase 2's V7) | `test_1_receiver_offline_every_developer_disabled_the_task_is_never_completed` | PASS (V7 applies) |
| 2a | Receiver rejects the task | "I decline this task": every deliverable condition fails and the task fails | `test_2_receiver_rejects_the_task_it_is_not_completed` | PASS |
| 2b | Receiver names a blocker | `BLOCKER:` with no change: the task is `skipped`/`blocked` and the goal pauses for a person, with no review of nothing | `test_2_receiver_names_a_blocker_the_goal_waits_for_a_person` | PASS |
| 3a | Invalid task message: the PM's answer has no JSON, depends on a task that does not exist, or names no receiver role | Refused by `contracts.validate_plan`, sent back to the PM once with the reason, and the corrected plan runs. Logged "plan … was invalid" | `test_3_and_6_an_invalid_task_message_goes_back_to_the_pm_once` (3 cases) | PASS |
| 3b | Invalid twice | The standard plan runs, and the log and board say so | `test_3_twice_invalid_the_standard_plan_runs_and_says_so` | PASS |
| 3c | Invalid agent message: an unknown kind, or a receiver role that does not exist | **Was** silently ignored or stored and never delivered. Now each one is reported in the goal's log, naming the sender and the line (V18) | `test_3_an_agent_message_to_no_role_or_in_no_known_kind_is_reported_not_lost` | FAIL → PASS (V18) |
| 4a | Duplicate message | The same message on the goal's board is stored once (`tests/test_blackboard.py::test_message_budget_and_duplicates`), and the inbox delivers it once per agent (4.3 test) | as named | PASS |
| 4b | Duplicate task delivery after a restart or resume | A finished task keeps its result. The resumed DAG does not hand it out again | `test_4_a_finished_task_is_not_run_again_when_its_iteration_resumes` | PASS |
| 4c | The same goal handed out twice at once | **Was:** both workers passed the "queued?" check, both ran it, and the goal ended `failed` (IllegalTransition). Now `run_goal` claims the goal with one conditional UPDATE, and the second worker returns (V19) | `test_4_a_goal_handed_out_twice_runs_once` (fails without the claim) | FAIL → PASS (V19) |
| 5 | Timeout | A contract `deadline` of 1 s against a provider that answers after 5 s: each attempt times out, and the task fails with the error. No process is left (`LiveProcess` empty) | `test_5_a_task_past_its_deadline_fails_and_nothing_keeps_running` | PASS |
| 6 | Incorrect task ID | In a plan: refused (3a). A worker given an id that does not exist raises before staffing: no run, no task, no busy agent. Agents never name task ids: the loop stamps the sender's own task on every message | `test_6_a_worker_given_a_task_id_that_does_not_exist_starts_nothing`, 3a | PASS |
| 7 | Incomplete result | No tests → the test file and command conditions fail. No execution result → the `contains` condition fails. Either way the task fails | `test_7_an_incomplete_result_is_not_completed` (2 cases) | PASS |

Limit of 7: completeness is only as good as the PM's acceptance conditions. A contract with none gets the default
(`min_length` 20, and a writing task must change something), so an incomplete answer to a loosely written contract
can pass its task. The gate's quality commands, QA reviews and phase 7 cover this; the communication layer does not.

## Live run (this server)

`docs/verification/phase4_live.py two`: goal #330 on this server's agents and models, in a scratch repository
(`data/workspaces/goal-330`). It ran with the plan's assignment plus "plan exactly one task, for the developer". In
this process only, the research step and the task critic were off and proposals were not auto-approved. Evidence:
`data/verification/phase4/two.json`; runs 2115–2120.

| Step | What the records show | Result |
|------|-----------------------|--------|
| PM receives the assignment | The GM (Claude Code; its own `claude_37` is benched, phase 3's V15, flagged on runs 2115–2116) planned one developer task with 8 conditions: both files, `python3 -B -m pytest -q test_factorial.py` (unit stage), a separate behaviour script (0, 1, 5, 10, -1, 3.0, True), an `## Execution result` section, a "N passed" line, an out-of-scope list and a $3 budget | PASS |
| The template adds QA | The planner chose the `feature` template, whose review policy (`independent_qa`) appended an independent QA review. The live run is therefore GM → Developer → QA: the plan's three-agent step, by design | as designed |
| Task message | Task 694: sender GM (#1), receiver Backend Developer (#3), created 23:08:37.951, assigned 23:08:37.988, finished 23:09:06; stages planned → assigned → in_progress → review → approved → completed | PASS |
| Developer → result | `factorial.py`, `test_factorial.py`. **The gate ran the tests in L3: 21 passed**, and the behaviour script passed. 8/8 conditions | PASS |
| Developer → QA message | `MESSAGE handoff to qa` (entry 2026): delivered to QA Engineer (#4) in its prompt, once (`delivered: [4]`) | PASS |
| QA → result | Task 695: QA ran the tests itself (21 passed, 1 doctest), checked the normal, edge and error cases: VERDICT: PASS. Its `done` message to the developer is stored; the developer had no later task, so it stays undelivered | PASS |
| PM / gate evaluates | Quality gate passed in iteration 1; the owner policy merged it into the scratch repository's main (e1b8d120c2) | PASS |
| Nothing left over | No live processes; the agents are idle. One follow-up proposal (#283) was rejected as a toy goal's; its content is V20 below | PASS |

Total 130 s.

## Findings

- **V17 (fixed):** a task did not record who sent it. The planner's agent id stayed only in the goal's checkpoint,
  which the next iteration overwrites, and tasks had no creation time. Added `Task.planned_by` and
  `Task.created_at`. `loop._persist` stores the planner for planned tasks; the loop's own review tasks have none.
  Both are shown in the goal trace.
- **V18 (fixed):** an agent message to a role that does not exist (`MESSAGE request to devloper: …`) was stored and
  never delivered, and a line with an unknown kind (`MESSAGE question to qa: …`) was ignored. In both cases nobody
  learnt that the message went nowhere. Each one is now an event in the goal's log naming the sender, the task and
  the line, and the misaddressed message is not stored.
- **V19 (fixed):** `run_goal` checked "queued" and then changed the status in a later transaction. Two workers given
  the same goal at once (a submit and the watcher, or the server and a CLI process) both ran it. They planned twice,
  made two worktrees, and the second failed the goal with an illegal transition. `loop._claim` now moves the goal to
  `planning` with one conditional UPDATE, the first statement of its transaction, so SQLite's write lock picks one
  worker.

- **V20 (fixed in phase 5):** the GM's acceptance commands (`python3 -B -m pytest …`, `python3 -B -c …`) were not in the
  developer's or QA's command allow-lists (`executors.CLAUDE_BASH`/`CLAUDE_CHECK` allow `pytest`). Both agents fell
  back to plain `pytest`, which leaves `__pycache__/` in the worktree, and QA could not remove it. The gate ran the
  exact commands in L3, so verification is unaffected. `executors.PYTEST_FORMS` now allows `python3 -m pytest` and
  `python3 -B -m pytest` in every writing and check list; `python3 -B -c` stays refused (it runs any code). QA's
  proposal #283 said the same (`test_v20_the_planners_pytest_command_is_one_the_developer_and_qa_may_run`).

## Phase 4 acceptance

| Criterion | Result |
|-----------|--------|
| 4.1 Two-agent test (PM + Developer) | PASS: offline, strictly two agents; live, with the template's QA review |
| 4.2 Communication chain PM → Developer → PM | PASS, including the correction cycle (offline) |
| 4.3 Communication data | PASS after V17 (sender and creation time of a task) |
| 4.4 Failures 1–7 | PASS; 3 (agent messages) after V18, 4 (a goal handed out twice) after V19 |
| Three agents | PASS live (GM → Developer → QA, a message delivered between them) |
| Full team | NOT TESTED in phase 4: research, critic and the remaining roles together come with phase 6's project run |

V20 fixed in phase 5. V7 and V15 from earlier phases still apply.

## Phase 5 starts from here

Phase 5 (project registration): TEST-PROJECT-001/002, their storage, agent assignments and isolation.
