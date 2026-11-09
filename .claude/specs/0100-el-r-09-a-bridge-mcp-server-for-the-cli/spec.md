# EL-R-09 spec: a bridge MCP server for the CLI agents

The spec follows below. The loop saves an architect's text answer as `.claude/specs/0100-el-r-09-a-bridge-mcp-server-for-the-cli/spec.md`, so I didn't write any files. Code claims are labelled FACT (read in this worktree) or INFERENCE.

## 0. Ground truth from the code

- **FACT** `mcp.claude_config(agent_id)` (mcp.py:596) builds `{"mcpServers": {slug: {"type": "stdio", "command", "args", "env"}}}` from the servers the agent may use now (`for_agent`: enabled, status `ok`, `everyone` or granted, allowed tools only). Bundled args are resolved to `BUNDLED / arg`.
- **FACT** `loop._execute` (loop.py:280) has `agent`, `goal_id` and `task_id` together. At :331 it caches `req.data["mcp"] = mcp.claude_config(agent.id)` only `if "mcp" not in req.data`. The critic loop (loop.py ~1010) calls `_execute` up to 3 times with the **same** `req`.
- **FACT** `executors.gate` (executors.py:366-369) grants `mcp` only to `claude_code` runs at autonomy G3 or higher. Below that the config is dropped (`"mcp: autonomy G{n}"`).
- **FACT** `ClaudeCodeExecutor._run` (executors.py:678-694) writes the config to a `tempfile.mkstemp` file (system temp dir, mode 0600, never the worktree). It passes `--mcp-config <file> --strict-mcp-config` and unlinks the file in `finally`.
- **FACT** `Run` rows are written by `usage.record` *after* a run (loop.py:342). There is no run id before the run, so a token binds to (goal, task, agent), not to a `Run.id`.
- **FACT** `_stdio.tool()` hard-codes `"readOnlyHint": True`. `mcp.test` uses `readOnlyHint` and the `allow` argument ("all" | "read_only") to decide which new tools start allowed. `digest()` hashes the annotations.
- **FACT** `blackboard.write` refuses any section outside `SECTIONS = (objective, planning, research, engineering, qa, reports, messages)` (column `String(16)`).
  - `put` writes a new version on top of the latest one.
  - `inbox(goal, role, agent)` delivers `messages` entries whose `meta.to == role`.
  - `extract_messages` writes `meta = {from, to, kind, delivered: [], asked: 1}`.
- **FACT** A `Proposal` (db.py:578) becomes a new goal or project when a person approves it (`projects.decide`).
- **FACT** `A2AClient` keeps only `auth._hash(token)` (SHA-256), and the token is `secrets.token_urlsafe(32)`.
- **FACT** `api._wrong_scheme` fires only when `X-Forwarded-Proto` is present. nginx sets `X-Forwarded-For`/`-Proto` (transport.py:328-329), so a direct loopback POST from the shim passes the scheme gate.
- **FACT** `McpCall.server_id` is a plain `Integer`, not a foreign key. `Goal.allowed_paths` is a list of globs. A task's acceptance list is `Task.contract["acceptance"]`.

## 1. Files, functions, route, table

| File | Change |
|---|---|
| `src/agent_company/db.py` | new table `BridgeToken` (below); `create_all` creates it (it is a new table, so `_add_missing_columns` is not involved) |
| `src/agent_company/bridge.py` | **new** service module: tokens, call handling, attach to a run |
| `src/agent_company/mcp_servers/bridge.py` | **new** stdlib-only shim (imports `_stdio` only) |
| `src/agent_company/mcp_servers/_stdio.py` | `tool(..., read_only=True)` keyword |
| `src/agent_company/mcp.py` | `CATALOG` entry `bridge`; `add_from_catalog("bridge")` refuses a second copy |
| `src/agent_company/api.py` | `POST /api/bridge/call` |
| `src/agent_company/loop.py` | `_execute`: `bridge.attach` before the chain loop, `bridge.revoke` in `finally` |
| `tests/test_mcp_bridge.py` | **new** (§6) |
| `docs/threat-model.md`, `docs/requirements/EVOLUTION-LOOPS.md`, `CLAUDE.md` | threat-model row (§7), EL-R-09 status → done, one layout line for `bridge.py` |

