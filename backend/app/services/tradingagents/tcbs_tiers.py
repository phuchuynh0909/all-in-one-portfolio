"""TCBS blocks for the analyst tools in ``vn_data.py``.

Each function here renders one tool's TCBS tier and returns ``None`` -- never
raises, never returns a sentinel -- when TCBS cannot serve it. The caller in
``vn_data.py`` then falls through to the source it used before, so a checkout
with no TCBS login behaves exactly as it did.

Only ticker-scoped tools are called. The connector can also expose the
authenticated user's own portfolio and transaction history; none of that
belongs in an agent's context.

Field names here come from ``docs/tcbs-mcp-tools.json`` and from real responses,
not from the help page: the published tool list documents names and prose but no
argument or payload shapes, and several differ from the obvious guess
(``priceToEarning`` rather than ``pe``, a per-tool list envelope rather than a
uniform ``data`` key).
"""
from __future__ import annotations

import logging
from typing import Any

from app.services import tcbs_mcp_client as tcbs

from .utils import fmt_count

logger = logging.getLogger(__name__)

#: Every tool on this server is namespaced. Callers below name tools the way the
#: documentation does and the prefix is applied in one place.
_TOOL_PREFIX = "tcinvest-"

#: Deals shown per block. The analyst needs the recent pattern, not the archive.
_INSIDER_ROWS = 15

#: ``dealingAction`` codes. The signed ``quantity`` says the same thing, but the
#: word is what the model reads.
_DEALING_ACTION = {"0": "Buy", "1": "Sell", 0: "Buy", 1: "Sell"}

#: TCBS returns SQL Server's minimum date for "this event has no such date".
_NULL_DATES = ("1753-01-01", "0001-01-01")


def _rows(payload: Any) -> list[dict]:
    """Normalize TCBS's response envelopes to a list of records.

    Each tool family wraps its list under its own key -- ``listInsiderDealing``,
    ``listVolumeForeignInfoDto``, ``value``, ``result``, ``listActivityNews``,
    ``listEventNews`` -- so the unwrap is generic rather than a key list that
    would need extending for every new tool.
    """
    if payload is None:
        return []
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for value in payload.values():
            if isinstance(value, list) and all(isinstance(r, dict) for r in value):
                return value
        return [payload]
    return []


def _try(tool: str, **params) -> Any:
    """Call one TCBS tool; None on any failure, so a block can degrade in parts."""
    try:
        return tcbs.call(f"{_TOOL_PREFIX}{tool}", **params)
    except (tcbs.TcbsNoData, tcbs.TcbsUnavailable) as exc:
        logger.info("TCBS %s unavailable: %s", tool, exc)
        return None
    except Exception as exc:  # noqa: BLE001 -- a tier must never raise
        logger.warning("TCBS %s failed: %s", tool, exc)
        return None


def _first(row: dict, *keys: str, default: Any = None) -> Any:
    """First present key. TCBS field names vary in case across tool families."""
    lowered = {k.lower(): v for k, v in row.items()}
    for key in keys:
        if row.get(key) not in (None, ""):
            return row[key]
        if lowered.get(key.lower()) not in (None, ""):
            return lowered[key.lower()]
    return default


def _vn_date(value: Any) -> str:
    """Normalize TCBS's several date spellings to ISO, or '-'.

    The feeds mix ``dd/mm/yy`` (insider deals), ``dd/mm/yyyy`` (foreign flow) and
    ``yyyy-mm-dd hh:mm:ss`` (news). An analyst comparing dates across blocks
    needs one spelling.
    """
    text = str(value or "").strip()
    if not text:
        return "-"
    if "/" in text:
        parts = text.split()[0].split("/")
        if len(parts) == 3:
            day, month, year = parts
            if len(year) == 2:
                year = f"20{year}"
            return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
        return text
    iso = text[:10]
    return iso if len(iso) == 10 else text


def _is_null_date(value: str) -> bool:
    return any(value.startswith(sentinel) for sentinel in _NULL_DATES)


