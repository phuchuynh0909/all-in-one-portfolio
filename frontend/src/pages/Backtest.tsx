import { useState } from 'react';
import {
  Box,
  Container,
  FormControl,
  InputLabel,
  MenuItem,
  Paper,
  Select,
  Typography,
  CircularProgress,
  Alert,
  Button,
  Chip,
  Stack,
} from '@mui/material';
import { DataGrid, GridToolbar } from '@mui/x-data-grid';
import type { 
  GridColDef, 
  GridRenderCellParams,
  GridValueFormatterParams
} from '@mui/x-data-grid';
import { useBacktest, type Trade } from '../lib/services/backtest';
import { format } from 'date-fns';

const STRATEGIES = ["Breakout TTM Version 2", "Squeeze Breakout"] as const;

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
    field: 'pnl', 
    headerName: 'PnL', 
    width: 100,
    renderCell: (params: GridRenderCellParams) => (
      <Typography
        color={params.value >= 0 ? 'success.main' : 'error.main'}
        fontWeight="bold"
      >
        {params.value.toFixed(2)}
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
    field: 'msr_rank_10', 
    headerName: 'MSR Rank', 
    width: 100,
    valueFormatter: (params: GridValueFormatterParams) => (params.value as number | undefined)?.toFixed(3) ?? '-'
  },
  { 
    field: 'metadata', 
    headerName: 'Parameters', 
    width: 300,
    valueFormatter: (params: GridValueFormatterParams) => formatMetadata(params.value as Record<string, any>)
  },
];

export default function BacktestPage() {
  const [strategy, setStrategy] = useState<typeof STRATEGIES[number]>(STRATEGIES[0]);
  
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
    start_date: "2024-01-01"
  });

  const handleStrategyChange = (newStrategy: typeof STRATEGIES[number]) => {
    setStrategy(newStrategy);
  };

  const addIdToTrades = (trades: Trade[]) => 
    trades.map((trade, index) => ({
      ...trade,
      id: `${trade.symbol}-${trade.date}-${index}`
    }));

  return (
    <Container maxWidth="xl">
      <Box sx={{ py: 4 }}>
        <Typography variant="h4" gutterBottom>
          Backtest Results
        </Typography>

        <Paper sx={{ p: 2, mb: 3 }}>
          <FormControl fullWidth>
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
                  rows={addIdToTrades((rawData as BacktestData).open_trades)}
                  columns={columns}
                  disableSelectionOnClick
                  hideFooter
                  autoHeight
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
                  rows={addIdToTrades((rawData as BacktestData).closed_trades)}
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
