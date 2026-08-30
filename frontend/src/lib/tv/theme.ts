import { colorsByMode, primitives, type ColorMode } from '../../theme/tokens';

/**
 * TradingView theming.
 *
 * The charting library renders to canvas and parses colour strings itself, so
 * it cannot read our CSS custom properties — these have to be concrete values
 * pulled from the token source.
 *
 * Chrome (background, grid, axes, candles) follows the colour mode. Study plot
 * colours deliberately do not: they are categorical identities, and a study
 * that changed hue with the theme would be harder to recognise, not easier.
 * They are picked from the mid-range of each primitive ramp so they stay legible
 * on both a near-black and a near-white pane.
 */

/** Hex primitive + alpha → rgba(), so translucent study colours track the ramp. */
function withAlpha(hex: string, alpha: number): string {
  const h = hex.replace('#', '');
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

export function tvOverrides(mode: ColorMode): Record<string, string | number | boolean> {
  const c = colorsByMode[mode];
  return {
    'paneProperties.background': c.bgInset,
    'paneProperties.backgroundType': 'solid',
    'paneProperties.vertGridProperties.color': c.chartGrid,
    'paneProperties.horzGridProperties.color': c.chartGrid,
    'paneProperties.crossHairProperties.color': c.accent,
    'paneProperties.legendProperties.showStudyTitles': true,

    'scalesProperties.textColor': c.textSecondary,
    'scalesProperties.lineColor': c.borderDefault,
    'scalesProperties.fontSize': 11,

    'mainSeriesProperties.candleStyle.upColor': c.long,
    'mainSeriesProperties.candleStyle.downColor': c.short,
    'mainSeriesProperties.candleStyle.wickUpColor': c.long,
    'mainSeriesProperties.candleStyle.wickDownColor': c.short,
    'mainSeriesProperties.candleStyle.borderUpColor': c.long,
    'mainSeriesProperties.candleStyle.borderDownColor': c.short,

    'mainSeriesProperties.hollowCandleStyle.upColor': c.long,
    'mainSeriesProperties.hollowCandleStyle.downColor': c.short,
    'mainSeriesProperties.barStyle.upColor': c.long,
    'mainSeriesProperties.barStyle.downColor': c.short,
    'mainSeriesProperties.lineStyle.color': c.accent,
    'mainSeriesProperties.areaStyle.linecolor': c.accent,
  };
}

/** Colour for a buy/sell position line drawn on the chart. */
export function tvSideColor(mode: ColorMode, side: 'buy' | 'sell'): string {
  const c = colorsByMode[mode];
  return side === 'buy' ? c.long : c.short;
}

/**
 * Named palette for custom study plots. Mode-independent by design — see the
 * note above. Every entry references a token primitive rather than a literal.
 */
export const studyPalette = {
  bull: primitives.cyan[400],
  bear: primitives.pink[500],
  transparent: 'rgba(0, 0, 0, 0)',

  violet: primitives.violet[500],
  cyan: primitives.cyan[500],
  pink: primitives.pink[500],
  orange: primitives.orange[500],
  red: primitives.red[500],
  green: primitives.green[500],
  teal: primitives.teal[500],
  yellow: primitives.amber[400],
  amber: primitives.amber[500],
  blue: primitives.blue[500],
  neutral: primitives.neutral[400],
  white: primitives.neutral[50],

  /** Zero / reference lines. */
  zeroLine: withAlpha(primitives.neutral[400], 0.4),
  overbought: withAlpha(primitives.red[500], 0.4),
  oversold: withAlpha(primitives.green[400], 0.4),
  overboughtStrong: withAlpha(primitives.red[500], 0.6),
  oversoldStrong: withAlpha(primitives.green[400], 0.6),

  greenFill: withAlpha(primitives.green[400], 0.5),
  redFill: withAlpha(primitives.red[400], 0.5),
  greenBar: withAlpha(primitives.green[400], 0.7),
  redBar: withAlpha(primitives.red[400], 0.7),
  neutralBar: withAlpha(primitives.neutral[50], 0.7),
  yellowBar: withAlpha(primitives.amber[400], 0.7),
  rangeFill: withAlpha(primitives.violet[500], 0.12),

  /** Chandelier exit / trend-flip pairs. */
  trendUp: primitives.cyan[400],
  trendDown: primitives.red[500],

  /** Linear-regression channel. */
  regression: primitives.blue[400],
  regressionBand: primitives.pink[500],
  regressionFill: withAlpha(primitives.blue[400], 0.08),

  /** G-FRAMA cloud. */
  cloudFill: withAlpha(primitives.violet[500], 0.08),
  neutralFill: withAlpha(primitives.neutral[400], 0.6),

  /** RSI study. */
  rsi: primitives.violet[400],
  rsiSignal: primitives.amber[500],
  rsiUpper: withAlpha(primitives.red[500], 0.5),
  rsiLower: withAlpha(primitives.green[400], 0.5),
} as const;
