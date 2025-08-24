import os
from typing import List
from pydantic import AnyHttpUrl
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    project_name: str = "Investment Tracker API"
    api_v1_prefix: str = "/api/v1"
    environment: str = "development"

    # Database
    database_url: str = os.getenv(
        "DATABASE_URL",
        "sqlite:///./portfolio.db"  # SQLite file in current directory by default
    )

    # CORS
    backend_cors_origins: List[AnyHttpUrl | str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost",
        "http://127.0.0.1",
        "*",
    ]

    # Delta Lake
    minio_access_key: str = os.getenv("MINIO_ACCESS_KEY", "")
    minio_secret_key: str = os.getenv("MINIO_SECRET_KEY", "")
    minio_endpoint: str = os.getenv("MINIO_ENDPOINT", "localhost:9000")
    stocks_delta_table: str = os.getenv("STOCKS_DELTA_TABLE", "s3://delta-table-storage/stocks")
    wichart_report_delta_table: str = os.getenv("WICHART_REPORT_DELTA_TABLE", "s3://delta-table-storage/raw_wichart_report")
    stocks_feature_store: str = os.getenv("STOCKS_FEATURE_STORE", "s3://delta-table-storage/stocks_feature_store")
    model_path: str = os.getenv("MODEL_PATH", "models")
    xgb_model_path: str = os.getenv("XGB_MODEL_PATH", "models/xgboost_model_05_19_2025.ubj")
    lgb_model_path: str = os.getenv("LGB_MODEL_PATH", "models/lightgbm_model_05_19_2025.ubj")
    catboost_model_path: str = os.getenv("CATBOOST_MODEL_PATH", "models/catboost_model_05_19_2025.cbm")

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
