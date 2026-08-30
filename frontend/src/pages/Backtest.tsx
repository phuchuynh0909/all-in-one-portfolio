import { useState } from 'react';
import {
  Box,
  FormControl,
  FormControlLabel,
  InputLabel,
  MenuItem,
  Select,
  Switch,
  Typography,
  Button,
  Chip,
  Stack,
  Tooltip,
} from '@mui/material';
import { DataGrid, GridToolbar } from '@mui/x-data-grid';
import type {
  GridColDef,
  GridRenderCellParams,
  GridValueFormatterParams,
  GridValueGetterParams,
} from '@mui/x-data-grid';
import { useBacktest, type Trade } from '../lib/services/backtest';
import { format } from 'date-fns';
import { PageContainer, PageHeader, Panel, StatRow, StatTile, QueryState } from '../components/ui';

const STRATEGIES = [
  "Breakout TTM 005C",
  "Breakout TTM Version 2",
  "Breakout TTM V1",
  "Breakout TTM V1b",
  "Breakout TTM V2",
  "Breakout TTM V3",
  "Squeeze Breakout",
  "Dual RSI",
] as const;

const formatMetadata = (metadata: Record<string, any>) => {
  return Object.entries(metadata)
    .map(([key, value]) => `${key}: ${value}`)
    .join(', ');
};

