# Documentation

High-level architecture docs for the all-in-one Vietnamese-market investment
platform.

> The root `README.md` describes an unrelated PostgreSQL schema tool and is
> **out of date**. Trust `AGENTS.md` and this `docs/` directory for the real
> stack.

## Index

| Doc | What it covers |
| --- | --- |
| [architecture.md](./architecture.md) | System overview, component responsibilities, request/data flows, the C4-style diagrams |
| [components.md](./components.md) | Per-deployable breakdown: backend, frontend, workers, vendored TradingAgents |
| [data-and-integrations.md](./data-and-integrations.md) | Data stores, external market-data providers, and how they connect |
| [deployment.md](./deployment.md) | Docker Compose topology, environments, and run commands |
| [experiments.md](./experiments.md) | Experiment store: logging vectorbt runs to Parquet, querying with DuckDB, serving the `/experiments` page |

## The 30-second version

Three deployables cooperate around a set of shared data stores:

- **Backend API** (`backend/`, FastAPI) — serves the REST API under `/api/v1`,
  wraps external market-data providers, runs indicators/backtests/ML, and
  fronts the vendored multi-agent trading logic.
- **Frontend** (`frontend/`, React + Vite + MUI) — single-page app that talks
  only to the backend API.
- **Stream workers** (`worker/`, Bytewax + Prefect) — long-running streaming
  jobs that ingest live ticks and derive signals into ClickHouse.

Shared stores: **ClickHouse** (time-series / ticks), **SQLite** (app/portfolio
state), and a **Delta Lake on MinIO/S3** (historical OHLCV, sector data,
research reports). Live data arrives over **MQTT** (DNSE market data) and signed
**DNSE OpenAPI** calls.
