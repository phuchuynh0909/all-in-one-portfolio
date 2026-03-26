# Tick Data ClickHouse Sync Runbook

## Overview
This pipeline uses two workers to sync tick data. The `tick_ingest.py` script runs a continuous Bytewax stream. The `reconciler.py` script runs once at 15:00 ICT. This version covers a single symbol, `41I1G4000`, for live and nearline data.

## Quick Start
```bash
# 1. Create ClickHouse table (one-time setup)
python -c "from config import config; from clickhouse_client import get_clickhouse_client; from model import TICKS_CREATE_TABLE_DDL; c = get_clickhouse_client(); c.query(TICKS_CREATE_TABLE_DDL.format(database=config.clickhouse.database))"

# 2. Start stream ingestor (keep running in background)
# IMPORTANT: must run from inside the worker/ directory so tick_ingest is on the Python path
cd worker && python -m bytewax.run tick_ingest:flow
# Alternative from project root:
# PYTHONPATH=worker python -m bytewax.run tick_ingest:flow

# 3. Run reconciler (at or after 15:00 ICT)
cd worker && python reconciler.py

# 4. Run full pipeline with audit (recommended)
cd worker && python run_pipeline.py
```

## Normal Daily Operations
The stream ingestor runs continuously during the trading session. Run the reconciler once at 15:00 ICT using `python reconciler.py`. You can also use a cron job.

All scripts must be run from inside the `worker/` directory (or with `PYTHONPATH=worker`).

Cron example:
`0 8 * * 1-5 cd /path/to/worker && python reconciler.py` (08:00 UTC = 15:00 ICT)

## Rerun and Recovery
```bash
# Rerun reconciler for today (force bypass schedule guard)
python worker/reconciler.py --force

# Rerun for a specific past date
python worker/reconciler.py --date 2026-03-25 --force

# Preview what reconciler would do (no writes)
python worker/reconciler.py --dry-run --force
```

## Audit and Monitoring
```bash
# Check for duplicates and merge health
python worker/run_audit.py --date 2026-03-26

# Save evidence to file
python worker/run_audit.py --date 2026-03-26 --output .sisyphus/evidence/task-11-audit-happy.txt

# Full pipeline with audit
python worker/run_pipeline.py --date 2026-03-26
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
1. Schedule guard: If `reconciler.py` says "not running", use the `--force` flag.
2. ClickHouse connection failed: Check `CLICKHOUSE_HOST`, `PORT`, `USER`, and `PASSWORD` in your `.env` file.
3. API returns null data: This is usually an authentication error. Check `ENTRADE_USER` and `ENTRADE_PASSWORD` in your `.env` file.
4. High duplicate count: Run `python run_audit.py --date YYYY-MM-DD`. Wait for the ClickHouse background merge to finish, then rerun.
5. Table doesn't exist: Run the `CREATE TABLE` command from the Quick Start section.
