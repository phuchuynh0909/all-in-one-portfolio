import os
import time as time_module
from datetime import datetime, timedelta
from typing import List, Optional

import clickhouse_connect
import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds
from deltalake import DeltaTable
from fastapi_cache.decorator import cache
from loguru import logger
from sqlalchemy.orm import Session

from app.core.settings import settings
from app.schemas.sector import SectorTimeseries, SectorTimeseriesData
from app.schemas.timeseries import Indicators, IndicatorParams, IndicatorsOnlyResponse, TimeseriesResponse

from .timeseries_indicators import compute_stock_indicators
from .utils import convert_nans

def _delta_storage_options() -> dict:
    return {
        "AWS_ACCESS_KEY_ID": settings.minio_access_key,
        "AWS_SECRET_ACCESS_KEY": settings.minio_secret_key,
        "AWS_ENDPOINT_URL": f"http://{settings.minio_endpoint}",
        "AWS_ALLOW_HTTP": "true",
        "AWS_EC2_METADATA_DISABLED": "true",
        "AWS_REGION": "us-east-1",
        "aws_conditional_put": "etag",
    }

# Cached DeltaTable instances with TTL
_delta_table_cache: dict = {}
_CACHE_TTL_SECONDS = 300  # 5 minutes

DEFAULT_STOCK_COLUMNS = ["date", "open", "high", "low", "close", "volume", "symbol"]


def _clickhouse_client():
    return clickhouse_connect.get_client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password,
        database=settings.clickhouse_db,
    )


def _get_cached_delta_table(table_path: str) -> DeltaTable:
    """Get a cached DeltaTable instance, refreshing if expired."""
    now = time_module.time()
    cache_entry = _delta_table_cache.get(table_path)
    
    if cache_entry is not None:
        dt, cached_at = cache_entry
        if now - cached_at < _CACHE_TTL_SECONDS:
            # Update incremental to pick up new data without full reload
            try:
                dt.update_incremental()
            except Exception:
                pass  # Ignore update errors, use cached version
            return dt
    
    # Create new instance and cache it
    dt = DeltaTable(table_path, storage_options=_delta_storage_options())
    _delta_table_cache[table_path] = (dt, now)
    return dt


def _build_filter(symbols: list | None, start: datetime | None, end: datetime | None):
    expr = None
    try:
        if start is not None:
            e = ds.field("date") >= pa.scalar(pd.Timestamp(start).to_pydatetime())
            expr = e if expr is None else (expr & e)
        if end is not None:
            e = ds.field("date") <= pa.scalar(pd.Timestamp(end).to_pydatetime())
            expr = e if expr is None else (expr & e)
        if symbols:
            e = ds.field("symbol").isin(list(symbols))
            expr = e if expr is None else (expr & e)
    except Exception:
        return None
    return expr


