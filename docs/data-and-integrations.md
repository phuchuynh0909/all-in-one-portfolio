# Data Stores & External Integrations

How the platform persists data and where market data comes from. Pairs with
[architecture.md](./architecture.md).

## Data stores

| Store | Tech | Owner | Holds |
| --- | --- | --- | --- |
| Time-series / ticks | **ClickHouse** | workers write, backend reads | Live ticks, 5m OHLC, streaming-derived signals, historical timeseries |
| App / portfolio DB | **SQLite** (`portfolio.db`) | backend (+ `price_alerts` worker reads) | Portfolio holdings, price alerts, app state |
| Lakehouse | **Delta Lake on MinIO/S3** | backend reads (`stores/`) | Historical stock OHLCV, sector data, raw + parsed wichart research reports, feature store |
| ML models | Local files (`models/`, `lstm_cache/`) | backend | XGBoost/LightGBM/CatBoost artifacts, LSTM caches |

### ClickHouse

- Backend: `db/clickhouse.py` yields a `clickhouse_connect` client from
  `CLICKHOUSE_HOST/PORT/USER/PASSWORD/DB`.
- Workers: `infra/clickhouse_client.py` + `model.py` define Arrow/ClickHouse
  schemas and table names; Bytewax `ch_operators` insert batches.
- A `mock_clickhouse` source/sink lets workers run offline.

### SQLite

- SQLAlchemy 2.0 models in `db/models/` (`market`, `financial`, `portfolio`).
- Configured via `APP_DATABASE_URL` (default `sqlite:///portfolio.db`).
- The `price_alerts` worker bind-mounts `backend/portfolio.db` to read alert
  definitions.

### Delta Lake (MinIO / S3)

Configured in `core/settings.py` via `MINIO_*` and `*_DELTA_TABLE` settings.
`delta_storage_options` builds the S3-compatible connection (AWS-style env for
`deltalake`/`object_store` against MinIO over HTTP). Tables:

- `stocks_delta_table` — historical stock OHLCV
- `sector_delta_table` — wichart sector data
- `wichart_report_delta_table` / `wichart_report_detail_delta_table` — research
  reports (raw + parsed)
- `stocks_feature_store` — engineered features for ML

Read via `stores/feature_store.py` and `stores/raw_wichart_report.py`.

## External integrations

| Provider | Client | Transport | Used for |
| --- | --- | --- | --- |
| **DNSE OpenAPI** | `services/dnse_client.py`, `worker/infra/dnse_client.py` | Signed REST | Live/last matched prices, trade data (server-side secret) |
| **DNSE MQTT** | `worker/infra/mqtt_input.py` | MQTT | Live tick stream (`.../tick/v1/roundlot/symbol/{symbol}`) |
| **money24h** | `services/money24h_client.py` | REST | Market data |
| **wichart** | `services/wichart_news_client.py`, `utils/wichart.py` | REST | News, sector data, research reports |
| **ruatichsan** | `services/ruatichsan_client.py` | REST | Market data |
| **Ollama** | via `services/tradingagents/runner.py` | HTTP (local LLM) | Multi-agent trading research |
| **Telegram** | `worker/infra/telegram_notifier.py` | Bot API | Signal/alert notifications |
| **Prefect** | `services/prefect_workflow_service.py`, `worker/infra/reconciler_schedule.py` | — | Workflow orchestration / reconciliation |

### Credentials & secrets

- Live in `.env` / `prod.env` / `.env.example` (**git-ignored**) and are read
  from the environment via `core/settings.py`. Notable keys: `DNSE_API_KEY`,
  `DNSE_API_SECRET`, `DNSE_API_VERSION`, `CLICKHOUSE_*`, `MINIO_*`.
- **Never** print, commit, or hardcode secrets.
- **Never** issue live trading/market calls in tests or verification — mock the
  DNSE / money24h / wichart clients. (Note: `testing/test_dnse_api.py` fires a
  live signed DNSE request at import time and has no assertions — do not run it,
  and do not copy its embedded-credential pattern.)

## Data lifecycle summary

1. **Live:** DNSE MQTT → `tick_ingest` → ClickHouse ticks → downstream workers
   (`ohlc_5m`, `hawkes`, `isp`, `large_order_*`) → ClickHouse signals →
   backend read-model services → frontend.
2. **Historical:** crawlers / importers → Delta Lake (MinIO) → backend
   `stores/` + services → frontend charts/tables.
3. **On-demand quotes:** frontend → backend `quote` route → `dnse_client`
   (signed REST) → frontend.
4. **Research/agents:** wichart reports (Delta) + ClickHouse OHLCV →
   TradingAgents (Ollama) → `trading_agents` / `chat` routes → frontend.
