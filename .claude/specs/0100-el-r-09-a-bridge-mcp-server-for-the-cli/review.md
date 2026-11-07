# Review (iteration 2)

## Review the bridge against EL-R-09 and the spec: scope, grants, token lifecycle, logging (qa, done)

# QA review: the EL-R-09 bridge (commits ef03b19 and 89f5442)

The bridge matches EL-R-09 and the spec for Claude Code runs. I found no defect against the scope or security rules; six small comments are listed below. I changed no files. The test command you asked for gives **`39 passed in 11.14s`**.

## Logic verification

This is a new feature, so the code builds the requirement directly rather than working around a symptom.

**`bridge.handle` and `bridge._resolve`** (`src/agent_company/bridge.py:245`, `:163`)
- **Normal case:**
  - The shim `mcp_servers/bridge.py` reads `AC_BRIDGE_URL` and `AC_BRIDGE_TOKEN`, accepts only a loopback URL and bypasses any proxy. It sends `Authorization: Bearer <token>` to `POST /api/bridge/call`.
  - In `api.py`, the endpoint `bridge_call` checks the body size and that the body is a JSON object, notes any `x-forwarded-*` header, and calls `handle` with the peer's host.
  - `handle` checks the peer is loopback, then `_resolve` checks the token, the server, the tool, the grant, the arguments, the goal and task, and the budgets.
  - The goal and task come from the token row, never from the arguments. `_run` then calls `blackboard.put(c["goal_id"], <role section>, "progress:<task key>", …)`.
  - `_log` (`bridge.py:274`) writes exactly one `McpCall` (`actor="bridge"`, `ok=True`). `test_granted_post_progress…` asserts this.
- **Edge case:** an argument `goal_id` or `task_id` that differs from the token's gets a 403 (`bridge.py:192-195`) with `scope=True`.
  - That writes the `McpCall` and a `bridge_scope` security event in the same session (`s=s`).
  - The test checks both refusals are logged and that nothing was written to g1 or g2.
  - Passing the run's own ids is accepted.
- **Error case:** each of these is refused, and each goes through `_log`:
  - forwarded request or non-loopback peer: 403, the first check at `:253`
  - missing token or wrong scheme: 401
  - unknown token: 401
  - revoked token: 401
  - expired token: 401
  - server disabled or missing: 403
  - tool not allowed, or not one of the four: 403. `approve_goal` is tested.
  - no grant: 403, checked on every call. Taking the grant away mid-run is tested.
  - bad arguments: 400
  - oversized or non-object body: 413 or 400, through `bridge.refuse`
- **Refusal order:** it matches spec §4 rows 1-11 exactly (`bridge.py:253`, `:167-209`).
- **Rollback:** a refusal inside `_resolve` rolls the session back, so `calls` is only incremented on success.
- **Server errors:** an exception becomes a 500. The event names only the exception type.

**`bridge.attach` and `bridge.revoke`** (`bridge.py:71-101`)
- **Normal case:** for a task run with the bridge entry in the config and a loopback API, a token is issued. Only `sha256` is stored (`:63`). The plain token is put only into that server entry's `env`.
- **Edge cases:**
  - Any other purpose (plan, critic, configure, research, lesson), a missing task or goal, or a non-loopback host removes the entry and its `mcp__<slug>__*` tools.
  - The critic builds its own `Request(purpose="critic")` (`loop.py:1193`), so it never carries a token.
  - The bridge entry is looked up by command line (`args == ["bridge.py"]`), so a custom server that took the slug `bridge` does not count.
- **Error case:** an agent without a grant has no entry, so it gets no token. `revoke` is safe to call twice and ignores unknown tokens.

**`loop._execute`** (`loop.py:345-351`)
- Each pass of `while True` (a retry or a fallback model) calls `attach` again, which replaces any earlier token.
- The executor run sits in `try/finally`, and the `finally` revokes the token.
- `test_a_task_run_gets_a_live_token…` shows:
  - the token works during the run (200)
  - it gets 401 after `_execute` returns
  - the next run gets a different token
- This is stricter than spec §2 step 4, which shares one token per `_execute` call. It matches the task statement.

## Checklist

- **Contract:**
  - get_task, post_progress, save_insight and request_review are present.
  - The scope comes from the token.
  - Writes go only to the blackboard. `bridge.py` imports only blackboard, auth, config and db, and `blackboard.py` has no paths to decisions, proposals or approvals.
  - Nothing writes approvals, decisions, budgets, permissions, grants or merges.
  - The acceptance tests are present and pass.
