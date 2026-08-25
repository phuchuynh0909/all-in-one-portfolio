"""Configuration management for ISP worker."""

import os
import json
from pathlib import Path
from dataclasses import dataclass
from datetime import time as dtime
from dotenv import load_dotenv
from core.vn30f_symbol import current_symbol as _current_vn30f_symbol

# Load environment variables from .env file
load_dotenv()

# Boards subscribed on the DNSE Trade-Extra feed when DNSE_TRADE_BOARDS is unset.
# Kept here rather than imported from ``infra.dnse_ws_input`` so that config stays
# free of the websocket SDK, which every other worker would then pay for on
# import. ``test_dnse_ws_input`` asserts the two defaults agree — they silently
# drifted once, and the module constant looked authoritative while this one was
# what production actually used.
DEFAULT_TRADE_BOARDS = ("G1",)


def _parse_bool(raw: str | None, default: bool = False) -> bool:
    """Parse a bool-like environment variable."""
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class ISPConfig:
    """ISP algorithm parameters."""

    bin_minutes: int
    windows: list[int]
    alpha: float
    session_start: dtime
    session_end: dtime
    bootstrap_days: int
    exchange_tz: str

    @classmethod
    def from_env(cls) -> "ISPConfig":
        """Load ISP configuration from environment variables."""
        bin_minutes = int(os.getenv("ISP_BIN_MINUTES", "15"))

        # Parse windows from comma-separated string
        _raw_windows = [
            int(x)
            for x in os.getenv("ISP_WINDOWS", "5,15,30,60").split(",")
            if x.strip()
        ]
        # Ensure required windows are present, dedupe, and sort
        windows = sorted(set(_raw_windows) | {bin_minutes, 60})

        alpha = float(os.getenv("ISP_ALPHA", "0.05"))
        bootstrap_days = int(os.getenv("ISP_BOOTSTRAP_DAYS", "3"))
        exchange_tz = os.getenv("EXCHANGE_TZ", "Asia/Ho_Chi_Minh")

        # Parse trading session
        session_str = os.getenv("ISP_SESSION", "09:00,14:45")
        session_start, session_end = cls._parse_session(session_str)

        return cls(
            bin_minutes=bin_minutes,
            windows=windows,
            alpha=alpha,
            session_start=session_start,
            session_end=session_end,
            bootstrap_days=bootstrap_days,
            exchange_tz=exchange_tz,
        )

    @staticmethod
    def _parse_session(session_str: str) -> tuple[dtime, dtime]:
        """Parse trading session string into start and end times."""
        try:
            start_str, end_str = [s.strip() for s in session_str.split(",")]
            sh, sm = start_str.split(":")
            eh, em = end_str.split(":")
            return dtime(int(sh), int(sm)), dtime(int(eh), int(em))
        except Exception:
            # Fallback to a sane default
            return dtime(9, 0), dtime(15, 0)


@dataclass
class ClickHouseConfig:
    """ClickHouse database configuration."""

    host: str
    port: int
    user: str
    password: str
    database: str
    secure: bool
    connect_timeout: int

    @classmethod
    def from_env(cls) -> "ClickHouseConfig":
        """Load ClickHouse configuration from environment variables."""
        port = int(os.getenv("CLICKHOUSE_PORT", "9010"))
        secure_raw = os.getenv("CLICKHOUSE_SECURE")
        # Auto-enable TLS for common HTTPS ClickHouse ports when unset.
        secure = _parse_bool(secure_raw, default=port in (443, 8443))
        return cls(
            host=os.getenv("CLICKHOUSE_HOST", "localhost"),
            port=port,
            user=os.getenv("CLICKHOUSE_USER", "myuser"),
            password=os.getenv("CLICKHOUSE_PASSWORD", "mypassword"),
            database=os.getenv("CLICKHOUSE_DB", "default"),
            secure=secure,
            connect_timeout=int(os.getenv("CLICKHOUSE_CONNECT_TIMEOUT", "10")),
        )