### 1.1 Table

```python
class BridgeToken(Base):
    """A per-run bearer token of the bridge MCP server (EL-R-09). Only its SHA-256 is kept."""
    __tablename__ = "bridge_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    goal_id: Mapped[int] = mapped_column(Integer, index=True)
    task_id: Mapped[int] = mapped_column(Integer)
    agent_id: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[float] = mapped_column(Float)            # epoch seconds (avoids SQLite's zone-less datetimes)
    revoked_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    calls: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
```

### 1.2 `bridge.py` (service)

Constants:
- `TTL = 3 * 3600`: a fallback only. The claude_code tool contract caps one run at 7200 s; revocation is the primary end.
- `CALLS_PER_TOKEN = 100`
- `INSIGHTS_PER_TASK = 5`
- `REVIEWS_PER_TASK = 2`
- `MAX_BODY = 65536`
- `TOOLS = ("get_task", "post_progress", "save_insight", "request_review")`

Functions:
- `server_row(s) -> McpServer | None`: the lowest-id `McpServer` with `transport == "stdio"` and `args == ["bridge.py"]`. The server is identified by its args, not its slug, so a custom server that took the slug `bridge` is never mistaken for it.
- `api_url() -> str | None`:
  - `f"http://127.0.0.1:{settings().port}"` when `settings().host` is in `{"127.0.0.1", "localhost", "0.0.0.0", "::", ""}`.
  - Otherwise `None`: the API is not reachable on loopback, so the bridge is not attached.
  - `settings().port` is the uvicorn bind (4910 behind nginx), not `public_port`.
- `issue(goal_id, task_id, agent_id, ttl=TTL) -> str`:
  - The token is `"acb_" + secrets.token_urlsafe(32)`. Only `auth._hash(token)` is stored, with `expires_at = time.time() + ttl`.
  - In the same session, delete rows whose `expires_at < time.time() - 86400`.
  - Return the plain token. It is never logged.
- `revoke(token) -> None`: sets `revoked_at = time.time()` (idempotent; unknown tokens are ignored).
- `attach(req, purpose, goal_id, task_id, agent_id) -> str | None`: edits `req.data["mcp"]` (the `(config, allowed_tools)` tuple).
  1. Find the bridge row's slug. If the config has no entry for that slug, return `None`.
  2. If `purpose != "task"`, `task_id is None` or `api_url()` is `None`, **remove** the slug's entry and every `mcp__<slug>__*` name from the allowed list, then return `None`. Without a task there is no scope.
  3. Otherwise build a **new** entry dict, `{**entry, "env": {**entry["env"], "AC_BRIDGE_URL": url, "AC_BRIDGE_TOKEN": token}}`, from a fresh `issue(...)`. Replace it in a copied config dict and return the token.
  4. It is called on every `_execute` call. This overwrites any token cached on a reused `req` (the critic retries), so a revoked token is never reused.
- `handle(authorization: str | None, tool: Any, arguments: Any, forwarded: bool, peer: str | None) -> tuple[int, dict]`: the endpoint's whole logic (§4). Every outcome writes exactly one `McpCall`.

### 1.3 Shim `mcp_servers/bridge.py`

- Stdlib only, started as `python bridge.py`. It holds no DB access and imports nothing from `agent_company`.
- `serve("agent-company-bridge", "1.0", TOOLS, handlers)`. `initialize`, `ping` and `tools/list` are answered locally with **no token and no network**.
- Each handler POSTs `{"tool": name, "arguments": args}` as JSON to `AC_BRIDGE_URL + "/api/bridge/call"` with `Authorization: Bearer <AC_BRIDGE_TOKEN>`. Details:
  - Timeout 30 s; at most 1 MB of the answer is read.
  - It uses `urllib.request.build_opener(urllib.request.ProxyHandler({}))`, so the token never goes through an `HTTP_PROXY`.
