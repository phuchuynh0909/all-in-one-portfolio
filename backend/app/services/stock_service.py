from datetime import datetime
from typing import List, Optional, Tuple
import os
from deltalake import DeltaTable
import deltalake
import pandas as pd
import pyarrow as pa
import numpy as np
import talib
from loguru import logger
from .indicators import trailing_sl, avwap, hawkes_BVC, kalman_zscore, calculate_yz_volatility
from .utils import convert_nans
from app.schemas.timeseries import TimeseriesResponse, Indicators, IndicatorParams
import pyarrow.dataset as ds

from datetime import datetime, date, timedelta
from fastapi_cache.decorator import cache
from app.core.settings import settings


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
    # Load watchlist if no symbols provided
    if not symbols:
        watchlist_path = os.path.join(settings.model_path, "watchlist.csv")
        if os.path.exists(watchlist_path):
            with open(watchlist_path, 'r') as f:
                symbols = [line.strip() for line in f if line.strip()]
            logger.info(f"Loaded {len(symbols)} symbols from watchlist")
        else:
            logger.warning(f"Watchlist not found at {watchlist_path}, using all available symbols")
            symbols = None
    
    print(symbols)

    dt = DeltaTable(settings.stocks_delta_table, storage_options=_delta_storage_options())
    dataset = dt.to_pyarrow_dataset()
    filt = _build_filter(symbols, start, end)
    try:
        table = dataset.to_table(filter=filt, columns=columns)
    except Exception:
        table = dataset.to_table(columns=columns)
    pdf = table.to_pandas()
    if pdf.empty:
        return pdf
    if "date" in pdf.columns:
        pdf["date"] = pd.to_datetime(pdf["date"])
    if "symbol" in pdf.columns and "date" in pdf.columns:
        pdf = pdf.sort_values(["symbol", "date"]).reset_index(drop=True)
    elif "symbol" in pdf.columns:
        pdf = pdf.sort_values(["symbol"]).reset_index(drop=True)
    return pdf


def _load_feature_store(
    symbols: list | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> pd.DataFrame:
    dt = DeltaTable(settings.stocks_feature_store, storage_options=_delta_storage_options())
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
        
        # Use DeltaTable's predicate pushdown for filtering
        dt = DeltaTable(settings.stocks_delta_table, storage_options=_delta_storage_options())
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
        # Load data from Delta Lake
        query_filters = [("symbol", "==", symbol)]
        if start_date:
            query_filters.append(("date", ">=", datetime.strptime(start_date, "%Y-%m-%d")))
        if end_date:
            query_filters.append(("date", "<=", datetime.strptime(end_date, "%Y-%m-%d")))

        dt = DeltaTable(settings.stocks_delta_table, storage_options=_delta_storage_options())
        df = dt.to_pandas(
            columns=["date", "open", "high", "low", "close", "volume"],
            filters=query_filters
        )
        
        if df.empty:
            raise ValueError(f"No data found for symbol {symbol}")

        # Sort by date
        df = df.sort_values("date")
        
        # Calculate indicators if requested
        indicator_data = {}
        close_prices = df["close"].values
        high_prices = df["high"].values
        low_prices = df["low"].values
        
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
                    window = ind.params.get("window", 200)
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
                    indicator_data["bvc"] = convert_nans(hawkes_BVC(
                        close_prices,
                        df["volume"].values,
                        window=window,
                        kappa=kappa
                    ))
                
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
                    window = ind.params.get("window", 20)
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
