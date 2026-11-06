# Evolution loops, the research lead's tool loop and model fallback (`/home/luca/update_2/1`, shared chats)

Sources, read on 2026-11-06:

- the nine diagrams `a_1.jpg` … `a_9.jpg` in `/home/luca/update_2/1/` (not kept in the repository; see
  [images/SOURCES.md](images/SOURCES.md)): a_1 the Execute → Evaluate → Evolve cycle with a failure log, CI-made rule
  proposals and hooks; a_2 Reflexion (actor, evaluator, self-reflection, experience memory); a_3 planning,
  verification and a lessons file that updates the rules; a_4 orchestrator, event bus, state store, observability and
  specialised agents; a_5 memories distilled from sessions and recalled by new agents; a_6 short-term memory →
  self-feedback (trajectory comparison) → self-optimisation, keep or roll back; a_7 a meta agent sampling designs from
  an archive, implementing and evaluating a new one and storing it with its evaluation log; a_8 "loops within loops":
  a planner, specialists each with a critic loop, a red-team agent, testing and CI/CD loops, the lifecycle
  discover → plan → execute → critique → validate → learn; a_9 a self-improvement runtime (teacher, critique and
  evaluation agents, people, metrics).
- the owner's shared ChatGPT conversations (read through `chatgpt.com/backend-api/share/<id>`; the share pages
  themselves are script shells): "Create Research Agent", "Research Agent Architecture" (research agent + Codex),
  "Evaluating Evolving Agents", "Design Autonomous AI Company", "Codex components and Bridge", "Improve AXoR Design",
  "AXoR Redesign Proposal"; "Learn AI Engineering" and "Assistant Introduction" hold no requirement. The long ones were
  read in part (about the first 39 000 characters word for word, the rest as a summary), so items resting only on a
  summary are marked *(summary)*.
- the owner's instruction of 2026-11-06: Claude Code, GitHub Copilot and OpenAI Codex as models; the research agent
  calls its model through the Model Router and completes the work with its own tools; the router switches to another
  model when one reaches its usage limit; Codex cooperates with the research agent.

IDs `EL-R-nn`; status words as in [README.md](README.md).

## 1. Compared with Agent Company

