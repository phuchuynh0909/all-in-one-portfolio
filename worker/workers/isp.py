import os, asyncio, math, time
from zoneinfo import ZoneInfo
from dataclasses import dataclass, field
from collections import deque
import orjson
import numpy as np
import pyarrow as pa
from bytewax.dataflow import Dataflow
from bytewax.inputs import DynamicSource, StatelessSourcePartition
import bytewax.operators as op
from bytewax.connectors.stdio import StdOutSink
from bytewax.clickhouse import operators as ch_operators

from infra.mqtt_input import MqttSource
from infra.mock_clickhouse import MockClickHouseSource
from datetime import time as dtime
from datetime import timedelta, datetime, timezone
from config import config
from core.helper import _num_bins, _bin_index, _normalize
from infra.clickhouse_client import get_clickhouse_client
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
class SlidingVolumeWindow:
    """Tracks realized volume over a sliding time window (e.g., 5 seconds)"""
    window_seconds: float = 5.0
    ticks: deque = field(default_factory=deque)  # Store (timestamp, volume, side) tuples
    
    def update(self, ts: datetime, volume: float, side: int):
        """Add new tick and remove old ticks outside the window"""
        # Add new tick with side information (BUY=1, SELL=2)
        self.ticks.append((ts, volume, side))
        
        # Remove ticks older than window_seconds
        cutoff_time = ts - timedelta(seconds=self.window_seconds)
        while self.ticks and self.ticks[0][0] < cutoff_time:
            self.ticks.popleft()
    
    def get_realized_volume(self) -> float:
        """Get total volume in the current window"""
        return sum(vol for _, vol, _ in self.ticks)
    
    def get_buy_volume(self) -> float:
        """Get buy volume in the current window"""
        return sum(vol for _, vol, side in self.ticks if side == BUY)
    
    def get_sell_volume(self) -> float:
        """Get sell volume in the current window"""
        return sum(vol for _, vol, side in self.ticks if side == SELL)
    
    def get_ofi(self) -> float:
        """Calculate Order Flow Imbalance: (Buy - Sell) / Total"""
        buy_vol = self.get_buy_volume()
        sell_vol = self.get_sell_volume()
        total_vol = buy_vol + sell_vol
        
        if total_vol < EPS:
            return 0.0
        return (buy_vol - sell_vol) / total_vol
    
    def get_tick_count(self) -> int:
        """Get number of ticks in the current window"""
        return len(self.ticks)

@dataclass
class WindowState:
    bin_minutes: int
    bin_count: int = 0
    isp: list[float] = field(default_factory=list)
    vol_today: list[float] = field(default_factory=list)
    cum_vol: float = 0.0
    # Welford statistics for abnormality ratio
    welford: WelfordState = field(default_factory=WelfordState)
    # RVOL: Historical average volume per bin (from bootstrap)
    avg_volume_per_bin: list[float] = field(default_factory=list)
    total_historical_volume: float = 0.0  # Sum of all historical volume
    # Order Flow Imbalance tracking
    buy_vol_today: list[float] = field(default_factory=list)
    sell_vol_today: list[float] = field(default_factory=list)
    cum_buy_vol: float = 0.0
    cum_sell_vol: float = 0.0

