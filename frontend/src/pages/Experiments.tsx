import { useEffect, useMemo, useState } from 'react';
import {
  Alert, Box, Chip, CircularProgress, Grid, Paper, Stack, Tab, Tabs, Typography,
} from '@mui/material';
import RunList from '../components/experiments/RunList';
import OverviewTab from '../components/experiments/OverviewTab';
import { useCatalog } from '../lib/experiments/catalog';

export default function Experiments() {
  const { data, isLoading, error } = useCatalog();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [comparedIds, setComparedIds] = useState<string[]>([]);
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);
  const [tab, setTab] = useState(0);

  const runs = useMemo(
    () => [...(data?.runs ?? [])].sort((a, b) => b.created_at.localeCompare(a.created_at)),
    [data],
  );

  useEffect(() => {
    if (!selectedId && runs.length) setSelectedId(runs[0].run_id);
  }, [runs, selectedId]);

  const selectedRun = runs.find((r) => r.run_id === selectedId) ?? null;

  const toggleCompare = (id: string) =>
    setComparedIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));

  const pickSymbol = (symbol: string) => {
    setSelectedSymbol(symbol);
    setTab(3);
  };

  if (isLoading) return <CircularProgress />;
  if (error) return <Alert severity="error">{(error as Error).message}</Alert>;
  if (!runs.length) {
    return (
      <Alert severity="info">
        No experiments yet. Run <code>log_experiment(pf, name=...)</code> in a notebook,
        or repair a missing catalog with <code>ExperimentStore.from_env().rebuild_catalog()</code>.
      </Alert>
    );
  }

  return (
    <Grid container spacing={2}>
      <Grid item xs={12} md={3}>
        <Paper sx={{ p: 1, maxHeight: '80vh', overflowY: 'auto' }}>
          <RunList
            runs={runs}
            selectedId={selectedId}
            onSelect={setSelectedId}
            comparedIds={comparedIds}
            onToggleCompare={toggleCompare}
          />
        </Paper>
      </Grid>
      <Grid item xs={12} md={9}>
        {selectedRun && (
          <>
            <Stack direction="row" spacing={1} alignItems="center">
              <Typography variant="h6">{selectedRun.name}</Typography>
              {selectedSymbol && <Chip size="small" label={selectedSymbol} />}
            </Stack>
            <Typography variant="caption" color="text.secondary">
              {selectedRun.data_start} to {selectedRun.data_end} ·{' '}
              {selectedRun.n_symbols} symbols · {selectedRun.n_trades} trades
            </Typography>
            <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 2 }}>
              <Tab label="Overview" />
              <Tab label="Trades" />
              <Tab label="Attribution" />
              <Tab label="Symbol" />
            </Tabs>
            <Box>
              {tab === 0 && <OverviewTab run={selectedRun} onSelectSymbol={pickSymbol} />}
              {tab > 0 && (
                <Alert severity="info">This tab is added by a later task.</Alert>
              )}
            </Box>
          </>
        )}
      </Grid>
    </Grid>
  );
}
