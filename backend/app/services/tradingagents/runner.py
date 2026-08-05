"""Run the vendored TradingAgents graph against Vietnamese-market data.

Responsibilities:
  * Build a config that points the framework at a local Ollama server and at the
    ``portfolio`` (VN) data vendor.
  * Register the VN vendor into TradingAgents' dispatch table (idempotent) and
    patch the verification-snapshot loader to use VN OHLCV.
  * Drive the graph and yield progress events for SSE streaming.

Only the ``market`` and ``news`` analysts run by default — those are the two the
platform has real Vietnamese data for (OHLCV/indicators and research reports).
Sector analysis is not a separate agent: upstream's analyst team has no sector
role, so the VN ``get_news`` tool serves sector context (industry, sector metrics,
sector research) alongside company news and the News Analyst folds it into its own
report. The full researcher debate → trader → risk-management → portfolio-manager
pipeline downstream is unchanged.

Prerequisite: a running Ollama server with the configured models pulled. See
``README.md`` in this package.
"""
from __future__ import annotations

import contextlib
import functools
import logging
import os
import re
import threading
import time
from typing import Iterator

# Importing the package installs the sys.path shim for ``import tradingagents``.
from . import vn_data

logger = logging.getLogger(__name__)

DEFAULT_ANALYSTS = ("market", "news", "fundamentals")

# Per-analyst model overrides. Upstream builds exactly two clients — a "quick"
# one shared by every analyst (plus the researchers, trader and risk debators)
# and a "deep" one for the two managers — so an individual analyst has no model
# of its own. These maps let one be given one: the env var that names it, and
# the agent factory in ``tradingagents.graph.setup`` whose model must be swapped
# to honour it (see ``apply_model_overrides``).
_ANALYST_MODEL_ENV: dict[str, str] = {
    "market": "TRADINGAGENTS_MARKET_LLM",
    "social": "TRADINGAGENTS_SOCIAL_LLM",
    "news": "TRADINGAGENTS_NEWS_LLM",
    "fundamentals": "TRADINGAGENTS_FUNDAMENTALS_LLM",
}

_ANALYST_FACTORY_NAMES: dict[str, str] = {
    "market": "create_market_analyst",
    "social": "create_sentiment_analyst",
    "news": "create_news_analyst",
    "fundamentals": "create_fundamentals_analyst",
}

# Analyst keys that accept a model override — the API validates against this.
ANALYST_MODEL_KEYS = tuple(_ANALYST_MODEL_ENV)

# The roles a model can play. "deep" is the two managers, "quick" the default for
# every other agent (analysts, researchers, trader, risk debators), and each
# analyst key may override "quick" for itself alone.
LLM_ROLES = ("deep", "quick", *ANALYST_MODEL_KEYS)

# Serialises the module-global rebinds in ``apply_model_overrides`` against
# concurrent runs: two SSE requests building their graphs at the same time could
# otherwise pick up each other's models.
_graph_build_lock = threading.Lock()

# The backend runs in Docker while Ollama runs on the host, so reach it via
# host.docker.internal by default. Override with TRADINGAGENTS_LLM_BACKEND_URL or
# OLLAMA_BASE_URL (e.g. http://localhost:11434/v1 when running the backend
# outside a container).
DEFAULT_OLLAMA_URL = "http://host.docker.internal:11434/v1"

# Local providers run against a self-hosted OpenAI-compatible endpoint (no API
# key, needs a base URL); everything else is a hosted API (key required, uses the
# provider's own default endpoint).
_LOCAL_PROVIDERS_FOR_URL = ("ollama", "openai_compatible")

# Per-provider default model IDs, used when the TRADINGAGENTS_*_LLM env vars are
# unset. Keeps a bare `TRADINGAGENTS_LLM_PROVIDER=deepseek` working without also
# having to pick model names. (deep = manager/debate, quick = analysts.)
_PROVIDER_MODEL_DEFAULTS: dict[str, tuple[str, str]] = {
    "ollama": ("llama3-groq-tool-use", "llama3-groq-tool-use"),
    "deepseek": ("deepseek-v4-pro", "deepseek-v4-flash"),
    "openai": ("gpt-5.6-luna", "gpt-5.6-luna"),
    "anthropic": ("claude-opus-4-8", "claude-haiku-4-5-20251001"),
    "google": ("gemini-3.1-pro-preview", "gemini-3.5-flash"),
}

# Friendly provider aliases → the canonical name the vendored framework expects.
_PROVIDER_ALIASES: dict[str, str] = {
    "gemini": "google",
    "google-genai": "google",
    "googleai": "google",
    "claude": "anthropic",
    "gpt": "openai",
}

# Alternative env vars accepted for a provider's canonical API-key var. Lets a
# user set the "obvious" name (GEMINI_API_KEY) and have it bridged to what the
# framework actually reads (GOOGLE_API_KEY).
_KEY_ALIASES: dict[str, tuple[str, ...]] = {
    "GOOGLE_API_KEY": ("GEMINI_API_KEY", "GOOGLE_GENAI_API_KEY"),
}