| Idea (source) | Agent Company | Decision |
| --- | --- | --- |
| The router switches model at a usage limit (owner) | before: a limited model failed every run; staffing then cloned the degraded agents (41 clones of the GM on 2026-11-06) | **EL-R-01** done |
| The research agent calls its model through the router and completes the task with its own tools; "the LLM provides reasoning, the research agent provides execution"; a JSON plan validated against registered tools, never executed as code (research agent + Codex) | before: one model call over the stored material | **EL-R-02** done |
| Codex cooperates with the research agent (owner); an independent evaluator of another family (evolving agents) | the review of a research report used another family only among API models | **EL-R-03** done |
| Execute → evaluate → evolve; evaluation-driven evolution with a baseline, hard constraints, no combined score, regressions reject (a_1, a_6, evolving agents) | experiments with baseline vs candidate, regressions, canary and rollback (`experiments.py`, SPEC-13, G-R-11); verified self-evolution of the platform's code (`selfevolve.py`) | done (`experiments.py`, `selfevolve.py`) |
| Failure log collected automatically (a_1 `failures.jsonl`) | every failed run, check and gate result is in the DB; RCA of failed goals with failure layers (`rca.py`, G-R-17) | done, adapted (`rca.py`, `performance.operations`) |
| A recurring failure becomes a rule proposal (a_1 "CI → LLM analysis → AGENTS.md rule as a PR"; a_3 lessons update the rules) | RCA actions become proposals; playbooks are curated from lessons (`evolution.py`); nothing proposes a project *rule* | **EL-R-04** open |
| Hooks check quality after each edit and the agent corrects itself (a_1 PreToolUse/PostToolUse) | the team hooks only log (`hooks.py`); syntax was checked at the gate, after the run | **EL-R-05** done |
| Reflexion: evaluator, self-reflection, experience memory (a_2); memories distilled and recalled by new agents (a_5) | lessons after every task, success and failure, screened and retrieved into the next task package; clones and recruits inherit experience (`learning.py`, SPEC-11, S12-R-02) | done (`learning.learn`, `learning.retrieve`) |
| Orchestrator, event bus, state store, observability, specialised agents (a_4); runtime with teacher, critique and evaluation agents and metrics (a_9) | goal loop, blackboard, SQLite state, `/metrics`, roles; instructor, QA, evaluation harness | done (`loop.py`, `blackboard.py`, `metrics.py`, `training.py`, `evaluation.py`) |
| A critic loop per specialist inside the iteration (a_8 purple loops) | review tasks check a whole iteration's change (`quality.REVIEW_ROLES`), not one task before it is handed on | **EL-R-06** open |
| A red-team agent (a_8) | adversarial suite (S09-R-05), security review role | done (`evals/adversarial.json`) |
| An archive of designs with their evaluation logs; the next candidate sampled from it (a_7) | experiments keep baseline and candidate, no lineage between candidates | **EL-R-07** open |
| Several competing candidates per improvement (evolving agents *(summary)*) | one candidate per experiment | part of EL-R-07 |
| Evaluate the evaluator: mutation testing of the checks (evolving agents *(summary)*) | the eval suites' checks are verified to fail on the seed (`exercises.py`); the gate's own checks are not mutation-tested | **EL-R-08** open |
| A bridge: an MCP server through which Codex calls the company's tools (get task, update progress, save insight, request review) (Codex and Bridge *(summary)*) | CLI agents get MCP servers an administrator granted (`mcp.py`); the company's own functions are not offered as tools | **EL-R-09** open |
| GitHub Copilot CLI as a model (owner; a_8) | `copilot` kind behind `enable_copilot` (`executors.CopilotExecutor`); the CLI is not installed on this host | skip (needs the owner's GitHub sign-in: Models → Add model → Install, then `copilot` login) |
| Research agent layers, source registry, evidence statuses, report (create research agent) | research room, sources with monitoring, claims checked against documents, independent review (ASEAO06) | done (`research.py`) |
| Control Hub, mission form, marketplace (AXoR chats) | Simple mode and mission report (REQ-003/004), marketplace (REQ-002), merged 2026-11-06 | done (`mission.py`, `marketplace.py`) |

## 2. Requirements

### EL-R-01 Usage limits move the work to another model

**Status: done (`router.LIMIT_RE`, `router.mark_limited`, `Route.fallback`, `registry.ModelLimitReached`, `max_clones`)**

A run whose error names a plan or quota limit marks
the model `limited_until` its reset (parsed from the message, else 60 minutes); the router skips it and the route's
ordered fallbacks take the work; a usage limit does not degrade an agent; when every real model is limited the
simulator does not stand in, staffing raises `ModelLimitReached` and the goal resumes at the earliest reset; clones per
original are capped (`max_clones`). Configured 2026-11-06: every role falls back to Claude Code (host sign-in) and
Codex CLI; the research room runs on Codex CLI with the Claude models as fallbacks.
Acceptance: `tests/test_model_limits.py`.

### EL-R-02 The research lead's tool loop

**Status: done (`research_agent.py`)**

The research request's model, called
through the router, answers with a JSON plan; the plan is validated against `TOOLS` (`search_knowledge`,
`search_papers`, `fetch_url`), at most 4 tasks per round; the research lead runs them (every fetch through
`research.collect`), feeds the results back, and stops when the model says the evidence suffices or after
`research_tool_rounds` rounds. The documents found join the report's material; each round is kept in `tool_steps`.
`search_papers` asks OpenAlex (arXiv's robots.txt forbids its API and search to crawlers) and stores each result's
title and abstract under its landing page. A CLI model called without a worktree gets an empty scratch folder
(`data/worktrees/_router-<role>`); before that no research report could run on a CLI model. Checked on 2026-11-06 with
a real request on Codex CLI: two planning rounds, three arXiv abstract pages fetched, the report approved.
Acceptance: `tests/test_research_agent.py`.

### EL-R-03 Codex and Claude check each other

**Status: done (`router.resolve_chain` with `avoid_provider`, `router.run`)**

When another family is asked for (the review of a research report, a reviewer of a developer's work), an agent CLI of
another sign-in counts as well as an API model: Codex reviews what Claude wrote and the other way round. For now only
outside the goal loop (the research room's reviews): a code review there must run the tests, which Codex's read-only
sandbox cannot (no writes, not even uv's cache), so `loop._execute` passes `other_cli=False` until goal 93 hands QA
the gate's own output. Codex needed an ACL `u:root:x` on `/home/luca` (0750) to enter the worktrees at all.
Acceptance: `tests/test_model_limits.py::test_another_family_can_be_an_agent_cli`.

### EL-R-04 A recurring failure becomes a proposed rule

**Status: open**

When the RCA finds the same cause (layer and check)
in N goals, the instructor drafts a one-line rule for `.claude/rules/` with the evidence; it is a proposal decided like
the others, and an accepted rule is exported by `catalog`/`gm configure`. Acceptance: a test where three failed goals
with one cause yield one rule proposal citing them, and a fourth does not duplicate it.

### EL-R-05 A syntax check after every edit

**Status: done (`edit_check.py`, `executors.EDIT_CHECK_SETTINGS`)**

A writing Claude Code run gets a PostToolUse hook on Edit/Write/MultiEdit through `--settings`: `python3 -I
edit_check.py` compiles an edited `.py` file in memory (no .pyc) and runs `node --check` on an edited `.js` file (both
parse only, no project code runs; paths outside the working directory are ignored) and returns a syntax error to the
agent with exit code 2, so it corrects the edit before the gate. Checked on 2026-11-06 with a real run: the agent
received the error at once. Acceptance: `tests/test_edit_check.py`.

### EL-R-06 A critic per writing task

**Status: open**

With the setting `task_critic` on, a writing task's diff is reviewed
by a critic of another model family (EL-R-03) before its dependants start; a FAIL returns it to the same agent with
the critic's points, at most twice. Acceptance: a test with a fake executor where the critic fails the first attempt
and passes the second, and the dependent task starts only after that.

### EL-R-07 An archive of candidates

**Status: open**

Experiments record their parent experiment and evaluation log; a new
candidate for the same target may start from the best or a diverse earlier candidate, and up to three candidates may
compete on one suite. Acceptance: a test that a candidate's lineage is recorded and the best of three is the one
proposed.

### EL-R-08 Mutation testing of the gate

**Status: open**

`evaluation` can apply known mutations (remove an assertion, flip a
boundary) to a passing change and checks that the gate then fails; the share of mutations caught is reported per
check. Acceptance: a test where a removed assertion is caught by the quality command.

### EL-R-09 A bridge for the CLI agents

**Status: open (needs the owner's decision: a new MCP tool surface)**

A bundled MCP
server offering `get_task`, `post_progress`, `save_insight` and `request_review` for the running goal only, granted per
agent in Tools & MCP. Acceptance: a test that a granted agent's call reaches the blackboard and an ungranted one is
refused.
