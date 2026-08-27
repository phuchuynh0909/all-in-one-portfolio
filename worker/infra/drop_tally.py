"""Accounting for rows a streaming filter throws away.

A dataflow filter has two bad options by default. Drop silently, and "the ticks
are missing from ClickHouse" is indistinguishable from "the feed never sent
them" — the gap between the source's arrival counter and the sink's insert
counter is real but unattributed. Log every drop, and the log is unusable: the
classes being dropped here are *ordinary* traffic (a symbol belonging to a
board-mate we never subscribed to, a put-through print), several thousand a
second at the open.

This takes the middle: the first sighting of each distinct thing dropped is
named once — that answers *which* — and the running totals are reported every
``summary_every`` drops, which answers *how many*. A pipeline that drops
nothing logs nothing.

Not thread-safe on purpose: it is called from the dataflow thread that owns the
filter, and a lock on a path that runs per tick would cost more than the
counters are worth. Bytewax runs one of these per worker process, so with
several workers the totals are per worker.
"""

from __future__ import annotations

import logging
from typing import Dict, Optional, Tuple

# Longest a single detail may appear as. Reasons and symbols are short; an
# unparseable payload is the one detail with no bound on its size.
_DETAIL_CHARS = 80


class DropTally:
    """Counts dropped rows by reason, and names each distinct one once."""

    def __init__(
        self,
        log: Optional[logging.Logger] = None,
        name: str = "",
        summary_every: int = 1000,
        max_details: int = 500,
    ):
        """
        :arg name: pipeline name, prefixed onto every line so drops are
            greppable and attributable when two flows share a log.

        :arg summary_every: emit the running totals every this many drops.

        :arg max_details: stop naming new details past this many distinct ones.
            A feed sending garbage symbols would otherwise grow the seen-set and
            the log without limit; counting continues either way.
        """
        self._log = log or logging.getLogger(__name__)
        self._prefix = f"{name}: " if name else ""
        self._summary_every = summary_every
        self._max_details = max_details
        self._totals: Dict[str, int] = {}
        self._seen: set[Tuple[str, str]] = set()
        self._total = 0
        self._capped = False

    @property
    def totals(self) -> Dict[str, int]:
        """Drops per reason so far. Reasons are whatever callers passed."""
        return dict(self._totals)

    def note(self, reason: str, detail: object = "") -> None:
        """Record one dropped row, logging it only if it is news."""
        self._totals[reason] = self._totals.get(reason, 0) + 1
        self._total += 1

        key = (reason, str(detail))
        if key not in self._seen:
            if len(self._seen) < self._max_details:
                self._seen.add(key)
                self._log.info(
                    "%sdropped a row: %s=%s (first of this kind)",
                    self._prefix,
                    reason,
                    _clip(detail),
                )
            elif not self._capped:
                # Said once, so a garbage feed costs one line rather than one
                # per distinct value. The totals below still tell the story.
                self._capped = True
                self._log.info(
                    "%s%d distinct kinds of drop seen; no longer naming new ones",
                    self._prefix,
                    self._max_details,
                )

        if self._total % self._summary_every == 0:
            self._log.info(
                "%sdropped %d rows so far: %s",
                self._prefix,
                self._total,
                ", ".join(
                    f"{name}={count}"
                    for name, count in sorted(
                        self._totals.items(), key=lambda kv: -kv[1]
                    )
                ),
            )


def _clip(detail: object) -> str:
    """``repr`` of a detail, truncated — a payload can be any size at all."""
    text = repr(detail)
    return text if len(text) <= _DETAIL_CHARS else text[:_DETAIL_CHARS] + "…"