# Structured-output method for local (Ollama / OpenAI-compatible) models. The
# framework defaults unknown model IDs to "function_calling", which binds the
# output schema as a tool — local models frequently answer in plain text instead
# of emitting that tool call, so the structured parse returns nothing and the
# agent (Research Manager / Trader / Portfolio Manager) noisily falls back to
# free text. "json_schema" uses Ollama's native structured-output response_format
# and is far more reliable. Override via TRADINGAGENTS_OLLAMA_STRUCTURED_METHOD
# ("json_schema" | "json_mode" | "function_calling"); any failure still degrades
# to free text, so this can only help.
_LOCAL_PROVIDERS = ("ollama", "openai_compatible")

# Every data category is served by the single VN vendor.
_ALL_CATEGORIES = (
    "core_stock_apis",
    "technical_indicators",
    "fundamental_data",
    "news_data",
    "macro_data",
    "prediction_markets",
)

# Sections we surface as they first appear in the streamed state. Order matches
# the pipeline so the UI can render them top-to-bottom.
_SECTION_KEYS: tuple[tuple[str, str], ...] = (
    ("market_report", "market"),
    ("sentiment_report", "sentiment"),
    ("news_report", "news"),
    ("fundamentals_report", "fundamentals"),
    ("investment_plan", "research_manager"),
    ("trader_investment_plan", "trader"),
    ("final_trade_decision", "final"),
)

# The debate sections carry the *transcript*, not the manager's verdict. Upstream
# writes that verdict to both ``investment_plan`` and
# ``investment_debate_state["judge_decision"]`` (likewise ``final_trade_decision``
# and ``risk_debate_state["judge_decision"]``), so surfacing judge_decision here
# would print the Investment Plan / Final Decision a second time under a debate
# heading. The turns themselves are already in ``history`` and go unused.
#
# Each turn is stamped with one of these speaker prefixes by the researcher/risk
# nodes and the turns are concatenated into one string, so the prefixes are the
# only seams available for splitting it back apart.
_DEBATE_SPEAKERS = (
    "Bull Analyst",
    "Bear Analyst",
    "Aggressive Analyst",
    "Conservative Analyst",
    "Neutral Analyst",
)

_SPEAKER_RE = re.compile(r"^(" + "|".join(_DEBATE_SPEAKERS) + r"):[ \t]*", re.MULTILINE)

_registered = False
_empty_response_patched = False

_EMPTY_TURN = "_(the model returned an empty turn)_"


def _debate_section(debate: object) -> str:
    """Render a debate state's transcript as markdown, one heading per turn.

    A turn whose body is empty is labelled rather than left as a bare heading:
    the debate nodes interpolate ``response.content`` unconditionally, so a model
    that answers with nothing still contributes its speaker prefix, and a silent
    gap reads as a rendering bug instead of a missing argument.

    Falls back to the judge's verdict when there is no transcript at all (a
    zero-round debate), so the section is never empty.
    """
    if not isinstance(debate, dict):
        return ""
    history = str(debate.get("history") or "").strip()
    if not history:
        return str(debate.get("judge_decision") or "")

    # One capturing group → [preamble, speaker, body, speaker, body, ...].
    parts = _SPEAKER_RE.split(history)
    if len(parts) < 3:
        return history

    sections: list[str] = []
    preamble = parts[0].strip()
    if preamble:
        sections.append(preamble)
    for speaker, body in zip(parts[1::2], parts[2::2]):
        sections.append(f"### {speaker}\n\n{body.strip() or _EMPTY_TURN}")
    return "\n\n".join(sections)


def register_vn_vendor() -> None:
    """Wire the VN data adapter into TradingAgents (idempotent).

    Registers each VN implementation under the ``portfolio`` vendor key in the
    dispatch table and repoints the verification-snapshot loader at VN OHLCV.
    Safe to call on every run.
    """
    global _registered
    if _registered:
        return

    from tradingagents.dataflows import interface as ta_interface

    for method, impl in vn_data.VN_VENDOR_METHODS.items():
        ta_interface.VENDOR_METHODS.setdefault(method, {})["portfolio"] = impl

    # get_verified_market_snapshot -> build_verified_market_snapshot references
    # the module-global ``load_ohlcv`` bound at import time in the validator
    # module; repoint it at the VN loader.
    import tradingagents.dataflows.market_data_validator as validator

    validator.load_ohlcv = vn_data.load_ohlcv

    _registered = True
    logger.info("Registered VN 'portfolio' data vendor into TradingAgents dispatch")


def patch_empty_response_recovery() -> None:
    """Stop an empty assistant message from silently erasing an agent's turn.

    The debate nodes (bull/bear researchers, the three risk debators) interpolate
    ``llm.invoke(prompt).content`` straight into the transcript with no check, so
    a reply whose content is empty costs the whole turn — observed with
    ``deepseek-v4``: a VCG run recorded ``"Bull Analyst: "`` and nothing after it,
    while the bear turn was 6.8k characters. Thinking models are the usual source:
    the prose can end up in ``reasoning_content`` alone, or the answer is cut off
    after the reasoning consumes the budget.

    Two recoveries, cheapest first: retry once (a truncated generation is usually
    transient), then fall back to the reasoning text — verbose, but it carries the
    argument, and a wordy turn beats a missing one.

    **Tool-calling replies are left alone**: empty content plus ``tool_calls`` is
    how every analyst requests data, and retrying those would double the cost of
    each tool round-trip for no reason.
    """
    global _empty_response_patched
    if _empty_response_patched:
        return

    from tradingagents.llm_clients.openai_client import NormalizedChatOpenAI

    original_invoke = NormalizedChatOpenAI.invoke

    def invoke(self, input, config=None, **kwargs):  # noqa: A002 — match the signature
        response = original_invoke(self, input, config, **kwargs)
        if _has_text(response) or getattr(response, "tool_calls", None):
            return response

        logger.warning(
            "%s returned an empty message; retrying once", self.model_name
        )
        retry = original_invoke(self, input, config, **kwargs)
        if _has_text(retry) or getattr(retry, "tool_calls", None):
            return retry

        reasoning = (getattr(retry, "additional_kwargs", None) or {}).get(
            "reasoning_content"
        )
        if isinstance(reasoning, str) and reasoning.strip():
            logger.warning(
                "%s returned no content twice; using its reasoning text instead",
                self.model_name,
            )
            retry.content = reasoning.strip()
        return retry

    NormalizedChatOpenAI.invoke = invoke
    _empty_response_patched = True
    logger.info("Patched TradingAgents LLM client with empty-response recovery")


