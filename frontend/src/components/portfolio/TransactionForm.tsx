import { useState } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  TextField,
  Stack,
  Select,
  MenuItem,
  InputLabel,
  FormControl,
} from '@mui/material';
import { API_BASE_URL } from '../../lib/api';

type Transaction = {
  ticker: string;
  transaction_type: 'buy' | 'sell';
  quantity: number;
  price: number;
  close_price?: number;
  transaction_date: string;
  fees?: number;
  notes?: string;
};

type TransactionFormProps = {
  open: boolean;
  onClose: () => void;
  onSuccess: () => void;
  transaction?: Transaction;
  mode: 'create' | 'edit';
};

export default function TransactionForm({
  open,
  onClose,
  onSuccess,
  transaction,
  mode,
}: TransactionFormProps) {
  const [ticker, setTicker] = useState(transaction?.ticker || '');
  const [type, setType] = useState<'buy' | 'sell'>(
    transaction?.transaction_type || 'buy'
  );
  const [quantity, setQuantity] = useState(
    transaction?.quantity?.toString() || ''
  );
  const [price, setPrice] = useState(
    transaction?.price ? Number(transaction.price) : ''
  );
  const [closePrice, setClosePrice] = useState(
    transaction?.close_price ? Number(transaction.close_price) : ''
  );
  const [date, setDate] = useState(
    transaction?.transaction_date || new Date().toISOString().split('T')[0]
  );
  const [fees, setFees] = useState(
    transaction?.fees ? Number(transaction.fees) : '0'
  );
  const [notes, setNotes] = useState(transaction?.notes || '');
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    const data = {
      ticker: ticker.toUpperCase(),
      transaction_type: type,
      quantity: Math.floor(Number(quantity)), // Ensure integer
      price: Number(Number(price)), // Ensure 2 decimal places
      close_price: closePrice ? Number(Number(closePrice)) : undefined,
      transaction_date: date,
      fees: Number(Number(fees)),
      notes: notes || undefined,
    };

    try {
      const res = await fetch(
        `${API_BASE_URL}/portfolio/transactions${
          mode === 'edit' ? `/${transaction?.id}` : ''
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
    // Allow decimal numbers with up to 2 decimal places
    if (/^\d*\.?\d{0,2}$/.test(value)) {
      setPrice(value);
    }
  };

  const handleClosePriceChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value;
    // Allow decimal numbers with up to 2 decimal places
    if (/^\d*\.?\d{0,2}$/.test(value)) {
      setClosePrice(value);
    }
  };

  const handleFeesChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value;
    // Allow decimal numbers with up to 2 decimal places
    if (/^\d*\.?\d{0,2}$/.test(value)) {
      setFees(value);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>
        {mode === 'create' ? 'Add Transaction' : 'Edit Transaction'}
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
            <FormControl fullWidth>
              <InputLabel>Type</InputLabel>
              <Select
                value={type}
                label="Type"
                onChange={(e) => setType(e.target.value as 'buy' | 'sell')}
                required
              >
                <MenuItem value="buy">Buy</MenuItem>
                <MenuItem value="sell">Sell</MenuItem>
              </Select>
            </FormControl>
            <TextField
              label="Quantity"
              type="text"
              value={quantity}
              onChange={handleQuantityChange}
              required
              helperText="Enter a whole number"
            />
            <TextField
              label="Price"
              type="text"
              value={price}
              onChange={handlePriceChange}
              required
              helperText="Enter a number with up to 2 decimal places"
            />
            {type === 'sell' && (
              <TextField
                label="Close Price"
                type="text"
                value={closePrice}
                onChange={handleClosePriceChange}
                helperText="Enter a number with up to 2 decimal places"
              />
            )}
            <TextField
              label="Transaction Date"
              type="date"
              value={date}
              onChange={(e) => setDate(e.target.value)}
              required
              InputLabelProps={{ shrink: true }}
            />
            <TextField
              label="Fees"
              type="text"
              value={fees}
              onChange={handleFeesChange}
              helperText="Enter a number with up to 2 decimal places"
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
