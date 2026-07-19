Here are the key insights from the notebook outputs.

  ---
  1. Strategy wins in BEAR markets, loses in BULL markets

  The regime win-rate table is the most counter-intuitive finding:

  ┌───────────────────────────────────┬───────┬──────────┬────────────┐
  │           Market regime           │   n   │ Win rate │ Avg return │
  ├───────────────────────────────────┼───────┼──────────┼────────────┤
  │ Below EMA50 + Below EMA200 (bear) │ 1,294 │ 44.7%    │ +9.2%      │
  ├───────────────────────────────────┼───────┼──────────┼────────────┤
  │ Above EMA50 + Below EMA200        │ 459   │ 25.7%    │ +3.6%      │
  ├───────────────────────────────────┼───────┼──────────┼────────────┤
  │ Below EMA50 + Above EMA200        │ 979   │ 21.9%    │ -0.3%      │
  ├───────────────────────────────────┼───────┼──────────┼────────────┤
  │ Above EMA50 + Above EMA200 (bull) │ 1,495 │ 19.5%    │ -0.8%      │
  └───────────────────────────────────┴───────┴──────────┴────────────┘

  The strategy is a bear-market contrarian, not a bull-market momentum play. In the strongest bull regime it loses money on
  average. This suggests WVF (the Williams VIX Fix entry signal for v3) fires most productively during oversold bounces in
  downtrends.

  ---
  2. Symbol GKYZ is the #1 predictor of catastrophic loss — and it's medium strength

  From the catastrophic-vs-others discrimination table:

  ┌───────────────┬────────────┬─────────────┬────────────────┐
  │    Feature    │ Loser mean │ Winner mean │    Cohen d     │
  ├───────────────┼────────────┼─────────────┼────────────────┤
  │ gkyz_sym_raw  │ 53.1%      │ 41.2%       │ −0.53 (medium) │
  ├───────────────┼────────────┼─────────────┼────────────────┤
  │ gkyz_sym_norm │ 0.597      │ 0.502       │ −0.23          │
  └───────────────┴────────────┴─────────────┴────────────────┘

  When a stock's own GKYZ volatility is high at entry, catastrophic loss risk jumps sharply. This is the only medium-effect
  feature in the entire dataset — everything else is small/trivial. The GKYZ filter you added to compute_signals directly
  targets this.

  ---
  3. Market GKYZ raw vs normalized tell opposite stories

  - gkyz_market_raw (general losers vs winners): Winners entered when raw market vol was higher (21.9 vs 19.5). Some market
  activity is needed for breakouts to fire.
  - gkyz_market_norm (catastrophic losers): Catastrophic losers entered when normalized market vol was higher (0.638 vs 0.559,
  d=−0.19).

  The takeaway: absolute market vol is fine (even healthy), but a relative spike in market vol (normalized high within its
  recent range) is a danger signal at entry.

  ---
  4. 105 out of 197 symbols are chronic losers

  53% of all symbols have win rate < 35% and ≥ 20 trades. The worst:

  ┌────────┬──────────┬────────────┬────────┐
  │ Symbol │ Win rate │ Avg return │ Trades │
  ├────────┼──────────┼────────────┼────────┤
  │ AGG    │ 12%      │ −3.1%      │ 33     │
  ├────────┼──────────┼────────────┼────────┤
  │ HPX    │ 12%      │ −0.7%      │ 26     │
  ├────────┼──────────┼────────────┼────────┤
  │ HUT    │ 14%      │ −0.1%      │ 22     │
  ├────────┼──────────┼────────────┼────────┤
  │ DVM    │ 20%      │ −2.6%      │ 25     │
  └────────┴──────────┴────────────┴────────┘

  A rolling symbol-win-rate blacklist (the notebook's own #1 suggestion) would eliminate a large fraction of losing trades
  purely by filtering the watchlist.

  ---
  5. Cluster 2 is the danger regime — avoid it entirely

  The KMeans clustering found 4 loser archetypes. Cluster 2 (485 trades) is the worst:

  ┌──────────────────┬──────────────────────┬─────────────┐
  │     Feature      │      Cluster 2       │ Winner mean │
  ├──────────────────┼──────────────────────┼─────────────┤
  │ VN above EMA200  │ 0.014 (almost never) │ 0.432       │
  ├──────────────────┼──────────────────────┼─────────────┤
  │ gkyz_sym_raw     │ 59.5%                │ 45.8%       │
  ├──────────────────┼──────────────────────┼─────────────┤
  │ gkyz_market_norm │ 0.687                │ 0.637       │
  ├──────────────────┼──────────────────────┼─────────────┤
  │ VN drawdown      │ −27.1%               │ −13.1%      │
  └──────────────────┴──────────────────────┴─────────────┘

  This cluster = "bear market + high stock vol + market at stress peak". It concentrates the worst absolute losses. A composite
  filter of vnindex_above_ema200 == 0 AND gkyz_sym_norm > 0.6 would surgically exclude it.

  ---
  6. Holding period asymmetry is enormous

  - Losers median: 10 days
  - Winners median: 95 days

  Winners are held 9.5× longer. This means the ATR trailing stop fires quickly on bad trades (correct) but the exit mechanism
  works. The edge is entirely in letting winners compound, not in entry quality. The low win rate (28%) is fine as long as the
  9:1 holding ratio holds.

  ---
  This cluster = "bear market + high stock vol + market at stress peak". It concentrates the worst absolute losses. A composite
  filter of vnindex_above_ema200 == 0 AND gkyz_sym_norm > 0.6 would surgically exclude it

  Actionable priority list
  ──────────┬───────────────────────────────────────────────────────────────────────┬──────────────────────────────────────┐
  │ Priority │                                Action                                 │                Target                │
  ├──────────┼───────────────────────────────────────────────────────────────────────┼──────────────────────────────────────┤
  │ 🔴 High  │ Symbol blacklist — rolling 90-trade win rate < 30%                    │ 53% of symbols                       │
  ├──────────┼───────────────────────────────────────────────────────────────────────┼──────────────────────────────────────┤
  │ 🔴 High  │ Add gkyz_sym_norm ≤ 0.65 filter at entry                              │ Cuts catastrophic loss rate          │
  │ 🔴 High  │ Disable entries when vnindex_above_ema200 = 1 AND vnindex_above_ema50 │ Bull regime has −0.8% avg            │
  │          │  = 1                                                                  │                                      │
  ├──────────┼───────────────────────────────────────────────────────────────────────┼──────────────────────────────────────┤
  │ 🟡 Med   │ HMM gate: only enter in Risk-On regime                                │ d=0.18, but only 37% of time is      │
  ├──────────┼───────────────────────────────────────────────────────────────────────┼──────────────────────────────────────┤
  │ 🔴 High  │ Symbol blacklist — rolling 90-trade win rate < 30%                    │ 53% of symbols                       │
  ├──────────┼───────────────────────────────────────────────────────────────────────┼──────────────────────────────────────┤
  │ 🔴 High  │ Add gkyz_sym_norm ≤ 0.65 filter at entry                              │ Cuts catastrophic loss rate          │
  ├──────────┼───────────────────────────────────────────────────────────────────────┼──────────────────────────────────────┤
  │ 🔴 High  │ Disable entries when vnindex_above_ema200 = 1 AND vnindex_above_ema50 │ Bull regime has −0.8% avg            │
  │          │  = 1                                                                  │                                      │
  ├──────────┼───────────────────────────────────────────────────────────────────────┼──────────────────────────────────────┤
  │ 🟡 Med   │ HMM gate: only enter in Risk-On regime                                │ d=0.18, but only 37% of time is      