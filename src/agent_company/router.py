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
    "general_manager": "Claude Code",  # the owner's choice: the GM is Claude Code (change it on the GM's card)
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
# Model used for a role's retries after a failed gate (escalation, as in /home/thomas/bit).
DEFAULT_ESCALATIONS: dict[str, str] = {
    "general_manager": "Claude Opus 5.5",
    "qa": "Claude Opus 5.5", "security": "Claude Opus 5.5", "analyst": "Claude Opus 5.5",
    "research": "Claude Sonnet 5.5", "external_data": "Claude Haiku 5.5",
}

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
    base_url: str = ""
    api_key_env: str = ""
    session_id: str = ""

    @property
    def provider(self) -> str:
        return {"claude_code": "anthropic", "codex": "openai", "offline": "offline"}.get(
            self.kind, "local" if self.base_url else provider_of(self.model_id))


def model_dict(m: ModelEntry) -> dict[str, Any]:
    ok, why = available(m)
    return {"id": m.id, "name": m.name, "kind": m.kind, "model_id": m.model_id,
            "input_cost": m.input_cost, "output_cost": m.output_cost,
            "daily_token_limit": m.daily_token_limit, "weekly_token_limit": m.weekly_token_limit,
            "enabled": m.enabled, "builtin": m.builtin, "notes": m.notes, "base_url": m.base_url,
            "api_key_env": m.api_key_env, "session_id": m.session_id, "available": ok, "unavailable_reason": why,
            "clearance": clearance(m), "clearance_set": bool(m.clearance),
            "terms": m.terms or {}, "terms_due": terms_due(m)}


# --- a model's terms (G-R-18) -----------------------------------------------------------------
# "Free" means four different things (a provider's free tier, trial credits, open weights run here, a web chat that is
# no API at all), and a subscription is not API access: routing and data handling depend on what was verified.

BILLING = ("api", "subscription", "free_tier", "trial", "local")
TERMS_STALE_DAYS = 90
_YES_NO = ("yes", "no")
TERMS = {"billing", "context_window", "tool_calling", "trains_on_prompts", "commercial_use", "terms_url"}


