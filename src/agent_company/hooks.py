"""Team hooks (fail-open): TaskCreated, TaskCompleted and TeammateIdle are appended to
`data/logs/team-hooks.jsonl`, so every decision of the agent team is on record (see
docs/1.claude-code-multi-agent.jpg). A hook that cannot write never stops the work.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any

from agent_company.config import settings

EVENTS = ("TaskCreated", "TaskCompleted", "TeammateIdle")
_lock = threading.Lock()


def path():  # noqa: ANN201
    return settings().data_dir / "logs" / "team-hooks.jsonl"


def emit(event: str, **data: Any) -> None:
    try:
        p = path()
        p.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "event": event, **data},
                          default=str)
        with _lock, p.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:  # noqa: BLE001 - fail-open by design
        pass


def recent(limit: int = 200) -> list[dict[str, Any]]:
    try:
        lines = path().read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError:
        return []
    out = []
    for ln in reversed(lines):
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
    return out
