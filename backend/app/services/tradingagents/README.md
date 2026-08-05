# TradingAgents integration (Vietnamese market + Ollama)

Wires [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)
into this platform. The framework itself is vendored, **unmodified**, at
`vendor/TradingAgents/` (repo root). All platform-specific glue lives in this
package so the fork can be re-pulled from upstream cleanly.

## How it fits together

```
frontend/src/pages/TradingAgents.tsx      UI: symbol -> live agent reports + decision
   │  SSE  (frontend/src/lib/services/tradingAgents.ts)
   ▼
POST /api/v1/trading-agents/analyze/stream (backend/app/api/v1/routes/trading_agents.py)
   ▼
runner.run_analysis_stream()              config + streaming
   ├─ build_config()          -> points the graph at Ollama + the "portfolio" vendor
   ├─ register_vn_vendor()     -> injects VN data into route_to_vendor dispatch
   └─ TradingAgentsGraph.stream(...)   market+news analysts -> debate -> trader -> risk -> PM
        └─ vn_data.py           OHLCV/indicators (ClickHouse ohlc_eod) + reports (wichart)
```

Every analyst data tool in TradingAgents routes through
`tradingagents.dataflows.interface.route_to_vendor(method, *args)`. We register a
`portfolio` vendor for each method (`vn_data.VN_VENDOR_METHODS`) and set every
`data_vendors` category to `portfolio`, so **no US data source (yfinance,
Finnhub, FRED, Reddit) is ever contacted.** The one tool that bypasses the
router — `get_verified_market_snapshot` — is handled by repointing
`market_data_validator.load_ohlcv` at `vn_data.load_ohlcv`.

Only the `market` and `news` analysts run by default (the two with real VN data).
Fundamentals/insider/macro/prediction categories return explicit
`*_UNAVAILABLE` sentinels so agents never fabricate values.

## Prerequisites

### 1. Python deps (added to `backend/requirements.txt`)

```
langgraph, langgraph-checkpoint-sqlite, langchain-core, langchain-openai,
stockstats
```

> `yfinance` is intentionally **not** a dependency — this deployment uses
> Vietnamese-market data only. The vendored framework imports `yfinance` at
> module load, so `app/services/tradingagents/__init__.py` registers a
> lightweight stub when it is absent; Yahoo is never actually called (all data
> routes to the VN `portfolio` vendor).

Install into the backend environment (or rebuild the backend image). Run from
**inside `backend/`** — `requirements.txt` has an editable line
(`-e ./libs/backtesting.py`) whose relative path only resolves from there:

```bash
cd backend && pip install -r requirements.txt
```

The vendored package is put on `sys.path` automatically by
`app/services/tradingagents/__init__.py` — no `pip install -e vendor/...`
needed.

**Docker:** `vendor/` lives at the repo root, outside the backend build
context, so it is bind-mounted into the container at `/app/vendor` (added to
the `backend` service in both `docker-compose.yml` and
`docker-compose.prod.yml`). The shim finds it there automatically. After adding
the mount, recreate the container:

```bash
docker-compose up -d --force-recreate backend
```

For a non-standard layout, point the shim explicitly with
`TRADINGAGENTS_VENDOR_PATH=/path/to/vendor/TradingAgents`.

### 2. Ollama

```bash
# install: https://ollama.com
ollama serve                       # starts the server on :11434
ollama pull llama3-groq-tool-use   # default for both deep- and quick-think
```

The agents rely heavily on tool calling, so use a tool-calling-capable model.
`llama3-groq-tool-use` is the default (both deep- and quick-think). You can point
the deep-think model at something larger via `TRADINGAGENTS_DEEP_THINK_LLM`.

## Switching the LLM provider (e.g. DeepSeek)

The runner is provider-aware. Ollama is the default, but any provider the
vendored framework supports works by setting a couple of env vars — model
defaults, endpoint, and auth are picked automatically.

