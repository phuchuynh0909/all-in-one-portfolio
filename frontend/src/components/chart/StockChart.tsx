import { useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Box, Button, Checkbox, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, TextField, Typography } from '@mui/material';

import { listChartLayouts, saveChartLayout, selectChartLayout, importChartLayouts, renameChartLayout, deleteChartLayout } from '../../lib/services/chartLayouts';

import { listOpenScripts, saveOpenScript, deleteOpenScript, updateOpenScriptVisibility, importOpenScripts } from '../../lib/services/openscripts';

import { createTvWidget, LIBRARY_PATH } from '../../lib/tv';
import type {
  DropdownParams,
  EntityId,
  IChartingLibraryWidget,
  IDropdownApi,
  IExecutionLineAdapter,
  LanguageCode,
  ResolutionString,
  StudyInputId,
} from '../../lib/tv';
import { createDatafeed } from '../../lib/tv/datafeed';
import { tvOverrides, tvSideColor } from '../../lib/tv/theme';
import { useColorMode } from '../../theme';
import { tvStore, HISTORY_YEARS } from '../../lib/tv/store';
import {
  resolveWatchListApi,
  type ResolvedWatchList,
} from '../../lib/tv/watchlist';
import {
  STUDY_CATALOGUE,
  STUDY_NAME_BY_ID,
  computedStudyInputs,
  customIndicatorsGetter,
} from '../../lib/tv/studies';
import { formatChartTime, type IndicatorParams } from '../../lib/services/timeseries';
import {
  getPositions,
  getTransactions,
  type Position,
  type Transaction,
} from '../../lib/services/portfolio';

import { readCustomLayouts, type ChartLayout } from '../../lib/tv/chartLayouts';
export type { ChartLayout } from '../../lib/tv/chartLayouts';

import IndicatorManager from './IndicatorManager';
import ScriptEditor from './ScriptEditor';
import { buildOpenScriptStudy } from '../../lib/tv/openscript';
import { validateUserScript } from '../../lib/tv/scriptCompiler';
import { readUserScripts, userScriptSpec, type UserScript } from '../../lib/tv/userScripts';

type StockChartProps = {
  symbol: string;
  /** Called when the user submits a new symbol from the in-header picker. */
  onSymbolChange?: (symbol: string) => void;
  height?: number;
  /** Retained for API compatibility; large-order bubbles are not yet ported. */
  showLargeOrders?: boolean;
  onToggleLargeOrders?: (value: boolean) => void;
  /** Called when the in-header sync button is pressed. */
  onSync?: () => void;
  syncing?: boolean;
  /**
   * Called once the Watch List API is resolved — the library's own widget when
   * this is a Trading Terminal build, the app-side list otherwise.
   */
  onWatchListResolved?: (resolved: ResolvedWatchList) => void;
};

type ExecutionMark = {
  time: number;
  price: number;
  quantity: number;
  side: 'buy' | 'sell';
};

type ExecutionSummary = {
  quantity: number;
  value: number;
};

/**
 * Charts use daily bars, so one execution shape per day keeps all activity on
 * that bar readable. Its direction and price represent the larger side; the
 * tooltip preserves both sides' daily totals.
 */
function consolidateExecutionMarks(marks: ExecutionMark[]) {
  const days = new Map<number, Record<'buy' | 'sell', ExecutionSummary>>();

  for (const mark of marks) {
    const day = days.get(mark.time) ?? {
      buy: { quantity: 0, value: 0 },
      sell: { quantity: 0, value: 0 },
    };
    const summary = day[mark.side];
    summary.quantity += mark.quantity;
    summary.value += mark.price * mark.quantity;
    days.set(mark.time, day);
  }

  return [...days].map(([time, { buy, sell }]) => {
    const side: 'buy' | 'sell' = sell.quantity > buy.quantity ? 'sell' : 'buy';
    const summary = side === 'buy' ? buy : sell;
    const averagePrice = summary.quantity > 0 ? summary.value / summary.quantity : 0;
    const tooltip = [
      buy.quantity > 0 && `Mua ${buy.quantity.toFixed(0)} \n CP @ ${(buy.value / buy.quantity).toFixed(2)}`,
      sell.quantity > 0 && `Ban ${sell.quantity.toFixed(0)} \n CP @ ${(sell.value / sell.quantity).toFixed(2)}`,
    ].filter(Boolean).join('\n');

    return { time, price: averagePrice, side, tooltip };
  });
}

export interface ParamDef {
  key: string;
  label: string;
  min: number;
  max: number;
  step: number;
}

export interface IndicatorConfig {
  id: string;
  name: string;
  label: string;
  params: Record<string, number>;
  visible: boolean;
  paramDefs: ParamDef[];
  /**
   * True when the study computes itself in the browser (PineJS or OpenScript).
   * Its params become study inputs and it is omitted from backend requests.
   */
  computed?: boolean;
}

