# Dividend & Corporate Action Handling for Portfolio Positions

**Date:** 2026-08-25
**Status:** Approved design, not yet implemented
**Affects:** `backend/app/services/portfolio_service.py`, `backend/app/db/models/portfolio.py`,
`backend/app/schemas/portfolio.py`, `backend/app/api/v1/routes/portfolio.py`, a new worker task

## Problem

Positions store `quantity` and `purchase_price` frozen at purchase, while `current_price`
comes live from ClickHouse `ohlc_eod`. Corporate actions break that pairing:

- A **stock dividend or bonus issue** raises the share count and the market price adjusts
  down proportionally. With `quantity` frozen, the position shows a large loss that did not
  happen.
- A **cash dividend** pays out and the price drops on the ex-date. With no record of the
  payment, the drop is booked as a loss and the income vanishes.

Positions are already per-lot — the same ticker appears as several rows with their own
`purchase_price` and `purchase_date` — so actions must be applied per lot, not per ticker.

### Measured impact at time of writing

All eight open lots are wrong. Figures in thousands of VND, matching the price units.
Quantities assume the round-down rule below.

| | reported | corrected |
|---|---|---|
| total_invested | 5,693,634 | 5,693,634 (unchanged) |
| total_value | 4,053,718 | 4,588,581 |
| unrealized P/L | −1,639,916 | −1,105,053 |
| dividend income, gross | — | +307,044 |
| dividend income, net of 5% PIT | — | +291,692 |
| **total return (net)** | **−1,639,916** | **−813,361** |

The dashboard reports roughly twice the real loss. `total_invested` is undistorted, which is
what makes the cost-preserving rule below the right choice. The error is entirely **35,523
uncredited bonus shares** (13.2% of portfolio value) and **307,044,000 VND** of unrecorded
gross cash dividends.

Per-lot detail:

| lot | ticker | stored qty | stored px | correct qty | correct px |
|---|---|---|---|---|---|
| 27 | NKG | 10,900 | 15.460000 | 11,990 | 14.054545 |
| 29 | NKG | 9,600 | 14.300000 | 10,560 | 13.000000 |
| 33 | PAN | 66,240 | 22.670000 | 79,488 | 18.891667 |
| 4 | VCG | 40,820 | 24.100000 | 47,611 | 20.662494 |
| 25 | VCG | 31,500 | 19.250000 | 34,020 | 17.824074 |
| 32 | VCG | 19,000 | 22.000000 | 20,520 | 20.370370 |
| 9 | YEG | 100,000 | 14.160000 | 107,000 | 13.233645 |
| 1 | YEG | 34,200 | 13.510000 | 36,594 | 12.626168 |

Lot 4 is the only one where rounding bites: `40,820 × 0.08 = 3,265.6` truncates to 3,265,
and the 2026-07-14 event then compounds off 44,085 rather than 44,086.

## Data source

Two DNSE endpoints, neither requiring authentication. Both return the same row schema, so
one parser serves both.

**`GET /senses-api/corporate-actions/history?symbol=X&pageSize=N&page=N`** — authoritative.
Complete per-ticker history (VCG 44 events back to 2008), honours `symbol` and paging,
contains **only past events**. Correctness rests here: because it is complete, a missed poll
is self-healing.

**`GET /senses-api/corporate-actions`** — a ~30-day forward calendar. Ignores every
parameter tried (`symbol`, `symbols`, `fromDate`/`toDate`, `exRightsDateFrom`/`To`, `page`,
`size` all return the identical 74 rows). Not used in v1; a later Telegram heads-up could
use it via the notifier already in `worker/`.

Row schema: `symbol`, `name`, `eventId`, `exRightsDate`, `recordDate`, `actionDate`
(payment), `title`, `content`, `note`, `url`, `eventType`.

Six `name` values appear; three affect a position:

