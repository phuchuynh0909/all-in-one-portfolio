"""Client for 24hmoney's company-index endpoint.

One GET returns a company's current valuation/profitability snapshot — P/E, P/B,
EPS, book value, ROE, ROA, EV multiples, beta, market cap, 52-week range, free
float, foreign ownership room, ICB industry chain and auditor. It is the ratio
layer the raw financial statements (see ``ruatichsan_client``) do not carry.

Two response quirks the callers rely on:

  * An unknown symbol is **not** an error — the API answers ``200`` with an empty
    ``data`` object. That is normalized to :class:`Money24hError` here so callers
    do not have to distinguish "no such ticker" from "no fields".
  * Metrics that do not apply to a sector come back as ``0.0`` rather than
    ``null`` (banks report ``ev_per_ebitda: 0.0``), so formatting code must treat
    an exact zero as missing.

Configuration (all optional):

    MONEY24H_BASE_URL    default https://api-finance-t19.24hmoney.vn/v2/ios
    MONEY24H_TIMEOUT     per-request timeout, seconds (default 15)
    MONEY24H_CACHE_TTL   snapshot cache TTL, seconds (default 900)
    MONEY24H_USER_AGENT  sent verbatim; the endpoint rejects some default UAs
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

BASE_URL = os.getenv(
    "MONEY24H_BASE_URL", "https://api-finance-t19.24hmoney.vn/v2/ios"
).rstrip("/")
TIMEOUT = float(os.getenv("MONEY24H_TIMEOUT", "15"))

# The ratios are recomputed from the last close plus the newest published report,
# so a quarter-hour of staleness is invisible while collapsing the repeated calls
# an analyst turn makes for one symbol.
CACHE_TTL_SECONDS = float(os.getenv("MONEY24H_CACHE_TTL", "900"))

USER_AGENT = os.getenv(
    "MONEY24H_USER_AGENT",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
)

_cache: dict[str, tuple[float, dict[str, Any]]] = {}


class Money24hError(RuntimeError):
    """Upstream returned no usable company-index data."""


def fetch_company_index(symbol: str) -> dict[str, Any]:
    """Return the company-index snapshot for ``symbol``.

    Raises :class:`Money24hError` when the symbol is unknown (empty ``data``) or
    the response is not the expected envelope; transport failures propagate as
    ``requests`` exceptions.
    """
    import requests

    sym = str(symbol).strip().upper()
    now = time.monotonic()
    cached = _cache.get(sym)
    if cached and now - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]

    resp = requests.get(
        f"{BASE_URL}/companies/index",
        params={"symbol": sym},
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    envelope = resp.json()

    data = envelope.get("data") if isinstance(envelope, dict) else None
    if not isinstance(data, dict) or not data:
        raise Money24hError(
            f"no company-index data for {sym} "
            f"(status={envelope.get('status') if isinstance(envelope, dict) else '?'})"
        )

    _cache[sym] = (now, data)
    logger.debug("Fetched 24hmoney company index for %s (%d fields)", sym, len(data))
    return data
