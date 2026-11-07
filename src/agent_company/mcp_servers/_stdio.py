"""The MCP side of Agent Company's bundled servers (papers.py, database.py): newline-delimited JSON-RPC on stdin/stdout.

Standard library only, and no import of `app`: mcp_hub.py starts each server as `python app/mcp_servers/<name>.py` in backend/,
with a bare environment (none of Agent Company's own variables) plus what the administrator set for that server.
"""
import json, sys, traceback

class ToolError(Exception):
    """A problem the agent should read (bad input, nothing found): answered as the tool's own error, not a protocol error."""

def tool(name, description, props, required=(), title=None, read_only=True):
    return {"name": name, "description": description, "annotations": {"readOnlyHint": bool(read_only), **({"title": title} if title else {})},
            "inputSchema": {"type": "object", "properties": props, "required": list(required)}}

def serve(name, version, tools, handlers):
    """tools: the definitions; handlers: {tool name: fn(args) -> str}. Runs until stdin closes."""
    def send(m): sys.stdout.write(json.dumps(m) + "\n"); sys.stdout.flush()
    for line in sys.stdin:
        if not line.strip(): continue
        try: m = json.loads(line)
        except ValueError: continue
        meth, mid, p = m.get("method"), m.get("id"), m.get("params") or {}
        if mid is None: continue  # notifications
        if meth == "initialize":
            r = {"protocolVersion": p.get("protocolVersion") or "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": name, "version": version}}
        elif meth == "ping": r = {}
        elif meth == "tools/list": r = {"tools": tools}
        elif meth == "tools/call":
            fn = handlers.get(p.get("name"))
            if not fn:
                send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"Unknown tool {p.get('name')}"}}); continue
            try: r = {"content": [{"type": "text", "text": fn(p.get("arguments") or {})}]}
            except ToolError as e: r = {"content": [{"type": "text", "text": str(e)}], "isError": True}
            except Exception as e:  # noqa: BLE001 - one broken call never ends the server
                traceback.print_exc(file=sys.stderr)
                r = {"content": [{"type": "text", "text": f"The tool failed: {type(e).__name__}: {e}"[:500]}], "isError": True}
        else:
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Unknown method {meth}"}}); continue
        send({"jsonrpc": "2.0", "id": mid, "result": r})
