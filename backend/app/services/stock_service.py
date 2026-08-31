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
from app.schemas.sector import (
    SectorConstituentRow,
    SectorConstituents,
    SectorDominance,
    SectorDominanceRow,
    SectorRelativeStrength,
    SectorRelativeStrengthRow,
    SectorRotation,
    SectorRotationRow,
    SectorTimeseries,
    SectorTimeseriesData,
)
from app.schemas.timeseries import (
    BarsResponse,
    Indicators,
    IndicatorParams,
    IndicatorsOnlyResponse,
    TimeseriesResponse,
)

from .sector_lists import SECTOR_LEVEL_5, level5_constituents
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

def _slice_aligned(node, start: int, end: int):
    """Slice every per-bar array inside a (possibly nested) indicator payload."""
    if isinstance(node, list):
        return node[start:end]
    if isinstance(node, dict):
        return {key: _slice_aligned(value, start, end) for key, value in node.items()}
    return node


def _empty_timeseries() -> dict:
    return {"open": [], "high": [], "low": [], "close": [], "volume": []}


async def get_stock_bars(
    symbol: str,
    interval: str = "1d",
    indicators: Optional[List[IndicatorParams]] = None,
    to_ts: Optional[int] = None,
    count_back: Optional[int] = None,
    from_ts: Optional[int] = None,
) -> BarsResponse:
    """
    One page of bars ending at ``to_ts`` (exclusive), newest page first as the
    chart scrolls back. Paging happens here rather than in the browser so the
    frontend never has to hold the whole history.

    Indicators are computed over the symbol's *full* history and only then
    sliced to the page, so every page carries the same values it would have in
    a single full-history response — no warmup artifacts at page boundaries and
    no drift for cumulative indicators.
    """
    indicators = indicators or []
    try:
        df = _load_delta_stocks(
            symbols=[symbol],
            columns=["date", "open", "high", "low", "close", "volume", "symbol"],
        )
        df = df[df["symbol"] == symbol].drop(columns=["symbol"])

        if df.empty:
            return BarsResponse(
                symbol=symbol,
                interval=interval,
                timestamps=[],
                timeseries=_empty_timeseries(),
                no_data=True,
            )

        dates = df["date"]
        # `to` is exclusive: a bar stamped exactly at `to` belongs to the next page.
        to_dt = pd.Timestamp(to_ts, unit="s").normalize() if to_ts is not None else None
        end_idx = int((dates < to_dt).sum()) if to_dt is not None else len(df)

        if count_back is not None:
            start_idx = max(0, end_idx - count_back)
        elif from_ts is not None:
            start_idx = int((dates < pd.Timestamp(from_ts, unit="s").normalize()).sum())
        else:
            start_idx = 0

        if start_idx >= end_idx:
            # Nothing in the window. If bars exist after it, tell the chart where
            # to resume so it can skip the gap instead of giving up.
            next_time = None
            if to_dt is not None:
                later = dates[dates >= to_dt]
                if not later.empty:
                    next_time = int(later.iloc[0].timestamp())
            return BarsResponse(
                symbol=symbol,
                interval=interval,
                timestamps=[],
                timeseries=_empty_timeseries(),
                no_data=True,
                next_time=next_time,
            )

        indicator_data = _slice_aligned(
            compute_stock_indicators(df, indicators), start_idx, end_idx
        )
        window = df.iloc[start_idx:end_idx]

        return BarsResponse(
            symbol=symbol,
            interval=interval,
            meta={"total_bars": len(df), "start_index": start_idx, "end_index": end_idx},
            timestamps=window["date"].dt.strftime("%Y-%m-%d").tolist(),
            timeseries={
                "open": window["open"].tolist(),
                "high": window["high"].tolist(),
                "low": window["low"].tolist(),
                "close": window["close"].tolist(),
                "volume": window["volume"].tolist(),
            },
            indicators=Indicators(**indicator_data) if indicator_data else None,
            no_data=False,
            has_more_history=start_idx > 0,
        )
    except Exception as e:
        logger.error("Error getting bars page for {}: {}", symbol, e)
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


SECTOR_CLOSE_COLUMNS = ["date", "sector_id", "sector_name", "close"]

# The sector page calls three endpoints at once and each needs the same sector
# and benchmark history — ~3s of ClickHouse per call, serialised because the
# handlers do their work synchronously inside `async def`. One short-lived cache
# turns the 2nd and 3rd calls into lookups. Same 5 minutes as the route caches.
_frame_cache: dict = {}


def _cached_frame(key, producer):
    """Memoise a loader for ``_CACHE_TTL_SECONDS``, handing back a copy.

    The copy is cheap insurance: several callers reshape what they get, and a
    shared frame they could mutate would be a nasty way to find that out.
    """
    now = time_module.time()
    hit = _frame_cache.get(key)
    if hit is not None and now - hit[1] < _CACHE_TTL_SECONDS:
        return hit[0].copy()
    value = producer()
    _frame_cache[key] = (value, now)
    return value.copy()


