import { Box } from '@mui/material';
import SectorPerformanceChart from '../components/sector/SectorPerformanceChart';
import SectorList from '../components/sector/SectorList';
import StockList from '../components/sector/StockList';

export default function Sector() {
  return (
    <Box sx={{ width: '95%', margin: '0 auto' }}>
      <SectorPerformanceChart />
      <Box sx={{ display: 'flex', flexDirection: { xs: 'column', md: 'row' }, gap: 2, mt: 2 }}>
        <Box sx={{ flex: 1 }}>
          <SectorList />
        </Box>
        <Box sx={{ flex: 2 }}>
          <StockList />
        </Box>
      </Box>
    </Box>
  );
}