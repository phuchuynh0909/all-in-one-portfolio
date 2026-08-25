# Worker Architecture

## Overview

Three independent workers run concurrently. Together they form a pipeline from raw market ticks to Telegram trading alerts.

```
  MQTT broker (DNSE/KRX)
        │
        ▼
┌─────────────────────────────────────────────────────────────────┐
│  tick_ingest.py  (Bytewax streaming)                            │
│  Subscribes to MQTT topic per symbol, normalises tick fields,   │
│  and bulk-inserts rows into ClickHouse `ticks` table.           │
└───────────────────────────┬─────────────────────────────────────┘
                            │ writes
                            ▼
                 ClickHouse — table: ticks
                 (symbol, sending_time, match_price,
                  match_qty, side, received_at, board_id)
                 ENGINE = ReplacingMergeTree
                            │
              ┌─────────────┴──────────────────────┐
              │                                    │
              ▼                                    ▼
┌─────────────────────────────┐    ┌──────────────────────────────────────────┐
│  reconciler.py              │    │  hawkes_signal_worker.py  (poll loop)    │
│  Runs once at 15:00 ICT.    │    │  Every HAWKES_POLL_INTERVAL seconds:     │
│  Back-fills any ticks missed│    │  1. Re-aggregate ticks → ohlc_5m (upsert)│
│  by the stream via DNSE API.│    │  2. Load full ohlc_5m bar history        │
└─────────────────────────────┘    │  3. Compute Hawkes BSI + KAMA gate       │
                                   │  4. Run signal state machine             │
                                   │  5. New signal? → Telegram alert         │
                                   └──────────────┬───────────────────────────┘
                                                  │ reads / writes
                                                  ▼
                                   ClickHouse — table: ohlc_5m
                                   (symbol, ts [5-min bucket],
                                    open, high, low, close,
                                    volume, buy_volume, sell_volume)
                                   ENGINE = ReplacingMergeTree(ver)
                                                  │
                                                  ▼
                                         Telegram Bot API
                                         (entry / exit alerts)
```

---

## Workers

### 1. `tick_ingest.py` — tick ingestion (always-on)

| | |
|---|---|
| **Runtime** | Bytewax streaming dataflow |
| **Source** | DNSE OpenAPI Trade-Extra WebSocket (live) or `MockClickHouseSource` (dev) |
| **Sink** | ClickHouse `ticks` table via `infra.clickhouse_sink` |
| **Key modules** | `dnse_ws_input.DnseTradeSource`, `dnse_sdk` (vendored `TradingClient`), `watchlist.load_symbols`, `tick_contract.normalize_tick`, `clickhouse_sink.output`, `model.TICKS_*` |

Symbol scope: every symbol in `watchlist.json` **plus** the current VN30F front-month
contract (`TICK_SYMBOL`, defaulting to `vn30f_symbol.current_symbol()`).

Feed: `wss://ws-openapi.dnse.com.vn/v1/stream`, HMAC-SHA256 auth, channel
`tick_extra.{board}.{json|msgpack}`. Boards `G1` (even lot) and `G4` (odd lot)
carry both stocks and derivatives, so one subscription covers equities and the
futures contract.

SDK: transport, TLS, the welcome/auth handshake, message encoding and the
heartbeat come from the **official DNSE SDK vendored at `worker/dnse_sdk`**
(`dnse.websocket.TradingClient`). It sits inside the worker tree alongside
`core/`, `infra/` and `workers/`, so it is an ordinary top-level `import
dnse_sdk` off the root that is already on the path (`PYTHONPATH=/app`) — no
`sys.path` shim, no bind mount, and it is copied into the image by the normal
build. Only its third-party deps (`msgpack`) need `worker/requirements.txt`.
`worker/dnse_sdk/dnse/**` is upstream code and mostly stays that way — local
*policy* belongs in `_TradeExtraClient` below. It carries two deliberate
patches, each marked `LOCAL PATCH` in the source. Both are fixed there rather
than worked around in the Bytewax module because both govern every channel, not
just Trade-Extra:

- **`websocket/client.py` — message routing.** Upstream routed market data
  solely on the abbreviated `T` tag, with no `else` on the chain, so a frame
  without that tag was discarded silently. DNSE's Trade-Extra payload has no
  `T`, which made a tick_extra subscription look healthy while delivering
  nothing. `_infer_msg_type` now recovers the type from the frame's shape,
  using the client's subscription set to break ties, and unroutable frames are
  logged once per shape.