- A missing token or URL raises `ToolError("The bridge only works inside an agent's run (no bridge token).")`.
- A URL that is not `http://127.0.0.1:<port>`, `http://localhost:<port>` or `http://[::1]:<port>` raises `ToolError`, so the token never leaves the host.
- Answers:
  - 200: the `text` of the reply.
  - Any other status: `ToolError(body["error"] or f"HTTP {code}")`.
  - Connection errors: `ToolError("Couldn't reach the company API: …")`.
- Tool definitions (`goal_id`/`task_id` are optional integers on every tool, described as "must be this run's own; anything else is refused"):

| Tool | Arguments | `read_only` |
|---|---|---|
| `get_task` | `goal_id?`, `task_id?` | **True** |
| `post_progress` | `text` (string, 1-4000 characters), `percent?` (integer 0-100) | False |
| `save_insight` | `title` (1-200), `text` (1-2000), `evidence?` (array of ≤5 strings, each ≤300) | False |
| `request_review` | `summary` (1-1000), `files?` (array of ≤20 strings, each ≤300), `reviewer?` (enum `qa`\|`security`, default `qa`) | False |

### 1.4 `_stdio.tool`

```python
def tool(name, description, props, required=(), title=None, read_only=True):
    return {"name": name, "description": description,
            "annotations": {"readOnlyHint": bool(read_only), **({"title": title} if title else {})}, ...}
```

The default `True` gives byte-identical output for papers.py and database.py, so their `digest()` values don't change and the G-R-15 drift check doesn't fire.

### 1.5 Route

`POST /api/bridge/call`:
- No session dependency; authentication is the bearer token only.
- Reads `await request.body()`. More than `MAX_BODY` bytes → 413. A body that isn't JSON or isn't an object → 400. Both are logged.
- Then `status, out = await asyncio.to_thread(bridge.handle, request.headers.get("authorization"), body.get("tool"), body.get("arguments"), forwarded=bool(request.headers.get("x-forwarded-for") or request.headers.get("x-forwarded-proto")), peer=request.client.host if request.client else None)` and `JSONResponse(out, status_code=status)`.
- A 401 adds `WWW-Authenticate: Bearer realm="bridge"`.
- Replies: success `{"ok": true, "text": "…"}`; refusal `{"ok": false, "error": "refused: …"}`.

### 1.6 Catalog entry

```python
{"key": "bridge", "name": "Company bridge", "transport": "stdio", "command": "python", "args": ["bridge.py"],
 "allow": "all", "fields": [],
 "about": "The company's own tools for a running agent: its task, progress, insights, review requests. "
          "Scoped to the run's goal and task by a per-run token. Bundled; grant it per agent."}
```

- `everyone` stays False: `save_server` defaults it, and the entry never sets it.
- `fields: []`: nothing lands in `mcp_servers.secrets`. The token exists only in the in-memory config and the temp `--mcp-config` file.
- **`allow` is `"all"`.** These are the gates that do the work:
  1. the per-agent grant (the default is no agent),
  2. G3 at the tool gateway,
  3. the per-run token,
  4. the scope checks in §3.

  The write tools reach only the caller's own goal's blackboard. The agent already writes there with `MESSAGE` lines and the loop's own output entry, so they add no authority. With `"read_only"`, three of the four tools would stay unusable until a second, per-server administrator step that protects nothing extra.

  `read_only` on `get_task` is still correct metadata. If an administrator re-tests with `allow="read_only"` or un-ticks the writers, `get_task` alone remains.

## 2. Token lifecycle

1. **Mint.** In `loop._execute`, right after the `claude_config` block (loop.py:331-337): `bridge_token = bridge.attach(req, purpose, goal_id, task_id, agent.id)`. It is minted only for `purpose == "task"`, with a `task_id`, when the agent's config contains the bridge entry. (INFERENCE: that entry exists only if the agent is granted, or `everyone`, and the server is enabled, tested `ok` and has allowed tools.)
2. **Inject.** The token goes only into `mcpServers[<bridge slug>].env` as `AC_BRIDGE_TOKEN`, next to `AC_BRIDGE_URL`. `ClaudeCodeExecutor` writes the config to its mkstemp file in the system temp dir (FACT executors.py:683) and unlinks it after the run. Never written: the worktree, the DB (only the hash), events or `Run.tool_calls` (no tool input carries it).
3. **Gateway.** If `gate` withholds MCP (below G3), the file is never written. The unused token expires or is revoked in step 4.
4. **Revoke.** `try: <the existing for-chain loop> finally: if bridge_token: bridge.revoke(bridge_token)`. One `_execute` call is one run of the task step, so retries and fallback models inside it share the token. The next `_execute` call (critic retry) mints a new one.
5. **Expiry.** A fallback for a crash between mint and revoke: `expires_at = issue time + 3 h`.

