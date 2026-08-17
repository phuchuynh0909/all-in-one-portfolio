"""Client for wichart's xbrain-news feed (Vietnamese macro & market news).

Each item pairs an AI-written summary with the structured datapoint behind it —
``macro_info`` carries ``{title, value, unit, data_date}`` — so the feed doubles
as a macro-indicator source: interbank rates, OMO/SBV operations, the USD/VND
fix, CPI, GDP, FDI, trade and PMI releases all arrive as dated values with
commentary attached.

Server-side filters (all optional, combinable):

  * ``category_type`` — the stream. ``"Việt Nam"`` is the macro stream; leaving it
    off returns the firehose, which is ~95% single-company news (earnings,
    analyst PDFs, insider trades) and useless for macro. ``"Thế giới"`` returns
    nothing, so there is no global/US macro here.
  * ``tag_level_1`` — exact topic tag: ``Lãi suất``, ``Tỷ giá``,
    ``Chính sách tiền tệ``, ``Giá cả``, ``Tăng trưởng kinh tế``, ``Sản xuất``,
    ``Đầu tư``, ``Giao dịch quốc tế``, ``Tiêu dùng``, ``Hệ thống ngân hàng``, …
  * ``search`` — free text over the item, for topics with no exact tag.
  * ``important_level`` — 1/2/3; 3 is the editor's highest.

The browser sends a pile of signed headers (``device-token``, ``sign``,
``sign-token``, ``nonce``, ``stime``, ``visit-id``). None are required — the
endpoint answers 200 with just ``Accept``/``Origin``/``Referer``/``User-Agent``,
so this client does not carry, refresh, or forge any of them.

Configuration (all optional):

    WICHART_NEWS_BASE_URL   default https://wichart.vn/wichartapi
    WICHART_NEWS_TIMEOUT    per-request timeout, seconds (default 20)
    WICHART_NEWS_CACHE_TTL  cache TTL, seconds (default 300)
    WICHART_NEWS_ORIGIN     Origin/Referer sent with the request
    WICHART_NEWS_USER_AGENT overrides the browser UA sent upstream
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

BASE_URL = os.getenv("WICHART_NEWS_BASE_URL", "https://wichart.vn/wichartapi").rstrip("/")
TIMEOUT = float(os.getenv("WICHART_NEWS_TIMEOUT", "20"))

# The feed publishes a handful of items a day, so five minutes of staleness costs
# nothing and keeps one analyst turn from refetching per query.
CACHE_TTL_SECONDS = float(os.getenv("WICHART_NEWS_CACHE_TTL", "300"))

#: The macro stream. Company/analyst-report items live under other category_types.
MACRO_CATEGORY_TYPE = "Việt Nam"

_ORIGIN = os.getenv("WICHART_NEWS_ORIGIN", "https://widata.vn")
_USER_AGENT = os.getenv(
    "WICHART_NEWS_USER_AGENT",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
)

_cache: dict[tuple, tuple[float, list[dict[str, Any]]]] = {}


class WichartNewsError(RuntimeError):
    """Upstream returned no usable news payload."""


def fetch_news(
    *,
    category_type: Optional[str] = MACRO_CATEGORY_TYPE,
    tag_level_1: Optional[str] = None,
    search: Optional[str] = None,
    codetag: Optional[str] = None,
    important_level: Optional[int] = None,
    limit: int = 20,
    page: int = 1,
) -> list[dict[str, Any]]:
    """Return one page of news items, newest first.

    ``codetag`` filters to a single ticker's company news (e.g. ``"KBC"``); it is
    the firehose stream rather than the macro one, so pass ``category_type=None``
    alongside it.

    Raises :class:`WichartNewsError` when the envelope is not the expected shape;
    an empty result set is returned as ``[]`` rather than raised, since "no items
    matched this filter" is a normal answer. Transport failures propagate as
    ``requests`` exceptions.
    """
    import requests

    params: dict[str, Any] = {"page": int(page), "limit": int(limit)}
    if category_type:
        params["category_type"] = category_type
    if tag_level_1:
        params["tag_level_1"] = tag_level_1
    if search:
        params["search"] = search
    if codetag:
        params["codetag"] = codetag
    if important_level is not None:
        params["important_level"] = int(important_level)

    key = tuple(sorted(params.items()))
    now = time.monotonic()
    cached = _cache.get(key)
    if cached and now - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]

    resp = requests.get(
        f"{BASE_URL}/xbrain-news/",
        params=params,
        headers={
            "Accept": "application/json, text/plain, */*",
            "Origin": _ORIGIN,
            "Referer": f"{_ORIGIN}/",
            "User-Agent": _USER_AGENT,
        },
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    envelope = resp.json()

    if not isinstance(envelope, dict) or not isinstance(envelope.get("data"), list):
        raise WichartNewsError(f"unexpected xbrain-news payload for {params}")

    items = [item for item in envelope["data"] if isinstance(item, dict)]
    _cache[key] = (now, items)
    logger.debug("Fetched %d xbrain-news items for %s", len(items), params)
    return items
