import os, asyncio, math, time
from datetime import datetime, timezone
from dataclasses import dataclass, field
import httpx, orjson
from bytewax.dataflow import Dataflow
from bytewax.inputs import DynamicSource, StatelessSourcePartition
import bytewax.operators as op
from bytewax.operators import stateful_map
from bytewax.connectors.stdio import StdOutSink
from bytewax.clickhouse import operators as chop

from dotenv import load_dotenv
load_dotenv()
import os
import requests
from mqtt_input import MqttSource


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
MQTT_TOPICS = ["plaintext/quotes/krx/mdds/tick/v1/roundlot/symbol/VCG", "plaintext/quotes/krx/mdds/tick/v1/roundlot/symbol/YEG"]
stream = op.input("mqtt", flow, MqttSource(MQTT_HOST, MQTT_PORT, MQTT_TOPICS))

# 2) Parse, key by symbol
keyed = op.map("key_by_symbol", stream, key_by_symbol)

@dataclass
class OrderBookState:
    symbol: str | None = None
    cum_delta: float = 0.0
    buy_vol: float = 0.0
    sell_vol: float = 0.0
    imbalance: float = 0.0
    last_ts: datetime | None = None
    last_side: int = 0
    last_price: float | None = None
    # Hawkes process state (per side)
    hawkes_S_bid: float = 0.0
    hawkes_S_ask: float = 0.0
    hawkes_lambda_bid: float = 0.0
    hawkes_lambda_ask: float = 0.0
    hawkes_alpha: float = 0.1
    hawkes_beta: float = 0.5
    hawkes_lambda0: float = 0.05
    # Running stats of cum_delta using Welford
    count: int = 0
    mean_cum_delta: float = 0.0
    M2_cum_delta: float = 0.0
    std_cum_delta: float = 0.0
    # EWMAs for maker detection
    ewma_price: float = 0.0
    ewma_price2: float = 0.0
    price_std: float = 0.0
    flip_ewma: float = 0.0
    mr_ewma: float = 0.0
    vol_ewma: float = 0.0
    imb_abs_ewma: float = 0.0

    def update(self, data):
        # Update rolling volumes and imbalance
        self.buy_vol += data["size"] if data["side"] == BUY else 0.0
        self.sell_vol += data["size"] if data["side"] == SELL else 0.0
        total = self.buy_vol + self.sell_vol
        self.imbalance = (self.buy_vol - self.sell_vol) / total if total > 0 else 0.0

        # Update cum_delta (buy as +, sell as -)
        if data["side"] == BUY:
            self.cum_delta += data["size"]
        elif data["side"] == SELL:
            self.cum_delta -= data["size"]

        # Hawkes: decay previous memory by time delta
        dt_sec = 0.0
        if self.last_ts is not None:
            try:
                dt_sec = max((data["ts"] - self.last_ts).total_seconds(), 0.0)
            except Exception:
                dt_sec = 0.0
        decay = math.exp(-self.hawkes_beta * dt_sec) if dt_sec > 0 else 1.0
        self.hawkes_S_bid *= decay
        self.hawkes_S_ask *= decay

        # Hawkes: add current side event magnitude
        event_bid = float(data["size"]) if data["side"] == BUY else 0.0
        event_ask = float(data["size"]) if data["side"] == SELL else 0.0
        self.hawkes_S_bid += event_bid
        self.hawkes_S_ask += event_ask

        # Hawkes: compute intensities with floor
        lambda_floor = 1e-3
        self.hawkes_lambda_bid = max(lambda_floor, self.hawkes_lambda0 + self.hawkes_alpha * self.hawkes_S_bid)
        self.hawkes_lambda_ask = max(lambda_floor, self.hawkes_lambda0 + self.hawkes_alpha * self.hawkes_S_ask)

        self.last_ts = data["ts"]

        # Update running stddev of cum_delta via Welford's algorithm
        self.count += 1
        x = self.cum_delta
        if self.count == 1:
            self.mean_cum_delta = x
            self.M2_cum_delta = 0.0
            self.std_cum_delta = 0.0
        else:
            delta = x - self.mean_cum_delta
            self.mean_cum_delta += delta / self.count
            delta2 = x - self.mean_cum_delta
            self.M2_cum_delta += delta * delta2
            # Unbiased sample std dev when count > 1
            self.std_cum_delta = math.sqrt(self.M2_cum_delta / (self.count - 1)) if self.count > 1 else 0.0

        return self

    def summarize(self):
        # Spike when cum_delta exceeds 2 standard deviations (one-sided)
        spike = self.cum_delta > 2.0 * self.std_cum_delta if self.std_cum_delta > 0 else False
        return {
            "symbol": self.symbol,
            "ts": self.last_ts.isoformat() if self.last_ts else None,
            "cum_delta": float(self.cum_delta),
            "buy_vol": float(self.buy_vol),
            "sell_vol": float(self.sell_vol),
            "imbalance": float(self.imbalance),
            "mean_cum_delta": float(self.mean_cum_delta),
            "std_cum_delta": float(self.std_cum_delta),
            "spike": spike,
            # Hawkes outputs
            "lambda_bid": float(self.hawkes_lambda_bid),
            "lambda_ask": float(self.hawkes_lambda_ask),
            "lambda_spread": float(self.hawkes_lambda_bid - self.hawkes_lambda_ask),
            "lambda_ratio": float(self.hawkes_lambda_bid / max(self.hawkes_lambda_ask, 1e-6)),
        }

def mapper(state, value):
    """Update the state with the given value and return the state and a summary."""
    if state is None:
        state = OrderBookState()
    # Attach symbol once
    if state.symbol is None and "symbol" in value:
        state.symbol = value["symbol"]
    state.update(value)
    return (state, state.summarize())

features = op.stateful_map("update_state", keyed, mapper)

op.output("out", features, StdOutSink())

# ---------------- ClickHouse Sinks ----------------
CLICKHOUSE_DB = os.getenv("CLICKHOUSE_DB", "mydb")
CLICKHOUSE_TABLE = os.getenv("CLICKHOUSE_TABLE", "orderbook_features")

CH_SCHEMA = """
        metric String,
        value Float64,
        ts DateTime
        """

ORDER_BY = "metric, ts"

PA_SCHEMA = pa.schema(
    [
        ("metric", pa.string()),
        ("value", pa.float64()),
        ("ts", pa.timestamp("us")),  # microsecond
    ]
)

chop.output(
    "output_clickhouse",
    features,
    "features",
    CLICKHOUSE_USER,
    CLICKHOUSE_PASSWORD,
    database="bytewax",
    port=8123,
    ch_schema=CH_SCHEMA,
    order_by=ORDER_BY,
    pa_schema=PA_SCHEMA,
    timeout=timedelta(seconds=1),
    max_size=10,
)


if __name__ == "__main__":
    from bytewax.execution import run_main
    run_main(flow)