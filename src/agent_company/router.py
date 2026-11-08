"""Model Router: the model catalog, role routes and availability checks.

Resolution order for an agent: its own `model_pref` -> the route for its role ->
the `MODEL` from `.env` -> the offline executor. A model is skipped when its
credentials or CLI are missing, it is disabled, or it hit its usage limit, so a
goal always runs (offline, at worst) and the reason is recorded.
"""

from __future__ import annotations

import os
import re
import shutil
import threading
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select, update

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
    account: str = ""
    requested: str = ""  # the model assigned to the work (agent's own, else the route's), set by resolve_chain

    @property
    def provider(self) -> str:
        return {"claude_code": "anthropic", "codex": "openai", "copilot": "github", "offline": "offline"}.get(
            self.kind, "local" if self.base_url else provider_of(self.model_id))


CLI_KINDS = {"claude_code", "codex", "copilot"}


def model_type(m: ModelEntry) -> str:
    """How the Models page groups a model: API (a hosted provider), CLI (an agent CLI on this host), Local."""
    if m.kind == "offline":
        return "offline"
    if m.kind in CLI_KINDS:
        return "CLI"
    return "Local" if is_local(m) else "API"


def model_dict(m: ModelEntry) -> dict[str, Any]:
    ok, why = available(m)
    return {"id": m.id, "name": m.name, "kind": m.kind, "type": model_type(m), "model_id": m.model_id,
            "key_set": bool(m.api_key_enc), "key_hint": key_hint(model_key(m)) if m.api_key_enc else "",
            "status": m.status or {},
            "input_cost": m.input_cost, "output_cost": m.output_cost,
            "daily_token_limit": m.daily_token_limit, "weekly_token_limit": m.weekly_token_limit,
            "enabled": m.enabled, "builtin": m.builtin, "notes": m.notes, "base_url": m.base_url,
            "api_key_env": m.api_key_env, "session_id": m.session_id, "account": m.account, "available": ok, "unavailable_reason": why,
            "clearance": clearance(m), "clearance_set": bool(m.clearance), "signin": m.signin or {},
            "terms": m.terms or {}, "terms_due": terms_due(m),
            "limited_until": lu.isoformat(timespec="seconds") if (lu := limited_until(m)) else None,
            "limited_reason": m.limited_reason if lu else ""}


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


# --- plan and quota limits a provider reports on a run ------------------------------------------
# Claude Code: "You've hit your session limit · resets 9:50pm (Europe/Berlin)"; Codex: "You've hit your usage limit
# ... try again in 2 hours 5 minutes" / usage_limit_reached; API: insufficient_quota, credit balance too low. A
# plain 429 is transient (loop.TRANSIENT) and retried; these are not, the model waits until its reset. OpenAI's
# per-minute "Rate limit reached for <model> ... try again in 1.2s" is such a plain 429, not a plan limit.
LIMIT_RE = re.compile(r"(?i)hit your (?:\w+ )?limit|usage[ _]limit[ _](?:reached|exceeded)|usage_limit_reached"
                      r"|rate_limit_reached|(?<!rate )limit (?:reached|exceeded) for|quota (?:exceeded|exhausted)"
                      r"|insufficient_quota|credit balance is too low|out of (?:credits|extra usage)")
LIMIT_DEFAULT_MINUTES = 60  # when the message names no reset time
LIMIT_MAX_DAYS = 8


def is_limit_error(error: str | None) -> bool:
    return bool(error) and bool(LIMIT_RE.search(error or ""))


def limit_reset(error: str, at: datetime | None = None) -> datetime:
    """When a limit named in `error` resets (UTC): 'resets 9:50pm (Europe/Berlin)', 'resets at 21:50',
    'try again in 2 hours 5 minutes', 'try again at 3:05 PM'; else LIMIT_DEFAULT_MINUTES from now."""
    from zoneinfo import ZoneInfo

    at = at or datetime.now(UTC)
    m = re.search(r"(?i)try again in ((?:\d+\s*(?:days?|d|hours?|h|minutes?|mins?|m|seconds?|s)\b[\s,and]*)+)", error)
    if m:
        units = {"d": 86400, "h": 3600, "m": 60, "s": 1}
        secs = sum(int(n) * units[u[0].lower()] for n, u in re.findall(r"(\d+)\s*([a-zA-Z]+)", m.group(1)))
        if secs:
            return at + timedelta(seconds=min(secs, LIMIT_MAX_DAYS * 86400))
    m = re.search(r"(?i)(?:resets?|try again)\s+(?:on\s+)?(?:[a-z]{3,9},?\s+)?"
                  r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?"
                  r"\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)?\s*(?:\(([\w/+-]+)\))?", error)
    if m:  # a weekly limit: "resets Nov 8 at 3pm (Europe/Berlin)"
        month = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"].index(
            m.group(1).lower()) + 1
        hour, minute = int(m.group(3)), int(m.group(4) or 0)
        ampm = (m.group(5) or "").lower().replace(".", "")
        hour += 12 if ampm == "pm" and hour < 12 else -12 if ampm == "am" and hour == 12 else 0
        try:
            tz = ZoneInfo(m.group(6)) if m.group(6) else UTC
        except Exception:  # noqa: BLE001
            tz = UTC
        try:
            local = at.astimezone(tz)
            reset = local.replace(month=month, day=int(m.group(2)), hour=hour, minute=minute, second=0, microsecond=0)
            if reset < local - timedelta(days=1):  # December's message read in January
                reset = reset.replace(year=reset.year + 1)
            return min(reset.astimezone(UTC), at + timedelta(days=LIMIT_MAX_DAYS))
        except ValueError:
            pass
    m = re.search(r"(?i)(?:resets?|try again)\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)?\s*(?:\(([\w/+-]+)\))?",
                  error)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        ampm = (m.group(3) or "").lower().replace(".", "")
        if ampm == "pm" and hour < 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        try:
            tz = ZoneInfo(m.group(4)) if m.group(4) else UTC
        except Exception:  # noqa: BLE001 - an unknown zone name: read the time as UTC
            tz = UTC
        if hour < 24 and minute < 60:
            local = at.astimezone(tz)
            reset = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if reset <= local:
                reset += timedelta(days=1)
            return reset.astimezone(UTC)
    return at + timedelta(minutes=LIMIT_DEFAULT_MINUTES)


def mark_limited(model_name: str, error: str) -> datetime | None:
    """A run of `model_name` hit its plan or quota limit: the router skips it until the reset."""
    until = limit_reset(error)
    with session() as s:
        m = s.scalars(select(ModelEntry).where(ModelEntry.name == model_name)).first()
        if m is None or m.kind == "offline":
            return None
        m.limited_until, m.limited_reason = until, error.strip()[:300]
    from agent_company.db import log_event

    log_event("model", f"{model_name} is at its usage limit until {until:%Y-%m-%d %H:%M} UTC: its routes' fallbacks "
              "take the work", model=model_name)
    return until


def clear_limit(model_name: str) -> None:
    with session() as s:
        m = s.scalars(select(ModelEntry).where(ModelEntry.name == model_name)).first()
        if m is not None and m.limited_until is not None:
            m.limited_until, m.limited_reason = None, ""