const DEFAULT_INDICATOR_CONFIGS: IndicatorConfig[] = [
  {
    // Computed in-browser by PineJS (see COMPUTED_STUDY_SPECS) — the params below
    // become study inputs instead of a backend indicator request.
    id: 'rsi', name: 'rsi', label: 'RSI', computed: true,
    params: { period: 14, fast_period: 5 }, visible: true,
    paramDefs: [
      { key: 'period', label: 'Period', min: 2, max: 200, step: 1 },
      { key: 'fast_period', label: 'Fast Period', min: 2, max: 200, step: 1 },
    ],
  },
  {
    id: 'rvol', name: 'rvol', label: 'RVOL', computed: true,
    params: { period: 20 }, visible: true,
    paramDefs: [
      { key: 'period', label: 'Period', min: 2, max: 200, step: 1 },
    ],
  },
  {
    id: 'atr_trailing', name: 'atr_trailing', label: 'ATR Trailing Stop',
    computed: true, params: { timeperiod: 10, multiplier: 1.8 }, visible: true,
    paramDefs: [
      { key: 'timeperiod', label: 'ATR Period',   min: 2,   max: 100, step: 1   },
      { key: 'multiplier', label: 'Multiplier',   min: 0.5, max: 10,  step: 0.1 },
    ],
  },
  {
    id: 'vwap', name: 'vwap', label: 'VWAP',
    computed: true, params: { window: 200 }, visible: true,
    paramDefs: [{ key: 'window', label: 'Window', min: 10, max: 1000, step: 10 }],
  },
  {
    id: 'kama', name: 'kama', label: 'KAMA', computed: true,
    params: { timeperiod: 10 }, visible: true,
    paramDefs: [{ key: 'timeperiod', label: 'Period', min: 2, max: 200, step: 1 }],
  },
  {
    id: 'linreg_channel', name: 'linreg_channel', label: 'LR Prediction Channel', computed: true,
    params: { reg_window: 50, confidence: 0.85 }, visible: false,
    paramDefs: [
      { key: 'reg_window', label: 'Reg Window', min: 10, max: 200,  step: 5    },
      { key: 'confidence', label: 'Confidence', min: 0.5, max: 0.99, step: 0.01 },
    ],
  },
  {
    id: 'bvc', name: 'bvc', label: 'BVC',
    computed: true, params: { window: 20, kappa: 0.1 }, visible: true,
    paramDefs: [
      { key: 'window', label: 'Window', min: 5, max: 200, step: 1 },
      { key: 'kappa', label: 'Kappa', min: 0.01, max: 1, step: 0.01 },
    ],
  },
  {
    id: 'kalman_zscore', name: 'kalman_zscore', label: 'Kalman Z-Score',
    computed: true, params: { window: 20 }, visible: true,
    paramDefs: [{ key: 'window', label: 'Window', min: 5, max: 200, step: 1 }],
  },
  {
    id: 'yz_volatility', name: 'yz_volatility', label: 'YZ Volatility',
    computed: true, params: { window: 30, periods: 252 }, visible: true,
    paramDefs: [
      { key: 'window', label: 'Window', min: 5, max: 200, step: 1 },
      { key: 'periods', label: 'Annual Periods', min: 52, max: 365, step: 1 },
    ],
  },
  {
    id: 'gkyz_volatility', name: 'gkyz_volatility', label: 'GKYZ Volatility',
    computed: true, params: { window: 21 }, visible: true,
    paramDefs: [
      { key: 'window', label: 'Window', min: 5, max: 200, step: 1 },
    ],
  },
  {
    id: 'matrix_series', name: 'matrix_series', label: 'Matrix Series',
    computed: true, params: { price_period: 20, sup_res_period: 50, sup_res_percentage: 100, smoother: 5 },
    visible: true,
    paramDefs: [
      { key: 'price_period', label: 'Price Period', min: 5, max: 200, step: 1 },
      { key: 'sup_res_period', label: 'S/R Period', min: 10, max: 500, step: 5 },
      { key: 'sup_res_percentage', label: 'S/R %', min: 10, max: 500, step: 10 },
      { key: 'smoother', label: 'Smoother', min: 1, max: 50, step: 1 },
    ],
  },
  {
    id: 'williams_vix_fix', name: 'williams_vix_fix', label: 'Williams VIX Fix',
    computed: true, params: {}, visible: true, paramDefs: [],
  },
  {
    id: 'squeeze_ttm', name: 'squeeze_ttm', label: 'Squeeze TTM',
    computed: true, params: {}, visible: true, paramDefs: [],
  },
  {
    id: 'chandelier_exit', name: 'chandelier_exit', label: 'Chandelier Exit', computed: true,
    params: { length: 22, multiplier: 3 }, visible: true,
    paramDefs: [
      { key: 'length',     label: 'ATR Length',     min: 1, max: 100, step: 1   },
      { key: 'multiplier', label: 'ATR Multiplier', min: 0, max: 10,  step: 0.1 },
    ],
  },
  {
    id: 'gaussian_frama', name: 'gaussian_frama', label: 'Gaussian FRAMA',
    computed: true, params: {
      gaussian_length: 4, sigma: 2.0, fm_len: 20, upper_limit: 8, lower_limit: 40,
      atr_period: 14, atr_mult: 1.9,
    },
    visible: true,
    paramDefs: [
      { key: 'gaussian_length', label: 'Gaussian Length', min: 2,  max: 20,  step: 1   },
      { key: 'sigma',           label: 'Sigma',           min: 0.5, max: 5,   step: 0.1 },
      { key: 'fm_len',          label: 'FRAMA Length',    min: 5,  max: 60,  step: 1   },
      { key: 'upper_limit',     label: 'Upper Limit',     min: 2,  max: 30,  step: 1   },
      { key: 'lower_limit',     label: 'Lower Limit',     min: 10, max: 100, step: 1   },
      { key: 'atr_period',      label: 'ATR Period',      min: 2,  max: 50,  step: 1   },
      { key: 'atr_mult',        label: 'ATR Mult',        min: 0.5, max: 5,  step: 0.1 },
    ],
  },
  {
    id: 'hull_butterfly', name: 'hull_butterfly', label: 'Hull Butterfly Oscillator',
    computed: true, params: { length: 14, mult: 2.0 }, visible: true,
    paramDefs: [
      { key: 'length', label: 'Length', min: 4,   max: 50, step: 1   },
      { key: 'mult',   label: 'Mult',   min: 0.5, max: 5,  step: 0.1 },
    ],
  },
  {
    id: 'iakf_oscillator',
    name: 'iakf_oscillator',
    label: 'IAKF Oscillator',
    computed: true,
    params: {
      smoothing_period: 20,
      noise_lookback: 20,
      anomaly_multiple: 2.05,
      outlier_rejection: 0.05,
    },
    visible: false,
    paramDefs: [
      { key: 'smoothing_period', label: 'Trend Smoothness', min: 2, max: 200, step: 1 },
      { key: 'noise_lookback', label: 'Noise Lookback', min: 2, max: 200, step: 1 },
      { key: 'anomaly_multiple', label: 'Unusual-Move Threshold', min: 1, max: 10, step: 0.05 },
      { key: 'outlier_rejection', label: 'Damping Strength', min: 0, max: 1, step: 0.05 },
    ],
  },
  {
    id: 'smart_money_flow', name: 'smart_money_flow', label: 'SMF Cloud',
    computed: true, params: {
      trend_len: 34, basis_type: 1, alma_offset: 0.85, alma_sigma: 6.0, basis_smooth: 3,
      mf_len: 24, mf_smooth: 5, mf_power: 1.2, atr_len: 14, min_mult: 0.9, max_mult: 2.2,
    },
    visible: true,
    paramDefs: [
      { key: 'trend_len',  label: 'Trend Length', min: 5,    max: 200,  step: 1    },
      { key: 'mf_len',     label: 'MF Length',    min: 5,    max: 200,  step: 1    },
      { key: 'mf_power',   label: 'MF Power',     min: 0.1,  max: 5,    step: 0.1  },
      { key: 'atr_len',    label: 'ATR Length',   min: 2,    max: 100,  step: 1    },
      { key: 'min_mult',   label: 'Min Mult',     min: 0.1,  max: 5,    step: 0.1  },
      { key: 'max_mult',   label: 'Max Mult',     min: 0.5,  max: 10,   step: 0.1  },
    ],
  },
];

/** Indicators that live in the secondary layout. */
const LAYOUT_2_IDS = ['rsi', 'bvc', 'kalman_zscore', 'yz_volatility'];

/** Indicators for the backtest_012 strategy, breakout volume, and IAKF. */
const LAYOUT_3_IDS = ['gaussian_frama', 'hull_butterfly', 'rvol', 'iakf_oscillator'];


/**
 * Selectable indicator layouts. Layout 1 excludes LR Prediction Channel and
 * every indicator reserved for another layout. Layout 2 holds BVC + RSI +
 * Kalman Z-Score + YZ Volatility; Layout 3 holds Gaussian FRAMA + Hull
 * Butterfly Oscillator + RVOL + IAKF.
 */
const CHART_LAYOUTS: ChartLayout[] = [
  {
    id: 'layout1',
    name: 'Layout 1',
    indicators: DEFAULT_INDICATOR_CONFIGS.map((c) => c.id).filter(
      (id) => id !== 'linreg_channel'
        && !LAYOUT_2_IDS.includes(id)
        && !LAYOUT_3_IDS.includes(id),
    ),
  },
  {
    id: 'layout2',
    name: 'Layout 2',
    indicators: LAYOUT_2_IDS,
  },
  {
    id: 'layout3',
    name: 'Layout 3',
    indicators: LAYOUT_3_IDS,
  },
];

const DAILY = '1D' as ResolutionString;

/**
 * Maps app indicator configs → the datafeed's indicator request payload.
 * In-browser (computed) studies are skipped: the backend has nothing to compute
 * for them, so requesting them would only inflate the response.
 */
function toIndicatorParams(configs: IndicatorConfig[]): IndicatorParams[] {
  return configs
    .filter((config) => STUDY_CATALOGUE.some((study) => study.id === config.id && !study.computed))
    .map((config) => ({
      name: config.name,
      ...(Object.keys(config.params).length > 0 ? { params: config.params } : {}),
    }));
}

