# Draft: Tick data ClickHouse sync

## Requirements (confirmed)
- Build a flow to sync tick data into ClickHouse.
- Follow the provided architecture diagram (stream ingest + reconciler).
- Use the worker pattern under `worker/`.
- Reconciler API behavior should follow `worker/crawl_dnse.py` style.
- v1 symbol scope: **single symbol**.
- Automated tests preference: **no automated tests** (QA scenarios still required in plan).
- v1 mode: **live + nearline only** (no historical backfill in v1).

## Technical Decisions
- Reconciler source of truth is REST API (from architecture note).
- Storage target is ClickHouse `ticks` table with deduplication semantics (from architecture note).
- Worker implementation style should align with existing Bytewax-style workers (`isp.py`, `price_alerts.py`) and shared config/client modules.

## Research Findings
- Existing worker script `worker/crawl_dnse.py` already has API pagination logic with `before` cursor and retry behavior.
- Worker directory already contains ClickHouse integration-related modules (`clickhouse_client.py`, `mock_clickhouse.py`, `model.py`, `config.py`).
- Architecture notes indicate:
  - Primary ingest path = WebSocket stream micro-batches into `ticks`
  - Reconciler path = periodic API truth check and corrective upsert
  - Suggested reconciler window = `now-30m` to `now-2m`
  - Read modes include exact (`FINAL`) and fast analytics (without `FINAL`)

## Research Tasks Launched
- `bg_4fddcca0`: Worker pattern mapping in `worker/`
- `bg_af81844a`: DNSE reconciler API contract extraction from `crawl_dnse.py`
- `bg_4623d039`: ClickHouse dedupe/reconciliation best-practice research
- `bg_1a746275`: Test infrastructure assessment for this repo

## Research Findings (completed)
- **Worker pattern mapping** (`bg_4fddcca0`):
  - Canonical pattern files: `worker/isp.py`, `worker/price_alerts.py`.
  - Shared modules for consistency: `worker/config.py`, `worker/clickhouse_client.py`, `worker/model.py`.
  - Typical structure: Dataflow entrypoint, centralized config, stateful operators, ClickHouse sink.
- **DNSE API contract** (`bg_af81844a`) from `worker/crawl_dnse.py`:
  - Backward pagination via `before` cursor.
  - First-call fallback to end-of-day anchor when empty.
  - Retry model: request failures retried once with backoff, then skip unit of work.
  - Data coercion expectations: ISO8601 UTC timestamp, numeric coercions, nullable integer fields.
- **ClickHouse guidance** (`bg_4623d039`):
  - `ReplacingMergeTree` provides eventual dedupe; correctness-sensitive reads need `FINAL` or equivalent latest-row query logic.
  - Avoid `FINAL` on hot large scans; use targeted windows/aggregations (`argMax`, `LIMIT BY`) where applicable.
  - Keep dedupe/idempotency strategy explicit for retries (`insert_deduplicate`, dedupe tokens where needed).
  - Add duplicate-audit queries and merge-lag monitoring as operational guardrails.
- **Test infra** (`bg_1a746275`):
  - Repo has mixed maturity; backend has pytest setup, worker area relies more on ad-hoc scripts.
  - For this work, user selected no automated tests.

## Open Questions
- Reconciler cadence and window likely tied to market session (`09:00` to `15:00`) with end-of-session run around `15:00`, but exact policy still needs explicit confirmation.
- Final ClickHouse schema and engine details to enforce.
- Idempotency/composite key exact fields still undefined (user selected custom key).
- Backfill boundaries and operational SLOs.
- Whether to include v1 backfill or forward-only sync.

## Scope Boundaries
- INCLUDE: Ingestion + reconciliation flow design in worker pattern.
- EXCLUDE: Unrelated frontend/backend features outside tick pipeline.
