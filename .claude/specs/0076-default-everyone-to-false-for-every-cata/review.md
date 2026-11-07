# Review (iteration 1)

## Review the least-privilege default for MCP servers (qa, done)

I found no blocking defect. I'd approve with one comment, but I could not run any of the tests myself: this session's permissions refused `uv run pytest` three times (two command variants) and also refused `node --check`. Everything below comes from reading the code. The test results quoted are the developer's report, not runs of mine.

## Logic verification

**Normal case (catalog server → ungranted agent → granted agent)**
- `POST /api/mcp/catalog/{key}` (`api.py:3567`) calls `mcp.add_from_catalog(key, body.values, …)`.
- `add_from_catalog` (`mcp.py:380-382`) builds `data` with name, slug, description, transport, url, command, args, env and headers. It never adds an `everyone` key.
- `save_server(data, actor)` (`mcp.py:355`) creates `McpServer(name=…, slug=…, created_by=…)`. Because `"everyone" not in data`, the guard at `mcp.py:367` skips it, so the row takes the column default, now `default=False` (`db.py:1270`). This was the root cause: before, that default was True.
- `test(..., allow=c["allow"])` (`mcp.py:396`) only sets tools and status. It never touches `everyone`.
- Reading it back: `claude_config(agent_id)` (`mcp.py:596`) iterates `for_agent`, whose filter is `enabled and status == "ok" and (everyone or agent_id in grants)` (`mcp.py:589`). With `everyone=False` and no grant, the server is left out, so the executor's `--mcp-config` (`executors.py:360`/`:649`, filled by `loop.py:334`) does not include it.
- After `set_grants(sid, [a])` (`mcp.py:532-545`), an `McpGrant` row exists, `agent_id in grants` is true, and the server appears for agent a only.
- Tests (b) and (c) cover this path. `deepwiki` has `"fields": []` (`mcp.py:268`), so the catalog test needs no values.
- The fix is at the source (the column default), not a symptom patch: every new row now starts closed.

**Edge cases**
- **Existing row with `everyone=True`, edited without the key:**
  - At the `save_server` level it stays True, because `"everyone" in data` is false. Test (f) covers this.
  - Through the API it does not. `PUT /api/mcp/servers/{id}` (`api.py:3555`) passes `body.model_dump()`, which always includes `everyone`. A body that omits it now sends `everyone=False` and turns an open server into a closed one.
  - Before this change the same omission sent True, which could open a closed server, so the new behaviour is safer and not a regression.
  - The only client, `agents.js:382`, always sends the checkbox value, so the dashboard is not affected. This is the FINDING below.
- **API body that omits `everyone`:** `McpServerBody.everyone: bool = False` (`api.py:457`), so the POST creates a closed server. Test (e) covers this.
- **UI checkbox:** `${s?.everyone === true ? "checked" : ""}` (`agents.js:374`):
  - new server (`s = null`): unchecked;
  - editing a stored True: checked;
  - editing a stored False: unchecked.

  The save handler sends the checkbox state, so editing keeps the stored value. The grants panel (`agents.js:357`) also sends its own checkbox explicitly.

**Error case (other creation paths)**
- Grep for `McpServer(` in `src/`: only `mcp.py:355`.
- `save_server(` callers: `api.py:3548`, `api.py:3555` and `mcp.py:394` (`add_from_catalog`).
- `add_from_catalog(` caller: `api.py:3568` only.
- The `gm mcp` toolbox command (`gm.py:255-257`) only lists servers. No CLI command in `__main__` creates one.
- `marketplace.py:67` lists `mcp` in `NEVER_ON_IMPORT`.
- No path still sets `everyone=True` implicitly.
- `mcp.call`: the grant check is at `mcp.py:555`, not 553 (553 is the tool-allowed check). The line `if agent_id is not None and not server["everyone"] and agent_id not in server["grants"]: raise PermissionError` now refuses an agent calling a new server without a grant. Calls from the dashboard (`agent_id=None`) are unaffected, which is correct.

## Checklist
- **Contract:**
  - `db.py:1270` default=False ✔
  - `api.py:457` `bool = False` ✔
  - docstring `mcp.py:16-17` ✔
  - `agents.js:374` ✔
  - threat-model row ✔
  - existing rows not migrated ✔
  - tests (a)–(f) present ✔
  - Steps 1–8 of the spec all show in the diff.
- **Interfaces:** No signatures changed. `server_dict` still returns `everyone`.
- **Error handling:** Unchanged. A closed server fails safe at `mcp.py:555`.
- **Edge cases:** All covered except the PUT-without-key case (see FINDING).
- **Tests:**
  - The test file is offline and uses a FakeSession.
  - No existing test relied on the old default: `test_company_config.py:29` already passes `everyone: False`, and `test_mcp_drift.py` never checks `everyone`. `test_gateway.py` does not mention `everyone` or `save_server`.
  - Missing: an API-level test for PUT without `everyone`.
- **Dependencies:** None added.
- **Security:** Least privilege improves. New and catalog servers reach no agent until an administrator grants it. All endpoints are still admin-only and audited.
- **Performance:** No effect.
- **Docs:** `threat-model.md` "Data exfiltration" row updated, citing `test_mcp_everyone.py`.

## Test results
- **Requested run** (`uv run pytest -q tests/test_mcp_everyone.py tests/test_mcp_drift.py tests/test_company_config.py tests/test_gateway.py`): not run, because permission was refused.
- **Developer's report:** 18 passed for test_mcp_everyone, test_mcp_drift and test_company_config; 68 passed for test_gateway and test_marketplace.
- **Gate:** `tasks.md:19` shows the gate reruns the first three files, both greps and `node --check src/agent_company/static/agents.js`. `test_gateway.py` is not in the gate's list, so its result rests on the developer's report only.

FINDING: src/agent_company/api.py:3555 | A `PUT /api/mcp/servers/{id}` whose JSON omits `everyone` gets `model_dump()` → `everyone=False`, so a server stored open to every agent is closed. The task's edge case "edited without the key must stay True" holds only for direct `save_server` calls, not for API clients other than the dashboard. It fails safe (before the fix the same omission opened servers), and `agents.js:382` always sends the key. | In `mcp_update`: `d = body.model_dump(); if "everyone" not in body.model_fields_set: d.pop("everyone")`, then `save_server(d, …)`. Don't use `exclude_unset=True` for the whole body: `save_server` reads `data["transport"]` directly. Add a TestClient test: PUT without `everyone` on an open server keeps it True.
FINDING: tests/test_mcp_everyone.py:73 | Test (f) checks the "keeps True" edge only through `save_server`, so the API-level gap above went unnoticed. | Add the API-level PUT test described above.

BLOCKER: This session refused `uv run pytest …` three times (two command variants) and `node --check src/agent_company/static/agents.js` once. I could not run the requested tests or the JS syntax check myself; the pass results above are the developer's report plus the gate's rerun.

NEXT: The gate reruns `uv run pytest -q tests/test_mcp_everyone.py tests/test_mcp_drift.py tests/test_company_config.py` and `node --check src/agent_company/static/agents.js`. Also run `uv run pytest -q tests/test_gateway.py`, which is not among the gate's commands.
NEXT: Developer: keep `everyone` unchanged on a PUT that omits it (`api.py:3555`) and add the API-level test.

MESSAGE request to developer: In `api.py:3555` (`mcp_update`), drop `everyone` from the dumped body when the client did not send it (`body.model_fields_set`), and add a TestClient test in `tests/test_mcp_everyone.py` showing that a PUT without `everyone` keeps an open server open.

OUTCOME: APPROVED_WITH_COMMENTS
VERDICT: PASS