@dataclass
class MockConfig:
    """Mock data source configuration for testing."""

    enabled: bool
    symbols: list[str]
    start_time: str
    end_time: str
    speed: float
    loop: bool

    @classmethod
    def from_env(cls) -> "MockConfig":
        """Load mock configuration from environment variables."""
        enabled = os.getenv("ISP_USE_MOCK", "0") in ("1", "true", "True")
        symbols = [
            s.strip()
            for s in os.getenv("ISP_MOCK_SYMBOLS", "").split(",")
            if s.strip()
        ]
        start_time = os.getenv("ISP_MOCK_START", "2025-10-17 09:00:00")
        end_time = os.getenv("ISP_MOCK_END", "2025-10-17 14:45:00")
        speed = float(os.getenv("ISP_MOCK_SPEED", "1.0"))
        loop = os.getenv("ISP_MOCK_LOOP", "0") in ("1", "true", "True")

        return cls(
            enabled=enabled,
            symbols=symbols,
            start_time=start_time,
            end_time=end_time,
            speed=speed,
            loop=loop,
        )


@dataclass
class DnseWsConfig:
    """DNSE OpenAPI market-data WebSocket source configuration.

    Used by ``infra.dnse_ws_input.DnseTradeSource`` to consume the Trade-Extra
    feed at ``wss://ws-openapi.dnse.com.vn/v1/stream``, which it reaches through
    the official SDK vendored at ``worker/dnse_sdk`` (HMAC auth; ``json`` or
    ``msgpack`` frames per ``DNSE_WS_ENCODING``). Credentials come from
    ``DNSE_API_KEY`` / ``DNSE_API_SECRET``.

    The session window bounds the hours the socket is attempted in. DNSE serves
    the endpoint only while the exchange is open — out of hours the hostname
    stops resolving — so the window is what keeps an overnight worker from
    retrying a dead DNS name every few seconds. Widen it with
    ``DNSE_WS_SESSION_START`` / ``DNSE_WS_SESSION_END``, or set
    ``DNSE_WS_SESSION_GATE=0`` to connect around the clock regardless.
    """

    base_url: str
    api_key: str
    api_secret: str
    boards: list[str]
    encoding: str
    session_tz: str
    session_start: str
    session_end: str
    session_gate: bool

    @classmethod
    def from_env(cls) -> "DnseWsConfig":
        # Defaults to G1 alone — the board TICK_ALLOWED_BOARDS stores anyway, so
        # a wider subscription only bought the per-board diagnostic. G1 carries
        # derivatives as well as equities, so the VN30F contract still arrives.
        # Widen with DNSE_TRADE_BOARDS (dnse_ws_input.ALL_BOARDS is the full set).
        boards_str = os.getenv("DNSE_TRADE_BOARDS", "")
        boards = (
            [b.strip() for b in boards_str.split(",") if b.strip()]
            if boards_str
            else list(DEFAULT_TRADE_BOARDS)
        )
        return cls(
            base_url=os.getenv("DNSE_WS_URL", "wss://ws-openapi.dnse.com.vn"),
            api_key=os.getenv("DNSE_API_KEY", ""),
            api_secret=os.getenv("DNSE_API_SECRET", ""),
            boards=boards,
            encoding=os.getenv("DNSE_WS_ENCODING", "json"),
            # Wider than TICK_SESSION_START/END (09:00-15:00): those bound
            # continuous trading, while this only has to cover the hours the feed
            # is actually served — pre-open auction through post-close run-off.
            session_tz=os.getenv(
                "DNSE_WS_SESSION_TZ", os.getenv("EXCHANGE_TZ", "Asia/Ho_Chi_Minh")
            ),
            session_start=os.getenv("DNSE_WS_SESSION_START", "08:00"),
            session_end=os.getenv("DNSE_WS_SESSION_END", "16:00"),
            session_gate=os.getenv("DNSE_WS_SESSION_GATE", "1")
            not in ("0", "false", "False"),
        )