## 3. Scope rules (per tool)

Common to every tool:
- `arguments` must be an object whose keys are exactly the tool's declared properties. Unknown keys → 400.
- `goal_id`/`task_id`, when given, must be an `int` (not a `bool`) **equal to the token's**:
  - not an int → 400;
  - not equal → **403** `"refused: goal N is not this run's goal"` (or the same for the task), plus `log_event("security", …, rule="bridge_scope", goal_id=<token goal>, agent_id=…)`.
- Everything else comes from the token's row, never from the arguments.

| Tool | Reads/writes | Exactly |
|---|---|---|
| `get_task` | reads | the token's `Task`: `key`, `title`, `contract["description"]` (≤8000 characters), `contract["acceptance"]`; the token's `Goal`: `title`, `allowed_paths`. Returned as JSON text. No other task, no blackboard, no output of other tasks. |
| `post_progress` | `blackboard.put(goal, SECTION_OF_ROLE.get(task.role, "engineering"), f"progress:{task.key}", text, author_agent_id, task_id, meta={"via": "bridge", "kind": "progress", "percent": p})` | each post is a new version of one key, so the history is kept. The key never collides with the loop's own `contract.key` entry (FACT loop.py:1120). Reply: `"posted <section>/<key> v<N>"`. |
| `save_insight` | `blackboard.put(goal, "reports", f"insight:{task.key}:{n}", f"{title}\n\n{text}" + evidence lines, …, meta={"via": "bridge", "kind": "insight", "title", "evidence"})` | `n` = 1 + the number of existing `insight:{task.key}:` keys. More than `INSIGHTS_PER_TASK` → 429. `reports` is read only by the GM (`ROLE_SECTIONS`), so insights don't flow into other agents' prompts (less cross-agent injection). |
| `request_review` | `blackboard.put(goal, "messages", f"{task.key}:review:{n}", summary + "\nFiles: …", …, meta={"from": task.role, "to": reviewer, "kind": "request", "delivered": [], "asked": 1, "via": "bridge"})` | a **blackboard request, not a Proposal**. `inbox()` delivers it to the reviewer role's next task on the same goal. A Proposal becomes a *new* goal and waits in a person's queue, which is the wrong lifetime for a review within this goal. More than `REVIEWS_PER_TASK` → 429. |

None of the four tools writes to `Proposal`, `Decision`, approvals, budgets, `Agent.permissions`/`autonomy`, `McpGrant`/`McpServer`, or merge/push state. `bridge.py` imports none of `decisions`, `budgets`, `policies`, `projects.decide` or the `loop.approve`/merge paths. A review checks this with grep.

Each call increments `BridgeToken.calls`. More than `CALLS_PER_TOKEN` → 429.

## 4. Refusals at the endpoint (in this order; each one writes `McpCall(ok=False)`)

| # | Condition | HTTP | `error` |
|---|---|---|---|
| 1 | `forwarded` or `peer` not in `{"127.0.0.1", "::1"}` | 403 | `refused: the bridge is reachable on loopback only` |
| 2 | missing or non-Bearer `Authorization` | 401 | `refused: no bridge token` |
| 3 | no row for the token's hash | 401 | `refused: unknown token` |
| 4 | `revoked_at` set | 401 | `refused: token revoked` |
| 5 | `expires_at <= time.time()` | 401 | `refused: token expired` |
| 6 | no bridge row, or `enabled` False | 403 | `refused: the bridge server is not added or turned off` |
| 7 | `tool` not in `TOOLS`, or not `allowed` in the row's `tools` | 403 | `refused: tool X is not allowed on the bridge` |
| 8 | `not row.everyone and agent_id not in grants`, checked **per call**, so taking a grant away mid-run refuses at once | 403 | `refused: this agent has no grant for the bridge` |
| 9 | bad arguments or sizes (§1.3 table) | 400 | `refused: <field> …` |
| 10 | goal/task mismatch | 403 | §3 |
| 11 | call or insight/review budget exhausted | 429 | `refused: … budget of this run reached` |

