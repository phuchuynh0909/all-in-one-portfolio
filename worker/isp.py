import os, asyncio, math, time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
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
ISP_WINDOWS = [int(x) for x in os.getenv("ISP_WINDOWS", "5,15,30,60").split(",") if x.strip()]
if BIN_MINUTES not in ISP_WINDOWS:
    ISP_WINDOWS.append(BIN_MINUTES)
ISP_ALPHA = float(os.getenv("ISP_ALPHA", "0.05"))
# Trading session in local exchange time, format "HH:MM,HH:MM"
ISP_SESSION = os.getenv("ISP_SESSION", "09:00,14:45")
EXCHANGE_TZ = os.getenv("EXCHANGE_TZ", "Asia/Ho_Chi_Minh")

CLICKHOUSE_HOST = os.getenv("CLICKHOUSE_HOST", "localhost")
CLICKHOUSE_PORT = int(os.getenv("CLICKHOUSE_PORT", "9010"))
CLICKHOUSE_USER = os.getenv("CLICKHOUSE_USER", "myuser")
CLICKHOUSE_PASSWORD = os.getenv("CLICKHOUSE_PASSWORD", "mypassword")
CLICKHOUSE_DB = os.getenv("CLICKHOUSE_DB", "default")
ISP_BOOTSTRAP_DAYS = int(os.getenv("ISP_BOOTSTRAP_DAYS", "3"))

def _parse_session(session_str: str):
    try:
        start_str, end_str = [s.strip() for s in session_str.split(",")]
        sh, sm = start_str.split(":")
        eh, em = end_str.split(":")
        return dtime(int(sh), int(sm)), dtime(int(eh), int(em))
    except Exception:
        # Fallback to a sane default
        return dtime(9, 00), dtime(15, 00)

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

    if ts.tzinfo is not None and ts.tzinfo.utcoffset(ts) is not None:
        start_dt = start_dt.replace(tzinfo=ts.tzinfo)
        end_dt = end_dt.replace(tzinfo=ts.tzinfo)

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
class WindowState:
    bin_minutes: int
    bin_count: int = 0
    isp: list[float] = field(default_factory=list)
    vol_today: list[float] = field(default_factory=list)
    cum_vol: float = 0.0


@dataclass
class ISPState:
    symbol: str | None = None
    alpha: float = ISP_ALPHA
    session_start: dtime = _parse_session(ISP_SESSION)[0]
    session_end: dtime = _parse_session(ISP_SESSION)[1]
    windows: dict[int, WindowState] = field(default_factory=dict)
    bootstrapped: bool = False

    def ensure_initialized(self, ts: datetime):
        # Initialize per-window state structures
        for w in ISP_WINDOWS:
            if w not in self.windows:
                self.windows[w] = WindowState(bin_minutes=w)
            ws = self.windows[w]
            if ws.bin_count <= 0:
                ws.bin_count = _num_bins(self.session_start, self.session_end, ws.bin_minutes)
            if not ws.isp or len(ws.isp) != ws.bin_count:
                ws.isp = [1.0 / ws.bin_count] * ws.bin_count
            if not ws.vol_today or len(ws.vol_today) != ws.bin_count:
                ws.vol_today = [0.0] * ws.bin_count

    def bootstrap_if_needed(self, ts: datetime, symbol: str):
        if self.bootstrapped:
            return
        try:
            end_day = ts.date()
            # Bootstrap each window independently using historical bins
            for w in ISP_WINDOWS:
                ws = self.windows.get(w) or WindowState(bin_minutes=w)
                if ws.bin_count <= 0:
                    ws.bin_count = _num_bins(self.session_start, self.session_end, ws.bin_minutes)
                bins = ch_fetch_hist_bins(
                    symbol,
                    end_day,
                    self.session_start,
                    self.session_end,
                    ws.bin_minutes,
                    ws.bin_count,
                    ISP_BOOTSTRAP_DAYS,
                )
                if bins is not None and sum(bins) > 0:
                    ws.isp = _normalize([v for v in bins])
                self.windows[w] = ws
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
    ## subtract 5 minutes from ts
    ts = ts - timedelta(minutes=25)
    # Convert to exchange local timezone for session binning consistency
    try:
        ts = ts.astimezone(ZoneInfo(EXCHANGE_TZ))
    except Exception:
        pass
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
MQTT_TOPICS = ["plaintext/quotes/krx/mdds/tick/v1/roundlot/symbol/VCG",
"plaintext/quotes/krx/mdds/tick/v1/roundlot/symbol/YEG"]
stream = op.input("mqtt", flow, MqttSource(MQTT_HOST, MQTT_PORT, MQTT_TOPICS))

# 2) Parse, key by symbol
keyed = op.map("key_by_symbol", stream, key_by_symbol)

