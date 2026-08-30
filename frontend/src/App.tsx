import { BrowserRouter, Routes, Route, useLocation } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { LocalizationProvider } from '@mui/x-date-pickers/LocalizationProvider';
import { AdapterDateFns } from '@mui/x-date-pickers/AdapterDateFns';

import { ColorModeProvider } from './theme';
import AppShell from './components/layout/AppShell';
import ErrorBoundary from './components/ErrorBoundary';

import Home from './pages/Home';
import Portfolio from './pages/Portfolio';
import Chart from './pages/Chart';
import Sector from './pages/Sector';
import Report from './pages/Report';
import ReportDetail from './pages/ReportDetail';
import Backtest from './pages/Backtest';
import BacktestVisualization from './pages/BacktestVisualization';
import Experiments from './pages/Experiments';
import { FinancialStatements } from './pages/FinancialStatements';
import Scanner from './pages/Scanner';
import Regime from './pages/Regime';
import Alerts from './pages/Alerts';
import ChatAgents from './pages/ChatAgents';
import TradingAgents from './pages/TradingAgents';
import Future from './pages/Future';
import CW from './pages/CW';
import Live from './pages/Live';
import Health from './pages/Health';
import NotFound from './pages/NotFound';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { refetchOnWindowFocus: false, retry: 1 },
  },
});

function AppRoutes() {
  const { pathname } = useLocation();

  return (
    <AppShell>
      <ErrorBoundary resetKey={pathname}>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/portfolio" element={<Portfolio />} />
          <Route path="/alerts" element={<Alerts />} />

          <Route path="/backtest" element={<Backtest />} />
          <Route path="/backtest-viz" element={<BacktestVisualization />} />
          <Route path="/experiments" element={<Experiments />} />

          <Route path="/trading-agents" element={<TradingAgents />} />
          <Route path="/chat" element={<ChatAgents />} />
          <Route path="/report" element={<Report />} />
          <Route path="/report/:reportId" element={<ReportDetail />} />

          <Route path="/chart" element={<Chart />} />
          <Route path="/sector" element={<Sector />} />
          <Route path="/scanner" element={<Scanner />} />
          <Route path="/regime" element={<Regime />} />
          <Route path="/financial" element={<FinancialStatements />} />
          <Route path="/cw" element={<CW />} />
          <Route path="/future" element={<Future />} />
          <Route path="/live" element={<Live />} />

          <Route path="/health" element={<Health />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </ErrorBoundary>
    </AppShell>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ColorModeProvider>
        <LocalizationProvider dateAdapter={AdapterDateFns}>
          <BrowserRouter>
            <AppRoutes />
          </BrowserRouter>
        </LocalizationProvider>
      </ColorModeProvider>
    </QueryClientProvider>
  );
}
