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
| **Source** | MQTT broker (live) or `MockClickHouseSource` (dev) |
| **Sink** | ClickHouse `ticks` table via `bytewax.clickhouse` |
| **Key modules** | `mqtt_input.MqttSource`, `tick_contract.normalize_tick`, `model.TICKS_*` |

Steps inside the dataflow:

1. `op.input` — subscribe to `plaintext/quotes/krx/mdds/tick/v1/roundlot/symbol/{symbol}`
2. `key_by_symbol_ingest` — parse JSON, normalise fields via `tick_contract.normalize_tick`, key by symbol
3. `op.filter` — drop malformed / non-matching rows
4. `transform_for_ticks` — convert to ClickHouse insertion tuple
5. `ch_operators.output` — batch-insert into `ticks`

**Run:**
```bash
python -m bytewax.run tick_ingest:flow
```

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

## Data model

### `ticks`
```sql
symbol       String
sending_time DateTime64(6, 'UTC')
match_price  Float64
match_qty    Int64
side         Int32   -- 0=unknown, 1=BUY, 2=SELL
received_at  DateTime64(6, 'UTC')

ENGINE = ReplacingMergeTree(received_at)
ORDER BY (symbol, sending_time, match_price, match_qty, side)
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
| `MQTT_HOST` | `datafeed-lts-krx.dnse.com.vn` | tick_ingest |
| `MQTT_PORT` | `443` | tick_ingest |
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