def _has_text(response: object) -> bool:
    return bool(str(getattr(response, "content", "") or "").strip())


def apply_structured_method(cfg: dict) -> None:
    """Steer the configured models onto a structured-output method that holds.

    The structured agents (Research Manager, Trader, Portfolio Manager) parse the
    model's reply into a Pydantic schema; a miss is survivable (they retry as free
    text) but loses the typed plan, so it's worth avoiding.

    ``function_calling`` is the weakest option whenever the model also rejects
    ``tool_choice`` — the schema is offered as a tool the model is never *forced*
    to call, so it can return a partial object and fail validation (observed with
    ``deepseek-v4-pro``: a ResearchPlan missing ``strategic_actions``). Both
    remedies are applied here:

      * **Local models** (Ollama / OpenAI-compatible) genuinely support Ollama's
        native ``json_schema`` response format, so declare it outright.
      * **Hosted models** get ``json_schema`` when supported, else forced
        ``function_calling`` when ``tool_choice`` is accepted, else ``"none"`` —
        which tells the framework to skip the structured attempt and generate free
        text directly. Declaring ``"none"`` is not a downgrade: the agents already
        fall back to free text on failure, so a doomed attempt only burns one
        extra (expensive, slow) generation per structured agent before landing in
        the same place.

    DeepSeek's v4 thinking models are the case that forced this: ``json_schema``
    400s ("This response_format type is unavailable now"), ``tool_choice`` 400s
    ("Thinking mode does not support this tool_choice") so the schema tool can
    never be forced and the model just answers in prose, and ``json_mode`` — while
    accepted by the API — requires the word "json" in the prompt, which the
    vendored agent prompts do not contain. Nothing works, so we stop paying to
    find that out on every call.

    Only ``preferred_structured_method`` is rewritten; the real support flags and
    provider quirks (e.g. DeepSeek's reasoning-content roundtrip) are preserved, so
    we never advertise a format the API will reject.

    Override with ``TRADINGAGENTS_STRUCTURED_METHOD`` (legacy
    ``TRADINGAGENTS_OLLAMA_STRUCTURED_METHOD`` still honoured for local).

    Each role is judged against *its own* provider, so a mixed run (say OpenAI
    managers + local analysts) gets the local treatment for the local models and
    the capability-driven one for the hosted models.
    """
    import dataclasses

    from tradingagents.llm_clients import capabilities as caps_mod

    override = os.getenv("TRADINGAGENTS_STRUCTURED_METHOD", "").strip()
    local_method = (
        override
        or os.getenv("TRADINGAGENTS_OLLAMA_STRUCTURED_METHOD", "json_schema").strip()
    )

    done: set[tuple[str, str]] = set()
    for role in (cfg.get("llm_roles") or {}).values():
        provider = str(role.get("provider") or "").lower()
        model = str(role.get("model") or "")
        if not model or (provider, model) in done:
            continue
        done.add((provider, model))

        if provider in _LOCAL_PROVIDERS:
            if local_method == "function_calling":
                continue  # explicitly asked for the framework default
            caps_mod._BY_ID[model] = caps_mod.ModelCapabilities(
                supports_tool_choice=True,
                supports_json_mode=True,
                supports_json_schema=True,
                preferred_structured_method=local_method,  # type: ignore[arg-type]
            )
            logger.info(
                "Structured-output method for local model %s set to %r",
                model,
                local_method,
            )
            continue

        caps = caps_mod.get_capabilities(model)
        if override:
            method = override
        elif caps.supports_json_schema:
            method = "json_schema"
        elif caps.supports_tool_choice:
            method = "function_calling"  # forcible, so the schema tool is reliable
        else:
            # Unforced function calling loses the schema; json_mode needs the
            # prompt to mention JSON. Skip the attempt rather than pay for it.
            method = "none"
        if method == caps.preferred_structured_method:
            continue
        # Rewrite only the method — the support flags and provider quirks stand.
        caps_mod._BY_ID[model] = dataclasses.replace(
            caps, preferred_structured_method=method  # type: ignore[arg-type]
        )
        logger.info("Structured-output method for %s set to %r", model, method)


