import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Alert, Box, Card, CardContent, Chip, Stack, Typography,
} from '@mui/material';
import { DataGrid, type GridColDef } from '@mui/x-data-grid';
import {
  Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import {
  DEFAULT_QUANTILES, getFeatureDiscrimination, getOutcomeBuckets, hasFeatures,
} from '../../lib/experiments/queries';
import type { DiscriminationRow, RunMeta, TradeRow } from '../../lib/experiments/types';

const num = (v: number | null) => (v == null ? '—' : v.toFixed(3));

const DISC_COLUMNS: GridColDef[] = [
  { field: 'feature', headerName: 'Feature', width: 200 },
  { field: 'separation', headerName: 'Separation (σ)', width: 150, type: 'number',
    valueFormatter: ({ value }) => num(value as number | null) },
  { field: 'loser_mean', headerName: 'Worst 10% mean', width: 160, type: 'number',
    valueFormatter: ({ value }) => num(value as number | null) },
  { field: 'winner_mean', headerName: 'Best 10% mean', width: 160, type: 'number',
    valueFormatter: ({ value }) => num(value as number | null) },
  { field: 'coverage', headerName: 'Coverage', width: 120, type: 'number',
    valueFormatter: ({ value }) =>
      value == null ? '—' : `${((value as number) * 100).toFixed(0)}%` },
  { field: 'n_obs', headerName: 'N', width: 90, type: 'number' },
];

export default function AttributionTab({ runs }: { runs: RunMeta[] }) {
  const key = runs.map((r) => r.run_id).sort().join('|');

  const buckets = useQuery({
    queryKey: ['experiments', 'pooled-buckets', key],
    queryFn: () => getOutcomeBuckets(runs, DEFAULT_QUANTILES),
    enabled: runs.length > 0,
  });
  const featured = hasFeatures(runs);
  const disc = useQuery({
    queryKey: ['experiments', 'pooled-discrimination', key],
    queryFn: () => getFeatureDiscrimination(runs, DEFAULT_QUANTILES),
    enabled: runs.length > 0 && featured,
  });

  const bucketCounts = useMemo(() => {
    const counts = new Map<string, { outcome: string; n: number; sum: number }>();
    for (const t of (buckets.data ?? []) as TradeRow[]) {
      const o = String(t.outcome);
      const prev = counts.get(o) ?? { outcome: o, n: 0, sum: 0 };
      counts.set(o, { outcome: o, n: prev.n + 1, sum: prev.sum + Number(t.net_return ?? 0) });
    }
    return [...counts.values()]
      .sort((a, b) => a.outcome.localeCompare(b.outcome))
      .map((r) => ({ ...r, mean_net_return: r.n ? r.sum / r.n : 0 }));
  }, [buckets.data]);

  const lowCoverage = ((disc.data ?? []) as DiscriminationRow[]).filter((d) => d.coverage < 0.5);

  if (!runs.length) return <Alert severity="info">Select at least one run.</Alert>;
  if (buckets.error) return <Alert severity="error">{(buckets.error as Error).message}</Alert>;

  return (
    <Stack spacing={2}>
      <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap' }}>
        {runs.map((r) => <Chip key={r.run_id} label={r.name} size="small" />)}
      </Stack>

      <Card><CardContent>
        <Typography variant="subtitle2">Outcome distribution</Typography>
        <Box sx={{ height: 260 }}>
          <ResponsiveContainer>
            <BarChart data={bucketCounts}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="outcome" />
              <YAxis />
              <Tooltip />
              <Bar dataKey="n" name="Trades" />
            </BarChart>
          </ResponsiveContainer>
        </Box>
      </CardContent></Card>

      <Card><CardContent>
        <Typography variant="subtitle2" gutterBottom>Feature discrimination</Typography>
        {!featured && (
          <Alert severity="info">
            No entry features were logged for {runs.length === 1 ? 'this run' : 'these runs'}.
            Pass <code>features=</code> to <code>log_experiment</code> — a DataFrame keyed on
            (<code>symbol</code>, <code>entry_dt</code>) with one column per indicator value at
            entry — to rank which of them separates the worst trades from the best.
          </Alert>
        )}
        {featured && lowCoverage.length > 0 && (
          <Alert severity="warning" sx={{ mb: 1 }}>
            {lowCoverage.length} feature(s) are present on under half the pooled trades;
            read their separation with care.
          </Alert>
        )}
        {disc.error && <Alert severity="error">{(disc.error as Error).message}</Alert>}
        {featured && (
          <Box sx={{ height: 420 }}>
            <DataGrid
              rows={((disc.data ?? []) as DiscriminationRow[]).map((r, i) => ({ id: i, ...r }))}
              columns={DISC_COLUMNS}
              loading={disc.isLoading}
              density="compact"
            />
          </Box>
        )}
      </CardContent></Card>
    </Stack>
  );
}
