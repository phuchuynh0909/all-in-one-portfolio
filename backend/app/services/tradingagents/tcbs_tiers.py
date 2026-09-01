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