- **`websocket/models.py` — `parse_timestamp`.** Upstream built every datetime
  with a bare `fromtimestamp()` and formatted with `strftime`, dropping the
  offset: protobuf and unix inputs came out in the *host's* zone, a string came
  out in whichever zone it arrived in, and the three were indistinguishable
  once returned. It now resolves to UTC and keeps the offset, refuses to guess
  a naive timestamp, and still accepts a naive calendar date under `date_only`
  (`finalTradeDate`, `listingDate`).

**Re-vendoring the SDK reverts both**; the tests under "Vendored-SDK patch" in
`test_dnse_ws_input.py` are what will tell you.

`_TradeExtraClient` subclasses `TradingClient` for one behaviour that is a
choice for this ingester rather than an SDK defect (both upstream defects — `T`
routing and timestamps — are fixed in the SDK itself, above):

- **Session-gated reconnection.** The SDK's own reconnect is disabled
  (`auto_reconnect=False`, `max_retries=1`) so the loop below owns the pacing.

Subscribing goes through the SDK's `subscribe_trade_extra`, one frame per board,
driven from the configured board list — its `board_id=None` default means the
SDK's *nine* boards, not ours, so the loop has to be on this side. An earlier
version batched every board into a single frame; that assumed `channels` accepts
multiple entries, which no upstream path exercises (every SDK subscribe sends
exactly one, and `subscribe_trade_extra` fans its nine-board default out as nine
separate frames). A gateway reading only `channels[0]` would have left the rest
silently unsubscribed — indistinguishable from quiet boards, which is the very
failure the batching was meant to avoid.

Frames are consumed as raw dicts rather than through `TradeExtra` — the
partition reshapes them for `normalize_tick` and logs unrecognised control
frames, and the model renames `matchPrice`→`price`. A fit question, not a
correctness one: `TradeExtra.time` is trustworthy since the timestamp patch.

Encoding: `DNSE_WS_ENCODING` accepts `json` (default) or `msgpack`, both decoded
by the SDK codec; anything else is refused at construction. A frame the codec
cannot read is counted and skipped rather than ending the session.

Stall detection: `_consume` polls `is_stalled()` every 10s and drops the session
when it trips, so `_run` rebuilds it. This wraps rather than uses the SDK's
`is_healthy` because that fails any connection with no `pong` inside twice the
heartbeat, and DNSE only documents the *server* pinging us — if the gateway
never answers our heartbeat, raw `is_healthy` would read false 50s into every
connection and turn the reconnect loop into a storm. The pong clock is therefore
only trusted once a pong has actually been seen; before that the check falls
back to socket and auth state. It is polled on a slow cadence because
`is_healthy` logs a warning every time it finds a stale clock.

Boards: each trade carries the order book it matched on, stored in the
`board_id` column (`G1` main continuous, `G4`/`G7` odd lot, `T1`..`T6`
put-through). Only `TICK_ALLOWED_BOARDS` (default `G1`) is written — put-through
prices are negotiated off-book and would distort any bar or VWAP built from
`ticks`; the backend excludes them for the same reason
(`dnse_client.BOARD_PRIORITY`).

`DNSE_TRADE_BOARDS` decides what arrives and `TICK_ALLOWED_BOARDS` what is
stored; both now default to `G1` alone, so nothing is received and then thrown
away before insert. G1 carries derivatives as well as equities, so the VN30F
contract still arrives on it. The subscription *can* be widened independently
(`DNSE_TRADE_BOARDS=G1,G4,G7,…`, the full valid set being
`dnse_ws_input.ALL_BOARDS`), and the source logs the per-board split of
everything that arrives — which is the only place boards that get filtered out
are still visible. Note the default lives in **two** places that must agree,
`config.DEFAULT_TRADE_BOARDS` and `dnse_ws_input.DEFAULT_BOARDS`: config is what
`tick_ingest` actually passes, so changing only the module constant looks
effective and is not. A test asserts they match. Rows written
before this column exists read back as `''` (board unknown, not `G1`), so
`WHERE board_id = 'G1'` excludes history: use `board_id IN ('', 'G1')` to span
the migration.

