/**
 * Chart watchlist panel.
 *
 * The charting library's built-in Watch List widget ships only with the Trading
 * Terminal package; this deployment serves the Advanced Charts build (no
 * `window.widgetbar`), so the list lives in the app instead and drives the
 * chart's symbol through `onSelect`.
 *
 * Rows are quoted from `POST /quote/batch`: live matched trades where the
 * provider has them, and the app's own last end-of-day bar for indices and
 * symbols that have not traded yet. Polling follows the VN session, so a closed
 * market costs one request on mount and nothing after.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Box,
  IconButton,
  InputAdornment,
  List,
  ListItemButton,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import { Add, Close, DeleteOutline } from '@mui/icons-material';

import { fetchQuotes, isVnMarketSession, type LatestQuote } from '../../lib/services/quote';

/** How often quotes refresh while the market is open. */
const POLL_MS = 5_000;

const STORAGE_KEY = 'chartWatchlist';

const DEFAULT_SYMBOLS = ['VNINDEX', 'VCG', 'SHS', 'HPG'];

const UP = '#22c55e';
const DOWN = '#ef4444';
const FLAT = '#9ca3af';

function loadSymbols(): string[] {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (!stored) return DEFAULT_SYMBOLS;
    const parsed: unknown = JSON.parse(stored);
    if (!Array.isArray(parsed)) return DEFAULT_SYMBOLS;
    const symbols = parsed
      .filter((s): s is string => typeof s === 'string')
      .map((s) => s.trim().toUpperCase())
      .filter(Boolean);
    // An empty stored list is a real state (user removed everything).
    return symbols;
  } catch {
    return DEFAULT_SYMBOLS;
  }
}