def register_configured_models(cfg: dict) -> None:
    """Register the run's models as "known" so the framework stops warning.

    ``BaseLLMClient.warn_if_unknown_model`` checks each model against a catalog
    baked into the vendored snapshot, so anything released since then warns on
    every client build::

        RuntimeWarning: Model 'gpt-5.6-terra' is not in the known model list for
        provider 'openai'. Continuing anyway.

    The check cannot tell a typo from a newer release, and the model is used
    either way — the warning only adds noise to a run the operator configured
    deliberately. Registering the configured models silences it without loosening
    anything: providers that already accept any model string (ollama, openrouter,
    openai_compatible, …) are absent from ``VALID_MODELS`` and skipped.

    Capabilities are a separate table: an unlisted model resolves to the default
    (tool_choice + json_mode + json_schema all supported), which
    ``apply_structured_method`` then narrows per model.
    """
    from tradingagents.llm_clients import validators

    for role in (cfg.get("llm_roles") or {}).values():
        provider, model = str(role["provider"]), str(role["model"])
        known = validators.VALID_MODELS.get(provider)
        # None → the provider takes any model name, so there is nothing to add.
        if known is None or not model or model in known:
            continue
        known.append(model)
        logger.info("Registered %r as a known %s model", model, provider)


def is_local_provider(provider: str) -> bool:
    """Whether the provider is a self-hosted OpenAI-compatible endpoint."""
    return provider.lower() in _LOCAL_PROVIDERS_FOR_URL


def _bridge_api_key_aliases(provider: str) -> None:
    """Populate a provider's canonical API-key env var from a known alias.

    The framework (and langchain-google-genai) read ``GOOGLE_API_KEY``, but users
    naturally set ``GEMINI_API_KEY``. If the canonical var is unset and an alias
    is present, mirror it so both the readiness check and the SDK find the key.
    """
    from tradingagents.llm_clients.api_key_env import get_api_key_env

    canonical = get_api_key_env(provider)
    if not canonical or os.getenv(canonical):
        return
    for alt in _KEY_ALIASES.get(canonical, ()):
        value = os.getenv(alt)
        if value:
            os.environ[canonical] = value
            return


@functools.lru_cache(maxsize=1)
def known_providers() -> frozenset[str]:
    """Every provider name accepted in a ``provider:model`` spec, incl. aliases."""
    from tradingagents.llm_clients.api_key_env import PROVIDER_API_KEY_ENV

    return frozenset(PROVIDER_API_KEY_ENV) | frozenset(_PROVIDER_ALIASES)


def parse_model_spec(spec: str, default_provider: str) -> tuple[str, str]:
    """Split a ``provider:model`` spec; a bare model keeps ``default_provider``.

    This is what makes a mixed setup expressible — ``openai:gpt-5.6-luna`` for the
    managers, ``deepseek:deepseek-v4-flash`` for the analysts — in a single env
    var or request field.

    Ollama model IDs are themselves colon-separated (``qwen3:latest``,
    ``deepseek-r1:7b``), so the prefix only counts as a provider when it actually
    names one; ``deepseek-r1`` is a model, ``deepseek`` is a provider.
    """
    text = str(spec or "").strip()
    if ":" in text:
        head, rest = text.split(":", 1)
        provider = _PROVIDER_ALIASES.get(head.lower(), head.lower())
        if provider in known_providers() and rest.strip():
            return provider, rest.strip()
    return default_provider, text


def _backend_url_for(provider: str, default_provider: str) -> str | None:
    """Base URL for one provider, or None to use the provider's own endpoint.

    ``TRADINGAGENTS_<PROVIDER>_BASE_URL`` wins, so a mixed setup can point one
    provider at a gateway without touching the others. The unscoped
    ``TRADINGAGENTS_LLM_BACKEND_URL`` / ``OLLAMA_BASE_URL`` applies to local
    providers and to the default provider it was configured for — never to a
    secondary hosted provider, which would otherwise send (say) OpenAI traffic to
    the Ollama endpoint.
    """
    scoped = os.getenv(f"TRADINGAGENTS_{re.sub(r'[^A-Z0-9]', '_', provider.upper())}_BASE_URL")
    if scoped:
        return scoped
    explicit = os.getenv("TRADINGAGENTS_LLM_BACKEND_URL") or os.getenv("OLLAMA_BASE_URL")
    if is_local_provider(provider):
        return explicit or DEFAULT_OLLAMA_URL
    return explicit if provider == default_provider else None


def _role(provider: str, model: str, default_provider: str) -> dict[str, str | None]:
    return {
        "provider": provider,
        "model": model,
        "base_url": _backend_url_for(provider, default_provider),
    }


def _resolve_llm_roles(
    *,
    default_provider: str,
    deep_spec: str,
    quick_spec: str,
    analyst_specs: dict[str, str],
) -> dict[str, dict[str, str | None]]:
    """Resolve every role to a concrete ``(provider, model, base_url)``.

    Analyst specs fall back to the *quick* role's provider rather than the default
    one: ``TRADINGAGENTS_QUICK_THINK_LLM=deepseek:…`` plus a bare
    ``TRADINGAGENTS_MARKET_LLM=deepseek-v4-pro`` means "another DeepSeek model",
    which is what a reader expects.

    An analyst role identical to the quick role is dropped — it is what the
    analyst would use anyway, and keeping it would patch the graph (and build a
    second, identical client) for nothing.
    """
    deep_provider, deep_model = parse_model_spec(deep_spec, default_provider)
    quick_provider, quick_model = parse_model_spec(quick_spec, default_provider)

    roles = {
        "deep": _role(deep_provider, deep_model, default_provider),
        "quick": _role(quick_provider, quick_model, default_provider),
    }
    for analyst, spec in analyst_specs.items():
        provider, model = parse_model_spec(spec, quick_provider)
        if not model or (provider, model) == (quick_provider, quick_model):
            continue
        roles[analyst] = _role(provider, model, default_provider)
    return roles


