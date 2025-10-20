import os, asyncio, math, time
from zoneinfo import ZoneInfo
from dataclasses import dataclass, field
import orjson
import numpy as np
import pyarrow as pa
from bytewax.dataflow import Dataflow
from bytewax.inputs import DynamicSource, StatelessSourcePartition
import bytewax.operators as op
from bytewax.connectors.stdio import StdOutSink
from bytewax.clickhouse import operators as ch_operators

from mqtt_input import MqttSource
from mock_clickhouse import MockClickHouseSource
from datetime import time as dtime
from datetime import timedelta, datetime, timezone
from config import config
from helper import _num_bins, _bin_index, _normalize
from clickhouse_client import get_clickhouse_client
from model import ISP_ALERT_ARROW_SCHEMA, ISP_ALERT_CLICKHOUSE_SCHEMA, ISP_ALERT_CLICKHOUSE_TABLE, ISP_ALERT_CLICKHOUSE_ORDER_BY

EPS = 1e-9
BUY = 1
SELL = 2

@dataclass
class WelfordState:
    """Welford's algorithm for online mean/variance calculation"""
    count: int = 0
    mean: float = 0.0
    m2: float = 0.0  # Sum of squared differences from mean
    
    def update(self, value: float):
        """Update running statistics with new value"""
        self.count += 1
        delta = value - self.mean
        self.mean += delta / self.count
        delta2 = value - self.mean
        self.m2 += delta * delta2
    
    def variance(self) -> float:
        """Return variance (population variance)"""
        return self.m2 / self.count if self.count > 1 else 0.0
    
    def std_dev(self) -> float:
        """Return standard deviation"""
        return math.sqrt(self.variance())
    
    def z_score(self, value: float) -> float:
        """Calculate z-score for a given value"""
        std = self.std_dev()
        if std < EPS or self.count < 2:
            return 0.0
        return (value - self.mean) / std

@dataclass
class WindowState:
    bin_minutes: int
    bin_count: int = 0
    isp: list[float] = field(default_factory=list)
    vol_today: list[float] = field(default_factory=list)
    cum_vol: float = 0.0
    # Welford statistics for abnormality ratio
    welford: WelfordState = field(default_factory=WelfordState)

@dataclass
class ISPState:
    symbol: str | None = None
    alpha: float = field(default_factory=lambda: config.isp.alpha)
    session_start: dtime = field(default_factory=lambda: config.isp.session_start)
    session_end: dtime = field(default_factory=lambda: config.isp.session_end)
    windows: dict[int, WindowState] = field(default_factory=dict)
    bootstrapped: bool = False
    current_day: datetime | None = None

    def ensure_initialized(self, ts: datetime):
        # Initialize per-window state structures
        for w in config.isp.windows:
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
            end_day = ts.date() - timedelta(days=1)
            ch_client = get_clickhouse_client()
            # Bootstrap each window independently using historical bins
            for w in config.isp.windows:
                ws = self.windows.get(w) or WindowState(bin_minutes=w)
                if ws.bin_count <= 0:
                    ws.bin_count = _num_bins(self.session_start, self.session_end, ws.bin_minutes)
                bins = ch_client.fetch_historical_bins(
                    symbol=symbol,
                    end_day=end_day,
                    session_start=self.session_start,
                    session_end=self.session_end,
                    bin_minutes=ws.bin_minutes,
                    bin_count=ws.bin_count,
                    lookback_days=config.isp.bootstrap_days,
                )
                if bins is not None and sum(bins) > 0:
                    ws.isp = _normalize([v for v in bins])
                self.windows[w] = ws
        except Exception:
            pass
        self.bootstrapped = True


