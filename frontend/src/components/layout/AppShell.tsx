import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { Link, useLocation } from 'react-router-dom';
import {
  AppBar,
  Box,
  Divider,
  Drawer,
  IconButton,
  Toolbar,
  Tooltip,
  Typography,
  useMediaQuery,
  useTheme,
} from '@mui/material';
import MenuIcon from '@mui/icons-material/Menu';
import MenuOpenIcon from '@mui/icons-material/MenuOpen';
import GitHubIcon from '@mui/icons-material/GitHub';
import { layout } from '../../theme';
import BrandMark from './BrandMark';
import SidebarNav from './SidebarNav';
import ModeToggle from './ModeToggle';
import DeskClock from './DeskClock';
import { findNavItem } from './navigation';

const COLLAPSE_KEY = 'aiop.sidebar-collapsed';

export default function AppShell({ children }: { children: ReactNode }) {
  const theme = useTheme();
  const isDesktop = useMediaQuery(theme.breakpoints.up('md'));
  const { pathname } = useLocation();

  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem(COLLAPSE_KEY) === '1';
    } catch {
      return false;
    }
  });
  const [mobileOpen, setMobileOpen] = useState(false);

  useEffect(() => {
    try {
      localStorage.setItem(COLLAPSE_KEY, collapsed ? '1' : '0');
    } catch {
      // Best-effort only.
    }
  }, [collapsed]);

  // Close the mobile drawer whenever the route changes.
  useEffect(() => setMobileOpen(false), [pathname]);

  const toggleSidebar = useCallback(() => {
    if (isDesktop) setCollapsed((v) => !v);
    else setMobileOpen((v) => !v);
  }, [isDesktop]);

  const current = findNavItem(pathname);
  const sidebarWidth = collapsed ? layout.sidebarCollapsedWidth : layout.sidebarWidth;

  const sidebarContent = (
    <>
      <Toolbar
        disableGutters
        sx={{
          px: collapsed ? 0 : 2,
          justifyContent: collapsed ? 'center' : 'flex-start',
          minHeight: layout.appBarHeight,
          borderBottom: 1,
          borderColor: 'line.subtle',
        }}
      >
        <Box component={Link} to="/" sx={{ textDecoration: 'none', display: 'flex' }}>
          <BrandMark collapsed={collapsed && isDesktop} />
        </Box>
      </Toolbar>

      <SidebarNav collapsed={collapsed && isDesktop} onNavigate={() => setMobileOpen(false)} />

      <Divider sx={{ borderColor: 'line.subtle' }} />
      <Box
        sx={{
          p: 1,
          display: 'flex',
          alignItems: 'center',
          justifyContent: collapsed && isDesktop ? 'center' : 'space-between',
        }}
      >
        {(!collapsed || !isDesktop) && (
          <Typography variant="overline2" sx={{ pl: 1, fontSize: '0.5625rem' }}>
            v0.1 · local
          </Typography>
        )}
        <Tooltip title="Source">
          <IconButton
            size="small"
            component="a"
            href="https://github.com/phuchuynh0909"
            target="_blank"
            rel="noreferrer"
            aria-label="Source repository"
          >
            <GitHubIcon sx={{ fontSize: 16 }} />
          </IconButton>
        </Tooltip>
      </Box>
    </>
  );

  return (
    <Box sx={{ display: 'flex', minHeight: '100vh', bgcolor: 'surface.canvas' }}>
      {/* Sidebar — permanent on desktop, temporary drawer on mobile. */}
      {isDesktop ? (
        <Box
          component="aside"
          sx={{
            width: sidebarWidth,
            flexShrink: 0,
            position: 'fixed',
            insetBlock: 0,
            left: 0,
            zIndex: theme.zIndex.appBar + 1,
            display: 'flex',
            flexDirection: 'column',
            bgcolor: 'surface.chrome',
            borderRight: 1,
            borderColor: 'line.subtle',
            transition: theme.transitions.create('width', { duration: 180 }),
          }}
        >
          {sidebarContent}
        </Box>
      ) : (
        <Drawer
          open={mobileOpen}
          onClose={() => setMobileOpen(false)}
          ModalProps={{ keepMounted: true }}
          PaperProps={{
            sx: { width: layout.sidebarWidth, display: 'flex', flexDirection: 'column' },
          }}
        >
          {sidebarContent}
        </Drawer>
      )}

      {/* Main column */}
      <Box
        sx={{
          flex: 1,
          minWidth: 0,
          display: 'flex',
          flexDirection: 'column',
          ml: isDesktop ? sidebarWidth : 0,
          transition: theme.transitions.create('margin-left', { duration: 180 }),
        }}
      >
        <AppBar position="sticky">
          <Toolbar sx={{ gap: 1.5, px: { xs: 1.5, md: 2.5 } }}>
            <IconButton
              onClick={toggleSidebar}
              size="small"
              edge="start"
              aria-label={collapsed ? 'Expand navigation' : 'Collapse navigation'}
            >
              {isDesktop && !collapsed ? (
                <MenuOpenIcon fontSize="small" />
              ) : (
                <MenuIcon fontSize="small" />
              )}
            </IconButton>

            <Typography
              variant="subtitle2"
              sx={{ fontWeight: 600, color: 'text.primary', flexShrink: 0 }}
            >
              {current?.label ?? 'Quant Terminal'}
            </Typography>

            <Box sx={{ flex: 1 }} />
            <DeskClock />
            <Divider
              orientation="vertical"
              flexItem
              sx={{ my: 1.25, display: { xs: 'none', md: 'block' } }}
            />
            <ModeToggle />
          </Toolbar>
        </AppBar>

        <Box component="main" sx={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
          {children}
        </Box>
      </Box>
    </Box>
  );
}