export default function StockChart({
  symbol,
  onSymbolChange,
  height,
  showLargeOrders = false,
  onToggleLargeOrders,
  onSync,
  syncing = false,
  onWatchListResolved,
}: StockChartProps) {
  const { mode } = useColorMode();
  const modeRef = useRef(mode);
  useEffect(() => {
    modeRef.current = mode;
  }, [mode]);
  const containerRef = useRef<HTMLDivElement>(null);
  const widgetRef = useRef<IChartingLibraryWidget | null>(null);
  const studyEntitiesRef = useRef<Map<string, EntityId>>(new Map());
  const executionShapesRef = useRef<IExecutionLineAdapter[]>([]);
  const [ready, setReady] = useState(false);
  const readyRef = useRef(ready);
  readyRef.current = ready;

  const [serverLayouts, setServerLayouts] = useState<ChartLayout[]>([]);
  const layouts = useMemo(() => [
    ...CHART_LAYOUTS.map((preset) => serverLayouts.find((layout) => layout.id === preset.id) ?? preset),
    ...serverLayouts.filter((layout) => layout.id.startsWith('custom_')),
  ], [serverLayouts]);
  const [activeLayoutId, setActiveLayoutId] = useState(CHART_LAYOUTS[0].id);
  const activeLayout = layouts.find((layout) => layout.id === activeLayoutId) ?? CHART_LAYOUTS[0];
  const activeLayoutRef = useRef(activeLayout);
  activeLayoutRef.current = activeLayout;
  const [legacyLayouts, setLegacyLayouts] = useState<ChartLayout[]>(() => {
    try {
      const old = readCustomLayouts(localStorage);
      const selected = localStorage.getItem('chartLayoutId');
      const preset = CHART_LAYOUTS.find((layout) => layout.id === selected);
      const configs = JSON.parse(localStorage.getItem('indicatorConfigs') ?? '[]');
      if (preset && Array.isArray(configs) && configs.length) {
        old.push({
          ...preset, id: `custom_browser_${preset.id}`, name: `Imported ${preset.name}`,
          indicators: configs.filter((config) => config?.visible && typeof config.id === 'string').map((config) => config.id),
          params: Object.fromEntries(configs.filter((config) => config?.params && typeof config.id === 'string').map((config) => [config.id, config.params])),
          scripts: readUserScripts(localStorage).filter((script) => script.visible).map((script) => script.id),
        });
      }
      return old;
    } catch { return []; }
  });
  const [importingLayouts, setImportingLayouts] = useState(false);
  const [layoutSaveStatus, setLayoutSaveStatus] = useState('');
  const layoutSaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const layoutSaveQueueRef = useRef<Promise<unknown>>(Promise.resolve());
  const scheduleLayoutSaveRef = useRef<() => void>(() => {});
  const skipRestoredInputsRef = useRef(false);
  const switchingLayoutRef = useRef(false);
  const [layoutDialog, setLayoutDialog] = useState<{ kind: 'create' } | { kind: 'rename' | 'delete'; layout: ChartLayout } | null>(null);
  const [layoutName, setLayoutName] = useState('');
  const [emptyLayout, setEmptyLayout] = useState(false);
  const [changingLayout, setChangingLayout] = useState(false);
  const [layoutNameError, setLayoutNameError] = useState('');
  const [layoutError, setLayoutError] = useState('');
  const layoutDropdownRef = useRef<IDropdownApi | null>(null);

  const [scriptEditorOpen, setScriptEditorOpen] = useState(false);
  const [scriptError, setScriptError] = useState('');
  const [userScripts, setUserScripts] = useState<UserScript[]>([]);
  const [scriptsLoaded, setScriptsLoaded] = useState(false);
  const [scriptsLoading, setScriptsLoading] = useState(true);
  const [scriptLoadAttempt, setScriptLoadAttempt] = useState(0);
  const [legacyScripts, setLegacyScripts] = useState<UserScript[]>(() => {
    try { return readUserScripts(localStorage); } catch { return []; }
  });
  const [importingScripts, setImportingScripts] = useState(false);
  const userScriptsRef = useRef(userScripts);
  userScriptsRef.current = userScripts;
  const replaceScripts = (scripts: UserScript[]) => {
    userScriptsRef.current = scripts;
    setUserScripts(scripts);
  };

  useEffect(() => {
    let cancelled = false;
    setScriptsLoading(true);
    void Promise.all([listOpenScripts(), listChartLayouts()]).then(async ([scripts, library]) => {
      if (cancelled) return;
      if (!library.layouts.length) {
        const defaults = Object.fromEntries(DEFAULT_INDICATOR_CONFIGS.map((config) => [config.id, config.params]));
        library = await importChartLayouts(CHART_LAYOUTS.map((preset) => ({
          ...preset, params: defaults,
          scripts: preset.id === CHART_LAYOUTS[0].id ? scripts.filter((script) => script.visible).map((script) => script.id) : [],
        })), CHART_LAYOUTS[0].id);
      }
      if (cancelled) return;
      const layout = library.layouts.find((item) => item.id === library.activeLayoutId) ?? library.layouts[0];
      setServerLayouts(library.layouts);
      setActiveLayoutId(layout.id);
      activeLayoutRef.current = layout;
      savedLayoutRef.current = layout.chartState;
      setIndicatorConfigs(DEFAULT_INDICATOR_CONFIGS.map((config) => ({
        ...config, params: { ...config.params, ...layout.params?.[config.id] }, visible: layout.indicators.includes(config.id),
      })));
      replaceScripts(scripts.map((script) => ({ ...script, visible: (layout.scripts ?? []).includes(script.id) })));
      const serverIds = new Set(scripts.map((script) => script.id));
      setLegacyScripts((legacy) => legacy.filter((script) => !serverIds.has(script.id)));
      const layoutIds = new Set(library.layouts.map((item) => item.id));
      setLegacyLayouts((legacy) => legacy.filter((item) => !layoutIds.has(item.id)));
      setScriptError('');
      setLayoutSaveStatus('Layout saved to server');
      setScriptsLoaded(true);
    }).catch((error) => {
      if (!cancelled) setScriptError(`Could not load your server chart setup: ${error instanceof Error ? error.message : String(error)}`);
    }).finally(() => { if (!cancelled) setScriptsLoading(false); });
    return () => { cancelled = true; };
    // Load once before constructing the chart so every saved definition is registered.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scriptLoadAttempt]);

  const scriptEntitiesRef = useRef(new Map<string, EntityId>());
  const savedLayoutRef = useRef<object | undefined>(undefined);
  const restoredEntitiesRef = useRef<{ studies: Map<string, EntityId>; scripts: Map<string, EntityId> } | null>(null);
  const [scriptRevision, setScriptRevision] = useState(0);

  const persistScripts = async (scripts: UserScript[]) => {
    const changes = scripts.filter((script) => userScriptsRef.current.find((current) => current.id === script.id)?.visible !== script.visible)
      .map(({ id, visible }) => ({ id, visible }));
    if (changes.length) await updateOpenScriptVisibility(changes);
    // Merge visibility only so a delayed layout request cannot overwrite source edits.
    const visibleById = new Map(changes.map((change) => [change.id, change.visible]));
    replaceScripts(userScriptsRef.current.map((script) => visibleById.has(script.id) ? { ...script, visible: visibleById.get(script.id)! } : script));
  };

  const importBrowserScripts = async () => {
    setImportingScripts(true);
    try {
      const scripts = await importOpenScripts(legacyScripts);
      replaceScripts(scripts);
      setLegacyScripts([]);
      // Existing local copies are retained as a recovery backup, never used for new saves.
      setScriptError('');
      if (ready && widgetRef.current) {
        savedLayoutRef.current = await new Promise<object>((resolve) => widgetRef.current!.save(resolve));
        restoredEntitiesRef.current = { studies: new Map(studyEntitiesRef.current), scripts: new Map(scriptEntitiesRef.current) };
      }
      setReady(false);
      setScriptRevision((revision) => revision + 1);
    } catch (error) {
      setScriptError(`Could not import browser scripts: ${error instanceof Error ? error.message : String(error)}`);
    } finally { setImportingScripts(false); }
  };

  const saveScript = async (id: string, source: string, add: boolean) => {
    const { title } = validateUserScript(source, id);
    const previous = userScriptsRef.current.find((item) => item.id === id);
    const next: UserScript = {
      id, source, title, visible: add || (previous?.visible ?? false),
      appliedSource: add ? source : previous?.appliedSource,
    };
    const widget = widgetRef.current;
    if (add && (!ready || !widget)) throw new Error('Wait for the chart to finish loading.');
    // Only update the running chart after the server confirms the source is saved.
    const saved = await saveOpenScript(next);
    replaceScripts([...userScriptsRef.current.filter((item) => item.id !== id), saved]);
    setScriptError('');
    if (!add || !widget) return;
    const oldEntity = scriptEntitiesRef.current.get(id);
    // Remove the old definition from the snapshot; the new study can have a
    // different plot/input schema. All other studies and drawings are restored.
    if (oldEntity) {
      scriptEntitiesRef.current.delete(id);
      widget.activeChart().removeEntity(oldEntity);
    }
    savedLayoutRef.current = await new Promise<object>((resolve) => widget.save(resolve));
    restoredEntitiesRef.current = {
      studies: new Map(studyEntitiesRef.current), scripts: new Map(scriptEntitiesRef.current),
    };
    setReady(false);
    setScriptRevision((revision) => revision + 1);
  };

  const removeScript = async (id: string, deleteScript: boolean) => {
    const scripts = deleteScript ? userScriptsRef.current.filter((item) => item.id !== id)
      : userScriptsRef.current.map((item) => item.id === id ? { ...item, visible: false } : item);
    if (deleteScript) {
      await deleteOpenScript(id);
      replaceScripts(userScriptsRef.current.filter((item) => item.id !== id));
    } else await persistScripts(scripts);
    const entity = scriptEntitiesRef.current.get(id);
    scriptEntitiesRef.current.delete(id);
    if (entity) widgetRef.current?.activeChart().removeEntity(entity);
    setScriptError('');
  };

  // Header DOM controls (created imperatively in the widget header) + latest
  // handler snapshot so their event listeners always call current props.
  const viewDropdownRef = useRef<IDropdownApi | null>(null);
  const syncBtnElRef = useRef<HTMLElement | null>(null);
  const headerHandlersRef = useRef({ onSymbolChange, onToggleLargeOrders, onSync });
  useEffect(() => {
    headerHandlersRef.current = { onSymbolChange, onToggleLargeOrders, onSync };
  }, [onSymbolChange, onToggleLargeOrders, onSync]);

  // Resolved once on mount (below), so read through a ref like the header ones.
  const onWatchListResolvedRef = useRef(onWatchListResolved);
  useEffect(() => { onWatchListResolvedRef.current = onWatchListResolved; }, [onWatchListResolved]);

  const [indicatorConfigs, setIndicatorConfigs] = useState<IndicatorConfig[]>(DEFAULT_INDICATOR_CONFIGS);
  const indicatorConfigsRef = useRef(indicatorConfigs);
  indicatorConfigsRef.current = indicatorConfigs;
  const pendingStudiesRef = useRef(new Set<string>());
  const [indicatorPanelOpen, setIndicatorPanelOpen] = useState(false);

  const replaceLayout = (layout: ChartLayout) => {
    setServerLayouts((previous) => [...previous.filter((item) => item.id !== layout.id), layout]);
    if (activeLayoutRef.current.id === layout.id) activeLayoutRef.current = layout;
  };

  const captureLayout = async (): Promise<ChartLayout> => {
    const widget = widgetRef.current;
    if (!widget || !readyRef.current) throw new Error('Wait for the chart to finish loading.');
    const layout = activeLayoutRef.current;
    // Wait for indicator calculations/creation before taking the snapshot.
    const deadline = Date.now() + 15_000;
    while (pendingStudiesRef.current.size || pendingScriptsRef.current.size) {
      if (Date.now() > deadline || widgetRef.current !== widget) throw new Error('Indicators are still loading. Try saving again.');
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    const chartState = await new Promise<object>((resolve) => widget.save(resolve));
    if (widgetRef.current !== widget || !readyRef.current) throw new Error('The chart changed before it could be saved. Try again.');
    return {
      ...layout,
      indicators: indicatorConfigsRef.current.filter((config) => config.visible).map((config) => config.id),
      params: Object.fromEntries(indicatorConfigsRef.current.map((config) => [config.id, config.params])),
      scripts: userScriptsRef.current.filter((script) => script.visible).map((script) => script.id),
      chartState,
    };
  };

  const saveCurrentLayout = async () => {
    if (layoutSaveTimerRef.current) clearTimeout(layoutSaveTimerRef.current);
    setLayoutSaveStatus('Saving layout…');
    const operation = layoutSaveQueueRef.current.catch(() => {}).then(async () => {
      const snapshot = await captureLayout();
      const saved = await saveChartLayout(snapshot);
      replaceLayout(saved);
      setLayoutError('');
      setLayoutSaveStatus('Layout saved to server');
      return saved;
    });
    layoutSaveQueueRef.current = operation;
    try { return await operation; }
    catch (error) {
      setLayoutSaveStatus('Layout not saved');
      setLayoutError(`Could not save layout: ${error instanceof Error ? error.message : String(error)}`);
      throw error;
    }
  };

  const scheduleLayoutSave = () => {
    if (layoutSaveTimerRef.current) clearTimeout(layoutSaveTimerRef.current);
    if (!ready || switchingLayoutRef.current) return;
    setLayoutSaveStatus('Unsaved layout changes');
    layoutSaveTimerRef.current = setTimeout(() => { void saveCurrentLayout().catch(() => {}); }, 700);
  };
  scheduleLayoutSaveRef.current = scheduleLayoutSave;

  const restoreLayout = (layout: ChartLayout) => {
    if (layoutSaveTimerRef.current) clearTimeout(layoutSaveTimerRef.current);
    activeLayoutRef.current = layout;
    savedLayoutRef.current = layout.chartState;
    restoredEntitiesRef.current = null;
    replaceLayout(layout);
    replaceScripts(userScriptsRef.current.map((script) => ({ ...script, visible: (layout.scripts ?? []).includes(script.id) })));
    setIndicatorConfigs(DEFAULT_INDICATOR_CONFIGS.map((config) => ({
      ...config, params: { ...config.params, ...layout.params?.[config.id] }, visible: layout.indicators.includes(config.id),
    })));
    setActiveLayoutId(layout.id);
    setReady(false);
    setScriptRevision((revision) => revision + 1);
  };

  const applyLayout = async (layoutId: string) => {
    if (!ready || switchingLayoutRef.current || layoutId === activeLayoutRef.current.id) return;
    switchingLayoutRef.current = true;
    try {
      await saveCurrentLayout();
      // Read the latest server version and persist selection in the same transaction.
      const layout = await selectChartLayout(layoutId);
      restoreLayout(layout);
    } catch (error) {
      setLayoutError(`Could not switch layouts: ${error instanceof Error ? error.message : String(error)}`);
    } finally { switchingLayoutRef.current = false; }
  };

  const importBrowserLayouts = async () => {
    if (!ready || switchingLayoutRef.current) return;
    switchingLayoutRef.current = true;
    setImportingLayouts(true);
    try {
      await saveCurrentLayout();
      let selected = legacyLayouts[0]?.id ?? activeLayoutRef.current.id;
      try {
        const oldSelected = localStorage.getItem('chartLayoutId');
        if (legacyLayouts.some((layout) => layout.id === oldSelected)) selected = oldSelected!;
        else if (legacyLayouts.some((layout) => layout.id === `custom_browser_${oldSelected}`)) selected = `custom_browser_${oldSelected}`;
      } catch { /* Import the first available layout when browser storage is blocked. */ }
      const library = await importChartLayouts(legacyLayouts, selected);
      setServerLayouts(library.layouts);
      setLegacyLayouts([]);
      const layout = library.layouts.find((item) => item.id === library.activeLayoutId);
      if (layout) restoreLayout(layout);
    } catch (error) {
      setLayoutError(`Could not import browser layouts: ${error instanceof Error ? error.message : String(error)}`);
    } finally { setImportingLayouts(false); switchingLayoutRef.current = false; }
  };

  const createLayout = async () => {
    if (changingLayout || switchingLayoutRef.current) return;
    const name = layoutName.trim();
    if (!name) { setLayoutNameError('Enter a layout name.'); return; }
    if (name.length > 60) { setLayoutNameError('Use 60 characters or fewer.'); return; }
    if (layouts.some((layout) => layout.name.toLocaleLowerCase() === name.toLocaleLowerCase())) {
      setLayoutNameError('A layout with this name already exists.'); return;
    }
    setChangingLayout(true);
    switchingLayoutRef.current = true;
    try {
      const current = await saveCurrentLayout();
      const layout = await saveChartLayout({
        ...current, id: `custom_${crypto.randomUUID()}`, name,
        indicators: emptyLayout ? [] : current.indicators,
        scripts: emptyLayout ? [] : current.scripts,
        chartState: emptyLayout ? undefined : current.chartState,
      });
      const selected = await selectChartLayout(layout.id);
      restoreLayout(selected);
      setLayoutDialog(null);
    } catch (error) {
      setLayoutNameError(`Could not save this layout: ${error instanceof Error ? error.message : String(error)}`);
    } finally { setChangingLayout(false); switchingLayoutRef.current = false; }
  };

  const renameLayout = async () => {
    if (layoutDialog?.kind !== 'rename' || changingLayout || switchingLayoutRef.current) return;
    const { layout } = layoutDialog;
    const name = layoutName.trim();
    if (!name) { setLayoutNameError('Enter a layout name.'); return; }
    if (name.length > 60) { setLayoutNameError('Use 60 characters or fewer.'); return; }
    if (layouts.some((item) => item.id !== layout.id && item.name.toLocaleLowerCase() === name.toLocaleLowerCase())) {
      setLayoutNameError('A layout with this name already exists.'); return;
    }
    setChangingLayout(true);
    switchingLayoutRef.current = true;
    try {
      // Drain autosaves before changing the name so an older save cannot undo it.
      await saveCurrentLayout();
      const saved = await renameChartLayout(layout.id, name);
      replaceLayout(saved);
      setLayoutSaveStatus('Layout saved to server');
      setLayoutDialog(null);
    } catch (error) {
      setLayoutNameError(`Could not rename this layout: ${error instanceof Error ? error.message : String(error)}`);
    } finally { setChangingLayout(false); switchingLayoutRef.current = false; }
  };

  const deleteLayout = async () => {
    if (layoutDialog?.kind !== 'delete' || changingLayout || switchingLayoutRef.current) return;
    const { layout } = layoutDialog;
    setChangingLayout(true);
    switchingLayoutRef.current = true;
    if (layoutSaveTimerRef.current) clearTimeout(layoutSaveTimerRef.current);
    try {
      // Complete in-flight writes before deletion so autosave cannot recreate it.
      await layoutSaveQueueRef.current.catch(() => {});
      if (layout.id === activeLayoutRef.current.id) {
        const fallback = layouts.find((item) => item.id === CHART_LAYOUTS[0].id)!;
        await saveChartLayout(fallback);
      }
      const library = await deleteChartLayout(layout.id);
      setServerLayouts(library.layouts);
      if (layout.id === activeLayoutRef.current.id) {
        const fallback = library.layouts.find((item) => item.id === library.activeLayoutId)!;
        restoreLayout(fallback);
      }
      setLayoutSaveStatus('Layout saved to server');
      setLayoutError('');
      setLayoutDialog(null);
    } catch (error) {
      setLayoutNameError(`Could not delete this layout: ${error instanceof Error ? error.message : String(error)}`);
    } finally { setChangingLayout(false); switchingLayoutRef.current = false; }
  };

  const layoutOptionsRef = useRef<DropdownParams>({ title: activeLayout.name, align: 'right', items: [] });
  layoutOptionsRef.current = {
    title: activeLayout.name, tooltip: 'Chart layout', align: 'right',
    items: [
      ...layouts.map((layout) => ({ title: layout.name, onSelect: () => { void applyLayout(layout.id); } })),
      ...(activeLayout.id.startsWith('custom_') ? [
        { title: 'Rename layout…', onSelect: () => {
          if (!readyRef.current || switchingLayoutRef.current) return;
          setLayoutName(activeLayout.name); setLayoutNameError(''); setLayoutDialog({ kind: 'rename', layout: activeLayout });
        } },
        { title: 'Delete layout…', onSelect: () => {
          if (!readyRef.current || switchingLayoutRef.current) return;
          setLayoutNameError(''); setLayoutDialog({ kind: 'delete', layout: activeLayout });
        } },
      ] : []),
      { title: '+ Create new layout…', onSelect: () => {
        if (!readyRef.current || switchingLayoutRef.current) return;
        setLayoutName(''); setEmptyLayout(false); setLayoutNameError(''); setLayoutDialog({ kind: 'create' });
      } },
    ],
  };
  useEffect(() => { layoutDropdownRef.current?.applyOptions(layoutOptionsRef.current); }, [layouts, activeLayoutId]);

  useEffect(() => {
    scheduleLayoutSaveRef.current();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeLayoutId, indicatorConfigs, userScripts, ready]);

  useEffect(() => () => {
    if (layoutSaveTimerRef.current) clearTimeout(layoutSaveTimerRef.current);
  }, []);

  // Params are split by study kind: only the backend-computed ones require a
  // refetch, so a change to an in-browser study must not reset the data store.
  const bridgedParamsKey = useMemo(
    () => JSON.stringify(
      indicatorConfigs.filter((c) => !c.computed).map((c) => ({ id: c.id, params: c.params })),
    ),
    [indicatorConfigs],
  );
  const computedParamsKey = useMemo(
    () => JSON.stringify(
      indicatorConfigs.filter((c) => c.computed).map((c) => ({ id: c.id, params: c.params })),
    ),
    [indicatorConfigs],
  );
  const visibilityKey = useMemo(
    () => indicatorConfigs.map((c) => `${c.id}:${c.visible ? 1 : 0}`).join(','),
    [indicatorConfigs],
  );

  const handleToggleIndicator = (id: string) =>
    setIndicatorConfigs((prev) => prev.map((c) => (c.id === id ? { ...c, visible: !c.visible } : c)));
  const handleChangeParams = (id: string, params: Record<string, number>) =>
    setIndicatorConfigs((prev) => prev.map((c) => (c.id === id ? { ...c, params } : c)));

  const resolvedHeight = height ?? 800;

  // ── Sync bridged studies with the visibility config ────────────────────────
  const applyStudies = async () => {
    const widget = widgetRef.current;
    if (!widget) return;
    const chart = widget.activeChart();
    const entities = studyEntitiesRef.current;
    for (const spec of STUDY_CATALOGUE) {
      if (widgetRef.current !== widget) return;
      const config = indicatorConfigsRef.current.find((item) => item.id === spec.id);
      const shouldShow = config?.visible ?? false;
      const existing = entities.get(spec.id);
      if (shouldShow && !existing) {
        if (pendingStudiesRef.current.has(spec.id)) continue;
        pendingStudiesRef.current.add(spec.id);
        try {
          const inputs = computedStudyInputs(spec.id, config?.params ?? {}) ?? {};
          const id = await chart.createStudy(STUDY_NAME_BY_ID[spec.id], false, false, inputs);
          if (widgetRef.current !== widget) return;
          if (id) {
            if (indicatorConfigsRef.current.find((item) => item.id === spec.id)?.visible) entities.set(spec.id, id);
            else chart.removeEntity(id);
          }
        } catch (e) {
          console.warn(`Failed to create study ${spec.id}:`, e);
        } finally {
          if (widgetRef.current === widget) pendingStudiesRef.current.delete(spec.id);
        }
      } else if (!shouldShow && existing) {
        try {
          chart.removeEntity(existing);
        } catch (e) {
          console.warn(`Failed to remove study ${spec.id}:`, e);
        }
        entities.delete(spec.id);
      }
    }
    setMainPaneHeight();
  };

  /** Resize the main price pane (pane 0) to 50% of the total pane area. */
  const setMainPaneHeight = () => {
    const widget = widgetRef.current;
    if (!widget || savedLayoutRef.current) return;
    try {
      const panes = widget.activeChart().getPanes();
      if (panes.length < 2) return; // nothing to redistribute into
      const total = panes.reduce((sum, p) => sum + p.getHeight(), 0);
      panes[0].setHeight(Math.round(total * 0.5));
    } catch (e) {
      console.warn('Failed to set main pane height:', e);
    }
  };

  // ── Draw buy/sell execution marks for the current symbol ────────────────────
  const drawPositions = async () => {
    const widget = widgetRef.current;
    if (!widget) return;

    executionShapesRef.current.forEach((s) => { try { s.remove(); } catch { /* gone */ } });
    executionShapesRef.current = [];

    const symbolKey = symbol.trim().toUpperCase();
    const [txs, positions] = await Promise.all([
      getTransactions().then((data) => data.filter((tx) => tx.ticker?.toUpperCase() === symbolKey)).catch(() => [] as Transaction[]),
      getPositions().then((data) => data.filter((pos) => pos.ticker?.toUpperCase() === symbolKey)).catch(() => [] as Position[]),
    ]);

    const chart = widget.activeChart();
    const addMark = (
      time: number, price: number, side: 'buy' | 'sell', tooltip: string,
    ) => {
      try {
        chart.createExecutionShape()
          .setTime(time)
          .setPrice(price)
          .setDirection(side)
          // The execution renderer suppresses an empty label. A directional
          // glyph keeps the marker visible while preserving icon-only chrome.
          .setText(side === 'buy' ? '▲' : '▼')
          .setTooltip(tooltip)
          .setArrowColor(tvSideColor(modeRef.current, side))
          .setTextColor(tvSideColor(modeRef.current, side));
      } catch (e) {
        console.warn('Failed to create execution shape:', e);
      }
    };

    // Only real trades get an execution marker. A dividend row is not a sell:
    // drawn as one it became a red "S" labelled e.g. `Ban 10900 CP @ 800.00`,
    // which never happened.
    const executionMarks: ExecutionMark[] = [];
    // Positions are the entry source for holdings created through the Positions
    // screen. The data model has no link between it and a manual buy transaction,
    // so equal date-and-price rows describe the same entry and must not be counted
    // twice. A transaction is preferred because its quantity is immutable after
    // partial position closes.
    const transactionBuyKeys = new Set<string>();
    for (const tx of txs) {
      if (tx.transaction_type !== 'buy' && tx.transaction_type !== 'sell') continue;
      const time = formatChartTime(tx.transaction_date);
      const price = Number(tx.price);
      if (tx.transaction_type === 'buy') transactionBuyKeys.add(`${time}:${price}`);
      executionMarks.push({
        time,
        price,
        quantity: Number(tx.quantity),
        side: tx.transaction_type,
      });
    }
    for (const pos of positions) {
      const time = formatChartTime(pos.purchase_date);
      const price = Number(pos.purchase_price);
      if (transactionBuyKeys.has(`${time}:${price}`)) continue;
      executionMarks.push({ time, price, quantity: Number(pos.quantity), side: 'buy' });
    }

    for (const mark of consolidateExecutionMarks(executionMarks)) {
      addMark(mark.time, mark.price, mark.side, mark.tooltip);
    }
  };

  // Register new script metadata by rebuilding from the saved chart layout.
  useEffect(() => {
    if (!containerRef.current || !scriptsLoaded) return;

    tvStore.setIndicators(toIndicatorParams(indicatorConfigs));
    tvStore.reset();

    let disposed = false;
    createTvWidget({
      container: containerRef.current,
      datafeed: createDatafeed(tvStore),
      library_path: LIBRARY_PATH,
      symbol,
      interval: DAILY,
      // Default initial visible range: the library otherwise shows its own
      // default (~1Y) regardless of how much history is actually loaded into
      // the store (see HISTORY_YEARS in store.ts) — keep these in sync.
      timeframe: `${HISTORY_YEARS * 12}M`,
      locale: 'en' as LanguageCode,
      autosize: true,
      theme: mode,
      timezone: 'Asia/Ho_Chi_Minh',
      saved_data: savedLayoutRef.current,
      auto_save_delay: 1,
      custom_indicators_getter: async (pine) => [
        ...await customIndicatorsGetter(pine),
        ...userScriptsRef.current.map((script) => buildOpenScriptStudy(pine, {
          ...userScriptSpec(script),
          onError: (message) => { if (!disposed) setScriptError(`${script.title}: ${message}`); },
        })),
      ],
      // The app panel owns server-backed lists independently of chart layouts.
      widgetbar: {
        watchlist: false,
      },
      disabled_features: [],
      overrides: tvOverrides(mode),
    }).then((widget) => {
      if (disposed) { widget.remove(); return; }
      widgetRef.current = widget;
      widget.onChartReady(() => {
        if (disposed) return;
        skipRestoredInputsRef.current = Boolean(savedLayoutRef.current);
        const restored = restoredEntitiesRef.current;
        const existingStudies = widget.activeChart().getAllStudies();
        if (restored) {
          const ids = new Set(existingStudies.map((study) => study.id));
          studyEntitiesRef.current = new Map([...restored.studies].filter(([, id]) => ids.has(id)));
          scriptEntitiesRef.current = new Map([...restored.scripts].filter(([, id]) => ids.has(id)));
          restoredEntitiesRef.current = null;
        }
        // Reconcile by name as well: saved layouts can be restored independently
        // of our in-memory IDs (for example after a development hot reload).
        for (const spec of STUDY_CATALOGUE) {
          const existing = existingStudies.find((study) => study.name === STUDY_NAME_BY_ID[spec.id]);
          if (existing) studyEntitiesRef.current.set(spec.id, existing.id);
        }
        for (const script of userScriptsRef.current) {
          const name = userScriptSpec(script).name;
          const existing = existingStudies.find((study) => study.name === name);
          if (existing) scriptEntitiesRef.current.set(script.id, existing.id);
        }
        setReady(true);
        void drawPositions();
      });
      widget.subscribe('study_event', (entityId, event) => {
        if (disposed || event !== 'remove') return;
        const builtin = [...studyEntitiesRef.current].find(([, id]) => id === entityId);
        if (builtin) {
          studyEntitiesRef.current.delete(builtin[0]);
          setIndicatorConfigs((configs) => configs.map((config) => config.id === builtin[0] ? { ...config, visible: false } : config));
        }
        scheduleLayoutSaveRef.current();
        const entry = [...scriptEntitiesRef.current].find(([, id]) => id === entityId);
        if (!entry) return;
        scriptEntitiesRef.current.delete(entry[0]);
        void persistScripts(userScriptsRef.current.map((script) => script.id === entry[0] ? { ...script, visible: false } : script))
          .catch(() => { if (!disposed) setScriptError('Could not save indicator visibility on the server.'); });
      });
      widget.subscribe('onAutoSaveNeeded', () => { if (!disposed) scheduleLayoutSaveRef.current(); });
      widget.subscribe('study_properties_changed', () => { if (!disposed) scheduleLayoutSaveRef.current(); });
      void resolveWatchListApi(widget).then((resolved) => {
        if (disposed) return;
        onWatchListResolvedRef.current?.(resolved);
      });
      // Move all app controls into the chart header toolbar: symbol picker,
      // Chart/Large-Orders toggle, and sync on the left; layout + indicators
      // on the right (next to the built-in settings / fullscreen buttons).
      widget.headerReady().then(() => {
        if (disposed) return;

        // Symbol selection uses the library's built-in Symbol Search (header).
        // Propagate its changes back up to the app.
        widget.activeChart().onSymbolChanged().subscribe(null, () => {
          if (disposed) return;
          const next = widget.activeChart().symbol().toUpperCase();
          if (next) headerHandlersRef.current.onSymbolChange?.(next);
        });

        // ── View toggle: Chart / Large Orders ────────────────────────────────
        const viewTitle = (large: boolean) => (large ? 'Large Orders' : 'Chart');
        widget.createDropdown({
          title: viewTitle(showLargeOrders),
          tooltip: 'View',
          align: 'left',
          items: [
            { title: 'Chart', onSelect: () => headerHandlersRef.current.onToggleLargeOrders?.(false) },
            { title: 'Large Orders', onSelect: () => headerHandlersRef.current.onToggleLargeOrders?.(true) },
          ],
        }).then((dropdown) => {
          if (disposed) { dropdown.remove(); return; }
          viewDropdownRef.current = dropdown;
        });

        // ── Sync button ──────────────────────────────────────────────────────
        const syncBtn = widget.createButton({ align: 'left', useTradingViewStyle: false });
        syncBtn.setAttribute('title', 'Sync data for this symbol');
        syncBtn.style.cursor = 'pointer';
        syncBtn.innerHTML =
          '<span style="display:flex;align-items:center;gap:4px">' +
          '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>' +
          'Sync</span>';
        syncBtn.addEventListener('click', () => headerHandlersRef.current.onSync?.());
        syncBtnElRef.current = syncBtn;

        // ── Layout picker + indicators (right side) ──────────────────────────
        widget.createDropdown(layoutOptionsRef.current).then((dropdown) => {
          if (disposed) { dropdown.remove(); return; }
          layoutDropdownRef.current = dropdown;
          dropdown.applyOptions(layoutOptionsRef.current);
        });

        const btn = widget.createButton({ align: 'right', useTradingViewStyle: false });
        btn.setAttribute('title', 'Indicators');
        btn.style.cursor = 'pointer';
        btn.innerHTML =
          '<span style="display:flex;align-items:center;gap:4px">' +
          '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 17 9 11 13 15 21 7"/><polyline points="14 7 21 7 21 14"/></svg>' +
          'Indicators</span>';
        btn.addEventListener('click', () => setIndicatorPanelOpen((p) => !p));
        const scriptButton = widget.createButton({ align: 'right', useTradingViewStyle: false });
        scriptButton.textContent = 'OpenScript Editor';
        scriptButton.title = 'Write or paste a custom indicator';
        scriptButton.addEventListener('click', () => setScriptEditorOpen((open) => !open));
      });
    }).catch((e) => {
      console.error('Failed to create TradingView widget:', e);
      if (!disposed) setScriptError('Could not load the chart. Reload the page to try again.');
    });

    return () => {
      disposed = true;
      if (layoutSaveTimerRef.current) clearTimeout(layoutSaveTimerRef.current);
      setReady(false);
      studyEntitiesRef.current.clear();
      pendingStudiesRef.current.clear();
      scriptEntitiesRef.current.clear();
      pendingScriptsRef.current.clear();
      executionShapesRef.current = [];
      if (widgetRef.current) {
        try { widgetRef.current.remove(); } catch { /* already gone */ }
        widgetRef.current = null;
      }
    };
    // Symbol / params are handled by dedicated effects below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scriptRevision, scriptsLoaded]);

  const scriptVisibilityKey = userScripts.map((script) => `${script.id}:${script.visible}`).join(',');
  const pendingScriptsRef = useRef(new Set<string>());
  useEffect(() => {
    if (!ready || !widgetRef.current) return;
    const widget = widgetRef.current;
    const chart = widget.activeChart();
    for (const [scriptId, entityId] of scriptEntitiesRef.current) {
      if (userScriptsRef.current.some((script) => script.id === scriptId && script.visible)) continue;
      scriptEntitiesRef.current.delete(scriptId);
      chart.removeEntity(entityId);
    }
    void (async () => {
      for (const script of userScriptsRef.current) {
        if (widgetRef.current !== widget) return;
        if (!script.visible || scriptEntitiesRef.current.has(script.id) || pendingScriptsRef.current.has(script.id)) continue;
        pendingScriptsRef.current.add(script.id);
        try {
          const id = await chart.createStudy(userScriptSpec(script).name, false, false);
          if (widgetRef.current !== widget) return;
          if (!id) throw new Error('The chart did not create an indicator.');
          if (userScriptsRef.current.some((current) => current.id === script.id && current.visible)) scriptEntitiesRef.current.set(script.id, id);
          else chart.removeEntity(id);
        } catch (error) {
          if (widgetRef.current === widget) setScriptError(`Could not add ${script.title}: ${error instanceof Error ? error.message : String(error)}`);
        } finally {
          if (widgetRef.current === widget) pendingScriptsRef.current.delete(script.id);
        }
      }
      if (widgetRef.current === widget) setMainPaneHeight();
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, scriptRevision, scriptVisibilityKey]);

  // ── Symbol change ──────────────────────────────────────────────────────────
  useEffect(() => {
    if (!ready || !widgetRef.current) return;
    const widget = widgetRef.current;
    // Skip if the chart is already on this symbol (change came from the
    // widget's own Symbol Search → avoids a redundant reload loop).
    try {
      if (widget.activeChart().symbol().toUpperCase() === symbol.toUpperCase()) return;
    } catch { /* chart not ready yet */ }
    widget.setSymbol(symbol, DAILY, () => {
      void drawPositions();
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol]);

  // ── Header control state sync (view toggle title + sync button state) ───────
  useEffect(() => {
    viewDropdownRef.current?.applyOptions({ title: showLargeOrders ? 'Large Orders' : 'Chart' });
  }, [showLargeOrders]);

  useEffect(() => {
    const btn = syncBtnElRef.current;
    if (!btn) return;
    btn.style.opacity = syncing ? '0.5' : '1';
    btn.style.pointerEvents = syncing ? 'none' : 'auto';
  }, [syncing]);

  // ── Indicator param change → refetch + recompute studies ────────────────────
  useEffect(() => {
    if (!ready || !widgetRef.current) return;
    tvStore.setIndicators(toIndicatorParams(indicatorConfigs));
    // Drop the pages loaded under the old params; resetData makes the library
    // re-request them (getBars refetches with the new indicator payload).
    tvStore.reset();
    try { widgetRef.current?.activeChart().resetData(); } catch { /* not ready */ }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bridgedParamsKey]);

  // ── Computed-study param change → push new study inputs ─────────────────────
  // These recalculate in the browser, so no refetch: just hand the study its new
  // inputs and the library re-runs it over the bars it already has.
  useEffect(() => {
    const widget = widgetRef.current;
    if (!ready || !widget) return;
    if (skipRestoredInputsRef.current) { skipRestoredInputsRef.current = false; return; }
    const chart = widget.activeChart();
    for (const config of indicatorConfigs) {
      const entity = studyEntitiesRef.current.get(config.id);
      const inputs = entity ? computedStudyInputs(config.id, config.params) : null;
      if (!entity || !inputs) continue;
      try {
        chart.getStudyById(entity).setInputValues(
          Object.entries(inputs).map(([id, value]) => ({
            id: id as StudyInputId,
            value,
          })),
        );
      } catch (e) {
        console.warn(`Failed to update inputs for study ${config.id}:`, e);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [computedParamsKey, ready]);

  // ── Indicator visibility change → add/remove studies ────────────────────────
  useEffect(() => {
    if (!ready) return;
    void applyStudies();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visibilityKey, ready]);

  // Follow the app colour mode without remounting the widget: changeTheme()
  // swaps the library chrome, applyOverrides() restores our token colours on
  // top of it (the library resets overrides as part of a theme change).
  useEffect(() => {
    const widget = widgetRef.current;
    if (!widget || !ready) return;
    let cancelled = false;
    void widget
      .changeTheme(mode, { disableUndo: true })
      .then(() => {
        if (!cancelled) widget.applyOverrides(tvOverrides(mode));
      })
      .catch((e) => console.warn('Failed to apply chart theme:', e));
    return () => {
      cancelled = true;
    };
  }, [mode, ready]);

  return (
    <Box sx={{
      width: '100%',
      height: resolvedHeight,
      display: 'flex',
      flexDirection: 'column',
      position: 'relative',
      bgcolor: 'surface.inset',
      borderRadius: 1,
      overflow: 'hidden',
    }}>
      <div ref={containerRef} style={{ width: '100%', flex: 1, minHeight: 160 }} />
      <Box sx={{ borderTop: 1, borderColor: 'divider', px: 1, flexShrink: 0 }}>
        <Button size="small" onClick={() => setScriptEditorOpen((open) => !open)} aria-expanded={scriptEditorOpen}>OpenScript Editor</Button>
        <Button size="small" disabled={!ready || changingLayout || layoutSaveStatus === 'Saving layout…'} onClick={() => { void saveCurrentLayout().catch(() => {}); }}>Save layout</Button>
        <Typography component="span" variant="caption" color="text.secondary" role="status">{layoutSaveStatus}</Typography>
      </Box>
      {scriptEditorOpen && scriptsLoaded && <ScriptEditor scripts={userScripts} ready={ready} error={scriptError} onSave={saveScript} onRemove={removeScript} onClose={() => setScriptEditorOpen(false)} />}
      {scriptsLoading && <Alert severity="info">Loading your chart layouts and indicators from server…</Alert>}
      {(!scriptEditorOpen || !scriptsLoaded) && scriptError && <Alert severity="error" action={!scriptsLoaded ? <Button onClick={() => setScriptLoadAttempt((attempt) => attempt + 1)}>Retry</Button> : undefined}>{scriptError}</Alert>}
      {legacyScripts.length > 0 && scriptsLoaded && <Alert severity="info" action={<Button disabled={importingScripts} onClick={() => void importBrowserScripts()}>{importingScripts ? 'Importing…' : 'Import browser scripts'}</Button>}>{legacyScripts.length} scripts from the old browser library can be imported into your server library.</Alert>}

      {legacyLayouts.length > 0 && scriptsLoaded && <Alert severity="info" action={<Button disabled={importingLayouts || !ready} onClick={() => void importBrowserLayouts()}>{importingLayouts ? 'Importing…' : 'Import browser layouts'}</Button>}>{legacyLayouts.length} browser layouts can be imported into your account.</Alert>}
      {layoutError && <Alert severity="error" onClose={() => setLayoutError('')}>{layoutError}</Alert>}
      <Dialog open={layoutDialog?.kind === 'create' || layoutDialog?.kind === 'rename'} onClose={() => { if (!changingLayout) setLayoutDialog(null); }} maxWidth="xs" fullWidth>
        <Box component="form" onSubmit={(event) => { event.preventDefault(); void (layoutDialog?.kind === 'rename' ? renameLayout() : createLayout()); }}>
          <DialogTitle>{layoutDialog?.kind === 'rename' ? 'Rename layout' : 'Create new layout'}</DialogTitle>
          <DialogContent>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>{layoutDialog?.kind === 'rename' ? 'Choose a new name for this layout. Your indicators and settings stay with it.' : 'Save your indicator selection and settings as a named layout. Layouts and their indicators are saved automatically to your account on the server.'}</Typography>
            <TextField autoFocus fullWidth size="small" disabled={changingLayout} label="Layout name" value={layoutName} onChange={(event) => { setLayoutName(event.target.value); setLayoutNameError(''); }} error={!!layoutNameError} helperText={layoutNameError || 'For example, Momentum or My OpenScript indicators'} inputProps={{ maxLength: 60 }} />
            {layoutDialog?.kind === 'create' && <FormControlLabel sx={{ mt: 1 }} control={<Checkbox disabled={changingLayout} checked={emptyLayout} onChange={(event) => setEmptyLayout(event.target.checked)} />} label="Start without indicators" />}
          </DialogContent>
          <DialogActions sx={{ px: 3, pb: 2 }}>
            <Button disabled={changingLayout} onClick={() => setLayoutDialog(null)}>Cancel</Button>
            <Button disabled={changingLayout || !ready} type="submit" variant="contained">{changingLayout ? 'Saving…' : layoutDialog?.kind === 'rename' ? 'Save name' : 'Create layout'}</Button>
          </DialogActions>
        </Box>
      </Dialog>
      <Dialog open={layoutDialog?.kind === 'delete'} onClose={() => { if (!changingLayout) setLayoutDialog(null); }} maxWidth="xs" fullWidth aria-labelledby="delete-layout-title">
        <DialogTitle id="delete-layout-title">Delete layout?</DialogTitle>
        <DialogContent>
          <Typography variant="body2">Delete “{layoutDialog && 'layout' in layoutDialog ? layoutDialog.layout.name : ''}” from your account? The chart will switch to Layout 1. Your saved OpenScripts will remain in your library.</Typography>
          {layoutNameError && <Alert severity="error" sx={{ mt: 2 }}>{layoutNameError}</Alert>}
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button disabled={changingLayout} onClick={() => setLayoutDialog(null)}>Cancel</Button>
          <Button disabled={changingLayout || !ready} color="error" variant="contained" onClick={() => void deleteLayout()}>{changingLayout ? 'Deleting…' : 'Delete layout'}</Button>
        </DialogActions>
      </Dialog>

      {/* Layout dropdown + indicators button live in the chart header toolbar
          (added imperatively via widget.createDropdown / createButton). */}

      {indicatorPanelOpen && (
        <IndicatorManager
          configs={indicatorConfigs}
          onToggleVisible={handleToggleIndicator}
          onChangeParams={handleChangeParams}
          onClose={() => setIndicatorPanelOpen(false)}
          onOpenScriptEditor={() => { setIndicatorPanelOpen(false); setScriptEditorOpen(true); }}
        />
      )}
    </Box>
  );
}
