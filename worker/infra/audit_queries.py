from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ReconcilerMetrics:
    run_date: str
    fetched_rows: int = 0
    mismatches_missing: int = 0
    mismatches_drift: int = 0
    patched_rows: int = 0
    skipped_rows: int = 0
    failed_rows: int = 0
    duration_s: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Without FINAL: cheaper but may report rows awaiting background merge
DUPLICATE_AUDIT_SQL = """
SELECT
    symbol,
    sending_time,
    match_price,
    match_qty,
    side,
    count()       AS version_count,
    max(received_at) AS latest_received
FROM {database}.{table}
WHERE toYYYYMMDD(sending_time) = toYYYYMMDD(toDate('{date}'))
GROUP BY symbol, sending_time, match_price, match_qty, side
HAVING count() > 1
ORDER BY version_count DESC
LIMIT 1000
""".strip()

# With FINAL: expensive but accurate — use only for explicit exact audit checks
DUPLICATE_AUDIT_FINAL_SQL = """
SELECT
    symbol,
    sending_time,
    match_price,
    match_qty,
    side,
    count()       AS version_count,
    max(received_at) AS latest_received
FROM {database}.{table} FINAL
WHERE toYYYYMMDD(sending_time) = toYYYYMMDD(toDate('{date}'))
GROUP BY symbol, sending_time, match_price, match_qty, side
HAVING count() > 1
ORDER BY version_count DESC
LIMIT 1000
""".strip()

MERGE_HEALTH_SQL = """
SELECT
    partition,
    count() AS parts
FROM system.parts
WHERE active
  AND database = '{database}'
  AND table    = '{table}'
GROUP BY partition
HAVING count() > 10
ORDER BY parts DESC
""".strip()

# 09:00-15:00 Asia/Ho_Chi_Minh == 02:00-08:00 UTC
SESSION_COUNT_SQL = """
SELECT count() AS tick_count
FROM {database}.{table}
WHERE symbol = '{symbol}'
  AND sending_time >= toDateTime64('{date} 02:00:00', 6, 'UTC')
  AND sending_time <= toDateTime64('{date} 08:00:00', 6, 'UTC')
""".strip()


def run_duplicate_audit(
    client: Any,
    database: str,
    table: str,
    date_str: str,
) -> dict[str, Any]:
    sql = DUPLICATE_AUDIT_SQL.format(database=database, table=table, date=date_str)
    result = client.query(sql)
    rows = result.result_rows

    duplicate_key_groups = len(rows)
    max_version_count = max((r[5] for r in rows), default=0)
    sample_keys = [
        {
            "symbol": r[0],
            "sending_time": str(r[1]),
            "match_price": r[2],
            "match_qty": r[3],
            "side": r[4],
            "version_count": r[5],
        }
        for r in rows[:5]
    ]

    if duplicate_key_groups > 100:
        logger.error(
            "Duplicate audit CRITICAL: %d duplicate key groups on %s",
            duplicate_key_groups,
            date_str,
        )
    elif duplicate_key_groups > 0:
        logger.warning(
            "Duplicate audit: %d duplicate key groups on %s",
            duplicate_key_groups,
            date_str,
        )
    else:
        logger.info("Duplicate audit clean for %s", date_str)

    return {
        "duplicate_key_groups": duplicate_key_groups,
        "max_version_count": max_version_count,
        "sample_keys": sample_keys,
    }


def run_merge_health(
    client: Any,
    database: str,
    table: str,
) -> dict[str, Any]:
    sql = MERGE_HEALTH_SQL.format(database=database, table=table)
    result = client.query(sql)
    rows = result.result_rows

    hot_partitions = [{"partition": r[0], "parts": r[1]} for r in rows]
    total_active_parts = sum(r[1] for r in rows)

    if hot_partitions:
        logger.warning(
            "Merge health: %d hot partitions (>10 parts), total active parts in hot: %d",
            len(hot_partitions),
            total_active_parts,
        )
    else:
        logger.info("Merge health OK — no partitions with >10 active parts")

    return {
        "hot_partitions": hot_partitions,
        "total_active_parts": total_active_parts,
    }


def print_metrics(metrics: ReconcilerMetrics) -> None:
    logger.info(
        "Reconciler run complete — date=%s  duration=%.2fs",
        metrics.run_date,
        metrics.duration_s,
    )
    print(
        f"\n{'=' * 50}\n"
        f"  Reconciler Report  —  {metrics.run_date}\n"
        f"{'=' * 50}\n"
        f"  Fetched rows      : {metrics.fetched_rows:>10,}\n"
        f"  Missing mismatches : {metrics.mismatches_missing:>10,}\n"
        f"  Drift mismatches   : {metrics.mismatches_drift:>10,}\n"
        f"  Patched rows       : {metrics.patched_rows:>10,}\n"
        f"  Skipped rows       : {metrics.skipped_rows:>10,}\n"
        f"  Failed rows        : {metrics.failed_rows:>10,}\n"
        f"  Duration (s)       : {metrics.duration_s:>10.2f}\n"
        f"{'=' * 50}"
    )
