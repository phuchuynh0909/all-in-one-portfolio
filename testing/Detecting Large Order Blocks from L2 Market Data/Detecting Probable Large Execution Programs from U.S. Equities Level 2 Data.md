# Detecting Probable Large Execution Programs from U.S. Equities Level 2 Data

**Prepared by Manus AI**  
**Purpose:** offline market-microstructure research and backtesting design  
**Scope:** displayed, venue-observable U.S. equities order-book activity

## Executive definition

The correct research target is **not “an institution”**. Public Level 2 data normally does not reveal the beneficial owner, broker customer, or parent order. It also omits some non-displayed and off-exchange activity. The detector should instead identify a **probable large execution program**: a directionally persistent cluster of aggressive executions and/or passive replenishment that is unusually large for the stock, time of day, venue, and current available depth.

> A positive result means: *the visible data are statistically consistent with a large buy or sell program at this venue.* It does **not** mean: *an institution has been identified.*

This distinction is fundamental. Nasdaq describes TotalView as a full-depth record of orders trading **on Nasdaq**; NYSE’s integrated historical products similarly describe venue-specific matching-engine message sequences. They provide rich evidence about displayed liquidity at those venues, but not a universal view of every U.S. execution or a participant identity.[1] [2]

| Research question | Valid answer from Level 2 data | Not identifiable from Level 2 alone |
|---|---|---|
| Is unusually large, persistent signed execution flow present? | Yes, probabilistically | — |
| Is displayed liquidity repeatedly replenishing after fills? | Yes, subject to feed semantics | The owner or total hidden quantity |
| Is a displayed block absorbing flow at a price? | Yes, as an observable pattern | Whether it is one parent order versus several participants |
| Is the source an institution? | Only if an external participant label is joined | Yes, from public L2 alone |
| Is this market-wide? | Only after careful venue-local aggregation | Yes, from one exchange feed alone |

## 1. Data standard and research universe

Build the initial study on **one complete exchange-native order-level feed** rather than mixing retail Level 2 snapshots or unsynchronised feeds. The feed must preserve an exchange sequence number and message timestamp, and it must permit reconstruction of every displayed price level after add, cancel/delete, modification, and execution messages. NYSE’s TAQ Integrated Feed, for example, describes add/modify/delete depth updates and trade messages ordered at the matching engine; this is the type of deterministic event sequence required for the study.[2]

Use a liquid-stock universe first: constituents that trade continuously, have stable penny quoting for most of the session, and satisfy a liquidity screen fixed before each test day. A practical starting screen is median daily notional volume above USD 20 million, median inside quoted spread no wider than 10 basis points, and at least 95% regular-session message coverage over the trailing 20 trading days. These are **research design defaults**, not universal constants; hold them constant within an experiment, then report sensitivity to looser and stricter screens.

Exclude opening and closing auctions, halts, limit-up/limit-down states, symbols with corporate-action flags, and the first and final 10 regular-session minutes in the primary test. Analyse those segments separately because their message and liquidity mechanics differ. Keep the regular session in local exchange time, preserve all original timestamps in UTC or nanoseconds since epoch, and never reorder messages with the same venue sequence.

| Required input | Minimum fields | Why it is needed |
|---|---|---|
| Order-book events | `ts_exchange`, `venue_seq`, `symbol`, `type`, `side`, `price`, `shares`, `order_id/ref_id` when provided | Deterministic reconstruction and queue/replenishment analysis |
| Execution events | `ts_exchange`, `price`, `shares`, execution/order reference, trade condition | Signed aggressive-flow and fill-rate features |
| Venue status | Trading state, auction/imbalance, halt, LULD/correction flags | Regime exclusion and quality checks |
| NBBO/consolidated context | Quote timestamp, bid/ask, sizes, trade conditions, venue | Mark price response and contextualise venue-local activity |
| Security master | Split factors, listings, corporate actions, tick size | Point-in-time universe and price normalization |

The SEC’s public market-structure material is useful for **calibration and diagnostics**, including trade-to-order and quote-life concepts, but it is not a substitute for the event-level depth data needed to rebuild a book.[3]

## 2. Reconstruct the book before computing features

Maintain an order map keyed by the feed’s order reference when one is provided. Each active order should retain side, limit price, displayed shares, original timestamp, most recent update timestamp, and modification count. Maintain separate bid and ask price-level maps so that the best `K` levels can be queried after every message. Log every reconstruction anomaly rather than silently repairing it.