def isp_mapper(state: ISPState | None, tick: dict):
    if state is None:
        state = ISPState()
        state.symbol = tick.get("symbol")
    ts: datetime = tick["ts"]
    state.ensure_initialized(ts)
    state.bootstrap_if_needed(ts, state.symbol)
    day = ts.date()

    # Build per-window metrics
    by_window = {}
    observed_vol = float(tick.get("size", 0.0))
    for w, ws in state.windows.items():
        bin_idx_w = _bin_index(ts, state.session_start, state.session_end, ws.bin_minutes)
        if bin_idx_w < 0 or bin_idx_w >= ws.bin_count:
            by_window[w] = {
                "bin_index": bin_idx_w,
            }
            continue
        ws.vol_today[bin_idx_w] += observed_vol
        ws.cum_vol += observed_vol
        S_k_w = sum(ws.isp[: bin_idx_w + 1])
        Vhat_day_w = ws.cum_vol / max(S_k_w, EPS)
        expected_bin_w = ws.isp[bin_idx_w] * Vhat_day_w
        abnormality_w = observed_vol / max(expected_bin_w, EPS)
        by_window[w] = {
            "bin_index": int(bin_idx_w),
            "expected_volume": float(expected_bin_w),
            "abnormality_ratio": float(abnormality_w),
            "cum_volume_so_far": float(ws.cum_vol),
            "S_k": float(S_k_w),
            "Vhat_day": float(Vhat_day_w),
        }
        state.windows[w] = ws

    # Preserve top-level fields for the default window (BIN_MINUTES)
    ws_def = state.windows[BIN_MINUTES]
    bin_idx_def = by_window.get(BIN_MINUTES, {}).get("bin_index", -1)
    out = {
        "symbol": state.symbol,
        "ts": ts.isoformat(),
        "date": day.isoformat(),
        "bin_minutes": BIN_MINUTES,
        "bin_index": bin_idx_def,
        "volume_observed": observed_vol,
        "expected_volume": float(by_window.get(BIN_MINUTES, {}).get("expected_volume", 0.0)),
        "abnormality_ratio": float(by_window.get(BIN_MINUTES, {}).get("abnormality_ratio", 0.0)),
        "cum_volume_so_far": float(by_window.get(BIN_MINUTES, {}).get("cum_volume_so_far", 0.0)),
        "S_k": float(by_window.get(BIN_MINUTES, {}).get("S_k", 0.0)),
        "Vhat_day": float(by_window.get(BIN_MINUTES, {}).get("Vhat_day", 0.0)),
        "by_window": by_window,
    }

    return (state, out)

isp_metrics = op.stateful_map("isp_update", keyed, isp_mapper)

op.output("isp_out", isp_metrics, StdOutSink())


def ch_fetch_prev_day_bins(symbol: str | None, day, 
    session_start: dtime, 
    session_end: dtime, 
    bin_minutes: int, 
    bin_count: int) -> list[float] | None:

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
                  (toRelativeMinuteNum(ts) - toRelativeMinuteNum(toStartOfDay(ts))) - sess_start_min,
                  {bin_minutes}
                ) AS bin_idx,
                sum(volume) AS vol
              FROM default.ohlc_1m
              WHERE ts >= start_dt
                AND ts <  end_dt
                AND symbol = '{symbol}'
                AND (toRelativeMinuteNum(ts) - toRelativeMinuteNum(toStartOfDay(ts))) >= sess_start_min
                AND (toRelativeMinuteNum(ts) - toRelativeMinuteNum(toStartOfDay(ts))) <  sess_end_min
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


def ch_fetch_hist_bins(symbol: str | None, end_day, 
    session_start: dtime, session_end: dtime, 
    bin_minutes: int, bin_count: int, lookback_days: int) -> list[float] | None:
    
    if not symbol:
        return None
    try:
        # Aggregate per-day per-bin over the last `lookback_days` days
        start_day_dt = datetime.combine(end_day, dtime(0, 0)) - timedelta(days=lookback_days - 1)
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
            SELECT d, bin_idx, vol
            FROM (
              SELECT
                toDate(ts) AS d,
                intDiv(
                  (toRelativeMinuteNum(ts) - toRelativeMinuteNum(toStartOfDay(ts))) - sess_start_min,
                  {bin_minutes}
                ) AS bin_idx,
                sum(volume) AS vol
              FROM default.ohlc_1m
              WHERE ts >= start_dt
                AND ts <= end_dt
                AND symbol = '{symbol}'
                AND (toRelativeMinuteNum(ts) - toRelativeMinuteNum(toStartOfDay(ts))) >= sess_start_min
                AND (toRelativeMinuteNum(ts) - toRelativeMinuteNum(toStartOfDay(ts))) <  sess_end_min
              GROUP BY d, bin_idx
            )
            ORDER BY d, bin_idx
        """
        result = client.query(sql)
        rows = result.result_rows
        if not rows:
            return [0.0] * bin_count

        from collections import OrderedDict
        daily = OrderedDict()
        for d, idx, vol in rows:
            key = str(d)
            if key not in daily:
                daily[key] = [0.0] * bin_count
            if 0 <= idx < bin_count:
                daily[key][idx] = float(vol)

        alpha = ISP_ALPHA
        smoothed = None
        for _, day_bins in daily.items():
            if smoothed is None:
                smoothed = [float(v) for v in day_bins]
            else:
                smoothed = [alpha * float(vd) + (1.0 - alpha) * float(vs) for vd, vs in zip(day_bins, smoothed)]

        if smoothed is None:
            return [0.0] * bin_count
        return smoothed
    except Exception as e:
        print("Exception in ch_fetch_hist_bins")
        print(e)
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
    except Exception as e:
        print("Exception in _get_ch_client")
        print(e)
        return None




if __name__ == "__main__":
    from bytewax.execution import run_main
    run_main(flow)