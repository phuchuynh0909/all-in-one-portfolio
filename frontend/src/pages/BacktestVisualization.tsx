import { useState } from 'react';
import {
  Box,
  Container,
  Typography,
  Paper,
  Stack,
  CircularProgress,
  Alert,
  Autocomplete,
  TextField,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableRow,
} from '@mui/material';
import { useBacktestPlot, useWatchlistSymbols } from '../lib/services/backtest';

export default function BacktestVisualization() {
  const [selectedSymbol, setSelectedSymbol] = useState<string>('VCG');
  const [selectedStrategy, setSelectedStrategy] = useState<string>('Breakout TTM');

  const strategies = ['Breakout DeMarker', 'Breakout TTM'];

  const { data: symbolsData } = useWatchlistSymbols();
  const symbols = symbolsData || [];

  const { data, isLoading, error } = useBacktestPlot(selectedSymbol, undefined, selectedStrategy);

  const formatStatValue = (value: unknown) => {
    if (value === null || value === undefined) return '—';
    if (typeof value === 'number') return value.toFixed(4);
    return String(value);
  };

  return (
    <Container maxWidth="xl" sx={{ py: 4 }}>
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
          }}
        >
          Backtest Visualization
        </Typography>
        <Typography variant="body2" sx={{ color: 'text.secondary' }}>
          Backtesting.py interactive chart
          {symbols.length > 0 && ` • ${symbols.length} symbols available`}
        </Typography>
      </Box>

      <Paper
        sx={{
          p: 2,
          mb: 3,
          background: 'linear-gradient(135deg, rgba(30, 30, 46, 0.9) 0%, rgba(30, 30, 40, 0.95) 100%)',
          border: '1px solid rgba(99, 102, 241, 0.2)',
          borderRadius: 2,
        }}
      >
        <Stack direction="row" alignItems="center" justifyContent="space-between" flexWrap="wrap" gap={2}>
          <Autocomplete<string, false, true, false>
            value={selectedSymbol}
            onChange={(_event, newValue) => {
              if (newValue) setSelectedSymbol(newValue);
            }}
            options={symbols}
            sx={{
              minWidth: 150,
              '& .MuiOutlinedInput-notchedOutline': {
                borderColor: 'rgba(99, 102, 241, 0.3)',
              },
            }}
            renderInput={(params) => <TextField {...params} label="Symbol" size="small" />}
            disableClearable
            autoHighlight
          />
          <Autocomplete<string, false, true, false>
            value={selectedStrategy}
            onChange={(_event, newValue) => {
              if (newValue) setSelectedStrategy(newValue);
            }}
            options={strategies}
            sx={{
              minWidth: 220,
              '& .MuiOutlinedInput-notchedOutline': {
                borderColor: 'rgba(99, 102, 241, 0.3)',
              },
            }}
            renderInput={(params) => <TextField {...params} label="Strategy" size="small" />}
            disableClearable
            autoHighlight
          />
        </Stack>
      </Paper>

      {isLoading && (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 8 }}>
          <CircularProgress />
        </Box>
      )}

      {error && (
        <Alert severity="error" sx={{ mb: 3 }}>
          {error instanceof Error ? error.message : 'Failed to load backtest plot'}
        </Alert>
      )}

      {data?.html && !isLoading && (
        <Paper
          sx={{
            p: 2,
            background: 'transparent',
            border: 'none',
            boxShadow: 'none',
          }}
        >
          <Box
            sx={{
              width: '100%',
              minHeight: 720,
              borderRadius: 2,
              overflow: 'hidden',
              border: '1px solid rgba(99, 102, 241, 0.15)',
              backgroundColor: 'rgba(10, 10, 20, 0.6)',
            }}
          >
            <iframe
              title={`Backtest plot for ${data.symbol}`}
              srcDoc={data.html}
              style={{ width: '100%', height: 720, border: 'none' }}
              sandbox="allow-scripts allow-same-origin"
            />
          </Box>
        </Paper>
      )}

      {data?.stats && !isLoading && (
        <Paper
          sx={{
            p: 2,
            mt: 3,
            border: '1px solid rgba(99, 102, 241, 0.2)',
            borderRadius: 2,
            background: 'linear-gradient(135deg, rgba(30, 30, 46, 0.9) 0%, rgba(30, 30, 40, 0.95) 100%)',
          }}
        >
          <Typography variant="subtitle1" sx={{ mb: 1.5, fontWeight: 600 }}>
            Backtest Stats
          </Typography>
          <TableContainer>
            <Table size="small">
              <TableBody>
                {Object.entries(data.stats).map(([key, value]) => (
                  <TableRow key={key}>
                    <TableCell sx={{ borderBottom: '1px solid rgba(99, 102, 241, 0.1)' }}>
                      {key}
                    </TableCell>
                    <TableCell
                      align="right"
                      sx={{ borderBottom: '1px solid rgba(99, 102, 241, 0.1)', fontFamily: 'monospace' }}
                    >
                      {formatStatValue(value)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        </Paper>
      )}
    </Container>
  );
}