A day is **invalid for alpha evaluation** if the reconstructed feed produces crossed books outside documented states, negative displayed quantity, unknown cancellations above a small tolerance, non-monotone venue sequence numbers, or a material mismatch against a trusted top-of-book check. Retain invalid days only for parser debugging. Book reconstruction is not plumbing: errors here create artificial cancellation, replenishment, and order-flow signals.

At every feature clock `t`, store the post-message book state. Use three parallel clocks: 1 second, 5 seconds, and 30 seconds; also compute a 100-message event-time representation. Clock time makes results interpretable across symbols, while event time protects the detector from confusing a quiet stock with a low-signal stock.

## 3. Observable signatures of a large execution program

The detector should combine independent traces instead of interpreting a single large quote as a block. Cont, Kukanov, and Stoikov show that short-interval price changes are more closely related to order-flow imbalance than to raw trading volume, and that depth conditions the relationship.[4] This motivates depth-normalized signed-flow measures rather than simple volume thresholds.

Let `m_t = (a_t + b_t)/2` be the venue midprice after event `t`, and let `D^b_{t,l}` and `D^a_{t,l}` be displayed shares at the `l`th bid and ask levels. Let `q_i` be executed shares, `s_i ∈ {-1,+1}` the aggressor sign, and `Δt` the feature window. A feed-supplied aggressor flag is preferred. If it is unavailable, classify trades by matching the print to the contemporaneous reconstructed quote and mark ambiguous prints as low-confidence. A central-bank study finds that conventional quote-rule classifiers degrade in fast markets and that active quote matching can materially improve classification.[5]

| Feature family | Formula or construction | Interpretation | Main false positive |
|---|---|---|---|
| Signed execution pressure | `SEV = Σ_i s_i q_i` | Net aggressive buying or selling | One large news-driven print |
| Relative execution surprise | `zSEV = (SEV - μ_{symbol,ToD}) / σ_{symbol,ToD}` | Flow unusual for the stock and time of day | Poor seasonal baseline |
| Depth-normalised flow | `DNF = SEV / (ε + Σ_{l=1}^{K}(D^b_l + D^a_l)/2)` | Aggression relative to immediately available liquidity | Book-feed gaps |
| Multi-level order-flow imbalance | `MLOFI = Σ_{l=1}^{K} w_l(ΔD^b_l - ΔD^a_l)`, `w_l = exp[-λ(l-1)]` | Combined effect of adds, cancellations, and fills close to the touch | Reactive market-making |
| Directional persistence | Mean sign, longest same-sign run, and `1 - normalized_entropy(signs)` | Child-order-like one-way flow | Short-lived momentum burst |
| Queue depletion / sweep | Executed shares divided by pre-window same-side touch depth; count price levels consumed | Aggression that exhausts displayed liquidity | A single isolated sweep |
| Passive absorption | Same-price executions followed by add/replace volume within `τ` milliseconds; track retained depth | Reserve-liquidity or slicing-like footprint | Multiple market makers replenishing independently |
| Cancellation asymmetry | Near-touch opposite-side cancels minus same-side cancels, depth-normalised | Liquidity withdrawal in the flow direction | Quote flickering or feed artefact |
| Signed impact efficiency | `s × (m_{t+h}-m_t)/spread_t` and its residual after controlling for DNF | Whether pressure translates into price discovery | News shock or stale NBBO |
| Displayed block quality | Depth percentile, lifetime, executed fraction, cancellation-to-fill ratio | A large displayed level that actually absorbs flow | Spoof-like, rapidly cancelled quote |

Set `K = 5` initially and estimate `λ` within the training set, starting with `λ = ln(2)` so that each extra level receives half the preceding level’s weight. Use shares and notional versions of every volume feature; rank-normalise them by symbol, venue, 30-minute bucket, and trailing volatility/depth regime.

The **passive absorption** component deserves separate treatment. A same-price refill after partial execution can indicate reserve liquidity or a participant-managed slicing pattern, but it is not proof of either. Research on iceberg detection uses post-trade order modification or rapid replacement patterns as evidence, while explicitly accounting for cancellation after partial fills.[6] In this design, label the result `iceberg_like` or `absorption_like`, never `institutional iceberg`, unless the feed exposes a native reserve-order flag.

## 4. Candidate generation and composite scoring

The detector has two stages. Stage A is intentionally permissive and finds short windows with unusual directional activity. Stage B requires agreement across different evidence types and stitches adjacent candidate windows into interpretable episodes. This reduces multiple-testing noise and keeps an isolated large trade from becoming an “institutional block.”

### Stage A — directional candidate gates

