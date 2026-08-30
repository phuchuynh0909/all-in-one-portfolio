/**
 * Design tokens — single source of truth.
 *
 * Three layers, per the design-system architecture:
 *
 *   primitives  →  raw values, no meaning        (neutral[900], amber[400])
 *   semantic    →  purpose aliases, theme-aware  (bg.canvas, text.primary, accent)
 *   component   →  per-component overrides       (appBar.height, panel.padding)
 *
 * The semantic + component layers are emitted onto `document.documentElement`
 * as CSS custom properties by `applyCssVars()`, so non-MUI consumers
 * (Bokeh, lightweight-charts, the TradingView charting library, plain CSS)
 * read the exact same values MUI does. Never hardcode a hex in a component.
 */

// ---------------------------------------------------------------------------
// Layer 1 — Primitives
// ---------------------------------------------------------------------------

export const primitives = {
  neutral: {
    0: '#FFFFFF',
    25: '#FBFBFC',
    50: '#F5F6F8',
    100: '#EBEDF1',
    150: '#DFE2E8',
    200: '#CFD4DD',
    300: '#AFB6C3',
    400: '#8A93A3',
    500: '#666F7F',
    600: '#4B5361',
    700: '#363D49',
    800: '#262C36',
    850: '#1E232B',
    900: '#171B22',
    925: '#12161C',
    950: '#0D1117',
    1000: '#07090C',
  },
  amber: {
    200: '#FDE68A',
    300: '#FCD34D',
    400: '#FBBF24',
    500: '#F59E0B',
    600: '#D97706',
    700: '#B45309',
    800: '#92400E',
  },
  green: {
    200: '#A7F3D0',
    300: '#6EE7B7',
    400: '#34D399',
    500: '#10B981',
    600: '#059669',
    700: '#047857',
    800: '#065F46',
  },
  red: {
    200: '#FECACA',
    300: '#FCA5A5',
    400: '#F87171',
    500: '#EF4444',
    600: '#DC2626',
    700: '#B91C1C',
    800: '#991B1B',
  },
  blue: {
    200: '#BFDBFE',
    300: '#93C5FD',
    400: '#60A5FA',
    500: '#3B82F6',
    600: '#2563EB',
    700: '#1D4ED8',
    800: '#1E40AF',
  },
  cyan: { 300: '#67E8F9', 400: '#22D3EE', 500: '#06B6D4', 600: '#0891B2' },
  teal: { 300: '#5EEAD4', 400: '#2DD4BF', 500: '#14B8A6', 600: '#0D9488' },
  violet: { 300: '#C4B5FD', 400: '#A78BFA', 500: '#8B5CF6', 600: '#7C3AED' },
  pink: { 300: '#F9A8D4', 400: '#F472B6', 500: '#EC4899', 600: '#DB2777' },
  lime: { 300: '#BEF264', 400: '#A3E635', 500: '#84CC16', 600: '#65A30D' },
  orange: { 300: '#FDBA74', 400: '#FB923C', 500: '#F97316', 600: '#EA580C' },
} as const;

/** 4px base spacing scale. */
export const space = {
  0: '0px',
  px: '1px',
  0.5: '2px',
  1: '4px',
  1.5: '6px',
  2: '8px',
  2.5: '10px',
  3: '12px',
  4: '16px',
  5: '20px',
  6: '24px',
  8: '32px',
  10: '40px',
  12: '48px',
  16: '64px',
  20: '80px',
  24: '96px',
} as const;

/** Dense terminal type scale. Body sits at 14px, tabular data at 12–13px. */
export const fontSize = {
  '2xs': '0.6875rem', // 11px — dense table meta, badges
  xs: '0.75rem', // 12px — table cells, captions
  sm: '0.8125rem', // 13px — secondary UI
  base: '0.875rem', // 14px — body
  md: '1rem', // 16px — emphasised body
  lg: '1.125rem', // 18px — panel titles
  xl: '1.375rem', // 22px — page titles
  '2xl': '1.75rem', // 28px — stat values
  '3xl': '2.25rem', // 36px — hero stats
  '4xl': '3rem', // 48px — landing hero
} as const;

export const fontWeight = {
  regular: 400,
  medium: 500,
  semibold: 600,
  bold: 700,
} as const;

export const lineHeight = {
  tight: 1.2,
  snug: 1.35,
  normal: 1.5,
  relaxed: 1.65,
} as const;

export const letterSpacing = {
  tighter: '-0.02em',
  tight: '-0.01em',
  normal: '0',
  wide: '0.02em',
  wider: '0.06em',
  widest: '0.12em', // section eyebrows / overlines
} as const;