def limited_until(m: ModelEntry, at: datetime | None = None) -> datetime | None:
    if m.limited_until is None:
        return None
    until = m.limited_until if m.limited_until.tzinfo else m.limited_until.replace(tzinfo=UTC)
    return until if until > (at or datetime.now(UTC)) else None


def any_limited() -> bool:
    with session() as s:
        return any(limited_until(m) for m in s.scalars(select(ModelEntry).where(ModelEntry.limited_until.is_not(None))))


def next_reset() -> datetime | None:
    """The earliest reset among the models at their usage limit now."""
    with session() as s:
        times = [u for m in s.scalars(select(ModelEntry).where(ModelEntry.limited_until.is_not(None)))
                 if (u := limited_until(m))]
    return min(times) if times else None


def available(m: ModelEntry, ignore_limit: bool = False) -> tuple[bool, str]:
    if not m.enabled:
        return False, "disabled"
    if m.kind == "offline":
        return True, ""
    until = None if ignore_limit else limited_until(m)
    if until is not None:
        return False, f"usage limit reached; resets {until:%Y-%m-%d %H:%M} UTC"
    if m.kind == "claude_code":
        if not settings().enable_claude_code:
            return False, "set ENABLE_CLAUDE_CODE=1 to let agents run Claude Code"
        if not shutil.which("claude"):
            return False, "`claude` CLI not found on PATH"
        if m.account and not (claude_config_dir(m.account) / ".credentials.json").is_file():
            return False, f"no Claude Code sign-in in data/claude-accounts/{m.account}"
        if m.session_id and not claude_session_file(m.session_id, m.account):
            return False, f"Claude Code session {m.session_id[:8]} not found on this host"
    elif m.kind == "codex":
        if not settings().enable_codex:
            return False, "set ENABLE_CODEX=1 to let agents run Codex"
        if not shutil.which("codex"):
            return False, "`codex` CLI not found on PATH"
        if m.account and not (codex_home(m.account) / "auth.json").is_file():
            return False, f"no Codex sign-in in data/codex-accounts/{m.account}"
    elif m.kind == "copilot":
        if not settings().enable_copilot:
            return False, "set ENABLE_COPILOT=1 to let agents run GitHub Copilot"
        if not shutil.which("copilot"):
            return False, "`copilot` CLI not found on PATH"
    elif m.api_key_enc:  # its own key, entered on the Models page
        if not model_key(m):
            return False, "its stored API key cannot be decrypted (the server's key changed): enter it again"
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


ACCOUNT_RE = r"[a-z0-9][a-z0-9._-]{0,63}"


ACCOUNT_KINDS = {"claude_code": "claude", "codex": "codex"}  # CLI kinds with a sign-in per model: data/<x>-accounts/


def account_dir(tool: str, account: str) -> Path:
    """A CLI model's own sign-in folder: data/claude-accounts/<account>/ or data/codex-accounts/<account>/."""
    import re

    if not re.fullmatch(ACCOUNT_RE, account) or ".." in account:
        raise ValueError(f"invalid {CLI_TOOLS[tool]['label']} account {account!r}")
    return settings().data_dir / f"{tool}-accounts" / account


def claude_config_dir(account: str) -> Path:
    """A claude_code model's own Claude Code sign-in (its CLAUDE_CONFIG_DIR): data/claude-accounts/<account>/."""
    return account_dir("claude", account)


def claude_session_file(session_id: str, account: str = "") -> str:
    """The transcript of a Claude Code session on this host (any project folder), or ''. Sessions belong to a
    config folder: a model with its own account resumes only its own (an agent's `resume` id too)."""
    import re

    if not re.fullmatch(SESSION_RE, session_id):
        return ""
    home = claude_config_dir(account) if account else Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    root = home / "projects"
    return next((str(p) for p in root.glob(f"*/{session_id}.jsonl")), "")


def _choice(m: ModelEntry, reason: str) -> Choice:
    return Choice(name=m.name, kind=m.kind, model_id=m.model_id, reason=reason, base_url=m.base_url or "",
                  api_key_env=m.api_key_env or "", session_id=m.session_id or "", account=m.account or "")


def _choice_from_dict(m: dict[str, Any]) -> Choice:
    return Choice(name=m["name"], kind=m["kind"], model_id=m["model_id"], reason="", base_url=m.get("base_url") or "",
                  api_key_env=m.get("api_key_env") or "", session_id=m.get("session_id") or "",
                  account=m.get("account") or "")


