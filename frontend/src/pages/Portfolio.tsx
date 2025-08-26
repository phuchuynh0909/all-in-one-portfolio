import { useState, useEffect } from 'react';
import { Box, Tab, Tabs } from '@mui/material';
import PositionList from '../components/portfolio/PositionList';
import TransactionList from '../components/portfolio/TransactionList';
import PortfolioSummary from '../components/portfolio/PortfolioSummary';
import PerformanceCharts from '../components/portfolio/PerformanceCharts';
import PositionsTable from '../components/portfolio/PositionsTable';
import PortfolioPieChart from '../components/portfolio/PortfolioPieChart';
import CurrentPortfolioPieChart from '../components/portfolio/CurrentPortfolioPieChart';
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
  ticker: string;
  quantity: number;
  purchase_price: number;
  current_price: number;
  purchase_date: string;
  notes?: string;
  created_at: string;
};

export default function Portfolio() {
  const [value, setValue] = useState(0);
  const [positions, setPositions] = useState<Position[]>([]);
  const [loading, setLoading] = useState(true);

  const loadPositions = async () => {
    try {
      setLoading(true);
      const data = await apiGet<Position[]>('/portfolio/positions');
      // Calculate performance metrics for each position
      setPositions(data);
    } catch (error) {
      console.error('Error loading positions:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadPositions();
  }, []);

  const handleChange = (_: React.SyntheticEvent, newValue: number) => {
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
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            <PerformanceCharts positions={positions.map(p => ({
              ...p,
              cost_basis: p.purchase_price * p.quantity,
              current_value: p.current_price * p.quantity,
              unrealized_gain: (p.current_price - p.purchase_price) * p.quantity,
              return_pct: ((p.current_price / p.purchase_price) - 1) * 100
            }))} />
            
            <Box sx={{ display: 'flex', flexDirection: { xs: 'column', md: 'row' }, gap: 2 }}>
              <Box sx={{ flex: 1 }}>
                <CurrentPortfolioPieChart positions={positions} />
              </Box>
              <Box sx={{ flex: 1 }}>
                <PortfolioPieChart tickers={positions.map(p => p.ticker)} />
              </Box>
            </Box>
          </Box>

          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, marginTop: 2 }}>
            <PositionsTable positions={positions} onPositionUpdate={loadPositions} />
          </Box>
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