| `name` | meaning | handling |
|---|---|---|
| `Trả cổ tức bằng tiền mặt` | cash dividend | income |
| `Trả cổ tức bằng cổ phiếu` | stock dividend | share count + basis |
| `Thưởng cổ phiếu` | bonus shares | share count + basis |
| `Họp ĐHCĐ bất thường` | EGM | ignored |
| `Họp ĐHCĐ thường niên` | AGM | ignored |
| `Lấy ý kiến CĐ bằng văn bản` | written shareholder vote | ignored |

**Amounts are not structured fields.** They exist only inside the Vietnamese `title`, which
is why parse failures must never be guessed at:

- cash — `Trả cổ tức năm 2025 bằng tiền 800 đồng/CP`
- stock — `Trả cổ tức năm 2025 bằng cổ phiếu tỷ lệ 100:8`
- bonus — `Thưởng cổ phiếu tỷ lệ 100:10.5`

## Accounting decisions

**Cash dividends are income; cost basis is untouched.** `purchase_price` keeps meaning
"what I actually paid", so `total_invested` stays honest and unrealized P/L stays comparable
against the adjusted market price. The cash becomes its own reported component.

**Stock dividends mutate the lot, preserving total cost.** `quantity × (1+r)` and
`purchase_price ÷ (1+r)`, so `purchase_price × quantity` is invariant. Every existing
consumer of `quantity`/`purchase_price` — `get_positions`, `get_portfolio_summary`, the
optimizer — stays correct with no extra computation. The event log is the audit trail.

**Gross is stored; net is derived.** Vietnam withholds 5% PIT on cash dividends. The gross
per-share amount DNSE reports is the factual record, so it is what gets stored, alongside a
nullable `tax_withheld_pct` defaulted to `0.05` for cash events. Net is computed from the
two, so the summary can report either and the headline figure can change without a
migration.

## Data model

### New table `corporate_action`

Both the archive of what DNSE reported and the ledger of what was done about it.

| column | type | notes |
|---|---|---|
| `id` | INTEGER PK | |
| `symbol` | VARCHAR(20) | indexed |
| `event_id` | BIGINT | DNSE `eventId`, **UNIQUE** — the idempotency key |
| `name` | VARCHAR(255) | raw Vietnamese, verbatim |
| `action_type` | ENUM(`cash`,`stock`) | derived from `name` |
| `ex_date` | DATE | indexed; drives eligibility and ordering |
| `record_date` | DATE | nullable |
| `pay_date` | DATE | nullable, from `actionDate` |
| `amount_per_share` | DECIMAL(15,6) | nullable; cash only, gross |
| `ratio` | DECIMAL(15,8) | nullable; stock only, `B/A` |
| `tax_withheld_pct` | DECIMAL(5,4) | nullable; defaults 0.05 for cash |
| `title` | VARCHAR(500) | **verbatim** — the only evidence of what was parsed |
| `url` | VARCHAR(1024) | nullable |
| `source` | ENUM(`dnse_history`,`dnse_calendar`,`manual`) | |
| `status` | ENUM(`pending`,`applied`,`ignored`,`unparsed`) | |
| `applied_at` | TIMESTAMP | nullable |
| `created_at` / `updated_at` | TIMESTAMP | |

`title` is kept verbatim because it is the only way to adjudicate an ambiguous parse — the
real case being `TRC`'s `Thưởng cổ phiếu tỷ lệ 01:03`, where zero-padding makes the intended
ratio genuinely unclear.

### New table `corporate_action_application`

What makes the lot mutation reversible.

| column | type | notes |
|---|---|---|
| `id` | INTEGER PK | |
| `corporate_action_id` | FK → `corporate_action.id` | |
| `position_id` | INTEGER | nullable — the lot may later be closed |
| `transaction_id` | FK → `transactions.id` | the row written |
| `qty_before` / `qty_after` | DECIMAL(15,6) | |
| `price_before` / `price_after` | DECIMAL(15,6) | |
| `cash_amount` | DECIMAL(20,6) | nullable; gross, cash events only |
| `created_at` | TIMESTAMP | |

