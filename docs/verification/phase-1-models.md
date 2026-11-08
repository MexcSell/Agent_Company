# Verification phase 1: model registration

Date: 2026-11-08. Question: can a model be registered, stored safely, reached, refused when it is wrong or disabled,
and removed? The platform must never report a model as active only because its configuration was saved.

Phases: 1 models, 2 routing, 3 single agents, 4 agent communication, 5 projects, 6 task execution, 7 quality,
8 evolution. Each phase starts from the smallest component.

## How it was tested

- **Automated:** `tests/test_phase1_models.py`, 18 cases. Each one runs the real path (router → `LLMExecutor` →
  crewai/OpenAI client → HTTP) against a local OpenAI-compatible stub. The stub checks the key, the path and the
  model name, and can answer 429 (per-minute), 429 (`insufficient_quota`) or too slowly. Run:
  `uv run pytest -q tests/test_phase1_models.py`.
- **Live:** the Models page's Test (`router.test_model`) on every model in this server's catalog.

## Acceptance criteria

| Test | Pass condition | Evidence | Result |
|------|----------------|----------|--------|
| Model registration | Saved with all fields; duplicate name, unknown kind, missing model id, bad URL and bad key are refused | `test_registration_stores_the_record_with_the_key_encrypted` | PASS |
| Database record | Row has name, kind, model id, base URL and enabled; key only in `api_key_enc`, encrypted; only a hint (`sk-...WXYZ`) leaves the server | same test, and `test_the_api_registers_tests_and_removes_with_audit` (key absent from `/api/models`, `/api/models/info` and the audit log) | PASS |
| Saved is not tested | A new model has no Test status until a Test runs | `status == {}` after registration | PASS |
| Valid key | Request reaches the provider; reply, tokens, time and `status.ok` are recorded | `test_a_valid_model_answers_and_the_result_is_recorded` | PASS |
| Invalid key | Test fails with `AuthenticationError`; the key is not in the error | `test_a_wrong_key_endpoint_or_model_fails_the_test` | PASS |
| Incorrect endpoint | Nothing listening → `ConnectionError`; wrong path on the right host → failure | same, and `test_a_wrong_path_on_the_right_host_fails_the_test` | PASS |
| Unsupported model name | Test fails with "not found" | same parametrised test | PASS |
| Disabled model | Test refused with no request sent; routing skips it ("Stub: disabled") | `test_a_disabled_model_is_refused_and_never_routed` | PASS |
| Provider timeout | Test fails with "timed out" | `test_a_timeout_is_identified` | PASS |
| Rate limit | A per-minute 429 fails the Test but does not bench the model. A used-up quota benches it until its reset, and a later passing Test lifts that | `test_a_per_minute_rate_limit_is_transient_but_a_quota_benches_the_model`, `test_limit_messages_are_told_apart` | FIXED (V1) |
| Model removal | Gone from the list; no route, escalation or fallback names it; not resolvable; cannot be routed to again; Test → 404 | `test_a_removed_model_is_no_longer_selectable_anywhere` | FIXED (V2) |
| Permissions and audit | A user without `manage_models` cannot add or test models; add, Test and delete are audited | `test_the_api_registers_tests_and_removes_with_audit` | FIXED (V3) |

## Live catalog (this server, 2026-11-08)

| Model | Type | Test | Notes |
|-------|------|------|-------|
| Claude Code | CLI | PASS (OK, 3.1 s) | |
| Codex CLI | CLI | PASS (OK, 9.1 s) | ChatGPT plan plus |
| claude_37 | CLI | FAIL: weekly limit, resets Nov 11 7pm Europe/Berlin | Benched until then. This is the route of every role except research; their fallbacks (Claude Code, Codex CLI) take the work |
| Claude Fable/Opus/Sonnet/Haiku 5.5, OpenRouter Free router | API | not run | No key on this host (`ANTHROPIC_API_KEY`/`OPENROUTER_API_KEY` not set, no key of their own); shown as unavailable (V6) |
| Offline simulator | offline | n/a | Last fallback; cannot be disabled or deleted |

## Defects found and decisions

Full list: `docs/known-issues.md`, V1–V6.

- **V1:** a per-minute 429 was read as a plan limit. Decision: only quota and plan messages bench a model; a
  per-minute 429 stays a transient failure that the loop retries (`loop.TRANSIENT`).
- **V2:** deleting a model left routes escalating to it. Decision: delete clears the escalation, as it already
  cleared the route and fallbacks.
- **V3:** a Test was not audited. Decision: it is now, because it spends money at the provider.
- **V4:** agents that preferred a deleted model still name it. Left open: resolution falls back to the route
  and says why.
- **V5:** a Test from the CLI without the DB settings recorded false refusals. That happened once during this
  verification. Repaired: the CLI models were tested again with the settings loaded, and the API models' status
  was set back to empty.

## Phase 2 starts from here

Phase 2 (routing): does each role's agent resolve to its assigned model, and does the run actually go to that
model? The same stub can record which model a run reached.
