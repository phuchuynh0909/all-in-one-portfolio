import { useQuery } from '@tanstack/react-query';
import { apiGet } from '../api';
import { getPositions, getTransactions, type Position, type Transaction } from '../services/portfolio';

/** Server-computed portfolio totals — authoritative over any client-side sum. */
export interface PortfolioSummaryResponse {
  total_value: number | null;
  total_invested: number;
  total_profit_loss: number | null;
  total_profit_loss_pct: number | null;
  total_realized_pl: number;
  total_dividend_income: number;
  positions: Array<{
    id: number;
    ticker: string;
    quantity: number;
    purchase_price: number;
    purchase_date: string;
  }>;
}

export const portfolioKeys = {
  summary: ['portfolio', 'summary'] as const,
  positions: ['portfolio', 'positions'] as const,
  transactions: ['portfolio', 'transactions'] as const,
};

export function usePortfolioSummary() {
  return useQuery<PortfolioSummaryResponse>({
    queryKey: portfolioKeys.summary,
    queryFn: () => apiGet<PortfolioSummaryResponse>('/portfolio/summary'),
  });
}

export function usePositions() {
  return useQuery<Position[]>({ queryKey: portfolioKeys.positions, queryFn: getPositions });
}

export function useTransactions() {
  return useQuery<Transaction[]>({ queryKey: portfolioKeys.transactions, queryFn: getTransactions });
}
