"""
Price Alerts Worker

Consumes realtime tick data and triggers alerts when price conditions are met.
Sends notifications via Telegram when alerts are triggered.

Following the pattern from isp.py for Bytewax dataflow processing.
"""
import os
import pymysql
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional
from zoneinfo import ZoneInfo
import orjson

from bytewax.dataflow import Dataflow
import bytewax.operators as op

from infra.mqtt_input import MqttSource
from infra.mock_clickhouse import MockClickHouseSource
from config import config
from infra.logging_setup import setup_logging
from infra.telegram_notifier import AlertNotification, send_alert_sync

# Same reason as tick_ingest: bytewax.run owns __main__, so without this the
# infra modules' log.info() calls never reach the container's stdout. This
# worker's own status lines use print(), which is why they show regardless.
setup_logging()


@dataclass
class PriceAlert:
    """Price alert configuration from database."""
    id: int
    symbol: str
    condition: str  # gt, gte, lt, lte, eq
    target_price: float
    is_active: bool
    is_triggered: bool
    notes: Optional[str] = None


@dataclass
class AlertState:
    """State for tracking alerts per symbol."""
    symbol: str
    last_price: float = 0.0
    last_alert_time: dict = field(default_factory=dict)  # alert_id -> datetime
    alerts_cache: list = field(default_factory=list)  # Cached alerts for this symbol
    cache_updated_at: Optional[datetime] = None


# ---------- Database helpers ----------
def get_db_connection():
    """Get a MySQL connection to the alert store.

    The alerts moved from the SQLite ``portfolio.db`` to MySQL along with the
    rest of the backend's tables.
    """
    cfg = config.price_alert
    return pymysql.connect(
        host=cfg.mysql_host,
        port=cfg.mysql_port,
        user=cfg.mysql_user,
        password=cfg.mysql_password,
        database=cfg.mysql_db,
        charset="utf8mb4",
        # Short timeouts: this runs per tick, and a hung connection must not
        # stall the dataflow. A failure here degrades to "no alerts", handled
        # by the caller's except.
        connect_timeout=5,
        read_timeout=5,
    )