def check_terms(data: dict[str, Any]) -> dict[str, Any]:
    """The terms given, validated; `verified: true` stamps them as checked by a person now. "" clears a field."""
    import re
    from datetime import datetime, timezone

    unknown = set(data) - TERMS - {"verified"}
    if unknown:
        raise ValueError(f"unknown model terms: {', '.join(sorted(unknown))}")
    out: dict[str, Any] = {}
    for k in TERMS & set(data):
        v = data[k]
        if k == "context_window":
            if v in ("", None):
                v = 0
            if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                raise ValueError("context_window must be a whole number of tokens (0 = unknown)")
        elif k == "billing":
            if v not in ("", *BILLING):
                raise ValueError(f"billing must be one of {', '.join(BILLING)}")
        elif k == "terms_url":
            if v and not re.match(r"^https?://\S+$", str(v)):
                raise ValueError("terms_url must be an http(s) URL")
        elif v not in ("", *_YES_NO):
            raise ValueError(f"{k} must be yes, no or empty (unknown)")
        out[k] = v
    if data.get("verified"):
        out["verified_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return out


def terms_due(m: ModelEntry) -> bool:
    """A model that sends prompts somewhere whose terms no person verified in the last TERMS_STALE_DAYS days."""
    from datetime import datetime, timedelta, timezone

    if m.kind == "offline":
        return False
    at = (m.terms or {}).get("verified_at")
    try:
        return not at or datetime.fromisoformat(at) < datetime.now(timezone.utc) - timedelta(days=TERMS_STALE_DAYS)
    except ValueError:
        return True


# --- classification clearance (SPEC-14 S14-R-05) --------------------------------------------

class NotCleared(RuntimeError):
    """No usable model may receive content of this classification."""


def is_local(m: ModelEntry) -> bool:
    """Runs on this host: an OpenAI-compatible server on localhost, or a local provider (Ollama, vLLM)."""
    from urllib.parse import urlparse

    if m.base_url:
        return (urlparse(m.base_url).hostname or "") in {"localhost", "127.0.0.1", "::1"}
    return m.kind == "llm" and provider_of(m.model_id) in LOCAL_PROVIDERS


def clearance(m: ModelEntry) -> str:
    """The highest classification a model may receive: set on the model, or by default `restricted` for local
    models and `internal` for every hosted one. The offline simulator sends nothing anywhere. Secret content
    goes only to a model an administrator cleared for `secret`."""
    if m.kind == "offline":
        return "secret"
    if m.clearance:
        return m.clearance
    return "restricted" if is_local(m) else "internal"


def cleared(m: ModelEntry, level: str) -> bool:
    from agent_company.learning import rank

    return rank(clearance(m)) >= rank(level)


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
        _migrate_gm(s)
        have_routes = set(s.scalars(select(Route.role)))
        from agent_company.org import ROLES

        for role in ROLES:
            if role not in have_routes:
                s.add(Route(role=role, model_name=DEFAULT_ROUTES.get(role, DEFAULT_ROUTE_OTHER),
                            escalation=DEFAULT_ESCALATIONS.get(role, "")))


def _migrate_gm(s) -> None:  # noqa: ANN001
    """Once: databases made before the GM became Claude Code by default get the new route."""
    from agent_company.db import Setting

    if s.get(Setting, "_migration_gm_claude_code") is not None:
        return
    r = s.get(Route, "general_manager")
    if r is not None and r.model_name == "Claude Opus 5.5":
        r.model_name = "Claude Code"
        r.escalation = r.escalation or "Claude Opus 5.5"
    s.add(Setting(key="_migration_gm_claude_code", value={"v": True}))


def agent_model(model_pref: str | None) -> tuple[str | None, str | None]:
    """A preference may name another agent ("agent:<id>"): it then uses that agent's model (or its role's route).

    Returns (model name or None, label). This is how the General Manager can be switched to an agent you created.
    """
    if not model_pref or not model_pref.startswith("agent:"):
        return model_pref, None
    from agent_company.db import Agent

    try:
        aid = int(model_pref.split(":", 1)[1])
    except ValueError:
        return None, None
    with session() as s:
        a = s.get(Agent, aid)
        if a is None:
            return None, None
        pref = a.model_pref if a.model_pref and not a.model_pref.startswith("agent:") else None
        if pref:
            return pref, f"model of agent {a.name}"
        r = s.get(Route, a.role)
        return (r.model_name if r else None), f"route of agent {a.name} ({a.role})"


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
        if m.session_id and not claude_session_file(m.session_id):
            return False, f"Claude Code session {m.session_id[:8]} not found on this host"
    elif m.kind == "codex":
        if not settings().enable_codex:
            return False, "set ENABLE_CODEX=1 to let agents run Codex"
        if not shutil.which("codex"):
            return False, "`codex` CLI not found on PATH"
    elif m.base_url:
        if m.api_key_env and not os.environ.get(m.api_key_env):
            return False, f"{m.api_key_env} is not set"
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


SESSION_RE = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"


def claude_session_file(session_id: str) -> str:
    """The transcript of a Claude Code session on this host (any project folder), or ''."""
    import re
    from pathlib import Path

    if not re.fullmatch(SESSION_RE, session_id):
        return ""
    root = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"
    return next((str(p) for p in root.glob(f"*/{session_id}.jsonl")), "")


def _choice(m: ModelEntry, reason: str) -> Choice:
    return Choice(name=m.name, kind=m.kind, model_id=m.model_id, reason=reason, base_url=m.base_url or "",
                  api_key_env=m.api_key_env or "", session_id=m.session_id or "")


def _choice_from_dict(m: dict[str, Any]) -> Choice:
    return Choice(name=m["name"], kind=m["kind"], model_id=m["model_id"], reason="", base_url=m.get("base_url") or "",
                  api_key_env=m.get("api_key_env") or "", session_id=m.get("session_id") or "")


def resolve_chain(role: str, model_pref: str | None = None, escalate: bool = False,
                  avoid_provider: str | None = None, classification: str = "internal") -> list[Choice]:
    """Every usable model for the role, best first, ending with the offline simulator.

    Only models cleared for `classification` are used (S14-R-05). When that rules out every real model that
    would otherwise have been used, NotCleared is raised: the simulator must not stand in for real work.

    Order: the escalation model (on retries), the agent's preference, the role's route,
    MODEL from .env. With `avoid_provider` (a reviewer checking a developer's work), an
    available API model from another provider is tried first, so the reviewer is not
    blind to the same model family's mistakes.
    """
    with session() as s:
        offline = s.scalars(select(ModelEntry).where(ModelEntry.kind == "offline")).first()
        if offline is None:
            raise RuntimeError("model catalog not seeded")
        if settings().executor == "offline":
            return [_choice(offline, "EXECUTOR=offline")]
        route = s.get(Route, role)
        candidates: list[tuple[str, str | None]] = []
        if escalate and route and route.escalation:
            candidates.append((f"escalation for {role}", route.escalation))
        pref, via = agent_model(model_pref)
        candidates += [(via or "agent preference", pref), (f"route for {role}", route.model_name if route else None)]
        default = settings().default_model
        if default:
            m = s.scalars(select(ModelEntry).where(ModelEntry.model_id == default)).first()
            candidates.append(("MODEL in .env", m.name if m else None))
        chain: list[Choice] = []
        skipped: list[str] = []
        blocked: list[str] = []
        for label, name in candidates:
            if not name or any(c.name == name for c in chain):
                continue
            m = s.scalars(select(ModelEntry).where(ModelEntry.name == name)).first()
            if m is None:
                skipped.append(f"{name}: not in catalog")
                continue
            ok, why = available(m)
            if ok and m.kind != "offline" and not cleared(m, classification):
                blocked.append(f"{name} (cleared for {clearance(m)})")
                skipped.append(f"{name}: not cleared for {classification}")
            elif ok:
                chain.append(_choice(m, label))
            else:
                skipped.append(f"{name}: {why}")
        if avoid_provider and (not chain or chain[0].provider == avoid_provider):
            for m in s.scalars(select(ModelEntry).where(ModelEntry.kind == "llm").order_by(ModelEntry.id)):
                c = _choice(m, f"different family from {avoid_provider}")
                if (c.provider != avoid_provider and available(m)[0] and cleared(m, classification)
                        and all(x.name != m.name for x in chain)):
                    chain.insert(0, c)
                    break
        if blocked and not any(c.kind != "offline" for c in chain):
            raise NotCleared(f"no model is cleared for {classification} content: {', '.join(blocked)}. Clear a "
                             "local model for it (Models), or lower the classification")
        chain.append(_choice(offline, "fallback (" + "; ".join(skipped) + ")" if skipped else "fallback"))
        return chain


def resolve(role: str, model_pref: str | None = None, escalate: bool = False,
            avoid_provider: str | None = None, classification: str = "internal") -> Choice:
    """Pick the model an agent of `role` should use right now."""
    return resolve_chain(role, model_pref, escalate, avoid_provider, classification)[0]


def list_models() -> list[dict[str, Any]]:
    with session() as s:
        return [model_dict(m) for m in s.scalars(select(ModelEntry).order_by(ModelEntry.id)).all()]


def list_routes() -> dict[str, str]:
    with session() as s:
        return {r.role: r.model_name for r in s.scalars(select(Route).order_by(Route.role))}


def list_escalations() -> dict[str, str]:
    with session() as s:
        return {r.role: r.escalation for r in s.scalars(select(Route).order_by(Route.role))}


def set_route(role: str, model_name: str, escalation: str | None = None) -> None:
    with session() as s:
        for name in [model_name] + ([escalation] if escalation else []):
            if not s.scalars(select(ModelEntry).where(ModelEntry.name == name)).first():
                raise ValueError(f"unknown model {name!r}")
        r = s.get(Route, role)
        if r is None:
            r = Route(role=role, model_name=model_name)
            s.add(r)
        r.model_name = model_name
        if escalation is not None:
            r.escalation = escalation


MODEL_FIELDS = {"name", "kind", "model_id", "input_cost", "output_cost", "daily_token_limit",
                "weekly_token_limit", "enabled", "notes", "base_url", "api_key_env", "session_id", "clearance"}


def add_model(data: dict[str, Any]) -> dict[str, Any]:
    terms = check_terms(data.get("terms") or {})
    data = {k: v for k, v in data.items() if k in MODEL_FIELDS}
    if data.get("clearance"):
        from agent_company.learning import check_level

        check_level(data["clearance"])
    if not data.get("name") or not data.get("kind"):
        raise ValueError("name and kind are required")
    if data["kind"] not in {"llm", "claude_code", "codex"}:
        raise ValueError("kind must be llm, claude_code or codex")
    if data["kind"] == "llm" and not data.get("model_id"):
        raise ValueError("model_id is required (LiteLLM-style provider/model)")
    _check_endpoint(data)
    with session() as s:
        if s.scalars(select(ModelEntry).where(ModelEntry.name == data["name"])).first():
            raise ValueError("a model with that name exists")
        m = ModelEntry(**{"model_id": "", **data}, terms=terms)
        s.add(m)
        s.flush()
        return model_dict(m)


def update_model(model_id: int, data: dict[str, Any]) -> dict[str, Any]:
    terms = check_terms(data["terms"]) if isinstance(data.get("terms"), dict) else None
    if data.get("clearance"):
        from agent_company.learning import check_level

        check_level(data["clearance"])
    with session() as s:
        m = s.get(ModelEntry, model_id)
        if m is None:
            raise KeyError(model_id)
        fields = MODEL_FIELDS - ({"name", "kind", "model_id"} if m.builtin else set())
        _check_endpoint(data)
        for k, v in data.items():
            if k in fields:
                setattr(m, k, v)
        if terms is not None:  # a new dict: in-place changes of a JSON column are not seen
            m.terms = {k: v for k, v in {**(m.terms or {}), **terms}.items() if v not in ("", None)}
        s.flush()
        return model_dict(m)


def _check_endpoint(data: dict[str, Any]) -> None:
    import re

    url = data.get("base_url") or ""
    if url and not re.match(r"^https?://[\w.-]+(:\d+)?(/[\w./-]*)?$", url):
        raise ValueError("base_url must be an http(s) URL such as http://127.0.0.1:8080/v1")
    env = data.get("api_key_env") or ""
    if env and not re.match(r"^[A-Z][A-Z0-9_]{1,63}$", env):
        raise ValueError("api_key_env must be an environment variable name such as LOCAL_LLM_KEY")
    sid = data.get("session_id") or ""
    if sid and not re.fullmatch(SESSION_RE, sid):
        raise ValueError("session_id must be a Claude Code session id such as 3067d536-a81c-492b-9302-1b000247bdea")


def test_model(model_id: int) -> dict[str, Any]:
    """Send a tiny prompt through the model and report the answer, tokens and time."""
    from agent_company import executors

    with session() as s:
        m = s.get(ModelEntry, model_id)
        if m is None:
            raise KeyError(model_id)
        ok, why = available(m)
        choice = _choice(m, "test")
    if not ok:
        return {"ok": False, "error": why}
    workdir = settings().worktrees_dir / "_model-test"
    workdir.mkdir(parents=True, exist_ok=True)
    req = executors.Request(purpose="test", role="test", system="You are a connectivity test.",
                            prompt="Reply with the single word OK.", workdir=workdir)
    res = executors.for_choice(choice, 120).run(req)
    usage.record(model_name=choice.name, kind=choice.kind, input_tokens=res.input_tokens,
                 output_tokens=res.output_tokens, cost=res.cost, duration=res.duration, ok=res.ok,
                 error=res.error, purpose="test", res=res)
    from agent_company import registry

    registry.recheck_model(choice.name)  # agents asking for this model first may now be degraded, or healthy
    return {"ok": res.ok, "reply": res.text[:500], "error": res.error[:500], "seconds": round(res.duration, 2),
            "input_tokens": res.input_tokens, "output_tokens": res.output_tokens}


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
        # from the catalog, not verified: a ":free" model is a free tier with daily caps, not free production use
        "terms": {"billing": "free_tier" if row and row["free"] else "api",
                  "context_window": int(row["context_length"] or 0) if row else 0},
    })


