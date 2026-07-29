import React from 'react';
import {
  Container,
  Box,
  Typography,
  TextField,
  Button,
  CircularProgress,
  Alert,
  Chip,
  Paper,
  Stack,
  Tooltip,
  Card,
  CardContent,
  Divider,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  IconButton,
} from '@mui/material';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import RadioButtonUncheckedIcon from '@mui/icons-material/RadioButtonUnchecked';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import StopIcon from '@mui/icons-material/Stop';
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';
import RefreshIcon from '@mui/icons-material/Refresh';
import { Markdown } from '../components/Markdown';
import {
  startAnalysis,
  fetchTradingAgentsHealth,
  fetchAnalyses,
  fetchAnalysis,
  type TADecision,
  type TAHealth,
  type AnalysisSummary,
} from '../lib/services/tradingAgents';

// ---------------------------------------------------------------------------
// Agent + pipeline metadata (titanlabs-style "AI council" pipeline)
// ---------------------------------------------------------------------------

interface AgentMeta {
  icon: string;
  label: string;
  subtitle: string;
}

const AGENT_META: Record<string, AgentMeta> = {
  market: { icon: '📈', label: 'Market Analyst', subtitle: 'Price action & technical indicators' },
  news: { icon: '📰', label: 'News & Sentiment', subtitle: 'Company + sector research, KB first' },
  fundamentals: { icon: '📊', label: 'Fundamentals Analyst', subtitle: 'Balance sheet, income & cash flow' },
  research_debate: { icon: '⚖️', label: 'Bull vs Bear Debate', subtitle: 'Research-manager verdict' },
  research_manager: { icon: '🧭', label: 'Investment Plan', subtitle: 'Synthesized research thesis' },
  trader: { icon: '💹', label: 'Trader', subtitle: 'Concrete trade proposal' },
  risk_debate: { icon: '🛡️', label: 'Risk Management', subtitle: 'Aggressive / neutral / conservative' },
  final: { icon: '✅', label: 'Portfolio Manager', subtitle: 'Final decision' },
};

const PIPELINE: { group: string; tint: string; items: string[] }[] = [
  { group: 'Data Analysis', tint: '#3b82f6', items: ['market', 'news', 'fundamentals'] },
  { group: 'Research Debate', tint: '#f59e0b', items: ['research_debate', 'research_manager'] },
  { group: 'Action', tint: '#8b5cf6', items: ['trader'] },
  { group: 'Risk Check', tint: '#ef4444', items: ['risk_debate'] },
  { group: 'Decision', tint: '#22c55e', items: ['final'] },
];

const FLAT_ORDER = PIPELINE.flatMap((s) => s.items);
// Agent cards shown in the grid (everything except the final, which is a hero card).
const CARD_ORDER = FLAT_ORDER.filter((k) => k !== 'final');

const signalColor = (signal: string): 'success' | 'error' | 'warning' | 'default' => {
  const s = signal.toUpperCase();
  if (s.includes('BUY')) return 'success';
  if (s.includes('SELL')) return 'error';
  if (s.includes('HOLD')) return 'warning';
  return 'default';
};

const signalHex = (signal?: string): string => {
  if (!signal) return '#64748b';
  const s = signal.toUpperCase();
  if (s.includes('BUY')) return '#22c55e';
  if (s.includes('SELL')) return '#ef4444';
  if (s.includes('HOLD')) return '#f59e0b';
  return '#64748b';
};

const formatWhen = (iso: string): string => {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
};

type StepStatus = 'done' | 'running' | 'pending';

