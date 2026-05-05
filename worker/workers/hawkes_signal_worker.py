"""
Live Hawkes-BSI signal worker.

Pipeline (every poll_interval seconds):
  1. Refresh ohlc_5m for current session by re-aggregating ticks from ClickHouse.
  2. Load full ohlc_5m bar history for the configured symbol.
  3. Compute Hawkes BSI + rolling quantile bands + KAMA gate.
  4. Run the signal state machine on the full bar series.
  5. If the latest bar carries a new entry (or exit, if HAWKES_ALERT_EXITS=1)
     signal not yet alerted → send Telegram notification.

Run:
  python hawkes_signal_worker.py
  python hawkes_signal_worker.py --symbol VN30F1M --poll 60

Environment variables (all optional, see config.HawkesConfig):
  HAWKES_SYMBOL, HAWKES_POLL_INTERVAL, HAWKES_KAPPA,
  HAWKES_QUANTILE_LOOKBACK, HAWKES_Q_LO_PCT, HAWKES_Q_HI_PCT,
  HAWKES_KAMA_PERIOD, HAWKES_ALLOW_SHORT, HAWKES_SL_BARS,
  HAWKES_CALM_BARS, HAWKES_CALM_THRESHOLD, HAWKES_STATE_PATH,
  HAWKES_ALERT_EXITS
  CLICKHOUSE_HOST, CLICKHOUSE_PORT (8123), CLICKHOUSE_USER,
  CLICKHOUSE_PASSWORD, CLICKHOUSE_DATABASE
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_ENABLED
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, date, timezone

import numpy as np
import pandas as pd

from core.hawkes_indicators import compute_hawkes_bsi, compute_kama, generate_signals
from analysis.backtest_hawkes_quant import load_ohlc_from_clickhouse
from config import config
from infra.telegram_notifier import send_telegram_message

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

_cfg = config.hawkes
_ch = config.clickhouse

# ClickHouse HTTP client kwargs (port 8123 default for clickhouse_connect)
_CH_KWARGS = dict(
    host=_ch.host,
    port=int(os.getenv("CLICKHOUSE_PORT", "8123")),
    username=_ch.user,
    password=_ch.password,
    database=_ch.database,
)

VN30F1M_OUTPUT_SYMBOL = "VN30F1M"
OHLC_COL_NAMES = ["symbol", "ts", "open", "high", "low", "close", "volume", "buy_volume", "sell_volume"]


# ---------------------------------------------------------------------------
# ohlc_5m incremental refresh
# ---------------------------------------------------------------------------

def _ensure_ohlc_table(client) -> None:
    db = _ch.database
    client.command(f"CREATE DATABASE IF NOT EXISTS {db}")
    client.command(
        f"""
        CREATE TABLE IF NOT EXISTS {db}.{_cfg.ohlc_table} (
            symbol String,
            ts DateTime('Asia/Ho_Chi_Minh'),
            open Float64,
            high Float64,
            low Float64,
            close Float64,
            volume Int64,
            buy_volume Int64,
            sell_volume Int64,
            ver DateTime64(3) DEFAULT now64(3)
        )
        ENGINE = ReplacingMergeTree(ver)
        PARTITION BY toYYYYMM(ts)
        ORDER BY (symbol, ts)
        """
    )


def _refresh_ohlc_session(session_date: str) -> int:
    """Re-aggregate ticks for *session_date* into ohlc_5m (upsert via ReplacingMergeTree)."""
    import clickhouse_connect

    date_from = f"{session_date} 02:00:00"
    date_to = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    db = _ch.database

    client = clickhouse_connect.get_client(**_CH_KWARGS)
    _ensure_ohlc_table(client)

    sql = f"""
        SELECT
            '{VN30F1M_OUTPUT_SYMBOL}',
            toStartOfFiveMinutes(toTimezone(sending_time, 'Asia/Ho_Chi_Minh')) AS ts,
            argMin(match_price, sending_time) AS open,
            max(match_price)                  AS high,
            min(match_price)                  AS low,
            argMax(match_price, sending_time) AS close,
            sum(match_qty)                    AS volume,
            sumIf(match_qty, side = 1)        AS buy_volume,
            sumIf(match_qty, side = 2)        AS sell_volume
        FROM {db}.{_cfg.ticks_table} FINAL
        WHERE sending_time >= toDateTime64('{date_from}', 6, 'UTC')
          AND sending_time <= toDateTime64('{date_to}', 6, 'UTC')
          AND (symbol LIKE '41I1%' OR symbol LIKE 'VN30%')
        GROUP BY ts
        ORDER BY ts
    """

    raw_rows = client.query(sql).result_rows
    if not raw_rows:
        log.debug("No ticks found for session %s", session_date)
        return 0

    rows = [
        (
            VN30F1M_OUTPUT_SYMBOL,
            r[1] if isinstance(r[1], datetime) else datetime.fromisoformat(str(r[1])),
            float(r[2]), float(r[3]), float(r[4]), float(r[5]),
            int(r[6]), int(r[7]), int(r[8]),
        )
        for r in raw_rows
    ]

    # Insert in batches of 50k (idempotent via ReplacingMergeTree)
    batch_size = 50_000
    inserted = 0
    for start in range(0, len(rows), batch_size):
        batch = rows[start: start + batch_size]
        client.insert(f"{db}.{_cfg.ohlc_table}", batch, column_names=OHLC_COL_NAMES)
        inserted += len(batch)

    log.info("Refreshed %d ohlc_5m rows for session %s", inserted, session_date)
    return inserted


# ---------------------------------------------------------------------------
# Signal state persistence
# ---------------------------------------------------------------------------

def _load_state() -> dict:
    path = _cfg.state_path
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        log.warning("Could not load signal state from %s: %s", path, e)
        return {}


def _save_state(state: dict) -> None:
    path = _cfg.state_path
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w") as f:
        json.dump(state, f)


# ---------------------------------------------------------------------------
# Telegram message formatting
# ---------------------------------------------------------------------------

def _fmt(val: float, decimals: int = 1) -> str:
    fmt = f"{{:,.{decimals}f}}"
    return fmt.format(val)


def _format_entry_message(direction: str, symbol: str, bar: pd.Series,
                           bsi: float, q_lo: float, q_hi: float) -> str:
    ts_str = pd.Timestamp(bar["stamp"]).strftime("%Y-%m-%d %H:%M")
    kama_val = bar.get("kama", np.nan)
    kama_str = _fmt(float(kama_val)) if not np.isnan(float(kama_val)) else "N/A"

    if direction == "LONG":
        emoji = "\U0001f7e2"  # green circle
        threshold_info = f"q\\_hi: {_fmt(q_hi, 0)}"
    else:
        emoji = "\U0001f534"  # red circle
        threshold_info = f"q\\_lo: {_fmt(q_lo, 0)}"

    vol = int(bar.get("volume", 0))
    buy_vol = int(bar.get("buyvolume", 0))
    sell_vol = int(bar.get("sellvolume", 0))

    return (
        f"{emoji} *{direction} ENTRY* — `{symbol}`\n\n"
        f"*Time:* {ts_str}\n"
        f"*Close:* {_fmt(bar['close'])}\n"
        f"*BSI:* {_fmt(bsi, 0)}  ({threshold_info})\n"
        f"*KAMA:* {kama_str}\n"
        f"*Volume:* {vol:,}  (B: {buy_vol:,} / S: {sell_vol:,})"
    )


def _format_exit_message(direction: str, symbol: str, bar: pd.Series) -> str:
    ts_str = pd.Timestamp(bar["stamp"]).strftime("%Y-%m-%d %H:%M")
    emoji = "\U000026aa"  # grey circle
    return (
        f"{emoji} *{direction} EXIT* — `{symbol}`\n\n"
        f"*Time:* {ts_str}\n"
        f"*Close:* {_fmt(bar['close'])}"
    )


def _send_sync(msg: str) -> None:
    try:
        asyncio.run(send_telegram_message(msg))
    except Exception as e:
        log.error("Telegram send failed: %s", e)


# ---------------------------------------------------------------------------
# One poll cycle
# ---------------------------------------------------------------------------

def _run_once(symbol: str, state: dict) -> dict:
    session_date = date.today().isoformat()

    # 1. Refresh ohlc_5m
    try:
        _refresh_ohlc_session(session_date)
    except Exception as e:
        log.warning("OHLC refresh failed (continuing with stale data): %s", e)

    # 2. Load bars
    try:
        bars = load_ohlc_from_clickhouse(symbol, table=_cfg.ohlc_table, **_CH_KWARGS)
    except Exception as e:
        log.warning("Failed to load OHLC bars: %s", e)
        return state

    n = len(bars)
    min_bars = _cfg.quantile_lookback + _cfg.kama_period + 6  # +1 for forming candle
    if n < min_bars:
        log.info("Not enough bars yet (%d / %d needed)", n, min_bars)
        return state

    # 3. Compute indicators
    bars = compute_hawkes_bsi(
        bars,
        kappa=_cfg.kappa,
        quantile_lookback=_cfg.quantile_lookback,
        q_lo_pct=_cfg.q_lo_pct,
        q_hi_pct=_cfg.q_hi_pct,
    )
    bars = compute_kama(bars, period=_cfg.kama_period)

    # 4. Generate signals
    long_e, short_e, long_x, short_x = generate_signals(
        bars,
        allow_short=_cfg.allow_short,
        sl_bars=_cfg.sl_bars,
        calm_bars=_cfg.calm_bars,
        calm_threshold=_cfg.calm_threshold,
    )

    # 5. Check the last *closed* bar (n-2); n-1 is the still-forming candle
    if n < 2:
        return state
    idx = n - 2
    bar = bars.iloc[idx]
    bar_ts = str(pd.Timestamp(bar["stamp"]))
    bsi_val = float(bars["bsi"].iloc[idx])
    q_lo_val = float(bars["q_lo"].iloc[idx])
    q_hi_val = float(bars["q_hi"].iloc[idx])

    def _already_sent(key: str) -> bool:
        return state.get(f"{symbol}_{key}") == bar_ts

    def _mark_sent(key: str) -> None:
        state[f"{symbol}_{key}"] = bar_ts
        _save_state(state)

    if long_e[idx] and not _already_sent("last_long_entry"):
        msg = _format_entry_message("LONG", symbol, bar, bsi_val, q_lo_val, q_hi_val)
        _send_sync(msg)
        log.info("LONG entry signal at %s close=%.1f", bar_ts, bar["close"])
        _mark_sent("last_long_entry")

    if short_e[idx] and not _already_sent("last_short_entry"):
        msg = _format_entry_message("SHORT", symbol, bar, bsi_val, q_lo_val, q_hi_val)
        _send_sync(msg)
        log.info("SHORT entry signal at %s close=%.1f", bar_ts, bar["close"])
        _mark_sent("last_short_entry")

    if _cfg.alert_exits:
        if long_x[idx] and not _already_sent("last_long_exit"):
            msg = _format_exit_message("LONG", symbol, bar)
            _send_sync(msg)
            log.info("LONG exit signal at %s", bar_ts)
            _mark_sent("last_long_exit")

        if short_x[idx] and not _already_sent("last_short_exit"):
            msg = _format_exit_message("SHORT", symbol, bar)
            _send_sync(msg)
            log.info("SHORT exit signal at %s", bar_ts)
            _mark_sent("last_short_exit")

    return state


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Hawkes BSI live signal worker")
    parser.add_argument("--symbol", default=_cfg.symbol, help="OHLC symbol to monitor")
    parser.add_argument("--poll", type=int, default=_cfg.poll_interval,
                        metavar="SECONDS", help="Poll interval (default: %(default)s)")
    args = parser.parse_args()

    log.info(
        "Starting hawkes_signal_worker: symbol=%s poll=%ds kappa=%.3f lookback=%d",
        args.symbol, args.poll, _cfg.kappa, _cfg.quantile_lookback,
    )

    state = _load_state()
    while True:
        try:
            state = _run_once(args.symbol, state)
        except Exception as e:
            log.error("Unhandled error in poll cycle: %s", e, exc_info=True)
        time.sleep(args.poll)


if __name__ == "__main__":
    main()
