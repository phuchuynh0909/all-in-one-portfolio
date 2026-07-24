"""Run the vendored TradingAgents graph against Vietnamese-market data.

Responsibilities:
  * Build a config that points the framework at a local Ollama server and at the
    ``portfolio`` (VN) data vendor.
  * Register the VN vendor into TradingAgents' dispatch table (idempotent) and
    patch the verification-snapshot loader to use VN OHLCV.
  * Drive the graph and yield progress events for SSE streaming.

Only the ``market`` and ``news`` analysts run by default — those are the two the
platform has real Vietnamese data for (OHLCV/indicators and research reports).
The full researcher debate → trader → risk-management → portfolio-manager
pipeline downstream is unchanged.

Prerequisite: a running Ollama server with the configured models pulled. See
``README.md`` in this package.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Iterator

# Importing the package installs the sys.path shim for ``import tradingagents``.
from . import vn_data

logger = logging.getLogger(__name__)

DEFAULT_ANALYSTS = ("market", "news")


def _sector_enabled() -> bool:
    """Whether to append the standalone sector-analyst section (default yes)."""
    return os.getenv("TRADINGAGENTS_SECTOR_ANALYST", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )

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
    "deepseek": ("deepseek-reasoner", "deepseek-chat"),
    "openai": ("gpt-5.5", "gpt-5.4-mini"),
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

_registered = False


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


def apply_structured_method(cfg: dict) -> None:
    """Make local models use a reliable structured-output method.

    Registers a capability override for the configured model IDs so the
    structured agents (Research Manager, Trader, Portfolio Manager) use
    ``json_schema`` (or the env-selected method) instead of the default
    ``function_calling``, which local models handle poorly. No-op for hosted
    providers and when the method is left at ``function_calling``.
    """
    provider = str(cfg.get("llm_provider", "")).lower()
    if provider not in _LOCAL_PROVIDERS:
        return

    method = os.getenv("TRADINGAGENTS_OLLAMA_STRUCTURED_METHOD", "json_schema").strip()
    if method == "function_calling":
        return  # keep the framework default

    from tradingagents.llm_clients import capabilities as caps_mod

    overridden = caps_mod.ModelCapabilities(
        supports_tool_choice=True,
        supports_json_mode=True,
        supports_json_schema=True,
        preferred_structured_method=method,  # type: ignore[arg-type]
    )
    for model in {cfg.get("deep_think_llm"), cfg.get("quick_think_llm")}:
        if model:
            caps_mod._BY_ID[model] = overridden
    logger.info("Structured-output method for local models set to %r", method)


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


def build_config() -> dict:
    """Build a TradingAgents config for the VN data vendor + selected LLM provider.

    Provider-agnostic: defaults to a local Ollama server, but set
    ``TRADINGAGENTS_LLM_PROVIDER`` to any supported provider (e.g. ``deepseek``,
    ``openai``, ``anthropic``) and it configures the right models, endpoint, and
    auth path. Env overrides (all optional):

      TRADINGAGENTS_LLM_PROVIDER      (default: ollama)
      TRADINGAGENTS_DEEP_THINK_LLM    (default: per-provider, see _PROVIDER_MODEL_DEFAULTS)
      TRADINGAGENTS_QUICK_THINK_LLM   (default: per-provider)
      TRADINGAGENTS_LLM_BACKEND_URL   (default: Ollama URL for local providers, else provider default)
      TRADINGAGENTS_MAX_DEBATE_ROUNDS (default: 1)
      TRADINGAGENTS_MAX_RISK_ROUNDS   (default: 1)
      TRADINGAGENTS_OUTPUT_LANGUAGE   (default: English)

    Hosted providers read their API key from the standard env var
    (e.g. ``DEEPSEEK_API_KEY``, ``OPENAI_API_KEY``).
    """
    from tradingagents.default_config import DEFAULT_CONFIG

    cfg = dict(DEFAULT_CONFIG)  # shallow copy; nested dicts replaced wholesale below

    provider = os.getenv("TRADINGAGENTS_LLM_PROVIDER", "ollama").lower()
    provider = _PROVIDER_ALIASES.get(provider, provider)
    cfg["llm_provider"] = provider
    _bridge_api_key_aliases(provider)

    deep_default, quick_default = _PROVIDER_MODEL_DEFAULTS.get(
        provider, (cfg["deep_think_llm"], cfg["quick_think_llm"])
    )
    cfg["deep_think_llm"] = os.getenv("TRADINGAGENTS_DEEP_THINK_LLM", deep_default)
    cfg["quick_think_llm"] = os.getenv("TRADINGAGENTS_QUICK_THINK_LLM", quick_default)

    # Local providers need a base URL (default: host-run Ollama reachable from the
    # container via host.docker.internal). Hosted providers (deepseek, openai, …)
    # use their own default endpoint, so leave backend_url None unless explicitly
    # overridden — otherwise deepseek requests would be sent to the Ollama URL.
    explicit_url = os.getenv("TRADINGAGENTS_LLM_BACKEND_URL") or os.getenv(
        "OLLAMA_BASE_URL"
    )
    if is_local_provider(provider):
        cfg["backend_url"] = explicit_url or DEFAULT_OLLAMA_URL
    else:
        cfg["backend_url"] = explicit_url or None

    cfg["max_debate_rounds"] = int(os.getenv("TRADINGAGENTS_MAX_DEBATE_ROUNDS", "1"))
    cfg["max_risk_discuss_rounds"] = int(os.getenv("TRADINGAGENTS_MAX_RISK_ROUNDS", "1"))
    cfg["output_language"] = os.getenv("TRADINGAGENTS_OUTPUT_LANGUAGE", "Vietnamese")
    cfg["checkpoint_enabled"] = False
    cfg["online_tools"] = True
    cfg["data_vendors"] = {cat: "portfolio" for cat in _ALL_CATEGORIES}
    cfg["tool_vendors"] = {}
    return cfg


def check_backend(cfg: dict | None = None) -> tuple[bool, str]:
    """Readiness check for the configured LLM backend.

    * Local providers (Ollama): probe the server's ``/api/tags`` endpoint.
    * Hosted providers (deepseek, openai, …): verify the API key env var is set
      (a cheap, offline check — the first real call surfaces auth errors).

    Returns ``(ok, message)``. The route calls this to fail fast with a friendly
    error before spinning up the whole graph.
    """
    cfg = cfg or build_config()
    provider = str(cfg.get("llm_provider", "")).lower()

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

    base = cfg.get("backend_url") or DEFAULT_OLLAMA_URL
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
    debate = state.get("investment_debate_state")
    if isinstance(debate, dict) and debate.get("judge_decision"):
        out["research_debate"] = str(debate["judge_decision"])
    risk = state.get("risk_debate_state")
    if isinstance(risk, dict) and risk.get("judge_decision"):
        out["risk_debate"] = str(risk["judge_decision"])
    return out


def run_analysis_stream(
    symbol: str,
    trade_date: str,
    analysts: tuple[str, ...] = DEFAULT_ANALYSTS,
) -> Iterator[tuple[str, dict]]:
    """Run one analysis, yielding ``(event_type, data)`` tuples.

    Event types:
      started  {symbol, date, analysts}
      node     {node}                       — a graph step advanced
      report   {section, content}           — a section report became available
      decision {signal, full}               — final BUY/HOLD/SELL + rationale
      saved    {id}                          — persisted to ClickHouse
      error    {error}
      done     {}
    """
    symbol = symbol.strip().upper()
    register_vn_vendor()
    cfg = build_config()
    apply_structured_method(cfg)
    t0 = time.perf_counter()

    from tradingagents.agents.utils.agent_utils import build_instrument_context
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    yield "started", {"symbol": symbol, "date": trade_date, "analysts": list(analysts)}

    try:
        ta = TradingAgentsGraph(
            selected_analysts=list(analysts), debug=False, config=cfg
        )

        # Skip the yfinance identity lookup (VN tickers aren't on Yahoo): pass a
        # ticker-only instrument context so no network call is made at run start.
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

            # Research-debate verdict (bull vs bear judged by the research manager).
            debate = state.get("investment_debate_state")
            if (
                isinstance(debate, dict)
                and debate.get("judge_decision")
                and "research_debate" not in seen_sections
            ):
                seen_sections.add("research_debate")
                yield "report", {
                    "section": "research_debate",
                    "content": str(debate["judge_decision"]),
                }

            # Risk-management verdict.
            risk = state.get("risk_debate_state")
            if (
                isinstance(risk, dict)
                and risk.get("judge_decision")
                and "risk_debate" not in seen_sections
            ):
                seen_sections.add("risk_debate")
                yield "report", {
                    "section": "risk_debate",
                    "content": str(risk["judge_decision"]),
                }

        # Sector analyst — a standalone section (not part of the vendored graph).
        # Runs after the analysts so it can reuse the same quick-thinking LLM.
        sector_report = ""
        if _sector_enabled():
            try:
                from . import sector_analyst

                sector_report = sector_analyst.run_sector_analyst(
                    symbol,
                    trade_date,
                    ta.quick_thinking_llm,
                    language=str(cfg.get("output_language", "English")),
                )
                if sector_report:
                    yield "report", {"section": "sector", "content": sector_report}
            except Exception as exc:  # noqa: BLE001 — never fail the run on the extra section
                logger.warning("Sector analyst failed for %s: %s", symbol, exc)

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
            if sector_report:
                sections["sector"] = sector_report
            analysis_id = store.save_analysis(
                symbol=symbol,
                trade_date=trade_date,
                provider=str(cfg.get("llm_provider", "")),
                model=str(cfg.get("deep_think_llm", "")),
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
