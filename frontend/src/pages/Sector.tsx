import { useState } from 'react';
import { Box, FormControl, InputLabel, MenuItem, Select } from '@mui/material';

import StockList from '../components/sector/StockList';
import SectorRelativeStrengthHeatmap from '../components/sector/SectorRelativeStrengthHeatmap';
import SectorDominanceTable from '../components/sector/SectorDominanceTable';
import SectorRotationGraph from '../components/sector/SectorRotationGraph';
import StockComparisonChart from '../components/sector/StockComparisonChart';
import { PageContainer, PageHeader, Panel, EmptyState } from '../components/ui';

// Levels 1-4 are the ICB hierarchy; level 5 is a different taxonomy entirely
// (sieucophieu's trade groups), so it says so rather than implying more depth.
const LEVELS: { value: number; label: string }[] = [
  { value: 1, label: 'Level 1' },
  { value: 2, label: 'Level 2' },
  { value: 3, label: 'Level 3' },
  { value: 4, label: 'Level 4' },
  { value: 5, label: 'Level 5 · trade groups' },
];

export default function Sector() {
  const [sectorLevel, setSectorLevel] = useState<number>(3);
  const [selected, setSelected] = useState<{ id: number; name: string } | null>(null);

  return (
    <PageContainer>
      <PageHeader
        title="Sectors"
        description="Relative strength and rotation across the sector hierarchy."
        actions={
          <FormControl size="small" sx={{ minWidth: 190 }}>
            <InputLabel>Sector level</InputLabel>
            <Select
              value={sectorLevel}
              label="Sector level"
              onChange={(e) => {
                setSectorLevel(e.target.value as number);
                // Selection is level-scoped, so it cannot survive a level change.
                setSelected(null);
              }}
            >
              {LEVELS.map(({ value, label }) => (
                <MenuItem key={value} value={value}>
                  {label}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
        }
      />

      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        <Panel
          title="Relative strength"
          subtitle={`Level ${sectorLevel} · vs VNINDEX (50d) · newest session on the left`}
        >
          <SectorRelativeStrengthHeatmap level={sectorLevel} />
        </Panel>

        <Box
          sx={{
            display: 'grid',
            gap: 2,
            gridTemplateColumns: { xs: '1fr', lg: 'minmax(0, 3fr) minmax(0, 2fr)' },
            alignItems: 'start',
          }}
        >
          <Panel
            title="Dominance"
            subtitle="Sort any column · click a sector to list its constituents"
          >
            <SectorDominanceTable
              level={sectorLevel}
              selectedSectorId={selected?.id ?? null}
              onSectorSelect={(id, name) => setSelected({ id, name })}
            />
          </Panel>

          <Panel
            title={selected ? `Constituents · ${selected.name}` : 'Constituents'}
            subtitle={selected ? `Level ${sectorLevel} · sector ${selected.id}` : undefined}
            flush
          >
            {selected ? (
              <StockList level={sectorLevel} sectorId={selected.id} />
            ) : (
              <EmptyState
                title="No sector selected"
                description="Click a row in Dominance to list the stocks inside that sector."
              />
            )}
          </Panel>
        </Box>

        <Panel
          title="Rotation"
          subtitle="RS-ratio vs RS-momentum · 100 is VNINDEX · tail shows direction"
        >
          <SectorRotationGraph level={sectorLevel} />
        </Panel>

        <Panel title="Stock comparison" subtitle="Normalised price series across a custom basket">
          <StockComparisonChart />
        </Panel>
      </Box>
    </PageContainer>
  );
}
