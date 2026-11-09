# Verification phase 2: model routing

Date: 2026-11-08. Question: does each agent use the model assigned to it, and does the backend's own record prove
which provider and model handled each request? Critical rule: the model selected in the UI, the model the router
resolves and the model the provider uses must agree, unless an explicitly configured fallback is used.

## What was missing

Before this phase, a run (`Run`) recorded only the catalog name and executor kind. It did not record the assigned
model, the model ID, the provider, the address, why that model was chosen, or what the provider reported. So the
backend could not prove a routing decision, and editing a model's ID silently rewrote the history of every past run.

## What was added

`Run.trace`, written by every executor for every run (`Executor.run` stamps it; `usage.record` stores it):

| Field | Meaning |
|-------|---------|
| `requested` | The model assigned to the work: the agent's own model (agent card), otherwise its role's route (Models → Routes) |
| `model`, `model_id`, `provider`, `base_url` | The model that actually ran, as configured at run time |
| `route` | Why this model: `agent preference`, `route for <role>`, `fallback for <role>`, `escalation for <role>`, `different family from <provider>`, or the simulator's `fallback (<why each model was skipped>)` |
| `served` | The model the provider itself reported: the response's `model` for API models (a crewai transport interceptor), the assistant messages' `model` for Claude Code. Empty when the provider reports none (Codex, Copilot) |

`router.trace_issues(trace)` applies the critical rule to one run and names each problem it finds:
- a model ran instead of the assigned one without a configured fallback;
- the offline simulator stood in;
- the provider answered as a different model.

The trace and its issues appear in the agent card's run detail (Runs tab) and in the goal trace.

## Acceptance criteria

`tests/test_phase2_routing.py`: two local OpenAI-compatible stubs (Model A, Model B), requests through
`loop._execute`, the path every task takes.

| Test | Pass condition | Evidence | Result |
|------|----------------|----------|--------|
| 2.1 Assignments: research → A, developer → B, QA → A | Each run's trace: requested = ran = assigned; model ID and address are A's or B's; served = the stub's model; no issues. Stub A received exactly the research and QA requests, stub B only the developer's | `test_each_agent_runs_on_its_assigned_model_and_the_trace_proves_it` | PASS |
| 2.2 The 125 × 48 request | Answer 6000. Model B's reply claims to be "gpt-5 / openai"; the trace shows `openai/model-b` ran and the provider answered as `model-b` | same test | PASS |
| 2.3 The backend record, not the agent's claim | The trace is on `Run`, the run-detail API and the goal trace | `test_the_run_detail_shows_the_trace` | ADDED |
| A role route as the assignment | An agent without its own model: requested = the route's model, `route for qa` | `test_a_role_route_is_the_assignment_when_the_agent_has_none` | PASS |
| Assigned model disabled | Skipped without a request. The fallback runs it, and the trace says `requested B, ran A, fallback for developer` (no issue) | `test_a_disabled_model_moves_the_work_to_the_configured_fallback` | PASS |
| Invalid credentials | Run 1 on B fails (`AuthenticationError`), then run 2 on fallback A succeeds; both are recorded | `test_invalid_credentials_fail_on_the_model_then_the_fallback_takes_it` | PASS |
| Assigned model unavailable | Connection refused, retried `retry_max_attempts` times on B, then fallback A | `test_an_unreachable_model_is_retried_then_the_fallback_takes_it` | PASS |
| Fallback configured | As the three rows above | | PASS |
| No fallback, model fails | The work fails on B; no other model, no simulator | `test_without_a_fallback_a_failing_model_fails_the_work` | PASS |
| No fallback, model disabled | The **offline simulator** produces the output; the trace records `provider: offline, requested: Model B`, and `trace_issues` flags it | `test_without_a_fallback_a_disabled_model_leaves_the_simulator_and_the_trace_says_so` | FINDING V7 |
| Two agents, different models, simultaneously | Started together (barrier). Each stub received only its own model; each trace matches its agent | `test_two_agents_on_different_models_at_the_same_time` | PASS |
| Provider substitutes a model | Stub A answers as `model-z`; the issue "the provider answered as model-z, not model-a" is raised | `test_a_provider_answering_as_another_model_is_flagged` | ADDED |

Both stubs are OpenAI-compatible servers on this host, so both runs show `provider: local`. They are told apart by
`model_id` and `base_url`; the live runs below cover two real providers.

Mutation check: with `requested` dropped from the router, 6 of the 12 tests fail.

## Live run (this server, 2026-11-08)

The 125 × 48 request through `loop._execute` on the live agents. No assignment was changed. The last row's
assignment exists only in memory, to match the plan's "QA → Model A".

| Agent | Assigned | Ran | Provider | Provider answered as | Why | Agent's own claim |
|-------|----------|-----|----------|----------------------|-----|-------------------|
| Research Lead | Codex CLI (route) | Codex CLI | openai | not reported | route for research | "Model identifier: unavailable, OpenAI" |
| Backend Developer | claude_37 (route) | Claude Code | anthropic | claude-opus-5-5 | fallback for developer (claude_37 is at its weekly limit) | "claude-opus-5-5, Anthropic" |
| QA Engineer | Claude Code (own) | Claude Code | anthropic | claude-opus-5-5 | agent preference | "claude-opus-5-5, anthropic" |
| QA Engineer (Codex in memory) | Codex CLI | Codex CLI | openai | not reported | agent preference | "GPT-6 (Codex), OpenAI" |

All four answered 6000, and `trace_issues` is empty for each. Runs 1993–1996 in the live DB, purpose `verify`.

## Findings

- **V7: the offline simulator stands in when no model is usable.** If the only assigned model is disabled or
  has no key, and no fallback is configured, the simulator produces placeholder output in its place. CLAUDE.md
  states this as the design ("only stands in when no real model is usable"). The phase 2 rule allows only
  configured fallbacks, so the two conflict. The trace now records the stand-in and `trace_issues` flags it. **The
  owner must decide** whether a real assignment without a usable model should fail instead. Not changed here.
- **V8: Codex and Copilot runs do not report their model.** `codex exec --json` carries no model, so these runs'
  `served` is empty. Codex's model is its account's default, so `model_id` is empty too. The self-reports were
  "unavailable" and "GPT-6"; Codex's own session files name `gpt-6.1-sol`. Proof stops at "Codex CLI ran".
- **V9: two OpenAI-compatible servers share one provider name.** Every model with a base URL reports
  `provider: local`. The trace keeps `base_url` to tell them apart.
