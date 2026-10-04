"""Generate, rank, and persist explainable daily stock-pick snapshots.

The external interface is deliberately small: ``generate_snapshot`` performs a
point-in-time run, while ``save_snapshot`` and ``get_snapshot`` persist/read the
immutable result.  Signal generation, feature construction, model inference,
eligibility, and deterministic ranking stay behind that seam.
"""
from __future__ import annotations

import json
import math
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence, cast

import numpy as np
import pandas as pd
from clickhouse_connect.driver.client import Client
from loguru import logger

from app.core.settings import settings
from app.schemas.daily_picks import (
    DailyPick,
    DailyPickComponents,
    DailyPickHistory,
    DailyPickSnapshot,
)

DEFAULT_LIMIT = 10
MAX_LIMIT = 50
MAX_HISTORY_LIMIT = 365
MIN_HISTORY_BARS = 252
MIN_SESSION_COVERAGE = 0.80
MIN_LIQUIDITY_PERCENTILE = 0.40
HISTORY_CALENDAR_DAYS = 3 * 366
SIGNAL_VERSIONS = ("v1", "v2", "v4")
BENCHMARK_SYMBOL = "VNINDEX"

_SCORE_WEIGHTS = {
    "model_probability": 0.55,
    "relative_strength_rank": 0.20,
    "trend_rank": 0.10,
    "liquidity_rank": 0.10,
    "volatility_safety_rank": 0.05,
}
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _numeric_column(frame: pd.DataFrame, name: str, default: float) -> pd.Series:
    if name not in frame.columns:
        return pd.Series(default, index=frame.index, dtype="float64")
    return pd.Series(
        pd.to_numeric(frame[name], errors="coerce"),
        index=frame.index,
        dtype="float64",
    ).fillna(default)


def _percentile_rank(values: pd.Series, *, ascending: bool = True) -> pd.Series:
    numeric = pd.Series(
        pd.to_numeric(values, errors="coerce"),
        index=values.index,
        dtype="float64",
    )
    ranked = numeric.rank(method="average", pct=True, ascending=ascending)
    return ranked.fillna(0.5).clip(0.0, 1.0)


def rank_daily_candidates(
    candidates: pd.DataFrame,
    *,
    limit: int = DEFAULT_LIMIT,
    min_probability: float = 0.57,
) -> list[DailyPick]:
    """Rank model-scored strategy candidates with stable, inspectable inputs.

    Required columns are ``symbol`` and ``y_pred_ensemble``. Missing optional
    features are neutral rather than silently becoming either best or worst.
    The model threshold is applied before top-N, so weak days may return fewer
    than ``limit`` picks.
    """
    if not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
    if not 0.0 <= min_probability <= 1.0:
        raise ValueError("min_probability must be between 0 and 1")
    required = {"symbol", "y_pred_ensemble"}
    missing = required - set(candidates.columns)
    if missing:
        raise ValueError(f"candidate frame missing columns: {sorted(missing)}")
    if candidates.empty:
        return []

    ranked = cast(pd.DataFrame, candidates.copy())
    ranked["symbol"] = ranked["symbol"].astype(str).str.strip().str.upper()
    ranked["model_probability"] = _numeric_column(
        ranked, "y_pred_ensemble", 0.0
    ).clip(0.0, 1.0)
    ranked = cast(
        pd.DataFrame,
        ranked.loc[ranked["model_probability"] >= min_probability].copy(),
    )
    if ranked.empty:
        return []

    ranked["relative_strength_rank"] = _numeric_column(
        ranked, "msr_rank_20", 0.5
    ).clip(0.0, 1.0)
    ranked["trend_rank"] = _percentile_rank(
        _numeric_column(ranked, "trend_20", np.nan)
    )
    ranked["liquidity_rank"] = _numeric_column(
        ranked, "dollar_vol_pctile", 0.5
    ).clip(0.0, 1.0)

    ranked["volatility_rank"] = _percentile_rank(
        _numeric_column(ranked, "atr_pct", np.nan)
    )
    ranked["volatility_safety_rank"] = 1.0 - ranked["volatility_rank"]
    ranked["score"] = 100.0 * sum(
        ranked[name] * weight for name, weight in _SCORE_WEIGHTS.items()
    )

    # One symbol may trigger multiple TTM versions. Keep one scored row and
    # carry every agreeing signal into the explanation.
    ranked = cast(
        pd.DataFrame,
        ranked.sort_values(
            ["score", "model_probability", "symbol"],
            ascending=[False, False, True],
            kind="mergesort",
        ).drop_duplicates("symbol", keep="first"),
    )
    ranked = cast(pd.DataFrame, ranked.head(limit).reset_index(drop=True))

    picks: list[DailyPick] = []
    for position, (_, row) in enumerate(ranked.iterrows(), start=1):
        signals = row.get("signal_versions", [])
        if isinstance(signals, str):
            signals = [signals]
        elif not isinstance(signals, (list, tuple, set)):
            signals = []
        risk_flags = []
        if float(row["volatility_rank"]) >= 0.90:
            risk_flags.append("high_volatility")
        components = DailyPickComponents(
            model_probability=float(row["model_probability"]),
            relative_strength_rank=float(row["relative_strength_rank"]),
            trend_rank=float(row["trend_rank"]),
            liquidity_rank=float(row["liquidity_rank"]),
            volatility_safety_rank=float(row["volatility_safety_rank"]),
        )
        picks.append(
            DailyPick(
                rank=position,
                symbol=str(row["symbol"]),
                score=round(float(row["score"]), 4),
                model_probability=float(row["model_probability"]),
                signal_versions=sorted({str(v) for v in signals}),
                components=components,
                risk_flags=risk_flags,
            )
        )
    return picks


