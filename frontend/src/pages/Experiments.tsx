import { useEffect, useMemo, useState } from 'react';
import { Box, Chip, Stack, Tab, Tabs } from '@mui/material';
import RunList from '../components/experiments/RunList';
import OverviewTab from '../components/experiments/OverviewTab';
import TradesTab from '../components/experiments/TradesTab';
import AttributionTab from '../components/experiments/AttributionTab';
import SymbolTab from '../components/experiments/SymbolTab';
import { useCatalog } from '../lib/experiments/catalog';
import { PageContainer, PageHeader, Panel, QueryState, EmptyState } from '../components/ui';

const TABS = ['Overview', 'Trades', 'Attribution', 'Symbol'] as const;

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

  // Attribution pools every checked run; with none checked it falls back to
  // whichever run is currently selected.
  const pooledRuns = comparedIds.length
    ? runs.filter((r) => comparedIds.includes(r.run_id))
    : selectedRun
      ? [selectedRun]
      : [];

  const toggleCompare = (id: string) =>
    setComparedIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));

  const pickSymbol = (symbol: string) => {
    setSelectedSymbol(symbol);
    setTab(3);
  };

  return (
    <PageContainer>
      <PageHeader
        title="Experiments"
        description="Logged backtest runs, their trade-level attribution and per-symbol behaviour."
        actions={
          comparedIds.length > 0 ? (
            <Chip
              label={`${comparedIds.length} pooled for attribution`}
              color="primary"
              variant="outlined"
              onDelete={() => setComparedIds([])}
            />
          ) : undefined
        }
      />

      <QueryState
        isLoading={isLoading}
        error={error}
        isEmpty={!isLoading && !error && runs.length === 0}
        loadingLabel="Loading experiment catalog"
        emptyTitle="No experiments logged"
        emptyDescription={
          <>
            Run <code>log_experiment(pf, name=…)</code> in a notebook, or rebuild a missing catalog
            with <code>ExperimentStore.from_env().rebuild_catalog()</code>.
          </>
        }
      >
        <Box
          sx={{
            display: 'grid',
            gap: 2,
            gridTemplateColumns: { xs: '1fr', md: 'minmax(240px, 300px) minmax(0, 1fr)' },
            alignItems: 'start',
          }}
        >
          <Panel title="Runs" subtitle={`${runs.length} logged`} flush sx={{ maxHeight: '80vh' }}>
            <Box sx={{ overflowY: 'auto', maxHeight: 'calc(80vh - 52px)' }}>
              <RunList
                runs={runs}
                selectedId={selectedId}
                onSelect={setSelectedId}
                comparedIds={comparedIds}
                onToggleCompare={toggleCompare}
              />
            </Box>
          </Panel>

          {selectedRun ? (
            <Panel
              title={
                <Stack direction="row" spacing={1} alignItems="center">
                  <span>{selectedRun.name}</span>
                  {selectedSymbol && <Chip size="small" label={selectedSymbol} />}
                </Stack>
              }
              subtitle={`${selectedRun.data_start} → ${selectedRun.data_end} · ${selectedRun.n_symbols} symbols · ${selectedRun.n_trades} trades`}
              flush
            >
              <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ px: 1 }}>
                {TABS.map((label) => (
                  <Tab key={label} label={label} />
                ))}
              </Tabs>
              <Box sx={{ p: 2 }}>
                {tab === 0 && <OverviewTab run={selectedRun} onSelectSymbol={pickSymbol} />}
                {tab === 1 && <TradesTab run={selectedRun} onSelectSymbol={pickSymbol} />}
                {tab === 2 && <AttributionTab runs={pooledRuns} />}
                {tab === 3 && <SymbolTab run={selectedRun} symbol={selectedSymbol} />}
              </Box>
            </Panel>
          ) : (
            <Panel>
              <EmptyState title="Select a run" description="Pick a run from the list to inspect it." />
            </Panel>
          )}
        </Box>
      </QueryState>
    </PageContainer>
  );
}
