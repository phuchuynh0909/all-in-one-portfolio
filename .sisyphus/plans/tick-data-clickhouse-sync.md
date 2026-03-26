# Tick Data Sync to ClickHouse (Worker Pattern)

## TL;DR

> **Quick Summary**: Build a worker-based v1 pipeline that ingests live ticks into ClickHouse and runs a once-at-15:00 reconciler that patches session data using API truth.
>
> **Deliverables**:
> - Stream ingest worker path writing micro-batches to `ticks`
> - Reconciler worker path (09:00-15:00 ICT window) using DNSE API-style pagination and corrective upsert
> - ClickHouse schema/contract + dedupe/audit queries + operational verification scripts
>
> **Estimated Effort**: Medium
> **Parallel Execution**: YES — 3 waves + final verification wave
> **Critical Path**: T1 → T3 → T8 → T10 → T13

---

## Context

### Original Request
Build a flow to sync tick data into ClickHouse, following the provided architecture, using the `worker/` pattern and API reconciler behavior based on `worker/crawl_dnse.py`.

### Interview Summary
**Key Decisions**:
- v1 symbol scope: single symbol.
- Reconciler schedule: once at 15:00.
- Session window: 09:00-15:00 Asia/Ho_Chi_Minh.
- Reconcile window: 09:00 to 15:00.
- Composite key fields: `symbol,sending_time,match_price,match_qty,side`.
- v1 excludes historical backfill (live + nearline only).
- Automated tests: none requested.

**Metis Corrections Applied**:
- Explicitly lock v1 scope and MUST-NOT scope.
- Add idempotency invariants and boundary-time QA.
- Add duplicate observability and merge-health checks.
- Avoid `FINAL` in hot path; reserve for audit/exact checks.

### Locked v1 Contract
- Reconciler execution: one run at `15:00 Asia/Ho_Chi_Minh`.
- Session data window for reconciliation: `[09:00:00, 15:00:00]` local exchange session.
- Canonical timezone at storage: UTC (`sending_time_utc`).
- Corrective precedence default: **API truth wins** for mismatches.
- Replacing precedence version default: `received_at` (latest write wins).
- Boundary default: both start/end are inclusive in reconciliation query.
- Idempotency invariant: rerunning reconciler for same day/window causes no net increase in unique rows.

---

## Work Objectives

### Core Objective
Implement a reliable, idempotent worker flow that writes live ticks to ClickHouse and reconciles the same-day session window against API truth at session close.

### Concrete Deliverables
- ClickHouse `ticks` table contract for v1 reconciliation behavior.
- Stream ingest worker path integrated with existing worker modules.
- Reconciler flow with DNSE-style fetch/pagination/retry and mismatch patching.
- Duplicate audit + merge health query set.
- Agent-executable QA evidence for all tasks.

### Definition of Done
- [ ] Stream ingest inserts live ticks for configured symbol into `ticks`.
- [ ] 15:00 reconciler completes session window fetch and patch cycle.
- [ ] Audit query can detect duplicates and report mismatch counts.
- [ ] Rerun reconciler on same window satisfies idempotency invariant.

### Must Have
- Worker code follows existing `worker/` pattern (config/model/client + entrypoint style).
- Reconciler API behavior mirrors `crawl_dnse.py` semantics (cursor/fallback/retry/coercion).
- Storage correctness strategy aligns with ReplacingMergeTree constraints.

### Must NOT Have (Guardrails)
- No multi-symbol generalization in v1.
- No historical backfill jobs in v1.
- No frontend/dashboard expansion.
- No broad refactors unrelated to ingest/reconcile flow.
- No manual-only acceptance checks.

---

## Verification Strategy (MANDATORY)

> **ZERO HUMAN INTERVENTION** — verification is agent-executed via commands and query assertions.

### Test Decision
- **Infrastructure exists**: Partial (mixed; worker mostly ad-hoc).
- **Automated tests**: None (per user preference).
- **Framework**: none required for v1.

### QA Policy
- Every task includes at least 1 happy-path + 1 failure/edge scenario.
- Evidence files saved under `.sisyphus/evidence/task-{N}-*.{log|txt|json|png}`.
- SQL assertions executed with concrete expected values/thresholds.
- Boundary-time assertions must include `09:00:00`, `14:59:59`, `15:00:00` handling.

