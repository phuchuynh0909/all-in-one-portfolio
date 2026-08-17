"""Small formatting/date helpers shared by the TradingAgents integration.

These are the pieces of ``vn_data`` that carry no market knowledge of their own:
they turn whatever the upstream APIs hand us (ISO timestamps as strings, numbers
that may be ``None`` or unparseable text) into the strings the analyst prompts
read. Kept apart from ``vn_data`` so the vendor module stays about *where the
data comes from* rather than how it is rendered.

Every formatter is total: it returns a placeholder rather than raising, because
these run inside tool bodies whose output goes straight into a prompt — a
``TypeError`` on one missing field would cost the analyst the whole section.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

# Statement line items and market caps arrive in dong; the tables show billions.
BILLION = 1e9


def iso_day(value: Any) -> str:
    """The ``YYYY-MM-DD`` part of an ISO timestamp, or "" when unparseable.

    Deliberately a prefix check rather than a parse: the feeds mix
    ``2026-08-17``, ``2026-08-17T09:30:00`` and ``None`` in the same field, and
    the day is all any caller wants. The ``text[4] == "-"`` guard is what keeps a
    non-date string (an id, a slug) from yielding a plausible-looking day.
    """
    text = str(value or "")
    return text[:10] if len(text) >= 10 and text[4] == "-" else ""


def lookback_days(start_date: str, end_date: str) -> int:
    """Width of a ``YYYY-MM-DD`` window in days, defaulting to a week.

    Used to translate a date range into the "last N days" recency filter web
    search takes. Never raises: an unparseable range degrades to 7 days rather
    than sinking the tool it is called from.
    """
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
        return max(1, (end - start).days)
    except Exception:  # noqa: BLE001
        return 7


def fmt_billion(value: Any) -> str:
    """A dong amount in billions with one decimal; "-" when absent."""
    if value is None:
        return "-"
    try:
        return f"{float(value) / BILLION:,.1f}"
    except (TypeError, ValueError):
        return str(value)


def fmt_ratio(value: Any, digits: int) -> str:
    """Format a metric, treating an exact zero as "not reported".

    The 24hmoney endpoint uses ``0.0`` rather than ``null`` for metrics that do
    not apply to a sector (banks have no EV/EBITDA), and none of the ratios we
    render can legitimately be exactly zero.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    if number == 0.0:
        return "-"
    return f"{number:,.{digits}f}"


def fmt_count(value: Any) -> str:
    """A share/peer count as a thousands-separated integer; "-" when absent."""
    try:
        return f"{int(float(value)):,}"
    except (TypeError, ValueError):
        return "-"
