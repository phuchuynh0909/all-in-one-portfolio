import { useEffect, useMemo, useState } from 'react';
import {
  Box,
  Button,
  Card,
  CardContent,
  Divider,
  FormControl,
  Grid,
  IconButton,
  InputLabel,
  MenuItem,
  Select,
  SelectChangeEvent,
  Stack,
  TextField,
  Typography,
  Checkbox,
  FormControlLabel,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import DeleteIcon from '@mui/icons-material/Delete';
import { format } from 'date-fns';
import { DatePicker } from '@mui/x-date-pickers/DatePicker';
import { getScannerColumns, scanFeatures, ConditionOperator } from '../lib/services/scanner';

type ConditionRow = {
  id: string;
  column: string;
  operator: ConditionOperator;
  value: string; // raw input, parsed by operator
};

const operators: { value: ConditionOperator; label: string }[] = [
  { value: 'eq', label: '=' },
  { value: 'ne', label: '≠' },
  { value: 'gt', label: '>' },
  { value: 'gte', label: '≥' },
  { value: 'lt', label: '<' },
  { value: 'lte', label: '≤' },
  { value: 'between', label: 'Between' },
  { value: 'in', label: 'In' },
  { value: 'notin', label: 'Not in' },
  { value: 'contains', label: 'Contains' },
];

export default function Scanner() {
  const [columns, setColumns] = useState<string[]>([]);
  const [rows, setRows] = useState<ConditionRow[]>([
    { id: crypto.randomUUID(), column: '', operator: 'gt', value: '' },
  ]);
  const [startDate, setStartDate] = useState<Date | null>(null);
  const [endDate, setEndDate] = useState<Date | null>(null);
  const [latestOnly, setLatestOnly] = useState<boolean>(true);
  const [extraColumns, setExtraColumns] = useState<string>('');
  const [symbols, setSymbols] = useState<string>('');
  const [loading, setLoading] = useState<boolean>(false);
  const [items, setItems] = useState<{ symbol: string; date: string; values: Record<string, unknown> }[]>([]);

  useEffect(() => {
    getScannerColumns().then(setColumns).catch(console.error);
  }, []);

  const availableValueHint = useMemo(() => {
    const op = rows[0]?.operator;
    switch (op) {
      case 'between':
        return 'e.g., 10,20';
      case 'in':
      case 'notin':
        return 'comma-separated list';
      default:
        return 'number or text';
    }
  }, [rows]);

  function updateRow(idx: number, patch: Partial<ConditionRow>) {
    setRows((prev) => prev.map((r, i) => (i === idx ? { ...r, ...patch } : r)));
  }

  function addRow() {
    setRows((prev) => [...prev, { id: crypto.randomUUID(), column: '', operator: 'gt', value: '' }]);
  }

  function removeRow(id: string) {
    setRows((prev) => prev.filter((r) => r.id !== id));
  }

  function parseValue(op: ConditionOperator, raw: string): unknown {
    if (op === 'between') {
      const parts = raw.split(',').map((s) => s.trim()).filter(Boolean).map(Number);
      return parts.slice(0, 2);
    }
    if (op === 'in' || op === 'notin') {
      return raw.split(',').map((s) => s.trim()).filter(Boolean);
    }
    const n = Number(raw);
    return Number.isFinite(n) && raw.trim() !== '' ? n : raw;
  }

  async function onScan() {
    try {
      setLoading(true);
      const req = {
        conditions: rows
          .filter((r) => r.column && r.operator && r.value !== '')
          .map((r) => ({ column: r.column, operator: r.operator, value: parseValue(r.operator, r.value) })),
        columns_to_return: extraColumns
          .split(',')
          .map((s) => s.trim())
          .filter(Boolean),
        start_date: startDate ? format(startDate, 'yyyy-MM-dd') : undefined,
        end_date: endDate ? format(endDate, 'yyyy-MM-dd') : undefined,
        symbols: symbols
          .split(',')
          .map((s) => s.trim())
          .filter(Boolean),
        latest_only: latestOnly,
      };
      const res = await scanFeatures(req);
      setItems(res.items);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }

  return (
    <Box>
      <Typography variant="h5" sx={{ mb: 2 }}>Feature Scanner</Typography>

      <Card sx={{ mb: 2 }}>
        <CardContent>
          <Stack spacing={2}>
            <Typography variant="subtitle1">Filters</Typography>
            {rows.map((r, idx) => (
              <Grid container spacing={2} alignItems="center" key={r.id}>
                <Grid item xs={12} md={4}>
                  <FormControl fullWidth size="small">
                    <InputLabel id={`col-${r.id}`}>Column</InputLabel>
                    <Select
                      labelId={`col-${r.id}`}
                      value={r.column}
                      label="Column"
                      onChange={(e: SelectChangeEvent<string>) => updateRow(idx, { column: e.target.value })}
                    >
                      {columns.map((c) => (
                        <MenuItem key={c} value={c}>{c}</MenuItem>
                      ))}
                    </Select>
                  </FormControl>
                </Grid>
                <Grid item xs={12} md={3}>
                  <FormControl fullWidth size="small">
                    <InputLabel id={`op-${r.id}`}>Operator</InputLabel>
                    <Select
                      labelId={`op-${r.id}`}
                      value={r.operator}
                      label="Operator"
                      onChange={(e: SelectChangeEvent<string>) => updateRow(idx, { operator: e.target.value as ConditionOperator })}
                    >
                      {operators.map((op) => (
                        <MenuItem key={op.value} value={op.value}>{op.label}</MenuItem>
                      ))}
                    </Select>
                  </FormControl>
                </Grid>
                <Grid item xs={12} md={4}>
                  <TextField
                    fullWidth
                    size="small"
                    label={`Value (${availableValueHint})`}
                    value={r.value}
                    onChange={(e) => updateRow(idx, { value: e.target.value })}
                  />
                </Grid>
                <Grid item xs={12} md={1}>
                  <IconButton onClick={() => removeRow(r.id)} aria-label="delete">
                    <DeleteIcon />
                  </IconButton>
                </Grid>
              </Grid>
            ))}
            <Button startIcon={<AddIcon />} onClick={addRow} variant="outlined" size="small" sx={{ alignSelf: 'flex-start' }}>
              Add condition
            </Button>
            <Divider />
            <Grid container spacing={2}>
              <Grid item xs={12} md={3}>
                <DatePicker label="Start date" value={startDate} onChange={setStartDate} slotProps={{ textField: { size: 'small', fullWidth: true } }} />
              </Grid>
              <Grid item xs={12} md={3}>
                <DatePicker label="End date" value={endDate} onChange={setEndDate} slotProps={{ textField: { size: 'small', fullWidth: true } }} />
              </Grid>
              <Grid item xs={12} md={3}>
                <TextField size="small" label="Symbols (comma-separated)" fullWidth value={symbols} onChange={(e) => setSymbols(e.target.value)} />
              </Grid>
              <Grid item xs={12} md={3}>
                <TextField size="small" label="Extra columns (comma-separated)" fullWidth value={extraColumns} onChange={(e) => setExtraColumns(e.target.value)} />
              </Grid>
            </Grid>
            <FormControlLabel control={<Checkbox checked={latestOnly} onChange={(e) => setLatestOnly(e.target.checked)} />} label="Latest row per symbol" />
            <Box>
              <Button variant="contained" onClick={onScan} disabled={loading}>
                {loading ? 'Scanning...' : 'Scan'}
              </Button>
            </Box>
          </Stack>
        </CardContent>
      </Card>

      <Card>
        <CardContent>
          <Typography variant="subtitle1" sx={{ mb: 2 }}>Results ({items.length})</Typography>
          <Box sx={{ overflowX: 'auto' }}>
            {items.length === 0 ? (
              <Typography variant="body2">No results</Typography>
            ) : (
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr>
                    <th style={{ textAlign: 'left', padding: 8 }}>Symbol</th>
                    <th style={{ textAlign: 'left', padding: 8 }}>Date</th>
                    {Object.keys(items[0].values).map((c) => (
                      <th key={c} style={{ textAlign: 'left', padding: 8 }}>{c}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {items.map((it) => (
                    <tr key={`${it.symbol}-${it.date}`}>
                      <td style={{ padding: 8 }}>{it.symbol}</td>
                      <td style={{ padding: 8 }}>{format(new Date(it.date), 'yyyy-MM-dd')}</td>
                      {Object.keys(items[0].values).map((c) => (
                        <td key={`${it.symbol}-${it.date}-${c}`} style={{ padding: 8 }}>
                          {String(it.values[c] ?? '')}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Box>
        </CardContent>
      </Card>
    </Box>
  );
}