def providers_in_use(cfg: dict) -> tuple[str, ...]:
    """Distinct providers across all roles, in role order."""
    seen: dict[str, None] = {}
    for role in (cfg.get("llm_roles") or {}).values():
        provider = str(role.get("provider") or "")
        if provider:
            seen.setdefault(provider, None)
    return tuple(seen)


def build_config(
    *,
    deep_think_llm: str | None = None,
    quick_think_llm: str | None = None,
    analyst_llms: dict[str, str] | None = None,
) -> dict:
    """Build a TradingAgents config for the VN data vendor + selected LLM models.

    Provider-agnostic: defaults to a local Ollama server, but set
    ``TRADINGAGENTS_LLM_PROVIDER`` to any supported provider (e.g. ``deepseek``,
    ``openai``, ``anthropic``) and it configures the right models, endpoint, and
    auth path. Env overrides (all optional):

      TRADINGAGENTS_LLM_PROVIDER      (default: deepseek) — provider for bare model names
      TRADINGAGENTS_DEEP_THINK_LLM    (default: per-provider, see _PROVIDER_MODEL_DEFAULTS)
      TRADINGAGENTS_QUICK_THINK_LLM   (default: per-provider)
      TRADINGAGENTS_MARKET_LLM        (default: quick-think model)
      TRADINGAGENTS_NEWS_LLM          (default: quick-think model)
      TRADINGAGENTS_FUNDAMENTALS_LLM  (default: quick-think model)
      TRADINGAGENTS_SOCIAL_LLM        (default: quick-think model)
      TRADINGAGENTS_LLM_BACKEND_URL   (default: Ollama URL for local providers, else provider default)
      TRADINGAGENTS_<PROVIDER>_BASE_URL  (per-provider endpoint, e.g. TRADINGAGENTS_OLLAMA_BASE_URL)
      TRADINGAGENTS_MAX_DEBATE_ROUNDS (default: 1)
      TRADINGAGENTS_MAX_RISK_ROUNDS   (default: 1)
      TRADINGAGENTS_OUTPUT_LANGUAGE   (default: English)

    **Every model var takes an optional ``provider:`` prefix**, so roles can run on
    different providers — e.g. ``TRADINGAGENTS_DEEP_THINK_LLM=openai:gpt-5.6-luna``
    with ``TRADINGAGENTS_QUICK_THINK_LLM=deepseek:deepseek-v4-flash``. Bare names
    use ``TRADINGAGENTS_LLM_PROVIDER`` (analyst vars: the quick model's provider).
    The keyword arguments are per-run overrides (from the API request), accept the
    same prefix, and win over the env vars.

    The resolved assignment lives in ``cfg["llm_roles"]``
    (``{role: {provider, model, base_url}}``) and is applied by
    ``apply_model_overrides``; ``llm_provider``/``deep_think_llm``/
    ``quick_think_llm`` keep the shapes the framework expects.

    Hosted providers read their API key from the standard env var
    (e.g. ``DEEPSEEK_API_KEY``, ``OPENAI_API_KEY``) — one per provider in use.
    """
    from tradingagents.default_config import DEFAULT_CONFIG

    cfg = dict(DEFAULT_CONFIG)  # shallow copy; nested dicts replaced wholesale below

    default_provider = os.getenv("TRADINGAGENTS_LLM_PROVIDER", "deepseek").lower()
    default_provider = _PROVIDER_ALIASES.get(default_provider, default_provider)

    deep_default, quick_default = _PROVIDER_MODEL_DEFAULTS.get(
        default_provider, (cfg["deep_think_llm"], cfg["quick_think_llm"])
    )
    roles = _resolve_llm_roles(
        default_provider=default_provider,
        deep_spec=deep_think_llm or os.getenv("TRADINGAGENTS_DEEP_THINK_LLM", deep_default),
        quick_spec=quick_think_llm
        or os.getenv("TRADINGAGENTS_QUICK_THINK_LLM", quick_default),
        analyst_specs={
            analyst: str(
                (analyst_llms or {}).get(analyst) or os.getenv(env_var, "")
            ).strip()
            for analyst, env_var in _ANALYST_MODEL_ENV.items()
        },
    )
    cfg["llm_roles"] = roles

    # The framework builds its two clients from these three keys, so they carry
    # the quick/deep models and the provider that serves most of the run; a role
    # on a different provider is redirected by apply_model_overrides.
    cfg["llm_provider"] = default_provider
    cfg["deep_think_llm"] = roles["deep"]["model"]
    cfg["quick_think_llm"] = roles["quick"]["model"]
    cfg["backend_url"] = _backend_url_for(default_provider, default_provider)
    # Kept for the API/UI: analyst → model, only where it differs from quick.
    cfg["analyst_llms"] = {
        analyst: str(roles[analyst]["model"])
        for analyst in ANALYST_MODEL_KEYS
        if analyst in roles
    }

    for provider in providers_in_use(cfg):
        _bridge_api_key_aliases(provider)

    cfg["max_debate_rounds"] = int(os.getenv("TRADINGAGENTS_MAX_DEBATE_ROUNDS", "1"))
    cfg["max_risk_discuss_rounds"] = int(os.getenv("TRADINGAGENTS_MAX_RISK_ROUNDS", "1"))
    cfg["output_language"] = os.getenv("TRADINGAGENTS_OUTPUT_LANGUAGE", "Vietnamese")
    cfg["checkpoint_enabled"] = False
    cfg["online_tools"] = True
    cfg["data_vendors"] = {cat: "portfolio" for cat in _ALL_CATEGORIES}
    cfg["tool_vendors"] = {}
    return cfg