**DeepSeek** (hosted, no local server needed):

```bash
TRADINGAGENTS_LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-...            # get one at platform.deepseek.com
# Optional — these are the defaults for deepseek:
# TRADINGAGENTS_DEEP_THINK_LLM=deepseek-reasoner   # manager/debate
# TRADINGAGENTS_QUICK_THINK_LLM=deepseek-chat      # analysts
```

Add those to the backend service in `docker-compose.yml` (or its `.env`), then
`docker-compose up -d --force-recreate backend`. No Ollama / model pulls needed;
`host.docker.internal` is ignored for hosted providers (they use their own
endpoint). Check it: `curl .../trading-agents/health` →
`"provider": "deepseek", "backend_ready": true`.

**Gemini** (Google, hosted):

```bash
TRADINGAGENTS_LLM_PROVIDER=gemini      # alias for `google`
GEMINI_API_KEY=AQ...                   # https://aistudio.google.com/apikey
#   (GEMINI_API_KEY is auto-bridged to the GOOGLE_API_KEY the SDK reads)
# Optional — gemini defaults:
# TRADINGAGENTS_DEEP_THINK_LLM=gemini-3.1-pro-preview
# TRADINGAGENTS_QUICK_THINK_LLM=gemini-3.5-flash
```

Gemini needs the `langchain-google-genai` package (already in
`requirements.txt`); rebuild the backend image / reinstall if you added it after
your last build.

The same pattern works for `openai` (`OPENAI_API_KEY`), `anthropic`
(`ANTHROPIC_API_KEY`), etc. — only the provider name and its key env var change.
To point at a custom OpenAI-compatible gateway, also set
`TRADINGAGENTS_LLM_BACKEND_URL`.

## Configuration (env vars, all optional)

| Var | Default | Meaning |
|---|---|---|
| `TRADINGAGENTS_LLM_PROVIDER` | `ollama` | provider used for model names given without a `provider:` prefix |
| `TRADINGAGENTS_DEEP_THINK_LLM` | per-provider | manager/debate model (ollama: `llama3-groq-tool-use`) |
| `TRADINGAGENTS_QUICK_THINK_LLM` | per-provider | default analyst model — also researchers/trader/risk (ollama: `llama3-groq-tool-use`) |
| `TRADINGAGENTS_MARKET_LLM` | quick-think model | model for the Market Analyst only |
| `TRADINGAGENTS_NEWS_LLM` | quick-think model | model for the News Analyst only |
| `TRADINGAGENTS_FUNDAMENTALS_LLM` | quick-think model | model for the Fundamentals Analyst only |
| `TRADINGAGENTS_SOCIAL_LLM` | quick-think model | model for the Sentiment Analyst only (not run by default) |
| `TRADINGAGENTS_LLM_BACKEND_URL` | local: host.docker.internal; hosted: provider default | OpenAI-compatible base URL |
| `TRADINGAGENTS_<PROVIDER>_BASE_URL` | _(unset)_ | endpoint for one provider only, e.g. `TRADINGAGENTS_OPENAI_COMPATIBLE_BASE_URL` |
| `OLLAMA_BASE_URL` | (same default) | alt. way to set the endpoint |
| `TRADINGAGENTS_MAX_DEBATE_ROUNDS` | `1` | bull/bear rounds |
| `TRADINGAGENTS_MAX_RISK_ROUNDS` | `1` | risk-team rounds |
| `TRADINGAGENTS_OUTPUT_LANGUAGE` | `English` | report language (e.g. `Vietnamese`) |
| `TRADINGAGENTS_OLLAMA_STRUCTURED_METHOD` | `json_schema` | structured-output method for local models (`json_schema` / `json_mode` / `function_calling`) |
| `TRADINGAGENTS_WEB_SEARCH` | `1` | enable internet search for the news/sentiment analysts (`0` to disable) |
| `TAVILY_API_KEY` | _(unset)_ | use Tavily search instead of keyless DuckDuckGo |
| `TRADINGAGENTS_NEWS_QUERIES` | _(built-in)_ | comma-separated macro/market queries for `get_global_news` |
| `TRADINGAGENTS_SEARCH_MAX_RESULTS` | `5` | results per web query |