def _load_sector_closes(level: int, db: Session = None) -> pd.DataFrame:
    """Cached wrapper around :func:`_load_sector_closes_uncached`."""
    return _cached_frame(("sector_closes", int(level)), lambda: _load_sector_closes_uncached(level, db))


def _load_sector_closes_uncached(level: int, db: Session = None) -> pd.DataFrame:
    """Daily closes for every sector at ``level``, as a tidy frame.

    Columns: ``date``, ``sector_id``, ``sector_name``, ``close``, sorted by
    (name, date) so row order is stable across calls.

    Every level now comes from ClickHouse ``ohlc_eod``, where each sector index
    rides as a pseudo-symbol: levels 1 and 2 as the 4-digit zero-padded id
    (``0500``) out of the MetaStock index files, levels 3 and 4 as
    ``SECTOR3_26`` out of the wichart crawl. ``sectorSymbol`` owns that scheme —
    do not rebuild it here. Names come from the ``sector`` table either way.
    """
    from app.utils.wichart import sectorSymbol

    empty = pd.DataFrame(columns=SECTOR_CLOSE_COLUMNS)

    from app.db.base import get_db

    if db is None:
        db = next(get_db())
    sectors = _get_sectors_from_db(db, level)
    if not sectors:
        logger.info("No sectors at level {} in the database", level)
        return empty

    sector_info = {
        sectorSymbol(level, sector.id): (sector.id, sector.name) for sector in sectors
    }
    pdf = _load_delta_stocks(
        symbols=list(sector_info),
        columns=["date", "close", "symbol"],
    )
    if pdf.empty:
        logger.warning(
            "No rows in ClickHouse for any level {} sector — has the wichart sector crawl run?",
            level,
        )
        return empty

    pdf = pdf[pdf["symbol"].isin(sector_info)].copy()
    pdf["sector_id"] = pdf["symbol"].map(lambda s: sector_info[s][0])
    pdf["sector_name"] = pdf["symbol"].map(lambda s: sector_info[s][1])
    out = pdf[SECTOR_CLOSE_COLUMNS].copy()

    out["date"] = pd.to_datetime(out["date"]).dt.normalize()
    out = out.drop_duplicates(subset=["sector_id", "date"], keep="last")
    return out.sort_values(["sector_name", "date"]).reset_index(drop=True)


def _build_sector_timeseries(closes: pd.DataFrame, sector_level: str) -> SectorTimeseries:
    """Shape a tidy sector-close frame into the API's SectorTimeseries."""
    if closes.empty:
        return _create_empty_sector_timeseries(sector_level)

    timestamps = sorted(closes["date"].dt.strftime("%Y-%m-%d").unique().tolist())

    sectors_data = []
    for sector_id, sector_df in closes.groupby("sector_id", sort=False):
        sectors_data.append(
            SectorTimeseriesData(
                id=int(sector_id),
                name=str(sector_df["sector_name"].iloc[0]),
                data=sector_df["close"].tolist(),
            )
        )

    return SectorTimeseries(
        sector_level=sector_level,
        interval="1d",
        meta={},
        timestamps=timestamps,
        sector_data=sectors_data,
    )


async def get_sector_timeseries(
    sector_level: str,
    db: Session = None
) -> SectorTimeseries:
    """Get sector timeseries data with optional indicators."""
    level = int(sector_level)
    try:
        return _build_sector_timeseries(_load_sector_closes(level, db), sector_level)
    except Exception as e:
        logger.error("Error getting sector timeseries for level {}: {}", sector_level, e)
        return _create_empty_sector_timeseries(sector_level)


SECTOR_RS_BENCHMARK = "VNINDEX"
DEFAULT_SECTOR_RS_LOOKBACK = 41   # T-0 .. T-40
DEFAULT_SECTOR_RS_WINDOW = 50
# 50 sessions is ~10 weeks, so the weekly view keeps the same calendar reach.
DEFAULT_SECTOR_RS_WINDOW_WEEKLY = 10
# Weeks end Friday; a week is labelled by the last session actually traded in
# it, so a Friday holiday shows Thursday's date rather than a date with no bar.
SECTOR_RS_WEEK_ANCHOR = "W-FRI"
DEFAULT_SECTOR_RS_METRIC = "mansfield"
# Sessions an outperformance reference bar may be carried over a data hole.
_RS_REFERENCE_FFILL_LIMIT = 5


def _load_benchmark_closes(symbol: str = SECTOR_RS_BENCHMARK) -> pd.Series:
    """Benchmark closes from ClickHouse, indexed by normalised date. Cached."""
    return _cached_frame(("benchmark", symbol), lambda: _load_benchmark_closes_uncached(symbol))


def _load_benchmark_closes_uncached(symbol: str) -> pd.Series:
    pdf = _load_delta_stocks(symbols=[symbol], columns=["date", "close", "symbol"])
    if pdf.empty:
        return pd.Series(dtype="float64")
    series = pdf.set_index(pd.to_datetime(pdf["date"]).dt.normalize())["close"]
    return series[~series.index.duplicated(keep="last")].sort_index()