For every symbol, venue, and 1-second window, calculate buy-side and sell-side values. A buy candidate requires at least two of the following three gates; a sell candidate is symmetric.

| Gate | Initial robust threshold | Rationale |
|---|---:|---|
| Flow surprise | `zSEV ≥ 2.5` or above the trailing 99.0th percentile | Unusually large aggressive buy flow |
| Liquidity pressure | `DNF ≥ 0.20` and `MLOFI z-score ≥ 2.0` | Flow is material relative to displayed depth |
| Persistence or sweep | Sign agreement ≥ 0.70 with ≥ 3 executions, or ≥ 1.5 touch depths consumed | Reduces reliance on one print |

Estimate all thresholds from a **rolling, prior-only** baseline. A valid baseline uses the preceding 20 regular sessions for the same symbol, venue, 30-minute time-of-day bucket, and volatility/depth quintile; fall back hierarchically to a sector- or liquidity-bucket baseline only when the local sample is insufficient. Do not calculate a day’s standardisation using its later observations.

### Stage B — interpretable score

Standardise every feature to a bounded robust score using its trailing empirical CDF: `r(x) = 2·CDF_train(x) - 1`. For a direction `s`, form the following component scores over 1-, 5-, and 30-second windows and retain the maximum after requiring sign agreement across at least two horizons.

```text
AggressiveProgram_s =
  0.30 * r(s * SEV / depth)
+ 0.20 * r(s * MLOFI)
+ 0.20 * r(persistence_s)
+ 0.15 * r(sweep_or_depletion_s)
+ 0.15 * r(s * impact_efficiency)

Absorption_s =
  0.45 * r(same_price_replenishment_s)
+ 0.25 * r(executed_fraction_at_level_s)
+ 0.20 * r(level_lifetime_s)
- 0.20 * r(cancel_to_fill_ratio_s)
+ 0.10 * r(s * local_order_flow)

DisplayedBlock_s =
  0.40 * r(displayed_depth_percentile_s)
+ 0.30 * r(executed_fraction_at_level_s)
+ 0.20 * r(level_lifetime_s)
- 0.30 * r(cancel_to_fill_ratio_s)
+ 0.10 * r(s * subsequent_flow)
```

Each component lies approximately in `[-1, 1]`. Convert to a calibrated probability only after the validation process described below. Before calibration, report the raw components and their evidence: a score is useful only if an analyst can tell whether it arose from aggression, absorption, or a large displayed level.

The primary episode score is:

```text
ProgramScore_s = max(AggressiveProgram_s,
                     0.85 * AggressiveProgram_s + 0.35 * Absorption_s,
                     0.70 * DisplayedBlock_s + 0.30 * Absorption_s)
```

This structure prevents passive liquidity from being automatically interpreted as directional institutional flow, while allowing a program that first absorbs liquidity and then trades aggressively to score highly. The weights are **initial priors**, not tuned results. Freeze them for the first out-of-sample test, then compare them with constrained logistic regression, gradient boosting, and a transparent monotonic model trained only on historical labelled data.

Classify a candidate as follows.

| Output class | Rule after calibration | Meaning |
|---|---|---|
| `aggressive_buy_program` / `aggressive_sell_program` | `P(program) ≥ 0.80`, flow and persistence components both positive | Persistent directional execution pressure |
| `absorption_buy` / `absorption_sell` | `P(absorption) ≥ 0.80`, replenishment evidence dominates | Passive bid/ask absorption or iceberg-like pattern |
| `displayed_block` | High block-quality score, low cancellation-to-fill, material executions | Visible level likely absorbing flow |
| `watch` | `0.60 ≤ P(program) < 0.80` | Evidence insufficient for a high-confidence event |
| `reject` | Any feed/quality exclusion or conflicting evidence | Do not use in event or strategy backtests |

### Episode stitching

Merge same-direction candidates when the gap between their end and the next start is at most 5 seconds and the midprice has not reversed by more than one prevailing spread. The episode begins at the first eligible candidate, ends after a 10-second quiet period, and has a maximum duration of 15 minutes. Store both the first-detection timestamp and the peak-score timestamp. The first is used for actionable backtests; the peak is useful only for forensic analysis.

For every episode, report: signed executed notional, maximum and median depth-normalised flow, price-level range, replenishment ratio, cancellation-to-fill ratio, number of venues when applicable, score path, first-detection time, and a confidence tier. A confidence tier should be empirical: `high` means the score’s held-out precision meets a predeclared target; it must not be chosen from in-sample visual examples.

## 5. Ground truth, labels, and calibration