def parse_tick(msg_payload: bytes) -> dict | None:
    # Expecting JSON payload per tick:
    # {'marketId': 'MARKET_ID_STO', 'boardId': 'BOARD_ID_G1', 'isin': 'VN000000YEG3', 'symbol': 'YEG', 'matchPrice': 13.0, 'matchQtty': 100.0, 'sendingTime': '2025-10-15T07:45:07.049Z', 'boardIdOriginal': 'BOARD_ID_G1', 'tradingSessionId': 'TRADING_SESSION_ID_30', 'totalVolumeTraded': '421570', 'grossTradeAmount': 55.095725, 'side': 'SIDE_SELL'} 
    try:
        d = orjson.loads(msg_payload)
        side = BUY if d.get("side") in ("B", "SIDE_BUY", 1) else SELL if d.get("side") in ("S","SIDE_SELL", 2) else 0
        # Normalize ts to epoch ns
        ts = datetime.fromisoformat(d["sendingTime"].replace("Z","+00:00"))
        # Convert to exchange local timezone for session binning consistency
        ts = ts.astimezone(ZoneInfo(config.isp.exchange_tz))
    except Exception as e:
        print("Exception in parse_tick: ", e)
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

# ---------- ISP mapper ----------
def isp_mapper(state: ISPState | None, tick: dict):
    if state is None:
        state = ISPState()
        state.symbol = tick.get("symbol")
    ts: datetime = tick["ts"]
    state.ensure_initialized(ts)
    state.bootstrap_if_needed(ts, state.symbol)
    day = ts.date()

    # Reset volumes on new trading day
    if state.current_day != day:
        for ws in state.windows.values():
            ws.vol_today = [0.0] * ws.bin_count
            ws.cum_vol = 0.0
        state.current_day = day

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
        actual_bin_vol = ws.vol_today[bin_idx_w]  # Volume accumulated in this bin today
        abnormality_w = actual_bin_vol / max(expected_bin_w, EPS)
        
        # Update Welford statistics with the new abnormality ratio
        ws.welford.update(abnormality_w)
        z_score = ws.welford.z_score(abnormality_w)
        
        by_window[w] = {
            "bin_index": int(bin_idx_w),
            "expected_volume": float(expected_bin_w), ## model's expected volume for the current bin.
            "actual_bin_volume": float(actual_bin_vol), ## actual volume observed in the current bin.
            "abnormality_ratio": float(abnormality_w), ## how surprising the current-bin volume is relative to expectation.
            "z_score": float(z_score), ## z-score of abnormality ratio (standard deviations from mean)
            "welford_mean": float(ws.welford.mean), ## running mean of abnormality ratios
            "welford_std": float(ws.welford.std_dev()), ## running standard deviation
            "welford_count": int(ws.welford.count), ## number of samples in Welford statistics
            "cum_volume_so_far": float(ws.cum_vol), ## cumulative traded volume observed up to and including the current bin (in the current window)
            "S_k": float(S_k_w), ##  the cumulative ISP share up to the current bin k.
            "Vhat_day": float(Vhat_day_w), ## Estimated total day volume implied by volume so far and the ISP curve.
        }
        state.windows[w] = ws

    out = {
        "symbol": state.symbol,
        "ts": ts.isoformat(),
        "date": day.isoformat(),
        "volume_observed": observed_vol,
        "isp_by_window": by_window,
    }

    return (state, out)

# ---------- Filter metrics ----------
def filter_metrics(item):
    """Filter based on z-scores to reduce noise.
    Only alert if:
    1. Z-score > 2.0 (more than 2 standard deviations from mean)
    2. Welford has enough samples (>= 30 for statistical significance)
    3. Abnormality ratio > 1.5 (at least 50% above expected)
    """
    symbol, data = item
    Z_SCORE_THRESHOLD = 2.0  # Standard deviations from mean
    MIN_SAMPLES = 30  # Minimum samples for statistical significance
    MIN_ABNORMALITY = 1.5  # Minimum abnormality ratio
    
    for _, window_data in data.get("isp_by_window", {}).items():
        z_score = float(window_data.get("z_score", 0.0))
        welford_count = int(window_data.get("welford_count", 0))
        abnormality = float(window_data.get("abnormality_ratio", 0.0))
        
        # Alert if statistically significant AND abnormal
        if (z_score > Z_SCORE_THRESHOLD and 
            welford_count >= MIN_SAMPLES and 
            abnormality > MIN_ABNORMALITY):
            return True
    return False

