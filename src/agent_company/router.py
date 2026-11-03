"""Model Router: the model catalog, role routes and availability checks.

Resolution order for an agent: its own `model_pref` -> the route for its role ->
the `MODEL` from `.env` -> the offline executor. A model is skipped when its
credentials or CLI are missing, it is disabled, or it hit its usage limit, so a
goal always runs (offline, at worst) and the reason is recorded.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import select

from agent_company import usage
from agent_company.config import settings
from agent_company.db import ModelEntry, Route, session

OFFLINE = "Offline simulator"

# name, kind, model_id, input $/1M, output $/1M, notes
BUILTIN_MODELS: list[tuple[str, str, str, float, float, str]] = [
    (OFFLINE, "offline", "offline", 0, 0, "Deterministic stand-in used when no model is reachable."),
    ("Claude Code", "claude_code", "claude-opus-5-5", 0, 0,
     "Runs the `claude` CLI in the task worktree with your Claude subscription login "
     "(or ANTHROPIC_API_KEY). Needs ENABLE_CLAUDE_CODE=1."),
    ("Codex CLI", "codex", "", 0, 0, "Runs `codex exec` in the task worktree. Needs ENABLE_CODEX=1."),
    ("Claude Fable 5.1", "llm", "anthropic/claude-fable-5-1", 10.0, 50.0, "Anthropic API."),
    ("Claude Opus 5.5", "llm", "anthropic/claude-opus-5-5", 4.0, 20.0, "Anthropic API."),
    ("Claude Sonnet 5.5", "llm", "anthropic/claude-sonnet-5-5", 2.0, 10.0, "Anthropic API."),
    ("Claude Haiku 5.5", "llm", "anthropic/claude-haiku-5-5", 0.10, 0.50, "Anthropic API."),
    ("OpenRouter Free router", "llm", "openrouter/openrouter/free", 0, 0,
     "Routes to a free OpenRouter model. Needs OPENROUTER_API_KEY (free keys work)."),
]

# Recommended routing: strong models plan and design, the coding agent writes code,
# a mid model reviews, cheap or free models do bulk reading and collection.
DEFAULT_ROUTES: dict[str, str] = {
    "general_manager": "Claude Opus 5.5",
    "architect": "Claude Opus 5.5",
    "developer": "Claude Code",
    "frontend": "Claude Code",
    "devops": "Claude Code",
    "tech_scout": "Claude Code",
    "qa": "Claude Sonnet 5.5",
    "security": "Claude Sonnet 5.5",
    "network_security": "Claude Sonnet 5.5",
    "analyst": "Claude Sonnet 5.5",
    "comprehensive_analyst": "Claude Sonnet 5.5",
    "research": "Claude Haiku 5.5",
    "external_data": "OpenRouter Free router",
    "student": "OpenRouter Free router",
}
DEFAULT_ROUTE_OTHER = "Claude Haiku 5.5"

PROVIDER_KEYS: dict[str, tuple[str, ...]] = {
    "anthropic": ("ANTHROPIC_API_KEY",),
    "claude": ("ANTHROPIC_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "openrouter": ("OPENROUTER_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "google": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "deepseek": ("DEEPSEEK_API_KEY",),
    "cerebras": ("CEREBRAS_API_KEY",),
    "deepinfra": ("DEEPINFRA_API_KEY",),
    "dashscope": ("DASHSCOPE_API_KEY",),
}
LOCAL_PROVIDERS = {"ollama", "ollama_chat", "hosted_vllm"}


@dataclass
class Choice:
    name: str
    kind: str
    model_id: str
    reason: str


def model_dict(m: ModelEntry) -> dict[str, Any]:
    ok, why = available(m)
    return {"id": m.id, "name": m.name, "kind": m.kind, "model_id": m.model_id,
            "input_cost": m.input_cost, "output_cost": m.output_cost,
            "daily_token_limit": m.daily_token_limit, "weekly_token_limit": m.weekly_token_limit,
            "enabled": m.enabled, "builtin": m.builtin, "notes": m.notes,
            "available": ok, "unavailable_reason": why}


def seed() -> None:
    with session() as s:
        have = set(s.scalars(select(ModelEntry.name)))
        for name, kind, mid, cin, cout, notes in BUILTIN_MODELS:
            if name not in have:
                s.add(ModelEntry(name=name, kind=kind, model_id=mid, input_cost=cin,
                                 output_cost=cout, notes=notes, builtin=True))
        default = settings().default_model
        if default and not s.scalars(select(ModelEntry).where(ModelEntry.model_id == default)).first():
            s.add(ModelEntry(name=f"MODEL ({default})", kind="llm", model_id=default,
                             notes="From MODEL in .env."))
        have_routes = set(s.scalars(select(Route.role)))
        from agent_company.org import ROLES

        for role in ROLES:
            if role not in have_routes:
                s.add(Route(role=role, model_name=DEFAULT_ROUTES.get(role, DEFAULT_ROUTE_OTHER)))


def provider_of(model_id: str) -> str:
    if model_id.startswith("claude-"):
        return "anthropic"
    return model_id.split("/", 1)[0] if "/" in model_id else "openai"


def available(m: ModelEntry) -> tuple[bool, str]:
    if not m.enabled:
        return False, "disabled"
    if m.kind == "offline":
        return True, ""
    if m.kind == "claude_code":
        if not settings().enable_claude_code:
            return False, "set ENABLE_CLAUDE_CODE=1 to let agents run Claude Code"
        if not shutil.which("claude"):
            return False, "`claude` CLI not found on PATH"
    elif m.kind == "codex":
        if not settings().enable_codex:
            return False, "set ENABLE_CODEX=1 to let agents run Codex"
        if not shutil.which("codex"):
            return False, "`codex` CLI not found on PATH"
    else:
        provider = provider_of(m.model_id)
        if provider in LOCAL_PROVIDERS:
            if provider.startswith("ollama") and not os.environ.get("OLLAMA_API_BASE"):
                return False, "set OLLAMA_API_BASE (e.g. http://localhost:11434)"
        else:
            keys = PROVIDER_KEYS.get(provider, (f"{provider.upper()}_API_KEY",))
            if not any(os.environ.get(k) for k in keys):
                return False, f"{' or '.join(keys)} is not set"
    limit = usage.over_limit(m)
    if limit:
        return False, limit
    return True, ""


def _choice(m: ModelEntry, reason: str) -> Choice:
    return Choice(name=m.name, kind=m.kind, model_id=m.model_id, reason=reason)


def resolve(role: str, model_pref: str | None = None) -> Choice:
    """Pick the model an agent of `role` should use right now."""
    with session() as s:
        offline = s.scalars(select(ModelEntry).where(ModelEntry.kind == "offline")).first()
        if offline is None:
            raise RuntimeError("model catalog not seeded")
        if settings().executor == "offline":
            return _choice(offline, "EXECUTOR=offline")
        skipped: list[str] = []
        candidates: list[tuple[str, str | None]] = [("agent preference", model_pref)]
        route = s.get(Route, role)
        candidates.append((f"route for {role}", route.model_name if route else None))
        default = settings().default_model
        if default:
            m = s.scalars(select(ModelEntry).where(ModelEntry.model_id == default)).first()
            candidates.append(("MODEL in .env", m.name if m else None))
        for label, name in candidates:
            if not name:
                continue
            m = s.scalars(select(ModelEntry).where(ModelEntry.name == name)).first()
            if m is None:
                skipped.append(f"{name}: not in catalog")
                continue
            ok, why = available(m)
            if ok:
                return _choice(m, label)
            skipped.append(f"{name}: {why}")
        return _choice(offline, "fallback (" + "; ".join(skipped) + ")" if skipped else "fallback")


def list_models() -> list[dict[str, Any]]:
    with session() as s:
        return [model_dict(m) for m in s.scalars(select(ModelEntry).order_by(ModelEntry.id)).all()]


def list_routes() -> dict[str, str]:
    with session() as s:
        return {r.role: r.model_name for r in s.scalars(select(Route).order_by(Route.role))}


def set_route(role: str, model_name: str) -> None:
    with session() as s:
        if not s.scalars(select(ModelEntry).where(ModelEntry.name == model_name)).first():
            raise ValueError(f"unknown model {model_name!r}")
        r = s.get(Route, role)
        if r is None:
            s.add(Route(role=role, model_name=model_name))
        else:
            r.model_name = model_name


MODEL_FIELDS = {"name", "kind", "model_id", "input_cost", "output_cost", "daily_token_limit",
                "weekly_token_limit", "enabled", "notes"}


def add_model(data: dict[str, Any]) -> dict[str, Any]:
    data = {k: v for k, v in data.items() if k in MODEL_FIELDS}
    if not data.get("name") or not data.get("kind"):
        raise ValueError("name and kind are required")
    if data["kind"] not in {"llm", "claude_code", "codex"}:
        raise ValueError("kind must be llm, claude_code or codex")
    if data["kind"] == "llm" and not data.get("model_id"):
        raise ValueError("model_id is required (LiteLLM-style provider/model)")
    with session() as s:
        if s.scalars(select(ModelEntry).where(ModelEntry.name == data["name"])).first():
            raise ValueError("a model with that name exists")
        m = ModelEntry(**{"model_id": "", **data})
        s.add(m)
        s.flush()
        return model_dict(m)


def update_model(model_id: int, data: dict[str, Any]) -> dict[str, Any]:
    with session() as s:
        m = s.get(ModelEntry, model_id)
        if m is None:
            raise KeyError(model_id)
        fields = MODEL_FIELDS - ({"name", "kind", "model_id"} if m.builtin else set())
        for k, v in data.items():
            if k in fields:
                setattr(m, k, v)
        s.flush()
        return model_dict(m)


def delete_model(model_id: int) -> None:
    with session() as s:
        m = s.get(ModelEntry, model_id)
        if m is None:
            raise KeyError(model_id)
        if m.kind == "offline":
            raise ValueError("the offline simulator cannot be deleted")
        for r in s.scalars(select(Route).where(Route.model_name == m.name)):
            r.model_name = DEFAULT_ROUTE_OTHER if m.name != DEFAULT_ROUTE_OTHER else OFFLINE
        s.delete(m)


# --- OpenRouter catalog ---------------------------------------------------------

_catalog: tuple[float, list[dict[str, Any]]] | None = None
_catalog_lock = threading.Lock()
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"


def openrouter_catalog(query: str = "", free_only: bool = False, limit: int = 200,
                       client: httpx.Client | None = None) -> list[dict[str, Any]]:
    """OpenRouter's public model list (cached 10 minutes), with prices per 1M tokens."""
    global _catalog
    with _catalog_lock:
        if _catalog is None or time.time() - _catalog[0] > 600:
            c = client or httpx.Client(timeout=20)
            resp = c.get(OPENROUTER_MODELS_URL)
            resp.raise_for_status()
            rows = []
            for m in resp.json().get("data", []):
                p = m.get("pricing") or {}
                cin = float(p.get("prompt") or 0) * 1_000_000
                cout = float(p.get("completion") or 0) * 1_000_000
                rows.append({"id": m["id"], "name": m.get("name") or m["id"],
                             "context_length": m.get("context_length"),
                             "input_cost": round(cin, 4), "output_cost": round(cout, 4),
                             "free": cin == 0 and cout == 0})
            _catalog = (time.time(), rows)
        rows = _catalog[1]
    q = query.lower().strip()
    out = [r for r in rows if (not q or q in r["id"].lower() or q in r["name"].lower())
           and (not free_only or r["free"])]
    return sorted(out, key=lambda r: (not r["free"], r["input_cost"], r["id"]))[:limit]


def add_openrouter_model(or_id: str, name: str | None = None, **costs: float) -> dict[str, Any]:
    row = next((r for r in openrouter_catalog(or_id, limit=10_000) if r["id"] == or_id), None)
    return add_model({
        "name": name or f"OpenRouter · {row['name'] if row else or_id}",
        "kind": "llm",
        "model_id": f"openrouter/{or_id}",
        "input_cost": costs.get("input_cost", row["input_cost"] if row else 0.0),
        "output_cost": costs.get("output_cost", row["output_cost"] if row else 0.0),
        "notes": "OpenRouter. Needs OPENROUTER_API_KEY.",
    })
