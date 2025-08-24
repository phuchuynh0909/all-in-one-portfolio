import { useState, useEffect } from 'react';
import { Box, Tab, Tabs } from '@mui/material';
import PositionList from '../components/portfolio/PositionList';
import TransactionList from '../components/portfolio/TransactionList';
import PortfolioSummary from '../components/portfolio/PortfolioSummary';
import PerformanceCharts from '../components/portfolio/PerformanceCharts';
import PositionsTable from '../components/portfolio/PositionsTable';
import { apiGet } from '../lib/api';

type TabPanelProps = {
  children?: React.ReactNode;
  index: number;
  value: number;
};

function TabPanel(props: TabPanelProps) {
  const { children, value, index, ...other } = props;

  return (
    <div
      role="tabpanel"
      hidden={value !== index}
      id={`simple-tabpanel-${index}`}
      aria-labelledby={`simple-tab-${index}`}
      {...other}
    >
      {value === index && <Box sx={{ p: 3 }}>{children}</Box>}
    </div>
  );
}

type Position = {
  id: number;
  symbol: string;
  quantity: number;
  cost_basis: number;
  current_value: number;
  unrealized_gain: number;
  return_pct: number;
};

export default function Portfolio() {
  const [value, setValue] = useState(0);
  const [positions, setPositions] = useState<Position[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const loadPositions = async () => {
      try {
        const data = await apiGet<Position[]>('/portfolio/positions');
        // Calculate performance metrics for each position
        const positionsWithMetrics = data.map(pos => ({
          ...pos,
          cost_basis: pos.purchase_price * pos.quantity,
          current_value: pos.current_price * pos.quantity,
          unrealized_gain: (pos.current_price * pos.quantity) - (pos.purchase_price * pos.quantity),
          return_pct: ((pos.current_price / pos.purchase_price) - 1) * 100
        }));
        setPositions(positionsWithMetrics);
      } catch (error) {
        console.error('Error loading positions:', error);
      } finally {
        setLoading(false);
      }
    };

    loadPositions();
  }, []);

  const handleChange = (event: React.SyntheticEvent, newValue: number) => {
    setValue(newValue);
  };

  if (loading) {
    return <div>Loading...</div>;
  }

  return (
    <Box sx={{ width: '95%', margin: '0 auto' }}>
      <PortfolioSummary />
      
      {positions.length > 0 && (
        <>
          <PerformanceCharts positions={positions} />
          <PositionsTable positions={positions} />
        </>
      )}

      <Box sx={{ borderBottom: 1, borderColor: 'divider', mt: 4 }}>
        <Tabs value={value} onChange={handleChange}>
          <Tab label="Positions" />
          <Tab label="Transactions" />
        </Tabs>
      </Box>

      <TabPanel value={value} index={0}>
        <PositionList />
      </TabPanel>
      <TabPanel value={value} index={1}>
        <TransactionList />
      </TabPanel>
    </Box>
  );
}