def resolve_chain(role: str, model_pref: str | None = None, escalate: bool = False,
                  avoid_provider: str | None = None, classification: str = "internal",
                  other_cli: bool = True) -> list[Choice]:
    """Every usable model for the role, best first, ending with the offline simulator.

    Only models cleared for `classification` are used (S14-R-05). When that rules out every real model that
    would otherwise have been used, NotCleared is raised: the simulator must not stand in for real work.

    Order: the escalation model (on retries), the agent's preference, the role's route, the route's fallbacks,
    MODEL from .env. A model at its usage limit (`mark_limited`) is skipped until its reset; when that leaves no
    real model, the simulator's reason names the limit and `limit_blocked` tells the loop not to use it. With `avoid_provider` (a reviewer checking a developer's work), an
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
        # the role's fallbacks take the work when the models above are at their usage limit or fail
        candidates += [(f"fallback for {role}", n.strip()) for n in ((route.fallback or "") if route else "").split(",")
                       if n.strip()]
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
            other = next((c for c in chain if c.provider not in (avoid_provider, "offline")
                          and (other_cli or c.kind not in CLI_KINDS)), None)
            if other is not None:  # a model of another family is in the chain already (a fallback): it goes first
                chain.remove(other)
                chain.insert(0, replace(other, reason=f"different family from {avoid_provider} ({other.reason})"))
                avoid_provider = None
        if avoid_provider and (not chain or chain[0].provider == avoid_provider):
            # another family: an API model, else an agent CLI on another sign-in (Codex reviewing Claude's work)
            for m in s.scalars(select(ModelEntry).where(ModelEntry.kind.in_(("llm", *(CLI_KINDS if other_cli else ()))))
                               .order_by(ModelEntry.kind != "llm", ModelEntry.id)):
                c = _choice(m, f"different family from {avoid_provider}")
                if (c.provider != avoid_provider and available(m)[0] and cleared(m, classification)
                        and all(x.name != m.name for x in chain)):
                    chain.insert(0, c)
                    break
        if blocked and not any(c.kind != "offline" for c in chain):
            raise NotCleared(f"no model is cleared for {classification} content: {', '.join(blocked)}. Clear a "
                             "local model for it (Models), or lower the classification")
        chain.append(_choice(offline, "fallback (" + "; ".join(skipped) + ")" if skipped else "fallback"))
        requested = pref or (route.model_name if route else "")
        return [replace(c, requested=requested) for c in chain]


def scratch_dir(role: str) -> Path:
    """Where an agent CLI answers a request that has no worktree: an agent CLI runs only inside data/worktrees, so
    an empty scratch folder, read-only for a request that does not write (Claude Code without Edit/Write, Codex
    --sandbox read-only); the material is in the prompt."""
    path = settings().worktrees_dir / f"_router-{role}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def run(role: str, req: Any, model_pref: str | None = None, *, first: Choice | None = None,
        avoid_provider: str | None = None, classification: str = "internal", timeout: int | None = None,
        agent_id: int | None = None, goal_id: int | None = None) -> tuple[Any, Choice]:
    """Send `req` to the role's model and return (result, choice): what an agent that calls a model directly and then
    works with the answer through its own tools (the research room) uses. A failed run, a usage limit above all,
    moves the request to the chain's next model (the route's fallbacks: Codex when Claude is at its limit); every run
    is recorded. With `first` that model is tried before the chain."""
    from agent_company import executors

    chain = resolve_chain(role, model_pref, avoid_provider=avoid_provider, classification=classification)
    blocked = limit_blocked(chain)
    if blocked and first is None:
        return executors.Result(text="", ok=False, error=f"usage limit: {blocked}"), chain[0]
    if first is not None:
        chain = [first, *[c for c in chain if c.name != first.name]]
    if chain[0].kind != "offline":  # once a real model is tried, the simulator never stands in for it
        chain = [c for c in chain if c.kind != "offline"]
    if req.workdir is None and any(c.kind in CLI_KINDS for c in chain[:3]):
        req.workdir = scratch_dir(role)
    res, choice = None, chain[0]
    for choice in chain[:3]:
        res = executors.for_choice(choice, timeout or settings().command_timeout).run(req)
        usage.record(model_name=choice.name, kind=choice.kind, input_tokens=res.input_tokens,
                     output_tokens=res.output_tokens, cost=res.cost, duration=res.duration, ok=res.ok,
                     error=res.error, agent_id=agent_id, purpose=req.purpose, goal_id=goal_id, res=res)
        if res.ok:
            break
    return res, choice


EXPLAINED = ("fallback for ", "escalation for ", "different family from ")


def trace_issues(trace: dict[str, Any]) -> list[str]:
    """Where a run's model disagrees with the one assigned to the work, unless configuration explains it: a route's
    fallback or escalation, or a reviewer of another family (verification phase 2). Empty: they agree."""
    out: list[str] = []
    requested, used, route = trace.get("requested") or "", trace.get("model") or "", trace.get("route") or ""
    if trace.get("provider") == "offline" and requested and requested != used:
        out.append(f"the offline simulator stood in for {requested} ({route})")
    elif requested and used and used != requested and not route.startswith(EXPLAINED):
        out.append(f"{used} ran instead of the assigned {requested} without a configured fallback ({route})")
    served, mid = trace.get("served") or "", (trace.get("model_id") or "").rsplit("/", 1)[-1]
    if served and mid and not (served.startswith(mid) or mid.startswith(served)):
        out.append(f"the provider answered as {served}, not {mid}")
    return out


def limit_blocked(chain: list[Choice]) -> str:
    """Why the chain has no real model because of usage limits ('' when it has one, or for another reason)."""
    if chain and chain[0].kind == "offline" and "usage limit reached" in chain[0].reason:
        return chain[0].reason.removeprefix("fallback (").removesuffix(")")
    return ""


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


def list_fallbacks() -> dict[str, list[str]]:
    with session() as s:
        return {r.role: [n.strip() for n in (r.fallback or "").split(",") if n.strip()]
                for r in s.scalars(select(Route).order_by(Route.role))}


def set_fallback(role: str, models: list[str]) -> list[str]:
    """The models that take `role`'s work, in order, when its route's model is at its usage limit or fails."""
    names = [n.strip() for n in models if n and n.strip()]
    with session() as s:
        for name in names:
            m = s.scalars(select(ModelEntry).where(ModelEntry.name == name)).first()
            if m is None:
                raise ValueError(f"unknown model {name!r}")
            if m.kind == "offline":
                raise ValueError("the offline simulator is always the last fallback; it is not listed")
        r = s.get(Route, role)
        if r is None:
            raise ValueError(f"no route for role {role!r}")
        r.fallback = ",".join(dict.fromkeys(names))[:512]
        return [n for n in r.fallback.split(",") if n]


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


# --- a model's own API key ---------------------------------------------------------------------
KEY_PURPOSE = b"ac-model-key"


def key_hint(key: str) -> str:
    """Enough of a key to recognise it (sk-...YSUA), never enough to use it."""
    return f"{key[:3]}...{key[-4:]}" if len(key) >= 12 else "set" if key else ""


def model_key(m: ModelEntry) -> str:
    """The key a run of this model sends: its own (encrypted in the DB), else its environment variable's."""
    from agent_company.transport import decrypt_text

    if m.api_key_enc:
        return decrypt_text(m.api_key_enc, KEY_PURPOSE) or ""
    return os.environ.get(m.api_key_env, "") if m.api_key_env else ""


def key_for(name: str) -> str:
    """model_key by model name: what an executor asks for (a Choice never carries the key)."""
    with session() as s:
        m = s.scalars(select(ModelEntry).where(ModelEntry.name == name)).first()
        return model_key(m) if m is not None else ""


def _set_key(m: ModelEntry, key: str) -> None:
    from agent_company.transport import encrypt_text

    key = key.strip()
    if key and (len(key) > 512 or any(c.isspace() for c in key)):
        raise ValueError("the API key must be one line without spaces")
    m.api_key_enc = encrypt_text(key, KEY_PURPOSE) if key else ""


MODEL_FIELDS = {"name", "kind", "model_id", "input_cost", "output_cost", "daily_token_limit",
                "weekly_token_limit", "enabled", "notes", "base_url", "api_key_env", "session_id", "account", "clearance"}


