"""Trade-flow anomaly detection — Isolation Forest over windowed features.

Replaces the z-score block-episode detector. The worker no longer computes
anything: ClickHouse maintains `trade_flow_windows` (a view over a materialized
view on `ticks`), and this service reads those features, normalizes them, and
scores on demand.

Why this shape, given the feed. The tape carries trades, not Market-By-Order —
no resting book, no order IDs, no quotes, no adds/cancels — so OFI, book
imbalance, replenishment and queue depletion are not computable. What is
available is trade flow, and the features target the parts of it that carry
signal: size concentration, temporal clustering, directional imbalance and
price impact.

Isolation Forest answers one question — "is this window unusual?" — as a
point-in-time verdict over the whole normalized feature vector. It says nothing
about persistence: a lone odd window and the first of a sustained run score the
same. (A CUSUM pass over the normalized imbalance used to add that, and was
dropped.)

Normalization is robust and per symbol *and* time-of-day, because 09:15 does not
behave like 13:45 and an illiquid ticker is not comparable to HPG:

    z = (x - median(symbol, tod_bucket)) / (1.4826 * MAD(symbol, tod_bucket))

Median/MAD rather than mean/std so the outliers being hunted do not inflate the
scale that is supposed to reveal them.

Interpretation caveat: aggressor side is real (from the feed), so imbalance is
directional — but "absorption" still cannot tell you *who* absorbed whom
without book data. A footprint is evidence of sustained one-sided execution,
not proof of an institution.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import List, Optional

import numpy as np
import pandas as pd
from clickhouse_connect.driver import Client
from clickhouse_connect.driver.exceptions import DatabaseError
from loguru import logger
from pydantic import BaseModel

WINDOWS_VIEW = "trade_flow_windows"

# Features fed to Isolation Forest. Mirrors core.trade_flow.FEATURE_COLUMNS in
# the worker; kept explicit here so the API does not silently change shape when
# the worker adds a column.
FEATURE_COLUMNS: tuple[str, ...] = (
    "trade_count",
    "volume",
    "trades_per_second",
    "volume_per_second",
    "active_ratio",
    "max_trades_per_second",
    "burstiness",
    "median_interarrival_ms",
    "p90_interarrival_ms",
    "same_ms_share",
    "avg_trade_size",
    "median_trade_size",
    "p95_trade_size",
    "max_trade_size",
    "p95_to_median",
    "size_hhi",
    "top_trade_share",
    "ret",
    "price_range",
    "realized_vol",
    "trade_imbalance",
    "count_imbalance",
    "impact",
    "absorption",
)

# Heavy-tailed by nature; compressed before normalization so a single auction
# print cannot dominate the scale.
_LOG_FEATURES = frozenset(
    {
        "trade_count",
        "volume",
        "trades_per_second",
        "volume_per_second",
        "max_trades_per_second",
        "avg_trade_size",
        "median_trade_size",
        "p95_trade_size",
        "max_trade_size",
        "p95_to_median",
        "burstiness",
        "absorption",
        "median_interarrival_ms",
        "p90_interarrival_ms",
    }
)

_MAD_TO_SIGMA = 1.4826

# Robust z-scores are clipped to this magnitude. A feature that barely moves
# inside a time-of-day bucket has a near-zero MAD, and dividing by it produces
# z in the hundreds (measured: price_range reached 1749). Unclipped, that one
# degenerate scale dominates every Isolation Forest split and the other twenty
# features stop mattering. Clipping keeps "this is extreme" without letting the
# magnitude of extremeness swamp the model.
_Z_CLIP = 10.0


class TradeFlowWindow(BaseModel):
    symbol: str
    window_start: str          # ISO8601 UTC
    time: int                  # unix seconds (chart x)
    trade_count: int
    volume: int
    vwap: Optional[float]
    ret: Optional[float]
    realized_vol: Optional[float]
    trade_imbalance: Optional[float]
    max_trade_size: int
    size_hhi: Optional[float]
    top_trade_share: Optional[float]
    burstiness: Optional[float]
    median_interarrival_ms: Optional[float]
    same_ms_share: Optional[float]
    impact: Optional[float]
    absorption: Optional[float]
    # scores
    anomaly_score: float       # higher = more unusual (Isolation Forest)
    is_anomaly: bool
    side: int                  # 1=BUY-leaning, 2=SELL-leaning, 0=neutral
    # forward returns for validation (None where the horizon runs off the range)
    fwd_ret_1m: Optional[float]
    fwd_ret_5m: Optional[float]
    fwd_ret_15m: Optional[float]


class TradeFlowResponse(BaseModel):
    symbol: str
    window_seconds: int
    windows_scanned: int
    anomalies_found: int
    note: Optional[str] = None
    windows: List[TradeFlowWindow]


def _is_unknown_table(exc: Exception) -> bool:
    """True for ClickHouse 'table does not exist' (code 60).

    The feature view is created by
    ``worker/workers/block_episode_ingest.py --setup``; before that runs a fresh
    cluster legitimately has nothing, which is an empty result, not an error.
    """
    msg = str(exc)
    return "UNKNOWN_TABLE" in msg or "code: 60" in msg or "Unknown table" in msg


def _epoch(dt) -> int:
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def robust_z(frame: pd.DataFrame, group: pd.Series) -> pd.DataFrame:
    """Median/MAD z-scores computed within each `group`.

    A zero MAD means the feature does not move inside that bucket, so it carries
    no information there and is returned as 0 rather than ±inf. Buckets thinner
    than 8 rows fall back to the column's overall median/MAD, since a MAD from 3
    points is noise pretending to be a scale.
    """
    out = pd.DataFrame(index=frame.index, columns=frame.columns, dtype=float)
    overall_med = frame.median()
    overall_mad = (frame - overall_med).abs().median()
    fallback = overall_mad * _MAD_TO_SIGMA

    for _, idx in frame.groupby(group).groups.items():
        block = frame.loc[idx]
        if len(block) >= 8:
            med = block.median()
            mad = (block - med).abs().median()
        else:
            med, mad = overall_med, overall_mad
        scale = mad * _MAD_TO_SIGMA
        # Fall back to the overall scale where this bucket is degenerate; a
        # still-zero scale becomes NaN so the division yields NaN, which the
        # final fillna turns into 0 (no variation => no information).
        scale = scale.where(scale > 0, fallback)
        scale = scale.mask(scale <= 0)
        # axis=1 is load-bearing: `med` and `scale` are indexed by feature name,
        # and the default alignment for DataFrame.div/sub is over the index, not
        # the columns. Getting this wrong silently produces an all-NaN matrix.
        out.loc[idx] = block.sub(med, axis=1).div(scale, axis=1)

    return (
        out.replace([np.inf, -np.inf], np.nan)
        .fillna(0.0)
        .clip(lower=-_Z_CLIP, upper=_Z_CLIP)
    )


class TradeFlowService:
    def __init__(
        self,
        client: Client,
        window_seconds: int = 30,
        tod_bucket_minutes: int = 30,
        min_windows_to_fit: int = 200,
        contamination: float = 0.01,
    ):
        self.client = client
        self.window_seconds = window_seconds
        self.tod_bucket_minutes = tod_bucket_minutes
        self.min_windows_to_fit = min_windows_to_fit
        self.contamination = contamination

    # -- data ---------------------------------------------------------------
    def _fetch(self, symbol: str, from_day: date, to_day: date) -> pd.DataFrame:
        cols = ", ".join(
            ("symbol", "window_start", "vwap") + FEATURE_COLUMNS
        )
        sql = f"""
            SELECT {cols}
            FROM {WINDOWS_VIEW}
            WHERE symbol = {{symbol:String}}
              AND toDate(window_start, 'Asia/Ho_Chi_Minh')
                  BETWEEN {{from:String}} AND {{to:String}}
            ORDER BY window_start
        """
        params = {
            "symbol": symbol,
            "from": from_day.isoformat(),
            "to": to_day.isoformat(),
        }
        try:
            result = self.client.query(sql, parameters=params)
        except DatabaseError as exc:
            if _is_unknown_table(exc):
                logger.warning(
                    "{} missing — run block_episode_ingest.py --setup", WINDOWS_VIEW
                )
                return pd.DataFrame()
            raise
        return pd.DataFrame(result.result_rows, columns=result.column_names)

    # -- scoring ------------------------------------------------------------
    def get_anomalies(
        self,
        symbol: str,
        from_day: date,
        to_day: date,
        limit: int = 500,
        only_flagged: bool = True,
    ) -> TradeFlowResponse:
        df = self._fetch(symbol, from_day, to_day)
        if df.empty:
            return TradeFlowResponse(
                symbol=symbol,
                window_seconds=self.window_seconds,
                windows_scanned=0,
                anomalies_found=0,
                note=f"no windows in {WINDOWS_VIEW} for this symbol/range",
                windows=[],
            )

        df = df.reset_index(drop=True)
        feats = df[list(FEATURE_COLUMNS)].apply(pd.to_numeric, errors="coerce")
        # Only volume-bearing windows are scorable; an empty window has no shape.
        feats = feats.fillna(0.0)
        for col in _LOG_FEATURES:
            feats[col] = np.log1p(feats[col].clip(lower=0))

        ts = pd.to_datetime(df["window_start"], utc=True)
        local = ts.dt.tz_convert("Asia/Ho_Chi_Minh")
        tod_bucket = (
            local.dt.hour * 60 + local.dt.minute
        ) // self.tod_bucket_minutes

        z = robust_z(feats, tod_bucket)

        note = None
        n = len(z)
        if n >= self.min_windows_to_fit:
            from sklearn.ensemble import IsolationForest

            forest = IsolationForest(
                n_estimators=200,
                contamination=self.contamination,
                random_state=0,
                n_jobs=-1,
            )
            forest.fit(z.values)
            # score_samples: lower = more abnormal. Flip so higher = more unusual.
            anomaly_score = -forest.score_samples(z.values)
            is_anomaly = forest.predict(z.values) == -1
        else:
            anomaly_score = np.zeros(n)
            is_anomaly = np.zeros(n, dtype=bool)
            note = (
                f"only {n} windows (< {self.min_windows_to_fit}); Isolation Forest "
                "not fitted, so nothing is scored. Widen the date range."
            )

        fwd = self._forward_returns(df)

        rows: List[TradeFlowWindow] = []
        for i in range(n):
            if only_flagged and not is_anomaly[i]:
                continue
            burst = df.at[i, "burstiness"]
            burst = float(burst) if burst is not None and not pd.isna(burst) else 0.0
            # 1 = peak second equals the window's own mean rate; only clustered
            # windows (max_trades_per_second / mean) are worth returning.
            if burst <= 1:
                continue
            imb = df.at[i, "trade_imbalance"]
            imb = float(imb) if imb is not None and not pd.isna(imb) else 0.0
            rows.append(
                TradeFlowWindow(
                    symbol=df.at[i, "symbol"],
                    window_start=ts.iloc[i].isoformat(),
                    time=int(ts.iloc[i].timestamp()),
                    trade_count=int(df.at[i, "trade_count"] or 0),
                    volume=int(df.at[i, "volume"] or 0),
                    vwap=_f(df.at[i, "vwap"]),
                    ret=_f(df.at[i, "ret"]),
                    realized_vol=_f(df.at[i, "realized_vol"]),
                    trade_imbalance=imb,
                    max_trade_size=int(df.at[i, "max_trade_size"] or 0),
                    size_hhi=_f(df.at[i, "size_hhi"]),
                    top_trade_share=_f(df.at[i, "top_trade_share"]),
                    burstiness=burst,
                    median_interarrival_ms=_f(df.at[i, "median_interarrival_ms"]),
                    same_ms_share=_f(df.at[i, "same_ms_share"]),
                    impact=_f(df.at[i, "impact"]),
                    absorption=_f(df.at[i, "absorption"]),
                    anomaly_score=float(anomaly_score[i]),
                    is_anomaly=bool(is_anomaly[i]),
                    side=1 if imb > 0.05 else (2 if imb < -0.05 else 0),
                    fwd_ret_1m=_f(fwd["1m"][i]),
                    fwd_ret_5m=_f(fwd["5m"][i]),
                    fwd_ret_15m=_f(fwd["15m"][i]),
                )
            )

        rows.sort(key=lambda w: w.anomaly_score, reverse=True)
        return TradeFlowResponse(
            symbol=symbol,
            window_seconds=self.window_seconds,
            windows_scanned=n,
            anomalies_found=int(is_anomaly.sum()),
            note=note,
            windows=rows[:limit],
        )

    def _forward_returns(self, df: pd.DataFrame) -> dict[str, np.ndarray]:
        """VWAP-to-VWAP forward returns at 1/5/15 minutes.

        Shifted by whole windows, so these are only meaningful where the
        session is contiguous; gaps (lunch break, day boundaries) make a shifted
        window land further ahead than the label claims. Treat as a rough
        validation aid, not a backtest.
        """
        vwap = pd.to_numeric(df["vwap"], errors="coerce")
        out = {}
        for label, minutes in (("1m", 1), ("5m", 5), ("15m", 15)):
            steps = max(1, int(minutes * 60 / self.window_seconds))
            future = vwap.shift(-steps)
            out[label] = (future / vwap - 1.0).to_numpy()
        return out


def _f(v) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if not np.isfinite(f) else f
