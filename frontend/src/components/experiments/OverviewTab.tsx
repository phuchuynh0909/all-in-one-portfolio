import { useQuery } from '@tanstack/react-query';
import {
  Alert, Box, Card, CardContent, CircularProgress, Grid, Stack, Typography,
} from '@mui/material';
import { DataGrid, type GridColDef } from '@mui/x-data-grid';
import {
  CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { getEquity, getSymbolStats } from '../../lib/experiments/queries';
import type { RunMeta, SymbolStatRow } from '../../lib/experiments/types';

const pct = (v: number | null) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`);
const num = (v: number | null) => (v == null ? '—' : v.toFixed(3));

const STAT_COLUMNS: GridColDef[] = [
  { field: 'symbol', headerName: 'Symbol', width: 110 },
  { field: 'n_trades', headerName: 'Trades', width: 90, type: 'number' },
  { field: 'total_return', headerName: 'Total Return', width: 130, type: 'number',
    valueFormatter: ({ value }) => pct(value as number | null) },
  { field: 'sharpe', headerName: 'Sharpe', width: 100, type: 'number',
    valueFormatter: ({ value }) => num(value as number | null) },
  { field: 'sortino', headerName: 'Sortino', width: 100, type: 'number',
    valueFormatter: ({ value }) => num(value as number | null) },
  { field: 'max_drawdown', headerName: 'Max DD', width: 110, type: 'number',
    valueFormatter: ({ value }) => pct(value as number | null) },
  { field: 'win_rate', headerName: 'Win Rate', width: 110, type: 'number',
    valueFormatter: ({ value }) => pct(value as number | null) },
  { field: 'exposure', headerName: 'Exposure', width: 110, type: 'number',
    valueFormatter: ({ value }) => pct(value as number | null) },
];

interface Props {
  run: RunMeta;
  onSelectSymbol: (symbol: string) => void;
}

export default function OverviewTab({ run, onSelectSymbol }: Props) {
  const equity = useQuery({
    queryKey: ['experiments', run.run_id, 'equity'],
    queryFn: () => getEquity(run),
  });
  const stats = useQuery({
    queryKey: ['experiments', run.run_id, 'symbol_stats'],
    queryFn: () => getSymbolStats(run),
  });

  return (
    <Stack spacing={2}>
      <Grid container spacing={2}>
        {[
          ['Mean total return', pct(run.metrics.mean_total_return)],
          ['Mean Sharpe', num(run.metrics.mean_sharpe)],
          ['Symbols positive', pct(run.metrics.pct_symbols_positive)],
          ['Trades', String(run.n_trades)],
        ].map(([label, value]) => (
          <Grid item xs={6} md={3} key={label}>
            <Card><CardContent>
              <Typography variant="caption" color="text.secondary">{label}</Typography>
              <Typography variant="h6">{value}</Typography>
            </CardContent></Card>
          </Grid>
        ))}
      </Grid>

      <Card><CardContent>
        <Typography variant="subtitle2">
          Equity —{' '}
          {run.equity_agg === 'mean'
            ? 'equal-weight composite of independent per-symbol books'
            : 'cash-shared portfolio'}
        </Typography>
        {equity.isLoading && <CircularProgress size={20} />}
        {equity.error && <Alert severity="error">{(equity.error as Error).message}</Alert>}
        {equity.data && (
          <Box sx={{ height: 320 }}>
            <ResponsiveContainer>
              <LineChart data={equity.data}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="dt" tickFormatter={(v) => String(v).slice(0, 10)} minTickGap={40} />
                <YAxis />
                <Tooltip />
                <Legend />
                <Line type="monotone" dataKey="value" name="Strategy" dot={false} />
                <Line type="monotone" dataKey="benchmark_value" name="Benchmark" dot={false}
                      strokeDasharray="4 2" />
              </LineChart>
            </ResponsiveContainer>
          </Box>
        )}
      </CardContent></Card>

      <Card><CardContent>
        <Typography variant="subtitle2" gutterBottom>Parameters</Typography>
        <Box component="pre" sx={{ m: 0, fontSize: 12, overflowX: 'auto' }}>
          {JSON.stringify(run.params, null, 2)}
        </Box>
      </CardContent></Card>

      <Card><CardContent>
        <Typography variant="subtitle2" gutterBottom>Per-symbol stats</Typography>
        {stats.error && <Alert severity="error">{(stats.error as Error).message}</Alert>}
        <Box sx={{ height: 420 }}>
          <DataGrid
            rows={(stats.data ?? []).map((r, i) => ({ id: i, ...r }))}
            columns={STAT_COLUMNS}
            loading={stats.isLoading}
            density="compact"
            onRowClick={(p) => onSelectSymbol(String((p.row as SymbolStatRow).symbol))}
          />
        </Box>
      </CardContent></Card>
    </Stack>
  );
}
