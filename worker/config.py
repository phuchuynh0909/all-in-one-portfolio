"""Configuration management for ISP worker."""
import os
import json
from pathlib import Path
from dataclasses import dataclass
from datetime import time as dtime
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


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
            int(x) for x in os.getenv("ISP_WINDOWS", "5,15,30,60").split(",") 
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

    @classmethod
    def from_env(cls) -> "ClickHouseConfig":
        """Load ClickHouse configuration from environment variables."""
        return cls(
            host=os.getenv("CLICKHOUSE_HOST", "localhost"),
            port=int(os.getenv("CLICKHOUSE_PORT", "9010")),
            user=os.getenv("CLICKHOUSE_USER", "myuser"),
            password=os.getenv("CLICKHOUSE_PASSWORD", "mypassword"),
            database=os.getenv("CLICKHOUSE_DB", "default"),
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
            s.strip() for s in os.getenv("ISP_MOCK_SYMBOLS", "ANV").split(",") 
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
            with open(watchlist_path, 'r') as f:
                data = json.load(f)
                symbols = data.get("symbols", [])

                # Get topic template from env or use default
                topic_template = os.getenv(
                    "MQTT_TOPIC_TEMPLATE",
                    "plaintext/quotes/krx/mdds/tick/v1/roundlot/symbol/{symbol}"
                )
                
                # Generate topics for all symbols
                topics = [topic_template.format(symbol=symbol) for symbol in symbols]
                return topics
                
        except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
            print(f"Warning: Could not load watchlist from {watchlist_path}: {e}")
            return []


@dataclass
class Config:
    """Main configuration container."""
    isp: ISPConfig
    clickhouse: ClickHouseConfig
    mock: MockConfig
    mqtt: MQTTConfig

    @classmethod
    def load(cls) -> "Config":
        """Load all configuration from environment variables."""
        return cls(
            isp=ISPConfig.from_env(),
            clickhouse=ClickHouseConfig.from_env(),
            mock=MockConfig.from_env(),
            mqtt=MQTTConfig.from_env(),
        )


# Global config instance
config = Config.load()

