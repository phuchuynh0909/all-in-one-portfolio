import React from 'react';
import { PageContainer, PageHeader, Panel, EmptyState } from '../components/ui';
import {
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
  Autocomplete,
  Collapse,
  Link,
} from '@mui/material';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import RadioButtonUncheckedIcon from '@mui/icons-material/RadioButtonUnchecked';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import StopIcon from '@mui/icons-material/Stop';
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';
import RefreshIcon from '@mui/icons-material/Refresh';
import LinkIcon from '@mui/icons-material/Link';
import { Markdown } from '../components/Markdown';
import {
  startAnalysis,
  fetchTradingAgentsHealth,
  fetchModelOptions,
  fetchAnalyses,
  fetchAnalysis,
  fetchTcbsStatus,
  startTcbsLogin,
  modelChoices,
  type TADecision,
  type TAHealth,
  type TAModelOptions,
  type TALlmRole,
  type ModelChoice,
  type AnalysisSummary,
  type TATcbsStatus,
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
  research_debate: { icon: '⚖️', label: 'Bull vs Bear Debate', subtitle: 'Researcher turns, verbatim' },
  research_manager: { icon: '🧭', label: 'Investment Plan', subtitle: 'Research-manager verdict' },
  trader: { icon: '💹', label: 'Trader', subtitle: 'Concrete trade proposal' },
  risk_debate: { icon: '🛡️', label: 'Risk Debate', subtitle: 'Aggressive / conservative / neutral turns' },
  final: { icon: '✅', label: 'Portfolio Manager', subtitle: 'Final decision' },
};