UNIQUE `(corporate_action_id, position_id)` — a second attempt to apply one event to one lot
cannot insert.

### Migration to `transactions`

`transaction_type` moves from `ENUM('buy','sell')` to
`ENUM('buy','sell','dividend_cash','dividend_stock')` so applied events live in the existing
ledger and stay visible in transaction history.

Cash dividend rows: `price` = gross amount per share, `quantity` = eligible shares,
`close_price` = NULL. Income is `price × quantity`.

Stock dividend rows: `quantity` = shares added, `price` = 0, `close_price` = NULL. They
record the event; they contribute nothing to either P/L figure.

### Pydantic schema changes (easy to miss)

`app/schemas/portfolio.py` currently blocks both new row types and must be relaxed:

- `TransactionBase.transaction_type` is `Field(..., pattern="^(buy|sell)$")` — the pattern
  must accept `dividend_cash` and `dividend_stock`, or the ENUM migration alone achieves
  nothing.
- `TransactionBase.price` is `Field(..., gt=0)`, which rejects the `price = 0` of a stock
  dividend row. It needs `ge=0`.

Both are validation-layer only; the DB columns already permit these values.

## Application semantics

1. **Eligibility** — an event applies to every open lot of that symbol with
   `purchase_date <= ex_date`. Lots bought after the ex-date get nothing.

2. **All events sharing an ex-date settle off one opening quantity.** PAN has a bonus
   (`eventId` 35231197) and a cash dividend (35231241) both on ex-date 2026-05-29. Computing
   the cash on the post-bonus count overstates income by 39,744,000 VND. Snapshot the
   quantity at the start of the ex-date, settle every event in that group against it, then
   apply the quantity change once.

3. **Cost preservation** — added shares are `floor(quantity × r)`, since Vietnam does not
   trade fractions and the residue is dropped rather than paid as cash (a deliberate
   simplification). `purchase_price` is then `purchase_price × quantity_before ÷
   quantity_after`, quantized to `DECIMAL(15,6)`, which keeps
   `purchase_price × quantity` invariant. Deriving the new price from the *floored* share
   count rather than from `1+r` is what makes the invariant hold; using `1+r` directly
   would leak the rounding residue into total cost. The invariant holds up to price
   quantization only — see the testing section.

4. **Idempotency** — unique `event_id`, unique `(corporate_action_id, position_id)`, and
   `status` gating. A re-poll or a double submit cannot double-apply.

5. **Ordering** — apply strictly ascending by `ex_date`. Later events must see the share
   count produced by earlier ones (VCG lot 4 compounds 2025-06-11 then 2026-07-14).

6. **One transaction per apply** — the lot mutation, the `transactions` row, the
   application record and the status change commit together or not at all, following the
   pattern `close_position` already uses.

## Ingestion

A worker task iterates the distinct symbols in `positions` (4 today) and pages
`/history?symbol=`. Daily is ample; ex-dates are announced well ahead, and a missed run
self-heals on the next one.

Parsing is driven off `name`, never guessed from free text:

- cash — `bằng tiền\s*([\d.,]+)\s*đồng/CP`
- stock — `tỷ lệ\s*(\d+(?:[.,]\d+)?)\s*:\s*(\d+(?:[.,]\d+)?)`, ratio `= B/A`

Must survive, all observed in the held tickers' real history: `100:8`, `100:10`, `100:20`,
`100:7`, `1000:75`, decimal `100:10.5`, zero-padded `01:03`, thousands separators, and
multi-year titles (`Trả cổ tức năm 2024, 2025 bằng cổ phiếu`).

Anything that does not match is stored `status='unparsed'` with `title` intact and applied to
nothing. It surfaces for manual entry.

New events land as `pending`. Application is an explicit action, not automatic: the amount
comes from prose, and a misparse silently corrupts cost basis. With a handful of events per
year across four tickers, review costs nothing while capture stays automatic.

## API surface

All under the existing `/portfolio` router.

