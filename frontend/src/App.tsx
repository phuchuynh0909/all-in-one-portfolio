import { BrowserRouter, Routes, Route, Link } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  CssBaseline,
  ThemeProvider,
  createTheme,
  AppBar,
  Toolbar,
  Typography,
  Box,
  Container,
  Button,
} from '@mui/material';
import { LocalizationProvider } from '@mui/x-date-pickers/LocalizationProvider';
import { AdapterDateFns } from '@mui/x-date-pickers/AdapterDateFns';

import Home from './pages/Home';
import Portfolio from './pages/Portfolio';
import Chart from './pages/Chart';
import Sector from './pages/Sector';
import Report from './pages/Report';
import ReportDetail from './pages/ReportDetail';
import Backtest from './pages/Backtest';
import BacktestVisualization from './pages/BacktestVisualization';
import { FinancialStatements } from './pages/FinancialStatements';
import Scanner from './pages/Scanner';
import Regime from './pages/Regime';
import Alerts from './pages/Alerts';
import ChatAgents from './pages/ChatAgents';
import TradingAgents from './pages/TradingAgents';
import Future from './pages/Future';
import CW from './pages/CW';

const theme = createTheme({
  palette: {
    mode: 'dark',
    primary: {
      main: '#1976d2',
    },
    secondary: {
      main: '#dc004e',
    },
    background: {
      default: '#121212',
      paper: '#1e1e1e',
    },
    text: {
      primary: '#ffffff',
      secondary: 'rgba(255, 255, 255, 0.7)',
    },
  },
});

const navItems = [
  { path: '/', label: 'Home' },
  { path: '/portfolio', label: 'Portfolio' },
  { path: '/chart', label: 'Chart' },
  { path: '/sector', label: 'Sector' },
  { path: '/report', label: 'Report' },
  { path: '/backtest', label: 'Backtest' },
  { path: '/backtest-viz', label: '📊 BT Visual' },
  { path: '/financial', label: 'Financial Statements' },
  { path: '/scanner', label: 'Scanner' },
  { path: '/regime', label: '📊 Regime' },
  { path: '/future', label: '⚡ Future' },
  { path: '/cw', label: 'CW' },
  { path: '/alerts', label: '🔔 Alerts' },
  { path: '/chat', label: '🤖 Chat' },
  { path: '/trading-agents', label: '🤝 Agents' },
];

// Create a client
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider theme={theme}>
      <LocalizationProvider dateAdapter={AdapterDateFns}>
        <CssBaseline />
        <BrowserRouter>
          <Box sx={{ flexGrow: 1 }}>
            <AppBar position="static">
              <Container maxWidth="xl">
                <Toolbar disableGutters>
                  <Typography
                    variant="h6"
                    noWrap
                    component={Link}
                    to="/"
                    sx={{
                      mr: 2,
                      color: 'inherit',
                      textDecoration: 'none',
                    }}
                  >
                    Investment Tracker
                  </Typography>
                  <Box sx={{ flexGrow: 1, display: 'flex', gap: 2 }}>
                    {navItems.map((item) => (
                      <Button
                        key={item.path}
                        component={Link}
                        to={item.path}
                        sx={{ color: 'white' }}
                      >
                        {item.label}
                      </Button>
                    ))}
                  </Box>
                </Toolbar>
              </Container>
            </AppBar>
          </Box>

          <Box component="main" sx={{ py: 3 }}>
            <Routes>
              <Route path="/" element={<Home />} />
              <Route path="/portfolio" element={<Portfolio />} />
              <Route path="/chart" element={<Chart />} />
              <Route path="/sector" element={<Sector />} />
              <Route path="/report" element={<Report />} />
              <Route path="/report/:reportId" element={<ReportDetail />} />
              <Route path="/backtest" element={<Backtest />} />
              <Route path="/backtest-viz" element={<BacktestVisualization />} />
              <Route path="/financial" element={<FinancialStatements />} />
              <Route path="/scanner" element={<Scanner />} />
              <Route path="/regime" element={<Regime />} />
              <Route path="/future" element={<Future />} />
              <Route path="/cw" element={<CW />} />
              <Route path="/trading-agents" element={<TradingAgents />} />
              <Route path="/alerts" element={<Alerts />} />
              <Route
                path="/chat"
                element={
                  <Box sx={{ my: -3, height: 'calc(100vh - 64px)', overflow: 'hidden' }}>
                    <ChatAgents />
                  </Box>
                }
              />
            </Routes>
          </Box>
        </BrowserRouter>
      </LocalizationProvider>
    </ThemeProvider>
    </QueryClientProvider>
  );
}