const columns: GridColDef[] = [
  { field: 'symbol', headerName: 'Symbol', width: 100 },
  { 
    field: 'date', 
    headerName: 'Entry Date', 
    width: 120,
    valueFormatter: (params: GridValueFormatterParams) => format(new Date(params.value as string), 'dd/MM/yyyy')
  },
  { 
    field: 'close_date', 
    headerName: 'Close Date', 
    width: 120,
    valueFormatter: (params: GridValueFormatterParams) => params.value ? format(new Date(params.value as string), 'dd/MM/yyyy') : '-'
  },
  { field: 'entry_price', headerName: 'Entry Price', width: 120 },
  {
    field: 'return_pct',
    headerName: 'Return',
    width: 100,
    renderCell: (params: GridRenderCellParams) => (
      <Typography
        color={params.value >= 0 ? 'success.main' : 'error.main'}
        fontWeight="bold"
      >
        {params.value != null ? `${(params.value * 100).toFixed(2)}%` : '-'}
      </Typography>
    )
  },
  { field: 'trading_days', headerName: 'Days', width: 80 },
  { 
    field: 'y_pred_xgb', 
    headerName: 'XGB Score', 
    width: 100,
    valueFormatter: (params: GridValueFormatterParams) => (params.value as number | undefined)?.toFixed(3) ?? '-'
  },
  { 
    field: 'y_pred_lgbm', 
    headerName: 'LGBM Score', 
    width: 100,
    valueFormatter: (params: GridValueFormatterParams) => (params.value as number | undefined)?.toFixed(3) ?? '-'
  },
  {
    field: 'y_pred_catboost',
    headerName: 'CatBoost Score',
    width: 120,
    valueFormatter: (params: GridValueFormatterParams) => (params.value as number | undefined)?.toFixed(3) ?? '-'
  },
  {
    field: 'y_pred_ensemble',
    headerName: 'Ensemble Score',
    width: 120,
    renderCell: (params: GridRenderCellParams) => {
      const v = params.value as number | undefined | null;
      if (v == null) return <Typography variant="body2">-</Typography>;
      const color = v >= 0.6 ? 'success.main' : v >= 0.4 ? 'warning.main' : 'error.main';
      return <Typography variant="body2" color={color} fontWeight="bold">{v.toFixed(3)}</Typography>;
    }
  },
  {
    field: 'msr_rank_10',
    headerName: 'MSR Rank %',
    width: 100,
    valueFormatter: (params: GridValueFormatterParams) => {
      const v = params.value as number | undefined;
      return v != null ? `${(v * 100).toFixed(1)}%` : '-';
    }
  },
  {
    field: 'risk_regime',
    headerName: 'Symbol Regime',
    width: 120,
    renderCell: (params: GridRenderCellParams) => {
      const v = params.value as boolean | null | undefined;
      if (v == null) return <Typography variant="body2" color="text.disabled">—</Typography>;
      return (
        <Tooltip title={v ? 'Symbol GKYZ: risk-on (high-vol)' : 'Symbol GKYZ: risk-off (low-vol)'}>
          <Chip
            label={v ? 'Risk-On' : 'Risk-Off'}
            size="small"
            sx={{
              bgcolor: v ? 'market.shortSubtle' : 'market.longSubtle',
              color: v ? 'market.short' : 'market.long',
              border: 1,
              borderColor: v ? 'market.short' : 'market.long',
              fontWeight: 600,
              fontSize: '0.7rem',
            }}
          />
        </Tooltip>
      );
    },
  },
  {
    field: 'market_risk_regime',
    headerName: 'Market Regime',
    width: 125,
    renderCell: (params: GridRenderCellParams) => {
      const v = params.value as boolean | null | undefined;
      if (v == null) return <Typography variant="body2" color="text.disabled">—</Typography>;
      return (
        <Tooltip title={v ? 'VNINDEX GKYZ: risk-on (high-vol)' : 'VNINDEX GKYZ: risk-off (low-vol)'}>
          <Chip
            label={v ? 'Risk-On' : 'Risk-Off'}
            size="small"
            sx={{
              bgcolor: v ? 'market.shortSubtle' : 'market.longSubtle',
              color: v ? 'market.short' : 'market.long',
              border: 1,
              borderColor: v ? 'market.short' : 'market.long',
              fontWeight: 600,
              fontSize: '0.7rem',
            }}
          />
        </Tooltip>
      );
    },
  },
  {
    field: 'breadth_regime',
    headerName: 'Breadth Regime',
    width: 130,
    renderCell: (params: GridRenderCellParams) => {
      const v = params.value as boolean | null | undefined;
      if (v == null) return <Typography variant="body2" color="text.disabled">—</Typography>;
      return (
        <Tooltip title={v ? 'McClellan Summation > SMA20 (bullish breadth)' : 'McClellan Summation < SMA20 (bearish breadth)'}>
          <Chip
            label={v ? 'Bullish' : 'Bearish'}
            size="small"
            sx={{
              bgcolor: v ? 'market.longSubtle' : 'market.shortSubtle',
              color: v ? 'market.long' : 'market.short',
              border: 1,
              borderColor: v ? 'market.long' : 'market.short',
              fontWeight: 600,
              fontSize: '0.7rem',
            }}
          />
        </Tooltip>
      );
    },
  },
  {
    field: 'metadata',
    headerName: 'Parameters',
    width: 300,
    valueGetter: (params: GridValueGetterParams) => {
      const meta = params.value as Record<string, any> | null | undefined;
      if (!meta || typeof meta !== 'object') return '';
      return formatMetadata(meta);
    },
  },
];

