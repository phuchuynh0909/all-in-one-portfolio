import { useEffect, useState } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  TextField,
  Stack,
} from '@mui/material';
import { API_BASE_URL } from '../../lib/api';
import { formatLocalDate } from '../../lib/format';
import type { Position } from '../../lib/services/portfolio';

type PositionFormProps = {
  open: boolean;
  onClose: () => void;
  onSuccess: () => void;
  position?: Position;
  mode: 'create' | 'edit';
};

export default function PositionForm({
  open,
  onClose,
  onSuccess,
  position,
  mode,
}: PositionFormProps) {
  const [ticker, setTicker] = useState(position?.ticker || '');
  const [quantity, setQuantity] = useState(position?.quantity?.toString() || '');
  const [price, setPrice] = useState(
    position?.purchase_price != null ? String(position.purchase_price) : ''
  );
  const [date, setDate] = useState(position?.purchase_date || formatLocalDate(new Date()));
  const [notes, setNotes] = useState(position?.notes || '');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setTicker(position?.ticker ?? '');
    setQuantity(position?.quantity?.toString() ?? '');
    setPrice(position?.purchase_price != null ? String(position.purchase_price) : '');
    setDate(position?.purchase_date ?? formatLocalDate(new Date()));
    setNotes(position?.notes ?? '');
    setError(null);
  }, [open, position]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    const data = {
      ticker: ticker.toUpperCase(),
      quantity,
      purchase_price: price,
      purchase_date: date,
      notes: notes || undefined,
      ...(mode === 'edit' && position ? { original: {
        ticker: position.ticker,
        quantity: position.quantity,
        purchase_price: position.purchase_price,
        purchase_date: position.purchase_date,
        notes: position.notes ?? null,
      } } : {}),
    };

    try {
      const res = await fetch(
        `${API_BASE_URL}/portfolio/positions${
          mode === 'edit' ? `/${position?.id}` : ''
        }`,
        {
          method: mode === 'create' ? 'POST' : 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(data),
        }
      );

      if (!res.ok) {
        throw new Error(`Error ${res.status}: ${await res.text()}`);
      }

      onSuccess();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'An error occurred');
    }
  };

  const handleQuantityChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value;
    // Only allow positive integers
    if (/^\d*$/.test(value)) {
      setQuantity(value);
    }
  };

  const handlePriceChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value;
    if (/^\d*\.?\d{0,6}$/.test(value)) {
      setPrice(value);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>
        {mode === 'create' ? 'Add Position' : 'Edit Position'}
      </DialogTitle>
      <form onSubmit={handleSubmit}>
        <DialogContent>
          <Stack spacing={3}>
            <TextField
              label="Ticker"
              value={ticker}
              onChange={(e) => setTicker(e.target.value)}
              required
              inputProps={{ maxLength: 10 }}
            />
            <TextField
              label="Quantity"
              type="text"
              value={quantity}
              onChange={handleQuantityChange}
              required
              helperText="Enter a whole number"
            />
            <TextField
              label="Purchase Price"
              type="text"
              value={price}
              onChange={handlePriceChange}
              required
              helperText="Enter a number with up to 6 decimal places"
            />
            <TextField
              label="Purchase Date"
              type="date"
              value={date}
              onChange={(e) => setDate(e.target.value)}
              required
              InputLabelProps={{ shrink: true }}
            />
            <TextField
              label="Notes"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              multiline
              rows={3}
            />
            {error && (
              <TextField
                error
                value={error}
                disabled
                fullWidth
                variant="filled"
              />
            )}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="contained">
            {mode === 'create' ? 'Add' : 'Save'}
          </Button>
        </DialogActions>
      </form>
    </Dialog>
  );
}
