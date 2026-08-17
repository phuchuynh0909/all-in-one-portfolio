# Implementation Contract: Event Schema and Module Boundaries

## Canonical event schema

The parser for each venue should map source-specific messages into the canonical schema below without discarding source fields. Store the original message payload or a lossless reference alongside this table. Prices should be stored as signed integers in a declared `price_scale`; do not use binary floating point as the source of truth.

| Column | Type | Required | Notes |
|---|---|---:|---|
| `trade_date` | date | Yes | Session date in exchange calendar |
| `venue` | string | Yes | Source matching venue, never an inferred routing venue |
| `venue_seq` | int64 | Yes | Strictly ordered source sequence where supplied |
| `ts_exchange_ns` | int64 | Yes | Exchange event timestamp in nanoseconds |
| `ts_receive_ns` | int64 | No | Data-receipt timestamp; never substitute for exchange time silently |
| `symbol` | string | Yes | Point-in-time symbol mapping |
| `event_type` | enum | Yes | `add`, `modify`, `cancel`, `execute`, `trade`, `replace`, `status`, `imbalance`, `correction` |
| `side` | enum | Conditional | `B`, `S`, or null when the source does not carry side |
| `price_int` | int64 | Conditional | Integer price using `price_scale` |
| `price_scale` | int32 | Yes | E.g., 10,000 for four decimal places |
| `shares` | int64 | Conditional | Displayed shares or execution shares, source-defined |
| `order_ref` | string/int64 | Conditional | Source order reference; preserve exact representation |
| `match_ref` | string/int64 | No | Execution/trade linkage if supplied |
| `trade_condition` | string | No | Conditions used to filter non-standard prints |
| `aggressor_side` | enum | No | Feed supplied only; derived sign belongs in a separate field |
| `sign_source` | enum | Yes | `feed`, `quote_match`, `unclassified`, or `not_applicable` |
| `sign_confidence` | float32 | Yes | `[0,1]`; set to 1 only for an explicit reliable feed flag |
| `source_schema_version` | string | Yes | Vendor protocol/version identifier |
| `raw_payload_ref` | string | Yes | Path/offset/hash pointing to original raw content |

## Book-state and feature schemas

Persist book states only at feature clocks and at candidate boundaries unless a complete replay archive is explicitly needed. The level table is long-format so that it accommodates different depth limits without altering the schema.

| Dataset | Primary key | Core columns |
|---|---|---|
| `book_level` | `(trade_date, venue, symbol, ts_exchange_ns, level, side)` | `price_int`, `displayed_shares`, `order_count`, `book_valid`, `venue_seq` |
| `book_top` | `(trade_date, venue, symbol, ts_exchange_ns)` | `best_bid`, `best_ask`, `bid_size`, `ask_size`, `midprice`, `spread_ticks`, `book_valid` |
| `feature_window` | `(run_id, trade_date, venue, symbol, clock, window_end_ns)` | raw metrics, normalised metrics, baseline IDs, quality flags |
| `candidate` | `candidate_id` | direction, gate values, raw/components scores, first-detect time, baseline IDs |
| `episode` | `episode_id` | start/end/peak timestamps, direction, class, confidence, evidence fields, stitch lineage |
| `simulated_trade` | `(run_id, episode_id, latency_ms, exit_horizon_s)` | decision time, entry/exit quote, costs, realised return, exclusion reason |

## Modules and invariant tests

| Module | Responsibility | Invariant tests |
|---|---|---|
| `ingest` | Decode raw venue files and store immutable source manifests | File checksum equals manifest; all input fields preserved |
| `normalise` | Map source messages to canonical events | Every canonical event has source schema and raw reference |
| `reconstruct` | Maintain orders, price levels, and top `K` book | No negative depth; valid cancel/modify references; monotone sequence |
| `sign` | Use feed side or quote-match signed prints | Ambiguous signs are retained but excluded by declared rules |
| `features` | Build strictly causal windows and trailing baselines | Baseline end date precedes scored event date |
| `detect` | Apply gates, components, and episode stitcher | First detection never moves after forward-return join |
| `label` | Produce tiered labels and audit samples | Feature set cannot include label-forward fields |
| `evaluate` | Run event study, calibration, and backtest | Decisions use only data available at decision time |
| `report` | Emit tables, charts, and data-quality results | Every table carries `run_id` and data manifest hash |

The minimal test suite should include a hand-authored message sequence covering add, partial fill, full fill, cancellation, replacement, a same-price replenishment pattern, a crossed-book rejection, and an out-of-sequence rejection. Replay these fixtures on every change to the parser or book engine. The detector should be unit-tested separately from reconstruction with fixed feature rows, so that a score change cannot be mistaken for a book-parsing change.
