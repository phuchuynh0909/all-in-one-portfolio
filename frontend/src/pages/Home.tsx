import { useMemo } from 'react';
import { Link as RouterLink } from 'react-router-dom';
import { Box, Button, Stack, Typography } from '@mui/material';
import ArrowForwardIcon from '@mui/icons-material/ArrowForward';

import MarketBreadthChart from '../components/market/MarketBreadthChart';
import {
  PageContainer,
  PageHeader,
  Panel,
  StatRow,
  StatTile,
  Numeric,
  QueryState,
} from '../components/ui';
import { usePortfolioSummary, usePositions, useTransactions } from '../lib/portfolio/queries';
import { computePortfolioMetrics } from '../lib/portfolio/metrics';
import { formatQuantity, formatNumber } from '../lib/format';
import { navGroups } from '../components/layout/navigation';

/** The three domains this terminal is built around, surfaced as entry points. */
const capabilityGroups = ['quant', 'agents', 'market'] as const;

export default function Home() {
  const summaryQuery = usePortfolioSummary();
  const positionsQuery = usePositions();
  const transactionsQuery = useTransactions();

  const metrics = useMemo(
    () => computePortfolioMetrics(positionsQuery.data, transactionsQuery.data),
    [positionsQuery.data, transactionsQuery.data],
  );

  const summary = summaryQuery.data;
  const totalValue = summary?.total_value ?? metrics.totalValue;
  const totalCost = summary?.total_invested ?? metrics.totalCost;
  const unrealizedPl = summary?.total_profit_loss ?? metrics.totalUnrealizedPl;
  const unrealizedPlPct = summary?.total_profit_loss_pct ?? metrics.totalUnrealizedPlPct;
  const realizedPl = summary?.total_realized_pl ?? metrics.realizedPl;

  const isLoading = positionsQuery.isLoading || summaryQuery.isLoading;
  const holdings = useMemo(
    () => [...metrics.positions].sort((a, b) => b.marketValue - a.marketValue).slice(0, 6),
    [metrics.positions],
  );

  return (
    <PageContainer>
      <PageHeader
        title="Dashboard"
        description="Portfolio state, market breadth and the research tooling behind them."
        actions={
          <Button
            component={RouterLink}
            to="/portfolio"
            variant="outlined"
            size="small"
            endIcon={<ArrowForwardIcon fontSize="small" />}
          >
            Open portfolio
          </Button>
        }
      />

      {/* --- Portfolio KPIs ------------------------------------------------ */}
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
            label="Unrealized P&L"
            value={unrealizedPl}
            format="currency"
            signed
            showSign
            loading={isLoading}
            accent={unrealizedPl >= 0 ? 'long' : 'short'}
            hint={
              <Numeric
                value={unrealizedPlPct}
                format="percent"
                signed
                showSign
                sx={{ fontSize: '0.6875rem' }}
              />
            }
          />
          <StatTile
            label="Cost basis"
            value={totalCost}
            format="currency"
            loading={isLoading}
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
            label="Win / loss"
            loading={isLoading}
            hint={metrics.staleCount > 0 ? `${metrics.staleCount} without a live quote` : 'All quotes live'}
          >
            <Stack direction="row" spacing={0.75} alignItems="baseline">
              <Numeric
                value={metrics.winners}
                decimals={0}
                sx={{ fontSize: '1.375rem', fontWeight: 600, color: 'market.long' }}
              />
              <Typography sx={{ color: 'text.tertiary' }}>/</Typography>
              <Numeric
                value={metrics.losers}
                decimals={0}
                sx={{ fontSize: '1.375rem', fontWeight: 600, color: 'market.short' }}
              />
            </Stack>
          </StatTile>
        </StatRow>
      </Box>

      {/* --- Breadth + holdings ------------------------------------------- */}
      <Box
        sx={{
          display: 'grid',
          gap: 2,
          gridTemplateColumns: { xs: '1fr', lg: 'minmax(0, 2fr) minmax(0, 1fr)' },
          mb: 2.5,
        }}
      >
        <Panel title="Market breadth" subtitle="Advance/decline participation across the universe" flush>
          <Box sx={{ p: 1.5 }}>
            <MarketBreadthChart />
          </Box>
        </Panel>

        <Panel
          title="Largest holdings"
          subtitle="By market value"
          actions={
            <Button component={RouterLink} to="/portfolio" size="small" variant="text">
              All
            </Button>
          }
          flush
        >
          <QueryState
            isLoading={isLoading}
            error={positionsQuery.error}
            isEmpty={holdings.length === 0}
            onRetry={() => positionsQuery.refetch()}
            emptyTitle="No positions"
            emptyDescription="Add a position on the portfolio page to see it here."
          >
            <Box sx={{ display: 'flex', flexDirection: 'column' }}>
              {holdings.map((p) => (
                <Box
                  key={p.id}
                  sx={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    gap: 1,
                    px: 2,
                    py: 1,
                    borderBottom: 1,
                    borderColor: 'line.subtle',
                    '&:last-of-type': { borderBottom: 0 },
                    '&:hover': { bgcolor: 'action.hover' },
                  }}
                >
                  <Box sx={{ minWidth: 0 }}>
                    <Typography variant="mono" sx={{ fontWeight: 600, color: 'text.primary' }}>
                      {p.ticker}
                    </Typography>
                    <Typography variant="caption" component="div" sx={{ color: 'text.tertiary' }}>
                      {formatQuantity(p.quantity)} @ {formatNumber(p.purchase_price)}
                      {p.isStale && ' · no quote'}
                    </Typography>
                  </Box>
                  <Box sx={{ textAlign: 'right', flexShrink: 0 }}>
                    <Numeric value={p.marketValue} format="compact" sx={{ fontWeight: 600 }} />
                    <Box>
                      <Numeric
                        value={p.unrealizedPlPct}
                        format="percent"
                        signed
                        showSign
                        arrow
                        sx={{ fontSize: '0.6875rem' }}
                      />
                    </Box>
                  </Box>
                </Box>
              ))}
            </Box>
          </QueryState>
        </Panel>
      </Box>

      {/* --- Capability surface -------------------------------------------- */}
      <Box>
        <Typography variant="overline2" sx={{ mb: 1.5 }}>
          Research tooling
        </Typography>
        <Box
          sx={{
            display: 'grid',
            gap: 2,
            gridTemplateColumns: { xs: '1fr', md: 'repeat(3, minmax(0, 1fr))' },
            alignItems: 'start',
          }}
        >
          {capabilityGroups.map((groupId) => {
            const group = navGroups.find((g) => g.id === groupId);
            if (!group) return null;
            return (
              <Panel key={group.id} title={group.label} flush>
                <Box sx={{ display: 'flex', flexDirection: 'column' }}>
                  {group.items.map((item) => (
                    <Box
                      key={item.path}
                      component={RouterLink}
                      to={item.path}
                      sx={{
                        display: 'flex',
                        gap: 1.5,
                        px: 2,
                        py: 1.25,
                        textDecoration: 'none',
                        color: 'inherit',
                        borderBottom: 1,
                        borderColor: 'line.subtle',
                        '&:last-of-type': { borderBottom: 0 },
                        '&:hover': { bgcolor: 'action.hover', '& .cap-title': { color: 'primary.main' } },
                      }}
                    >
                      <Box sx={{ color: 'text.tertiary', display: 'flex', pt: '2px' }}>{item.icon}</Box>
                      <Box sx={{ minWidth: 0 }}>
                        <Typography
                          className="cap-title"
                          variant="subtitle2"
                          sx={{ color: 'text.primary', transition: 'color 120ms' }}
                        >
                          {item.label}
                        </Typography>
                        <Typography variant="caption" sx={{ color: 'text.tertiary' }} component="div">
                          {item.description}
                        </Typography>
                      </Box>
                    </Box>
                  ))}
                </Box>
              </Panel>
            );
          })}
        </Box>
      </Box>
    </PageContainer>
  );
}