def _provider_kwargs(cfg: dict, provider: str | None = None) -> dict:
    """The provider-specific LLM kwargs the framework would use for this config.

    Reuses upstream's own resolution (thinking level, reasoning effort,
    temperature, retry budget) rather than duplicating it, so an extra client is
    built exactly like the quick/deep ones. ``_get_provider_kwargs`` only reads
    ``self.config``, hence the stand-in object.

    ``provider`` resolves the kwargs *as if* that provider were configured, which
    is what a mixed run needs: ``openai_reasoning_effort`` must reach an OpenAI
    role and no other, even when the default provider is DeepSeek.
    """
    from types import SimpleNamespace

    from tradingagents.graph.trading_graph import TradingAgentsGraph

    if provider and provider != cfg.get("llm_provider"):
        cfg = {**cfg, "llm_provider": provider}
    try:
        return TradingAgentsGraph._get_provider_kwargs(SimpleNamespace(config=cfg))
    except Exception as exc:  # noqa: BLE001 — upstream refactor; defaults are fine
        logger.warning("Could not resolve provider kwargs (%s); using none", exc)
        return {}


def _build_role_llm(cfg: dict, role: dict) -> object:
    """Build the chat model for one role, on that role's own provider."""
    from tradingagents.llm_clients import create_llm_client

    provider = str(role["provider"])
    return create_llm_client(
        provider=provider,
        model=str(role["model"]),
        base_url=role.get("base_url"),
        **_provider_kwargs(cfg, provider),
    ).get_llm()


@contextlib.contextmanager
def apply_model_overrides(cfg: dict) -> Iterator[None]:
    """Apply ``cfg["llm_roles"]`` while the graph is built. Two overrides:

    **Cross-provider roles.** ``TradingAgentsGraph`` builds its quick and deep
    clients from the single ``config["llm_provider"]``, so a mixed assignment
    (OpenAI managers, DeepSeek analysts) is not expressible in the config. It
    calls the factory it imported into its own module namespace
    (``from tradingagents.llm_clients import create_llm_client``), so rebinding
    ``trading_graph.create_llm_client`` to a wrapper that looks the model up in
    the role table and substitutes that role's provider, base URL and
    provider-specific kwargs is enough.

    **Per-analyst models.** Upstream hands every analyst the one
    ``quick_thinking_llm``, chosen inside ``GraphSetup.setup_graph`` via a *local*
    factory dict — no seam to pass a per-analyst client through. Same trick one
    level down: ``setup.py`` does ``from tradingagents.agents import
    create_market_analyst`` (etc.), so those names are rebound to wrappers that
    ignore the llm they are handed and close over ours.

    Scope is the block, not the process: the wrappers are only needed while the
    graph is constructed (nodes hold their own closures afterwards) and the
    originals are restored on exit, so a later run with a different assignment is
    unaffected. Wrap the ``TradingAgentsGraph(...)`` construction with this. The
    block holds ``_graph_build_lock`` either way, so concurrent runs cannot
    observe each other's rebinds.
    """
    roles = cfg.get("llm_roles") or {}
    default_provider = str(cfg.get("llm_provider", ""))
    # The framework builds these two itself, deep first, so they are matched by
    # model *and* consumed in that order — the same model ID can legitimately be
    # served by two providers (`ollama:gpt-oss:latest` vs `openai_compatible:…`),
    # which a model→role map alone could not tell apart. Analyst roles need no
    # wrapper; they get their client from _build_role_llm directly.
    pending = [roles[key] for key in ("deep", "quick") if key in roles]
    needs_redirect = any(role["provider"] != default_provider for role in pending)
    analyst_roles = {a: roles[a] for a in ANALYST_MODEL_KEYS if a in roles}

    with _graph_build_lock:
        if not needs_redirect and not analyst_roles:
            yield
            return

        from tradingagents.graph import setup as setup_mod
        from tradingagents.graph import trading_graph as graph_mod

        saved: list[tuple[object, str, object]] = []
        try:
            if needs_redirect:
                original_factory = graph_mod.create_llm_client

                def create_llm_client(provider, model, base_url=None, **kwargs):
                    role = next(
                        (r for r in pending if str(r["model"]) == str(model)), None
                    )
                    # Not one of ours (or already built): leave it to the config.
                    if role is None:
                        return original_factory(provider, model, base_url, **kwargs)
                    pending.remove(role)
                    if role["provider"] == default_provider:
                        return original_factory(provider, model, base_url, **kwargs)
                    # Re-resolve the kwargs for the role's provider, but keep the
                    # caller's callbacks (stats tracking) — those are not config.
                    fixed = _provider_kwargs(cfg, str(role["provider"]))
                    if "callbacks" in kwargs:
                        fixed["callbacks"] = kwargs["callbacks"]
                    logger.info(
                        "Model %s served by provider %r", model, role["provider"]
                    )
                    return original_factory(
                        str(role["provider"]), model, role.get("base_url"), **fixed
                    )

                saved.append((graph_mod, "create_llm_client", original_factory))
                graph_mod.create_llm_client = create_llm_client

            for analyst, role in analyst_roles.items():
                name = _ANALYST_FACTORY_NAMES[analyst]
                original = getattr(setup_mod, name)
                llm = _build_role_llm(cfg, role)
                saved.append((setup_mod, name, original))
                setattr(
                    setup_mod,
                    name,
                    lambda _shared_llm, _orig=original, _llm=llm: _orig(_llm),
                )
                logger.info(
                    "Analyst %r pinned to %s:%s",
                    analyst,
                    role["provider"],
                    role["model"],
                )
            yield
        finally:
            for module, name, original in saved:
                setattr(module, name, original)


