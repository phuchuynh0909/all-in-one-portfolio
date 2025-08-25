import { useState } from 'react';
import { 
  Box, 
  FormControl, 
  InputLabel, 
  Select, 
  MenuItem, 
  Typography,
  Stack 
} from '@mui/material';

import SectorList from '../components/sector/SectorList';
import StockList from '../components/sector/StockList';
import SectorChart from '../components/sector/SectorChart';

export default function Sector() {
  const [sectorLevel, setSectorLevel] = useState<number>(3);
  const [selectedSectorId, setSelectedSectorId] = useState<number | null>(null);

  return (
    <Box sx={{ width: '95%', margin: '0 auto' }}>
      {/* Level Selector */}
      <Stack direction="row" alignItems="center" spacing={2} sx={{ mb: 3 }}>
        <Typography variant="h5">Sector Analysis</Typography>
        <FormControl size="small" sx={{ minWidth: 150 }}>
          <InputLabel>Sector Level</InputLabel>
          <Select
            value={sectorLevel}
            label="Sector Level"
            onChange={(e) => {
              setSectorLevel(e.target.value as number);
              setSelectedSectorId(null); // Clear selection when level changes
            }}
          >
            <MenuItem value={3}>Level 3</MenuItem>
            <MenuItem value={4}>Level 4</MenuItem>
          </Select>
        </FormControl>
      </Stack>

      <SectorChart level={sectorLevel} />
      <Box sx={{ display: 'flex', flexDirection: { xs: 'column', md: 'row' }, gap: 2, mt: 2 }}>
        <Box sx={{ flex: 1 }}>
          <SectorList level={sectorLevel} onSectorSelect={setSelectedSectorId} />
        </Box>
        <Box sx={{ flex: 2 }}>
          {selectedSectorId ? (
            <StockList level={sectorLevel} sectorId={selectedSectorId} />
          ) : (
            <Box sx={{ 
              height: 800, 
              display: 'flex', 
              alignItems: 'center', 
              justifyContent: 'center',
              border: '1px dashed #ccc',
              borderRadius: 1,
              color: 'text.secondary'
            }}>
              <Typography>Select a sector to view stocks</Typography>
            </Box>
          )}
        </Box>
      </Box>
    </Box>
  );
}