@dataclass
class ISPState:
    symbol: str | None = None
    alpha: float = field(default_factory=lambda: config.isp.alpha)
    session_start: dtime = field(default_factory=lambda: config.isp.session_start)
    session_end: dtime = field(default_factory=lambda: config.isp.session_end)
    windows: dict[int, WindowState] = field(default_factory=dict)
    bootstrapped: bool = False
    current_day: datetime | None = None
    # Short-term sliding window for realized volume (5 seconds)
    sliding_window: SlidingVolumeWindow = field(default_factory=SlidingVolumeWindow)
    # Welford statistics for 5s realized volumes (statistical baseline)
    welford_5s: WelfordState = field(default_factory=WelfordState)

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
            if not ws.buy_vol_today or len(ws.buy_vol_today) != ws.bin_count:
                ws.buy_vol_today = [0.0] * ws.bin_count
            if not ws.sell_vol_today or len(ws.sell_vol_today) != ws.bin_count:
                ws.sell_vol_today = [0.0] * ws.bin_count

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
                    # Store normalized ISP curve
                    ws.isp = _normalize([v for v in bins])
                    # Store raw average volumes for RVOL calculation
                    ws.avg_volume_per_bin = [float(v) for v in bins]
                    ws.total_historical_volume = sum(bins)
                else:
                    # Initialize with uniform distribution if no data
                    ws.avg_volume_per_bin = [0.0] * ws.bin_count
                    ws.total_historical_volume = 0.0
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

    # Update sliding window with new tick volume
    observed_vol = float(tick.get("size", 0.0))
    side = tick.get("side", 0)
    state.sliding_window.update(ts, observed_vol, side)
    
    # Calculate short-term realized volume (5s window)
    realized_vol_5s = state.sliding_window.get_realized_volume()
    tick_count_5s = state.sliding_window.get_tick_count()
    
    # === BASELINE 2: Welford Statistical Baseline (Data-Driven) ===
    # Update Welford statistics with current 5s volume
    state.welford_5s.update(realized_vol_5s)
    # Calculate z-score (statistical baseline)
    z_score_5s = state.welford_5s.z_score(realized_vol_5s)

    # Build per-window metrics
    by_window = {}
    # Track expected 5m bin volume for 5s comparison
    expected_bin_5m = 0.0
    
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
        
        # === BASELINE 1: Compare 5s volume against 5m ISP bucket ===
        # Store the 5-minute bucket's expected volume for comparison
        if w == 5:
            expected_bin_5m = expected_bin_w
        
        # Calculate RVOL (Relative Volume)
        # RVOL = actual volume / historical average volume for this bin
        avg_vol_for_bin = ws.avg_volume_per_bin[bin_idx_w] if ws.avg_volume_per_bin else 0.0
        rvol = actual_bin_vol / max(avg_vol_for_bin, EPS)
        
        # Update Welford statistics with the new abnormality ratio
        ws.welford.update(abnormality_w)
        z_score = ws.welford.z_score(abnormality_w)
        
        by_window[w] = {
            "bin_index": int(bin_idx_w),
            "expected_volume": float(expected_bin_w), ## model's expected volume for the current bin.
            "actual_bin_volume": float(actual_bin_vol), ## actual volume observed in the current bin.
            "abnormality_ratio": float(abnormality_w), ## how surprising the current-bin volume is relative to expectation.
            "rvol": float(rvol), ## Relative Volume: actual / historical average for this time of day
            "z_score": float(z_score), ## z-score of abnormality ratio (standard deviations from mean)
            "welford_mean": float(ws.welford.mean), ## running mean of abnormality ratios
            "welford_std": float(ws.welford.std_dev()), ## running standard deviation
            "welford_count": int(ws.welford.count), ## number of samples in Welford statistics
            "cum_volume_so_far": float(ws.cum_vol), ## cumulative traded volume observed up to and including the current bin (in the current window)
            "S_k": float(S_k_w), ##  the cumulative ISP share up to the current bin k.
            "Vhat_day": float(Vhat_day_w), ## Estimated total day volume implied by volume so far and the ISP curve.
            "avg_volume_historical": float(avg_vol_for_bin), ## Historical average volume for this bin
        }
        state.windows[w] = ws

    # Calculate surge ratio: what fraction of the 5m bin is in the last 5s
    # This shows concentration - if 5s contains significant portion of 5m expected, it's a surge
    surge_ratio_5s = realized_vol_5s / max(expected_bin_5m, EPS)

    out = {
        "symbol": state.symbol,
        "ts": ts.isoformat(),
        "date": day.isoformat(),
        "volume_observed": observed_vol,
        "ofi_5s": state.sliding_window.get_ofi(),
        # 5s window metrics
        "realized_volume_5s": realized_vol_5s,  # Actual volume in last 5 seconds
        "expected_bin_5m": expected_bin_5m,  # Expected volume for entire 5m bin (ISP baseline)
        "surge_ratio_5s": surge_ratio_5s,  # Fraction of 5m bin concentrated in 5s
        "z_score_5s": z_score_5s,  # Statistical z-score (Welford baseline)
        "welford_5s_mean": state.welford_5s.mean,  # Running mean of 5s volumes
        "welford_5s_std": state.welford_5s.std_dev(),  # Running std dev of 5s volumes
        "welford_5s_count": state.welford_5s.count,  # Sample count for statistics
        "tick_count_5s": tick_count_5s,  # Number of ticks in last 5 seconds
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
    OR
    4. Short-term surge (5s): Both baselines agree
       - Surge ratio > 5.0 (5x ISP expectation)
       - Z-score > 2.0 (2+ std deviations)
    """
    symbol, data = item
    
    ## only alert buyside
    ofi_5s = float(data.get("ofi_5s", 0.0))
    if ofi_5s <= 0:
        return False

    surge_ratio_5s = float(data.get("surge_ratio_5s", 0.0))
    z_score_5s = float(data.get("z_score_5s", 0.0))

    # Ignore 0 or too small values of expected volume in the 5s window
    expected_bin_5m = float(data.get("expected_bin_5m", 0.0))
    if expected_bin_5m <= 0:
        return False


    # === Check for 5s volume surge (dual baseline confirmation) ===
    # Normal uniform: 5s / 300s = 1/60 = 0.0167 (1.67%)
    # 6x normal rate = 0.1 (10% of 5m bin in 5s)
    # 12x normal rate = 0.2 (20% of 5m bin in 5s)
    SURGE_RATIO_THRESHOLD = 1  ## (100% of 5m bin in 5s)
    Z_SCORE_5S_THRESHOLD = 3.0    # 2 std deviations (statistical baseline)
    ## Alert if surge_ratio_5s > SURGE_RATIO_THRESHOLD or z_score_5s > Z_SCORE_5S_THRESHOLD
    ## The total volume in the 5s window is greater than the expected volume in the 5m bin (ISP baseline)
    ## The z-score of the 5s volume is greater than 2 standard deviations from the mean of the Welford statistics
    print(f"surge_ratio_5s: {surge_ratio_5s}, z_score_5s: {z_score_5s}")
    if surge_ratio_5s > SURGE_RATIO_THRESHOLD or z_score_5s > Z_SCORE_5S_THRESHOLD:
        return True
    return False

# ---------- Rate limiting (1 alert per symbol per minute) ----------
def rate_limit_alerts(state: datetime | None, data):
    """
    Rate limit alerts: only allow 1 alert per symbol per minute.
    State stores the timestamp of the last alert for this symbol.
    In stateful_map on keyed stream, this receives (state, value) where value is just the data.
    """
    # Get current timestamp
    ts_str = data.get("ts")
    current_ts = datetime.fromisoformat(ts_str)
    
    # If no previous alert, allow this one
    if state is None:
        return (current_ts, data)
    
    # Check if 1 minute has passed since last alert
    time_since_last = (current_ts - state).total_seconds()
    if time_since_last >= 60:
        # Allow alert and update last alert time
        return (current_ts, data)
    else:
        # Suppress alert - too soon since last one
        return (state, None)

def filter_rate_limited(item):
    """Filter out suppressed alerts (None values from rate limiter)"""
    symbol, data = item
    return data is not None

# ---------- Transform out ----------
def transform_out_func(item):
    symbol, data = item
    
    # Ensure symbol is a string
    symbol_str = str(symbol).strip() if symbol is not None else ""
    
    ts = data.get("ts")
    ## Convert from isoformat (string) to timestamp in microseconds
    ts_us = datetime.fromisoformat(ts).timestamp() * 1000000
    
    ofi_5s = float(data.get("ofi_5s", 0.0))
    
    # Extract 5s surge metrics (two baselines)
    realized_vol_5s = float(data.get("realized_volume_5s", 0.0))
    expected_bin_5m = float(data.get("expected_bin_5m", 0.0))
    surge_ratio_5s = float(data.get("surge_ratio_5s", 0.0))
    z_score_5s = float(data.get("z_score_5s", 0.0))
    tick_count_5s = int(data.get("tick_count_5s", 0))
    
    # Initialize ratios, z-scores, and RVOL with defaults
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
    rvols = {
        "rvol_5m": float(0.0),
        "rvol_15m": float(0.0),
        "rvol_30m": float(0.0),
        "rvol_60m": float(0.0),
    }
    
    # Extract metrics for each window from actual data
    for window, window_data in data.get("isp_by_window", {}).items():
        ratio_field = f"abnormality_ratio_{window}m"
        z_score_field = f"z_score_{window}m"
        rvol_field = f"rvol_{window}m"
        
        if ratio_field in ratios: 
            ratios[ratio_field] = float(window_data.get("abnormality_ratio", 0.0))
        if z_score_field in z_scores:
            z_scores[z_score_field] = float(window_data.get("z_score", 0.0))
        if rvol_field in rvols:
            rvols[rvol_field] = float(window_data.get("rvol", 0.0))
    
    output_tuple = (
        str(symbol_str),
        ts_us,
        float(ofi_5s),
        float(ratios["abnormality_ratio_5m"]),
        float(ratios["abnormality_ratio_15m"]),
        float(ratios["abnormality_ratio_30m"]),
        float(ratios["abnormality_ratio_60m"]),
        float(z_scores["z_score_5m"]),
        float(z_scores["z_score_15m"]),
        float(z_scores["z_score_30m"]),
        float(z_scores["z_score_60m"]),
        float(rvols["rvol_5m"]),
        float(rvols["rvol_15m"]),
        float(rvols["rvol_30m"]),
        float(rvols["rvol_60m"]),
        float(realized_vol_5s),   # Actual volume in last 5s
        float(expected_bin_5m),   # Expected volume for entire 5m bin (ISP baseline)
        float(surge_ratio_5s),    # Fraction of 5m bin in last 5s
        float(z_score_5s),        # Z-score of 5s volume (Welford baseline)
        int(tick_count_5s),       # Number of ticks in 5s window
    )
    
    # Return as (key, tuple_of_values) for ClickHouse operator
    return (symbol_str, output_tuple)

# ---------- Build dataflow ----------
flow = Dataflow("mqtt_ticks_to_clickhouse")

# 1) Ingest from MQTT or Mock source based on configuration
if config.mock.enabled:
    print("Using MockClickHouseSource")
    stream = op.input("mock_ch", flow,
        MockClickHouseSource(config.clickhouse.host, config.clickhouse.port, 
        config.clickhouse.user, config.clickhouse.password, 
        config.clickhouse.database, config.mock.symbols, 
        config.mock.start_time, config.mock.end_time, config.mock.speed, config.mock.loop, "mock/ch"),
    )
else:
    print("Using MqttSource")
    stream = op.input("mqtt", flow, MqttSource(
        config.mqtt.host,
        config.mqtt.port,
        config.mqtt.topics
    ))

# 2) Parse, key, transform by symbol
keyed = op.map("key_by_symbol", stream, key_by_symbol)
isp_metrics = op.stateful_map("isp_update", keyed, isp_mapper)
filtered = op.filter("filter_metrics", isp_metrics, filter_metrics)

# 3) Rate limit: only 1 alert per symbol per minute
rate_limited = op.stateful_map("rate_limit", filtered, rate_limit_alerts)
rate_limited_filtered = op.filter("filter_rate_limited", rate_limited, filter_rate_limited)

# 4) Transform for ClickHouse
transformed = op.map("transform_out", rate_limited_filtered, transform_out_func)


# 5) Sink to ClickHouse
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