export const fontFamily = {
  sans: "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif",
  mono: "'JetBrains Mono', ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, 'Liberation Mono', monospace",
} as const;

/** Sharp corners — this is a terminal, not a consumer app. */
export const radius = {
  none: '0px',
  xs: '2px',
  sm: '3px',
  md: '4px',
  lg: '6px',
  xl: '8px',
  '2xl': '12px',
  full: '9999px',
} as const;

/**
 * Stacking order. Follows MUI's convention: the app bar sits BELOW drawers and
 * modals, so a temporary drawer covers it rather than being clipped by it.
 * The permanent sidebar opts above the app bar explicitly (appBar + 1) — they
 * never overlap horizontally, so that only settles their shared edge.
 */
export const zIndex = {
  base: 0,
  sticky: 100,
  appBar: 1100,
  drawer: 1200,
  modal: 1300,
  tooltip: 1500,
} as const;

export const duration = {
  instant: '80ms',
  fast: '120ms',
  normal: '180ms',
  slow: '260ms',
} as const;

export const easing = {
  standard: 'cubic-bezier(0.2, 0, 0, 1)',
  decelerate: 'cubic-bezier(0, 0, 0, 1)',
  accelerate: 'cubic-bezier(0.3, 0, 1, 1)',
} as const;

// ---------------------------------------------------------------------------
// Layer 2 — Semantic
// ---------------------------------------------------------------------------

export type ColorMode = 'dark' | 'light';

const p = primitives;

/**
 * Semantic colour contract. Both modes MUST implement every key — that
 * guarantee is what makes the mode toggle safe.
 */
export interface SemanticColors {
  /** Page background, behind every surface. */
  bgCanvas: string;
  /** Default panel / card surface. */
  bgSurface: string;
  /** Raised surface: popovers, active rows, nested panels. */
  bgSurfaceRaised: string;
  /** Highest surface: menus, dialogs, tooltips. */
  bgSurfaceOverlay: string;
  /** Recessed wells: code blocks, chart backdrops, table headers. */
  bgInset: string;
  /** Chrome: app bar and sidebar. */
  bgChrome: string;
  /** Transparent interaction states — layer over any surface. */
  bgHover: string;
  bgActive: string;
  bgSelected: string;

  borderSubtle: string;
  borderDefault: string;
  borderStrong: string;

  textPrimary: string;
  textSecondary: string;
  textTertiary: string;
  textDisabled: string;
  /** Text on top of an accent fill. */
  textOnAccent: string;

  /** Brand + primary interactive colour. */
  accent: string;
  accentHover: string;
  accentPressed: string;
  accentSubtle: string;
  accentBorder: string;

  /** Market direction. Long/profit and short/loss. */
  long: string;
  longSubtle: string;
  short: string;
  shortSubtle: string;
  /** Unchanged / flat. */
  flat: string;

  success: string;
  successSubtle: string;
  warning: string;
  warningSubtle: string;
  danger: string;
  dangerSubtle: string;
  info: string;
  infoSubtle: string;

  focusRing: string;
  /** Chart gridlines and axis strokes. */
  chartGrid: string;
  chartAxis: string;
  /** Ordered categorical series palette. */
  chartSeries: string[];
}

export const darkColors: SemanticColors = {
  bgCanvas: p.neutral[950],
  bgSurface: p.neutral[900],
  bgSurfaceRaised: p.neutral[850],
  bgSurfaceOverlay: p.neutral[800],
  bgInset: p.neutral[1000],
  bgChrome: p.neutral[925],
  bgHover: 'rgba(255, 255, 255, 0.045)',
  bgActive: 'rgba(255, 255, 255, 0.075)',
  bgSelected: 'rgba(251, 191, 36, 0.10)',

  borderSubtle: '#20252E',
  borderDefault: '#2C333E',
  borderStrong: '#414A58',

  textPrimary: '#E6E9EF',
  textSecondary: '#9AA4B2',
  textTertiary: '#6B7481',
  textDisabled: '#4B5361',
  textOnAccent: p.neutral[1000],

  accent: p.amber[400],
  accentHover: p.amber[300],
  accentPressed: p.amber[500],
  accentSubtle: 'rgba(251, 191, 36, 0.12)',
  accentBorder: 'rgba(251, 191, 36, 0.35)',

  long: p.green[400],
  longSubtle: 'rgba(52, 211, 153, 0.13)',
  short: p.red[400],
  shortSubtle: 'rgba(248, 113, 113, 0.13)',
  flat: p.neutral[400],

  success: p.green[400],
  successSubtle: 'rgba(52, 211, 153, 0.13)',
  warning: p.amber[400],
  warningSubtle: 'rgba(251, 191, 36, 0.13)',
  danger: p.red[400],
  dangerSubtle: 'rgba(248, 113, 113, 0.13)',
  info: p.blue[400],
  infoSubtle: 'rgba(96, 165, 250, 0.13)',

  focusRing: p.amber[400],
  chartGrid: 'rgba(255, 255, 255, 0.06)',
  chartAxis: '#6B7481',
  chartSeries: [
    p.amber[400],
    p.cyan[400],
    p.violet[400],
    p.green[400],
    p.pink[400],
    p.blue[400],
    p.lime[400],
    p.orange[400],
    p.teal[400],
    p.red[400],
  ],
};

