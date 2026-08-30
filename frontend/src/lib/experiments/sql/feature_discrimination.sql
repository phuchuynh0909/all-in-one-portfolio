-- Ranks feat_* columns by how well they separate the worst trades from the best.
--
-- Expects a relation named `trades_src` in the session.
-- Parameter 1: DOUBLE[4] quantile cut points, ascending.
--
-- UNPIVOT drops NULL values and rejects INCLUDE NULLS in this dynamic
-- COLUMNS(...) form, so coverage divides by the trade count from n_total.
-- Using count(*) over the unpivoted rows would always yield 1.0.
WITH q AS (
    SELECT quantile_cont(net_return, ?::DOUBLE[]) AS cuts
    FROM trades_src
    WHERE net_return IS NOT NULL
),
bucketed AS (
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
),
n_total AS (
    SELECT count(*) AS n FROM bucketed
),
long AS (
    UNPIVOT bucketed
    ON COLUMNS('^feat_')
    INTO NAME feature VALUE value
),
agg AS (
    SELECT
        l.feature,
        count(l.value) AS n_obs,
        count(l.value)::DOUBLE / n_total.n AS coverage,
        avg(l.value) FILTER (WHERE l.outcome = '1_catastrophic_loss') AS loser_mean,
        avg(l.value) FILTER (WHERE l.outcome = '5_big_win') AS winner_mean,
        stddev_samp(l.value) AS sd
    FROM long l
    CROSS JOIN n_total
    GROUP BY l.feature, n_total.n
)
SELECT
    feature, n_obs, coverage, loser_mean, winner_mean, sd,
    (winner_mean - loser_mean) / nullif(sd, 0) AS separation
FROM agg
ORDER BY abs((winner_mean - loser_mean) / nullif(sd, 0)) DESC NULLS LAST
