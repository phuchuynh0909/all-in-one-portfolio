---
name: portfolio-api
description: Call the local investment portfolio API safely
version: 1.0.0
metadata:
  hermes:
    tags: [api, portfolio, finance, fastapi, vietnam-market]
    category: finance
    requires_toolsets: [terminal]
---

# Portfolio API

## When to Use

Use this skill when the user asks Hermes to:

- Read or change portfolio positions, transactions, investment, dividends, or corporate actions.
- Retrieve quotes, price depth, timeseries, sectors, statements, reports, or alerts.
- Run scanners, backtests, regime analysis, market-flow analysis, crawlers, or workflows.
- Start chat, MVF, or trading-agent SSE streams.
- Inspect an `/api/v1` request or response schema.

## Prerequisites

Run commands from the repository root. The helper uses `requests`, which is declared in `backend/requirements.txt`.

Set both credentials in the environment:

```bash
export PORTFOLIO_API_USERNAME='<username>'
export PORTFOLIO_API_PASSWORD='<password>'
```

Never place credentials in `SKILL.md`, shell arguments, tracked files, or chat output.

Optional configuration:

```bash
export PORTFOLIO_API_BASE_URL='http://localhost:8000/api/v1'
export PORTFOLIO_API_TIMEOUT='60'
```

## Procedure

1. Find the operation. The helper prefers live OpenAPI and falls back to the bundled snapshot:

   ```bash
   python3 .hermes/skills/portfolio-api/scripts/portfolio_api.py list --query depth
   ```

2. Inspect its parameters, request model, and response model:

   ```bash
   python3 .hermes/skills/portfolio-api/scripts/portfolio_api.py show GET /trade-flow/depth
   ```

3. Before a mutation or background job, load the side-effect reference with `skill_view("portfolio-api", "references/api-reference.md")`. Require explicit user intent for the exact operation and target.

4. Preview uncertain requests without authentication or network mutation:

   ```bash
   python3 .hermes/skills/portfolio-api/scripts/portfolio_api.py call DELETE /price-alerts/42 \
     --confirm-write --dry-run
   ```

5. Execute the smallest operation that satisfies the request. Every non-dry-run `call` performs the fixed login flow first, uses the returned token for one API call, then discards it.

   Read with query parameters:

   ```bash
   python3 .hermes/skills/portfolio-api/scripts/portfolio_api.py call GET /trade-flow/depth \
     --query symbol=BCM --query days=1
   ```

   Send JSON from a file:

   ```bash
   python3 .hermes/skills/portfolio-api/scripts/portfolio_api.py call POST /scanner/scan \
     --data-file /tmp/scanner-request.json --confirm-write
   ```

   Upload multipart data:

   ```bash
   python3 .hermes/skills/portfolio-api/scripts/portfolio_api.py call POST /sector/import/1 \
     --file file=/tmp/sectors.json --confirm-write
   ```

   Consume Server-Sent Events:

   ```bash
   python3 .hermes/skills/portfolio-api/scripts/portfolio_api.py call POST /trading-agents/analyze/stream \
     --data-file /tmp/analysis-request.json --confirm-write --stream
   ```

6. Verify the returned business value. After a durable mutation, read the affected resource back when an endpoint exists.

## Contract Files

- `references/api-reference.md` groups routes, classifies side effects, and documents authentication and errors.
- `references/openapi.json` is the complete offline snapshot used by the helper. Inspect individual operations with `show`; do not load the entire snapshot into context.
- `scripts/portfolio_api.py` is the only supported invocation path.

Refresh the snapshot after backend route or schema changes:

```bash
python3 .hermes/skills/portfolio-api/scripts/portfolio_api.py refresh
```

## Pitfalls

- `call` always logs in first, including for the backend's public `/health` route. A pre-issued token is intentionally unsupported.
- `POST`, `PUT`, `PATCH`, and `DELETE` require `--confirm-write`. Some analytical `POST` endpoints are computational reads, but still require the flag.
- Keep tokens, passwords, callback codes, and Authorization headers out of output.
- Do not target a non-local or production base URL without explicit user authorization.
- Use `--stream` for `/chat/stream`, `/portfolio/mvf/stream`, and `/trading-agents/analyze/stream`.
- Preserve response units. Price-depth sizes are shares; the backend converts DNSE board lots by `×10`.

## Verification

Confirm discovery and the bundled contract:

```bash
python3 .hermes/skills/portfolio-api/scripts/portfolio_api.py list
python3 .hermes/skills/portfolio-api/scripts/portfolio_api.py show POST /auth/login
```

The current snapshot must report 98 operations, and the login contract must resolve `LoginRequest` to `TokenResponse`. For an API call, success requires both a successful login and a valid domain response from the requested endpoint.