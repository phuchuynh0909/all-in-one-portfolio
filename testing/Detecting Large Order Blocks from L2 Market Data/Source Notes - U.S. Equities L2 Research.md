# Source Notes: U.S. Equities L2 Research

- **SEC Market Structure Data Downloads** — https://www.sec.gov/data-research/market-structure-data
  - The SEC publishes market-structure datasets and documents its use of MIDAS data for trade-to-order ratios and quote-lifetime distributions.
  - The downloads include metrics by security, by security and exchange, exchange summaries, quote-life distributions, conditional cancel-and-trade distributions, and historical spreads/depth. These are useful for cross-sectional calibration and diagnostic benchmarking, but they are not a replacement for event-level full-depth feed data.

- **Nasdaq TotalView** — https://www.nasdaq.com/solutions/data/equities/nasdaq-totalview
  - Nasdaq describes TotalView as full order-book depth on Nasdaq, covering every quote and order at every price level for securities *trading on Nasdaq*.
  - The feed offers displayed liquidity at that venue, not a consolidated reconstruction of all displayed and non-displayed liquidity across U.S. venues. A detector built from it therefore identifies venue-observable execution patterns, not the identity of a participant or all-market parent orders.

## Design Implication

The research target must be a **probabilistic latent event**: a sustained, unusually large buying or selling program inferred from displayed order-book updates and prints. It must not claim to identify an institution, beneficial owner, broker, or hidden/off-exchange activity. Event-level depth and trade data must be time-synchronized and sourced consistently; top-of-book snapshots or delayed retail Level 2 are insufficient for a credible reconstruction.

## Candidate Data Requirement

Use direct-feed, event-level message data with at least: add, cancel/delete, execute, replace, trade, auction/status messages; exchange timestamps; sequence numbers; price, size, side, order reference where disseminated; and a symbol/security master. Include a complementary consolidated trade/NBBO feed where available to assess off-venue activity and mark execution context.

- **Cont, Kukanov & Stoikov, “The Price Impact of Order Book Events”** — https://ideas.repec.org/p/arx/papers/1011.6402.html
  - Using NYSE TAQ data for 50 U.S. stocks, the authors report that short-interval price changes are chiefly related to best-quote order-flow imbalance; their estimated relation is linear, with a slope inversely related to market depth. This supports a detector that uses signed execution pressure together with depth-normalized order-flow imbalance, rather than raw volume alone.

- **Jurkatis, “Inferring trade directions in fast markets”** — https://www.bankofengland.co.uk/working-paper/2020/inferring-trade-directions-in-fast-markets
  - The paper reports that conventional trade-direction classification becomes less reliable as quote changes accelerate. Its quote-matching method searches for the quote matching a trade and outperforms established methods, especially with coarser timestamps. The detector should therefore use a feed-supplied aggressor flag where available; otherwise it must perform quote matching with a bounded synchronization tolerance, track confidence, and exclude ambiguous prints from primary scoring.

## Feature-Design Consequence

Combine multiple independent observables: depth-normalized signed executions, best-level and multi-level order-flow imbalance, persistence of replenishment after executions, cancellation asymmetry, queue depletion, and contemporaneous price response. No single metric, and particularly not a large displayed order alone, is sufficient evidence of a large parent order.

- **NYSE TAQ Integrated Feed** — https://www.nyse.com/market-data/historical/taq-integrated-feed
  - NYSE states that this historical feed provides a sequential, order-by-order view of events on NYSE, NYSE Arca, NYSE American, NYSE National, and NYSE Texas. It includes complete displayed depth, add/modify/delete messages, trades and corrections, imbalances, and status messages in matching-engine order. This is an appropriate venue-native source for deterministic historical reconstruction and is particularly valuable for studying displayed replenishment and cancellation behavior.

## Data-Source Decision

For the first valid prototype, use **one venue's complete, sequential, order-level feed** (for example, Nasdaq TotalView-ITCH or a NYSE TAQ Integrated Feed) and constrain conclusions to that venue. Add a consolidated trade/NBBO feed only as a contextual overlay. Do not combine venue feeds and assume global message-time ordering without a documented clock-alignment method. A second phase can form an all-venue score by computing venue-local features first and then aggregating on conservative time bars.

- **Zotikov & Antonov, “CME Iceberg Order Detection and Prediction”** — https://arxiv.org/abs/1909.09495
  - This study separates exchange-managed and participant-managed iceberg patterns. It detects the latter from limit orders that arrive shortly after trades, and conditions total-size estimation on cancellation after partial fills. Although the market is CME rather than U.S. equities, the design supports treating rapid same-price replenishment after executions as an **iceberg-like / reserve-liquidity proxy**, subject to venue-specific message semantics and validation.

## Scope Boundary: Institutional versus Iceberg-like

A detected replenishment pattern is not proof of an institutional parent order. It may reflect native reserve functionality, participant-managed slicing, multiple independent liquidity providers, market-making inventory management, or order-routing behavior. The score should therefore expose separate components: **persistent aggressive flow**, **passive absorption / replenishment**, and **displayed-block presence**. It should label the combined result as a *probable large execution program*, never as an identified institution.
