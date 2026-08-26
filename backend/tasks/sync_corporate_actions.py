"""Daily capture of DNSE corporate actions for held tickers.

Run from ``backend/``::

    python -m tasks.sync_corporate_actions

Capture only — nothing is applied. New events land as ``pending`` (or
``unparsed``) for review at
``GET /api/v1/portfolio/corporate-actions``.

Daily is ample: ex-dates are announced well ahead, and because the DNSE history
endpoint returns a complete series, a missed run self-heals on the next one.
"""
from __future__ import annotations

import logging

from app.db.base import SessionLocal
from app.services.corporate_action_service import sync_all

logger = logging.getLogger(__name__)


def main() -> dict:
    db = SessionLocal()
    try:
        counts = sync_all(db)
        logger.info(
            "corporate action sync: %s inserted, %s already known, "
            "%s unparsed, %s not price-affecting",
            counts["inserted"], counts["skipped"],
            counts["unparsed"], counts["ignored"],
        )
        return counts
    finally:
        db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(main())
