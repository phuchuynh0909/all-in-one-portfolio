import { useState, useMemo } from 'react';
import {
  Box,
  Container,
  Typography,
  Paper,
  Chip,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  IconButton,
  Collapse,
  CircularProgress,
  Alert,
  Autocomplete,
  TextField,
  Grid,
  Divider,
} from '@mui/material';
import {
  KeyboardArrowDown,
  KeyboardArrowUp,
  TrendingUp,
  TrendingDown,
  ShowChart,
  AccessTime,
  Assessment,
} from '@mui/icons-material';
import BacktestChart, { type BacktestTrade } from '../components/backtest/BacktestChart';
import { useH5BacktestResults, useWatchlistSymbols, type H5Trade } from '../lib/services/backtest';

// Convert H5Trade from API to BacktestTrade format for the chart
const convertToBacktestTrade = (trade: H5Trade): BacktestTrade => ({
  id: trade.id,
  symbol: trade.symbol,
  size: trade.size,
  entryTimestamp: trade.entry_timestamp,
  avgEntryPrice: trade.avg_entry_price,
  entryFees: trade.entry_fees,
  exitTimestamp: trade.exit_timestamp,
  avgExitPrice: trade.avg_exit_price,
  exitFees: trade.exit_fees,
  pnl: trade.pnl,
  return: trade.return_pct / 100, // Convert from percentage back to decimal
  direction: trade.direction,
  status: trade.status,
});