const PIPELINE: { group: string; tint: string; items: string[] }[] = [
  { group: 'Data Analysis', tint: 'var(--color-chart-series-6)', items: ['market', 'news', 'fundamentals'] },
  { group: 'Research Debate', tint: 'var(--color-accent)', items: ['research_debate', 'research_manager'] },
  { group: 'Action', tint: 'var(--color-chart-series-3)', items: ['trader'] },
  { group: 'Risk Check', tint: 'var(--color-short)', items: ['risk_debate'] },
  { group: 'Decision', tint: 'var(--color-long)', items: ['final'] },
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
  if (!signal) return 'var(--color-text-tertiary)';
  const s = signal.toUpperCase();
  if (s.includes('BUY')) return 'var(--color-long)';
  if (s.includes('SELL')) return 'var(--color-short)';
  if (s.includes('HOLD')) return 'var(--color-warning)';
  return 'var(--color-text-tertiary)';
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

// Analysts that can be pinned to their own model, in pipeline order. Mirrors the
// backend's ANALYST_MODEL_KEYS minus `social`, which this deployment never runs
// (the VN news tool carries sentiment/sector context into the News Analyst).
const MODEL_PICKABLE_ANALYSTS = ['market', 'news', 'fundamentals'] as const;

/**
 * Display a resolved role: bare model on the default provider, `provider:model`
 * when it sits elsewhere — the provider is only worth the space when it differs.
 */
const specLabel = (role?: TALlmRole, defaultProvider?: string): string => {
  if (!role) return '';
  return role.provider === defaultProvider ? role.model : `${role.provider}:${role.model}`;
};

/**
 * One model field. Options are `provider:model` specs grouped by provider, so
 * roles can be spread across providers (deep on OpenAI, analysts on DeepSeek);
 * free text is still allowed since Ollama/OpenRouter serve arbitrary model IDs,
 * and a bare name goes to the backend's default provider. Empty means "leave it
 * to the backend", shown as the default in the placeholder.
 */
const ModelPicker: React.FC<{
  label: string;
  placeholder: string;
  value: string;
  onChange: (model: string) => void;
  choices: ModelChoice[];
  disabled?: boolean;
}> = ({ label, placeholder, value, onChange, choices, disabled }) => (
  <Autocomplete<ModelChoice, false, false, true>
    freeSolo
    size="small"
    disabled={disabled}
    options={choices}
    // The input text is the spec — that is what gets sent and parsed. The row
    // shows the model alone, since the group header already names the provider.
    getOptionLabel={(option) => (typeof option === 'string' ? option : option.spec)}
    groupBy={(option) => option.provider}
    renderOption={(props, option) => (
      <li {...props} key={option.spec}>
        {option.model}
      </li>
    )}
    // Text and selection are one piece of state: null while the text is a
    // free-typed model ID, which is legal here and must not be reset to a pick.
    value={choices.find((c) => c.spec === value) ?? null}
    inputValue={value}
    onChange={(_e, next) =>
      onChange(typeof next === 'string' ? next : next?.spec ?? '')
    }
    onInputChange={(_e, next, reason) => {
      // MUI re-syncs the input from `value` on blur; ignoring that empty reset
      // keeps a typed-but-unlisted model (Ollama tags, OpenRouter IDs) intact.
      if (reason === 'reset' && !next) return;
      onChange(next);
    }}
    autoHighlight
    selectOnFocus
    handleHomeEndKeys
    sx={{ minWidth: 230, flex: '1 1 230px' }}
    renderInput={(params) => (
      <TextField {...params} label={label} placeholder={placeholder} />
    )}
  />
);

const TradingAgents: React.FC = () => {
  const [symbol, setSymbol] = React.useState('');
  const [tradeDate, setTradeDate] = React.useState<string>('');
  const [running, setRunning] = React.useState(false);
  const [sections, setSections] = React.useState<Record<string, string>>({});
  const [decision, setDecision] = React.useState<TADecision | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [status, setStatus] = React.useState<string>('');
  const [health, setHealth] = React.useState<TAHealth | null>(null);
  // TCBS connector: null until the status call answers, so the banner never
  // flashes "not connected" at a page that simply has not asked yet.
  const [tcbs, setTcbs] = React.useState<TATcbsStatus | null>(null);
  const [connecting, setConnecting] = React.useState(false);
  const [started, setStarted] = React.useState<{ symbol: string; date: string } | null>(null);
  const [elapsed, setElapsed] = React.useState<string>('');
  const [analyses, setAnalyses] = React.useState<AnalysisSummary[]>([]);
  const [loadingId, setLoadingId] = React.useState<string | null>(null);
  const [viewingId, setViewingId] = React.useState<string | null>(null);

  // Per-run model selection. Empty string = "use the server default", so a run
  // never pins a model the user did not choose.
  const [modelOptions, setModelOptions] = React.useState<TAModelOptions | null>(null);
  const [showModels, setShowModels] = React.useState(false);
  const [quickModel, setQuickModel] = React.useState('');
  const [deepModel, setDeepModel] = React.useState('');
  const [analystModels, setAnalystModels] = React.useState<Record<string, string>>({});
  // Models the *last run* resolved to (echoed by the `started` event).
  const [ranModels, setRanModels] = React.useState<{
    quick: string;
    deep: string;
    analysts: Record<string, string>;
  } | null>(null);

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
    fetchModelOptions()
      .then(setModelOptions)
      .catch(() => setModelOptions(null));
    fetchTcbsStatus()
      .then(setTcbs)
      .catch(() => setTcbs(null));
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
      // Saved rows only record the manager model, so show that and nothing more.
      setRanModels({ quick: '', deep: a.model, analysts: {} });
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

    // Only send what the user actually picked — anything omitted falls back to
    // the backend's env-configured model.
    const pinned = Object.fromEntries(
      Object.entries(analystModels).filter(([, model]) => model.trim()),
    );

    const { controller } = startAnalysis(
      {
        symbol: sym,
        trade_date: tradeDate || undefined,
        quick_think_llm: quickModel.trim() || undefined,
        deep_think_llm: deepModel.trim() || undefined,
        analyst_models: Object.keys(pinned).length ? pinned : undefined,
      },
      {
        onStarted: (d) => {
          setStarted({ symbol: d.symbol, date: d.date });
          // Roles carry the provider each model ran on; fall back to the flat
          // fields if an older backend omits them.
          const roles = d.llm_roles ?? {};
          setRanModels({
            quick: specLabel(roles.quick, d.provider) || d.quick_think_llm || '',
            deep: specLabel(roles.deep, d.provider) || d.deep_think_llm || '',
            analysts: Object.fromEntries(
              Object.entries(roles)
                .filter(([role]) => role !== 'quick' && role !== 'deep')
                .map(([role, spec]) => [role, specLabel(spec, d.provider)]),
            ),
          });
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
          // A run is when the connector actually gets used, so it is also when
          // a lapsed token would have surfaced. Re-read rather than let the
          // header keep claiming a connection the run just found broken.
          fetchTcbsStatus().then(setTcbs).catch(() => undefined);
          const secs = Math.round((Date.now() - startMsRef.current) / 1000);
          setElapsed(secs >= 60 ? `${Math.floor(secs / 60)}m ${secs % 60}s` : `${secs}s`);
        },
      },
    );
    controllerRef.current = controller;
  };

  /**
   * Send the user to TCBS to authorize the connector. The redirect comes back
   * to this page, so the status call on mount reflects the new token.
   */
  const handleConnectTcbs = async () => {
    setConnecting(true);
    setError(null);
    try {
      window.location.href = await startTcbsLogin(window.location.href);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setConnecting(false);
    }
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

  // The whole verdict, whichever source carries it. Longest wins: the streamed
  // section and the stored decision are the same text, but a run interrupted
  // mid-stream leaves a partial section behind while the saved row is complete.
  const finalDecisionText = [sections.final, decision?.full]
    .filter((t): t is string => typeof t === 'string' && t.trim().length > 0)
    .sort((a, b) => b.length - a.length)[0];
  const asOf = started?.date ?? tradeDate ?? '';
  const displaySymbol = started?.symbol ?? symbol.trim().toUpperCase();

  // Analysts share the quick tier's catalog.
  const quickChoices = React.useMemo(
    () => modelChoices(modelOptions, 'quick'),
    [modelOptions],
  );

  /** What a role runs on unless overridden, provider-qualified when mixed. */
  const roleDefault = (role: string): string =>
    specLabel(modelOptions?.defaults.llm_roles?.[role], modelOptions?.provider);

  // Number of models explicitly chosen for the next run (badge on the toggle).
  const pinnedCount =
    (quickModel.trim() ? 1 : 0) +
    (deepModel.trim() ? 1 : 0) +
    Object.values(analystModels).filter((m) => m.trim()).length;

  // Everything the run used besides the manager model shown on the card.
  const analystModelSummary = [
    ...(ranModels?.quick ? [`Analysts: ${ranModels.quick}`] : []),
    ...Object.entries(ranModels?.analysts ?? {}).map(
      ([key, model]) => `${AGENT_META[key]?.label ?? key}: ${model}`,
    ),
  ].join(' · ');

  /**
   * How the connector reads in the header. Amber for a live connection —
   * `success` green is reserved for market direction (see the design system's
   * palette.market rule), so a healthy connector borrows the brand accent.
   */
  const tcbsChip: { label: string; color: 'primary' | 'warning' | 'default'; tooltip: string } =
    !tcbs?.connected
      ? {
          label: 'TCBS not connected',
          color: 'default',
          tooltip:
            'Fundamentals, statements, news and insider dealing are served by secondary sources.',
        }
      : tcbs.expired
        ? {
            label: 'TCBS expired',
            color: 'warning',
            tooltip: tcbs.expires_at
              ? `The connection lapsed ${formatWhen(tcbs.expires_at)}. Reconnect to restore first-party data.`
              : 'The connection has lapsed. Reconnect to restore first-party data.',
          }
        : {
            label: 'TCBS connected',
            color: 'primary',
            tooltip: tcbs.expires_at
              ? `First-party TCBS data is live. Token valid until ${formatWhen(tcbs.expires_at)}; it renews itself.`
              : 'First-party TCBS data is live.',
          };

  const statCards = [
    { label: 'Recommendation', value: decision?.signal ?? (running ? '…' : '—'), accent: signalHex(decision?.signal) },
    { label: 'As of', value: asOf || '—' },
    {
      label: 'Model',
      // Prefer what the run reported over the page-load default.
      value: ranModels?.deep || health?.deep_think_llm || health?.provider || '—',
      hint: analystModelSummary,
    },
    { label: 'Duration', value: elapsed || (running ? '…' : '—') },
  ];

  return (
    <PageContainer>
      <>
        <PageHeader
          title="Trading Agents"
          description="Multi-agent analysis over Vietnamese-market data — analysts debate, a trader acts, a risk team checks, and a portfolio manager decides."
          actions={
            <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
              {health && (
                <Tooltip title={health.message}>
                  <Chip
                    size="small"
                    label={
                      health.ollama_reachable
                        ? `${(health.providers ?? [health.provider]).join(' + ')} · ${
                            health.deep_think_llm
                          }`
                        : `${(health.providers ?? [health.provider]).join(' + ')} not ready`
                    }
                    color={health.ollama_reachable ? 'success' : 'error'}
                    variant="outlined"
                  />
                </Tooltip>
              )}
              {/* TCBS reach, in all three states — the banner below only appears
                  when something needs doing, and "it is working" is worth
                  seeing too. Amber, not green: green means market direction. */}
              {tcbs && (
                <Tooltip title={tcbsChip.tooltip}>
                  <Chip
                    size="small"
                    icon={<LinkIcon />}
                    label={tcbsChip.label}
                    color={tcbsChip.color}
                    variant="outlined"
                  />
                </Tooltip>
              )}
            </Stack>
          }
        />

        {/* TCBS connector. Rendered only when it needs attention: the tier
            degrades quietly to the other sources, so a working connection is
            not worth a line of chrome on every visit. */}
        {tcbs && (!tcbs.connected || tcbs.expired) && (
          <Alert
            severity="warning"
            sx={{ mb: 3 }}
            action={
              <Button
                color="inherit"
                size="small"
                variant="outlined"
                startIcon={connecting ? <CircularProgress size={14} /> : <LinkIcon />}
                onClick={handleConnectTcbs}
                disabled={connecting}
              >
                {connecting ? 'Opening TCBS…' : 'Connect TCBS'}
              </Button>
            }
          >
            {tcbs.expired
              ? 'The TCBS connection has expired. Fundamentals, statements, company news and insider dealing are falling back to secondary sources until you reconnect.'
              : 'TCBS is not connected. Connect it for first-party fundamentals, statements with industry averages, corporate events and insider dealing.'}
          </Alert>
        )}

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
            <Box sx={{ flexGrow: 1 }} />
            <Link
              component="button"
              type="button"
              variant="body2"
              underline="hover"
              onClick={() => setShowModels((v) => !v)}
              sx={{ whiteSpace: 'nowrap' }}
            >
              {showModels ? 'Hide models' : `Models${pinnedCount ? ` (${pinnedCount})` : ''}`}
            </Link>
          </Stack>

          {/* Per-run model selection. Blank = the backend's configured default. */}
          <Collapse in={showModels}>
            <Divider sx={{ my: 2 }} />
            <Typography variant="caption" color="text.secondary">
              Models for this run — leave blank to use the server default. Roles may
              sit on different providers: pick a <code>provider:model</code> entry, or
              type any model ID (a bare name goes to
              {modelOptions ? ` ${modelOptions.provider}` : ' the default provider'}).
              Only providers with an API key configured are listed.
            </Typography>
            <Stack
              direction={{ xs: 'column', md: 'row' }}
              spacing={2}
              sx={{ mt: 1.5, flexWrap: 'wrap' }}
            >
              <ModelPicker
                label="Analysts (default)"
                placeholder={roleDefault('quick')}
                value={quickModel}
                onChange={setQuickModel}
                choices={quickChoices}
                disabled={running}
              />
              <ModelPicker
                label="Managers (deep)"
                placeholder={roleDefault('deep')}
                value={deepModel}
                onChange={setDeepModel}
                choices={modelChoices(modelOptions, 'deep')}
                disabled={running}
              />
              {MODEL_PICKABLE_ANALYSTS.map((key) => (
                <ModelPicker
                  key={key}
                  label={AGENT_META[key]?.label ?? key}
                  // Falls back through: env pin for this analyst → whatever the
                  // user picked for analysts → the server's quick default.
                  placeholder={roleDefault(key) || quickModel || roleDefault('quick')}
                  value={analystModels[key] ?? ''}
                  onChange={(model) =>
                    setAnalystModels((prev) => ({ ...prev, [key]: model }))
                  }
                  choices={quickChoices}
                  disabled={running}
                />
              ))}
            </Stack>
          </Collapse>
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
          <Panel>
            <EmptyState
              title="Convene the council"
              description="Enter a symbol (e.g. FPT) and run an analysis to watch the agents work through their pipeline and reach a decision."
            />
          </Panel>
        )}

        {hasRun && (
          <>
            {/* Hero card */}
            <Paper
              elevation={0}
              sx={{
                p: 3,
                mb: 3,
                position: 'relative',
                overflow: 'hidden',
                bgcolor: 'surface.raised',
                borderColor: 'line.default',
                // Signal-coloured spine: the decision is the headline of this card.
                '&::before': {
                  content: '\'\'',
                  position: 'absolute',
                  insetBlock: 0,
                  left: 0,
                  width: 3,
                  bgcolor: signalHex(decision?.signal),
                },
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
                      color: 'surface.canvas',
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
                      bgcolor: 'surface.inset',
                      border: 1,
                      borderColor: 'line.subtle',
                    }}
                  >
                    <Typography variant="caption" sx={{ opacity: 0.7, textTransform: 'uppercase' }}>
                      {s.label}
                    </Typography>
                    <Typography
                      variant="h6"
                      sx={{ fontWeight: 700, color: s.accent ?? 'text.primary', wordBreak: 'break-word' }}
                    >
                      {s.value}
                    </Typography>
                    {/* Only set when analysts ran on their own models. */}
                    {s.hint && (
                      <Typography variant="caption" sx={{ opacity: 0.7, wordBreak: 'break-word' }}>
                        {s.hint}
                      </Typography>
                    )}
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

            {/* Final decision (conclusion).
                `sections.final` streams in during a live run; `decision.full` is
                the stored final_trade_decision returned by get_analysis. Both come
                from the same upstream key, so prefer whichever is present and
                render the card if either is — a saved row whose sections lack
                'final' still has the decision, and dropping it left the panel
                blank. */}
            {finalDecisionText !== undefined && (
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
                  <Markdown>{finalDecisionText}</Markdown>
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
      </>
    </PageContainer>
  );
};

export default TradingAgents;
