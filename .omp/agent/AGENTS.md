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
- **Auth:** every route except `/api/v1/health` and `/api/v1/auth/login` needs
  `Authorization: Bearer <token>`. `APP_AUTH_SECRET_KEY` must be set (the
  backend refuses to start without it in production). Create a login with
  `docker compose exec backend python -m app.scripts.create_user <username>`;
  revoke one with `--deactivate`. Backend tests run as a stubbed logged-in user
  via an autouse fixture in `tests/conftest.py`; a test that wants the real
  guard marks itself `@pytest.mark.real_auth`.

Production uses `docker-compose.prod.yml` + `prod.env` via `make prod-*`.
**Never** run `make prod-up` / `make prod-down` or otherwise touch production
without an explicit request.

## Fast research experiments

**Notebook/strategy experiments:** start from the local HDF cache. Do not call
`load_stocks()`, Delta Lake, MinIO, or another network source unless the user
explicitly asks to refresh data.

From the repository root:

```python
from pathlib import Path
import pandas as pd

DATA = Path("notebooks/stocks_data_latest.h5")
panel = pd.read_hdf(DATA, key="stocks").sort_index()

# panel: DatetimeIndex named "date"; MultiIndex columns: (field, symbol)
open_ = panel["open"]
high = panel["high"]
low = panel["low"]
close = panel["close"]
volume = panel["volume"]
```

List and select symbols without scanning notebook code:

```python
all_symbols = panel.columns.get_level_values("symbol").unique()
non_stocks = {"VNINDEX", "VIETNAM_1Y", "VIETNAM_5Y", "VIETNAM_10Y"}
symbols = all_symbols[~all_symbols.isin(non_stocks)]

# Optional: restrict to the repository watchlist.
watchlist = pd.read_csv("backend/models/watchlist.csv").iloc[:, 0].astype(str)
symbols = symbols.intersection(watchlist, sort=False)

# Vectorized date × symbol panels.
open_, high, low, close, volume = [
    frame.loc[:, symbols] for frame in (open_, high, low, close, volume)
]

# One symbol as an OHLCV DataFrame.
bars = panel.xs("FPT", axis=1, level="symbol")
bars = bars[["open", "high", "low", "close", "volume"]]
```

Keep experiments fast and causal:

- Slice dates before expensive indicators, for example `panel.loc["2019":]`.
- Prefer vectorized DataFrame operations; loop by symbol only for genuinely
  path-dependent state machines.
- The cache contains zero-volume / flat-price pre-listing padding. Mask those
  rows with `volume > 0`; do not use `bfill()`.
- Exclude the current bar from historical thresholds:
  `high.shift(1).rolling(60).max()` and
  `volume.shift(1).rolling(20).mean()`.
- A close-`t`, next-open experiment aligns outcomes as:
  `entry = open_.shift(-1)` and
  `ret20 = close.shift(-20).div(entry).sub(1)`.
- Extract a compact event table without Python loops:

  ```python
  events = (
      ret20.where(signal)
      .stack()
      .rename("ret20")
      .reset_index()  # date, symbol, ret20
  )
  ```

### VectorBT backtest template

Use VectorBT for fast panel backtests. A signal calculated after close `t` must
be shifted to row `t+1` and filled with that row's open:

```python
import numpy as np
import vectorbt as vbt

# Boolean date × symbol frames computed from completed bars.
entry_at_close = signal.fillna(False)
exit_at_close = exit_signal.fillna(False)

entries = entry_at_close.shift(1, fill_value=False) & open_.notna()
exits = exit_at_close.shift(1, fill_value=False) & open_.notna()

pf = vbt.Portfolio.from_signals(
    close=close,          # valuation series
    entries=entries,
    exits=exits,
    price=open_,          # execution price on the shifted order row
    init_cash=100_000_000,
    fees=0.002,           # per order; about 0.4% for entry + exit
    freq="1D",
    cash_sharing=False,   # independent capital per symbol
)

summary = pd.DataFrame({
    "total_return": pf.total_return(),
    "sharpe": pf.sharpe_ratio(),
    "max_drawdown": pf.max_drawdown(),
    "trades": pf.trades.count(),
}).replace([np.inf, -np.inf], np.nan)

trades = pf.trades.records_readable
```

- Shift signals, not prices. `price=close` on an unshifted close-derived signal
  assumes an unavailable signal-bar fill.
- `cash_sharing=False` measures symbols independently. For a deployable
  portfolio, explicitly define shared cash, position sizing, grouping, and
  simultaneous-signal priority; do not present mean per-symbol return as
  portfolio return.
- Print raw/shifted signal counts, trade count, and the first trade records
  before trusting aggregate metrics.

Report the data range, symbol count, signal count, date split, execution timing,
and costs. Treat the HDF universe as a current/survivor universe rather than a
point-in-time constituent history.

## The check that must pass (verify a change)

There is no single root verify command yet. Use the check that matches what you
touched, and prefer these over ad-hoc commands:

- **Backend (Python):** `cd backend && pytest tests` — offline, no external
  calls. Do **not** run bare `pytest` from the repo root: it can collect
  `testing/test_dnse_api.py`, which fires a live signed DNSE trading request at
  import time and has no assertions.
- **Frontend types:** `cd frontend && npm run build` (runs `tsc && vite build`).
- **Frontend lint:** `cd frontend && npm run lint`.
- **Frontend UI:** verification is manual. Give the user the exact surface and
  interactions to check, and report visual verification as pending until they
  confirm it. Do not run browser automation for frontend verification.
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
  charts via the TradingView charting library, `lightweight-charts`, or
  `recharts`.
- Keep changes narrow; do not refactor unrelated code.

## When you finish a change

State the exact verify command you ran and its result. "It should work" is not
acceptance — bind a passing check to the change you made.
