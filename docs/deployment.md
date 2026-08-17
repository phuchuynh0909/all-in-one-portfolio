# Deployment & Runbook

How the platform is built, run, and verified. Everything runs through Docker
Compose via the `Makefile`.

## Compose topology (dev — `docker-compose.yml`)

| Service | Image / build | Ports | Notes |
| --- | --- | --- | --- |
| `backend` | `./backend` | `8000:8000` | `uvicorn app.main:app --reload`; mounts `./backend` and `./vendor` (for the TradingAgents shim). Joins `appnet` + external `shared_network`. |
| `frontend` | `./frontend` | `5173:5173` | `npm run dev --host`; `VITE_API_BASE_URL=http://localhost:8000/api/v1`; depends on backend. |
| `worker-tick-ingest` | `./worker` | — | `bytewax.run workers.tick_ingest:flow`. |
| `worker-price-alerts` | `./worker` | — | `bytewax.run workers.price_alerts:flow`; mounts `backend/portfolio.db`. |
| `worker-hawkes` | `./worker` | — | `python -m workers.hawkes_signal_worker`; state files under `state_dir`. |

- **Networks:** `appnet` (internal bridge) + `shared_network` (external,
  `my-common-network`) that connects to shared infra (ClickHouse, MinIO).
- **Volumes:** `frontend_node_modules`, `worker_state` (durable Bytewax operator
  state), and bind-mounts for live-reload.
- Additional workers (`ohlc_5m`, `isp`, `large_order_*`, `reconciler`) run via
  `python -m bytewax.run workers.<name>:flow` — add them to compose or run
  ad hoc as needed.

### Endpoints

- Backend API: <http://localhost:8000> (prefix `/api/v1`, docs at `/docs`)
- Frontend dev server: <http://localhost:5173>

## Common commands

```bash
make up            # dev: backend :8000, frontend :5173, workers
make down          # stop all
make restart       # down + up
make logs          # tail all containers
make backend-logs  # backend loguru output (the real debug route)
make build         # rebuild dev images
make clean         # remove containers + volumes
```

## Environments

- **Dev:** `docker-compose.yml` + `.env`. `APP_ENVIRONMENT=development`,
  SQLite app DB, ClickHouse/MinIO reached over `shared_network`.
- **Prod:** `docker-compose.prod.yml` + `prod.env`, via `make prod-*`. See
  `PRODUCTION.md` and `DOMAIN_SETUP.md`. **Off-limits** unless explicitly
  requested — never run `make prod-up` / `make prod-down` or otherwise touch
  production without an explicit ask.

## Verify a change (the check that must pass)

There is no single root verify command. Use the check matching what you touched:

| You changed | Run | Notes |
| --- | --- | --- |
| Backend (Python) | `cd backend && pytest tests` | Offline, no external calls. **Do not** run bare `pytest` from repo root — it can collect `testing/test_dnse_api.py`, which fires a live signed DNSE request at import time with no assertions. |
| Frontend types | `cd frontend && npm run build` | Runs `tsc && vite build`. |
| Frontend lint | `cd frontend && npm run lint` | |
| Workers | `cd worker && pytest tests` | |

If you change pure price/indicator/session logic (e.g.
`dnse_client.py::_pick_trade`/`_to_quote`, `quote.ts::isVnMarketSession`,
`tv/studies.ts`), add or extend a test that fails when the numbers are wrong
before declaring the change done.

When you finish, state the exact verify command you ran and its result.

## Operational notes

- **Config** is entirely env-driven (`core/settings.py`, env prefix `APP_`, plus
  `CLICKHOUSE_*`, `DNSE_*`, `MINIO_*`). Secrets stay in git-ignored env files.
- **Caching:** backend uses an in-memory `fastapi-cache` (1h default TTL);
  restarting the backend clears it.
- **Worker state** persists in the `worker_state` volume (and
  `HAWKES_STATE_PATH` / `OHLC_5M_SYNC_STATE_PATH` files) — deleting it forces a
  cold rebuild/backfill.
- **Vendored `vendor/TradingAgents`** is a gitlink with no `.gitmodules`; a
  fresh clone won't populate it. The backend loads it via a `sys.path` shim and
  the compose `./vendor` mount — no pip install.
- **Do not commit** `.playwright-mcp/` scratch output; keep diffs to real source.