const TradingAgents: React.FC = () => {
  const [symbol, setSymbol] = React.useState('');
  const [tradeDate, setTradeDate] = React.useState<string>('');
  const [running, setRunning] = React.useState(false);
  const [sections, setSections] = React.useState<Record<string, string>>({});
  const [decision, setDecision] = React.useState<TADecision | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [status, setStatus] = React.useState<string>('');
  const [health, setHealth] = React.useState<TAHealth | null>(null);
  const [started, setStarted] = React.useState<{ symbol: string; date: string } | null>(null);
  const [elapsed, setElapsed] = React.useState<string>('');
  const [analyses, setAnalyses] = React.useState<AnalysisSummary[]>([]);
  const [loadingId, setLoadingId] = React.useState<string | null>(null);
  const [viewingId, setViewingId] = React.useState<string | null>(null);

  const controllerRef = React.useRef<AbortController | null>(null);
  const startMsRef = React.useRef<number>(0);

  const loadHistory = React.useCallback(() => {
    fetchAnalyses()
      .then(setAnalyses)
      .catch(() => setAnalyses([]));
  }, []);

  React.useEffect(() => {
    fetchTradingAgentsHealth()
      .then(setHealth)
      .catch(() => setHealth(null));
    loadHistory();
    return () => controllerRef.current?.abort();
  }, [loadHistory]);

  const openAnalysis = async (id: string) => {
    setLoadingId(id);
    setError(null);
    try {
      const a = await fetchAnalysis(id);
      controllerRef.current?.abort();
      setRunning(false);
      setStatus('');
      setSections(a.sections);
      setDecision({ signal: a.signal, full: a.final_decision });
      setStarted({ symbol: a.symbol, date: a.trade_date });
      setElapsed(a.duration_ms ? `${Math.round(a.duration_ms / 1000)}s` : '');
      setViewingId(id);
      window.scrollTo({ top: 0, behavior: 'smooth' });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoadingId(null);
    }
  };

  const handleRun = () => {
    const sym = symbol.trim().toUpperCase();
    if (!sym) return;

    setRunning(true);
    setSections({});
    setDecision(null);
    setError(null);
    setElapsed('');
    setViewingId(null);
    setStatus('Connecting…');
    startMsRef.current = Date.now();

    const { controller } = startAnalysis(
      { symbol: sym, trade_date: tradeDate || undefined },
      {
        onStarted: (d) => {
          setStarted({ symbol: d.symbol, date: d.date });
          setStatus(`Convening the council for ${d.symbol} as of ${d.date}…`);
        },
        onNode: (node) => setStatus(`Working… (${node})`),
        onReport: (report) =>
          setSections((prev) => ({ ...prev, [report.section]: report.content })),
        onDecision: (d) => setDecision(d),
        onSaved: () => loadHistory(),
        onError: (e) => {
          setError(e instanceof Error ? e.message : String(e));
          setRunning(false);
          setStatus('');
        },
        onComplete: () => {
          setRunning(false);
          setStatus('');
          const secs = Math.round((Date.now() - startMsRef.current) / 1000);
          setElapsed(secs >= 60 ? `${Math.floor(secs / 60)}m ${secs % 60}s` : `${secs}s`);
        },
      },
    );
    controllerRef.current = controller;
  };

  const handleStop = () => {
    controllerRef.current?.abort();
    setRunning(false);
    setStatus('');
  };

  const firstPending = FLAT_ORDER.find((k) => sections[k] === undefined);
  const stepStatus = (key: string): StepStatus => {
    if (sections[key] !== undefined) return 'done';
    if (running && key === firstPending) return 'running';
    return 'pending';
  };

  const hasRun = started !== null || Object.keys(sections).length > 0 || decision !== null;
  const asOf = started?.date ?? tradeDate ?? '';
  const displaySymbol = started?.symbol ?? symbol.trim().toUpperCase();

  const statCards = [
    { label: 'Recommendation', value: decision?.signal ?? (running ? '…' : '—'), accent: signalHex(decision?.signal) },
    { label: 'As of', value: asOf || '—' },
    { label: 'Model', value: health?.deep_think_llm ?? health?.provider ?? '—' },
    { label: 'Duration', value: elapsed || (running ? '…' : '—') },
  ];

  return (
    <Container maxWidth="lg">
      <Box sx={{ py: 3 }}>
        {/* Header */}
        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 2 }}>
          <Box>
            <Typography variant="h4" component="h1" sx={{ fontWeight: 700 }}>
              🤝 AI Trading Council
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Multi-agent analysis over Vietnamese-market data — analysts debate, a trader acts, a
              risk team checks, and a portfolio manager decides.
            </Typography>
          </Box>
          {health && (
            <Tooltip title={health.message}>
              <Chip
                size="small"
                label={
                  health.ollama_reachable
                    ? `${health.provider} · ${health.deep_think_llm}`
                    : `${health.provider} not ready`
                }
                color={health.ollama_reachable ? 'success' : 'error'}
                variant="outlined"
              />
            </Tooltip>
          )}
        </Box>

        {/* Controls */}
        <Paper sx={{ p: 2, mb: 3 }}>
          <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} alignItems="center">
            <TextField
              label="Symbol"
              value={symbol}
              onChange={(e) => setSymbol(e.target.value.toUpperCase())}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !running) handleRun();
              }}
              size="small"
              placeholder="e.g. FPT"
              disabled={running}
              sx={{ width: 160 }}
            />
            <TextField
              label="As-of date"
              type="date"
              value={tradeDate}
              onChange={(e) => setTradeDate(e.target.value)}
              size="small"
              disabled={running}
              InputLabelProps={{ shrink: true }}
            />
            {running ? (
              <Button variant="outlined" color="error" startIcon={<StopIcon />} onClick={handleStop}>
                Stop
              </Button>
            ) : (
              <Button
                variant="contained"
                startIcon={<PlayArrowIcon />}
                onClick={handleRun}
                disabled={!symbol.trim()}
              >
                Run analysis
              </Button>
            )}
            {running && (
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                <CircularProgress size={18} />
                <Typography variant="body2" color="text.secondary">
                  {status}
                </Typography>
              </Box>
            )}
          </Stack>
        </Paper>

        {error && (
          <Alert severity="error" sx={{ mb: 3 }} onClose={() => setError(null)}>
            {error}
          </Alert>
        )}

        {/* History dashboard */}
        {analyses.length > 0 && (
          <Paper variant="outlined" sx={{ mb: 3 }}>
            <Box
              sx={{
                px: 2,
                py: 1.5,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
              }}
            >
              <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>
                Saved analyses ({analyses.length})
              </Typography>
              <Tooltip title="Refresh">
                <IconButton size="small" onClick={loadHistory}>
                  <RefreshIcon fontSize="small" />
                </IconButton>
              </Tooltip>
            </Box>
            <Divider />
            <TableContainer sx={{ maxHeight: 320 }}>
              <Table size="small" stickyHeader>
                <TableHead>
                  <TableRow>
                    <TableCell>Symbol</TableCell>
                    <TableCell>Signal</TableCell>
                    <TableCell>Model</TableCell>
                    <TableCell>As of</TableCell>
                    <TableCell>When</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {analyses.map((a) => (
                    <TableRow
                      key={a.id}
                      hover
                      onClick={() => openAnalysis(a.id)}
                      sx={{ cursor: 'pointer' }}
                      selected={viewingId === a.id}
                    >
                      <TableCell sx={{ fontWeight: 700 }}>{a.symbol}</TableCell>
                      <TableCell>
                        <Chip
                          size="small"
                          label={loadingId === a.id ? '…' : a.signal || '—'}
                          color={signalColor(a.signal)}
                          variant="outlined"
                        />
                      </TableCell>
                      <TableCell sx={{ color: 'text.secondary' }}>{a.model}</TableCell>
                      <TableCell sx={{ color: 'text.secondary' }}>{a.trade_date}</TableCell>
                      <TableCell sx={{ color: 'text.secondary', whiteSpace: 'nowrap' }}>
                        {formatWhen(a.created_at)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          </Paper>
        )}

        {!hasRun && !error && (
          <Paper variant="outlined" sx={{ p: 6, textAlign: 'center', borderStyle: 'dashed' }}>
            <Typography variant="h6" color="text.secondary" gutterBottom>
              Convene the council
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Enter a symbol (e.g. FPT) and run an analysis to watch the agents work through their
              pipeline and reach a decision.
            </Typography>
          </Paper>
        )}

        {hasRun && (
          <>
            {/* Hero card */}
            <Paper
              elevation={0}
              sx={{
                p: 3,
                mb: 3,
                borderRadius: 3,
                color: '#fff',
                background: 'linear-gradient(135deg, #1e3a5f 0%, #2d1b4e 100%)',
              }}
            >
              <Box
                sx={{
                  display: 'flex',
                  flexWrap: 'wrap',
                  gap: 2,
                  justifyContent: 'space-between',
                  alignItems: 'flex-start',
                }}
              >
                <Box>
                  <Typography variant="overline" sx={{ opacity: 0.7, letterSpacing: 1 }}>
                    ✨ AI TRADING COUNCIL
                  </Typography>
                  <Typography variant="h3" sx={{ fontWeight: 800, lineHeight: 1 }}>
                    {displaySymbol || '—'}
                  </Typography>
                  <Typography variant="body2" sx={{ opacity: 0.8, mt: 1 }}>
                    {asOf ? `Analysis as of ${asOf}` : 'Vietnamese equity'} · eight perspectives,
                    debated and risk-checked.
                  </Typography>
                </Box>
                <Box sx={{ textAlign: 'right' }}>
                  <Chip
                    label={decision?.signal ?? (running ? 'analyzing…' : 'pending')}
                    sx={{
                      fontWeight: 800,
                      fontSize: '1rem',
                      py: 2.2,
                      px: 1,
                      color: '#fff',
                      bgcolor: signalHex(decision?.signal),
                    }}
                  />
                </Box>
              </Box>

              {/* Stat cards */}
              <Box
                sx={{
                  mt: 3,
                  display: 'grid',
                  gap: 1.5,
                  gridTemplateColumns: { xs: '1fr 1fr', md: 'repeat(4, 1fr)' },
                }}
              >
                {statCards.map((s) => (
                  <Box
                    key={s.label}
                    sx={{
                      p: 1.5,
                      borderRadius: 2,
                      bgcolor: 'rgba(255,255,255,0.08)',
                      border: '1px solid rgba(255,255,255,0.12)',
                    }}
                  >
                    <Typography variant="caption" sx={{ opacity: 0.7, textTransform: 'uppercase' }}>
                      {s.label}
                    </Typography>
                    <Typography
                      variant="h6"
                      sx={{ fontWeight: 700, color: s.accent ?? '#fff', wordBreak: 'break-word' }}
                    >
                      {s.value}
                    </Typography>
                  </Box>
                ))}
              </Box>
            </Paper>

            {/* Agent pipeline */}
            <Typography variant="overline" color="text.secondary" sx={{ fontWeight: 700 }}>
              Agent pipeline
            </Typography>
            <Box
              sx={{
                mb: 3,
                mt: 1,
                display: 'grid',
                gap: 1.5,
                gridTemplateColumns: {
                  xs: '1fr',
                  sm: 'repeat(2, 1fr)',
                  md: `repeat(${PIPELINE.length}, 1fr)`,
                },
              }}
            >
              {PIPELINE.map((stage) => (
                <Paper
                  key={stage.group}
                  variant="outlined"
                  sx={{ p: 1.5, borderTop: 3, borderTopColor: stage.tint, borderRadius: 2 }}
                >
                  <Typography
                    variant="caption"
                    sx={{ fontWeight: 700, textTransform: 'uppercase', color: stage.tint }}
                  >
                    {stage.group}
                  </Typography>
                  <Stack spacing={0.75} sx={{ mt: 1 }}>
                    {stage.items.map((key) => {
                      const st = stepStatus(key);
                      return (
                        <Box key={key} sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
                          {st === 'done' && (
                            <CheckCircleIcon sx={{ fontSize: 16, color: 'success.main' }} />
                          )}
                          {st === 'running' && <CircularProgress size={13} />}
                          {st === 'pending' && (
                            <RadioButtonUncheckedIcon
                              sx={{ fontSize: 16, color: 'text.disabled' }}
                            />
                          )}
                          <Typography
                            variant="caption"
                            sx={{
                              color: st === 'pending' ? 'text.disabled' : 'text.primary',
                              fontWeight: st === 'running' ? 700 : 400,
                            }}
                          >
                            {AGENT_META[key]?.label ?? key}
                          </Typography>
                        </Box>
                      );
                    })}
                  </Stack>
                </Paper>
              ))}
            </Box>

            {/* Final decision (conclusion) */}
            {sections.final !== undefined && (
              <Card
                variant="outlined"
                sx={{ mb: 3, borderLeft: 6, borderLeftColor: signalHex(decision?.signal) }}
              >
                <CardContent>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 1 }}>
                    <Typography variant="h6" sx={{ fontWeight: 700 }}>
                      {AGENT_META.final.icon} {AGENT_META.final.label} — Final Decision
                    </Typography>
                    {decision?.signal && (
                      <Chip label={decision.signal} color={signalColor(decision.signal)} size="small" />
                    )}
                  </Box>
                  <Divider sx={{ mb: 1 }} />
                  <Markdown>{sections.final}</Markdown>
                </CardContent>
              </Card>
            )}

            {/* Agent report cards */}
            <Box
              sx={{
                display: 'grid',
                gap: 2,
                gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' },
              }}
            >
              {CARD_ORDER.filter((k) => sections[k] !== undefined).map((key) => {
                const meta = AGENT_META[key];
                return (
                  <Card key={key} variant="outlined" sx={{ borderRadius: 2 }}>
                    <CardContent>
                      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                        <Typography variant="h6" sx={{ fontWeight: 700 }}>
                          {meta?.icon} {meta?.label ?? key}
                        </Typography>
                      </Box>
                      <Typography variant="caption" color="text.secondary">
                        {meta?.subtitle}
                      </Typography>
                      <Divider sx={{ my: 1 }} />
                      <Markdown>{sections[key]}</Markdown>
                    </CardContent>
                  </Card>
                );
              })}
            </Box>

            {/* Disclaimer */}
            <Alert
              icon={<InfoOutlinedIcon fontSize="inherit" />}
              severity="info"
              variant="outlined"
              sx={{ mt: 3 }}
            >
              This report is auto-generated by an AI multi-agent system from available market data
              and public web sources at run time. It is research for reference only — not personal
              investment advice. Every buy/sell decision is your own responsibility.
            </Alert>
          </>
        )}
      </Box>
    </Container>
  );
};

export default TradingAgents;