export default function BacktestVisualization() {
  const [selectedSymbol, setSelectedSymbol] = useState<string>('VCG');
  const [expandedRow, setExpandedRow] = useState<number | null>(null);

  // Fetch available symbols from watchlist
  const { data: symbolsData } = useWatchlistSymbols();
  const symbols = symbolsData || [];

  // Fetch backtest results for selected symbol
  const { data, isLoading, error } = useH5BacktestResults(selectedSymbol);

  // Convert trades to chart format
  const filteredTrades: BacktestTrade[] = useMemo(() => {
    if (!data?.trades) return [];
    return data.trades.map(convertToBacktestTrade);
  }, [data?.trades]);

  // Get stats directly from API response (now a single object)
  const selectedStats = data?.stats;

  // Calculate summary stats from trades (fallback if stats not available)
  const summaryStats = useMemo(() => {
    const totalPnL = filteredTrades.reduce((sum, t) => sum + t.pnl, 0);
    
    if (selectedStats) {
      return {
        totalPnL,
        totalReturn: selectedStats.total_return_pct || 0,
        benchmarkReturn: selectedStats.benchmark_return_pct,
        winRate: selectedStats.win_rate_pct || 0,
        sharpeRatio: selectedStats.sharpe_ratio,
        sortinoRatio: selectedStats.sortino_ratio,
        calmarRatio: selectedStats.calmar_ratio,
        omegaRatio: selectedStats.omega_ratio,
        maxDrawdown: selectedStats.max_drawdown_pct,
        maxDrawdownDuration: selectedStats.max_drawdown_duration,
        totalTrades: selectedStats.total_trades,
        totalClosedTrades: selectedStats.total_closed_trades,
        totalOpenTrades: selectedStats.total_open_trades,
        openTradePnl: selectedStats.open_trade_pnl,
        bestTrade: selectedStats.best_trade_pct,
        worstTrade: selectedStats.worst_trade_pct,
        avgWinningTrade: selectedStats.avg_winning_trade_pct,
        avgLosingTrade: selectedStats.avg_losing_trade_pct,
        avgWinningTradeDuration: selectedStats.avg_winning_trade_duration,
        avgLosingTradeDuration: selectedStats.avg_losing_trade_duration,
        startValue: selectedStats.start_value,
        endValue: selectedStats.end_value,
        period: selectedStats.period,
        maxGrossExposure: selectedStats.max_gross_exposure_pct,
        totalFeesPaid: selectedStats.total_fees_paid,
        profitFactor: selectedStats.profit_factor,
        expectancy: selectedStats.expectancy,
      };
    }

    if (filteredTrades.length === 0) {
      return { totalPnL: 0, totalReturn: 0, winRate: 0 };
    }

    const totalReturn = filteredTrades.reduce((sum, t) => sum + t.return, 0) * 100;
    const wins = filteredTrades.filter((t) => t.pnl > 0).length;
    const winRate = (wins / filteredTrades.length) * 100;

    return { totalPnL, totalReturn, winRate };
  }, [filteredTrades, selectedStats]);

  const formatDate = (dateStr: string) => {
    const date = new Date(dateStr);
    return date.toLocaleDateString('en-GB', {
      day: '2-digit',
      month: 'short',
      year: 'numeric',
    });
  };

  const getHoldingDays = (entry: string, exit: string) => {
    const entryDate = new Date(entry);
    const exitDate = new Date(exit);
    return Math.round((exitDate.getTime() - entryDate.getTime()) / (1000 * 60 * 60 * 24));
  };

  return (
    <Container maxWidth="xl" sx={{ py: 4 }}>
      {/* Header */}
      <Box sx={{ mb: 4 }}>
        <Typography
          variant="h4"
          sx={{
            fontWeight: 700,
            background: 'linear-gradient(135deg, #6366f1 0%, #8b5cf6 50%, #a855f7 100%)',
            backgroundClip: 'text',
            WebkitBackgroundClip: 'text',
            WebkitTextFillColor: 'transparent',
            mb: 1,
          }}
        >
          Backtest Visualization
        </Typography>
        <Typography variant="body2" sx={{ color: 'text.secondary' }}>
          Interactive analysis of trading strategy performance
          {symbols.length > 0 && ` • ${symbols.length} symbols available`}
        </Typography>
      </Box>

      {/* Loading State */}
      {isLoading && (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 8 }}>
          <CircularProgress />
        </Box>
      )}

      {/* Error State */}
      {error && (
        <Alert severity="error" sx={{ mb: 3 }}>
          {error instanceof Error ? error.message : 'Failed to load backtest results'}
        </Alert>
      )}

      {/* Main Content */}
      {data && !isLoading && (
        <>
          {/* Symbol Selector & Risk Metrics */}
          <Paper
            sx={{
              p: 2,
              mb: 3,
              background: 'linear-gradient(135deg, rgba(30, 30, 46, 0.9) 0%, rgba(30, 30, 40, 0.95) 100%)',
              border: '1px solid rgba(99, 102, 241, 0.2)',
              borderRadius: 2,
            }}
          >
            <Stack 
              direction="row" 
              alignItems="center" 
              justifyContent="space-between"
              flexWrap="wrap"
              gap={2}
            >
              {/* Symbol Selector */}
              <Autocomplete<string, false, true, false>
                value={selectedSymbol}
                onChange={(_event, newValue) => {
                  if (newValue) setSelectedSymbol(newValue);
                }}
                options={symbols}
                sx={{
                  minWidth: 150,
                  '& .MuiOutlinedInput-notchedOutline': {
                    borderColor: 'rgba(99, 102, 241, 0.3)',
                  },
                }}
                renderInput={(params) => (
                  <TextField {...params} label="Symbol" size="small" />
                )}
                disableClearable
                autoHighlight
              />

              {/* Risk Ratios (metrics not shown in chart) */}
              {selectedStats && (
                <Stack direction="row" spacing={3} flexWrap="wrap" sx={{ gap: 2 }}>
                  {summaryStats.sharpeRatio != null && (
                    <Box sx={{ textAlign: 'center', minWidth: 60 }}>
                      <Typography variant="caption" sx={{ color: '#6b7280', display: 'block', fontSize: '0.65rem', textTransform: 'uppercase' }}>Sharpe</Typography>
                      <Typography variant="body1" sx={{ 
                        fontFamily: 'monospace', fontWeight: 700,
                        color: summaryStats.sharpeRatio >= 1 ? '#22c55e' : summaryStats.sharpeRatio >= 0.5 ? '#f59e0b' : '#ef4444'
                      }}>
                        {summaryStats.sharpeRatio.toFixed(2)}
                      </Typography>
                    </Box>
                  )}
                  {summaryStats.sortinoRatio != null && (
                    <Box sx={{ textAlign: 'center', minWidth: 60 }}>
                      <Typography variant="caption" sx={{ color: '#6b7280', display: 'block', fontSize: '0.65rem', textTransform: 'uppercase' }}>Sortino</Typography>
                      <Typography variant="body1" sx={{ fontFamily: 'monospace', fontWeight: 700 }}>{summaryStats.sortinoRatio.toFixed(2)}</Typography>
                    </Box>
                  )}
                  {summaryStats.calmarRatio != null && (
                    <Box sx={{ textAlign: 'center', minWidth: 60 }}>
                      <Typography variant="caption" sx={{ color: '#6b7280', display: 'block', fontSize: '0.65rem', textTransform: 'uppercase' }}>Calmar</Typography>
                      <Typography variant="body1" sx={{ fontFamily: 'monospace', fontWeight: 700 }}>{summaryStats.calmarRatio.toFixed(2)}</Typography>
                    </Box>
                  )}
                  {summaryStats.omegaRatio != null && (
                    <Box sx={{ textAlign: 'center', minWidth: 60 }}>
                      <Typography variant="caption" sx={{ color: '#6b7280', display: 'block', fontSize: '0.65rem', textTransform: 'uppercase' }}>Omega</Typography>
                      <Typography variant="body1" sx={{ fontFamily: 'monospace', fontWeight: 700 }}>{summaryStats.omegaRatio.toFixed(2)}</Typography>
                    </Box>
                  )}
                  {summaryStats.profitFactor != null && (
                    <Box sx={{ textAlign: 'center', minWidth: 60 }}>
                      <Typography variant="caption" sx={{ color: '#6b7280', display: 'block', fontSize: '0.65rem', textTransform: 'uppercase' }}>Profit Factor</Typography>
                      <Typography variant="body1" sx={{ 
                        fontFamily: 'monospace', fontWeight: 700,
                        color: summaryStats.profitFactor >= 1.5 ? '#22c55e' : summaryStats.profitFactor >= 1 ? '#f59e0b' : '#ef4444'
                      }}>
                        {summaryStats.profitFactor.toFixed(2)}
                      </Typography>
                    </Box>
                  )}
                  {summaryStats.expectancy != null && (
                    <Box sx={{ textAlign: 'center', minWidth: 60 }}>
                      <Typography variant="caption" sx={{ color: '#6b7280', display: 'block', fontSize: '0.65rem', textTransform: 'uppercase' }}>Expectancy</Typography>
                      <Typography variant="body1" sx={{ fontFamily: 'monospace', fontWeight: 700 }}>{summaryStats.expectancy.toFixed(2)}</Typography>
                    </Box>
                  )}
                </Stack>
              )}
            </Stack>
          </Paper>

          {/* Chart */}
          {filteredTrades.length > 0 && (
            <Paper
              sx={{
                p: 2,
                mb: 3,
                background: 'transparent',
                border: 'none',
                boxShadow: 'none',
              }}
            >
              <BacktestChart symbol={selectedSymbol} trades={filteredTrades} initialCash={100} />
            </Paper>
          )}

          {/* Trades Table */}
          <Paper
            sx={{
              background: 'linear-gradient(135deg, rgba(30, 30, 46, 0.9) 0%, rgba(30, 30, 40, 0.95) 100%)',
              border: '1px solid rgba(99, 102, 241, 0.2)',
              borderRadius: 2,
              overflow: 'hidden',
            }}
          >
            <Box sx={{ p: 2, borderBottom: '1px solid rgba(99, 102, 241, 0.2)' }}>
              <Typography variant="h6" sx={{ fontWeight: 600 }}>
                Trade History ({filteredTrades.length} trades)
              </Typography>
            </Box>
            <TableContainer sx={{ maxHeight: 500 }}>
              <Table size="small" stickyHeader>
                <TableHead>
                  <TableRow
                    sx={{
                      '& th': {
                        fontWeight: 600,
                        color: '#9ca3af',
                        bgcolor: '#1a1a2e',
                        borderBottom: '1px solid rgba(99, 102, 241, 0.2)',
                        fontSize: 12,
                        textTransform: 'uppercase',
                        letterSpacing: 0.5,
                      },
                    }}
                  >
                    <TableCell width={50} />
                    <TableCell>ID</TableCell>
                    <TableCell>Direction</TableCell>
                    <TableCell align="right">Size</TableCell>
                    <TableCell>Entry</TableCell>
                    <TableCell align="right">Entry Price</TableCell>
                    <TableCell>Exit</TableCell>
                    <TableCell align="right">Exit Price</TableCell>
                    <TableCell align="right">P&L</TableCell>
                    <TableCell align="right">Return</TableCell>
                    <TableCell align="right">Days</TableCell>
                    <TableCell>Status</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {filteredTrades.map((trade) => (
                    <>
                      <TableRow
                        key={trade.id}
                        sx={{
                          '&:hover': { bgcolor: 'rgba(99, 102, 241, 0.05)' },
                          '& td': { borderBottom: '1px solid rgba(42, 46, 57, 0.5)' },
                        }}
                      >
                        <TableCell>
                          <IconButton
                            size="small"
                            onClick={() => setExpandedRow(expandedRow === trade.id ? null : trade.id)}
                          >
                            {expandedRow === trade.id ? (
                              <KeyboardArrowUp fontSize="small" />
                            ) : (
                              <KeyboardArrowDown fontSize="small" />
                            )}
                          </IconButton>
                        </TableCell>
                        <TableCell sx={{ fontFamily: 'monospace', color: '#9ca3af' }}>
                          #{trade.id}
                        </TableCell>
                        <TableCell>
                          <Chip
                            label={trade.direction}
                            size="small"
                            sx={{
                              bgcolor:
                                trade.direction === 'Long'
                                  ? 'rgba(34, 197, 94, 0.15)'
                                  : 'rgba(239, 68, 68, 0.15)',
                              color: trade.direction === 'Long' ? '#22c55e' : '#ef4444',
                              fontWeight: 500,
                              fontSize: 11,
                            }}
                          />
                        </TableCell>
                        <TableCell align="right" sx={{ fontFamily: 'monospace' }}>
                          {trade.size.toFixed(2)}
                        </TableCell>
                        <TableCell sx={{ color: '#9ca3af' }}>
                          {formatDate(trade.entryTimestamp)}
                        </TableCell>
                        <TableCell align="right" sx={{ fontFamily: 'monospace' }}>
                          {trade.avgEntryPrice.toFixed(2)}
                        </TableCell>
                        <TableCell sx={{ color: '#9ca3af' }}>
                          {formatDate(trade.exitTimestamp)}
                        </TableCell>
                        <TableCell align="right" sx={{ fontFamily: 'monospace' }}>
                          {trade.avgExitPrice.toFixed(2)}
                        </TableCell>
                        <TableCell
                          align="right"
                          sx={{
                            fontFamily: 'monospace',
                            fontWeight: 600,
                            color: trade.pnl >= 0 ? '#22c55e' : '#ef4444',
                          }}
                        >
                          {trade.pnl >= 0 ? '+' : ''}
                          {trade.pnl.toFixed(2)}
                        </TableCell>
                        <TableCell
                          align="right"
                          sx={{
                            fontFamily: 'monospace',
                            fontWeight: 600,
                            color: trade.return >= 0 ? '#22c55e' : '#ef4444',
                          }}
                        >
                          {trade.return >= 0 ? '+' : ''}
                          {(trade.return * 100).toFixed(2)}%
                        </TableCell>
                        <TableCell align="right" sx={{ fontFamily: 'monospace', color: '#9ca3af' }}>
                          {getHoldingDays(trade.entryTimestamp, trade.exitTimestamp)}
                        </TableCell>
                        <TableCell>
                          <Chip
                            label={trade.status}
                            size="small"
                            sx={{
                              bgcolor:
                                trade.status === 'Closed'
                                  ? 'rgba(99, 102, 241, 0.15)'
                                  : 'rgba(245, 158, 11, 0.15)',
                              color: trade.status === 'Closed' ? '#6366f1' : '#f59e0b',
                              fontWeight: 500,
                              fontSize: 11,
                            }}
                          />
                        </TableCell>
                      </TableRow>
                      <TableRow>
                        <TableCell style={{ paddingBottom: 0, paddingTop: 0 }} colSpan={12}>
                          <Collapse in={expandedRow === trade.id} timeout="auto" unmountOnExit>
                            <Box sx={{ py: 2, px: 3 }}>
                              <Typography
                                variant="subtitle2"
                                sx={{ color: '#6366f1', mb: 1, fontWeight: 600 }}
                              >
                                Trade Details
                              </Typography>
                              <Stack direction="row" spacing={4}>
                                <Box>
                                  <Typography variant="caption" sx={{ color: '#6b7280' }}>
                                    Entry Fees
                                  </Typography>
                                  <Typography sx={{ fontFamily: 'monospace' }}>
                                    {trade.entryFees.toFixed(2)}
                                  </Typography>
                                </Box>
                                <Box>
                                  <Typography variant="caption" sx={{ color: '#6b7280' }}>
                                    Exit Fees
                                  </Typography>
                                  <Typography sx={{ fontFamily: 'monospace' }}>
                                    {trade.exitFees.toFixed(2)}
                                  </Typography>
                                </Box>
                                <Box>
                                  <Typography variant="caption" sx={{ color: '#6b7280' }}>
                                    Net P&L
                                  </Typography>
                                  <Typography
                                    sx={{
                                      fontFamily: 'monospace',
                                      color: trade.pnl >= 0 ? '#22c55e' : '#ef4444',
                                    }}
                                  >
                                    {(trade.pnl - trade.entryFees - trade.exitFees).toFixed(2)}
                                  </Typography>
                                </Box>
                                <Box>
                                  <Typography variant="caption" sx={{ color: '#6b7280' }}>
                                    Position Value
                                  </Typography>
                                  <Typography sx={{ fontFamily: 'monospace' }}>
                                    {(trade.size * trade.avgEntryPrice).toFixed(2)}
                                  </Typography>
                                </Box>
                              </Stack>
                            </Box>
                          </Collapse>
                        </TableCell>
                      </TableRow>
                    </>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          </Paper>
        </>
      )}
    </Container>
  );
}
