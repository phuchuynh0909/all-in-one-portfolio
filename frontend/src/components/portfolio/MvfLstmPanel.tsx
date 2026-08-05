import { useEffect, useRef, useState } from 'react';
import {
  Alert,
  Autocomplete,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  Collapse,
  FormControlLabel,
  LinearProgress,
  Switch,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from '@mui/material';
import { getAllStockSymbols, type StockSymbol } from '../../lib/services/portfolio';
import { startMvfRun, type MvfRequest, type MvfResult } from '../../lib/services/mvf';

/** The universe the notebook (notebooks/mvf_lstm_portfolio.ipynb) ships as its
 * default, so the panel is runnable without first hunting for tickers. */
const NOTEBOOK_UNIVERSE = [
  'PET', 'VIC', 'VHM', 'ABB', 'MSB', 'LPB', 'HCM', 'SSB', 'GEX', 'VPI', 'GMD', 'NAB',
  'DVM', 'STB', 'BSR', 'ACB', 'VCG', 'POW', 'VJC', 'PVD', 'HNG', 'SHS', 'MBS', 'VND',
];

type Stage = 'idle' | 'loading' | 'training' | 'forecasting' | 'optimizing' | 'done';

const STAGE_LABEL: Record<Stage, string> = {
  idle: '',
  loading: 'Loading price history…',
  training: 'Training LSTMs',
  forecasting: 'Forecasting price paths…',
  optimizing: 'Solving max-Sharpe weights…',
  done: 'Complete',
};

const pct = (x: number, digits = 1) => `${(x * 100).toFixed(digits)}%`;

const compact = (x: number) =>
  new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 }).format(x);

const num = (x: number, digits = 0) =>
  x.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });

/** Label + value. Proportional figures — `tabular-nums` would make a display-size
 * number look loose; it is reserved for the aligned table columns below. */
function StatTile({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <Box sx={{ flex: '1 1 150px', minWidth: 0 }}>
      <Typography variant="caption" sx={{ color: 'text.secondary', display: 'block' }}>
        {label}
      </Typography>
      <Typography sx={{ fontSize: 26, fontWeight: 600, lineHeight: 1.2 }}>{value}</Typography>
      {hint && (
        <Typography variant="caption" sx={{ color: 'text.disabled' }}>
          {hint}
        </Typography>
      )}
    </Box>
  );
}

/** Magnitude meter: one hue, fill on a lighter step of the same ramp, grown from a
 * square baseline with a 4px rounded data-end. Scaled to the per-asset cap so the
 * bar reads against the constraint that actually binds, not against 100%. */
function WeightMeter({ weight, cap }: { weight: number; cap: number }) {
  const filled = Math.min(weight / Math.max(cap, 1e-9), 1);
  return (
    <Box
      sx={{
        height: 8,
        width: '100%',
        minWidth: 56,
        bgcolor: 'primary.light',
        opacity: 0.9,
        borderRadius: '2px',
        overflow: 'hidden',
      }}
    >
      <Box
        sx={{
          height: '100%',
          width: `${filled * 100}%`,
          bgcolor: 'primary.main',
          borderRadius: '0 4px 4px 0',
        }}
      />
    </Box>
  );
}

