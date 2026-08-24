# High-Level Architecture

An all-in-one Vietnamese-market investment platform. It combines live and
historical market data, technical/statistical indicators, backtesting, ML
models, and a multi-agent trading-research framework behind a single web app.

## System context

```
                         ┌──────────────────────────────┐
                         │        External sources        │
                         │  DNSE OpenAPI (signed REST)     │
                         │  DNSE MQTT (live ticks)         │
                         │  money24h / wichart / ruatichsan│
                         │  Ollama (local LLM)             │
                         └───────────────┬────────────────┘
                                         │
        ┌───────────────┐        ┌───────┴────────┐        ┌──────────────────┐
        │   Frontend    │  HTTP  │   Backend API   │        │  Stream workers   │
        │ React + Vite  │──────▶ │    FastAPI      │        │  Bytewax/Prefect  │
        │  (browser)    │  /api  │  app.main:app   │        │  (long-running)   │
        └───────────────┘        └───────┬────────┘        └────────┬─────────┘
                                         │                          │
                          ┌──────────────┼──────────────┬──────────┘
                          ▼              ▼              ▼
                    ┌──────────┐   ┌──────────┐   ┌───────────────────┐
                    │ ClickHouse│   │  SQLite  │   │ Delta Lake (MinIO)│
                    │ ticks /   │   │ portfolio│   │ OHLCV / sector /  │
                    │ timeseries│   │ + app db │   │ research reports  │
                    └──────────┘   └──────────┘   └───────────────────┘
```

Key idea: **the browser only ever talks to the backend.** External provider
credentials (DNSE trading API, MinIO, ClickHouse) stay server-side. Workers
write derived data into the shared stores; the backend reads from them.

## The three deployables

| Component | Path | Stack | Entry point | Responsibility |
| --- | --- | --- | --- | --- |
| Backend API | `backend/` | FastAPI, SQLAlchemy 2.0, Pydantic v2, loguru | `app.main:app` (via `get_app()`) | REST API, business logic, indicators, backtests, ML inference, TradingAgents orchestration |
| Frontend | `frontend/` | React 18, TypeScript, Vite, MUI, TanStack Query, lightweight-charts | `frontend/src/main.tsx` | SPA UI: charts, portfolio, scanner, backtests, reports, agents |
| Stream workers | `worker/` | Bytewax streaming, Prefect, ClickHouse | `python -m bytewax.run workers.<name>:flow` | Ingest live ticks, aggregate OHLC, compute signals/alerts |

See [components.md](./components.md) for the internal breakdown of each.

## Backend structure (`backend/app/`)

```
main.py            App factory: CORS, request-timing middleware, router wiring,
                   in-memory FastAPI cache.
api/v1/routes/     HTTP endpoints (one module per domain).
services/          Business logic. External clients + indicator/strategy libs +
                   TradingAgents integration.
core/settings.py   Pydantic-settings config (env prefix APP_, plus ClickHouse,
                   DNSE, MinIO/Delta, model paths).
db/                SQLAlchemy base + models (market, financial, portfolio) and
                   the ClickHouse client dependency.
schemas/           Pydantic request/response models.
stores/            Delta/feature-store readers.
utils/             Shared helpers (wichart parsing, chat protos, etc.).
```

All routes mount under `settings.api_v1_prefix` (`/api/v1`). Interactive docs at
`/docs`. A `log_request_time` middleware logs `METHOD PATH - status - Xms` for
every request (the primary debug channel — `make backend-logs`).

### Route domains (selected)

`health`, `portfolio`, `sector`, `timeseries`, `report`, `backtest`,
`financial_statements`, `data_crawler`, `scanner`, `workflows`, `isp_alerts`,
`large_orders`, `price_alerts`, `chat`, `auth`, `future`, `cw`, `regime`,
`trading_agents`, `quote`, `mvf`.

## Request flow (typical read)

1. Browser calls `GET /api/v1/<domain>/...` via the frontend service layer
   (`frontend/src/lib/services/*.ts`, base URL `VITE_API_BASE_URL`).
2. FastAPI route validates params, delegates to a `services/*` function.
3. The service reads from a store (ClickHouse / SQLite / Delta) and/or calls an
   external provider client, computes indicators/strategy output, and returns
   Pydantic models.
4. Response is cached in the in-memory FastAPI cache where decorated, and
   rendered by the SPA (charts via lightweight-charts / recharts, tables via
   MUI DataGrid).

## Live data flow (streaming)

```
DNSE WebSocket ─▶ worker: tick_ingest ─▶ ClickHouse (ticks)
                    │
                    ├─▶ ClickHouse MV           ─▶ large_order_blocks (no worker)
                    ├─▶ worker: ohlc_5m         ─▶ ClickHouse (5m OHLC)
                    ├─▶ worker: hawkes_signal   ─▶ signals + Telegram
                    ├─▶ worker: isp             ─▶ ISP alerts
                    └─▶ worker: price_alerts    ─▶ reads portfolio.db, notifies
```

Large-order blocks are aggregated inside ClickHouse by a materialized view on
`ticks`, so they need no worker container and no reconciliation pass —
`large_order_reconciler` is retired.

Workers are independent Bytewax dataflows (each its own container). They share
`worker_state` (a Docker volume) for durable operator state and write results to
ClickHouse; the backend then serves that data to the frontend. Reconciler
workers/schedules (Prefect) audit and backfill.

## Cross-cutting concerns

- **Config:** `backend/app/core/settings.py` (env prefix `APP_`) plus explicit
  `CLICKHOUSE_*`, `DNSE_*`, `MINIO_*` vars. Secrets come from `.env` /
  `prod.env` (git-ignored) — never hardcoded.
- **Caching:** in-memory `fastapi-cache` backend, default 1h TTL, with a custom
  `cache_with_logging` wrapper that logs HIT/MISS.
- **Logging:** loguru everywhere; structured request timing in middleware.
- **Auth:** `auth` route + `chat`/agents; most market-data reads are open to the
  configured CORS origins.

## Vendored TradingAgents

`vendor/TradingAgents` is a multi-agent LLM trading-research framework, kept
**unmodified** (a gitlink / submodule-style pointer with no `.gitmodules`). It
is not pip-installed; instead `backend/app/services/tradingagents/__init__.py`
prepends the vendored source to `sys.path`. Platform-specific wiring
(`vn_data`, `runner`, `sector_analyst`, `store`, `kb_search`, `web_search`)
lives in that integration package so the fork stays pristine. It runs against a
local **Ollama** server and reads VN-market data (ClickHouse OHLCV + wichart
reports). See [components.md](./components.md#vendored-tradingagents).

## Where to look next

- Component internals → [components.md](./components.md)
- Stores & providers → [data-and-integrations.md](./data-and-integrations.md)
- Running it → [deployment.md](./deployment.md)
