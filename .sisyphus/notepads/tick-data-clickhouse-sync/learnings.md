# Learnings

## [2026-03-26] Session ses_2d7ab7484ffelG22ua5LGrVioN

### Worker Pattern
- Workers use Bytewax Dataflow pattern (isp.py, price_alerts.py)
- Schema: PyArrow schema in model.py + ClickHouse DDL string
- Config: dataclass + from_env() classmethod in config.py; global `config = Config.load()`
- ClickHouse client: lazy singleton via `get_clickhouse_client()` in clickhouse_client.py
- Input: MqttSource (Bytewax DynamicSource) in mqtt_input.py
- State: SQLite in `state_dir/part-0.sqlite3` (Bytewax stateful state)
- Output: `bytewax_clickhouse.operators.output()` with pa_schema + ch_schema + order_by

### Key Field Names (from crawl_dnse.py + isp.py)
- API raw fields: `symbol`, `matchPrice`, `matchQtty`, `sendingTime`, `side` (1=BUY/SIDE_BUY, 2=SELL/SIDE_SELL)
- ISP parse_tick maps: ts (datetime UTC), symbol, price=matchPrice, size=matchQtty, side (int BUY=1/SELL=2)
- Canonical v1 ClickHouse storage: `symbol`, `sending_time` (DateTime64 UTC), `match_price`, `match_qty` (Int64 nullable), `side` (Int32 nullable), `received_at` (DateTime64 UTC versioning)

### ClickHouse Connection
- Using clickhouse-connect library (not native driver)
- Default port 9010 (not 8123/9000 - custom), user=myuser, password=mypassword, db=default
- `.env` in worker/ sets: CLICKHOUSE_HOST, CLICKHOUSE_PORT etc.

### v1 Symbol
- Single symbol: `41I1G4000` (from .env MQTT_TOPICS + crawl_dnse.py SYMBOL)
- Board: 2

### Session
- Asia/Ho_Chi_Minh 09:00 - 15:00
- Reconcile once at 15:00 local time
- Window: [09:00, 15:00] inclusive both ends
- Storage timezone: UTC

### Composite Key (dedupe)
- symbol, sending_time, match_price, match_qty, side
- ReplacingMergeTree version: received_at (latest write wins)

## [2026-03-26] Scope fidelity re-check

- `worker/crawl_dnse.py` is tracked as pre-existing reference script (`git log -- worker/crawl_dnse.py` shows dedicated tracking commit), not part of new tick-sync implementation logic.
- v1 contract anchors are implemented in code: default symbol `41I1G4000`, session `09:00-15:00` Asia/Ho_Chi_Minh, dedupe key `(symbol, sending_time, match_price, match_qty, side)`, and once-per-day gate after 15:00 via `should_run_today`.
