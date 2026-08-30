-- Segments trades into outcome buckets by net_return quantiles.
--
-- Expects a relation named `trades_src` in the session.
-- Parameter 1: DOUBLE[4] cut points, ascending (default [0.10,0.30,0.70,0.90]).
-- DuckDB lists are 1-indexed.
WITH q AS (
    SELECT quantile_cont(net_return, ?::DOUBLE[]) AS cuts
    FROM trades_src
    WHERE net_return IS NOT NULL
)
SELECT
    t.*,
    CASE
        WHEN t.net_return <= q.cuts[1] THEN '1_catastrophic_loss'
        WHEN t.net_return <= q.cuts[2] THEN '2_medium_loss'
        WHEN t.net_return <= q.cuts[3] THEN '3_marginal'
        WHEN t.net_return <= q.cuts[4] THEN '4_medium_win'
        ELSE '5_big_win'
    END AS outcome
FROM trades_src t
CROSS JOIN q
