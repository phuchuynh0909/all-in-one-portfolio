"""TradingAgents multi-agent analysis API.

Runs the vendored TauricResearch/TradingAgents multi-agent graph (market +
news analysts -> bull/bear debate -> trader -> risk management -> portfolio
manager) against this platform's Vietnamese-market data, driven by a local
Ollama server. Progress and section reports stream to the client via SSE.

See app/services/tradingagents/ for the data adapter, runner, and setup notes.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Generator, List, Optional

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


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.get("/health")
def health() -> dict:
    """Report whether the Ollama backend is reachable and the current models."""
    from app.services.tradingagents.runner import build_config, check_backend
    from app.services.tradingagents import web_search

    cfg = build_config()
    ok, message = check_backend(cfg)
    return {
        "backend_ready": ok,
        # Back-compat with the frontend, which reads `ollama_reachable`.
        "ollama_reachable": ok,
        "message": message,
        "provider": cfg["llm_provider"],
        "deep_think_llm": cfg["deep_think_llm"],
        "quick_think_llm": cfg["quick_think_llm"],
        "web_search_backend": web_search.active_backend(),
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

        try:
            for event_type, data in run_analysis_stream(symbol, trade_date, analysts):
                yield _sse(event_type, data)
        except Exception as exc:  # noqa: BLE001
            logger.exception("TradingAgents stream crashed")
            yield _sse("error", {"error": str(exc)})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
