import { useEffect, useRef, useState } from 'react';
import {
  Box,
  Container,
  Paper,
  Dialog,
  DialogTitle,
  DialogContent,
  Typography,
  Link,
  Snackbar,
  Alert,
  Stack,
  Chip,
} from '@mui/material';
import { Article, BarChart, Notes, Radar, SmartToy, ViewList } from '@mui/icons-material';
import { syncStock } from '../lib/services/workflows';
import type { Report } from '../lib/services/report';
import StockChart from '../components/chart/StockChart';
import Watchlist from '../components/chart/Watchlist';
import AnomalyPanel from '../components/chart/AnomalyPanel';
import NotesPanel from '../components/chart/NotesPanel';
import ReportsPanel from '../components/chart/ReportsPanel';
import AgentDecisionsPanel from '../components/chart/AgentDecisionsPanel';
import ChartSideRail from '../components/chart/ChartSideRail';
import PriceDepthPanel from '../components/chart/PriceDepthPanel';
import type { ResolvedWatchList } from '../lib/tv/watchlist';

/** Drag-resize bounds for the right side panel (watchlist + anomalies). */
const SIDE_PANEL_MIN = 220;
const SIDE_PANEL_DEFAULT = 320;
/** Leave the chart at least this much room, whatever the viewport. */
const CHART_MIN_WIDTH = 480;
const SIDE_PANEL_WIDTH_KEY = 'chartSidePanelWidth';
const SIDE_PANEL_KEY = 'chartSidePanel';

/** Which panel the right-hand column is showing, or null when it is closed. */
type SidePanel = 'watchlist' | 'anomalies' | 'notes' | 'reports' | 'agents' | 'priceDepth';

