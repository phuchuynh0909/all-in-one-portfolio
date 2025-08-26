import React, { useState, useEffect } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  TextField,
  Box,
  Typography,
  Checkbox,
  FormControlLabel,
  Alert,
  Divider,
  Grid,
  Paper,
} from '@mui/material';
import { DatePicker } from '@mui/x-date-pickers/DatePicker';
import { LocalizationProvider } from '@mui/x-date-pickers/LocalizationProvider';
import { AdapterDateFns } from '@mui/x-date-pickers/AdapterDateFns';
import { closePosition, ClosePositionRequest, ClosePositionResponse } from '../../lib/services/portfolio';

type Position = {
  id: number;
  ticker: string;
  quantity: number;
  purchase_price: number;
  current_price: number;
  purchase_date: string;
  notes?: string;
  created_at: string;
};

interface ClosePositionDialogProps {
  open: boolean;
  position: Position | null;
  onClose: () => void;
  onSuccess: () => void;
}

export default function ClosePositionDialog({ 
  open, 
  position, 
  onClose, 
  onSuccess 
}: ClosePositionDialogProps) {
  const [quantityToClose, setQuantityToClose] = useState<number>(0);
  const [closingPrice, setClosingPrice] = useState<number>(0);
  const [closingDate, setClosingDate] = useState<Date>(new Date());
  const [fees, setFees] = useState<number>(0);
  const [notes, setNotes] = useState<string>('');
  const [overridePrice, setOverridePrice] = useState<boolean>(false);
  const [confirmed, setConfirmed] = useState<boolean>(false);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string>('');

  // Reset form when dialog opens with new position
  useEffect(() => {
    if (position && open) {
      setQuantityToClose(position.quantity);
      setClosingPrice(position.current_price);
      setClosingDate(new Date());
      setFees(0);
      setNotes('');
      setOverridePrice(false);
      setConfirmed(false);
      setError('');
    }
  }, [position, open]);

  // Calculate estimated P/L
  const calculatePL = () => {
    if (!position || quantityToClose <= 0 || closingPrice <= 0) {
      return { pl: 0, plPct: 0 };
    }

    const purchaseValue = position.purchase_price * quantityToClose;
    const closingValue = closingPrice * quantityToClose;
    const pl = closingValue - purchaseValue - fees;
    const plPct = ((closingPrice / position.purchase_price) - 1) * 100;

    return { pl, plPct };
  };

  const { pl, plPct } = calculatePL();

  const handleSubmit = async () => {
    if (!position) return;

    if (!confirmed) {
      setError('Please confirm the transaction by checking the confirmation box');
      return;
    }

    if (quantityToClose <= 0 || quantityToClose > position.quantity) {
      setError(`Invalid quantity. Must be between 1 and ${position.quantity}`);
      return;
    }

    if (closingPrice <= 0) {
      setError('Invalid closing price. Must be greater than 0');
      return;
    }

    setLoading(true);
    setError('');

    try {
      const request: ClosePositionRequest = {
        position_id: position.id,
        quantity_to_close: quantityToClose,
        closing_price: closingPrice,
        closing_date: closingDate.toISOString().split('T')[0], // YYYY-MM-DD format
        fees: fees || 0,
        notes: notes || undefined,
      };

      const response: ClosePositionResponse = await closePosition(request);

      if (response.success) {
        onSuccess();
        onClose();
      } else {
        setError(response.message || 'Failed to close position');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'An error occurred while closing the position');
    } finally {
      setLoading(false);
    }
  };

  const formatCurrency = (value: number) => {
    const rounded = Math.round((value || 0) * 100) / 100;
    return rounded.toLocaleString('en-US', {
      style: 'currency',
      currency: 'VND',
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  };

  const formatPercentage = (value: number) => {
    return `${value.toFixed(2)}%`;
  };

  if (!position) return null;

  return (
    <LocalizationProvider dateAdapter={AdapterDateFns}>
      <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
        <DialogTitle>Close Position - {position.ticker}</DialogTitle>
        <DialogContent>
          <Box sx={{ mt: 2 }}>
            {/* Current Position Details */}
            <Paper sx={{ p: 2, mb: 3, backgroundColor: 'grey.50' }}>
              <Typography variant="h6" gutterBottom>
                Current Position Details
              </Typography>
              <Grid container spacing={2}>
                <Grid item xs={6}>
                  <Typography variant="body2">
                    <strong>Ticker:</strong> {position.ticker}
                  </Typography>
                </Grid>
                <Grid item xs={6}>
                  <Typography variant="body2">
                    <strong>Available Quantity:</strong> {Math.floor(position.quantity)} shares
                  </Typography>
                </Grid>
                <Grid item xs={6}>
                  <Typography variant="body2">
                    <strong>Purchase Price:</strong> {formatCurrency(position.purchase_price)}
                  </Typography>
                </Grid>
                <Grid item xs={6}>
                  <Typography variant="body2">
                    <strong>Current Price:</strong> {formatCurrency(position.current_price)}
                  </Typography>
                </Grid>
              </Grid>
            </Paper>

            {/* Form Fields */}
            <Grid container spacing={3}>
              <Grid item xs={12} sm={6}>
                <TextField
                  fullWidth
                  label="Quantity to Close"
                  type="number"
                  value={Math.floor(quantityToClose)}
                  onChange={(e) => setQuantityToClose(Math.floor(Number(e.target.value)))}
                  inputProps={{
                    min: 0,
                    max: position.quantity,
                    // step: 1,
                  }}
                  helperText={`Max: ${Math.floor(position.quantity)} shares`}
                />
              </Grid>

              <Grid item xs={12} sm={6}>
                <DatePicker
                  label="Closing Date"
                  value={closingDate}
                  onChange={(newValue) => newValue && setClosingDate(newValue)}
                  slotProps={{
                    textField: {
                      fullWidth: true,
                      helperText: 'Select the date when the position was closed',
                    },
                  }}
                />
              </Grid>

              <Grid item xs={12}>
                <Box sx={{ mb: 2 }}>
                  <Typography variant="body2" gutterBottom>
                    Current Closing Price: {formatCurrency(position.current_price)}
                  </Typography>
                  <FormControlLabel
                    control={
                      <Checkbox
                        checked={overridePrice}
                        onChange={(e) => setOverridePrice(e.target.checked)}
                      />
                    }
                    label="Override Closing Price"
                  />
                </Box>
                
                {overridePrice && (
                  <TextField
                    fullWidth
                    label="Manual Closing Price"
                    type="number"
                    value={Math.round(closingPrice * 100) / 100}
                    onChange={(e) => setClosingPrice(Math.round(Number(e.target.value) * 100) / 100)}
                    inputProps={{
                      min: 0.01,
                      step: 0.01,
                    }}
                    helperText="Enter a manual closing price to override the current price"
                  />
                )}
              </Grid>

              <Grid item xs={12} sm={6}>
                <TextField
                  fullWidth
                  label="Transaction Fees (VND)"
                  type="number"
                  value={fees}
                  onChange={(e) => setFees(Math.round(Number(e.target.value) * 100) / 100)}
                  helperText="Enter fees with up to 2 decimal places"
                />
              </Grid>

              <Grid item xs={12} sm={6}>
                <TextField
                  fullWidth
                  label="Notes (optional)"
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  multiline
                  rows={2}
                />
              </Grid>
            </Grid>

            {/* Estimated P/L Display */}
            {quantityToClose > 0 && closingPrice > 0 && (
              <Paper sx={{ p: 2, mt: 3, backgroundColor: pl >= 0 ? 'success.light' : 'error.light' }}>
                <Typography variant="h6" gutterBottom>
                  Estimated P/L for this Close
                </Typography>
                <Typography variant="h5" sx={{ color: pl >= 0 ? 'success.dark' : 'error.dark' }}>
                  {formatCurrency(pl)} ({formatPercentage(plPct)})
                </Typography>
                
                <Divider sx={{ my: 2 }} />
                
                <Typography variant="h6" gutterBottom>
                  Transaction Preview
                </Typography>
                <Grid container spacing={2}>
                  <Grid item xs={6}>
                    <Typography variant="body2">Quantity to Close:</Typography>
                  </Grid>
                  <Grid item xs={6}>
                    <Typography variant="body2">{Math.floor(quantityToClose)} shares</Typography>
                  </Grid>
                  
                  <Grid item xs={6}>
                    <Typography variant="body2">Closing Price:</Typography>
                  </Grid>
                  <Grid item xs={6}>
                    <Typography variant="body2">{formatCurrency(closingPrice)}</Typography>
                  </Grid>
                  
                  <Grid item xs={6}>
                    <Typography variant="body2">Transaction Fees:</Typography>
                  </Grid>
                  <Grid item xs={6}>
                    <Typography variant="body2">{formatCurrency(fees)}</Typography>
                  </Grid>
                  
                  <Grid item xs={6}>
                    <Typography variant="body2">Total Value:</Typography>
                  </Grid>
                  <Grid item xs={6}>
                    <Typography variant="body2">{formatCurrency(quantityToClose * closingPrice)}</Typography>
                  </Grid>
                  
                  <Grid item xs={6}>
                    <Typography variant="body2">Remaining Quantity:</Typography>
                  </Grid>
                  <Grid item xs={6}>
                    <Typography variant="body2">{Math.floor(position.quantity - quantityToClose)} shares</Typography>
                  </Grid>
                </Grid>
              </Paper>
            )}

            {/* Confirmation */}
            <Box sx={{ mt: 3 }}>
              <FormControlLabel
                control={
                  <Checkbox
                    checked={confirmed}
                    onChange={(e) => setConfirmed(e.target.checked)}
                  />
                }
                label="I confirm that I want to close this position with the details shown above"
              />
            </Box>

            {/* Error Display */}
            {error && (
              <Alert severity="error" sx={{ mt: 2 }}>
                {error}
              </Alert>
            )}
          </Box>
        </DialogContent>

        <DialogActions>
          <Button onClick={onClose} disabled={loading}>
            Cancel
          </Button>
          <Button
            onClick={handleSubmit}
            variant="contained"
            disabled={loading || !confirmed}
            color={pl >= 0 ? 'success' : 'error'}
          >
            {loading ? 'Closing...' : 'Close Position'}
          </Button>
        </DialogActions>
      </Dialog>
    </LocalizationProvider>
  );
}