def _load_delta_stocks(
    *,
    symbols: list | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    columns: list | None = None,
) -> pd.DataFrame:
    import time
    total_start = time.perf_counter()
    
    # Load watchlist if no symbols provided
    if not symbols:
        watchlist_path = os.path.join("models", "watchlist.csv")
        if os.path.exists(watchlist_path):
            with open(watchlist_path, 'r') as f:
                # Exclude index symbols (VNINDEX, VN30) from stock list
                exclude_symbols = {"VNINDEX", "VN30"}
                symbols = [line.strip() for line in f if line.strip() and line.strip() not in exclude_symbols]
            logger.info(f"Loaded {len(symbols)} symbols from watchlist (excluding indices)")
        else:
            logger.warning(f"Watchlist not found at {watchlist_path}, using all available symbols")
            symbols = None

    query_columns = columns or DEFAULT_STOCK_COLUMNS
    required_cols = ["date", "symbol"]
    selected_cols = list(dict.fromkeys([*query_columns, *required_cols]))

    conditions: list[str] = []
    if start is not None:
        conditions.append(f"date >= toDate('{pd.Timestamp(start).strftime('%Y-%m-%d')}')")
    if end is not None:
        conditions.append(f"date <= toDate('{pd.Timestamp(end).strftime('%Y-%m-%d')}')")
    if symbols:
        sanitized_symbols = [s.replace("'", "''") for s in symbols]
        symbol_list = ", ".join([f"'{symbol}'" for symbol in sanitized_symbols])
        conditions.append(f"symbol IN ({symbol_list})")

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    order_clause = "ORDER BY symbol, date"

    table_name = os.getenv("CLICKHOUSE_OHLC_EOD_TABLE", "ohlc_eod")
    sql = f"SELECT {', '.join(selected_cols)} FROM {settings.clickhouse_db}.{table_name} FINAL {where_clause} {order_clause}"
    logger.debug(f"[PERF] clickhouse query: {sql}")
    t0 = time.perf_counter()
    client = _clickhouse_client()
    try:
        result = client.query(sql)
        pdf = pd.DataFrame(result.result_rows, columns=result.column_names)
        # Deduplicate by (symbol, date), keeping last occurrence
        if "symbol" in pdf.columns and "date" in pdf.columns:
            pdf = pdf.drop_duplicates(subset=["symbol", "date"], keep="last")
    finally:
        client.close()
    logger.debug(f"[PERF] clickhouse query: {(time.perf_counter() - t0) * 1000:.2f}ms, rows={len(pdf)}")
    
    if pdf.empty:
        logger.debug(f"[PERF] Total: {(time.perf_counter() - total_start) * 1000:.2f}ms (empty)")
        return pdf
    
    t0 = time.perf_counter()
    if "date" in pdf.columns:
        pdf["date"] = pd.to_datetime(pdf["date"])
    if columns:
        missing_cols = [col for col in columns if col not in pdf.columns]
        if missing_cols:
            logger.warning(f"[CLICKHOUSE] Missing columns in result: {missing_cols}")
        available_cols = [col for col in columns if col in pdf.columns]
        pdf = pdf[available_cols]
    if "symbol" in pdf.columns and "date" in pdf.columns:
        pdf = pdf.sort_values(["symbol", "date"]).reset_index(drop=True)
    elif "symbol" in pdf.columns:
        pdf = pdf.sort_values(["symbol"]).reset_index(drop=True)
    logger.debug(f"[PERF] post-processing: {(time.perf_counter() - t0) * 1000:.2f}ms")
    
    logger.debug(f"[PERF] Total _load_delta_stocks: {(time.perf_counter() - total_start) * 1000:.2f}ms")
    return pdf


