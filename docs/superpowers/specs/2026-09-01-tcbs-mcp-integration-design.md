# TCBS MCP as a data tier for TradingAgents

**Status:** approved, not yet implemented
**Date:** 2026-09-01

## Problem

The Vietnamese-market data behind the TradingAgents analysts is assembled from
scraped and third-party sources, and one tool has no source at all.
`backend/app/services/tradingagents/vn_data.py` registers a `portfolio` vendor
covering every analyst tool, but its fundamentals tier is 24hmoney's company
index, its statements come from ruatichsan, its company news is wichart plus a
web-search fallback, and `get_insider_transactions` is a hard-coded
`INSIDER_DATA_UNAVAILABLE` sentinel (`vn_data.py:1337`) that tells the model to
stop asking.

TCBS — the broker behind TCInvest — now publishes a remote MCP server exposing
49 read-only tools over the same data its own platform runs on: company
overviews, ratio sets split for banks and non-banks, full financial statements
with industry averages, insider dealing, foreign flow, corporate events and
multi-dimension ratings, for HOSE, HNX and UPCOM. It is a first-party source
where we currently have third-party ones, and it covers a gap we currently
cannot fill.

## Goal

Put TCBS in front of the existing sources for fundamentals, statements, news
and insider data, without changing what the analysts see structurally and
without a single line of diff in the `vendor/TradingAgents` submodule.

## Non-goals

- **Prices and technical indicators.** ClickHouse `ohlc_eod` stays the
  authoritative source for `get_stock_data` and `get_indicators`. TCBS has no
  OHLCV history tool in any case — only `getPriceVolatility`,
  `getTechnicalIndicator` and a *link* to a candle chart.
- **Macro and prediction markets.** Unchanged (wichart xbrain-news, and the
  Polymarket sentinel).
- **Personal and account data.** The connector can expose the authenticated
  user's portfolio, holdings and transaction history. None of it enters an
  agent's context. The adapter calls ticker-scoped tools only.
- **Trading.** The connector is read-only by construction; nothing here changes
  that.
- **A new vendor in the submodule.** Rejected below.
- **Replacing the knowledge base.** Curated wichart research stays the top news
  tier; TCBS slots beneath it.

## Decisions

| Decision | Choice | Why |
| --- | --- | --- |
| Integration point | A new tier inside the existing `portfolio` vendor in `vn_data.py` | The vendor and its dispatch registration already exist (`runner.py:195`); this keeps the submodule diff at zero, which keeps rebasing the fork on `upstream/main` cheap |
| Login surface | Host-side CLI in this repo, `backend/scripts/tcbs_login.py` | One login surface, beside `manage_users.py`, rather than a second one buried in the submodule |
| Token storage | MySQL table, written by the host CLI, read by the backend | The CLI runs on the host, the consumer runs in the container. `manage_users.py` already solves exactly this by talking to MySQL directly, "whether or not the stack is running" |
| OAuth client | Dynamic client registration, per-install | `https://mcp.tcbs.com.vn/tcinvest/register` is advertised, so no client_id has to be negotiated with TCBS out of band |
| Refresh | Refresh token, silently on 401 | The AS advertises `refresh_token`; the browser + iOTP step stays one-time until TCBS expires the grant |
| Bank vs non-bank | Local lookup in `backend/app/sector_map.json` | The map already tags `"Ngân hàng"`; resolving the split costs no TCBS call. Falls back to the money24h ICB chain, then to non-bank |
| Failure mode | Every TCBS call inside `_best_effort` | A missing token, an expired grant or a TCBS outage drops to today's source and the run continues. No analysis fails because a new tier is down |
| Enablement | Presence of a valid token row, plus a `TCBS_ENABLED=0` kill switch | Nothing changes for a checkout that never logs in |
| Output shapes | Unchanged | The analyst prompts were written against the current shapes; new material is appended as blocks, not substituted into existing ones |

### Rejected alternatives

**A new `tcbs` vendor inside `vendor/TradingAgents`.** The obvious reading of
"integrate TCBS", and wrong here: `vn_data.py` already owns every method a TCBS
vendor would claim, so the two would compete in `VENDOR_METHODS` for the same
keys, and the fork would carry a permanent diff for no gain.

**A generic MCP tool bridge** — load all 49 tools as LangChain tools and hand
them to the analysts. More raw capability, but it bypasses the vendor
abstraction the whole data layer is built on, puts 49 extra tool descriptions
into every analyst prompt, and the upstream prompts are written for US-market
tooling. Revisit only for TCBS tools that have no vendor slot at all.

**A file-based token store** (`~/.tcbs/token.json`). Simpler, until the backend
container needs to read it and the mount has to be threaded through
`docker-compose.yml` and `docker-compose.prod.yml`.

