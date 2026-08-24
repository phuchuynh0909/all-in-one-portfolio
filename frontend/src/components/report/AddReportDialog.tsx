import React from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  TextField,
  Stack,
  Alert,
  CircularProgress,
} from '@mui/material';
import { createReport } from '../../lib/services/report';
import type { Report } from '../../lib/services/report';

interface AddReportDialogProps {
  open: boolean;
  onClose: () => void;
  onCreated: (report: Report) => void;
}

const today = () => new Date().toISOString().split('T')[0];

/** Hand-enter a report the crawler didn't pick up. Fields mirror the list columns. */
export const AddReportDialog: React.FC<AddReportDialogProps> = ({ open, onClose, onCreated }) => {
  const [reportId, setReportId] = React.useState('');
  const [symbol, setSymbol] = React.useState('');
  const [title, setTitle] = React.useState('');
  const [url, setUrl] = React.useState('');
  const [source, setSource] = React.useState('manual');
  const [date, setDate] = React.useState(today());
  const [sector, setSector] = React.useState('');
  const [error, setError] = React.useState<string | null>(null);
  const [isSaving, setIsSaving] = React.useState(false);

  const reset = () => {
    setReportId('');
    setSymbol('');
    setTitle('');
    setUrl('');
    setSource('manual');
    setDate(today());
    setSector('');
    setError(null);
  };

  const handleClose = () => {
    if (isSaving) return;
    reset();
    onClose();
  };

  const urlError = url.trim() !== '' && !/^https?:\/\//i.test(url.trim());
  const idError = reportId !== '' && Number(reportId) <= 0;

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (urlError) {
      setError('URL must start with http:// or https://');
      return;
    }
    setError(null);
    setIsSaving(true);
    try {
      const report = await createReport({
        // Blank means "allocate one" — the backend picks from its manual id band.
        id: reportId ? Number(reportId) : null,
        tenbaocao: title.trim(),
        url: url.trim(),
        mack: symbol.trim() || null,
        nguon: source.trim() || 'manual',
        // Date-only input; send midnight local time so the list sorts sensibly.
        ngaykn: date ? `${date}T00:00:00` : null,
        rsnganh: sector.trim() || null,
      });
      reset();
      onCreated(report);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to add report');
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="sm" fullWidth>
      <DialogTitle>Add Report</DialogTitle>
      <form onSubmit={handleSubmit}>
        <DialogContent>
          <Stack spacing={2.5}>
            {error && <Alert severity="error">{error}</Alert>}
            <TextField
              label="Report ID"
              autoFocus
              value={reportId}
              onChange={(e) => {
                // Digits only; the feed id is a bigint.
                if (/^\d*$/.test(e.target.value)) setReportId(e.target.value);
              }}
              error={idError}
              helperText={
                idError
                  ? 'Must be greater than 0'
                  : 'Optional — leave blank to assign one automatically'
              }
              // 15 digits keeps the value inside the exact-integer range of a
              // JS number on the way to the bigint column.
              inputProps={{ inputMode: 'numeric', maxLength: 15 }}
            />
            <TextField
              label="Report Name"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              required
              inputProps={{ maxLength: 512 }}
            />
            <TextField
              label="PDF URL"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              required
              error={urlError}
              helperText={urlError ? 'Must start with http:// or https://' : 'Link to the report PDF'}
              inputProps={{ maxLength: 1024 }}
            />
            <TextField
              label="Symbol"
              value={symbol}
              onChange={(e) => setSymbol(e.target.value.toUpperCase())}
              helperText="Optional — leave blank for market/sector reports"
              inputProps={{ maxLength: 32 }}
            />
            <TextField
              label="Source"
              value={source}
              onChange={(e) => setSource(e.target.value)}
              required
              helperText="Broker or publisher"
              inputProps={{ maxLength: 128 }}
            />
            <TextField
              label="Report Date"
              type="date"
              value={date}
              onChange={(e) => setDate(e.target.value)}
              required
              InputLabelProps={{ shrink: true }}
            />
            <TextField
              label="Sector"
              value={sector}
              onChange={(e) => setSector(e.target.value)}
              helperText="Optional"
              inputProps={{ maxLength: 255 }}
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={handleClose} disabled={isSaving}>
            Cancel
          </Button>
          <Button
            type="submit"
            variant="contained"
            disabled={isSaving || !title.trim() || !url.trim() || urlError || idError}
            startIcon={isSaving ? <CircularProgress size={16} /> : undefined}
          >
            {isSaving ? 'Adding...' : 'Add'}
          </Button>
        </DialogActions>
      </form>
    </Dialog>
  );
};