/** Prices are in thousands of VND; indices are whole numbers of points. */
function formatPrice(quote: LatestQuote): string {
  return quote.price.toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function formatPct(pct: number): string {
  return `${pct > 0 ? '+' : ''}${pct.toFixed(2)}%`;
}

function colorFor(pct: number | null | undefined): string {
  if (pct == null || pct === 0) return FLAT;
  return pct > 0 ? UP : DOWN;
}

export interface WatchlistProps {
  /** Symbol currently shown on the chart (highlighted in the list). */
  activeSymbol: string;
  /** Called when a row is clicked. */
  onSelect: (symbol: string) => void;
}

export default function Watchlist({ activeSymbol, onSelect }: WatchlistProps) {
  const [symbols, setSymbols] = useState<string[]>(loadSymbols);
  const [quotes, setQuotes] = useState<Record<string, LatestQuote>>({});
  const [unavailable, setUnavailable] = useState<Set<string>>(new Set());
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState('');
  const [error, setError] = useState<string | null>(null);

  // The poll reads the current symbol list without re-subscribing on every edit.
  const symbolsRef = useRef(symbols);
  symbolsRef.current = symbols;

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(symbols));
    } catch { /* quota / private mode — ignore */ }
  }, [symbols]);

  const refresh = useCallback(async () => {
    const list = symbolsRef.current;
    if (list.length === 0) {
      setQuotes({});
      setUnavailable(new Set());
      return;
    }
    try {
      const batch = await fetchQuotes(list);
      setQuotes(Object.fromEntries(batch.quotes.map((q) => [q.symbol, q])));
      setUnavailable(new Set(batch.unavailable));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load quotes');
    }
  }, []);

  useEffect(() => {
    // Prime once regardless of session state so the list shows last prices, then
    // keep refreshing only while the market is open.
    void refresh();
    const timer = window.setInterval(() => {
      if (isVnMarketSession()) void refresh();
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [refresh]);

  // A newly added symbol should not wait for the next tick.
  const addSymbol = () => {
    const symbol = draft.trim().toUpperCase();
    setDraft('');
    if (!symbol) { setAdding(false); return; }
    if (!symbols.includes(symbol)) {
      setSymbols((prev) => [...prev, symbol]);
      symbolsRef.current = [...symbolsRef.current, symbol];
      void refresh();
    }
    setAdding(false);
  };

  const removeSymbol = (symbol: string) => {
    setSymbols((prev) => prev.filter((s) => s !== symbol));
    symbolsRef.current = symbolsRef.current.filter((s) => s !== symbol);
  };

  const active = activeSymbol.trim().toUpperCase();
  const rows = useMemo(
    () => symbols.map((symbol) => ({ symbol, quote: quotes[symbol] })),
    [symbols, quotes],
  );

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ px: 1, pb: 0.5 }}>
        <Typography variant="subtitle2" sx={{ color: '#e5e7eb', fontWeight: 600, letterSpacing: 0.3 }}>
          Watchlist
        </Typography>
        <Tooltip title={adding ? 'Cancel' : 'Add symbol'}>
          <IconButton size="small" onClick={() => { setAdding((p) => !p); setDraft(''); }} sx={{ color: '#9ca3af' }}>
            {adding ? <Close fontSize="small" /> : <Add fontSize="small" />}
          </IconButton>
        </Tooltip>
      </Stack>

      {adding && (
        <Box sx={{ px: 1, pb: 1 }}>
          <TextField
            autoFocus
            fullWidth
            size="small"
            placeholder="Ticker, e.g. FPT"
            value={draft}
            onChange={(e) => setDraft(e.target.value.toUpperCase())}
            onKeyDown={(e) => {
              if (e.key === 'Enter') addSymbol();
              if (e.key === 'Escape') { setAdding(false); setDraft(''); }
            }}
            InputProps={{
              endAdornment: (
                <InputAdornment position="end">
                  <IconButton size="small" onClick={addSymbol} sx={{ color: '#9ca3af' }}>
                    <Add fontSize="small" />
                  </IconButton>
                </InputAdornment>
              ),
              sx: { color: '#e5e7eb', fontSize: 13 },
            }}
            sx={{
              '& .MuiOutlinedInput-notchedOutline': { borderColor: 'rgba(99,102,241,0.3)' },
            }}
          />
        </Box>
      )}

      {error && (
        <Typography variant="caption" sx={{ px: 1, pb: 0.5, color: DOWN }}>
          {error}
        </Typography>
      )}

      <List dense disablePadding sx={{ overflowY: 'auto', flex: 1, minHeight: 0 }}>
        {rows.length === 0 && (
          <Typography variant="caption" sx={{ px: 1, color: FLAT }}>
            No symbols yet — add one with the + button.
          </Typography>
        )}
        {rows.map(({ symbol, quote }) => {
          const isActive = symbol === active;
          const pct = quote?.change_pct ?? null;
          return (
            <ListItemButton
              key={symbol}
              selected={isActive}
              onClick={() => onSelect(symbol)}
              sx={{
                py: 0.5,
                px: 1,
                borderLeft: '2px solid',
                borderLeftColor: isActive ? '#6366f1' : 'transparent',
                '&.Mui-selected': { bgcolor: 'rgba(99,102,241,0.14)' },
                '&.Mui-selected:hover': { bgcolor: 'rgba(99,102,241,0.2)' },
                '& .wl-remove': { opacity: 0 },
                '&:hover .wl-remove': { opacity: 1 },
              }}
            >
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <Stack direction="row" alignItems="center" spacing={0.5}>
                  <Typography
                    noWrap
                    sx={{ fontSize: 13, fontWeight: 600, color: isActive ? '#c7d2fe' : '#e5e7eb' }}
                  >
                    {symbol}
                  </Typography>
                  {quote?.source === 'eod' && (
                    <Tooltip title="End-of-day close — no live trade for this symbol">
                      <Typography sx={{ fontSize: 9, color: FLAT, border: '1px solid', borderColor: FLAT, borderRadius: 0.5, px: 0.3, lineHeight: 1.4 }}>
                        EOD
                      </Typography>
                    </Tooltip>
                  )}
                </Stack>
              </Box>

              <Stack alignItems="flex-end" sx={{ minWidth: 84 }}>
                <Typography sx={{ fontSize: 13, color: quote ? '#e5e7eb' : FLAT, fontVariantNumeric: 'tabular-nums' }}>
                  {quote ? formatPrice(quote) : unavailable.has(symbol) ? 'n/a' : '—'}
                </Typography>
                {pct != null && (
                  <Typography sx={{ fontSize: 11, color: colorFor(pct), fontVariantNumeric: 'tabular-nums' }}>
                    {formatPct(pct)}
                  </Typography>
                )}
              </Stack>

              <Tooltip title="Remove">
                <IconButton
                  className="wl-remove"
                  size="small"
                  onClick={(e) => { e.stopPropagation(); removeSymbol(symbol); }}
                  sx={{ ml: 0.5, color: FLAT, transition: 'opacity 120ms' }}
                >
                  <DeleteOutline sx={{ fontSize: 15 }} />
                </IconButton>
              </Tooltip>
            </ListItemButton>
          );
        })}
      </List>
    </Box>
  );
}