**An env-var access token.** Matches the `ALPHA_VANTAGE_API_KEY` pattern, but
TCBS documents no way to mint a token by hand, and it expires.

## The connector

Endpoint `https://mcp.tcbs.com.vn/mcp/tcinvest/`, streamable HTTP. An
unauthenticated `initialize` returns `401` with

```
www-authenticate: Bearer resource_metadata="https://mcp.tcbs.com.vn/.well-known/oauth-protected-resource/mcp/tcinvest"
```

so it is a spec-compliant remote MCP server (RFC 9728 discovery). Following
that metadata to the resource's authorization server
(`https://mcp.tcbs.com.vn/tcinvest`) yields:

```json
{
  "issuer": "https://mcp.tcbs.com.vn",
  "authorization_endpoint": "https://mcp.tcbs.com.vn/tcinvest/authorize",
  "token_endpoint": "https://mcp.tcbs.com.vn/tcinvest/token",
  "registration_endpoint": "https://mcp.tcbs.com.vn/tcinvest/register",
  "grant_types_supported": ["authorization_code", "refresh_token"],
  "code_challenge_methods_supported": ["S256"],
  "token_endpoint_auth_methods_supported": ["client_secret_post"]
}
```

Note this is **not** the metadata at the bare `/.well-known/oauth-authorization-server`
path, which points at a different pair of endpoints under `/v1/mcp/oauth/` and
advertises no registration endpoint and no refresh grant. The per-resource
document is the one to follow; a client that guesses the root path gets a
non-refreshable session and no way to register.

## New files

### `backend/app/services/tcbs_mcp_client.py`

The MCP client, following the conventions of the neighbouring external clients
(`money24h_client.py`, `dnse_client.py`): module-level env configuration, a
process-local TTL cache, an explicit timeout, and a typed error for "no data
for this symbol" distinct from "this call broke".

- `call(tool_name, **params) -> dict` — the single entry point the adapter uses.
- Session handling: one long-lived MCP session per process, re-established on
  drop. Tokens are read from the store, refreshed on `401`, and the refreshed
  pair written back.
- **Async→sync bridge.** The MCP SDK is async; `route_to_vendor` and every tool
  in `vn_data.py` are sync. A single dedicated event loop runs in a daemon
  thread for the process lifetime, and `call` submits coroutines to it with
  `asyncio.run_coroutine_threadsafe(...).result(timeout=...)`. Not
  `asyncio.run` per call: that would tear down the MCP session, and its OAuth
  handshake, on every tool invocation.
- Cache TTL defaults to 900s, matching `money24h_client`: an analyst turn calls
  several tools for one symbol, and fundamentals do not move within a run.

Configuration, all optional:

```
TCBS_MCP_URL      default https://mcp.tcbs.com.vn/mcp/tcinvest/
TCBS_TIMEOUT      per-call timeout, seconds (default 30)
TCBS_CACHE_TTL    per-symbol cache TTL, seconds (default 900)
TCBS_ENABLED      set to 0 to disable the tier outright
```

### `backend/scripts/tcbs_login.py`

Host-side CLI, conventions taken from `manage_users.py`: loads the repository
root `.env`, talks to MySQL directly so it works whether or not the stack is
running, and never takes a secret in `argv`.

```
python backend/scripts/tcbs_login.py login
python backend/scripts/tcbs_login.py status
python backend/scripts/tcbs_login.py logout
```

`login` performs: dynamic client registration → PKCE `S256` authorize →
`webbrowser.open` on the authorization URL, with the URL also printed for
headless hosts → a loopback HTTP server on an ephemeral port catches the
redirect → code exchange → tokens written to MySQL. The browser step includes a
TCBS account login and iOTP confirmation, which is why it cannot be automated
and why refresh matters.

`status` prints the connected account, token expiry and last refresh. `logout`
deletes the row and prints the reminder that revoking access properly also
means TCInvest → AI Connector → HỦY CHIA SẺ, since deleting our copy of a token
does not revoke the grant on TCBS's side.

### Alembic migration — `tcbs_oauth_tokens`

Single-row-per-install table: `client_id`, `client_secret`, `access_token`,
`refresh_token`, `expires_at`, `created_at`, `updated_at`. Secrets are stored
as-is; the database is not reachable from outside the compose network and the
row is no more sensitive than the broker credentials already in `.env`.

## Changed files

### `backend/app/services/tradingagents/vn_data.py`

Each tool below keeps its signature, its `@failsafe` decorator and its current
output shape. TCBS becomes the top tier; today's source stays as the fallback
tier beneath it. New material is appended as clearly-labelled blocks.

