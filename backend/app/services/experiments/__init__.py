"""Experiment store: persist backtest runs as Parquet for DuckDB analysis."""
from __future__ import annotations

from app.services.experiments.schema import SCHEMA_VERSION

__all__ = ["SCHEMA_VERSION"]