export const lightColors: SemanticColors = {
  bgCanvas: p.neutral[50],
  bgSurface: p.neutral[0],
  bgSurfaceRaised: p.neutral[0],
  bgSurfaceOverlay: p.neutral[0],
  bgInset: p.neutral[100],
  bgChrome: p.neutral[0],
  bgHover: 'rgba(13, 17, 23, 0.04)',
  bgActive: 'rgba(13, 17, 23, 0.07)',
  bgSelected: 'rgba(217, 119, 6, 0.10)',

  borderSubtle: p.neutral[100],
  borderDefault: p.neutral[200],
  borderStrong: p.neutral[300],

  textPrimary: p.neutral[950],
  textSecondary: p.neutral[500],
  textTertiary: p.neutral[400],
  textDisabled: p.neutral[300],
  textOnAccent: p.neutral[0],

  accent: p.amber[600],
  accentHover: p.amber[700],
  accentPressed: p.amber[800],
  accentSubtle: 'rgba(217, 119, 6, 0.10)',
  accentBorder: 'rgba(217, 119, 6, 0.35)',

  long: p.green[600],
  longSubtle: 'rgba(5, 150, 105, 0.12)',
  short: p.red[600],
  shortSubtle: 'rgba(220, 38, 38, 0.12)',
  flat: p.neutral[500],

  success: p.green[600],
  successSubtle: 'rgba(5, 150, 105, 0.12)',
  warning: p.amber[600],
  warningSubtle: 'rgba(217, 119, 6, 0.12)',
  danger: p.red[600],
  dangerSubtle: 'rgba(220, 38, 38, 0.12)',
  info: p.blue[600],
  infoSubtle: 'rgba(37, 99, 235, 0.12)',

  focusRing: p.amber[600],
  chartGrid: 'rgba(13, 17, 23, 0.07)',
  chartAxis: p.neutral[400],
  chartSeries: [
    p.amber[600],
    p.cyan[600],
    p.violet[600],
    p.green[600],
    p.pink[600],
    p.blue[600],
    p.lime[600],
    p.orange[600],
    p.teal[600],
    p.red[600],
  ],
};

export const colorsByMode: Record<ColorMode, SemanticColors> = {
  dark: darkColors,
  light: lightColors,
};

/**
 * Shadows are mode-dependent: on dark surfaces a drop shadow reads as noise,
 * so dark leans on borders and only the highest overlays cast a shadow.
 */
export const shadowsByMode: Record<ColorMode, Record<string, string>> = {
  dark: {
    none: 'none',
    sm: '0 1px 2px rgba(0, 0, 0, 0.45)',
    md: '0 2px 8px rgba(0, 0, 0, 0.5)',
    lg: '0 8px 24px rgba(0, 0, 0, 0.55)',
    xl: '0 16px 48px rgba(0, 0, 0, 0.6)',
  },
  light: {
    none: 'none',
    sm: '0 1px 2px rgba(13, 17, 23, 0.06)',
    md: '0 2px 8px rgba(13, 17, 23, 0.08)',
    lg: '0 8px 24px rgba(13, 17, 23, 0.10)',
    xl: '0 16px 48px rgba(13, 17, 23, 0.14)',
  },
};

// ---------------------------------------------------------------------------
// Layer 3 — Component tokens
// ---------------------------------------------------------------------------

/** Fixed chrome dimensions the layout depends on. */
export const layout = {
  appBarHeight: '52px',
  sidebarWidth: '236px',
  sidebarCollapsedWidth: '56px',
  contentMaxWidth: '1600px',
  pageGutter: space[6],
  pageGutterSm: space[4],
} as const;

