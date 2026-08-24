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
    feed at ``wss://ws-openapi.dnse.com.vn/v1/stream`` (HMAC auth, JSON frames).
    Credentials come from ``DNSE_API_KEY`` / ``DNSE_API_SECRET``.
    """

    base_url: str
    api_key: str
    api_secret: str
    boards: list[str]
    encoding: str

    @classmethod
    def from_env(cls) -> "DnseWsConfig":
        boards_str = os.getenv("DNSE_TRADE_BOARDS", "")
        boards = (
            [b.strip() for b in boards_str.split(",") if b.strip()]
            if boards_str
            else ["G1", "G3", "G4", "G7", "T1", "T2", "T3", "T4", "T6"]
        )
        return cls(
            base_url=os.getenv("DNSE_WS_URL", "wss://ws-openapi.dnse.com.vn"),
            api_key=os.getenv("DNSE_API_KEY", ""),
            api_secret=os.getenv("DNSE_API_SECRET", ""),
            boards=boards,
            encoding=os.getenv("DNSE_WS_ENCODING", "json"),
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
    """Price alert worker configuration."""

    db_path: str
    check_interval_seconds: float
    rate_limit_seconds: int

    @classmethod
    def from_env(cls) -> "PriceAlertConfig":
        """Load price alert configuration from environment variables."""
        return cls(
            db_path=os.getenv("PRICE_ALERT_DB_PATH", "../backend/portfolio.db"),
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

        return cls(
            symbol=symbol,
            board=board,
            session_tz=session_tz,
            session_start_str=session_start_str,
            session_end_str=session_end_str,
            dry_run=dry_run,
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
class BlockEpisodeConfig:
    """Large-execution ("block episode") detection configuration.

    Symbol scope, session window, board and request pacing are reused from
    ``LargeOrderConfig`` (same watchlist tape). These fields only cover the
    statistical-detection knobs and the output table. ``detection_params``
    lazily builds the ``core.large_execution.DetectionParams`` used by the
    reconciler (numpy is imported there, not at config load time).
    """

    table: str
    rolling_seconds: int
    bin_seconds: int
    baseline_bins: int
    min_baseline_bins: int
    z_threshold: float
    imbalance_threshold: float
    min_trades_per_bin: int
    run_length: int
    large_print_quantile: float
    large_print_window: int
    large_print_min_prior: int
    episode_gap_bins: int

    @classmethod
    def from_env(cls) -> "BlockEpisodeConfig":
        return cls(
            table=os.getenv("CLICKHOUSE_BLOCK_EPISODES_TABLE", "block_episodes"),
            rolling_seconds=int(os.getenv("BLOCK_EP_ROLLING_SECONDS", "30")),
            bin_seconds=int(os.getenv("BLOCK_EP_BIN_SECONDS", "1")),
            baseline_bins=int(os.getenv("BLOCK_EP_BASELINE_BINS", "1800")),
            min_baseline_bins=int(os.getenv("BLOCK_EP_MIN_BASELINE_BINS", "300")),
            z_threshold=float(os.getenv("BLOCK_EP_Z_THRESHOLD", "2.5")),
            imbalance_threshold=float(os.getenv("BLOCK_EP_IMBALANCE_THRESHOLD", "0.70")),
            min_trades_per_bin=int(os.getenv("BLOCK_EP_MIN_TRADES_PER_BIN", "3")),
            run_length=int(os.getenv("BLOCK_EP_RUN_LENGTH", "2")),
            large_print_quantile=float(os.getenv("BLOCK_EP_LARGE_PRINT_QUANTILE", "0.99")),
            large_print_window=int(os.getenv("BLOCK_EP_LARGE_PRINT_WINDOW", "500")),
            large_print_min_prior=int(os.getenv("BLOCK_EP_LARGE_PRINT_MIN_PRIOR", "30")),
            episode_gap_bins=int(os.getenv("BLOCK_EP_EPISODE_GAP_BINS", "5")),
        )

    @property
    def detection_params(self):
        """Build the DetectionParams for core.large_execution.detect."""
        from core.large_execution import DetectionParams

        return DetectionParams(
            rolling_seconds=self.rolling_seconds,
            bin_seconds=self.bin_seconds,
            baseline_bins=self.baseline_bins,
            min_baseline_bins=self.min_baseline_bins,
            z_threshold=self.z_threshold,
            imbalance_threshold=self.imbalance_threshold,
            min_trades_per_bin=self.min_trades_per_bin,
            run_length=self.run_length,
            large_print_quantile=self.large_print_quantile,
            large_print_window=self.large_print_window,
            large_print_min_prior=self.large_print_min_prior,
            episode_gap_bins=self.episode_gap_bins,
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
    block_episode: BlockEpisodeConfig
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
            block_episode=BlockEpisodeConfig.from_env(),
            ingest=IngestTuningConfig.from_env(),
        )


# Global config instance
config = Config.load()
