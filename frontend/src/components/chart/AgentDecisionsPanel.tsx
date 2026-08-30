/**
 * Final call from each saved multi-agent analysis of the current symbol.
 *
 * The list endpoint returns a 240-character server-side snippet, which is all
 * the collapsed rows need. Expanding a row fetches that analysis's full
 * final_decision on demand and caches it, so reading one verdict in full never
 * costs 50 multi-kilobyte payloads. The full debate still lives on the Trading
 * Agents page.
 */
import { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Box,
  ButtonBase,
  Chip,
  CircularProgress,
  Collapse,
  IconButton,
  Stack,
  Tooltip,
  Typography,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import RefreshIcon from '@mui/icons-material/Refresh';
import {
  fetchAnalyses,
  fetchAnalysis,
  type AnalysisSummary,
} from '../../lib/services/tradingAgents';
import { Markdown } from '../Markdown';
import { EmptyState, ErrorState } from '../ui';

/**
 * Map a free-text verdict onto the market palette. The agents emit more than
 * BUY/SELL/HOLD — "Overweight", "Underweight", "Accumulate" and friends all
 * show up — so this matches on intent rather than an exact enum.
 */
function decisionTone(signal: string): { color: string; bg: string } {
  const s = (signal || '').toUpperCase();
  if (/\b(BUY|OVERWEIGHT|ACCUMULATE|LONG|BULLISH)\b/.test(s)) {
    return { color: 'market.long', bg: 'market.longSubtle' };
  }
  if (/\b(SELL|UNDERWEIGHT|REDUCE|SHORT|BEARISH)\b/.test(s)) {
    return { color: 'market.short', bg: 'market.shortSubtle' };
  }
  if (/\b(HOLD|NEUTRAL|WAIT)\b/.test(s)) {
    return { color: 'warning.main', bg: 'warning.main' };
  }
  return { color: 'text.secondary', bg: 'action.hover' };
}

export default function AgentDecisionsPanel({ symbol }: { symbol: string }) {
  const [analyses, setAnalyses] = useState<AnalysisSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  // id -> full final_decision, fetched once per analysis and kept for the session.
  const [fullText, setFullText] = useState<Record<string, string>>({});
  const [detailLoading, setDetailLoading] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    const sym = symbol.trim().toUpperCase();
    if (!sym) {
      setAnalyses([]);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const rows = await fetchAnalyses(sym, 50);
      // Newest first — the current call is what matters most.
      setAnalyses(
        [...rows].sort((a, b) => (b.created_at ?? '').localeCompare(a.created_at ?? '')),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load agent analyses');
    } finally {
      setLoading(false);
    }
  }, [symbol]);

  useEffect(() => {
    void load();
  }, [load]);

  // Switching symbols reloads the list; drop the open row and its cache with it.
  useEffect(() => {
    setExpandedId(null);
    setFullText({});
    setDetailError({});
  }, [symbol]);

  const toggle = useCallback(
    async (id: string) => {
      if (expandedId === id) {
        setExpandedId(null);
        return;
      }
      setExpandedId(id);
      if (fullText[id] !== undefined) return;

      setDetailLoading(id);
      setDetailError((prev) => {
        const { [id]: _drop, ...rest } = prev;
        return rest;
      });
      try {
        const detail = await fetchAnalysis(id);
        setFullText((prev) => ({ ...prev, [id]: detail.final_decision ?? '' }));
      } catch (e) {
        setDetailError((prev) => ({
          ...prev,
          [id]: e instanceof Error ? e.message : 'Failed to load the full decision',
        }));
      } finally {
        setDetailLoading(null);
      }
    },
    [expandedId, fullText],
  );

  const latest = analyses[0];

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', minHeight: 0, minWidth: 0, width: '100%' }}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.75}
        sx={{ px: 0.75, pb: 0.5, flexShrink: 0, minWidth: 0 }}
      >
        <Typography sx={{ fontSize: 11.5, fontWeight: 700, letterSpacing: 0.3 }}>
          AGENT CALLS
        </Typography>
        <Typography sx={{ fontSize: 11.5, color: 'primary.light', fontWeight: 700 }}>
          {symbol}
        </Typography>
        {loading && <CircularProgress size={10} />}
        <Box sx={{ flex: 1 }} />
        {!loading && analyses.length > 0 && (
          <Typography sx={{ fontSize: 10, color: 'text.disabled', whiteSpace: 'nowrap' }}>
            {analyses.length}
          </Typography>
        )}
        <Tooltip title="Refresh">
          <span>
            <IconButton size="small" onClick={() => void load()} disabled={loading} sx={{ p: 0.25 }}>
              <RefreshIcon sx={{ fontSize: 14 }} />
            </IconButton>
          </span>
        </Tooltip>
      </Stack>

      {/* Latest call, called out — the rest is history. */}
      {latest && !loading && (
        <Box
          sx={{
            mx: 0.75,
            mb: 0.75,
            p: 1,
            borderRadius: 1,
            border: 1,
            borderColor: decisionTone(latest.signal).color,
            bgcolor: 'surface.inset',
            flexShrink: 0,
          }}
        >
          <Typography variant="overline2" sx={{ fontSize: 9 }}>
            Latest call
          </Typography>
          <Typography
            sx={{ fontSize: 18, fontWeight: 700, lineHeight: 1.2, color: decisionTone(latest.signal).color }}
          >
            {latest.signal || '—'}
          </Typography>
          <Typography variant="mono" sx={{ fontSize: 9.5, color: 'text.tertiary' }}>
            {latest.trade_date} · {latest.model}
          </Typography>
        </Box>
      )}

      <Box sx={{ flex: 1, minHeight: 0, overflowY: 'auto', px: 0.75, pb: 0.75 }}>
        {error ? (
          <ErrorState error={error} title="Could not load agent calls" onRetry={() => void load()} />
        ) : !loading && analyses.length === 0 ? (
          <EmptyState
            compact
            title="No agent analyses"
            description={`Run an analysis for ${symbol} on the Trading Agents page.`}
          />
        ) : (
          <Stack spacing={0.5}>
            {analyses.map((a) => {
              const tone = decisionTone(a.signal);
              const open = expandedId === a.id;
              const full = fullText[a.id];
              return (
                <Box
                  key={a.id}
                  sx={{
                    borderRadius: 1,
                    border: 1,
                    borderColor: open ? tone.color : 'line.subtle',
                    bgcolor: 'surface.inset',
                    minWidth: 0,
                  }}
                >
                  <ButtonBase
                    onClick={() => void toggle(a.id)}
                    aria-expanded={open}
                    sx={{ width: '100%', justifyContent: 'stretch', borderRadius: 1 }}
                  >
                    <Stack
                      direction="row"
                      alignItems="center"
                      spacing={0.75}
                      sx={{ px: 0.75, py: 0.5, width: '100%', minWidth: 0 }}
                    >
                      <Chip
                        label={a.signal || '—'}
                        size="small"
                        sx={{
                          color: tone.color,
                          borderColor: tone.color,
                          border: 1,
                          bgcolor: 'transparent',
                          flexShrink: 0,
                        }}
                      />
                      <Box sx={{ flex: 1, minWidth: 0, textAlign: 'left' }}>
                        <Typography variant="mono" sx={{ fontSize: 10, display: 'block' }}>
                          {a.trade_date}
                        </Typography>
                        <Typography
                          sx={{ fontSize: 9.5, color: 'text.tertiary' }}
                          noWrap
                          component="div"
                        >
                          {a.model}
                        </Typography>
                      </Box>
                      {detailLoading === a.id ? (
                        <CircularProgress size={10} sx={{ flexShrink: 0 }} />
                      ) : (
                        <ExpandMoreIcon
                          sx={{
                            fontSize: 14,
                            flexShrink: 0,
                            color: 'text.tertiary',
                            transform: open ? 'rotate(180deg)' : 'none',
                            transition: 'transform 120ms',
                          }}
                        />
                      )}
                    </Stack>
                  </ButtonBase>

                  <Collapse in={open} unmountOnExit>
                    <Box
                      sx={{
                        px: 0.75,
                        pb: 0.75,
                        maxHeight: 360,
                        overflowY: 'auto',
                        borderTop: 1,
                        borderColor: 'line.subtle',
                        // The verdict is long-form markdown in a narrow column.
                        fontSize: 11,
                        '& p': { margin: '0.4em 0' },
                        '& h1, & h2, & h3': { fontSize: 12, margin: '0.6em 0 0.3em' },
                        '& table': { fontSize: 10, width: '100%' },
                        '& pre, & code': { fontSize: 10, whiteSpace: 'pre-wrap' },
                      }}
                    >
                      {detailError[a.id] ? (
                        <Alert severity="error" sx={{ mt: 0.75, fontSize: 10, py: 0 }}>
                          {detailError[a.id]}
                        </Alert>
                      ) : full !== undefined ? (
                        full.trim() ? (
                          <Markdown>{full}</Markdown>
                        ) : (
                          <Typography sx={{ fontSize: 10, color: 'text.tertiary', mt: 0.75 }}>
                            This analysis recorded no final decision.
                          </Typography>
                        )
                      ) : (
                        // Snippet stands in until the full text lands, so the row
                        // never expands to a blank box.
                        <Typography sx={{ fontSize: 10.5, color: 'text.tertiary', mt: 0.75 }}>
                          {a.snippet || 'Loading…'}
                        </Typography>
                      )}
                    </Box>
                  </Collapse>
                </Box>
              );
            })}
          </Stack>
        )}
      </Box>
    </Box>
  );
}