@dataclass
class MQTTConfig:
    """MQTT broker configuration."""

    host: str
    port: int
    topics: list[str]

    @classmethod
    def from_env(cls) -> "MQTTConfig":
        """Load MQTT configuration from environment variables."""
        # Default MQTT configuration
        host = os.getenv("MQTT_HOST", "datafeed-lts-krx.dnse.com.vn")
        port = int(os.getenv("MQTT_PORT", "443"))

        # Parse topics from environment or load from watchlist
        topics_str = os.getenv("MQTT_TOPICS", "")
        if topics_str:
            topics = [t.strip() for t in topics_str.split(",") if t.strip()]
        else:
            # Load topics from watchlist.json
            topics = cls._load_topics_from_watchlist()

        return cls(host=host, port=port, topics=topics)

    @staticmethod
    def _load_topics_from_watchlist() -> list[str]:
        """Load symbols from watchlist.json and generate MQTT topics."""
        # Get watchlist file path from env or use default
        watchlist_path = os.getenv("MQTT_WATCHLIST_FILE", "watchlist.json")

        # Convert to absolute path if relative
        if not os.path.isabs(watchlist_path):
            # Assume it's relative to the worker directory
            base_dir = Path(__file__).parent
            watchlist_path = base_dir / watchlist_path

        try:
            with open(watchlist_path, "r") as f:
                data = json.load(f)
                symbols = data.get("symbols", [])

                # Get topic template from env or use default
                topic_template = os.getenv(
                    "MQTT_TOPIC_TEMPLATE",
                    "plaintext/quotes/krx/mdds/tick/v1/roundlot/symbol/{symbol}",
                )

                # Generate topics for all symbols
                topics = [topic_template.format(symbol=symbol) for symbol in symbols]
                return topics

        except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
            print(f"Warning: Could not load watchlist from {watchlist_path}: {e}")
            return []


@dataclass
class TelegramConfig:
    """Telegram bot configuration for notifications."""

    bot_token: str
    chat_id: str
    enabled: bool

    @classmethod
    def from_env(cls) -> "TelegramConfig":
        """Load Telegram configuration from environment variables."""
        return cls(
            bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            enabled=os.getenv("TELEGRAM_ENABLED", "0") in ("1", "true", "True"),
        )


@dataclass
class PriceAlertConfig:
    """Price alert worker configuration.

    The alerts live in MySQL (``my_portfolio.price_alerts``) since the backend
    moved off the single-file ``portfolio.db``. The worker only reads them, but
    it has to read the same rows the API writes — pointed at the retired SQLite
    file it would see an empty table and silently never fire an alert.

    Connection: ``MYSQL_HOST/PORT/USER/PASSWORD/DB``, the same variables the
    backend uses.
    """

    mysql_host: str
    mysql_port: int
    mysql_user: str
    mysql_password: str
    mysql_db: str
    check_interval_seconds: float
    rate_limit_seconds: int

    @property
    def endpoint(self) -> str:
        """``host:port/db``, for startup logging. Never includes the password."""
        return f"{self.mysql_host}:{self.mysql_port}/{self.mysql_db}"

    @classmethod
    def from_env(cls) -> "PriceAlertConfig":
        """Load price alert configuration from environment variables."""
        return cls(
            mysql_host=os.getenv("MYSQL_HOST", "192.168.1.3"),
            mysql_port=int(os.getenv("MYSQL_PORT", "3306")),
            mysql_user=os.getenv("MYSQL_USER", "root"),
            mysql_password=os.getenv("MYSQL_PASSWORD", "kyostyle1"),
            mysql_db=os.getenv("MYSQL_DB", "my_portfolio"),
            check_interval_seconds=float(
                os.getenv("PRICE_ALERT_CHECK_INTERVAL", "1.0")
            ),
            rate_limit_seconds=int(os.getenv("PRICE_ALERT_RATE_LIMIT", "60")),
        )


@dataclass
class TickSyncConfig:
    """Tick data synchronization configuration."""

    symbol: str
    board: int
    session_tz: str
    session_start_str: str
    session_end_str: str
    dry_run: bool
    # Order books whose trades may be stored in `ticks`, as bare ids ("G1").
    # Empty = store every board. See TICK_ALLOWED_BOARDS in from_env.
    allowed_boards: frozenset[str]

    @property
    def session_start(self) -> dtime:
        """Parse session start time."""
        start_str, _ = ISPConfig._parse_session(
            f"{self.session_start_str},{self.session_end_str}"
        )
        return start_str

    @property
    def session_end(self) -> dtime:
        """Parse session end time."""
        _, end_str = ISPConfig._parse_session(
            f"{self.session_start_str},{self.session_end_str}"
        )
        return end_str

    @classmethod
    def from_env(cls) -> "TickSyncConfig":
        """Load tick sync configuration from environment variables."""
        symbol = os.getenv("TICK_SYMBOL") or _current_vn30f_symbol()
        if not symbol:
            raise ValueError("TICK_SYMBOL must not be empty")

        board = int(os.getenv("TICK_BOARD", "2"))
        session_tz = os.getenv("EXCHANGE_TZ", "Asia/Ho_Chi_Minh")
        session_start_str = os.getenv("TICK_SESSION_START", "09:00")
        session_end_str = os.getenv("TICK_SESSION_END", "15:00")
        dry_run = os.getenv("TICK_DRY_RUN", "0") in ("1", "true", "True")

        # Default G1 only: the main continuous order book. Odd lot (G4/G7) and
        # put-through (T1..T6) print at prices set outside continuous trading —
        # the backend already excludes them when picking a quote — so storing
        # them in the same table would distort any bar or VWAP built from it.
        # Widen with TICK_ALLOWED_BOARDS=G1,G7,G4; set it empty to store all.
        boards_str = os.getenv("TICK_ALLOWED_BOARDS", "G1")
        allowed_boards = frozenset(
            b.strip().upper() for b in boards_str.split(",") if b.strip()
        )

        return cls(
            symbol=symbol,
            board=board,
            session_tz=session_tz,
            session_start_str=session_start_str,
            session_end_str=session_end_str,
            dry_run=dry_run,
            allowed_boards=allowed_boards,
        )


