"""DNSE GraphQL API client for tick data fetching.

Encapsulates the DNSE price-api GraphQL endpoint with full pagination,
end-of-day anchor fallback, and retry-once semantics.

Inputs
------
- symbol : str   – instrument code (e.g. "41I1G4000")
- day    : date  – trading date
- board  : int   – board identifier (v1 value = 2)

Outputs
-------
- list[dict] of raw API tick dicts with keys:
    symbol, matchPrice, matchQtty, sendingTime, side
  No normalization is applied — side values may be int (1/2) or
  string ("SIDE_BUY"/"SIDE_SELL"); sendingTime is ISO 8601 UTC.

Retry contract
--------------
- ``requests.RequestException`` → sleep 5 s → retry once → raise on second failure.
- GraphQL ``errors`` key → log warning, return whatever ticks were returned.
- ``data`` is None → log warning, return [].
- Empty first page without ``before`` → retry with ``before="{date}T23:59:59.999Z"`` once.
"""

from __future__ import annotations

import logging
import time
from datetime import date

import requests

REQUEST_DELAY = 0.1

API_URL = "https://api.dnse.com.vn/price-api/query"
HEADERS = {
    "accept": "*/*",
    "accept-language": "en-US,en;q=0.9,vi;q=0.8",
    "cache-control": "max-age=0",
    "content-type": "application/json",
    "origin": "https://banggia.dnse.com.vn",
    "referer": "https://banggia.dnse.com.vn/",
    "user-agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
    ),
}

GRAPHQL_QUERY = """
query GetKrxTicksBySymbols {{
    GetKrxTicksBySymbols(symbols: "{symbol}", date: "{date}", limit: {limit}{before_clause}, board: {board}) {{
      ticks {{
        symbol
        matchPrice
        matchQtty
        sendingTime
        side
      }}
    }}
  }}
"""

LIMIT = 100000


class DNSEClient:
    """Reusable DNSE GraphQL tick-data client with retry and pagination."""

    def __init__(
        self,
        request_delay: float = REQUEST_DELAY,
        timeout: int = 30,
        logger: logging.Logger | None = None,
    ) -> None:
        self.request_delay = request_delay
        self.timeout = timeout
        self.log = logger or logging.getLogger(__name__)

    def fetch_page(
        self,
        symbol: str,
        date_str: str,
        before: str | None,
        board: int,
        limit: int = LIMIT,
    ) -> list[dict]:
        """Fetch one page of ticks from the DNSE GraphQL API.

        On ``requests.RequestException`` the call is retried once after a 5 s
        sleep.  If the retry also fails the exception propagates to the caller.
        """
        before_clause = "" if before is None else f', before: "{before}"'
        payload = {
            "query": GRAPHQL_QUERY.format(
                symbol=symbol,
                date=date_str,
                limit=limit,
                before_clause=before_clause,
                board=board,
            )
        }

        data = self._post_with_retry(payload, date_str, before)

        if data.get("errors"):
            self.log.warning(
                "GraphQL errors (symbol=%s, date=%s, before=%s): %s",
                symbol,
                date_str,
                before,
                data["errors"],
            )

        if data.get("data") is None and not data.get("errors"):
            self.log.warning(
                "GraphQL returned data=null (symbol=%s, date=%s, before=%s)",
                symbol,
                date_str,
                before,
            )

        ticks_node = data.get("data", {}).get("GetKrxTicksBySymbols") or {}
        raw_ticks = ticks_node.get("ticks")
        if raw_ticks is None and data.get("data") is not None:
            self.log.warning(
                "GraphQL returned ticks=null (symbol=%s, date=%s, before=%s)",
                symbol,
                date_str,
                before,
            )

        ticks: list[dict] = raw_ticks or []
        return ticks

    def fetch_day_ticks(
        self,
        symbol: str,
        day: date,
        board: int,
    ) -> list[dict]:
        """Paginate backward through all ticks for *symbol* on *day*.

        Returns the raw API dicts — no pandas / PyArrow coercion.
        """
        date_str = day.isoformat()
        if day > date.today():
            self.log.warning(
                "%s is after today (%s): there is usually no tick data for future calendar dates.",
                date_str,
                date.today().isoformat(),
            )

        before: str | None = None
        all_ticks: list[dict] = []
        page = 0

        while True:
            page += 1
            self.log.debug(
                "  Page %d | before=%s",
                page,
                before if before is not None else "(none)",
            )

            ticks = self.fetch_page(symbol, date_str, before, board)

            # First-page empty → end-of-day anchor fallback
            if not ticks and page == 1 and before is None:
                self.log.info(
                    "  No ticks on first call without `before` for %s — retrying with before=end-of-day UTC",
                    date_str,
                )
                before = f"{date_str}T23:59:59.999Z"
                ticks = self.fetch_page(symbol, date_str, before, board)

            if not ticks:
                break

            all_ticks.extend(ticks)
            self.log.debug("  Fetched %d ticks (total %d)", len(ticks), len(all_ticks))

            # Fewer results than limit → reached the beginning
            if len(ticks) < LIMIT:
                break

            # Advance cursor to oldest tick in this batch
            before = ticks[-1]["sendingTime"]
            time.sleep(self.request_delay)

        self.log.info(
            "%s %s — %d ticks fetched across %d page(s)",
            symbol,
            date_str,
            len(all_ticks),
            page,
        )
        return all_ticks

    def _post_with_retry(
        self,
        payload: dict,
        date_str: str,
        before: str | None,
    ) -> dict:
        """POST *payload* to the DNSE API; retry once on network errors."""
        try:
            return self._post(payload)
        except requests.RequestException as exc:
            self.log.warning(
                "Request failed (date=%s, before=%s): %s — retrying in 5s",
                date_str,
                before,
                exc,
            )
            time.sleep(5)
            return self._post(payload)

    def _post(self, payload: dict) -> dict:
        resp = requests.post(
            API_URL, headers=HEADERS, json=payload, timeout=self.timeout
        )
        try:
            resp.raise_for_status()
        except requests.HTTPError:
            self.log.error(
                "DNSE API HTTP %s: %s",
                resp.status_code,
                resp.text[:2000],
            )
            raise
        return resp.json()
