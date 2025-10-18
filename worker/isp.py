import os, asyncio, math, time
from datetime import datetime, timezone
from dataclasses import dataclass, field
import orjson
import numpy as np
from bytewax.dataflow import Dataflow
from bytewax.inputs import DynamicSource, StatelessSourcePartition
import bytewax.operators as op
from bytewax.connectors.stdio import StdOutSink

from dotenv import load_dotenv
load_dotenv()
import os
from mqtt_input import MqttSource
from datetime import time as dtime
from datetime import timedelta
import clickhouse_connect  # type: ignore

EPS = 1e-9

# ISP parameters (can override via env)
BIN_MINUTES = int(os.getenv("ISP_BIN_MINUTES", "15"))
ISP_ALPHA = float(os.getenv("ISP_ALPHA", "0.05"))
# Trading session in local exchange time, format "HH:MM,HH:MM"
ISP_SESSION = os.getenv("ISP_SESSION", "09:00,14:30")

# ClickHouse configuration (optional; used if ISP_CH_TABLE is set)
CLICKHOUSE_HOST = os.getenv("CLICKHOUSE_HOST", "localhost")
CLICKHOUSE_PORT = int(os.getenv("CLICKHOUSE_PORT", "8123"))
CLICKHOUSE_USER = os.getenv("CLICKHOUSE_USER", "myuser")
CLICKHOUSE_PASSWORD = os.getenv("CLICKHOUSE_PASSWORD", "mypassword")
CLICKHOUSE_DB = os.getenv("CLICKHOUSE_DB", "mydb")
ISP_CH_TABLE = os.getenv("ISP_CH_TABLE", "")
ISP_CH_TS_COL = os.getenv("ISP_CH_TS_COL", "ts")
ISP_CH_SYMBOL_COL = os.getenv("ISP_CH_SYMBOL_COL", "symbol")
ISP_CH_VOLUME_COL = os.getenv("ISP_CH_VOLUME_COL", "volume")
ISP_BOOTSTRAP_DAYS = int(os.getenv("ISP_BOOTSTRAP_DAYS", "10"))

def _parse_session(session_str: str):
    try:
        start_str, end_str = [s.strip() for s in session_str.split(",")]
        sh, sm = start_str.split(":")
        eh, em = end_str.split(":")
        return dtime(int(sh), int(sm)), dtime(int(eh), int(em))
    except Exception:
        # Fallback to a sane default
        return dtime(9, 30), dtime(16, 0)

