import { useState, useEffect } from 'react';
import { Alert, Box, CircularProgress, Tab, Tabs, Typography } from '@mui/material';
import PositionList from '../components/portfolio/PositionList';
import TransactionList from '../components/portfolio/TransactionList';
import PortfolioSummary from '../components/portfolio/PortfolioSummary';
import PerformanceCharts from '../components/portfolio/PerformanceCharts';
import PositionsTable from '../components/portfolio/PositionsTable';
import PortfolioPieChart from '../components/portfolio/PortfolioPieChart';
import CurrentPortfolioPieChart from '../components/portfolio/CurrentPortfolioPieChart';
import { getPositions, type Position } from '../lib/services/portfolio';

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
      {value === index && <Box sx={{ pt: 3 }}>{children}</Box>}
    </div>
  );
}

/** A position with `current_price` resolved to a number — the backend may not have
 * priced a position yet, in which case we fall back to the purchase price so
 * derived P/L reads as 0 rather than silently becoming NaN in the charts/table.
 * `notes` is normalized from `string | null` to `string | undefined` to match
 * what the child table/chart components expect. */
type PricedPosition = Omit<Position, 'notes'> & { current_price: number; notes?: string };

export default function Portfolio() {
  const [tab, setTab] = useState(0);
  const [positions, setPositions] = useState<Position[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Bumped whenever positions change from anywhere on the page, so
  // independently-fetching widgets (PortfolioSummary) know to refetch.
  const [refreshToken, setRefreshToken] = useState(0);

  const loadPositions = async () => {
    try {
      setLoading(true);
      setError(null);
      setPositions(await getPositions());
      setRefreshToken((t) => t + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load positions');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadPositions();
  }, []);

  const pricedPositions: PricedPosition[] = positions.map((p) => ({
    ...p,
    current_price: p.current_price ?? p.purchase_price,
    notes: p.notes ?? undefined,
  }));

  return (
    <Box sx={{ width: '95%', margin: '0 auto', pb: 4 }}>
      <Typography variant="h5" sx={{ mb: 3 }}>Portfolio</Typography>

      {/* Overview — fetches its own summary, independent of the positions load below */}
      <Box sx={{ mb: 4 }}>
        <PortfolioSummary key={refreshToken} />
      </Box>

      {error && <Alert severity="error" sx={{ mb: 3 }}>{error}</Alert>}

      {loading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', my: 6 }}>
          <CircularProgress />
        </Box>
      ) : positions.length > 0 ? (
        <>
          {/* Performance */}
          <Box sx={{ mb: 4 }}>
            <Typography variant="h6" sx={{ mb: 2 }}>Performance</Typography>
            <PerformanceCharts positions={pricedPositions} />

            <Box sx={{ display: 'flex', flexDirection: { xs: 'column', md: 'row' }, gap: 2, mt: 2 }}>
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <CurrentPortfolioPieChart positions={pricedPositions} />
              </Box>
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <PortfolioPieChart tickers={pricedPositions.map((p) => p.ticker)} />
              </Box>
            </Box>
          </Box>

        </>
      ) : (
        <Alert severity="info" sx={{ mb: 4 }}>
          No open positions yet — add one from the Positions tab below.
        </Alert>
      )}

      {/* Holdings & Manage — side by side */}
      <Box sx={{ display: 'flex', flexDirection: { xs: 'column', lg: 'row' }, gap: 3, mb: 4 }}>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Box sx={{ borderBottom: 1, borderColor: 'divider' }}>
            <Tabs value={0}>
              <Tab label="Holdings" />
            </Tabs>
          </Box>
          <TabPanel value={0} index={0}>
            <PositionsTable positions={pricedPositions} onPositionUpdate={loadPositions} />
          </TabPanel>
        </Box>

        <Box sx={{ flex: 1, minWidth: 0 }}>
          {/* Manage — full CRUD over positions & transactions */}
          <Box sx={{ borderBottom: 1, borderColor: 'divider' }}>
            <Tabs value={tab} onChange={(_, v) => setTab(v)}>
              <Tab label="Positions" />
              <Tab label="Transactions" />
            </Tabs>
          </Box>

          <TabPanel value={tab} index={0}>
            <PositionList onDataChanged={loadPositions} />
          </TabPanel>
          <TabPanel value={tab} index={1}>
            <TransactionList />
          </TabPanel>
        </Box>
      </Box>
    </Box>
  );
}