def _fmt(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return "-"


def insider_transactions(symbol: str) -> str | None:
    """Insider dealing plus foreign flow, or None when TCBS cannot serve it.

    The feed carries no dealer name or position -- only the announcement date,
    direction, size and price -- so the table does not have those columns. An
    empty column would read as missing data rather than as absent by design.
    """
    if not tcbs.enabled():
        return None

    sym = str(symbol).upper()
    deals = _rows(_try("getInsiderDealing", ticker=sym, size=_INSIDER_ROWS))
    if not deals:
        return None

    lines = [
        f"# {sym} — insider dealing",
        "",
        "| Announced | Action | Volume | Price (VND) | Ratio of holding |",
        "|---|---|---|---|---|",
    ]
    for row in deals[:_INSIDER_ROWS]:
        action_raw = _first(row, "dealingAction", "action", default="")
        quantity = _first(row, "quantity", "dealVolume", default=0)
        lines.append(
            "| {date} | {action} | {volume} | {price} | {ratio} |".format(
                date=_vn_date(_first(row, "anDate", "dealAnnounceDate", default="")),
                action=_DEALING_ACTION.get(action_raw, str(action_raw) or "-"),
                volume=fmt_count(abs(float(quantity)) if quantity else 0),
                price=_fmt(_first(row, "price"), 0),
                ratio=_fmt(_first(row, "ratio"), 4),
            )
        )

    foreign = _rows(_try("getVolumeAndForeign", ticker=sym))
    if foreign:
        # The series is oldest-first; the analyst wants the latest session.
        latest = foreign[-1]
        net = _first(latest, "netForeignVol")
        rank = _first(latest, "rsRank")
        bits = []
        if net is not None:
            direction = "net foreign buying" if float(net) > 0 else "net foreign selling"
            bits.append(f"{direction} of {fmt_count(abs(float(net)))} shares")
        if rank is not None:
            bits.append(f"RS rank {_fmt(rank, 0)}")
        if bits:
            lines += [
                "",
                f"Latest session ({_vn_date(_first(latest, 'dateReport'))}): "
                + ", ".join(bits)
                + ".",
            ]

    lines += [
        "",
        "Source: TCBS (TCInvest). Dates are announcement dates, not trade dates, "
        "and the feed does not identify the individual dealer. Do not "
        "extrapolate beyond the rows shown.",
    ]
    return "\n".join(lines)


from functools import lru_cache  # noqa: E402

from .sector_analyst import sector_tags  # noqa: E402

#: TCBS splits every statement and ratio tool into bank and non-bank variants,
#: because the two report different line items entirely. The committed sector
#: map already tags banks, so the split costs no extra call. (TCBS agrees:
#: getTickerOverview returns companyType "NH" for one, but that needs a call.)
_BANK_TAGS = {"ngân hàng", "ngan hang", "banking", "bank"}

#: Peers shown in the comparison table.
_PEER_ROWS = 8

#: (label, field, decimals) for metrics every company reports.
_COMMON_RATIOS = (
    ("Market cap (bn VND)", "capitalize", 0),
    ("P/E", "priceToEarning", 2),
    ("P/B", "priceToBook", 2),
    ("EPS (VND/share)", "earningPerShare", 0),
    ("Book value (VND/share)", "bookValuePerShare", 0),
    ("ROE", "roe", 3),
    ("Dividend yield", "dividend", 3),
    ("Revenue (bn VND)", "revenue", 0),
    ("Net profit (bn VND)", "netProfit", 0),
    ("Equity (bn VND)", "equity", 0),
    ("Beta", "betaIndex", 2),
)

#: Metrics that only mean something for a bank.
_BANK_RATIOS = (
    ("Loan/deposit", "loanOnDeposit", 3),
    ("Bad debt %", "badDebtPercentage", 4),
    ("Provision on bad debt", "provisionOnBadDebt", 3),
    ("Credit growth", "creditGrowth", 3),
    ("Non-interest income / TOI", "nonInterestOnToi", 3),
)

#: ...and the ones that only mean something for everyone else.
_NON_BANK_RATIOS = (
    ("Profit margin", "profitMargin", 3),
    ("EV/EBITDA", "valueBeforeEbitda", 2),
    ("Inventory age (days)", "ageOfInventory", 1),
    ("Receivable age (days)", "ageOfReceivable", 1),
    ("Payable/equity", "payableOnEquity", 3),
    ("EBIT/interest", "ebitOnInterest", 2),
)


@lru_cache(maxsize=2048)
def is_bank(symbol: str) -> bool:
    """Whether ``symbol`` reports as a bank.

    Non-bank is the default for an unmapped ticker: it is the far larger
    population, and a wrong guess costs one degraded block rather than a run.
    """
    for tag in sector_tags(str(symbol).upper()):
        if str(tag).strip().lower() in _BANK_TAGS:
            return True
    return False


def fundamentals(symbol: str) -> str | None:
    """Valuation, peers and rating from TCBS, or None when it cannot serve."""
    if not tcbs.enabled():
        return None

    sym = str(symbol).upper()
    ratios = _rows(_try("getStockRatio", ticker=sym))
    overview = _rows(_try("getTickerOverview", ticker=sym))
    if not ratios and not overview:
        return None

    bank = is_bank(sym)
    lines = [f"# {sym} — fundamentals snapshot", ""]

    if overview:
        row = overview[0]
        bits = []
        for label, key in (
            ("Exchange", "exchange"),
            ("Industry", "industry"),
            ("Employees", "noEmployees"),
            ("Shareholders", "noShareholders"),
            ("Established", "establishedYear"),
        ):
            value = _first(row, key)
            if value is not None:
                bits.append(f"{label}: {value}")
        foreign = _first(row, "foreignPercent")
        if foreign is not None:
            bits.append(f"Foreign ownership: {float(foreign) * 100:,.1f}%")
        if bits:
            lines += ["  ·  ".join(bits), ""]

    if ratios:
        row = ratios[0]
        applicable = _COMMON_RATIOS + (_BANK_RATIOS if bank else _NON_BANK_RATIOS)
        lines += ["| Metric | Value |", "|---|---|"]
        for label, key, digits in applicable:
            value = _first(row, key)
            if value is not None:
                lines.append(f"| {label} | {_fmt(value, digits)} |")
        lines.append("")

    peers = _rows(_try("getStockSameIndustry", ticker=sym))
    if peers:
        lines += [
            "## Peers in the same industry",
            "",
            "| Ticker | Company | P/E | P/B | ROE | Beta | Market cap (bn VND) |",
            "|---|---|---|---|---|---|---|",
        ]
        for row in peers[:_PEER_ROWS]:
            lines.append(
                "| {t} | {n} | {pe} | {pb} | {roe} | {beta} | {cap} |".format(
                    t=_first(row, "ticker", default="-"),
                    n=str(_first(row, "companyName", default="-"))[:40],
                    pe=_fmt(_first(row, "pe")),
                    pb=_fmt(_first(row, "pb")),
                    roe=_fmt(_first(row, "roe"), 3),
                    beta=_fmt(_first(row, "beta")),
                    cap=_fmt(_first(row, "marketCap"), 0),
                )
            )
        lines.append("")

    rating = _rows(_try("getGeneralRating", ticker=sym, fType="TICKER"))
    if rating:
        row = rating[0]
        bits = []
        for label, key in (
            ("Overall", "stockRating"),
            ("Valuation", "valuation"),
            ("Financial health", "financialHealth"),
            ("Business model", "businessModel"),
            ("Business operation", "businessOperation"),
            ("RS rating", "rsRating"),
        ):
            value = _first(row, key)
            if value is not None:
                bits.append(f"{label} {_fmt(value, 1)}")
        if bits:
            lines += ["## TCBS rating (out of 5)", "", ", ".join(bits) + ".", ""]

    lines.append(
        f"Amounts in billions of VND. Ratios are on the "
        f"{'bank' if bank else 'non-bank'} reporting basis, so the metric set "
        f"differs by sector. Call get_balance_sheet / get_income_statement / "
        f"get_cashflow for the underlying line items (freq='annual' for yearly). "
        f"Source: TCBS (TCInvest); the rating is TCBS's own model, not a "
        f"recommendation to act on."
    )
    return "\n".join(lines)
