import { useState, useEffect, useCallback } from 'react';
import {
  Box,
  Typography,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Chip,
  CircularProgress,
  Alert,
  Stack,
  ToggleButtonGroup,
  ToggleButton,
  TextField,
  IconButton,
  Collapse,
} from '@mui/material';
import KeyboardArrowDownIcon from '@mui/icons-material/KeyboardArrowDown';
import KeyboardArrowUpIcon from '@mui/icons-material/KeyboardArrowUp';
import { getLatestAlerts } from '../lib/services/ispAlerts';
import type { ISPAlert } from '../lib/services/ispAlerts';

interface GroupedAlert {
  symbol: string;
  latest: ISPAlert;
  history: ISPAlert[];
  count: number;
}

const Live = () => {
  const [alerts, setAlerts] = useState<ISPAlert[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);
  const [refreshInterval, setRefreshInterval] = useState(1000); // milliseconds
  const [filterSymbol, setFilterSymbol] = useState('');
  const [isInitialLoad, setIsInitialLoad] = useState(true);
  const [expandedRows, setExpandedRows] = useState<Set<string>>(new Set());

  // Get the maximum timestamp from current alerts (in milliseconds)
  const getMaxTimestamp = useCallback((): number | null => {
    if (alerts.length === 0) return null;
    const maxTs = alerts.reduce((max, alert) => {
      return alert.ts > max ? alert.ts : max;
    }, 0);
    return maxTs;
  }, [alerts]);

  // Get start of today as Unix timestamp in milliseconds
  const getTodayStart = (): number => {
    const today = new Date(); 
    today.setDate(today.getDate()-2);
    today.setHours(0, 0, 0, 0);
    return today.getTime();
  };

  const fetchAlerts = useCallback(async () => {
    try {
      let data: ISPAlert[];
      
      if (isInitialLoad) {
        // Initial load: Get all alerts from today
        const todayStart = getTodayStart();
        data = await getLatestAlerts({
          limit: 5000,
          since: todayStart,
        });
        setAlerts(data);
        setIsInitialLoad(false);
      } else {
        // Incremental load: Get only new alerts since max timestamp
        const maxTs = getMaxTimestamp();
        if (maxTs) {
          data = await getLatestAlerts({
            limit: 1000,
            since: maxTs,
          });
          
          // Filter out duplicates and append new alerts
          const existingTimestamps = new Set(alerts.map(a => a.ts));
          const newAlerts = data.filter(alert => !existingTimestamps.has(alert.ts));
          
          if (newAlerts.length > 0) {
            setAlerts(prev => [...newAlerts, ...prev].slice(0, 5000)); // Keep max 5000 alerts
          }
        }
      }
      
      setLastUpdate(new Date());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch alerts');
    } finally {
      setLoading(false);
    }
  }, [isInitialLoad, getMaxTimestamp, alerts]);

  // Initial load
  useEffect(() => {
    fetchAlerts();
  }, []); // Only run once on mount

  // Auto-refresh for incremental updates
  useEffect(() => {
    if (isInitialLoad) return; // Don't start interval until initial load is done
    
    const interval = setInterval(() => {
      fetchAlerts();
    }, refreshInterval);

    return () => clearInterval(interval);
  }, [refreshInterval, isInitialLoad]); // Removed fetchAlerts from deps to avoid recreation

  // Separate effect for periodic fetching
  useEffect(() => {
    if (!isInitialLoad) {
      fetchAlerts();
    }
  }, [isInitialLoad]);

  const getMaxAbnormality = (alert: ISPAlert): number => {
    return Math.max(
      alert.abnormality_ratio_5m,
      alert.abnormality_ratio_15m,
      alert.abnormality_ratio_30m,
      alert.abnormality_ratio_60m
    );
  };

  const getSeverity = (ratio: number): { label: string; color: 'default' | 'info' | 'warning' | 'error' } => {
    if (ratio >= 4) return { label: 'EXPLOSION', color: 'error' };
    if (ratio >= 3) return { label: 'ALERT', color: 'error' };
    if (ratio >= 2) return { label: 'WARNING', color: 'warning' };
    if (ratio >= 1) return { label: 'NORMAL', color: 'info' };
    return { label: 'LOW', color: 'default' };
  };

  const formatTimestamp = (ts: number): string => {
    const date = new Date(ts);
    return date.toLocaleTimeString('en-US', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
      timeZone: 'Asia/Ho_Chi_Minh', // UTC+7
    });
  };

  const formatRatio = (ratio: number): string => {
    return ratio.toFixed(2);
  };

  // Group alerts by symbol and get latest for each
  const groupAlertsBySymbol = useCallback((): GroupedAlert[] => {
    const grouped = new Map<string, ISPAlert[]>();
    
    // Group all alerts by symbol
    alerts.forEach(alert => {
      if (!grouped.has(alert.symbol)) {
        grouped.set(alert.symbol, []);
      }
      grouped.get(alert.symbol)!.push(alert);
    });
    
    // Create grouped alerts with latest first
    const result: GroupedAlert[] = [];
    grouped.forEach((symbolAlerts, symbol) => {
      // Sort by timestamp descending
      const sorted = symbolAlerts.sort((a, b) => b.ts - a.ts);
      result.push({
        symbol,
        latest: sorted[0],
        history: sorted.slice(1),
        count: sorted.length,
      });
    });
    
    // Sort by latest timestamp
    return result.sort((a, b) => b.latest.ts - a.latest.ts);
  }, [alerts]);

  // Toggle row expansion
  const toggleRow = (symbol: string) => {
    setExpandedRows(prev => {
      const newSet = new Set(prev);
      if (newSet.has(symbol)) {
        newSet.delete(symbol);
      } else {
        newSet.add(symbol);
      }
      return newSet;
    });
  };

  // Filter grouped alerts by symbol
  const groupedAlerts = groupAlertsBySymbol();
  const filteredGroupedAlerts = filterSymbol
    ? groupedAlerts.filter(group => group.symbol.toLowerCase().includes(filterSymbol.toLowerCase()))
    : groupedAlerts;

  if (loading && alerts.length === 0) {
    return (
      <Box display="flex" justifyContent="center" alignItems="center" minHeight="400px">
        <CircularProgress />
      </Box>
    );
  }

  return (
    <Box sx={{ p: 3 }}>
      <Stack spacing={3}>
        {/* Header */}
        <Box display="flex" justifyContent="space-between" alignItems="center">
          <Typography variant="h4" component="h1">
            🔴 Live ISP Alerts
          </Typography>
          <Stack direction="row" spacing={2} alignItems="center">
            <Chip
              label={`Last Update: ${lastUpdate ? lastUpdate.toLocaleTimeString() : 'Never'}`}
              color="primary"
              variant="outlined"
              size="small"
            />
            <Chip
              label={`${filteredGroupedAlerts.length}${filterSymbol ? ` / ${groupedAlerts.length}` : ''} Symbols • ${alerts.length} Total Alerts`}
              color="secondary"
              size="small"
            />
            {loading && <CircularProgress size={20} />}
          </Stack>
        </Box>

        {/* Controls */}
        <Paper sx={{ p: 2 }}>
          <Stack direction="row" spacing={3} alignItems="center" flexWrap="wrap">
            {/* Refresh Interval */}
            <Box>
              <Typography variant="caption" color="text.secondary" display="block" gutterBottom>
                Refresh Interval
              </Typography>
              <ToggleButtonGroup
                value={refreshInterval}
                exclusive
                onChange={(_, value) => value && setRefreshInterval(value)}
                size="small"
              >
                <ToggleButton value={100}>100ms</ToggleButton>
                <ToggleButton value={500}>500ms</ToggleButton>
                <ToggleButton value={1000}>1s</ToggleButton>
                <ToggleButton value={5000}>5s</ToggleButton>
                <ToggleButton value={10000}>10s</ToggleButton>
              </ToggleButtonGroup>
            </Box>

            {/* Symbol Filter */}
            <TextField
              label="Filter Symbol"
              value={filterSymbol}
              onChange={(e) => setFilterSymbol(e.target.value)}
              size="small"
              placeholder="e.g., VCG"
              sx={{ minWidth: 150 }}
            />

            {/* Info */}
            <Box sx={{ ml: 'auto' }}>
              <Typography variant="caption" color="text.secondary">
                Showing alerts from today (incremental updates)
              </Typography>
            </Box>
          </Stack>
        </Paper>

        {/* Error Alert */}
        {error && (
          <Alert severity="error" onClose={() => setError(null)}>
            {error}
          </Alert>
        )}

        {/* Alerts Table */}
        <TableContainer component={Paper}>
          <Table size="small" stickyHeader>
            <TableHead>
              <TableRow>
                <TableCell width={50} />
                <TableCell><strong>Time</strong></TableCell>
                <TableCell><strong>Symbol</strong></TableCell>
                <TableCell align="right"><strong>5m</strong></TableCell>
                <TableCell align="right"><strong>15m</strong></TableCell>
                <TableCell align="right"><strong>30m</strong></TableCell>
                <TableCell align="right"><strong>60m</strong></TableCell>
                <TableCell align="right"><strong>Max</strong></TableCell>
                <TableCell><strong>Severity (60m)</strong></TableCell>
                <TableCell align="center"><strong>History</strong></TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {filteredGroupedAlerts.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={10} align="center">
                    <Typography variant="body2" color="text.secondary" sx={{ py: 4 }}>
                      {filterSymbol 
                        ? `No alerts found for "${filterSymbol}"`
                        : 'No alerts today yet'}
                    </Typography>
                  </TableCell>
                </TableRow>
              ) : (
                filteredGroupedAlerts.map((group, index) => {
                  const maxRatio = getMaxAbnormality(group.latest);
                  const severity = getSeverity(group.latest.abnormality_ratio_60m);
                  const isExpanded = expandedRows.has(group.symbol);
                  
                  return (
                    <>
                      {/* Main Row - Latest Alert */}
                      <TableRow
                        key={group.symbol}
                        hover
                        sx={{
                          '&:nth-of-type(odd)': { backgroundColor: 'action.hover' },
                          animation: index < 5 ? 'pulse 1s ease-in-out' : 'none',
                          '@keyframes pulse': {
                            '0%': { backgroundColor: 'action.selected' },
                            '100%': { backgroundColor: 'inherit' },
                          },
                        }}
                      >
                        <TableCell>
                          {group.history.length > 0 && (
                            <IconButton
                              size="small"
                              onClick={() => toggleRow(group.symbol)}
                            >
                              {isExpanded ? <KeyboardArrowUpIcon /> : <KeyboardArrowDownIcon />}
                            </IconButton>
                          )}
                        </TableCell>
                        <TableCell>{formatTimestamp(group.latest.ts)}</TableCell>
                        <TableCell>
                          <Typography variant="body2" fontWeight="bold">
                            {group.symbol}
                          </Typography>
                        </TableCell>
                        <TableCell align="right">
                          <Typography
                            variant="body2"
                            color={group.latest.abnormality_ratio_5m >= 3 ? 'error' : 'text.primary'}
                          >
                            {formatRatio(group.latest.abnormality_ratio_5m)}
                          </Typography>
                        </TableCell>
                        <TableCell align="right">
                          <Typography
                            variant="body2"
                            color={group.latest.abnormality_ratio_15m >= 3 ? 'error' : 'text.primary'}
                          >
                            {formatRatio(group.latest.abnormality_ratio_15m)}
                          </Typography>
                        </TableCell>
                        <TableCell align="right">
                          <Typography
                            variant="body2"
                            color={group.latest.abnormality_ratio_30m >= 3 ? 'error' : 'text.primary'}
                          >
                            {formatRatio(group.latest.abnormality_ratio_30m)}
                          </Typography>
                        </TableCell>
                        <TableCell align="right">
                          <Typography
                            variant="body2"
                            color={group.latest.abnormality_ratio_60m >= 3 ? 'error' : 'text.primary'}
                          >
                            {formatRatio(group.latest.abnormality_ratio_60m)}
                          </Typography>
                        </TableCell>
                        <TableCell align="right">
                          <Typography variant="body2" fontWeight="bold">
                            {formatRatio(maxRatio)}
                          </Typography>
                        </TableCell>
                        <TableCell>
                          <Chip
                            label={severity.label}
                            color={severity.color}
                            size="small"
                          />
                        </TableCell>
                        <TableCell align="center">
                          <Chip
                            label={group.count > 1 ? `${group.count - 1} more` : 'Latest'}
                            size="small"
                            variant="outlined"
                          />
                        </TableCell>
                      </TableRow>

                      {/* Expanded History Rows */}
                      {group.history.length > 0 && (
                        <TableRow>
                          <TableCell style={{ paddingBottom: 0, paddingTop: 0 }} colSpan={10}>
                            <Collapse in={isExpanded} timeout="auto" unmountOnExit>
                              <Box sx={{ margin: 1, backgroundColor: 'background.default', borderRadius: 1, p: 2 }}>
                                <Typography variant="subtitle2" gutterBottom component="div" color="text.secondary">
                                  History for {group.symbol}
                                </Typography>
                                <Table size="small">
                                  <TableHead>
                                    <TableRow>
                                      <TableCell><strong>Time</strong></TableCell>
                                      <TableCell align="right"><strong>5m</strong></TableCell>
                                      <TableCell align="right"><strong>15m</strong></TableCell>
                                      <TableCell align="right"><strong>30m</strong></TableCell>
                                      <TableCell align="right"><strong>60m</strong></TableCell>
                                      <TableCell align="right"><strong>Max</strong></TableCell>
                                      <TableCell><strong>Severity</strong></TableCell>
                                    </TableRow>
                                  </TableHead>
                                  <TableBody>
                                    {group.history.map((histAlert, histIndex) => {
                                      const histMaxRatio = getMaxAbnormality(histAlert);
                                      const histSeverity = getSeverity(histAlert.abnormality_ratio_60m);
                                      
                                      return (
                                        <TableRow key={`${group.symbol}-history-${histIndex}`}>
                                          <TableCell>{formatTimestamp(histAlert.ts)}</TableCell>
                                          <TableCell align="right">
                                            <Typography variant="body2" color={histAlert.abnormality_ratio_5m >= 3 ? 'error' : 'text.secondary'}>
                                              {formatRatio(histAlert.abnormality_ratio_5m)}
                                            </Typography>
                                          </TableCell>
                                          <TableCell align="right">
                                            <Typography variant="body2" color={histAlert.abnormality_ratio_15m >= 3 ? 'error' : 'text.secondary'}>
                                              {formatRatio(histAlert.abnormality_ratio_15m)}
                                            </Typography>
                                          </TableCell>
                                          <TableCell align="right">
                                            <Typography variant="body2" color={histAlert.abnormality_ratio_30m >= 3 ? 'error' : 'text.secondary'}>
                                              {formatRatio(histAlert.abnormality_ratio_30m)}
                                            </Typography>
                                          </TableCell>
                                          <TableCell align="right">
                                            <Typography variant="body2" color={histAlert.abnormality_ratio_60m >= 3 ? 'error' : 'text.secondary'}>
                                              {formatRatio(histAlert.abnormality_ratio_60m)}
                                            </Typography>
                                          </TableCell>
                                          <TableCell align="right">
                                            <Typography variant="body2" fontWeight="medium">
                                              {formatRatio(histMaxRatio)}
                                            </Typography>
                                          </TableCell>
                                          <TableCell>
                                            <Chip
                                              label={histSeverity.label}
                                              color={histSeverity.color}
                                              size="small"
                                              variant="outlined"
                                            />
                                          </TableCell>
                                        </TableRow>
                                      );
                                    })}
                                  </TableBody>
                                </Table>
                              </Box>
                            </Collapse>
                          </TableCell>
                        </TableRow>
                      )}
                    </>
                  );
                })
              )}
            </TableBody>
          </Table>
        </TableContainer>
      </Stack>
    </Box>
  );
};

export default Live;

