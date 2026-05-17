import { useState } from 'react';
import {
  Box,
  Container,
  FormControl,
  FormControlLabel,
  InputLabel,
  MenuItem,
  Paper,
  Select,
  Switch,
  Typography,
  CircularProgress,
  Alert,
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
              bgcolor: v ? 'rgba(239,68,68,0.15)' : 'rgba(34,197,94,0.15)',
              color: v ? '#ef4444' : '#22c55e',
              border: `1px solid ${v ? 'rgba(239,68,68,0.4)' : 'rgba(34,197,94,0.4)'}`,
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
              bgcolor: v ? 'rgba(239,68,68,0.15)' : 'rgba(34,197,94,0.15)',
              color: v ? '#ef4444' : '#22c55e',
              border: `1px solid ${v ? 'rgba(239,68,68,0.4)' : 'rgba(34,197,94,0.4)'}`,
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
              bgcolor: v ? 'rgba(34,197,94,0.15)' : 'rgba(239,68,68,0.15)',
              color: v ? '#22c55e' : '#ef4444',
              border: `1px solid ${v ? 'rgba(34,197,94,0.4)' : 'rgba(239,68,68,0.4)'}`,
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

  return (
    <Container maxWidth="xl">
      <Box sx={{ py: 4 }}>
        <Typography variant="h4" gutterBottom>
          Backtest Results
        </Typography>

        <Paper sx={{ p: 2, mb: 3 }}>
          <Stack direction="row" spacing={3} alignItems="center">
            <FormControl sx={{ minWidth: 300 }}>
              <InputLabel>Strategy</InputLabel>
              <Select
                value={strategy}
                label="Strategy"
                onChange={(e) => handleStrategyChange(e.target.value as typeof STRATEGIES[number])}
                disabled={loading}
              >
                {STRATEGIES.map((s) => (
                  <MenuItem key={s} value={s}>{s}</MenuItem>
                ))}
              </Select>
            </FormControl>
            <FormControlLabel
              control={
                <Switch
                  checked={applyML}
                  onChange={(e) => setApplyML(e.target.checked)}
                  disabled={loading}
                  color="primary"
                />
              }
              label="Apply ML Predictions"
            />
          </Stack>
        </Paper>

        {loading && (
          <Box sx={{ display: 'flex', justifyContent: 'center', my: 4 }}>
            <CircularProgress />
          </Box>
        )}

        {error && (
          <Alert 
            severity="error" 
            sx={{ mb: 3 }}
            action={
              <Button color="inherit" size="small" onClick={() => refetch()}>
                Retry
              </Button>
            }
          >
            {error instanceof Error ? error.message : 'An error occurred'}
          </Alert>
        )}

        {rawData && typeof rawData === 'object' && 'execution_time' in rawData && 'open_trades' in rawData && 'closed_trades' in rawData && Array.isArray(rawData.open_trades) && Array.isArray(rawData.closed_trades) ? (
          <>
            <Paper sx={{ p: 2, mb: 3 }}>
              <Typography variant="h6" gutterBottom>
                Execution Time
              </Typography>
              <Stack direction="row" spacing={2}>
                <Chip 
                  label={`Total: ${(rawData as BacktestData).execution_time.total_seconds}s`}
                  color="primary"
                />
                <Chip 
                  label={`Data Loading: ${(rawData as BacktestData).execution_time.data_loading_seconds}s`}
                  variant="outlined"
                />
                <Chip 
                  label={`Strategy: ${(rawData as BacktestData).execution_time.strategy_seconds}s`}
                  variant="outlined"
                />
                <Chip 
                  label={`Features: ${(rawData as BacktestData).execution_time.feature_building_seconds}s`}
                  variant="outlined"
                />
                <Chip 
                  label={`Predictions: ${(rawData as BacktestData).execution_time.prediction_seconds}s`}
                  variant="outlined"
                />
              </Stack>
            </Paper>

            <Paper sx={{ p: 2, mb: 3 }}>
              <Typography variant="h6" gutterBottom>
                Open Trades ({(rawData as BacktestData).open_trades.length})
              </Typography>
              <Box sx={{ width: '100%' }}>
                <DataGrid<Trade>
                  rows={addIdAndDaysToTrades((rawData as BacktestData).open_trades, true)}
                  columns={columns}
                  disableSelectionOnClick
                  hideFooter
                  autoHeight
                  initialState={{
                    sorting: {
                      sortModel: [{ field: 'date', sort: 'desc' }],
                    },
                  }}
                  components={{
                    Toolbar: GridToolbar,
                  }}
                  componentsProps={{
                    toolbar: {
                      showQuickFilter: true,
                      quickFilterProps: { debounceMs: 500 },
                    },
                  }}
                  sx={{
                    '& .MuiDataGrid-toolbarContainer': {
                      padding: 2,
                      backgroundColor: 'background.paper',
                      borderBottom: 1,
                      borderColor: 'divider',
                    },
                  }}
                />
              </Box>
            </Paper>

            <Paper sx={{ p: 2 }}>
              <Typography variant="h6" gutterBottom>
                Closed Trades ({(rawData as BacktestData).closed_trades.length})
              </Typography>
              <Box sx={{ width: '100%' }}>
                <DataGrid<Trade>
                  rows={addIdAndDaysToTrades((rawData as BacktestData).closed_trades, false)}
                  columns={columns}
                  disableSelectionOnClick
                  hideFooter
                  autoHeight
                  initialState={{
                    sorting: {
                      sortModel: [{ field: 'close_date', sort: 'desc' }],
                    },
                  }}
                  components={{
                    Toolbar: GridToolbar,
                  }}
                  componentsProps={{
                    toolbar: {
                      showQuickFilter: true,
                      quickFilterProps: { debounceMs: 500 },
                    },
                  }}
                  sx={{
                    '& .MuiDataGrid-toolbarContainer': {
                      padding: 2,
                      backgroundColor: 'background.paper',
                      borderBottom: 1,
                      borderColor: 'divider',
                    },
                  }}
                />
              </Box>
            </Paper>
          </>
        ) : null}
      </Box>
    </Container>
  );
}
