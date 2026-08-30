import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Alert, Box, MenuItem, Stack, TextField } from '@mui/material';
import { DataGrid, type GridColDef } from '@mui/x-data-grid';
import { DEFAULT_QUANTILES, getOutcomeBuckets } from '../../lib/experiments/queries';
import { toIsoDate } from '../../lib/experiments/time';
import type { RunMeta, TradeRow } from '../../lib/experiments/types';

const OUTCOMES = [
  '1_catastrophic_loss', '2_medium_loss', '3_marginal', '4_medium_win', '5_big_win',
];

const COLUMNS: GridColDef[] = [
  { field: 'symbol', headerName: 'Symbol', width: 100 },
  { field: 'entry_dt', headerName: 'Entry', width: 120,
    valueFormatter: ({ value }) => toIsoDate(value) },
  { field: 'exit_dt', headerName: 'Exit', width: 120,
    valueFormatter: ({ value }) => toIsoDate(value) },
  { field: 'net_return', headerName: 'Net Return', width: 120, type: 'number',
    valueFormatter: ({ value }) =>
      value == null ? '—' : `${((value as number) * 100).toFixed(2)}%` },
  { field: 'bars_held', headerName: 'Bars', width: 80, type: 'number' },
  { field: 'exit_reason', headerName: 'Exit Reason', width: 140,
    valueFormatter: ({ value }) => (value as string | null) ?? '—' },
  { field: 'outcome', headerName: 'Outcome', width: 170 },
];

export default function TradesTab({
  run, onSelectSymbol,
}: { run: RunMeta; onSelectSymbol: (symbol: string) => void }) {
  const [outcome, setOutcome] = useState('');
  const [symbol, setSymbol] = useState('');

  const { data, isLoading, error } = useQuery({
    queryKey: ['experiments', run.run_id, 'buckets', DEFAULT_QUANTILES],
    queryFn: () => getOutcomeBuckets([run], DEFAULT_QUANTILES),
  });

  const rows = useMemo(() => {
    const all: TradeRow[] = data ?? [];
    return all
      .filter((t) => (outcome ? t.outcome === outcome : true))
      .filter((t) => (symbol ? t.symbol.toUpperCase().includes(symbol.toUpperCase()) : true))
      .map((t, i) => ({ id: i, ...t }));
  }, [data, outcome, symbol]);

  if (error) return <Alert severity="error">{(error as Error).message}</Alert>;

  return (
    <Stack spacing={2}>
      <Stack direction="row" spacing={2}>
        <TextField size="small" label="Symbol" value={symbol}
                   onChange={(e) => setSymbol(e.target.value)} />
        <TextField select size="small" label="Outcome" value={outcome} sx={{ minWidth: 200 }}
                   onChange={(e) => setOutcome(e.target.value)}>
          <MenuItem value="">All</MenuItem>
          {OUTCOMES.map((o) => <MenuItem key={o} value={o}>{o}</MenuItem>)}
        </TextField>
      </Stack>
      <Box sx={{ height: 560 }}>
        <DataGrid
          rows={rows} columns={COLUMNS} loading={isLoading} density="compact"
          onRowClick={(p) => onSelectSymbol(String((p.row as TradeRow).symbol))}
        />
      </Box>
    </Stack>
  );
}