Session gate: DNSE serves this endpoint only while the exchange is open — out of
hours `ws-openapi.dnse.com.vn` does not resolve, so a connect attempt fails in
`getaddrinfo` (`[Errno -2] Name or service not known`). The reconnect loop checks
the clock first and sleeps until the next open, defaulting to a 08:00–16:00 ICT
window on weekdays (`DNSE_WS_SESSION_START` / `_END` / `_TZ`, or
`DNSE_WS_SESSION_GATE=0` to dial around the clock). In-session failures retry
with exponential backoff, 5s doubling to 60s. Public holidays are not modelled:
the socket is attempted and falls back to that backoff.

Steps inside the dataflow:

1. `op.input` — subscribe to `tick_extra.{board}.json` for every ingested symbol
2. `key_by_symbol_ingest` — parse JSON, drop symbols outside the ingest set, normalise fields via `tick_contract.normalize_tick`, key by symbol
3. `op.filter` — drop malformed / non-matching rows
4. `transform_for_ticks` — convert to ClickHouse insertion tuple
5. `clickhouse_sink.output` — coalesce onto one batching key, then batch-insert into `ticks`

**Run:**
```bash
python -m bytewax.run tick_ingest:flow
```

#### Ingestion tuning

`bytewax.clickhouse` cannot express either of ClickHouse's ingestion levers, so
the sink lives in `infra/clickhouse_sink.py` instead:

- **Batching.** `op.collect` is *keyed*. A symbol-keyed stream therefore emits
  one block per symbol per flush — measured at **196 blocks averaging 25.5
  rows** for 5,000 ticks, since the previous call also left `max_size` at
  bytewax's default of 50. The sink re-keys every row onto a single batching
  key, giving **one block of 5,000**. Limits come from
  `INGEST_BATCH_MAX_SIZE` (100,000) and `INGEST_BATCH_TIMEOUT_SECONDS` (2.0),
  whichever is hit first.
- **Async inserts.** The upstream sink hardcodes `settings={"buffer_size": 0}`,
  so `async_insert` could not be passed — and this deployment's ClickHouse user
  lacks `ALTER USER`, ruling out a user-level default. `INGEST_ASYNC_INSERT=1`
  (default) lets the server coalesce our per-flush blocks into larger parts,
  which is the documented remedy for clients that cannot reach ~100k rows per
  insert. At this tape's rate the 2-second timeout fires long before the size
  cap, so this is the lever that actually does the work.

`INGEST_WAIT_FOR_ASYNC_INSERT=0` (default) is fire-and-forget: the server acks
before the data is durably written, so a crash can lose the in-flight buffer and
insert-time errors never reach the client. That is a deliberate trade for a tick
archive the daily reconciler back-fills from the authoritative API — set it to
`1` to trade throughput for durability confirmation.

---

### 2. `reconciler.py` — daily back-fill (once per session)

Polls the DNSE REST API at 15:05 ICT for any ticks that the stream missed (connectivity gaps, late symbols). Compares against what is already in ClickHouse and patches only the delta.

**Run:**
```bash
python reconciler.py          # respects schedule guard (15:00 ICT)
python reconciler.py --force  # bypass guard
```

---

### 3. `hawkes_signal_worker.py` — live signal + Telegram alerts (always-on)

Polls on a configurable interval (default 60 s). Each cycle:

1. **OHLC refresh** — runs an aggregate SQL over `ticks` for today's session and upserts the result into `ohlc_5m`. Safe to re-run; `ReplacingMergeTree(ver)` deduplicates on `(symbol, ts)`.

2. **Bar load** — reads full `ohlc_5m` history for the symbol via `load_ohlc_from_clickhouse`.

3. **Hawkes BSI** — exponentially-decayed buy/sell imbalance:
   ```
   BSI[i] = BSI[i-1] * exp(-κ) + (buy_volume[i] - sell_volume[i])
   q_lo[i] = p-th percentile of BSI over last N bars   (default p=5)
   q_hi[i] = (1-p)-th percentile of BSI over last N bars (default p=95)
   ```

4. **KAMA gate** — Kaufman Adaptive MA filters out noise:
   - LONG requires `close > KAMA`
   - SHORT requires `close < KAMA`

