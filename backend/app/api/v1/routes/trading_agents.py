"""TradingAgents multi-agent analysis API.

Runs the vendored TauricResearch/TradingAgents multi-agent graph (market +
news analysts -> bull/bear debate -> trader -> risk management -> portfolio
manager) against this platform's Vietnamese-market data, driven by a local
Ollama server. Progress and section reports stream to the client via SSE.

See app/services/tradingagents/ for the data adapter, runner, and setup notes.
"""
from __future__ import annotations

import json
import os
from datetime import date
from typing import Dict, Generator, List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

router = APIRouter(prefix="/trading-agents", tags=["trading-agents"])


class AnalyzeRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=32)
    trade_date: Optional[str] = Field(
        None, description="Analysis date YYYY-MM-DD; defaults to today"
    )
    analysts: Optional[List[str]] = Field(
        None,
        description=(
            "Analyst subset; defaults to runner.DEFAULT_ANALYSTS (the ones backed "
            "by VN data)"
        ),
    )
    # Model overrides for this run. Each accepts a bare model name (served by the
    # env-configured provider) or a "provider:model" spec, so roles can run on
    # different providers — e.g. deep on openai, quick on deepseek.
    quick_think_llm: Optional[str] = Field(
        None,
        description=(
            "Default analyst model (also researchers/trader/risk), optionally "
            "'provider:model'; defaults to TRADINGAGENTS_QUICK_THINK_LLM"
        ),
    )
    deep_think_llm: Optional[str] = Field(
        None,
        description=(
            "Research- and portfolio-manager model, optionally 'provider:model'; "
            "defaults to TRADINGAGENTS_DEEP_THINK_LLM"
        ),
    )
    analyst_models: Optional[Dict[str, str]] = Field(
        None,
        description=(
            "Per-analyst model, e.g. {\"market\": \"deepseek:deepseek-v4-flash\"}. "
            "Wins over quick_think_llm for that analyst only. Keys: "
            "market | news | fundamentals | social"
        ),
    )


def _validated_analyst_models(request: AnalyzeRequest) -> Dict[str, str]:
    """Per-analyst overrides, rejected up front rather than mid-stream.

    A bad key would otherwise be silently ignored (the runner only looks up the
    analysts it knows), so the frontend would show a run that quietly used the
    default model.
    """
    from app.services.tradingagents.runner import ANALYST_MODEL_KEYS

    models = {
        analyst: str(model).strip()
        for analyst, model in (request.analyst_models or {}).items()
        if str(model).strip()
    }
    unknown = sorted(set(models) - set(ANALYST_MODEL_KEYS))
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown analyst(s) in analyst_models: {', '.join(unknown)}. "
                f"Valid keys: {', '.join(ANALYST_MODEL_KEYS)}."
            ),
        )
    return models


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.get("/health")
def health() -> dict:
    """Report whether every configured LLM provider is ready, and which models."""
    from app.services.tradingagents.runner import (
        build_config,
        check_backend,
        providers_in_use,
    )
    from app.services.tradingagents import web_search

    cfg = build_config()
    ok, message = check_backend(cfg)
    return {
        "backend_ready": ok,
        # Back-compat with the frontend, which reads `ollama_reachable`.
        "ollama_reachable": ok,
        "message": message,
        # Default provider for bare model names; a run may use several.
        "provider": cfg["llm_provider"],
        "providers": list(providers_in_use(cfg)),
        "deep_think_llm": cfg["deep_think_llm"],
        "quick_think_llm": cfg["quick_think_llm"],
        # Analysts left out of this map run on quick_think_llm.
        "analyst_llms": cfg.get("analyst_llms") or {},
        # role -> {provider, model}, the full assignment (base_url withheld).
        "llm_roles": {
            role: {"provider": spec["provider"], "model": spec["model"]}
            for role, spec in (cfg.get("llm_roles") or {}).items()
        },
        "web_search_backend": web_search.active_backend(),
    }