def _num_bins(session_start: dtime, session_end: dtime, bin_minutes: int) -> int:
    base = datetime.now().date()
    start_dt = datetime.combine(base, session_start)
    end_dt = datetime.combine(base, session_end)
    total_minutes = int((end_dt - start_dt).total_seconds() // 60)
    return max(1, total_minutes // bin_minutes)

def _bin_index(ts: datetime, session_start: dtime, session_end: dtime, bin_minutes: int) -> int:
    base_date = ts.date()
    start_dt = datetime.combine(base_date, session_start)
    end_dt = datetime.combine(base_date, session_end)
    if ts < start_dt or ts >= end_dt:
        return -1
    minutes_since_start = int((ts - start_dt).total_seconds() // 60)
    return minutes_since_start // bin_minutes

def _normalize(vec):
    s = sum(vec)
    if s <= 0:
        n = len(vec) if len(vec) > 0 else 1
        return [1.0 / n] * n
    return [v / s for v in vec]

@dataclass
class ISPState:
    symbol: str | None = None
    alpha: float = ISP_ALPHA
    bin_minutes: int = BIN_MINUTES
    session_start: dtime = _parse_session(ISP_SESSION)[0]
    session_end: dtime = _parse_session(ISP_SESSION)[1]
    bin_count: int = 0
    isp: list[float] = field(default_factory=list)
    vol_today: list[float] = field(default_factory=list)
    cum_vol: float = 0.0
    bootstrapped: bool = False

    def ensure_initialized(self, ts: datetime):
        if self.bin_count <= 0:
            self.bin_count = _num_bins(self.session_start, self.session_end, self.bin_minutes)
        if not self.isp or len(self.isp) != self.bin_count:
            self.isp = [1.0 / self.bin_count] * self.bin_count
        if not self.vol_today or len(self.vol_today) != self.bin_count:
            self.vol_today = [0.0] * self.bin_count

    def bootstrap_if_needed(self, ts: datetime):
        if self.bootstrapped:
            return
        if not ISP_CH_TABLE:
            self.bootstrapped = True
            return
        try:
            end_day = ts.date()
            bins = ch_fetch_hist_bins(
                self.symbol,
                end_day,
                self.session_start,
                self.session_end,
                self.bin_minutes,
                self.bin_count,
                ISP_BOOTSTRAP_DAYS,
            )
            if bins is not None:
                total = sum(bins)
                if total > 0:
                    self.isp = _normalize([v for v in bins])
        except Exception:
            pass
        self.bootstrapped = True


BUY = 1
SELL = 2

def parse_tick(msg_payload: bytes):
    # Expecting JSON payload per tick for MVP:
    # {'marketId': 'MARKET_ID_STO', 'boardId': 'BOARD_ID_G1', 'isin': 'VN000000YEG3', 'symbol': 'YEG', 'matchPrice': 13.0, 'matchQtty': 100.0, 'sendingTime': '2025-10-15T07:45:07.049Z', 'boardIdOriginal': 'BOARD_ID_G1', 'tradingSessionId': 'TRADING_SESSION_ID_30', 'totalVolumeTraded': '421570', 'grossTradeAmount': 55.095725, 'side': 'SIDE_SELL'} 
    d = orjson.loads(msg_payload)
    side = BUY if d.get("side") in ("B", "SIDE_BUY", 1) else SELL if d.get("side") in ("S","SIDE_SELL", 2) else 0
    # Normalize ts to epoch ns
    ts = datetime.fromisoformat(d["sendingTime"].replace("Z","+00:00"))
    return {
        "ts": ts,
        "symbol": d["symbol"],
        "price": float(d["matchPrice"]),
        "size": float(d.get("matchQtty", 0.0)),
        "side": side,
        "totalVolumeTraded": d.get("totalVolumeTraded", 0.0),
        "grossTradeAmount": d.get("grossTradeAmount", 0.0),
    }

# ---------- Stateful feature logic ----------
def key_by_symbol(item):
    topic, payload = item
    tick = parse_tick(payload)
    return tick["symbol"], tick


# ---------- Build dataflow ----------
flow = Dataflow("mqtt_ticks_to_clickhouse")

# 1) Ingest from MQTT
# Configuration
MQTT_HOST = "datafeed-lts-krx.dnse.com.vn"
MQTT_PORT = 443
MQTT_TOPICS = ["plaintext/quotes/krx/mdds/tick/v1/roundlot/symbol/VCG"]
stream = op.input("mqtt", flow, MqttSource(MQTT_HOST, MQTT_PORT, MQTT_TOPICS))

# 2) Parse, key by symbol
keyed = op.map("key_by_symbol", stream, key_by_symbol)

def isp_mapper(state: ISPState | None, tick: dict):
    if state is None:
        state = ISPState()
        state.symbol = tick.get("symbol")
    ts: datetime = tick["ts"]
    state.ensure_initialized(ts)
    state.bootstrap_if_needed(ts)

    day = ts.date()

    bin_idx = _bin_index(ts, state.session_start, state.session_end, state.bin_minutes)
    out = {
        "symbol": state.symbol,
        "ts": ts.isoformat(),
        "date": day.isoformat(),
        "bin_minutes": state.bin_minutes,
        "bin_index": bin_idx,
        "volume_observed": float(tick.get("size", 0.0)),
        "expected_volume": 0.0,
        "abnormality_ratio": 0.0,
        "cum_volume_so_far": float(state.cum_vol),
        "S_k": 0.0,
        "Vhat_day": 0.0,
    }

    if bin_idx < 0 or bin_idx >= state.bin_count:
        return (state, out)

    # Update day accumulators
    vol = float(tick.get("size", 0.0))
    state.vol_today[bin_idx] += vol
    state.cum_vol += vol

    S_k = sum(state.isp[: bin_idx + 1])
    Vhat_day = state.cum_vol / max(S_k, EPS)
    expected_bin = state.isp[bin_idx] * Vhat_day
    abnormality = vol / max(expected_bin, EPS)

    out.update({
        "expected_volume": float(expected_bin),
        "abnormality_ratio": float(abnormality),
        "cum_volume_so_far": float(state.cum_vol),
        "S_k": float(S_k),
        "Vhat_day": float(Vhat_day),
    })

    return (state, out)

isp_metrics = op.stateful_map("isp_update", keyed, isp_mapper)

op.output("isp_out", isp_metrics, StdOutSink())


def ch_fetch_prev_day_bins(symbol: str | None, day, session_start: dtime, session_end: dtime, bin_minutes: int, bin_count: int) -> list[float] | None:
    if not symbol:
        return None
    try:
        client = _get_ch_client()
        start_dt = datetime.combine(day, session_start)
        end_dt = datetime.combine(day, session_end)
        start_str = start_dt.strftime("%Y-%m-%d %H:%M:%S")
        end_str = end_dt.strftime("%Y-%m-%d %H:%M:%S")
        sess_start_min = session_start.hour * 60 + session_start.minute
        sess_end_min = session_end.hour * 60 + session_end.minute

        # Aggregate volumes into session bins via SQL; return zero-filled vector of length bin_count
        sql = f"""
            WITH
              toDateTime('{start_str}') AS start_dt,
              toDateTime('{end_str}')   AS end_dt,
              {sess_start_min} AS sess_start_min,
              {sess_end_min}   AS sess_end_min
            SELECT bin_idx, vol
            FROM (
              SELECT
                intDiv(
                  (toRelativeMinuteNum({ISP_CH_TS_COL}) - toRelativeMinuteNum(toStartOfDay({ISP_CH_TS_COL}))) - sess_start_min,
                  {bin_minutes}
                ) AS bin_idx,
                sum({ISP_CH_VOLUME_COL}) AS vol
              FROM {CLICKHOUSE_DB}.{ISP_CH_TABLE}
              WHERE {ISP_CH_TS_COL} >= start_dt
                AND {ISP_CH_TS_COL} <  end_dt
                AND {ISP_CH_SYMBOL_COL} = '{symbol.replace("'", "''")}'
                AND (toRelativeMinuteNum({ISP_CH_TS_COL}) - toRelativeMinuteNum(toStartOfDay({ISP_CH_TS_COL}))) >= sess_start_min
                AND (toRelativeMinuteNum({ISP_CH_TS_COL}) - toRelativeMinuteNum(toStartOfDay({ISP_CH_TS_COL}))) <  sess_end_min
              GROUP BY bin_idx
            )
            ORDER BY bin_idx
        """
        result = client.query(sql)
        rows = result.result_rows

        # Build zero-filled bins
        bins = [0.0] * bin_count
        for r in rows:
            idx, vol = r[0], r[1]
            if 0 <= idx < bin_count:
                bins[idx] = float(vol)
        return bins
    except Exception:
        return None


def ch_fetch_hist_bins(symbol: str | None, end_day, session_start: dtime, session_end: dtime, bin_minutes: int, bin_count: int, lookback_days: int) -> list[float] | None:
    if not symbol:
        return None
    try:
        # Aggregate over [end_day - lookback_days, end_day] per bin across all days
        start_day_dt = datetime.combine(end_day, dtime(0, 0)) - timedelta(days=lookback_days)
        end_day_dt = datetime.combine(end_day, dtime(23, 59, 59))
        client = _get_ch_client()
        start_str = start_day_dt.strftime('%Y-%m-%d %H:%M:%S')
        end_str = end_day_dt.strftime('%Y-%m-%d %H:%M:%S')
        sess_start_min = session_start.hour * 60 + session_start.minute
        sess_end_min = session_end.hour * 60 + session_end.minute
        sql = f"""
            WITH
              toDateTime('{start_str}') AS start_dt,
              toDateTime('{end_str}')   AS end_dt,
              {sess_start_min} AS sess_start_min,
              {sess_end_min}   AS sess_end_min
            SELECT bin_idx, vol
            FROM (
              SELECT
                intDiv(
                  (toRelativeMinuteNum({ISP_CH_TS_COL}) - toRelativeMinuteNum(toStartOfDay({ISP_CH_TS_COL}))) - sess_start_min,
                  {bin_minutes}
                ) AS bin_idx,
                sum({ISP_CH_VOLUME_COL}) AS vol
              FROM {CLICKHOUSE_DB}.{ISP_CH_TABLE}
              WHERE {ISP_CH_TS_COL} >= start_dt
                AND {ISP_CH_TS_COL} <= end_dt
                AND {ISP_CH_SYMBOL_COL} = '{symbol.replace("'", "''")}'
                AND (toRelativeMinuteNum({ISP_CH_TS_COL}) - toRelativeMinuteNum(toStartOfDay({ISP_CH_TS_COL}))) >= sess_start_min
                AND (toRelativeMinuteNum({ISP_CH_TS_COL}) - toRelativeMinuteNum(toStartOfDay({ISP_CH_TS_COL}))) <  sess_end_min
              GROUP BY bin_idx
            )
            ORDER BY bin_idx
        """
        result = client.query(sql)
        rows = result.result_rows

        bins = [0.0] * bin_count
        for r in rows:
            idx, vol = r[0], r[1]
            if 0 <= idx < bin_count:
                bins[idx] = float(vol)
        return bins
    except Exception:
        return None


_CH_CLIENT = None

def _get_ch_client():
    global _CH_CLIENT
    try:
        if _CH_CLIENT is None:
            _CH_CLIENT = clickhouse_connect.get_client(
                host=CLICKHOUSE_HOST,
                port=CLICKHOUSE_PORT,
                username=CLICKHOUSE_USER,
                password=CLICKHOUSE_PASSWORD,
                database=CLICKHOUSE_DB,
            )
        return _CH_CLIENT
    except Exception:
        return None




if __name__ == "__main__":
    from bytewax.execution import run_main
    run_main(flow)