export default function BacktestPage() {
  const [strategy, setStrategy] = useState<typeof STRATEGIES[number]>(STRATEGIES[0]);
  const [applyML, setApplyML] = useState(false);
  
  interface BacktestData {
    execution_time: {
      total_seconds: number;
      data_loading_seconds: number;
      strategy_seconds: number;
      feature_building_seconds: number;
      prediction_seconds: number;
    };
    open_trades: Trade[];
    closed_trades: Trade[];
  }

  const { 
    data: rawData,
    isLoading: loading,
    error,
    refetch
  } = useBacktest({
    strategy,
    start_date: (() => {
      const now = new Date();
      const last2Year = new Date(now.getFullYear() - 2, now.getMonth(), now.getDate());
      return format(last2Year, 'yyyy-MM-dd');
    })(),
    apply_ml: applyML
  });

  const handleStrategyChange = (newStrategy: typeof STRATEGIES[number]) => {
    setStrategy(newStrategy);
  };

  const addIdAndDaysToTrades = (trades: Trade[], isOpenTrades: boolean) => 
    trades.map((trade, index) => {
      const entryDate = new Date(trade.date);
      const days = isOpenTrades 
        ? Math.floor((new Date().getTime() - entryDate.getTime()) / (1000 * 60 * 60 * 24))
        : trade.trading_days;

      return {
        ...trade,
        id: `${trade.symbol}-${trade.date}-${index}`,
        trading_days: days
      };
    });

  const data = rawData as BacktestData | undefined;
  const hasResults =
    !!data &&
    typeof data === 'object' &&
    'execution_time' in data &&
    Array.isArray(data.open_trades) &&
    Array.isArray(data.closed_trades);

  /** Toolbar + sorting config shared by both trade grids. */
  const gridProps = {
    columns,
    disableSelectionOnClick: true,
    hideFooter: true,
    autoHeight: true,
    components: { Toolbar: GridToolbar },
    componentsProps: {
      toolbar: { showQuickFilter: true, quickFilterProps: { debounceMs: 500 } },
    },
    sx: {
      border: 0,
      '& .MuiDataGrid-toolbarContainer': {
        p: 1.5,
        borderBottom: 1,
        borderColor: 'line.subtle',
      },
    },
  } as const;

  const timing = data?.execution_time;

  return (
    <PageContainer>
      <PageHeader
        title="Backtest"
        description="Run a strategy over historical data and inspect the resulting open and closed trades."
        actions={
          <Button
            variant="outlined"
            size="small"
            onClick={() => refetch()}
            disabled={loading}
          >
            {loading ? 'Running…' : 'Re-run'}
          </Button>
        }
      />

      <Panel dense sx={{ mb: 2.5 }}>
        <Stack direction="row" spacing={3} alignItems="center" flexWrap="wrap" useFlexGap>
          <FormControl sx={{ minWidth: 280 }} size="small">
            <InputLabel>Strategy</InputLabel>
            <Select
              value={strategy}
              label="Strategy"
              onChange={(e) => handleStrategyChange(e.target.value as typeof STRATEGIES[number])}
              disabled={loading}
            >
              {STRATEGIES.map((s) => (
                <MenuItem key={s} value={s}>
                  {s}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
          <FormControlLabel
            control={
              <Switch
                checked={applyML}
                onChange={(e) => setApplyML(e.target.checked)}
                disabled={loading}
              />
            }
            label="Apply ML predictions"
          />
        </Stack>
      </Panel>

      <QueryState
        isLoading={loading}
        error={error}
        isEmpty={!loading && !error && !hasResults}
        onRetry={() => refetch()}
        loadingLabel="Running backtest"
        emptyTitle="No results"
        emptyDescription="Pick a strategy above and re-run to produce trades."
      >
        {hasResults && data && timing && (
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            <StatRow min={140}>
              <StatTile
                label="Total"
                value={timing.total_seconds}
                decimals={2}
                accent="primary"
                hint="seconds"
              />
              <StatTile label="Data loading" value={timing.data_loading_seconds} decimals={2} hint="seconds" />
              <StatTile label="Strategy" value={timing.strategy_seconds} decimals={2} hint="seconds" />
              <StatTile label="Features" value={timing.feature_building_seconds} decimals={2} hint="seconds" />
              <StatTile label="Predictions" value={timing.prediction_seconds} decimals={2} hint="seconds" />
              <StatTile
                label="Trades"
                value={data.open_trades.length + data.closed_trades.length}
                decimals={0}
                hint={`${data.open_trades.length} open · ${data.closed_trades.length} closed`}
              />
            </StatRow>

            <Panel title="Open trades" subtitle={`${data.open_trades.length} positions still running`} flush>
              <DataGrid<Trade>
                {...gridProps}
                rows={addIdAndDaysToTrades(data.open_trades, true)}
                initialState={{ sorting: { sortModel: [{ field: 'date', sort: 'desc' }] } }}
              />
            </Panel>

            <Panel title="Closed trades" subtitle={`${data.closed_trades.length} completed round trips`} flush>
              <DataGrid<Trade>
                {...gridProps}
                rows={addIdAndDaysToTrades(data.closed_trades, false)}
                initialState={{ sorting: { sortModel: [{ field: 'close_date', sort: 'desc' }] } }}
              />
            </Panel>
          </Box>
        )}
      </QueryState>
    </PageContainer>
  );
}