def fetch_active_alerts(symbol: str) -> list[PriceAlert]:
    """Fetch active alerts for a specific symbol from database."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        # ``condition`` is a MySQL reserved word — it has to be backticked.
        # Placeholders are ``%s`` here, not SQLite's ``?``.
        cursor.execute("""
            SELECT id, symbol, `condition`, target_price, is_active, is_triggered, notes
            FROM price_alerts
            WHERE symbol = %s AND is_active = 1 AND is_triggered = 0
        """, (symbol.upper(),))

        rows = cursor.fetchall()
        conn.close()

        alerts = [
            PriceAlert(
                id=row[0],
                symbol=row[1],
                condition=row[2],
                target_price=float(row[3]),
                is_active=bool(row[4]),
                is_triggered=bool(row[5]),
                notes=row[6],
            )
            for row in rows
        ]
        
        if alerts:
            print(f"[DB] Found {len(alerts)} active alert(s) for {symbol}: "
                  f"{[(a.condition, a.target_price) for a in alerts]}")
        
        return alerts
    except Exception as e:
        print(f"[DB] Error fetching alerts for {symbol}: {e}")
        return []


# ---------- Alert condition checking ----------
def check_condition(condition: str, current_price: float, target_price: float) -> bool:
    """Check if the price condition is met."""
    if condition == "gt":
        return current_price > target_price
    elif condition == "gte":
        return current_price >= target_price
    elif condition == "lt":
        return current_price < target_price
    elif condition == "lte":
        return current_price <= target_price
    elif condition == "eq":
        # Allow small tolerance for equality
        tolerance = target_price * 0.001  # 0.1% tolerance
        return abs(current_price - target_price) <= tolerance
    return False


# ---------- Tick parsing ----------
def parse_tick(msg_payload: bytes) -> dict | None:
    """Parse incoming tick message."""
    try:
        d = orjson.loads(msg_payload)
        ts = datetime.fromisoformat(d["sendingTime"].replace("Z", "+00:00"))
        ts = ts.astimezone(ZoneInfo(config.isp.exchange_tz))
        
        return {
            "ts": ts,
            "symbol": d["symbol"],
            "price": float(d["matchPrice"]),
            "size": float(d.get("matchQtty", 0.0)),
        }
    except Exception as e:
        print(f"[Parse] Error parsing tick: {e}")
        return None


def key_by_symbol(item):
    """Extract symbol as key from tick message."""
    topic, payload = item
    tick = parse_tick(payload)
    if tick is None:
        return None, None
    return tick["symbol"], tick


def filter_valid_ticks(item):
    """Filter out invalid ticks."""
    symbol, tick = item
    return symbol is not None and tick is not None


# ---------- Stateful alert mapper ----------
def alert_mapper(state: AlertState | None, tick: dict):
    """
    Stateful mapper that checks price alerts for each tick.
    Returns (state, triggered_alerts) where triggered_alerts is a list of triggered alert info.
    """
    if state is None:
        state = AlertState(symbol=tick["symbol"])
    
    current_price = tick["price"]
    ts = tick["ts"]
    state.last_price = current_price
    
    # Refresh alerts cache every 30 seconds
    cache_age = (ts - state.cache_updated_at).total_seconds() if state.cache_updated_at else float('inf')
    if cache_age > 30:
        state.alerts_cache = fetch_active_alerts(state.symbol)
        state.cache_updated_at = ts
    
    triggered_alerts = []
    
    for alert in state.alerts_cache:
        # Check if this alert was recently triggered (rate limiting)
        last_alert = state.last_alert_time.get(alert.id)
        if last_alert:
            time_since_last = (ts - last_alert).total_seconds()
            if time_since_last < config.price_alert.rate_limit_seconds:
                continue
        
        # Check if condition is met
        if check_condition(alert.condition, current_price, alert.target_price):
            triggered_alerts.append({
                "alert_id": alert.id,
                "symbol": alert.symbol,
                "condition": alert.condition,
                "target_price": alert.target_price,
                "current_price": current_price,
                "notes": alert.notes,
                "ts": ts.isoformat(),
            })
            
            # Update rate limit tracking
            state.last_alert_time[alert.id] = ts
    
    return (state, {
        "symbol": state.symbol,
        "price": current_price,
        "ts": ts.isoformat(),
        "triggered_alerts": triggered_alerts,
    })


def filter_triggered(item):
    """Filter for items with triggered alerts."""
    symbol, data = item
    return len(data.get("triggered_alerts", [])) > 0


# ---------- Notification sender ----------
def send_notifications(item):
    """Send Telegram notifications for triggered alerts."""
    symbol, data = item
    triggered_alerts = data.get("triggered_alerts", [])
    
    for alert_data in triggered_alerts:
        # Create notification
        notification = AlertNotification(
            symbol=alert_data["symbol"],
            condition=alert_data["condition"],
            target_price=alert_data["target_price"],
            current_price=alert_data["current_price"],
            notes=alert_data.get("notes"),
        )
        
        # Send notification
        success = send_alert_sync(notification)
        
        if success:
            print(f"[Alert] Triggered: {alert_data['symbol']} @ {alert_data['current_price']} "
                  f"({alert_data['condition']} {alert_data['target_price']})")
        else:
            print(f"[Alert] Failed to send notification for {alert_data['symbol']}")
    
    return (symbol, data)


# ---------- Build dataflow ----------
flow = Dataflow("price_alerts_worker")

# 1) Ingest from MQTT or Mock source based on configuration
if config.mock.enabled:
    print("[PriceAlerts] Using MockClickHouseSource")
    stream = op.input("mock_ch", flow,
        MockClickHouseSource(
            config.clickhouse.host, config.clickhouse.port,
            config.clickhouse.user, config.clickhouse.password,
            config.clickhouse.database, config.mock.symbols,
            config.mock.start_time, config.mock.end_time,
            config.mock.speed, config.mock.loop, "mock/ch"
        ),
    )
else:
    print("[PriceAlerts] Using MqttSource")
    stream = op.input("mqtt", flow, MqttSource(
        config.mqtt.host,
        config.mqtt.port,
        config.mqtt.topics
    ))

# 2) Parse and key by symbol
keyed = op.map("key_by_symbol", stream, key_by_symbol)
valid = op.filter("filter_valid", keyed, filter_valid_ticks)

# 3) Stateful alert checking
alert_results = op.stateful_map("alert_check", valid, alert_mapper)

# 4) Filter for triggered alerts only
triggered = op.filter("filter_triggered", alert_results, filter_triggered)

# 5) Send notifications
notified = op.map("send_notifications", triggered, send_notifications)

# 6) Log to stdout for monitoring
op.inspect("log_alerts", notified)


if __name__ == "__main__":
    from bytewax.execution import run_main
    print("[PriceAlerts] Starting price alerts worker...")
    print(f"[PriceAlerts] Database: mysql://{config.price_alert.endpoint}")
    print(f"[PriceAlerts] Telegram enabled: {config.telegram.enabled}")
    run_main(flow)

