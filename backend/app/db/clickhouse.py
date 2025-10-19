"""ClickHouse database connection and dependency."""
from typing import Generator
import clickhouse_connect
from clickhouse_connect.driver import Client

from app.core.settings import settings


def get_clickhouse_client() -> Generator[Client, None, None]:
    """
    Dependency to get ClickHouse client.
    
    Uses centralized settings configuration.
    
    Yields:
        ClickHouse client instance
    """
    client = clickhouse_connect.get_client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password,
        database=settings.clickhouse_db,
    )
    try:
        yield client
    finally:
        client.close()

