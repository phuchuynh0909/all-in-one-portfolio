"""MVF (Mean-Variance with Forecasting) portfolio API.

Trains one LSTM per asset to forecast the next `horizon` days, feeds those
forward-looking returns plus a Ledoit-Wolf-shrunk historical covariance into a
capped long-only max-Sharpe optimizer, and returns deployable weights with a
share-count order sheet.

Runs take minutes on a cold cache, so the work streams over SSE with per-asset
progress rather than blocking a single request. See
app/services/mvf_lstm_service.py for the pipeline.
"""
from __future__ import annotations

import json
from typing import Generator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from loguru import logger

from app.schemas.mvf import MvfRequest

router = APIRouter(prefix="/portfolio/mvf", tags=["MVF LSTM"])


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.post("/stream")
def mvf_stream(request: MvfRequest) -> StreamingResponse:
    """Run the MVF pipeline for a universe, streaming progress then the result."""

    def event_generator() -> Generator[str, None, None]:
        # Imported here so torch's import cost is paid by this endpoint's first
        # request rather than by app startup.
        from app.services.mvf_lstm_service import stream_mvf

        logger.info("MVF run: {} tickers, horizon={}d",
                    len(request.tickers), request.horizon)
        try:
            for event, payload in stream_mvf(request):
                yield _sse(event, payload)
        except ValueError as exc:
            # Bad universe / not enough history — the user's input, not a crash.
            logger.warning("MVF rejected request: {}", exc)
            yield _sse("error", {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            logger.exception("MVF stream crashed")
            yield _sse("error", {"error": str(exc)})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
