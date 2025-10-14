from __future__ import annotations

from datetime import datetime, date, timedelta
from typing import List, Optional

import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from loguru import logger
from deltalake import DeltaTable
import pyarrow as pa
import pyarrow.dataset as ds

from app.core.settings import settings
from app.schemas.scanner import (
    ScannerColumnsResponse,
    ScannerRequest,
    ScannerResponse,
    ScannerResultItem,
    ConditionOperator,
)


router = APIRouter(prefix="/scanner", tags=["scanner"])


def _load_feature_store(
    symbols: List[str] | None,
    start: datetime | None,
    end: datetime | None,
    columns: List[str] | None = None,
) -> pd.DataFrame:
    dt = DeltaTable(settings.stocks_feature_store, storage_options=settings.delta_storage_options)
    dataset = dt.to_pyarrow_dataset()
    # Build PyArrow filter expression (consistent with stock_service)
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
    except Exception as e:
        logger.error(f"Failed to build filter expression: {e}")
        expr = None

    try:
        table = dataset.to_table(filter=expr, columns=columns) if expr is not None else dataset.to_table(columns=columns)
        pdf = table.to_pandas()
        return pdf
    except Exception as e:
        logger.error(f"Failed loading feature store: {e}")
        raise


def _latest_trading_date(today: date | None = None) -> date:
    """Return today's date if weekday; otherwise the previous Friday."""
    d = today or date.today()
    # Monday=0 ... Sunday=6
    if d.weekday() == 5:  # Saturday
        return d - timedelta(days=1)
    if d.weekday() == 6:  # Sunday
        return d - timedelta(days=2)
    return d


@router.get("/columns", response_model=ScannerColumnsResponse)
async def list_columns() -> ScannerColumnsResponse:
    try:
        # Read columns from Delta Lake schema (no data scan)
        dt = DeltaTable(settings.stocks_feature_store, storage_options=settings.delta_storage_options)
        dataset = dt.to_pyarrow_dataset()
        cols = list(dataset.schema.names) if dataset.schema is not None else []
        # Ensure canonical column order: date, symbol, others
        ordered = [c for c in ["date", "symbol"] if c in cols] + [c for c in cols if c not in ("date", "symbol")]
        return ScannerColumnsResponse(columns=ordered)
    except Exception as e:
        logger.error(f"Error listing feature columns: {e}")
        raise HTTPException(status_code=500, detail="Unable to list feature columns")


def _apply_conditions(df: pd.DataFrame, req: ScannerRequest) -> pd.DataFrame:
    filtered = df
    for cond in req.conditions:
        col = cond.column
        if col not in filtered.columns:
            # skip unknown column
            continue
        op = cond.operator
        val = cond.value
        if op == ConditionOperator.eq:
            filtered = filtered[filtered[col] == val]
        elif op == ConditionOperator.ne:
            filtered = filtered[filtered[col] != val]
        elif op == ConditionOperator.gt:
            filtered = filtered[filtered[col] > val]
        elif op == ConditionOperator.gte:
            filtered = filtered[filtered[col] >= val]
        elif op == ConditionOperator.lt:
            filtered = filtered[filtered[col] < val]
        elif op == ConditionOperator.lte:
            filtered = filtered[filtered[col] <= val]
        elif op == ConditionOperator.isin:
            filtered = filtered[filtered[col].isin(val)]
        elif op == ConditionOperator.notin:
            filtered = filtered[~filtered[col].isin(val)]
        elif op == ConditionOperator.between:
            filtered = filtered[(filtered[col] >= val[0]) & (filtered[col] <= val[1])]
        elif op == ConditionOperator.contains:
            filtered = filtered[filtered[col].astype(str).str.contains(str(val), na=False)]
    return filtered


@router.post("/scan", response_model=ScannerResponse)
async def scan(req: ScannerRequest) -> ScannerResponse:
    try:
        # Default to latest trading date if no dates provided
        if not req.start_date and not req.end_date:
            target = _latest_trading_date()
            start = pd.to_datetime(target)
            end = pd.to_datetime(target)
        else:
            start = pd.to_datetime(req.start_date) if req.start_date else None
            end = pd.to_datetime(req.end_date) if req.end_date else None

        base_columns = ["date", "symbol"]
        extra_columns = req.columns_to_return or []

        # Collect columns referenced in conditions
        condition_columns = list({c.column for c in req.conditions})
        columns = list(dict.fromkeys(base_columns + condition_columns + extra_columns))

        df = _load_feature_store(
            symbols=req.symbols,
            start=start,
            end=end,
            columns=None if not columns else columns,
        )
        if df.empty:
            return ScannerResponse(items=[], total=0)

        # Ensure types
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])

        df = _apply_conditions(df, req)

        if req.latest_only and {"symbol", "date"}.issubset(df.columns):
            df = df.sort_values(["symbol", "date"]).groupby("symbol", as_index=False).tail(1)

        # Build response
        result_items: List[ScannerResultItem] = []
        value_columns = [c for c in df.columns if c not in ("date", "symbol")]
        for _, row in df.iterrows():
            values = {c: (None if pd.isna(row[c]) else row[c]) for c in value_columns}
            result_items.append(
                ScannerResultItem(symbol=str(row["symbol"]), date=row["date"].to_pydatetime(), values=values)
            )

        return ScannerResponse(items=result_items, total=len(result_items))
    except Exception as e:
        logger.error(f"Error scanning: {e}")
        raise HTTPException(status_code=500, detail="Scan failed")


