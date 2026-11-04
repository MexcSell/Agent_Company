"""Tools & MCP: the MCP servers agents may use, the tools each offers, and which agents may use which.

Taken from AXoR (/home/thomas/subnet-scout, backend/app/mcp_hub.py), reduced to what this company needs:

- Servers (`mcp_servers`): administrators add a remote server (Streamable HTTP: a URL and headers) or a local
  one (stdio: a command, its arguments and environment). Header and environment *values* are secrets: they are
  stored encrypted (transport.encrypt_text) and never leave the server; the API shows their names only.
- Discovery: "Test" runs initialize + tools/list and keeps the tools. A tool starts **not allowed** unless the
  server marks it read-only (readOnlyHint) or the catalog entry says the server only reads; an administrator
  ticks the rest.
- Grants (`mcp_grants`): a server open to `everyone` is offered to every agent; otherwise only to granted agents.
- Runtime: `claude_config(agent_id)` is the `--mcp-config` JSON for Claude Code (granted, enabled, tested
  servers only, with their allowed tools as `mcp__<slug>__<tool>` names). `call()` runs one tool and is
  written to `mcp_calls`, as every call from the dashboard is.

The client is stdlib-only: newline-delimited JSON-RPC over a child's stdin/stdout, or JSON-RPC POSTs whose
answer is JSON or an SSE stream. A local server never gets this service's environment (API keys...).
"""

from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path
from typing import Any

from sqlalchemy import select

from agent_company.db import Agent, McpCall, McpGrant, McpServer, now, session

PROTOCOL = "2025-06-18"
TIMEOUT, LIST_TIMEOUT = 60, 30
RESULT_CHARS, MAX_BODY = 20000, 5 * 1024 * 1024
BUNDLED = Path(__file__).parent / "mcp_servers"
_PURPOSE = b"ac-mcp"


class McpError(Exception):
    """The server could not be reached or broke the protocol (not a tool's own error)."""


# --- transports -----------------------------------------------------------------------

class _Stdio:
    def __init__(self, command: str, args: list[str], env: dict[str, str]):
        base = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TZ", "HTTP_PROXY",
                                           "HTTPS_PROXY", "NO_PROXY", "SSL_CERT_FILE") if k in os.environ}
        try:
            self.p = subprocess.Popen([command, *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, cwd=str(BUNDLED), env={**base, "PYTHONUNBUFFERED": "1", **env},
                                      text=True, bufsize=1, start_new_session=True)
        except OSError as e:
            raise McpError(f"could not start {command}: {e.strerror or e}") from e
        self.q: queue.Queue[Any] = queue.Queue()
        self.err: deque[str] = deque(maxlen=20)
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=lambda: [self.err.append(ln.rstrip()) for ln in self.p.stderr], daemon=True).start()

    def _read(self) -> None:
        for line in self.p.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self.q.put(json.loads(line))
            except ValueError:
                self.err.append("not JSON on stdout: " + line[:200])
        self.q.put(None)

    def _send(self, msg: dict[str, Any]) -> None:
        try:
            self.p.stdin.write(json.dumps(msg) + "\n")
            self.p.stdin.flush()
        except (OSError, ValueError) as e:
            raise McpError(f"the server's process is not running ({e}). {self._tail()}") from e

    def _tail(self) -> str:
        return ("Its last output: " + " | ".join(list(self.err)[-3:])) if self.err else ""

    def request(self, msg: dict[str, Any], timeout: float) -> dict[str, Any]:
        self._send(msg)
        end = time.time() + timeout
        while True:
            try:
                m = self.q.get(timeout=max(0.05, end - time.time()))
            except queue.Empty as e:
                raise McpError(f"no answer within {timeout:g} s") from e
            if m is None:
                raise McpError(f"the server's process ended. {self._tail()}")
            if m.get("id") == msg["id"] and ("result" in m or "error" in m):
                return m
            if "method" in m and "id" in m:  # the server asks something: ping answered, the rest declined
                self._send({"jsonrpc": "2.0", "id": m["id"], "result": {}} if m["method"] == "ping"
                           else {"jsonrpc": "2.0", "id": m["id"], "error": {"code": -32601, "message": "not supported"}})
            if time.time() > end:
                raise McpError(f"no answer within {timeout:g} s")

    def notify(self, msg: dict[str, Any]) -> None:
        self._send(msg)

    def close(self) -> None:
        try:
            self.p.stdin.close()
            self.p.terminate()
            self.p.wait(3)
        except Exception:  # noqa: BLE001
            try:
                self.p.kill()
            except Exception:  # noqa: BLE001
                pass