def _check_provider(provider: str, base_url: str | None) -> tuple[bool, str]:
    """Readiness of one provider: Ollama gets probed, hosted ones key-checked."""
    if not is_local_provider(provider):
        from tradingagents.llm_clients.api_key_env import get_api_key_env

        key_env = get_api_key_env(provider)
        if key_env is None:
            return True, f"provider '{provider}' (no key check available)"
        if os.getenv(key_env):
            return True, f"provider '{provider}': {key_env} is set"
        return False, (
            f"provider '{provider}' requires {key_env}, which is not set. "
            f"Add {key_env}=... to the backend environment/.env."
        )

    base = base_url or DEFAULT_OLLAMA_URL
    # /api/tags lives at the server root, not under the OpenAI /v1 prefix.
    root = base.rstrip("/")
    if root.endswith("/v1"):
        root = root[: -len("/v1")]
    try:
        import requests

        resp = requests.get(f"{root}/api/tags", timeout=3)
        resp.raise_for_status()
        return True, f"ollama reachable at {root}"
    except Exception as exc:  # noqa: BLE001
        return False, (
            f"Ollama not reachable at {root} ({exc}). Start it with `ollama serve` "
            f"and pull the models (see tradingagents/README.md)."
        )


def check_backend(cfg: dict | None = None) -> tuple[bool, str]:
    """Readiness check for every LLM provider this config uses.

    * Local providers (Ollama): probe the server's ``/api/tags`` endpoint.
    * Hosted providers (deepseek, openai, …): verify the API key env var is set
      (a cheap, offline check — the first real call surfaces auth errors).

    A mixed assignment is only ready when *all* its providers are, since the run
    would fail partway through otherwise. Returns ``(ok, message)``; the route
    calls this to fail fast with a friendly error before building the graph.
    """
    cfg = cfg or build_config()
    roles = cfg.get("llm_roles") or {}
    urls = {
        str(role["provider"]): role.get("base_url")
        for role in roles.values()
        if role.get("provider")
    }
    providers = providers_in_use(cfg) or (str(cfg.get("llm_provider", "")).lower(),)

    results = [_check_provider(p, urls.get(p) or cfg.get("backend_url")) for p in providers]
    ok = all(status for status, _ in results)
    # Failures first — the actionable part of a mixed message.
    message = "; ".join(
        [msg for status, msg in results if not status]
        + [msg for status, msg in results if status]
    )
    return ok, message


# Back-compat alias — the route/health imported check_ollama before the runner
# became provider-aware.
def check_ollama() -> tuple[bool, str]:
    return check_backend()


def _collect_sections(state: dict) -> dict[str, str]:
    """Extract the per-agent report sections from a final graph state.

    Mirrors the section mapping used while streaming, so a saved analysis holds
    exactly what the live view showed.
    """
    out: dict[str, str] = {}
    for key, section in _SECTION_KEYS:
        value = state.get(key)
        if value:
            out[section] = str(value)
    # Gated on judge_decision — that is what marks the debate finished, even
    # though the transcript is what we render.
    debate = state.get("investment_debate_state")
    if isinstance(debate, dict) and debate.get("judge_decision"):
        out["research_debate"] = _debate_section(debate)
    risk = state.get("risk_debate_state")
    if isinstance(risk, dict) and risk.get("judge_decision"):
        out["risk_debate"] = _debate_section(risk)
    return out