@dataclass
class ReconcilerConfig:
    """Reconciler worker configuration."""

    api_url: str
    request_delay: float
    page_limit: int
    max_retries: int
    reconciler_hour: int
    force_rerun: bool

    @classmethod
    def from_env(cls) -> "ReconcilerConfig":
        """Load reconciler configuration from environment variables."""
        return cls(
            api_url=os.getenv(
                "DNSE_API_URL", "https://api.dnse.com.vn/price-api/query"
            ),
            request_delay=float(os.getenv("RECONCILER_REQUEST_DELAY", "0.1")),
            page_limit=int(os.getenv("RECONCILER_PAGE_LIMIT", "100000")),
            max_retries=int(os.getenv("RECONCILER_MAX_RETRIES", "1")),
            reconciler_hour=int(os.getenv("RECONCILER_HOUR", "15")),
            force_rerun=os.getenv("RECONCILER_FORCE_RERUN", "0")
            in ("1", "true", "True"),
        )


@dataclass
class HawkesConfig:
    """Hawkes BSI live signal worker configuration."""

    symbol: str
    poll_interval: int
    kappa: float
    quantile_lookback: int
    q_lo_pct: float
    q_hi_pct: float
    kama_period: int
    allow_short: bool
    sl_bars: int
    calm_bars: int
    calm_threshold: float
    state_path: str
    ohlc_table: str
    ticks_table: str
    alert_exits: bool

    @classmethod
    def from_env(cls) -> "HawkesConfig":
        return cls(
            symbol=os.getenv("HAWKES_SYMBOL", "VN30F1M"),
            poll_interval=int(os.getenv("HAWKES_POLL_INTERVAL", "60")),
            kappa=float(os.getenv("HAWKES_KAPPA", "0.1")),
            quantile_lookback=int(os.getenv("HAWKES_QUANTILE_LOOKBACK", "100")),
            q_lo_pct=float(os.getenv("HAWKES_Q_LO_PCT", "5.0")),
            q_hi_pct=float(os.getenv("HAWKES_Q_HI_PCT", "95.0")),
            kama_period=int(os.getenv("HAWKES_KAMA_PERIOD", "10")),
            allow_short=_parse_bool(os.getenv("HAWKES_ALLOW_SHORT"), default=True),
            sl_bars=int(os.getenv("HAWKES_SL_BARS", "10")),
            calm_bars=int(os.getenv("HAWKES_CALM_BARS", "5")),
            calm_threshold=float(os.getenv("HAWKES_CALM_THRESHOLD", "500.0")),
            state_path=os.getenv("HAWKES_STATE_PATH", "./.state/hawkes_signal_state.json"),
            ohlc_table=os.getenv("CLICKHOUSE_OHLC_5M_TABLE", "ohlc_5m"),
            ticks_table=os.getenv("CLICKHOUSE_TICKS_TABLE", "ticks"),
            alert_exits=_parse_bool(os.getenv("HAWKES_ALERT_EXITS"), default=False),
        )