class _Http:
    def __init__(self, url: str, headers: dict[str, str]):
        self.url, self.headers, self.sid, self.proto = url, headers, None, None

    def _post(self, msg: dict[str, Any], timeout: float) -> dict[str, Any] | None:
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
             "User-Agent": "AgentCompany-MCP/1", **self.headers}
        if self.sid:
            h["Mcp-Session-Id"] = self.sid
        if self.proto:
            h["MCP-Protocol-Version"] = self.proto
        req = urllib.request.Request(self.url, data=json.dumps(msg).encode(), headers=h, method="POST")
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 - an administrator's URL
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise McpError(f"the server refused the credentials (HTTP {e.code}); check the headers") from e
            raise McpError(f"HTTP {e.code}: {e.read(300).decode('utf-8', 'replace')}") from e
        except (urllib.error.URLError, OSError) as e:
            raise McpError(f"could not reach {self.url}: {getattr(e, 'reason', e)}") from e
        with resp:
            if resp.headers.get("Mcp-Session-Id"):
                self.sid = resp.headers["Mcp-Session-Id"]
            if "id" not in msg or resp.status == 202:
                return None
            ctype = resp.headers.get("Content-Type", "")
            if "text/event-stream" in ctype:
                data: list[str] = []
                for raw in resp:
                    line = raw.decode("utf-8", "replace").rstrip("\r\n")
                    if line.startswith("data:"):
                        data.append(line[5:].lstrip())
                    elif line == "" and data:
                        try:
                            m = json.loads("\n".join(data))
                        except ValueError:
                            m = {}
                        data = []
                        if isinstance(m, dict) and m.get("id") == msg["id"]:
                            return m
                raise McpError("the server's stream ended without an answer")
            body = resp.read(MAX_BODY + 1)
            if len(body) > MAX_BODY:
                raise McpError("the server's answer is too large")
            try:
                m = json.loads(body or b"{}")
            except ValueError as e:
                raise McpError(f"the server did not answer with JSON ({ctype or 'no content type'})") from e
            return next((x for x in m if x.get("id") == msg["id"]), {}) if isinstance(m, list) else m

    def request(self, msg: dict[str, Any], timeout: float) -> dict[str, Any]:
        m = self._post(msg, timeout)
        if not m:
            raise McpError("the server sent no answer")
        return m

    def notify(self, msg: dict[str, Any]) -> None:
        self._post(msg, 15)

    def close(self) -> None:
        pass


