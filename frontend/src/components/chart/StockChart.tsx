import { useEffect, useMemo, useRef, useState } from 'react';
import { Box } from '@mui/material';

import { createTvWidget, LIBRARY_PATH } from '../../lib/tv';
import type {
  EntityId,
  IChartingLibraryWidget,
  IDropdownApi,
  IExecutionLineAdapter,
  LanguageCode,
  ResolutionString,
  StudyInputId,
} from '../../lib/tv';
import { createDatafeed } from '../../lib/tv/datafeed';
import { tvStore, HISTORY_YEARS } from '../../lib/tv/store';
import {
  DEFAULT_WATCHLIST_SYMBOLS,
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

import IndicatorManager from './IndicatorManager';

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
   * True when the study computes itself in the browser (PineJS). Its params are
   * passed as study inputs and it is left out of the backend indicator request.
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
    id: 'atr_trailing', name: 'atr_trailing', label: 'ATR Trailing Stop',
    params: { timeperiod: 10, multiplier: 1.8 }, visible: true,
    paramDefs: [
      { key: 'timeperiod', label: 'ATR Period',   min: 2,   max: 100, step: 1   },
      { key: 'multiplier', label: 'Multiplier',   min: 0.5, max: 10,  step: 0.1 },
    ],
  },
  {
    id: 'vwap', name: 'vwap', label: 'VWAP',
    params: { window: 200 }, visible: true,
    paramDefs: [{ key: 'window', label: 'Window', min: 10, max: 1000, step: 10 }],
  },
  {
    id: 'kama', name: 'kama', label: 'KAMA',
    params: { timeperiod: 10 }, visible: true,
    paramDefs: [{ key: 'timeperiod', label: 'Period', min: 2, max: 200, step: 1 }],
  },
  {
    id: 'linreg_channel', name: 'linreg_channel', label: 'LR Prediction Channel',
    params: { reg_window: 50, confidence: 0.85 }, visible: true,
    paramDefs: [
      { key: 'reg_window', label: 'Reg Window', min: 10, max: 200,  step: 5    },
      { key: 'confidence', label: 'Confidence', min: 0.5, max: 0.99, step: 0.01 },
    ],
  },
  {
    id: 'bvc', name: 'bvc', label: 'BVC',
    params: { window: 20, kappa: 0.1 }, visible: true,
    paramDefs: [
      { key: 'window', label: 'Window', min: 5, max: 200, step: 1 },
      { key: 'kappa', label: 'Kappa', min: 0.01, max: 1, step: 0.01 },
    ],
  },
  {
    id: 'kalman_zscore', name: 'kalman_zscore', label: 'Kalman Z-Score',
    params: { window: 20 }, visible: true,
    paramDefs: [{ key: 'window', label: 'Window', min: 5, max: 200, step: 1 }],
  },
  {
    id: 'yz_volatility', name: 'yz_volatility', label: 'YZ Volatility',
    params: { window: 30, periods: 252 }, visible: true,
    paramDefs: [
      { key: 'window', label: 'Window', min: 5, max: 200, step: 1 },
      { key: 'periods', label: 'Annual Periods', min: 52, max: 365, step: 1 },
    ],
  },
  {
    id: 'gkyz_volatility', name: 'gkyz_volatility', label: 'GKYZ Volatility',
    params: { window: 21 }, visible: true,
    paramDefs: [
      { key: 'window', label: 'Window', min: 5, max: 200, step: 1 },
    ],
  },
  {
    id: 'matrix_series', name: 'matrix_series', label: 'Matrix Series',
    params: { price_period: 20, sup_res_period: 50, sup_res_percentage: 100, smoother: 5 },
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
    params: {}, visible: true, paramDefs: [],
  },
  {
    id: 'squeeze_ttm', name: 'squeeze_ttm', label: 'Squeeze TTM',
    params: {}, visible: true, paramDefs: [],
  },
  {
    id: 'chandelier_exit', name: 'chandelier_exit', label: 'Chandelier Exit',
    params: { length: 31, multiplier: 2.2 }, visible: true,
    paramDefs: [
      { key: 'length',     label: 'Length',     min: 5,   max: 100, step: 1   },
      { key: 'multiplier', label: 'Multiplier', min: 0.5, max: 10,  step: 0.1 },
    ],
  },
  {
    id: 'gaussian_frama', name: 'gaussian_frama', label: 'Gaussian FRAMA',
    params: {
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
    params: { length: 14, mult: 2.0 }, visible: true,
    paramDefs: [
      { key: 'length', label: 'Length', min: 4,   max: 50, step: 1   },
      { key: 'mult',   label: 'Mult',   min: 0.5, max: 5,  step: 0.1 },
    ],
  },
  {
    id: 'smart_money_flow', name: 'smart_money_flow', label: 'SMF Cloud',
    params: {
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

/** A named preset controlling which indicators are visible. */
export interface ChartLayout {
  id: string;
  name: string;
  indicators: string[];
}

/** Indicators that live in the secondary layout. */
const LAYOUT_2_IDS = ['rsi', 'bvc', 'kalman_zscore', 'yz_volatility'];

/** Indicators for the backtest_012 (Gaussian FRAMA + Hull Butterfly) strategy. */
const LAYOUT_3_IDS = ['gaussian_frama', 'hull_butterfly'];

/**
 * Selectable indicator layouts. Layout 1 holds everything except the Layout 2
 * and Layout 3 indicators; Layout 2 holds BVC + RSI + Kalman Z-Score + YZ
 * Volatility; Layout 3 holds the Gaussian FRAMA + Hull Butterfly Oscillator
 * strategy (notebooks/backtest_012.ipynb).
 */
const CHART_LAYOUTS: ChartLayout[] = [
  {
    id: 'layout1',
    name: 'Layout 1',
    indicators: DEFAULT_INDICATOR_CONFIGS.map((c) => c.id).filter(
      (id) => !LAYOUT_2_IDS.includes(id) && !LAYOUT_3_IDS.includes(id),
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
    .filter((c) => !c.computed)
    .map((c) => ({
      name: c.name,
      ...(Object.keys(c.params).length > 0 ? { params: c.params } : {}),
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
  const containerRef = useRef<HTMLDivElement>(null);
  const widgetRef = useRef<IChartingLibraryWidget | null>(null);
  const studyEntitiesRef = useRef<Map<string, EntityId>>(new Map());
  const executionShapesRef = useRef<IExecutionLineAdapter[]>([]);
  const [ready, setReady] = useState(false);

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

  const [indicatorConfigs, setIndicatorConfigs] = useState<IndicatorConfig[]>(() => {
    try {
      const stored = localStorage.getItem('indicatorConfigs');
      if (!stored) return DEFAULT_INDICATOR_CONFIGS;
      const parsed: IndicatorConfig[] = JSON.parse(stored);
      return DEFAULT_INDICATOR_CONFIGS.map((def) => {
        const saved = parsed.find((s) => s.id === def.id);
        return saved ? { ...def, params: saved.params, visible: saved.visible } : def;
      });
    } catch {
      return DEFAULT_INDICATOR_CONFIGS;
    }
  });
  const [indicatorPanelOpen, setIndicatorPanelOpen] = useState(false);

  // Active indicator layout — persisted; drives which indicators are visible.
  const [activeLayoutId, setActiveLayoutId] = useState<string>(() => {
    const stored = localStorage.getItem('chartLayoutId');
    return CHART_LAYOUTS.some((l) => l.id === stored) ? (stored as string) : CHART_LAYOUTS[0].id;
  });
  const layoutDropdownRef = useRef<IDropdownApi | null>(null);

  /** Selects a layout: updates visibility preset, persists, and syncs the header dropdown. */
  const applyLayout = (layoutId: string) => {
    const layout = CHART_LAYOUTS.find((l) => l.id === layoutId);
    if (!layout) return;
    setActiveLayoutId(layout.id);
    try { localStorage.setItem('chartLayoutId', layout.id); } catch { /* ignore */ }
    // An indicator is visible iff it belongs to this layout.
    const inLayout = new Set(layout.indicators);
    setIndicatorConfigs((prev) => prev.map((c) => ({ ...c, visible: inLayout.has(c.id) })));
    layoutDropdownRef.current?.applyOptions({ title: layout.name });
  };

  useEffect(() => {
    try {
      localStorage.setItem('indicatorConfigs', JSON.stringify(
        indicatorConfigs.map(({ id, params, visible }) => ({ id, params, visible })),
      ));
    } catch { /* quota / private mode — ignore */ }
  }, [indicatorConfigs]);

  // Apply the persisted layout once on mount so visibility matches the preset.
  useEffect(() => {
    const layout = CHART_LAYOUTS.find((l) => l.id === activeLayoutId) ?? CHART_LAYOUTS[0];
    const inLayout = new Set(layout.indicators);
    setIndicatorConfigs((prev) => prev.map((c) => ({ ...c, visible: inLayout.has(c.id) })));
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
    const visibleById = new Map(indicatorConfigs.map((c) => [c.id, c.visible]));

    const paramsById = new Map(indicatorConfigs.map((c) => [c.id, c.params]));

    for (const spec of STUDY_CATALOGUE) {
      const shouldShow = visibleById.get(spec.id) ?? false;
      const existing = entities.get(spec.id);
      if (shouldShow && !existing) {
        try {
          const inputs = computedStudyInputs(spec.id, paramsById.get(spec.id) ?? {}) ?? {};
          const id = await chart.createStudy(STUDY_NAME_BY_ID[spec.id], false, false, inputs);
          if (id) entities.set(spec.id, id);
        } catch (e) {
          console.warn(`Failed to create study ${spec.id}:`, e);
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
    if (!widget) return;
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
      getTransactions().then((d) => d.filter((t) => t.ticker?.toUpperCase() === symbolKey)).catch(() => [] as Transaction[]),
      getPositions().then((d) => d.filter((p) => p.ticker?.toUpperCase() === symbolKey)).catch(() => [] as Position[]),
    ]);

    const chart = widget.activeChart();
    const addMark = (
      time: number, price: number, side: 'buy' | 'sell', text: string, tooltip: string,
    ) => {
      try {
        chart.createExecutionShape()
          .setTime(time)
          .setPrice(price)
          .setDirection(side)
          .setText(text)
          .setTooltip(tooltip)
          .setArrowColor(side === 'buy' ? '#22c55e' : '#ef4444')
          .setTextColor(side === 'buy' ? '#22c55e' : '#ef4444');
      } catch (e) {
        console.warn('Failed to create execution shape:', e);
      }
    };

    // Only real trades get an execution marker. A dividend row is not a sell:
    // drawn as one it became a red "S" labelled e.g. `Ban 10900 CP @ 800.00`,
    // which never happened.
    const trades = txs.filter(
      (tx) => tx.transaction_type === 'buy' || tx.transaction_type === 'sell',
    );

    trades.forEach((tx) => {
      const side = tx.transaction_type === 'buy' ? 'buy' : 'sell';
      addMark(
        formatChartTime(tx.transaction_date),
        Number(tx.price),
        side,
        side === 'buy' ? 'B' : 'S',
        `${side === 'buy' ? 'Mua' : 'Ban'} ${tx.quantity} CP @ ${Number(tx.price).toFixed(2)}`,
      );
    });
    positions.forEach((pos) => {
      addMark(
        formatChartTime(pos.purchase_date),
        Number(pos.purchase_price),
        'buy',
        'B',
        `Mua ${pos.quantity} CP @ ${Number(pos.purchase_price).toFixed(2)}`,
      );
    });
  };

  // ── Create the widget once on mount ────────────────────────────────────────
  useEffect(() => {
    if (!containerRef.current) return;

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
      theme: 'dark',
      timezone: 'Asia/Ho_Chi_Minh',
      custom_indicators_getter: customIndicatorsGetter,
      // Trading Terminal only: asks for the widget bar's Watch List. The
      // Advanced Charts build has no widget bar and ignores this, which is why
      // resolveWatchListApi below decides who actually renders the list.
      widgetbar: {
        watchlist: true,
        watchlist_settings: { default_symbols: DEFAULT_WATCHLIST_SYMBOLS },
      },
      disabled_features: [],
      overrides: {
        'paneProperties.background': '#0a0a0f',
        'paneProperties.backgroundType': 'solid',
        'mainSeriesProperties.candleStyle.upColor': '#22c55e',
        'mainSeriesProperties.candleStyle.downColor': '#ef4444',
        'mainSeriesProperties.candleStyle.wickUpColor': '#22c55e',
        'mainSeriesProperties.candleStyle.wickDownColor': '#ef4444',
        'mainSeriesProperties.candleStyle.borderVisible': false,
      },
    }).then((widget) => {
      if (disposed) { widget.remove(); return; }
      widgetRef.current = widget;
      widget.onChartReady(() => {
        if (disposed) return;
        setReady(true);
        void applyStudies();
        void drawPositions();
      });
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
        const initial = CHART_LAYOUTS.find((l) => l.id === activeLayoutId) ?? CHART_LAYOUTS[0];
        widget.createDropdown({
          title: initial.name,
          tooltip: 'Chart layout',
          align: 'right',
          items: CHART_LAYOUTS.map((l) => ({ title: l.name, onSelect: () => applyLayout(l.id) })),
        }).then((dropdown) => {
          if (disposed) { dropdown.remove(); return; }
          layoutDropdownRef.current = dropdown;
        });

        const btn = widget.createButton({ align: 'right', useTradingViewStyle: false });
        btn.setAttribute('title', 'Indicators');
        btn.style.cursor = 'pointer';
        btn.innerHTML =
          '<span style="display:flex;align-items:center;gap:4px">' +
          '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 17 9 11 13 15 21 7"/><polyline points="14 7 21 7 21 14"/></svg>' +
          'Indicators</span>';
        btn.addEventListener('click', () => setIndicatorPanelOpen((p) => !p));
      });
    }).catch((e) => console.error('Failed to create TradingView widget:', e));

    return () => {
      disposed = true;
      setReady(false);
      studyEntitiesRef.current.clear();
      executionShapesRef.current = [];
      if (widgetRef.current) {
        try { widgetRef.current.remove(); } catch { /* already gone */ }
        widgetRef.current = null;
      }
    };
    // Mount-only: symbol / params handled by dedicated effects below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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

  return (
    <Box sx={{
      width: '100%',
      height: resolvedHeight,
      position: 'relative',
      bgcolor: '#0a0a0f',
      borderRadius: 2,
      overflow: 'hidden',
    }}>
      <div ref={containerRef} style={{ width: '100%', height: '100%' }} />

      {/* Layout dropdown + indicators button live in the chart header toolbar
          (added imperatively via widget.createDropdown / createButton). */}

      {indicatorPanelOpen && (
        <IndicatorManager
          configs={indicatorConfigs}
          onToggleVisible={handleToggleIndicator}
          onChangeParams={handleChangeParams}
          onClose={() => setIndicatorPanelOpen(false)}
        />
      )}
    </Box>
  );
}
