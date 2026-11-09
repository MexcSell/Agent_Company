"""Agent Company's bridge MCP server (EL-R-09): a running agent's own task, progress, insights and review requests.

Standard library only, no database access: each call is forwarded with the run's bearer token to the company API on
loopback (`POST /api/bridge/call`), which works out the goal, task and agent from the token and refuses everything else.
Environment (set per run by the loop, never by an administrator): AC_BRIDGE_URL, AC_BRIDGE_TOKEN. initialize, ping and
tools/list need neither. Run by mcp.py (Agents → Tools & MCP → catalog → Company bridge).
"""
import json, re, sys, urllib.error, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _stdio import ToolError, serve, tool  # noqa: E402

import os  # noqa: E402
TIMEOUT = 30
MAX_ANSWER = 1_000_000
LOOPBACK = re.compile(r"^http://(127\.0\.0\.1|localhost|\[::1\]):\d{1,5}/?$")
SCOPE = {"goal_id": {"type": "integer", "description": "Must be this run's own goal; anything else is refused."},
         "task_id": {"type": "integer", "description": "Must be this run's own task; anything else is refused."}}

TOOLS = [
    tool("get_task", "This run's task: its title, description and acceptance conditions, and the goal's title and "
         "allowed paths.", dict(SCOPE), title="Get my task"),
    tool("post_progress", "Post a progress note on this run's goal blackboard (a new version each time).",
         {"text": {"type": "string", "minLength": 1, "maxLength": 4000},
          "percent": {"type": "integer", "minimum": 0, "maximum": 100}, **SCOPE},
         required=("text",), title="Post progress", read_only=False),
    tool("save_insight", "Save a lesson or discovery from this task for the General Manager (at most 5 per task).",
         {"title": {"type": "string", "minLength": 1, "maxLength": 200},
          "text": {"type": "string", "minLength": 1, "maxLength": 2000},
          "evidence": {"type": "array", "maxItems": 5, "items": {"type": "string", "maxLength": 300}}, **SCOPE},
         required=("title", "text"), title="Save insight", read_only=False),
    tool("request_review", "Ask the QA or security role on this goal to review your work (at most 2 per task).",
         {"summary": {"type": "string", "minLength": 1, "maxLength": 1000},
          "files": {"type": "array", "maxItems": 20, "items": {"type": "string", "maxLength": 300}},
          "reviewer": {"type": "string", "enum": ["qa", "security"], "default": "qa"}, **SCOPE},
         required=("summary",), title="Request review", read_only=False),
]


def forward(name, args):
    url, token = os.environ.get("AC_BRIDGE_URL", "").strip(), os.environ.get("AC_BRIDGE_TOKEN", "").strip()
    if not url or not token:
        raise ToolError("The bridge only works inside an agent's run (no bridge token).")
    if not LOOPBACK.match(url):
        raise ToolError("The bridge only talks to the company API on this host (loopback URL).")
    req = urllib.request.Request(url.rstrip("/") + "/api/bridge/call", method="POST",
                                 data=json.dumps({"tool": name, "arguments": args}).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # the token never goes through a proxy
    try:
        with opener.open(req, timeout=TIMEOUT) as r:
            body = json.loads(r.read(MAX_ANSWER) or b"{}")
            return str(body.get("text", ""))
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read(MAX_ANSWER) or b"{}")
        except ValueError:
            body = {}
        raise ToolError(str(body.get("error") or f"HTTP {e.code}"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise ToolError(f"Couldn't reach the company API: {getattr(e, 'reason', e)}")


if __name__ == "__main__":
    serve("agent-company-bridge", "1.0", TOOLS, {t["name"]: (lambda a, n=t["name"]: forward(n, a)) for t in TOOLS})