- **Token storage:**
  - Only the hash is stored (`BridgeToken.token_hash`).
  - The config file is a `tempfile.mkstemp` file in the system temp directory (`executors.py:683`), never the worktree.
  - `req.data` is never written to the Run row, events or logs. A grep finds only the `resume`, `feedback`, `approved` and `max_turns` keys being read.
  - `test_revoked…` asserts the token never appears in `McpCall.error`.
- **No session cookie:** the endpoint has no `Depends(current_user)`; only the `Authorization` header is read. `_wrong_scheme` only fires when `x-forwarded-proto` is set, so the shim's plain loopback POST is not refused with 421. Requests that came through nginx carry `X-Forwarded-For` (`transport.py:328`) and are refused.
- **Default grant:** `McpServer.everyone` defaults to False (`db.py:1276`). The test fixture asserts `everyone is False` after `add_from_catalog("bridge")`, and nothing seeds a grant.
- **Read-only hints:**
  - `_stdio.tool` defaults to `read_only=True`.
  - `papers.py` and `database.py` don't pass `read_only`, so their tools still say `readOnlyHint: true`.
  - Only the three writing bridge tools set `read_only=False`; a grep across `mcp_servers/` confirms it.
  - `test_the_shim_lists…` asserts only `get_task` is read-only.
- **Gateway:** `executors.py:366-369` is unchanged. MCP is still only for `claude_code` at G3 and above.
- **Docs:**
  - The threat-model row is present after the A2A row (`docs/threat-model.md:29`).
  - EL-R-09 says "done for Claude Code … Codex wiring open (decision #94)" in both the summary table (`EVOLUTION-LOOPS.md:45`) and the section.
  - The CLAUDE.md layout line is missing; see finding 5.
- **Performance:** one small DB row per attempt, and rows expired for over a day are deleted when a new token is issued.

## Findings

FINDING: tests/test_mcp_bridge.py:159 | The non-loopback refusal is only tested through `X-Forwarded-For`. The plain non-loopback peer case (`bridge.py:253`, `peer not in LOOPBACK`) has no test, so a regression there would go unnoticed. | Add one call from a `TestClient(..., client=("10.0.0.5", 1))` and assert 403 plus an `McpCall(ok=False)`. (low)

FINDING: tests/test_mcp_bridge.py (none) | No test sends a valid session cookie without a bearer token. "The endpoint never accepts the session cookie" is true by construction (`api.py` `bridge_call` has no `Depends`), but nothing would catch a regression. | Add a test where a signed-in client posts without `Authorization` and gets 401. (low)

FINDING: src/agent_company/loop.py:346 | Unlike `mcp.claude_config` at `loop.py:331-336`, `bridge.attach` has no `try/except`. A DB error in `issue` aborts the attempt instead of running it without the bridge. | Wrap it so a failure drops the bridge entry and logs an event. (low)

FINDING: src/agent_company/loop.py:346 | A token row is issued for attempts that can never use it: a fallback to Codex or an API model, or a claude_code agent below G3 whose MCP the gateway drops. It is revoked in `finally`, so nothing leaks; it only adds DB rows. | Only attach when `choice.kind == "claude_code"`. (low)

FINDING: CLAUDE.md:Layout, src/agent_company/mcp.py add_from_catalog | `bridge.py` has no layout line, and the bundled bridge can be added twice. With two copies, `server_row` uses the lowest id, so a grant on the second copy gives a bridge with no token. It fails closed (the shim says "no bridge token"), but an administrator won't see why. | Add the layout line, and refuse a second `add_from_catalog("bridge")`. The developer couldn't do this because the files were outside the task's allowed paths. (low)

FINDING: src/agent_company/bridge.py:202-209 | The insight and review budgets are counted and then written in separate sessions. Two calls at the same moment could both pass and go one over `INSIGHTS_PER_TASK` or `REVIEWS_PER_TASK`. This stays inside the goal and the per-token call cap (100). | Use a unique-key insert, or count inside the write. (low)

Not a finding, just noted: `request_review` writes a `messages` request on the blackboard, not a proposal. That follows spec §3, and writing to only one of the two allowed places stays within scope. Codex runs are not wired yet; that is recorded as open (decision #94), as the task allows.

NEXT: Add the two refusal tests (a bare non-loopback peer, and a session cookie without a token).
NEXT: Add the CLAUDE.md layout line and the guard against adding the bridge twice in a task allowed to change `mcp.py` and `CLAUDE.md`.

MESSAGE done to developer: The bridge passes review against EL-R-09 and spec §4. The open items are the low findings above: two refusal tests, `try/except` around `bridge.attach` in `loop.py:346`, and the guard against adding the bridge twice.

PROPOSAL: Wire the bridge into Codex runs :: EL-R-09 names Codex first, and `CodexExecutor` still gets no MCP config or per-run token (decision #94).

OUTCOME: APPROVED_WITH_COMMENTS
VERDICT: PASS
