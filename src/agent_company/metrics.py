"""Metrics for Prometheus (FW-R-03, after Flowise's metrics endpoint): `GET /metrics` in the text exposition format
0.0.4, for an administrator's session or a scraper with the metrics token.

Only counts and sums: no label carries a goal title, a prompt, a path or a person's name. The token is shown once
when an administrator creates it and stored as its SHA-256 (settings row `_metrics_token`); creating or revoking it
is audited by the API.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Any

from sqlalchemy import func, select

from agent_company import governance, usage
from agent_company.db import Agent, Goal, Incident, Run, Setting, Task, session

TOKEN_KEY = "_metrics_token"  # not a prefs setting: never shown on the Settings page


def create_token() -> str:
    """A new scrape token (the previous one stops working); only its hash is kept."""
    token = "acm_" + secrets.token_urlsafe(32)
    with session() as s:
        row = s.get(Setting, TOKEN_KEY) or Setting(key=TOKEN_KEY, value={})
        row.value = {"sha256": hashlib.sha256(token.encode()).hexdigest(), "hint": token[-4:]}
        s.merge(row)
    return token


def revoke_token() -> None:
    with session() as s:
        row = s.get(Setting, TOKEN_KEY)
        if row is not None:
            s.delete(row)


def token_status() -> dict[str, Any]:
    with session() as s:
        row = s.get(Setting, TOKEN_KEY)
        return {"set": bool(row and (row.value or {}).get("sha256")), "hint": (row.value or {}).get("hint", "") if row else ""}


def token_ok(token: str) -> bool:
    if not token:
        return False
    with session() as s:
        row = s.get(Setting, TOKEN_KEY)
        want = (row.value or {}).get("sha256", "") if row else ""
    return bool(want) and hmac.compare_digest(want, hashlib.sha256(token.encode()).hexdigest())


def _esc(v: Any) -> str:
    return str(v).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


class _Out:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def family(self, name: str, kind: str, help_: str, samples: list[tuple[dict[str, Any], float]]) -> None:
        self.lines += [f"# HELP agent_company_{name} {help_}", f"# TYPE agent_company_{name} {kind}"]
        for labels, value in samples:
            lab = ",".join(f'{k}="{_esc(v)}"' for k, v in labels.items())
            num = int(value) if float(value).is_integer() else round(float(value), 6)
            self.lines.append(f"agent_company_{name}{{{lab}}} {num}" if lab else f"agent_company_{name} {num}")


def render() -> str:
    """The current values in the Prometheus text format."""
    o = _Out()
    today = usage.day_start()
    with session() as s:
        goals = s.execute(select(Goal.status, func.count()).group_by(Goal.status)).all()
        tasks = s.execute(select(Task.status, func.count()).group_by(Task.status)).all()
        runs = s.execute(select(Run.kind, Run.ok, func.count(), func.coalesce(func.sum(func.json_array_length(Run.tool_calls)), 0),
                                func.coalesce(func.sum(Run.input_tokens), 0), func.coalesce(func.sum(Run.output_tokens), 0),
                                func.coalesce(func.sum(Run.cost), 0.0), func.coalesce(func.sum(Run.duration), 0.0))
                         .group_by(Run.kind, Run.ok)).all()
        day = s.execute(select(func.coalesce(func.sum(Run.input_tokens + Run.output_tokens), 0),
                               func.coalesce(func.sum(Run.cost), 0.0)).where(Run.started_at >= today)).one()
        histories = s.scalars(select(Goal.history).where(Goal.origin != "eval")).all()
        incidents = s.execute(select(Incident.severity, func.count()).where(Incident.status != "closed")
                              .group_by(Incident.severity)).all()
        agents = s.execute(select(Agent.lifecycle, Agent.health, func.count()).group_by(Agent.lifecycle, Agent.health)).all()
    o.family("up", "gauge", "The Agent Company server answers.", [({}, 1)])
    o.family("goals", "gauge", "Goals by status.", [({"status": st}, n) for st, n in sorted(goals)])
    o.family("tasks", "gauge", "Tasks by execution status.", [({"status": st}, n) for st, n in sorted(tasks)])
    o.family("runs_total", "counter", "Executor runs by executor kind and outcome.",
             [({"kind": k, "ok": str(bool(ok)).lower()}, n) for k, ok, n, *_ in sorted(runs, key=lambda r: (r[0], r[1]))])
    by_kind: dict[str, list[float]] = {}
    for k, _ok, _n, calls, tin, tout, cost, dur in runs:
        acc = by_kind.setdefault(k, [0, 0, 0, 0.0, 0.0])
        for i, v in enumerate((calls, tin, tout, cost, dur)):
            acc[i] += v or 0
    kinds = sorted(by_kind.items())
    o.family("tool_calls_total", "counter", "Tool calls made by executor runs.", [({"kind": k}, v[0]) for k, v in kinds])
    o.family("tokens_total", "counter", "Model tokens by executor kind and direction.",
             [({"kind": k, "direction": "input"}, v[1]) for k, v in kinds] + [({"kind": k, "direction": "output"}, v[2]) for k, v in kinds])
    o.family("cost_usd_total", "counter", "Model cost in USD by executor kind.", [({"kind": k}, v[3]) for k, v in kinds])
    o.family("run_seconds_total", "counter", "Seconds spent in executor runs by kind.", [({"kind": k}, v[4]) for k, v in kinds])
    o.family("tokens_today", "gauge", "Model tokens since 00:00 UTC.", [({}, day[0])])
    o.family("cost_usd_today", "gauge", "Model cost in USD since 00:00 UTC.", [({}, day[1])])
    gates = [h for hist in histories for h in (hist or []) if isinstance(h, dict) and "passed" in h]
    o.family("gate_runs_total", "counter", "Quality-gate evaluations of real goals by outcome.",
             [({"passed": "true"}, sum(bool(h["passed"]) for h in gates)), ({"passed": "false"}, sum(not h["passed"] for h in gates))])
    o.family("incidents_open", "gauge", "Security incidents not closed, by severity.",
             [({"severity": sev}, n) for sev, n in sorted(incidents)])
    o.family("agents", "gauge", "Agents by lifecycle and health.",
             [({"lifecycle": lc, "health": h}, n) for lc, h, n in sorted(agents)])
    active = {x["level"] for x in governance.stops()} | ({"E5"} if governance.emergency() else set())
    o.family("emergency_stop", "gauge", "1 while an emergency stop of this level (E0-E5) is active.",
             [({"level": f"E{i}"}, int(f"E{i}" in active)) for i in range(6)])
    return "\n".join(o.lines) + "\n"