@router.get("/models")
def list_models() -> dict:
    """Model choices per provider, for the frontend's pickers.

    Catalog entries are a convenience, not a whitelist: any model a provider
    serves is accepted by ``/analyze/stream`` (Ollama, OpenRouter and the like are
    open-ended, so the catalog offers a "custom" entry rather than a complete
    list). ``ready`` says whether that provider's API key is present — an
    unqualified pick still goes to ``provider``, so a picker can offer
    ``provider:model`` specs from any ready provider and mix them across roles.
    """
    from app.services.tradingagents.runner import (
        ANALYST_MODEL_KEYS,
        DEFAULT_ANALYSTS,
        build_config,
        is_local_provider,
    )

    cfg = build_config()
    provider = str(cfg["llm_provider"])

    providers: dict[str, dict] = {}
    try:
        from tradingagents.llm_clients.api_key_env import get_api_key_env
        from tradingagents.llm_clients.model_catalog import MODEL_OPTIONS

        for name, modes in MODEL_OPTIONS.items():
            key_env = get_api_key_env(name)
            providers[name] = {
                "quick": [m for _label, m in modes.get("quick", ()) if m != "custom"],
                "deep": [m for _label, m in modes.get("deep", ()) if m != "custom"],
                "key_env": key_env,
                # Local runtimes and keyless relays authenticate with nothing.
                "ready": bool(
                    is_local_provider(name) or not key_env or os.getenv(key_env)
                ),
            }
    except Exception as exc:  # noqa: BLE001 — catalog drift must not break the page
        logger.warning("Could not read the model catalog: {}", exc)

    # The configured models are the ones this deployment actually runs, and they
    # are routinely newer than the vendored catalog — offer them as picks instead
    # of leaving the operator to retype them.
    for role, spec in (cfg.get("llm_roles") or {}).items():
        entry = providers.setdefault(
            str(spec["provider"]),
            {"quick": [], "deep": [], "key_env": None, "ready": True},
        )
        mode = "deep" if role == "deep" else "quick"
        if spec["model"] not in entry[mode]:
            entry[mode].insert(0, spec["model"])

    entry = providers.get(provider, {})
    return {
        "provider": provider,
        "providers": providers,
        # Back-compat: the default provider's own catalog.
        "options": {"quick": entry.get("quick", []), "deep": entry.get("deep", [])},
        "defaults": {
            "deep_think_llm": cfg["deep_think_llm"],
            "quick_think_llm": cfg["quick_think_llm"],
            "analyst_llms": cfg.get("analyst_llms") or {},
            "llm_roles": {
                role: {"provider": spec["provider"], "model": spec["model"]}
                for role, spec in (cfg.get("llm_roles") or {}).items()
            },
        },
        "analyst_keys": list(ANALYST_MODEL_KEYS),
        "default_analysts": list(DEFAULT_ANALYSTS),
    }


@router.get("/analyses")
def list_analyses(
    symbol: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
) -> dict:
    """List saved analyses (metadata + snippet), newest first."""
    from app.services.tradingagents import store

    return {"analyses": store.list_analyses(symbol=symbol, limit=limit)}


@router.get("/analyses/{analysis_id}")
def get_analysis(analysis_id: str) -> dict:
    """Fetch one saved analysis with its full per-agent reports."""
    from app.services.tradingagents import store

    record = store.get_analysis(analysis_id)
    if not record:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return record


@router.post("/analyze/stream")
def analyze_stream(request: AnalyzeRequest) -> StreamingResponse:
    """Run a multi-agent analysis for one symbol, streaming progress via SSE."""
    from app.services.tradingagents.runner import DEFAULT_ANALYSTS

    symbol = request.symbol.strip().upper()
    trade_date = request.trade_date or date.today().strftime("%Y-%m-%d")
    # Fall back to the runner's list rather than a second hardcoded copy, so
    # enabling an analyst there actually reaches this endpoint.
    analysts = tuple(request.analysts) if request.analysts else DEFAULT_ANALYSTS
    # Raised before the StreamingResponse so a bad key is a 400, not an SSE
    # error event the caller has to dig out of the stream.
    analyst_models = _validated_analyst_models(request)

    def event_generator() -> Generator[str, None, None]:
        # Import here so a heavy/broken TradingAgents install can't crash app
        # startup — only requests to this endpoint pay the import cost.
        from app.services.tradingagents.runner import (
            check_backend,
            run_analysis_stream,
        )

        logger.info("TradingAgents analyze: {} on {}", symbol, trade_date)

        ok, message = check_backend()
        if not ok:
            yield _sse("error", {"error": message})
            return

        # Held by name so the runner's cleanup (releasing the checkpointer,
        # unwinding the graph) is driven from this thread when the client
        # disconnects, instead of being left to whenever the generator is
        # collected. A disconnect closes *this* generator at the yield below, so
        # GeneratorExit — a BaseException — deliberately passes the handler: there
        # is no longer a client to send an error event to.
        events = run_analysis_stream(
            symbol,
            trade_date,
            analysts,
            deep_think_llm=request.deep_think_llm,
            quick_think_llm=request.quick_think_llm,
            analyst_llms=analyst_models,
        )
        try:
            for event_type, data in events:
                yield _sse(event_type, data)
        except Exception as exc:  # noqa: BLE001
            logger.exception("TradingAgents stream crashed")
            yield _sse("error", {"error": str(exc)})
        finally:
            events.close()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