5. **Signal state machine** (`generate_signals`):

   | Event | Condition | Action |
   |---|---|---|
   | LONG entry | BSI crosses above q_hi AND price up since last q_lo cross AND calm gate AND KAMA | `long_entries[i+1] = True` |
   | SHORT entry | BSI crosses below q_lo AND KAMA AND calm gate | `short_entries[i+1] = True` |
   | LONG exit | BSI drops below q_lo OR stop-loss hit | `long_exits[i+1] = True` |
   | SHORT exit | BSI rises above q_hi OR stop-loss hit | `short_exits[i+1] = True` |

6. **Dedup** — signal state persisted in `.state/hawkes_signal_state.json`. A bar timestamp is stored after each alert; the same bar never triggers twice across restarts.

7. **Telegram** — sends formatted message via `telegram_notifier.send_telegram_message`.

**Run:**
```bash
python hawkes_signal_worker.py
python hawkes_signal_worker.py --symbol VN30F1M --poll 60
```

---

### 4. `large_order_ingest.py` — Layer 3 large-order blocks (materialized view)

**Not a running worker.** A single institutional order arrives as many
sub-second fills, so trades are **merged into fixed-second blocks** per
`(symbol, side)` to capture the true size that per-tick filtering would miss.
Since `tick_ingest` already writes every watchlist symbol into `ticks`, that
merge is a ClickHouse **materialized view** — no second feed subscription, no
event-time watermark, no process to keep alive. `large_order_ingest.py` is now
the CLI that creates and backfills it.

```
ticks ──(MV: bucket → drop auctions → sum)──▶ large_order_blocks   AggregatingMergeTree
                                                      │
                                                      └──▶ large_orders_live   view: + vwap
```

**Why `AggregatingMergeTree` and not the `large_orders` table.** An MV sees only
the rows of one INSERT. `tick_ingest` flushes every ~2s while blocks are 1s
wide, so one bucket's fills routinely arrive across several inserts and the view
emits a *partial* block each time. `SimpleAggregateFunction(sum, …)` makes those
partials additive; `ReplacingMergeTree` would overwrite them and silently
undercount. Verified on real ticks: 60k ticks inserted in 120 batches produced
15,971 partial rows that merge to exactly the 15,963 blocks Python computes.

**No threshold in the MV.** A partial block can sit below
`LARGE_ORDER_MIN_VALUE` while the finished block clears it, so filtering must
happen on read — `large_orders_live` exposes `dollar_value` just as the
`large_orders` table did.

**Reading it.** `large_orders_live` aggregates on read, which is required rather
than cosmetic: `AggregatingMergeTree` merges parts in the background, so a plain
`SELECT` on `large_order_blocks` sees unmerged partials and reports several
small blocks where there is one. Aggregating in the view cannot be forgotten by
a caller, unlike `FINAL`.

**Bucket alignment.** `bucket_start` floors against `BLOCK_ALIGN_EPOCH`;
ClickHouse's `toStartOfInterval` floors against the unix epoch. They agree only
when `LARGE_ORDER_WINDOW_SECONDS` divides the offset between them — true for 1,
2, 5, 10, 15, 30, 60 but not e.g. 7. `verify_bucket_alignment` refuses setup
rather than letting the live and reconciled paths diverge.

> **Auction exclusion** (on by default, `LARGE_ORDER_EXCLUDE_AUCTIONS=1`):
> ATO/ATC trades clear at a single auction price and would otherwise form one
> huge fake block (e.g. the FPT close prints 185k shares in one 14:45 tick).
> Trades whose exchange-local time (truncated to the second) fall in
> `LARGE_ORDER_ATO_WINDOW` (`09:00:00,09:15:00`) or `LARGE_ORDER_ATC_WINDOW`
> (`14:30:00,15:00:00`) are dropped on both the live and reconciler paths.

Block aggregation lives in `core/large_order.py` **twice**: the Python
accumulators (`new_block_acc` / `fold_tick` / `merge_acc` / `finalize_block`),
still used verbatim by the reconciler, and their SQL mirror
(`auction_predicate_sql` / `block_aggregation_sql`) used by the MV. They are
adjacent deliberately — `tests/test_large_order_mv.py` pins the SQL shape and
`--verify` checks both agree on real ticks.

