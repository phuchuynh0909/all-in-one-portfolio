import { useMemo, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Box, Button, Tab, Tabs } from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';

import PositionList from '../components/portfolio/PositionList';
import TransactionList from '../components/portfolio/TransactionList';
import PerformanceCharts from '../components/portfolio/PerformanceCharts';
import PositionsTable from '../components/portfolio/PositionsTable';
import PortfolioPieChart from '../components/portfolio/PortfolioPieChart';
import CurrentPortfolioPieChart from '../components/portfolio/CurrentPortfolioPieChart';
import MvfLstmPanel from '../components/portfolio/MvfLstmPanel';

import { PageContainer, PageHeader, Panel, StatRow, StatTile, Numeric, QueryState } from '../components/ui';
import { usePortfolioSummary, usePositions, useTransactions } from '../lib/portfolio/queries';
import { computePortfolioMetrics } from '../lib/portfolio/metrics';
import type { Position } from '../lib/services/portfolio';

/**
 * A position with `current_price` resolved to a number. The backend may not have
 * priced a position yet; falling back to the purchase price keeps derived P&L at
 * 0 rather than letting NaN leak into the charts and table. `notes` is narrowed
 * from `string | null` to `string | undefined` to match the child components.
 */
type PricedPosition = Omit<Position, 'notes'> & { current_price: number; notes?: string };

const TABS = ['Overview', 'Holdings', 'Manage', 'Allocation'] as const;

