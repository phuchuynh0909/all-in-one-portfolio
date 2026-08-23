# Tick Data ClickHouse Sync Runbook

## Overview
This pipeline uses two workers to sync tick data. The `tick_ingest.py` script runs a continuous Bytewax stream, consuming the DNSE OpenAPI Trade-Extra **WebSocket** feed (requires `DNSE_API_KEY` / `DNSE_API_SECRET`). The `reconciler.py` script runs once at 15:00 ICT. `tick_ingest` covers every symbol in `watchlist.json` plus the current VN30F front-month contract; the reconciler still back-fills only `TICK_SYMBOL`.

> **Working directory**: All commands must be run from inside `worker/` (or use `PYTHONPATH=worker` from the project root). Bytewax locates modules by name on the Python path.

## Quick Start
```bash
# All commands below assume you are inside the worker/ directory
cd worker

# 1. Create ClickHouse table (optional — tick_ingest does this itself on startup)
python -c "from workers.tick_ingest import ensure_ticks_table; ensure_ticks_table()"

# 2. Start stream ingestor (keep running in background)
python -m bytewax.run workers.tick_ingest:flow

# 3. Run reconciler (at or after 15:00 ICT)
python workers/reconciler.py

# 4. Run full pipeline with audit (recommended)
python scripts/run_pipeline.py
```

## Normal Daily Operations
The stream ingestor runs continuously during the trading session. Run the reconciler once at 15:00 ICT using `python reconciler.py`. You can also use a cron job.

All scripts must be run from inside the `worker/` directory (or with `PYTHONPATH=worker`).

Cron example:
`0 8 * * 1-5 cd /path/to/worker && python workers/reconciler.py` (08:00 UTC = 15:00 ICT)

## Rerun and Recovery
```bash
cd worker

# Rerun reconciler for today (force bypass schedule guard)
python workers/reconciler.py --force

# Rerun for a specific past date
python workers/reconciler.py --date 2026-03-25 --force

# Preview what reconciler would do (no writes)
python workers/reconciler.py --dry-run --force
```

## Audit and Monitoring
```bash
cd worker

# Check for duplicates and merge health
python scripts/run_audit.py --date 2026-03-26

# Save evidence to file
python scripts/run_audit.py --date 2026-03-26 --output ../.sisyphus/evidence/task-11-audit-happy.txt

# Full pipeline with audit
python scripts/run_pipeline.py --date 2026-03-26
```

## Rollback and Disable
To disable the reconciler, set the environment variable `RECONCILER_FORCE_RERUN=0` and stop running `reconciler.py`. Stop the Bytewax process to disable the stream ingestor. Set `TICK_DRY_RUN=1` in your environment to prevent any writes during a rollback.

## Evidence Capture Checklist
Check these paths for evidence:
- task-1: `task-1-schema-check.txt` (schema validation)
- task-7: `task-7-ingest-happy.txt` (stream inserts > 0)
- task-8: `task-8-fetch-happy.log` (reconciler fetch)
- task-10: `task-10-patch-happy.txt` (mismatch count is 0)
- task-11: `task-11-audit-happy.txt` (audit PASS)
- task-14: `task-14-idempotency-happy.txt` (second run patches count is 0)

## Troubleshooting
Common issues and fixes:

1. **`ModuleNotFoundError: No module named 'tick_ingest'`** — You are running from the wrong directory. Run from inside `worker/`:
   ```bash
   cd worker && python -m bytewax.run tick_ingest:flow
   ```

2. **Schedule guard**: If `reconciler.py` says "not running", use the `--force` flag.

3. **ClickHouse connection failed**: Check `CLICKHOUSE_HOST`, `PORT`, `USER`, and `PASSWORD` in your `.env` file.

4. **API returns null data**: This is usually an authentication error. Check `ENTRADE_USER` and `ENTRADE_PASSWORD` in your `.env` file.

5. **High duplicate count**: Run `python run_audit.py --date YYYY-MM-DD`. Wait for the ClickHouse background merge to finish, then rerun.

6. **Table doesn't exist**: Run the `CREATE TABLE` command from Quick Start. If `bytewax.clickhouse` logs `Table 'ticks' exists` on import, the table is already present and the step can be skipped.