With public Level 2 data there is no direct ground truth for “institution.” Treat the project as a **proxy-detection** problem. The first model must be evaluated for whether it detects large, persistent, visible execution episodes—not whether it magically attributes ownership.

A defensible label hierarchy is shown below. Keep labels separate from model features and record their provenance.

| Label tier | Construction | Appropriate use | Limitation |
|---|---|---|---|
| Tier 1: participant-labelled | Broker, execution-management, or regulator data with authorised parent/child linkage | Gold-standard supervised model and calibration | Usually unavailable and confidential |
| Tier 2: exchange-native reserve / special-order truth | Explicit feed flags or venue records, if licensed | Validate absorption/iceberg submodel | Does not label all large programs |
| Tier 3: high-confidence proxy | Rolling ≥99.5th percentile signed notional, sign consistency ≥0.70, duration ≥5 seconds, material depth pressure | Initial research and ranking validation | Circular; call it a proxy |
| Tier 4: blinded human audit | Randomised book replay panels with fixed rules, independently adjudicated | Error taxonomy and model governance | Expensive and subjective |

For a Tier 3 positive, require all three: directional executed notional above the rolling 99.5th percentile, at least five seconds of same-direction activity or at least five qualifying 1-second windows within 30 seconds, and a signed-flow persistence measure above 0.70. Define hard negatives from high message-rate intervals with balanced flow, fast cancellation, and no sustained execution. Sample ordinary intervals separately to reflect the true base rate.

Never train and assess on random rows. Split by **whole day**, use an embargo around each test day, and maintain time order. A robust schedule is 60 trading days of training, 20 days of validation, and 20 days of locked test, rolled forward through at least six non-overlapping test blocks. The label threshold, weights, probability calibration, and trade rule must all be selected on the validation segment before a locked test is inspected.

Evaluate ranking and calibration separately. Report precision-recall AUC, precision at the daily top `N` episodes, recall against each label tier, Brier score, expected calibration error, and score stability by symbol liquidity, time of day, volatility regime, spread regime, and venue. ROC-AUC alone is misleading for a rare-event detector.

## 6. Event study and strategy backtest

The detector’s primary evaluation is an **event study**, not a trading P&L. Use the first-detection timestamp `t0` and measure midprice returns over 1, 5, 30, 60, and 300 seconds. Measure both gross signed response and a benchmark-adjusted response using matched non-event intervals with similar symbol, time-of-day, volatility, spread, and depth. Report confidence intervals from a block bootstrap clustered by symbol-day.

A secondary signal backtest can test whether the first-detection score has predictive value after all trading frictions. This is a research simulation, not a guarantee of tradability. Execute no earlier than the first message after `t0 + latency_assumption`. Use at least three latency scenarios, such as 50 ms, 250 ms, and 1,000 ms, and disclose that historical exchange timestamps do not equal the strategy’s real-world receipt and computation time.

| Backtest rule | Conservative implementation |
|---|---|
| Signal time | First detection only; never peak-score time |
| Entry | Next observable eligible quote after `t0 + latency`; buy at ask / sell at bid for marketable entry |
| Position direction | Long for high-confidence buy program; short for high-confidence sell program |
| Position size | Fixed notional or volatility-scaled notional, with a participation cap of 1% of observed 1-minute volume |
| Exit | Evaluate fixed 5 s, 30 s, 300 s exits plus adverse-stop sensitivity; use next executable quote |
| Costs | Half-spread or full spread depending order type, exchange fees/rebates, per-share fees, and a pessimistic slippage grid |
| Capacity | Re-run under 0.1%, 0.5%, and 1.0% participation assumptions; reject results that disappear at conservative assumptions |
| Portfolio aggregation | Cap concurrent names, sector exposure, and daily gross exposure; include borrow availability if shorting is simulated |

When simulating passive fills, do not assume a fill merely because price touched the limit. Estimate queue position from the reconstructed order book only when message semantics permit it; otherwise model fill probabilities pessimistically and report marketable-only results as the baseline. Do not use future trades, end-of-bar quotes, corrected prints that were unknown at decision time, or a consolidated quote timestamp that is later than the simulated decision time.

Evaluate the incremental value of the detector by comparing it against simple baselines: signed-volume z-score only, top-of-book imbalance only, 5-second return momentum, and a random time-of-day-matched episode sample. A complex score that does not beat these baselines after cost is not useful.

## 7. Data architecture and reproducible pipeline

Persist raw data unchanged and produce derived datasets with immutable versions. Partition all event tables by trade date, venue, and symbol; use columnar files such as Parquet with integer prices in ticks or price-nanounits and integer shares. Keep message sequence as a first-class column. The recommended project artifacts are below.

