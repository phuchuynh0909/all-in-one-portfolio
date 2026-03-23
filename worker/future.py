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


# ---------- Build dataflow ----------
flow = Dataflow("mqtt_ticks_future_market")

# 1) Ingest from MQTT or Mock source based on configuration
stream = op.input("mqtt", flow, MqttSource(
    config.mqtt.host,
    config.mqtt.port,
    config.mqtt.topics
))

# keyed = op.map("key_by_symbol", stream, key_by_symbol)
op.output("future_market", stream, StdOutSink())

if __name__ == "__main__":
    from bytewax.execution import run_main
    run_main(flow)