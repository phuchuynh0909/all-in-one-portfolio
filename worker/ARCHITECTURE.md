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
                  match_qty, side, received_at)
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
| **Key modules** | `dnse_ws_input.DnseTradeSource`, `watchlist.load_symbols`, `tick_contract.normalize_tick`, `clickhouse_sink.output`, `model.TICKS_*` |

Symbol scope: every symbol in `watchlist.json` **plus** the current VN30F front-month
contract (`TICK_SYMBOL`, defaulting to `vn30f_symbol.current_symbol()`).

Feed: `wss://ws-openapi.dnse.com.vn/v1/stream`, HMAC-SHA256 auth, channel
`tick_extra.{board}.json`. Boards `G1` (even lot) and `G4` (odd lot) carry both
stocks and derivatives, so one subscription covers equities and the futures
contract. Trade-Extra payloads have no message-type field — trades are matched
on shape (`symbol` + `matchPrice` + `time`).

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

### 4. `large_order_ingest.py` — Layer 3 large-order blocks (always-on)

The sparse counterpart to `tick_ingest`. Subscribes to the live tick feed for
**every symbol in the watchlist** (not just the VN30F contract) and **merges
trades into fixed-second blocks** per `(symbol, side)`: a single institutional
order usually arrives as many sub-second fills, so block-merging captures the
true size that per-tick filtering would miss. Only blocks whose total notional
`Σ(price × qty)` clears `LARGE_ORDER_MIN_VALUE` (default `1000`) are stored.

Bytewax steps: MQTT input (all watchlist topics) → normalize via
`tick_contract.normalize_tick` → key by `SYMBOL|SIDE` → **drop ATO/ATC auction
prints** (`is_auction_time`) → `fold_window` (event-time `TumblingWindower`,
`LARGE_ORDER_WINDOW_SECONDS`, default `1s`) merging ticks into blocks →
`finalize_block` (vwap = notional/qty) → `op.filter` keep `is_large_block` →
ClickHouse `large_orders` sink.

> **Auction exclusion** (on by default, `LARGE_ORDER_EXCLUDE_AUCTIONS=1`):
> ATO/ATC trades clear at a single auction price and would otherwise form one
> huge fake block (e.g. the FPT close prints 185k shares in one 14:45 tick).
> Trades whose exchange-local time (truncated to the second) fall in
> `LARGE_ORDER_ATO_WINDOW` (`09:00:00,09:15:00`) or `LARGE_ORDER_ATC_WINDOW`
> (`14:30:00,15:00:00`) are dropped on both the live and reconciler paths.

Block aggregation lives in `core/large_order.py` (`new_block_acc` / `fold_tick`
/ `merge_acc` / `finalize_block`) and is shared verbatim with the reconciler.

> Event-time windows flush a block only once the watermark passes the bucket
> end (driven by later trades + `LARGE_ORDER_WAIT_SECONDS`, default `2s`). A
> quiet symbol's final block may lag until more ticks arrive; the daily
> reconciler back-fills the authoritative end-of-day blocks regardless.

**Run:**
```bash
python -m bytewax.run workers.large_order_ingest:flow
```

### 5. `large_order_reconciler.py` — Layer 3 daily back-fill

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
| `DNSE_API_KEY` | — (required) | tick_ingest, block_episode_ingest |
| `DNSE_API_SECRET` | — (required) | tick_ingest, block_episode_ingest |
| `DNSE_WS_URL` | `wss://ws-openapi.dnse.com.vn` | tick_ingest, block_episode_ingest |
| `DNSE_TRADE_BOARDS` | `G1,G3,G4,G7,T1,T2,T3,T4,T6` | tick_ingest, block_episode_ingest |
| `DNSE_WS_ENCODING` | `json` | tick_ingest, block_episode_ingest |
| `INGEST_BATCH_MAX_SIZE` | `100000` | tick_ingest |
| `INGEST_BATCH_TIMEOUT_SECONDS` | `2.0` | tick_ingest |
| `INGEST_ASYNC_INSERT` | `1` | tick_ingest |
| `INGEST_WAIT_FOR_ASYNC_INSERT` | `0` | tick_ingest |
| `INGEST_ASYNC_BUSY_TIMEOUT_MS` | `1000` | tick_ingest |
| `INGEST_ASYNC_MAX_DATA_SIZE` | `10485760` | tick_ingest |
| `MQTT_HOST` | `datafeed-lts-krx.dnse.com.vn` | isp, price_alerts, large_order_ingest |
| `MQTT_PORT` | `443` | isp, price_alerts, large_order_ingest |
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
