import { useState, useEffect, useCallback } from 'react';
import {
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  IconButton,
  InputAdornment,
  InputLabel,
  MenuItem,
  Select,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Typography,
  Tooltip,
  CircularProgress,
  Alert,
  Paper,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import DeleteIcon from '@mui/icons-material/Delete';
import EditIcon from '@mui/icons-material/Edit';
import RefreshIcon from '@mui/icons-material/Refresh';
import NotificationsActiveIcon from '@mui/icons-material/NotificationsActive';
import NotificationsOffIcon from '@mui/icons-material/NotificationsOff';
import RestoreIcon from '@mui/icons-material/Restore';
import TrendingUpIcon from '@mui/icons-material/TrendingUp';
import TrendingDownIcon from '@mui/icons-material/TrendingDown';
import {
  getPriceAlerts,
  createPriceAlert,
  updatePriceAlert,
  deletePriceAlert,
  togglePriceAlert,
  resetPriceAlert,
  getConditionLabel,
  type PriceAlertWithPrice,
  type AlertCondition,
  type CreateAlertRequest,
  type UpdateAlertRequest,
} from '../lib/services/priceAlerts';

const CONDITIONS: { value: AlertCondition; label: string; description: string }[] = [
  { value: 'gt', label: '>', description: 'Greater than' },
  { value: 'gte', label: '≥', description: 'Greater than or equal' },
  { value: 'lt', label: '<', description: 'Less than' },
  { value: 'lte', label: '≤', description: 'Less than or equal' },
  { value: 'eq', label: '=', description: 'Equal to' },
];

interface AlertFormData {
  symbol: string;
  condition: AlertCondition;
  target_price: string;
  notes: string;
}

const initialFormData: AlertFormData = {
  symbol: '',
  condition: 'gte',
  target_price: '',
  notes: '',
};

export default function Alerts() {
  const [alerts, setAlerts] = useState<PriceAlertWithPrice[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingAlert, setEditingAlert] = useState<PriceAlertWithPrice | null>(null);
  const [formData, setFormData] = useState<AlertFormData>(initialFormData);
  const [submitting, setSubmitting] = useState(false);
  const [filterSymbol, setFilterSymbol] = useState('');
  const [filterStatus, setFilterStatus] = useState<'all' | 'active' | 'inactive' | 'triggered'>('all');

  const fetchAlerts = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const params: { is_active?: boolean; is_triggered?: boolean } = {};
      
      if (filterStatus === 'active') {
        params.is_active = true;
        params.is_triggered = false;
      } else if (filterStatus === 'inactive') {
        params.is_active = false;
      } else if (filterStatus === 'triggered') {
        params.is_triggered = true;
      }
      
      const response = await getPriceAlerts(params);
      setAlerts(response.alerts);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch alerts');
    } finally {
      setLoading(false);
    }
  }, [filterStatus]);

  useEffect(() => {
    fetchAlerts();
  }, [fetchAlerts]);

  const handleOpenDialog = (alert?: PriceAlertWithPrice) => {
    if (alert) {
      setEditingAlert(alert);
      setFormData({
        symbol: alert.symbol,
        condition: alert.condition,
        target_price: String(alert.target_price),
        notes: alert.notes || '',
      });
    } else {
      setEditingAlert(null);
      setFormData(initialFormData);
    }
    setDialogOpen(true);
  };

  const handleCloseDialog = () => {
    setDialogOpen(false);
    setEditingAlert(null);
    setFormData(initialFormData);
  };

  const handleSubmit = async () => {
    try {
      setSubmitting(true);
      
      if (editingAlert) {
        const updateData: UpdateAlertRequest = {
          symbol: formData.symbol.toUpperCase(),
          condition: formData.condition,
          target_price: parseFloat(formData.target_price),
          notes: formData.notes || undefined,
        };
        await updatePriceAlert(editingAlert.id, updateData);
      } else {
        const createData: CreateAlertRequest = {
          symbol: formData.symbol.toUpperCase(),
          condition: formData.condition,
          target_price: parseFloat(formData.target_price),
          notes: formData.notes || undefined,
        };
        await createPriceAlert(createData);
      }
      
      handleCloseDialog();
      fetchAlerts();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save alert');
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (alertId: number) => {
    if (!confirm('Are you sure you want to delete this alert?')) return;
    
    try {
      await deletePriceAlert(alertId);
      fetchAlerts();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete alert');
    }
  };

  const handleToggle = async (alertId: number) => {
    try {
      await togglePriceAlert(alertId);
      fetchAlerts();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to toggle alert');
    }
  };

  const handleReset = async (alertId: number) => {
    try {
      await resetPriceAlert(alertId);
      fetchAlerts();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to reset alert');
    }
  };

  const formatPrice = (price: number | null | undefined) => {
    if (price === null || price === undefined) return '—';
    return new Intl.NumberFormat('en-US', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(price);
  };

  const formatPercentage = (pct: number | null | undefined) => {
    if (pct === null || pct === undefined) return '—';
    const sign = pct >= 0 ? '+' : '';
    return `${sign}${pct.toFixed(2)}%`;
  };

  const getStatusChip = (alert: PriceAlertWithPrice) => {
    if (alert.is_triggered) {
      return <Chip label="Triggered" color="warning" size="small" icon={<NotificationsActiveIcon />} />;
    }
    if (!alert.is_active) {
      return <Chip label="Inactive" color="default" size="small" icon={<NotificationsOffIcon />} />;
    }
    return <Chip label="Active" color="success" size="small" icon={<NotificationsActiveIcon />} />;
  };

  const getPriceDiffColor = (diff: number | null | undefined, condition: AlertCondition) => {
    if (diff === null || diff === undefined) return 'text.secondary';
    
    // For "greater than" conditions, positive diff means we're above target (good)
    // For "less than" conditions, negative diff means we're below target (good)
    const isAboveTarget = diff > 0;
    
    if (condition === 'gt' || condition === 'gte') {
      return isAboveTarget ? 'success.main' : 'error.main';
    } else if (condition === 'lt' || condition === 'lte') {
      return isAboveTarget ? 'error.main' : 'success.main';
    }
    
    return Math.abs(diff) < 0.01 ? 'success.main' : 'warning.main';
  };

  const filteredAlerts = alerts.filter((alert) => {
    if (filterSymbol && !alert.symbol.toLowerCase().includes(filterSymbol.toLowerCase())) {
      return false;
    }
    return true;
  });

  return (
    <Box sx={{ p: 3, maxWidth: 1400, mx: 'auto' }}>
      <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 3 }}>
        <Typography variant="h4" sx={{ fontWeight: 700, letterSpacing: -0.5 }}>
          🔔 Price Alerts
        </Typography>
        <Stack direction="row" spacing={1}>
          <Button
            variant="outlined"
            startIcon={<RefreshIcon />}
            onClick={fetchAlerts}
            disabled={loading}
          >
            Refresh
          </Button>
          <Button
            variant="contained"
            startIcon={<AddIcon />}
            onClick={() => handleOpenDialog()}
          >
            New Alert
          </Button>
        </Stack>
      </Stack>

      {/* Filters */}
      <Card sx={{ mb: 3, bgcolor: 'background.paper' }}>
        <CardContent>
          <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
            <TextField
              label="Filter by Symbol"
              size="small"
              value={filterSymbol}
              onChange={(e) => setFilterSymbol(e.target.value)}
              sx={{ minWidth: 200 }}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">🔍</InputAdornment>
                ),
              }}
            />
            <FormControl size="small" sx={{ minWidth: 150 }}>
              <InputLabel>Status</InputLabel>
              <Select
                value={filterStatus}
                label="Status"
                onChange={(e) => setFilterStatus(e.target.value as typeof filterStatus)}
              >
                <MenuItem value="all">All</MenuItem>
                <MenuItem value="active">Active</MenuItem>
                <MenuItem value="inactive">Inactive</MenuItem>
                <MenuItem value="triggered">Triggered</MenuItem>
              </Select>
            </FormControl>
            <Box sx={{ flexGrow: 1 }} />
            <Chip
              label={`${filteredAlerts.length} Alert${filteredAlerts.length !== 1 ? 's' : ''}`}
              color="primary"
              variant="outlined"
            />
          </Stack>
        </CardContent>
      </Card>

      {/* Error Alert */}
      {error && (
        <Alert severity="error" sx={{ mb: 3 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      {/* Alerts Table */}
      <TableContainer component={Paper} sx={{ bgcolor: 'background.paper' }}>
        <Table>
          <TableHead>
            <TableRow sx={{ bgcolor: 'action.hover' }}>
              <TableCell sx={{ fontWeight: 700 }}>Symbol</TableCell>
              <TableCell sx={{ fontWeight: 700 }}>Condition</TableCell>
              <TableCell sx={{ fontWeight: 700 }} align="right">Target Price</TableCell>
              <TableCell sx={{ fontWeight: 700 }} align="right">Current Price</TableCell>
              <TableCell sx={{ fontWeight: 700 }} align="right">Difference</TableCell>
              <TableCell sx={{ fontWeight: 700 }}>Status</TableCell>
              <TableCell sx={{ fontWeight: 700 }}>Notes</TableCell>
              <TableCell sx={{ fontWeight: 700 }} align="center">Actions</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {loading ? (
              <TableRow>
                <TableCell colSpan={8} align="center" sx={{ py: 6 }}>
                  <CircularProgress />
                </TableCell>
              </TableRow>
            ) : filteredAlerts.length === 0 ? (
              <TableRow>
                <TableCell colSpan={8} align="center" sx={{ py: 6 }}>
                  <Typography variant="body1" color="text.secondary">
                    {filterSymbol || filterStatus !== 'all'
                      ? 'No alerts match your filters'
                      : 'No alerts yet. Create your first alert!'}
                  </Typography>
                </TableCell>
              </TableRow>
            ) : (
              filteredAlerts.map((alert) => (
                <TableRow
                  key={alert.id}
                  sx={{
                    '&:hover': { bgcolor: 'action.hover' },
                    opacity: !alert.is_active ? 0.6 : 1,
                  }}
                >
                  <TableCell>
                    <Typography variant="body1" sx={{ fontWeight: 600, fontFamily: 'monospace' }}>
                      {alert.symbol}
                    </Typography>
                  </TableCell>
                  <TableCell>
                    <Tooltip title={CONDITIONS.find(c => c.value === alert.condition)?.description || ''}>
                      <Chip
                        label={`Price ${getConditionLabel(alert.condition)} Target`}
                        size="small"
                        variant="outlined"
                        sx={{ fontFamily: 'monospace' }}
                      />
                    </Tooltip>
                  </TableCell>
                  <TableCell align="right">
                    <Typography variant="body1" sx={{ fontFamily: 'monospace', fontWeight: 500 }}>
                      {formatPrice(alert.target_price)}
                    </Typography>
                  </TableCell>
                  <TableCell align="right">
                    <Typography variant="body1" sx={{ fontFamily: 'monospace' }}>
                      {formatPrice(alert.current_price)}
                    </Typography>
                  </TableCell>
                  <TableCell align="right">
                    <Stack direction="row" alignItems="center" justifyContent="flex-end" spacing={0.5}>
                      {alert.price_diff !== null && (
                        alert.price_diff >= 0 ? (
                          <TrendingUpIcon fontSize="small" sx={{ color: 'success.main' }} />
                        ) : (
                          <TrendingDownIcon fontSize="small" sx={{ color: 'error.main' }} />
                        )
                      )}
                      <Typography
                        variant="body2"
                        sx={{
                          fontFamily: 'monospace',
                          color: getPriceDiffColor(alert.price_diff, alert.condition),
                        }}
                      >
                        {formatPercentage(alert.price_diff_pct)}
                      </Typography>
                    </Stack>
                  </TableCell>
                  <TableCell>{getStatusChip(alert)}</TableCell>
                  <TableCell>
                    <Typography
                      variant="body2"
                      color="text.secondary"
                      sx={{
                        maxWidth: 200,
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {alert.notes || '—'}
                    </Typography>
                  </TableCell>
                  <TableCell align="center">
                    <Stack direction="row" spacing={0.5} justifyContent="center">
                      <Tooltip title="Edit">
                        <IconButton size="small" onClick={() => handleOpenDialog(alert)}>
                          <EditIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                      <Tooltip title={alert.is_active ? 'Deactivate' : 'Activate'}>
                        <IconButton size="small" onClick={() => handleToggle(alert.id)}>
                          {alert.is_active ? (
                            <NotificationsOffIcon fontSize="small" />
                          ) : (
                            <NotificationsActiveIcon fontSize="small" />
                          )}
                        </IconButton>
                      </Tooltip>
                      {alert.is_triggered && (
                        <Tooltip title="Reset">
                          <IconButton size="small" onClick={() => handleReset(alert.id)}>
                            <RestoreIcon fontSize="small" />
                          </IconButton>
                        </Tooltip>
                      )}
                      <Tooltip title="Delete">
                        <IconButton size="small" color="error" onClick={() => handleDelete(alert.id)}>
                          <DeleteIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    </Stack>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </TableContainer>

      {/* Create/Edit Dialog */}
      <Dialog open={dialogOpen} onClose={handleCloseDialog} maxWidth="sm" fullWidth>
        <DialogTitle>
          {editingAlert ? 'Edit Alert' : 'Create New Alert'}
        </DialogTitle>
        <DialogContent>
          <Stack spacing={3} sx={{ mt: 1 }}>
            <TextField
              label="Symbol"
              fullWidth
              value={formData.symbol}
              onChange={(e) => setFormData({ ...formData, symbol: e.target.value.toUpperCase() })}
              placeholder="e.g., VNM, FPT, VIC"
              autoFocus
              inputProps={{ style: { textTransform: 'uppercase' } }}
            />
            <FormControl fullWidth>
              <InputLabel>Condition</InputLabel>
              <Select
                value={formData.condition}
                label="Condition"
                onChange={(e) => setFormData({ ...formData, condition: e.target.value as AlertCondition })}
              >
                {CONDITIONS.map((c) => (
                  <MenuItem key={c.value} value={c.value}>
                    <Stack direction="row" alignItems="center" spacing={1}>
                      <Typography sx={{ fontFamily: 'monospace', minWidth: 30 }}>{c.label}</Typography>
                      <Typography variant="body2" color="text.secondary">
                        {c.description}
                      </Typography>
                    </Stack>
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
            <TextField
              label="Target Price"
              type="number"
              fullWidth
              value={formData.target_price}
              onChange={(e) => setFormData({ ...formData, target_price: e.target.value })}
              placeholder="e.g., 100.50"
              InputProps={{
                inputProps: { min: 0, step: 0.01 },
              }}
            />
            <TextField
              label="Notes (optional)"
              fullWidth
              multiline
              rows={2}
              value={formData.notes}
              onChange={(e) => setFormData({ ...formData, notes: e.target.value })}
              placeholder="Add any notes about this alert..."
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={handleCloseDialog}>Cancel</Button>
          <Button
            variant="contained"
            onClick={handleSubmit}
            disabled={submitting || !formData.symbol || !formData.target_price}
          >
            {submitting ? <CircularProgress size={24} /> : editingAlert ? 'Save' : 'Create'}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}