`McpCall` fields:
- `server_id` = the bridge row's id, or `0` when there is no row.
- `agent_id` = the token's agent, or `None` for #2-#3.
- `actor="bridge"`.
- `tool` = the requested name capped at 128 characters, or `"?"`.
- `duration` and `error[:2000]`.

A success writes `ok=True`, so the call appears in `mcp.recent_calls()` with the others.

Sessions: resolve and count the token in one session, write the blackboard via `blackboard.put` (its own sessions), then log the `McpCall` in a final session. They are never nested (CLAUDE.md, one writer).

## 5. Out of scope here

The catalog key, `everyone=False`, the `read_only` flag and `allow` are covered in §1.4 and §1.6. Out of scope: an `mcp.call()` from the dashboard can't supply a token, so the shim answers bridge tools there with the `ToolError` from §1.3. That is intended.

## 6. Tests: `tests/test_mcp_bridge.py` (all offline)

Fixture:
- Two goals and one task each, created directly via `session()` with the required columns, as other tests do (task `key="t1"`, `role="developer"`, `contract={"description": "…", "acceptance": [{"type": "min_length", "value": 20}]}`).
- Two agents from `registry.list_agents()`.
- `srv = mcp.add_from_catalog("bridge", {}, "test")`. This really starts the shim over `_Stdio` with no token, which is itself the proof that initialize and tools/list need none. Assert `srv["status"] == "ok"`, 4 tools, all allowed.
- `mcp.set_grants(srv["id"], [agent_a])`.
- Client: `TestClient(create_app(start_background=False), client=("127.0.0.1", 50000))`.

1. `test_granted_post_progress_reaches_its_goal_blackboard_and_is_logged`: token for (g1, t1, A) → 200.
   - `blackboard.read(g1)` has `engineering/progress:t1` with the text, `author_agent_id == A` and `task_id == t1`.
   - `blackboard.read(g2)` has none of it.
   - An `McpCall` row exists with `ok=True`, `tool="post_progress"`, `agent_id=A`, `server_id=srv["id"]`.
2. `test_a_call_naming_another_goal_or_task_is_refused`: `goal_id=g2` → 403, then `task_id=<g2's task>` → 403. No `progress:` entry on g1 or g2. Two `McpCall(ok=False)` rows whose error contains "goal" or "task".
3. `test_an_ungranted_agent_is_refused_until_granted`: token for (g1, t1, B) → 403 with `McpCall(ok=False, agent_id=B)`. After `set_grants(srv_id, [], everyone=True)` → 200. After `set_grants(srv_id, [], everyone=False)` with the same token → 403 (grant checked per call).
4. `test_revoked_expired_unknown_and_missing_tokens_are_refused`: `bridge.revoke(tok)` → 401 "revoked"; `issue(..., ttl=-1)` → 401 "expired"; random token → 401; no header → 401. Each writes `McpCall(ok=False)`; `agent_id is None` for the last two.
5. `test_the_shim_lists_its_tools_without_a_token`:
   - Run `subprocess.run([sys.executable, "-I", str(mcp.BUNDLED / "bridge.py")], input=<initialize line>\n<tools/list line>\n<tools/call post_progress line>\n, env={"PATH": os.environ["PATH"]}, capture_output=True, text=True, timeout=30)`.
   - initialize returns `serverInfo.name == "agent-company-bridge"`.
   - tools/list returns the 4 tools, `readOnlyHint` True **only** on `get_task`.
   - tools/call returns `isError` with "token" in the text.