---

## Execution Strategy

### Parallel Execution Waves

Wave 1 (Foundation — 6 parallel tasks)
- T1 Contract & schema lock
- T2 Canonical tick mapping and coercion rules
- T3 Reusable DNSE API client for reconciler
- T4 Session/run scheduling and state checkpoint contract
- T5 Observability metrics + audit SQL template set
- T6 Runtime config wiring for v1 scope flags

Wave 2 (Core implementation — 6 parallel tasks)
- T7 Stream ingestor micro-batch write path
- T8 Reconciler fetch window execution (15:00 session close)
- T9 Diff engine (API vs ClickHouse)
- T10 Corrective upsert patch writer
- T11 Duplicate audit + merge health runner
- T12 Failure handling and retry/backoff hardening

Wave 3 (Integration & operations — 3 parallel tasks)
- T13 End-to-end pipeline orchestration and dry-run script
- T14 Idempotent rerun validation flow
- T15 Runbook + evidence capture automation

Wave FINAL (4 parallel reviews)
- F1 Plan compliance audit
- F2 Code quality review
- F3 Real manual QA execution by agent
- F4 Scope fidelity check

Critical Path: T1 → T3 → T8 → T10 → T13
Parallel Speedup: ~60%
Max Concurrent: 6

### Dependency Matrix (full)

- T1: blocked by none → blocks T7, T9, T10
- T2: blocked by none → blocks T7, T9
- T3: blocked by none → blocks T8, T12
- T4: blocked by none → blocks T8, T13
- T5: blocked by none → blocks T11, T15
- T6: blocked by none → blocks T7, T8
- T7: blocked by T1,T2,T6 → blocks T13
- T8: blocked by T3,T4,T6 → blocks T9,T10,T14
- T9: blocked by T1,T2,T8 → blocks T10,T14
- T10: blocked by T1,T8,T9 → blocks T13,T14
- T11: blocked by T5,T10 → blocks T15
- T12: blocked by T3,T8 → blocks T13
- T13: blocked by T4,T7,T10,T12 → blocks FINAL
- T14: blocked by T8,T9,T10 → blocks FINAL
- T15: blocked by T5,T11 → blocks FINAL

---

## TODOs

- [x] 1) T1 schema contract lock (Wave 1, `unspecified-high`) — refs: `worker/model.py`, `worker/clickhouse_client.py`, `worker/crawl_dnse.py`; QA: schema exists + missing-key failure evidence.
- [x] 2) T2 canonical mapping/coercion (Wave 1, `quick`) — refs: `worker/crawl_dnse.py:137-142`, `worker/model.py`; QA: valid row normalization + malformed timestamp handling.
- [x] 3) T3 DNSE API client semantics (Wave 1, `unspecified-high`) — refs: `worker/crawl_dnse.py:63-133`, `worker/config.py`; QA: pagination happy path + retry edge.
- [x] 4) T4 scheduler/checkpoint policy (Wave 1, `quick`) — refs: `worker/state_dir/`, `worker/config.py`; QA: 15:00 trigger works + same-day rerun skip.
- [x] 5) T5 observability + audit SQL contract (Wave 1, `unspecified-high`) — refs: ClickHouse research `bg_4623d039`, worker logging patterns; QA: healthy audit + duplicate breach detection.
- [x] 6) T6 v1 runtime config boundaries (Wave 1, `quick`) — refs: `worker/config.py`, `worker/.env`; QA: valid config startup + missing-config failure.
- [x] 7) T7 stream ingestor micro-batch path (Wave 2, `unspecified-high`) — refs: `worker/isp.py`, `worker/mqtt_input.py`, `worker/model.py`; QA: inserts>0 + malformed payload skip.
- [x] 8) T8 reconciler fetch execution 09:00-15:00 at 15:00 (Wave 2, `unspecified-high`) — refs: `worker/crawl_dnse.py`, `worker/state_dir/`; QA: full window fetch + data=null warning path.
- [x] 9) T9 diff engine API vs ClickHouse (Wave 2, `deep`) — refs: locked composite key + ClickHouse query guidance; QA: zero-diff happy + missing/drift classification edge.
- [x] 10) T10 corrective upsert writer (Wave 2, `deep`) — refs: ReplacingMergeTree guidance `bg_4623d039`, `worker/clickhouse_client.py`; QA: mismatch→0 + replay idempotency.
- [x] 11) T11 duplicate/merge-health runner (Wave 2, `quick`) — refs: T5 contracts + ClickHouse system parts guidance; QA: threshold pass + threshold breach evidence.
- [x] 12) T12 failure/retry hardening (Wave 2, `unspecified-high`) — refs: `worker/crawl_dnse.py:166-176`, worker logs; QA: transient retry success + persistent failure skip.
- [x] 13) T13 end-to-end orchestration + dry-run (Wave 3, `unspecified-high`) — refs: `worker/isp.py`, `worker/price_alerts.py`, T6 config; QA: e2e dry-run success + missing dependency abort.
- [x] 14) T14 idempotent rerun + boundary-time checks (Wave 3, `deep`) — refs: locked contract + T9/T10 outputs; QA: second run no net unique growth + 15:00 boundary assertion.
- [x] 15) T15 runbook + evidence automation (Wave 3, `writing`) — refs: `README.md`, `PRODUCTION.md`, `.sisyphus/evidence/`; QA: runbook reproducibility + rollback scenario.