| Layer | Dataset | Primary key | Essential quality controls |
|---|---|---|---|
| Raw | `raw_messages` | `(trade_date, venue, venue_seq)` | File checksum, original archive path, ingest timestamp |
| Normalised | `events` | `(trade_date, venue, venue_seq)` | Schema-version and protocol-version fields |
| Reconstructed | `book_state` | `(trade_date, venue, symbol, ts, venue_seq)` | Best bid/ask validity, depth conservation diagnostics |
| Feature | `features_1s`, `features_5s`, `features_30s` | `(trade_date, venue, symbol, window_end)` | Prior-only baseline version and missingness flags |
| Candidate | `candidates` | `candidate_id` | Gate values, component scores, detection timestamp |
| Episode | `episodes` | `episode_id` | Stitch provenance, confidence tier, direction |
| Evaluation | `event_study`, `trades`, `metrics` | Run ID plus row key | Data cut, code commit, parameter hash |

Use a single configuration file for the universe, window sizes, rolling baseline length, quality exclusions, candidate gates, episode stitching, latency grid, cost assumptions, and evaluation horizons. Each run should save its configuration, git commit hash, data manifest, random seed, and output checksum. This makes a future score change auditable rather than anecdotal.

## 8. Minimal implementation blueprint

The implementation can be built in Python with `polars` or `pandas` for feature tables, `pyarrow` for Parquet, `numpy` for numeric work, `duckdb` for research queries, and an order-book reconstructor tailored to the source protocol. Parse and reconstruct in chronological batches; do not load an entire month of event messages into memory.

```python
for trade_date in dates:
    for venue in venues:
        messages = read_and_validate_raw_messages(trade_date, venue)
        book = OrderBookReconstructor(protocol=protocol[venue])

        for msg in messages.in_strict_sequence():
            event = normalise_message(msg)
            book.apply(event)                       # validate references and quantities
            emit_event_row(event, book.top_k(5))

        features = build_prior_safe_features(
            events=event_rows,
            book_states=book_states,
            baselines=load_baselines_as_of(trade_date - 1),
            windows=["1s", "5s", "30s", "100msg"],
        )
        candidates = generate_candidates(features, quality_flags)
        episodes = stitch_and_score(candidates, features)
        write_versioned_outputs(features, candidates, episodes)
```

Run `build_baselines_as_of(d)` only on sessions strictly before `d`. For each test date, write the prediction rows before calculating any forward-return columns. A separate evaluator may later join forward returns by timestamp. That physical separation is a useful defence against accidental look-ahead bias.

## 9. Acceptance criteria and failure analysis

Do not promote the detector beyond research until it passes predeclared checks. It should reconstruct a clean book on at least 99.9% of eligible messages; generate a stable episode rate by liquidity bucket after time-of-day normalisation; show materially better held-out precision than signed-volume and top-of-book-imbalance baselines; and retain economically meaningful results under the pessimistic latency/cost grid. The exact numerical target should be set before the locked test based on the data vendor, exchange, and intended use.

Every false positive should be tagged into an error taxonomy: isolated news-driven sweep, auction/status leak, crossed-book/reconstruction issue, reactive market making, cancellation flicker, multi-participant replenishment, or unclassified. Every false negative should be reviewed for off-exchange routing, hidden liquidity, quiet passive execution, low-liquidity symbol conditions, and trade-sign ambiguity. These categories often improve a detector more than adding another opaque model.

The expected result is a transparent ranked list of **observable, probable execution programs**, with direction, timestamp, component evidence, and calibrated uncertainty. It is not an identity-resolution system, a surveillance conclusion, or a stand-alone trading instruction.

## References

[1]: https://www.nasdaq.com/solutions/data/equities/nasdaq-totalview "Nasdaq TotalView"
[2]: https://www.nyse.com/market-data/historical/taq-integrated-feed "NYSE TAQ Integrated Feed"
[3]: https://www.sec.gov/data-research/market-structure-data "SEC Market Structure Data Downloads"
[4]: https://ideas.repec.org/p/arx/papers/1011.6402.html "Cont, Kukanov & Stoikov — The Price Impact of Order Book Events"
[5]: https://www.bankofengland.co.uk/working-paper/2020/inferring-trade-directions-in-fast-markets "Jurkatis — Inferring trade directions in fast markets"
[6]: https://arxiv.org/abs/1909.09495 "Zotikov & Antonov — CME Iceberg Order Detection and Prediction"