class Session:
    """One initialized connection; used for a single test or call, then closed."""

    def __init__(self, server: dict[str, Any], secrets: dict[str, Any]):
        if server["transport"] == "stdio":
            self.t: Any = _Stdio(_command(server["command"]), list(server["args"]), secrets.get("env") or {})
        else:
            self.t = _Http(server["url"], secrets.get("headers") or {})
        self.ids = 0
        try:
            r = self._call("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                          "clientInfo": {"name": "Agent Company", "version": "1"}}, LIST_TIMEOUT)
            self.info = r.get("serverInfo") or {}
            if isinstance(self.t, _Http):
                self.t.proto = r.get("protocolVersion") or PROTOCOL
            self.t.notify({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except BaseException:
            self.t.close()
            raise

    def _call(self, method: str, params: dict[str, Any], timeout: float) -> dict[str, Any]:
        self.ids += 1
        m = self.t.request({"jsonrpc": "2.0", "id": self.ids, "method": method, "params": params}, timeout)
        if "error" in m:
            e = m["error"] or {}
            raise McpError(f"{method}: {e.get('message') or e}")
        return m.get("result") or {}

    def tools(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        cursor = None
        for _ in range(10):
            r = self._call("tools/list", {"cursor": cursor} if cursor else {}, LIST_TIMEOUT)
            out += r.get("tools") or []
            cursor = r.get("nextCursor")
            if not cursor:
                break
        return out

    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        return self._call("tools/call", {"name": name, "arguments": args}, TIMEOUT)

    def close(self) -> None:
        self.t.close()


def _command(cmd: str) -> str:
    """`python` means this interpreter: the bundled servers run with the service's own Python."""
    return sys.executable if cmd in {"python", "python3"} else cmd


def flatten(r: dict[str, Any]) -> tuple[str, bool]:
    parts = []
    for c in r.get("content") or []:
        t = c.get("type")
        if t == "text":
            parts.append(c.get("text") or "")
        elif t == "resource":
            res = c.get("resource") or {}
            parts.append(res.get("text") or f"[resource {res.get('uri', '')}]")
        elif t in ("image", "audio"):
            parts.append(f"[{t} {c.get('mimeType') or ''} not shown]")
    text = "\n".join(p for p in parts if p)
    if not text and r.get("structuredContent") is not None:
        text = json.dumps(r["structuredContent"])
    return text[:RESULT_CHARS], bool(r.get("isError"))


# --- the catalog: servers added in one click ----------------------------------------------
# "allow": which discovered tools start allowed: "read_only" (the server's readOnlyHint) or "all".
CATALOG: list[dict[str, Any]] = [
    {"key": "context7", "name": "Context7", "transport": "http", "url": "https://mcp.context7.com/mcp", "allow": "read_only",
     "about": "Current documentation and code examples for libraries and frameworks. Works without a key (rate-limited).",
     "fields": [{"part": "headers", "key": "CONTEXT7_API_KEY", "label": "Context7 API key", "optional": True}]},
    {"key": "deepwiki", "name": "DeepWiki", "transport": "http", "url": "https://mcp.deepwiki.com/mcp", "allow": "all",
     "about": "Questions about any public GitHub repository and its generated documentation. Free, no key.", "fields": []},
    {"key": "github", "name": "GitHub", "transport": "http", "url": "https://api.githubcopilot.com/mcp/", "allow": "read_only",
     "about": "GitHub's official server: repositories, files, issues, pull requests, code search, Actions. Writing tools wait for a tick.",
     "fields": [{"part": "headers", "key": "Authorization", "label": "GitHub token", "prefix": "Bearer "}]},
    {"key": "exa", "name": "Exa search", "transport": "http", "url": "https://mcp.exa.ai/mcp", "allow": "read_only",
     "about": "Web search and page reading for research and fact-checking. Works without a key.", "fields": []},
    {"key": "papers", "name": "Research papers", "transport": "stdio", "command": "python", "args": ["papers.py"], "allow": "all",
     "about": "arXiv, Semantic Scholar and OpenAlex: abstracts, authors, citations. Bundled (from AXoR), no key.",
     "fields": [{"part": "env", "key": "S2_API_KEY", "label": "Semantic Scholar API key", "optional": True}]},
    {"key": "database", "name": "Database (read-only)", "transport": "stdio", "command": "python", "args": ["database.py"],
     "allow": "all", "about": "Read-only SQL on PostgreSQL or an SQLite file: tables, columns, SELECT queries. Bundled (from AXoR).",
     "fields": [{"part": "env", "key": "DATABASE_URL", "label": "Database URL (postgresql://… or sqlite:////path.db)"}]},
]
CATALOG_BY_KEY = {c["key"]: c for c in CATALOG}


# --- servers ------------------------------------------------------------------------------

def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")[:16].strip("_") or "server"


def _secrets(row: McpServer) -> dict[str, Any]:
    from agent_company.transport import decrypt_text

    try:
        return json.loads(decrypt_text(row.secrets, _PURPOSE) or "{}")
    except ValueError:
        return {}


def _seal(values: dict[str, Any]) -> str:
    from agent_company.transport import encrypt_text

    return encrypt_text(json.dumps(values), _PURPOSE)


def server_dict(row: McpServer, grants: list[int] | None = None) -> dict[str, Any]:
    return {"id": row.id, "name": row.name, "slug": row.slug, "description": row.description,
            "transport": row.transport, "command": row.command, "args": row.args, "url": row.url,
            "env": (row.secret_names or {}).get("env", []), "headers": (row.secret_names or {}).get("headers", []),
            "enabled": row.enabled, "everyone": row.everyone, "status": row.status, "error": row.last_error,
            "tested_at": row.tested_at.isoformat() if row.tested_at else None,
            "tools": row.tools or [], "grants": grants or []}


def list_servers() -> dict[str, Any]:
    with session() as s:
        rows = s.scalars(select(McpServer).order_by(McpServer.name)).all()
        grants: dict[int, list[int]] = {}
        for g in s.scalars(select(McpGrant)):
            grants.setdefault(g.server_id, []).append(g.agent_id)
        added = {r.slug for r in rows}
        return {"servers": [server_dict(r, grants.get(r.id)) for r in rows],
                "catalog": [{k: c.get(k) for k in ("key", "name", "about", "transport", "url", "fields")}
                            | {"added": c["key"] in added} for c in CATALOG]}


def _validate(data: dict[str, Any]) -> None:
    if data.get("transport") not in {"stdio", "http"}:
        raise ValueError("transport is stdio or http")
    if data["transport"] == "http" and not re.match(r"^https?://[^\s]+$", data.get("url") or ""):
        raise ValueError("an HTTP server needs a URL starting with http:// or https://")
    if data["transport"] == "stdio" and not re.match(r"^[\w./@+-]{1,256}$", data.get("command") or ""):
        raise ValueError("a local server needs a command (a program name or path, no spaces)")
    for part in ("env", "headers"):
        for k in (data.get(part) or {}):
            if not re.match(r"^[\w.-]{1,128}$", k):
                raise ValueError(f"bad {part} name {k!r}")


def save_server(data: dict[str, Any], actor: str, server_id: int | None = None) -> dict[str, Any]:
    """Create or update a server. `env`/`headers` map names to values; an empty value keeps the stored one."""
    _validate(data)
    with session() as s:
        row = s.get(McpServer, server_id) if server_id else None
        if server_id and row is None:
            raise KeyError(server_id)
        name = (data.get("name") or "").strip()[:64]
        if not name:
            raise ValueError("name the server")
        if row is None:
            slug, n = slugify(data.get("slug") or name), 1
            base = slug
            while s.scalars(select(McpServer).where(McpServer.slug == slug)).first():
                n += 1
                slug = f"{base[:13]}_{n}"
            row = McpServer(name=name, slug=slug, created_by=actor)
            s.add(row)
        elif s.scalars(select(McpServer).where(McpServer.name == name, McpServer.id != row.id)).first():
            raise ValueError("another server has that name")
        old = _secrets(row) if row.secrets else {}
        values = {part: {k: (v if v else (old.get(part) or {}).get(k, "")) for k, v in (data.get(part) or {}).items()}
                  for part in ("env", "headers")}
        row.name, row.description = name, (data.get("description") or "")[:2000]
        row.transport, row.url = data["transport"], (data.get("url") or "").strip()
        row.command, row.args = (data.get("command") or "").strip(), [str(a) for a in (data.get("args") or [])][:40]
        row.secrets = _seal(values)
        row.secret_names = {part: sorted(values[part]) for part in values}
        if "everyone" in data:
            row.everyone = bool(data["everyone"])
        if "enabled" in data:
            row.enabled = bool(data["enabled"])
        row.status = "untested"
        s.flush()
        return server_dict(row)


def add_from_catalog(key: str, values: dict[str, str], actor: str) -> dict[str, Any]:
    c = CATALOG_BY_KEY.get(key)
    if c is None:
        raise KeyError(key)
    data: dict[str, Any] = {"name": c["name"], "slug": key, "description": c["about"], "transport": c["transport"],
                            "url": c.get("url", ""), "command": c.get("command", ""), "args": c.get("args", []),
                            "env": {}, "headers": {}}
    if key == "database":
        from agent_company.config import settings

        data["args"] = [*data["args"], "--protect", str(settings().data_dir)]
    for f in c["fields"]:
        v = (values.get(f["key"]) or "").strip()
        if not v and not f.get("optional"):
            raise ValueError(f"{f['label']} is required")
        if v:
            prefix = f.get("prefix", "")
            data[f["part"]][f["key"]] = v if v.startswith(prefix) else prefix + v
    out = save_server(data, actor)
    try:
        out = test(out["id"], allow=c["allow"])
    except McpError:
        pass
    return get_server(out["id"])


def get_server(server_id: int) -> dict[str, Any]:
    with session() as s:
        row = s.get(McpServer, server_id)
        if row is None:
            raise KeyError(server_id)
        grants = [g.agent_id for g in s.scalars(select(McpGrant).where(McpGrant.server_id == server_id))]
        return server_dict(row, grants)


def delete_server(server_id: int) -> None:
    with session() as s:
        row = s.get(McpServer, server_id)
        if row is None:
            raise KeyError(server_id)
        for g in s.scalars(select(McpGrant).where(McpGrant.server_id == server_id)):
            s.delete(g)
        s.delete(row)


def _loaded(server_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
    with session() as s:
        row = s.get(McpServer, server_id)
        if row is None:
            raise KeyError(server_id)
        return server_dict(row), _secrets(row)


def test(server_id: int, allow: str = "read_only") -> dict[str, Any]:
    """initialize + tools/list; keeps the tools, keeping earlier allow decisions."""
    server, secrets = _loaded(server_id)
    started = time.monotonic()
    try:
        sess = Session(server, secrets)
        try:
            tools = sess.tools()
        finally:
            sess.close()
    except McpError as e:
        with session() as s:
            row = s.get(McpServer, server_id)
            row.status, row.last_error, row.tested_at = "error", str(e)[:2000], now()
        raise
    before = {t["name"]: t.get("allowed") for t in server["tools"]}
    kept = []
    for t in tools[:200]:
        ro = bool((t.get("annotations") or {}).get("readOnlyHint"))
        allowed = before[t["name"]] if t.get("name") in before else (allow == "all" or ro)
        kept.append({"name": t.get("name", ""), "description": (t.get("description") or "")[:500],
                     "read_only": ro, "allowed": bool(allowed), "schema": t.get("inputSchema") or {}})
    with session() as s:
        row = s.get(McpServer, server_id)
        row.tools, row.status, row.last_error, row.tested_at = kept, "ok", "", now()
        s.add(McpCall(server_id=server_id, actor="test", tool="tools/list", ok=True,
                      duration=time.monotonic() - started))
    return get_server(server_id)


def set_tools(server_id: int, allowed: list[str]) -> dict[str, Any]:
    with session() as s:
        row = s.get(McpServer, server_id)
        if row is None:
            raise KeyError(server_id)
        row.tools = [{**t, "allowed": t["name"] in set(allowed)} for t in row.tools or []]
    return get_server(server_id)


def set_grants(server_id: int, agent_ids: list[int], everyone: bool | None = None) -> dict[str, Any]:
    with session() as s:
        row = s.get(McpServer, server_id)
        if row is None:
            raise KeyError(server_id)
        if everyone is not None:
            row.everyone = everyone
        for g in s.scalars(select(McpGrant).where(McpGrant.server_id == server_id)):
            s.delete(g)
        s.flush()
        valid = set(s.scalars(select(Agent.id).where(Agent.id.in_(agent_ids))))
        for aid in sorted(valid):
            s.add(McpGrant(server_id=server_id, agent_id=aid))
    return get_server(server_id)


def call(server_id: int, tool: str, args: dict[str, Any], actor: str, agent_id: int | None = None) -> dict[str, Any]:
    """Run one allowed tool. Every call is logged."""
    server, secrets = _loaded(server_id)
    if not server["enabled"]:
        raise PermissionError("this server is turned off")
    if not any(t["name"] == tool and t["allowed"] for t in server["tools"]):
        raise PermissionError(f"tool {tool!r} is not allowed on {server['name']}")
    if agent_id is not None and not server["everyone"] and agent_id not in server["grants"]:
        raise PermissionError("this agent has no grant for the server")
    started = time.monotonic()
    ok, err, text = True, "", ""
    try:
        sess = Session(server, secrets)
        try:
            text, is_err = flatten(sess.call(tool, args))
            ok = not is_err
        finally:
            sess.close()
    except McpError as e:
        ok, err = False, str(e)
    with session() as s:
        s.add(McpCall(server_id=server_id, agent_id=agent_id, actor=actor, tool=tool, ok=ok,
                      duration=time.monotonic() - started, error=err[:2000]))
    if err:
        raise McpError(err)
    return {"ok": ok, "text": text}


def recent_calls(limit: int = 100) -> list[dict[str, Any]]:
    with session() as s:
        names = {r.id: r.name for r in s.scalars(select(McpServer))}
        return [{"id": c.id, "server": names.get(c.server_id, f"#{c.server_id}"), "agent_id": c.agent_id,
                 "actor": c.actor, "tool": c.tool, "ok": c.ok, "duration": round(c.duration, 2), "error": c.error,
                 "at": c.created_at.isoformat()}
                for c in s.scalars(select(McpCall).order_by(McpCall.id.desc()).limit(limit))]


def for_agent(agent_id: int) -> list[dict[str, Any]]:
    """The servers (with allowed tools) an agent may use right now."""
    out = []
    for srv in list_servers()["servers"]:
        if srv["enabled"] and srv["status"] == "ok" and (srv["everyone"] or agent_id in srv["grants"]):
            tools = [t["name"] for t in srv["tools"] if t["allowed"]]
            if tools:
                out.append({**srv, "allowed": tools})
    return out


def claude_config(agent_id: int) -> tuple[dict[str, Any], list[str]]:
    """Claude Code's --mcp-config for this agent, and the tool names to allow (mcp__<slug>__<tool>)."""
    servers: dict[str, Any] = {}
    allowed: list[str] = []
    for srv in for_agent(agent_id):
        _, secrets = _loaded(srv["id"])
        if srv["transport"] == "stdio":
            servers[srv["slug"]] = {"type": "stdio", "command": _command(srv["command"]),
                                    "args": [str(BUNDLED / a) if (BUNDLED / a).is_file() else a for a in srv["args"]],
                                    "env": secrets.get("env") or {}}
        else:
            servers[srv["slug"]] = {"type": "http", "url": srv["url"], "headers": secrets.get("headers") or {}}
        allowed += [f"mcp__{srv['slug']}__{t}" for t in srv["allowed"]]
    return {"mcpServers": servers}, allowed