def add_model(data: dict[str, Any]) -> dict[str, Any]:
    terms = check_terms(data.get("terms") or {})
    key = str(data.get("api_key") or "")
    data = {k: v for k, v in data.items() if k in MODEL_FIELDS}
    if data.get("clearance"):
        from agent_company.learning import check_level

        check_level(data["clearance"])
    if not data.get("name") or not data.get("kind"):
        raise ValueError("name and kind are required")
    if data["kind"] not in {"llm", *CLI_KINDS}:
        raise ValueError("kind must be llm, claude_code, codex or copilot")
    if data["kind"] == "llm" and not data.get("model_id"):
        raise ValueError("model_id is required (LiteLLM-style provider/model)")
    if data.get("account") and data["kind"] not in ACCOUNT_KINDS:
        raise ValueError("account is a Claude Code or Codex sign-in: only for claude_code and codex models")
    _check_endpoint(data)
    with session() as s:
        if s.scalars(select(ModelEntry).where(ModelEntry.name == data["name"])).first():
            raise ValueError("a model with that name exists")
        m = ModelEntry(**{"model_id": "", **data}, terms=terms,
                       signin=cli_signin(data["kind"], data.get("account") or "") if data["kind"] in CLI_KINDS else {})
        if key:
            if m.kind != "llm":
                raise ValueError("an API key is for API and local models; a CLI uses its own sign-in")
            _set_key(m, key)
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
        if m.kind == "offline" and data.get("enabled") is False:
            raise ValueError("the offline simulator is the last fallback: it stays enabled")
        if "api_key" in data:  # "" removes the model's own key
            if data["api_key"] and m.kind != "llm":
                raise ValueError("an API key is for API and local models; a CLI uses its own sign-in")
            _set_key(m, str(data["api_key"] or ""))
        if data.get("account") and data.get("kind", m.kind) not in ACCOUNT_KINDS:
            raise ValueError("account is a Claude Code or Codex sign-in: only for claude_code and codex models")
        _check_endpoint({**data, "kind": data.get("kind", m.kind)})
        renamed = None
        if "name" in data:
            data = {**data, "name": str(data["name"] or "").strip()}
            if data["name"] != m.name:
                if m.builtin:
                    raise ValueError("a built-in model keeps its name")
                if not data["name"]:
                    raise ValueError("name is required")
                if s.scalars(select(ModelEntry).where(ModelEntry.name == data["name"])).first():
                    raise ValueError("a model with that name exists")
                renamed = (m.name, data["name"])
        for k, v in data.items():
            if k in fields:
                setattr(m, k, v)
        if terms is not None:  # a new dict: in-place changes of a JSON column are not seen
            m.terms = {k: v for k, v in {**(m.terms or {}), **terms}.items() if v not in ("", None)}
        if renamed:  # models are referenced by name: routes, agents' own models, and the runs quotas count
            from agent_company.db import Agent, Run

            old, new = renamed
            for r in s.scalars(select(Route).where((Route.model_name == old) | (Route.escalation == old))):
                r.model_name = new if r.model_name == old else r.model_name
                r.escalation = new if r.escalation == old else r.escalation
            for r in s.scalars(select(Route).where(Route.fallback.contains(old))):
                r.fallback = ",".join(new if n == old else n for n in r.fallback.split(","))
            for a in s.scalars(select(Agent).where(Agent.model_pref == old)):
                a.model_pref = new
            s.execute(update(Run).where(Run.model_name == old).values(model_name=new))
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
    if data.get("account"):
        tool = ACCOUNT_KINDS.get(data.get("kind") or "", "claude")
        try:
            account_dir(tool, data["account"])
        except ValueError:
            raise ValueError(f"account must be a folder name in data/{tool}-accounts/ such as {tool}-2") from None


def test_model(model_id: int) -> dict[str, Any]:
    """Send a tiny prompt through the model and report the answer, tokens and time, and what the provider says
    about its usage (`status`, kept on the model for the Models page)."""
    from agent_company import executors

    with session() as s:
        m = s.get(ModelEntry, model_id)
        if m is None:
            raise KeyError(model_id)
        ok, why = available(m)
        if not ok and limited_until(m) is not None:  # a Test shows whether the limit is gone before its reset time
            ok, why = available(m, ignore_limit=True)
        choice = _choice(m, "test")
        key = model_key(m)
    if not ok:
        status = _save_status(model_id, {"ok": False, "error": why, **provider_status(choice, key)})
        return {"ok": False, "error": why, "status": status}
    workdir = settings().worktrees_dir / "_model-test"
    workdir.mkdir(parents=True, exist_ok=True)
    req = executors.Request(purpose="test", role="test", system="You are a connectivity test.",
                            prompt="Reply with the single word OK.", workdir=workdir)
    res = executors.for_choice(choice, 120).run(req)
    usage.record(model_name=choice.name, kind=choice.kind, input_tokens=res.input_tokens,
                 output_tokens=res.output_tokens, cost=res.cost, duration=res.duration, ok=res.ok,
                 error=res.error, purpose="test", res=res)
    from agent_company import registry

    if res.ok:
        clear_limit(choice.name)
    registry.recheck_model(choice.name)  # agents asking for this model first may now be degraded, or healthy
    status = _save_status(model_id, {"ok": res.ok, "error": res.error[:300], "seconds": round(res.duration, 2),
                                     **provider_status(choice, key)})
    return {"ok": res.ok, "reply": res.text[:500], "error": res.error[:500], "seconds": round(res.duration, 2),
            "input_tokens": res.input_tokens, "output_tokens": res.output_tokens, "status": status}