def _load_feature_store(
    symbols: list | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> pd.DataFrame:
    dt = _get_cached_delta_table(settings.stocks_feature_store)
    dataset = dt.to_pyarrow_dataset()
    filt = _build_filter(symbols, start, end)
    try:
        table = dataset.to_table(filter=filt)
    except Exception:
        table = dataset.to_table()
    pdf = table.to_pandas()
    return pdf

@cache(expire=300)  # Cache for 5 minutes
async def get_current_price(ticker: str) -> Optional[float]:
    """Latest EOD close for ``ticker`` from ClickHouse ``ohlc_eod`` (or ``CLICKHOUSE_OHLC_EOD_TABLE``)."""
    if not ticker or not str(ticker).strip():
        return None
    sym = str(ticker).strip().replace("'", "''")
    table_name = os.getenv("CLICKHOUSE_OHLC_EOD_TABLE", "ohlc_eod")
    sql = (
        f"SELECT close FROM {settings.clickhouse_db}.{table_name} FINAL "
        f"WHERE symbol = '{sym}' ORDER BY date DESC LIMIT 1"
    )
    try:
        client = _clickhouse_client()
        try:
            result = client.query(sql)
            if not result.result_rows:
                return None
            val = result.result_rows[0][0]
            if val is None:
                return None
            return float(val)
        finally:
            client.close()
    except Exception as e:
        logger.warning("Error getting current price for {} from ClickHouse: {}", ticker, e)
        return None

async def get_stock_timeseries(
    symbol: str,
    interval: str = "1d",
    indicators: List[IndicatorParams] = [],
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> TimeseriesResponse:
    """Get stock timeseries data with optional indicators."""
    try:
        # Load data from Delta Lake using PyArrow dataset for faster predicate pushdown
        start = datetime.strptime(start_date, "%Y-%m-%d") if start_date else None
        end = datetime.strptime(end_date, "%Y-%m-%d") if end_date else None
        
        df = _load_delta_stocks(
            symbols=[symbol],
            start=start,
            end=end,
            columns=["date", "open", "high", "low", "close", "volume", "symbol"]
        )
        
        # Filter to exact symbol (in case of case sensitivity issues)
        df = df[df["symbol"] == symbol].drop(columns=["symbol"])
        
        if df.empty:
            raise ValueError(f"No data found for symbol {symbol}")

        indicator_data = compute_stock_indicators(df, indicators)

        return TimeseriesResponse(
            symbol=symbol,
            interval=interval,
            timestamps=df["date"].dt.strftime("%Y-%m-%d").tolist(),
            timeseries={
                "open": df["open"].tolist(),
                "high": df["high"].tolist(),
                "low": df["low"].tolist(),
                "close": df["close"].tolist(),
                "volume": df["volume"].tolist(),
            },
            indicators=Indicators(**indicator_data) if indicator_data else None
        )
    except Exception as e:
        logger.error("Error getting timeseries data for {}: {}", symbol, e)
        raise

async def get_stock_indicators(
    symbol: str,
    indicators: List[IndicatorParams],
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> IndicatorsOnlyResponse:
    """Get stock indicators only (without OHLCV data)."""
    try:
        # Load data from Delta Lake using PyArrow dataset for faster predicate pushdown
        start = datetime.strptime(start_date, "%Y-%m-%d") if start_date else None
        end = datetime.strptime(end_date, "%Y-%m-%d") if end_date else None
        
        df = _load_delta_stocks(
            symbols=[symbol],
            start=start,
            end=end,
            columns=["date", "open", "high", "low", "close", "volume", "symbol"]
        )
        
        # Filter to exact symbol
        df = df[df["symbol"] == symbol].drop(columns=["symbol"])
        
        if df.empty:
            raise ValueError(f"No data found for symbol {symbol}")

        indicator_data = compute_stock_indicators(df, indicators)

        return IndicatorsOnlyResponse(
            symbol=symbol,
            interval="1d",
            timestamps=df["date"].dt.strftime("%Y-%m-%d").tolist(),
            indicators=Indicators(**indicator_data)
        )
    except Exception as e:
        logger.error(f"Error getting indicators for {symbol}: {e}")
        raise


def _create_empty_sector_timeseries(sector_level: str) -> SectorTimeseries:
    """Create an empty SectorTimeseries response."""
    return SectorTimeseries(
        sector_level=sector_level,
        interval="1d",
        meta={},
        timestamps=[],
        sector_data=[]
    )


def _get_sectors_from_db(db: Session, sector_level: int) -> List:
    """Get all sectors at the specified level from database."""
    from app.db.models.market import Sector
    return db.query(Sector).filter(Sector.level == sector_level).all()


def _process_ohlc_data_for_sectors(sectors: List, sector_level: str) -> SectorTimeseries:
    """Process OHLC data for multiple sectors using a single query."""
    try:
        dt = _get_cached_delta_table(settings.stocks_delta_table)
        
        # Prepare sector data with 4-digit padding transformation
        padded_sectors = [(f"{sector.id:04d}", sector.id, sector.name) for sector in sectors]
        sector_ids = [padded_id for padded_id, _, _ in padded_sectors]
        sector_info_map = {padded_id: (original_id, name) for padded_id, original_id, name in padded_sectors}
        
        print(f"Querying OHLC data for {len(sector_ids)} sectors in single query")
        
        # Single query for all sectors
        all_sectors_pdf = dt.to_pyarrow_table(
            filters=[("symbol", "in", sector_ids)]
        ).to_pandas()
        
        if all_sectors_pdf.empty:
            print("No OHLC data found for any sectors")
            return _create_empty_sector_timeseries(sector_level)
        
        return _build_sector_timeseries_from_dataframe(
            all_sectors_pdf, sector_ids, sector_info_map, sector_level
        )
        
    except Exception as e:
        print(f"Error accessing OHLC delta table: {e}")
        return _create_empty_sector_timeseries(sector_level)


def _build_sector_timeseries_from_dataframe(
    df: pd.DataFrame, 
    sector_ids: List[str], 
    sector_info_map: dict,
    sector_level: str
) -> SectorTimeseries:
    """Build SectorTimeseries from processed dataframe."""
    # Process dataframe
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["symbol", "date"])
    
    # Collect timestamps
    timestamps = df["date"].dt.strftime('%Y-%m-%d').unique()
    sorted_timestamps = sorted(timestamps.tolist())
    
    # Create sector data
    sectors_data = []
    for sector_id in sector_ids:
        sector_df = df[df["symbol"] == sector_id]
        
        if not sector_df.empty:
            original_id, name = sector_info_map[sector_id]
            sector_data = SectorTimeseriesData(
                id=original_id,
                name=name,
                data=sector_df["close"].tolist()
            )
            sectors_data.append(sector_data)
            print(f"Added data for {name}: {len(sector_df)} records")
        else:
            _, name = sector_info_map[sector_id]
            print(f"No data found for {name} (ID: {sector_id})")
    
    return SectorTimeseries(
        sector_level=sector_level,
        interval="1d",
        meta={},
        timestamps=sorted_timestamps,
        sector_data=sectors_data
    )


def _get_sector_timeseries_from_delta_table(sector_level: str) -> SectorTimeseries:
    """Get sector timeseries from original delta table approach."""
    try:
        dt = _get_cached_delta_table(settings.sector_delta_table)
        pdf = dt.to_pyarrow_table(filters=[("sector_type", "==", int(sector_level))]).to_pandas()

        if pdf.empty:
            return _create_empty_sector_timeseries(sector_level)

        pdf["date"] = pd.to_datetime(pdf["date"])
        pdf = pdf.sort_values(["sector_name", "date"])
        
        # Convert to SectorTimeseries format
        timestamps = pdf["date"].dt.strftime('%Y-%m-%d').unique().tolist()
        
        # Group by sector and create data
        sectors_data = []
        for sector_name in pdf["sector_name"].unique():
            sector_df = pdf[pdf["sector_name"] == sector_name]
            sector_data = SectorTimeseriesData(
                id=sector_df["sector_id"].iloc[0],
                name=sector_name,
                data=sector_df["close"].tolist()
            )
            sectors_data.append(sector_data)
        
        return SectorTimeseries(
            sector_level=sector_level,
            interval="1d",
            meta={},
            timestamps=timestamps,
            sector_data=sectors_data
        )
        
    except Exception as e:
        print(f"Error with delta table approach: {e}")
        return _create_empty_sector_timeseries(sector_level)


async def get_sector_timeseries(
    sector_level: str,
    db: Session = None
) -> SectorTimeseries:
    """Get sector timeseries data with optional indicators."""
    from app.db.base import get_db
    
    print(f"Getting sector timeseries for level: {sector_level}")
    level = int(sector_level)
    
    # For levels 1 and 2, use database sectors with OHLC data
    if level in [1, 2]:
        if db is None:
            db = next(get_db())
        
        try:
            sectors = _get_sectors_from_db(db, level)
            print(f"Found {len(sectors)} sectors at level {sector_level}")
            
            if not sectors:
                return _create_empty_sector_timeseries(sector_level)
            
            return _process_ohlc_data_for_sectors(sectors, sector_level)
            
        except Exception as e:
            print(f"Error querying sector database: {e}")
            return _create_empty_sector_timeseries(sector_level)
    
    # For other levels, use original delta table approach
    return _get_sector_timeseries_from_delta_table(sector_level)


async def get_market_indicators(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
) -> dict:
    """
    Calculate market breadth indicators:
    - A/D Line (Advance-Decline Line): Cumulative sum of (advances - declines)
    - McClellan Oscillator: 19-day EMA - 39-day EMA of daily advance-decline values
    - McClellan Summation Index: Cumulative sum of McClellan Oscillator
    """
    # Load data from Delta Lake
    start = datetime.strptime(start_date, "%Y-%m-%d") if start_date else None
    end = datetime.strptime(end_date, "%Y-%m-%d") if end_date else None

    df = _load_delta_stocks(
        symbols=None,
        start=start,
        end=end,
        columns=["date", "close", "symbol"]
    )

    if df.empty:
        return {
            "timestamps": [],
            "ad_line": [],
            "mcclellan_oscillator": [],
            "mcclellan_summation": [],
            "advances": [],
            "declines": [],
            "unchanged": [],
        }

    # Sort by symbol and date for proper calculation
    df = df.sort_values(["symbol", "date"]).reset_index(drop=True)

    # Calculate daily change for each stock
    df["prev_close"] = df.groupby("symbol")["close"].shift(1)
    df["change"] = df["close"] - df["prev_close"]

    # Classify each stock-day as advancing, declining, or unchanged
    df["advancing"] = (df["change"] > 0).astype(int)
    df["declining"] = (df["change"] < 0).astype(int)
    df["unchanged"] = (df["change"] == 0).astype(int)

    # Group by date and count advances/declines
    daily_breadth = df.groupby("date").agg({
        "advancing": "sum",
        "declining": "sum",
        "unchanged": "sum",
    }).reset_index()

    daily_breadth = daily_breadth.sort_values("date").reset_index(drop=True)

    # Calculate Advance-Decline values
    daily_breadth["ad_value"] = daily_breadth["advancing"] - daily_breadth["declining"]

    # A/D Line: Cumulative sum of daily advance-decline values
    daily_breadth["ad_line"] = daily_breadth["ad_value"].cumsum()

    # McClellan Oscillator: 19-day EMA - 39-day EMA of AD values
    # Using the Ratio-Adjusted formula: (Advances - Declines) / (Advances + Declines) * 1000
    daily_breadth["ad_ratio"] = (
        (daily_breadth["advancing"] - daily_breadth["declining"]) /
        (daily_breadth["advancing"] + daily_breadth["declining"]).replace(0, 1)
    ) * 1000

    # Calculate EMAs for McClellan Oscillator
    ema_19 = daily_breadth["ad_ratio"].ewm(span=19, adjust=False).mean()
    ema_39 = daily_breadth["ad_ratio"].ewm(span=39, adjust=False).mean()
    daily_breadth["mcclellan_oscillator"] = ema_19 - ema_39

    # McClellan Summation Index: Cumulative sum of McClellan Oscillator
    daily_breadth["mcclellan_summation"] = daily_breadth["mcclellan_oscillator"].cumsum()

    # Convert to response format
    timestamps = daily_breadth["date"].dt.strftime("%Y-%m-%d").tolist()

    return {
        "timestamps": timestamps,
        "ad_line": convert_nans(daily_breadth["ad_line"].values),
        "mcclellan_oscillator": convert_nans(daily_breadth["mcclellan_oscillator"].values),
        "mcclellan_summation": convert_nans(daily_breadth["mcclellan_summation"].values),
        "advances": daily_breadth["advancing"].values,
        "declines": daily_breadth["declining"].values,
        "unchanged": daily_breadth["unchanged"].values,
    }
