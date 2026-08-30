import { useState } from 'react';
import { Box, FormControl, InputLabel, MenuItem, Select } from '@mui/material';

import SectorList from '../components/sector/SectorList';
import StockList from '../components/sector/StockList';
import SectorChart from '../components/sector/SectorChart';
import StockComparisonChart from '../components/sector/StockComparisonChart';
import { PageContainer, PageHeader, Panel, EmptyState } from '../components/ui';

const LEVELS = [1, 2, 3, 4];

export default function Sector() {
  const [sectorLevel, setSectorLevel] = useState<number>(3);
  const [selectedSectorId, setSelectedSectorId] = useState<number | null>(null);

  return (
    <PageContainer>
      <PageHeader
        title="Sectors"
        description="Relative strength and rotation across the sector hierarchy."
        actions={
          <FormControl size="small" sx={{ minWidth: 150 }}>
            <InputLabel>Sector level</InputLabel>
            <Select
              value={sectorLevel}
              label="Sector level"
              onChange={(e) => {
                setSectorLevel(e.target.value as number);
                // Selection is level-scoped, so it cannot survive a level change.
                setSelectedSectorId(null);
              }}
            >
              {LEVELS.map((l) => (
                <MenuItem key={l} value={l}>
                  Level {l}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
        }
      />

      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        <Panel title="Sector performance" subtitle={`Level ${sectorLevel} · indexed returns`}>
          <SectorChart level={sectorLevel} />
        </Panel>

        <Box
          sx={{
            display: 'grid',
            gap: 2,
            gridTemplateColumns: { xs: '1fr', md: 'minmax(0, 1fr) minmax(0, 2fr)' },
            alignItems: 'start',
          }}
        >
          <Panel title="Sectors" flush>
            <SectorList level={sectorLevel} onSectorSelect={setSelectedSectorId} />
          </Panel>

          <Panel title="Constituents" flush>
            {selectedSectorId ? (
              <StockList level={sectorLevel} sectorId={selectedSectorId} />
            ) : (
              <EmptyState
                title="No sector selected"
                description="Pick a sector on the left to list the stocks inside it."
              />
            )}
          </Panel>
        </Box>

        <Panel title="Stock comparison" subtitle="Normalised price series across a custom basket">
          <StockComparisonChart />
        </Panel>
      </Box>
    </PageContainer>
  );
}
