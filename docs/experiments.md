# Experiment store

Persists vectorbt backtest runs as Parquet so notebooks can query them with
DuckDB and the `/experiments` page can analyse them in the browser.

Design: `superpowers/specs/2026-08-30-experiment-analytics-platform-design.md`

## Logging a run

```python
from backend.app.services.experiments import log_experiment

run = log_experiment(
    pf_oos,                                  # a vectorbt Portfolio
    name="backtest_012_gframa_hbo",
    params=best_params,
    tags=["oos", "optuna", "balanced"],
    features=entry_features_df,              # optional; keyed (symbol, entry_dt)
    benchmark=pf_base.value(),               # optional
    exit_reasons=exit_reason_df,             # optional; keyed (symbol, entry_dt)
    notes="balanced selection from the NSGA-II study",
    notebook="notebooks/backtest_012.ipynb",
)
print(run.run_id)
```

`features` columns are prefixed `feat_` and left-joined on `(symbol, entry_dt)`.
A feature that would overwrite an existing trade column raises
`FeatureCollisionError` rather than silently replacing it.

## Where it writes

| Env var | Default | Meaning |
| --- | --- | --- |
| `EXPERIMENTS_BACKEND` | `local` | Only `local` is implemented; `r2` raises `NotImplementedError` |
| `EXPERIMENTS_DIR` | `<repo>/data/experiments` | Root of the store |

```
data/experiments/
  catalog.json                       # derived index of every run
  experiments.duckdb                 # views only, no data
  runs/<run_id>/{meta,trades,symbol_stats,equity}.{json,parquet}
```

Everything under `data/experiments/` is gitignored.

## Querying from a notebook

```python
import duckdb
con = duckdb.connect("data/experiments/experiments.duckdb", read_only=True)
con.sql("SELECT run_id, avg(net_return) FROM trades GROUP BY 1")
```

Four views: `runs`, `trades`, `symbol_stats`, `equity`. The database holds no
data — only views over the Parquet — so it is disposable.

## Recovery

Both are safe to run at any time; neither touches the Parquet.

```python
from backend.app.services.experiments import ExperimentStore
store = ExperimentStore.from_env()
store.rebuild_catalog()   # regenerate catalog.json from runs/*/meta.json
store.rebuild_views()     # regenerate experiments.duckdb
```

`catalog.json` is derived, never authoritative, so concurrent notebook writes
cannot corrupt it — a run missing from the catalog is fixed by a rebuild.

## Serving the page in dev

The page reads Parquet over HTTP with DuckDB-WASM, so the store must be exposed
as static assets. How that happens differs by run mode.

### Docker (`make up`, the normal path)

`docker-compose.yml` bind-mounts the store into the frontend container:

```yaml
- ./data/experiments:/app/public/experiment-data
```

A symlink in `frontend/public/` cannot be used here — it would resolve outside
`/app` inside the container and 404.

`node_modules` is the named volume `frontend_node_modules`, populated by
`npm ci` when the **image** is built. Adding a frontend dependency therefore
needs a rebuild, and because a non-empty named volume is never repopulated, the
volume has to be dropped first:

```bash
docker compose rm -sf frontend
docker volume rm all-in-one-portfolio_frontend_node_modules
make rebuild-frontend
```

Symptom when this is skipped: `Failed to resolve import "@duckdb/duckdb-wasm"`
with paths under `/app/`.

### Host (`npm run dev` directly)



The React page reads the Parquet directly with DuckDB-WASM, so the files must be
reachable over HTTP. In dev that is a symlink into the Vite public dir:

```bash
mkdir -p data/experiments
ln -sfn ../../data/experiments frontend/public/experiment-data
cd frontend && npm run dev     # then open /experiments
```

Do not create this symlink if you also run the Docker stack: the bind mount
above covers that case, and the two would fight over the same path.

Vite serves the symlinked directory and honours Range requests (verified: a
range request returns `206 Partial Content`), which is what lets DuckDB read
only the column chunks a query needs. In production the same must hold — nginx
supports ranges by default. Override the location with
`VITE_EXPERIMENTS_BASE_URL` if the files are served from elsewhere.

The directory is `experiment-data`, **not** `experiments`: the latter collides
with the `/experiments` SPA route. Vite happens to survive that collision
because its HTML-fallback middleware runs before static file resolution, but
nginx gives no such guarantee, so the two names are kept distinct.

## Things that will surprise you

- **`equity_agg` is usually `"mean"`.** The notebooks run
  `from_signals(..., cash_sharing=False)`, so every symbol is an independent
  book and there is no single traded equity curve. What is stored is the
  equal-weight mean of the per-symbol curves, and the UI labels it as such. Only
  a genuinely cash-shared `Portfolio` yields `equity_agg="portfolio"`.
- **`exit_reason` is NULL unless you pass it.** vectorbt does not record why a
  position closed. Supply `exit_reasons=` to slice by it in the Attribution tab.
- **Outcome buckets are not stored.** The `1_catastrophic_loss …  5_big_win`
  segmentation is a quantile cut computed in SQL at query time, so cut points
  can be changed without rewriting any file.
- **Sortino and profit factor can be infinite** for symbols with no downside or
  no losing trades. Those are written as NULL, not `inf`.
- **Timestamps read back as epoch milliseconds** in the browser (Arrow), not ISO
  strings. Use `lib/experiments/time.ts` rather than string-slicing them.