def _weekly_groups(dates: pd.DatetimeIndex) -> tuple[pd.PeriodIndex, pd.Series]:
    """Week key per date, plus each week's display date.

    The key is the calendar week (``SECTOR_RS_WEEK_ANCHOR``); the display date is
    the last session inside that week. Callers group *every* series with the one
    key array so sectors and benchmark cannot land on different week labels — a
    sector whose last bar was Thursday still shares the week of a benchmark that
    traded Friday.
    """
    weeks = dates.to_period(SECTOR_RS_WEEK_ANCHOR)
    labels = pd.Series(dates, index=weeks).groupby(level=0).max()
    return weeks, labels


def _relative_strength(
    closes: pd.DataFrame,
    benchmark: pd.Series,
    window: int,
    metric: str = "mansfield",
    timeframe: str = "daily",
) -> pd.DataFrame:
    """Relative strength for every column of ``closes``, against ``benchmark``.

    Both metrics are the two outputs of
    ``app.services.indicators.common.relative_strength_nb``, off the same ratio:

        rs_ratio        = close / benchmark_close
        mansfield       = (rs_ratio / mean(rs_ratio over the *previous* `window` bars) - 1) * 100
        outperformance  = (rs_ratio[t] / rs_ratio[t - window]) * 100 - 100

    Mansfield asks whether a sector is above its own recent average strength;
    outperformance asks by how much it beat the benchmark over the window. The
    mansfield mean excludes the current bar — hence the ``shift(1)`` after the
    roll. Both series are restricted to dates they share, so a benchmark holiday
    cannot smear a sector's ratio, and both are centred on zero.

    Both depart from the numba version in the same direction, because sector
    series are missing scattered days and a strict reading of a hole poisons far
    more than the hole itself: mansfield's mean uses ``min_periods`` (``np.mean``
    over a window holding one hole would blank the next ``window`` values), and
    outperformance carries its reference bar forward over holes. In both cases
    the *current* bar is untouched, so a day the sector did not trade still
    reads NaN — the honest answer for that day.
    """
    shared = closes.index.intersection(benchmark.index)
    if shared.empty:
        return pd.DataFrame(index=closes.index[:0], columns=closes.columns, dtype="float64")

    aligned_closes = closes.loc[shared]
    aligned_benchmark = benchmark.loc[shared]
    week_labels = None

    if timeframe == "weekly":
        # ``last()`` skips NaN, so a week keeps its last *traded* close: the
        # scattered missing days that pit the daily grid mostly vanish here, and
        # a week with no bar at all stays NaN.
        weeks, week_labels = _weekly_groups(pd.DatetimeIndex(shared))
        aligned_closes = aligned_closes.groupby(weeks).last()
        aligned_benchmark = aligned_benchmark.groupby(weeks).last()

    rs_ratio = aligned_closes.div(aligned_benchmark, axis=0)

    if metric == "outperformance":
        # The reference bar is carried forward over holes, bounded so a long
        # halt cannot supply a stale one. A raw positional shift lands on a
        # missing day often enough to blank half the T-0 column at level 1 —
        # the bar 50 sessions back simply was not crawled. The numerator stays
        # untouched, so a sector with no bar today still reads blank today.
        reference = rs_ratio.ffill(limit=_RS_REFERENCE_FFILL_LIMIT).shift(window)
        result = (rs_ratio / reference) * 100 - 100
    else:
        mean_rs_ratio = (
            rs_ratio.rolling(window, min_periods=max(2, window // 2)).mean().shift(1)
        )
        result = (rs_ratio / mean_rs_ratio - 1) * 100

    if week_labels is not None:
        # Back from week keys to real dates, so callers keep formatting an index
        # of sessions whichever timeframe they asked for.
        result.index = pd.DatetimeIndex(week_labels.reindex(result.index).values)

    return result


def _empty_sector_relative_strength(
    sector_level: str, window: int, metric: str, timeframe: str
) -> SectorRelativeStrength:
    return SectorRelativeStrength(
        sector_level=sector_level,
        interval="1d",
        benchmark=SECTOR_RS_BENCHMARK,
        window=window,
        metric=metric,
        timeframe=timeframe,
        dates=[],
        rows=[],
    )


async def get_sector_relative_strength(
    sector_level: str,
    lookback: int = DEFAULT_SECTOR_RS_LOOKBACK,
    window: int = None,
    metric: str = DEFAULT_SECTOR_RS_METRIC,
    timeframe: str = "daily",
    db: Session = None,
) -> SectorRelativeStrength:
    """Relative strength of every sector at ``sector_level`` against the benchmark.

    Returns the last ``lookback`` bars (oldest first, T-0 last) as a dense
    matrix: one row per sector, one value per bar, ``None`` where the sector
    has no bar then or is still inside the rolling warmup. Rows come back
    strongest-first at T-0, which is the order the heatmap draws them in.

    ``timeframe="weekly"`` rolls the closes up to one bar per calendar week
    first, so ``lookback`` and ``window`` then count weeks. An unset ``window``
    takes the timeframe's default — 50 sessions or 10 weeks, the same reach.
    """
    level = int(sector_level)
    if window is None:
        window = (
            DEFAULT_SECTOR_RS_WINDOW_WEEKLY
            if timeframe == "weekly"
            else DEFAULT_SECTOR_RS_WINDOW
        )
    try:
        closes = _load_sector_closes(level, db)
        if closes.empty:
            return _empty_sector_relative_strength(sector_level, window, metric, timeframe)

        benchmark = _load_benchmark_closes()
        if benchmark.empty:
            logger.warning("No {} closes available; cannot compute sector RS", SECTOR_RS_BENCHMARK)
            return _empty_sector_relative_strength(sector_level, window, metric, timeframe)

        wide = closes.pivot_table(index="date", columns="sector_id", values="close")
        names = closes.drop_duplicates("sector_id").set_index("sector_id")["sector_name"]

        rs = _relative_strength(wide, benchmark, window, metric, timeframe).tail(lookback)
        if rs.empty:
            return _empty_sector_relative_strength(sector_level, window, metric, timeframe)

        # Strongest at T-0 first; a sector with no value at T-0 sorts to the end.
        latest = rs.iloc[-1]
        ordered = latest.sort_values(ascending=False, na_position="last").index

        rows = [
            SectorRelativeStrengthRow(
                id=int(sector_id),
                name=str(names.get(sector_id, sector_id)),
                values=[None if pd.isna(v) else round(float(v), 4) for v in rs[sector_id]],
            )
            for sector_id in ordered
        ]

        return SectorRelativeStrength(
            sector_level=sector_level,
            interval="1d",
            benchmark=SECTOR_RS_BENCHMARK,
            window=window,
            metric=metric,
            timeframe=timeframe,
            dates=rs.index.strftime("%Y-%m-%d").tolist(),
            rows=rows,
        )
    except Exception as e:
        logger.error("Error computing sector relative strength for level {}: {}", sector_level, e)
        return _empty_sector_relative_strength(sector_level, window, metric, timeframe)



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


# ── Sector dominance ──────────────────────────────────────────────────────────

DEFAULT_SECTOR_ROTATION_TAIL = 8
# Momentum is the Mansfield of the RS ratio, so its window is the second
# smoothing. Shorter than `window` on purpose: it has to turn before the ratio.
DEFAULT_SECTOR_MOMENTUM_WINDOW = 10
DEFAULT_SECTOR_MOMENTUM_WINDOW_WEEKLY = 4
# Breadth below this many rated constituents is reported but kept out of the
# score. Only ~half of the mapped symbols are inside the crawler's watchlist,
# so a sector can end up with 4 readings, where breadth quantises to 0.25
# steps and would swing the composite on noise.
MIN_BREADTH_SAMPLE = 5


def _sector_constituents(level: int, db: Session) -> dict[int, list[str]]:
    """Symbols mapped to each sector at ``level``, from ``stock_symbol``."""
    from app.db.models.market import StockSymbol

    if int(level) == SECTOR_LEVEL_5:
        # Multi-tag, so it cannot live in a single stock_symbol column.
        return dict(level5_constituents())

    column = {
        1: StockSymbol.id_sector_level_1,
        2: StockSymbol.id_sector_level_2,
        3: StockSymbol.id_sector_level_3,
        4: StockSymbol.id_sector_level_4,
    }[int(level)]

    mapping: dict[int, list[str]] = {}
    for symbol, sector_id in db.query(StockSymbol.symbol, column).filter(column.isnot(None)).all():
        mapping.setdefault(int(sector_id), []).append(str(symbol))
    return mapping


def _slope_per_bar(frame: pd.DataFrame) -> pd.Series:
    """Least-squares slope of each column against bar index, ignoring gaps.

    Answers "is this sector's relative strength still improving", which the
    level alone cannot: a sector can top the table on the way down.
    """
    slopes = {}
    for column in frame.columns:
        series = frame[column].dropna()
        if len(series) < 3:
            slopes[column] = None
            continue
        x = pd.Series(range(len(series)), index=series.index, dtype="float64")
        variance = ((x - x.mean()) ** 2).sum()
        slopes[column] = (
            None if variance == 0 else float(((x - x.mean()) * (series - series.mean())).sum() / variance)
        )
    return pd.Series(slopes, dtype="float64")


def _normalise_0_100(series: pd.Series) -> pd.Series:
    """Rank-scale to 0-100 so components with different units can be averaged.

    Ranks rather than min-max: one runaway sector would otherwise compress
    everything else into a narrow band, and the score is meant to order sectors,
    not measure distance between them.
    """
    valid = series.dropna()
    if valid.empty:
        return pd.Series(index=series.index, dtype="float64")
    if len(valid) == 1:
        return pd.Series({valid.index[0]: 100.0}).reindex(series.index)
    return valid.rank(pct=True).mul(100).reindex(series.index)


def _load_constituent_ohlcv(
    symbols: list, window: int, lookback: int, timeframe: str
) -> pd.DataFrame:
    """Constituent OHLCV, enough history to compute `window` + `lookback` bars.

    Cached on the level's symbol set so the dominance score and the constituent
    RS column share one ClickHouse read: clicking through sectors then costs
    nothing, because dominance has already warmed it.
    """
    bars_needed = (window + lookback) * (7 if timeframe == "weekly" else 1)
    start = pd.Timestamp.today().normalize() - pd.Timedelta(days=int(bars_needed * 1.6) + 30)
    return _cached_frame(
        ("constituent_ohlcv", start.date(), len(symbols), hash(tuple(symbols))),
        lambda: _load_delta_stocks(
            symbols=symbols,
            start=start,
            columns=["date", "close", "volume", "symbol"],
        ),
    )


def _latest_constituent_rs(
    stock_df: pd.DataFrame, benchmark: pd.Series, window: int, metric: str, timeframe: str
) -> pd.Series:
    """Each symbol's most recent relative-strength reading.

    Tolerates a reading up to 3 bars stale: a stock with no bar on the very last
    date would otherwise vanish, which cost ~60% of constituents at level 2.
    """
    if stock_df.empty:
        return pd.Series(dtype="float64")
    closes = stock_df.pivot_table(index="date", columns="symbol", values="close")
    rs = _relative_strength(closes, benchmark, window, metric, timeframe)
    if rs.empty:
        return pd.Series(dtype="float64")
    return rs.ffill(limit=3).iloc[-1]


async def get_sector_dominance(
    sector_level: str,
    lookback: int = DEFAULT_SECTOR_RS_LOOKBACK,
    window: int = None,
    metric: str = DEFAULT_SECTOR_RS_METRIC,
    timeframe: str = "daily",
    min_constituents: int = 3,
    db: Session = None,
) -> SectorDominance:
    """Rank sectors by *sustained* leadership rather than by today's RS.

    The latest RS value is a poor answer on its own — measured on this data the
    top-ranked sector at level 2 changed 18 times in 40 sessions across 9
    different sectors. So the score blends four things that can disagree:

    * **persistence** — mean cross-sectional rank and share of bars spent in the
      strongest quintile, which is what "led this window" actually means;
    * **breadth** — share of the sector's constituents with positive RS, which
      separates a sector being bid from one heavyweight running;
    * **momentum** — slope of its RS line, so fading leaders rank below rising ones;
    * **turnover share** — whether the money agrees.

    Each is rank-scaled to 0-100 and averaged, so the units never have to be
    reconciled. Sectors under ``min_constituents`` are returned with their
    components but no score: a one-stock sector cannot be a dominant sector, and
    levels 3 and 4 have several.
    """
    from app.db.base import get_db

    level = int(sector_level)
    if window is None:
        window = (
            DEFAULT_SECTOR_RS_WINDOW_WEEKLY if timeframe == "weekly" else DEFAULT_SECTOR_RS_WINDOW
        )
    empty = SectorDominance(
        sector_level=sector_level,
        benchmark=SECTOR_RS_BENCHMARK,
        window=window,
        metric=metric,
        timeframe=timeframe,
        lookback=lookback,
        min_constituents=min_constituents,
        rows=[],
    )

    try:
        if db is None:
            db = next(get_db())

        closes = _load_sector_closes(level, db)
        if closes.empty:
            return empty
        benchmark = _load_benchmark_closes()
        if benchmark.empty:
            return empty

        wide = closes.pivot_table(index="date", columns="sector_id", values="close")
        names = closes.drop_duplicates("sector_id").set_index("sector_id")["sector_name"]
        rs = _relative_strength(wide, benchmark, window, metric, timeframe).tail(lookback)
        if rs.empty:
            return empty

        # Persistence: rank each bar across sectors, 1 = strongest.
        rank = rs.rank(axis=1, ascending=False)
        quintile_cut = max(1, int(round(rs.shape[1] / 5)))
        mean_rank = rank.mean()
        top_quintile_share = (rank <= quintile_cut).mean()
        momentum = _slope_per_bar(rs)
        latest = rs.iloc[-1]

        # Breadth and turnover come from the constituents, not the sector series:
        # the level 3/4 sector bars carry no volume at all.
        constituents = _sector_constituents(level, db)
        symbols = sorted({s for group in constituents.values() for s in group})
        breadth = pd.Series(dtype="float64")
        rated = pd.Series(0, index=rs.columns, dtype="int64")
        turnover_share = pd.Series(dtype="float64")

        if symbols:
            stock_df = _load_constituent_ohlcv(symbols, window, lookback, timeframe)
            if not stock_df.empty:
                stock_df = stock_df.copy()
                stock_df["date"] = pd.to_datetime(stock_df["date"]).dt.normalize()
                latest_stock_rs = _latest_constituent_rs(
                    stock_df, benchmark, window, metric, timeframe
                )
                # Turnover in value terms over the same window, so a penny stock
                # with huge share counts does not dominate the money signal.
                stock_df["turnover"] = stock_df["close"] * stock_df["volume"]
                recent = stock_df[stock_df["date"] >= rs.index[0]]
                turnover_by_symbol = recent.groupby("symbol")["turnover"].sum()

                breadth_values, rated_values, turnover_values = {}, {}, {}
                for sector_id in rs.columns:
                    members = constituents.get(int(sector_id), [])
                    values = latest_stock_rs.reindex(members).dropna()
                    rated_values[sector_id] = int(len(values))
                    breadth_values[sector_id] = (
                        float((values > 0).mean()) if len(values) else None
                    )
                    turnover_values[sector_id] = float(
                        turnover_by_symbol.reindex(members).fillna(0).sum()
                    )

                breadth = pd.Series(breadth_values, dtype="float64")
                rated = pd.Series(rated_values, dtype="int64")
                total_turnover = pd.Series(turnover_values, dtype="float64")
                if total_turnover.sum() > 0:
                    turnover_share = total_turnover / total_turnover.sum()

        # Rank-scale every component, then average what each sector actually has.
        # Thin breadth samples are still reported, just not scored.
        scorable_breadth = (
            breadth.where(rated.reindex(breadth.index).fillna(0) >= MIN_BREADTH_SAMPLE)
            if not breadth.empty
            else breadth
        )
        components = pd.DataFrame({
            # Low mean rank is good, so invert before scaling.
            "persistence_rank": _normalise_0_100(-mean_rank),
            "persistence_share": _normalise_0_100(top_quintile_share),
            "breadth": _normalise_0_100(scorable_breadth) if not scorable_breadth.empty else None,
            "momentum": _normalise_0_100(momentum),
            "turnover": _normalise_0_100(turnover_share) if not turnover_share.empty else None,
        })
        score = components.mean(axis=1, skipna=True)

        counts = {sid: len(constituents.get(int(sid), [])) for sid in rs.columns}

        def _value(series, sector_id, digits=4):
            if series is None or sector_id not in series.index:
                return None
            v = series.get(sector_id)
            return None if v is None or pd.isna(v) else round(float(v), digits)

        rows = [
            SectorDominanceRow(
                id=int(sector_id),
                name=str(names.get(sector_id, sector_id)),
                score=(
                    _value(score, sector_id, 2)
                    if counts.get(sector_id, 0) >= min_constituents
                    else None
                ),
                rs=_value(latest, sector_id),
                mean_rank=_value(mean_rank, sector_id, 2),
                top_quintile_share=_value(top_quintile_share, sector_id),
                breadth=_value(breadth, sector_id),
                momentum=_value(momentum, sector_id, 6),
                turnover_share=_value(turnover_share, sector_id),
                constituents=counts.get(sector_id, 0),
                constituents_rated=int(rated.get(sector_id, 0)),
            )
            for sector_id in rs.columns
        ]
        # Scored sectors first, best score first; the unscorable tail keeps its
        # components visible rather than being dropped.
        rows.sort(key=lambda r: (r.score is None, -(r.score or 0)))

        return SectorDominance(
            sector_level=sector_level,
            benchmark=SECTOR_RS_BENCHMARK,
            window=window,
            metric=metric,
            timeframe=timeframe,
            lookback=lookback,
            min_constituents=min_constituents,
            as_of=rs.index[-1].strftime("%Y-%m-%d"),
            rows=rows,
        )
    except Exception as e:
        logger.error("Error computing sector dominance for level {}: {}", sector_level, e)
        return empty


async def get_sector_rotation(
    sector_level: str,
    tail: int = DEFAULT_SECTOR_ROTATION_TAIL,
    window: int = None,
    momentum_window: int = None,
    timeframe: str = "daily",
    db: Session = None,
) -> SectorRotation:
    """Relative rotation graph coordinates: RS-ratio against RS-momentum.

    Both axes are centred on 100, which is the benchmark. The ratio is the
    Mansfield RS shifted to 100, and momentum is the Mansfield *of that ratio* —
    the standard JdK construction, and it falls straight out of
    ``_relative_strength`` rather than needing its own maths.

    Quadrants read clockwise from the top right: leading (strong, strengthening),
    weakening (strong, fading), lagging (weak, fading), improving (weak,
    strengthening). Each sector returns a tail so the direction of travel is
    visible, which is the whole point of the chart.
    """
    from app.db.base import get_db

    level = int(sector_level)
    if window is None:
        window = (
            DEFAULT_SECTOR_RS_WINDOW_WEEKLY if timeframe == "weekly" else DEFAULT_SECTOR_RS_WINDOW
        )
    if momentum_window is None:
        momentum_window = (
            DEFAULT_SECTOR_MOMENTUM_WINDOW_WEEKLY
            if timeframe == "weekly"
            else DEFAULT_SECTOR_MOMENTUM_WINDOW
        )

    empty = SectorRotation(
        sector_level=sector_level,
        benchmark=SECTOR_RS_BENCHMARK,
        window=window,
        momentum_window=momentum_window,
        timeframe=timeframe,
        dates=[],
        rows=[],
    )

    try:
        if db is None:
            db = next(get_db())

        closes = _load_sector_closes(level, db)
        if closes.empty:
            return empty
        benchmark = _load_benchmark_closes()
        if benchmark.empty:
            return empty

        wide = closes.pivot_table(index="date", columns="sector_id", values="close")
        names = closes.drop_duplicates("sector_id").set_index("sector_id")["sector_name"]

        ratio = _relative_strength(wide, benchmark, window, "mansfield", timeframe) + 100
        if ratio.empty:
            return empty

        # Momentum of the ratio: same Mansfield shape, one level up. A flat
        # benchmark series keeps the helper's alignment logic happy.
        flat = pd.Series(1.0, index=ratio.index)
        momentum = (
            _relative_strength(ratio, flat, momentum_window, "mansfield", "daily") + 100
        )

        window_dates = ratio.index[-tail:]
        ratio = ratio.reindex(window_dates)
        momentum = momentum.reindex(window_dates)

        def _series(frame, sector_id):
            if sector_id not in frame.columns:
                return [None] * len(window_dates)
            return [None if pd.isna(v) else round(float(v), 4) for v in frame[sector_id]]

        rows = [
            SectorRotationRow(
                id=int(sector_id),
                name=str(names.get(sector_id, sector_id)),
                ratio=_series(ratio, sector_id),
                momentum=_series(momentum, sector_id),
            )
            for sector_id in ratio.columns
        ]
        def _quadrant_order(row: SectorRotationRow) -> tuple:
            """Leading, improving, weakening, lagging — then boldest first.

            Distance alone would put the deepest laggard at the top, and the
            chart labels only the first handful of rows.
            """
            ratio = row.ratio[-1] if row.ratio[-1] is not None else 100.0
            mom = row.momentum[-1] if row.momentum[-1] is not None else 100.0
            strong, rising = ratio >= 100, mom >= 100
            quadrant = 0 if (strong and rising) else 1 if rising else 2 if strong else 3
            distance = (ratio - 100) ** 2 + (mom - 100) ** 2
            return (quadrant, -distance)

        rows.sort(key=_quadrant_order)

        return SectorRotation(
            sector_level=sector_level,
            benchmark=SECTOR_RS_BENCHMARK,
            window=window,
            momentum_window=momentum_window,
            timeframe=timeframe,
            dates=pd.DatetimeIndex(window_dates).strftime("%Y-%m-%d").tolist(),
            rows=rows,
        )
    except Exception as e:
        logger.error("Error computing sector rotation for level {}: {}", sector_level, e)
        return empty


# ── Level 5 sector indices ────────────────────────────────────────────────────

LEVEL5_INDEX_BASE = 100.0


def build_level5_sector_index(start: str = "2020-01-01") -> pd.DataFrame:
    """Equal-weighted daily-rebalanced index per level-5 sector, as OHLC rows.

    Levels 1-4 arrive as ready-made index series (MetaStock files, or the
    wichart crawl). Level 5 is the sieucophieu ``stock_lists`` taxonomy, which
    publishes daily *metrics* but no price history, so the index is derived from
    the constituents already in ``ohlc_eod``.

    The construction is the textbook equal-weighted one: chain the mean simple
    return across the constituents that traded both bars, from a base of 100.
    Chaining returns rather than averaging prices is what makes it survive
    membership changes — a stock joining contributes its *return*, not its price
    level, so it cannot step the index. Volume is the constituent sum, which is
    real turnover, unlike the levels 3/4 series that carry none.

    Coverage is uneven and the caller should say so: 187 of the 368 mapped
    symbols are in ``ohlc_eod``, from 12/12 for Dầu khí down to 1/11 for
    Dược - Y tế, whose "index" is one stock.
    """
    from app.services.sector_lists import LEVEL5_SECTORS, level5_constituents
    from app.utils.wichart import sectorSymbol

    members = level5_constituents()
    symbols = sorted({s for group in members.values() for s in group})
    if not symbols:
        logger.warning("No level 5 constituents in the sector map")
        return pd.DataFrame()

    stocks = _load_delta_stocks(
        symbols=symbols,
        start=pd.Timestamp(start),
        columns=["date", "close", "volume", "symbol"],
    )
    if stocks.empty:
        logger.warning("No constituent OHLC in ClickHouse for level 5")
        return pd.DataFrame()

    stocks["date"] = pd.to_datetime(stocks["date"]).dt.normalize()
    closes = stocks.pivot_table(index="date", columns="symbol", values="close").sort_index()
    volumes = stocks.pivot_table(index="date", columns="symbol", values="volume").sort_index()
    # fill_method=None: the pandas default pads a gap and turns a day the
    # stock did not trade into a fabricated 0% return. NaN is correct there —
    # the mean below skips it, so the stock simply sits out that bar.
    returns = closes.pct_change(fill_method=None)

    frames = []
    for sector_id, name in LEVEL5_SECTORS.items():
        available = [s for s in members.get(sector_id, []) if s in closes.columns]
        if not available:
            logger.info("Level 5 sector {} ({}) has no constituent data", sector_id, name)
            continue

        # Mean across whoever traded that bar; a bar nobody traded contributes
        # nothing rather than breaking the chain.
        sector_returns = returns[available].mean(axis=1, skipna=True).fillna(0.0)
        index = LEVEL5_INDEX_BASE * (1 + sector_returns).cumprod()
        turnover = volumes[available].sum(axis=1, skipna=True)

        frames.append(pd.DataFrame({
            "date": index.index,
            "symbol": sectorSymbol(5, sector_id),
            "open": index.values,
            "high": index.values,
            "low": index.values,
            "close": index.values,
            "volume": turnover.reindex(index.index).fillna(0.0).values,
        }))

    if not frames:
        return pd.DataFrame()

    out = pd.concat(frames, ignore_index=True)
    logger.info(
        "Level 5: {} rows across {} indices, {} -> {}",
        f"{len(out):,}",
        out["symbol"].nunique(),
        out["date"].min().date(),
        out["date"].max().date(),
    )
    return out


async def get_sector_constituents(
    sector_level: str,
    sector_id: int,
    window: int = None,
    metric: str = DEFAULT_SECTOR_RS_METRIC,
    timeframe: str = "daily",
    db: Session = None,
) -> SectorConstituents:
    """One sector's constituents with each symbol's relative strength.

    The same measure, window and benchmark as the sector panels above it, so a
    constituent's number is comparable to its sector's — the point being to see
    whether a strong sector is broadly strong or carried by two names.

    ``rs_rank`` percentiles each symbol against every constituent *at this
    level*, not just its own sector, which is what makes a 70 mean the same
    thing in Ngân hàng as in Thép. Symbols with no series in ``ohlc_eod`` come
    back with ``rs=None`` rather than being dropped: mapped is not covered, and
    the count belongs on screen.
    """
    from app.db.base import get_db
    from app.db.models.market import StockSymbol

    level = int(sector_level)
    if window is None:
        window = (
            DEFAULT_SECTOR_RS_WINDOW_WEEKLY if timeframe == "weekly" else DEFAULT_SECTOR_RS_WINDOW
        )
    empty = SectorConstituents(
        sector_level=sector_level,
        sector_id=int(sector_id),
        benchmark=SECTOR_RS_BENCHMARK,
        window=window,
        metric=metric,
        timeframe=timeframe,
    )

    try:
        if db is None:
            db = next(get_db())

        constituents = _sector_constituents(level, db)
        members = constituents.get(int(sector_id), [])
        if not members:
            logger.info("No constituents mapped to level {} sector {}", level, sector_id)
            return empty

        benchmark = _load_benchmark_closes()
        if benchmark.empty:
            return empty

        # Load the whole level so the percentile has a market to rank against,
        # and so the cache is shared with the dominance table.
        all_symbols = sorted({s for group in constituents.values() for s in group})
        stock_df = _load_constituent_ohlcv(
            all_symbols, window, DEFAULT_SECTOR_RS_LOOKBACK, timeframe
        )
        if not stock_df.empty:
            stock_df = stock_df.copy()
            stock_df["date"] = pd.to_datetime(stock_df["date"]).dt.normalize()

        level_rs = _latest_constituent_rs(stock_df, benchmark, window, metric, timeframe)
        ranks = level_rs.dropna().rank(pct=True).mul(100).clip(1, 99).round()

        meta = {
            row.symbol: row
            for row in db.query(StockSymbol).filter(StockSymbol.symbol.in_(members)).all()
        }

        rows = []
        for symbol in members:
            value = level_rs.get(symbol)
            has_value = value is not None and not pd.isna(value)
            record = meta.get(symbol)
            rows.append(
                SectorConstituentRow(
                    symbol=symbol,
                    name=getattr(record, "name", None),
                    vonhoa_d=getattr(record, "vonhoa_d", None),
                    rs=round(float(value), 4) if has_value else None,
                    rs_rank=int(ranks.get(symbol)) if symbol in ranks.index else None,
                )
            )
        rows.sort(key=lambda r: (r.rs is None, -(r.rs if r.rs is not None else 0)))

        as_of = None
        if not stock_df.empty:
            as_of = pd.Timestamp(stock_df["date"].max()).strftime("%Y-%m-%d")

        return SectorConstituents(
            sector_level=sector_level,
            sector_id=int(sector_id),
            benchmark=SECTOR_RS_BENCHMARK,
            window=window,
            metric=metric,
            timeframe=timeframe,
            as_of=as_of,
            covered=sum(1 for r in rows if r.rs is not None),
            mapped=len(rows),
            rows=rows,
        )
    except Exception as e:
        logger.error(
            "Error computing constituents for level {} sector {}: {}", sector_level, sector_id, e
        )
        return empty
