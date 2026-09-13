# Portfolio API reference

## Route groups

| Domain | Paths | Typical use |
|---|---|---|
| Access | `/health`, `/auth/*` | Health, login, current user |
| Portfolio | `/portfolio/*` | Positions, transactions, investment, optimization, MVF, corporate actions, dividends |
| Market data | `/quote/*`, `/timeseries/*`, `/sector/*`, `/financial/*` | Quotes, bars, indicators, breadth, sector and financial data |
| Analytics | `/scanner/*`, `/backtest/*`, `/future/*`, `/cw/*`, `/regime/*` | Screening, simulations, futures, covered warrants, regimes |
| Execution flow | `/isp/*`, `/large-orders`, `/trade-flow/*` | Alerts, large executions, price depth, anomaly windows |
| Research | `/report/*`, `/chat/*` | Reports, RAG jobs, notes, streamed chat |
| Agents and broker | `/trading-agents/*`, `/broker/*` | Analysis jobs, model catalog, TCBS authorization, token refresh |
| Operations | `/crawler/*`, `/workflows/*`, `/price-alerts/*` | Data synchronization, feature jobs, alert lifecycle |

## Side effects

These operations mutate durable application state or start work:

- All portfolio position, transaction, investment, corporate-action, dividend, and MVF `POST`/`PUT`/`DELETE` operations.
- All `/price-alerts` mutations.
- `/crawler/crawl-symbol/{symbol}` and `/workflows/sync-stock/{symbol}`.
- Report creation, report summary updates, report sync, and report RAG triggers.
- Chat-note creation and streamed chat requests.
- Trading-agent analysis, TCBS authorization/completion/callback, and broker token refresh.

Several analytics use `POST` only because their inputs are structured JSON: timeseries calculations, scanner runs, backtests, and regime analysis. They are computational reads, but the helper still requires `--confirm-write`; inspect the operation and use the flag only when the user requested the computation.

## Streaming operations

Use `call ... --stream` for:

- `POST /chat/stream`
- `POST /trading-agents/analyze/stream`
- `POST /portfolio/mvf/stream`

Each returns Server-Sent Events. Keep reading until the server closes the response or emits its terminal event.

## Authentication

The helper uses one fixed flow before every non-dry-run `call`:

1. `POST /api/v1/auth/login` with `PORTFOLIO_API_USERNAME` and `PORTFOLIO_API_PASSWORD`.
2. Read `access_token` from the response.
3. Send the requested API operation with `Authorization: Bearer <access_token>`.
4. Discard the token when that call completes.

This happens even for the backend's runtime-public `GET /api/v1/health` route. The flow is hard-coded; credentials are not, because repository secrets must remain environment-backed.

| Variable | Default | Purpose |
|---|---|---|
| `PORTFOLIO_API_BASE_URL` | `http://localhost:8000/api/v1` | API prefix used by `call` |
| `PORTFOLIO_OPENAPI_URL` | Base origin plus `/openapi.json` | Optional discovery override |
| `PORTFOLIO_API_TIMEOUT` | `60` | Request timeout in seconds |
| `PORTFOLIO_API_USERNAME` | none | Required username for the mandatory login |
| `PORTFOLIO_API_PASSWORD` | none | Required password for the mandatory login |

Configure both credentials:

```bash
export PORTFOLIO_API_USERNAME='<username>'
export PORTFOLIO_API_PASSWORD='<password>'
```

The helper never accepts a pre-issued token, prints a credential, or persists the returned token. `requests` is supplied by `backend/requirements.txt`.

## Errors

- `401`: token missing, expired, or user inactive. Re-authenticate; do not retry unchanged credentials repeatedly.
- `404`: verify both the concrete path and resource identifier.
- `422`: inspect the operation with `show`; FastAPI returns field-level validation details.
- `5xx`: report the response body after redaction and inspect backend logs.

## Contract maintenance

`openapi.json` is generated from `app.openapi()` via the running backend. Refresh it whenever a route, parameter, request model, or response model changes:

```bash
python3 .hermes/skills/portfolio-api/scripts/portfolio_api.py refresh
python3 .hermes/skills/portfolio-api/scripts/portfolio_api.py list
```

Coverage is complete when the snapshot operation count matches the live OpenAPI operation count.