def run_analysis_stream(
    symbol: str,
    trade_date: str,
    analysts: tuple[str, ...] = DEFAULT_ANALYSTS,
    *,
    deep_think_llm: str | None = None,
    quick_think_llm: str | None = None,
    analyst_llms: dict[str, str] | None = None,
) -> Iterator[tuple[str, dict]]:
    """Run one analysis, yielding ``(event_type, data)`` tuples.

    The model keyword arguments are per-run overrides layered on the env config
    (see ``build_config``): ``quick_think_llm`` is the default analyst model,
    ``deep_think_llm`` the manager model, and ``analyst_llms`` pins individual
    analysts (``{"market": "...", "news": "..."}``) above both. Each accepts a
    ``provider:model`` spec, so roles may run on different providers.

    Event types:
      started  {symbol, date, analysts, provider, deep_think_llm,
                quick_think_llm, analyst_llms, llm_roles}
      node     {node}                       — a graph step advanced
      report   {section, content}           — a section report became available
      decision {signal, full}               — final BUY/HOLD/SELL + rationale
      saved    {id}                          — persisted to ClickHouse
      error    {error}
      done     {}
    """
    symbol = symbol.strip().upper()
    register_vn_vendor()
    patch_empty_response_recovery()
    cfg = build_config(
        deep_think_llm=deep_think_llm,
        quick_think_llm=quick_think_llm,
        analyst_llms=analyst_llms,
    )
    register_configured_models(cfg)
    apply_structured_method(cfg)
    t0 = time.perf_counter()

    from tradingagents.agents.utils.agent_utils import build_instrument_context
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    # Echo the resolved assignment so the UI can show what actually ran, not just
    # what it asked for (env defaults fill anything it left unset).
    yield "started", {
        "symbol": symbol,
        "date": trade_date,
        "analysts": list(analysts),
        "provider": str(cfg.get("llm_provider", "")),
        "deep_think_llm": str(cfg.get("deep_think_llm", "")),
        "quick_think_llm": str(cfg.get("quick_think_llm", "")),
        "analyst_llms": dict(cfg.get("analyst_llms") or {}),
        # role -> {provider, model}: the only place a mixed-provider run is
        # visible in full. base_url is omitted (it can carry a keyed gateway URL).
        "llm_roles": {
            role: {"provider": str(spec["provider"]), "model": str(spec["model"])}
            for role, spec in (cfg.get("llm_roles") or {}).items()
        },
    }

    try:
        # Model overrides are applied while the graph is built; the compiled
        # nodes keep their own clients once the block exits.
        with apply_model_overrides(cfg):
            ta = TradingAgentsGraph(
                selected_analysts=list(analysts), debug=True, config=cfg
            )

        instrument_context = build_instrument_context(symbol, "stock")
        try:
            past_context = ta.memory_log.get_past_context(symbol)
        except Exception:  # noqa: BLE001 — memory log is optional context
            past_context = ""

        init_state = ta.propagator.create_initial_state(
            symbol,
            trade_date,
            asset_type="stock",
            past_context=past_context,
            instrument_context=instrument_context,
        )
        args = ta.propagator.get_graph_args()

        seen_sections: set[str] = set()
        step = 0
        final_state: dict = {}

        # stream_mode="values": each chunk is the full accumulated state.
        for state in ta.graph.stream(init_state, **args):
            if not isinstance(state, dict):
                continue
            final_state = state
            step += 1
            yield "node", {"node": f"step {step}"}

            for key, section in _SECTION_KEYS:
                value = state.get(key)
                if value and section not in seen_sections:
                    seen_sections.add(section)
                    yield "report", {"section": section, "content": str(value)}

            # Bull vs bear transcript. Held until the research manager has ruled:
            # the transcript grows a turn at a time, and a judge_decision is what
            # says the debate is over rather than merely partway through.
            debate = state.get("investment_debate_state")
            if (
                isinstance(debate, dict)
                and debate.get("judge_decision")
                and "research_debate" not in seen_sections
            ):
                seen_sections.add("research_debate")
                yield "report", {
                    "section": "research_debate",
                    "content": _debate_section(debate),
                }

            # Aggressive / conservative / neutral transcript, same gating.
            risk = state.get("risk_debate_state")
            if (
                isinstance(risk, dict)
                and risk.get("judge_decision")
                and "risk_debate" not in seen_sections
            ):
                seen_sections.add("risk_debate")
                yield "report", {
                    "section": "risk_debate",
                    "content": _debate_section(risk),
                }

        final_decision = final_state.get("final_trade_decision", "")
        try:
            signal = ta.process_signal(final_decision) if final_decision else "HOLD"
        except Exception:  # noqa: BLE001 — signal extraction is best-effort
            signal = "HOLD"

        yield "decision", {"signal": signal, "full": str(final_decision)}

        # Persist the completed analysis so it appears in the history dashboard.
        try:
            from . import store

            sections = _collect_sections(final_state)
            # One provider column, so a mixed run records all of them ("openai+
            # deepseek"); the model stays the manager's, qualified when the run
            # spanned providers and the bare name would be ambiguous.
            used = providers_in_use(cfg)
            deep = cfg["llm_roles"]["deep"]
            analysis_id = store.save_analysis(
                symbol=symbol,
                trade_date=trade_date,
                provider="+".join(used) or str(cfg.get("llm_provider", "")),
                model=(
                    f"{deep['provider']}:{deep['model']}"
                    if len(used) > 1
                    else str(deep["model"])
                ),
                signal=signal,
                analysts=list(analysts),
                sections=sections,
                final_decision=str(final_decision),
                duration_ms=int((time.perf_counter() - t0) * 1000),
            )
            yield "saved", {"id": analysis_id}
        except Exception as exc:  # noqa: BLE001 — persistence must not fail the run
            logger.warning("Failed to persist analysis for %s: %s", symbol, exc)

        yield "done", {}
    except Exception as exc:  # noqa: BLE001
        logger.exception("TradingAgents run failed for %s", symbol)
        yield "error", {"error": str(exc)}