> **The MV only sees future inserts.** History needs `--backfill`, which is
> idempotent per day (it deletes the day before reinserting, because summing
> partials twice would double every block).

> **Tick re-inserts double-count.** The MV counts rows as they are inserted, so
> a tick written to `ticks` twice is aggregated twice, even though
> `ReplacingMergeTree` later collapses the duplicate. `reconciler.py` inserts
> only genuinely missing ticks, so this is bounded — but with
> `large_order_reconciler` retired there is no longer a pass that corrects it.
> `--backfill` for the affected day recomputes it from `ticks` if needed.

> **Equity history starts 2026-08-24**, when `tick_ingest` began ingesting the
> watchlist. The MV can only aggregate what is in `ticks`, and equity ticks do
> not exist before that date, so `--backfill` cannot recover it. Futures blocks
> go back to 2025-05-05. The 41 reconciled days in `large_orders`
> (2026-05-04 → 2026-06-29, 1,446 symbol-days) are no longer read.

**Run:**
```bash
python workers/large_order_ingest.py --setup      # create table + MV + view
python workers/large_order_ingest.py --backfill   # aggregate existing ticks
python workers/large_order_ingest.py --verify     # view vs core.large_order
python workers/large_order_ingest.py --status
```

### 5. `large_order_reconciler.py` — Layer 3 daily back-fill (**retired**)

> **No longer run.** The materialized view above is the only large-order path;
> the backend reads `large_orders_live` and nothing reads `large_orders`. The
> script and its `large_orders` table are kept as a frozen archive of the 41
> days it reconciled. Nothing schedules it (no compose service, cron or Prefect
> flow) — it was always manual. Documented below for the archive's provenance.

**Same DNSE GraphQL API as the tick reconciler** (`DNSEClient.fetch_day_ticks`);
the only difference is scope — it loops over the watchlist symbols instead of
the single front-month contract. Per symbol: pull the day's tape, merge into
in-session large blocks with the **same aggregation as the live path**, then
**upsert all** large blocks. Because a block's size can grow as fills arrive,
the reconciler is authoritative — `ReplacingMergeTree(received_at)` overwrites
any partial live block by `(symbol, sending_time, side)`; it also reports how
many block keys were new. Uses its own run-state file
(`state_dir/large_order_reconciler_run_state.json`).

**Run:**
```bash
python workers/large_order_reconciler.py                  # respects 15:00 ICT guard
python workers/large_order_reconciler.py --force          # bypass guard
python workers/large_order_reconciler.py --date 2026-06-20 --dry-run
python workers/large_order_reconciler.py --symbol FPT --symbol HPG
# Date-range backfill — weekends skipped, bypasses the schedule guard:
python workers/large_order_reconciler.py --from-date 2026-06-01 --to-date 2026-06-10
```

---

### 6. `block_episode_ingest.py` — trade-flow features (materialized view)

**Not a running worker**, and no longer a detector. Replaced the Bytewax
dataflow and the z-score detector it ran, both since deleted. Features are
maintained by ClickHouse; anomaly scoring happens on demand in the backend.

```
ticks ──MV──▶ trade_flow_seconds     1-second bars, AggregatingMergeTree
                    │
                    └──view──▶ trade_flow_windows     21 features per N-second window
                                        │
                          backend: robust z per (symbol, time-of-day)
                                        │
                                Isolation Forest
                                   "unusual?"
                                        │
                            GET /api/v1/trade-flow/anomalies
```

**What the feed allows.** This is a trade/ticker tape, not Market-By-Order:
no resting book, no order IDs, no quotes, no adds/cancels. OFI, book imbalance,
replenishment and queue depletion are therefore **not computable**. The features
target what trade flow does carry — size concentration, temporal clustering,
directional imbalance, price impact.

**Why two levels.** An MV sees only the rows of one INSERT, and `tick_ingest`
flushes every ~2s, so computing anything from tick *order* inside it would be
silently wrong at every insert boundary. Level 1 therefore stores only
order-independent values — sums, min/max, `argMin`/`argMax`/`quantiles`
**states**, and a per-tick millisecond offset. Level 2 reads a table, so it can
restore order: that is where returns, realized volatility, burst intensity and
inter-arrival are derived. Verified on real ticks: 60k ticks in 200 inserts
produced 14,085 torn partial bars whose window features are **bit-identical** to
a one-shot rebuild (`--verify` runs this check on demand).