6. `test_disabled_server_and_unallowed_tool_are_refused`: set `row.enabled = False` in a session → 403. Re-enable, then `mcp.set_tools(srv_id, ["get_task"])`: `post_progress` → 403 and `get_task` → 200.
7. `test_get_task_returns_only_the_token_task`: the text has t1's title, acceptance and g1's title, and none of g2's.
8. `test_forwarded_and_oversized_calls_are_refused`: header `X-Forwarded-For: 1.2.3.4` → 403; `text` of 4001 characters → 400; unknown argument key → 400. Nothing is written.
9. `test_attach_mints_per_run_and_drops_the_bridge_outside_tasks`:
   - `req = executors.Request(..., data={"mcp": mcp.claude_config(A)})`.
   - `bridge.attach(req, "task", g1, t1, A)` sets `env["AC_BRIDGE_TOKEN"]`; a second call gives another token.
   - `attach(..., "critic", …)` removes the entry and its `mcp__<slug>__*` names.
   - `revoke` then a call → 401.
   - No file under the goal's worktree contains the token.

Command for developers and QA: `uv run pytest -q tests/test_mcp_bridge.py tests/test_mcp_everyone.py tests/test_mcp_drift.py tests/test_gateway.py`. Read the summary line; there is no xdist.

## 7. Threat-model row (`docs/threat-model.md`, data-flow table)

| CLI agent run → company bridge (EL-R-09) | a Claude Code run whose agent an administrator granted the bundled `bridge` MCP server calls `get_task`, `post_progress`, `save_insight`, `request_review`; the stdlib shim (`mcp_servers/bridge.py`) forwards each call with a per-run bearer token to `POST /api/bridge/call` on loopback | not granted to any agent by default (`everyone=False`), granted per agent in Tools & MCP and checked per call, G3 at the tool gateway; the token is random, only its SHA-256 is stored (`bridge_tokens`), bound to (goal, task, agent), minted per `_execute` call for task runs only, placed only in the bridge entry's env in the temporary 0600 `--mcp-config` outside the worktree, revoked when the run ends, expiring after 3 h; every goal or task argument must equal the token's (a mismatch is refused and raises a security event); writes go only to that goal's blackboard (`<role section>/progress:<task>`, `reports/insight:<task>:<n>`, `messages` review requests), never to approvals, decisions, proposals, budgets, permissions, grants or merges; inputs size-bounded, call budgets per token and task; the endpoint refuses forwarded (nginx) requests and non-loopback peers; the shim refuses non-loopback URLs and ignores proxies; every call, refused ones included, is an `McpCall`. Known gap: the agent can read its own token (same uid as the shim), which gives no more than its own tools | `test_mcp_bridge.py` |

## 8. Open items

- **Codex wiring** (`CodexExecutor` gets no MCP config today), per the goal's decision.
- Promoting bridge insights from `reports` into knowledge items (`learning`, screened) is not done here.
- An emergency stop doesn't revoke live tokens. INFERENCE: a stop kills the process (`executors.terminate`), so the shim dies with it.

FINDING: `loop._execute` caches `req.data["mcp"]` on the Request, and the critic loop reuses that `req`. Any per-run secret in the config must be re-minted on every `_execute` call (handled by `bridge.attach`, §1.2).
FINDING: `mcp.save_server` takes the slug from the name, so a custom server named "Bridge" could take the slug `bridge`. The bridge is therefore identified by `transport == "stdio"` and `args == ["bridge.py"]`, never by slug.
NEXT: implement_bridge builds §1.1-1.5, §3, §4 and tests 1-8. wire_runs builds §1.6, §2, test 9, the §7 row, the EL-R-09 status and the CLAUDE.md line.

MESSAGE handoff to developer: the full contract is in `.claude/specs/0100-el-r-09-a-bridge-mcp-server-for-the-cli/spec.md`. Keep the `_stdio.tool` default `read_only=True` so the papers and database digests don't change.
MESSAGE request to qa: check §4's refusal order and that every refusal writes an `McpCall`, by running only the §6 command, to stay within the task budget.

PROPOSAL: Wire the bridge into Codex runs :: EL-R-09 names Codex first; `CodexExecutor` needs an MCP config path and the same per-run token.