export default function Portfolio() {
  const [tab, setTab] = useState(0);
  const [manageTab, setManageTab] = useState(0);
  const queryClient = useQueryClient();

  const summaryQuery = usePortfolioSummary();
  const positionsQuery = usePositions();
  const transactionsQuery = useTransactions();

  const positions = positionsQuery.data ?? [];
  const metrics = useMemo(
    () => computePortfolioMetrics(positions, transactionsQuery.data),
    [positions, transactionsQuery.data],
  );

  const pricedPositions: PricedPosition[] = useMemo(
    () =>
      positions.map((p) => ({
        ...p,
        current_price: p.current_price ?? p.purchase_price,
        notes: p.notes ?? undefined,
      })),
    [positions],
  );

  /** Any mutation anywhere on the page invalidates every portfolio query. */
  const refreshAll = () => {
    queryClient.invalidateQueries({ queryKey: ['portfolio'] });
  };

  const summary = summaryQuery.data;
  const totalValue = summary?.total_value ?? metrics.totalValue;
  const totalCost = summary?.total_invested ?? metrics.totalCost;
  const unrealizedPl = summary?.total_profit_loss ?? metrics.totalUnrealizedPl;
  const unrealizedPlPct = summary?.total_profit_loss_pct ?? metrics.totalUnrealizedPlPct;
  const realizedPl = summary?.total_realized_pl ?? metrics.realizedPl;

  const isLoading = positionsQuery.isLoading || summaryQuery.isLoading;
  const isFetching = positionsQuery.isFetching || summaryQuery.isFetching;

  return (
    <PageContainer>
      <PageHeader
        title="Portfolio"
        description="Holdings, realised and unrealised P&L, and the allocation models behind them."
        actions={
          <Button
            variant="outlined"
            size="small"
            startIcon={<RefreshIcon fontSize="small" />}
            onClick={refreshAll}
            disabled={isFetching}
          >
            {isFetching ? 'Refreshing' : 'Refresh'}
          </Button>
        }
        below={
          <Tabs value={tab} onChange={(_, v) => setTab(v)}>
            {TABS.map((label) => (
              <Tab key={label} label={label} />
            ))}
          </Tabs>
        }
      />

      {/* KPIs stay visible on every tab — they are the page's headline. */}
      <Box sx={{ mb: 2.5 }}>
        <StatRow>
          <StatTile
            label="Market value"
            value={totalValue}
            format="currency"
            loading={isLoading}
            accent="primary"
            hint={`${metrics.positionCount} position${metrics.positionCount === 1 ? '' : 's'}`}
          />
          <StatTile
            label="Invested"
            value={totalCost}
            format="currency"
            loading={isLoading}
            hint="Cost basis"
          />
          <StatTile
            label="Unrealized P&L"
            value={unrealizedPl}
            format="currency"
            signed
            showSign
            loading={isLoading}
            accent={unrealizedPl >= 0 ? 'long' : 'short'}
            hint={
              <Numeric value={unrealizedPlPct} format="percent" signed showSign sx={{ fontSize: '0.6875rem' }} />
            }
          />
          <StatTile
            label="Realized P&L"
            value={realizedPl}
            format="currency"
            signed
            showSign
            loading={isLoading}
            hint="Closed positions"
          />
          <StatTile
            label="Dividends"
            value={metrics.dividendIncome}
            format="currency"
            loading={isLoading}
            hint="Cash received"
          />
          <StatTile
            label="Best / worst"
            loading={isLoading}
            hint={metrics.best ? `${metrics.best.ticker} vs ${metrics.worst?.ticker ?? '—'}` : undefined}
          >
            <Box sx={{ display: 'flex', gap: 1, alignItems: 'baseline' }}>
              <Numeric
                value={metrics.best?.unrealizedPlPct ?? null}
                format="percent"
                signed
                showSign
                sx={{ fontSize: '1.125rem', fontWeight: 600 }}
              />
              <Numeric
                value={metrics.worst?.unrealizedPlPct ?? null}
                format="percent"
                signed
                showSign
                sx={{ fontSize: '0.875rem' }}
              />
            </Box>
          </StatTile>
        </StatRow>
      </Box>

      {/* --- Overview ------------------------------------------------------ */}
      {tab === 0 && (
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <Panel title="Performance" subtitle="Contribution and P&L distribution across holdings" flush>
            <QueryState
              isLoading={isLoading}
              error={positionsQuery.error}
              isEmpty={pricedPositions.length === 0}
              onRetry={refreshAll}
              emptyTitle="No open positions"
              emptyDescription="Add a position from the Manage tab to see performance here."
            >
              <Box sx={{ p: 2 }}>
                <PerformanceCharts positions={pricedPositions} />
              </Box>
            </QueryState>
          </Panel>

          <Box
            sx={{
              display: 'grid',
              gap: 2,
              gridTemplateColumns: { xs: '1fr', lg: 'repeat(2, minmax(0, 1fr))' },
              alignItems: 'start',
            }}
          >
            <Panel title="Current allocation" subtitle="By market value">
              <CurrentPortfolioPieChart positions={pricedPositions} />
            </Panel>
            <Panel title="Sector exposure" subtitle="Holdings grouped by sector">
              <PortfolioPieChart tickers={pricedPositions.map((p) => p.ticker)} />
            </Panel>
          </Box>
        </Box>
      )}

      {/* --- Holdings ------------------------------------------------------ */}
      {tab === 1 && (
        <Panel title="Holdings" subtitle={`${pricedPositions.length} open positions`} flush>
          <QueryState
            isLoading={isLoading}
            error={positionsQuery.error}
            isEmpty={pricedPositions.length === 0}
            onRetry={refreshAll}
            emptyTitle="No open positions"
            emptyDescription="Add a position from the Manage tab."
          >
            <PositionsTable positions={pricedPositions} onPositionUpdate={refreshAll} />
          </QueryState>
        </Panel>
      )}

      {/* --- Manage -------------------------------------------------------- */}
      {tab === 2 && (
        <Panel
          title="Manage"
          subtitle="Create, edit and close positions and transactions"
          flush
          actions={
            <Tabs value={manageTab} onChange={(_, v) => setManageTab(v)} sx={{ borderBottom: 0, minHeight: 32 }}>
              <Tab label="Positions" sx={{ minHeight: 32 }} />
              <Tab label="Transactions" sx={{ minHeight: 32 }} />
            </Tabs>
          }
        >
          <Box sx={{ p: 2 }}>
            {manageTab === 0 ? <PositionList onDataChanged={refreshAll} /> : <TransactionList />}
          </Box>
        </Panel>
      )}

      {/* --- Allocation ---------------------------------------------------- */}
      {tab === 3 && (
        <Panel
          title="Optimizer"
          subtitle="Forward-looking weights — what to hold, not what is held"
          flush
        >
          <Box sx={{ p: 2 }}>
            <MvfLstmPanel />
          </Box>
        </Panel>
      )}
    </PageContainer>
  );
}