def _save_status(model_id: int, status: dict[str, Any]) -> dict[str, Any]:
    from datetime import datetime, timezone

    status = {**status, "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    _info_cache.clear()  # the Models page reads the provider again
    with session() as s:
        m = s.get(ModelEntry, model_id)
        if m is not None:
            m.status = status
    return status


# --- what a provider reports about a model's usage --------------------------------------------
# `limits`: [{label, used_percent, resets_at (epoch seconds) or None}], the plan's or key's own windows.

OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/key"


def provider_status(choice: Choice, key: str = "", client: httpx.Client | None = None) -> dict[str, Any]:
    """Current usage from the provider, best effort: never raises, an unreachable provider is reported."""
    try:
        if choice.kind == "codex":  # the model's own sign-in (data/codex-accounts/<account>), else the server's
            return codex_limits(codex_home(choice.account) if choice.account else None)
        if choice.kind == "llm" and choice.model_id.startswith("openrouter/") and not choice.base_url:
            return openrouter_key_status(key or os.environ.get("OPENROUTER_API_KEY", ""), client)
        if choice.kind == "llm" and choice.base_url:
            ids = local_models(choice.base_url, key, client)
            return {"source": "server", "detail": f"server answers; serves {', '.join(ids[:5]) or 'no models'}"}
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        why = "the key was refused" if code in (401, 403) else "rate limited" if code == 429 else "the provider failed"
        return {"source": "error", "detail": f"usage not available: {why} (HTTP {code})"}
    except (httpx.HTTPError, ValueError, OSError) as e:
        return {"source": "error", "detail": f"usage not available: {type(e).__name__}: {str(e)[:150]}"}
    return {}


def _next_reset(period: str | None, t: float | None = None) -> float | None:
    """The UTC start of the next day, week (Monday) or month: when OpenRouter resets a key's limit."""
    from datetime import datetime, timedelta, timezone

    now_ = datetime.fromtimestamp(t or time.time(), timezone.utc)
    day = now_.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "daily":
        return (day + timedelta(days=1)).timestamp()
    if period == "weekly":
        return (day + timedelta(days=7 - day.weekday())).timestamp()
    if period == "monthly":
        first = day.replace(day=1)
        return (first.replace(year=first.year + 1, month=1) if first.month == 12 else first.replace(month=first.month + 1)).timestamp()
    return None


def openrouter_key_status(key: str, client: httpx.Client | None = None) -> dict[str, Any]:
    """OpenRouter's account of this key: credits used against its limit, and when that limit resets."""
    if not key:
        return {"source": "error", "detail": "no OpenRouter key: enter one on the model or set OPENROUTER_API_KEY"}
    c = client or httpx.Client(timeout=15)
    resp = c.get(OPENROUTER_KEY_URL, headers={"Authorization": f"Bearer {key}"})
    resp.raise_for_status()
    d = resp.json().get("data") or {}
    used, limit = float(d.get("usage") or 0), d.get("limit")
    out: dict[str, Any] = {"source": "openrouter", "free_tier": bool(d.get("is_free_tier")),
                           "detail": f"${used:.4f} used" + (f" of ${float(limit):.2f}" if limit else ", no credit limit")
                           + (" · free tier" if d.get("is_free_tier") else "")}
    if limit:
        period = d.get("limit_reset")  # daily|weekly|monthly: the limit counts that period's usage
        spent = d.get(f"usage_{period}") if period else None
        spent = used if spent is None else float(spent)
        out["limits"] = [{"label": f"credit limit ({period or 'no reset'})",
                          "used_percent": round(min(100.0, 100 * spent / float(limit)), 1),
                          "resets_at": _next_reset(period)}]
    return out


def window_label(minutes: int, fallback: str = "limit") -> str:
    """A limit window as Claude Code's /usage names it: 5-hour session, Weekly limit, ..."""
    if minutes == 300:
        return "5-hour session"
    if minutes == 10080:
        return "Weekly limit"
    if minutes and minutes % 1440 == 0:
        return f"{minutes // 1440}-day limit"
    if minutes and minutes % 60 == 0:
        return f"{minutes // 60}-hour limit"
    return f"{minutes}-minute limit" if minutes else fallback.capitalize() + " limit"


def codex_home(account: str = "") -> Path:
    """Codex's home (CODEX_HOME, else ~/.codex); a codex model's own ChatGPT sign-in: data/codex-accounts/<account>/."""
    if account:
        return account_dir("codex", account)
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def codex_limits(home: Path | None = None) -> dict[str, Any]:
    """The plan limits Codex last reported (its newest session file's `rate_limits`): used share and reset time of
    the primary and secondary windows. Codex reports them with every turn; a Test runs one."""
    import json

    root = (home or codex_home()) / "sessions"
    files = sorted(root.glob("*/*/*/rollout-*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:5]
    for f in files:
        last = None
        with f.open(errors="replace") as fh:
            for line in fh:
                if '"rate_limits"' in line:
                    last = line
        if last is None:
            continue
        ev = json.loads(last)
        rl = (ev.get("payload") or {}).get("rate_limits") or ev.get("rate_limits") or {}
        limits = []
        for which in ("primary", "secondary"):
            w = rl.get(which)
            if not isinstance(w, dict) or w.get("used_percent") is None:
                continue
            resets = w.get("resets_at") or (time.time() + float(w["resets_in_seconds"]) if w.get("resets_in_seconds") else None)
            limits.append({"label": window_label(int(w.get("window_minutes") or 0), which),
                           "used_percent": float(w["used_percent"]), "resets_at": float(resets) if resets else None})
        plan = rl.get("plan_type") or ""
        return {"source": "codex", "limits": limits, "plan": plan,
                "detail": (f"ChatGPT plan {plan}" if plan else "ChatGPT plan") + (" · limit reached" if rl.get("rate_limit_reached_type") else "")}
    return {"source": "codex", "detail": "Codex has reported no plan limits yet (run Test after signing in)"}


def delete_model(model_id: int) -> None:
    with session() as s:
        m = s.get(ModelEntry, model_id)
        if m is None:
            raise KeyError(model_id)
        if m.kind == "offline":
            raise ValueError("the offline simulator cannot be deleted")
        for r in s.scalars(select(Route).where(Route.model_name == m.name)):
            r.model_name = DEFAULT_ROUTE_OTHER if m.name != DEFAULT_ROUTE_OTHER else OFFLINE
        for r in s.scalars(select(Route).where(Route.escalation == m.name)):
            r.escalation = ""
        for r in s.scalars(select(Route).where(Route.fallback.contains(m.name))):
            r.fallback = ",".join(n for n in r.fallback.split(",") if n != m.name)
        s.delete(m)


# --- OpenRouter catalog ---------------------------------------------------------

_catalog: tuple[float, list[dict[str, Any]]] | None = None
_catalog_lock = threading.Lock()
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"


def capability(row: dict[str, Any]) -> float:
    """"Performance" in the catalog: OpenRouter publishes no benchmark, so this ranks by what a model lists that
    agents need (tool calling, reasoning, context, output length); within a tie the dearer model comes first."""
    import math

    return (3 * row.get("tools", False) + 2 * row.get("reasoning", False)
            + math.log2(max(1, (row.get("context_length") or 0) / 8192))
            + 0.5 * math.log2(max(1, (row.get("max_output") or 0) / 4096)))


def openrouter_catalog(query: str = "", free_only: bool = False, limit: int = 200,
                       client: httpx.Client | None = None, sort: str = "price") -> list[dict[str, Any]]:
    """OpenRouter's public model list (cached 10 minutes), with prices per 1M tokens; models that answer with text.
    `sort`: price (free first, then cheapest) or performance (`capability`, best first)."""
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
                params = m.get("supported_parameters") or []
                outputs = (m.get("architecture") or {}).get("output_modalities") or ["text"]
                rows.append({"id": m["id"], "name": m.get("name") or m["id"],
                             "context_length": m.get("context_length"),
                             "max_output": (m.get("top_provider") or {}).get("max_completion_tokens"),
                             "input_cost": round(cin, 4), "output_cost": round(cout, 4),
                             "free": cin == 0 and cout == 0, "tools": "tools" in params,
                             "reasoning": "reasoning" in params or bool(m.get("reasoning")),
                             "text": "text" in outputs})
            _catalog = (time.time(), rows)
        rows = _catalog[1]
    q = query.lower().strip()
    out = [r for r in rows if (not q or q in r["id"].lower() or q in r["name"].lower())
           and (not free_only or r["free"]) and r.get("text", True)]
    if sort == "performance":
        out = [{**r, "score": round(capability(r), 1)} for r in out]
        return sorted(out, key=lambda r: (-r["score"], -r["output_cost"], r["id"]))[:limit]
    return sorted(out, key=lambda r: (not r["free"], r["input_cost"], r["id"]))[:limit]


def add_openrouter_model(or_id: str, name: str | None = None, api_key: str = "", **costs: float) -> dict[str, Any]:
    """An OpenRouter model; `api_key` is stored with it (encrypted), else it uses OPENROUTER_API_KEY."""
    row = next((r for r in openrouter_catalog(or_id, limit=10_000) if r["id"] == or_id), None)
    return add_model({
        "name": name or f"OpenRouter · {row['name'] if row else or_id}", "api_key": api_key,
        "kind": "llm",
        "model_id": f"openrouter/{or_id}",
        "input_cost": costs.get("input_cost", row["input_cost"] if row else 0.0),
        "output_cost": costs.get("output_cost", row["output_cost"] if row else 0.0),
        "notes": "OpenRouter, with its own key." if api_key else "OpenRouter. Needs OPENROUTER_API_KEY.",
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


# --- local servers and agent CLIs (the Add model dialog) --------------------------------------

def local_models(url: str, key: str = "", client: httpx.Client | None = None) -> list[str]:
    """The model ids an OpenAI-compatible server (llama-server, LM Studio, vLLM, Ollama's /v1) serves."""
    _check_endpoint({"base_url": url})
    c = client or httpx.Client(timeout=8)
    resp = c.get(url.rstrip("/") + "/models", headers={"Authorization": f"Bearer {key}"} if key else {})
    resp.raise_for_status()
    body = resp.json()
    return [str(m.get("id") or m.get("name")) for m in body.get("data", body.get("models", []))
            if isinstance(m, dict) and (m.get("id") or m.get("name"))]


# How the server gets each CLI. They run on this host (Linux), as the server's user; signing in is interactive,
# so it happens in a terminal (Workspace). GitHub retired the `gh copilot` extension for the Copilot CLI.
CLI_TOOLS: dict[str, dict[str, Any]] = {
    "codex": {"label": "OpenAI Codex", "kind": "codex", "exe": "codex", "package": "@openai/codex",
              "steps": ["npm install -g @openai/codex", "codex --version",
                        "codex   # choose Sign in with ChatGPT, sign in with your ChatGPT account and finish the "
                        "browser sign-in; on a server without a browser: codex login --device-auth"],
              "enable": "enable_codex"},
    "copilot": {"label": "GitHub Copilot", "kind": "copilot", "exe": "copilot", "package": "@github/copilot",
                "steps": ["npm install -g @github/copilot", "copilot --version",
                          "copilot   # then /login and follow the GitHub device sign-in (needs a Copilot plan); "
                          "or set GH_TOKEN to a fine-grained token with the Copilot Requests permission"],
                "enable": "enable_copilot"},
    "claude": {"label": "Claude Code", "kind": "claude_code", "exe": "claude", "package": "@anthropic-ai/claude-code",
               "steps": ["npm install -g @anthropic-ai/claude-code", "claude --version", "claude   # then /login"],
               "enable": "enable_claude_code"},
}


def _signed_in(tool: str, account: str = "") -> bool:
    if tool == "codex":
        return (codex_home(account) / "auth.json").is_file()
    if tool == "claude":
        home = claude_config_dir(account) if account else Path(os.environ.get("CLAUDE_CONFIG_DIR")
                                                               or Path.home() / ".claude")
        return (home / ".credentials.json").is_file() or bool(os.environ.get("ANTHROPIC_API_KEY"))
    if any(os.environ.get(k) for k in ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN")):
        return True
    return bool(copilot_users()[0])


def copilot_users() -> tuple[list[Any], dict[str, Any]]:
    """The signed-in users and the last one from the Copilot CLI's config: 1.0.x writes `loggedInUsers` and
    `lastLoggedInUser`, earlier versions `logged_in_users` and `last_logged_in_user`."""
    d = copilot_config()
    users = d.get("loggedInUsers") or d.get("logged_in_users") or []
    last = d.get("lastLoggedInUser") or d.get("last_logged_in_user") or {}
    return (users if isinstance(users, list) else []), (last if isinstance(last, dict) else {})


def copilot_config() -> dict[str, Any]:
    """The Copilot CLI's config.json ($COPILOT_HOME, else ~/.copilot): JSON after `//` comment lines it writes."""
    import json

    home = os.environ.get("COPILOT_HOME") or Path(os.environ.get("XDG_CONFIG_HOME") or Path.home()) / ".copilot"
    try:
        text = (Path(home) / "config.json").read_text()
        d = json.loads("\n".join(x for x in text.splitlines() if not x.lstrip().startswith("//")))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def cli_status(tool: str, account: str = "") -> dict[str, Any]:
    """Whether the CLI is installed, its version, whether it is signed in (with `account`: that sign-in folder's) and
    enabled for agents, and the setup."""
    if tool not in CLI_TOOLS:
        raise KeyError(tool)
    if account and tool not in ACCOUNT_KINDS.values():
        raise ValueError(f"the {CLI_TOOLS[tool]['label']} CLI has no account folders")
    from agent_company.workspace import _version

    t = CLI_TOOLS[tool]
    exe = shutil.which(t["exe"])
    version = _version(exe)
    return {"tool": tool, "label": t["label"], "kind": t["kind"], "installed": bool(exe), "version": version,
            "signed_in": bool(exe) and _signed_in(tool, account), "enabled": bool(getattr(settings(), t["enable"])),
            "account": cli_signin(t["kind"], account) if exe else {}, "can_login": tool in LOGIN_CMDS,
            "site": LOGIN_SITES.get(tool, ""), "next_account": next_account(tool) if tool == "codex" else "",
            "enable_setting": t["enable"], "steps": t["steps"]}


def install_cli(tool: str) -> dict[str, Any]:
    """`npm install -g <package>` of a CLI on this host (an administrator's action, like the Host tools)."""
    import subprocess

    if tool not in CLI_TOOLS:
        raise KeyError(tool)
    npm = shutil.which("npm")
    if not npm:
        raise RuntimeError("npm is not installed on the server")
    proc = subprocess.run([npm, "install", "-g", CLI_TOOLS[tool]["package"]], capture_output=True, text=True,
                          timeout=600, stdin=subprocess.DEVNULL)
    from agent_company import workspace

    workspace._VERSION.pop(shutil.which(CLI_TOOLS[tool]["exe"]) or "", None)  # a new version
    return {"ok": proc.returncode == 0, "output": (proc.stdout + proc.stderr)[-3000:], **cli_status(tool)}


# --- signing a CLI in from the website ---------------------------------------------------------------
# The GitHub sign-in of the Copilot CLI: `copilot login --device-code` runs on the server (an administrator's action);
# the page shows its one-time code and address, the person confirms on github.com, and the job ends signed_in only when
# the CLI exited cleanly and its own config says it is signed in (never from what it printed).
# Codex the same with `codex login --device-auth` on auth.openai.com, always into a new or named sign-in folder
# (data/codex-accounts/<account>/, its CODEX_HOME): another ChatGPT account beside the host's, which stays as it is.

LOGIN_CMDS: dict[str, list[str]] = {"copilot": ["login", "--device-code"], "codex": ["login", "--device-auth"]}
LOGIN_SITES = {"copilot": "GitHub", "codex": "ChatGPT"}
LOGIN_TIMEOUT = 900  # GitHub's and OpenAI's device codes expire after 15 minutes
_DEVICE_RE = re.compile(r"(https://[^\s]+/login/device)\S*.*?\b([A-Z0-9]{4}-[A-Z0-9]{4})\b")
# Codex prints the address and the code on separate lines: "https://auth.openai.com/codex/device" … "792J-YS4QE"
_CODEX_DEVICE_RE = re.compile(r"(https://auth\.openai\.com/\S*device)\b.*?\b([A-Z0-9]{4}-[A-Z0-9]{4,6})\b", re.S)
# Without a system keychain the CLI asks, in a terminal only, whether to keep the token as plain text in its config
# file; a person answers that on the page (state `consent`), never the server on its own.
_CONSENT_RE = re.compile(r"Store token in plain ?text config file\?", re.I)
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\r")
_NOISE_RE = re.compile(r"Waiting for authorization|Failed to copy to clipboard|To authenticate, visit|"
                       r"Store token in plain ?text|^[yYnN]$")
_ACTIVE = ("starting", "waiting", "consent", "finishing")
_logins: dict[str, dict[str, Any]] = {}
_login_lock = threading.Lock()


def login_state(tool: str) -> dict[str, Any]:
    """The tool's sign-in job (`none` before one ran): state starting|waiting|consent|finishing|signed_in|failed|
    cancelled, the address and code to enter, the seconds left, the account it signed in as and the error. The CLI's
    own output stays on the server (the error quotes its last lines)."""
    if tool not in LOGIN_CMDS:
        raise KeyError(tool)
    job = _logins.get(tool)
    if job is None:
        return {"tool": tool, "state": "none"}
    left = max(0, int(job["started"] + LOGIN_TIMEOUT - time.time())) if job["state"] in _ACTIVE else 0
    return {k: v for k, v in job.items() if k not in ("proc", "output", "fd", "home", "new_home")} | {
        "seconds_left": left, "site": LOGIN_SITES[tool]}


def next_account(tool: str) -> str:
    """The first free sign-in folder name for a new account: codex-2, codex-3, …"""
    n = 2
    while account_dir(tool, f"{tool}-{n}").exists():
        n += 1
    return f"{tool}-{n}"


def cli_login(tool: str, by: str = "system", account: str = "") -> dict[str, Any]:
    """Start the CLI's device sign-in in a pseudo-terminal (so it can ask about storing the token), or return the
    one running; waits up to 10 s for the code. Codex signs in into its own folder (`account`, else a new one), never
    the host's. Its result is audited in the name of `by` when it ends."""
    import pty
    import subprocess

    if tool not in LOGIN_CMDS:
        raise KeyError(tool)
    if account and tool != "codex":
        raise ValueError(f"the {CLI_TOOLS[tool]['label']} sign-in has no account folders")
    exe = shutil.which(CLI_TOOLS[tool]["exe"])
    if not exe:
        raise RuntimeError(f"the {CLI_TOOLS[tool]['label']} CLI is not installed on the server")
    with _login_lock:
        job = _logins.get(tool)
        if job is None or job["state"] not in _ACTIVE:
            env, home, new_home = {**os.environ, "TERM": "dumb", "NO_COLOR": "1"}, None, False
            if tool == "codex":
                account = account or next_account(tool)
                home = codex_home(account)
                new_home = not home.exists()
                home.mkdir(parents=True, exist_ok=True, mode=0o700)
                env["CODEX_HOME"] = str(home)
            master, slave = pty.openpty()
            try:
                proc = subprocess.Popen([exe, *LOGIN_CMDS[tool]], stdin=slave, stdout=slave, stderr=slave,
                                        cwd=str(Path.home()), start_new_session=True, env=env)
            except OSError:
                os.close(master)
                raise
            finally:
                os.close(slave)
            job = {"tool": tool, "state": "starting", "url": "", "code": "", "started": time.time(), "account": {},
                   "error": "", "output": "", "by": by, "proc": proc, "fd": master, "folder": account,
                   "home": home, "new_home": new_home}
            _logins[tool] = job
            threading.Thread(target=_watch_login, args=(job,), daemon=True, name=f"login-{tool}").start()
    end = time.time() + 10
    while job["state"] == "starting" and time.time() < end:
        time.sleep(0.1)
    return login_state(tool)


def answer_login(tool: str, store: bool) -> dict[str, Any]:
    """The person's answer to "store the token as plain text?": yes finishes the sign-in, no cancels it."""
    job = _logins.get(tool)
    if job is None or job["state"] != "consent":
        raise ValueError("the sign-in is not waiting for that answer")
    job.update(state="finishing", stored=store)
    os.write(job["fd"], b"y\n" if store else b"n\n")
    return login_state(tool)


def cancel_login(tool: str) -> dict[str, Any]:
    job = _logins.get(tool)
    if job is not None and job["state"] in _ACTIVE:
        job["state"] = "cancelled"
        _end_login(job)
    return login_state(tool)


def _end_login(job: dict[str, Any]) -> None:
    import signal

    proc = job["proc"]
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except OSError:
            proc.terminate()


def _watch_login(job: dict[str, Any]) -> None:
    proc, tool, fd = job["proc"], job["tool"], job["fd"]
    timer = threading.Timer(LOGIN_TIMEOUT, lambda: (job.update(error="the code expired before it was confirmed"),
                                                     _end_login(job)))
    timer.daemon = True
    timer.start()
    try:
        while True:
            try:
                chunk = os.read(fd, 4096)
            except OSError:  # EIO: the CLI closed the terminal
                break
            if not chunk:
                break
            job["output"] = (job["output"] + _ANSI_RE.sub("", chunk.decode("utf-8", "replace")))[-4000:]
            hit = (_CODEX_DEVICE_RE if tool == "codex" else _DEVICE_RE).search(job["output"])
            if hit and job["state"] == "starting":
                job.update(url=hit.group(1), code=hit.group(2), state="waiting")
            if job["state"] == "waiting" and _CONSENT_RE.search(job["output"]):
                job["state"] = "consent"
        rc = proc.wait()
    finally:
        timer.cancel()
        os.close(fd)
    home = job.get("home")
    signed_in = (home / "auth.json").is_file() if home is not None else _signed_in(tool)
    if job["new_home"] and not (rc == 0 and signed_in and job["state"] != "cancelled"):
        shutil.rmtree(home, ignore_errors=True)  # a new folder without a sign-in: nothing to keep
    if job["state"] == "cancelled":
        return
    if rc == 0 and signed_in:
        job.update(state="signed_in", account=cli_signin(CLI_TOOLS[tool]["kind"], job["folder"]))
    elif job.get("stored") is False:
        job.update(state="cancelled", error="the token was not stored, so the CLI is not signed in; nothing changed")
    else:
        last = [x.strip() for x in job["output"].splitlines() if x.strip() and not _NOISE_RE.search(x.strip())][-2:]
        job.update(state="failed", error=job["error"] or " ".join(last)[-500:] or f"the sign-in ended with code {rc}")
    from agent_company import governance

    governance.audit(f"user:{job['by']}", "cli_login_result", tool, None,
                     {"state": job["state"], "account": job["account"].get("email", ""), "folder": job["folder"],
                      "error": job["error"][:200],
                      "plaintext_token": bool(job.get("stored"))},
                     "info" if job["state"] == "signed_in" else "warning")


def cli_signin(kind: str, account: str = "") -> dict[str, Any]:
    """Whose sign-in a CLI model uses now (as the Models page shows it), with the time: kept when it is registered."""
    from agent_company import workspace

    if kind == "copilot":
        a = copilot_account()
    elif kind == "codex":
        a = codex_account(codex_home(account) if account else None)
    elif kind == "claude_code":
        a = workspace._account(claude_config_dir(account) if account else None)
    else:
        return {}
    return {k: str(v) for k, v in a.items() if v} | {"at": datetime.now(UTC).isoformat(timespec="seconds")}


# --- what the Models page shows for each model (as the Claude Code panel's info) ---------------
# Status, model, the CLI's version, the account it signs in with, and its usage: each limit window with the share used,
# a severity and the reset time. Read on demand (`/api/models/info`), never part of the model list itself.

INFO_TTL = 60.0
_info_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def severity(percent: float) -> str:
    """Claude's own words for a limit's state, for providers that report only a share."""
    return "exceeded" if percent >= 100 else "critical" if percent >= 90 else "warning" if percent >= 75 else "normal"


def _iso(ts: float | None) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds") if ts else ""


def _jwt_claims(token: str) -> dict[str, Any]:
    """The claims of a JWT, unverified: only to show whose sign-in it is."""
    import base64
    import json

    try:
        part = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (IndexError, ValueError):
        return {}


def codex_account(home: Path | None = None) -> dict[str, str]:
    """Whose sign-in the server's Codex uses (its auth.json); never a token."""
    import json

    try:
        auth = json.loads(((home or codex_home()) / "auth.json").read_text())
    except (OSError, ValueError):
        return {"method": "not signed in"}
    if (auth.get("auth_mode") or "").lower() == "apikey" or (auth.get("OPENAI_API_KEY") and not auth.get("tokens")):
        return {"method": "OpenAI API key"}
    claims = _jwt_claims(str((auth.get("tokens") or {}).get("id_token") or ""))
    plan = str((claims.get("https://api.openai.com/auth") or {}).get("chatgpt_plan_type") or "")
    return {"method": "ChatGPT account", "email": str(claims.get("email") or ""), "plan": plan}


def copilot_account() -> dict[str, str]:
    for k in ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        if os.environ.get(k):
            return {"method": f"GitHub token ({k})"}
    users, last = copilot_users()
    user = last or next((u for u in users if isinstance(u, dict)), None) or {}
    return {"method": "GitHub account", "email": str(user.get("login") or "")} if user else {"method": "not signed in"}


def _cached(key: str, fn: Any) -> dict[str, Any]:
    hit = _info_cache.get(key)
    if hit and time.time() - hit[0] < INFO_TTL:
        return hit[1]
    out = fn()
    _info_cache[key] = (time.time(), out)
    return out


def _own_limits(m: ModelEntry) -> list[dict[str, Any]]:
    """This server's daily/weekly token limits of the model, as limit windows."""
    from datetime import timedelta

    out = []
    for label, limit, start, span in (("Daily token limit", m.daily_token_limit, usage.day_start(), timedelta(days=1)),
                                      ("Weekly token limit", m.weekly_token_limit, usage.week_start(), timedelta(days=7))):
        if limit:
            pct = round(100 * usage.tokens_since(m.name, start) / limit, 1)
            out.append({"label": label, "percent": pct, "severity": severity(pct),
                        "resets_at": (start + span).isoformat(timespec="seconds"), "source": "server"})
    return out


def model_info(m: ModelEntry) -> dict[str, Any]:
    """One model as the Models page shows it: status, CLI, account, usage windows and this server's own counts."""
    from agent_company import workspace

    ok, why = available(m)
    info: dict[str, Any] = {"status": "inactive" if not m.enabled else "available" if ok else "unavailable",
                            "reason": "" if ok or not m.enabled else why, "cli": None, "account": {}, "limits": [],
                            "scope": "", "note": ""}
    if m.kind == "offline":
        info.update(scope="", note="Sends nothing anywhere: no account, no limits.")
        return info
    if m.kind in CLI_KINDS:
        tool = {"claude_code": "claude", "codex": "codex", "copilot": "copilot"}[m.kind]
        exe = shutil.which(CLI_TOOLS[tool]["exe"])
        info["cli"] = {"label": {"claude": "Claude Code", "codex": "Codex", "copilot": "Copilot"}[tool],
                       "version": workspace._version(exe) if exe else "not installed"}
    if m.kind == "claude_code":
        home = claude_config_dir(m.account) if m.account else None
        info["account"] = workspace._account(home)
        if m.base_url:
            info["account"]["note"] = f"at {m.base_url}"
        p = workspace.claude_limits(home)
        info["limits"] = [{**x, "source": "claude"} for x in p.get("limits") or []]
        info["scope"] = f"the Claude {p['plan']} plan of this sign-in" if p.get("plan") else "the Claude plan of this sign-in"
        info["note"] = "" if p.get("available") else p.get("reason") or ""
    elif m.kind == "codex":
        home = codex_home(m.account) if m.account else None
        info["account"] = codex_account(home)
        st = codex_limits(home)
        info["limits"] = [{"label": w["label"], "percent": w["used_percent"], "severity": severity(w["used_percent"]),
                           "resets_at": _iso(w.get("resets_at")), "source": "codex"}
                          for w in st.get("limits") or [] if not w.get("resets_at") or w["resets_at"] > time.time()]
        plan = st.get("plan") or info["account"].get("plan") or ""
        whose = f"the Codex sign-in in data/codex-accounts/{m.account}" if m.account else "the server's Codex sign-in"
        info["scope"] = f"the ChatGPT {plan} plan of {whose}" if plan else f"the ChatGPT plan of {whose}"
        info["note"] = "" if info["limits"] else "Codex reports its plan limits with each run: press Test to read them"
    elif m.kind == "copilot":
        info["account"] = copilot_account()
        info["scope"] = "the GitHub Copilot plan"
        info["note"] = "The Copilot CLI reports no plan limits the server can read: see github.com/settings/copilot"
    else:
        key = model_key(m)
        if m.api_key_enc:
            info["account"] = {"method": "own API key", "email": key_hint(key)}
        elif m.api_key_env or not m.base_url:
            env = m.api_key_env or " or ".join(PROVIDER_KEYS.get(provider_of(m.model_id), (f"{provider_of(m.model_id).upper()}_API_KEY",)))
            info["account"] = {"method": "the server's key", "email": env}
        else:
            info["account"] = {"method": "no key (local server)"}
        if m.model_id.startswith("openrouter/") and not m.base_url:
            k = key or os.environ.get("OPENROUTER_API_KEY", "")
            st = _cached(f"openrouter:{hash(k)}", lambda: provider_status(Choice(m.name, m.kind, m.model_id, ""), k))
            info["limits"] = [{"label": w["label"].capitalize(), "percent": w["used_percent"], "severity": severity(w["used_percent"]),
                               "resets_at": _iso(w.get("resets_at")), "source": "openrouter"} for w in st.get("limits") or []]
            info["scope"] = "this OpenRouter key"
            info["note"] = "" if info["limits"] else st.get("detail", "")
        elif m.base_url and is_local(m):
            info["scope"] = "this server's own count"
        else:
            info["scope"] = "this server's own count (the provider reports no usage to the server)"
    own = _own_limits(m)
    info["limits"] += own
    if own and not info["scope"]:
        info["scope"] = "this server's own count"
    info["tokens"] = {"today": usage.tokens_since(m.name, usage.day_start()),
                      "week": usage.tokens_since(m.name, usage.week_start())}
    return info


def models_info() -> dict[int, dict[str, Any]]:
    """model_info of every model, read side by side (providers answer at their own pace)."""
    from concurrent.futures import ThreadPoolExecutor

    with session() as s:
        rows = s.scalars(select(ModelEntry).order_by(ModelEntry.id)).all()
        for m in rows:
            s.expunge(m)

    def one(m: ModelEntry) -> dict[str, Any]:
        try:
            return model_info(m)
        except Exception as e:  # noqa: BLE001 - one odd model must not hide the others
            return {"status": "unavailable", "reason": f"info unavailable: {str(e)[:200]}", "limits": [], "account": {}}

    with ThreadPoolExecutor(max_workers=8) as pool:
        return dict(zip((m.id for m in rows), pool.map(one, rows), strict=True))
