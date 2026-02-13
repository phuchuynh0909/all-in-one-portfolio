import { useEffect, useRef, useState } from 'react';
import {
  Box,
  TextField,
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
} from '@mui/material';
import { Sync, ShowChart } from '@mui/icons-material';
import { syncStock } from '../lib/services/workflows';
import type { Report } from '../lib/services/report';
import StockChart from '../components/chart/StockChart';

export default function ChartPage() {
  const [symbol, setSymbol] = useState('VNINDEX');
  const [currentSymbol, setCurrentSymbol] = useState('VNINDEX');
  const [isFocused, setIsFocused] = useState(false);
  const [selectedReport, setSelectedReport] = useState<Report | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [snackbar, setSnackbar] = useState<{ open: boolean; message: string; severity: 'success' | 'error' }>({ open: false, message: '', severity: 'success' });
  const chartPaperRef = useRef<HTMLDivElement | null>(null);
  const [chartHeight, setChartHeight] = useState(800);

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

  const handleKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter') {
      setCurrentSymbol(symbol);
      setIsFocused(false);
    }
  };

  const handleFocus = () => {
    setIsFocused(true);
    setSymbol('');
  };

  const handleBlur = () => {
    setIsFocused(false);
    if (!symbol) {
      setSymbol(currentSymbol);
    }
  };

  const handleReportClick = (report: Report) => {
    setSelectedReport(report);
  };

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

  return (
    <Container maxWidth={false} sx={{ py: 4 }}>
      {/* Header */}
      <Box sx={{ mb: 4 }}>
        <Typography
          variant="h4"
          sx={{
            fontWeight: 700,
            background: 'linear-gradient(135deg, #6366f1 0%, #8b5cf6 50%, #a855f7 100%)',
            backgroundClip: 'text',
            WebkitBackgroundClip: 'text',
            WebkitTextFillColor: 'transparent',
            mb: 1,
            display: 'flex',
            alignItems: 'center',
            gap: 1.5,
          }}
        >
          <ShowChart sx={{ fontSize: 36, color: '#6366f1' }} />
          Stock Chart
        </Typography>
        <Typography variant="body2" sx={{ color: 'text.secondary' }}>
          Real-time market data visualization with technical indicators
        </Typography>
      </Box>

      {/* Symbol Selector */}
      <Paper
        sx={{
          p: 2,
          mb: 3,
          background: 'linear-gradient(135deg, rgba(30, 30, 46, 0.9) 0%, rgba(30, 30, 40, 0.95) 100%)',
          border: '1px solid rgba(99, 102, 241, 0.2)',
          borderRadius: 2,
        }}
      >
        <Stack
          direction={{ xs: 'column', sm: 'row' }}
          spacing={2}
          alignItems="center"
          justifyContent="space-between"
        >
          <Stack direction="row" spacing={2} alignItems="center">
            <TextField
              label="Symbol"
              value={symbol}
              onChange={(e) => setSymbol(e.target.value.toUpperCase())}
              onKeyDown={handleKeyDown}
              onFocus={handleFocus}
              onBlur={handleBlur}
              variant="outlined"
              size="small"
              placeholder={isFocused ? 'Enter symbol' : 'Press Enter to update'}
              sx={{
                minWidth: 150,
                '& .MuiOutlinedInput-notchedOutline': {
                  borderColor: 'rgba(99, 102, 241, 0.3)',
                },
                '& .MuiOutlinedInput-root:hover .MuiOutlinedInput-notchedOutline': {
                  borderColor: 'rgba(99, 102, 241, 0.5)',
                },
                '& .MuiOutlinedInput-root.Mui-focused .MuiOutlinedInput-notchedOutline': {
                  borderColor: '#6366f1',
                },
              }}
            />
            <Chip
              label={currentSymbol}
              sx={{
                bgcolor: 'rgba(99, 102, 241, 0.15)',
                color: '#a5b4fc',
                fontWeight: 600,
                fontSize: '0.875rem',
              }}
            />
          </Stack>

          <Button
            variant="outlined"
            onClick={handleSync}
            disabled={syncing}
            startIcon={<Sync sx={{ animation: syncing ? 'spin 1s linear infinite' : 'none' }} />}
            sx={{
              borderColor: 'rgba(99, 102, 241, 0.5)',
              color: '#a5b4fc',
              '&:hover': {
                borderColor: '#6366f1',
                bgcolor: 'rgba(99, 102, 241, 0.1)',
              },
              '@keyframes spin': {
                '0%': { transform: 'rotate(0deg)' },
                '100%': { transform: 'rotate(360deg)' },
              },
            }}
          >
            {syncing ? 'Syncing…' : `Sync ${currentSymbol}`}
          </Button>
        </Stack>
      </Paper>

      {/* Chart */}
      <Paper
        ref={chartPaperRef}
        sx={{
          p: 2,
          background: 'linear-gradient(135deg, rgba(30, 30, 46, 0.9) 0%, rgba(30, 30, 40, 0.95) 100%)',
          border: '1px solid rgba(99, 102, 241, 0.2)',
          borderRadius: 2,
        }}
      >
        <StockChart symbol={currentSymbol} onReportClick={handleReportClick} height={chartHeight} />
      </Paper>

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
