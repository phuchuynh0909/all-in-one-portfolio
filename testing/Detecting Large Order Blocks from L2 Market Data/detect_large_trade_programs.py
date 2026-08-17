#!/usr/bin/env python3
"""Detect large block-like execution activity from trade-tape data.

Input columns required by default:
    timestamp, side, match_qty, match_price

Optional but strongly recommended:
    symbol, venue, sequence_or_trade_id, trade_condition

This is a trade-flow MVP, not a Level 2 order-book reconstruction. It flags:
  1. large individual trade prints, relative to prior trades; and
  2. block-like directional flow clusters, based on causal rolling signed volume,
     signed-notional surprise, one-sidedness, and persistence.

The supplied `side` is assumed to be the aggressor/taker side unless
--side-is-resting is passed. Confirm that convention with the data provider.

Example:
    python detect_large_trade_programs.py trades.csv --symbol-col symbol \
        --output-dir output --timestamp-unit auto

Outputs:
    event_features.csv       One row per input trade plus rolling features.
    bin_features.csv         One row per symbol and time bin.
    block_episodes.csv       Stitched block-like directional flow episodes.
    run_metadata.json        Parameters, input columns, and output row counts.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import pandas as pd


REQUIRED_DEFAULT_COLUMNS = ("timestamp", "side", "match_qty", "match_price")


@dataclass(frozen=True)
class Parameters:
    bin_seconds: int
    rolling_seconds: int
    baseline_bins: int
    min_baseline_bins: int
    z_threshold: float
    imbalance_threshold: float
    min_trades: int
    min_consecutive_bins: int
    large_print_quantile: float
    large_print_lookback_trades: int
    min_print_history: int
    episode_gap_bins: int
    side_is_resting: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compute causal rolling signed-volume features and flag large block-like trade-flow episodes."
    )
    parser.add_argument("input_path", type=Path, help="Input CSV, CSV.GZ, Parquet, or PQ file.")
    parser.add_argument(
        "--output-dir", type=Path, default=Path("large_trade_output"), help="Directory for output files."
    )
    parser.add_argument("--timestamp-col", default="timestamp", help="Input timestamp column name.")
    parser.add_argument("--side-col", default="side", help="Input aggressor-side column name.")
    parser.add_argument("--qty-col", default="match_qty", help="Input matched-quantity column name.")
    parser.add_argument("--price-col", default="match_price", help="Input matched-price column name.")
    parser.add_argument(
        "--symbol-col",
        default=None,
        help="Optional symbol column. Omit only when the file contains exactly one instrument.",
    )
    parser.add_argument(
        "--timestamp-unit",
        choices=("auto", "s", "ms", "us", "ns"),
        default="auto",
        help="Unit for numeric timestamps. Text timestamps are parsed directly. Default: auto.",
    )
    parser.add_argument(
        "--buy-values",
        default="B,BUY,BUYER,1,+1",
        help="Comma-separated values representing a buy aggressor. Default: B,BUY,BUYER,1,+1",
    )
    parser.add_argument(
        "--sell-values",
        default="S,SELL,SELLER,-1",
        help="Comma-separated values representing a sell aggressor. Default: S,SELL,SELLER,-1",
    )
    parser.add_argument(
        "--side-is-resting",
        action="store_true",
        help="Flip input signs when side identifies the resting/maker side instead of the aggressor side.",
    )
    parser.add_argument("--bin-seconds", type=int, default=1, help="Aggregation bin width in seconds. Default: 1.")
    parser.add_argument(
        "--rolling-seconds", type=int, default=30, help="Rolling event-window width in seconds. Default: 30."
    )
    parser.add_argument(
        "--baseline-bins",
        type=int,
        default=1800,
        help="Prior-only rolling baseline length in bins. Default: 1800 (30 minutes for 1-second bins).",
    )
    parser.add_argument(
        "--min-baseline-bins",
        type=int,
        default=300,
        help="Minimum prior bins before a flow z-score is valid. Default: 300.",
    )
    parser.add_argument("--z-threshold", type=float, default=2.5, help="Absolute signed-notional z-score threshold.")
    parser.add_argument(
        "--imbalance-threshold", type=float, default=0.70, help="Absolute trade-flow imbalance threshold [0, 1]."
    )
    parser.add_argument("--min-trades", type=int, default=3, help="Minimum trades in a qualifying bin.")
    parser.add_argument(
        "--min-consecutive-bins",
        type=int,
        default=2,
        help="Minimum same-direction qualifying bin run for a causal flow-cluster flag.",
    )
    parser.add_argument(
        "--large-print-quantile", type=float, default=0.99, help="Prior trade-notional quantile for a large print."
    )
    parser.add_argument(
        "--large-print-lookback-trades",
        type=int,
        default=500,
        help="Prior trades used for the rolling large-print threshold.",
    )
    parser.add_argument(
        "--min-print-history",
        type=int,
        default=100,
        help="Minimum prior trades before flagging individual large prints.",
    )
    parser.add_argument(
        "--episode-gap-bins",
        type=int,
        default=5,
        help="Maximum non-candidate gap, in bins, used to stitch same-direction candidates.",
    )
    args = parser.parse_args()

    if args.bin_seconds <= 0 or args.rolling_seconds <= 0:
        parser.error("--bin-seconds and --rolling-seconds must be positive.")
    if args.baseline_bins <= 1 or args.min_baseline_bins <= 1:
        parser.error("Baseline lengths must exceed one bin.")
    if args.min_baseline_bins > args.baseline_bins:
        parser.error("--min-baseline-bins cannot exceed --baseline-bins.")
    if not 0 < args.imbalance_threshold <= 1:
        parser.error("--imbalance-threshold must be in (0, 1].")
    if not 0 < args.large_print_quantile < 1:
        parser.error("--large-print-quantile must be in (0, 1).")
    if args.min_print_history > args.large_print_lookback_trades:
        parser.error("--min-print-history cannot exceed --large-print-lookback-trades.")
    return args


def read_input(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Input file does not exist: {path}")
    suffixes = "".join(path.suffixes).lower()
    if suffixes.endswith(".parquet") or suffixes.endswith(".pq"):
        return pd.read_parquet(path)
    return pd.read_csv(path)


def infer_numeric_timestamp_unit(values: pd.Series) -> str:
    """Infer a likely Unix epoch unit from magnitude; users may override this."""
    finite = pd.to_numeric(values, errors="coerce").dropna()
    if finite.empty:
        raise ValueError("Timestamp column has no parseable numeric values.")
    magnitude = float(finite.abs().median())
    if magnitude >= 1e17:
        return "ns"
    if magnitude >= 1e14:
        return "us"
    if magnitude >= 1e11:
        return "ms"
    return "s"


def parse_timestamps(values: pd.Series, requested_unit: str) -> tuple[pd.Series, Optional[str]]:
    """Return UTC timestamps and the inferred numeric unit, if used."""
    numeric = pd.api.types.is_numeric_dtype(values)
    if numeric:
        unit = infer_numeric_timestamp_unit(values) if requested_unit == "auto" else requested_unit
        parsed = pd.to_datetime(values, unit=unit, utc=True, errors="coerce")
        return parsed, unit

    parsed = pd.to_datetime(values, utc=True, errors="coerce")
    return parsed, None


def parse_values(text: str) -> set[str]:
    return {value.strip().upper() for value in text.split(",") if value.strip()}


def map_sides(
    raw_side: pd.Series,
    buy_values: set[str],
    sell_values: set[str],
    side_is_resting: bool,
) -> pd.Series:
    normalized = raw_side.astype(str).str.strip().str.upper()
    sign = pd.Series(np.nan, index=raw_side.index, dtype="float64")
    sign.loc[normalized.isin(buy_values)] = 1.0
    sign.loc[normalized.isin(sell_values)] = -1.0
    unknown = normalized[sign.isna()].drop_duplicates().tolist()
    if unknown:
        examples = ", ".join(repr(value) for value in unknown[:10])
        raise ValueError(
            "Unrecognised side values: "
            f"{examples}. Update --buy-values/--sell-values, or verify the input-side semantics."
        )
    if side_is_resting:
        sign *= -1.0
    return sign.astype("int8")


def validate_and_prepare(raw: pd.DataFrame, args: argparse.Namespace) -> tuple[pd.DataFrame, Optional[str]]:
    required = {args.timestamp_col, args.side_col, args.qty_col, args.price_col}
    missing = sorted(required.difference(raw.columns))
    if missing:
        raise ValueError(f"Missing required input columns: {missing}. Present columns: {list(raw.columns)}")
    if args.symbol_col and args.symbol_col not in raw.columns:
        raise ValueError(f"--symbol-col {args.symbol_col!r} was not found in the input file.")

    data = raw.copy()
    data["_source_order"] = np.arange(len(data), dtype="int64")
    timestamps, inferred_unit = parse_timestamps(data[args.timestamp_col], args.timestamp_unit)
    data["timestamp"] = timestamps
    data["match_qty"] = pd.to_numeric(data[args.qty_col], errors="coerce")
    data["match_price"] = pd.to_numeric(data[args.price_col], errors="coerce")
    data["aggressor_sign"] = map_sides(
        data[args.side_col],
        parse_values(args.buy_values),
        parse_values(args.sell_values),
        args.side_is_resting,
    )
    data["symbol"] = data[args.symbol_col].astype(str) if args.symbol_col else "ALL"

    invalid = (
        data["timestamp"].isna()
        | data["match_qty"].isna()
        | data["match_price"].isna()
        | (data["match_qty"] <= 0)
        | (data["match_price"] <= 0)
    )
    if invalid.any():
        examples = data.index[invalid].tolist()[:10]
        raise ValueError(
            f"Found {int(invalid.sum())} invalid rows (timestamp, quantity, or price). "
            f"Example input row indices: {examples}. Fix them before detection."
        )

    data = data.sort_values(["symbol", "timestamp", "_source_order"], kind="mergesort").reset_index(drop=True)
    data["notional"] = data["match_qty"] * data["match_price"]
    data["signed_volume"] = data["aggressor_sign"] * data["match_qty"]
    data["signed_notional"] = data["aggressor_sign"] * data["notional"]
    data["is_buy"] = (data["aggressor_sign"] == 1).astype("int8")
    data["is_sell"] = (data["aggressor_sign"] == -1).astype("int8")
    return data, inferred_unit


def add_event_features(data: pd.DataFrame, params: Parameters) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    window = f"{params.rolling_seconds}s"
    for symbol, group in data.groupby("symbol", sort=False, group_keys=False):
        group = group.copy().set_index("timestamp", drop=False)
        group["rolling_signed_volume"] = group["signed_volume"].rolling(window, closed="both").sum()
        group["rolling_total_volume"] = group["match_qty"].rolling(window, closed="both").sum()
        group["rolling_signed_notional"] = group["signed_notional"].rolling(window, closed="both").sum()
        group["rolling_flow_imbalance"] = np.divide(
            group["rolling_signed_volume"],
            group["rolling_total_volume"],
            out=np.zeros(len(group), dtype="float64"),
            where=group["rolling_total_volume"].to_numpy() > 0,
        )
        prior_notional = group["notional"].shift(1)
        group["large_print_threshold_notional"] = prior_notional.rolling(
            params.large_print_lookback_trades,
            min_periods=params.min_print_history,
        ).quantile(params.large_print_quantile)
        group["is_large_print"] = (
            group["notional"] >= group["large_print_threshold_notional"]
        ).fillna(False)
        frames.append(group.reset_index(drop=True))
    return pd.concat(frames, ignore_index=True).sort_values("_source_order", kind="mergesort").reset_index(drop=True)


def direction_run(values: Iterable[int]) -> list[int]:
    """Return the current non-zero same-direction run length at every observation."""
    previous = 0
    run = 0
    result: list[int] = []
    for value in values:
        value = int(value)
        if value == 0:
            run = 0
            previous = 0
        elif value == previous:
            run += 1
        else:
            previous = value
            run = 1
        result.append(run)
    return result


def add_bin_features(events: pd.DataFrame, params: Parameters) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    bin_rule = f"{params.bin_seconds}s"

    for symbol, group in events.groupby("symbol", sort=False, group_keys=False):
        indexed = group.sort_values(["timestamp", "_source_order"], kind="mergesort").set_index("timestamp")
        bins = indexed.resample(bin_rule).agg(
            trade_count=("match_qty", "size"),
            total_volume=("match_qty", "sum"),
            signed_volume=("signed_volume", "sum"),
            total_notional=("notional", "sum"),
            signed_notional=("signed_notional", "sum"),
            buy_trade_count=("is_buy", "sum"),
            sell_trade_count=("is_sell", "sum"),
            last_trade_price=("match_price", "last"),
            vwap_numerator=("notional", "sum"),
            large_print_count=("is_large_print", "sum"),
            max_trade_notional=("notional", "max"),
        )
        bins = bins.dropna(subset=["trade_count"]).copy()
        bins["symbol"] = symbol
        bins["vwap"] = bins["vwap_numerator"] / bins["total_volume"]
        bins["trade_flow_imbalance"] = bins["signed_volume"] / bins["total_volume"]
        bins["direction"] = np.sign(bins["signed_notional"]).astype("int8")

        # Strictly causal baseline: the current bin is shifted out before every estimate.
        prior_flow = bins["signed_notional"].shift(1)
        bins["baseline_mean_signed_notional"] = prior_flow.rolling(
            params.baseline_bins,
            min_periods=params.min_baseline_bins,
        ).mean()
        bins["baseline_std_signed_notional"] = prior_flow.rolling(
            params.baseline_bins,
            min_periods=params.min_baseline_bins,
        ).std(ddof=0)
        bins["signed_notional_z"] = (
            (bins["signed_notional"] - bins["baseline_mean_signed_notional"])
            / bins["baseline_std_signed_notional"].replace(0.0, np.nan)
        )
        bins["flow_gate"] = (
            bins["signed_notional_z"].abs().ge(params.z_threshold)
            & bins["trade_flow_imbalance"].abs().ge(params.imbalance_threshold)
            & bins["trade_count"].ge(params.min_trades)
        )
        # Only consecutive *qualifying* bins count toward a flow program. A quiet or
        # weak same-direction bin resets persistence rather than extending the run.
        bins["qualifying_direction"] = bins["direction"].where(bins["flow_gate"], 0).astype("int8")
        bins["same_direction_run"] = direction_run(bins["qualifying_direction"].tolist())
        # This gate is causal: a bin is eligible only after the required qualifying
        # direction run has already occurred.
        bins["flow_cluster_candidate"] = bins["flow_gate"] & bins["same_direction_run"].ge(
            params.min_consecutive_bins
        )
        bins["large_print_candidate"] = bins["large_print_count"].gt(0)
        bins["block_like_candidate"] = bins["flow_cluster_candidate"] | bins["large_print_candidate"]
        bins["candidate_type"] = np.select(
            [
                bins["flow_cluster_candidate"] & bins["large_print_candidate"],
                bins["flow_cluster_candidate"],
                bins["large_print_candidate"],
            ],
            ["FLOW_CLUSTER_AND_LARGE_PRINT", "FLOW_CLUSTER", "LARGE_PRINT"],
            default="NONE",
        )
        frames.append(bins.reset_index(names="bin_end"))

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).sort_values(["symbol", "bin_end"], kind="mergesort").reset_index(drop=True)


def make_episodes(bins: pd.DataFrame, params: Parameters) -> pd.DataFrame:
    rows: list[dict] = []
    if bins.empty:
        return pd.DataFrame()

    max_gap = pd.Timedelta(seconds=params.episode_gap_bins * params.bin_seconds)
    for symbol, group in bins.groupby("symbol", sort=False):
        candidates = group[group["block_like_candidate"]].sort_values("bin_end", kind="mergesort")
        current: list[pd.Series] = []
        previous_end: Optional[pd.Timestamp] = None
        previous_direction = 0

        def close_episode(items: list[pd.Series]) -> None:
            if not items:
                return
            episode = pd.DataFrame(items)
            signed_notional = float(episode["signed_notional"].sum())
            signed_volume = float(episode["signed_volume"].sum())
            direction = "BUY" if signed_notional > 0 else "SELL" if signed_notional < 0 else "MIXED"
            start = episode["bin_end"].min()
            end = episode["bin_end"].max() + pd.Timedelta(seconds=params.bin_seconds)
            rows.append(
                {
                    "episode_id": f"{symbol}_{start.isoformat()}_{direction}",
                    "symbol": symbol,
                    "direction": direction,
                    "start_time": start,
                    "end_time": end,
                    "duration_seconds": (end - start).total_seconds(),
                    "candidate_bins": int(len(episode)),
                    "total_trades": int(episode["trade_count"].sum()),
                    "total_volume": float(episode["total_volume"].sum()),
                    "signed_volume": signed_volume,
                    "total_notional": float(episode["total_notional"].sum()),
                    "signed_notional": signed_notional,
                    "max_abs_signed_notional_z": float(episode["signed_notional_z"].abs().max(skipna=True)),
                    "max_abs_flow_imbalance": float(episode["trade_flow_imbalance"].abs().max(skipna=True)),
                    "large_print_count": int(episode["large_print_count"].sum()),
                    "peak_candidate_type": episode.loc[
                        episode["signed_notional_z"].abs().fillna(-np.inf).idxmax(), "candidate_type"
                    ],
                    "last_trade_price": float(episode["last_trade_price"].iloc[-1]),
                }
            )

        for _, item in candidates.iterrows():
            direction = int(item["direction"])
            if direction == 0:
                # A zero-net bin has no trustworthy direction; retain it in bin output but do not stitch it.
                close_episode(current)
                current, previous_end, previous_direction = [], None, 0
                continue
            if (
                current
                and previous_end is not None
                and item["bin_end"] - previous_end <= max_gap
                and direction == previous_direction
            ):
                current.append(item)
            else:
                close_episode(current)
                current = [item]
            previous_end = item["bin_end"]
            previous_direction = direction
        close_episode(current)

    episodes = pd.DataFrame(rows)
    if episodes.empty:
        return episodes
    return episodes.sort_values(["symbol", "start_time"], kind="mergesort").reset_index(drop=True)


def write_outputs(
    events: pd.DataFrame,
    bins: pd.DataFrame,
    episodes: pd.DataFrame,
    args: argparse.Namespace,
    params: Parameters,
    inferred_timestamp_unit: Optional[str],
) -> None:
    args.output_dir.mkdir(parents=True, exist_ok=True)

    event_columns = [
        "timestamp", "symbol", "aggressor_sign", "match_qty", "match_price", "notional",
        "signed_volume", "signed_notional", "rolling_signed_volume", "rolling_total_volume",
        "rolling_signed_notional", "rolling_flow_imbalance", "large_print_threshold_notional",
        "is_large_print", "_source_order",
    ]
    events.loc[:, event_columns].to_csv(args.output_dir / "event_features.csv", index=False)
    bins.to_csv(args.output_dir / "bin_features.csv", index=False)
    episodes.to_csv(args.output_dir / "block_episodes.csv", index=False)

    metadata = {
        "input_path": str(args.input_path.resolve()),
        "input_rows": int(len(events)),
        "symbols": sorted(events["symbol"].unique().tolist()),
        "timestamp_unit_requested": args.timestamp_unit,
        "timestamp_unit_inferred_for_numeric_input": inferred_timestamp_unit,
        "assumed_side_semantics": "resting_side_flipped_to_aggressor" if params.side_is_resting else "aggressor_side",
        "parameters": asdict(params),
        "output_rows": {
            "event_features": int(len(events)),
            "bin_features": int(len(bins)),
            "block_episodes": int(len(episodes)),
            "large_prints": int(events["is_large_print"].sum()),
            "flow_cluster_bins": int(bins["flow_cluster_candidate"].sum()) if not bins.empty else 0,
        },
        "limitations": [
            "This trade-tape MVP cannot reconstruct resting order-book depth, queues, cancellations, or replenishment.",
            "A detected episode is an observable large directional execution pattern, not proof of institutional ownership.",
            "Without bid/ask data, match_price is not an executable backtest entry/exit price.",
        ],
    }
    with (args.output_dir / "run_metadata.json").open("w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2, default=str)


def main() -> int:
    args = parse_args()
    params = Parameters(
        bin_seconds=args.bin_seconds,
        rolling_seconds=args.rolling_seconds,
        baseline_bins=args.baseline_bins,
        min_baseline_bins=args.min_baseline_bins,
        z_threshold=args.z_threshold,
        imbalance_threshold=args.imbalance_threshold,
        min_trades=args.min_trades,
        min_consecutive_bins=args.min_consecutive_bins,
        large_print_quantile=args.large_print_quantile,
        large_print_lookback_trades=args.large_print_lookback_trades,
        min_print_history=args.min_print_history,
        episode_gap_bins=args.episode_gap_bins,
        side_is_resting=args.side_is_resting,
    )
    raw = read_input(args.input_path)
    events, inferred_timestamp_unit = validate_and_prepare(raw, args)
    events = add_event_features(events, params)
    bins = add_bin_features(events, params)
    episodes = make_episodes(bins, params)
    write_outputs(events, bins, episodes, args, params, inferred_timestamp_unit)

    print(f"Processed {len(events):,} trades across {events['symbol'].nunique():,} symbol(s).")
    print(f"Large individual prints: {int(events['is_large_print'].sum()):,}")
    print(f"Flow-cluster bins: {int(bins['flow_cluster_candidate'].sum()) if not bins.empty else 0:,}")
    print(f"Block-like episodes: {len(episodes):,}")
    print(f"Output directory: {args.output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, ValueError, KeyError, pd.errors.ParserError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(2)
