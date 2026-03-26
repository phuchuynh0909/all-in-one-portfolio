"""Configuration management for ISP worker."""

import os
import json
from pathlib import Path
from dataclasses import dataclass
from datetime import time as dtime
from dotenv import load_dotenv
from vn30f_symbol import current_symbol as _current_vn30f_symbol

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
            for s in os.getenv("ISP_MOCK_SYMBOLS", "ANV").split(",")
            if s.strip()
        ]
        print(symbols)
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
class Config:
    """Main configuration container."""

    isp: ISPConfig
    clickhouse: ClickHouseConfig
    mock: MockConfig
    mqtt: MQTTConfig
    telegram: TelegramConfig
    price_alert: PriceAlertConfig
    tick_sync: TickSyncConfig
    reconciler: ReconcilerConfig

    @classmethod
    def load(cls) -> "Config":
        """Load all configuration from environment variables."""
        return cls(
            isp=ISPConfig.from_env(),
            clickhouse=ClickHouseConfig.from_env(),
            mock=MockConfig.from_env(),
            mqtt=MQTTConfig.from_env(),
            telegram=TelegramConfig.from_env(),
            price_alert=PriceAlertConfig.from_env(),
            tick_sync=TickSyncConfig.from_env(),
            reconciler=ReconcilerConfig.from_env(),
        )


# Global config instance
config = Config.load()