| method | path | purpose |
|---|---|---|
| POST | `/portfolio/corporate-actions/sync` | poll DNSE for held tickers |
| GET | `/portfolio/corporate-actions?status=&symbol=` | list, default `pending` |
| POST | `/portfolio/corporate-actions/{id}/apply` | apply to eligible lots |
| POST | `/portfolio/corporate-actions/{id}/ignore` | mark irrelevant |
| POST | `/portfolio/corporate-actions/{id}/unapply` | reverse from the application records |
| POST | `/portfolio/dividends` | manual entry, `source='manual'` |

`PortfolioSummary` gains `total_dividend_income_gross` and `total_dividend_income` (net of
recorded withholding). `total_realized_pl` becomes trading gains + net dividend income.

`_calculate_realized_pl` needs no change: it already filters `transaction_type == 'sell'`, so
dividend rows are excluded from the trading formula automatically. Dividend income is a
second aggregate over `transaction_type == 'dividend_cash'`.

`Position` schema is unchanged.

## Backfill

The eight lots are corrected through the same path as the daily poll — sync → `pending` →
review → apply — not a bespoke script, so the one-off is exercised by exactly the logic that
runs every day. Applied strictly in `ex_date` order across all lots, with the before/after
table presented for approval first.

## Testing

- **Parser** — table-driven over the real titles collected here, including `01:03`,
  `100:10.5`, `1000:75`, `3000 đồng/CP`, and multi-year titles. Unparseable input must yield
  `unparsed`, never a guess.
- **Same-ex-date regression** — PAN 2026-05-29 must yield 198,720,000, not 238,464,000. This
  guards a bug made while computing the impact table above.
- **Cost preservation invariant** — `purchase_price × quantity` constant across any stock
  event, asserted **within tolerance, not exactly**. Quantizing the price to `DECIMAL(15,6)`
  leaves a small residue: VCG lot 4 drifts 0.0018 on a cost of 983,762 across its two
  events. A tolerance of one unit in the last place of the price times the share count is
  the honest bound; exact equality would fail.
- **Eligibility** — a lot bought after `ex_date` receives nothing.
- **Rounding** — `40,820 × 0.08` must add 3,265 shares, not 3,266. Truncation, not
  round-half-even.
- **Compounding order** — VCG lot 4 across 2025-06-11 and 2026-07-14 reaches 47,611 @
  20.662494, the second event compounding off 44,085.
- **Idempotency** — a second apply is rejected; a re-sync inserts nothing.
- **Unapply** — restores `quantity` and `purchase_price` exactly and removes the transaction.
- **Golden** — the eight real lots produce exactly 307,044,000 VND gross and 35,523 shares.

Integration tests run against MySQL using the nested-transaction rollback fixture already in
`backend/tests/test_portfolio_service_mysql.py`.

## Non-goals

- **Rights issues** (`quyền mua`) — they require cash for new shares, different mechanics,
  and fall outside the three handled `name` values.
- **Dividends on closed positions** — backfill reaches open lots only. Reconstructing past
  holdings from `transactions` is materially larger work.
- **Share splits / consolidations / delistings** — absent from the observed taxonomy.
- **The forward calendar endpoint** — correctness does not need it.
- **Automatic application without review** — deliberate, given amounts come from prose.

## Known limitations

- Fractional entitlements round down and the residue is dropped; brokers sometimes pay it as
  cash.
- `tax_withheld_pct` defaults to the 5% statutory rate rather than reading actual broker
  statements, so net income is an estimate.
- Whether `ohlc_eod` is fully back-adjusted was not established. Single-day drops beyond
  Vietnam's ±7% limit appear in older PAN data (2015–2021), suggesting some ex-dates are not
  price-adjusted. It does not affect these eight lots — none has an unexplained gap since
  purchase — but if the series is inconsistently adjusted, `current_price` could double-count
  an adjustment the lot mutation already made. Worth confirming before trusting long-run
  history.
