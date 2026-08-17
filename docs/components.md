# Component Breakdown

Internal structure of each deployable. Pairs with the system overview in
[architecture.md](./architecture.md).

## Backend API (`backend/`)

FastAPI app factory in `backend/app/main.py` (`get_app()`), served as
`app.main:app`. Layered as routes → services → stores/clients.

### `api/v1/routes/`

One module per domain, each exposing a router mounted under `/api/v1`:

| Route | Purpose |
| --- | --- |
| `health` | Liveness/readiness. |
| `quote` | Live/last matched prices (DNSE-backed). |
| `timeseries` | Historical OHLCV + computed indicators. |
| `portfolio` | Portfolio holdings, P&L, optimization. |
| `scanner` | Market scans over indicator/strategy signals. |
| `backtest` | Run/visualize strategy backtests. |
| `regime` | Market-regime classification (HMM/GARCH/GMM). |
| `sector` | Sector aggregates & rankings. |
| `financial_statements` | Fundamental financial data. |
| `report` / `chat` | Research reports + RAG chat over them. |
| `trading_agents` | Drive the vendored multi-agent framework. |
| `isp_alerts` (`/isp`), `large_orders`, `price_alerts` | Streaming-derived signals surfaced from ClickHouse / SQLite. |
| `cw`, `future`, `mvf` | Covered warrants, futures (VN30F), multi-variate LSTM forecasts. |
| `data_crawler`, `workflows` | Trigger crawls / Prefect workflows. |
| `auth` | Authentication. |

### `services/`

Business logic, grouped by concern:

- **External clients:** `dnse_client.py` (live quotes/trades via signed DNSE
  OpenAPI), `money24h_client.py`, `wichart_news_client.py`,
  `ruatichsan_client.py`.
- **Indicators** (`services/indicators/`): reusable statistical/technical
  indicators — `squeeze_ttm`, `vwap`, `chandelier_exit`, `hull_butterfly`,
  `williams_vix_fix`, `gkyz_volatility`, `garch_regime`, `spread_gmm`,
  `tica_hmm_regime`, `zcore`, `directional_change`, `trailing_sl`,
  `regime_signals`, `common`.
- **Strategies** (`services/strategies/` + `services/backtest_strategies/`):
  `dual_rsi`, `squeeze_breakout`, `breakout_ttm(_v1)`, etc.
- **Backtesting:** `backtest_service.py`, `backtest_plot_service.py`.
- **ML / forecasting:** `ml_models.py`, `mvf_lstm_service.py`,
  `optimization_service.py`, `embeddings.py` (XGBoost/LightGBM/CatBoost model
  paths configured in settings; LSTM cache in `lstm_cache/`).
- **Data:** `data_loader.py`, `financial_data_importer.py`, `stock_service.py`,
  `sector_service.py`, `timeseries_indicators.py`.
- **RAG / reports:** `report_service.py`, `report_rag_service.py`.
- **Streaming-derived read models:** `isp_alerts_service.py`,
  `large_orders_service.py`, `price_alert_service.py`,
  `prefect_workflow_service.py`.
- **TradingAgents integration:** `services/tradingagents/` (see below).

### `core/`, `db/`, `schemas/`, `stores/`, `utils/`

- `core/settings.py` — pydantic-settings config (env prefix `APP_`), plus
  ClickHouse, DNSE, MinIO/Delta, and ML model paths.
- `db/` — SQLAlchemy 2.0 base + models (`market`, `financial`, `portfolio`) on
  SQLite; `db/clickhouse.py` yields a `clickhouse_connect` client as a FastAPI
  dependency.
- `schemas/` — Pydantic v2 request/response models per domain.
- `stores/` — Delta Lake readers (`feature_store.py`, `raw_wichart_report.py`).
- `utils/` — helpers (`wichart.py`, `chat_protos.py`).

### Vendored TradingAgents

`backend/app/services/tradingagents/` wires the unmodified
`vendor/TradingAgents` framework into the platform:

- `__init__.py` — `sys.path` shim that locates the vendored tree (dev repo root,
  Docker `/app/vendor` mount, or `TRADINGAGENTS_VENDOR_PATH` override). No pip
  install required; a no-op if the package is already installed.
- `vn_data.py` — a "portfolio" data vendor backed by this platform's VN-market
  data (ClickHouse OHLCV + wichart reports).
- `runner.py` — configures the agent graph for a local **Ollama** server,
  registers the VN vendor into TradingAgents' dispatch, and streams a run.
- `sector_analyst.py`, `store.py`, `kb_search.py`, `web_search.py` — supporting
  agents/tools.

> Do not modify code inside `vendor/`. It shows up only as a moved submodule
> pointer, not a normal diff. Surface any change explicitly.

## Frontend (`frontend/`)

React 18 + TypeScript SPA built with Vite; MUI for UI; TanStack Query for
server state.

```
src/
  main.tsx, App.tsx      App bootstrap + routing (react-router-dom).
  pages/                 One screen per domain (see below).
  components/            Reusable UI by area: chart, portfolio, sector,
                         financial, report, chat, market, backtest.
  lib/
    api.ts               Axios/fetch base client (VITE_API_BASE_URL).
    services/*.ts        Typed API wrappers, one per backend domain.
    tv/                  Charting: datafeed, studies, store, TradingView-style
                         charting_library typings.
  hooks/                 Shared React hooks.
  theme.ts               MUI theme.
```

**Pages:** `Home`, `Portfolio`, `Chart`, `Live`, `Scanner`, `Backtest`,
`BacktestVisualization`, `Sector`, `Report`/`ReportDetail`, `FinancialStatements`,
`Regime`, `Future`, `CW`, `Alerts`, `TradingAgents`, `ChatAgents`, `Health`.

The frontend talks **only** to the backend (`VITE_API_BASE_URL`, default
`http://localhost:8000/api/v1`). Charts render via `lightweight-charts`,
`recharts`, `@mui/x-charts`, and `@bokeh/bokehjs`.

## Stream workers (`worker/`)

Independent Bytewax dataflows + Prefect schedules, each run as its own process /
container. Shared code:

```
workers/     Dataflow entry points (run via python -m bytewax.run workers.<n>:flow).
core/        Domain logic: tick_contract, vn30f_symbol, hawkes_indicators,
             large_order, watchlist, helper.
infra/       I/O: mqtt_input (DNSE MQTT), clickhouse_client, dnse_client,
             telegram_notifier, reconciler_schedule, audit_queries, mock_clickhouse.
config.py    Env-driven config (ISP params, tick sync, session windows, TZ).
model.py     ClickHouse/Arrow schemas & table definitions.
scripts/     Backfills and one-off runs (backfill_ticks, run_pipeline, run_audit).
```

**Workers:**

| Worker | Role |
| --- | --- |
| `tick_ingest` | Subscribe to DNSE MQTT tick topic, normalize, write to ClickHouse `ticks`. |
| `ohlc_5m` | Aggregate ticks into 5-minute OHLC bars. |
| `hawkes_signal_worker` | Hawkes-process signal detection; notifies (Telegram). |
| `isp` | Intraday statistical-profile alerts. |
| `large_order_ingest` / `large_order_reconciler` | Detect & reconcile large orders. |
| `price_alerts` | Evaluate user price alerts (reads `backend/portfolio.db`), notify. |
| `reconciler` | Audit/backfill consistency (Prefect-scheduled). |

Config toggles a `MockClickHouseSource` / `MockClickHouse` for offline/dev runs
so workers can run without live MQTT or a real ClickHouse.
