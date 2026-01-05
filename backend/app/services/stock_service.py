from datetime import datetime
from typing import List, Optional, Tuple
from sqlalchemy.orm import Session
import os
from deltalake import DeltaTable
import deltalake
import pandas as pd
import pyarrow as pa
import numpy as np
import talib
from loguru import logger
from .indicators import trailing_sl, avwap, hawkes_BVC, kalman_zscore, calculate_yz_volatility, matrix_series
from .utils import convert_nans
from app.schemas.timeseries import TimeseriesResponse, Indicators, IndicatorParams, IndicatorsOnlyResponse
from app.schemas.sector import SectorTimeseries, SectorTimeseriesData
import pyarrow.dataset as ds
from app.stores.feature_store import FeatureStore
from datetime import datetime, date, timedelta
from fastapi_cache.decorator import cache
from app.core.settings import settings
import vectorbt as vbt
import time as time_module

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
    """Load OHLCV from Delta table using predicate pushdown via PyArrow filters."""
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

    t0 = time.perf_counter()
    dt = _get_cached_delta_table(settings.stocks_delta_table)
    logger.debug(f"[PERF] DeltaTable init (cached): {(time.perf_counter() - t0) * 1000:.2f}ms")
    
    logger.debug(f"[PERF] Partition columns: {dt.metadata().partition_columns}")
    
    t0 = time.perf_counter()
    dataset = dt.to_pyarrow_dataset()
    logger.debug(f"[PERF] to_pyarrow_dataset: {(time.perf_counter() - t0) * 1000:.2f}ms")
    
    filt = _build_filter(symbols, start, end)
    logger.debug(f"[PERF] Filter: {filt}")
    
    t0 = time.perf_counter()
    try:
        table = dataset.to_table(filter=filt, columns=columns)
    except Exception:
        table = dataset.to_table(columns=columns)
    logger.debug(f"[PERF] to_table (query): {(time.perf_counter() - t0) * 1000:.2f}ms, rows={table.num_rows}")
    
    t0 = time.perf_counter()
    pdf = table.to_pandas()
    logger.debug(f"[PERF] to_pandas: {(time.perf_counter() - t0) * 1000:.2f}ms")
    
    if pdf.empty:
        logger.debug(f"[PERF] Total: {(time.perf_counter() - total_start) * 1000:.2f}ms (empty)")
        return pdf
    
    t0 = time.perf_counter()
    if "date" in pdf.columns:
        pdf["date"] = pd.to_datetime(pdf["date"])
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
    """Get the most recent price for a ticker."""
    try:
        now = datetime.now()
        
        # Fetch from Delta Lake
        current_date = date(now.year, now.month, now.day)
        start_date = current_date - timedelta(days=3)
        
        # Use cached DeltaTable with predicate pushdown for filtering
        dt = _get_cached_delta_table(settings.stocks_delta_table)
        stocks = dt.to_pandas(
            columns=["date", "close"],
            filters=[
                ("symbol", "==", ticker),
                ("date", ">=", start_date),
                ("date", "<=", current_date),
            ]
        )
        
        if stocks.empty:
            return None

        # Get the latest price by sorting in pandas
        latest_price = float(stocks.sort_values("date", ascending=False)["close"].iloc[0])
        return latest_price
    except Exception as e:
        print(f"Error getting current price for {ticker}: {e}")
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
        
        # Calculate indicators if requested
        indicator_data = {}
        close_prices = df["close"].values
        high_prices = df["high"].values
        low_prices = df["low"].values
        volume_prices = df["volume"].values
        

                    
        feature_store = FeatureStore()
        
        for ind in indicators:
            try:
                if ind.name == "rsi":
                    timeperiod = ind.params.get("timeperiod", 14)
                    indicator_data["rsi"] = convert_nans(talib.RSI(close_prices, timeperiod=timeperiod))
                    indicator_data["rsi_5"] = convert_nans(talib.RSI(close_prices, timeperiod=5))
                
                elif ind.name == "macd":
                    fastperiod = ind.params.get("fastperiod", 12)
                    slowperiod = ind.params.get("slowperiod", 26)
                    signalperiod = ind.params.get("signalperiod", 9)
                    macd_line, signal_line, histogram = talib.MACD(
                        close_prices,
                        fastperiod=fastperiod,
                        slowperiod=slowperiod,
                        signalperiod=signalperiod
                    )
                    indicator_data["macd"] = {
                        "macd": convert_nans(macd_line),
                        "signal": convert_nans(signal_line),
                        "histogram": convert_nans(histogram)
                    }
                
                elif ind.name == "bbands":
                    timeperiod = ind.params.get("timeperiod", 20)
                    nbdevup = ind.params.get("nbdevup", 2)
                    nbdevdn = ind.params.get("nbdevdn", 2)
                    upper, middle, lower = talib.BBANDS(
                        close_prices,
                        timeperiod=timeperiod,
                        nbdevup=nbdevup,
                        nbdevdn=nbdevdn
                    )
                    indicator_data["bbands"] = {
                        "upper": convert_nans(upper),
                        "middle": convert_nans(middle),
                        "lower": convert_nans(lower)
                    }
                
                elif ind.name == "sma":
                    timeperiod = ind.params.get("timeperiod", 20)
                    indicator_data["sma"] = convert_nans(talib.SMA(close_prices, timeperiod=timeperiod))
                
                elif ind.name == "ema":
                    timeperiod = ind.params.get("timeperiod", 20)
                    indicator_data["ema"] = convert_nans(talib.EMA(close_prices, timeperiod=timeperiod))
                
                elif ind.name == "atr_trailing":
                    timeperiod = ind.params.get("timeperiod", 10)
                    atr = talib.ATR(high_prices, low_prices, close_prices, timeperiod=timeperiod)
                    indicator_data["atr_trailing"] = convert_nans(trailing_sl(close_prices, atr))
                
                elif ind.name == "vwap":
                    window = ind.params.get("window", 100)
                    indicator_data["vwap_highest"] = convert_nans(avwap(
                        close_prices,
                        high_prices,
                        low_prices,
                        df["volume"].values,
                        is_highest=True,
                        window=window
                    ))
                    indicator_data["vwap_lowest"] = convert_nans(avwap(
                        close_prices,
                        high_prices,
                        low_prices,
                        df["volume"].values,
                        is_highest=False,
                        window=window
                    ))
                
                elif ind.name == "bvc":
                    window = ind.params.get("window", 20)
                    kappa = ind.params.get("kappa", 0.1)
                    bvc_values = hawkes_BVC(
                        close_prices,
                        volume_prices,
                        window=window,
                        kappa=kappa
                    )
                    indicator_data["bvc"] = convert_nans(bvc_values)
                
                elif ind.name == "stoch":
                    fastk_period = ind.params.get("fastk_period", 14)
                    slowk_period = ind.params.get("slowk_period", 3)
                    slowd_period = ind.params.get("slowd_period", 3)
                    slowk, slowd = talib.STOCH(
                        high_prices,
                        low_prices,
                        close_prices,
                        fastk_period=fastk_period,
                        slowk_period=slowk_period,
                        slowd_period=slowd_period
                    )
                    indicator_data["stoch"] = {
                        "slowk": convert_nans(slowk),
                        "slowd": convert_nans(slowd)
                    }
                
                elif ind.name == "kalman_zscore":
                    # Try to retrieve from feature store; fallback to on-the-fly calc
                    try:
                        col_name = f"zscore_kf_{window}"
                        fs_df = feature_store.get_features(
                            symbol,
                            start=df["date"].min(),
                            end=df["date"].max(),
                            columns=["date", col_name],
                        )
                        if not fs_df.empty and col_name in fs_df.columns:
                            # Align by date
                            merged = df[["date"]].merge(fs_df, on="date", how="left")
                            indicator_data["kalman_zscore"] = convert_nans(merged[col_name].values)
                        else:
                            indicator_data["kalman_zscore"] = kalman_zscore.calculate_kalman_zscore(close_prices, window=window)
                    except Exception:
                        print("Error calculating kalman zscore on-the-fly")
                        indicator_data["kalman_zscore"] = kalman_zscore.calculate_kalman_zscore(close_prices, window=window)
                
                elif ind.name == "yz_volatility":
                    window = ind.params.get("window", 30)
                    periods = ind.params.get("periods", 252)
                    indicator_data["yz_volatility"] = calculate_yz_volatility(
                            df["open"].values,
                            df["high"].values,
                            df["low"].values,
                            df["close"].values,
                            window=window,
                            periods=periods
                        )
                    
                elif ind.name == "rs_rating":
                    rs_rating = feature_store.get_features(symbol, start=df["date"].min(), end=df["date"].max(), columns=["date", "rs_rating_20", 'rs_rating_50', 'rs_rating_252'])
                    # left join with df
                    df = df.merge(rs_rating, on="date", how="left")

                    # Apply EMA smoothing with a fixed short span (10 days) to reduce noise
                    # Using the same smoothing period for all ratings for consistency
                    ema_span = 10
                    df["rs_rating_20_ema"] = df["rs_rating_20"].ewm(span=ema_span, adjust=False).mean().round(2)
                    df["rs_rating_50_ema"] = df["rs_rating_50"].ewm(span=ema_span, adjust=False).mean().round(2)
                    df["rs_rating_252_ema"] = df["rs_rating_252"].ewm(span=ema_span, adjust=False).mean().round(2)

                    indicator_data["rs_rating_20"] = convert_nans(df["rs_rating_20"].values)
                    indicator_data["rs_rating_50"] = convert_nans(df["rs_rating_50"].values)
                    indicator_data["rs_rating_252"] = convert_nans(df["rs_rating_252"].values)

                    indicator_data["rs_rating_20_ema"] = convert_nans(df["rs_rating_20_ema"].values)
                    indicator_data["rs_rating_50_ema"] = convert_nans(df["rs_rating_50_ema"].values)
                    indicator_data["rs_rating_252_ema"] = convert_nans(df["rs_rating_252_ema"].values)

                elif ind.name == "matrix_series":
                    price_period = ind.params.get("price_period", 16)
                    sup_res_period = ind.params.get("sup_res_period", 30)
                    sup_res_percentage = ind.params.get("sup_res_percentage", 100)
                    smoother = ind.params.get("smoother", 5)

                    close_arr = df["close"].to_numpy().reshape(-1, 1)
                    high_arr = df["high"].to_numpy().reshape(-1, 1)
                    low_arr = df["low"].to_numpy().reshape(-1, 1)

                    matrix_series_indicator = vbt.IndicatorFactory(
                        class_name='MatrixSeries',
                        short_name='matrix_series',
                        input_names=['close', 'high', 'low'],
                        param_names=['price_period', 'sup_res_period', 'sup_res_percentage', 'smoother'],
                        output_names=['hh', 'll', 'support_line', 'resistance_line', 'up_line', 'down_line']
                    ).from_apply_func(matrix_series)

                    matrix_series_indicator = matrix_series_indicator.run(close_arr, high_arr, low_arr, price_period=price_period, sup_res_period=sup_res_period, sup_res_percentage=sup_res_percentage, smoother=smoother)
                    indicator_data["matrix_series"] = {
                        "hh": convert_nans(matrix_series_indicator.hh.to_numpy().reshape(-1)),
                        "ll": convert_nans(matrix_series_indicator.ll.to_numpy().reshape(-1)),
                        "support_line": convert_nans(matrix_series_indicator.support_line.to_numpy().reshape(-1)),
                        "resistance_line": convert_nans(matrix_series_indicator.resistance_line.to_numpy().reshape(-1)),
                        "up_line": convert_nans(matrix_series_indicator.up_line.to_numpy().reshape(-1)),
                        "down_line": convert_nans(matrix_series_indicator.down_line.to_numpy().reshape(-1))
                    }
            except Exception as e:
                print(f"Error calculating {ind.name}: {e}")

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
        print(f"Error getting timeseries data for {symbol}: {e}")
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
        
        # Calculate indicators
        indicator_data = {}
                
        for ind in indicators:
            try:
                if ind.name == "matrix_series":
                    price_period = ind.params.get("price_period", 16)
                    sup_res_period = ind.params.get("sup_res_period", 30)
                    sup_res_percentage = ind.params.get("sup_res_percentage", 100)
                    smoother = ind.params.get("smoother", 5)

                    close_arr = df["close"].to_numpy().reshape(-1, 1)
                    high_arr = df["high"].to_numpy().reshape(-1, 1)
                    low_arr = df["low"].to_numpy().reshape(-1, 1)

                    matrix_series_indicator = vbt.IndicatorFactory(
                        class_name='MatrixSeries',
                        short_name='matrix_series',
                        input_names=['close', 'high', 'low'],
                        param_names=['price_period', 'sup_res_period', 'sup_res_percentage', 'smoother'],
                        output_names=['hh', 'll', 'support_line', 'resistance_line', 'up_line', 'down_line']
                    ).from_apply_func(matrix_series)

                    matrix_series_indicator = matrix_series_indicator.run(close_arr, high_arr, low_arr, price_period=price_period, sup_res_period=sup_res_period, sup_res_percentage=sup_res_percentage, smoother=smoother)
                    indicator_data["matrix_series"] = {
                        "hh": convert_nans(matrix_series_indicator.hh.to_numpy().reshape(-1)),
                        "ll": convert_nans(matrix_series_indicator.ll.to_numpy().reshape(-1)),
                        "support_line": convert_nans(matrix_series_indicator.support_line.to_numpy().reshape(-1)),
                        "resistance_line": convert_nans(matrix_series_indicator.resistance_line.to_numpy().reshape(-1)),
                        "up_line": convert_nans(matrix_series_indicator.up_line.to_numpy().reshape(-1)),
                        "down_line": convert_nans(matrix_series_indicator.down_line.to_numpy().reshape(-1))
                    }

            except Exception as e:
                logger.error(f"Error calculating {ind.name}: {e}")

        return IndicatorsOnlyResponse(
            symbol=symbol,
            interval="1d",
            timestamps=df["date"].dt.strftime("%Y-%m-%d").tolist(),
            indicators=Indicators(**indicator_data)
        )
    except Exception as e:
        logger.error(f"Error getting indicators for {symbol}: {e}")
        raise


def calculate_rsi(prices: np.ndarray, period: int = 14) -> List[float]:
    """Calculate RSI indicator using TA-Lib."""
    rsi = talib.RSI(prices, timeperiod=period)
    return convert_nans(rsi)

def calculate_macd(
    prices: np.ndarray,
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Calculate MACD indicator using TA-Lib."""
    macd_line, signal_line, histogram = talib.MACD(
        prices,
        fastperiod=fast_period,
        slowperiod=slow_period,
        signalperiod=signal_period
    )
    return map(convert_nans, (macd_line, signal_line, histogram))


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