**Inter-arrival is exact, not approximated.** A tick's millisecond offset within
its second depends only on that tick, so collecting the offsets is
order-independent, and `SimpleAggregateFunction(groupArrayArray, Array(UInt16))`
merges them by concatenation. Level 2 rebuilds absolute milliseconds, sorts them
back into arrival order and differences them — giving
`median_interarrival_ms`, `p90_interarrival_ms` and `same_ms_share` that match
raw ticks exactly (measured: 0 differences across 28,904 windows). Costs
517 KiB compressed per trading day, ~9% of the bars table.

The per-second proxies alone were **not** sufficient: the best of them
(`max_trades_per_second`) correlates with true median inter-arrival at only
r = −0.61, so roughly 60% of the timing variance was invisible before this.

**Deliberate departures from a textbook MBO feature set:**

| Wanted | What is built | Why |
|---|---|---|
| `p10_interarrival_ms` | `same_ms_share` | The exchange stamps at **millisecond** resolution and 32% of gaps are already 0, so p10 is pinned at zero for almost every window. The same-millisecond *share* measures the same clustering and stays informative — simultaneous fills are a strong algo tell. |
| `large_trade_volume_ratio` vs a trailing threshold | `size_hhi` (Σq²/V²), `top_trade_share`, `p95_to_median` | A per-tick threshold is not knowable at aggregation time. Σq² is additive, so HHI is exact and threshold-free; the scorer normalizes per symbol instead. |
| Tick-rule direction proxy | real aggressor `side` | The feed carries it on ~99.7% of ticks. `side = 0` counts toward volume but neither direction. |

Timestamps are **millisecond**, not nanosecond, despite the `DateTime64(6)`
column — measured: 0% of ticks carry sub-millisecond detail, so this is the
exchange's resolution, not truncation on our side. It is why `p10_interarrival`
is unusable and `same_ms_share` replaces it.

**Run:**
```bash
python workers/block_episode_ingest.py --setup      # table + MV + window view
python workers/block_episode_ingest.py --backfill   # bars from existing ticks
python workers/block_episode_ingest.py --verify     # MV path vs one-shot rebuild
python workers/block_episode_ingest.py --status
```

Changing `BLOCK_EP_WINDOW_SECONDS` only needs `--setup` again — the 1-second bars
are window-agnostic, so no backfill is required.

> **Removed alongside:** `core/large_execution.py`,
> `block_episode_reconciler.py`, the `BLOCK_EPISODES_*` schemas, the
> `block_episodes` table and the `/block-episodes` endpoint and its UI panel.
> The file name `block_episode_ingest.py` is the last vestige of the old name.

---

## Data model

### `large_orders` (Layer 3 blocks)
```sql
symbol       String
sending_time DateTime64(6, 'UTC')   -- block bucket start (floored to window)
side         Int32                  -- 1=BUY, 2=SELL, 0=unknown
vwap         Float64                -- volume-weighted price = dollar_value / total_qty
total_qty    Int64                  -- summed quantity in the block
dollar_value Float64                -- summed notional Σ(price * qty)
num_trades   Int64                  -- fills merged into the block
received_at  DateTime64(6, 'UTC')

ENGINE = ReplacingMergeTree(received_at)
ORDER BY (symbol, sending_time, side)
PARTITION BY toYYYYMMDD(sending_time)
```

### `ticks`
```sql
symbol       LowCardinality(String)   -- ~200 distinct: watchlist + VN30F contracts
sending_time DateTime64(6, 'UTC')
match_price  Float64
match_qty    Int64
side         Int32   -- 0=unknown, 1=BUY, 2=SELL
received_at  DateTime64(6, 'UTC')

ENGINE = ReplacingMergeTree(received_at)
ORDER BY (symbol, sending_time, match_price, match_qty, side)
PARTITION BY toYYYYMM(sending_time)
```

### `ohlc_5m`
```sql
symbol      String
ts          DateTime('Asia/Ho_Chi_Minh')   -- 5-min bucket start
open        Float64
high        Float64
low         Float64
close       Float64
volume      Int64
buy_volume  Int64
sell_volume Int64
ver         DateTime64(3) DEFAULT now64(3)

ENGINE = ReplacingMergeTree(ver)
ORDER BY (symbol, ts)
```

