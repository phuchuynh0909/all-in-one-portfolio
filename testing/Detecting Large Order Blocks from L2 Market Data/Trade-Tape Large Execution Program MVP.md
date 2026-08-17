# Trade-Tape Large Execution Program MVP

`detect_large_trade_programs.py` is a standalone Python script for CSV or Parquet trade-tape data. It assumes that every row is a matched trade and that the input contains at least `timestamp`, `side`, `match_qty`, and `match_price`.

## Important side convention

The script interprets `side` as the **aggressor/taker side** by default. Thus `B` means buyer-initiated activity and `S` means seller-initiated activity. Confirm this with your data vendor. If the file records the **resting/maker side**, use `--side-is-resting` to flip all signs. A wrong choice here reverses the detector’s direction.

## Quick start

For a single-instrument file with ISO-8601 timestamps:

```bash
python3 detect_large_trade_programs.py trades.csv --output-dir output
```

For a multi-symbol file with a `symbol` column:

```bash
python3 detect_large_trade_programs.py trades.csv \
  --symbol-col symbol \
  --output-dir output
```

For numeric timestamps, leave `--timestamp-unit auto` in place if they are Unix epoch values. If your timestamp’s unit is known, set it explicitly, for example `--timestamp-unit ms`.

## Default detection behaviour

The script calculates a rolling 30-second signed-volume and signed-notional measure for every trade. It also aggregates trades into one-second bins. A flow-cluster bin requires all of the following: an absolute signed-notional z-score of at least 2.5 relative to a strictly prior-only 30-minute baseline, absolute trade-flow imbalance of at least 0.70, at least three trades, and a two-bin same-direction run.

An individual trade is marked `is_large_print` when its notional is at or above the 99th percentile of the preceding 500 trades in that symbol. The rolling threshold excludes the current print, preventing it from qualifying itself. The output stitches nearby same-direction flow clusters and/or large-print bins into `block_episodes.csv`. A detected episode is a **block-like execution footprint**, not confirmation of an institution or parent-order owner.

| Parameter | Default | Use |
|---|---:|---|
| `--rolling-seconds` | 30 | Window for row-level rolling signed volume |
| `--baseline-bins` | 1,800 | Prior 1-second bins used for signed-notional baseline |
| `--min-baseline-bins` | 300 | Minimum history before flow z-scores are valid |
| `--z-threshold` | 2.5 | Signed-notional surprise needed for a flow cluster |
| `--imbalance-threshold` | 0.70 | One-sidedness required in a bin |
| `--large-print-quantile` | 0.99 | Prior trade-notional percentile used for a large print |
| `--episode-gap-bins` | 5 | Maximum gap between same-side candidate bins in an episode |

## Outputs

| File | Granularity | Important columns |
|---|---|---|
| `event_features.csv` | One row per trade | `rolling_signed_volume`, `rolling_signed_notional`, `rolling_flow_imbalance`, `is_large_print` |
| `bin_features.csv` | One row per symbol and active time bin | `signed_notional_z`, `trade_flow_imbalance`, `flow_cluster_candidate`, `candidate_type` |
| `block_episodes.csv` | One row per stitched episode | direction, start/end, signed notional, maximum surprise and imbalance, large-print count |
| `run_metadata.json` | One row per run | Input details, parameter values, row counts, explicit limitations |

## Interpreting a result

Treat `FLOW_CLUSTER` as sustained, one-sided execution activity. Treat `LARGE_PRINT` as a large matched trade relative to that symbol’s own recent trade-size distribution. Treat `FLOW_CLUSTER_AND_LARGE_PRINT` as the strongest trade-tape combination. The absolute threshold is intentionally configurable because trade size and message rate vary substantially between equities.

The script does not reconstruct Level 2 depth, locate displayed liquidity, observe cancels, verify queue depletion, infer iceberg replenishment, or calculate executable P&L. Add NBBO data before simulating trading costs and full order-book events before claiming L2-level absorption or block detection.

## Validation status

The delivered file was compiled with Python 3.11 and its command-line interface was checked. No sample from your actual feed was available in this session, so the output has **not** yet been calibrated or run against your symbols. Before analysing results, inspect a few raw rows, verify side semantics and timestamp units, and compare event timestamps to the source feed documentation.
