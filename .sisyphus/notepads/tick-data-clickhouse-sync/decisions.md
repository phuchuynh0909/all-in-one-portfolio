# Decisions

## [2026-03-26] Session ses_2d7ab7484ffelG22ua5LGrVioN

### Architecture Decisions
- Stream ingestor: New Bytewax dataflow `tick_ingest.py` - mirrors isp.py structure
- Reconciler: Standalone script `reconciler.py` - mirrors crawl_dnse.py structure (not Bytewax)
- Reconciler is NOT a Bytewax flow - it's a one-shot script triggered at 15:00 (like crawl_dnse.py main())
- New modules: `tick_contract.py` (mapping), `dnse_client.py` (API), `reconciler_schedule.py` (state), `audit_queries.py` (SQL)
- Config additions: TickSyncConfig + ReconcilerConfig added to config.py
- Model additions: TICKS_* schema constants added to model.py

### ClickHouse Table Design
- Table: `ticks`
- Engine: ReplacingMergeTree(received_at)
- ORDER BY: (symbol, sending_time, match_price, match_qty, side)
- PARTITION BY: toYYYYMMDD(sending_time)
- No FINAL on hot paths; use FINAL only in reconciler diff queries

### Idempotency Strategy
- received_at for reconciler inserts = now() at time of reconciler execution
- Since API truth rows get fresh received_at on each reconciler run, ReplacingMergeTree keeps latest
- Same session-day rerun: same composite keys → latest received_at wins → net unique key count stable
