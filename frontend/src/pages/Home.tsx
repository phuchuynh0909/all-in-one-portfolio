import { Container, Typography, Paper, Box } from '@mui/material';
import MarketBreadthChart from '../components/market/MarketBreadthChart';

export default function Home() {
  return (
    <Container maxWidth="xl">
      <Box sx={{ py: 4 }}>

        {/* Market Breadth Section */}
        <Paper 
          sx={{ 
            p: 3,
            width: '100%',
            background: 'linear-gradient(135deg, rgba(30, 30, 46, 0.95) 0%, rgba(24, 24, 36, 0.98) 100%)',
            border: '1px solid rgba(99, 102, 241, 0.2)',
          }}
        >
          <MarketBreadthChart />
        </Paper>
      </Box>
    </Container>
  );
}