# ---------- Transform out ----------
def transform_out_func(item):
    symbol, data = item
    
    # Ensure symbol is a string
    symbol_str = str(symbol).strip() if symbol is not None else ""
    
    ts = data.get("ts")
    ## Convert from isoformat (string) to timestamp in microseconds
    ts_us = datetime.fromisoformat(ts).timestamp() * 1000000
    
    # Initialize ratios and z-scores with defaults
    ratios = {
        "abnormality_ratio_5m": float(0.0),
        "abnormality_ratio_15m": float(0.0),
        "abnormality_ratio_30m": float(0.0),
        "abnormality_ratio_60m": float(0.0),
    }
    z_scores = {
        "z_score_5m": float(0.0),
        "z_score_15m": float(0.0),
        "z_score_30m": float(0.0),
        "z_score_60m": float(0.0),
    }
    
    # Extract metrics for each window from actual data
    for window, window_data in data.get("isp_by_window", {}).items():
        ratio_field = f"abnormality_ratio_{window}m"
        z_score_field = f"z_score_{window}m"
        
        if ratio_field in ratios: 
            ratios[ratio_field] = float(window_data.get("abnormality_ratio", 0.0))
        if z_score_field in z_scores:
            z_scores[z_score_field] = float(window_data.get("z_score", 0.0))
    
    output_tuple = (
        str(symbol_str),
        ts_us,
        float(ratios["abnormality_ratio_5m"]),
        float(ratios["abnormality_ratio_15m"]),
        float(ratios["abnormality_ratio_30m"]),
        float(ratios["abnormality_ratio_60m"]),
        float(z_scores["z_score_5m"]),
        float(z_scores["z_score_15m"]),
        float(z_scores["z_score_30m"]),
        float(z_scores["z_score_60m"]),
    )
    
    # Return as (key, tuple_of_values) for ClickHouse operator
    return (symbol_str, output_tuple)

# ---------- Build dataflow ----------
flow = Dataflow("mqtt_ticks_to_clickhouse")

# 1) Ingest from MQTT or Mock source based on configuration
if config.mock.enabled:
    stream = op.input("mock_ch", flow,
        MockClickHouseSource(config.clickhouse.host, config.clickhouse.port, 
        config.clickhouse.user, config.clickhouse.password, 
        config.clickhouse.database, config.mock.symbols, 
        config.mock.start_time, config.mock.end_time, config.mock.speed, config.mock.loop, "mock/ch"),
    )
else:
    stream = op.input("mqtt", flow, MqttSource(
        config.mqtt.host,
        config.mqtt.port,
        config.mqtt.topics
    ))

# 2) Parse, key, transform by symbol
keyed = op.map("key_by_symbol", stream, key_by_symbol)
isp_metrics = op.stateful_map("isp_update", keyed, isp_mapper)
filtered = op.filter("filter_metrics", isp_metrics, filter_metrics)
transformed = op.map("transform_out", filtered, transform_out_func)


# 4) Sink to ClickHouse
ch_operators.output(
    "isp_alerts",
    transformed,
    pa_schema=ISP_ALERT_ARROW_SCHEMA,
    table_name=ISP_ALERT_CLICKHOUSE_TABLE,
    ch_schema=ISP_ALERT_CLICKHOUSE_SCHEMA,
    order_by=ISP_ALERT_CLICKHOUSE_ORDER_BY,
    database=config.clickhouse.database,
    host=config.clickhouse.host,
    port=config.clickhouse.port,
    username=config.clickhouse.user,
    password=config.clickhouse.password,
    timeout=timedelta(seconds=10),
)

if __name__ == "__main__":
    from bytewax.execution import run_main
    run_main(flow)