export default function MvfLstmPanel() {
  const [symbols, setSymbols] = useState<StockSymbol[]>([]);
  const [tickers, setTickers] = useState<string[]>(NOTEBOOK_UNIVERSE);

  // Config — defaults match the notebook's production settings.
  const [horizon, setHorizon] = useState(21);
  const [seqLen, setSeqLen] = useState(60);
  const [epochs, setEpochs] = useState(40);
  const [maxWeight, setMaxWeight] = useState(40); // percent, converted on submit
  const [capital, setCapital] = useState(1_000_000_000);
  const [covLookback, setCovLookback] = useState(252);
  const [covShrink, setCovShrink] = useState(true);
  const [benchmark, setBenchmark] = useState('VNINDEX');
  const [years, setYears] = useState(10);
  const [lr, setLr] = useState(0.001);
  const [batchSize, setBatchSize] = useState(64);
  const [forceRetrain, setForceRetrain] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);

  // Run state
  const [stage, setStage] = useState<Stage>('idle');
  const [trained, setTrained] = useState({ index: 0, total: 0, symbol: '', cached: 0 });
  const [result, setResult] = useState<MvfResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dropped, setDropped] = useState<string[]>([]);
  const controllerRef = useRef<AbortController | null>(null);

  const running = stage !== 'idle' && stage !== 'done';

  useEffect(() => {
    getAllStockSymbols(500)
      .then(setSymbols)
      .catch(() => setSymbols([])); // the picker still accepts free-typed tickers
  }, []);

  // Cancel an in-flight run if the page unmounts, so training doesn't keep
  // streaming into a dead component.
  useEffect(() => () => controllerRef.current?.abort(), []);

  const run = () => {
    setError(null);
    setResult(null);
    setDropped([]);
    setTrained({ index: 0, total: tickers.length, symbol: '', cached: 0 });
    setStage('loading');

    const payload: MvfRequest = {
      tickers,
      benchmark: benchmark.trim().toUpperCase() || 'VNINDEX',
      seq_len: seqLen,
      horizon,
      epochs,
      lr,
      batch_size: batchSize,
      force_retrain: forceRetrain,
      max_weight: maxWeight / 100,
      cov_lookback: covLookback,
      cov_shrink: covShrink,
      capital,
      years,
    };

    const { controller } = startMvfRun(payload, {
      onLoaded: (data) => {
        setDropped(data.dropped);
        setTrained((t) => ({ ...t, total: data.universe.length }));
        setStage('training');
      },
      onAsset: (data) =>
        setTrained((t) => ({
          index: data.index,
          total: data.total,
          symbol: data.symbol,
          cached: t.cached + (data.source === 'cached' ? 1 : 0),
        })),
      onForecasting: () => setStage('forecasting'),
      onOptimizing: () => setStage('optimizing'),
      onResult: (r) => {
        setResult(r);
        setDropped(r.dropped);
      },
      onError: (e) => {
        setError(e instanceof Error ? e.message : 'MVF run failed');
        setStage('idle');
      },
      onComplete: () => setStage((s) => (s === 'idle' ? s : 'done')),
    });
    controllerRef.current = controller;
  };

  const cancel = () => {
    controllerRef.current?.abort();
    setStage('idle');
  };

  const progressValue =
    stage === 'training' && trained.total > 0 ? (trained.index / trained.total) * 100 : undefined;

  return (
    <Card variant="outlined">
      <CardContent>
        <Typography variant="h6">MVF — Mean-Variance with Forecasting (LSTM)</Typography>
        <Typography variant="body2" sx={{ color: 'text.secondary', mt: 0.5, mb: 2 }}>
          Markowitz mean-variance is extremely sensitive to its inputs, and historical average
          returns are noisy and backward-looking. MVF replaces expected returns with an LSTM
          forecast — one model per asset, fed past log-returns plus GKYZ volatility — then feeds
          that forward-looking μ and a Ledoit-Wolf-shrunk historical Σ into a capped, long-only
          max-Sharpe optimizer. Ported from{' '}
          <Box component="code" sx={{ fontSize: '0.85em' }}>
            notebooks/mvf_lstm_portfolio.ipynb
          </Box>{' '}
          (Phase 4 — production fit on the full history).
        </Typography>

        <Autocomplete
          multiple
          freeSolo
          fullWidth
          options={symbols.map((s) => s.symbol)}
          value={tickers}
          disabled={running}
          onChange={(_, next) =>
            setTickers([
              ...new Set(next.map((v) => (typeof v === 'string' ? v.toUpperCase() : v))),
            ])
          }
          renderTags={(values, getTagProps) =>
            values.map((option, index) => (
              <Chip
                label={option}
                size="small"
                {...getTagProps({ index })}
                key={option}
                color={symbols.some((s) => s.symbol === option) ? 'primary' : 'default'}
                variant="outlined"
              />
            ))
          }
          renderInput={(params) => (
            <TextField
              {...params}
              label="Universe"
              placeholder="Add ticker"
              helperText={`${tickers.length} tickers — one LSTM is trained per asset (cached across runs)`}
            />
          )}
        />

        <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2, mt: 2 }}>
          <TextField
            label="Horizon (days)"
            type="number"
            size="small"
            sx={{ width: 150 }}
            value={horizon}
            disabled={running}
            onChange={(e) => setHorizon(Number(e.target.value))}
            helperText="Hold period"
          />
          <TextField
            label="Look-back (days)"
            type="number"
            size="small"
            sx={{ width: 150 }}
            value={seqLen}
            disabled={running}
            onChange={(e) => setSeqLen(Number(e.target.value))}
            helperText="LSTM sequence"
          />
          <TextField
            label="Epochs"
            type="number"
            size="small"
            sx={{ width: 120 }}
            value={epochs}
            disabled={running}
            onChange={(e) => setEpochs(Number(e.target.value))}
          />
          <TextField
            label="Max weight (%)"
            type="number"
            size="small"
            sx={{ width: 140 }}
            value={maxWeight}
            disabled={running}
            onChange={(e) => setMaxWeight(Number(e.target.value))}
            helperText="Per-asset cap"
          />
          <TextField
            label="Capital"
            type="number"
            size="small"
            sx={{ width: 190 }}
            value={capital}
            disabled={running}
            onChange={(e) => setCapital(Number(e.target.value))}
            helperText={`${compact(capital)} — EOD close units`}
          />
        </Box>

        <Button size="small" sx={{ mt: 1 }} onClick={() => setShowAdvanced((v) => !v)}>
          {showAdvanced ? 'Hide' : 'Show'} advanced
        </Button>
        <Collapse in={showAdvanced}>
          <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2, alignItems: 'center', mt: 1 }}>
            <TextField
              label="Σ lookback (days)"
              type="number"
              size="small"
              sx={{ width: 160 }}
              value={covLookback}
              disabled={running}
              onChange={(e) => setCovLookback(Number(e.target.value))}
            />
            <TextField
              label="Benchmark"
              size="small"
              sx={{ width: 140 }}
              value={benchmark}
              disabled={running}
              onChange={(e) => setBenchmark(e.target.value)}
              helperText="Market-vol feature"
            />
            <TextField
              label="History (years)"
              type="number"
              size="small"
              sx={{ width: 140 }}
              value={years}
              disabled={running}
              onChange={(e) => setYears(Number(e.target.value))}
            />
            <TextField
              label="Learning rate"
              type="number"
              size="small"
              sx={{ width: 140 }}
              value={lr}
              disabled={running}
              onChange={(e) => setLr(Number(e.target.value))}
            />
            <TextField
              label="Batch size"
              type="number"
              size="small"
              sx={{ width: 130 }}
              value={batchSize}
              disabled={running}
              onChange={(e) => setBatchSize(Number(e.target.value))}
            />
            <FormControlLabel
              control={
                <Switch
                  checked={covShrink}
                  disabled={running}
                  onChange={(e) => setCovShrink(e.target.checked)}
                />
              }
              label="Ledoit-Wolf shrink Σ"
            />
            <FormControlLabel
              control={
                <Switch
                  checked={forceRetrain}
                  disabled={running}
                  onChange={(e) => setForceRetrain(e.target.checked)}
                />
              }
              label="Force retrain"
            />
          </Box>
        </Collapse>

        <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', mt: 2 }}>
          <Button
            variant="contained"
            onClick={run}
            disabled={running || tickers.length < 2}
          >
            {running ? 'Running…' : 'Run MVF'}
          </Button>
          {running && (
            <Button variant="outlined" color="warning" onClick={cancel}>
              Cancel
            </Button>
          )}
        </Box>

        {running && (
          <Box sx={{ mt: 2 }}>
            <Typography variant="caption" sx={{ color: 'text.secondary' }}>
              {STAGE_LABEL[stage]}
              {stage === 'training' && trained.total > 0 && (
                <>
                  {' '}
                  — {trained.index}/{trained.total}
                  {trained.symbol && ` (${trained.symbol})`}
                  {trained.cached > 0 && `, ${trained.cached} from cache`}
                </>
              )}
            </Typography>
            <LinearProgress
              variant={progressValue === undefined ? 'indeterminate' : 'determinate'}
              value={progressValue}
              sx={{ mt: 0.5, height: 6, borderRadius: '3px' }}
            />
            <Typography variant="caption" sx={{ color: 'text.disabled', mt: 0.5, display: 'block' }}>
              A cold run trains one model per asset and can take several minutes. Fitted weights
              are cached, so reruns with the same settings are near-instant.
            </Typography>
          </Box>
        )}

        {error && (
          <Alert severity="error" sx={{ mt: 2 }}>
            {error}
          </Alert>
        )}

        {dropped.length > 0 && (
          <Alert severity="warning" sx={{ mt: 2 }}>
            Skipped for insufficient clean history: {dropped.join(', ')}
          </Alert>
        )}

        {result && (
          <Box sx={{ mt: 3 }}>
            <Box
              sx={{
                display: 'flex',
                flexWrap: 'wrap',
                gap: 3,
                p: 2,
                mb: 2,
                borderRadius: 1,
                bgcolor: 'action.hover',
              }}
            >
              <StatTile
                label="Predicted return"
                value={pct(result.predicted_return)}
                hint="Annualized"
              />
              <StatTile
                label="Predicted volatility"
                value={pct(result.predicted_volatility)}
                hint="Annualized"
              />
              <StatTile
                label="Predicted Sharpe"
                value={result.predicted_sharpe.toFixed(2)}
                hint={`Cap ${pct(result.max_weight, 0)}`}
              />
              <StatTile
                label="Names held"
                value={`${result.holdings.length} / ${result.universe.length}`}
                hint={`${result.bars} bars`}
              />
              <StatTile
                label="As of"
                value={result.as_of}
                hint={`Hold ~${result.horizon} trading days`}
              />
            </Box>

            <Table size="small" sx={{ '& td, & th': { whiteSpace: 'nowrap' } }}>
              <TableHead>
                <TableRow>
                  <TableCell>Ticker</TableCell>
                  <TableCell sx={{ width: '18%' }}>Weight</TableCell>
                  <TableCell align="right">Predicted μ</TableCell>
                  <TableCell align="right">Ann. vol</TableCell>
                  <TableCell align="right">Last price</TableCell>
                  <TableCell align="right">Shares</TableCell>
                  <TableCell align="right">Target value</TableCell>
                  <TableCell align="right">Allocated</TableCell>
                </TableRow>
              </TableHead>
              <TableBody sx={{ '& td': { fontVariantNumeric: 'tabular-nums' } }}>
                {result.holdings.map((h) => (
                  <TableRow key={h.ticker} hover>
                    <TableCell sx={{ fontWeight: 600 }}>{h.ticker}</TableCell>
                    <TableCell>
                      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                        <WeightMeter weight={h.weight} cap={result.max_weight} />
                        <Typography
                          variant="body2"
                          sx={{ minWidth: 44, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}
                        >
                          {pct(h.weight)}
                        </Typography>
                      </Box>
                    </TableCell>
                    <TableCell align="right">{pct(h.pred_ann_return, 0)}</TableCell>
                    <TableCell align="right">{pct(h.ann_vol, 0)}</TableCell>
                    <TableCell align="right">{num(h.last_price, 2)}</TableCell>
                    <TableCell align="right">{num(h.shares)}</TableCell>
                    <TableCell align="right">{compact(h.target_value)}</TableCell>
                    <TableCell align="right">{compact(h.alloc_value)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>

            <Typography variant="caption" sx={{ color: 'text.secondary', display: 'block', mt: 1.5 }}>
              Deployed {compact(result.deployed_value)} of {compact(result.capital)} — cash
              residual {compact(result.cash_residual)} (share counts are floored to whole lots).
              Weights sum to {result.weight_sum.toFixed(3)}.
              {result.excluded.length > 0 &&
                ` Zero-weighted by the optimizer: ${result.excluded.join(', ')}.`}
            </Typography>
          </Box>
        )}
      </CardContent>
    </Card>
  );
}