/**
 * Component tokens reference the semantic layer, so they flip with the mode
 * for free. Anything a component needs to style itself belongs here rather
 * than inline in the component.
 */
export function componentTokens(c: SemanticColors, mode: ColorMode) {
  const shadow = shadowsByMode[mode];
  return {
    panelBg: c.bgSurface,
    panelBorder: c.borderSubtle,
    panelRadius: radius.lg,
    panelPadding: space[5],
    panelPaddingCompact: space[4],
    panelHeaderHeight: '40px',

    statTileBg: c.bgSurface,
    statTileBorder: c.borderSubtle,
    statTileLabelColor: c.textTertiary,
    statTileValueColor: c.textPrimary,

    tableHeaderBg: c.bgInset,
    tableHeaderColor: c.textTertiary,
    tableRowHeight: '34px',
    tableRowHeightCompact: '28px',
    tableBorder: c.borderSubtle,
    tableRowHoverBg: c.bgHover,

    buttonRadius: radius.md,
    buttonPaddingX: space[3],
    buttonHeight: '32px',
    buttonHeightSm: '26px',
    buttonHeightLg: '38px',

    inputBg: mode === 'dark' ? c.bgInset : c.bgSurface,
    inputBorder: c.borderDefault,
    inputBorderHover: c.borderStrong,
    inputRadius: radius.md,
    inputHeight: '32px',

    chipRadius: radius.sm,
    chipHeight: '20px',

    tooltipBg: c.bgSurfaceOverlay,
    tooltipColor: c.textPrimary,
    tooltipShadow: shadow.lg,

    scrollbarThumb: c.borderStrong,
    scrollbarTrack: 'transparent',
  };
}

// ---------------------------------------------------------------------------
// CSS custom property emission
// ---------------------------------------------------------------------------

const kebab = (s: string) => s.replace(/([a-z0-9])([A-Z])/g, '$1-$2').toLowerCase();

/**
 * Flattens every layer into `--custom-property` form for the given mode.
 * Primitives and the static scales are mode-independent; semantic and
 * component tokens are not.
 */
export function toCssVars(mode: ColorMode): Record<string, string> {
  const c = colorsByMode[mode];
  const vars: Record<string, string> = {};

  // Layer 1 — primitives
  for (const [family, scale] of Object.entries(primitives)) {
    for (const [step, value] of Object.entries(scale)) {
      vars[`--color-${family}-${step}`] = value as string;
    }
  }
  for (const [k, v] of Object.entries(space)) vars[`--space-${String(k).replace('.', '_')}`] = v;
  for (const [k, v] of Object.entries(fontSize)) vars[`--font-size-${k}`] = v;
  for (const [k, v] of Object.entries(fontWeight)) vars[`--font-weight-${k}`] = String(v);
  for (const [k, v] of Object.entries(lineHeight)) vars[`--line-height-${k}`] = String(v);
  for (const [k, v] of Object.entries(letterSpacing)) vars[`--letter-spacing-${k}`] = v;
  for (const [k, v] of Object.entries(radius)) vars[`--radius-${k}`] = v;
  for (const [k, v] of Object.entries(duration)) vars[`--duration-${k}`] = v;
  for (const [k, v] of Object.entries(easing)) vars[`--easing-${k}`] = v;
  vars['--font-family-sans'] = fontFamily.sans;
  vars['--font-family-mono'] = fontFamily.mono;

  // Layer 2 — semantic
  for (const [k, v] of Object.entries(c)) {
    if (Array.isArray(v)) {
      v.forEach((series, i) => {
        vars[`--color-chart-series-${i + 1}`] = series;
      });
    } else {
      vars[`--color-${kebab(k)}`] = v as string;
    }
  }
  for (const [k, v] of Object.entries(shadowsByMode[mode])) vars[`--shadow-${k}`] = v;

  // Layer 3 — component
  for (const [k, v] of Object.entries(componentTokens(c, mode))) {
    vars[`--${kebab(k)}`] = v;
  }
  for (const [k, v] of Object.entries(layout)) vars[`--layout-${kebab(k)}`] = v;

  return vars;
}

/** Writes the token set for `mode` onto :root. Called on every mode change. */
export function applyCssVars(mode: ColorMode): void {
  const root = document.documentElement;
  const vars = toCssVars(mode);
  for (const [name, value] of Object.entries(vars)) {
    root.style.setProperty(name, value);
  }
  root.setAttribute('data-theme', mode);
  root.style.colorScheme = mode;
}