> The default endpoint is `http://host.docker.internal:11434/v1` so the
> containerized backend reaches host-run Ollama out of the box. Running the
> backend outside Docker? Override with
> `TRADINGAGENTS_LLM_BACKEND_URL=http://localhost:11434/v1`.

## Per-role and per-provider models

Upstream builds exactly two clients — a *quick* one shared by every analyst (and
the researchers, trader and risk debators) and a *deep* one for the two managers
— both on the single `llm_provider`. So neither an individual analyst nor a
second provider is expressible. This integration adds both, as **roles**:
`deep`, `quick`, and one per analyst (`market`, `news`, `fundamentals`,
`social`).

Every model var takes an optional `provider:` prefix, so roles can be spread
across providers — expensive reasoning where it pays off, cheap or local models
everywhere else:

```bash
TRADINGAGENTS_LLM_PROVIDER=deepseek                      # for bare model names
TRADINGAGENTS_DEEP_THINK_LLM=openai:gpt-5.6-luna         # managers on OpenAI
TRADINGAGENTS_QUICK_THINK_LLM=deepseek-v4-flash          # analysts on DeepSeek
TRADINGAGENTS_FUNDAMENTALS_LLM=deepseek-v4-pro           # …except this one
TRADINGAGENTS_MARKET_LLM=ollama:qwen3:latest             # and this one, locally
```

A bare name uses `TRADINGAGENTS_LLM_PROVIDER` (analyst vars: the quick model's
provider). Only a prefix that *names a provider* counts, so colon-bearing Ollama
IDs stay intact — `deepseek-r1:7b` is a model, `deepseek:…` is a provider. Every
provider in use needs its own API key (`OPENAI_API_KEY`, `DEEPSEEK_API_KEY`, …);
`/health` reports readiness for all of them and a run is blocked until each is
satisfied. Endpoints resolve per provider: local ones get the Ollama URL, hosted
ones their own, and `TRADINGAGENTS_<PROVIDER>_BASE_URL` overrides just that
provider — an unscoped `TRADINGAGENTS_LLM_BACKEND_URL` is never applied to a
secondary hosted provider, which would send its traffic to the wrong endpoint.

Mechanics (`runner.apply_model_overrides`, active only while the graph is built,
originals restored right after — the compiled nodes keep their own clients):

* **Cross-provider roles** — `TradingAgentsGraph` builds quick+deep from
  `config["llm_provider"]`, so `trading_graph.create_llm_client` is rebound to a
  wrapper that substitutes the role's provider, base URL and provider-specific
  kwargs (reasoning effort, thinking level). Roles are matched by model and
  consumed in construction order, so the same model ID served by two providers
  still resolves correctly.
* **Per-analyst models** — `GraphSetup.setup_graph` picks the analyst factories
  from a *local* dict, so the factory names are rebound in
  `tradingagents.graph.setup` to wrappers closing over the role's own client.

The resolved assignment lives in `cfg["llm_roles"]` (`{role: {provider, model,
base_url}}`) and drives the structured-output fixes per role, so a mixed run
treats its local and hosted models each on their own terms.

`runner.register_configured_models` additionally registers those models as known,
so a model newer than the vendored catalog no longer warns on every client build
(`RuntimeWarning: Model 'gpt-5.6-terra' is not in the known model list for
provider 'openai'`). Nothing else changes — an unlisted model was already used
as-is, and its capabilities fall back to the permissive default that
`apply_structured_method` then narrows.

Models can also be chosen **per run** in the `POST /trading-agents/analyze/stream`
body, same `provider:model` syntax, winning over the env defaults:

