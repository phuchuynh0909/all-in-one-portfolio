import type { ReactNode } from 'react';
import SpaceDashboardOutlinedIcon from '@mui/icons-material/SpaceDashboardOutlined';
import AccountBalanceWalletOutlinedIcon from '@mui/icons-material/AccountBalanceWalletOutlined';
import NotificationsActiveOutlinedIcon from '@mui/icons-material/NotificationsActiveOutlined';
import ScienceOutlinedIcon from '@mui/icons-material/ScienceOutlined';
import QueryStatsOutlinedIcon from '@mui/icons-material/QueryStatsOutlined';
import InsightsOutlinedIcon from '@mui/icons-material/InsightsOutlined';
import SmartToyOutlinedIcon from '@mui/icons-material/SmartToyOutlined';
import ForumOutlinedIcon from '@mui/icons-material/ForumOutlined';
import ArticleOutlinedIcon from '@mui/icons-material/ArticleOutlined';
import CandlestickChartOutlinedIcon from '@mui/icons-material/CandlestickChartOutlined';
import DonutSmallOutlinedIcon from '@mui/icons-material/DonutSmallOutlined';
import FilterAltOutlinedIcon from '@mui/icons-material/FilterAltOutlined';
import ThermostatOutlinedIcon from '@mui/icons-material/ThermostatOutlined';
import TableChartOutlinedIcon from '@mui/icons-material/TableChartOutlined';
import ReceiptLongOutlinedIcon from '@mui/icons-material/ReceiptLongOutlined';
import BoltOutlinedIcon from '@mui/icons-material/BoltOutlined';
import SensorsOutlinedIcon from '@mui/icons-material/SensorsOutlined';
import MonitorHeartOutlinedIcon from '@mui/icons-material/MonitorHeartOutlined';

export interface NavItem {
  path: string;
  label: string;
  /** Shown in the page header under the title. */
  description: string;
  icon: ReactNode;
  /** Renders full-bleed with no page padding (chat, full-screen charts). */
  fullBleed?: boolean;
}

export interface NavGroup {
  id: string;
  label: string;
  items: NavItem[];
}

export const navGroups: NavGroup[] = [
  {
    id: 'overview',
    label: 'Overview',
    items: [
      {
        path: '/',
        label: 'Dashboard',
        description: 'Market breadth, portfolio health and desk activity at a glance',
        icon: <SpaceDashboardOutlinedIcon fontSize="small" />,
      },
    ],
  },
  {
    id: 'portfolio',
    label: 'Portfolio',
    items: [
      {
        path: '/portfolio',
        label: 'Positions',
        description: 'Holdings, transactions, allocation and realised P&L',
        icon: <AccountBalanceWalletOutlinedIcon fontSize="small" />,
      },
      {
        path: '/alerts',
        label: 'Alerts',
        description: 'Price and indicator triggers monitored in the background',
        icon: <NotificationsActiveOutlinedIcon fontSize="small" />,
      },
    ],
  },
  {
    id: 'quant',
    label: 'Quant Research',
    items: [
      {
        path: '/backtest',
        label: 'Backtest',
        description: 'Configure and run strategy simulations over historical data',
        icon: <ScienceOutlinedIcon fontSize="small" />,
      },
      {
        path: '/backtest-viz',
        label: 'Backtest Visual',
        description: 'Equity curves, drawdowns and trade-level playback',
        icon: <QueryStatsOutlinedIcon fontSize="small" />,
      },
      {
        path: '/experiments',
        label: 'Experiments',
        description: 'Run registry, attribution and feature discrimination analysis',
        icon: <InsightsOutlinedIcon fontSize="small" />,
      },
    ],
  },
  {
    id: 'agents',
    label: 'AI Agents',
    items: [
      {
        path: '/trading-agents',
        label: 'Trading Agents',
        description: 'Multi-agent debate and consensus on a ticker',
        icon: <SmartToyOutlinedIcon fontSize="small" />,
      },
      {
        path: '/chat',
        label: 'Analyst Chat',
        description: 'Conversational research assistant with tool access',
        icon: <ForumOutlinedIcon fontSize="small" />,
        fullBleed: true,
      },
      {
        path: '/report',
        label: 'Reports',
        description: 'Generated research notes and archived analyses',
        icon: <ArticleOutlinedIcon fontSize="small" />,
      },
    ],
  },
  {
    id: 'market',
    label: 'Market Data',
    items: [
      {
        path: '/chart',
        label: 'Charts',
        description: 'Advanced charting with custom studies and watchlists',
        icon: <CandlestickChartOutlinedIcon fontSize="small" />,
        fullBleed: true,
      },
      {
        path: '/sector',
        label: 'Sectors',
        description: 'Relative strength and rotation across sectors',
        icon: <DonutSmallOutlinedIcon fontSize="small" />,
      },
      {
        path: '/scanner',
        label: 'Scanner',
        description: 'Screen the universe against technical and fundamental filters',
        icon: <FilterAltOutlinedIcon fontSize="small" />,
      },
      {
        path: '/regime',
        label: 'Regime',
        description: 'Market state classification and regime transitions',
        icon: <ThermostatOutlinedIcon fontSize="small" />,
      },
      {
        path: '/financial',
        label: 'Financials',
        description: 'Statement data, ratios and the crawler that feeds them',
        icon: <TableChartOutlinedIcon fontSize="small" />,
      },
      {
        path: '/cw',
        label: 'Warrants',
        description: 'Covered warrant pricing, greeks and issuer comparison',
        icon: <ReceiptLongOutlinedIcon fontSize="small" />,
      },
      {
        path: '/future',
        label: 'Futures',
        description: 'Index futures basis, open interest and intraday flow',
        icon: <BoltOutlinedIcon fontSize="small" />,
      },
      {
        path: '/live',
        label: 'Live Tape',
        description: 'Real-time quotes, large orders and trade flow',
        icon: <SensorsOutlinedIcon fontSize="small" />,
      },
    ],
  },
  {
    id: 'system',
    label: 'System',
    items: [
      {
        path: '/health',
        label: 'Health',
        description: 'Service status, data freshness and pipeline diagnostics',
        icon: <MonitorHeartOutlinedIcon fontSize="small" />,
      },
    ],
  },
];

export const navItems: NavItem[] = navGroups.flatMap((g) => g.items);

/** Longest-prefix match so nested routes (/report/:id) resolve to their parent. */
export function findNavItem(pathname: string): NavItem | undefined {
  if (pathname === '/') return navItems.find((i) => i.path === '/');
  return navItems
    .filter((i) => i.path !== '/' && pathname.startsWith(i.path))
    .sort((a, b) => b.path.length - a.path.length)[0];
}
