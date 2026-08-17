# Assessment of the Available Dataset

## Direct answer

Your schema

```text
timestamp, side, match_qty, match_price
```

is sufficient for an **offline trade-flow detector**, but it is **not sufficient for a true Level 2 order-book detector**. With it, you can identify **probable large, directional execution clusters**—for example, unusually persistent buy-initiated or sell-initiated matched volume. You cannot reconstruct the book, observe displayed blocks before they trade, measure queue depletion, distinguish cancellation from execution, or reliably identify replenishment/iceberg-like liquidity.

The terminology should therefore change from **“institutional order-block detector”** to **“probable large aggressive execution-program detector based on trade prints.”** It detects an observable footprint, not the owner of the order and not all hidden liquidity.

> The decisive assumption is that `side` denotes the **aggressor side** of each match: `B` means a buyer crossed the spread to execute and `S` means a seller crossed the spread. If it instead denotes the resting/maker side, reverse the sign. If it does not define either role, it cannot be used as a reliable signed-flow input without further documentation.

| Available column | What it supports | Data-quality requirement |
|---|---|---|
| `timestamp` | Event ordering, 1/5/30-second aggregation, trade runs, forward price response | Must be exchange/event time with at least millisecond precision and a known timezone |
| `side` | Signed executed volume and directional persistence | Must document whether it is aggressor/taker or resting/maker side |
| `match_qty` | Executed shares, trade-size surprise, signed volume, signed notional | Must be positive, split-adjusted or accompanied by a corporate-action factor |
| `match_price` | VWAP, tick return, forward return, approximate impact efficiency | Must be a valid executed price in a consistent price scale |

## Signals you can build now

Form one-second bins and calculate the following fields for each symbol. Use 5-second, 30-second, and 100-trade windows as parallel views. Let `s_i` be `+1` for buyer-initiated and `−1` for seller-initiated transactions, `q_i` be `match_qty`, and `p_i` be `match_price`.

| Signal | Calculation | What it detects |
|---|---|---|
| Signed volume | `SV_t = Σ(s_i q_i)` | Net aggressive buying or selling during the window |
| Signed notional | `SN_t = Σ(s_i q_i p_i)` | Directional flow adjusted for price level |
| Trade-flow imbalance | `TFI_t = Σ(s_i q_i) / Σ(q_i)` | Whether volume is strongly one-sided; bounded in `[-1, 1]` |
| Directional persistence | Proportion of trades with dominant sign, longest same-sign run, and sign entropy | A sequence consistent with child-order execution rather than one isolated print |
| Trade-size surprise | Percentile or robust z-score of `q_i` and `q_i p_i` versus same-symbol, same-time-of-day history | Unusually large individual child executions |
| Flow surprise | Robust z-score of `SV_t` and `SN_t` versus a trailing, prior-only baseline | Material directional activity for that instrument and time of day |
| Local participation | `Σq_i / trailing total matched volume` | A program that dominates nearby activity |
| Price response | `s × (price_{t+h} - price_t)` for horizons `h` of 1, 5, 30, 60 seconds | Whether one-sided flow is followed by a directional move |
| Impact efficiency | Signed price response divided by absolute signed notional | Strong flow that actually moves the trade-price process |

A conservative initial candidate is a one-second window that satisfies all of the following after normalisation by a **prior-only**, same-symbol and same-time-of-day baseline: absolute signed-notional z-score of at least 2.5; absolute trade-flow imbalance of at least 0.70; at least three trades; and the same directional condition in either the previous or next second. Consecutive same-side candidates separated by at most five seconds can be stitched into one episode.

A transparent first score is:

```text
TradeProgramScore_s =
    0.40 × robust_rank(s × signed_notional)
  + 0.25 × robust_rank(s × trade_flow_imbalance)
  + 0.20 × robust_rank(directional_persistence_s)
  + 0.15 × robust_rank(s × short_horizon_price_response)
```