```jsonc
{
  "symbol": "FPT",
  "quick_think_llm": "deepseek:deepseek-v4-flash",         // analysts by default
  "deep_think_llm": "openai:gpt-5.6-luna",                 // managers
  "analyst_models": { "market": "ollama:qwen3:latest" }    // this analyst only
}
```

An unknown key in `analyst_models` is a 400 (valid: `market`, `news`,
`fundamentals`, `social`). `GET /trading-agents/models` returns the current
per-role defaults plus each provider's catalog and whether its key is present — a
convenience for the UI pickers, not a whitelist, since Ollama and friends serve
arbitrary model IDs. The `started` SSE event echoes `llm_roles` as actually
resolved, and the **🤝 Agents** page exposes all of this under the "Models"
toggle (options grouped by provider, free text allowed). A mixed run is saved with
`provider` as `openai+deepseek` and a provider-qualified manager model.

## History / persistence

Every completed run is saved to ClickHouse (table
`trading_agent_analyses`, override with `CLICKHOUSE_TRADING_AGENTS_TABLE`) — all
per-agent reports (as a JSON blob), the final decision, signal, provider/model,
and duration. The stream emits a `saved {id}` event on success. The table is
auto-created on first write (`store.py`).

Endpoints:

| Method | Path | Purpose |
|---|---|---|
| GET | `/trading-agents/analyses?symbol=&limit=` | list saved analyses (metadata + snippet), newest first |
| GET | `/trading-agents/analyses/{id}` | one analysis with full per-agent sections |

The **🤝 Agents** page shows a "Saved analyses" dashboard table; clicking a row
reopens that analysis in the same hero / pipeline / report-card view.

## Try it

```bash
# health / model + Ollama reachability
curl http://localhost:8000/api/v1/trading-agents/health

# stream an analysis (SSE)
curl -N -X POST http://localhost:8000/api/v1/trading-agents/analyze/stream \
  -H 'Content-Type: application/json' \
  -d '{"symbol":"FPT"}'
```

Or open the **🤝 Agents** tab in the web app.

## Internet search

The news & sentiment analysts search the web live (`web_search.py`):

- **`get_news(ticker, …)`** — curated wichart research reports **plus** a live
  web search for the ticker. The two are independent: either can be empty
  without suppressing the other.
- **`get_global_news(…)`** — macro/market headlines via web search over a set of
  Vietnam-market + global-macro queries (override with
  `TRADINGAGENTS_NEWS_QUERIES`).

Backends, tried in order: **Tavily** (when `TAVILY_API_KEY` is set — cleaner,
dated results) → **DuckDuckGo** (keyless default, via the `ddgs` package). Any
failure degrades to a clear "search unavailable" note; nothing crashes the run.
`GET /trading-agents/health` reports the active backend under
`web_search_backend`.

## Troubleshooting

**"structured-output invocation failed … retrying once as free text"** — a local
model didn't emit the output schema as a tool call. It's non-fatal (the agent
falls back to free text and the run completes), but to get clean structured
output the runner sets `TRADINGAGENTS_OLLAMA_STRUCTURED_METHOD=json_schema` for
local models by default, which Ollama handles far more reliably than
tool-calling. If your Ollama build is old and rejects json-schema
`response_format`, try `json_mode`, or upgrade Ollama.

## Notes / limitations

- First run is slow: a full pipeline is many LLM calls; on local Ollama expect
  several minutes. Lower `*_ROUNDS` or use smaller models to speed it up.
- The reflection/memory-log alpha-attribution feature originally used yfinance
  for realized returns; with yfinance stubbed out that path is inert (and it is
  never reached anyway, since the runner drives `graph.stream` directly rather
  than `propagate()`). Decisions still work; the self-reflection loop just has no
  realized-return feedback.
- `vn_data.get_news` reads wichart research reports; report bodies come from the
  DeltaLake `wichart_reports` store and may be sparse for some tickers.
