import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Box,
  Container,
  Paper,
  Dialog,
  DialogTitle,
  DialogContent,
  Typography,
  Link,
  Button,
  Snackbar,
  Alert,
  Stack,
  Chip,
  Divider,
  Drawer,
  Fab,
} from '@mui/material';
import { Notes, ViewList } from '@mui/icons-material';
import { syncStock } from '../lib/services/workflows';
import type { Report } from '../lib/services/report';
import { getChatNotes, type ChatNoteItem } from '../lib/services/chat';
import { MarkdownContent } from '../components/chat/MarkdownContent';
import StockChart from '../components/chart/StockChart';
import Watchlist from '../components/chart/Watchlist';
import AnomalyPanel from '../components/chart/AnomalyPanel';
import ChartSideRail from '../components/chart/ChartSideRail';
import type { ResolvedWatchList } from '../lib/tv/watchlist';

/** Drag-resize bounds for the right side panel (watchlist + anomalies). */
const SIDE_PANEL_MIN = 220;
const SIDE_PANEL_DEFAULT = 320;
/** Leave the chart at least this much room, whatever the viewport. */
const CHART_MIN_WIDTH = 480;
const SIDE_PANEL_WIDTH_KEY = 'chartSidePanelWidth';

export default function ChartPage() {
  const [currentSymbol, setCurrentSymbol] = useState('VNINDEX');
  const [view, setView] = useState<'chart' | 'largeOrders'>('chart');
  const [selectedReport, setSelectedReport] = useState<Report | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [snackbar, setSnackbar] = useState<{ open: boolean; message: string; severity: 'success' | 'error' }>({ open: false, message: '', severity: 'success' });
  const chartPaperRef = useRef<HTMLDivElement | null>(null);
  const [chartHeight, setChartHeight] = useState(800);
  const [notesOpen, setNotesOpen] = useState(false);
  const [watchlistOpen, setWatchlistOpen] = useState(() => {
    try { return localStorage.getItem('chartWatchlistOpen') !== '0'; } catch { return true; }
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
  const [notesLoading, setNotesLoading] = useState(false);
  const [notesError, setNotesError] = useState<string | null>(null);
  const [notes, setNotes] = useState<ChatNoteItem[]>([]);
  const [drawerWidth, setDrawerWidth] = useState(560);

  const loadNotes = useCallback(async () => {
    const sym = currentSymbol.trim().toUpperCase();
    if (!sym) {
      setNotes([]);
      return;
    }
    setNotesLoading(true);
    setNotesError(null);
    try {
      const response = await getChatNotes(sym);
      setNotes(response.notes);
    } catch (error) {
      setNotesError(error instanceof Error ? error.message : 'Failed to load notes');
    } finally {
      setNotesLoading(false);
    }
  }, [currentSymbol]);

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

  const handleToggleWatchlist = () => {
    setWatchlistOpen((prev) => {
      const next = !prev;
      try { localStorage.setItem('chartWatchlistOpen', next ? '1' : '0'); } catch { /* ignore */ }
      return next;
    });
  };

  const handleOpenNotes = async () => {
    setNotesOpen(true);
    await loadNotes();
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

  const handleStartResize = (event: React.MouseEvent<HTMLDivElement>) => {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = drawerWidth;

    const handleMouseMove = (moveEvent: MouseEvent) => {
      const delta = moveEvent.clientX - startX;
      const maxWidth = Math.max(420, window.innerWidth - 32);
      const nextWidth = Math.min(maxWidth, Math.max(360, startWidth + delta));
      setDrawerWidth(nextWidth);
    };

    const handleMouseUp = () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };

    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);
  };

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
            background: 'linear-gradient(135deg, rgba(30, 30, 46, 0.9) 0%, rgba(30, 30, 40, 0.95) 100%)',
            border: '1px solid rgba(99, 102, 241, 0.2)',
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

        {watchlistOpen && watchList && !watchList.native && (
          <Paper
            sx={{
              width: sidePanelWidth,
              flexShrink: 0,
              p: 1,
              pl: 0,
              // Match StockChart's measured height instead of stretching to the
              // row. This also makes the inner 60/40 split resolve properly: a
              // percentage maxHeight needs a definite height to resolve against,
              // and a stretched flex item does not reliably provide one.
              height: chartHeight,
              alignSelf: 'flex-start',
              overflow: 'hidden',
              display: 'flex',
              minHeight: 0,
              background: 'linear-gradient(135deg, rgba(30, 30, 46, 0.9) 0%, rgba(30, 30, 40, 0.95) 100%)',
              border: '1px solid rgba(99, 102, 241, 0.2)',
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
                '&:hover': { background: 'rgba(99,102,241,0.45)' },
              }}
            />

            <Box
              sx={{
                flex: 1,
                minWidth: 0,
                minHeight: 0,
                display: 'flex',
                flexDirection: 'column',
                gap: 0.5,
              }}
            >
              {/* Content-sized, capped at 60% so a long list scrolls instead of
                  pushing the anomalies panel out of view. Not `1 1 auto`: that
                  stretches a short list and leaves dead space beneath it. */}
              <Box
                sx={{
                  flex: '0 1 auto',
                  minHeight: 0,
                  maxHeight: '60%',
                  display: 'flex',
                  minWidth: 0,
                }}
              >
                <Watchlist
                  api={watchList.api}
                  activeSymbol={currentSymbol}
                  onSelect={setCurrentSymbol}
                />
              </Box>

              <Divider sx={{ borderColor: 'rgba(255,255,255,0.08)', flexShrink: 0 }} />

              {/* Absorbs everything the watchlist does not use. */}
              <Box sx={{ flex: '1 1 auto', minHeight: 120, display: 'flex', minWidth: 0 }}>
                <AnomalyPanel symbol={currentSymbol} days={5} />
              </Box>
            </Box>
          </Paper>
        )}

        {/* Right-edge rail: one icon per side panel, highlighted while open.
            The watchlist toggle is dropped when the library shows its own. */}
        <ChartSideRail
          items={watchList && !watchList.native ? [{
            id: 'watchlist',
            label: watchlistOpen ? 'Hide watchlist' : 'Show watchlist',
            icon: <ViewList sx={{ fontSize: 19 }} />,
            active: watchlistOpen,
            onClick: handleToggleWatchlist,
          }] : []}
        />
      </Box>

      <Fab
        color="primary"
        onClick={handleOpenNotes}
        size="large"
        sx={{
          position: 'fixed',
          right: 24,
          bottom: 24,
          zIndex: 1200,
          width: 50,
          height: 50,
        }}
      >
        <Notes sx={{ fontSize: 34 }} />
      </Fab>

      <Drawer
        anchor="right"
        open={notesOpen}
        onClose={() => setNotesOpen(false)}
        PaperProps={{ sx: { width: { xs: '100%', sm: drawerWidth }, p: 3, position: 'relative' } }}
      >
        <Box
          onMouseDown={handleStartResize}
          sx={{
            position: 'absolute',
            top: 0,
            bottom: 0,
            right: 0,
            width: 10,
            cursor: 'col-resize',
            zIndex: 2,
            display: { xs: 'none', sm: 'block' },
          }}
        />
        <Stack spacing={2} sx={{ height: '100%' }}>
          <Stack direction="row" justifyContent="space-between" alignItems="center">
            <Typography variant="h5">Notes ({currentSymbol})</Typography>
            <Button size="small" onClick={loadNotes} disabled={notesLoading}>
              {notesLoading ? 'Loading...' : 'Refresh'}
            </Button>
          </Stack>

          {notesError ? <Alert severity="error">{notesError}</Alert> : null}

          <Box sx={{ overflowY: 'auto', flex: 1, pr: 0.5 }}>
            <Stack spacing={1.5}>
              {!notesLoading && notes.length === 0 ? (
                <Typography variant="body2" color="text.secondary">
                  No notes found for this symbol.
                </Typography>
              ) : null}
              {notes.map((note, idx) => (
                <Paper
                  key={`${note.created_at}-${idx}`}
                  sx={{
                    p: 2,
                    border: '1px solid',
                    borderColor: 'rgba(99, 102, 241, 0.25)',
                    background: 'linear-gradient(135deg, rgba(30, 30, 46, 0.9) 0%, rgba(30, 30, 40, 0.95) 100%)',
                  }}
                >
                  <Typography
                    variant="body2"
                    sx={{
                      color: '#a5b4fc',
                      fontFamily: "'SF Mono', 'Fira Code', 'Monaco', monospace",
                      letterSpacing: 0.2,
                    }}
                  >
                    {note.created_at ? new Date(note.created_at).toLocaleString() : 'N/A'}
                  </Typography>
                  <Box
                    sx={{
                      mt: 1,
                      color: '#f1f5f9',
                      fontWeight: 500,
                      fontSize: '1rem',
                      lineHeight: 1.75,
                    }}
                  >
                    <MarkdownContent content={note.message} />
                  </Box>
                </Paper>
              ))}
            </Stack>
          </Box>
        </Stack>
      </Drawer>

      {/* Report Dialog */}
      <Dialog
        open={!!selectedReport}
        onClose={() => setSelectedReport(null)}
        maxWidth="md"
        fullWidth
        PaperProps={{
          sx: {
            background: 'linear-gradient(135deg, rgba(30, 30, 46, 0.98) 0%, rgba(30, 30, 40, 0.98) 100%)',
            border: '1px solid rgba(99, 102, 241, 0.3)',
            borderRadius: 2,
          },
        }}
      >
        {selectedReport && (
          <>
            <DialogTitle
              sx={{
                borderBottom: '1px solid rgba(99, 102, 241, 0.2)',
                fontWeight: 600,
                color: '#e2e8f0',
              }}
            >
              Research Report
            </DialogTitle>
            <DialogContent>
              <Box sx={{ py: 3 }}>
                <Typography variant="h6" gutterBottom sx={{ color: '#f1f5f9', fontWeight: 600 }}>
                  {selectedReport.tenbaocao}
                </Typography>
                <Stack spacing={1.5} sx={{ mt: 2 }}>
                  <Box sx={{ display: 'flex', gap: 1 }}>
                    <Typography variant="body2" sx={{ color: '#6b7280', minWidth: 80 }}>
                      Symbol:
                    </Typography>
                    <Chip
                      label={selectedReport.mack}
                      size="small"
                      sx={{
                        bgcolor: 'rgba(99, 102, 241, 0.15)',
                        color: '#a5b4fc',
                        fontWeight: 500,
                      }}
                    />
                  </Box>
                  <Box sx={{ display: 'flex', gap: 1 }}>
                    <Typography variant="body2" sx={{ color: '#6b7280', minWidth: 80 }}>
                      Source:
                    </Typography>
                    <Typography variant="body2" sx={{ color: '#e2e8f0' }}>
                      {selectedReport.nguon}
                    </Typography>
                  </Box>
                  <Box sx={{ display: 'flex', gap: 1 }}>
                    <Typography variant="body2" sx={{ color: '#6b7280', minWidth: 80 }}>
                      Date:
                    </Typography>
                    <Typography variant="body2" sx={{ color: '#e2e8f0' }}>
                      {selectedReport.ngaykn ? new Date(selectedReport.ngaykn).toLocaleDateString() : 'N/A'}
                    </Typography>
                  </Box>
                  <Box sx={{ display: 'flex', gap: 1 }}>
                    <Typography variant="body2" sx={{ color: '#6b7280', minWidth: 80 }}>
                      Sector:
                    </Typography>
                    <Typography variant="body2" sx={{ color: '#e2e8f0' }}>
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
                      color: '#6366f1',
                      fontWeight: 500,
                      textDecoration: 'none',
                      '&:hover': {
                        textDecoration: 'underline',
                        color: '#818cf8',
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
            bgcolor: snackbar.severity === 'success' ? 'rgba(34, 197, 94, 0.15)' : 'rgba(239, 68, 68, 0.15)',
            color: snackbar.severity === 'success' ? '#22c55e' : '#ef4444',
            border: `1px solid ${snackbar.severity === 'success' ? 'rgba(34, 197, 94, 0.3)' : 'rgba(239, 68, 68, 0.3)'}`,
          }}
        >
          {snackbar.message}
        </Alert>
      </Snackbar>
    </Container>
  );
}