export default function ChartPage() {
  const [currentSymbol, setCurrentSymbol] = useState('VNINDEX');
  const [view, setView] = useState<'chart' | 'largeOrders'>('chart');
  const [selectedReport, setSelectedReport] = useState<Report | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [snackbar, setSnackbar] = useState<{ open: boolean; message: string; severity: 'success' | 'error' }>({ open: false, message: '', severity: 'success' });
  const chartPaperRef = useRef<HTMLDivElement | null>(null);
  const [chartHeight, setChartHeight] = useState(800);
  const [sidePanel, setSidePanel] = useState<SidePanel | null>(() => {
    try {
      const stored = localStorage.getItem(SIDE_PANEL_KEY);
      const known: SidePanel[] = ['watchlist', 'anomalies', 'notes', 'reports', 'agents', 'priceDepth'];
      if (known.includes(stored as SidePanel)) return stored as SidePanel;
      if (stored === 'none') return null;
    } catch {
      /* ignore */
    }
    return 'watchlist';
  });
  // Resolved by StockChart once the widget exists: the library's own Watch List
  // when this is a Trading Terminal build, the app-side list otherwise.
  const [watchList, setWatchList] = useState<ResolvedWatchList | null>(null);
  const [sidePanelWidth, setSidePanelWidth] = useState(() => {
    try {
      const stored = Number(localStorage.getItem(SIDE_PANEL_WIDTH_KEY));
      return Number.isFinite(stored) && stored >= SIDE_PANEL_MIN ? stored : SIDE_PANEL_DEFAULT;
    } catch {
      return SIDE_PANEL_DEFAULT;
    }
  });

  useEffect(() => {
    const updateChartHeight = () => {
      if (!chartPaperRef.current) return;
      const { top } = chartPaperRef.current.getBoundingClientRect();
      const available = window.innerHeight - top - 24;
      setChartHeight(Math.max(360, Math.floor(available)));
    };

    updateChartHeight();
    window.addEventListener('resize', updateChartHeight);
    return () => window.removeEventListener('resize', updateChartHeight);
  }, []);

  const handleSync = async () => {
    try {
      setSyncing(true);
      const sym = currentSymbol.trim();
      if (!sym) return;
      const res = await syncStock(sym);
      setSnackbar({ open: true, message: `Submitted sync for ${sym}: ${res.detail}`, severity: 'success' });
    } catch (e) {
      const message = e instanceof Error ? e.message : 'Failed to submit sync';
      setSnackbar({ open: true, message, severity: 'error' });
    } finally {
      setSyncing(false);
    }
  };

  /** Rail items are radio-like: picking one swaps the panel, picking the open
   *  one closes the column entirely. */
  const handleSelectSidePanel = (panel: SidePanel) => {
    setSidePanel((prev) => {
      const next = prev === panel ? null : panel;
      try { localStorage.setItem(SIDE_PANEL_KEY, next ?? 'none'); } catch { /* ignore */ }
      return next;
    });
  };

  /** Drag the side panel's left edge. Dragging left widens it, so the delta is
   *  inverted relative to the notes drawer, which is anchored the other way. */
  const handleStartSideResize = (event: React.MouseEvent<HTMLDivElement>) => {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = sidePanelWidth;

    const handleMouseMove = (moveEvent: MouseEvent) => {
      const delta = startX - moveEvent.clientX;
      // Cap against the viewport so the chart can never be squeezed away.
      const maxWidth = Math.max(SIDE_PANEL_MIN, window.innerWidth - CHART_MIN_WIDTH);
      setSidePanelWidth(Math.min(maxWidth, Math.max(SIDE_PANEL_MIN, startWidth + delta)));
    };

    const handleMouseUp = () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };

    // Held on <body> so the cursor survives the pointer leaving the 6px grip,
    // and so dragging across the chart does not select text.
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);
  };

  useEffect(() => {
    try {
      localStorage.setItem(SIDE_PANEL_WIDTH_KEY, String(sidePanelWidth));
    } catch {
      /* ignore */
    }
  }, [sidePanelWidth]);

  const canShowWatchlist = Boolean(watchList && !watchList.native);
  const activePanel: SidePanel | null =
    sidePanel === 'watchlist' && !canShowWatchlist ? null : sidePanel;

  return (
    <Container
      maxWidth={false}
      sx={{
        py: 2,
        minHeight: '100vh',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      {/* Chart — symbol picker, view toggle and sync now live in the widget header.
          The watchlist beside it is the app's stand-in for the library's Watch
          List widget (Trading Terminal only): same `IWatchListApi`, so if that
          build is ever served the native widget renders in its own widget bar
          and this panel — along with its rail toggle — drops out. */}
      <Box sx={{ display: 'flex', gap: 2, flex: 1, minHeight: 0 }}>
        <Paper
          ref={chartPaperRef}
          sx={{
            p: 2,
            flex: 1,
            minWidth: 0,
            minHeight: 0,
            position: 'relative',
            background: 'var(--color-bg-surface)',
            border: '1px solid var(--color-border-subtle)',
            borderRadius: 2,
          }}
        >
          <StockChart
            symbol={currentSymbol}
            onSymbolChange={setCurrentSymbol}
            height={chartHeight}
            showLargeOrders={view === 'largeOrders'}
            onToggleLargeOrders={(v) => setView(v ? 'largeOrders' : 'chart')}
            onSync={handleSync}
            syncing={syncing}
            onWatchListResolved={setWatchList}
          />
        </Paper>

        {activePanel && (
          <Paper
            sx={{
              width: sidePanelWidth,
              flexShrink: 0,
              p: 1,
              pl: 0,
              // Match StockChart's measured height rather than stretching to the
              // row, so the panel's own internal scrolling has a definite height
              // to resolve against.
              height: chartHeight,
              alignSelf: 'flex-start',
              overflow: 'hidden',
              display: 'flex',
              minHeight: 0,
              background: 'var(--color-bg-surface)',
              border: '1px solid var(--color-border-subtle)',
              borderRadius: 2,
            }}
          >
            {/* Drag grip on the panel's left edge. */}
            <Box
              onMouseDown={handleStartSideResize}
              sx={{
                width: 6,
                flexShrink: 0,
                cursor: 'col-resize',
                borderRadius: 1,
                transition: 'background 120ms',
                '&:hover': { background: 'var(--color-border-default)' },
              }}
            />

            {/* One panel at a time — the rail decides which. Both bring their
                own header, so nothing is added around them here. */}
            <Box sx={{ flex: 1, minWidth: 0, minHeight: 0, display: 'flex' }}>
              {activePanel === 'watchlist' && watchList ? (
                <Watchlist
                  api={watchList.api}
                  activeSymbol={currentSymbol}
                  onSelect={setCurrentSymbol}
                />
              ) : null}
              {activePanel === 'anomalies' ? (
                <AnomalyPanel symbol={currentSymbol} days={5} />
              ) : null}
              {activePanel === 'notes' ? <NotesPanel symbol={currentSymbol} /> : null}
              {activePanel === 'reports' ? (
                <ReportsPanel symbol={currentSymbol} onSelect={setSelectedReport} />
              ) : null}
              {activePanel === 'priceDepth' ? <PriceDepthPanel symbol={currentSymbol} /> : null}
              {activePanel === 'agents' ? <AgentDecisionsPanel symbol={currentSymbol} /> : null}
            </Box>
          </Paper>
        )}

        {/* Right-edge rail: one icon per side panel, highlighted while open.
            The watchlist toggle is dropped when the library shows its own list. */}
        <ChartSideRail
          items={[
            ...(canShowWatchlist
              ? [{
                  id: 'watchlist',
                  label: activePanel === 'watchlist' ? 'Hide watchlist' : 'Watchlist',
                  icon: <ViewList sx={{ fontSize: 19 }} />,
                  active: activePanel === 'watchlist',
                  onClick: () => handleSelectSidePanel('watchlist'),
                }]
              : []),
            {
              id: 'anomalies',
              label: activePanel === 'anomalies' ? 'Hide anomalies' : `Anomalies for ${currentSymbol}`,
              icon: <Radar sx={{ fontSize: 19 }} />,
              active: activePanel === 'anomalies',
              onClick: () => handleSelectSidePanel('anomalies'),
            },
            {
              id: 'notes',
              label: activePanel === 'notes' ? 'Hide notes' : `Notes for ${currentSymbol}`,
              icon: <Notes sx={{ fontSize: 19 }} />,
              active: activePanel === 'notes',
              onClick: () => handleSelectSidePanel('notes'),
            },
            {
              id: 'reports',
              label: activePanel === 'reports' ? 'Hide reports' : `Reports for ${currentSymbol}`,
              icon: <Article sx={{ fontSize: 19 }} />,
              active: activePanel === 'reports',
              onClick: () => handleSelectSidePanel('reports'),
            },
            {
              id: 'agents',
              label: activePanel === 'agents' ? 'Hide agent calls' : `Agent calls for ${currentSymbol}`,
              icon: <SmartToy sx={{ fontSize: 19 }} />,
              active: activePanel === 'agents',
              onClick: () => handleSelectSidePanel('agents'),
            },
            {
              id: 'priceDepth',
              label: activePanel === 'priceDepth' ? 'Hide price depth' : `Price depth for ${currentSymbol}`,
              icon: <BarChart sx={{ fontSize: 19 }} />,
              active: activePanel === 'priceDepth',
              onClick: () => handleSelectSidePanel('priceDepth'),
            },
          ]}
        />
      </Box>

      {/* Report Dialog */}
      <Dialog
        open={!!selectedReport}
        onClose={() => setSelectedReport(null)}
        maxWidth="md"
        fullWidth
        PaperProps={{
          sx: {
            background: 'var(--color-bg-surface)',
            border: '1px solid var(--color-border-default)',
            borderRadius: 2,
          },
        }}
      >
        {selectedReport && (
          <>
            <DialogTitle
              sx={{
                borderBottom: '1px solid var(--color-border-subtle)',
                fontWeight: 600,
                color: 'var(--color-text-primary)',
              }}
            >
              Research Report
            </DialogTitle>
            <DialogContent>
              <Box sx={{ py: 3 }}>
                <Typography variant="h6" gutterBottom sx={{ color: 'var(--color-text-primary)', fontWeight: 600 }}>
                  {selectedReport.tenbaocao}
                </Typography>
                <Stack spacing={1.5} sx={{ mt: 2 }}>
                  <Box sx={{ display: 'flex', gap: 1 }}>
                    <Typography variant="body2" sx={{ color: 'var(--color-text-tertiary)', minWidth: 80 }}>
                      Symbol:
                    </Typography>
                    <Chip
                      label={selectedReport.mack}
                      size="small"
                      sx={{
                        bgcolor: 'var(--color-border-subtle)',
                        color: 'var(--color-accent)',
                        fontWeight: 500,
                      }}
                    />
                  </Box>
                  <Box sx={{ display: 'flex', gap: 1 }}>
                    <Typography variant="body2" sx={{ color: 'var(--color-text-tertiary)', minWidth: 80 }}>
                      Source:
                    </Typography>
                    <Typography variant="body2" sx={{ color: 'var(--color-text-primary)' }}>
                      {selectedReport.nguon}
                    </Typography>
                  </Box>
                  <Box sx={{ display: 'flex', gap: 1 }}>
                    <Typography variant="body2" sx={{ color: 'var(--color-text-tertiary)', minWidth: 80 }}>
                      Date:
                    </Typography>
                    <Typography variant="body2" sx={{ color: 'var(--color-text-primary)' }}>
                      {selectedReport.ngaykn ? new Date(selectedReport.ngaykn).toLocaleDateString() : 'N/A'}
                    </Typography>
                  </Box>
                  <Box sx={{ display: 'flex', gap: 1 }}>
                    <Typography variant="body2" sx={{ color: 'var(--color-text-tertiary)', minWidth: 80 }}>
                      Sector:
                    </Typography>
                    <Typography variant="body2" sx={{ color: 'var(--color-text-primary)' }}>
                      {selectedReport.rsnganh || 'N/A'}
                    </Typography>
                  </Box>
                </Stack>
                <Box sx={{ mt: 3 }}>
                  <Link
                    href={selectedReport.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    sx={{
                      color: 'var(--color-accent)',
                      fontWeight: 500,
                      textDecoration: 'none',
                      '&:hover': {
                        textDecoration: 'underline',
                        color: 'var(--color-accent)',
                      },
                    }}
                  >
                    View Full Report →
                  </Link>
                </Box>
              </Box>
            </DialogContent>
          </>
        )}
      </Dialog>

      <Snackbar
        open={snackbar.open}
        autoHideDuration={4000}
        onClose={() => setSnackbar({ ...snackbar, open: false })}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
      >
        <Alert
          onClose={() => setSnackbar({ ...snackbar, open: false })}
          severity={snackbar.severity}
          sx={{
            bgcolor: snackbar.severity === 'success' ? 'var(--color-long-subtle)' : 'var(--color-short-subtle)',
            color: snackbar.severity === 'success' ? 'var(--color-long)' : 'var(--color-short)',
            border: `1px solid ${snackbar.severity === 'success' ? 'var(--color-long)' : 'var(--color-short)'}`,
          }}
        >
          {snackbar.message}
        </Alert>
      </Snackbar>
    </Container>
  );
}
