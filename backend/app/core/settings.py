import os
from pathlib import Path
from typing import List
from urllib.parse import quote_plus
from pydantic import AnyHttpUrl
from pydantic_settings import BaseSettings


def _build_mysql_url(
    user: str | None = None,
    password: str | None = None,
    host: str | None = None,
    port: int | str | None = None,
    db: str | None = None,
) -> str:
    """Assemble the ``mysql+pymysql`` DSN from parts, env vars as the fallback.

    Shared by ``mysql_url`` and by the ``database_url`` default so the ORM and
    the report stores cannot drift onto different servers. utf8mb4 is not
    optional — the data is Vietnamese.
    """
    user = user if user is not None else os.getenv("MYSQL_USER", "root")
    password = password if password is not None else os.getenv("MYSQL_PASSWORD", "kyostyle1")
    host = host if host is not None else os.getenv("MYSQL_HOST", "192.168.1.3")
    port = port if port is not None else os.getenv("MYSQL_PORT", "3306")
    db = db if db is not None else os.getenv("MYSQL_DB", "my_portfolio")
    return (
        f"mysql+pymysql://{quote_plus(str(user))}:{quote_plus(str(password))}"
        f"@{host}:{port}/{db}?charset=utf8mb4"
    )


def _default_database_url() -> str:
    """The ORM's DSN: an explicit override, else the MySQL server.

    The app used to run on a local ``portfolio.db`` SQLite file. It now lives on
    MySQL (``my_portfolio``) alongside the report stores. Setting
    ``APP_DATABASE_URL``/``DATABASE_URL`` still points the ORM anywhere else —
    including back at ``sqlite:///app/portfolio.db`` — which is how the tests
    keep using a throwaway file.
    """
    override = os.getenv("APP_DATABASE_URL") or os.getenv("DATABASE_URL")
    return override or _build_mysql_url()


class Settings(BaseSettings):
    project_name: str = "Investment Tracker API"
    api_v1_prefix: str = "/api/v1"
    environment: str = "development"

    # Database — MySQL (``my_portfolio``); see ``_default_database_url``.
    database_url: str = _default_database_url()

    # ClickHouse
    clickhouse_host: str = os.getenv("CLICKHOUSE_HOST", "localhost")
    clickhouse_port: int = int(os.getenv("CLICKHOUSE_PORT", "9010"))
    clickhouse_user: str = os.getenv("CLICKHOUSE_USER", "myuser")
    clickhouse_password: str = os.getenv("CLICKHOUSE_PASSWORD", "mypassword")
    clickhouse_db: str = os.getenv("CLICKHOUSE_DB", "default")

    # MySQL — now the app's primary store. Holds the portfolio/market/financial
    # tables migrated off ``portfolio.db`` as well as the wichart report store:
    # the crawled feed (``raw_wichart_report``) and the enriched detail rows we
    # write back (``wichart_reports``: report_title, llm_summary, status …).
    mysql_host: str = os.getenv("MYSQL_HOST", "192.168.1.3")
    mysql_port: int = int(os.getenv("MYSQL_PORT", "3306"))
    mysql_user: str = os.getenv("MYSQL_USER", "root")
    mysql_password: str = os.getenv("MYSQL_PASSWORD", "kyostyle1")
    mysql_db: str = os.getenv("MYSQL_DB", "my_portfolio")

    # DNSE OpenAPI (real-time matched prices; secret must stay server-side)
    dnse_api_key: str = os.getenv("DNSE_API_KEY", "")
    dnse_api_secret: str = os.getenv("DNSE_API_SECRET", "")
    dnse_api_version: str = os.getenv("DNSE_API_VERSION", "2026-05-07")

    # CORS
    backend_cors_origins: List[AnyHttpUrl | str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost",
        "http://127.0.0.1",
        "http://localhost:8080",
        "https://phuchuynh.site",
        "https://www.phuchuynh.site",
        "https://api.phuchuynh.site",
        "http://api.phuchuynh.site",
        "*",
    ]

    # Delta Lake
    minio_access_key: str = os.getenv("MINIO_ACCESS_KEY", "")
    minio_secret_key: str = os.getenv("MINIO_SECRET_KEY", "")
    minio_endpoint: str = os.getenv("MINIO_ENDPOINT", "localhost:9000")
    stocks_delta_table: str = os.getenv("STOCKS_DELTA_TABLE", "s3://delta-table-storage/stocks")
    sector_delta_table: str = os.getenv("SECTOR_DELTA_TABLE", "s3://delta-table-storage/wichart_sector")
    wichart_report_delta_table: str = os.getenv("WICHART_REPORT_DELTA_TABLE", "s3://delta-table-storage/raw_wichart_report")
    wichart_report_detail_delta_table: str = os.getenv("WICHART_REPORT_DETAIL_DELTA_TABLE", "s3://delta-table-storage/wichart_reports")
    stocks_feature_store: str = os.getenv("STOCKS_FEATURE_STORE", "s3://delta-table-storage/stocks_feature_store")
    model_path: str = os.getenv("MODEL_PATH", "models")
    xgb_model_path: str = os.getenv("XGB_MODEL_PATH", "models/xgboost_model_05_19_2025.ubj")
    lgb_model_path: str = os.getenv("LGB_MODEL_PATH", "models/lightgbm_model_05_19_2025.ubj")
    catboost_model_path: str = os.getenv("CATBOOST_MODEL_PATH", "models/catboost_model_05_19_2025.cbm")

    @property
    def mysql_url(self) -> str:
        """SQLAlchemy URL for the MySQL store.

        ``MYSQL_URL`` overrides it wholesale (e.g. to point at a managed
        instance); otherwise it is built from the parts above.
        """
        override = os.getenv("MYSQL_URL")
        if override:
            return override
        return _build_mysql_url(
            user=self.mysql_user,
            password=self.mysql_password,
            host=self.mysql_host,
            port=self.mysql_port,
            db=self.mysql_db,
        )

    @property
    def delta_storage_options(self) -> dict:
        return {
            "AWS_ACCESS_KEY_ID": self.minio_access_key,
            "AWS_SECRET_ACCESS_KEY": self.minio_secret_key,
            "AWS_ENDPOINT_URL": f"http://{self.minio_endpoint}",
            "AWS_ALLOW_HTTP": "true",
            "AWS_EC2_METADATA_DISABLED": "true",
            "AWS_REGION": "us-east-1",
            "aws_conditional_put": "etag",
        }

    class Config:
        env_file = ".env"
        env_prefix = "APP_"


settings = Settings()
