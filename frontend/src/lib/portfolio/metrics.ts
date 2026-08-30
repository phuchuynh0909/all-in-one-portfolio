import type { Position, Transaction } from '../services/portfolio';

export interface PositionMetrics extends Position {
  /** Falls back to purchase price when no quote is available. */
  markPrice: number;
  /** True when markPrice is a fallback, not a live quote. */
  isStale: boolean;
  costBasis: number;
  marketValue: number;
  unrealizedPl: number;
  unrealizedPlPct: number;
  /** Share of total portfolio market value, 0–100. */
  weightPct: number;
}

export interface PortfolioMetrics {
  positions: PositionMetrics[];
  totalCost: number;
  totalValue: number;
  totalUnrealizedPl: number;
  totalUnrealizedPlPct: number;
  realizedPl: number;
  dividendIncome: number;
  positionCount: number;
  /** Positions with no live quote — the numbers above are partly estimates. */
  staleCount: number;
  winners: number;
  losers: number;
  best: PositionMetrics | null;
  worst: PositionMetrics | null;
}

/**
 * Derives every portfolio number the UI shows from raw positions.
 * Kept here rather than in components so the dashboard and the portfolio
 * page can never disagree about what "unrealized P&L" means.
 */
export function computePortfolioMetrics(
  positions: Position[] = [],
  transactions: Transaction[] = [],
): PortfolioMetrics {
  const enriched = positions.map((p) => {
    const hasQuote = p.current_price != null && p.current_price > 0;
    const markPrice = hasQuote ? p.current_price! : p.purchase_price;
    const costBasis = p.quantity * p.purchase_price;
    const marketValue = p.quantity * markPrice;
    const unrealizedPl = marketValue - costBasis;
    return {
      ...p,
      markPrice,
      isStale: !hasQuote,
      costBasis,
      marketValue,
      unrealizedPl,
      unrealizedPlPct: costBasis === 0 ? 0 : (unrealizedPl / costBasis) * 100,
      weightPct: 0,
    } satisfies PositionMetrics;
  });

  const totalValue = enriched.reduce((s, p) => s + p.marketValue, 0);
  const totalCost = enriched.reduce((s, p) => s + p.costBasis, 0);

  for (const p of enriched) {
    p.weightPct = totalValue === 0 ? 0 : (p.marketValue / totalValue) * 100;
  }

  const sorted = [...enriched].sort((a, b) => b.unrealizedPlPct - a.unrealizedPlPct);
  const totalUnrealizedPl = totalValue - totalCost;

  // Sells realise P&L against the price recorded on the transaction.
  const realizedPl = transactions
    .filter((t) => t.transaction_type === 'sell')
    .reduce((sum, t) => {
      const proceeds = t.quantity * t.price - (t.fees ?? 0);
      const cost = t.close_price != null ? t.quantity * t.close_price : 0;
      return sum + (proceeds - cost);
    }, 0);

  const dividendIncome = transactions
    .filter((t) => t.transaction_type === 'dividend_cash')
    .reduce((sum, t) => sum + t.quantity * t.price, 0);

  return {
    positions: enriched,
    totalCost,
    totalValue,
    totalUnrealizedPl,
    totalUnrealizedPlPct: totalCost === 0 ? 0 : (totalUnrealizedPl / totalCost) * 100,
    realizedPl,
    dividendIncome,
    positionCount: enriched.length,
    staleCount: enriched.filter((p) => p.isStale).length,
    winners: enriched.filter((p) => p.unrealizedPl > 0).length,
    losers: enriched.filter((p) => p.unrealizedPl < 0).length,
    best: sorted[0] ?? null,
    worst: sorted.length > 1 ? sorted[sorted.length - 1] : null,
  };
}