@dataclass
class LargeOrderConfig:
    """Large-order ("Layer 3") pipeline configuration.

    Tracks the whole watchlist but keeps only trades whose notional value
    (match_price * match_qty) clears ``min_dollar_value``.
    """

    min_dollar_value: float
    table: str
    watchlist_file: str
    board: int
    session_tz: str
    session_start_str: str
    session_end_str: str
    request_delay: float
    window_seconds: int
    wait_seconds: float
    exclude_auctions: bool
    auction_windows: list[tuple[dtime, dtime]]

    @staticmethod
    def _parse_time(s: str) -> dtime:
        parts = [int(x) for x in s.strip().split(":")]
        while len(parts) < 3:
            parts.append(0)
        return dtime(parts[0], parts[1], parts[2])

    @classmethod
    def _parse_window(cls, s: str) -> tuple[dtime, dtime]:
        start_str, end_str = [p.strip() for p in s.split(",")]
        return cls._parse_time(start_str), cls._parse_time(end_str)

    @property
    def session_start(self) -> dtime:
        start_str, _ = ISPConfig._parse_session(
            f"{self.session_start_str},{self.session_end_str}"
        )
        return start_str

    @property
    def session_end(self) -> dtime:
        _, end_str = ISPConfig._parse_session(
            f"{self.session_start_str},{self.session_end_str}"
        )
        return end_str

    @classmethod
    def from_env(cls) -> "LargeOrderConfig":
        # Auction (ATO/ATC) windows — trades inside these clear at a single
        # auction price and are dropped so they don't form fake blocks.
        exclude_auctions = _parse_bool(
            os.getenv("LARGE_ORDER_EXCLUDE_AUCTIONS"), default=True
        )
        auction_windows: list[tuple[dtime, dtime]] = []
        if exclude_auctions:
            auction_windows = [
                cls._parse_window(os.getenv("LARGE_ORDER_ATO_WINDOW", "09:00:00,09:15:00")),
                cls._parse_window(os.getenv("LARGE_ORDER_ATC_WINDOW", "14:30:00,15:00:00")),
            ]

        return cls(
            min_dollar_value=float(os.getenv("LARGE_ORDER_MIN_VALUE", "500000")),
            table=os.getenv("CLICKHOUSE_LARGE_ORDERS_TABLE", "large_orders"),
            watchlist_file=os.getenv("LARGE_ORDER_WATCHLIST_FILE", "watchlist.json"),
            board=int(os.getenv("LARGE_ORDER_BOARD", os.getenv("TICK_BOARD", "2"))),
            session_tz=os.getenv("EXCHANGE_TZ", "Asia/Ho_Chi_Minh"),
            session_start_str=os.getenv("TICK_SESSION_START", "09:00"),
            session_end_str=os.getenv("TICK_SESSION_END", "15:00"),
            request_delay=float(os.getenv("LARGE_ORDER_REQUEST_DELAY", "0.1")),
            # Block aggregation: bucket width and how long the live windower
            # waits (system time) for late/out-of-order ticks before closing.
            window_seconds=int(os.getenv("LARGE_ORDER_WINDOW_SECONDS", "1")),
            wait_seconds=float(os.getenv("LARGE_ORDER_WAIT_SECONDS", "2")),
            exclude_auctions=exclude_auctions,
            auction_windows=auction_windows,
        )


@dataclass
class TradeFlowConfig:
    """Trade-flow feature and anomaly-scoring configuration.

    Symbol scope, session window and auction windows are reused from
    ``LargeOrderConfig`` (same watchlist tape); these fields cover only the
    feature window and the scoring knobs.

    ``window_seconds`` is read by the worker when it builds the
    ``trade_flow_windows`` view; the rest are read by the backend, which does
    the scoring. The env names keep the ``BLOCK_EP_`` prefix because the backend
    reads the same variables and they are already deployed — renaming them would
    silently fall back to defaults on every existing environment.
    """

    window_seconds: int
    tod_bucket_minutes: int
    min_windows_to_fit: int
    contamination: float

    @classmethod
    def from_env(cls) -> "TradeFlowConfig":
        return cls(
            # Feature window. The 1-second bars are window-agnostic, so changing
            # this only needs the window view recreated, not a backfill.
            window_seconds=int(os.getenv("BLOCK_EP_WINDOW_SECONDS", "30")),
            # Robust normalization bucket: features are compared against the same
            # symbol at the same time of day, because 09:15 and 13:45 behave
            # differently and an illiquid ticker is not comparable to HPG.
            tod_bucket_minutes=int(os.getenv("BLOCK_EP_TOD_BUCKET_MINUTES", "30")),
            min_windows_to_fit=int(os.getenv("BLOCK_EP_MIN_WINDOWS_TO_FIT", "200")),
            # Isolation Forest expected outlier fraction.
            contamination=float(os.getenv("BLOCK_EP_CONTAMINATION", "0.01")),
        )


