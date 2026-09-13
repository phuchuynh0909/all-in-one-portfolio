# Tick Data ClickHouse Sync Runbook

## Overview

`tick_ingest.py` continuously stores DNSE Trade-Extra WebSocket events.
`scheduled_backfill.py` discovers every historical symbol/date pair that still
has migrated legacy identities, plus every symbol in the latest completed data
date. It reconciles them from the authoritative GraphQL history endpoint at
15:05 Asia/Ho_Chi_Minh on weekdays. Both feeds carry DNSE
`totalVolumeTraded`, normalized as `event_sequence`, so replayed events replace
cleanly while otherwise-identical legitimate executions remain distinct.

> **Working directory**: All commands must be run from inside `worker/` (or use `PYTHONPATH=worker` from the project root). Bytewax locates modules by name on the Python path.

## Quick Start
```bash
# All commands below assume you are inside the worker/ directory
cd worker

# 1. Existing installations only: stop ingestion and migrate event identity
python scripts/migrate_ticks_event_identity.py --swap --confirm-ingest-stopped

# 2. Start the live ingestor
python -m bytewax.run workers.tick_ingest:flow

# 3. Start the all-symbol after-session scheduler
python -m workers.scheduled_backfill

# 4. Run the complete discovered backlog immediately
python -m workers.scheduled_backfill --once --force

# 5. Reconcile an explicit symbol/range
python scripts/backfill_ticks.py --symbol BCM --start 2026-09-11 --end 2026-09-11
```

## Normal Daily Operations

Docker Compose runs `worker-tick-backfill`. It polls once per minute and runs at
or after 15:05 ICT on weekdays. Each run discovers:

- every historical `(session date, symbol)` pair still containing migration-only
  legacy rows; and
- every symbol present in the latest completed data date.

Successful historical pairs leave the backlog when their legacy rows are
replaced. Derived-table repair dates are persisted before tick mutation, so a
restart retries an interrupted repair. The daily completion marker is written
only after every discovered pair and repair succeeds.

Configuration:

- `RECONCILER_HOUR=15`, `RECONCILER_MINUTE=5` — ICT trigger
- `RECONCILER_POLL_SECONDS=60` — scheduler polling interval

## Rerun and Recovery

```bash
cd worker

# Preview one symbol/day; fetches DNSE and reads ClickHouse, but does not write
python scripts/backfill_ticks.py --symbol BCM \
  --start 2026-09-11 --end 2026-09-11 --dry-run

# Apply one symbol/day
python scripts/backfill_ticks.py --symbol BCM \
  --start 2026-09-11 --end 2026-09-11

# Restore the pre-migration table; stop ingestion first
python scripts/migrate_ticks_event_identity.py \
  --rollback --confirm-ingest-stopped
```

## Audit and Monitoring
```bash
cd worker

# Check for duplicates and merge health
python scripts/run_audit.py --date 2026-03-26

```

## Rollback and Disable

Stop `worker-tick-backfill` to disable scheduled writes. Stop
`worker-tick-ingest` before swapping or rolling back the tick table. The
migration retains `ticks_pre_event_identity` until
`migrate_ticks_event_identity.py --drop-old` is run.

## Troubleshooting
Common issues and fixes:

1. **`ModuleNotFoundError: No module named 'tick_ingest'`** — You are running from the wrong directory. Run from inside `worker/`:
   ```bash
   cd worker && python -m bytewax.run tick_ingest:flow
   ```

2. **Schedule guard**: the automatic job runs only on weekdays at or after the
   configured ICT trigger and only once per date.

3. **Old sorting key**: stop ingestion and run
   `python scripts/migrate_ticks_event_identity.py --swap --confirm-ingest-stopped`.

4. **ClickHouse connection failed**: check `CLICKHOUSE_HOST`, `PORT`, `USER`,
   and `PASSWORD`.

5. **API returns null data**: inspect the DNSE response and network access; the
   history endpoint used here does not require trading credentials.

6. **Duplicate audit**: duplicate groups now mean multiple stored versions of
   one `(symbol, date, board_id, event_sequence, sending_time)` identity.
   `sending_time` disambiguates cumulative-volume resets between auction phases;
   `FINAL` resolves actual replays.