class DailyPicksService:
    def __init__(self, client: Client, watchlist_path: Path | None = None):
        self.client = client
        self.watchlist_path = watchlist_path or self._default_watchlist_path()
        self.database = self._identifier(settings.clickhouse_db, "database")
        self.ohlc_table = self._identifier(
            os.getenv("CLICKHOUSE_OHLC_EOD_TABLE", "ohlc_eod"), "OHLC table"
        )
        self.snapshot_table = self._identifier(
            os.getenv("CLICKHOUSE_DAILY_PICK_SNAPSHOTS_TABLE", "daily_pick_snapshots"),
            "snapshot table",
        )
        self.pick_table = self._identifier(
            os.getenv("CLICKHOUSE_DAILY_PICKS_TABLE", "daily_picks"), "picks table"
        )

    @staticmethod
    def _identifier(value: str, label: str) -> str:
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError(f"Invalid ClickHouse {label}: {value!r}")
        return value

    @staticmethod
    def _default_watchlist_path() -> Path:
        configured = Path(settings.model_path) / "watchlist.csv"
        if configured.exists():
            return configured
        return Path(__file__).resolve().parents[2] / "models" / "watchlist.csv"

    def _watchlist(self) -> list[str]:
        symbols = []
        for line in self.watchlist_path.read_text(encoding="utf-8").splitlines():
            symbol = line.strip().upper()
            if symbol and symbol not in {BENCHMARK_SYMBOL, "VN30"} and symbol not in symbols:
                symbols.append(symbol)
        if not symbols:
            raise ValueError(f"Watchlist is empty: {self.watchlist_path}")
        return symbols

    @staticmethod
    def _quoted_symbols(symbols: list[str]) -> str:
        return ", ".join(f"'{symbol.replace(chr(39), chr(39) * 2)}'" for symbol in symbols)

    def _resolve_as_of(self, symbols: list[str], requested: date | None) -> tuple[date, int]:
        minimum = max(1, math.ceil(len(symbols) * MIN_SESSION_COVERAGE))
        date_clause = (
            f"AND date = toDate('{requested.isoformat()}')" if requested is not None else ""
        )
        result = self.client.query(
            f"""
            SELECT date, countDistinct(symbol) AS coverage
            FROM {self.database}.{self.ohlc_table} FINAL
            WHERE symbol IN ({self._quoted_symbols(symbols)})
              {date_clause}
            GROUP BY date
            HAVING coverage >= {minimum}
            ORDER BY date DESC
            LIMIT 1
            """
        )
        if not result.result_rows:
            target = requested.isoformat() if requested else "latest session"
            raise ValueError(
                f"No complete EOD session for {target}; require at least "
                f"{minimum}/{len(symbols)} watchlist symbols"
            )
        resolved, coverage = result.result_rows[0]
        resolved_timestamp = pd.Timestamp(resolved)
        if resolved_timestamp is pd.NaT:
            raise ValueError("ClickHouse returned an invalid EOD session date")
        return cast(date, resolved_timestamp.date()), int(coverage)

    def _load_bars(self, symbols: list[str], as_of: date) -> pd.DataFrame:
        start = as_of - timedelta(days=HISTORY_CALENDAR_DAYS)
        all_symbols = [*symbols, BENCHMARK_SYMBOL]
        result = self.client.query(
            f"""
            SELECT date, symbol, open, high, low, close, volume
            FROM {self.database}.{self.ohlc_table} FINAL
            WHERE symbol IN ({self._quoted_symbols(all_symbols)})
              AND date BETWEEN toDate('{start.isoformat()}') AND toDate('{as_of.isoformat()}')
            ORDER BY date, symbol
            """
        )
        columns = pd.Index([str(name) for name in result.column_names])
        frame = pd.DataFrame(result.result_rows, columns=columns)
        if frame.empty:
            raise ValueError(f"No OHLCV data through {as_of.isoformat()}")
        frame["date"] = pd.to_datetime(frame["date"])
        for column in ("open", "high", "low", "close", "volume"):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        return frame.drop_duplicates(["date", "symbol"], keep="last")

    @staticmethod
    def _eligible_universe(
        raw: pd.DataFrame, watchlist: list[str], as_of: date
    ) -> tuple[list[str], pd.Series]:
        stocks = cast(pd.DataFrame, raw.loc[raw["symbol"].isin(watchlist)].copy())
        valid = cast(
            pd.DataFrame,
            stocks.loc[
                (stocks[["open", "high", "low", "close"]] > 0).all(axis=1)
                & (stocks["volume"] > 0)
            ].sort_values(["symbol", "date"]),
        )
        counts = cast(pd.Series, valid.groupby("symbol").size())
        latest = set(valid.loc[valid["date"].dt.date == as_of, "symbol"])
        turnover = cast(
            pd.DataFrame,
            valid.assign(turnover=valid["close"] * valid["volume"]),
        )
        average_turnover = cast(
            pd.Series,
            turnover.groupby("symbol", group_keys=False).tail(20)
            .groupby("symbol")["turnover"].mean(),
        )
        liquidity_rank = cast(
            pd.Series,
            average_turnover.rank(method="average", pct=True),
        )
        eligible = sorted(
            str(symbol) for symbol in latest
            if int(counts.get(symbol, 0) or 0) >= MIN_HISTORY_BARS
            and float(liquidity_rank.get(symbol, 0.0) or 0.0)
            >= MIN_LIQUIDITY_PERCENTILE
        )
        return eligible, liquidity_rank

    @staticmethod
    def _panel(raw: pd.DataFrame, symbols: list[str]) -> pd.DataFrame:
        selected = raw[raw["symbol"].isin([*symbols, BENCHMARK_SYMBOL])].copy()
        invalid = (
            (selected[["open", "high", "low", "close"]] <= 0).any(axis=1)
            | (selected["volume"] <= 0)
        )
        selected.loc[invalid, ["open", "high", "low", "close", "volume"]] = np.nan
        panel = (
            selected.set_index(["date", "symbol"])
            .sort_index()
            .unstack("symbol")
            .sort_index(axis=1)
            .ffill()
        )
        return cast(pd.DataFrame, panel)

    @staticmethod
    def _fresh_signals(panel: pd.DataFrame, as_of: date) -> dict[str, list[str]]:
        from app.services.strategies import BreakoutTTMV1

        target = pd.Timestamp(as_of)
        signals: dict[str, list[str]] = {}
        for version in SIGNAL_VERSIONS:
            entries = BreakoutTTMV1(panel, version).get_entries().fillna(False)
            if target not in entries.index:
                continue
            for symbol, active in entries.loc[target].items():
                if bool(active):
                    signals.setdefault(str(symbol), []).append(version)
        return signals

    @staticmethod
    def _model_metadata() -> tuple[str, float]:
        models_dir = Path(settings.model_path)
        matches = sorted(
            models_dir.glob("meta_label_training_metadata_*.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if not matches:
            raise FileNotFoundError(f"No meta-label metadata in {models_dir}")
        path = matches[0]
        metadata = json.loads(path.read_text(encoding="utf-8"))
        ensemble = metadata.get("best_ensemble_name", "Ensemble: Val-AUC Weighted")
        threshold = float(
            metadata.get("holdout_metrics", {}).get(ensemble, {}).get("opt_threshold", 0.57)
        )
        return path.stem.removeprefix("meta_label_training_metadata_"), threshold

    def generate_snapshot(
        self, *, as_of: date | None = None, limit: int = DEFAULT_LIMIT
    ) -> DailyPickSnapshot:
        if not 1 <= limit <= MAX_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
        watchlist = self._watchlist()
        resolved, coverage = self._resolve_as_of(watchlist, as_of)
        raw = self._load_bars(watchlist, resolved)
        eligible, liquidity_rank = self._eligible_universe(raw, watchlist, resolved)
        model_version, threshold = self._model_metadata()
        generated_at = datetime.now(timezone.utc)

        if not eligible:
            return DailyPickSnapshot(
                as_of=resolved,
                generated_at=generated_at,
                model_version=model_version,
                universe_size=len(watchlist),
                eligible_size=0,
                candidate_size=0,
                picks=[],
            )

        panel = self._panel(raw, eligible)
        tradable_panel = panel.loc[
            :, panel.columns.get_level_values("symbol").isin(eligible)
        ]
        signals = self._fresh_signals(tradable_panel, resolved)
        if not signals:
            return DailyPickSnapshot(
                as_of=resolved,
                generated_at=generated_at,
                model_version=model_version,
                universe_size=len(watchlist),
                eligible_size=len(eligible),
                candidate_size=0,
                picks=[],
            )

        from app.services.backtest_service import build_features_meta, predict_features_meta

        target = pd.Timestamp(resolved)
        columns = list(tradable_panel["close"].columns)
        entry_idx = int(tradable_panel.index.get_loc(target))
        candidate_rows = [
            {
                "symbol": symbol,
                "date": target,
                "col": columns.index(symbol),
                "entry_idx": entry_idx,
                "type": "open_trades",
                "signal_versions": versions,
            }
            for symbol, versions in signals.items()
        ]
        candidate_frame = pd.DataFrame(candidate_rows)
        features = build_features_meta(panel, candidate_frame)
        scored = predict_features_meta(features)
        liquidity_lookup = {
            str(symbol): float(value)
            for symbol, value in liquidity_rank.items()
            if value is not None
        }
        scored["dollar_vol_pctile"] = [
            liquidity_lookup.get(str(symbol), np.nan)
            for symbol in scored["symbol"]
        ]
        picks = rank_daily_candidates(
            scored, limit=limit, min_probability=threshold
        )
        logger.info(
            "Daily picks generated: as_of={}, coverage={}, eligible={}, candidates={}, picks={}",
            resolved, coverage, len(eligible), len(candidate_frame), len(picks),
        )
        return DailyPickSnapshot(
            as_of=resolved,
            generated_at=generated_at,
            model_version=model_version,
            universe_size=len(watchlist),
            eligible_size=len(eligible),
            candidate_size=len(candidate_frame),
            picks=picks,
        )

    def _ensure_store(self) -> None:
        self.client.command(
            f"""
            CREATE TABLE IF NOT EXISTS {self.database}.{self.snapshot_table} (
                as_of Date,
                generated_at DateTime64(3, 'UTC'),
                model_version String,
                universe_size UInt32,
                eligible_size UInt32,
                candidate_size UInt32
            ) ENGINE = MergeTree
            PARTITION BY toYYYYMM(as_of)
            ORDER BY (as_of, generated_at)
            """
        )
        self.client.command(
            f"""
            CREATE TABLE IF NOT EXISTS {self.database}.{self.pick_table} (
                as_of Date,
                generated_at DateTime64(3, 'UTC'),
                rank UInt16,
                symbol LowCardinality(String),
                score Float64,
                model_probability Float64,
                signal_versions Array(String),
                components String,
                risk_flags Array(String)
            ) ENGINE = MergeTree
            PARTITION BY toYYYYMM(as_of)
            ORDER BY (as_of, generated_at, rank)
            """
        )

    def save_snapshot(self, snapshot: DailyPickSnapshot) -> None:
        self._ensure_store()
        self.client.insert(
            f"{self.database}.{self.snapshot_table}",
            [[
                snapshot.as_of,
                snapshot.generated_at,
                snapshot.model_version,
                snapshot.universe_size,
                snapshot.eligible_size,
                snapshot.candidate_size,
            ]],
            column_names=[
                "as_of", "generated_at", "model_version", "universe_size",
                "eligible_size", "candidate_size",
            ],
        )
        if snapshot.picks:
            self.client.insert(
                f"{self.database}.{self.pick_table}",
                [[
                    snapshot.as_of,
                    snapshot.generated_at,
                    pick.rank,
                    pick.symbol,
                    pick.score,
                    pick.model_probability,
                    pick.signal_versions,
                    json.dumps(pick.components.model_dump(), separators=(",", ":")),
                    pick.risk_flags,
                ] for pick in snapshot.picks],
                column_names=[
                    "as_of", "generated_at", "rank", "symbol", "score",
                    "model_probability", "signal_versions", "components", "risk_flags",
                ],
            )

    def get_snapshot(self, as_of: date | None = None) -> DailyPickSnapshot | None:
        self._ensure_store()
        where = f"WHERE as_of = toDate('{as_of.isoformat()}')" if as_of else ""
        meta = self.client.query(
            f"""
            SELECT as_of, generated_at, model_version, universe_size,
                   eligible_size, candidate_size
            FROM {self.database}.{self.snapshot_table}
            {where}
            ORDER BY as_of DESC, generated_at DESC
            LIMIT 1
            """
        )
        if not meta.result_rows:
            return None
        row = meta.result_rows[0]
        snapshot_timestamp = pd.Timestamp(row[0])
        generated_timestamp = pd.Timestamp(row[1])
        if snapshot_timestamp is pd.NaT or generated_timestamp is pd.NaT:
            raise ValueError("Stored daily-pick snapshot has an invalid timestamp")
        snapshot_date = cast(date, snapshot_timestamp.date())
        generated_at = cast(datetime, generated_timestamp.to_pydatetime())
        picks_result = self.client.query(
            f"""
            SELECT rank, symbol, score, model_probability, signal_versions,
                   components, risk_flags
            FROM {self.database}.{self.pick_table}
            WHERE as_of = toDate('{snapshot_date.isoformat()}')
              AND generated_at = toDateTime64('{generated_at.isoformat()}', 3, 'UTC')
            ORDER BY rank
            """
        )
        picks = [self._pick_from_row(pick) for pick in picks_result.result_rows]
        return DailyPickSnapshot(
            as_of=snapshot_date,
            generated_at=generated_at,
            model_version=str(row[2]),
            universe_size=int(row[3]),
            eligible_size=int(row[4]),
            candidate_size=int(row[5]),
            picks=picks,
        )

    @staticmethod
    def _pick_from_row(row: Sequence[Any], offset: int = 0) -> DailyPick:
        return DailyPick(
            rank=int(row[offset]),
            symbol=str(row[offset + 1]),
            score=float(row[offset + 2]),
            model_probability=float(row[offset + 3]),
            signal_versions=list(row[offset + 4]),
            components=DailyPickComponents(**json.loads(row[offset + 5])),
            risk_flags=list(row[offset + 6]),
        )

    def get_history(self, limit: int = 60) -> DailyPickHistory:
        """Return the latest generation for each recent session, oldest first."""
        if not 1 <= limit <= MAX_HISTORY_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_HISTORY_LIMIT}")
        self._ensure_store()
        meta = self.client.query(
            f"""
            SELECT
                as_of,
                max(generated_at) AS latest_generated_at,
                argMax(model_version, generated_at) AS model_version,
                argMax(universe_size, generated_at) AS universe_size,
                argMax(eligible_size, generated_at) AS eligible_size,
                argMax(candidate_size, generated_at) AS candidate_size
            FROM {self.database}.{self.snapshot_table}
            GROUP BY as_of
            ORDER BY as_of DESC
            LIMIT {limit}
            """
        )
        if not meta.result_rows:
            return DailyPickHistory()

        metadata: list[tuple[date, datetime, Sequence[Any]]] = []
        selectors: list[str] = []
        for row in reversed(meta.result_rows):
            snapshot_timestamp = pd.Timestamp(row[0])
            generated_timestamp = pd.Timestamp(row[1])
            if snapshot_timestamp is pd.NaT or generated_timestamp is pd.NaT:
                raise ValueError("Stored daily-pick history has an invalid timestamp")
            snapshot_date = cast(date, snapshot_timestamp.date())
            generated_at = cast(datetime, generated_timestamp.to_pydatetime())
            metadata.append((snapshot_date, generated_at, row))
            selectors.append(
                f"(as_of = toDate('{snapshot_date.isoformat()}') "
                f"AND generated_at = toDateTime64('{generated_at.isoformat()}', 3, 'UTC'))"
            )

        picks_result = self.client.query(
            f"""
            SELECT as_of, generated_at, rank, symbol, score, model_probability,
                   signal_versions, components, risk_flags
            FROM {self.database}.{self.pick_table}
            WHERE {' OR '.join(selectors)}
            ORDER BY as_of, generated_at, rank
            """
        )
        picks_by_snapshot: dict[tuple[str, str], list[DailyPick]] = {}
        for row in picks_result.result_rows:
            pick_date_timestamp = pd.Timestamp(row[0])
            pick_generated_timestamp = pd.Timestamp(row[1])
            if pick_date_timestamp is pd.NaT or pick_generated_timestamp is pd.NaT:
                raise ValueError("Stored daily-pick history has an invalid pick timestamp")
            pick_date = cast(date, pick_date_timestamp.date())
            pick_generated_at = cast(datetime, pick_generated_timestamp.to_pydatetime())
            key = (pick_date.isoformat(), pick_generated_at.isoformat())
            picks_by_snapshot.setdefault(key, []).append(self._pick_from_row(row, offset=2))

        snapshots = []
        for snapshot_date, generated_at, row in metadata:
            key = (snapshot_date.isoformat(), generated_at.isoformat())
            snapshots.append(
                DailyPickSnapshot(
                    as_of=snapshot_date,
                    generated_at=generated_at,
                    model_version=str(row[2]),
                    universe_size=int(row[3]),
                    eligible_size=int(row[4]),
                    candidate_size=int(row[5]),
                    picks=picks_by_snapshot.get(key, []),
                )
            )
        return DailyPickHistory(snapshots=snapshots)

    def generate_and_save(
        self, *, as_of: date | None = None, limit: int = DEFAULT_LIMIT
    ) -> DailyPickSnapshot:
        snapshot = self.generate_snapshot(as_of=as_of, limit=limit)
        self.save_snapshot(snapshot)
        return snapshot