@dataclass
class IngestTuningConfig:
    """ClickHouse ingestion tuning for the streaming workers.

    Two levers, per ClickHouse's ingestion guidance:

    * **Client-side batching** — ``batch_max_size`` / ``batch_timeout_seconds``
      feed ``op.collect``. Whichever limit is hit first flushes the block. The
      size cap is deliberately large so bursts build big blocks; in practice
      this tape flushes on the timeout, which is why async inserts matter.
    * **Server-side buffering** — ``async_insert`` lets ClickHouse coalesce our
      per-flush blocks into larger parts itself, the documented remedy for
      clients that cannot reach ~100k rows per insert.

    ``wait_for_async_insert=False`` makes inserts fire-and-forget: the server
    acknowledges before the data is durably written, so a crash can lose the
    in-flight buffer and insert-time errors never reach the client. That is a
    deliberate trade for a tick archive that the daily reconciler back-fills
    from the authoritative API; set ``INGEST_WAIT_FOR_ASYNC_INSERT=1`` if you
    would rather have durability confirmation than throughput.
    """

    batch_max_size: int
    batch_timeout_seconds: float
    async_insert: bool
    wait_for_async_insert: bool
    async_insert_busy_timeout_ms: int
    async_insert_max_data_size: int

    @classmethod
    def from_env(cls) -> "IngestTuningConfig":
        return cls(
            batch_max_size=int(os.getenv("INGEST_BATCH_MAX_SIZE", "100000")),
            batch_timeout_seconds=float(
                os.getenv("INGEST_BATCH_TIMEOUT_SECONDS", "2.0")
            ),
            async_insert=_parse_bool(os.getenv("INGEST_ASYNC_INSERT"), default=True),
            wait_for_async_insert=_parse_bool(
                os.getenv("INGEST_WAIT_FOR_ASYNC_INSERT"), default=False
            ),
            async_insert_busy_timeout_ms=int(
                os.getenv("INGEST_ASYNC_BUSY_TIMEOUT_MS", "1000")
            ),
            async_insert_max_data_size=int(
                os.getenv("INGEST_ASYNC_MAX_DATA_SIZE", "10485760")
            ),
        )

    def insert_settings(self) -> dict:
        """clickhouse-connect ``settings`` for one INSERT."""
        # buffer_size=0 streams the Arrow block straight out, as the upstream
        # bytewax sink does.
        settings: dict = {"buffer_size": 0}
        if self.async_insert:
            settings.update(
                async_insert=1,
                wait_for_async_insert=1 if self.wait_for_async_insert else 0,
                async_insert_busy_timeout_ms=self.async_insert_busy_timeout_ms,
                async_insert_max_data_size=self.async_insert_max_data_size,
            )
        return settings


@dataclass
class Config:
    """Main configuration container."""

    isp: ISPConfig
    clickhouse: ClickHouseConfig
    mock: MockConfig
    mqtt: MQTTConfig
    dnse_ws: DnseWsConfig
    telegram: TelegramConfig
    price_alert: PriceAlertConfig
    tick_sync: TickSyncConfig
    reconciler: ReconcilerConfig
    hawkes: HawkesConfig
    large_order: LargeOrderConfig
    trade_flow: TradeFlowConfig
    ingest: IngestTuningConfig

    @classmethod
    def load(cls) -> "Config":
        """Load all configuration from environment variables."""
        return cls(
            isp=ISPConfig.from_env(),
            clickhouse=ClickHouseConfig.from_env(),
            mock=MockConfig.from_env(),
            mqtt=MQTTConfig.from_env(),
            dnse_ws=DnseWsConfig.from_env(),
            telegram=TelegramConfig.from_env(),
            price_alert=PriceAlertConfig.from_env(),
            tick_sync=TickSyncConfig.from_env(),
            reconciler=ReconcilerConfig.from_env(),
            hawkes=HawkesConfig.from_env(),
            large_order=LargeOrderConfig.from_env(),
            trade_flow=TradeFlowConfig.from_env(),
            ingest=IngestTuningConfig.from_env(),
        )


# Global config instance
config = Config.load()
