# AGENTS.md

Scoped instructions for coding agents working in this repository. Read this
before touching code. The root `README.md` describes an unrelated PostgreSQL
schema tool and is **out of date** — trust this file for the real stack.

## What this project actually is

An all-in-one Vietnamese-market investment platform with three deployables:

| Component | Path | Stack | Entry point |
| --- | --- | --- | --- |
| Backend API | `backend/` | FastAPI + SQLAlchemy + Pydantic v2, loguru | `backend/app/main.py` (`get_app()`), served as `app.main:app` |
| Frontend | `frontend/` | React 18 + TypeScript + Vite + MUI, TanStack Query, lightweight-charts | `frontend/src/` |
| Stream workers | `worker/` | Bytewax streaming + Prefect + ClickHouse | `worker/workers/*.py` (run via `python -m bytewax.run workers.<name>:flow`) |

There is **no Django** anywhere despite what stale profilers report.

### Backend layout (`backend/app/`)
- `main.py` — app factory, CORS, request-timing middleware, router wiring
- `api/v1/routes/` — HTTP routes (health, portfolio, quote, scanner, backtest,
  trading_agents, etc.)
- `services/` — business logic; notable: `dnse_client.py` (live DNSE quote/trade
  API), `services/tradingagents/` (integration with the vendored package)
- `core/`, `db/`, `schemas/`, `stores/`, `utils/`

### Vendored trading logic — `vendor/TradingAgents`
- This is a **git submodule / gitlink** (mode 160000) with **no `.gitmodules`**.
  A fresh clone will not populate it.
- It is loaded via a `sys.path` shim in
  `backend/app/services/tradingagents/__init__.py`, not installed as a package.
- Edits inside `vendor/` do **not** show up as a normal diff — only as a moved
  submodule pointer. Do not silently modify vendored code; surface any change
  explicitly and confirm with the user.

## Running the stack

Everything runs through Docker Compose (see `Makefile`):

```bash
make up            # dev: backend :8000, frontend :5173, workers
make logs          # tail all
make backend-logs  # backend loguru output (the real debug route)
make down
```

- Backend API: http://localhost:8000 (prefix `/api/v1`, docs at `/docs`)
- Frontend dev server: http://localhost:5173 (talks to `VITE_API_BASE_URL=http://localhost:8000/api/v1`)

Production uses `docker-compose.prod.yml` + `prod.env` via `make prod-*`.
**Never** run `make prod-up` / `make prod-down` or otherwise touch production
without an explicit request.

## The check that must pass (verify a change)

There is no single root verify command yet. Use the check that matches what you
touched, and prefer these over ad-hoc commands:

- **Backend (Python):** `cd backend && pytest tests` — offline, no external
  calls. Do **not** run bare `pytest` from the repo root: it can collect
  `testing/test_dnse_api.py`, which fires a live signed DNSE trading request at
  import time and has no assertions.
- **Frontend types:** `cd frontend && npm run build` (runs `tsc && vite build`).
- **Frontend lint:** `cd frontend && npm run lint`.
- **Workers:** `cd worker && pytest tests`.

If you change pure price/indicator/session logic (e.g.
`backend/app/services/dnse_client.py::_pick_trade`/`_to_quote`,
`frontend/src/lib/services/quote.ts::isVnMarketSession`,
`frontend/src/lib/tv/studies.ts`), add or extend a test that fails when the
numbers are wrong before declaring the change done.

## Safety rules (do not violate)

1. **Secrets:** `.env`, `prod.env`, and `.env.example` hold DNSE trading-API
   credentials and are git-ignored. Never print, commit, or paste their
   contents. Do not hardcode API keys in tracked files — read from the
   environment. `testing/test_dnse_api.py` currently embeds live-looking
   credentials; do not copy that pattern, and flag it if you touch it.
2. **No live trading calls in tests or verification.** Anything that hits the
   DNSE / money24h / wichart clients must be mocked in tests.
3. **Do not commit agent scratch output.** `.playwright-mcp/` (browser
   automation dumps) is not ignored — do not stage its files. Keep diffs to real
   source changes.
4. **Production is off-limits** unless explicitly asked (see above).

## Conventions

- Python: loguru for logging (import `from loguru import logger`); Pydantic v2
  models; SQLAlchemy 2.0 style. Log enough to distinguish failure causes — when
  a quote path exits early (missing creds vs. empty upstream vs. filtered
  board), emit a log line saying which.
- TypeScript: strict typing; MUI for UI; TanStack Query for data fetching;
  charts via `lightweight-charts` / `recharts`.
- Keep changes narrow; do not refactor unrelated code.

## When you finish a change

State the exact verify command you ran and its result. "It should work" is not
acceptance — bind a passing check to the change you made.
