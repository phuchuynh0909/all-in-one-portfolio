from fastapi import APIRouter

from app.schemas.quote import LatestQuote, QuoteBatchRequest, QuoteBatchResponse
from app.services.dnse_client import get_latest_quote, get_latest_quotes


router = APIRouter(prefix="/quote", tags=["quote"])


@router.get("/{symbol}/latest", response_model=LatestQuote)
async def latest_quote(symbol: str) -> LatestQuote:
    """Latest matched trade for a symbol.

    Polled by the chart's TradingView datafeed (`subscribeBars`) to keep the
    current daily bar live. Responses are cached upstream for a couple of
    seconds, so polling this at a few-second interval is cheap.
    """
    return await get_latest_quote(symbol)


@router.post("/batch", response_model=QuoteBatchResponse)
async def batch_quotes(request: QuoteBatchRequest) -> QuoteBatchResponse:
    """Latest matched trades for a list of symbols, for the chart watchlist.

    Symbols the provider cannot answer for are reported in `unavailable` rather
    than failing the batch.
    """
    quotes, unavailable = await get_latest_quotes(request.symbols)
    return QuoteBatchResponse(quotes=quotes, unavailable=unavailable)