| Tool | TCBS tools used | Fallback tier |
| --- | --- | --- |
| `get_fundamentals` | `getTickerOverview`, `getStockRatio`, `getStockSameIndustry`, `getGeneralRating`, `getFinancialRatioIndustryFor{Bank,NonBank}` | 24hmoney company index |
| `get_balance_sheet` | `getBalanceSheetFor{Bank,NonBank}`, `getBalanceSheetIndustryFor{Bank,NonBank}` | ruatichsan `cdkt` |
| `get_income_statement` | `getIncomeStatementFor{Bank,NonBank}`, `getIncomeStatementIndustryFor{Bank,NonBank}` | ruatichsan `kqkd` |
| `get_cashflow` | `getCashFlowFor{Bank,NonBank}`, `getCashFlowAnalyze` | ruatichsan `lctt` |
| `get_news` | `getTickerActivityNews` + `getActivityNewsDetail`, `getTickerEventNews` + `getEventNewsDetail` | wichart feed, then web search |
| `get_insider_transactions` | `getInsiderDealing`, `getVolumeAndForeign` | none — degrades to today's sentinel |

Three of these are genuinely new capability rather than a swap:

- **Insider dealing.** `get_insider_transactions` returns real filings instead
  of `INSIDER_DATA_UNAVAILABLE`.
- **Industry averages.** The `*Industry*` statement and ratio tools give a
  "versus sector" column that ruatichsan and 24hmoney cannot produce; today the
  snapshot only names a peer group.
- **Corporate events.** `getTickerEventNews` carries ex-rights dates, AGM
  schedules and board resolutions, which no current news tier has.

In `get_news`, TCBS is inserted **below** the knowledge-base tier and **above**
the wichart feed and web search: curated research remains the better signal,
but a live ticker-tagged first-party feed beats both a scraped one and the open
web.

### Helper: bank/non-bank resolution

A small cached resolver, `_is_bank(symbol) -> bool`: `sector_map.json` says
`"Ngân hàng"` → bank; otherwise the money24h ICB chain if already loaded;
otherwise non-bank. Cached per symbol per process. Every statement and ratio
call routes through it to pick the `ForBank` / `ForNonBank` variant.

## Implementation order

1. **Discover the real tool schemas.** TCBS documents 49 tool names and
   descriptions but **no parameter schemas**, and the endpoint answers nothing
   without a completed login. So: build the client and CLI far enough to log
   in, dump `tools/list`, and commit the result as
   `docs/tcbs-mcp-tools.json`. Everything downstream is written against that
   dump, not against the help page. The mapping table above is provisional on
   argument shape, not on existence.
2. Token store + migration, then `tcbs_login.py` end to end.
3. `tcbs_mcp_client.py` against the committed schema dump.
4. `get_insider_transactions` first — it has no fallback tier to preserve, so
   it is the cleanest proof the whole path works.
5. Fundamentals, then the three statements, then news.

If a tool needs an argument we cannot supply, that tier degrades and the
fallback serves; the blast radius of a wrong guess is one block of one report.

## Testing

Mirrors `backend/tests/test_tradingagents_*.py`.

- `test_tcbs_mcp_client.py` — the async→sync bridge, cache TTL, 401-triggered
  refresh, and that a refresh failure raises the "no data" error rather than
  propagating.
- `test_tcbs_tiering.py` — with a fake MCP session, each tool returns TCBS
  content; with the session raising, each tool returns exactly what it returns
  today. This is the regression guard: **the fallback path must stay
  byte-identical to current behaviour.**
- `test_tcbs_login.py` — DCR request shape, PKCE verifier/challenge derivation,
  the loopback callback parse, and that `logout` clears the row.
- One integration test, marked and skipped without a token, that calls
  `getTickerOverview` for a known symbol.

No unit test touches the network.

### Verify commands

```
cd backend && python -m pytest tests/test_tcbs_mcp_client.py tests/test_tcbs_tiering.py tests/test_tcbs_login.py -v
cd backend && python -m pytest tests/ -q          # full suite stays green
ruff check backend/app/services/tcbs_mcp_client.py backend/scripts/tcbs_login.py
```

## Rollout

The tier is inert until someone runs `login`, so merging is safe before any
credential exists. After login, one analysis on a bank (e.g. `TCB`) and one on
a non-bank (e.g. `HPG`) exercises both statement variants; compare the two
reports against a pre-merge run of the same symbols to confirm the shapes the
prompts expect are intact.

## Known loose ends, not addressed

- **Grant expiry is silent.** TCInvest shows an expiry date per connected
  agent. When it lapses, the tier degrades and logs; nothing alerts. A
  `status` check in a scheduled job would close this, and is deferred.
- **`getDividendPaymentHistories`** overlaps
  `backend/app/services/corporate_action_service.py`. Out of scope here; worth
  revisiting when that service next needs a source.
- **Rate limits are undocumented.** The 900s cache is a guess at politeness,
  not a measured fit. Revisit if TCBS starts throttling.
- **One TCBS account, one token row.** Multi-user token scoping is not
  modelled, consistent with the app's single-portfolio design.