Here, `s` is `+1` for a buy episode and `−1` for a sell episode. Preserve each component in the output, rather than only a total score. In particular, a large trade or large signed-notional result with low persistence should usually be classified as an **isolated large execution**, not a probable program.

## What this data cannot establish

| Missing L2 concept | Why the four fields cannot measure it | Minimum additional data |
|---|---|---|
| Displayed large limit block | No resting bid/ask quantity is present before a match | Best bid/ask prices and sizes; preferably depth by level |
| Queue depletion or sweep depth | The number of shares available at each price is unknown | Level 2 bid/ask price and displayed quantity for at least 5 levels |
| Passive absorption | There is no way to see whether new liquidity refills after an execution | Add/cancel/modify events or order-book snapshots with high frequency |
| Iceberg-like replenishment | A repeated print does not show the same resting order being replenished | Order-level references plus add/modify/delete/execute linkage |
| Cancellation asymmetry or spoof-like behaviour | Cancellations are not in the trade tape | Cancel/delete event messages and queue state |
| Reliable trade classification if `side` is unclear | A price-only tick rule is inferior and can be ambiguous | Explicit aggressor/taker flag, or contemporaneous NBBO |
| Multi-instrument research | The schema lacks an identifier | `symbol` and, ideally, `venue` |

## Minimum viable schema

If your file covers more than one equity or venue, the following fields are required before serious research. The first six fields define a viable **trade-tape** version. The remaining fields upgrade it toward an L2 version.

```text
# Required for a usable trade-flow detector
symbol, venue, timestamp, sequence_or_trade_id,
aggressor_side, match_qty, match_price, trade_condition

# Minimum quote context: greatly improves signing and price-response measures
best_bid_price, best_bid_qty, best_ask_price, best_ask_qty

# Required for actual Level 2 / replenishment analysis
level, book_side, book_price, displayed_qty, order_count,
event_type(add|modify|cancel|execute|replace), order_ref
```

`symbol` is non-negotiable if the data contains more than one instrument. `sequence_or_trade_id` is important for deterministic ordering of events that share a timestamp. `trade_condition` is needed to exclude corrections, auctions, and special/late prints from the regular-session model. `venue` matters because a single venue’s trade tape is not the consolidated U.S. market.

## Recommended implementation path with the present data

Start with a **trade-tape MVP**. First, verify the meaning of `side`, timestamp precision/timezone, whether the data represents one or multiple symbols, and whether trades include auctions, corrections, or off-exchange prints. Next, build causal 1-second feature rows and fit trailing 20-session baselines by symbol and 30-minute time-of-day bucket. Generate candidates from signed-notional surprise, one-sidedness, and persistence, then stitch them into episodes. Assess them through an event study at 1, 5, 30, 60, and 300 seconds using the **first detection time** only.

Do not backtest a marketable trading rule from these columns alone as though you know the bid, ask, or fill price. The price in `match_price` is an observed past execution and is not proof that your hypothetical trade could execute there. With no quote data, the credible first deliverable is a ranked event-study and detection-quality report, not execution P&L. Add NBBO before any cost-aware trading simulation, and full order-book events before testing replenishment or displayed-block hypotheses.

## Decision summary

| Objective | Feasible now? | Correct output label |
|---|---:|---|
| Detect unusually large, one-sided trade clusters | Yes | `probable_aggressive_execution_program` |
| Detect large individual matched trades | Yes | `large_trade_print` |
| Measure short-horizon post-flow price response | Yes, with trade-price caveats | `post_episode_trade_price_response` |
| Simulate realistic entries, exits, and costs | No | Requires bid/ask or NBBO |
| Detect visible order blocks, queue depletion, and cancellations | No | Requires L2 book messages/snapshots |
| Detect iceberg-like replenishment | No | Requires order-level or high-fidelity book-event data |
| Identify institutional ownership | No | Requires authorised external participant/parent-order labels |