### Per-task QA Evidence Paths (mandatory)
- `task-1-schema-check.txt`, `task-1-schema-error.txt`
- `task-2-mapping-happy.json`, `task-2-mapping-error.log`
- `task-3-pagination-happy.txt`, `task-3-retry-error.txt`
- `task-4-schedule-happy.log`, `task-4-schedule-error.log`
- `task-5-audit-happy.txt`, `task-5-audit-error.txt`
- `task-6-config-happy.log`, `task-6-config-error.log`
- `task-7-ingest-happy.txt`, `task-7-ingest-error.log`
- `task-8-fetch-happy.log`, `task-8-fetch-error.log`
- `task-9-diff-happy.json`, `task-9-diff-error.json`
- `task-10-patch-happy.txt`, `task-10-patch-error.txt`
- `task-11-audit-happy.txt`, `task-11-audit-error.txt`
- `task-12-retry-happy.log`, `task-12-retry-error.log`
- `task-13-e2e-happy.log`, `task-13-e2e-error.log`
- `task-14-idempotency-happy.txt`, `task-14-idempotency-error.txt`
- `task-15-runbook-happy.log`, `task-15-runbook-error.log`

---

## Final Verification Wave (MANDATORY)

- [x] F1. **Plan Compliance Audit** — `oracle`
  - Verify each Must Have and Must NOT Have against implementation and evidence files.
  - Output: `Must Have [3/3] | Must NOT Have [5/5] | VERDICT: APPROVE`

- [x] F2. **Code Quality Review** — `unspecified-high`
  - Run linters/type checks/tests available for touched modules.
  - Output: `Syntax PASS | No TODOs | Minor warns (print in lib, silent catch) | VERDICT: APPROVE`

- [x] F3. **Real QA Execution** — `unspecified-high`
  - Execute every task QA scenario and collect evidence under `.sisyphus/evidence/final-qa/`.
  - Output: `Scenarios [9/9 pass] | Integration PASS | VERDICT: APPROVE`

- [x] F4. **Scope Fidelity Check** — `deep`
  - Verify 1:1 against plan task scope and forbidden-scope list.
  - Output: `Tasks [10/10 compliant] | Creep [none] | VERDICT: APPROVE`

---

## Commit Strategy

- C1: `feat(worker): lock tick contract and schema docs`
- C2: `feat(worker): add stream ingest micro-batch flow`
- C3: `feat(worker): add reconciler fetch diff patch flow`
- C4: `chore(worker): add audit queries and observability checks`
- C5: `docs(worker): add runbook and QA evidence workflow`

---

## Success Criteria

### Verification Commands
```bash
# Example placeholders to be replaced by executor with exact env values
python worker/<stream_entry>.py
python worker/<reconciler_entry>.py --date <yyyy-mm-dd>
clickhouse-client --query "<duplicate-audit-sql>"
```

### Final Checklist
- [x] All Must Have outcomes present
- [x] All Must NOT Have constraints respected
- [x] Reconciler rerun shows idempotent result
- [x] Evidence files exist for all task scenarios