# --- OpenAI-compatible provider catalogs (Groq, Mistral, Cohere, SambaNova) ---------------

PROVIDERS: dict[str, dict[str, str]] = {
    "groq": {"label": "Groq", "base_url": "https://api.groq.com/openai/v1", "key": "GROQ_API_KEY"},
    "mistral": {"label": "Mistral", "base_url": "https://api.mistral.ai/v1", "key": "MISTRAL_API_KEY"},
    "cohere": {"label": "Cohere", "base_url": "https://api.cohere.ai/compatibility/v1", "key": "COHERE_API_KEY"},
    "sambanova": {"label": "SambaNova", "base_url": "https://api.sambanova.ai/v1", "key": "SAMBANOVA_API_KEY"},
}
NOT_CHAT = ("whisper", "tts", "speech", "transcri", "embed", "guard", "moderation", "ocr", "rerank", "distil-whisper")
_provider_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def providers() -> list[dict[str, Any]]:
    return [{"id": k, "label": v["label"], "base_url": v["base_url"], "key": v["key"],
             "key_set": bool(os.environ.get(v["key"]))} for k, v in PROVIDERS.items()]


def provider_catalog(provider: str, query: str = "", client: httpx.Client | None = None) -> list[dict[str, Any]]:
    """The provider's chat models (its /models list, cached 10 minutes)."""
    if provider not in PROVIDERS:
        raise KeyError(provider)
    cfg = PROVIDERS[provider]
    cached = _provider_cache.get(provider)
    if cached is None or time.time() - cached[0] > 600:
        key = os.environ.get(cfg["key"], "")
        if not key and client is None:
            raise ValueError(f"{cfg['key']} is not set; the {cfg['label']} model list needs a key")
        c = client or httpx.Client(timeout=20)
        resp = c.get(cfg["base_url"].rstrip("/") + "/models", headers={"Authorization": f"Bearer {key}"})
        resp.raise_for_status()
        body = resp.json()
        rows = []
        for m in body.get("data", body.get("models", [])):
            mid = m.get("id") or m.get("name")
            if not mid or any(w in mid.lower() for w in NOT_CHAT) or m.get("deprecated") or m.get("active") is False:
                continue
            p = m.get("pricing") or {}
            rows.append({"id": mid, "name": m.get("name") or mid,
                         "context_length": m.get("context_window") or m.get("context_length") or m.get("max_context_length"),
                         "input_cost": round(float(p.get("prompt") or 0) * 1_000_000, 4),
                         "output_cost": round(float(p.get("completion") or 0) * 1_000_000, 4)})
        _provider_cache[provider] = (time.time(), rows)
    rows = _provider_cache[provider][1]
    q = query.lower().strip()
    return sorted([r for r in rows if not q or q in r["id"].lower() or q in r["name"].lower()], key=lambda r: r["id"])


def add_provider_model(provider: str, model: str, name: str | None = None) -> dict[str, Any]:
    if provider not in PROVIDERS:
        raise KeyError(provider)
    cfg = PROVIDERS[provider]
    row = next((r for r in _provider_cache.get(provider, (0, []))[1] if r["id"] == model), None)
    return add_model({"name": name or f"{cfg['label']} · {model}", "kind": "llm", "model_id": f"openai/{model}",
                      "base_url": cfg["base_url"], "api_key_env": cfg["key"],
                      "input_cost": row["input_cost"] if row else 0.0, "output_cost": row["output_cost"] if row else 0.0,
                      "notes": f"{cfg['label']} (OpenAI-compatible API). Needs {cfg['key']}.",
                      "terms": {"billing": "api", "context_window": int(row["context_length"] or 0) if row else 0}})
