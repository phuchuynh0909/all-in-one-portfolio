import { useState } from 'react';
import { BrowserRouter, Routes, Route, Link, useLocation } from 'react-router-dom';
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
  IconButton,
  Drawer,
  List,
  ListItem,
  ListItemButton,
  ListItemText,
} from '@mui/material';
import MenuIcon from '@mui/icons-material/Menu';
import ViewListIcon from '@mui/icons-material/ViewList';
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
import BlockEpisodesPanel from './components/blockEpisodes/BlockEpisodesPanel';

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

const drawerWidth = 260;
const episodesWidth = 440;

function AppShell() {
  const [navOpen, setNavOpen] = useState(false);
  const [episodesOpen, setEpisodesOpen] = useState(false);
  const location = useLocation();

  return (
    <>
      <Box sx={{ flexGrow: 1 }}>
        <AppBar position="static">
          <Container maxWidth="xl">
            <Toolbar disableGutters>
              <IconButton
                color="inherit"
                edge="start"
                onClick={() => setNavOpen(true)}
                sx={{ mr: 2 }}
              >
                <MenuIcon />
              </IconButton>
              <Typography
                variant="h6"
                noWrap
                component={Link}
                to="/"
                sx={{
                  color: 'inherit',
                  textDecoration: 'none',
                  flexGrow: 1,
                }}
              >
                Investment Tracker
              </Typography>
              <IconButton
                color="inherit"
                edge="end"
                onClick={() => setEpisodesOpen((v) => !v)}
                title="Block Episodes"
              >
                <ViewListIcon />
              </IconButton>
            </Toolbar>
          </Container>
        </AppBar>
      </Box>

      <Drawer
        anchor="left"
        open={navOpen}
        onClose={() => setNavOpen(false)}
        PaperProps={{ sx: { width: drawerWidth } }}
      >
        <List sx={{ pt: 1 }}>
          {navItems.map((item) => (
            <ListItem key={item.path} disablePadding>
              <ListItemButton
                component={Link}
                to={item.path}
                selected={location.pathname === item.path}
                onClick={() => setNavOpen(false)}
              >
                <ListItemText primary={item.label} />
              </ListItemButton>
            </ListItem>
          ))}
        </List>
      </Drawer>

      <Drawer
        anchor="right"
        variant="persistent"
        open={episodesOpen}
        PaperProps={{ sx: { width: episodesWidth } }}
      >
        <BlockEpisodesPanel />
      </Drawer>

      <Box
        component="main"
        sx={{ py: 3, transition: 'margin 0.2s', mr: episodesOpen ? `${episodesWidth}px` : 0 }}
      >
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
    </>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider theme={theme}>
      <LocalizationProvider dateAdapter={AdapterDateFns}>
        <CssBaseline />
        <BrowserRouter>
          <AppShell />
        </BrowserRouter>
      </LocalizationProvider>
    </ThemeProvider>
    </QueryClientProvider>
  );
}