---

## Configuration

All workers read from environment variables (or `.env`). Key variables:

| Variable | Default | Used by |
|---|---|---|
| `LOG_LEVEL` | `INFO` | all workers |
| `DNSE_API_KEY` | — (required) | tick_ingest |
| `DNSE_API_SECRET` | — (required) | tick_ingest |
| `DNSE_WS_URL` | `wss://ws-openapi.dnse.com.vn` | tick_ingest |
| `DNSE_TRADE_BOARDS` | `G1` | tick_ingest (subscribed; `ALL_BOARDS` is the full set) |
| `TICK_ALLOWED_BOARDS` | `G1` | tick_ingest (stored; empty = all) |
| `DNSE_WS_ENCODING` | `json` | tick_ingest (`json` or `msgpack`) |
| `DNSE_WS_SESSION_START` | `08:00` | tick_ingest |
| `DNSE_WS_SESSION_END` | `16:00` | tick_ingest |
| `DNSE_WS_SESSION_TZ` | `EXCHANGE_TZ` / `Asia/Ho_Chi_Minh` | tick_ingest |
| `DNSE_WS_SESSION_GATE` | `1` | tick_ingest |
| `INGEST_BATCH_MAX_SIZE` | `100000` | tick_ingest |
| `INGEST_BATCH_TIMEOUT_SECONDS` | `2.0` | tick_ingest |
| `INGEST_ASYNC_INSERT` | `1` | tick_ingest |
| `INGEST_WAIT_FOR_ASYNC_INSERT` | `0` | tick_ingest |
| `INGEST_ASYNC_BUSY_TIMEOUT_MS` | `1000` | tick_ingest |
| `INGEST_ASYNC_MAX_DATA_SIZE` | `10485760` | tick_ingest |
| `BLOCK_EP_WINDOW_SECONDS` | `30` | block_episode_ingest, backend |
| `BLOCK_EP_TOD_BUCKET_MINUTES` | `30` | backend (normalization) |
| `BLOCK_EP_MIN_WINDOWS_TO_FIT` | `200` | backend (Isolation Forest) |
| `BLOCK_EP_CONTAMINATION` | `0.01` | backend (Isolation Forest) |
| `MQTT_HOST` | `datafeed-lts-krx.dnse.com.vn` | isp, price_alerts |
| `MQTT_PORT` | `443` | isp, price_alerts |
| `TICK_SYMBOL` | current VN30F contract | tick_ingest, reconciler |
| `CLICKHOUSE_HOST` | `localhost` | all |
| `CLICKHOUSE_PORT` | `9010` (native) / `8123` (HTTP) | all |
| `CLICKHOUSE_USER` | — | all |
| `CLICKHOUSE_PASSWORD` | — | all |
| `CLICKHOUSE_DB` | `default` | all |
| `HAWKES_SYMBOL` | `VN30F1M` | signal worker |
| `HAWKES_POLL_INTERVAL` | `60` | signal worker |
| `HAWKES_KAPPA` | `0.1` | signal worker |
| `HAWKES_QUANTILE_LOOKBACK` | `100` | signal worker |
| `HAWKES_ALLOW_SHORT` | `1` | signal worker |
| `HAWKES_ALERT_EXITS` | `0` | signal worker |
| `TELEGRAM_ENABLED` | `0` | signal worker, price_alerts |
| `TELEGRAM_BOT_TOKEN` | — | signal worker, price_alerts |
| `TELEGRAM_CHAT_ID` | — | signal worker, price_alerts |

Full schema: `config.py` — one `@dataclass` per concern.

---

## Deployment (minimal)

Three processes, run in parallel:

```bash
# Terminal 1 — tick stream (Bytewax)
python -m bytewax.run workers.tick_ingest:flow

# Terminal 2 — Hawkes signal + alerts
TELEGRAM_ENABLED=1 TELEGRAM_BOT_TOKEN=xxx TELEGRAM_CHAT_ID=yyy \
python workers/hawkes_signal_worker.py

# Cron — daily reconciler (08:05 UTC = 15:05 ICT, weekdays)
5 8 * * 1-5  cd /path/to/worker && python workers/reconciler.py
```

For production, wrap each process in a systemd unit or Docker container and set env vars via a `.env` file or secrets manager.
